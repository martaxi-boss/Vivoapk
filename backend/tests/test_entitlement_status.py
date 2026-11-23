"""Synthetic tests for credential-free Mega subscription revalidation."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app
from app.models import SubscriptionMapping


class FakeMega:
    def __init__(self, row):
        self.row = row
        self.calls = 0

    def get_subscription(self, subscription_id):
        self.calls += 1
        assert subscription_id == 735
        return self.row

    def close(self):
        pass


class FakeXtream:
    def __init__(self):
        self.code = "SUCCESS"
        self.account_expires_at = None
        self.calls = 0

    def authenticate(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(
            code=self.code, account_expires_at=self.account_expires_at
        )

    def close(self):
        pass


def test_monitor_key_and_provider_expiry_renewal(settings, session_factory):
    # Real subscription passwords are never required in this test or sent to Jellyfin.
    settings.entitlement_monitor_key = SecretStr("synthetic-private-monitor-key-1234567890")
    future = datetime.now(UTC) + timedelta(days=90)
    past = datetime.now(UTC) - timedelta(days=1)
    with session_factory() as db:
        db.add(SubscriptionMapping(
            mega_subscription_id=735, username="client-a",
            dns_link="http://origin.example.com", dns_link_samsung_lg=None,
            expiring_at=past, last_synced_at=past,
        ))
        db.commit()

    upstream = SimpleNamespace(
        id=735, username="client-a", expiring_at=future,
        dns_link="http://origin.example.com", dns_link_for_samsung_lg=None,
        password=SecretStr("synthetic-provider-secret"),
    )
    mega, xtream = FakeMega(upstream), FakeXtream()
    app = create_app(settings)
    app.state.session_factory = session_factory
    app.state.mega_client_factory = lambda: mega
    app.state.xtream_client_factory = lambda: xtream
    client = TestClient(app)
    route = "/_internal/entitlement/status"
    body = {"username": "client-a"}
    headers = {"X-Vivoapk-Monitor-Key": settings.entitlement_monitor_key.get_secret_value()}

    for denied_headers in ({}, {"X-Vivoapk-Monitor-Key": "guessed"}):
        r = client.post(route, json=body, headers=denied_headers)
        assert r.status_code == 403 and r.headers["cache-control"] == "no-store"
        assert mega.calls == 0
    assert client.post(route, json={**body, "password": "unsafe"},
                       headers=headers).status_code == 422
    assert mega.calls == 0

    # Renewal updates a previously expired local mapping with fresh official data.
    r = client.post(route, json=body, headers=headers)
    assert r.status_code == 200 and r.json() == {"state": "ACTIVE"}
    assert "synthetic-provider-secret" not in r.text and "client-a" not in r.text
    assert r.headers["cache-control"] == "no-store"
    with session_factory() as db:
        mapping = db.query(SubscriptionMapping).filter_by(username="client-a").one()
        assert mapping.expiring_at.replace(tzinfo=UTC) == future

    upstream.expiring_at = past
    r = client.post(route, json=body, headers=headers)
    assert r.json() == {"state": "EXPIRED"}

    upstream.expiring_at = future
    xtream.code = "DISABLED"
    assert client.post(route, json=body, headers=headers).json() == {"state": "DISABLED"}

    xtream.code = "EXPIRED"
    assert client.post(route, json=body, headers=headers).json() == {"state": "EXPIRED"}

    xtream.code = "SUCCESS"
    xtream.account_expires_at = past
    assert client.post(route, json=body, headers=headers).json() == {"state": "EXPIRED"}

    xtream.account_expires_at = None
    upstream.password = None
    assert client.post(route, json=body, headers=headers).json() == {"state": "UNAVAILABLE"}

    upstream.password = SecretStr("synthetic-provider-secret")
    upstream.username = "different-user"
    assert client.post(route, json=body, headers=headers).json() == {"state": "DISABLED"}
    upstream.username = "client-a"

    assert client.post(route, json={"username": "unknown"}, headers=headers).json() == {
        "state": "UNAVAILABLE"
    }


def test_entitlement_endpoint_is_disabled_without_private_key(settings, session_factory):
    settings.entitlement_monitor_key = None
    app = create_app(settings)
    app.state.session_factory = session_factory
    r = TestClient(app).post(
        "/_internal/entitlement/status",
        json={"username": "unknown"},
        headers={"X-Vivoapk-Monitor-Key": "synthetic-private-monitor-key-1234567890"},
    )
    assert r.status_code == 403
