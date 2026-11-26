"""Synthetic artifact provenance checks; never production approval."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_release_manifest as m


class CandidateManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.source = "a" * 40
        self.values = {"SOURCE_COMMIT": self.source, "COMBINED_LAB_GATE_EXIT": "0",
                       "SYNTHETIC_PLUGIN_SHA256": "b" * 64}
        for name, (field, _) in m.FIELDS.items():
            path = self.bundle / name
            path.write_bytes(name.encode())
            self.values[field] = m.sha256(path)
        (self.bundle / "meta.json").write_text("{}")
        self.ffmpeg = self.root / "ffmpeg"
        self.ffprobe = self.root / "ffprobe"
        self.ffmpeg.write_bytes(b"encoder")
        self.ffprobe.write_bytes(b"probe")
        self.values["FFMPEG_SHA256"] = m.sha256(self.ffmpeg)
        self.evidence = self.root / "evidence"

    def build(self):
        self.evidence.write_text("\n".join(k + "=" + v for k, v in self.values.items()))
        return m.build(self.bundle, self.evidence, self.ffmpeg, self.ffprobe,
                       self.source, "123")

    def test_candidate_exact_but_never_authorized(self):
        result = self.build()
        self.assertFalse(result["activation_allowed"])
        self.assertEqual(set(result["components"]), set(m.FIELDS))
        self.assertEqual(result["source"], self.source)

    def test_modified_dll_refused(self):
        (self.bundle / "MediaBrowser.Controller.dll").write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "candidate digest mismatch"):
            self.build()

    def test_fixture_rebuild_refused(self):
        self.values["SYNTHETIC_PLUGIN_SHA256"] = self.values["REGULAR_PLUGIN_SHA256"]
        with self.assertRaisesRegex(ValueError, "ordinary/fixture"):
            self.build()

    def test_failed_or_wrong_ci_refused(self):
        self.values["COMBINED_LAB_GATE_EXIT"] = "1"
        with self.assertRaisesRegex(ValueError, "not passed"):
            self.build()

    def test_unexpected_file_refused(self):
        (self.bundle / "extra").write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValueError, "unexpected candidate file set"):
            self.build()

    def test_encoder_changed_refused(self):
        self.ffmpeg.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "FFmpeg changed"):
            self.build()


if __name__ == "__main__":
    unittest.main()
