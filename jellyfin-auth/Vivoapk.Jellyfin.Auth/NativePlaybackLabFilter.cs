#if VIVOAPK_NATIVE_PLAYBACK_LAB
using System.Collections.Concurrent;
using System.Globalization;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Controller.Channels;
using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Controller.Session;
using Microsoft.Extensions.Hosting;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Library;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// Disposable synthetic fixture only. Enforce authorization before the media
/// endpoint pipeline, rather than assuming every HLS response runs an MVC
/// action filter. Not compiled into the normal plugin.
/// </summary>
public sealed class NativePlaybackLabFilter : IStartupFilter
{
    // Uses the existing private entitlement endpoint; no new backend or cache.
    // Only compiled in the disposable synthetic laboratory.
    private static readonly MegaResolverClient Entitlements = new();
    // The experiment is compiled only into the disposable fixture plugin.
    // No filesystem sweep, global cache deletion, external process or service.
    private const long FixtureIdleMilliseconds = 80000;
    private readonly ConcurrentDictionary<string, FixtureRecovery> _recoveries = new(StringComparer.OrdinalIgnoreCase);

    private sealed class FixtureRecovery(TranscodingJob job)
    {
        internal readonly TranscodingJob Job = job;
        internal long LastMediaRequest = Environment.TickCount64;
    }

    private static bool MatchesFixtureJob(TranscodingJob? job, string device, string session)
    {
        return job is not null && job.Type == TranscodingJobType.Hls && !string.IsNullOrWhiteSpace(job.Path) &&
            string.Equals(job.DeviceId, device, StringComparison.Ordinal) &&
            string.Equals(job.PlaySessionId, session, StringComparison.OrdinalIgnoreCase) &&
            Uri.TryCreate(job.MediaSource?.Path, UriKind.Absolute, out var origin) &&
            origin.IsLoopback && origin.Scheme == "http" && origin.Port >= 1024 &&
            origin.AbsolutePath == "/fixture/native.ts" &&
            origin.UserInfo.Length == 0 && origin.Query.Length == 0 && origin.Fragment.Length == 0;
    }

    private void ObserveFixtureSegment(HttpContext http, Guid owner, Guid itemId, ILogger logger)
    {
        if (Environment.GetEnvironmentVariable("VIVOAPK_CI_LONG_CACHE") != "1" ||
            !HttpMethods.IsGet(http.Request.Method) || http.Response.StatusCode != 200 ||
            !(http.Request.Path.Value ?? "").Contains("/hls1/", StringComparison.OrdinalIgnoreCase))
            return;

        var values = http.Request.Query["PlaySessionId"];
        var device = http.User.FindFirst("Jellyfin-DeviceId")?.Value;
        if (values.Count != 1 || !Guid.TryParse(values[0], out _) ||
            string.IsNullOrWhiteSpace(device))
            return;

        var session = values[0]!;
        var manager = http.RequestServices.GetRequiredService<ITranscodeManager>();
        var job = manager.GetTranscodingJob(session);
        if (!MatchesFixtureJob(job, device, session))
            return;

        var state = new FixtureRecovery(job!);
        var existing = _recoveries.GetOrAdd(session, state);
        if (!ReferenceEquals(existing.Job, job))
            return; // Ambiguous or replaced job: never recover.
        Interlocked.Exchange(ref existing.LastMediaRequest, Environment.TickCount64);
        if (!ReferenceEquals(existing, state))
            return;

        logger.LogInformation("Native fixture job observed: cleaner={Cleaner}, downloadTicks={DownloadTicks}, active={Active}",
            job!.TranscodingSegmentCleaner is not null, job.DownloadPositionTicks, job.ActiveRequestCount);
        var stopping = http.RequestServices.GetRequiredService<IHostApplicationLifetime>().ApplicationStopping;
        var sessions = http.RequestServices.GetRequiredService<ISessionManager>();
        var config = http.RequestServices.GetService<IConfigurationManager>();
        _ = RecoverAbandonedFixtureAsync(manager, sessions, config, session, device, owner, itemId, state, logger, stopping);
    }

    // Read-only diagnostics for the exact v12.4 cleaner prerequisites.
    // No alteration of its timers, configuration, jobs or segment files.
    private static void ObserveNativeCleanerEligibility(TranscodingJob job,
        IConfigurationManager? config, ILogger logger)
    {
        try
        {
            var options = config?.GetEncodingOptions();
            var keep = Math.Max(options?.SegmentKeepSeconds ?? 20, 20);
            var ticks = job.DownloadPositionTicks;
            var cutoff = ticks.GetValueOrDefault() - TimeSpan.FromSeconds(keep).Ticks;
            var eligible = cutoff > 0 ? job.GetHighestServedSegmentIndexEndingAtOrBefore(cutoff) : null;
            var matching = 0;
            var earliest = int.MaxValue;
            var latest = -1;
            var scanned = 0;
            var path = job.Path!;
            var root = Environment.GetEnvironmentVariable("RUNNER_TEMP");
            var directory = Path.GetDirectoryName(path);
            var stem = Path.GetFileNameWithoutExtension(path);
            // Restrict enumeration to the disposable runner and this observed
            // playlist's own segment namespace. Never output paths or names.
            if (root is not null && directory is not null &&
                Path.GetFullPath(directory).StartsWith(
                    Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar,
                    StringComparison.Ordinal) && stem.Length > 0 &&
                stem.All(char.IsAsciiHexDigit))
            {
                foreach (var file in Directory.EnumerateFiles(directory, stem + "*.ts").Take(512))
                {
                    scanned++;
                    var name = Path.GetFileNameWithoutExtension(file);
                    if (!name.StartsWith(stem, StringComparison.Ordinal) ||
                        !int.TryParse(name.AsSpan(stem.Length), NumberStyles.None,
                            CultureInfo.InvariantCulture, out var index))
                        continue;
                    earliest = Math.Min(earliest, index);
                    latest = Math.Max(latest, index);
                    if (eligible.HasValue && index >= 0 && index <= eligible.Value)
                        matching++;
                }
            }
            logger.LogInformation("VIVOAPK_LAB_NATIVE_CLEANER_DIAG configAvailable={Config} enabled={Enabled} keepSec={Keep} eligibleIndex={Eligible} matchingOldTs={Matching} scannedTs={Scanned} minIndex={Min} maxIndex={Max} isHls={Hls}",
                options is not null, options?.EnableSegmentDeletion, keep, eligible,
                matching, scanned, earliest == int.MaxValue ? -1 : earliest, latest,
                job.Type == TranscodingJobType.Hls);
        }
        catch (Exception ex)
        {
            logger.LogWarning("VIVOAPK_LAB_NATIVE_CLEANER_DIAG_ERROR ({ErrorType})", ex.GetType().Name);
        }
    }

    private async Task RecoverAbandonedFixtureAsync(ITranscodeManager manager, ISessionManager sessions,
        IConfigurationManager? config, string session,
        string device, Guid owner, Guid itemId, FixtureRecovery state, ILogger logger, CancellationToken stopping)
    {
        try
        {
            // Bounded observation of this exact finite fixture, not a general
            // playback watchdog. Only the synthetic job may be recovered while
            // FFmpeg is still running, after both genuine idle checks pass.
            var started = Environment.TickCount64;
            var lastDiagnostic = started - 20000;
            while (Environment.TickCount64 - started < 240000)
            {
                await Task.Delay(1000, stopping).ConfigureAwait(false);
                var job = manager.GetTranscodingJob(session);
                if (!ReferenceEquals(job, state.Job) || !MatchesFixtureJob(job, device, session))
                    return;
                // Native automatic progress may keep LastPingDate fresh even
                // after the client vanished. Use the actual client check-in
                // of the uniquely matched synthetic account/device/item.
                var matches = sessions.Sessions.Where(candidate =>
                    candidate.UserId == owner && string.Equals(candidate.DeviceId, device, StringComparison.Ordinal)
                    && candidate.NowPlayingItem?.Id == itemId).Take(2).ToArray();
                var client = matches.Length == 1 ? matches[0] : null;
                var actualIdle = client is null || client.LastPlaybackCheckIn == default
                    ? -1d : (DateTime.UtcNow - client.LastPlaybackCheckIn).TotalMilliseconds;
                var mediaIdle = Environment.TickCount64 - Interlocked.Read(ref state.LastMediaRequest);
                if (Environment.TickCount64 - lastDiagnostic >= 20000)
                {
                    lastDiagnostic = Environment.TickCount64;
                    logger.LogInformation("VIVOAPK_LAB_NATIVE_JOB_STATE cleaner={Cleaner} ticks={Ticks} active={Active} exited={Exited} paused={Paused} matchedClients={Matches} realIdleMs={RealIdle} mediaIdleMs={MediaIdle} pingIdleMs={PingIdle}",
                        job!.TranscodingSegmentCleaner is not null, job.DownloadPositionTicks, job.ActiveRequestCount,
                        job.HasExited, job.IsUserPaused, matches.Length, (long)actualIdle, mediaIdle,
                        (long)(DateTime.UtcNow - job.LastPingDate).TotalMilliseconds);
                    ObserveNativeCleanerEligibility(job, config, logger);
                }
                if (client is null || client.PlayState.IsPaused || actualIdle < FixtureIdleMilliseconds ||
                    mediaIdle < FixtureIdleMilliseconds || job!.IsUserPaused || job.ActiveRequestCount > 0)
                    continue;

                // The native operation is scoped to the observed job's own
                // play-session ID and exact native playlist. No other path is
                // authorized for deletion. This is intentionally lab-only.
                var path = job.Path!;
                if (!ReferenceEquals(manager.GetTranscodingJob(session), state.Job))
                    return;
                if (client.UserId != owner || !string.Equals(client.DeviceId, device, StringComparison.Ordinal) ||
                    client.NowPlayingItem?.Id != itemId || client.PlayState.IsPaused || job.IsUserPaused ||
                    (DateTime.UtcNow - client.LastPlaybackCheckIn).TotalMilliseconds < FixtureIdleMilliseconds ||
                    Environment.TickCount64 - Interlocked.Read(ref state.LastMediaRequest) < FixtureIdleMilliseconds ||
                    job.ActiveRequestCount > 0)
                    continue;
                var exitedBeforeRecovery = job.HasExited;
                // Scope remains the known synthetic fixture, exact observed job,
                // native play-session ID, and playlist. No direct process/file operation.
                await manager.KillTranscodingJobs(device, session,
                    candidate => string.Equals(candidate, path, StringComparison.Ordinal)).ConfigureAwait(false);
                logger.LogInformation("VIVOAPK_LAB_NATIVE_ABANDONED_SESSION_RECOVERY invoked=true exitedBeforeRecovery={ExitedBefore} jobRemoved={Removed}",
                    exitedBeforeRecovery, manager.GetTranscodingJob(session) is null);
                return;
            }
            logger.LogInformation("VIVOAPK_LAB_NATIVE_ABANDONED_SESSION_RECOVERY=NOT_ELIGIBLE");
        }
        catch (OperationCanceledException) when (stopping.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            // Do not print exception text that might contain paths or tokens.
            logger.LogWarning("VIVOAPK_LAB_NATIVE_ABANDONED_SESSION_RECOVERY=FAIL ({ErrorType})", ex.GetType().Name);
        }
        finally
        {
            _recoveries.TryRemove(session, out _);
        }
    }

    public Action<IApplicationBuilder> Configure(Action<IApplicationBuilder> next)
    {
        return app =>
        {
            app.Use(async (HttpContext http, RequestDelegate downstream) =>
            {
                if (!NativePlaybackLab.Enabled)
                {
                    await downstream(http).ConfigureAwait(false);
                    return;
                }

                var parts = (http.Request.Path.Value ?? "")
                    .Split('/', StringSplitOptions.RemoveEmptyEntries);
                var media = parts.Length >= 2 &&
                    (parts[0].Equals("Videos", StringComparison.OrdinalIgnoreCase) ||
                     parts[0].Equals("Audio", StringComparison.OrdinalIgnoreCase));
                var info = parts.Length >= 3 &&
                    parts[0].Equals("Items", StringComparison.OrdinalIgnoreCase) &&
                    parts[2].Equals("PlaybackInfo", StringComparison.OrdinalIgnoreCase);
                if (!media && !info)
                {
                    await downstream(http).ConfigureAwait(false);
                    return;
                }

                // Fixture-only response marker proves that the guard actually
                // ran, independent of a successful or unsuccessful core action.
                var services = http.RequestServices;
                var logger = services.GetRequiredService<ILogger<NativePlaybackLabFilter>>();
                // Native clients can use a header or the server-generated HLS
                // query token. Never infer identity from an item or play session.
                // Reject missing, duplicate or ambiguous authentication sources
                // before invoking the native authentication handler.
                if (!HasExplicitNativeCredential(http.Request))
                {
                    Reject(http, logger);
                    return;
                }

                var schemes = services.GetRequiredService<IAuthenticationSchemeProvider>();
                var scheme = await schemes.GetDefaultAuthenticateSchemeAsync().ConfigureAwait(false);
                if (scheme is null || !Guid.TryParse(parts[1], out var id))
                {
                    Reject(http, logger);
                    return;
                }

                // This startup middleware precedes normal endpoint dispatch.
                // Use the server's own authentication handler and policy; never
                // accept a user id or play session supplied by the request as identity.
                var auth = await http.AuthenticateAsync(scheme.Name).ConfigureAwait(false);
                if (!auth.Succeeded || auth.Principal is null)
                {
                    Reject(http, logger);
                    return;
                }
                http.User = auth.Principal;
                var owner = NativePlaybackLab.AuthenticatedOwner(http.User);
                var policies = services.GetRequiredService<IAuthorizationPolicyProvider>();
                var authorization = services.GetRequiredService<IAuthorizationService>();
                var policy = await policies.GetDefaultPolicyAsync().ConfigureAwait(false);
                if (owner == Guid.Empty ||
                    !(await authorization.AuthorizeAsync(http.User, http, policy.Requirements)
                        .ConfigureAwait(false)).Succeeded)
                {
                    Reject(http, logger);
                    return;
                }

                var users = services.GetRequiredService<IUserManager>();
                var library = services.GetRequiredService<ILibraryManager>();
                var user = users.GetUserById(owner);
                var item = user is null ? null : library.GetItemById<BaseItem>(id, user);
                if (user is null || item is null || !NativePlaybackLab.IsFixtureAccount(user.Username) ||
                    item.IsFolder || item.GetPlayAccess(user) != PlayAccess.Full || !item.IsVisible(user))
                {
                    Reject(http, logger);
                    return;
                }
                var channel = services.GetServices<IChannel>().OfType<VivoapkLivePreviewChannel>()
                    .FirstOrDefault(c =>
                        library.GetNewItemId("Channel " + c.Name, typeof(Channel)) == item.ChannelId);
                if (channel is null || !channel.OwnsActiveSnapshot(owner))
                {
                    Reject(http, logger);
                    return;
                }

                // Cached HLS playlists and segments must not survive a revoked
                // entitlement. Reuse the existing private status check, fail closed
                // on unavailable/expired/disabled, and recheck snapshot ownership.
                if (await Entitlements.CheckEntitlementAsync(user.Username, http.RequestAborted)
                        .ConfigureAwait(false) != EntitlementState.Active ||
                    !channel.OwnsActiveSnapshot(owner))
                {
                    Reject(http, logger);
                    return;
                }

                http.Response.Headers.CacheControl = "no-store";
                http.Response.Headers["X-Vivoapk-Lab-Guard"] = "allowed";
                await downstream(http).ConfigureAwait(false);
                if (media)
                    ObserveFixtureSegment(http, owner, id, logger);
            });
            next(app);
        };
    }

    private static bool HasExplicitNativeCredential(HttpRequest request)
    {
        var queryKeys = request.Query.Keys.Where(IsAuthenticationQueryKey).ToArray();
        var hasHeader = request.Headers.TryGetValue("X-Emby-Token", out var headerToken);
        if (queryKeys.Length == 0)
            return hasHeader && headerToken.Count == 1 &&
                !string.IsNullOrWhiteSpace(headerToken[0]) &&
                !request.Headers.ContainsKey("X-MediaBrowser-Token");

        if (queryKeys.Length != 1 || hasHeader ||
            request.Headers.ContainsKey("Authorization") ||
            request.Headers.ContainsKey("X-MediaBrowser-Token"))
            return false;
        var key = queryKeys[0];
        if (!key.Equals("ApiKey", StringComparison.OrdinalIgnoreCase) &&
            !key.Equals("api_key", StringComparison.OrdinalIgnoreCase))
            return false;
        var value = request.Query[key];
        return value.Count == 1 && !string.IsNullOrWhiteSpace(value[0]);
    }

    private static bool IsAuthenticationQueryKey(string key)
    {
        var canonical = new string(key.Where(char.IsAsciiLetterOrDigit).ToArray()).ToLowerInvariant();
        return canonical is "apikey" or "accesstoken" or "token" or "xembytoken" or "xmediabrowsertoken";
    }

    private static void Reject(HttpContext http, ILogger logger)
    {
        logger.LogInformation("Native playback laboratory: access refused");
        http.Response.Headers["X-Vivoapk-Lab-Guard"] = "denied";
        http.Response.Headers.CacheControl = "no-store";
        http.Response.StatusCode = StatusCodes.Status404NotFound;
        http.Response.ContentLength = 0;
    }
}
#endif
