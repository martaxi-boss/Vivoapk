using Microsoft.AspNetCore.Http;
using System.Text.RegularExpressions;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>Ordinary-plugin opt-in. Neither a fixture build nor a configuration
/// flag alone can enable private server playback. Host inputs are local
/// process configuration; no caller supplies a socket, executable or secret.</summary>
internal static class NativePrivatePlaybackMode
{
    private const string RunDirectory = "/srv/projects/Vivoapk/run";
    private static readonly object SourceScopeKey = new();
    private sealed record SourceScope(Guid Owner, string ExternalId);

    private static bool HostOptIn =>
        Environment.GetEnvironmentVariable("VIVOAPK_NATIVE_PRIVATE_PLAYBACK") == "1";

    // If an operator turns off the plugin flag without removing the host
    // opt-in and restarting, keep the guard closed, never fall back to
    // credential-bearing direct source URLs.
    internal static bool Required => HostOptIn ||
        Plugin.Instance?.Configuration.NativePrivatePlayback == true;

    internal static bool Active => HostOptIn
        && Plugin.Instance?.Configuration is { Enabled: true, PilotLiveChannels: true,
            CategoryNavigation: true, NativePrivatePlayback: true }
        && TryHostPaths(out _, out _, out _);

    internal static bool FixturePrivate =>
#if VIVOAPK_NATIVE_PLAYBACK_LAB
        !Required && PrivateHlsNativeLab.Enabled;
#else
        false;
#endif

    internal static bool PrivateRoutes => Required || FixturePrivate;

    internal static bool IsAccountAllowed(string username) => Required
        ? Active : NativePlaybackLab.IsFixtureAccount(username);

    private static bool InRunDirectory(string path, string suffix)
    {
        if (!Path.IsPathFullyQualified(path) || !path.EndsWith(suffix, StringComparison.Ordinal))
            return false;
        return string.Equals(Path.GetDirectoryName(path), RunDirectory, StringComparison.Ordinal)
            && string.Equals(Path.GetFullPath(path), path, StringComparison.Ordinal);
    }

    private static bool TryHostPaths(out string socket, out string key, out string probe)
    {
        socket = Environment.GetEnvironmentVariable("VIVOAPK_PRIVATE_MEDIA_SOCKET")
            ?? MegaResolverClient.SocketPath;
        key = Environment.GetEnvironmentVariable("VIVOAPK_PRIVATE_MEDIA_KEY_FILE")
            ?? RunDirectory + "/private-media.key";
        probe = Environment.GetEnvironmentVariable("VIVOAPK_PRIVATE_FFPROBE") ?? "";
        try
        {
            return OperatingSystem.IsLinux() && InRunDirectory(socket, ".sock")
                && InRunDirectory(key, ".key") && Path.IsPathFullyQualified(probe)
                && Path.GetFileName(probe) == "ffprobe" && File.Exists(probe);
        }
        catch (Exception ex) when (ex is ArgumentException or IOException
            or UnauthorizedAccessException or NotSupportedException)
        {
            return false;
        }
    }

    internal static PrivateNativeHls CreateTransport()
    {
#if VIVOAPK_NATIVE_PLAYBACK_LAB
        if (FixturePrivate)
            return PrivateHlsNativeLab.CreateTransport();
#endif
        // Lazy registration is harmless when disabled. A malformed host
        // configuration cannot reach this transport from a playback request.
        if (!TryHostPaths(out var socket, out var key, out _))
            return new PrivateNativeHls(MegaResolverClient.SocketPath, RunDirectory + "/private-media.key");
        return new PrivateNativeHls(socket, key);
    }

    internal static bool AllowsCacheKey(string? key)
    {
        if (key is null)
            return false;
        if (Required)
            return Active && Regex.IsMatch(key,
                @"\Aslot:[0-9]{1,3}:(?:general|tv):(?:live:[0-9]{1,64}:[0-9]{1,64}|vod:[0-9]{1,64}:[0-9]{1,64}:[a-z0-9]{1,12}|ep:[0-9]{1,64}:[0-9]{1,64}:[0-9]{1,64}:[a-z0-9]{1,12})\z",
                RegexOptions.CultureInvariant);
#if VIVOAPK_NATIVE_PLAYBACK_LAB
        return FixturePrivate && Regex.IsMatch(key, PrivateHlsNativeLab.FileEnabled
            ? @"\Aslot:[0-9]{1,3}:(?:general|tv):(?:vod:110:501:mp4|ep:220:601:701:mp4)\z"
            : @"\Aslot:[0-9]{1,3}:(?:general|tv):live:(?:10|20):42\z",
            RegexOptions.CultureInvariant);
#else
        return false;
#endif
    }

    internal static void EnterSourceScope(HttpContext http, Guid owner, string externalId) =>
        http.Items[SourceScopeKey] = new SourceScope(owner, externalId);

    internal static void ExitSourceScope(HttpContext http) => http.Items.Remove(SourceScopeKey);

    internal static bool CanBind(HttpContext? http, Guid owner, string externalId) =>
        Active && http is not null && NativePlaybackAuthorization.AuthenticatedOwner(http.User) == owner
        && http.Items.TryGetValue(SourceScopeKey, out var value) && value is SourceScope scope
        && scope.Owner == owner && scope.ExternalId == externalId;

    internal static async Task<MediaBrowser.Model.Dto.MediaSourceInfo?> CreateSourceAsync(
        PrivateNativeHls transport, Guid owner, string token, string category, string asset,
        string platform, string kind, string? series, CancellationToken ct)
    {
        if (!Active || !TryHostPaths(out _, out _, out var executable))
            return null;
        var source = await transport.BindProbedSourceAsync(owner, token, category, asset,
            platform, executable, ct, kind, series).ConfigureAwait(false);
        if (source is not null)
        {
            // Remuxing remains possible via the native HLS encoder. Never
            // advertise an unguarded direct-stream/progressive route.
            source.SupportsDirectStream = false;
            source.SupportsDirectPlay = false;
        }
        return source;
    }
}
