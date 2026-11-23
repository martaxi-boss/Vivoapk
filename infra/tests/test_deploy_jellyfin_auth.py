"""Offline, rootless contract checks for reversible Jellyfin auth deployment."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("deploy_jellyfin_auth",root/"deploy_jellyfin_auth.py")
x=importlib.util.module_from_spec(spec)
spec.loader.exec_module(x)

class JellyfinAuthDeploymentContract(unittest.TestCase):
    def test_canonical_runtime(self):
        self.assertEqual(str(x.ROOT),"/srv/projects/Vivoapk")
        self.assertEqual(str(x.PLUGIN),"/srv/projects/jellyfin-server/data/plugins/VivoAPK Mega OTT Auth_0.1.0.0")
        self.assertEqual(str(x.CONF),"/srv/projects/jellyfin-server/data/plugins/configurations/Jellyfin.Plugin.VivoapkMegaAuth.xml")
    def test_full_git_commit_required(self):
        for value in ("main","latest","aaaa", "0"*39):
            with self.assertRaises(x.Blocked):
                x.sha_ok(value)
        self.assertEqual(x.sha_ok("a"*40),"a"*40)
    def test_no_unmanaged_services(self):
        code=(root/"deploy_jellyfin_auth.py").read_text()
        self.assertNotIn('systemctl","stop","pink',code)
        self.assertNotIn('systemctl","restart","pink',code)
        self.assertNotIn('apt-get',code)
        self.assertIn("admin_fingerprint()",code)
        self.assertIn("restore(sha)",code)
    def test_fails_closed_until_activation(self):
        code=(root/"deploy_jellyfin_auth.py").read_text()
        self.assertIn("plugin_must_default_disabled",code)
        self.assertIn("<Enabled>true</Enabled>",code)
        self.assertIn("plugin_not_loaded_or_jellyfin_unhealthy",code)
    def test_private_backup_and_per_project_packages(self):
        code=(root/"deploy_jellyfin_auth.py").read_text()
        self.assertIn("NUGET_PACKAGES",code)
        self.assertIn('os.chmod(archive, 0o600)',code)
        self.assertIn('tar.add(JELLY/"data"',code)
    def test_plugin_hash_verified_before_activation(self):
        code=(root/"deploy_jellyfin_auth.py").read_text()
        self.assertIn("deployed_plugin_binary_not_matching_release",code)

if __name__=="__main__":
    unittest.main()
