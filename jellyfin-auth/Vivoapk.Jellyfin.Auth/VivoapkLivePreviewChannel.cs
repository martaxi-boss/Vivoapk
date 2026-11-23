using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Jellyfin.Database.Implementations.Entities;
using MediaBrowser.Controller.Channels;
using MediaBrowser.Controller.Providers;
using MediaBrowser.Model.Channels;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.MediaInfo;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// Explicitly opt-in, ONE subscriber only pilot. Credentials stay transient in
/// Jellyfin RAM; never written to STRM/M3U files, databases, logs or GitHub.
/// The upstream URLs may still be visible to the account during playback.
/// This is NOT a certified multi-tenant channel provider.
/// </summary>
public sealed class VivoapkLivePreviewChannel : IChannel, IRequiresMediaInfoCallback, IHasCacheKey
{
    public const string ChannelName = "VivoAPK — TV de teste";
    private sealed record PilotStream(string ExternalId, string Title, string Url);
    private sealed record PilotSnapshot(Guid Owner, DateTime Expires, List<PilotStream> Streams);
    private static readonly object Gate = new();
    private static PilotSnapshot? _pilot;

    public string Name => ChannelName;
    public string Description => "Canais da subscrição Mega OTT autenticada (teste limitado).";
    public string DataVersion => "pilot-hls-v3-media-profile";
    // Embedded local preview artwork only. NOT the official logo of any station.
    // Never download upstream logos, query external image URLs or store credentials.
    private const string ChannelCoverPngBase64 = "iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAIAAABMXPacAAABjElEQVR42u3dO4oCQQBFUfdgKEYm4gYEt+CGXYHrMTEwMzBTlLa7iq5XfeCGQw28M9AfxFmttwfN2MoEAAAIAAABACAAAAQAgAAAEAAAAgBAAAAIAAABACAAAAQAgAAAEAAA6gpgf728iv4VPQBUGqj2+V0BFN+o6uH9XAPeZiqyVI0ze74Il92r8fUbvQsqtVr76zd9Gzpxu/anD3gOGD1iyvoBD2IjpgxaP+NJ+K9Bs9aPeRUxcNa49ZPeBf2+pYm44Yl/Gfdt5dz1896Gfm4dvX7q6+g+pp8KMOSPcTkBAAAAAAAA8wHsTudFBQAAAAAAAAAA8A1gczh2EwAAAAAAqAhwuz+GN/y0iT8DAACAxV4D/hoIAAAAAAAAAAAAAAAAAADMC/DjkHHHAgAAAAAAAAAAZAGUOg0AAAA+FeFTEQAAAPDhXAAAAAAAAAAAAAEAIAAANAeA/AsTAAIAoInvCs39GlEAAAAAAOAiLAAABACAAAAQAAACAEAAAAgAAAEAIAAABACAAPTVE+Jq1wctAnKeAAAAAElFTkSuQmCC";
    public string HomePageUrl => string.Empty;
    public ChannelParentalRating ParentalRating => ChannelParentalRating.GeneralAudience;

    internal static bool Stage(Guid owner, IReadOnlyList<MegaPreviewEntry> channels,
        string? origin, string username, string password)
    {
        if (owner == Guid.Empty || channels.Count is < 1 or > 5 ||
            string.IsNullOrWhiteSpace(origin) || string.IsNullOrWhiteSpace(username) ||
            string.IsNullOrWhiteSpace(password) ||
            !Uri.TryCreate(origin, UriKind.Absolute, out var uri) ||
            uri.Scheme is not ("http" or "https") ||
            !string.IsNullOrEmpty(uri.UserInfo) || !string.IsNullOrEmpty(uri.Query) ||
            !string.IsNullOrEmpty(uri.Fragment) || uri.AbsolutePath != "/")
            return false;

        lock (Gate)
        {
            // Never allow a second customer into the same global channel store.
            if (_pilot is not null && _pilot.Expires > DateTime.UtcNow &&
                _pilot.Owner != owner)
                return false;
            var streams = new List<PilotStream>();
            foreach (var channel in channels)
            {
                if (channel.Id.Length is < 1 or > 64 ||
                    !channel.Id.All(char.IsAsciiDigit) ||
                    channel.Title.Length is < 1 or > 250)
                    continue;
                // Android Jellyfin 2.7.3 forces HTTP direct play to HLS.
                // The authorized Mega OTT RTP1 .m3u8 was verified as a real
                // HLS manifest via a direct upstream 302 then HTTP 200.
                var source = origin.TrimEnd('/') + "/live/"
                    + Uri.EscapeDataString(username) + "/"
                    + Uri.EscapeDataString(password) + "/"
                    + Uri.EscapeDataString(channel.Id) + ".m3u8";
                streams.Add(new PilotStream(Guid.NewGuid().ToString("N"),
                    channel.Title, source));
            }
            if (streams.Count == 0)
                return false;
            _pilot = new PilotSnapshot(owner, DateTime.UtcNow.AddMinutes(30), streams);
            return true;
        }
    }

    private static PilotSnapshot? ForUser(Guid user)
    {
        lock (Gate)
        {
            return Plugin.Instance?.Configuration.PilotLiveChannels == true &&
                _pilot is { } p && p.Owner == user && p.Expires > DateTime.UtcNow
                ? p : null;
        }
    }

    public string GetCacheKey(string? userId)
    {
        if (!Guid.TryParse(userId, out var id))
            return "denied";
        var snapshot = ForUser(id);
        if (snapshot is null)
            return "denied";
        return id.ToString("N") + ":" + snapshot.Expires.Ticks.ToString(CultureInfo.InvariantCulture);
    }

    public bool IsEnabledFor(string userId)
        => Guid.TryParse(userId, out var id) && ForUser(id) is not null;

    public InternalChannelFeatures GetChannelFeatures() => new()
    {
        MediaTypes = new List<ChannelMediaType> { ChannelMediaType.Video },
        ContentTypes = new List<ChannelMediaContentType> { ChannelMediaContentType.Clip },
        SupportsSortOrderToggle = false,
        DefaultSortFields = new List<ChannelItemSortField> { ChannelItemSortField.Name },
        AutoRefreshLevels = 1
    };

    public Task<ChannelItemResult> GetChannelItems(
        InternalChannelItemQuery query, CancellationToken cancellationToken)
    {
        var snapshot = ForUser(query.UserId);
        if (snapshot is null)
            return Task.FromResult(new ChannelItemResult
            {
                Items = new List<ChannelItemInfo>(), TotalRecordCount = 0
            });
        var list = snapshot.Streams.Select(stream => new ChannelItemInfo
        {
            Id = stream.ExternalId,
            Name = stream.Title,
            Type = ChannelItemType.Media,
            MediaType = ChannelMediaType.Video,
            ContentType = ChannelMediaContentType.Clip,
            IsLiveStream = true
            // Never attach credential-bearing MediaSources to indexed items.
            // IRequiresMediaInfoCallback supplies the source only at playback.
        }).ToList();
        var total = list.Count;
        if (query.StartIndex is > 0)
            list = list.Skip(query.StartIndex.Value).ToList();
        if (query.Limit is > 0)
            list = list.Take(query.Limit.Value).ToList();
        return Task.FromResult(new ChannelItemResult { Items = list, TotalRecordCount = total });
    }

    public Task<IEnumerable<MediaSourceInfo>> GetChannelItemMediaInfo(
        string id, CancellationToken cancellationToken)
    {
        PilotStream? stream;
        lock (Gate)
        {
            stream = Plugin.Instance?.Configuration.PilotLiveChannels == true &&
                _pilot is { } p && p.Expires > DateTime.UtcNow
                ? p.Streams.FirstOrDefault(x => string.Equals(
                    x.ExternalId, id, StringComparison.Ordinal)) : null;
        }
        IEnumerable<MediaSourceInfo> sources = stream is null
            ? Array.Empty<MediaSourceInfo>() : new[] { Source(stream) };
        return Task.FromResult(sources);
    }

    private static MediaSourceInfo Source(PilotStream stream) => new()
    {
        Name = stream.Title,
        Path = stream.Url,
        Protocol = MediaProtocol.Http,
        IsRemote = true,
        IsInfiniteStream = true,
        // HLS is the delivery protocol; MPEG-TS is the media container in its
        // segments. The bounded Portugal pilot was measured against the real
        // provider (H.264/AAC). Android negotiates containers/codecs before it
        // opens the URL: a playlist extension and an empty stream list cause
        // Jellyfin to reject otherwise playable media for its device profile.
        Container = "mpegts",
        MediaStreams = new List<MediaStream>
        {
            new() { Index = 0, Type = MediaStreamType.Video, Codec = "h264" },
            new() { Index = 1, Type = MediaStreamType.Audio, Codec = "aac" }
        },
        SupportsDirectPlay = true,
        SupportsDirectStream = false,
        SupportsTranscoding = false,
        SupportsProbing = false,
        RequiresOpening = false,
        RequiresClosing = false
    };

    public Task<DynamicImageResponse> GetChannelImage(
        ImageType type, CancellationToken cancellationToken)
    {
        if (type is not (ImageType.Primary or ImageType.Thumb))
            return Task.FromResult(new DynamicImageResponse { HasImage = false });
        return Task.FromResult(new DynamicImageResponse
        {
            HasImage = true,
            Stream = new MemoryStream(Convert.FromBase64String(ChannelCoverPngBase64), writable: false),
            Format = MediaBrowser.Model.Drawing.ImageFormat.Png
        });
    }

    public IEnumerable<ImageType> GetSupportedChannelImages()
        => new[] { ImageType.Primary, ImageType.Thumb };
}
