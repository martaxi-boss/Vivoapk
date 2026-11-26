"""Scope exact finite video HLS segment seeking to the pinned disposable server."""
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
assert subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                               text=True).strip() == "83d0d0e7f76e23e2e085c8ff67d28b772d35967d"

def replace_once(path, old, new):
    source = path.read_text()
    if source.count(old) != 1:
        raise RuntimeError("Unexpected pinned source: " + path.name)
    path.write_text(source.replace(old, new, 1))

helper = root / "MediaBrowser.Controller/MediaEncoding/EncodingHelper.cs"
test = root / "tests/Jellyfin.Controller.Tests/MediaEncoding/EncodingHelperTests.cs"
for path, digest in (
    (helper, "ebfa78176a6fc90e7430898ad6667dbfb6bffa97"),
    (test, "aca55fbcc12dee220b6d53feba4474121f76a609"),
):
    actual = subprocess.check_output(["git", "-C", str(root), "hash-object", str(path)],
                                     text=True).strip()
    if actual != digest:
        raise RuntimeError("Unexpected upstream blob: " + path.name)

replace_once(helper, "using MediaBrowser.Controller.IO;\n",
    "using MediaBrowser.Controller.IO;\nusing MediaBrowser.Controller.Streaming;\n")
replace_once(helper,
    """                // Seeking beyond EOF makes no sense in transcoding. Clamp the seekTick value to
                // [0, RuntimeTicks - 5.0s], so that the muxer gets packets and avoid error codes.
                if (maxTime > 0)
                {
                    seekTick = Math.Clamp(seekTick, 0, Math.Max(maxTime - 50000000L, 0));
                }""",
    """                // Dynamic HLS requests describe an individual segment. A valid, bounded
                // finite video segment must begin at its advertised timeline position,
                // even in the final five seconds. Retain the original guard for remuxing,
                // progressive requests, live streams and invalid segment intervals.
                var exactFiniteSegment = state.IsVideoRequest
                    && state.TranscodingType == TranscodingJobType.Hls
                    && !IsCopyCodec(state.OutputVideoCodec)
                    && state.MediaSource?.IsInfiniteStream == false
                    && maxTime > time
                    && state.BaseRequest is StreamingRequestDto segment
                    && segment.CurrentRuntimeTicks == time
                    && segment.ActualSegmentLengthTicks > 0
                    && segment.ActualSegmentLengthTicks <= maxTime - time;
                if (maxTime > 0 && !exactFiniteSegment)
                {
                    seekTick = Math.Clamp(seekTick, 0, Math.Max(maxTime - 50000000L, 0));
                }""")

replace_once(test, "    private static EncodingHelper CreateHelper()",
    """    [Theory]
    [InlineData(true, true, false, false, 9, 9, 3, 9)]
    [InlineData(true, true, false, false, 11, 11, 1, 11)]
    [InlineData(true, true, false, false, 6, 6, 3, 6)]
    [InlineData(true, true, false, false, 9, 9, 4, 7)]
    [InlineData(true, true, false, false, 9, 9, 0, 7)]
    [InlineData(true, true, false, false, 9, 8, 3, 7)]
    [InlineData(true, true, false, false, 12, 12, 1, 7)]
    [InlineData(true, true, false, true, 9, 9, 3, 7)]
    [InlineData(true, true, true, false, 9, 9, 3, 7)]
    [InlineData(false, true, false, false, 9, 9, 3, 7)]
    [InlineData(true, false, false, false, 9, 9, 3, 7)]
    public void GetFastSeekCommandLineParameter_OnlyBoundedFiniteVideoHlsSegmentsUseExactTail(
        bool hls,
        bool video,
        bool copy,
        bool infinite,
        int requested,
        int segmentStart,
        int duration,
        int expected)
    {
        var encoder = new Mock<IMediaEncoder>();
        encoder.Setup(x => x.GetTimeParameter(It.IsAny<long>())).Returns("seek");
        var state = BuildState(null, null);
        state.TranscodingType = hls ? TranscodingJobType.Hls : TranscodingJobType.Progressive;
        state.IsVideoRequest = video;
        state.OutputVideoCodec = copy ? "copy" : "h264";
        state.MediaSource.IsInfiniteStream = infinite;
        state.RunTimeTicks = TimeSpan.FromSeconds(12).Ticks;
        state.BaseRequest = new VideoRequestDto
        {
            StartTimeTicks = TimeSpan.FromSeconds(requested).Ticks,
            CurrentRuntimeTicks = TimeSpan.FromSeconds(segmentStart).Ticks,
            ActualSegmentLengthTicks = TimeSpan.FromSeconds(duration).Ticks,
        };

        Assert.Equal("-ss seek", CreateHelper(encoder.Object).GetFastSeekCommandLineParameter(
            state, new EncodingOptions(), "ts"));
        encoder.Verify(x => x.GetTimeParameter(TimeSpan.FromSeconds(expected).Ticks), Times.Once);
    }

    private static EncodingHelper CreateHelper(IMediaEncoder? seekEncoder = null)""")
replace_once(test, "            mediaEncoder.Object,\n",
    "            seekEncoder ?? mediaEncoder.Object,\n")
print("JELLYFIN_EXACT_FINITE_HLS_TAIL_SOURCE_PATCH=PASS")
