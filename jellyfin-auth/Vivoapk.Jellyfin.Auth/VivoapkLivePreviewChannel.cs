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
using Microsoft.AspNetCore.Http;

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
    public const int MaxChannels = 64; // bounded, stable one-to-one Jellyfin ChannelIds
    private readonly int _slot;
    private readonly IHttpContextAccessor? _httpContextAccessor;
    private readonly MegaResolverClient _resolver = new();
    public VivoapkLivePreviewChannel(int slot, IHttpContextAccessor? httpContextAccessor = null)
    {
        if (slot is < 0 or >= MaxChannels)
            throw new ArgumentOutOfRangeException(nameof(slot));
        _slot = slot;
        _httpContextAccessor = httpContextAccessor;
    }
    public static string ChannelNameFor(int slot) => $"VivoAPK — TV de teste {slot + 1}";
    private sealed record PilotStream(string ExternalId, string Title, string ProviderId);
    private sealed record PilotSnapshot(
        Guid Owner, DateTime Expires, string Origin, string Username,
        string Password, List<PilotStream> Streams,
        bool CategoryNavigation, string Platform, string? SessionToken,
        List<PilotStream> MovieCategories, List<PilotStream> SeriesCategories)
    {
        public string? CurrentToken { get; set; } = SessionToken;
        // No global index of 28,841 channels in Jellyfin RAM:
        // each media source can be resolved from its stable, category-scoped ID.
    }
    private static readonly object Gate = new();
    private static readonly Dictionary<int, PilotSnapshot> Pilots = new();

    public string Name => ChannelNameFor(_slot);
    public string Description => "Canais da subscrição Mega OTT autenticada (teste limitado).";
    public string DataVersion => "vivoapk-native-catalog-root-v10";
    // Embedded local preview artwork only. NOT the official logo of any station.
    // Never download upstream logos, query external image URLs or store credentials.
    private const string ChannelCoverPngBase64 = "iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAIAAABMXPacAAABjElEQVR42u3dO4oCQQBFUfdgKEYm4gYEt+CGXYHrMTEwMzBTlLa7iq5XfeCGQw28M9AfxFmttwfN2MoEAAAIAAABACAAAAQAgAAAEAAAAgBAAAAIAAABACAAAAQAgAAAEAAA6gpgf728iv4VPQBUGqj2+V0BFN+o6uH9XAPeZiqyVI0ze74Il92r8fUbvQsqtVr76zd9Gzpxu/anD3gOGD1iyvoBD2IjpgxaP+NJ+K9Bs9aPeRUxcNa49ZPeBf2+pYm44Yl/Gfdt5dz1896Gfm4dvX7q6+g+pp8KMOSPcTkBAAAAAAAA8wHsTudFBQAAAAAAAAAA8A1gczh2EwAAAAAAqAhwuz+GN/y0iT8DAACAxV4D/hoIAAAAAAAAAAAAAAAAAADMC/DjkHHHAgAAAAAAAAAAZAGUOg0AAAA+FeFTEQAAAPDhXAAAAAAAAAAAAAEAIAAANAeA/AsTAAIAoInvCs39GlEAAAAAAOAiLAAABACAAAAQAAACAEAAAAgAAAEAIAAABACAAPTVE+Jq1wctAnKeAAAAAElFTkSuQmCC";
    public string HomePageUrl => string.Empty;
    public ChannelParentalRating ParentalRating => ChannelParentalRating.GeneralAudience;

    internal static string? Stage(Guid owner, IReadOnlyList<MegaPreviewEntry> channels,
        string? origin, string username, string password, bool fullLive = false,
        bool categoryNavigation = false, string? sessionToken = null, string platform = "android",
        IReadOnlyList<MegaPreviewEntry>? movieCategories = null,
        IReadOnlyList<MegaPreviewEntry>? seriesCategories = null)
    {
        if (owner == Guid.Empty ||
            (!categoryNavigation && channels.Count < 1) ||
            (categoryNavigation && channels.Count == 0 &&
                (movieCategories is null || movieCategories.Count == 0) &&
                (seriesCategories is null || seriesCategories.Count == 0)) ||
            channels.Count > (categoryNavigation ? 2000 : fullLive ? 20000 : 5) ||
            movieCategories is { Count: > 2000 } ||
            seriesCategories is { Count: > 2000 } ||
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
                    categoryNavigation ? "cat:live:" + channel.Id : Guid.NewGuid().ToString("N"),
                    channel.Title, channel.Id));
            }
            static List<PilotStream> CategoryEntries(
                IReadOnlyList<MegaPreviewEntry>? values, string categoryKind)
            {
                var result = new List<PilotStream>();
                if (values is null)
                    return result;
                foreach (var value in values)
                {
                    if (value.Id.Length is < 1 or > 64 ||
                        !value.Id.All(char.IsAsciiDigit) ||
                        value.Title.Length is < 1 or > 250)
                        continue;
                    result.Add(new PilotStream(
                        "cat:" + categoryKind + ":" + value.Id,
                        value.Title, value.Id));
                }
                return result;
            }
            var movies = CategoryEntries(movieCategories, "vod");
            var series = CategoryEntries(seriesCategories, "series");
            if (streams.Count + movies.Count + series.Count == 0)
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
                streams, categoryNavigation, platform, sessionToken,
                movies, series);
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
        ContentTypes = new List<ChannelMediaContentType> {
            ChannelMediaContentType.Clip, ChannelMediaContentType.Movie,
            ChannelMediaContentType.Episode },
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
            // This first-level folder deliberately prevents Jellyfin's
            // GetLatestChannelItems automatic one-level refresh from eagerly
            // downloading every provider category's streams.
            list = new List<ChannelItemInfo>();
            foreach (var (id, name, entries) in new[]
            {
                ("vivo:live-root", "TV ao vivo", snapshot.Streams),
                ("vivo:vod-root", "Filmes", snapshot.MovieCategories),
                ("vivo:series-root", "Séries", snapshot.SeriesCategories)
            })
            {
                if (entries.Count == 0)
                    continue;
                list.Add(new ChannelItemInfo
                {
                    Id = id, Name = name,
                    Type = ChannelItemType.Folder,
                    FolderType = ChannelFolderType.Container,
                    MediaType = ChannelMediaType.Video
                });
            }
        }
        else if (query.FolderId is "vivo:live-root" or "vivo:vod-root" or "vivo:series-root")
        {
            var entries = query.FolderId switch
            {
                "vivo:vod-root" => snapshot.MovieCategories,
                "vivo:series-root" => snapshot.SeriesCategories,
                _ => snapshot.Streams
            };
            list = entries.Select(category => new ChannelItemInfo
            {
                Id = category.ExternalId, Name = category.Title,
                Type = ChannelItemType.Folder,
                FolderType = ChannelFolderType.Container,
                MediaType = ChannelMediaType.Video
            }).ToList();
        }
        else if (query.FolderId.StartsWith("cat:", StringComparison.Ordinal))
        {
            var parts = query.FolderId.Split(':');
            if (parts.Length != 3 || !Digits(parts[2]))
                return EmptyResult();
            var kind = parts[1];
            var authorized = KindCategories(snapshot, kind);
            var category = authorized?.FirstOrDefault(entry =>
                string.Equals(entry.ExternalId, query.FolderId, StringComparison.Ordinal));
            if (category is null)
                return EmptyResult();
            var entries = await FetchCategoryItemsAsync(
                snapshot, category.ProviderId, kind, cancellationToken)
                .ConfigureAwait(false);
            if (entries is null)
                return EmptyResult();
            list = new List<ChannelItemInfo>(entries.Count);
            foreach (var entry in entries)
            {
                if (!Digits(entry.Id))
                    continue;
                if (kind == "series")
                {
                    list.Add(new ChannelItemInfo
                    {
                        Id = "show:" + category.ProviderId + ":" + entry.Id,
                        Name = entry.Title,
                        Type = ChannelItemType.Folder,
                        FolderType = ChannelFolderType.Series,
                        MediaType = ChannelMediaType.Video
                    });
                }
                else
                {
                    var ext = kind == "vod" ? ValidExtension(entry.Extension) : null;
                    list.Add(new ChannelItemInfo
                    {
                        Id = kind == "live"
                            ? "live:" + category.ProviderId + ":" + entry.Id
                            : "vod:" + category.ProviderId + ":" + entry.Id
                                + ":" + (ext ?? "unknown"),
                        Name = entry.Title,
                        Type = ChannelItemType.Media,
                        MediaType = ChannelMediaType.Video,
                        ContentType = kind == "live"
                            ? ChannelMediaContentType.Clip : ChannelMediaContentType.Movie,
                        IsLiveStream = kind == "live"
                    });
                }
            }
        }
        else if (query.FolderId.StartsWith("show:", StringComparison.Ordinal))
        {
            var parts = query.FolderId.Split(':');
            if (parts.Length != 3 || !Digits(parts[1]) || !Digits(parts[2]) ||
                !HasCategory(snapshot, "series", parts[1]))
                return EmptyResult();
            var detail = await FetchSeriesAsync(snapshot, parts[1], parts[2],
                cancellationToken).ConfigureAwait(false);
            if (detail is null)
                return EmptyResult();
            list = detail.Seasons.Select(season => new ChannelItemInfo
            {
                Id = "season:" + parts[1] + ":" + parts[2] + ":" + season.Id,
                Name = season.Title,
                Type = ChannelItemType.Folder,
                FolderType = ChannelFolderType.Season,
                MediaType = ChannelMediaType.Video,
                SeriesName = detail.Title
            }).ToList();
        }
        else if (query.FolderId.StartsWith("season:", StringComparison.Ordinal))
        {
            var parts = query.FolderId.Split(':');
            if (parts.Length != 4 || !Digits(parts[1]) || !Digits(parts[2]) ||
                !Digits(parts[3]) || !HasCategory(snapshot, "series", parts[1]))
                return EmptyResult();
            var detail = await FetchSeriesAsync(snapshot, parts[1], parts[2],
                cancellationToken).ConfigureAwait(false);
            if (detail is null || !detail.Seasons.Any(x =>
                    string.Equals(x.Id, parts[3], StringComparison.Ordinal)))
                return EmptyResult();
            list = detail.Episodes
                .Where(episode => string.Equals(
                    episode.SeasonId, parts[3], StringComparison.Ordinal))
                .Select(episode => new ChannelItemInfo
                {
                    Id = "ep:" + parts[1] + ":" + parts[2] + ":" + episode.Id
                        + ":" + (ValidExtension(episode.Extension) ?? "unknown"),
                    Name = episode.Title,
                    Type = ChannelItemType.Media,
                    MediaType = ChannelMediaType.Video,
                    ContentType = ChannelMediaContentType.Episode,
                    IsLiveStream = false,
                    SeriesName = detail.Title,
                    IndexNumber = int.TryParse(episode.EpisodeNumber, out var epnum)
                        ? epnum : null,
                    ParentIndexNumber = int.TryParse(parts[3], out var season)
                        ? season : null
                }).ToList();
        }
        else
        {
            return EmptyResult();
        }
        var total = list.Count;
        if (query.StartIndex is > 0)
            list = list.Skip(query.StartIndex.Value).ToList();
        if (query.Limit is > 0)
            list = list.Take(query.Limit.Value).ToList();
        return new ChannelItemResult { Items = list, TotalRecordCount = total };
    }

    private static bool Digits(string? value)
        => !string.IsNullOrEmpty(value) && value.Length <= 64 &&
            value.All(char.IsAsciiDigit);

    private static string? ValidExtension(string? value)
        => !string.IsNullOrEmpty(value) && value.Length <= 12 &&
            value.All(char.IsAsciiLetterOrDigit)
            ? value.ToLowerInvariant() : null;

    private static List<PilotStream>? KindCategories(PilotSnapshot snapshot, string kind)
        => kind switch
        {
            "live" => snapshot.Streams,
            "vod" => snapshot.MovieCategories,
            "series" => snapshot.SeriesCategories,
            _ => null
        };

    private static bool HasCategory(PilotSnapshot snapshot, string kind, string categoryId)
        => KindCategories(snapshot, kind)?.Any(x =>
            string.Equals(x.ProviderId, categoryId, StringComparison.Ordinal)) == true;

    private async Task<IReadOnlyList<MegaPreviewEntry>?> FetchCategoryItemsAsync(
        PilotSnapshot snapshot, string categoryId, string kind,
        CancellationToken cancellationToken)
    {
        var entries = await _resolver.CategoryItemsAsync(
            snapshot.CurrentToken, categoryId, cancellationToken,
            snapshot.Platform, kind).ConfigureAwait(false);
        if (entries is not null)
            return entries;
        // Short-lived session JWT can expire while the Jellyfin UI is open.
        // Renew using RAM-only credentials; backend rechecks Mega entitlement.
        var refreshed = await _resolver.ResolveAsync(
            snapshot.Username, snapshot.Password,
            cancellationToken, snapshot.Platform).ConfigureAwait(false);
        if (refreshed?.SessionToken is null ||
            !string.Equals(refreshed.Origin?.TrimEnd('/'),
                snapshot.Origin.TrimEnd('/'), StringComparison.OrdinalIgnoreCase))
            return null;
        entries = await _resolver.CategoryItemsAsync(
            refreshed.SessionToken, categoryId, cancellationToken,
            snapshot.Platform, kind).ConfigureAwait(false);
        if (entries is null)
            return null;
        lock (Gate)
        {
            if (Pilots.TryGetValue(_slot, out var active) &&
                ReferenceEquals(active, snapshot))
                snapshot.CurrentToken = refreshed.SessionToken;
        }
        return entries;
    }

    private async Task<MegaSeriesInfo?> FetchSeriesAsync(
        PilotSnapshot snapshot, string categoryId, string seriesId,
        CancellationToken cancellationToken)
    {
        var detail = await _resolver.SeriesDetailAsync(
            snapshot.CurrentToken, categoryId, seriesId,
            snapshot.Platform, cancellationToken).ConfigureAwait(false);
        if (detail is not null)
            return detail;
        var refreshed = await _resolver.ResolveAsync(
            snapshot.Username, snapshot.Password,
            cancellationToken, snapshot.Platform).ConfigureAwait(false);
        if (refreshed?.SessionToken is null ||
            !string.Equals(refreshed.Origin?.TrimEnd('/'),
                snapshot.Origin.TrimEnd('/'), StringComparison.OrdinalIgnoreCase))
            return null;
        detail = await _resolver.SeriesDetailAsync(
            refreshed.SessionToken, categoryId, seriesId,
            snapshot.Platform, cancellationToken).ConfigureAwait(false);
        if (detail is not null)
        {
            lock (Gate)
            {
                if (Pilots.TryGetValue(_slot, out var active) &&
                    ReferenceEquals(active, snapshot))
                    snapshot.CurrentToken = refreshed.SessionToken;
            }
        }
        return detail;
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
                    ? FindAuthorizedCategoryStream(snapshot, id)
                    : snapshot.Streams.FirstOrDefault(x =>
                        string.Equals(x.ExternalId, id, StringComparison.Ordinal));
            }
        }
        IEnumerable<MediaSourceInfo> sources = stream is null || active is null
            ? Array.Empty<MediaSourceInfo>() : new[] { Source(active, stream) };
        return Task.FromResult(sources);
    }

    private static PilotStream? FindAuthorizedCategoryStream(PilotSnapshot snapshot, string id)
    {
        // A callback receives only the item external ID, not the user.
        // The Jellyfin ChannelId remains exclusively reserved for the owner.
        // Reject guessed category IDs outside this subscriber's root list.
        if (!id.StartsWith("live:", StringComparison.Ordinal))
            return null;
        var parts = id.Split(':');
        if (parts.Length != 3 ||
            parts[1].Length is < 1 or > 64 || parts[2].Length is < 1 or > 64 ||
            !parts[1].All(char.IsAsciiDigit) || !parts[2].All(char.IsAsciiDigit) ||
            !snapshot.Streams.Any(x =>
                string.Equals(x.ProviderId, parts[1], StringComparison.Ordinal)))
            return null;
        return new PilotStream(id, "TV ao vivo", parts[2]);
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
