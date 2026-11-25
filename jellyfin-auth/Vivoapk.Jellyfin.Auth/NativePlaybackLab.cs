using System.Security.Claims;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.MediaInfo;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>Disposable CI only; never accepts real provider media or real accounts.</summary>
internal static class NativePlaybackLab
{
    internal static bool Enabled =>
#if VIVOAPK_NATIVE_PLAYBACK_LAB
        Plugin.Instance?.Configuration.NativeServerPlaybackLab == true &&
        Environment.GetEnvironmentVariable("VIVOAPK_CI_SERVER_PLAYBACK") == "1";
#else
        false;
#endif
    internal static bool IsFixtureAccount(string username)
        => username is "synthetic-mega-user" or "synthetic-mega-user-b";

    internal static Guid AuthenticatedOwner(ClaimsPrincipal? principal)
    {
        if (principal?.Identity?.IsAuthenticated != true ||
            !bool.TryParse(principal.FindFirst("Jellyfin-IsApiKey")?.Value, out var apiKey) ||
            apiKey || !Guid.TryParse(principal.FindFirst("Jellyfin-UserId")?.Value, out var owner))
            return Guid.Empty;
        return owner;
    }

    internal static MediaSourceInfo? CreateSource(string origin, string kind, string asset)
    {
        if (!Enabled || kind != "live" || asset != "42" ||
            !Uri.TryCreate(origin, UriKind.Absolute, out var uri) ||
            uri.Scheme != "http" || !uri.IsLoopback || uri.Port < 1024 ||
            uri.AbsolutePath != "/" || uri.UserInfo.Length != 0 ||
            uri.Query.Length != 0 || uri.Fragment.Length != 0)
            return null;
        // Separate CI-only case: a genuinely open-ended HTTP MPEG-TS source.
        var infinite = Environment.GetEnvironmentVariable("VIVOAPK_CI_INFINITE_LIVE") == "1";
        return new MediaSourceInfo
        {
            Name = "Synthetic native FFmpeg fixture",
            Path = new Uri(uri, infinite ? "/fixture/live.ts" : "/fixture/native.ts").AbsoluteUri,
            Protocol = MediaProtocol.Http,
            IsRemote = true,
            IsInfiniteStream = infinite,
            Container = "mpegts",
            // The extended fixture remains a finite, generated loopback asset.
            RunTimeTicks = infinite ? null : TimeSpan.FromSeconds(
                Environment.GetEnvironmentVariable("VIVOAPK_CI_LONG_CACHE") == "1" ? 360 : 15).Ticks,
            Bitrate = 500000,
            MediaStreams = new List<MediaStream>
            {
                new() { Index = 0, Type = MediaStreamType.Video, Codec = "mpeg2video",
                    Width = 160, Height = 90, AverageFrameRate = 25, BitRate = 380000 },
                new() { Index = 1, Type = MediaStreamType.Audio, Codec = "mp2",
                    Channels = 2, SampleRate = 48000, BitRate = 128000 }
            },
            SupportsDirectPlay = false,
            SupportsDirectStream = true,
            SupportsTranscoding = true,
            SupportsProbing = false,
            RequiresOpening = false,
            RequiresClosing = false
        };
    }
}
