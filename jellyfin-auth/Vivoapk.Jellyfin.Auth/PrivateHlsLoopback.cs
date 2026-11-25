using System.Collections.Concurrent;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>Disposable loopback adapter candidate, not registered or activated
/// in the production Jellyfin host. The trusted caller must already have
/// authenticated the Jellyfin user and authorized the specific asset. The
/// short-lived local capability must NEVER be exposed in PlaybackInfo or logs.
/// Future integration must keep it inside the isolated Jellyfin namespace.</summary>
internal sealed class PrivateHlsLoopback : IAsyncDisposable
{
    private static readonly Regex Segment = new(@"^segment/([A-Za-z0-9_-]{32})\.ts\z", RegexOptions.CultureInvariant);
    private static readonly Regex SafeTag = new(
        @"^#(?:EXT-X-VERSION:[0-9]{1,2}|EXT-X-TARGETDURATION:[0-9]{1,3}|EXT-X-MEDIA-SEQUENCE:[0-9]{1,16}|EXT-X-DISCONTINUITY-SEQUENCE:[0-9]{1,16}|EXTINF:[0-9]{1,3}(?:\.[0-9]{1,6})?,|EXT-X-DISCONTINUITY|EXT-X-ENDLIST)\z",
        RegexOptions.CultureInvariant);
    private readonly PrivateHlsSocketClient _backend;
    private readonly WebApplication _app;
    private readonly ConcurrentDictionary<string, Entry> _entries = new(StringComparer.Ordinal);
    private readonly SemaphoreSlim _admission = new(1, 1);
    private readonly Uri _base;

    private sealed class Entry(Guid owner, string token, string sessionId)
    {
        internal readonly Guid Owner = owner;
        internal readonly string Token = token;
        internal readonly string SessionId = sessionId;
        internal readonly SemaphoreSlim Gate = new(1, 1);
        internal long LastUse = Environment.TickCount64;
    }

    internal sealed class Lease(string id, Uri playlist)
    {
        internal string Id { get; } = id;
        internal Uri Playlist { get; } = playlist;
    }

    private PrivateHlsLoopback(PrivateHlsSocketClient backend, WebApplication app, Uri address)
    {
        _backend = backend;
        _app = app;
        _base = address;
    }

    internal static async Task<PrivateHlsLoopback> StartAsync(PrivateHlsSocketClient backend,
        CancellationToken ct)
    {
        // The CI harness is the only caller. No registration in ServiceRegistrator.
        // No inherited application configuration or logging destinations.
        var builder = WebApplication.CreateSlimBuilder(new WebApplicationOptions { Args = [] });
        builder.Configuration.Sources.Clear();
        builder.Logging.ClearProviders();
        builder.WebHost.ConfigureKestrel(options =>
        {
            options.Listen(IPAddress.Loopback, 0);
            options.AddServerHeader = false;
            options.Limits.MaxConcurrentConnections = 8;
            options.Limits.MaxRequestBodySize = 0;
            options.Limits.MaxRequestHeadersTotalSize = 8192;
            options.Limits.RequestHeadersTimeout = TimeSpan.FromSeconds(5);
        });
        var app = builder.Build();
        PrivateHlsLoopback? instance = null;
        app.Run(async http =>
        {
            if (instance is null)
            {
                http.Response.StatusCode = 503;
                return;
            }
            await instance.HandleAsync(http).ConfigureAwait(false);
        });
        try
        {
            await app.StartAsync(ct).ConfigureAwait(false);
            if (app.Urls.Count != 1
                || !Uri.TryCreate(app.Urls.Single(), UriKind.Absolute, out var address)
                || address.Scheme != "http" || address.Host != "127.0.0.1")
                throw new InvalidOperationException("Private loopback binding failed");
            instance = new PrivateHlsLoopback(backend, app, address);
            return instance;
        }
        catch
        {
            await app.DisposeAsync().ConfigureAwait(false);
            throw;
        }
    }

    internal async Task<Lease> OpenAsync(Guid owner, string token, string category, string asset,
        string platform, CancellationToken ct)
    {
        if (owner == Guid.Empty)
            throw new InvalidOperationException("Missing authenticated owner");
        await _admission.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (_entries.Count >= 2 || _entries.Values.Any(e => e.Owner == owner))
                throw new InvalidOperationException("Private media admission limit");
            var session = await _backend.OpenAsync(token, category, asset, platform, ct).ConfigureAwait(false);
            var id = Convert.ToHexString(RandomNumberGenerator.GetBytes(24)).ToLowerInvariant();
            if (!_entries.TryAdd(id, new Entry(owner, token, session)))
            {
                await _backend.CloseAsync(token, session, ct).ConfigureAwait(false);
                throw new InvalidOperationException("Private media admission failed");
            }
            return new Lease(id, new Uri(_base, "/vivoapk/" + id + "/index.m3u8"));
        }
        finally
        {
            _admission.Release();
        }
    }

    internal async Task CloseAsync(Guid owner, Lease lease, CancellationToken ct)
    {
        if (!_entries.TryGetValue(lease.Id, out var entry) || entry.Owner != owner)
            throw new InvalidOperationException("Private media session unavailable");
        await entry.Gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (!_entries.TryRemove(new KeyValuePair<string, Entry>(lease.Id, entry)))
                throw new InvalidOperationException("Private media session unavailable");
            await _backend.CloseAsync(entry.Token, entry.SessionId, ct).ConfigureAwait(false);
        }
        finally
        {
            entry.Gate.Release();
        }
    }

    private static bool SafePlaylist(byte[] bytes)
    {
        string text;
        try { text = new UTF8Encoding(false, true).GetString(bytes); }
        catch (DecoderFallbackException) { return false; }
        var lines = text.Split('\n');
        if (lines.Length is < 3 or > 256 || lines[0] != "#EXTM3U")
            return false;
        return lines.Skip(1).All(l => l.Length == 0 || SafeTag.IsMatch(l) || Segment.IsMatch(l))
            && lines.Any(l => Segment.IsMatch(l));
    }

    private async Task HandleAsync(HttpContext http)
    {
        http.Response.Headers.CacheControl = "no-store";
        var request = http.Request;
        var remote = http.Connection.RemoteIpAddress;
        if (remote is null || !IPAddress.Loopback.Equals(remote)
            || http.Connection.LocalIpAddress is null || !IPAddress.Loopback.Equals(http.Connection.LocalIpAddress)
            || request.Host.Value != _base.Authority || !HttpMethods.IsGet(request.Method)
            || request.QueryString.HasValue || request.Headers.ContainsKey("Forwarded")
            || request.Headers.ContainsKey("X-Forwarded-For")
            || request.Headers.ContainsKey("X-Forwarded-Host")
            || (request.Headers.TryGetValue("Range", out var range)
                && (range.Count != 1 || range[0] != "bytes=0-")))
        {
            http.Response.StatusCode = 404;
            return;
        }
        var path = request.Path.Value ?? "";
        var parts = path.Split('/');
        if (parts.Length is < 4 or > 5 || parts[0] != "" || parts[1] != "vivoapk"
            || parts[2].Length != 48 || !parts[2].All(c => c is >= 'a' and <= 'f' or >= '0' and <= '9')
            || !_entries.TryGetValue(parts[2], out var entry))
        {
            http.Response.StatusCode = 404;
            return;
        }
        bool playlist = parts.Length == 4 && parts[3] == "index.m3u8";
        var segment = parts.Length == 5 ? Segment.Match(parts[3] + "/" + parts[4]) : Match.Empty;
        if (!playlist && !segment.Success)
        {
            http.Response.StatusCode = 404;
            return;
        }
        bool locked = false;
        try
        {
            locked = await entry.Gate.WaitAsync(TimeSpan.FromSeconds(10), http.RequestAborted)
                .ConfigureAwait(false);
            if (!locked || !_entries.TryGetValue(parts[2], out var current)
                || !ReferenceEquals(current, entry)
                || Environment.TickCount64 - entry.LastUse >= 90000)
            {
                http.Response.StatusCode = 404;
                return;
            }
            var body = playlist
                ? await _backend.PlaylistAsync(entry.Token, entry.SessionId, http.RequestAborted).ConfigureAwait(false)
                : await _backend.SegmentAsync(entry.Token, entry.SessionId, segment.Groups[1].Value,
                    http.RequestAborted).ConfigureAwait(false);
            if (playlist && !SafePlaylist(body))
                throw new InvalidOperationException("Unsafe private playlist");
            entry.LastUse = Environment.TickCount64;
            http.Response.ContentType = playlist ? "application/vnd.apple.mpegurl" : "video/mp2t";
            http.Response.ContentLength = body.Length;
            await http.Response.Body.WriteAsync(body, http.RequestAborted).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException or OperationCanceledException)
        {
            if (!http.Response.HasStarted)
                http.Response.StatusCode = 404;
            else
                http.Abort();
        }
        finally
        {
            if (locked)
                entry.Gate.Release();
        }
    }

    public async ValueTask DisposeAsync()
    {
        foreach (var pair in _entries.ToArray())
        {
            try
            {
                using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                await CloseAsync(pair.Value.Owner,
                    new Lease(pair.Key, _base), deadline.Token).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is InvalidOperationException or OperationCanceledException)
            {
                _entries.TryRemove(pair.Key, out _);
            }
        }
        await _app.StopAsync().ConfigureAwait(false);
        await _app.DisposeAsync().ConfigureAwait(false);
        _admission.Dispose();
    }
}
