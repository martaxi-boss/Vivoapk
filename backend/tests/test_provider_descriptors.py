"""Synthetic account descriptors: restart speed without stored credentials."""
import json
from dataclasses import replace

import pytest
from pydantic import SecretStr

from app.schemas import CatalogKind
from app.services.media_body import MediaReadError
from app.services.media_origin import AuthorizedMediaOrigin, MediaAsset
from app.services.provider_descriptors import ProviderDescriptors
from app.services.provider_live import ProviderLiveLocatorCache, lookup_provider_ts_source
from test_provider_live import Pool, Response

KEY = b"synthetic-descriptor-key-32-bytes-minimum"


@pytest.fixture
def source():
    return AuthorizedMediaOrigin(MediaAsset(CatalogKind.LIVE, "10", "101"),
                                 "https://assigned.example.com", "fixture-owner",
                                 SecretStr("fixture-secret-value"), "ts")


def identity(source, http=False):
    return json.dumps([source.origin, source.username,
                       source.password.get_secret_value(), http])


def locator(source, asset="101", family=0, port=True):
    root = source.origin + (":443" if port else "")
    base = f"{source.username}/{source.password.get_secret_value()}/{asset}"
    return root + ("/" + base if family == 0 else "/live/" + base + ".ts")


def invoke(source, store, pool):
    return lookup_provider_ts_source(
        source, locator_cache=ProviderLiveLocatorCache(descriptors=store),
        resolver=lambda *_: ["93.184.216.34"], pool_factory=lambda _: pool)


def test_restart_reuses_exact_issued_routes_without_fetching_or_storing_secrets(tmp_path, source):
    store = ProviderDescriptors(tmp_path / "descriptors", KEY)
    urls = [locator(source), locator(source, "102", family=1), locator(source, "103", port=False)]
    pool = Pool(Response(("#EXTM3U\n" + "\n".join(urls) + "\n").encode()))
    assert invoke(source, store, pool)._request_url() == urls[0]
    assert len(pool.requests) == 1
    files = list((tmp_path / "descriptors").glob("*.json"))
    assert len(files) == 1
    raw = files[0].read_bytes()
    for private in (b"fixture-owner", b"fixture-secret-value", b"assigned.example.com", b"https://"):
        assert private not in raw
    assert files[0].stat().st_mode & 0o777 == 0o600
    # Fresh objects simulate backend restart and expiry of the RAM-only cache.
    restarted = ProviderDescriptors(tmp_path / "descriptors", KEY)
    for asset, expected in zip(("102", "103"), urls[1:]):
        selected = replace(source, asset=MediaAsset(CatalogKind.LIVE, "10", asset))
        assert invoke(selected, restarted, pool)._request_url() == expected
    assert len(pool.requests) == 1


def test_mac_scope_rejects_changed_account_password_dns_and_policy(tmp_path, source):
    store = ProviderDescriptors(tmp_path / "descriptors", KEY)
    assert store.write(identity(source), source, {"101": locator(source)})
    assert store.read(identity(source), source) == locator(source)
    for changed in (replace(source, username="other-owner"),
                    replace(source, password=SecretStr("different-secret")),
                    replace(source, origin="https://other.example.com")):
        assert store.read(identity(changed), changed) is None
    assert store.read(identity(source, True), source) is None
    rotated = ProviderDescriptors(tmp_path / "descriptors", b"rotated-synthetic-secret-key-32-bytes")
    assert rotated.read(identity(source), source) is None


def test_tampering_and_expiry_cannot_serve_stale_metadata_after_provider_denial(tmp_path, source):
    now = [1000.0]
    store = ProviderDescriptors(tmp_path / "descriptors", KEY, ttl_seconds=5, clock=lambda: now[0])
    assert store.write(identity(source), source, {"101": locator(source)})
    path = next((tmp_path / "descriptors").glob("*.json"))
    value = json.loads(path.read_text())
    value["body"]["items"]["101"] = [1, False]
    path.write_text(json.dumps(value))
    assert store.read(identity(source), source) is None
    assert store.write(identity(source), source, {"101": locator(source)})
    now[0] = 1006
    assert store.read(identity(source), source) is None
    denied = Response(b"private-response-must-not-be-read", status=403)
    pool = Pool(denied)
    with pytest.raises(MediaReadError):
        invoke(source, store, pool)
    assert len(pool.requests) == 1 and denied.reads == 0


def test_only_validated_asset_route_descriptors_are_written(tmp_path, source):
    store = ProviderDescriptors(tmp_path / "descriptors", KEY)
    urls = {"101": locator(source), "102": "https://foreign.example.com/fixture-owner/fixture-secret-value/102",
            "103": locator(source, "104"), "104": locator(source, "104") + "?token=unapproved"}
    assert store.write(identity(source), source, urls)
    body = json.loads(next((tmp_path / "descriptors").glob("*.json")).read_text())["body"]
    assert set(body["items"]) == {"101"}


def test_private_file_bounds_and_symlink_rejection(tmp_path, source):
    root = tmp_path / "descriptors"
    store = ProviderDescriptors(root, KEY)
    assert store.write(identity(source), source, {"101": locator(source)})
    path = next(root.glob("*.json"))
    outside = tmp_path / "outside"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    assert store.read(identity(source), source) is None
    root.chmod(0o755)
    with pytest.raises(ValueError):
        ProviderDescriptors(root, KEY)


def test_maximum_two_account_files_and_future_timestamp_rejected(tmp_path, source):
    root = tmp_path / "descriptors"
    store = ProviderDescriptors(root, KEY, clock=lambda: 2000.0)
    for i in range(4):
        current = replace(source, username=f"owner-{i}")
        assert store.write(identity(current), current, {"101": locator(current)})
    assert len(list(root.glob("*.json"))) == 2
    earlier = ProviderDescriptors(root, KEY, clock=lambda: 1999.0)
    assert earlier.read(identity(current), current) is None
