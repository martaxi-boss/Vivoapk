using System.Net;
using System.Net.Sockets;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>Private socket transport. No provider credentials in the response,
/// command line, URL, or log. Not registered in the production plugin yet.</summary>
internal sealed class PrivateHlsSocketClient : IDisposable
{
    private static readonly Regex Id = new(@"^[A-Za-z0-9_-]{32}\z", RegexOptions.CultureInvariant);
    private static readonly Regex Number = new(@"^[0-9]{1,64}\z", RegexOptions.CultureInvariant);
    private readonly HttpClient _client;
    private readonly string _key;

    internal PrivateHlsSocketClient(string socketPath, string key)
    {
        if (!Path.IsPathFullyQualified(socketPath) || key.Length is < 32 or > 1024
            || key.Any(c => char.IsWhiteSpace(c) || char.IsControl(c)))
            throw new InvalidOperationException("Invalid private transport configuration");
        _key = key;
        _client = new HttpClient(new SocketsHttpHandler
        {
            AllowAutoRedirect = false, UseProxy = false, UseCookies = false,
            ConnectTimeout = TimeSpan.FromSeconds(5),
            MaxConnectionsPerServer = 2,
            ConnectCallback = async (_, ct) =>
            {
                var socket = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
                try
                {
                    await socket.ConnectAsync(new UnixDomainSocketEndPoint(socketPath), ct)
                        .ConfigureAwait(false);
                    return new NetworkStream(socket, ownsSocket: true);
                }
                catch
                {
                    socket.Dispose();
                    throw;
                }
            }
        })
        {
            BaseAddress = new Uri("http://localhost/"),
            Timeout = Timeout.InfiniteTimeSpan
        };
    }

    internal static bool IsId(string? value) => value is not null && Id.IsMatch(value);

    internal sealed class Grant(string token, int seconds)
    {
        internal string Token { get; } = token;
        internal int Seconds { get; } = seconds;
    }

    internal async Task<(string SessionId, Grant Grant, bool ContinuousTs)> OpenAsync(string token, string category, string asset,
        string platform, CancellationToken ct)
    {
        if (!Number.IsMatch(category) || !Number.IsMatch(asset)
            || platform is not ("android" or "android_tv" or "ios" or "windows" or "tizen" or "webos"))
            throw new InvalidOperationException("Invalid media identity");
        var body = await SendAsync("open", token,
            new { category_id = category, asset_id = asset, platform },
            "application/json", 4096, ct).ConfigureAwait(false);
        using var json = JsonDocument.Parse(body);
        if (!json.RootElement.TryGetProperty("session_id", out var id)
            || id.ValueKind != JsonValueKind.String || !IsId(id.GetString()))
            throw new InvalidOperationException("Private media unavailable");
        string input = json.RootElement.TryGetProperty("input_format", out var format)
            && format.ValueKind == JsonValueKind.String ? format.GetString()! : "m3u8";
        if (input is not ("m3u8" or "ts"))
            throw new InvalidOperationException("Unknown private input contract");
        return (id.GetString()!, ParseGrant(json.RootElement), input == "ts");
    }

    internal const int FileWindowBytes = 4 * 1024 * 1024;
    internal const long MaxFileBytes = 128L * 1024 * 1024 * 1024;

    internal sealed record FileInfo(long Length, string Mime);

    internal async Task<(string SessionId, Grant Grant, FileInfo File)> OpenFileAsync(
        string token, string kind, string category, string asset, string? series,
        string platform, CancellationToken ct)
    {
        if (!Number.IsMatch(category) || !Number.IsMatch(asset)
            || kind is not ("vod" or "series")
            || (kind == "series" ? series is null || !Number.IsMatch(series) : series is not null)
            || platform is not ("android" or "android_tv" or "ios" or "windows" or "tizen" or "webos"))
            throw new InvalidOperationException("Invalid media identity");
        var body = await SendAsync("open", token,
            new { kind, category_id = category, asset_id = asset, series_id = series, platform },
            "application/json", 4096, ct, finite: true).ConfigureAwait(false);
        using var json = JsonDocument.Parse(body);
        var root = json.RootElement;
        if (!root.TryGetProperty("session_id", out var id) || id.ValueKind != JsonValueKind.String
            || !IsId(id.GetString())
            || !root.TryGetProperty("length", out var length) || !length.TryGetInt64(out var bytes)
            || bytes is < 1 or > MaxFileBytes
            || !root.TryGetProperty("content_type", out var type) || type.ValueKind != JsonValueKind.String
            || type.GetString() is not ("video/mp4" or "video/mp2t" or "video/x-matroska"
                or "video/webm" or "video/quicktime" or "video/x-msvideo" or "video/mpeg"))
            throw new InvalidOperationException("Private finite media unavailable");
        return (id.GetString()!, ParseGrant(root), new FileInfo(bytes, type.GetString()!));
    }

    internal Task<byte[]> ReadFileAsync(string token, string sessionId, FileInfo file,
        long start, int length, CancellationToken ct)
    {
        if (start < 0 || start >= file.Length || length < 1 || length > FileWindowBytes
            || length > file.Length - start)
            throw new InvalidOperationException("Invalid finite media window");
        var range = string.Create(CultureInfo.InvariantCulture,
            $"bytes {start}-{start + length - 1}/{file.Length}");
        return SendAsync("read", token, new { session_id = RequireId(sessionId), start, length },
            file.Mime, length, ct, finite: true, expectedRange: range);
    }

    private static Grant ParseGrant(JsonElement root)
    {
        if (!root.TryGetProperty("media_token", out var token) || token.ValueKind != JsonValueKind.String
            || token.GetString() is not string value || value.Length != 43
            || !value.All(c => char.IsAsciiLetterOrDigit(c) || c is '-' or '_')
            || !root.TryGetProperty("valid_for_seconds", out var ttl) || !ttl.TryGetInt32(out var seconds)
            || seconds is < 30 or > 300)
            throw new InvalidOperationException("Invalid private media grant");
        return new Grant(value, seconds);
    }

    internal async Task<Grant> RenewAsync(string grant, string sessionId, CancellationToken ct, bool finite = false)
    {
        var body = await SendAsync("renew", grant, new { session_id = RequireId(sessionId) },
            "application/json", 4096, ct, finite: finite).ConfigureAwait(false);
        using var json = JsonDocument.Parse(body);
        return ParseGrant(json.RootElement);
    }

    internal const int TsWindowBytes = 188 * 8192;

    internal Task<byte[]> ReadTsAsync(string token, string sessionId, bool probe, CancellationToken ct)
        => SendAsync(probe ? "ts-probe" : "ts-read", token,
            new { session_id = RequireId(sessionId) }, "video/mp2t",
            probe ? 188 * 2048 : TsWindowBytes, ct);

    internal async Task ResetTsAsync(string token, string sessionId, CancellationToken ct)
        => _ = await SendAsync("ts-reset", token,
            new { session_id = RequireId(sessionId) }, null, 0, ct).ConfigureAwait(false);

    internal Task<byte[]> PlaylistAsync(string token, string sessionId, CancellationToken ct)
        => SendAsync("playlist", token, new { session_id = RequireId(sessionId) },
            "application/vnd.apple.mpegurl", 128 * 1024, ct);

    internal Task<byte[]> SegmentAsync(string token, string sessionId, string segmentId, CancellationToken ct)
        => SendAsync("segment", token, new
        {
            session_id = RequireId(sessionId), segment_id = RequireId(segmentId)
        }, "video/mp2t", 8 * 1024 * 1024, ct);

    internal async Task CloseAsync(string token, string sessionId, CancellationToken ct, bool finite = false)
        => _ = await SendAsync("close", token, new { session_id = RequireId(sessionId) },
            null, 0, ct, finite: finite).ConfigureAwait(false);

    private static string RequireId(string id) => IsId(id) ? id
        : throw new InvalidOperationException("Invalid private media identity");

    private async Task<byte[]> SendAsync(string action, string token, object payload,
        string? mime, int cap, CancellationToken ct, bool finite = false, string? expectedRange = null)
    {
        if (string.IsNullOrEmpty(token) || token.Length > 4096
            || token.Any(c => char.IsControl(c) || char.IsWhiteSpace(c)))
            throw new InvalidOperationException("Invalid private account token");
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
        deadline.CancelAfter(TimeSpan.FromSeconds(30));
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Post,
                "_internal/media/" + (finite ? "file/" : "hls/") + action)
            {
                Content = new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json")
            };
            request.Headers.TryAddWithoutValidation("X-Vivoapk-Media-Key", _key);
            request.Headers.TryAddWithoutValidation("Authorization", "Bearer " + token);
            using var response = await _client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead,
                deadline.Token).ConfigureAwait(false);
            if (mime is null)
            {
                if (response.StatusCode != HttpStatusCode.NoContent)
                    throw new InvalidOperationException("Private media unavailable");
                return Array.Empty<byte>();
            }
            if (response.StatusCode != (expectedRange is null ? HttpStatusCode.OK : HttpStatusCode.PartialContent)
                || (expectedRange is not null && (response.Content.Headers.ContentRange?.ToString() != expectedRange
                    || response.Content.Headers.ContentLength != cap))
                || (expectedRange is null && response.Content.Headers.ContentRange is not null)
                || !string.Equals(response.Content.Headers.ContentType?.MediaType, mime,
                    StringComparison.OrdinalIgnoreCase)
                || response.Content.Headers.ContentEncoding.Count != 0
                || response.Content.Headers.ContentLength is long length && (length < 1 || length > cap))
                throw new InvalidOperationException("Private media unavailable");
            await using var input = await response.Content.ReadAsStreamAsync(deadline.Token)
                .ConfigureAwait(false);
            using var output = new MemoryStream();
            var buffer = new byte[32768];
            while (true)
            {
                var count = await input.ReadAsync(buffer.AsMemory(), deadline.Token).ConfigureAwait(false);
                if (count == 0)
                    break;
                if (output.Length + count > cap)
                    throw new InvalidOperationException("Private media response exceeds bound");
                output.Write(buffer, 0, count);
            }
            if (output.Length == 0 || (expectedRange is not null && output.Length != cap)
                || response.Content.Headers.ContentLength is long expected && output.Length != expected)
                throw new InvalidOperationException("Private media response incomplete");
            return output.ToArray();
        }
        catch (Exception ex) when (ex is HttpRequestException or IOException or OperationCanceledException)
        {
            throw new InvalidOperationException("Private media unavailable");
        }
    }

    public void Dispose() => _client.Dispose();
}

