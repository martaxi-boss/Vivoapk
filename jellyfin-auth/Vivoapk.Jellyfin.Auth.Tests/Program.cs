using System.Net;
using System.Text.Json;
using Jellyfin.Plugin.VivoapkMegaAuth;

var cases = new (string Response, HttpStatusCode Status, bool Expected)[]
{
    ("{\"code\":\"SUCCESS\",\"xtream_base_url\":\"http://hidden.example\"}", HttpStatusCode.OK, true),
    ("{\"code\":\"INVALID_CREDENTIALS\"}", HttpStatusCode.OK, false),
    ("{\"code\":\"EXPIRED\"}", HttpStatusCode.OK, false),
    ("{\"code\":\"UPSTREAM_ERROR\"}", HttpStatusCode.OK, false),
    ("{\"code\":\"SUCCESS\"}", HttpStatusCode.Unauthorized, false),
    ("not-json", HttpStatusCode.OK, false),
    ("{\"wrong_field\":\"SUCCESS\"}", HttpStatusCode.OK, false),
    (new string('x', 9000), HttpStatusCode.OK, false),
};
int passed = 0;
foreach (var item in cases)
{
    using var handler = new SyntheticHandler(item.Response, item.Status);
    using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost/") };
    var resolver = new MegaResolverClient(client);
    bool actual = await resolver.ValidateAsync("synthetic-user", "synthetic-password");
    if (actual != item.Expected || handler.Count != 1)
        throw new InvalidOperationException("Synthetic resolver regression.");
    passed++;
}
using (var handler = new SyntheticHandler("{\"code\":\"SUCCESS\"}", HttpStatusCode.OK))
{
    using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost/") };
    var resolver = new MegaResolverClient(client);
    if (await resolver.ValidateAsync("", "irrelevant") || handler.Count != 0)
        throw new InvalidOperationException("Empty username made upstream request.");
    passed++;
}
var monitorKey = "synthetic-private-monitor-key-1234567890";
foreach (var (status, expected) in new[]
{
    ("ACTIVE", EntitlementState.Active),
    ("EXPIRED", EntitlementState.Expired),
    ("DISABLED", EntitlementState.Disabled),
    ("UPSTREAM_UNKNOWN", EntitlementState.Unavailable)
})
{
    using var handler = new SyntheticEntitlementHandler(status, monitorKey);
    using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost/") };
    var resolver = new MegaResolverClient(client, () => monitorKey);
    if (await resolver.CheckEntitlementAsync("synthetic-user") != expected || handler.Count != 1)
        throw new InvalidOperationException("Synthetic entitlement monitor regression.");
    passed++;
}
using (var handler = new SyntheticEntitlementHandler("EXPIRED", monitorKey))
{
    using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost/") };
    var resolver = new MegaResolverClient(client, () => null);
    if (await resolver.CheckEntitlementAsync("synthetic-user") != EntitlementState.Unavailable
        || handler.Count != 0)
        throw new InvalidOperationException("Monitor sent request without a private key.");
    passed++;
}
Console.WriteLine($"VivoAPK Jellyfin auth and monitor synthetic checks: {passed}/{passed} PASS");

internal sealed class SyntheticHandler(string body, HttpStatusCode status) : HttpMessageHandler
{
    public int Count { get; private set; }

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        Count++;
        if (request.Method != HttpMethod.Post ||
            request.RequestUri?.AbsolutePath != "/v1/session/resolve")
            throw new InvalidOperationException("Wrong endpoint.");

        string requestBody = await request.Content!.ReadAsStringAsync(cancellationToken);
        using var json = JsonDocument.Parse(requestBody);
        if (json.RootElement.GetProperty("platform").GetString() != "android" ||
            json.RootElement.GetProperty("username").GetString() != "synthetic-user" ||
            json.RootElement.GetProperty("password").GetString() != "synthetic-password")
            throw new InvalidOperationException("Incorrect automatic DNS/username/password request.");

        return new HttpResponseMessage(status) { Content = new StringContent(body) };
    }
}

internal sealed class SyntheticEntitlementHandler(string state, string secret) : HttpMessageHandler
{
    public int Count { get; private set; }

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        Count++;
        if (request.Method != HttpMethod.Post ||
            request.RequestUri?.AbsolutePath != "/_internal/entitlement/status" ||
            !request.Headers.TryGetValues("X-Vivoapk-Monitor-Key", out var keys) ||
            keys.Single() != secret)
            throw new InvalidOperationException("Private monitor protocol mismatch.");
        using var body = JsonDocument.Parse(
            await request.Content!.ReadAsStringAsync(cancellationToken));
        if (body.RootElement.GetProperty("username").GetString() != "synthetic-user")
            throw new InvalidOperationException("Monitor identity mismatch.");
        return new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(JsonSerializer.Serialize(new { state }))
        };
    }
}
