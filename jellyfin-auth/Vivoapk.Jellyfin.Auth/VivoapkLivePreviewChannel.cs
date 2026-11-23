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
/// Experimental isolated-channel preview. Each provider is assigned a distinct
/// Jellyfin ChannelId; one globally shared IChannel indexed multiple users into
/// the same channel and exposed foreign items in Jellyfin 12.4. No credential
/// content may be persisted to STRM/M3U files, databases, logs or GitHub.
/// Not a production-ready full catalog.
/// </summary>
public sealed class VivoapkLivePreviewChannel : IChannel, IRequiresMediaInfoCallback, IHasCacheKey
{
    public const int MaxChannels = 2; // test-only experiment; no production rollout
    private const int MaxIndexedCategoryMedia = 20000;
    private readonly int _slot;
    private readonly MegaResolverClient _resolver = new();
    public VivoapkLivePreviewChannel(int slot)
    {
        if (slot is < 0 or >= MaxChannels)
            throw new ArgumentOutOfRangeException(nameof(slot));
        _slot = slot;
    }
    public static string ChannelNameFor(int slot) => $"VivoAPK — TV de teste {slot + 1}";
    private sealed record PilotStream(string ExternalId, string Title, string ProviderId);
    private sealed record PilotSnapshot(
        Guid Owner, DateTime Expires, string Origin, string Username,
        string Password, List<PilotStream> Streams,
        bool CategoryNavigation, string Platform, string? SessionToken)
    {
        public string? CurrentToken { get; set; } = SessionToken;
        // Ephemeral per-subscriber data, never included in the item database.
        public Dictionary<string, PilotStream> LoadedStreams { get; } =
            new(StringComparer.Ordinal);
    }
    private static readonly object Gate = new();
    private static readonly Dictionary<int, PilotSnapshot> Pilots = new();

    public string Name => ChannelNameFor(_slot);
    public string Description => "Canais da subscrição Mega OTT autenticada (teste limitado).";
    public string DataVersion => "pilot-hls-v7-private-category-folders";
    // Embedded local preview artwork only. NOT the official logo of any station.
    // Never download upstream logos, query external image URLs or store credentials.
    private const string ChannelCoverPngBase64 = "iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAIAAABMXPacAAABjElEQVR42u3dO4oCQQBFUfdgKEYm4gYEt+CGXYHrMTEwMzBTlLa7iq5XfeCGQw28M9AfxFmttwfN2MoEAAAIAAABACAAAAQAgAAAEAAAAgBAAAAIAAABACAAAAQAgAAAEAAA6gpgf728iv4VPQBUGqj2+V0BFN+o6uH9XAPeZiqyVI0ze74Il92r8fUbvQsqtVr76zd9Gzpxu/anD3gOGD1iyvoBD2IjpgxaP+NJ+K9Bs9aPeRUxcNa49ZPeBf2+pYm44Yl/Gfdt5dz1896Gfm4dvX7q6+g+pp8KMOSPcTkBAAAAAAAA8wHsTudFBQAAAAAAAAAA8A1gczh2EwAAAAAAqAhwuz+GN/y0iT8DAACAxV4D/hoIAAAAAAAAAAAAAAAAAADMC/DjkHHHAgAAAAAAAAAAZAGUOg0AAAA+FeFTEQAAAPDhXAAAAAAAAAAAAAEAIAAANAeA/AsTAAIAoInvCs39GlEAAAAAAOAiLAAABACAAAAQAAACAEAAAAgAAAEAIAAABACAAPTVE+Jq1wctAnKeAAAAAElFTkSuQmCC";
    public string HomePageUrl => string.Empty;
    public ChannelParentalRating ParentalRating => ChannelParentalRating.GeneralAudience;

    internal static string? Stage(Guid owner, IReadOnlyList<MegaPreviewEntry> channels,
        string? origin, string username, string password, bool fullLive = false,
        bool categoryNavigation = false, string? sessionToken = null, string platform = "android")
    {
        if (owner == Guid.Empty || channels.Count < 1 ||
            channels.Count > (categoryNavigation ? 2000 : fullLive ? 20000 : 5) ||
            string.IsNullOrWhiteSpace(origin) || string.IsNullOrWhiteSpace(username) ||
            string.IsNullOrWhiteSpace(password) ||
            !Uri.TryCreate(origin, UriKind.Absolute, out var uri) ||
            uri.Scheme is not ("http" or "https") ||
            !string.IsNullOrEmpty(uri.UserInfo) || !string.IsNullOrEmpty(uri.Query) ||
            !string.IsNullOrEmpty(uri.Fragment) || uri.AbsolutePath != "/")
            return null;

        lock (Gate)
        {
            // The channel database is global. A user's indexed items must
            // NEVER become visible to another person after snapshot expiry or
            // a Jellyfin restart. Persist ONLY the stable owner GUID binding.
            var plugin = Plugin.Instance;
            if (plugin is null)
                return null;
            var savedOwners = plugin.Configuration.ChannelSlotOwners;
            if (savedOwners is null || (savedOwners.Length != 0 &&
                                         savedOwners.Length != MaxChannels))
                return null;
            var reservations = new string[MaxChannels];
            Array.Copy(savedOwners, reservations, savedOwners.Length);
            var ownerKey = owner.ToString("N");
            var assignedSlot = Array.FindIndex(reservations,
                candidate => string.Equals(candidate, ownerKey, StringComparison.OrdinalIgnoreCase));
            var newReservation = assignedSlot == -1;
            if (newReservation)
                assignedSlot = Array.FindIndex(reservations, string.IsNullOrEmpty);
            if (assignedSlot == -1)
                return null;
            var now = DateTime.UtcNow;
            foreach (var expired in Pilots.Where(kv => kv.Value.Expires <= now)
                         .Select(kv => kv.Key).ToArray())
                Pilots.Remove(expired);
            // Reject inconsistent in-RAM state instead of changing subscriber owner.
            if (Pilots.TryGetValue(assignedSlot, out var occupied) &&
                occupied.Owner != owner)
                return null;
            var streams = new List<PilotStream>();
            foreach (var channel in channels)
            {
                if (channel.Id.Length is < 1 or > 64 ||
                    !channel.Id.All(char.IsAsciiDigit) ||
                    channel.Title.Length is < 1 or > 250)
                    continue;
                // No credential-bearing URLs in the channel item index.
                // Construct a direct HLS source only on authorized playback.
                streams.Add(new PilotStream(
                    categoryNavigation ? "cat:" + channel.Id : Guid.NewGuid().ToString("N"),
                    channel.Title, channel.Id));
            }
            if (streams.Count == 0)
                return null;
            if (newReservation)
            {
                // No media URL or password ever enters the XML config file.
                reservations[assignedSlot] = ownerKey;
                plugin.Configuration.ChannelSlotOwners = reservations;
                try
                {
                    plugin.SaveConfiguration();
                }
                catch (IOException)
                {
                    plugin.Configuration.ChannelSlotOwners = savedOwners;
                    return null;
                }
                catch (UnauthorizedAccessException)
                {
                    plugin.Configuration.ChannelSlotOwners = savedOwners;
                    return null;
                }
            }
            Pilots[assignedSlot] = new PilotSnapshot(
                owner, DateTime.UtcNow.AddMinutes(30), origin, username, password,
                streams, categoryNavigation, platform, sessionToken);
            return ChannelNameFor(assignedSlot);
        }
    }

    private static PilotSnapshot? ForUser(Guid user, int slot)
    {
        lock (Gate)
        {
            return Plugin.Instance?.Configuration.PilotLiveChannels == true &&
                Pilots.TryGetValue(slot, out var p) && p.Owner == user &&
                p.Expires > DateTime.UtcNow ? p : null;
        }
    }

    public string GetCacheKey(string? userId)
    {
        if (!Guid.TryParse(userId, out var id))
            return "denied";
        var snapshot = ForUser(id, _slot);
        if (snapshot is null)
            return "denied";
        return id.ToString("N") + ":" + snapshot.Expires.Ticks.ToString(CultureInfo.InvariantCulture);
    }

    public bool IsEnabledFor(string userId)
        => Guid.TryParse(userId, out var id) && ForUser(id, _slot) is not null;

    public InternalChannelFeatures GetChannelFeatures() => new()
    {
        MediaTypes = new List<ChannelMediaType> { ChannelMediaType.Video },
        ContentTypes = new List<ChannelMediaContentType> { ChannelMediaContentType.Clip },
        SupportsSortOrderToggle = false,
        DefaultSortFields = new List<ChannelItemSortField> { ChannelItemSortField.Name },
        AutoRefreshLevels = 1
    };

    public async Task<ChannelItemResult> GetChannelItems(
        InternalChannelItemQuery query, CancellationToken cancellationToken)
    {
        var snapshot = ForUser(query.UserId, _slot);
        if (snapshot is null)
            return EmptyResult();
        List<ChannelItemInfo> list;
        if (!snapshot.CategoryNavigation)
        {
            list = snapshot.Streams.Select(stream => new ChannelItemInfo
            {
                Id = stream.ExternalId,
                Name = stream.Title,
                Type = ChannelItemType.Media,
                MediaType = ChannelMediaType.Video,
                ContentType = ChannelMediaContentType.Clip,
                IsLiveStream = true
            }).ToList();
        }
        else if (string.IsNullOrEmpty(query.FolderId))
        {
            list = snapshot.Streams.Select(category => new ChannelItemInfo
            {
                Id = category.ExternalId,
                Name = category.Title,
                Type = ChannelItemType.Folder,
                FolderType = ChannelFolderType.Container,
                MediaType = ChannelMediaType.Video
            }).ToList();
        }
        else
        {
            // Folder IDs are whitelisted from this user's own authenticated
            // category index, never taken at face value from query parameters.
            var category = snapshot.Streams.FirstOrDefault(entry =>
                string.Equals(entry.ExternalId, query.FolderId, StringComparison.Ordinal));
            if (category is null)
                return EmptyResult();
            var entries = await _resolver.CategoryItemsAsync(
                snapshot.CurrentToken, category.ProviderId, cancellationToken,
                snapshot.Platform).ConfigureAwait(false);
            if (entries is null)
            {
                // A short-lived JWT expires after five minutes. Re-resolve
                // privately with this subscriber's RAM-only credentials.
                var refreshed = await _resolver.ResolveAsync(
                    snapshot.Username, snapshot.Password, cancellationToken,
                    snapshot.Platform).ConfigureAwait(false);
                if (refreshed?.SessionToken is null ||
                    !string.Equals(refreshed.Origin?.TrimEnd('/'),
                        snapshot.Origin.TrimEnd('/'), StringComparison.OrdinalIgnoreCase))
                    return EmptyResult();
                entries = await _resolver.CategoryItemsAsync(
                    refreshed.SessionToken, category.ProviderId, cancellationToken,
                    snapshot.Platform).ConfigureAwait(false);
                if (entries is null)
                    return EmptyResult();
                lock (Gate)
                {
                    if (Pilots.TryGetValue(_slot, out var active) &&
                        ReferenceEquals(active, snapshot))
                        snapshot.CurrentToken = refreshed.SessionToken;
                }
            }
            var staged = new List<PilotStream>(entries.Count);
            list = new List<ChannelItemInfo>(entries.Count);
            foreach (var entry in entries)
            {
                // Category-scoped item IDs avoid reparenting a channel
                // indexed in two different provider categories.
                var externalId = "live:" + category.ProviderId + ":" + entry.Id;
                staged.Add(new PilotStream(externalId, entry.Title, entry.Id));
                list.Add(new ChannelItemInfo
                {
                    Id = externalId,
                    Name = entry.Title,
                    Type = ChannelItemType.Media,
                    MediaType = ChannelMediaType.Video,
                    ContentType = ChannelMediaContentType.Clip,
                    IsLiveStream = true
                });
            }
            lock (Gate)
            {
                if (!Pilots.TryGetValue(_slot, out var active) ||
                    !ReferenceEquals(active, snapshot) ||
                    snapshot.Expires <= DateTime.UtcNow)
                    return EmptyResult();
                var prefix = "live:" + category.ProviderId + ":";
                var previous = snapshot.LoadedStreams.Keys.Where(key =>
                    key.StartsWith(prefix, StringComparison.Ordinal)).ToArray();
                var projected = snapshot.LoadedStreams.Count - previous.Length + staged.Count;
                if (projected > MaxIndexedCategoryMedia)
                    return EmptyResult();
                foreach (var key in previous)
                    snapshot.LoadedStreams.Remove(key);
                foreach (var item in staged)
                    snapshot.LoadedStreams[item.ExternalId] = item;
            }
        }
        var total = list.Count;
        if (query.StartIndex is > 0)
            list = list.Skip(query.StartIndex.Value).ToList();
        if (query.Limit is > 0)
            list = list.Take(query.Limit.Value).ToList();
        return new ChannelItemResult { Items = list, TotalRecordCount = total };
    }

    private static ChannelItemResult EmptyResult() => new()
    {
        Items = new List<ChannelItemInfo>(), TotalRecordCount = 0
    };

    public Task<IEnumerable<MediaSourceInfo>> GetChannelItemMediaInfo(
        string id, CancellationToken cancellationToken)
    {
        PilotStream? stream = null;
        PilotSnapshot? active = null;
        lock (Gate)
        {
            if (Plugin.Instance?.Configuration.PilotLiveChannels == true &&
                Pilots.TryGetValue(_slot, out var snapshot) &&
                snapshot.Expires > DateTime.UtcNow)
            {
                active = snapshot;
                stream = snapshot.CategoryNavigation
                    ? snapshot.LoadedStreams.GetValueOrDefault(id)
                    : snapshot.Streams.FirstOrDefault(x =>
                        string.Equals(x.ExternalId, id, StringComparison.Ordinal));
            }
        }
        IEnumerable<MediaSourceInfo> sources = stream is null || active is null
            ? Array.Empty<MediaSourceInfo>() : new[] { Source(active, stream) };
        return Task.FromResult(sources);
    }

    private static MediaSourceInfo Source(PilotSnapshot owner, PilotStream stream) => new()
    {
        Name = stream.Title,
        Path = owner.Origin.TrimEnd('/') + "/live/"
            + Uri.EscapeDataString(owner.Username) + "/"
            + Uri.EscapeDataString(owner.Password) + "/"
            + Uri.EscapeDataString(stream.ProviderId) + ".m3u8",
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
