"""Review exact drop-in payloads and enforce the rehearsal-only boundary."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import io

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_release_host as h
from native_release_transaction import Blocked


class HostPreparationTests(unittest.TestCase):
    def test_pinned_service_payload(self):
        backend, jellyfin = [x.decode() for x in h.unit_text()]
        self.assertIn("WorkingDirectory="+str(h.CHECKOUT/"backend"), backend)
        self.assertIn("--app-dir "+str(h.CHECKOUT/"backend"), backend)
        self.assertIn("--workers 1", backend)
        self.assertIn("EnvironmentFile="+str(h.MEDIA_ENV), backend)
        self.assertIn("Environment=JELLYFIN_ffmpeg="+str(h.FFMPEG), jellyfin)
        self.assertIn("Environment=VIVOAPK_PRIVATE_FFPROBE="+str(h.FFPROBE), jellyfin)
        self.assertIn("Environment=VIVOAPK_NATIVE_PRIVATE_PLAYBACK=1", jellyfin)
        for dangerous in ("PrivateNetwork=false","ProtectSystem=false","User=root"):
            self.assertNotIn(dangerous, backend+jellyfin)

    def test_private_key_only_fixed_format(self):
        value = "a"*64
        self.assertEqual(h.private_env(value), ("PRIVATE_HLS_ENABLED=true\nPRIVATE_MEDIA_KEY="+value+"\n").encode())
        for bad in ("", "a"*64+"\nOTHER=1", "x"*64):
            with self.assertRaises(Blocked):
                h.private_env(bad)

    def test_no_activation_entry(self):
        with patch.object(sys, "argv", ["host", "activate"]), patch.object(h, "rehearsal") as rehearsal:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(h.main(), 1)
            rehearsal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
