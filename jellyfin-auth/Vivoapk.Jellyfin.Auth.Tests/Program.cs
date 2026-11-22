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
Console.WriteLine($"VivoAPK Jellyfin auth synthetic transport checks: {passed}/{passed} PASS");

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
