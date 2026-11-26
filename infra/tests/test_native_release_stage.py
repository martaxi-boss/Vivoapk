"""No real VPS connection; verify isolated staging guardrails."""
from pathlib import Path
import sys
import tempfile
import tarfile
import io
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_release_stage as stage


class NativeStageTests(unittest.TestCase):
    def test_pins_are_exact_four_component_ci_candidate(self):
        self.assertEqual(len([p for p in stage.CANDIDATE if p.endswith(".dll")]), 4)
        self.assertTrue(all(len(v) == 64 for v in stage.CANDIDATE.values()))
        self.assertEqual(len(stage.SOURCE_SHA), 40)
        self.assertNotEqual(stage.SOURCE_SHA, stage.ORIGINAL_SHA)

    def test_absent_authenticated_transfer_fails_before_creating_stage(self):
        with patch.object(stage.shutil, "which", return_value=None), patch.object(stage, "new_phase") as create:
            with self.assertRaisesRegex(stage.Blocked, "authenticated_artifact_transfer_unavailable"):
                stage.stage_bundle()
            create.assert_not_called()

    def test_archive_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "a.tar.xz"
            dest = root / "dest"
            dest.mkdir()
            with tarfile.open(archive, "w:xz") as tf:
                entry = tarfile.TarInfo("../outside")
                entry.size = 1
                tf.addfile(entry, io.BytesIO(b"x"))
            with self.assertRaises((tarfile.TarError, stage.Blocked)):
                stage.safe_unpack(archive, dest)
            self.assertFalse((root / "outside").exists())

    def test_stage_never_replaces_existing_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "release"
            root.mkdir()
            (root / "backend").mkdir()
            with patch.object(stage, "STAGE", root), patch.object(stage, "trusted_directory"):
                with self.assertRaisesRegex(stage.Blocked, "existing_stage_requires_review"):
                    stage.new_phase("backend")

    def test_symlink_digest_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "real").write_bytes(b"test")
            (root / "link").symlink_to(root / "real")
            with self.assertRaisesRegex(stage.Blocked, "stage_file_missing_or_symlink"):
                stage.digest(root / "link")


if __name__ == "__main__":
    unittest.main()
