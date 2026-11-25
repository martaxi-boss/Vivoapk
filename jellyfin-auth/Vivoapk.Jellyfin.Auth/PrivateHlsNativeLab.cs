#if VIVOAPK_NATIVE_PLAYBACK_LAB
using System.Text.Json.Nodes;
using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Model.Dto;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>CI-only integration of the private transport inside the real Jellyfin
/// process. Fixed disposable socket and fixture accounts only. Neither this
/// class nor its registration exists in an ordinary plugin build.</summary>
internal sealed class PrivateHlsNativeLab : IAsyncDisposable
{
    private const string SocketPath = "/srv/projects/Vivoapk/run/private-hls-fixture.sock";
    private const string KeyPath = "/srv/projects/Vivoapk/run/private-hls-fixture.key";
    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly Dictionary<Guid, BoundLease> _leases = new();
    private PrivateHlsSocketClient? _client;
    private PrivateHlsLoopback? _bridge;

    private sealed record BoundLease(string Category, string Asset, string Platform,
        PrivateHlsLoopback.Lease Lease);

    internal static bool Enabled => NativePlaybackLab.Enabled &&
        Environment.GetEnvironmentVariable("VIVOAPK_CI_PRIVATE_HLS") == "1" &&
        !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("RUNNER_TEMP"));

    internal async Task<MediaSourceInfo?> CreateSourceAsync(Guid owner, string username, string token,
        string origin, string kind, string category, string asset, string platform, CancellationToken ct)
    {
        if (!Enabled || owner == Guid.Empty || !NativePlaybackLab.IsFixtureAccount(username)
            || (username == "synthetic-mega-user" ? category != "10" : category != "20")
            || kind != "live" || asset != "42")
            return null;
        // Require the original fixture-only metadata and loopback resolver origin.
        var source = NativePlaybackLab.CreateSource(origin, kind, asset);
        if (source is null)
            return null;
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (_bridge is null)
            {
                var info = new FileInfo(KeyPath);
                if (!info.Exists || info.Length is < 32 or > 1024 || info.LinkTarget is not null)
                    return null;
                _client = new PrivateHlsSocketClient(SocketPath, File.ReadAllText(KeyPath).Trim());
                _bridge = await PrivateHlsLoopback.StartAsync(_client, ct).ConfigureAwait(false);
            }
            if (!_leases.TryGetValue(owner, out var lease))
            {
                var opened = await _bridge.OpenAsync(owner, token, category, asset, platform, ct)
                    .ConfigureAwait(false);
                lease = new BoundLease(category, asset, platform, opened);
                _leases.Add(owner, lease);
            }
            if (lease.Category != category || lease.Asset != asset || lease.Platform != platform)
                return null;
            source.Name = "Synthetic private HLS native fixture";
            // Keep Id unset: ChannelManager.NormalizeMediaSources assigns the
            // actual item's native GUID (same contract as the passing finite
            // MPEG-TS fixture). DynamicHlsHelper.GetMasterPlaylistInternal
            // parses this ID as a Guid for finite-stream trickplay; an owner
            // label is not a valid MediaSourceId and caused the 500 path.
            // Isolation stays bound to the authenticated owner and private
            // lease, not to a custom public source identifier.
            source.Id = null;
            source.Path = lease.Lease.Playlist.AbsoluteUri;
            return source;
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException
            or UnauthorizedAccessException or OperationCanceledException)
        {
            return null;
        }
        finally
        {
            _gate.Release();
        }
    }

    internal async Task<bool> MatchesJobAsync(Guid owner, TranscodingJob? job, string? device,
        string session, CancellationToken ct)
    {
        if (job is null || string.IsNullOrEmpty(device)
            || job.DeviceId != device || !string.Equals(job.PlaySessionId, session,
                StringComparison.OrdinalIgnoreCase))
            return false;
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            return _leases.TryGetValue(owner, out var bound)
                && job.MediaSource?.Path == bound.Lease.Playlist.AbsoluteUri;
        }
        finally { _gate.Release(); }
    }

    internal async Task CloseAsync(Guid owner, CancellationToken ct)
    {
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (_bridge is not null && _leases.Remove(owner, out var bound))
                await _bridge.CloseAsync(owner, bound.Lease, ct).ConfigureAwait(false);
        }
        finally { _gate.Release(); }
    }

    internal static bool ContainsInternalPath(string text) =>
        text.Contains("/vivoapk/", StringComparison.OrdinalIgnoreCase)
        || text.Contains("%2fvivoapk%2f", StringComparison.OrdinalIgnoreCase)
        || text.Contains("private-hls-fixture", StringComparison.OrdinalIgnoreCase);

    internal static void SanitizePlaybackInfo(JsonNode node)
    {
        if (node is JsonObject obj)
        {
            foreach (var key in obj.Select(pair => pair.Key).ToArray())
            {
                var child = obj[key];
                if (child is JsonValue value && value.TryGetValue<string>(out var text)
                    && ContainsInternalPath(text))
                {
                    if (!key.Equals("Path", StringComparison.OrdinalIgnoreCase)
                        && !key.Equals("EncoderPath", StringComparison.OrdinalIgnoreCase))
                        throw new InvalidOperationException("Unexpected internal media reference");
                    obj.Remove(key);
                }
                else if (child is not null)
                    SanitizePlaybackInfo(child);
            }
        }
        else if (node is JsonArray array)
        {
            foreach (var child in array)
                if (child is not null)
                    SanitizePlaybackInfo(child);
        }
        else if (node is JsonValue value && value.TryGetValue<string>(out var text)
            && ContainsInternalPath(text))
            throw new InvalidOperationException("Unexpected internal media reference");
    }

    public async ValueTask DisposeAsync()
    {
        await _gate.WaitAsync().ConfigureAwait(false);
        try
        {
            if (_bridge is not null)
                await _bridge.DisposeAsync().ConfigureAwait(false);
            _leases.Clear();
            _client?.Dispose();
        }
        finally { _gate.Release(); }
    }
}
#endif
