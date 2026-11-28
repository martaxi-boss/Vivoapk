using System.Diagnostics;
using System.Net;
using System.Text.Json;
using System.Text;
using Jellyfin.Plugin.VivoapkMegaAuth;

// Real native FFmpeg live URI shape, with synthetic credentials only.
{
    const string stem = "0123456789abcdef0123456789abcdef";
    const string path = "/synthetic-cache/" + stem + ".m3u8";
    const string token = "synthetic-owned-jellyfin-token";
    const string device = "synthetic device&scope";
    const string session = "11111111-2222-3333-4444-555555555555";
    var item = Guid.Parse("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
    var prefix = "hls/" + stem + "/" + stem;
    var playlist = "#EXTM3U\r\n#EXT-X-MEDIA-SEQUENCE:7\r\n#EXTINF:3.0,\r\n"
        + prefix + "7.ts\r\n#EXTINF:3.0,\r\n" + prefix + "8.ts\r\n";
    if (!PrivateLivePlaylist.TryAuthorize(Encoding.UTF8.GetBytes(playlist), path,
            token, device, session, item, out var encoded))
        throw new InvalidOperationException("Exact native live playlist was not admitted");
    var text = Encoding.UTF8.GetString(encoded);
    var media = text.Split('\n').Select(v => v.TrimEnd('\r'))
        .Where(v => v.Length > 0 && v[0] != '#').ToArray();
    if (media.Length != 2 || !text.StartsWith("#EXTM3U\r\n#EXT-X-MEDIA-SEQUENCE:7\r\n", StringComparison.Ordinal))
        throw new InvalidOperationException("Native HLS tags, sequence or media count changed");
    for (var i = 0; i < media.Length; i++)
    {
        var uri = new Uri("https://synthetic-jellyfin.invalid/Videos/" + item + "/" + media[i]);
        var fields = Microsoft.AspNetCore.WebUtilities.QueryHelpers.ParseQuery(uri.Query);
        if (!uri.AbsolutePath.EndsWith(prefix + (i + 7) + ".ts", StringComparison.Ordinal)
            || fields.Count != 4 || fields["ApiKey"] != token || fields["DeviceId"] != device
            || fields["PlaySessionId"] != session || fields["MediaSourceId"] != item.ToString("N")
            || fields.ContainsKey("api_key"))
            throw new InvalidOperationException("Native segment lost its exact owner/device/session binding");
    }
    foreach (var bad in new[]
    {
        "https://external.invalid/segment.ts", "//external.invalid/segment.ts",
        "/Videos/foreign/segment.ts", "../" + prefix + "7.ts", prefix + "../7.ts",
        prefix + "7.ts?ApiKey=foreign", prefix + "7.ts#fragment", prefix + "-1.ts",
        prefix + "00000000000.ts", "hls/ffffffffffffffffffffffffffffffff/" + stem + "7.ts",
        "hls/" + stem + "/ffffffffffffffffffffffffffffffff7.ts", prefix + "7.mp4",
    })
    {
        if (PrivateLivePlaylist.TryAuthorize(Encoding.UTF8.GetBytes("#EXTM3U\n" + bad + "\n"),
                path, token, device, session, item, out var rejected) || rejected.Length != 0)
            throw new InvalidOperationException("Unbound native playlist reference received a credential");
    }
    foreach (var badToken in new[] { "", "a,b", "a\"b", "a b", "a\nb", new string('x', 513) })
        if (PrivateLivePlaylist.TryAuthorize(Encoding.UTF8.GetBytes(playlist), path,
                badToken, device, session, item, out _))
            throw new InvalidOperationException("Invalid live playlist credential accepted");
    if (PrivateLivePlaylist.TryAuthorize([0xff, 0xfe], path, token, device, session, item, out _)
        || PrivateLivePlaylist.TryAuthorize(new byte[262145], path, token, device, session, item, out _)
        || PrivateLivePlaylist.TryAuthorize(Encoding.UTF8.GetBytes(playlist), path, token, "", session, item, out _)
        || PrivateLivePlaylist.TryAuthorize(Encoding.UTF8.GetBytes(playlist), path, token, device, "forged", item, out _)
        || PrivateLivePlaylist.TryAuthorize(Encoding.UTF8.GetBytes(playlist), path, token, device, session, Guid.Empty, out _))
        throw new InvalidOperationException("Unbounded or unidentified live playlist accepted");
    Console.WriteLine("PRIVATE_NATIVE_LIVE_PLAYLIST_BOUND_QUERY_TESTS=PASS");
}


string checkpoint = "fixture configuration";
try
{
    using var data = JsonDocument.Parse(File.ReadAllBytes(args[0]));
    var root = data.RootElement;
    string Get(string name) => root.GetProperty(name).GetString()!;
    using var limit = new CancellationTokenSource(TimeSpan.FromSeconds(75));
    var ct = limit.Token;
    if (args.Length == 2 && args[1] == "ts-probe")
    {
        checkpoint = "explicit continuous MPEG-TS metadata";
        var uri = new Uri(Get("ts_probe_uri"));
        var metadata = await PrivateMediaProbe.ReadContinuousTsAsync(Get("ffprobe"), uri, ct);
        if (metadata.Container != "mpegts" || metadata.DurationTicks is not null
            || metadata.Streams.Count != 2
            || !metadata.Streams.Any(s => s.Video && s.Codec == "mpeg2video"
                && s.Width == 160 && s.Height == 90)
            || !metadata.Streams.Any(s => !s.Video && s.Codec == "mp2"
                && s.SampleRate == 48000))
            throw new InvalidOperationException("Unexpected typed TS metadata");
        foreach (var invalid in new[] {
            new Uri(uri, "index.m3u8"), new Uri(uri, "stream.ts?unexpected=1"),
            new UriBuilder(uri) { Host = "192.0.2.1" }.Uri,
        })
        {
            try
            {
                await PrivateMediaProbe.ReadContinuousTsAsync(Get("ffprobe"), invalid, ct);
                throw new Exception("Unadmitted TS path accepted");
            }
            catch (InvalidOperationException) { }
        }
        if (metadata.AncillaryStreams is not { Count: 1 }
            || metadata.AncillaryStreams[0].Index != 2
            || metadata.AncillaryStreams[0].Codec != "dvb_teletext")
            throw new InvalidOperationException("Generated TS ancillary metadata lost");

        // Codec combination observed on the authorized real source; all
        // numeric values here are an explicit synthetic parser fixture.
        const string typed = """
            {"format":{"format_name":"mpegts"},"streams":[{"index":0,"codec_type":"video","codec_name":"hevc","width":1920,"height":1080,"avg_frame_rate":"25/1"},{"index":1,"codec_type":"audio","codec_name":"aac","channels":2,"sample_rate":"48000"},{"index":2,"codec_type":"subtitle","codec_name":"dvb_teletext"}]}
            """;
        var admitted = PrivateMediaProbe.Parse(Encoding.UTF8.GetBytes(typed), false, true);
        if (admitted.Streams.Count != 2 || admitted.Streams.Single(s => s.Video).Codec != "hevc"
            || admitted.Streams.Single(s => !s.Video).Codec != "aac"
            || admitted.AncillaryStreams is not { Count: 1 })
            throw new InvalidOperationException("Ancillary track was mapped as audio/video");
        foreach (var invalid in new[] {
            typed.Replace("dvb_teletext", "unvalidated_subtitle"),
            typed.Replace("\"codec_type\":\"subtitle\"", "\"codec_type\":\"data\""),
            typed.Replace("\"index\":2", "\"index\":1"),
            typed.Replace("\"codec_type\":\"audio\",\"codec_name\":\"aac\"",
                "\"codec_type\":\"subtitle\",\"codec_name\":\"dvb_teletext\""),
        })
        {
            try
            {
                PrivateMediaProbe.Parse(Encoding.UTF8.GetBytes(invalid), false, true);
                throw new Exception("Unknown, duplicate or audio-less metadata accepted");
            }
            catch (InvalidOperationException) { }
        }
        Console.WriteLine("VIVOAPK_PRIVATE_LOOPBACK_NATIVE_TS_TELETEXT_AND_DENIAL=PASS");
        Console.WriteLine("VIVOAPK_PRIVATE_LOOPBACK_NATIVE_TS_PROBE=PASS");
        return 0;
    }
    if (args.Length == 2 && args[1] == "finite-probe")
    {
        checkpoint = "delayed finite response metadata";
        var metadata = await PrivateMediaProbe.ReadAsync(Get("ffprobe"),
            new Uri(Get("file_probe_uri")), finite: true, ct: ct);
        if (metadata.Container != "mp4"
            || metadata.DurationTicks is not { } duration
            || duration < TimeSpan.FromSeconds(1).Ticks
            || duration > TimeSpan.FromSeconds(3).Ticks
            || metadata.Streams.Count != 2
            || !metadata.Streams.Any(s => s.Video && s.Codec == "h264"
                && s.Width == 160 && s.Height == 90)
            || !metadata.Streams.Any(s => !s.Video && s.Codec == "aac"
                && s.SampleRate == 48000))
            throw new InvalidOperationException("Unexpected delayed finite metadata");
        Console.WriteLine("VIVOAPK_PRIVATE_DELAYED_FINITE_PROBE=PASS");
        return 0;
    }
    using var backend = new PrivateHlsSocketClient(Get("socket"), Get("key"));
    checkpoint = "loopback binding";
    await using var bridge = await PrivateHlsLoopback.StartAsync(backend, ct);
    var ownerA = Guid.NewGuid();
    var ownerB = Guid.NewGuid();
    if (args.Length == 2 && args[1] == "file")
    {
        checkpoint = "bounded finite delivery pacing";
        if (PrivateHlsLoopback.NextFileWindowBytes(512 * 1024, 1100) != 512 * 1024
            || PrivateHlsLoopback.NextFileWindowBytes(512 * 1024, 10) != 4 * 1024 * 1024
            || PrivateHlsLoopback.NextFileWindowBytes(4 * 1024 * 1024, 0) != 4 * 1024 * 1024)
            throw new InvalidOperationException("Finite delivery pacing outside bounds");
        checkpoint = "finite movie and episode sessions";
        return await FiniteFixture.RunAsync(bridge, Get, ownerA, ownerB, ct);
    }
    checkpoint = "two private sessions";
    var a = await bridge.OpenAsync(ownerA, Get("a"), "10", "101", "android", ct);
    var b = await bridge.OpenAsync(ownerB, Get("b"), "20", "101", "tizen", ct);
    using var http = new HttpClient(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false });
    checkpoint = "local capability and playlist sanitization";
    var pa = await http.GetStringAsync(a.Playlist, ct);
    var pb = await http.GetStringAsync(b.Playlist, ct);
    if (pa.Contains("fixture-", StringComparison.Ordinal) || pb.Contains("fixture-", StringComparison.Ordinal)
        || pa.Contains("https:", StringComparison.Ordinal) || pb.Contains("https:", StringComparison.Ordinal))
        throw new InvalidOperationException();
    var segmentA = pa.Split('\n').First(l => l.StartsWith("segment/", StringComparison.Ordinal));
    var segmentB = pb.Split('\n').First(l => l.StartsWith("segment/", StringComparison.Ordinal));
    if (segmentA == segmentB)
        throw new InvalidOperationException();
    using (var response = await http.GetAsync(new Uri(a.Playlist, "?unexpected=1"), ct))
        if (response.StatusCode != HttpStatusCode.NotFound)
            throw new InvalidOperationException();
    using (var response = await http.GetAsync(new Uri(a.Playlist,
        "../" + new string('0', 48) + "/index.m3u8"), ct))
        if (response.StatusCode != HttpStatusCode.NotFound)
            throw new InvalidOperationException();
    try
    {
        await bridge.CloseAsync(ownerB, a, ct);
        throw new Exception("Cross-owner close accepted");
    }
    catch (InvalidOperationException) { }

    // Use real FFmpeg to read both opaque HLS inputs concurrently, not fake TS.
    checkpoint = "two simultaneous FFmpeg decodes";
    async Task<(int Video, int Audio, string FirstFrame)> Decode(Uri uri, string label)
    {
        var start = new ProcessStartInfo(Get("ffmpeg")) {
            RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false
        };
        foreach (var arg in new[] { "-hide_banner", "-nostdin", "-v", "error",
                "-protocol_whitelist", "http,tcp", "-threads", "1", "-i", uri.AbsoluteUri,
                "-map", "0:v:0", "-map", "0:a:0", "-f", "framemd5", "pipe:1" })
            start.ArgumentList.Add(arg);
        using var process = Process.Start(start) ?? throw new InvalidOperationException();
        try
        {
            var output = process.StandardOutput.ReadToEndAsync(ct);
            var errors = process.StandardError.ReadToEndAsync(ct);
            await process.WaitForExitAsync(ct);
            var lines = (await output).Split('\n');
            // Error text may include a private loopback capability; never print it.
            var diagnostic = await errors;
            var video = lines.Where(l => l.StartsWith("0,", StringComparison.Ordinal)).ToArray();
            int audio = lines.Count(l => l.StartsWith("1,", StringComparison.Ordinal));
            // Preserve the original frame minima and require a real zero
            // exit code. A fixed vocabulary reports why any reader failed.
            var markers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                ["http_404"] = "404 Not Found", ["http_429"] = "429 Too Many Requests",
                ["http_500"] = "500 Internal Server Error", ["http_503"] = "503 Service Unavailable",
                ["open_segment"] = "Failed to open segment", ["input_error"] = "Error opening input",
                ["connection"] = "Connection refused", ["timeout"] = "timed out",
                ["resource"] = "Resource temporarily unavailable", ["invalid_data"] = "Invalid data",
                ["decoder"] = "Error while decoding", ["output"] = "Error opening output",
                ["protocol"] = "not on whitelist",
            };
            Console.WriteLine("VIVOAPK_PRIVATE_LOOPBACK_DECODE_OBSERVATION=" +
                JsonSerializer.Serialize(new
                {
                    fixture = label, exit = process.ExitCode, video = video.Length, audio,
                    diagnostic_bytes = diagnostic.Length,
                    markers = markers.Where(pair => diagnostic.Contains(pair.Value,
                        StringComparison.OrdinalIgnoreCase)).Select(pair => pair.Key).ToArray(),
                }));
            if (process.ExitCode != 0 || video.Length < 100 || audio < 100)
                throw new InvalidOperationException();
            return (video.Length, audio, video[0].Split(',').Last().Trim());
        }
        finally
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                await process.WaitForExitAsync();
            }
        }
    }
    var result = await Task.WhenAll(Decode(a.Playlist, "a"), Decode(b.Playlist, "b"));
    if (result[0].FirstFrame == result[1].FirstFrame)
        throw new InvalidOperationException();
    Console.WriteLine($"VIVOAPK_PRIVATE_LOOPBACK_FFMPEG_TWO_STREAMS=PASS video={result[0].Video}/{result[1].Video} audio={result[0].Audio}/{result[1].Audio}");
    if (args.Length == 2 && args[1] == "lifecycle")
    {
        // Wait beyond the actual initial grant and account-token lifetime.
        // Keep A active without touching B. A's scoped token must rotate
        // without changing the local playlist or reopening the account.
        checkpoint = "real grant renewal and abandonment";
        var started = Stopwatch.StartNew();
        while (started.Elapsed < TimeSpan.FromSeconds(42))
        {
            await Task.Delay(TimeSpan.FromSeconds(4), ct);
            using var response = await http.GetAsync(a.Playlist, ct);
            if (response.StatusCode != HttpStatusCode.OK)
                throw new InvalidOperationException();
        }
        using (var expired = await http.GetAsync(b.Playlist, ct))
            if (expired.StatusCode != HttpStatusCode.NotFound)
                throw new InvalidOperationException();
        var resumed = await Decode(a.Playlist, "a_after_initial_expiry");
        if (resumed.Video != result[0].Video || resumed.Audio != result[0].Audio
            || resumed.FirstFrame != result[0].FirstFrame)
            throw new InvalidOperationException();
        await bridge.CloseAsync(ownerA, a, ct);
        Console.WriteLine("VIVOAPK_PRIVATE_LOOPBACK_RENEWED_SAME_LEASE_AND_EXPIRED_OTHER=PASS");
        return 0;
    }
    checkpoint = "session close isolation";
    await bridge.CloseAsync(ownerA, a, ct);
    using (var response = await http.GetAsync(a.Playlist, ct))
        if (response.StatusCode != HttpStatusCode.NotFound)
            throw new InvalidOperationException();
    var surviving = await Decode(b.Playlist, "b_after_close");
    if (surviving.Video != result[1].Video || surviving.Audio != result[1].Audio)
        throw new InvalidOperationException();
    await bridge.CloseAsync(ownerB, b, ct);
    using (var response = await http.GetAsync(b.Playlist, ct))
        if (response.StatusCode != HttpStatusCode.NotFound)
            throw new InvalidOperationException();
    Console.WriteLine("VIVOAPK_PRIVATE_LOOPBACK_CLOSE_ISOLATION=PASS");
    Console.WriteLine("VIVOAPK_PRIVATE_LOOPBACK_NO_PRODUCTION_ACTIVATION=PASS");
    return 0;
}
catch (Exception)
{
    Console.Error.WriteLine("VIVOAPK_PRIVATE_LOOPBACK_FAILED_AT=" + checkpoint);
    return 1;
}
