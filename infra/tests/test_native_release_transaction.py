"""Real file transactions, simulated service controls, no VPS connections."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_release_transaction as nr


class HardInterruption(BaseException):
    pass


class NativeReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.jelly, self.stage, self.state = (root / name for name in ("jelly", "stage", "state"))
        for p in (self.jelly, self.stage, self.state):
            p.mkdir(mode=0o700)
        self.old = {}
        self.new = {}
        pins = []
        for index, (name, relative) in enumerate(nr.COMPONENTS.items()):
            path = self.jelly / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            self.old[name] = ("old-" + name).encode()
            self.new[name] = ("new-" + name).encode()
            path.write_bytes(self.old[name])
            path.chmod(0o640 if index == 1 else 0o644)
            (self.stage / name).write_bytes(self.new[name])
            pins.append(nr.Pin(name, nr.digest(self.old[name]), nr.digest(self.new[name])))
        self.release = nr.Release("a" * 40, "b" * 40, tuple(pins))
        self.modes = {name: stat.S_IMODE((self.jelly / relative).stat().st_mode)
                      for name, relative in nr.COMPONENTS.items()}
        self.active = True
        self.calls = []
        self.fail_health = False
        self.fail_stop = False
        self.fail_start = False
        self.sentinel = self.jelly / "untouched-database"
        self.sentinel.write_bytes(b"unrelated-data")

    def stop(self):
        self.calls.append("stop")
        if self.fail_stop:
            self.fail_stop = False
            raise RuntimeError("simulated stop failure")
        self.active = False

    def start(self):
        self.calls.append("start")
        if self.fail_start:
            self.fail_start = False
            raise RuntimeError("simulated start failure")
        self.active = True

    def health(self):
        self.calls.append("health")
        if not self.active:
            raise RuntimeError("service down")
        if self.fail_health and "start" in self.calls:
            self.fail_health = False
            raise RuntimeError("simulated postflight failure")

    def transaction(self):
        return nr.Transaction(self.jelly, self.stage, self.state, self.release,
                              self.stop, self.start, self.health)

    def assert_files(self, data):
        for name, relative in nr.COMPONENTS.items():
            target = self.jelly / relative
            self.assertEqual(target.read_bytes(), data[name])
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), self.modes[name])
        self.assertEqual(self.sentinel.read_bytes(), b"unrelated-data")

    def phase(self):
        return json.loads((self.state / self.release.source / "journal.json").read_text())["phase"]

    def test_no_lab_or_self_approved_release(self):
        self.assertEqual(nr.APPROVED_PRODUCTION, ())
        with self.assertRaisesRegex(nr.Blocked, "no_approved_regular_production_release"):
            nr.approved_release(self.release.source)

    def test_four_exact_component_locations_required(self):
        self.assertEqual(tuple(nr.COMPONENTS), ("Jellyfin.Api.dll", "Jellyfin.LiveTv.dll",
            "MediaBrowser.Controller.dll", "Jellyfin.Plugin.VivoapkMegaAuth.dll"))
        incomplete = nr.Release(self.release.source, self.release.backend,
                                tuple(p for p in self.release.pins if p.name != "MediaBrowser.Controller.dll"))
        with self.assertRaisesRegex(nr.Blocked, "component_set_mismatch"):
            nr.validate_release(incomplete)

    def test_preflight_is_read_only(self):
        self.transaction().preflight()
        self.assertEqual(list(self.state.iterdir()), [])
        self.assertEqual(self.calls, ["health"])
        self.assert_files(self.old)

    def test_commit_and_protected_file(self):
        tx = self.transaction()
        self.assertEqual(tx.activate(), "committed")
        self.assertEqual(self.phase(), "committed")
        self.assertEqual(self.calls, ["health", "stop", "start", "health"])
        self.assert_files(self.new)
        with self.assertRaisesRegex(nr.Blocked, "transaction_not_recoverable"):
            tx.recover_interrupted()

    def test_wrong_staged_hash_no_service_stop(self):
        (self.stage / next(iter(nr.COMPONENTS))).write_bytes(b"tampered")
        with self.assertRaisesRegex(nr.Blocked, "staged_component_digest_mismatch"):
            self.transaction().activate()
        self.assertNotIn("stop", self.calls)
        self.assert_files(self.old)

    def test_wrong_existing_hash_no_service_stop(self):
        name, relative = next(iter(nr.COMPONENTS.items()))
        (self.jelly / relative).write_bytes(b"changed")
        with self.assertRaisesRegex(nr.Blocked, "installed_component_digest_mismatch"):
            self.transaction().activate()
        self.assertNotIn("stop", self.calls)
        self.assertEqual((self.jelly / relative).read_bytes(), b"changed")

    def test_symlink_rejected_before_stop(self):
        name, relative = next(iter(nr.COMPONENTS.items()))
        p = self.stage / name
        p.unlink()
        p.symlink_to(self.jelly / relative)
        with self.assertRaises(OSError):
            self.transaction().activate()
        self.assertNotIn("stop", self.calls)
        self.assert_files(self.old)

    def test_postflight_failure_restores_all(self):
        self.fail_health = True
        self.assertEqual(self.transaction().activate(), "rolled_back_after_failure")
        self.assertEqual(self.phase(), "rolled_back")
        self.assertTrue(self.active)
        self.assert_files(self.old)

    def test_failed_start_restores_all(self):
        self.fail_start = True
        self.assertEqual(self.transaction().activate(), "rolled_back_after_failure")
        self.assertTrue(self.active)
        self.assert_files(self.old)

    def test_failed_stop_does_not_install(self):
        self.fail_stop = True
        self.assertEqual(self.transaction().activate(), "rolled_back_after_failure")
        self.assertTrue(self.active)
        self.assert_files(self.old)

    def test_partial_replace_failure(self):
        tx = self.transaction()
        real = nr.atomic_write
        failed = False
        target = tx.target(self.release.pins[1])

        def write(path, data, *args, **kwargs):
            nonlocal failed
            if path == target and not failed:
                failed = True
                self.assertFalse(self.active)
                self.assertEqual(tx.target(self.release.pins[0]).read_bytes(),
                                 self.new[self.release.pins[0].name])
                raise OSError("simulated interrupted replacement")
            return real(path, data, *args, **kwargs)

        with patch.object(nr, "atomic_write", side_effect=write):
            self.assertEqual(tx.activate(), "rolled_back_after_failure")
        self.assertTrue(failed)
        self.assertTrue(self.active)
        self.assert_files(self.old)

    def interrupted(self):
        tx = self.transaction()
        real = nr.atomic_write
        target = tx.target(self.release.pins[1])

        def write(path, data, *args, **kwargs):
            if path == target:
                raise HardInterruption()
            return real(path, data, *args, **kwargs)

        with patch.object(nr, "atomic_write", side_effect=write):
            with self.assertRaises(HardInterruption):
                tx.activate()
        self.assertEqual(self.phase(), "installing")
        self.assertFalse(self.active)
        return tx

    def test_durable_interruption_recovery(self):
        self.interrupted()
        # Fresh object models a new explicit Ferrari recovery invocation.
        self.assertEqual(self.transaction().recover_interrupted(), "rolled_back")
        self.assertEqual(self.phase(), "rolled_back")
        self.assertTrue(self.active)
        self.assert_files(self.old)

    def test_interruption_before_fourth_replacement_restores_every_component(self):
        tx = self.transaction()
        real = nr.atomic_write
        target = tx.target(self.release.pins[3])

        def write(path, data, *args, **kwargs):
            if path == target:
                raise HardInterruption()
            return real(path, data, *args, **kwargs)

        with patch.object(nr, "atomic_write", side_effect=write):
            with self.assertRaises(HardInterruption):
                tx.activate()
        self.assertEqual(self.phase(), "installing")
        for pin in self.release.pins[:3]:
            self.assertEqual(tx.target(pin).read_bytes(), self.new[pin.name])
        self.assertEqual(target.read_bytes(), self.old[self.release.pins[3].name])
        self.assertEqual(self.transaction().recover_interrupted(), "rolled_back")
        self.assert_files(self.old)

    def test_corrupt_backup_blocks_recovery_before_mutation(self):
        tx = self.interrupted()
        name = self.release.pins[1].name
        (tx.save / name).write_bytes(b"corrupt backup")
        old_calls = self.calls[:]
        with self.assertRaisesRegex(nr.Blocked, "backup_digest_or_permissions_mismatch"):
            tx.recover_interrupted()
        self.assertEqual(self.calls, old_calls)
        self.assertEqual(self.phase(), "installing")

    def test_other_component_change_blocks_recovery(self):
        tx = self.interrupted()
        target = tx.target(self.release.pins[2])
        target.write_bytes(b"unrelated later modification")
        old_calls = self.calls[:]
        with self.assertRaisesRegex(nr.Blocked, "unrelated_component_change_requires_review"):
            tx.recover_interrupted()
        self.assertEqual(self.calls, old_calls)
        self.assertEqual(target.read_bytes(), b"unrelated later modification")

    def test_second_transaction_lock_rejected(self):
        with self.transaction().lock():
            with self.assertRaisesRegex(nr.Blocked, "another_transaction_running"):
                self.transaction().activate()
        self.assert_files(self.old)


if __name__ == "__main__":
    unittest.main()
