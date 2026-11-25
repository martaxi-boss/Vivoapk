#if VIVOAPK_NATIVE_PLAYBACK_LAB
using System.Collections.Concurrent;
using System.Text.Json.Nodes;
using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Controller.Session;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>Two-account disposable experiment, not production recovery.
/// Only the negotiated, authorized owner/device/item/session and exact
/// observed native job can be recovered. No process scanning or file deletion.</summary>
internal sealed class PrivateLiveRecovery
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
    private sealed class State(Guid owner, Guid item, string device)
    {
        internal readonly Guid Owner = owner;
        internal readonly Guid Item = item;
        internal readonly string Device = device;
        internal readonly object Gate = new();
        internal long LastMedia = Environment.TickCount64;
        internal int InFlight;
        internal bool Recovering;
        internal bool Watching;
        internal TranscodingJob? Job;
    }

    internal bool Bind(JsonNode node, Guid owner, Guid item, string? device)
    {
        if (string.IsNullOrEmpty(device) || node is not JsonObject response
            || response["PlaySessionId"] is not JsonValue value
            || !value.TryGetValue<string>(out var session) || !Guid.TryParse(session, out _))
            return false;
        lock (_bindGate)
        {
            // Only an admitted negotiation can publish a new native GUID.
            // Retired IDs have no binding; their old cache stems do not match
            // the new random private source and observed job.
            return _negotiating.Contains(owner) && _states.Count < 2
                && !_states.Values.Any(s => s.Owner == owner)
                && _states.TryAdd(session, new State(owner, item, device));
        }
    }

    private static void Deny(HttpContext http)
    {
        http.Response.Headers["X-Vivoapk-Lab-Guard"] = "denied";
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
        var nativePlaylist = parts.Length == 3 && parts[0].Equals("Videos", StringComparison.OrdinalIgnoreCase)
            && (parts[2].Equals("master.m3u8", StringComparison.OrdinalIgnoreCase)
                || parts[2].Equals("live.m3u8", StringComparison.OrdinalIgnoreCase));
        if (string.IsNullOrEmpty(device) || (!legacy && !nativePlaylist))
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
        // If present, native caller-controlled device/source selectors must
        // agree with the already authenticated identity.
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
        var lab = services.GetRequiredService<PrivateHlsNativeLab>();
        var existing = manager.GetTranscodingJob(session);
        if (existing is not null && !await lab.MatchesJobAsync(owner, existing, device,
                session, http.RequestAborted).ConfigureAwait(false))
        {
            Deny(http);
            return;
        }
        lock (state.Gate)
        {
            if (state.Recovering || (state.Job is not null &&
                    !ReferenceEquals(state.Job, existing))
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
            bool start = false;
            lock (state.Gate)
            {
                state.Job ??= job;
                if (!ReferenceEquals(state.Job, job))
                    return;
                state.LastMedia = Environment.TickCount64;
                if (!state.Watching)
                {
                    state.Watching = true;
                    start = true;
                }
            }
            if (start)
            {
                var stopping = services.GetRequiredService<IHostApplicationLifetime>().ApplicationStopping;
                var sessions = services.GetRequiredService<ISessionManager>();
                _ = RecoverAsync(manager, sessions, lab, session, state, logger, stopping);
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
        var lab = services.GetRequiredService<PrivateHlsNativeLab>();
        var job = manager.GetTranscodingJob(session);
        if (job is null || !ReferenceEquals(job, state.Job) ||
            string.IsNullOrEmpty(job.MediaSource?.Path) ||
            !await lab.MatchesJobAsync(owner, job, device, session, http.RequestAborted).ConfigureAwait(false))
        {
            Deny(http);
            return;
        }
        lock (state.Gate)
        {
            if (state.Recovering || state.InFlight != 0 || !ReferenceEquals(state.Job, job))
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
            var released = await lab.RecoverAsync(owner, job.MediaSource!.Path!,
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

    private async Task RecoverAsync(ITranscodeManager manager, ISessionManager sessions,
        PrivateHlsNativeLab lab, string session, State state, ILogger logger,
        CancellationToken stopping)
    {
        try
        {
            while (true)
            {
                await Task.Delay(1000, stopping).ConfigureAwait(false);
                if (!_states.TryGetValue(session, out var currentState) || !ReferenceEquals(currentState, state))
                    return;
                var job = state.Job;
                if (job is null || string.IsNullOrEmpty(job.Path) ||
                    string.IsNullOrEmpty(job.MediaSource?.Path))
                    return;
                var current = manager.GetTranscodingJob(session);
                if (current is not null && !ReferenceEquals(current, job))
                    return;
                var candidates = sessions.Sessions.Where(s => s.UserId == state.Owner &&
                    string.Equals(s.DeviceId, state.Device, StringComparison.Ordinal) &&
                    s.NowPlayingItem?.Id == state.Item).Take(2).ToArray();
                if (candidates.Length != 1)
                    continue;
                var client = candidates[0];
                if (client.LastPlaybackCheckIn == default || client.PlayState.IsPaused ||
                    job.IsUserPaused || job.ActiveRequestCount > 0 ||
                    (DateTime.UtcNow - client.LastPlaybackCheckIn).TotalMilliseconds < IdleMs)
                    continue;
                lock (state.Gate)
                {
                    if (state.Recovering || state.InFlight != 0 ||
                        Environment.TickCount64 - state.LastMedia < IdleMs)
                        continue;
                    state.Recovering = true;
                }
                var path = job.Path!;
                var source = job.MediaSource!.Path!;
                var released = await lab.RecoverAsync(state.Owner, source, async () =>
                {
                    var latest = manager.GetTranscodingJob(session);
                    if (latest is not null && !ReferenceEquals(latest, job))
                        return false;
                    if (client.UserId != state.Owner || client.DeviceId != state.Device ||
                        client.NowPlayingItem?.Id != state.Item || client.PlayState.IsPaused ||
                        (DateTime.UtcNow - client.LastPlaybackCheckIn).TotalMilliseconds < IdleMs ||
                        job.IsUserPaused || job.ActiveRequestCount > 0)
                        return false;
                    var wasRunning = latest is not null && !job.HasExited;
                    if (latest is not null)
                        await manager.KillTranscodingJobs(state.Device, session,
                            candidate => string.Equals(candidate, path, StringComparison.Ordinal)).ConfigureAwait(false);
                    var removed = manager.GetTranscodingJob(session) is null;
                    logger.LogInformation("VIVOAPK_PRIVATE_NATIVE_RECOVERY_JOB runningBefore={Running} removed={Removed}",
                        wasRunning, removed);
                    return removed;
                }, stopping).ConfigureAwait(false);
                if (released)
                {
                    if (!Retire(session, state))
                        throw new InvalidOperationException("Private recovery binding changed");
                    logger.LogInformation("VIVOAPK_PRIVATE_NATIVE_RECOVERY_LEASE=PASS");
                    return;
                }
                lock (state.Gate)
                    state.Recovering = false;
            }
        }
        catch (OperationCanceledException) when (stopping.IsCancellationRequested) { }
        catch (Exception ex)
        {
            logger.LogWarning("VIVOAPK_PRIVATE_NATIVE_RECOVERY=FAIL ({ErrorType})", ex.GetType().Name);
        }
    }
}
#endif
