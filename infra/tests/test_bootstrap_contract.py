"""Bootstrap invariants after retiring the independent PINK Android client."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BootstrapHygieneTests(unittest.TestCase):
    def test_only_jellyfin_login_fields(self):
        doc = (ROOT / "BOOTSTRAP.md").read_text()
        for phrase in ("serveriptv.duckdns.org", "username", "password", "painel Mega OTT"):
            self.assertIn(phrase, doc)
        self.assertIn("NÃO DESVIAR", doc)

    def test_old_pink_android_and_migration_code_retired(self):
        self.assertFalse((ROOT / "reference/pink-android-core").exists())
        self.assertFalse((ROOT / "infra/sync_pink_api_token.py").exists())

    def test_no_pink_services_required_to_deploy(self):
        live = (ROOT / "infra/deploy_jellyfin_auth.py").read_text()
        backend = (ROOT / "infra/deploy.py").read_text()
        self.assertIn('OTHERS = ("vivoapk-backend.service", "nginx.service", "postgresql.service")', live)
        self.assertIn("retired_pink_service_unexpected", live)
        self.assertIn("retired_pink_service_unexpected", backend)
        self.assertNotIn('("jellyfin-pink.service", "pink-iptv-backend.service"', backend)


if __name__ == "__main__":
    unittest.main()
