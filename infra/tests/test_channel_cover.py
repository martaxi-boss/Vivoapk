"""Ensure the native Jellyfin pilot section has valid local artwork, never upstream URLs."""
import base64
from pathlib import Path
import re
import struct
import unittest

CHANNEL = (Path(__file__).resolve().parents[2] / "jellyfin-auth" /
           "Vivoapk.Jellyfin.Auth" / "VivoapkLivePreviewChannel.cs")


class ChannelCoverTests(unittest.TestCase):
    def test_embedded_local_png_has_real_dimensions(self):
        text = CHANNEL.read_text(encoding="utf-8")
        match = re.search(r'ChannelCoverPngBase64 = "([^"]+)";', text)
        self.assertIsNotNone(match)
        png = base64.b64decode(match.group(1), validate=True)
        self.assertGreater(len(png), 100)
        self.assertLess(len(png), 65536)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(struct.unpack(">II", png[16:24]), (128, 128))

    def test_native_channel_exposes_primary_and_thumb(self):
        source = CHANNEL.read_text(encoding="utf-8")
        self.assertIn("Stream = new MemoryStream(", source)
        self.assertIn("Format = MediaBrowser.Model.Drawing.ImageFormat.Png", source)
        self.assertIn("ImageType.Primary, ImageType.Thumb", source)


if __name__ == "__main__":
    unittest.main()
