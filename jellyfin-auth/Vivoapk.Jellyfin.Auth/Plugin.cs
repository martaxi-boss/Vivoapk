using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Serialization;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// Jellyfin 12.4 native login adapter for the independent VivoAPK backend.
/// Does not provide, index, proxy or transcode IPTV media.
/// </summary>
public sealed class Plugin : BasePlugin<PluginConfiguration>
{
    public Plugin(IApplicationPaths applicationPaths, IXmlSerializer xmlSerializer)
        : base(applicationPaths, xmlSerializer)
    {
        Instance = this;
    }

    public static Plugin? Instance { get; private set; }

    public override string Name => "VivoAPK Mega OTT Auth";

    public override Guid Id => Guid.Parse("e7a31bad-3643-4da2-834e-559c39cc90b1");
}
