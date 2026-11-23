import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

path=Path(__file__).resolve().parents[1]/"sync_pink_api_token.py"
spec=importlib.util.spec_from_file_location("pink_sync",path)
subject=importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)

class PinkApiKeyAlignmentTests(unittest.TestCase):
    def test_token_match_from_synthetic_systemd_env(self):
        blob="# sample\nMEGA_OTT_API_TOKEN='synthetic-token-value'\nMEGA_OTT_API_BASE=https://example.invalid/api\n"
        self.assertEqual(subject.env_value(blob,"MEGA_OTT_API_TOKEN"),"synthetic-token-value")

    def test_update_only_token_and_preserve_other_secrets(self):
        original="# keep comment\nMEGA_OTT_API_TOKEN=old-token\nSESSION_SIGNING_KEY=synthetic-signing-secret\n"
        changed=subject.replace_token(original,"new-token")
        self.assertIn("MEGA_OTT_API_TOKEN=new-token\n",changed)
        self.assertIn("SESSION_SIGNING_KEY=synthetic-signing-secret",changed)
        self.assertNotIn("old-token",changed)

    def test_update_requires_exactly_one_token_field(self):
        for blob in ("APP_ENV=production\n",
                     "MEGA_OTT_API_TOKEN=one\nMEGA_OTT_API_TOKEN=two\n"):
            with self.assertRaises(subject.Blocked):
                subject.replace_token(blob,"new")

    def test_injection_characters_rejected(self):
        for value in ("evil\nX=Y","evil\rX=Y","evil\x00X"):
            with self.assertRaises(subject.Blocked):
                subject.replace_token("MEGA_OTT_API_TOKEN=old\n",value)

    def test_production_side_effects_are_guarded(self):
        code=path.read_text()
        self.assertIn('parser.add_argument("mode", choices=("check", "apply"))',code)
        self.assertIn("if not valid_mega_list(pink_base, token)",code)
        self.assertIn("pink_file_not_running_token",code)
        self.assertIn('cmd(["systemctl","restart",VIVO_SERVICE]',code)
        self.assertNotIn('cmd(["systemctl","restart",PINK_SERVICE]',code)
        self.assertNotIn("git push",code)

if __name__=="__main__":
    unittest.main()
