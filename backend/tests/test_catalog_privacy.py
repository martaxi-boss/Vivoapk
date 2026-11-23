"""Contract tests for account-scoped catalogue metadata (synthetic only)."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import jwt
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.clients.catalog import CatalogUnavailable, XtreamCatalogClient
from app.main import create_app
from app.models import SubscriptionMapping
from app.schemas import CatalogKind
from app.security import issue_session_token


class FakeMega:
    def __init__(self, rows: dict[int, SimpleNamespace]):
        self.rows = rows
        self.calls: list[int] = []
        self.closed = False

    def get_subscription(self, mega_subscription_id: int):
        self.calls.append(mega_subscription_id)
        return self.rows[mega_subscription_id]

    def close(self):
        self.closed = True


class FakeXtream:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.status = "SUCCESS"

    def authenticate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(code=self.status, account_expires_at=None)

    def close(self):
        self.closed = True


class FakeCatalog:
    def __init__(self):
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        title = "Canal Azul" if kwargs["username"] == "sub-a" else "Canal Verde"
        return (
            [{"id": "20", "name": "Portugal"}],
            [{"id": "101", "title": title, "category_id": "20", "container_extension": None}],
        )


def test_two_subscriptions_are_isolated_by_signed_mapping_identity(settings, session_factory):
    future = datetime.now(UTC) + timedelta(days=10)
    with session_factory() as db:
        for n in (1, 2):
            db.add(SubscriptionMapping(
                mega_subscription_id=900 + n,
                username=f"sub-{'a' if n == 1 else 'b'}",
                dns_link=f"http://origin-{n}.example.com",
                dns_link_samsung_lg=None,
                expiring_at=future,
                last_synced_at=datetime.now(UTC),
            ))
        db.commit()
        id_a = db.query(SubscriptionMapping).filter_by(username="sub-a").one().id
        id_b = db.query(SubscriptionMapping).filter_by(username="sub-b").one().id

    accounts = {
        901: SimpleNamespace(
            id=901, username="sub-a", password=SecretStr("secret-a"),
            expiring_at=future, dns_link="http://origin-1.example.com",
            dns_link_for_samsung_lg=None,
        ),
        902: SimpleNamespace(
            id=902, username="sub-b", password=SecretStr("secret-b"),
            expiring_at=future, dns_link="http://origin-2.example.com",
            dns_link_for_samsung_lg=None,
        ),
    }
    mega, xtream, catalogue = FakeMega(accounts), FakeXtream(), FakeCatalog()
    app = create_app(settings)
    app.state.session_factory = session_factory
    app.state.mega_client_factory = lambda: mega
    app.state.xtream_client_factory = lambda: xtream
    app.state.catalog_client_factory = lambda: catalogue
    client = TestClient(app)

    token_a, _ = issue_session_token(id_a, settings)
    token_b, _ = issue_session_token(id_b, settings)
    def request(token, body=None):
        return client.post(
            "/v1/catalog/list",
            headers={"Authorization": "Bearer " + token},
            json=body or {"kind": "live", "platform": "android"},
        )

    a, b = request(token_a), request(token_b)
    assert a.status_code == b.status_code == 200
    assert a.headers["cache-control"] == b.headers["cache-control"] == "no-store"
    assert a.json()["items"][0]["title"] == "Canal Azul"
    assert b.json()["items"][0]["title"] == "Canal Verde"
    assert "origin-1" not in a.text and "secret-a" not in a.text
    assert "origin-2" not in b.text and "secret-b" not in b.text
    assert mega.calls == [901, 902]
    assert [i["username"] for i in catalogue.calls] == ["sub-a", "sub-b"]

    # The caller cannot override the signed identity with a body-level username.
    denied = request(token_a, {"kind": "live", "username": "sub-b"})
    assert denied.status_code == 422
    assert request("fabricated").status_code == 401
    assert client.post("/v1/catalog/list", json={"kind": "live"}).status_code == 401

    # Unsigned, expired and wrong-signature tokens are rejected before provider requests.
    now = datetime.now(UTC)
    old = jwt.encode(
        {"sub": f"mapping:{id_a}", "iss": "vivoapk",
         "iat": now - timedelta(minutes=11), "exp": now - timedelta(minutes=6)},
        settings.session_signing_key.get_secret_value(), algorithm="HS256",
    )
    fake = jwt.encode(
        {"sub": f"mapping:{id_b}", "iss": "vivoapk",
         "iat": now, "exp": now + timedelta(minutes=1)},
        "wrong-32-byte-key-wrong-32-byte-key", algorithm="HS256",
    )
    assert request(old).status_code == request(fake).status_code == 401
    assert mega.calls == [901, 902]

    # Revoked, expired or unresolvable upstream entitlements fail closed.
    accounts[901].username = "other-account"
    assert request(token_a).status_code == 401
    accounts[901].username = "sub-a"
    accounts[901].password = SecretStr("")
    assert request(token_a).status_code == 503
    accounts[901].password = SecretStr("secret-a")
    xtream.status = "DISABLED"
    assert request(token_a).status_code == 401


def test_xtream_metadata_actions_do_not_expose_unsafe_fields(public_resolver):
    requests = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        action = request.url.params.get("action")
        assert request.url.params.get("username") == "demo"
        assert request.url.params.get("password") == "test-only-password"
        body = {
            "get_live_categories": [
                {"category_id": "10", "category_name": "Notícias"},
                {"category_id": "10", "category_name": "Duplicado"},
            ],
            "get_live_streams": [
                {"stream_id": 42, "name": "Canal Exemplo", "category_id": "10",
                 "stream_icon": "http://image.example/a?token=secret",
                 "direct_source": "http://other.example/private", "password": "secret"},
                {"stream_id": 43, "name": "\ninvalid", "category_id": "10"},
            ],
        }[action]
        return httpx.Response(200, json=body)

    adapter = XtreamCatalogClient(
        resolver=public_resolver,
        transport_factory=lambda _target: httpx.MockTransport(handler),
    )
    categories, items = adapter.list(
        dns_link="http://stream.example.com", username="demo",
        password="test-only-password", kind=CatalogKind.LIVE,
    )
    assert len(requests) == 2
    assert [q.url.params.get("action") for q in requests] == [
        "get_live_categories", "get_live_streams",
    ]
    assert categories == [{"id": "10", "name": "Notícias"}]
    assert items == [{
        "id": "42", "title": "Canal Exemplo", "category_id": "10",
        "container_extension": None,
    }]
    assert "secret" not in str(items) and "http" not in str(items)


def test_catalogue_rejects_private_destinations_and_redirects(public_resolver):
    adapter = XtreamCatalogClient(
        resolver=public_resolver,
        transport_factory=lambda _target: httpx.MockTransport(
            lambda _req: httpx.Response(302, headers={"Location": "http://127.0.0.1/"})
        ),
    )
    for origin in ("http://127.0.0.1", "http://localhost", "http://stream.example.com"):
        try:
            adapter.list(
                dns_link=origin, username="demo", password="test-only-password",
                kind=CatalogKind.LIVE,
            )
        except CatalogUnavailable:
            continue
        raise AssertionError("Unsafe or redirected catalogue unexpectedly accepted")


def test_catalogue_rejects_oversized_metadata(public_resolver):
    adapter = XtreamCatalogClient(
        resolver=public_resolver,
        transport_factory=lambda _target: httpx.MockTransport(
            lambda _req: httpx.Response(200, content=b"[" + b"x" * (8 * 1024 * 1024) + b"]")
        ),
    )
    try:
        adapter.list(
            dns_link="http://stream.example.com", username="demo",
            password="test-only-password", kind=CatalogKind.LIVE,
        )
    except CatalogUnavailable:
        pass
    else:
        raise AssertionError("Oversized JSON was accepted")
