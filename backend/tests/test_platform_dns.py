"""Synthetic tests: no real Mega subscriptions or live network calls."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SubscriptionMapping
from app.schemas import ClientPlatform, ResolveCode
from app.services.session import SessionResolver


class FakeXtream:
    def __init__(self, code="SUCCESS"):
        self.code = code
        self.calls = []

    def authenticate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(code=self.code, account_expires_at=None)

    def close(self):
        pass


@pytest.fixture
def dual_dns_mapping(db):
    row = SubscriptionMapping(
        mega_subscription_id=789,
        username="synthetic-customer",
        dns_link="https://generic.example.org",
        dns_link_samsung_lg="https://native.example.org",
        expiring_at=datetime.now(UTC) + timedelta(days=1),
        last_synced_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.mark.parametrize("platform", [
    ClientPlatform.ANDROID, ClientPlatform.ANDROID_TV,
    ClientPlatform.IOS, ClientPlatform.WINDOWS,
])
def test_generic_platforms_use_generic_dns(db, dual_dns_mapping, settings, platform):
    xtream = FakeXtream()
    result = SessionResolver(db, xtream, settings).resolve(
        username=dual_dns_mapping.username, password="synthetic-pass", platform=platform
    )
    assert result.code == ResolveCode.SUCCESS
    assert result.xtream_base_url == "https://generic.example.org"
    assert xtream.calls[0]["dns_link"] == "https://generic.example.org"


@pytest.mark.parametrize("platform", [ClientPlatform.TIZEN, ClientPlatform.WEBOS])
def test_native_samsung_lg_use_native_dns(db, dual_dns_mapping, settings, platform):
    xtream = FakeXtream()
    result = SessionResolver(db, xtream, settings).resolve(
        username=dual_dns_mapping.username, password="synthetic-pass", platform=platform
    )
    assert result.code == ResolveCode.SUCCESS
    assert result.xtream_base_url == "https://native.example.org"
    assert xtream.calls[0]["dns_link"] == "https://native.example.org"


def test_missing_native_dns_fails_closed(db, dual_dns_mapping, settings):
    dual_dns_mapping.dns_link_samsung_lg = None
    db.commit()
    xtream = FakeXtream()
    result = SessionResolver(db, xtream, settings).resolve(
        username=dual_dns_mapping.username, password="synthetic-pass",
        platform=ClientPlatform.WEBOS
    )
    assert result.code == ResolveCode.UPSTREAM_ERROR
    assert xtream.calls == []


def test_wrong_password_leaks_no_url(db, dual_dns_mapping, settings):
    xtream = FakeXtream("INVALID_CREDENTIALS")
    result = SessionResolver(db, xtream, settings).resolve(
        username=dual_dns_mapping.username, password="wrong", platform=ClientPlatform.TIZEN
    )
    assert result.code == ResolveCode.INVALID_CREDENTIALS
    assert result.xtream_base_url is None


@pytest.mark.parametrize("platform", ["not_a_tv", "samsung", "", 1])
def test_unknown_platform_rejected_without_upstream(settings, session_factory, platform):
    app = create_app(settings)
    app.state.session_factory = session_factory
    app.state.xtream_client_factory = lambda: pytest.fail("invalid platform reached provider")
    response = TestClient(app).post(
        "/v1/session/resolve",
        json={"username": "fixture", "password": "fixture", "platform": platform},
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "Invalid request"}


def test_http_endpoint_selects_tizen_dns(settings, session_factory):
    with session_factory() as session:
        session.add(SubscriptionMapping(
            mega_subscription_id=145, username="native-user",
            dns_link="https://generic.example.org",
            dns_link_samsung_lg="https://native.example.org",
            expiring_at=datetime.now(UTC) + timedelta(days=1),
            last_synced_at=datetime.now(UTC),
        ))
        session.commit()
    xtream = FakeXtream()
    app = create_app(settings)
    app.state.session_factory = session_factory
    app.state.xtream_client_factory = lambda: xtream
    app.state.mega_client_factory = lambda: pytest.fail("known user should not trigger Mega scan")
    response = TestClient(app).post(
        "/v1/session/resolve",
        json={"username": "native-user", "password": "fixture", "platform": "tizen"}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["code"] == "SUCCESS"
    assert body["xtream_base_url"] == "https://native.example.org"
    assert "fixture" not in response.text
