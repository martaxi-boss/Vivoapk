#if VIVOAPK_NATIVE_PLAYBACK_LAB
using System.Collections.Concurrent;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
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
using Microsoft.Extensions.Caching.Memory;
using System.Text.RegularExpressions;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// Disposable synthetic fixture only. Enforce authorization before the media
/// endpoint pipeline, rather than assuming every HLS response runs an MVC
/// action filter. Not compiled into the normal plugin.
/// </summary>
public sealed class NativePlaybackLabFilter : IStartupFilter
{
    // The experiment is compiled only into the disposable fixture plugin.
    // No filesystem sweep, global cache deletion, external process or service.
    private const long FixtureIdleMilliseconds = 80000;
    private readonly ConcurrentDictionary<string, FixtureRecovery> _recoveries = new(StringComparer.OrdinalIgnoreCase);

    private readonly PrivateLiveRecovery _privateLive = new("X-Vivoapk-Lab-Guard");

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
                if (PrivateHlsNativeLab.Enabled && HttpMethods.IsPost(http.Request.Method)
                    && (http.Request.Path.Value ?? "").Equals("/Sessions/Playing/Stopped",
                        StringComparison.OrdinalIgnoreCase))
                {
                    await StopPrivateFixtureAsync(http, downstream).ConfigureAwait(false);
                    return;
                }
                if (!media && !info)
                {
                    await downstream(http).ConfigureAwait(false);
                    return;
                }

                // Fixture-only response marker proves that the guard actually
                // ran, independent of a successful or unsuccessful core action.
                var services = http.RequestServices;
                var logger = services.GetRequiredService<ILogger<NativePlaybackLabFilter>>();
                if (!Guid.TryParse(parts[1], out var id))
                {
                    Reject(http, logger);
                    return;
                }
                var owner = await NativePlaybackAuthorization.AuthorizeAsync(
                    http, id, NativePlaybackLab.IsFixtureAccount).ConfigureAwait(false);
                if (owner == Guid.Empty)
                {
                    Reject(http, logger);
                    return;
                }

                http.Response.Headers.CacheControl = "no-store";
                http.Response.Headers["X-Vivoapk-Lab-Guard"] = "allowed";
                if (PrivateHlsNativeLab.Enabled && info)
                    await PrivatePlaybackInfoAsync(http, downstream, owner, id).ConfigureAwait(false);
                else if (PrivateHlsNativeLab.LiveRecoveryEnabled && media)
                    await _privateLive.HandleAsync(http, downstream, owner, id, logger).ConfigureAwait(false);
                else
                    await downstream(http).ConfigureAwait(false);
                if (media)
                    ObserveFixtureSegment(http, owner, id, logger);
            });
            next(app);
        };
    }

    private async Task PrivatePlaybackInfoAsync(HttpContext http, RequestDelegate downstream, Guid owner, Guid item)
    {
        // ChannelManager v12.4 caches dynamic sources for five minutes under
        // the exact ExternalId. The old source is a retired private capability.
        // Admit a single new negotiation BEFORE evicting that one entry.
        // Authorization already confirmed the exact visible item and owner.
        bool reserved = false;
        bool bound = false;
        string? cacheKey = null;
        IMemoryCache? cache = null;
        var services = http.RequestServices;
        var logger = services.GetRequiredService<ILogger<NativePlaybackLabFilter>>();
        if (PrivateHlsNativeLab.LiveRecoveryEnabled)
        {
            var user = services.GetRequiredService<IUserManager>().GetUserById(owner);
            var entity = user is null ? null :
                services.GetRequiredService<ILibraryManager>().GetItemById<BaseItem>(item, user);
            cacheKey = entity?.ExternalId;
            if (cacheKey is null ||
                !Regex.IsMatch(cacheKey, @"\Aslot:[0-9]{1,3}:(?:general|tv):live:(?:10|20):42\z",
                    RegexOptions.CultureInvariant) ||
                !_privateLive.TryBeginNegotiation(owner))
            {
                Reject(http, logger);
                return;
            }
            reserved = true;
            cache = services.GetRequiredService<IMemoryCache>();
        }
        // Only the synthetic PlaybackInfo response is buffered.
        var original = http.Response.Body;
        await using var buffer = new MemoryStream();
        http.Response.Body = buffer;
        try
        {
            if (reserved)
                cache!.Remove(cacheKey!);
            await downstream(http).ConfigureAwait(false);
            if (buffer.Length > 512 * 1024)
                throw new InvalidOperationException("Oversized fixture playback response");
            if (http.Response.StatusCode == 200)
            {
                buffer.Position = 0;
                var node = await JsonNode.ParseAsync(buffer, cancellationToken: http.RequestAborted)
                    .ConfigureAwait(false) ?? throw new InvalidOperationException("Missing playback response");
                PrivatePlaybackResponse.SanitizePlaybackInfo(node);
                if (reserved)
                {
                    if (node is not JsonObject response || response["MediaSources"] is not JsonArray sources
                        || sources.Count != 1 || sources[0] is not JsonObject media
                        || media["TranscodingUrl"] is not JsonValue url || !url.TryGetValue<string>(out var route)
                        || string.IsNullOrEmpty(route) || !_privateLive.Bind(node, owner, item,
                            http.User.FindFirst("Jellyfin-DeviceId")?.Value))
                        throw new InvalidOperationException("Private fixture play session not bound");
                    bound = true;
                }
                var content = JsonSerializer.SerializeToUtf8Bytes(node);
                http.Response.ContentLength = content.Length;
                http.Response.Body = original;
                await original.WriteAsync(content, http.RequestAborted).ConfigureAwait(false);
            }
            else
            {
                buffer.Position = 0;
                http.Response.Body = original;
                await buffer.CopyToAsync(original, http.RequestAborted).ConfigureAwait(false);
            }
        }
        catch (Exception ex) when (ex is InvalidOperationException or JsonException)
        {
            http.Response.Body = original;
            if (http.Response.HasStarted)
                http.Abort();
            else
            {
                http.Response.StatusCode = 404;
                http.Response.ContentLength = 0;
            }
        }
        finally
        {
            http.Response.Body = original;
            if (reserved)
            {
                try
                {
                    if (!bound)
                    {
                        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(8));
                        await services.GetRequiredService<PrivateNativeHls>()
                            .ReleaseUnstartedAsync(owner, deadline.Token).ConfigureAwait(false);
                        cache!.Remove(cacheKey!);
                    }
                }
                catch (Exception ex)
                {
                    logger.LogWarning("VIVOAPK_PRIVATE_NEGOTIATION_RELEASE=FAIL ({Type})", ex.GetType().Name);
                }
                finally { _privateLive.EndNegotiation(owner); }
            }
        }
    }

    private async Task StopPrivateFixtureAsync(HttpContext http, RequestDelegate downstream)
    {
        var logger = http.RequestServices.GetRequiredService<ILogger<NativePlaybackLabFilter>>();
        if (http.Request.ContentLength is not long length || length is < 1 or > 16384)
        {
            Reject(http, logger);
            return;
        }
        Guid item = Guid.Empty;
        string? session = null;
        http.Request.EnableBuffering();
        try
        {
            using var json = await JsonDocument.ParseAsync(http.Request.Body,
                cancellationToken: http.RequestAborted).ConfigureAwait(false);
            var fields = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var prop in json.RootElement.EnumerateObject())
            {
                if (!fields.Add(prop.Name))
                    throw new InvalidOperationException("Duplicate stop field");
                if (prop.Name.Equals("ItemId", StringComparison.OrdinalIgnoreCase))
                    Guid.TryParse(prop.Value.GetString(), out item);
                if (prop.Name.Equals("PlaySessionId", StringComparison.OrdinalIgnoreCase))
                    session = prop.Value.GetString();
            }
        }
        catch (Exception ex) when (ex is JsonException or InvalidOperationException)
        {
            Reject(http, logger);
            return;
        }
        finally { http.Request.Body.Position = 0; }
        if (item == Guid.Empty || session is null || !Guid.TryParse(session, out _))
        {
            Reject(http, logger);
            return;
        }
        var owner = await NativePlaybackAuthorization.AuthorizeAsync(http, item,
            NativePlaybackLab.IsFixtureAccount).ConfigureAwait(false);
        if (PrivateHlsNativeLab.LiveRecoveryEnabled)
        {
            if (owner == Guid.Empty)
            {
                Reject(http, logger);
                return;
            }
            await _privateLive.StopAsync(http, downstream, owner, item, session).ConfigureAwait(false);
            return;
        }
        var lab = http.RequestServices.GetRequiredService<PrivateNativeHls>();
        var jobs = http.RequestServices.GetRequiredService<ITranscodeManager>();
        if (owner == Guid.Empty || !await lab.MatchesJobAsync(owner, jobs.GetTranscodingJob(session),
                http.User.FindFirst("Jellyfin-DeviceId")?.Value, session, http.RequestAborted).ConfigureAwait(false))
        {
            Reject(http, logger);
            return;
        }
        await downstream(http).ConfigureAwait(false);
        if (http.Response.StatusCode is 200 or 204)
            await lab.CloseAsync(owner, http.RequestAborted).ConfigureAwait(false);
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
