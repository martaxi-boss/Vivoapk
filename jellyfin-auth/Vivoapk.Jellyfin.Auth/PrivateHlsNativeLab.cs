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

    internal static async Task<MediaSourceInfo?> CreateSourceAsync(PrivateNativeHls transport,
        Guid owner, string username, string token, string origin, string kind,
        string category, string asset, string platform, CancellationToken ct)
    {
        if (!Enabled || owner == Guid.Empty || !NativePlaybackLab.IsFixtureAccount(username)
            || (username == "synthetic-mega-user" ? category != "10" : category != "20")
            || kind != "live" || asset != "42")
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
