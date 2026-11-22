using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

public sealed class PluginConfiguration : BasePluginConfiguration
{
    // Fail closed until the plugin and Jellyfin administrator are tested in isolation.
    public bool Enabled { get; set; } = false;
}
