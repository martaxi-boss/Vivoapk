"""Panel domain-menu examples vs. PINK IPTV login, using synthetic accounts only.

This file performs zero external requests and contains no customer addresses.
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SubscriptionMapping
from app.schemas import ClientPlatform, ResolveCode
from app.services.mappings import import_subscription
from app.services.provider_dns import ProviderDNSUnavailable, select_provider_dns
from app.services.session import SessionResolver


# The base-domain menu is an observed snapshot, not a hardcoded allow-list.
PANEL_GENERIC_BASES = ("zvpnm.com", "fqvpnw.com", "fhvpnw.com", "beuaya.com")
GENERIC_CLIENTS = (
    ClientPlatform.ANDROID, ClientPlatform.ANDROID_TV,
    ClientPlatform.IOS, ClientPlatform.WINDOWS,
)
NATIVE_CLIENTS = (ClientPlatform.TIZEN, ClientPlatform.WEBOS)


class FakeXtream:
    def __init__(self, code="SUCCESS"):
        self.code = code
        self.calls = []

    def authenticate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(code=self.code, account_expires_at=None)

    def close(self):
        pass


def make_mapping(db, generic, native, username="example-test-user"):
    mapping = SubscriptionMapping(
        mega_subscription_id=524,
        username=username,
        dns_link=generic,
        dns_link_samsung_lg=native,
        expiring_at=datetime.now(UTC) + timedelta(days=15),
        last_synced_at=datetime.now(UTC),
    )
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping


@pytest.mark.parametrize("generic_base", PANEL_GENERIC_BASES)
@pytest.mark.parametrize("platform", GENERIC_CLIENTS)
def test_each_panel_base_remains_usable_for_generic_login(
    db, settings, generic_base, platform
):
    generic = f"http://synthetic-menu-line.{generic_base}"
    native = "http://another-synthetic-line.daizsmart.com"
    mapping = make_mapping(db, generic, native)
    xtream = FakeXtream()
    result = SessionResolver(db, xtream, settings).resolve(
        username=mapping.username, password="synthetic-test-only", platform=platform
    )
    assert result.code == ResolveCode.SUCCESS
    assert result.xtream_base_url == generic
    assert xtream.calls[0]["dns_link"] == generic
    # Login never rewrites or normalizes the subscriber's actual URLs.
    db.refresh(mapping)
    assert mapping.dns_link == generic
    assert mapping.dns_link_samsung_lg == native


@pytest.mark.parametrize("generic_base", PANEL_GENERIC_BASES)
@pytest.mark.parametrize("platform", NATIVE_CLIENTS)
def test_native_tv_uses_independent_authoritative_url(
    db, settings, generic_base, platform
):
    generic = f"http://generic-synthetic-line.{generic_base}"
    native = "http://different-synthetic-line.daizsmart.com"
    mapping = make_mapping(db, generic, native)
    xtream = FakeXtream()
    result = SessionResolver(db, xtream, settings).resolve(
        username=mapping.username, password="synthetic-test-only", platform=platform
    )
    assert result.code == ResolveCode.SUCCESS
    assert result.xtream_base_url == native
    assert xtream.calls[0]["dns_link"] == native
    assert result.xtream_base_url != generic


def test_provider_hosts_are_not_limited_to_the_visible_menu(db):
    mapping = make_mapping(
        db,
        generic="http://synthetic-line.another-valid-provider.example",
        native="http://synthetic-tv.other-tv-provider.example",
    )
    assert select_provider_dns(mapping, ClientPlatform.ANDROID) == mapping.dns_link
    assert select_provider_dns(mapping, ClientPlatform.TIZEN) == mapping.dns_link_samsung_lg


def test_tv_without_provider_tv_dns_fails_without_generic_fallback(db, settings):
    mapping = make_mapping(db, "http://sample.zvpnm.com", None)
    xtream = FakeXtream()
    result = SessionResolver(db, xtream, settings).resolve(
        username=mapping.username, password="synthetic-only", platform=ClientPlatform.WEBOS
    )
    assert result.code == ResolveCode.UPSTREAM_ERROR
    assert xtream.calls == []


@pytest.mark.parametrize("unsafe", [
    "http://127.0.0.1",
    "http://localhost",
    "http://sample.zvpnm.com/path",
    "http://sample.zvpnm.com/?password=synthetic",
    "file:///etc/passwd",
])
def test_invalid_provider_origin_is_never_passed_to_xtream(db, settings, unsafe):
    mapping = make_mapping(db, unsafe, "http://different.daizsmart.com")
    xtream = FakeXtream()
    result = SessionResolver(db, xtream, settings).resolve(
        username=mapping.username, password="synthetic-only",
        platform=ClientPlatform.ANDROID
    )
    assert result.code == ResolveCode.UPSTREAM_ERROR
    assert xtream.calls == []


def test_two_visible_login_fields_and_hidden_app_platform(settings, session_factory):
    with session_factory() as db:
        make_mapping(db, "http://sample.fqvpnw.com", "http://sample.daizsmart.com")
    xtream = FakeXtream()
    app = create_app(settings)
    app.state.session_factory = session_factory
    app.state.xtream_client_factory = lambda: xtream
    app.state.mega_client_factory = lambda: pytest.fail("Mapped account must not scan Mega")
    client = TestClient(app)

    # Old PINK Android sends only username+password (no manually typed DNS).
    generic = client.post("/v1/session/resolve", json={
        "username": "example-test-user", "password": "synthetic-only"
    })
    assert generic.status_code == 200
    assert generic.json()["code"] == "SUCCESS"
    assert generic.json()["xtream_base_url"] == "http://sample.fqvpnw.com"

    # Native app embeds its platform internally; user still types only two fields.
    tv = client.post("/v1/session/resolve", json={
        "username": "example-test-user", "password": "synthetic-only", "platform": "webos"
    })
    assert tv.status_code == 200
    assert tv.json()["code"] == "SUCCESS"
    assert tv.json()["xtream_base_url"] == "http://sample.daizsmart.com"
    assert "synthetic-only" not in generic.text + tv.text

    # Customer cannot submit an arbitrary DNS or panel domain-menu selection.
    bad = client.post("/v1/session/resolve", json={
        "username": "example-test-user", "password": "synthetic-only",
        "dns": "http://attacker.example", "platform": "android"
    })
    assert bad.status_code == 422


def test_explicit_provider_refresh_does_not_reconstruct_tv_dns(db):
    old = make_mapping(
        db, "http://synthetic-old.zvpnm.com", "http://unrelated.daizsmart.com"
    )
    authoritative = SimpleNamespace(
        id=old.mega_subscription_id, username=old.username,
        dns_link="http://new-synthetic.beuaya.com",
        dns_link_for_samsung_lg="http://other-synthetic.daizsmart.com",
        expiring_at=datetime.now(UTC) + timedelta(days=10),
    )

    class FakeMega:
        def get_subscription(self, requested_id):
            assert requested_id == old.mega_subscription_id
            return authoritative

    refreshed = import_subscription(db, FakeMega(), old.mega_subscription_id)
    assert refreshed.id == old.id
    assert refreshed.dns_link == authoritative.dns_link
    assert refreshed.dns_link_samsung_lg == authoritative.dns_link_for_samsung_lg
