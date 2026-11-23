using MediaBrowser.Controller;
using MediaBrowser.Controller.Authentication;
using MediaBrowser.Controller.Channels;
using MediaBrowser.Controller.Plugins;
using Microsoft.Extensions.DependencyInjection;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

public sealed class ServiceRegistrator : IPluginServiceRegistrator
{
    public void RegisterServices(IServiceCollection services, IServerApplicationHost host)
    {
        services.AddHttpContextAccessor();
        services.AddSingleton<IAuthenticationProvider, MegaAuthenticationProvider>();
        // ChannelManager persists item IDs under a global ChannelId. Isolate
        // the channel itself, not merely its user-dependent cache key.
        for (int i = 0; i < VivoapkLivePreviewChannel.MaxChannels; i++)
            services.AddSingleton<IChannel>(new VivoapkLivePreviewChannel(i));
    }
}
