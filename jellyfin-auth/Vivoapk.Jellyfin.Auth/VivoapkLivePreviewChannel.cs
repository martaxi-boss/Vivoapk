using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Jellyfin.Data.Enums;
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
    public const int MaxChannels = 64; // 64 subscriber reservations, two source families each
    private readonly int _slot;
    private readonly bool _smartTv;
    private readonly IHttpContextAccessor? _httpContextAccessor;
    private readonly MegaResolverClient _resolver = new();
    public VivoapkLivePreviewChannel(int slot, bool smartTv = false, IHttpContextAccessor? httpContextAccessor = null)
    {
        if (slot is < 0 or >= MaxChannels)
            throw new ArgumentOutOfRangeException(nameof(slot));
        _slot = slot;
        _smartTv = smartTv;
        _httpContextAccessor = httpContextAccessor;
    }
    public static bool IsSmartTv(string platform) => platform is "tizen" or "webos";
    public static string ChannelNameFor(int slot, bool smartTv = false)
        => $"VivoAPK — Conteúdos {slot + 1}" + (smartTv ? " — Smart TV" : "");
    private string MediaIdPrefix => "slot:" + _slot + ":" + (_smartTv ? "tv" : "general") + ":";
    private sealed record PilotStream(string ExternalId, string Title, string ProviderId);
    private sealed record PilotSnapshot(
        Guid Owner, DateTime Expires, string Origin, string Username,
        string Password, List<PilotStream> Streams,
        bool CategoryNavigation, string Platform, string? SessionToken,
        List<PilotStream> MovieCategories, List<PilotStream> SeriesCategories)
    {
        public string? CurrentToken { get; set; } = SessionToken;
        // Keep per-platform session tokens in RAM: TV apps may require a
        // different authoritative Mega DNS than Android for the same account.
        // Never serialize credentials or provider URLs to Jellyfin's index.
        public Dictionary<string, (string Token, string Origin, DateTime Expires)> PlatformSessions
            { get; } = new(StringComparer.Ordinal);
    }
    private static readonly object Gate = new();
    private static readonly Dictionary<(int Slot, bool SmartTv), PilotSnapshot> Pilots = new();

    public string Name => ChannelNameFor(_slot, _smartTv);
    public string Description => "Canais da subscrição Mega OTT autenticada (teste limitado).";
    public string DataVersion => "vivoapk-native-catalog-root-v15-two-client-direct-hls-profiles";
    // Embedded local preview artwork only. NOT the official logo of any station.
    // Never download upstream logos, query external image URLs or store credentials.
    private const string ChannelCoverPngBase64 = "iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAIAAABMXPacAAABjElEQVR42u3dO4oCQQBFUfdgKEYm4gYEt+CGXYHrMTEwMzBTlLa7iq5XfeCGQw28M9AfxFmttwfN2MoEAAAIAAABACAAAAQAgAAAEAAAAgBAAAAIAAABACAAAAQAgAAAEAAA6gpgf728iv4VPQBUGqj2+V0BFN+o6uH9XAPeZiqyVI0ze74Il92r8fUbvQsqtVr76zd9Gzpxu/anD3gOGD1iyvoBD2IjpgxaP+NJ+K9Bs9aPeRUxcNa49ZPeBf2+pYm44Yl/Gfdt5dz1896Gfm4dvX7q6+g+pp8KMOSPcTkBAAAAAAAA8wHsTudFBQAAAAAAAAAA8A1gczh2EwAAAAAAqAhwuz+GN/y0iT8DAACAxV4D/hoIAAAAAAAAAAAAAAAAAADMC/DjkHHHAgAAAAAAAAAAZAGUOg0AAAA+FeFTEQAAAPDhXAAAAAAAAAAAAAEAIAAANAeA/AsTAAIAoInvCs39GlEAAAAAAOAiLAAABACAAAAQAAACAEAAAAgAAAEAIAAABACAAPTVE+Jq1wctAnKeAAAAAElFTkSuQmCC";
    public string HomePageUrl => string.Empty;
    public ChannelParentalRating ParentalRating => ChannelParentalRating.GeneralAudience;

    internal static int? Stage(Guid owner, IReadOnlyList<MegaPreviewEntry> channels,
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
            var family = IsSmartTv(platform);
            var now = DateTime.UtcNow;
            foreach (var expired in Pilots.Where(kv => kv.Value.Expires <= now)
                         .Select(kv => kv.Key).ToArray())
                Pilots.Remove(expired);
            // Reject inconsistent in-RAM state instead of changing subscriber owner.
            if (Pilots.TryGetValue((assignedSlot, family), out var occupied) &&
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
            var activeSnapshot = new PilotSnapshot(
                owner, DateTime.UtcNow.AddHours(12), origin, username, password,
                streams, categoryNavigation, platform, sessionToken,
                movies, series);
            if (!string.IsNullOrEmpty(sessionToken))
                activeSnapshot.PlatformSessions[platform] = (
                    sessionToken, origin, DateTime.UtcNow.AddMinutes(4));
            Pilots[(assignedSlot, family)] = activeSnapshot;
            return assignedSlot;
        }
    }

    private PilotSnapshot? ForUser(Guid user)
    {
        lock (Gate)
        {
            return Plugin.Instance?.Configuration.PilotLiveChannels == true &&
                Pilots.TryGetValue((_slot, _smartTv), out var p) && p.Owner == user &&
                p.Expires > DateTime.UtcNow ? p : null;
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
    {
        if (!Guid.TryParse(userId, out var id))
            return false;
        var snapshot = ForUser(id);
        // Visibility must follow the *requesting* official client. A fallback
        // to the stored login platform would expose both channel families to
        // the same device when the subscriber has also logged in elsewhere.
        return snapshot is not null && IsSmartTv(ClientPlatformHint()) == _smartTv;
    }

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
        var snapshot = ForUser(query.UserId);
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
                            ? MediaIdPrefix + "live:" + category.ProviderId + ":" + entry.Id
                            : MediaIdPrefix + "vod:" + category.ProviderId + ":" + entry.Id
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
                    Id = MediaIdPrefix + "ep:" + parts[1] + ":" + parts[2] + ":" + episode.Id
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
            if (Pilots.TryGetValue((_slot, _smartTv), out var active) &&
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
                if (Pilots.TryGetValue((_slot, _smartTv), out var active) &&
                    ReferenceEquals(active, snapshot))
                    snapshot.CurrentToken = refreshed.SessionToken;
            }
        }
        return detail;
    }

    private string ClientPlatformHint()
    {
        var headers = _httpContextAccessor?.HttpContext?.Request.Headers;
        if (headers is null)
            return "android";
        return JellyfinClientPlatformDetector.FromHeaders(
            string.Concat(headers["X-Emby-Authorization"].ToString(), " ",
                headers["Authorization"].ToString()),
            headers["User-Agent"].ToString());
    }

    private string RequestPlatform(PilotSnapshot snapshot)
    {
        var hinted = ClientPlatformHint();
        // Jellyfin may invoke a source callback without the original device
        // headers; never cross a channel's immutable DNS family.
        return IsSmartTv(hinted) == _smartTv ? hinted : snapshot.Platform;
    }

    private async Task<(string Token, string Origin)?> PlaybackSessionAsync(
        PilotSnapshot snapshot, string platform, CancellationToken cancellationToken)
    {
        lock (Gate)
        {
            if (Pilots.TryGetValue((_slot, _smartTv), out var active) &&
                ReferenceEquals(snapshot, active) &&
                snapshot.PlatformSessions.TryGetValue(platform, out var known) &&
                known.Expires > DateTime.UtcNow)
                return (known.Token, known.Origin);
        }
        var verified = await _resolver.ResolveAsync(
            snapshot.Username, snapshot.Password, cancellationToken, platform)
            .ConfigureAwait(false);
        if (verified?.SessionToken is null ||
            !SafeSourceOrigin(verified.Origin))
            return null;
        lock (Gate)
        {
            if (!Pilots.TryGetValue((_slot, _smartTv), out var active) ||
                !ReferenceEquals(snapshot, active) ||
                snapshot.Expires <= DateTime.UtcNow)
                return null;
            snapshot.PlatformSessions[platform] = (
                verified.SessionToken, verified.Origin!, DateTime.UtcNow.AddMinutes(4));
        }
        return (verified.SessionToken, verified.Origin!);
    }

    private static bool SafeSourceOrigin(string? value)
    {
        if (string.IsNullOrEmpty(value) ||
            !Uri.TryCreate(value, UriKind.Absolute, out var uri) ||
            uri.Scheme is not ("http" or "https") ||
            !string.IsNullOrEmpty(uri.UserInfo) ||
            !string.IsNullOrEmpty(uri.Query) ||
            !string.IsNullOrEmpty(uri.Fragment) ||
            uri.AbsolutePath != "/")
            return false;
        return true;
    }

    private static ChannelItemResult EmptyResult() => new()
    {
        Items = new List<ChannelItemInfo>(), TotalRecordCount = 0
    };

    public async Task<IEnumerable<MediaSourceInfo>> GetChannelItemMediaInfo(
        string id, CancellationToken cancellationToken)
    {
        PilotSnapshot? snapshot;
        lock (Gate)
        {
            snapshot = Plugin.Instance?.Configuration.PilotLiveChannels == true &&
                Pilots.TryGetValue((_slot, _smartTv), out var stored) &&
                stored.Expires > DateTime.UtcNow ? stored : null;
        }
        if (snapshot is null)
            return Array.Empty<MediaSourceInfo>();
        var requireGrant = Plugin.Instance?.Configuration.RequireAuthorizedPlaybackGrant == true;
        if (!snapshot.CategoryNavigation)
        {
            if (requireGrant)
                return Array.Empty<MediaSourceInfo>();
            // Legacy bounded one-subscriber preview remains unchanged.
            var pilot = snapshot.Streams.FirstOrDefault(x =>
                string.Equals(x.ExternalId, id, StringComparison.Ordinal));
            return pilot is null
                ? Array.Empty<MediaSourceInfo>() : LiveSourceVariants(
                    Source(snapshot, pilot), pilot.ExternalId);
        }

        // No indexed source is trusted at playback: the backend revalidates
        // provider entitlement and exact category/item membership each time.
        // This rejects guessed IDs and revoked subscriptions.
        var prefix = MediaIdPrefix;
        if (!id.StartsWith(prefix, StringComparison.Ordinal))
            return Array.Empty<MediaSourceInfo>();
        var parts = id[prefix.Length..].Split(':');
        string kind;
        string categoryId;
        string providerId;
        string? extension = null;
        string? seriesId = null;
        if (parts.Length == 3 && parts[0] == "live" &&
            Digits(parts[1]) && Digits(parts[2]))
        {
            kind = "live"; categoryId = parts[1]; providerId = parts[2];
        }
        else if (parts.Length == 4 && parts[0] == "vod" &&
            Digits(parts[1]) && Digits(parts[2]) &&
            ValidExtension(parts[3]) == parts[3] && parts[3] != "unknown")
        {
            kind = "vod"; categoryId = parts[1]; providerId = parts[2];
            extension = parts[3];
        }
        else if (parts.Length == 5 && parts[0] == "ep" &&
            Digits(parts[1]) && Digits(parts[2]) && Digits(parts[3]) &&
            ValidExtension(parts[4]) == parts[4] && parts[4] != "unknown")
        {
            kind = "series"; categoryId = parts[1]; seriesId = parts[2];
            providerId = parts[3]; extension = parts[4];
        }
        else
            return Array.Empty<MediaSourceInfo>();

        if (!HasCategory(snapshot, kind, categoryId))
            return Array.Empty<MediaSourceInfo>();
        var platform = RequestPlatform(snapshot);
        var session = await PlaybackSessionAsync(snapshot, platform, cancellationToken)
            .ConfigureAwait(false);
        if (session is null)
            return Array.Empty<MediaSourceInfo>();

        AuthorizedPlaybackGrant? grant;
        if (kind == "series")
        {
            var detail = await _resolver.SeriesDetailAsync(
                session.Value.Token, categoryId, seriesId!, platform,
                cancellationToken, requestPlaybackGrant: requireGrant).ConfigureAwait(false);
            var episode = detail?.Episodes.FirstOrDefault(ep =>
                    string.Equals(ep.Id, providerId, StringComparison.Ordinal) &&
                    string.Equals(ValidExtension(ep.Extension), extension,
                        StringComparison.OrdinalIgnoreCase));
            if (episode is null)
                return Array.Empty<MediaSourceInfo>();
            grant = episode.PlaybackGrant;
        }
        else
        {
            var entries = await _resolver.CategoryItemsAsync(
                session.Value.Token, categoryId, cancellationToken,
                platform, kind, requestPlaybackGrant: requireGrant).ConfigureAwait(false);
            var entry = entries?.FirstOrDefault(item =>
                    string.Equals(item.Id, providerId, StringComparison.Ordinal) &&
                    (kind == "live" ||
                     string.Equals(ValidExtension(item.Extension), extension,
                         StringComparison.OrdinalIgnoreCase)));
            if (entry is null)
                return Array.Empty<MediaSourceInfo>();
            grant = entry.PlaybackGrant;
        }

        // The experimental secure mode uses an explicit, independently
        // authorized playback grant instead of building a login/password URL.
        string? authorizedPath = null;
        if (requireGrant)
        {
            authorizedPath = grant?.GetValidatedUrl(providerId, kind,
                session.Value.Origin, snapshot.Username, snapshot.Password, DateTimeOffset.UtcNow);
            if (authorizedPath is null)
                return Array.Empty<MediaSourceInfo>();
        }

        // The URL is passed to the client's own Jellyfin player. It is NOT a
        // Jellyfin relay/transcode URL and never lands in the item database.
        var direct = DirectSource(snapshot, session.Value.Origin, providerId, kind, extension,
            authorizedPath);
        return kind == "live" ? LiveSourceVariants(direct, id) : new[] { direct };
    }

    // Jellyfin 12.4 rejects any HLS manifest container for server-side Direct
    // Play (StreamBuilder.ManifestContainers), even for a remote direct URL.
    // Supply two descriptors for the SAME authorized .m3u8 provider playlist:
    // native MPEG-TS and an HLS.js Web compatibility descriptor using MP4
    // decoder capabilities. The latter's MP4 is a client negotiation hint,
    // NOT a claim that the HLS segments are MP4. TranscodingSubProtocol=hls
    // selects the actual HLS parser. No media, proxy, or remux touches the VPS.
    // A stable public ID also avoids hashing a credential-bearing Path.
    private static MediaSourceInfo[] LiveSourceVariants(
        MediaSourceInfo native, string externalId)
    {
        var web = new MediaSourceInfo
        {
            Id = "hls:" + externalId,
            Name = native.Name + " (HLS)",
            Path = native.Path,
            Protocol = MediaProtocol.Http,
            IsRemote = true,
            IsInfiniteStream = true,
            Container = "mp4",
            TranscodingSubProtocol = MediaStreamProtocol.hls,
            MediaStreams = new List<MediaStream>(native.MediaStreams),
            SupportsDirectPlay = true,
            SupportsDirectStream = false,
            SupportsTranscoding = false,
            SupportsProbing = false,
            RequiresOpening = false,
            RequiresClosing = false
        };
        return new[] { native, web };
    }

    private static MediaSourceInfo DirectSource(
        PilotSnapshot snapshot, string origin, string providerId,
        string kind, string? extension, string? authorizedPath = null)
    {
        var folder = kind switch
        {
            "vod" => "movie",
            "series" => "series",
            _ => "live"
        };
        var ext = kind == "live" ? "m3u8" : extension!;
        var uri = authorizedPath ?? (origin.TrimEnd('/') + "/" + folder + "/"
            + Uri.EscapeDataString(snapshot.Username) + "/"
            + Uri.EscapeDataString(snapshot.Password) + "/"
            + Uri.EscapeDataString(providerId) + "." + ext);
        var mp4 = string.Equals(ext, "mp4", StringComparison.OrdinalIgnoreCase);
        var isLive = kind == "live";
        return new MediaSourceInfo
        {
            Name = kind switch
            {
                "live" => "TV ao vivo",
                "vod" => "Filme",
                _ => "Episódio"
            },
            Path = uri,
            Protocol = MediaProtocol.Http,
            IsRemote = true,
            IsInfiniteStream = isLive,
            Container = isLive ? "mpegts" : ext,
            // The source Path is the provider HLS playlist; the segments are MPEG-TS.
            // Jellyfin Web uses this protocol hint to select hls.js rather than
            // treating the remote .m3u8 as a progressive MPEG-TS file.
            // This is metadata only: no relay, remux, or transcoding via Jellyfin.
            TranscodingSubProtocol = isLive ? MediaStreamProtocol.hls : MediaStreamProtocol.http,
            MediaStreams = isLive || mp4 ? new List<MediaStream>
            {
                new() { Index = 0, Type = MediaStreamType.Video, Codec = "h264" },
                new() { Index = 1, Type = MediaStreamType.Audio, Codec = "aac" }
            } : new List<MediaStream>(),
            SupportsDirectPlay = true,
            SupportsDirectStream = false,
            SupportsTranscoding = false,
            SupportsProbing = false,
            RequiresOpening = false,
            RequiresClosing = false
        };
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
        // HTTP Direct Play uses the provider HLS manifest, not an HTTP TS stream.
        // Keep MPEG-TS as the segment container for device-profile negotiation.
        TranscodingSubProtocol = MediaStreamProtocol.hls,
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
