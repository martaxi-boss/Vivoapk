#if VIVOAPK_NATIVE_PLAYBACK_LAB
using MediaBrowser.Controller.Channels;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Library;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Controllers;
using Microsoft.AspNetCore.Mvc.Filters;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

public sealed class NativePlaybackLabFilter(
    ILibraryManager library, IUserManager users, IEnumerable<IChannel> channels) : IAsyncActionFilter
{
    public async Task OnActionExecutionAsync(ActionExecutingContext context, ActionExecutionDelegate next)
    {
        if (!NativePlaybackLab.Enabled ||
            context.ActionDescriptor is not ControllerActionDescriptor action ||
            action.ControllerTypeInfo.FullName is not (
                "Jellyfin.Api.Controllers.VideosController" or
                "Jellyfin.Api.Controllers.DynamicHlsController" or
                "Jellyfin.Api.Controllers.HlsSegmentController" or
                "Jellyfin.Api.Controllers.MediaInfoController") ||
            !context.ActionArguments.TryGetValue("itemId", out var rawId) || rawId is not Guid id)
        {
            await next().ConfigureAwait(false);
            return;
        }
        var indexed = library.GetItemById(id);
        var channel = indexed is null || indexed.ChannelId == Guid.Empty ? null :
            channels.OfType<VivoapkLivePreviewChannel>().FirstOrDefault(c =>
                library.GetNewItemId("Channel " + c.Name, typeof(Channel)) == indexed.ChannelId);
        if (channel is null)
        {
            await next().ConfigureAwait(false);
            return;
        }
        var owner = NativePlaybackLab.AuthenticatedOwner(context.HttpContext.User);
        var user = owner == Guid.Empty ? null : users.GetUserById(owner);
        var item = user is null ? null : library.GetItemById<BaseItem>(id, user);
        if (user is null || item is null ||
            item.GetPlayAccess(user) != PlayAccess.Full || !item.IsVisible(user) ||
            !NativePlaybackLab.IsFixtureAccount(user.Username) || !channel.OwnsActiveSnapshot(owner))
        {
            context.Result = new NotFoundResult();
            return;
        }
        await next().ConfigureAwait(false);
    }
}
#endif
