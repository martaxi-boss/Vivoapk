#if VIVOAPK_NATIVE_PLAYBACK_LAB
using MediaBrowser.Controller.Channels;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Library;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>Disposable fixture guard. Never deploy this experimental build.</summary>
public sealed class NativePlaybackLabFilter(
    ILibraryManager library, IUserManager users, IEnumerable<IChannel> channels,
    ILogger<NativePlaybackLabFilter> logger) : IAsyncActionFilter
{
    public async Task OnActionExecutionAsync(ActionExecutingContext context, ActionExecutionDelegate next)
    {
        if (!NativePlaybackLab.Enabled)
        {
            await next().ConfigureAwait(false);
            return;
        }

        // Classify the incoming media route instead of allowing a missing
        // model-binding value or a different controller to bypass the guard.
        // This laboratory has only synthetic accounts and synthetic channel items.
        var parts = (context.HttpContext.Request.Path.Value ?? "")
            .Split('/', StringSplitOptions.RemoveEmptyEntries);
        var media = parts.Length >= 2 &&
            (parts[0].Equals("Videos", StringComparison.OrdinalIgnoreCase) ||
             parts[0].Equals("Audio", StringComparison.OrdinalIgnoreCase));
        var info = parts.Length >= 3 &&
            parts[0].Equals("Items", StringComparison.OrdinalIgnoreCase) &&
            parts[2].Equals("PlaybackInfo", StringComparison.OrdinalIgnoreCase);
        if (!media && !info)
        {
            await next().ConfigureAwait(false);
            return;
        }

        context.Result = new NotFoundResult();
        var owner = NativePlaybackLab.AuthenticatedOwner(context.HttpContext.User);
        if (owner == Guid.Empty || !Guid.TryParse(parts[1], out var id))
        {
            logger.LogInformation("Native playback laboratory: rejected anonymous or invalid request");
            return;
        }
        var user = users.GetUserById(owner);
        var item = user is null ? null : library.GetItemById<BaseItem>(id, user);
        if (user is null || item is null || !NativePlaybackLab.IsFixtureAccount(user.Username) ||
            item.GetPlayAccess(user) != PlayAccess.Full || !item.IsVisible(user))
        {
            logger.LogInformation("Native playback laboratory: rejected inaccessible item");
            return;
        }
        var channel = channels.OfType<VivoapkLivePreviewChannel>().FirstOrDefault(c =>
            library.GetNewItemId("Channel " + c.Name, typeof(Channel)) == item.ChannelId);
        if (channel is null || !channel.OwnsActiveSnapshot(owner))
        {
            logger.LogInformation("Native playback laboratory: rejected non-owned channel");
            return;
        }

        // No request-supplied playback session, source id or route can substitute
        // for the checked channel's owner. These are further acceptance tests
        // required before implementing the production input path.
        context.Result = null;
        await next().ConfigureAwait(false);
    }
}
#endif
