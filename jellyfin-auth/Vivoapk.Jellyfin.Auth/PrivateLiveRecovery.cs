using System.Collections.Concurrent;
using System.Globalization;
using System.Text.Json.Nodes;
using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Controller.Session;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>Shared bounded native playback lifecycle, compiled in the normal
/// plugin but not registered for real-provider playback yet. Only a negotiated,
/// authorized owner/device/item/session and its exact observed job can be
/// recovered. No process scanning or file deletion. Current capacity: two.
/// The host supplies the optional diagnostic header, not the client.</summary>
internal sealed class PrivateLiveRecovery(string? diagnosticHeader = null)
{
    private const long IdleMs = 80000;
    private readonly ConcurrentDictionary<string, State> _states = new(StringComparer.OrdinalIgnoreCase);
    private readonly object _bindGate = new();
    private readonly HashSet<Guid> _negotiating = new();

    internal bool TryBeginNegotiation(Guid owner)
    {
        lock (_bindGate)
        {
            if (owner == Guid.Empty || _states.Values.Any(s => s.Owner == owner)
                || _states.Count + _negotiating.Count >= 2)
                return false;
            return _negotiating.Add(owner);
        }
    }

    internal void EndNegotiation(Guid owner)
    {
        lock (_bindGate)
            _negotiating.Remove(owner);
    }

    private bool Retire(string session, State state)
    {
        lock (_bindGate)
            return _states.TryRemove(new KeyValuePair<string, State>(session, state));
    }
    private sealed class State(Guid owner, Guid item, string device, bool finite, string sourcePath)
    {
        internal readonly Guid Owner = owner;
        internal readonly Guid Item = item;
        internal readonly string Device = device;
        internal readonly bool Finite = finite;
        internal readonly string SourcePath = sourcePath;
        internal readonly SemaphoreSlim FiniteRequests = new(1, 1);
        internal readonly object Gate = new();
        internal long LastMedia = Environment.TickCount64;
        internal int InFlight;
        internal bool Recovering;
        internal TranscodingJob? Job;
    }

    internal async Task<bool> BindAsync(JsonNode node, Guid owner, Guid item, HttpContext http)
    {
        var device = http.User.FindFirst("Jellyfin-DeviceId")?.Value;
        if (string.IsNullOrEmpty(device) || node is not JsonObject response
            || response["PlaySessionId"] is not JsonValue value
            || !value.TryGetValue<string>(out var session) || !Guid.TryParse(session, out _)
            || response["MediaSources"] is not JsonArray sources || sources.Count != 1
            || sources[0] is not JsonObject media || media["IsInfiniteStream"] is not JsonValue flag
            || !flag.TryGetValue<bool>(out var infinite))
            return false;
        var services = http.RequestServices;
        var lab = services.GetRequiredService<PrivateNativeHls>();
        var source = await lab.GetBoundPathAsync(owner, http.RequestAborted).ConfigureAwait(false);
        if (string.IsNullOrEmpty(source))
            return false;
        var manager = services.GetRequiredService<ITranscodeManager>();
        var sessions = services.GetRequiredService<ISessionManager>();
        var stopping = services.GetRequiredService<IHostApplicationLifetime>().ApplicationStopping;
        var logger = services.GetRequiredService<ILogger<PrivateLiveRecovery>>();
        var state = new State(owner, item, device, !infinite, source);
        lock (_bindGate)
        {
            if (!_negotiating.Contains(owner) || _states.Count >= 2
                || _states.Values.Any(s => s.Owner == owner)
                || !_states.TryAdd(session, state))
                return false;
        }
        // Start the bounded observer at negotiation, not at the first
        // successful segment. An unused PlaybackInfo must not reserve a
        // slot forever. Only singleton services and immutable state escape
        // the request; no HttpContext, response, credentials or raw data.
        _ = RecoverAsync(manager, sessions, lab, session, state, logger, stopping);
        return true;
    }

    private void Deny(HttpContext http)
    {
        if (diagnosticHeader is not null)
            http.Response.Headers[diagnosticHeader] = "denied";
        http.Response.Headers.CacheControl = "no-store";
        http.Response.StatusCode = 404;
        http.Response.ContentLength = 0;
    }

    // The pinned Jellyfin live controller emits hls/<playlist-stem>/
    // <playlist-stem><index>.ts WITHOUT PlaySessionId in the query. The
    // legacy segment controller resolves files from the segment filename.
    // Bind BOTH path components to the exact already observed native job,
    // not merely the authenticated item or a client-supplied playlist id.
    private static bool MatchesLegacySegment(string[] parts, TranscodingJob? job)
    {
        if (parts.Length != 5 || job is null || string.IsNullOrEmpty(job.Path)
            || !parts[0].Equals("Videos", StringComparison.OrdinalIgnoreCase)
            || !parts[2].Equals("hls", StringComparison.OrdinalIgnoreCase))
            return false;
        var stem = Path.GetFileNameWithoutExtension(job.Path);
        if (string.IsNullOrEmpty(stem) || !stem.All(char.IsAsciiHexDigit)
            || !string.Equals(parts[3], stem, StringComparison.Ordinal)
            || !parts[4].StartsWith(stem, StringComparison.Ordinal)
            || !parts[4].EndsWith(".ts", StringComparison.Ordinal))
            return false;
        var indexLength = parts[4].Length - stem.Length - 3;
        return indexLength is > 0 and <= 10
            && parts[4].AsSpan(stem.Length, indexLength).ToArray().All(char.IsAsciiDigit);
    }

    internal async Task HandleAsync(HttpContext http, RequestDelegate downstream,
        Guid owner, Guid item, ILogger logger)
    {
        var values = http.Request.Query["PlaySessionId"];
        var device = http.User.FindFirst("Jellyfin-DeviceId")?.Value;
        var parts = (http.Request.Path.Value ?? "").Split('/', StringSplitOptions.RemoveEmptyEntries);
        var legacy = parts.Length == 5 && parts[2].Equals("hls", StringComparison.OrdinalIgnoreCase);
        bool video = parts.Length >= 3 && parts[0].Equals("Videos", StringComparison.OrdinalIgnoreCase);
        bool master = video && parts.Length == 3 && parts[2].Equals("master.m3u8", StringComparison.OrdinalIgnoreCase);
        bool livePlaylist = video && parts.Length == 3 && parts[2].Equals("live.m3u8", StringComparison.OrdinalIgnoreCase);
        bool finitePlaylist = video && parts.Length == 3 && parts[2].Equals("main.m3u8", StringComparison.OrdinalIgnoreCase);
        // Exact dynamic MPEG-TS route emitted by Jellyfin v12.4's
        // CreateMainPlaylistRequest ("hls1/main/"). Unlike the legacy
        // controller, it derives the path from item and streaming state.
        bool dynamicSegment = video && parts.Length == 5
            && parts[2].Equals("hls1", StringComparison.OrdinalIgnoreCase)
            && parts[3].Equals("main", StringComparison.Ordinal)
            && parts[4].EndsWith(".ts", StringComparison.Ordinal)
            && int.TryParse(parts[4].AsSpan(0, parts[4].Length - 3), NumberStyles.None,
                CultureInfo.InvariantCulture, out var index) && index is >= 0 and <= 1000000;
        if (string.IsNullOrEmpty(device) || (!master && !livePlaylist && !finitePlaylist && !dynamicSegment && !legacy)
            || (!HttpMethods.IsGet(http.Request.Method) && !(master && HttpMethods.IsHead(http.Request.Method))))
        {
            Deny(http);
            return;
        }
        string session;
        State state;
        if (legacy && values.Count == 0)
        {
            // Select a unique previously negotiated identity. Never derive
            // ownership from the untrusted cache filename alone.
            var matches = _states.Where(p => p.Value.Owner == owner && p.Value.Item == item
                && p.Value.Device == device).Take(2).ToArray();
            if (matches.Length != 1)
            {
                Deny(http);
                return;
            }
            session = matches[0].Key;
            state = matches[0].Value;
        }
        else
        {
            if (values.Count != 1 || !Guid.TryParse(values[0], out _)
                || !_states.TryGetValue(values[0]!, out var bound)
                || bound.Owner != owner || bound.Item != item || bound.Device != device)
            {
                Deny(http);
                return;
            }
            session = values[0]!;
            state = bound;
        }
        if ((state.Finite && (legacy || livePlaylist)) || (!state.Finite && (finitePlaylist || dynamicSegment)))
        {
            Deny(http);
            return;
        }
        // If present, native caller-controlled device/source selectors must
        // agree with the already authenticated identity. Do not accept the
        // alternate encoded Params or LiveStreamId source selection.
        var userIds = http.Request.Query["UserId"];
        var packed = http.Request.Query["Params"];
        var liveIds = http.Request.Query["LiveStreamId"];
        if (userIds.Count > 1 || (userIds.Count == 1
                && (!Guid.TryParse(userIds[0], out var requestedUser) || requestedUser != owner))
            || packed.Count > 1 || packed.Any(v => !string.IsNullOrEmpty(v))
            || liveIds.Count > 1 || liveIds.Any(v => !string.IsNullOrEmpty(v)))
        {
            Deny(http);
            return;
        }
        var devices = http.Request.Query["DeviceId"];
        var sources = http.Request.Query["MediaSourceId"];
        if (devices.Count > 1 || (devices.Count == 1 && devices[0] != device)
            || sources.Count > 1 || (sources.Count == 1
                && (!Guid.TryParse(sources[0], out var sourceItem) || sourceItem != item)))
        {
            Deny(http);
            return;
        }
        var services = http.RequestServices;
        var manager = services.GetRequiredService<ITranscodeManager>();
        var lab = services.GetRequiredService<PrivateNativeHls>();
        // Finite segments can legitimately replace the native FFmpeg job
        // during a seek. Serialize these requests for that one play session
        // so an old response cannot publish a different job concurrently.
        bool finiteLocked = false;
        try
        {
            if (state.Finite)
            {
                finiteLocked = await state.FiniteRequests.WaitAsync(TimeSpan.FromSeconds(20),
                    http.RequestAborted).ConfigureAwait(false);
                if (!finiteLocked)
                {
                    Deny(http);
                    return;
                }
            }
            if (!_states.TryGetValue(session, out var active) || !ReferenceEquals(active, state))
            {
                Deny(http);
                return;
            }
            var existing = manager.GetTranscodingJob(session);
            if (existing is not null && !await lab.MatchesJobAsync(owner, existing, device,
                    session, http.RequestAborted).ConfigureAwait(false))
            {
                Deny(http);
                return;
            }
            lock (state.Gate)
            {
                // Existing finite jobs may end or be replaced by the native
                // dynamic segment controller. Accept replacement only for
                // this exact authorized source/session and same cache stem.
                if (state.Recovering || (state.Job is not null
                        && !ReferenceEquals(state.Job, existing)
                        && (!state.Finite || (existing is not null
                            && !string.Equals(state.Job.Path, existing.Path, StringComparison.Ordinal))))
                    || (legacy && (!ReferenceEquals(state.Job, existing)
                        || !MatchesLegacySegment(parts, existing))))
                {
                    Deny(http);
                    return;
                }
                state.InFlight++;
            }
            try
            {
                await downstream(http).ConfigureAwait(false);
                if (http.Response.StatusCode != 200)
                    return;
                if (legacy && values.Count == 0)
                    logger.LogDebug("VIVOAPK_PRIVATE_NATIVE_QUERYLESS_SEGMENT=PASS");
                var job = manager.GetTranscodingJob(session);
                if (job is null || job.Type != TranscodingJobType.Hls ||
                    string.IsNullOrEmpty(job.Path) ||
                    !await lab.MatchesJobAsync(owner, job, device, session, http.RequestAborted)
                        .ConfigureAwait(false))
                    return;
                lock (state.Gate)
                {
                    if (state.Job is null)
                        state.Job = job;
                    else if (state.Finite && dynamicSegment
                        && string.Equals(state.Job.Path, job.Path, StringComparison.Ordinal))
                        state.Job = job;
                    if (!ReferenceEquals(state.Job, job))
                        return;
                    state.LastMedia = Environment.TickCount64;
                }
            }
            finally
            {
                lock (state.Gate)
                {
                    state.InFlight--;
                    if (http.Response.StatusCode == 200)
                        state.LastMedia = Environment.TickCount64;
                }
            }
        }
        finally
        {
            if (finiteLocked)
                state.FiniteRequests.Release();
        }
    }

    // A ping carries no item ID; resolve only an existing binding. The
    // caller still authenticates and rechecks ownership before forwarding.
    internal Guid ItemForSession(string session) =>
        Guid.TryParse(session, out _) && _states.TryGetValue(session, out var state)
            ? state.Item : Guid.Empty;

    internal async Task ReportAsync(HttpContext http, RequestDelegate downstream,
        Guid owner, Guid item, string session)
    {
        var device = http.User.FindFirst("Jellyfin-DeviceId")?.Value;
        if (owner == Guid.Empty || string.IsNullOrEmpty(device)
            || !_states.TryGetValue(session, out var state)
            || state.Owner != owner || state.Item != item || state.Device != device)
        {
            Deny(http);
            return;
        }
        var manager = http.RequestServices.GetRequiredService<ITranscodeManager>();
        var lab = http.RequestServices.GetRequiredService<PrivateNativeHls>();
        var job = manager.GetTranscodingJob(session);
        if (job is not null && (job.MediaSource?.Path != state.SourcePath ||
            !await lab.MatchesJobAsync(owner, job, device, session, http.RequestAborted)
                .ConfigureAwait(false)))
        {
            Deny(http);
            return;
        }
        lock (state.Gate)
        {
            if (state.Recovering || !_states.TryGetValue(session, out var active)
                || !ReferenceEquals(active, state)
                || !ReferenceEquals(manager.GetTranscodingJob(session), job))
            {
                Deny(http);
                return;
            }
            state.InFlight++;
        }
        try
        {
            await downstream(http).ConfigureAwait(false);
        }
        finally
        {
            // Playback reports are not evidence of media consumption.
            lock (state.Gate)
                state.InFlight--;
        }
    }

    // ActiveEncodings is used to restart an encoder without ending playback.
    // Do not forward to the native fire-and-forget wildcard action. Wait for
    // the exact bound job/cache to be removed while holding admission closed,
    // but retain the private media capability and its inactivity watchdog.
    internal async Task RestartEncodingAsync(HttpContext http, Guid owner, Guid item,
        string session, string requestedDevice)
    {
        var device = http.User.FindFirst("Jellyfin-DeviceId")?.Value;
        if (owner == Guid.Empty || string.IsNullOrEmpty(device)
            || !string.Equals(requestedDevice, device, StringComparison.Ordinal)
            || !_states.TryGetValue(session, out var state)
            || state.Owner != owner || state.Item != item || state.Device != device)
        {
            Deny(http);
            return;
        }
        var services = http.RequestServices;
        var manager = services.GetRequiredService<ITranscodeManager>();
        var lab = services.GetRequiredService<PrivateNativeHls>();
        var job = manager.GetTranscodingJob(session);
        if (job is not null && (job.Type != TranscodingJobType.Hls
            || string.IsNullOrEmpty(job.Path) || job.MediaSource?.Path != state.SourcePath
            || !await lab.MatchesJobAsync(owner, job, device, session, http.RequestAborted)
                .ConfigureAwait(false)))
        {
            Deny(http);
            return;
        }
        lock (state.Gate)
        {
            if (state.Recovering || state.InFlight != 0
                || !_states.TryGetValue(session, out var active) || !ReferenceEquals(active, state)
                || !ReferenceEquals(manager.GetTranscodingJob(session), job)
                || (job is not null && (job.ActiveRequestCount > 0
                    || (state.Job is not null && !ReferenceEquals(state.Job, job)
                        && (!state.Finite || state.Job.Path != job.Path)))))
            {
                Deny(http);
                return;
            }
            state.Recovering = true;
        }
        try
        {
            if (job is not null)
            {
                var path = job.Path!;
                await manager.KillTranscodingJobs(device, session,
                    candidate => string.Equals(candidate, path, StringComparison.Ordinal))
                    .ConfigureAwait(false);
            }
            if (manager.GetTranscodingJob(session) is not null)
            {
                Deny(http);
                return;
            }
            lock (state.Gate)
                state.Job = null;
            http.Response.Headers.CacheControl = "no-store";
            if (diagnosticHeader is not null)
                http.Response.Headers[diagnosticHeader] = "allowed";
            http.Response.StatusCode = StatusCodes.Status204NoContent;
            services.GetRequiredService<ILogger<PrivateLiveRecovery>>()
                .LogInformation("VIVOAPK_PRIVATE_NATIVE_ENCODER_RESTART_READY=PASS");
        }
        finally
        {
            lock (state.Gate)
                state.Recovering = false;
        }
    }

    // Explicit stop must retire the same observed native job and lease.
    // Hold the state closed to media requests while native Stop is in flight.
    // A stale stop must never close the next playback for the same owner.
    internal async Task StopAsync(HttpContext http, RequestDelegate downstream,
        Guid owner, Guid item, string session)
    {
        var device = http.User.FindFirst("Jellyfin-DeviceId")?.Value;
        if (string.IsNullOrEmpty(device) || !_states.TryGetValue(session, out var state)
            || state.Owner != owner || state.Item != item || state.Device != device)
        {
            Deny(http);
            return;
        }
        var services = http.RequestServices;
        var manager = services.GetRequiredService<ITranscodeManager>();
        var lab = services.GetRequiredService<PrivateNativeHls>();
        var job = manager.GetTranscodingJob(session);
        if (job is not null && (job.Type != TranscodingJobType.Hls
            || job.MediaSource?.Path != state.SourcePath
            || !await lab.MatchesJobAsync(owner, job, device, session, http.RequestAborted)
                .ConfigureAwait(false)))
        {
            Deny(http);
            return;
        }
        lock (state.Gate)
        {
            if (state.Recovering || state.InFlight != 0
                || !_states.TryGetValue(session, out var active) || !ReferenceEquals(active, state)
                || !ReferenceEquals(manager.GetTranscodingJob(session), job)
                || (job is not null && state.Job is not null && !ReferenceEquals(state.Job, job)
                    && (!state.Finite || state.Job.Path != job.Path)))
            {
                Deny(http);
                return;
            }
            state.Recovering = true;
        }
        // The native controller awaits KillTranscodingJobs. Do not release
        // the lease until that exact job is gone. Cancellation or ambiguous
        // completion leaves the binding fail-closed for the watchdog.
        try
        {
            await downstream(http).ConfigureAwait(false);
            if (http.Response.StatusCode is not (200 or 204))
                return;
            using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(8));
            var released = await lab.RecoverAsync(owner, state.SourcePath,
                () => Task.FromResult(manager.GetTranscodingJob(session) is null),
                deadline.Token).ConfigureAwait(false);
            if (!released || !Retire(session, state))
                return;
            services.GetRequiredService<ILogger<PrivateLiveRecovery>>()
                .LogInformation("VIVOAPK_PRIVATE_NATIVE_STOP_RETIRED=PASS");
        }
        finally
        {
            // If native stop was interrupted, the original binding remains
            // fail-closed with respect to negotiation and retains its watchdog.
            if (_states.TryGetValue(session, out var retained) && ReferenceEquals(retained, state))
                lock (state.Gate)
                    state.Recovering = false;
        }
    }

    private static bool ClientInactive(ISessionManager sessions, State state, out bool missing)
    {
        // Exact owner/device/item. A different account or playing item
        // cannot make this lease appear active or authorize its cleanup.
        var candidates = sessions.Sessions.Where(s => s.UserId == state.Owner &&
            string.Equals(s.DeviceId, state.Device, StringComparison.Ordinal) &&
            s.NowPlayingItem?.Id == state.Item).Take(2).ToArray();
        missing = candidates.Length == 0;
        if (missing)
            return true;
        if (candidates.Length != 1)
            return false;
        var client = candidates[0];
        return !client.PlayState.IsPaused && client.LastPlaybackCheckIn != default
            && (DateTime.UtcNow - client.LastPlaybackCheckIn).TotalMilliseconds >= IdleMs;
    }

    private async Task RecoverAsync(ITranscodeManager manager, ISessionManager sessions,
        PrivateNativeHls lab, string session, State state, ILogger logger,
        CancellationToken stopping)
    {
        try
        {
            while (true)
            {
                await Task.Delay(1000, stopping).ConfigureAwait(false);
                if (!_states.TryGetValue(session, out var currentState) || !ReferenceEquals(currentState, state))
                    return;
                lock (state.Gate)
                {
                    if (state.Recovering || state.InFlight != 0
                        || Environment.TickCount64 - state.LastMedia < IdleMs)
                        continue;
                }
                if (!ClientInactive(sessions, state, out _))
                    continue;
                var current = manager.GetTranscodingJob(session);
                if (current is not null &&
                    (current.Type != TranscodingJobType.Hls || string.IsNullOrEmpty(current.Path)
                        || current.MediaSource?.Path != state.SourcePath
                        || current.IsUserPaused || current.ActiveRequestCount > 0
                        || !await lab.MatchesJobAsync(state.Owner, current, state.Device, session, stopping)
                            .ConfigureAwait(false)))
                    continue;
                lock (state.Gate)
                {
                    // Recheck after awaiting the exact source/owner test.
                    // Admission remains reserved throughout retirement.
                    if (state.Recovering || state.InFlight != 0
                        || Environment.TickCount64 - state.LastMedia < IdleMs
                        || !ReferenceEquals(manager.GetTranscodingJob(session), current)
                        || (current is not null && state.Job is not null
                            && !ReferenceEquals(state.Job, current)
                            && (!state.Finite || state.Job.Path != current.Path)))
                        continue;
                    state.Recovering = true;
                }
                bool retired = false;
                try
                {
                    var neverStarted = current is null && state.Job is null;
                    bool missingClient = false;
                    var released = await lab.RecoverAsync(state.Owner, state.SourcePath, async () =>
                    {
                        var latest = manager.GetTranscodingJob(session);
                        // Refuse all ambiguous replacements. A native job
                        // appearing after the unused-negotiation check
                        // cannot be killed or have its source retired.
                        if (!ReferenceEquals(latest, current)
                            || !ClientInactive(sessions, state, out missingClient)
                            || (latest is not null && (latest.IsUserPaused || latest.ActiveRequestCount > 0)))
                            return false;
                        if (latest is not null)
                        {
                            var path = latest.Path!;
                            var wasRunning = !latest.HasExited;
                            await manager.KillTranscodingJobs(state.Device, session,
                                candidate => string.Equals(candidate, path, StringComparison.Ordinal))
                                .ConfigureAwait(false);
                            var removed = manager.GetTranscodingJob(session) is null;
                            logger.LogInformation("VIVOAPK_PRIVATE_NATIVE_RECOVERY_JOB runningBefore={Running} removed={Removed}",
                                wasRunning, removed);
                            return removed;
                        }
                        return manager.GetTranscodingJob(session) is null;
                    }, stopping).ConfigureAwait(false);
                    if (released)
                    {
                        if (!Retire(session, state))
                            throw new InvalidOperationException("Private recovery binding changed");
                        retired = true;
                        if (neverStarted)
                            logger.LogInformation("VIVOAPK_PRIVATE_NATIVE_UNUSED_NEGOTIATION=PASS");
                        else if (missingClient)
                            logger.LogInformation("VIVOAPK_PRIVATE_NATIVE_MISSING_CLIENT_RECOVERY=PASS");
                        logger.LogInformation("VIVOAPK_PRIVATE_NATIVE_RECOVERY_LEASE=PASS");
                        return;
                    }
                }
                finally
                {
                    if (!retired)
                        lock (state.Gate)
                            state.Recovering = false;
                }
            }
        }
        catch (OperationCanceledException) when (stopping.IsCancellationRequested) { }
        catch (Exception ex)
        {
            logger.LogWarning("VIVOAPK_PRIVATE_NATIVE_RECOVERY=FAIL ({ErrorType})", ex.GetType().Name);
        }
    }
}
