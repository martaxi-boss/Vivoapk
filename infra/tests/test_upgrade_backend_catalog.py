"""Static safety guards for backend-only Ferrari upgrade."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "infra/upgrade_backend_catalog.py").read_text()
TREE = ast.parse(SOURCE)


def test_backend_upgrade_is_opt_in_root_only():
    assert 'choices=("preflight", "activate", "rollback", "verify")' in SOURCE
    assert 'root_required' in SOURCE and 'full_pin_required' in SOURCE
    assert 'backup_path' in SOURCE and 'original.sha' in SOURCE
    assert 'already_at_expected_revision' in SOURCE


def test_backend_upgrade_preserves_other_services_and_vault():
    assert 'cmd(["systemctl", "restart", VIVO]' in SOURCE
    assert 'cmd(["systemctl", "restart", JELLY]' not in SOURCE
    assert 'jellyfin-pink.service' in SOURCE
    assert 'pink-iptv-backend.service' in SOURCE
    assert "rm -rf" not in SOURCE
    assert "project-secrets" not in SOURCE
    assert 'stream_proxy_added": False' in SOURCE


def test_backend_requires_category_routes_unauthorized():
    for route in ('/v1/catalog/categories', '/v1/catalog/category-items',
                  '/v1/catalog/series', '/v1/catalog/epg'):
        assert route in SOURCE
    assert '!= 401' in SOURCE
    assert 'rollback(sha)' in SOURCE
