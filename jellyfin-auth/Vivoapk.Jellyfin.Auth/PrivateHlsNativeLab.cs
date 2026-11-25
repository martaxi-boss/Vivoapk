#if VIVOAPK_NATIVE_PLAYBACK_LAB
using MediaBrowser.Model.Dto;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>Disposable adapter only. Uses the SAME normal compiled lease
/// manager as the future production host; never accepts real accounts/media.</summary>
internal static class PrivateHlsNativeLab
{
    internal static PrivateNativeHls CreateTransport() => new(
        "/srv/projects/Vivoapk/run/private-hls-fixture.sock",
        "/srv/projects/Vivoapk/run/private-hls-fixture.key");

    internal static bool Enabled => NativePlaybackLab.Enabled &&
        Environment.GetEnvironmentVariable("VIVOAPK_CI_PRIVATE_HLS") == "1" &&
        !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("RUNNER_TEMP"));

    internal static bool LiveRecoveryEnabled => Enabled &&
        Environment.GetEnvironmentVariable("VIVOAPK_CI_PRIVATE_LIVE") == "1";

    internal static bool FileEnabled => Enabled && !LiveRecoveryEnabled &&
        Environment.GetEnvironmentVariable("VIVOAPK_CI_PRIVATE_FILE") == "1";

    internal static async Task<MediaSourceInfo?> CreateSourceAsync(PrivateNativeHls transport,
        Guid owner, string username, string token, string origin, string kind,
        string category, string asset, string? series, string? extension, string platform,
        CancellationToken ct)
    {
        if (!Enabled || owner == Guid.Empty || !NativePlaybackLab.IsFixtureAccount(username))
            return null;
        if (FileEnabled)
        {
            // Exact authorized fixture identities, not a generic provider
            // source template. Validate the same disposable resolver origin.
            bool movie = username == "synthetic-mega-user" && kind == "vod"
                && category == "110" && asset == "501" && series is null;
            bool episode = username == "synthetic-mega-user-b" && kind == "series"
                && category == "220" && asset == "701" && series == "601";
            if ((!movie && !episode) || extension != "mp4")
                return null;
            var finite = NativePlaybackLab.CreateSource(origin, "live", "42");
            if (finite is null)
                return null;
            string? verifiedFfmpeg = Environment.GetEnvironmentVariable("VIVOAPK_EXPECTED_FFMPEG");
            if (verifiedFfmpeg is null || !Path.IsPathFullyQualified(verifiedFfmpeg))
                return null;
            var executable = Path.Combine(Path.GetDirectoryName(verifiedFfmpeg)!, "ffprobe");
            return await transport.BindProbedSourceAsync(owner, token, category, asset, platform,
                executable, ct, kind, series).ConfigureAwait(false);
        }
        if ((username == "synthetic-mega-user" ? category != "10" : category != "20")
            || kind != "live" || asset != "42" || series is not null)
            return null;
        var source = NativePlaybackLab.CreateSource(origin, kind, asset);
        if (source is null)
            return null;
        source.Name = "Synthetic private HLS native fixture";
        return await transport.BindSourceAsync(owner, token, category, asset, platform, source, ct)
            .ConfigureAwait(false);
    }
}
#endif
