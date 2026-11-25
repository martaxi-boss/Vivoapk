using System.Security.Claims;
using MediaBrowser.Controller.Channels;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Library;
using MediaBrowser.Model.MediaInfo;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Controllers;
using Microsoft.AspNetCore.Mvc.Filters;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// Experimental adapter for native static video actions. Only handles
/// this plugin's items, subject to normal Jellyfin authorization and fresh
/// provider-issued grants. No media transport and no insecure fallback.
/// </summary>
public sealed class NativeStaticRedirectFilter(
    ILibraryManager library,
    IUserManager users,
    IEnumerable<IChannel> channels,
    IAuthorizationService authorization,
    IAuthorizationPolicyProvider policies) : IAsyncActionFilter
{
    internal static Guid AuthenticatedOwner(ClaimsPrincipal? principal)
    {
        if (principal?.Identity?.IsAuthenticated != true ||
            !bool.TryParse(principal.FindFirst("Jellyfin-IsApiKey")?.Value, out var isApiKey) ||
            isApiKey ||
            !Guid.TryParse(principal.FindFirst("Jellyfin-UserId")?.Value, out var owner))
            return Guid.Empty;
        return owner;
    }

    public async Task OnActionExecutionAsync(ActionExecutingContext context,
        ActionExecutionDelegate next)
    {
        if (Plugin.Instance?.Configuration.RequireAuthorizedPlaybackGrant != true ||
            context.ActionDescriptor is not ControllerActionDescriptor action ||
            action.ControllerTypeInfo.FullName != "Jellyfin.Api.Controllers.VideosController" ||
            action.MethodInfo.Name is not ("GetVideoStream" or "GetVideoStreamByContainer") ||
            !context.ActionArguments.TryGetValue("itemId", out var rawId) ||
            rawId is not Guid itemId)
        {
            await next().ConfigureAwait(false);
            return;
        }

        // Unscoped lookup only identifies whether the item belongs to the
        // plugin. It never authorizes playback or returns item metadata.
        var indexed = library.GetItemById(itemId);
        var channel = indexed is null || indexed.ChannelId == Guid.Empty ? null :
            channels.OfType<VivoapkLivePreviewChannel>().FirstOrDefault(c =>
                library.GetNewItemId("Channel " + c.Name, typeof(Channel)) == indexed.ChannelId);
        if (channel is null)
        {
            await next().ConfigureAwait(false);
            return;
        }

        var http = context.HttpContext;
        context.Result = new NotFoundResult();
        var owner = AuthenticatedOwner(http.User);
        if (owner == Guid.Empty)
            return;
        var policy = await policies.GetDefaultPolicyAsync().ConfigureAwait(false);
        if (!(await authorization.AuthorizeAsync(http.User, http, policy.Requirements)
                .ConfigureAwait(false)).Succeeded)
            return;
        var user = users.GetUserById(owner);
        var item = user is null ? null : library.GetItemById<BaseItem>(itemId, user);
        if (user is null || item is null || item.IsFolder ||
            item.GetPlayAccess(user) != PlayAccess.Full ||
            !item.IsVisible(user) ||
            !channel.IsEnabledFor(owner.ToString("N")) ||
            string.IsNullOrEmpty(item.ExternalId))
            return;

        // No fallback to the remote-body handler, including if the grant is
        // missing. Reject unsupported requests rather than reinterpreting them.
        context.Result = new BadRequestResult();
        if (!context.ModelState.IsValid ||
            !(HttpMethods.IsGet(http.Request.Method) || HttpMethods.IsHead(http.Request.Method)) ||
            !context.ActionArguments.TryGetValue("static", out var rawStatic) ||
            rawStatic is not true || HasValue(context, "params") ||
            HasValue(context, "liveStreamId") ||
            (context.ActionArguments.TryGetValue("startTimeTicks", out var start) &&
                start is long ticks && ticks != 0))
            return;

        // Fetch a current, item-scoped grant instead of using the 5-minute
        // core ChannelManager source cache.
        var sources = (await channel.GetFreshAuthorizedMediaSourcesAsync(
            item.ExternalId, http.RequestAborted).ConfigureAwait(false)).ToArray();
        var requested = context.ActionArguments.TryGetValue("mediaSourceId", out var rawSource)
            ? rawSource as string : null;
        var source = string.IsNullOrEmpty(requested) ||
            (Guid.TryParse(requested, out var sourceGuid) && sourceGuid == itemId)
                ? sources.FirstOrDefault()
                : sources.SingleOrDefault(s => s.Id == requested);
        context.Result = new StatusCodeResult(StatusCodes.Status409Conflict);
        if (source is null || !source.SupportsDirectPlay || !source.IsRemote ||
            source.Protocol != MediaProtocol.Http ||
            !Uri.TryCreate(source.Path, UriKind.Absolute, out var target) ||
            target.Scheme != Uri.UriSchemeHttps || target.UserInfo.Length != 0 ||
            target.Fragment.Length != 0)
            return;

        http.Response.Headers.Location = target.AbsoluteUri;
        http.Response.Headers.CacheControl = "no-store";
        http.Response.Headers["Referrer-Policy"] = "no-referrer";
        http.Response.ContentLength = 0;
        context.Result = new StatusCodeResult(StatusCodes.Status302Found);
    }

    private static bool HasValue(ActionExecutingContext context, string name)
        => context.ActionArguments.TryGetValue(name, out var value) &&
            value is string text && !string.IsNullOrEmpty(text);
}
