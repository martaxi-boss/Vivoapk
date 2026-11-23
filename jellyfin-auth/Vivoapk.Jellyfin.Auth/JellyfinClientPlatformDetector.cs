namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// Use only a platform hint from the official Jellyfin client, not a login permission.
/// Unknown client devices preserve the previously working generic DNS.
/// </summary>
public static class JellyfinClientPlatformDetector
{
    public static string FromHeaders(string? authorization, string? userAgent)
    {
        var info = string.Concat(authorization, " ", userAgent);
        if (info.Length > 4096)
            info = info[..4096];
        bool Has(string text) => info.Contains(text, StringComparison.OrdinalIgnoreCase);
        if (Has("webos") || Has("web0s") || (Has("lg") && Has("tv")))
            return "webos";
        if (Has("tizen") || (Has("samsung") && Has("tv") && !Has("android")))
            return "tizen";
        if (Has("android tv") || Has("google tv") || Has("fire tv"))
            return "android_tv";
        if (Has("ios") || Has("iphone") || Has("ipad") || Has("ipados"))
            return "ios";
        if (Has("windows"))
            return "windows";
        return "android";
    }
}
