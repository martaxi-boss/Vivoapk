using System.Diagnostics;
using System.Net;
using System.Text.Json;
using Jellyfin.Plugin.VivoapkMegaAuth;

string checkpoint = "fixture configuration";
try
{
    using var data = JsonDocument.Parse(File.ReadAllBytes(args[0]));
    var root = data.RootElement;
    string Get(string name) => root.GetProperty(name).GetString()!;
    using var limit = new CancellationTokenSource(TimeSpan.FromSeconds(75));
    var ct = limit.Token;
    using var backend = new PrivateHlsSocketClient(Get("socket"), Get("key"));
    checkpoint = "loopback binding";
    await using var bridge = await PrivateHlsLoopback.StartAsync(backend, ct);
    var ownerA = Guid.NewGuid();
    var ownerB = Guid.NewGuid();
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
    async Task<(int Video, int Audio, string FirstFrame)> Decode(Uri uri)
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
            _ = await errors;
            var video = lines.Where(l => l.StartsWith("0,", StringComparison.Ordinal)).ToArray();
            int audio = lines.Count(l => l.StartsWith("1,", StringComparison.Ordinal));
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
    var result = await Task.WhenAll(Decode(a.Playlist), Decode(b.Playlist));
    if (result[0].FirstFrame == result[1].FirstFrame)
        throw new InvalidOperationException();
    Console.WriteLine($"VIVOAPK_PRIVATE_LOOPBACK_FFMPEG_TWO_STREAMS=PASS video={result[0].Video}/{result[1].Video} audio={result[0].Audio}/{result[1].Audio}");
    checkpoint = "session close isolation";
    await bridge.CloseAsync(ownerA, a, ct);
    using (var response = await http.GetAsync(a.Playlist, ct))
        if (response.StatusCode != HttpStatusCode.NotFound)
            throw new InvalidOperationException();
    var surviving = await Decode(b.Playlist);
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
