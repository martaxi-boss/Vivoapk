using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// Calls the previously deployed VivoAPK authentication service over a Unix socket.
/// No Mega URLs, provider tokens, passwords or catalogue data are logged or cached.
/// </summary>
public sealed class MegaResolverClient
{
    private const int MaxResponseBytes = 8192;
    public const string SocketPath = "/srv/projects/Vivoapk/run/backend.sock";
    private readonly HttpClient _client;

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
    }

    // Injectable for offline protocol tests, never used by the plugin registration.
    public MegaResolverClient(HttpClient client)
    {
        _client = client;
    }

    public async Task<bool> ValidateAsync(string username, string password, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(username) || username.Length > 128 ||
            string.IsNullOrWhiteSpace(password) || password.Length > 2048)
        {
            return false;
        }

        try
        {
            // The platform is internal. The customer never enters a Mega OTT domain.
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
                request, HttpCompletionOption.ResponseHeadersRead, cancellationToken).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
                return false;

            await using var input = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
            using var output = new MemoryStream();
            var buffer = new byte[1024];
            while (true)
            {
                var count = await input.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
                if (count == 0)
                    break;
                if (output.Length + count > MaxResponseBytes)
                    return false;
                output.Write(buffer, 0, count);
            }

            output.Position = 0;
            using var json = JsonDocument.Parse(output);
            return json.RootElement.ValueKind == JsonValueKind.Object
                && json.RootElement.TryGetProperty("code", out var code)
                && code.ValueKind == JsonValueKind.String
                && code.GetString() == "SUCCESS";
        }
        catch (HttpRequestException)
        {
            return false;
        }
        catch (TaskCanceledException)
        {
            return false;
        }
        catch (JsonException)
        {
            return false;
        }
        catch (IOException)
        {
            return false;
        }
    }
}
