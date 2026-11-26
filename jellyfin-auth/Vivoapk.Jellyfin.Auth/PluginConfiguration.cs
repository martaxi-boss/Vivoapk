using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

public sealed class PluginConfiguration : BasePluginConfiguration
{
    // Fail closed until the plugin and Jellyfin administrator are tested in isolation.
    public bool Enabled { get; set; } = false;

    // Never create libraries in production until explicit opt-in and isolation validation.
    public bool ProvisionPrivateLibraries { get; set; } = false;

    // Entitlement scanning is separate opt-in: never affect production accounts by default.
    public bool MonitorEntitlements { get; set; } = false;

    // Experimental in-memory TV preview. Always off on production installs.
    public bool PilotLiveChannels { get; set; } = false;

    // Separate, opt-in experimental full Live list. No VOD/series/EPG or
    // production multiuser certification is implied by enabling this flag.
    public bool FullLiveCatalog { get; set; } = false;

    // Opt-in only. Categories appear as native Jellyfin folders; no global IPTV list.
    public bool CategoryNavigation { get; set; } = false;

    // Requires a separate, trusted host opt-in. Default releases do not
    // activate private server playback just by registering the guard.
    public bool NativePrivatePlayback { get; set; } = false;

    // Disposable test build only; normal releases ignore this field.
    public bool NativeServerPlaybackLab { get; set; } = false;

    // Owner GUIDs only (never passwords or DNS): reserving a Jellyfin ChannelId
    // prevents old indexed media items from being reassigned to a different
    // subscriber after the in-RAM snapshot expires or the server restarts.
    public string[] ChannelSlotOwners { get; set; } = Array.Empty<string>();
}
