"""Plugin-only deployment contracts; syntactic and safety CI."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "infra/upgrade_jellyfin_multimedia.py").read_text()


def test_jellyfin_guarded_upgrade_source_valid():
    ast.parse(SRC)
    for phrase in ('stage', 'preflight', 'activate', 'verify', 'rollback',
                   'backend_version_requires_review', 'admin_identity_modified',
                   'source_sha_mismatch', 'backend_changes_forbidden'):
        assert phrase in SRC


def test_never_restarts_backend_or_touch_private_secrets():
    assert 'guard.run(["systemctl", "stop", guard.JELLY_SERVICE]' in SRC
    assert 'guard.run(["systemctl", "restart", guard.VIVO]' not in SRC
    assert 'guard.run(["systemctl", "stop", guard.VIVO]' not in SRC
    assert "project-secrets" not in SRC
    assert "stream_proxy_installed" in SRC


def test_only_native_catalog_flags_are_enabled():
    for name in ('CategoryNavigation', 'PilotLiveChannels',
                 'FullLiveCatalog', 'ProvisionPrivateLibraries',
                 'ChannelSlotOwners'):
        assert name in SRC
    assert '("CategoryNavigation", "true")' in SRC
    assert '("FullLiveCatalog", "false")' in SRC
