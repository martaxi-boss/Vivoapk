using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// Calls the previously deployed VivoAPK authentication service over a Unix socket.
/// No Mega URLs, provider tokens, passwords or catalogue data are logged or cached.
/// </summary>
public enum EntitlementState { Active, Expired, Disabled, Unavailable }

internal sealed record MegaResolvedSession(string? SessionToken, string? Origin);
internal sealed record MegaPreviewEntry(string Id, string Title, string? Extension = null);
internal sealed record MegaSeasonEntry(string Id, string Title);
internal sealed record MegaEpisodeEntry(
    string Id, string SeasonId, string Title, string? EpisodeNumber, string? Extension);
internal sealed record MegaSeriesInfo(
    string SeriesId, string? Title,
    IReadOnlyList<MegaSeasonEntry> Seasons,
    IReadOnlyList<MegaEpisodeEntry> Episodes);

public sealed class MegaResolverClient
{
    private const int MaxResponseBytes = 8192;
    private const int MaxCatalogResponseBytes = 8 * 1024 * 1024;
    private const int MaxCatalogItems = 20000;
    public const string SocketPath = "/srv/projects/Vivoapk/run/backend.sock";
    private const string MonitorKeyPath = "/srv/projects/Vivoapk/run/entitlement-monitor.key";
    private readonly HttpClient _client;
    private readonly Func<string?> _monitorKeyProvider;

    private static bool IsSupportedPlatform(string? value)
        => value is "android" or "android_tv" or "ios" or "windows" or "tizen" or "webos";

    public MegaResolverClient()
    {
        var handler = new SocketsHttpHandler
        {
            AllowAutoRedirect = false,
            UseProxy = false,
            ConnectTimeout = TimeSpan.FromSeconds(5),
            ConnectCallback = async (_, cancellationToken) =>
            {
                var socket = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
                try
                {
                    await socket.ConnectAsync(new UnixDomainSocketEndPoint(SocketPath), cancellationToken)
                        .ConfigureAwait(false);
                    return new NetworkStream(socket, ownsSocket: true);
                }
                catch
                {
                    socket.Dispose();
                    throw;
                }
            }
        };
        _client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost/"),
            Timeout = TimeSpan.FromSeconds(10) };
        _monitorKeyProvider = ReadPrivateMonitorKey;
    }

    // Injectable for offline protocol tests, never used by the plugin registration.
    public MegaResolverClient(HttpClient client) : this(client, ReadPrivateMonitorKey)
    {
    }

    // Test-injectable provider. The production plugin reads only its private key file.
    public MegaResolverClient(HttpClient client, Func<string?> monitorKeyProvider)
    {
        _client = client;
        _monitorKeyProvider = monitorKeyProvider;
    }

    private static string? ReadPrivateMonitorKey()
    {
        // The secret must be provisioned privately on the VPS, never in this DLL,
        // source code, Jellyfin plugin configuration, logs or a public repository.
        var info = new FileInfo(MonitorKeyPath);
        if (!info.Exists || info.Length > 1024 || info.LinkTarget is not null)
            return null;
        return File.ReadAllText(MonitorKeyPath).Trim();
    }

    public async Task<EntitlementState> CheckEntitlementAsync(
        string username, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(username) || username.Length > 128)
            return EntitlementState.Unavailable;

        try
        {
            var secret = _monitorKeyProvider();
            if (secret is null || secret.Length < 32 || secret.Length > 1024)
                return EntitlementState.Unavailable;
            using var request = new HttpRequestMessage(
                HttpMethod.Post, "_internal/entitlement/status")
            {
                Content = new StringContent(
                    JsonSerializer.Serialize(new { username }),
                    Encoding.UTF8, "application/json")
            };
            request.Headers.TryAddWithoutValidation("X-Vivoapk-Monitor-Key", secret);
            using var response = await _client.SendAsync(
                request, HttpCompletionOption.ResponseHeadersRead, cancellationToken
            ).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
                return EntitlementState.Unavailable;

            await using var input = await response.Content.ReadAsStreamAsync(
                cancellationToken).ConfigureAwait(false);
            using var output = new MemoryStream();
            var buffer = new byte[1024];
            while (true)
            {
                var count = await input.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
                if (count == 0)
                    break;
                if (output.Length + count > MaxResponseBytes)
                    return EntitlementState.Unavailable;
                output.Write(buffer, 0, count);
            }
            output.Position = 0;
            using var json = JsonDocument.Parse(output);
            if (json.RootElement.ValueKind != JsonValueKind.Object ||
                !json.RootElement.TryGetProperty("state", out var state) ||
                state.ValueKind != JsonValueKind.String)
                return EntitlementState.Unavailable;
            return state.GetString() switch
            {
                "ACTIVE" => EntitlementState.Active,
                "EXPIRED" => EntitlementState.Expired,
                "DISABLED" => EntitlementState.Disabled,
                _ => EntitlementState.Unavailable
            };
        }
        catch (Exception exception) when (
            exception is HttpRequestException or TaskCanceledException or
            JsonException or IOException or UnauthorizedAccessException)
        {
            return EntitlementState.Unavailable;
        }
    }

    public async Task<bool> ValidateAsync(
        string username, string password, CancellationToken cancellationToken = default,
        string platform = "android")
        => await ResolveAsync(username, password, cancellationToken, platform).ConfigureAwait(false) is not null;

    internal async Task<MegaResolvedSession?> ResolveAsync(
        string username, string password, CancellationToken cancellationToken = default,
        string platform = "android")
    {
        if (string.IsNullOrWhiteSpace(username) || username.Length > 128 ||
            string.IsNullOrWhiteSpace(password) || password.Length > 2048 ||
            !IsSupportedPlatform(platform))
            return null;
        try
        {
            var body = JsonSerializer.Serialize(new
            {
                username,
                password,
                platform
            });
            using var request = new HttpRequestMessage(HttpMethod.Post, "v1/session/resolve")
            {
                Content = new StringContent(body, Encoding.UTF8, "application/json")
            };
            using var response = await _client.SendAsync(
                request, HttpCompletionOption.ResponseHeadersRead, cancellationToken
            ).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
                return null;

            await using var input = await response.Content.ReadAsStreamAsync(
                cancellationToken).ConfigureAwait(false);
            using var output = new MemoryStream();
            var buffer = new byte[1024];
            while (true)
            {
                var count = await input.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
                if (count == 0)
                    break;
                if (output.Length + count > MaxResponseBytes)
                    return null;
                output.Write(buffer, 0, count);
            }
            output.Position = 0;
            using var json = JsonDocument.Parse(output);
            var root = json.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("code", out var code)
                || code.GetString() != "SUCCESS")
                return null;
            var token = root.TryGetProperty("session_token", out var jwt)
                && jwt.ValueKind == JsonValueKind.String ? jwt.GetString() : null;
            var origin = root.TryGetProperty("xtream_base_url", out var dns)
                && dns.ValueKind == JsonValueKind.String ? dns.GetString() : null;
            return new MegaResolvedSession(token, origin);
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException
                                 or JsonException or IOException)
        {
            return null;
        }
    }


    // Private category endpoints return metadata only. Null = denied/unavailable,
    // not a legitimate empty category; never log upstream tokens or URLs.
    internal Task<IReadOnlyList<MegaPreviewEntry>?> CategoriesAsync(
        string? token, CancellationToken cancellationToken = default,
        string platform = "android", string kind = "live")
        => FetchCategoryMetadataAsync(token, null, platform, kind, cancellationToken);

    internal Task<IReadOnlyList<MegaPreviewEntry>?> CategoryItemsAsync(
        string? token, string categoryId, CancellationToken cancellationToken = default,
        string platform = "android", string kind = "live")
        => FetchCategoryMetadataAsync(token, categoryId, platform, kind, cancellationToken);

    private async Task<IReadOnlyList<MegaPreviewEntry>?> FetchCategoryMetadataAsync(
        string? token, string? categoryId, string platform, string kindRequested,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrEmpty(token) || token.Length > 4096 ||
            !IsSupportedPlatform(platform) ||
            kindRequested is not ("live" or "vod" or "series") ||
            (categoryId is not null && (categoryId.Length is < 1 or > 64 ||
                                        !categoryId.All(char.IsAsciiDigit))))
            return null;
        try
        {
            var path = categoryId is null
                ? "v1/catalog/categories" : "v1/catalog/category-items";
            object payload = categoryId is null
                ? new { kind = kindRequested, platform }
                : new { kind = kindRequested, platform, category_id = categoryId };
            using var request = new HttpRequestMessage(HttpMethod.Post, path)
            {
                Content = new StringContent(
                    JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json")
            };
            request.Headers.Authorization =
                new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);
            using var response = await _client.SendAsync(
                request, HttpCompletionOption.ResponseHeadersRead, cancellationToken
            ).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
                return null;
            await using var input = await response.Content.ReadAsStreamAsync(
                cancellationToken).ConfigureAwait(false);
            using var output = new MemoryStream();
            var buffer = new byte[8192];
            while (true)
            {
                var count = await input.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
                if (count == 0)
                    break;
                if (output.Length + count > MaxCatalogResponseBytes)
                    return null;
                output.Write(buffer, 0, count);
            }
            output.Position = 0;
            using var doc = JsonDocument.Parse(output);
            var root = doc.RootElement;
            var field = categoryId is null ? "categories" : "items";
            var countLimit = categoryId is null ? 2000 : MaxCatalogItems;
            if (root.ValueKind != JsonValueKind.Object ||
                !root.TryGetProperty("kind", out var kind) ||
                kind.ValueKind != JsonValueKind.String || kind.GetString() != kindRequested ||
                !root.TryGetProperty(field, out var rows) ||
                rows.ValueKind != JsonValueKind.Array ||
                rows.GetArrayLength() > countLimit)
                return null;
            var items = new List<MegaPreviewEntry>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var row in rows.EnumerateArray())
            {
                var label = categoryId is null ? "name" : "title";
                if (row.ValueKind != JsonValueKind.Object ||
                    !row.TryGetProperty("id", out var idJson) ||
                    !row.TryGetProperty(label, out var titleJson) ||
                    idJson.ValueKind != JsonValueKind.String ||
                    titleJson.ValueKind != JsonValueKind.String)
                    continue;
                var id = idJson.GetString() ?? string.Empty;
                var title = titleJson.GetString() ?? string.Empty;
                if (id.Length is < 1 or > 64 || !id.All(char.IsAsciiDigit) ||
                    string.IsNullOrWhiteSpace(title) || title.Length > 250 ||
                    !seen.Add(id))
                    continue;
                if (categoryId is not null &&
                    row.TryGetProperty("category_id", out var category) &&
                    category.ValueKind == JsonValueKind.String &&
                    !string.Equals(category.GetString(), categoryId, StringComparison.Ordinal))
                    continue;
                string? extension = null;
                if (categoryId is not null && row.TryGetProperty(
                        "container_extension", out var container)
                    && container.ValueKind == JsonValueKind.String)
                {
                    var rawExtension = container.GetString() ?? string.Empty;
                    if (rawExtension.Length is >= 1 and <= 12 &&
                        rawExtension.All(char.IsAsciiLetterOrDigit))
                        extension = rawExtension.ToLowerInvariant();
                }
                items.Add(new MegaPreviewEntry(id, title, extension));
            }
            return items;
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException
                                 or JsonException or IOException)
        {
            return null;
        }
    }


    internal async Task<MegaSeriesInfo?> SeriesDetailAsync(
        string? token, string categoryId, string seriesId,
        string platform = "android", CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(token) || token.Length > 4096 ||
            !IsSupportedPlatform(platform) ||
            categoryId.Length is < 1 or > 64 || !categoryId.All(char.IsAsciiDigit) ||
            seriesId.Length is < 1 or > 64 || !seriesId.All(char.IsAsciiDigit))
            return null;
        try
        {
            using var request = new HttpRequestMessage(
                HttpMethod.Post, "v1/catalog/series")
            {
                Content = new StringContent(
                    JsonSerializer.Serialize(new { category_id = categoryId,
                        series_id = seriesId, platform }),
                    Encoding.UTF8, "application/json")
            };
            request.Headers.Authorization =
                new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);
            using var response = await _client.SendAsync(request,
                HttpCompletionOption.ResponseHeadersRead, cancellationToken)
                .ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
                return null;
            await using var input = await response.Content.ReadAsStreamAsync(
                cancellationToken).ConfigureAwait(false);
            using var output = new MemoryStream();
            var buffer = new byte[8192];
            while (true)
            {
                var n = await input.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
                if (n == 0)
                    break;
                if (output.Length + n > MaxCatalogResponseBytes)
                    return null;
                output.Write(buffer, 0, n);
            }
            output.Position = 0;
            using var doc = JsonDocument.Parse(output);
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object ||
                !root.TryGetProperty("series_id", out var actual) ||
                actual.ValueKind != JsonValueKind.String ||
                !string.Equals(actual.GetString(), seriesId, StringComparison.Ordinal) ||
                !root.TryGetProperty("seasons", out var seasons) ||
                seasons.ValueKind != JsonValueKind.Array ||
                seasons.GetArrayLength() > 1000 ||
                !root.TryGetProperty("episodes", out var episodes) ||
                episodes.ValueKind != JsonValueKind.Array ||
                episodes.GetArrayLength() > MaxCatalogItems)
                return null;
            var seasonRows = new List<MegaSeasonEntry>();
            var episodeRows = new List<MegaEpisodeEntry>();
            foreach (var row in seasons.EnumerateArray())
            {
                if (row.ValueKind != JsonValueKind.Object ||
                    !row.TryGetProperty("id", out var id) ||
                    !row.TryGetProperty("title", out var title) ||
                    id.ValueKind != JsonValueKind.String ||
                    title.ValueKind != JsonValueKind.String)
                    continue;
                var name = title.GetString() ?? string.Empty;
                var seasonId = id.GetString() ?? string.Empty;
                if (seasonId.Length is < 1 or > 64 || !seasonId.All(char.IsAsciiDigit) ||
                    name.Length is < 1 or > 250)
                    continue;
                seasonRows.Add(new MegaSeasonEntry(seasonId, name));
            }
            foreach (var row in episodes.EnumerateArray())
            {
                if (row.ValueKind != JsonValueKind.Object ||
                    !row.TryGetProperty("id", out var id) ||
                    !row.TryGetProperty("season_id", out var season) ||
                    !row.TryGetProperty("title", out var title) ||
                    id.ValueKind != JsonValueKind.String ||
                    season.ValueKind != JsonValueKind.String ||
                    title.ValueKind != JsonValueKind.String)
                    continue;
                var idString = id.GetString() ?? string.Empty;
                var seasonString = season.GetString() ?? string.Empty;
                var episodeTitle = title.GetString() ?? string.Empty;
                if (idString.Length is < 1 or > 64 ||
                    !idString.All(char.IsAsciiDigit) ||
                    seasonString.Length is < 1 or > 64 ||
                    !seasonString.All(char.IsAsciiDigit) ||
                    episodeTitle.Length is < 1 or > 250)
                    continue;
                string? ext = null;
                if (row.TryGetProperty("container_extension", out var extension) &&
                    extension.ValueKind == JsonValueKind.String)
                {
                    var raw = extension.GetString() ?? string.Empty;
                    if (raw.Length is >= 1 and <= 12 && raw.All(char.IsAsciiLetterOrDigit))
                        ext = raw.ToLowerInvariant();
                }
                var epNum = row.TryGetProperty("episode_number", out var ep)
                    && ep.ValueKind == JsonValueKind.String ? ep.GetString() : null;
                episodeRows.Add(new MegaEpisodeEntry(
                    idString, seasonString, episodeTitle, epNum, ext));
            }
            var nameValue = root.TryGetProperty("title", out var seriesTitle)
                && seriesTitle.ValueKind == JsonValueKind.String
                ? seriesTitle.GetString() : null;
            return new MegaSeriesInfo(seriesId, nameValue, seasonRows, episodeRows);
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException
                                 or JsonException or IOException)
        {
            return null;
        }
    }

    internal Task<IReadOnlyList<MegaPreviewEntry>> PreviewAsync(
        string? token, CancellationToken cancellationToken = default, string platform = "android")
        => FetchLiveAsync(token, platform, full: false, cancellationToken);

    internal Task<IReadOnlyList<MegaPreviewEntry>> FullLiveAsync(
        string? token, CancellationToken cancellationToken = default, string platform = "android")
        => FetchLiveAsync(token, platform, full: true, cancellationToken);

    private async Task<IReadOnlyList<MegaPreviewEntry>> FetchLiveAsync(
        string? token, string platform, bool full, CancellationToken cancellationToken)
    {
        if (string.IsNullOrEmpty(token) || token.Length > 4096 || !IsSupportedPlatform(platform))
            return Array.Empty<MegaPreviewEntry>();
        try
        {
            // The backend returns ONLY authorized stream IDs and display titles:
            // it must not send media URLs, provider secrets or other clients' data.
            var path = full ? "v1/catalog/list" : "v1/catalog/preview";
            using var request = new HttpRequestMessage(HttpMethod.Post, path)
            {
                Content = new StringContent(
                    JsonSerializer.Serialize(new { kind = "live", platform }),
                    Encoding.UTF8, "application/json")
            };
            request.Headers.Authorization =
                new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);
            using var response = await _client.SendAsync(
                request, HttpCompletionOption.ResponseHeadersRead, cancellationToken
            ).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
                return Array.Empty<MegaPreviewEntry>();

            await using var input = await response.Content.ReadAsStreamAsync(
                cancellationToken).ConfigureAwait(false);
            using var output = new MemoryStream();
            var buf = new byte[8192];
            var byteLimit = full ? MaxCatalogResponseBytes : MaxResponseBytes;
            while (true)
            {
                var count = await input.ReadAsync(buf, cancellationToken).ConfigureAwait(false);
                if (count == 0)
                    break;
                if (output.Length + count > byteLimit)
                    return Array.Empty<MegaPreviewEntry>();
                output.Write(buf, 0, count);
            }
            output.Position = 0;
            using var doc = JsonDocument.Parse(output);
            if (!doc.RootElement.TryGetProperty("kind", out var kind)
                || kind.ValueKind != JsonValueKind.String || kind.GetString() != "live"
                || !doc.RootElement.TryGetProperty("items", out var items)
                || items.ValueKind != JsonValueKind.Array
                || items.GetArrayLength() > (full ? MaxCatalogItems : 5))
                return Array.Empty<MegaPreviewEntry>();
            var result = new List<MegaPreviewEntry>();
            var ids = new HashSet<string>(StringComparer.Ordinal);
            foreach (var item in items.EnumerateArray())
            {
                if (!item.TryGetProperty("id", out var itemId)
                    || !item.TryGetProperty("title", out var title)
                    || itemId.ValueKind != JsonValueKind.String
                    || title.ValueKind != JsonValueKind.String)
                    continue;
                var id = itemId.GetString() ?? string.Empty;
                var name = title.GetString() ?? string.Empty;
                if (id.Length is < 1 or > 64 || !id.All(char.IsAsciiDigit)
                    || string.IsNullOrWhiteSpace(name) || name.Length > 250
                    || !ids.Add(id))
                    continue;
                result.Add(new MegaPreviewEntry(id, name));
            }
            return result;
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException
                                 or JsonException or IOException)
        {
            return Array.Empty<MegaPreviewEntry>();
        }
    }

}
