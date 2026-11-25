using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.MediaInfo;

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

    private sealed record BoundLease(string Kind, string Category, string Asset, string? Series,
        string Platform, PrivateHlsLoopback.Lease Lease)
    {
        internal PrivateMediaProbe.Observation? Metadata { get; set; }
    }

    internal Task<MediaSourceInfo?> BindProbedSourceAsync(Guid owner, string token,
        string category, string asset, string platform, string executable, CancellationToken ct,
        string kind = "live", string? series = null)
        => BindSourceAsync(owner, token, category, asset, platform, null, ct, kind, series, executable);

    internal async Task<MediaSourceInfo?> BindSourceAsync(Guid owner, string token,
        string category, string asset, string platform, MediaSourceInfo? source, CancellationToken ct,
        string kind = "live", string? series = null, string? probeExecutable = null)
    {
        // The metadata is supplied by the authorized host, not guessed from
        // the catalogue or an arbitrary client URL. The transport replaces
        // the input path with its own opaque, process-private capability.
        if (owner == Guid.Empty || (source is null && string.IsNullOrEmpty(probeExecutable))
            || (source is not null && (source.SupportsDirectPlay || !source.SupportsTranscoding
                || source.RequiresOpening || source.RequiresClosing
                || (kind != "live" && source.IsInfiniteStream)))
            || kind is not ("live" or "vod" or "series")
            || (kind == "series" ? string.IsNullOrEmpty(series) : series is not null))
            return null;
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        BoundLease? openedHere = null;
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
                var opened = kind == "live"
                    ? await _bridge.OpenAsync(owner, token, category, asset, platform, ct).ConfigureAwait(false)
                    : await _bridge.OpenFileAsync(owner, token, kind, category, asset, series, platform, ct)
                        .ConfigureAwait(false);
                lease = new BoundLease(kind, category, asset, series, platform, opened);
                _leases.Add(owner, lease);
                openedHere = lease;
            }
            if (lease.Kind != kind || lease.Category != category || lease.Asset != asset
                || lease.Series != series || lease.Platform != platform)
                return null;
            if (source is null)
            {
                // Probe only this admitted lease, never an origin supplied by
                // a client. Cache typed observations for its exact lifetime.
                lease.Metadata ??= await _bridge.ProbeAsync(owner, lease.Lease, probeExecutable!, ct)
                    .ConfigureAwait(false);
                source = FromObservation(lease.Metadata, kind);
            }
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
            or UnauthorizedAccessException or OperationCanceledException or System.Text.Json.JsonException
            or System.ComponentModel.Win32Exception)
        {
            // Never leave a newly opened, unpublished private capability
            // behind after failed probing/negotiation. Do not touch any
            // preexisting lease or another owner's session.
            if (openedHere is not null && _bridge is not null
                && _leases.TryGetValue(owner, out var current) && ReferenceEquals(current, openedHere))
            {
                using var cleanup = new CancellationTokenSource(TimeSpan.FromSeconds(8));
                try
                {
                    await _bridge.ReleaseRecoveredAsync(owner, openedHere.Lease, cleanup.Token)
                        .ConfigureAwait(false);
                }
                catch (Exception failure) when (failure is InvalidOperationException or IOException
                    or OperationCanceledException) { }
                finally { _leases.Remove(owner); }
            }
            return null;
        }
        finally
        {
            _gate.Release();
        }
    }

    private static MediaSourceInfo FromObservation(PrivateMediaProbe.Observation metadata, string kind)
    {
        bool infinite = kind == "live" && metadata.DurationTicks is null;
        return new MediaSourceInfo
        {
            Name = "Private authorized media",
            Protocol = MediaProtocol.Http,
            IsRemote = true,
            IsInfiniteStream = infinite,
            AnalyzeDurationMs = infinite ? 5000 : null,
            Container = metadata.Container,
            RunTimeTicks = metadata.DurationTicks,
            Bitrate = metadata.Bitrate,
            MediaStreams = metadata.Streams.Select(s => new MediaStream
            {
                Index = s.Index, Type = s.Video ? MediaStreamType.Video : MediaStreamType.Audio,
                Codec = s.Codec, Width = s.Width, Height = s.Height,
                AverageFrameRate = s.FrameRate, Channels = s.Channels,
                SampleRate = s.SampleRate, BitRate = s.Bitrate
            }).ToList(),
            SupportsDirectPlay = false, SupportsDirectStream = true, SupportsTranscoding = true,
            SupportsProbing = false, RequiresOpening = false, RequiresClosing = false
        };
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
