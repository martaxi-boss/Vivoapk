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
internal sealed record MegaPreviewEntry(string Id, string Title);

public sealed class MegaResolverClient
{
    private const int MaxResponseBytes = 8192;
    public const string SocketPath = "/srv/projects/Vivoapk/run/backend.sock";
    private const string MonitorKeyPath = "/srv/projects/Vivoapk/run/entitlement-monitor.key";
    private readonly HttpClient _client;
    private readonly Func<string?> _monitorKeyProvider;

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
        string username, string password, CancellationToken cancellationToken = default)
        => await ResolveAsync(username, password, cancellationToken).ConfigureAwait(false) is not null;

    internal async Task<MegaResolvedSession?> ResolveAsync(
        string username, string password, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(username) || username.Length > 128 ||
            string.IsNullOrWhiteSpace(password) || password.Length > 2048)
            return null;
        try
        {
            var body = JsonSerializer.Serialize(new
            {
                username,
                password,
                platform = "android"
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

    internal async Task<IReadOnlyList<MegaPreviewEntry>> PreviewAsync(
        string? token, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrEmpty(token) || token.Length > 4096)
            return Array.Empty<MegaPreviewEntry>();
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Post, "v1/catalog/preview")
            {
                Content = new StringContent(
                    "{\"kind\":\"live\",\"platform\":\"android\"}",
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
            var buf = new byte[1024];
            while (true)
            {
                var count = await input.ReadAsync(buf, cancellationToken).ConfigureAwait(false);
                if (count == 0)
                    break;
                if (output.Length + count > MaxResponseBytes)
                    return Array.Empty<MegaPreviewEntry>();
                output.Write(buf, 0, count);
            }
            output.Position = 0;
            using var doc = JsonDocument.Parse(output);
            if (!doc.RootElement.TryGetProperty("items", out var items)
                || items.ValueKind != JsonValueKind.Array || items.GetArrayLength() > 5)
                return Array.Empty<MegaPreviewEntry>();
            var result = new List<MegaPreviewEntry>();
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
                    || string.IsNullOrWhiteSpace(name) || name.Length > 250)
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
