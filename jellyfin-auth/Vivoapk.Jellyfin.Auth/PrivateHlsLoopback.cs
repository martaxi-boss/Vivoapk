using System.Collections.Concurrent;
using System.Net;
using System.Globalization;
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
    private static readonly Regex FileRange = new(@"\Abytes=([0-9]{1,18})-([0-9]{0,18})\z", RegexOptions.CultureInvariant);
    private static readonly Regex Segment = new(@"^segment/([A-Za-z0-9_-]{32})\.ts\z", RegexOptions.CultureInvariant);
    private static readonly Regex SafeTag = new(
        @"^#(?:EXT-X-VERSION:[0-9]{1,2}|EXT-X-TARGETDURATION:[0-9]{1,3}|EXT-X-MEDIA-SEQUENCE:[0-9]{1,16}|EXT-X-DISCONTINUITY-SEQUENCE:[0-9]{1,16}|EXTINF:[0-9]{1,3}(?:\.[0-9]{1,6})?,|EXT-X-DISCONTINUITY|EXT-X-ENDLIST)\z",
        RegexOptions.CultureInvariant);
    private readonly PrivateHlsSocketClient _backend;
    private readonly WebApplication _app;
    private readonly ConcurrentDictionary<string, Entry> _entries = new(StringComparer.Ordinal);
    private readonly SemaphoreSlim _admission = new(1, 1);
    private readonly Uri _base;
    private readonly CancellationTokenSource _stopping = new();
    private readonly Task _sweeper;
    private int _disposed;

    private sealed class Entry(Guid owner, PrivateHlsSocketClient.Grant grant, string sessionId, long issued,
        PrivateHlsSocketClient.FileInfo? file = null)
    {
        internal readonly Guid Owner = owner;
        internal readonly PrivateHlsSocketClient.FileInfo? File = file;
        internal string Token = grant.Token;
        internal long GrantExpires = issued + grant.Seconds * 1000L;
        internal long RenewAt = issued + grant.Seconds * 500L;
        internal readonly string SessionId = sessionId;
        internal readonly SemaphoreSlim Gate = new(1, 1);
        // FFmpeg may open a second HTTP connection before closing its old
        // connection when it seeks. Bound responses separately from the
        // backend grant lock; never hold that lock during socket writes.
        internal readonly SemaphoreSlim FileReaders = new(2, 2);
        internal readonly CancellationTokenSource Retired = new();
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
        _sweeper = SweepAsync(_stopping.Token);
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
            var issued = Environment.TickCount64;
            var opened = await _backend.OpenAsync(token, category, asset, platform, ct).ConfigureAwait(false);
            var session = opened.SessionId;
            var id = Convert.ToHexString(RandomNumberGenerator.GetBytes(24)).ToLowerInvariant();
            if (!_entries.TryAdd(id, new Entry(owner, opened.Grant, session, issued)))
            {
                await _backend.CloseAsync(opened.Grant.Token, session, ct).ConfigureAwait(false);
                throw new InvalidOperationException("Private media admission failed");
            }
            return new Lease(id, new Uri(_base, "/vivoapk/" + id + "/index.m3u8"));
        }
        finally
        {
            _admission.Release();
        }
    }

    internal async Task<Lease> OpenFileAsync(Guid owner, string token, string kind, string category,
        string asset, string? series, string platform, CancellationToken ct)
    {
        if (owner == Guid.Empty)
            throw new InvalidOperationException("Missing authenticated owner");
        await _admission.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (_entries.Count >= 2 || _entries.Values.Any(e => e.Owner == owner))
                throw new InvalidOperationException("Private media admission limit");
            var issued = Environment.TickCount64;
            var opened = await _backend.OpenFileAsync(token, kind, category, asset, series, platform, ct)
                .ConfigureAwait(false);
            var id = Convert.ToHexString(RandomNumberGenerator.GetBytes(24)).ToLowerInvariant();
            if (!_entries.TryAdd(id, new Entry(owner, opened.Grant, opened.SessionId, issued, opened.File)))
            {
                await _backend.CloseAsync(opened.Grant.Token, opened.SessionId, ct, finite: true)
                    .ConfigureAwait(false);
                throw new InvalidOperationException("Private media admission failed");
            }
            return new Lease(id, new Uri(_base, "/vivoapk/" + id + "/file"));
        }
        finally { _admission.Release(); }
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
            entry.Retired.Cancel();
            try
            {
                await _backend.CloseAsync(entry.Token, entry.SessionId, ct, finite: entry.File is not null)
                    .ConfigureAwait(false);
            }
            finally { entry.Token = ""; }
        }
        finally
        {
            entry.Gate.Release();
        }
    }

    internal async Task ReleaseRecoveredAsync(Guid owner, Lease lease, CancellationToken ct)
    {
        if (!_entries.TryGetValue(lease.Id, out var entry) || entry.Owner != owner)
            return;
        await entry.Gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (!_entries.TryRemove(new KeyValuePair<string, Entry>(lease.Id, entry)))
                return;
            entry.Retired.Cancel();
            try
            {
                await _backend.CloseAsync(entry.Token, entry.SessionId, ct, finite: entry.File is not null).ConfigureAwait(false);
            }
            catch (InvalidOperationException)
            {
                // Local capability removed; backend's bounded sweeper
                // handles unavailable or previously expired grants.
            }
            finally { entry.Token = ""; }
        }
        finally { entry.Gate.Release(); }
    }

    // Backend and local capability cleanup need no extra service. Idle or
    // expired entries are removed without a subsequent client request.
    private async Task SweepAsync(CancellationToken stopping)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(2));
        try
        {
            while (await timer.WaitForNextTickAsync(stopping).ConfigureAwait(false))
            {
                foreach (var pair in _entries.ToArray())
                {
                    var entry = pair.Value;
                    if (!entry.Gate.Wait(0))
                        continue;
                    try
                    {
                        var now = Environment.TickCount64;
                        if (now < entry.GrantExpires && now - entry.LastUse < 90000)
                            continue;
                        if (!_entries.TryRemove(new KeyValuePair<string, Entry>(pair.Key, entry)))
                            continue;
                        entry.Retired.Cancel();
                        if (now < entry.GrantExpires)
                        {
                            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(stopping);
                            deadline.CancelAfter(TimeSpan.FromSeconds(5));
                            try
                            {
                                await _backend.CloseAsync(entry.Token, entry.SessionId, deadline.Token, finite: entry.File is not null)
                                    .ConfigureAwait(false);
                            }
                            catch (InvalidOperationException) { }
                        }
                        entry.Token = "";
                    }
                    finally { entry.Gate.Release(); }
                }
            }
        }
        catch (OperationCanceledException) when (stopping.IsCancellationRequested) { }
    }

    private async Task RenewIfNeededAsync(Entry entry, CancellationToken ct)
    {
        var now = Environment.TickCount64;
        if (now >= entry.GrantExpires)
            throw new InvalidOperationException("Private media grant expired");
        if (now < entry.RenewAt)
            return;
        var grant = await _backend.RenewAsync(entry.Token, entry.SessionId, ct, finite: entry.File is not null).ConfigureAwait(false);
        entry.Token = grant.Token;
        entry.GrantExpires = now + grant.Seconds * 1000L;
        entry.RenewAt = now + grant.Seconds * 500L;
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

    private static bool TryRange(HttpRequest request, long total, out long start, out long end, out bool partial)
    {
        start = 0;
        end = total - 1;
        partial = request.Headers.TryGetValue("Range", out var values);
        if (!partial)
            return true;
        if (values.Count != 1 || values[0] is not string value)
            return false;
        var match = FileRange.Match(value);
        if (!match.Success || !long.TryParse(match.Groups[1].Value, NumberStyles.None,
                CultureInfo.InvariantCulture, out start) || start >= total)
            return false;
        if (match.Groups[2].Length != 0)
        {
            if (!long.TryParse(match.Groups[2].Value, NumberStyles.None,
                    CultureInfo.InvariantCulture, out end) || end < start)
                return false;
            end = Math.Min(end, total - 1);
        }
        return true;
    }

    private async Task<byte[]> ReadFileWindowAsync(string id, Entry entry,
        PrivateHlsSocketClient.FileInfo file, long start, int length, CancellationToken ct)
    {
        // Serialize exactly one backend operation and any grant rotation.
        // A disconnected HTTP reader must not interrupt an already submitted
        // bounded operation and lose the returned rotated grant. The socket
        // client enforces its own 30-second deadline; cancellation prevents
        // new operations and prevents delivery of a completed old window.
        if (!await entry.Gate.WaitAsync(TimeSpan.FromSeconds(35), ct).ConfigureAwait(false))
            throw new InvalidOperationException("Private file operation busy");
        try
        {
            ct.ThrowIfCancellationRequested();
            if (!_entries.TryGetValue(id, out var current) || !ReferenceEquals(current, entry)
                || Environment.TickCount64 - entry.LastUse >= 90000)
                throw new InvalidOperationException("Private media session unavailable");
            await RenewIfNeededAsync(entry, CancellationToken.None).ConfigureAwait(false);
            ct.ThrowIfCancellationRequested();
            var bytes = await _backend.ReadFileAsync(entry.Token, entry.SessionId, file,
                start, length, CancellationToken.None).ConfigureAwait(false);
            entry.LastUse = Environment.TickCount64;
            ct.ThrowIfCancellationRequested();
            return bytes;
        }
        finally { entry.Gate.Release(); }
    }

    private async Task ServeFileAsync(HttpContext http, string id, Entry entry,
        PrivateHlsSocketClient.FileInfo file, long start, long end, bool partial)
    {
        using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(
            http.RequestAborted, entry.Retired.Token, _stopping.Token);
        var ct = lifetime.Token;
        if (!entry.FileReaders.Wait(0))
            throw new InvalidOperationException("Private file reader limit");
        try
        {
            if (HttpMethods.IsHead(http.Request.Method))
            {
                _ = await ReadFileWindowAsync(id, entry, file, start, 1, ct).ConfigureAwait(false);
                ct.ThrowIfCancellationRequested();
                SetFileHeaders(http, file, start, end, partial);
                return;
            }
            for (long offset = start; offset <= end;)
            {
                int length = (int)Math.Min(PrivateHlsSocketClient.FileWindowBytes, end - offset + 1);
                var body = await ReadFileWindowAsync(id, entry, file, offset, length, ct)
                    .ConfigureAwait(false);
                ct.ThrowIfCancellationRequested();
                if (offset == start)
                    SetFileHeaders(http, file, start, end, partial);
                // Gate is free here. A second authenticated range can open
                // even while the first connection is blocked on backpressure.
                // Bound a stalled response, and never buffer a whole file.
                using var writeDeadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
                writeDeadline.CancelAfter(TimeSpan.FromSeconds(20));
                await http.Response.Body.WriteAsync(body, writeDeadline.Token).ConfigureAwait(false);
                await http.Response.Body.FlushAsync(writeDeadline.Token).ConfigureAwait(false);
                offset += body.Length;
            }
        }
        finally { entry.FileReaders.Release(); }
    }

    private static void SetFileHeaders(HttpContext http, PrivateHlsSocketClient.FileInfo file,
        long start, long end, bool partial)
    {
        http.Response.StatusCode = partial ? 206 : 200;
        http.Response.ContentType = file.Mime;
        http.Response.ContentLength = end - start + 1;
        http.Response.Headers["Accept-Ranges"] = "bytes";
        if (partial)
            http.Response.Headers["Content-Range"] = string.Create(CultureInfo.InvariantCulture,
                $"bytes {start}-{end}/{file.Length}");
    }

    private async Task HandleAsync(HttpContext http)
    {
        http.Response.Headers.CacheControl = "no-store";
        var request = http.Request;
        var remote = http.Connection.RemoteIpAddress;
        if (remote is null || !IPAddress.Loopback.Equals(remote)
            || http.Connection.LocalIpAddress is null || !IPAddress.Loopback.Equals(http.Connection.LocalIpAddress)
            || request.Host.Value != _base.Authority
            || (!HttpMethods.IsGet(request.Method) && !HttpMethods.IsHead(request.Method))
            || request.QueryString.HasValue || request.Headers.ContainsKey("Forwarded")
            || request.Headers.ContainsKey("X-Forwarded-For")
            || request.Headers.ContainsKey("X-Forwarded-Host"))
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
        bool fileRoute = parts.Length == 4 && parts[3] == "file";
        long start = 0, end = 0;
        bool partial = false;
        if (entry.File is { } file)
        {
            if (!fileRoute || !TryRange(request, file.Length, out start, out end, out partial))
            {
                http.Response.StatusCode = 404;
                return;
            }
        }
        else if ((!playlist && !segment.Success) || !HttpMethods.IsGet(request.Method)
            || (request.Headers.TryGetValue("Range", out var range)
                && (range.Count != 1 || range[0] != "bytes=0-")))
        {
            http.Response.StatusCode = 404;
            return;
        }
        bool locked = false;
        try
        {
            if (entry.File is { } finiteFile)
            {
                await ServeFileAsync(http, parts[2], entry, finiteFile, start, end, partial)
                    .ConfigureAwait(false);
                return;
            }
            locked = await entry.Gate.WaitAsync(TimeSpan.FromSeconds(10), http.RequestAborted)
                .ConfigureAwait(false);
            if (!locked || !_entries.TryGetValue(parts[2], out var current)
                || !ReferenceEquals(current, entry)
                || Environment.TickCount64 - entry.LastUse >= 90000)
            {
                http.Response.StatusCode = 404;
                return;
            }
            await RenewIfNeededAsync(entry, http.RequestAborted).ConfigureAwait(false);
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
            {
                http.Response.Clear();
                http.Response.Headers.CacheControl = "no-store";
                http.Response.StatusCode = 404;
            }
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
        if (Interlocked.Exchange(ref _disposed, 1) != 0)
            return;
        _stopping.Cancel();
        await _sweeper.ConfigureAwait(false);
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
                if (_entries.TryRemove(pair.Key, out var retired))
                    retired.Retired.Cancel();
            }
        }
        await _app.StopAsync().ConfigureAwait(false);
        await _app.DisposeAsync().ConfigureAwait(false);
        _admission.Dispose();
        _stopping.Dispose();
    }
}
