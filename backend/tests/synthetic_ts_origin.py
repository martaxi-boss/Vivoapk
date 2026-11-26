"""Generated, paced MPEG-TS fixture; never contacts a real origin."""
import time

from test_media_origin import FakeResponse
from test_private_hls import Origin


class ContinuousResponse(FakeResponse):
    def __init__(self, segments):
        super().__init__(200, [(b"Content-Type", b"video/mp2t")], [])
        self.segments = segments

    def iter_stream(self):
        begin = time.monotonic()
        elapsed = 0.0
        for segment in self.segments:
            for offset in range(0, len(segment), 188 * 64):
                if self.closed:
                    return
                block = segment[offset:offset + 188 * 64]
                elapsed += 3 * len(block) / len(segment)
                delay = begin + elapsed - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                self.reads += 1
                yield block
        raise RuntimeError("Synthetic continuous TS horizon exhausted")


class ContinuousOrigin(Origin):
    def __init__(self, sources):
        super().__init__({url: lambda segments=segments: ContinuousResponse(segments)
                          for url, segments in sources.items()})
