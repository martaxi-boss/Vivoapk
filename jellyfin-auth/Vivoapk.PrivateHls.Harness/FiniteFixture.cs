using System.Diagnostics;
using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using Jellyfin.Plugin.VivoapkMegaAuth;

internal static class FiniteFixture
{
    internal static async Task<int> RunAsync(PrivateHlsLoopback bridge, Func<string, string> get,
        Guid ownerA, Guid ownerB, CancellationToken ct)
    {
        var a = await bridge.OpenFileAsync(ownerA, get("a"), "vod", "10", "501", null, "ios", ct);
        var b = await bridge.OpenFileAsync(ownerB, get("b"), "series", "20", "90", "401", "webos", ct);
        using var http = new HttpClient(new SocketsHttpHandler
        {
            UseProxy = false, AllowAutoRedirect = false,
            // Disposing the deliberately unread test response must close its
            // connection rather than silently draining it in the background.
            MaxResponseDrainSize = 0
        });
        async Task DrainReaders(Guid owner, PrivateHlsLoopback.Lease lease, string label)
        {
            var deadline = Stopwatch.StartNew();
            while (bridge.InspectFileReaders(owner, lease).Active != 0)
            {
                if (deadline.Elapsed > TimeSpan.FromSeconds(10))
                    throw new InvalidOperationException("Previous private file readers did not retire");
                await Task.Delay(20, ct);
            }
            var stats = bridge.InspectFileReaders(owner, lease);
            Console.WriteLine($"VIVOAPK_PRIVATE_LOOPBACK_FILE_READER_STATS={label} active={stats.Active} peak={stats.Peak} waits={stats.Waits} timeouts={stats.Timeouts} cancelled={stats.Cancelled} failures={stats.Failures}");
            if (stats.Peak > 3 || stats.Timeouts != 0 || stats.Failures != 0)
                throw new InvalidOperationException("Private reader operation failed");
        }
        // Probe through the actual protected lease, not the original file,
        // and check the generated fixture's real metadata. No fixture codec
        // or dimensions are passed to the production candidate parser.
        var probe = Path.Combine(Path.GetDirectoryName(get("ffmpeg"))!, "ffprobe");
        var observed = await Task.WhenAll(bridge.ProbeAsync(ownerA, a, probe, ct),
            bridge.ProbeAsync(ownerB, b, probe, ct));
        foreach (var info in observed)
        {
            var video = info.Streams.Single(s => s.Video);
            var audio = info.Streams.Single(s => !s.Video);
            if (info.Container != "mp4" || info.DurationTicks is not long ticks
                || Math.Abs(ticks - TimeSpan.FromSeconds(12).Ticks) > TimeSpan.FromMilliseconds(100).Ticks
                || video.Codec != "h264" || video.Width != 640 || video.Height != 360
                || video.FrameRate != 25 || audio.Codec != "aac" || audio.Channels != 1
                || audio.SampleRate != 48000)
                throw new InvalidOperationException("Private media metadata differs from fixture");
        }
        await Task.WhenAll(DrainReaders(ownerA, a, "movie_after_probe"),
            DrainReaders(ownerB, b, "episode_after_probe"));
        // Foreign ownership and arbitrary local URL must be rejected before
        // launching a probe, independent of the caller's guessed media path.
        try
        {
            await bridge.ProbeAsync(ownerB, a, probe, ct);
            throw new Exception("Cross-owner probe accepted");
        }
        catch (InvalidOperationException) { }
        try
        {
            var forged = new PrivateHlsLoopback.Lease(a.Id,
                new Uri(a.Playlist, "file?unexpected=1"));
            await bridge.ProbeAsync(ownerA, forged, probe, ct);
            throw new Exception("Forged probe accepted");
        }
        catch (InvalidOperationException) { }
        Console.WriteLine("VIVOAPK_PRIVATE_LOOPBACK_PROBED_METADATA_AND_DENIAL=PASS");
        var fixtures = new[] { (Lease: a, Reference: get("movie_reference"), Label: "movie"),
                              (Lease: b, Reference: get("episode_reference"), Label: "episode") };
        foreach (var item in fixtures)
        {
            using var head = await http.SendAsync(new HttpRequestMessage(HttpMethod.Head, item.Lease.Playlist), ct);
            long total = new FileInfo(item.Reference).Length;
            if (head.StatusCode != HttpStatusCode.OK || head.Content.Headers.ContentLength != total
                || !head.Headers.AcceptRanges.Contains("bytes") || total <= PrivateHlsSocketClient.FileWindowBytes)
                throw new InvalidOperationException("Finite HEAD failed");
            // Verify a single response spanning multiple backend windows,
            // including a backwards seek to the very beginning afterward.
            long first = 0, last = PrivateHlsSocketClient.FileWindowBytes + 1023;
            foreach (var (start, end) in new[] { (first, last), (total - 512, total - 1), (0L, 63L) })
            {
                using var request = new HttpRequestMessage(HttpMethod.Get, item.Lease.Playlist);
                request.Headers.Range = new RangeHeaderValue(start, end);
                using var response = await http.SendAsync(request, ct);
                if (response.StatusCode != HttpStatusCode.PartialContent
                    || response.Content.Headers.ContentRange?.From != start
                    || response.Content.Headers.ContentRange?.To != end
                    || response.Content.Headers.ContentRange?.Length != total)
                    throw new InvalidOperationException("Finite range metadata failed");
                byte[] actual = await response.Content.ReadAsByteArrayAsync(ct);
                await using var input = File.OpenRead(item.Reference);
                input.Position = start;
                var expected = new byte[checked((int)(end - start + 1))];
                await input.ReadExactlyAsync(expected, ct);
                if (!CryptographicOperations.FixedTimeEquals(SHA256.HashData(actual), SHA256.HashData(expected)))
                    throw new InvalidOperationException("Finite range content failed");
            }
            using var invalid = new HttpRequestMessage(HttpMethod.Get, item.Lease.Playlist);
            invalid.Headers.TryAddWithoutValidation("Range", "bytes=0-2,6-8");
            using var denied = await http.SendAsync(invalid, ct);
            if (denied.StatusCode != HttpStatusCode.NotFound)
                throw new InvalidOperationException("Multi-range accepted");
            using var wrongRoute = await http.GetAsync(new Uri(item.Lease.Playlist, "index.m3u8"), ct);
            using var query = await http.GetAsync(new Uri(item.Lease.Playlist, "?url=unexpected"), ct);
            if (wrongRoute.StatusCode != HttpStatusCode.NotFound || query.StatusCode != HttpStatusCode.NotFound)
                throw new InvalidOperationException("Unexpected finite route accepted");
        }
        Console.WriteLine("VIVOAPK_PRIVATE_LOOPBACK_FILE_MULTIWINDOW_AND_RANDOM_RANGE=PASS");

        // Reproduce the actual FFmpeg seek contract: it opens a new range
        // connection while preserving its original socket until the seek
        // succeeds. The original response is deliberately NOT consumed.
        // The old whole-response gate deadlocks this pair under backpressure.
        foreach (var item in fixtures)
        {
            long total = new FileInfo(item.Reference).Length;
            using var stalledRequest = new HttpRequestMessage(HttpMethod.Get, item.Lease.Playlist);
            stalledRequest.Headers.Range = new RangeHeaderValue(0, null);
            stalledRequest.Headers.ConnectionClose = true;
            using (var stalled = await http.SendAsync(stalledRequest,
                HttpCompletionOption.ResponseHeadersRead, ct))
            {
                if (stalled.StatusCode != HttpStatusCode.PartialContent)
                    throw new InvalidOperationException("Initial open-ended range failed");
                using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
                deadline.CancelAfter(TimeSpan.FromSeconds(8));
                using var tailRequest = new HttpRequestMessage(HttpMethod.Get, item.Lease.Playlist);
                tailRequest.Headers.Range = new RangeHeaderValue(total - 64, total - 1);
                using var tail = await http.SendAsync(tailRequest, deadline.Token);
                if (tail.StatusCode != HttpStatusCode.PartialContent)
                    throw new InvalidOperationException("Overlapping seek blocked");
                byte[] bytes = await tail.Content.ReadAsByteArrayAsync(deadline.Token);
                await using var reference = File.OpenRead(item.Reference);
                reference.Position = total - 64;
                var expected = new byte[64];
                await reference.ReadExactlyAsync(expected, deadline.Token);
                if (!bytes.SequenceEqual(expected))
                    throw new InvalidOperationException("Overlapping seek data differs");
            }
            // Disposing a partially read HTTP response is ordinary seek
            // cancellation, not a request to revoke the backend grant.
            using var next = new HttpRequestMessage(HttpMethod.Get, item.Lease.Playlist);
            next.Headers.Range = new RangeHeaderValue(0, 63);
            using var resumed = await http.SendAsync(next, ct);
            if (resumed.StatusCode != HttpStatusCode.PartialContent)
                throw new InvalidOperationException("Seek cancellation invalidated lease");
        }
        await Task.WhenAll(DrainReaders(ownerA, a, "movie_after_cancel"),
            DrainReaders(ownerB, b, "episode_after_cancel"));
        Console.WriteLine("VIVOAPK_PRIVATE_LOOPBACK_FILE_OVERLAPPING_SEEK_AND_CANCEL=PASS");

        async Task<string[]> Decode(string input, string label, bool local, string seek)
        {
            var info = new ProcessStartInfo(get("ffmpeg"))
            {
                UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true
            };
            foreach (var arg in new[] { "-hide_banner", "-nostdin", "-v", "error",
                "-protocol_whitelist", local ? "file" : "http,tcp", "-threads", "1",
                "-ss", seek, "-i", input, "-t", "2", "-map", "0:v:0", "-map", "0:a:0",
                "-threads:v", "1", "-threads:a", "1", "-f", "framemd5", "pipe:1" })
                info.ArgumentList.Add(arg);
            using var process = Process.Start(info) ?? throw new InvalidOperationException();
            try
            {
                var output = process.StandardOutput.ReadToEndAsync(ct);
                var errors = process.StandardError.ReadToEndAsync(ct);
                await process.WaitForExitAsync(ct);
                var records = (await output).Split('\n').Where(l =>
                    l.StartsWith("0,", StringComparison.Ordinal) ||
                    l.StartsWith("1,", StringComparison.Ordinal)).ToArray();
                var diagnostic = await errors;
                int video = records.Count(x => x.StartsWith("0,", StringComparison.Ordinal));
                int audio = records.Length - video;
                // Fixed error vocabulary only; never print the URL/capability.
                var known = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
                {
                    ["moov_missing"] = "moov atom not found",
                    ["invalid_data"] = "Invalid data found",
                    ["http_404"] = "404 Not Found",
                    ["http_429"] = "429 Too Many Requests",
                    ["http_500"] = "500 Internal Server Error",
                    ["http_503"] = "503 Service Unavailable",
                    ["partial_file"] = "partial file",
                    ["premature_end"] = "Stream ends prematurely",
                    ["protocol"] = "not on whitelist",
                    ["extension"] = "Filename extension",
                    ["connection"] = "Connection refused",
                    ["timeout"] = "timed out",
                    ["input_error"] = "Error opening input",
                    ["seek_error"] = "seek",
                };
                var markers = string.Join(",", known.Where(pair => diagnostic.Contains(pair.Value,
                    StringComparison.OrdinalIgnoreCase)).Select(pair => pair.Key));
                Console.WriteLine($"VIVOAPK_PRIVATE_LOOPBACK_FILE_DECODE={label} seek={seek} local={local} exit={process.ExitCode} video={video} audio={audio} diagnostic_bytes={diagnostic.Length} markers={markers}");
                if (process.ExitCode != 0 || video < 45 || audio < 40)
                    throw new InvalidOperationException("Finite decode failed");
                // Compare frame payloads in stream order (PTS may differ
                // between a local and HTTP demuxer; decoded data may not).
                return records.Select(l => l.Split(',')[0] + ":" + l.Split(',').Last().Trim()).ToArray();
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

        async Task<string[]> Compare((PrivateHlsLoopback.Lease Lease, string Reference, string Label) item,
            string seek)
        {
            var expected = await Decode(item.Reference, item.Label, true, seek);
            var actual = await Decode(item.Lease.Playlist.AbsoluteUri, item.Label, false, seek);
            // Compare each stream separately: cross-stream interleave is
            // not a codec correctness property.
            foreach (var prefix in new[] { "0:", "1:" })
                if (!expected.Where(x => x.StartsWith(prefix, StringComparison.Ordinal))
                    .SequenceEqual(actual.Where(x => x.StartsWith(prefix, StringComparison.Ordinal))))
                    throw new InvalidOperationException("Finite decoded payload differs from reference");
            return actual;
        }

        foreach (var seek in new[] { "0", "7", "2" })
        {
            string[][] actual;
            try
            {
                actual = await Task.WhenAll(fixtures.Select(item => Compare(item, seek)));
            }
            finally
            {
                // Even a failing decode must leave its sanitized counters
                // visible, and neither side can silently swallow the failure.
                foreach (var (owner, lease, label) in new[] {
                    (ownerA, a, "movie"), (ownerB, b, "episode") })
                {
                    var stats = bridge.InspectFileReaders(owner, lease);
                    Console.WriteLine($"VIVOAPK_PRIVATE_LOOPBACK_FILE_READER_STATS={label}_seek_{seek} active={stats.Active} peak={stats.Peak} waits={stats.Waits} timeouts={stats.Timeouts} cancelled={stats.Cancelled} failures={stats.Failures}");
                }
            }
            await Task.WhenAll(DrainReaders(ownerA, a, "movie_after_decode"),
                DrainReaders(ownerB, b, "episode_after_decode"));
            if (actual[0].Where(x => x.StartsWith("0:", StringComparison.Ordinal))
                .SequenceEqual(actual[1].Where(x => x.StartsWith("0:", StringComparison.Ordinal))))
                throw new InvalidOperationException("Movie and episode unexpectedly share decoded content");
        }
        Console.WriteLine("VIVOAPK_PRIVATE_LOOPBACK_FILE_FFMPEG_MOVIE_EPISODE_SEEK=PASS");
        try
        {
            await bridge.CloseAsync(ownerB, a, ct);
            throw new Exception("Cross-owner close accepted");
        }
        catch (InvalidOperationException) { }
        await bridge.CloseAsync(ownerA, a, ct);
        using (var retired = await http.GetAsync(a.Playlist, ct))
            if (retired.StatusCode != HttpStatusCode.NotFound)
                throw new InvalidOperationException("Closed file capability remains live");
        await Compare(fixtures[1], "5");
        await bridge.CloseAsync(ownerB, b, ct);
        using (var retired = await http.GetAsync(b.Playlist, ct))
            if (retired.StatusCode != HttpStatusCode.NotFound)
                throw new InvalidOperationException("Closed episode capability remains live");
        Console.WriteLine("VIVOAPK_PRIVATE_LOOPBACK_FILE_CLOSE_ISOLATION=PASS");
        return 0;
    }
}
