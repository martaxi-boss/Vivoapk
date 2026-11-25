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
            // Fixed lab accounts; retired IDs stay denied for this process.
            return _states.Count < 2 && !_states.Values.Any(s => s.Owner == owner)
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

    internal async Task HandleAsync(HttpContext http, RequestDelegate downstream,
        Guid owner, Guid item, ILogger logger)
    {
        var values = http.Request.Query["PlaySessionId"];
        var device = http.User.FindFirst("Jellyfin-DeviceId")?.Value;
        if (values.Count != 1 || !Guid.TryParse(values[0], out _) ||
            string.IsNullOrEmpty(device) ||
            !_states.TryGetValue(values[0]!, out var state) ||
            state.Owner != owner || state.Item != item || state.Device != device)
        {
            Deny(http);
            return;
        }
        var session = values[0]!;
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
                    !ReferenceEquals(state.Job, existing)))
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

    private static async Task RecoverAsync(ITranscodeManager manager, ISessionManager sessions,
        PrivateHlsNativeLab lab, string session, State state, ILogger logger,
        CancellationToken stopping)
    {
        try
        {
            while (true)
            {
                await Task.Delay(1000, stopping).ConfigureAwait(false);
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
