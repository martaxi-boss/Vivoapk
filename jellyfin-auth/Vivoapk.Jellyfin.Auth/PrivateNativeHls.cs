using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Model.Dto;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>Shared native private HLS lease manager, compiled into the normal
/// plugin. A trusted host must supply its private socket and key locations.
/// No registration or real-provider routing is enabled merely by this type.
/// The caller must authorize the item and supply accurate media metadata.</summary>
internal sealed class PrivateNativeHls(string socketPath, string keyPath) : IAsyncDisposable
{
    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly Dictionary<Guid, BoundLease> _leases = new();
    private PrivateHlsSocketClient? _client;
    private PrivateHlsLoopback? _bridge;

    private sealed record BoundLease(string Category, string Asset, string Platform,
        PrivateHlsLoopback.Lease Lease);

    internal async Task<MediaSourceInfo?> BindSourceAsync(Guid owner, string token,
        string category, string asset, string platform, MediaSourceInfo source, CancellationToken ct)
    {
        // The metadata is supplied by the authorized host, not guessed from
        // the catalogue or an arbitrary client URL. The transport replaces
        // the input path with its own opaque, process-private capability.
        if (owner == Guid.Empty || source.SupportsDirectPlay || !source.SupportsTranscoding
            || source.RequiresOpening || source.RequiresClosing)
            return null;
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (_bridge is null)
            {
                var info = new FileInfo(keyPath);
                if (!info.Exists || info.Length is < 32 or > 1024 || info.LinkTarget is not null)
                    return null;
                _client = new PrivateHlsSocketClient(socketPath, File.ReadAllText(keyPath).Trim());
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

    // Only a reserved, not-yet-published negotiation can call this. No
    // native job or other owner's lease can be retired by a failed response.
    internal async Task ReleaseUnstartedAsync(Guid owner, CancellationToken ct)
    {
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (_bridge is not null && _leases.TryGetValue(owner, out var bound))
            {
                await _bridge.ReleaseRecoveredAsync(owner, bound.Lease, ct).ConfigureAwait(false);
                _leases.Remove(owner);
            }
        }
        finally { _gate.Release(); }
    }

    internal async Task<bool> RecoverAsync(Guid owner, string sourcePath,
        Func<Task<bool>> recoverNativeJob, CancellationToken ct)
    {
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (_bridge is null || !_leases.TryGetValue(owner, out var bound)
                || !string.Equals(bound.Lease.Playlist.AbsoluteUri, sourcePath, StringComparison.Ordinal))
                return false;
            if (!await recoverNativeJob().ConfigureAwait(false))
                return false;
            await _bridge.ReleaseRecoveredAsync(owner, bound.Lease, ct).ConfigureAwait(false);
            _leases.Remove(owner);
            return true;
        }
        finally { _gate.Release(); }
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
