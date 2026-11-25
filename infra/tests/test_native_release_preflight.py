"""Safety contract for the Ferrari inventory, without touching the VPS."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_release_preflight as pf


class NativePreflightTests(unittest.TestCase):
    def healthy(self):
        result = {k: True for k in ("root", "services_healthy", "retired_services_inactive",
            "same_pids", "private_network", "private_namespace", "backend_socket_ok",
            "jellyfin_socket_ok", "backend_revision_matches", "source_remote_matches",
            "checkout_clean", "backend_unit_matches")}
        result.update(free_bytes=7 * 1024**3, ffmpeg_processes=0,
            components={n: {"matches_baseline": True, "mapped_exactly": True} for n in pf.BASELINE})
        return result

    def test_baseline_healthy(self):
        self.assertEqual(pf.assess(self.healthy()), [])

    def test_independent_blockers(self):
        result = self.healthy()
        result["private_network"] = False
        result["free_bytes"] = 1
        result["ffmpeg_processes"] = 1
        result["components"].pop(next(iter(pf.BASELINE)))
        self.assertEqual(set(pf.assess(result)), {"private_network", "insufficient_free_space",
            "active_or_unknown_playback", "component_identity_mismatch"})

    def test_missing_fields_fail_closed(self):
        self.assertTrue(pf.assess({}))

    def test_checkout_does_not_ignore_unknown_changes(self):
        self.assertTrue(pf.known_checkout_status(""))
        self.assertTrue(pf.known_checkout_status("?? jellyfin-auth/Vivoapk.Jellyfin.Auth/bin/a.dll"))
        self.assertFalse(pf.known_checkout_status(" M backend/app/main.py"))
        self.assertFalse(pf.known_checkout_status("?? unrelated"))
        self.assertFalse(pf.known_checkout_status("?? jellyfin-auth/Vivoapk.Jellyfin.Auth/bin/../x"))

    def test_component_real_read_only_and_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.dll"
            path.write_bytes(b"test")
            self.assertEqual(pf.component(path)["bytes"], 4)
            link = path.with_name("link.dll")
            link.symlink_to(path)
            with self.assertRaises(OSError):
                pf.component(link)

    def test_deleted_mapping_blocks_before_read(self):
        name, (relative, _) = next(iter(pf.BASELINE.items()))
        maps = "0000-0001 r--p 00000000 00:00 0 " + str(pf.JELLY / relative) + " (deleted)"
        with patch.object(pf, "component") as read:
            with self.assertRaisesRegex(pf.Blocked, "deleted_component_still_mapped"):
                pf.inspect_components(maps)
            read.assert_not_called()

    def test_mapping_must_match_exact_component(self):
        maps = "\n".join("0000-0001 r--p 00000000 00:00 0 " + str(pf.JELLY / relative)
                         for relative, _ in pf.BASELINE.values())
        with patch.object(pf, "component", side_effect=lambda path: {
                "sha256": pf.BASELINE[path.name][1], "bytes": 1, "mode": 0o644}):
            result = pf.inspect_components(maps)
            self.assertTrue(all(p["mapped_exactly"] and p["matches_baseline"] for p in result.values()))
            bad = maps + "\n0000-0001 r--p 00000000 00:00 0 /tmp/" + next(iter(pf.BASELINE))
            result = pf.inspect_components(bad)
            self.assertFalse(result[next(iter(pf.BASELINE))]["mapped_exactly"])

    def test_wrong_control_group_blocks_without_scanning(self):
        with self.assertRaisesRegex(pf.Blocked, "unexpected_jellyfin_control_group"):
            pf.ffmpeg_count("/")


if __name__ == "__main__":
    unittest.main()
