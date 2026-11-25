#if VIVOAPK_NATIVE_PLAYBACK_LAB
using MediaBrowser.Controller.Channels;
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

                http.Response.Headers["X-Vivoapk-Lab-Guard"] = "allowed";
                await downstream(http).ConfigureAwait(false);
            });
            next(app);
        };
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
