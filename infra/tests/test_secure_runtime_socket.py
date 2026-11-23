"""Purely local unit tests for existing VivoAPK socket permissions, no VPS access."""
import grp
import importlib.util
import os
from pathlib import Path
import socket
import stat
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "secure_runtime_socket.py"
spec = importlib.util.spec_from_file_location("vivoapk_socket", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SecureSocketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.directory.chmod(0o2770)
        self.target = self.directory / "backend.sock"
        self.group = grp.getgrgid(os.getgid()).gr_name

    def test_existing_socket_is_restricted_without_restarting_service(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bound:
            bound.bind(str(self.target))
            self.target.chmod(0o666)
            module.secure_socket(self.target, expected_group=self.group, max_wait=0.1)
            self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o660)
            # Idempotency: same socket remains connected after permission change.
            self.assertTrue(stat.S_ISSOCK(self.target.stat().st_mode))
            module.secure_socket(self.target, expected_group=self.group, max_wait=0.1)

    def test_refuses_symlink_even_when_target_is_socket(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bound:
            bound.bind(str(self.directory / "actual.sock"))
            self.target.symlink_to(self.directory / "actual.sock")
            with self.assertRaises(PermissionError):
                module.secure_socket(self.target, expected_group=self.group, max_wait=0.1)

    def test_refuses_regular_file(self):
        self.target.write_bytes(b"not a socket")
        with self.assertRaises(PermissionError):
            module.secure_socket(self.target, expected_group=self.group, max_wait=0.1)

    def test_missing_socket_fails_closed(self):
        with self.assertRaises(TimeoutError):
            module.secure_socket(self.target, expected_group=self.group, max_wait=0.01)


if __name__ == "__main__":
    unittest.main()
