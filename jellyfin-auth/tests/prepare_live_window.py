"""Patch the pinned disposable Jellyfin source, never an installed server."""
from pathlib import Path
import re
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
assembly_version, file_version = sys.argv[2:]
assert all(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+", v)
           for v in (assembly_version, file_version))
assert subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                               text=True).strip() == "83d0d0e7f76e23e2e085c8ff67d28b772d35967d"

def replace_once(path, expected, replacement):
    source = path.read_text()
    if source.count(expected) != 1:
        raise RuntimeError("Unexpected pinned source: " + path.name)
    path.write_text(source.replace(expected, replacement, 1))

controller = root / "Jellyfin.Api/Controllers/DynamicHlsController.cs"
test = root / "tests/Jellyfin.Api.Tests/Controllers/DynamicHlsControllerTests.cs"
for path, digest in (
    (controller, "6df76de0b44ce321be11a9b6128c53ced1bcd069"),
    (test, "a9f85739bcaf2b60dfb2d6f25e90ea5be015e6b3"),
):
    actual = subprocess.check_output(["git", "-C", str(root), "hash-object", str(path)],
                                     text=True).strip()
    if actual != digest:
        raise RuntimeError("Unexpected upstream blob: " + path.name)

# EVENT/VOD requires an append-only list (FFmpeg forces list size zero).
# Only the open-ended live branch gets the sliding window.
# Keep the event code path, native HLS routes, base URL and encoder settings.
# 12 listed segments plus up to 12 unlisted segments are a lab policy,
# not a certified byte limit or production configuration.
replace_once(controller,
    '        var hlsArguments = $"-hls_playlist_type {(isEventPlaylist ? "event" : "vod")} -hls_list_size 0";',
    '        var hlsArguments = GetHlsPlaylistArguments(isEventPlaylist, state.MediaSource.IsInfiniteStream);')
replace_once(controller,
    "    private string GetCommandLineArguments(string outputPath, StreamState state, bool isEventPlaylist, int startNumber)",
    """    internal static string GetHlsPlaylistArguments(bool isEventPlaylist, bool isInfiniteStream)
        => isEventPlaylist && isInfiniteStream
            ? "-hls_list_size 12 -hls_delete_threshold 12 -hls_flags delete_segments+temp_file"
            : $"-hls_playlist_type {(isEventPlaylist ? "event" : "vod")} -hls_list_size 0";

    private string GetCommandLineArguments(string outputPath, StreamState state, bool isEventPlaylist, int startNumber)""")
replace_once(test,
    "        [Theory]\n        [InlineData(8, 1,",
    """        [Theory]
        [InlineData(true, true, "-hls_list_size 12 -hls_delete_threshold 12 -hls_flags delete_segments+temp_file")]
        [InlineData(true, false, "-hls_playlist_type event -hls_list_size 0")]
        [InlineData(false, true, "-hls_playlist_type vod -hls_list_size 0")]
        [InlineData(false, false, "-hls_playlist_type vod -hls_list_size 0")]
        public void HlsPlaylistArguments_OnlyContinuousLiveUsesSlidingWindow(bool isEvent, bool infinite, string expected)
        {
            Assert.Equal(expected, DynamicHlsController.GetHlsPlaylistArguments(isEvent, infinite));
        }

        [Theory]
        [InlineData(8, 1,""")
project = root / "Jellyfin.Api/Jellyfin.Api.csproj"
replace_once(project, "<TargetFramework>net10.0</TargetFramework>",
    "<TargetFramework>net10.0</TargetFramework>\n    <AssemblyVersion>"
    + assembly_version + "</AssemblyVersion>\n    <FileVersion>"
    + file_version + "</FileVersion>")
print("JELLYFIN_LIVE_WINDOW_SOURCE_PATCH=PASS")
