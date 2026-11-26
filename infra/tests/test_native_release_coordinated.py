"""Coordinated release tests use actual private files and fake services only."""
from pathlib import Path
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_release_coordinated as c


class HardInterruption(BaseException):
    pass


class CoordinatedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.live, self.state = root / "live", root / "state"
        self.live.mkdir()
        self.state.mkdir(mode=0o700)
        self.targets = {name: self.live / name for name in c.NAMES}
        self.old = {}
        self.changes = []
        self.events = []
        self.active = True
        self.fail_new = False
        for name, target in self.targets.items():
            before = None if name in c.NEW_ONLY else (
                b"<PluginConfiguration><Enabled>true</Enabled><ChannelSlotOwners/></PluginConfiguration>"
                if name == "plugin.xml" else ("old-" + name).encode())
            if before is not None:
                target.write_bytes(before)
                target.chmod(0o640)
            after = c.set_xml_flag(before, "true") if name == "plugin.xml" else ("new-" + name).encode()
            self.old[name] = before
            self.changes.append(c.Change(name, None if before is None else c.digest(before), after,
                0o600 if name in ("media-key", "media-env") else 0o644 if before is None else 0o640,
                os.geteuid(), os.getegid()))

    def stop(self):
        self.events.extend(("stop-jellyfin", "stop-backend"))
        self.active = False

    def reload(self):
        self.assertFalse(self.active)
        self.events.append("reload")

    def start(self):
        self.events.extend(("start-backend", "start-jellyfin"))
        self.active = True

    def health(self, side):
        self.assertTrue(self.active)
        self.events.append("health-" + side)
        if side == "new" and self.fail_new:
            raise RuntimeError("postflight fail")

    def tx(self):
        return c.CoordinatedTransaction(self.targets, self.state, "a"*40,
            self.stop, self.reload, self.start, self.health)

    def assert_old(self):
        for name, path in self.targets.items():
            if self.old[name] is None:
                self.assertFalse(path.exists())
            else:
                self.assertEqual(path.read_bytes(), self.old[name])
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_commit_then_explicit_rollback(self):
        tx = self.tx()
        self.assertEqual(tx.activate(self.changes), "committed")
        self.assertEqual(self.events, ["health-old", "stop-jellyfin", "stop-backend",
            "reload", "start-backend", "start-jellyfin", "health-new"])
        self.assertEqual(tx.rollback(), "rolled_back")
        self.assert_old()

    def test_bad_old_hash_rejected_before_stop(self):
        self.targets["Jellyfin.Api.dll"].write_bytes(b"unexpected")
        with self.assertRaisesRegex(c.Blocked, "coordinated_original_changed"):
            self.tx().activate(self.changes)
        self.assertNotIn("stop-jellyfin", self.events)

    def test_existing_key_rejected_before_stop(self):
        self.targets["media-key"].write_bytes(b"existing-private-key")
        with self.assertRaisesRegex(c.Blocked, "coordinated_original_changed"):
            self.tx().activate(self.changes)
        self.assertEqual(self.targets["media-key"].read_bytes(), b"existing-private-key")
        self.assertNotIn("stop-jellyfin", self.events)

    def test_failed_postflight_recovers_all(self):
        self.fail_new = True
        self.assertEqual(self.tx().activate(self.changes), "rolled_back_after_failure")
        self.assert_old()
        self.assertTrue(self.active)

    def test_interrupted_after_config_and_before_key_recovers(self):
        original = c.atomic_write
        target = self.targets["media-key"]

        def interrupted(path, data, *args, **kwargs):
            if path == target:
                raise HardInterruption()
            return original(path, data, *args, **kwargs)
        with patch.object(c, "atomic_write", side_effect=interrupted):
            with self.assertRaises(HardInterruption):
                self.tx().activate(self.changes)
        self.assertFalse(self.active)
        self.assertEqual(self.tx().rollback(), "rolled_back")
        self.assert_old()
        self.assertTrue(self.active)

    def test_preserve_runtime_slot_change_on_rollback(self):
        tx = self.tx()
        self.assertEqual(tx.activate(self.changes), "committed")
        p = self.targets["plugin.xml"]
        xml = ET.fromstring(p.read_bytes())
        ET.SubElement(xml.find("ChannelSlotOwners"), "string").text = "synthetic-owner"
        p.write_bytes(ET.tostring(xml))
        self.assertEqual(tx.rollback(), "rolled_back")
        self.assertIsNone(c.xml_flag(p.read_bytes()))
        self.assertEqual(ET.fromstring(p.read_bytes()).findtext("ChannelSlotOwners/string"),
                         "synthetic-owner")

    def test_corrupt_backup_rejected_before_stopping(self):
        tx = self.tx()
        self.assertEqual(tx.activate(self.changes), "committed")
        (tx.save / "old/Jellyfin.Api.dll").write_bytes(b"wrong")
        events = self.events[:]
        with self.assertRaisesRegex(c.Blocked, "coordinated_backup_invalid"):
            tx.rollback()
        self.assertEqual(self.events, events)

    def test_unrelated_live_edit_rejected_before_stop(self):
        tx = self.tx()
        self.assertEqual(tx.activate(self.changes), "committed")
        self.targets["backend-dropin"].write_bytes(b"unrelated")
        events = self.events[:]
        with self.assertRaisesRegex(c.Blocked, "coordinated_unrelated_change"):
            tx.rollback()
        self.assertEqual(self.events, events)

    def test_secret_backup_private(self):
        tx = self.tx()
        self.assertEqual(tx.activate(self.changes), "committed")
        for name in ("media-key", "media-env"):
            path = tx.save / "new" / name
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn(path.read_text(), (tx.save / "journal.json").read_text())

    def test_concurrent_lock_rejected(self):
        with self.tx().lock():
            with self.assertRaisesRegex(c.Blocked, "coordinated_already_running"):
                self.tx().activate(self.changes)
        self.assert_old()


if __name__ == "__main__":
    unittest.main()
