using Microsoft.AspNetCore.Http;
using System.Net;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using Jellyfin.Plugin.VivoapkMegaAuth;

// Regression: a live TS probe may report only the audio bit_rate. Its actual
// video packets must determine the channel rate rather than selecting 234p.
{
    var probeType = typeof(MegaResolverClient).Assembly.GetType(
        "Jellyfin.Plugin.VivoapkMegaAuth.PrivateMediaProbe", true)!;
    var probeParse = probeType.GetMethod("Parse", BindingFlags.NonPublic | BindingFlags.Static)!;
    var probeFixture = new
    {
        format = new { format_name = "mpegts" },
        streams = new object[]
        {
            new { index = 0, codec_type = "video", codec_name = "h264", width = 1280,
                height = 720, avg_frame_rate = "25/1", profile = "High", level = 40 },
            new { index = 1, codec_type = "audio", codec_name = "aac", channels = 2,
                sample_rate = "48000", bit_rate = "160000", profile = "LC" },
        },
        packets = new object[]
        {
            new { stream_index = 0, size = "10000", pts_time = "0.00", duration_time = "0.04" },
            new { stream_index = 0, size = "10000", pts_time = "0.04", duration_time = "0.04" },
            new { stream_index = 0, size = "10000", pts_time = "0.08", duration_time = "0.04" },
            new { stream_index = 1, size = "480", pts_time = "0.000", duration_time = "0.024" },
            new { stream_index = 1, size = "480", pts_time = "0.024", duration_time = "0.024" },
            new { stream_index = 1, size = "480", pts_time = "0.048", duration_time = "0.024" },
            new { stream_index = 1, size = "480", pts_time = "0.072", duration_time = "0.024" },
            new { stream_index = 1, size = "480", pts_time = "0.096", duration_time = "0.024" },
        },
    };
    var probeResult = probeParse.Invoke(null, new object[] {
        JsonSerializer.SerializeToUtf8Bytes(probeFixture), false, true })!;
    if ((int?)probeResult.GetType().GetProperty("Bitrate")!.GetValue(probeResult) != 2160000)
        throw new InvalidOperationException("Live TS total bitrate omitted encoded video packets");
    if (probeResult.GetType().GetProperty("DurationTicks")!.GetValue(probeResult) is not null)
        throw new InvalidOperationException("Probe sample incorrectly became live channel duration");
    Console.WriteLine("VIVOAPK_LIVE_MEASURED_VIDEO_BITRATE=PASS");
}

// Exact dual transport used by official native players on server HLS URLs.
{
    const string own = "synthetic-native-owner-token";
    const string foreign = "synthetic-native-foreign-token";
    var type = typeof(MegaResolverClient).Assembly.GetType(
        "Jellyfin.Plugin.VivoapkMegaAuth.NativePlaybackAuthorization", true)!;
    var parse = type.GetMethod("TryGetExplicitToken", BindingFlags.NonPublic | BindingFlags.Static)!;
    (bool Allowed, string Token) Explicit(string query, params (string Key, string Value)[] headers)
    {
        var context = new Microsoft.AspNetCore.Http.DefaultHttpContext();
        context.Request.QueryString = new Microsoft.AspNetCore.Http.QueryString(query);
        foreach (var pair in headers)
            context.Request.Headers.Append(pair.Key, pair.Value);
        object?[] arguments = { context.Request, null };
        var allowed = (bool)parse.Invoke(null, arguments)!;
        return (allowed, arguments[1] as string ?? "");
    }
    string Header(string value) => "MediaBrowser Client=\"Jellyfin for Android\", DeviceId=\"synthetic-device\", Version=\"2.7.3\", Token=\"" + value + "\"";
    foreach (var credentialCase in new[]
    {
        Explicit("?ApiKey=" + own),
        Explicit("", ("Authorization", Header(own))),
        Explicit("?ApiKey=" + own, ("Authorization", Header(own))),
        Explicit("?ApiKey=" + own, ("Authorization", Header(own)), ("X-Emby-Token", own)),
    })
        if (!credentialCase.Allowed || credentialCase.Token != own)
            throw new InvalidOperationException("Identical native credentials were rejected");
    foreach (var credentialCase in new[]
    {
        Explicit("?ApiKey=" + own, ("Authorization", Header(foreign))),
        Explicit("?ApiKey=" + foreign, ("Authorization", Header(own))),
        Explicit("?ApiKey=" + own, ("Authorization", Header(own)), ("X-Emby-Token", foreign)),
        Explicit("?ApiKey=" + own + "&ApiKey=" + own),
        Explicit("?ApiKey=" + own + "&api_key=" + own),
        Explicit("?ApiKey=" + own, ("Authorization", Header(own) + ", Token=\"" + own + "\"")),
        Explicit("?ApiKey=" + own, ("Authorization", "Bearer " + own)),
        Explicit("?ApiKey=" + own, ("Authorization", "MediaBrowser Client=\"metadata only\"")),
    })
        if (credentialCase.Allowed)
            throw new InvalidOperationException("Ambiguous native credentials were accepted");
    Console.WriteLine("VIVOAPK_NATIVE_IDENTICAL_QUERY_HEADER_TOKENS=PASS");
}

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
foreach (var (header, agent, platform) in new (string, string, string)[]
{
    ("MediaBrowser Client=\"Jellyfin for Tizen\", Device=\"Samsung TV\"", "", "tizen"),
    ("MediaBrowser Client=\"Jellyfin WebOS\", Device=\"LG TV\"", "", "webos"),
    ("MediaBrowser Client=\"Jellyfin\", Device=\"Android TV\"", "", "android_tv"),
    ("MediaBrowser Client=\"Jellyfin\", Device=\"iPhone\"", "", "ios"),
    ("MediaBrowser Client=\"Jellyfin Media Player\", Device=\"Windows\"", "", "windows"),
    ("MediaBrowser Client=\"Jellyfin\", Device=\"Android\"", "", "android"),
    ("MediaBrowser Client=\"Jellyfin Web\"", "Mozilla/5.0 (Tizen Smart TV)", "tizen"),
    ("MediaBrowser Client=\"Jellyfin Web\"", "Mozilla/5.0 (Web0S LG SmartTV)", "webos"),
    ("", "", "android")
})
{
    if (JellyfinClientPlatformDetector.FromHeaders(header, agent) != platform)
        throw new InvalidOperationException("Jellyfin client platform detection regression.");
    using var handler = new SyntheticHandler(
        "{\"code\":\"SUCCESS\",\"xtream_base_url\":\"http://hidden.example\"}", HttpStatusCode.OK, platform);
    using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost/") };
    var resolver = new MegaResolverClient(client);
    if (!await resolver.ValidateAsync("synthetic-user", "synthetic-password", platform: platform)
        || handler.Count != 1)
        throw new InvalidOperationException("Platform-specific Mega DNS resolution failed.");
    passed++;
}
using (var handler = new SyntheticHandler("{\"code\":\"SUCCESS\"}", HttpStatusCode.OK))
{
    using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost/") };
    var resolver = new MegaResolverClient(client);
    if (await resolver.ValidateAsync("synthetic-user", "synthetic-password", platform: "unknown")
        || handler.Count != 0)
        throw new InvalidOperationException("Unsupported client platform reached backend.");
    passed++;
}
Console.WriteLine($"VivoAPK Jellyfin auth and monitor synthetic checks: {passed}/{passed} PASS");

// Read the actual ordinary plugin DLL, not a source-linked harness or a
// reflection load of all Jellyfin-dependent types. The shared guard and
// regular opt-in must exist; the fixture-only transport must not.
var assembly = typeof(MegaResolverClient).Assembly;
using (var dll = File.OpenRead(assembly.Location))
using (var pe = new PEReader(dll))
{
    var reader = pe.GetMetadataReader();
    var names = reader.TypeDefinitions.Select(h =>
        reader.GetString(reader.GetTypeDefinition(h).Name)).ToHashSet(StringComparer.Ordinal);
    if (!names.Contains("PrivateNativeHls") || !names.Contains("PrivateLiveRecovery")
        || !names.Contains("PrivatePlaybackResponse") || names.Contains("PrivateHlsNativeLab")
        || !names.Contains("NativePlaybackFilter") || !names.Contains("NativePrivatePlaybackMode"))
        throw new InvalidOperationException("Ordinary plugin shared/fixture type boundary failed.");
}
Console.WriteLine("VIVOAPK_REGULAR_DLL_SHARED_NATIVE_CORE=PASS");

// Exercise the actual common response sanitizer in the normal assembly.
var sanitizer = assembly.GetType("Jellyfin.Plugin.VivoapkMegaAuth.PrivatePlaybackResponse", true)!
    .GetMethod("SanitizePlaybackInfo", BindingFlags.Static | BindingFlags.NonPublic)
    ?? throw new InvalidOperationException("Shared sanitizer not found.");
var response = JsonNode.Parse("""
    {"PlaySessionId":"safe","MediaSources":[{"Id":"safe-id","Path":"http://127.0.0.1:12345/vivoapk/opaque/index.m3u8",
    "EncoderPath":"http://127.0.0.1:12345/vivoapk/opaque/index.m3u8","TranscodingUrl":"/Videos/item/master.m3u8"}]}
    """)!;
sanitizer.Invoke(null, [response]);
var result = response.ToJsonString();
if (result.Contains("/vivoapk/", StringComparison.OrdinalIgnoreCase)
    || !result.Contains("/Videos/item/master.m3u8", StringComparison.Ordinal)
    || response["MediaSources"]![0]!["Id"]!.GetValue<string>() != "safe-id")
    throw new InvalidOperationException("Normal DLL leaked private source or removed native playback route.");
foreach (var path in new[] {
    "http://127.0.0.1:12345/vivoapk/opaque/index.m3u8",
    "http://127.0.0.1:12345/%2fvivoapk%2fopaque",
    "/srv/projects/Vivoapk/run/backend.sock",
    "private-hls-fixture.key"})
{
    var unsafeResponse = new JsonObject { ["TranscodingUrl"] = path };
    bool refused = false;
    try { sanitizer.Invoke(null, [unsafeResponse]); }
    catch (TargetInvocationException ex) when (ex.InnerException is InvalidOperationException)
    { refused = true; }
    if (!refused)
        throw new InvalidOperationException("Normal DLL accepted unexpected private source reference.");
}
Console.WriteLine("VIVOAPK_REGULAR_DLL_PRIVATE_RESPONSE_SANITATION=PASS");


internal sealed class SyntheticHandler(string body, HttpStatusCode status, string expectedPlatform = "android") : HttpMessageHandler
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
        if (json.RootElement.GetProperty("platform").GetString() != expectedPlatform ||
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
