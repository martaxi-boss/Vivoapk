using System.Net;
using System.Security.Claims;
using MediaBrowser.Controller.Channels;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Library;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// Shared authorization core for future native playback. No listener, source
/// credentials, transcode recovery, or production activation is implemented here.
/// The disposable fixture exercises this same code compiled in ordinary builds.
/// </summary>
internal static class NativePlaybackAuthorization
{
    private static readonly MegaResolverClient Entitlements = new();

    private static bool ValidToken(string value) =>
        value.Length is > 0 and <= 512 &&
        value.All(c => !char.IsWhiteSpace(c) && !char.IsControl(c) && c != ',' && c != '"');

    private static bool ParseAuthorization(string header, out string token)
    {
        token = string.Empty;
        if (header.Length > 8192 || header.Any(char.IsControl))
            return false;
        int space = header.IndexOf(' ');
        if (space <= 0 || (!header[..space].Equals("MediaBrowser", StringComparison.OrdinalIgnoreCase)
            && !header[..space].Equals("Emby", StringComparison.OrdinalIgnoreCase)))
            return false;

        // Follow the server's comma/quoted-value grammar, but reject duplicates
        // rather than letting the last token or identity field silently win.
        var fields = header[(space + 1)..];
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        int start = 0;
        bool quoted = false;
        for (int i = 0; i <= fields.Length; i++)
        {
            if (i < fields.Length && fields[i] == '"')
            {
                quoted = !quoted;
                continue;
            }

            if (i < fields.Length && (fields[i] != ',' || quoted))
                continue;
            if (quoted)
                return false;
            var field = fields[start..i].TrimStart();
            start = i + 1;
            int equal = field.IndexOf('=');
            if (equal <= 0)
                return false;
            var key = field[..equal].Trim();
            if (key.Length == 0 || !key.All(char.IsAsciiLetterOrDigit) || !names.Add(key))
                return false;
            var value = field[(equal + 1)..];
            if (value.StartsWith('"'))
            {
                if (value.Length < 2 || !value.EndsWith('"') || value[1..^1].Contains('"'))
                    return false;
                value = value[1..^1];
            }
            else if (value.Contains('"') || value.Contains('='))
                return false;

            if (key.Equals("Token", StringComparison.OrdinalIgnoreCase))
            {
                // The native v12.4 dictionary uses the exact key "Token".
                if (key != "Token")
                    return false;
                token = WebUtility.UrlDecode(value);
                if (!ValidToken(token))
                    return false;
            }
        }
        return names.Count > 0;
    }

    private static bool IsAuthenticationQueryKey(string key)
    {
        var canonical = new string(key.Where(char.IsAsciiLetterOrDigit).ToArray()).ToLowerInvariant();
        return canonical is "apikey" or "accesstoken" or "token" or "xembytoken" or "xmediabrowsertoken";
    }

    internal static bool TryGetExplicitToken(HttpRequest request, out string token)
    {
        token = string.Empty;
        var queryKeys = request.Query.Keys.Where(IsAuthenticationQueryKey).ToArray();
        bool standard = request.Headers.ContainsKey("Authorization");
        bool legacy = request.Headers.ContainsKey("X-Emby-Authorization");
        bool embyToken = request.Headers.ContainsKey("X-Emby-Token");
        bool mediaToken = request.Headers.ContainsKey("X-MediaBrowser-Token");
        if (queryKeys.Length > 0)
        {
            if (queryKeys.Length != 1 || standard || legacy || embyToken || mediaToken)
                return false;
            var key = queryKeys[0];
            if (!key.Equals("ApiKey", StringComparison.OrdinalIgnoreCase)
                && !key.Equals("api_key", StringComparison.OrdinalIgnoreCase))
                return false;
            var values = request.Query[key];
            if (values.Count != 1 || values[0] is not string value || !ValidToken(value))
                return false;
            token = value;
            return true;
        }

        if ((standard && legacy) || (embyToken && mediaToken))
            return false;
        string fromAuthorization = string.Empty;
        if (standard || legacy)
        {
            var values = request.Headers[standard ? "Authorization" : "X-Emby-Authorization"];
            if (values.Count != 1 || values[0] is not string header
                || !ParseAuthorization(header, out fromAuthorization))
                return false;
        }
        string fromLegacy = string.Empty;
        if (embyToken || mediaToken)
        {
            var values = request.Headers[embyToken ? "X-Emby-Token" : "X-MediaBrowser-Token"];
            if (values.Count != 1 || values[0] is not string value || !ValidToken(value))
                return false;
            fromLegacy = value;
        }

        // Some official clients send the same token twice. Accept only an exact
        // match, never rely on the native precedence rule to select an identity.
        if (fromAuthorization.Length > 0 && fromLegacy.Length > 0
            && !string.Equals(fromAuthorization, fromLegacy, StringComparison.Ordinal))
            return false;
        token = fromAuthorization.Length > 0 ? fromAuthorization : fromLegacy;
        return ValidToken(token);
    }

    internal static Guid AuthenticatedOwner(ClaimsPrincipal? principal)
    {
        if (principal?.Identity?.IsAuthenticated != true
            || !bool.TryParse(principal.FindFirst("Jellyfin-IsApiKey")?.Value, out var apiKey)
            || apiKey || !Guid.TryParse(principal.FindFirst("Jellyfin-UserId")?.Value, out var owner))
            return Guid.Empty;
        return owner;
    }

    internal static async Task<Guid> AuthorizeAsync(HttpContext http, Guid itemId,
        Func<string, bool> accountAllowed)
    {
        if (itemId == Guid.Empty || !TryGetExplicitToken(http.Request, out var token))
            return Guid.Empty;
        var services = http.RequestServices;
        var schemes = services.GetRequiredService<IAuthenticationSchemeProvider>();
        var scheme = await schemes.GetDefaultAuthenticateSchemeAsync().ConfigureAwait(false);
        if (scheme is null)
            return Guid.Empty;
        var auth = await http.AuthenticateAsync(scheme.Name).ConfigureAwait(false);
        if (!auth.Succeeded || auth.Principal is null
            || !string.Equals(auth.Principal.FindFirst("Jellyfin-Token")?.Value,
                token, StringComparison.Ordinal))
            return Guid.Empty;
        http.User = auth.Principal;
        var owner = AuthenticatedOwner(http.User);
        var policies = services.GetRequiredService<IAuthorizationPolicyProvider>();
        var authorization = services.GetRequiredService<IAuthorizationService>();
        var policy = await policies.GetDefaultPolicyAsync().ConfigureAwait(false);
        if (owner == Guid.Empty || !(await authorization.AuthorizeAsync(http.User, http,
                policy.Requirements).ConfigureAwait(false)).Succeeded)
            return Guid.Empty;
        var users = services.GetRequiredService<IUserManager>();
        var library = services.GetRequiredService<ILibraryManager>();
        var user = users.GetUserById(owner);
        var item = user is null ? null : library.GetItemById<BaseItem>(itemId, user);
        if (user is null || item is null || !accountAllowed(user.Username)
            || (NativePrivatePlaybackMode.Required && !string.Equals(user.AuthenticationProviderId,
                typeof(MegaAuthenticationProvider).FullName, StringComparison.Ordinal))
            || item.IsFolder || item.GetPlayAccess(user) != PlayAccess.Full || !item.IsVisible(user))
            return Guid.Empty;
        var channel = services.GetServices<IChannel>().OfType<VivoapkLivePreviewChannel>()
            .FirstOrDefault(c => library.GetNewItemId("Channel " + c.Name, typeof(Channel)) == item.ChannelId);
        if (channel is null || !channel.OwnsActiveSnapshot(owner))
            return Guid.Empty;
        if (await Entitlements.CheckEntitlementAsync(user.Username, http.RequestAborted)
                .ConfigureAwait(false) != EntitlementState.Active || !channel.OwnsActiveSnapshot(owner))
            return Guid.Empty;
        return owner;
    }
}
