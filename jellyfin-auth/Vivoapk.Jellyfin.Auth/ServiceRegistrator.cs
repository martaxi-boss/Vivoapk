using MediaBrowser.Controller;
using MediaBrowser.Controller.Authentication;
using MediaBrowser.Controller.Channels;
using MediaBrowser.Controller.Plugins;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.AspNetCore.Hosting;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

public sealed class ServiceRegistrator : IPluginServiceRegistrator
{
    public void RegisterServices(IServiceCollection services, IServerApplicationHost host)
    {
        services.AddHttpContextAccessor();
#if VIVOAPK_NATIVE_PLAYBACK_LAB
        services.AddSingleton<IStartupFilter, NativePlaybackLabFilter>();
        services.AddSingleton<PrivateHlsNativeLab>();
#endif
        services.AddSingleton<IAuthenticationProvider, MegaAuthenticationProvider>();
        // ChannelManager persists item IDs under a global ChannelId. Isolate
        // the channel itself, not merely its user-dependent cache key.
        for (int i = 0; i < VivoapkLivePreviewChannel.MaxChannels; i++)
        {
            var slot = i;
            foreach (var smartTv in new[] { false, true })
            {
                // Different authoritative provider origins require different
                // ChannelIds: Jellyfin globally caches callbacks by item ID.
                var family = smartTv;
                services.AddSingleton<IChannel>(provider =>
                    new VivoapkLivePreviewChannel(slot, family,
                        provider.GetRequiredService<Microsoft.AspNetCore.Http.IHttpContextAccessor>()));
            }
        }
    }
}
