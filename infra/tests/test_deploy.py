"""Static deployment safeguards without VPS, sudo, provider calls or secrets."""
import importlib.util
import io
from pathlib import Path
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vivoapk_deploy", ROOT / "deploy.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DeploymentContractTests(unittest.TestCase):
    def test_canonical_project_path_matches_repo(self):
        self.assertEqual(str(module.ROOT), "/srv/projects/Vivoapk")
        self.assertEqual(module.REPO, "https://github.com/martaxi-boss/Vivoapk.git")

    def test_original_vault_is_read_only_except_new_runtime_file(self):
        self.assertEqual(str(module.VAULT), "/root/project-secrets/VIVOAPK")
        self.assertEqual(module.RUNTIME.name, "backend-runtime.env")
        self.assertEqual(module.CSV.name, "subscriber-mappings.csv")

    def test_retired_pink_not_required_for_new_install(self):
        source = (ROOT / "deploy.py").read_text()
        self.assertIn("retired_pink_service_unexpected", source)
        self.assertNotIn('("jellyfin-pink.service", "pink-iptv-backend.service"', source)
    def test_no_access_to_pink_data_beyond_service_health(self):
        source = (ROOT / "deploy.py").read_text()
        self.assertNotIn('"/srv/projects/pink-iptv"', source)
        self.assertNotIn("rm -rf", source)
        self.assertNotIn("DROP DATABASE", source)
        self.assertNotIn("iptables", source)

    def test_systemd_config_must_be_private_socket(self):
        source = (ROOT / "deploy.py").read_text()
        self.assertIn('f"ExecStart={VENV}/bin/uvicorn app.main:app --uds {SOCKET}', source)
        self.assertNotIn("--host 0.0.0.0", source)
        self.assertIn("ProtectSystem=strict", source)
        self.assertIn("ExecStartPost={VENV}/bin/python {SOURCE}/infra/secure_runtime_socket.py", source)
        self.assertTrue((ROOT / "secure_runtime_socket.py").is_file())

    def test_env_quote_denies_newlines(self):
        for value in ("value\nnew", "value\rnew", "value\x00new"):
            with self.assertRaises(module.Blocked):
                module.quoted(value)

    def test_env_quote_preserves_escaped_content(self):
        self.assertEqual(module.quoted('example"value'), '"example\\"value"')

    def test_snapshot_field_names_exclude_client_password(self):
        self.assertEqual(module.FIELDS, (
            "mega_subscription_id", "username", "dns_link",
            "dns_link_samsung_lg", "expiring_at", "last_synced_at",
        ))
        self.assertNotIn("password", module.FIELDS)


if __name__ == "__main__":
    unittest.main()
