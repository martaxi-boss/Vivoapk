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
        item_id = "301" if kwargs["username"] == "sub-a" else "401"
        if kwargs["kind"] == CatalogKind.SERIES:
            return (
                [{"id": "20", "name": "Séries"}],
                [{"id": item_id, "title": "Série " + title,
                  "category_id": "20", "container_extension": None}],
            )
        return (
            [{"id": "20", "name": "Portugal"}],
            [{"id": "101", "title": title, "category_id": "20", "container_extension": None}],
        )


    def list_categories(self, **kwargs):
        self.calls.append(kwargs)
        category = "10" if kwargs["username"] == "sub-a" else "20"
        return [{"id": category, "name": "Categoria particular"}]

    def list_category_items(self, **kwargs):
        self.calls.append(kwargs)
        suffix = "Azul" if kwargs["username"] == "sub-a" else "Verde"
        return [{
            "id": "301" if kwargs["username"] == "sub-a" else "401",
            "title": "Canal " + suffix,
            "category_id": kwargs["category_id"],
            "container_extension": None,
        }]

    def preview_live(self, **kwargs):
        self.calls.append(kwargs)
        suffix = "Azul" if kwargs["username"] == "sub-a" else "Verde"
        return ([{"id": "10", "name": "Portugal"}],
                [{"id": "88", "title": "Canal " + suffix,
                  "category_id": "10", "container_extension": None}])

    def series_detail(self, **kwargs):
        self.calls.append(kwargs)
        return {"series_id": kwargs["series_id"], "title": "Série pessoal",
                "seasons": [{"id": "1", "title": "Temporada 1"}],
                "episodes": [{"id": "90", "season_id": "1", "title": "Episódio 1",
                              "episode_number": "1", "container_extension": "mp4"}]}

    def short_epg(self, **kwargs):
        self.calls.append(kwargs)
        return {"stream_id": kwargs["stream_id"],
                "programmes": [{"title": "Jornal", "start_timestamp": 1800000000,
                                "end_timestamp": 1800003600}]}


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

    for token, title in ((token_a, "Canal Azul"), (token_b, "Canal Verde")):
        preview = client.post(
            "/v1/catalog/preview",
            headers={"Authorization": "Bearer " + token},
            json={"kind": "live", "platform": "android"},
        )
        assert preview.status_code == 200
        assert preview.json()["items"][0]["title"] == title
        assert "secret-" not in preview.text and "origin-" not in preview.text
        assert preview.headers["Cache-Control"] == "no-store"

    a, b = request(token_a), request(token_b)
    assert a.status_code == b.status_code == 200
    assert a.headers["cache-control"] == b.headers["cache-control"] == "no-store"
    assert a.json()["items"][0]["title"] == "Canal Azul"
    assert b.json()["items"][0]["title"] == "Canal Verde"
    assert "origin-1" not in a.text and "secret-a" not in a.text
    assert "origin-2" not in b.text and "secret-b" not in b.text
    assert mega.calls == [901, 902, 901, 902]
    assert [i["username"] for i in catalogue.calls] == ["sub-a", "sub-b", "sub-a", "sub-b"]

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
    assert mega.calls == [901, 902, 901, 902]

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
        # The real Mega/Xtream probe returned 200 on explicit close framing;
        # this avoids reusing an upstream connection after chunked replies.
        assert request.headers["connection"] == "close"
        assert request.headers["accept-encoding"] == "identity"
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


def test_smart_tv_uses_only_authoritative_dedicated_dns(settings, session_factory):
    future = datetime.now(UTC) + timedelta(days=7)
    with session_factory() as db:
        mapping = SubscriptionMapping(
            mega_subscription_id=1001, username="tv-a",
            dns_link="http://general.example.com",
            dns_link_samsung_lg="http://native.example.com",
            expiring_at=future, last_synced_at=datetime.now(UTC),
        )
        db.add(mapping)
        db.commit()
        mapping_id = mapping.id

    provider = SimpleNamespace(
        id=1001, username="tv-a", password=SecretStr("synthetic"),
        expiring_at=future, dns_link="http://general.example.com",
        dns_link_for_samsung_lg="http://native.example.com",
    )
    mega, xtream, catalogue = FakeMega({1001: provider}), FakeXtream(), FakeCatalog()
    app = create_app(settings)
    app.state.session_factory = session_factory
    app.state.mega_client_factory = lambda: mega
    app.state.xtream_client_factory = lambda: xtream
    app.state.catalog_client_factory = lambda: catalogue
    client = TestClient(app)
    token, _ = issue_session_token(mapping_id, settings)
    for platform, expected in (
        ("tizen", "http://native.example.com"),
        ("webos", "http://native.example.com"),
        ("android", "http://general.example.com"),
    ):
        response = client.post(
            "/v1/catalog/list",
            headers={"Authorization": "Bearer " + token},
            json={"kind": "live", "platform": platform},
        )
        assert response.status_code == 200
        assert catalogue.calls[-1]["dns_link"] == expected
        assert xtream.calls[-1]["dns_link"] == expected

    provider.dns_link_for_samsung_lg = None
    result = client.post(
        "/v1/catalog/list",
        headers={"Authorization": "Bearer " + token},
        json={"kind": "live", "platform": "webos"},
    )
    assert result.status_code == 503
    assert len(catalogue.calls) == 3


def test_vod_and_series_metadata_are_strictly_whitelisted(public_resolver):
    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params.get("action")
        rows = {
            "get_vod_categories": [{"category_id": 3, "category_name": "Cinema"}],
            "get_vod_streams": [
                {"stream_id": 55, "name": "Filme exemplo",
                 "category_id": 3, "container_extension": "mp4",
                 "direct_source": "http://private.example/movie"},
                {"stream_id": 56, "name": "Filme com extensão inválida",
                 "container_extension": "../../hidden"},
            ],
            "get_series_categories": [{"category_id": "4", "category_name": "Séries"}],
            "get_series": [
                {"series_id": 77, "name": "Série exemplo",
                 "category_id": 4, "cover": "https://private.example/cover"},
            ],
        }
        return httpx.Response(200, json=rows[action])

    adapter = XtreamCatalogClient(
        resolver=public_resolver,
        transport_factory=lambda _target: httpx.MockTransport(handler),
    )
    _, films = adapter.list(
        dns_link="https://public.example.com", username="a", password="b",
        kind=CatalogKind.VOD,
    )
    assert films[0] == {
        "id": "55", "title": "Filme exemplo",
        "category_id": "3", "container_extension": "mp4",
    }
    assert films[1]["container_extension"] is None
    _, series = adapter.list(
        dns_link="https://public.example.com", username="a", password="b",
        kind=CatalogKind.SERIES,
    )
    assert series == [{
        "id": "77", "title": "Série exemplo",
        "category_id": "4", "container_extension": None,
    }]
    assert "http" not in str(films) + str(series)



def test_series_and_guide_are_scoped_to_logged_in_account(settings, session_factory):
    future = datetime.now(UTC) + timedelta(days=14)
    with session_factory() as db:
        for idx, username in ((1, "sub-a"), (2, "sub-b")):
            db.add(SubscriptionMapping(
                mega_subscription_id=2000 + idx,
                username=username, dns_link="http://provider.example.com",
                dns_link_samsung_lg=None, expiring_at=future,
                last_synced_at=datetime.now(UTC),
            ))
        db.commit()
        ids = {name: db.query(SubscriptionMapping).filter_by(username=name).one().id
               for name in ("sub-a", "sub-b")}

    rows = {
        2001: SimpleNamespace(
            id=2001, username="sub-a", password=SecretStr("synthetic-a"),
            expiring_at=future, dns_link="http://provider.example.com",
            dns_link_for_samsung_lg=None,
        ),
        2002: SimpleNamespace(
            id=2002, username="sub-b", password=SecretStr("synthetic-b"),
            expiring_at=future, dns_link="http://provider.example.com",
            dns_link_for_samsung_lg=None,
        ),
    }
    mega, xtream, catalogue = FakeMega(rows), FakeXtream(), FakeCatalog()
    app = create_app(settings)
    app.state.session_factory = session_factory
    app.state.mega_client_factory = lambda: mega
    app.state.xtream_client_factory = lambda: xtream
    app.state.catalog_client_factory = lambda: catalogue
    client = TestClient(app)
    tokens = {name: issue_session_token(ids[name], settings)[0]
              for name in ("sub-a", "sub-b")}

    def post(name, endpoint, payload):
        return client.post(
            endpoint, headers={"Authorization": "Bearer " + tokens[name]},
            json=payload,
        )

    expected = {
        "sub-a": ("301", "101", "synthetic-a"),
        "sub-b": ("401", "101", "synthetic-b"),
    }
    for name, (series_id, channel_id, password) in expected.items():
        series = post(name, "/v1/catalog/series", {"series_id": series_id})
        assert series.status_code == 200
        assert series.headers["Cache-Control"] == "no-store"
        assert series.json()["episodes"][0]["title"] == "Episódio 1"
        assert password not in series.text
        epg = post(name, "/v1/catalog/epg", {"stream_id": channel_id, "limit": 2})
        assert epg.status_code == 200
        assert epg.json()["programmes"][0]["title"] == "Jornal"
        assert password not in epg.text

    # User A cannot ask the provider for a series present only in user B's list.
    before = len(catalogue.calls)
    forbidden = post("sub-a", "/v1/catalog/series", {"series_id": "401"})
    assert forbidden.status_code == 401
    assert len(catalogue.calls) == before + 1  # only the account-scoped listing
    assert post("sub-a", "/v1/catalog/epg", {"stream_id": "999"}).status_code == 401
    assert post("sub-b", "/v1/catalog/series", {"series_id": "../../bad"}).status_code == 422
    assert post("sub-b", "/v1/catalog/epg", {"stream_id": "101", "limit": 30}).status_code == 422
    assert client.post("/v1/catalog/series", json={"series_id": "301"}).status_code == 401


def test_series_and_guide_upstream_responses_are_sanitized(public_resolver):
    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params.get("action")
        data = {
            "get_series_info": {
                "info": {"name": "Programa Exemplo", "movie_image": "http://unsafe/private"},
                "seasons": [{"season_number": 1, "name": "Primeira temporada"}],
                "episodes": {"1": [{
                    "id": "901", "episode_num": 1, "title": "Piloto",
                    "container_extension": "mp4",
                    "movie_image": "http://unsafe/private",
                    "direct_source": "http://not-forwarded/private",
                }]},
            },
            "get_short_epg": {
                "epg_listings": [{
                    "title": "Notícias da noite", "start_timestamp": "1830000000",
                    "stop_timestamp": "1830003600", "password": "secret",
                    "direct_source": "http://not-forwarded/private",
                }],
            },
        }
        return httpx.Response(200, json=data[action])
    adapter = XtreamCatalogClient(
        resolver=public_resolver,
        transport_factory=lambda _target: httpx.MockTransport(handler),
    )
    args = {"dns_link": "http://public.example.com",
            "username": "fixture", "password": "fixture-secret"}
    info = adapter.series_detail(**args, series_id="123")
    assert info["series_id"] == "123"
    assert info["seasons"] == [{"id": "1", "title": "Primeira temporada"}]
    assert info["episodes"] == [{
        "id": "901", "season_id": "1", "episode_number": "1",
        "title": "Piloto", "container_extension": "mp4",
    }]
    guide = adapter.short_epg(**args, stream_id="12", limit=5)
    assert guide["programmes"] == [{
        "title": "Notícias da noite",
        "start_timestamp": 1830000000, "end_timestamp": 1830003600,
    }]
    assert "http" not in str(info) + str(guide)
    assert "secret" not in str(info) + str(guide)


def test_live_preview_skips_account_information_without_streaming(public_resolver):
    """The first Mega OTT category can be 'Account Information', not TV."""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params["action"]
        category_id = request.url.params.get("category_id")
        requests.append((action, category_id))
        assert request.url.path == "/player_api.php"
        if action == "get_live_categories":
            return httpx.Response(200, json=[
                {"category_id": "1", "category_name": "Account Information"},
                {"category_id": "2", "category_name": "Portugal"},
            ])
        assert action == "get_live_streams" and category_id == "2"
        return httpx.Response(200, json=[
            {"stream_id": 5, "name": "Account Information", "category_id": "2"},
            {"stream_id": 6, "name": "Canal Português", "category_id": "2"},
        ])

    adapter = XtreamCatalogClient(
        resolver=public_resolver,
        transport_factory=lambda _target: httpx.MockTransport(handler),
    )
    categories, items = adapter.preview_live(
        dns_link="http://stream.example.com", username="demo",
        password="test-only-password",
    )
    assert categories == [{"id": "2", "name": "Portugal"}]
    assert [item["title"] for item in items] == ["Canal Português"]
    assert requests == [("get_live_categories", None), ("get_live_streams", "2")]
    assert "test-only-password" not in str(categories) + str(items)


def test_live_preview_ignores_nonplayable_first_category(public_resolver):
    """When the info label is a channel, advance to the next TV category."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params["action"]
        category_id = request.url.params.get("category_id")
        calls.append((action, category_id))
        if action == "get_live_categories":
            return httpx.Response(200, json=[
                {"category_id": "1", "category_name": "Geral"},
                {"category_id": "2", "category_name": "Desporto"},
            ])
        return httpx.Response(200, json=(
            [{"stream_id": 10, "name": "Account Information", "category_id": "1"}]
            if category_id == "1"
            else [{"stream_id": 11, "name": "Desporto TV", "category_id": "2"}]
        ))

    adapter = XtreamCatalogClient(
        resolver=public_resolver,
        transport_factory=lambda _target: httpx.MockTransport(handler),
    )
    categories, items = adapter.preview_live(
        dns_link="http://stream.example.com", username="demo", password="test-only-password",
    )
    assert categories == [{"id": "2", "name": "Desporto"}]
    assert len(items) == 1 and items[0]["id"] == "11"
    assert calls == [
        ("get_live_categories", None), ("get_live_streams", "1"),
        ("get_live_streams", "2"),
    ]


def test_live_preview_bounds_metadata_requests(public_resolver):
    """An all-information lineup fails closed without querying every category."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params["action"]
        calls.append(action)
        if action == "get_live_categories":
            return httpx.Response(200, json=[
                {"category_id": str(i), "category_name": f"Categoria {i}"}
                for i in range(1, 25)
            ])
        return httpx.Response(200, json=[
            {"stream_id": 30, "name": "Account Information"}
        ])

    adapter = XtreamCatalogClient(
        resolver=public_resolver,
        transport_factory=lambda _target: httpx.MockTransport(handler),
    )
    assert adapter.preview_live(
        dns_link="http://stream.example.com", username="demo", password="test-only-password",
    ) == ([], [])
    assert calls == ["get_live_categories"] + ["get_live_streams"] * 6



def test_portugal_preview_uses_five_distinct_stations_not_duplicate_formats(public_resolver):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["action"] == "get_live_categories":
            return httpx.Response(200, json=[
                {"category_id": "97", "category_name": "EU | PORTUGAL GERAL"},
            ])
        assert request.url.params["category_id"] == "97"
        return httpx.Response(200, json=[
            {"stream_id": 1, "name": "PT| RTP 1 FHD", "category_id": "97"},
            {"stream_id": 2, "name": "PT| RTP 1 HD", "category_id": "97"},
            {"stream_id": 3, "name": "PT| RTP 2 FHD", "category_id": "97"},
            {"stream_id": 4, "name": "PT| SIC FHD", "category_id": "97"},
            {"stream_id": 5, "name": "PT| SIC HD", "category_id": "97"},
            {"stream_id": 6, "name": "PT| TVI FHD", "category_id": "97"},
            {"stream_id": 7, "name": "PT| RTP 3 FHD", "category_id": "97"},
        ])

    adapter = XtreamCatalogClient(
        resolver=public_resolver,
        transport_factory=lambda _target: httpx.MockTransport(handler),
    )
    category, channels = adapter.preview_live(
        dns_link="http://stream.example.com", username="demo",
        password="synthetic-only-secret",
    )
    assert category == [{"id": "97", "name": "EU | PORTUGAL GERAL"}]
    assert [item["id"] for item in channels] == ["1", "3", "4", "6", "7"]
    assert "synthetic-only-secret" not in str(channels)


def test_preview_prioritizes_portugal_geral_even_after_hundreds(public_resolver):
    """Live Portuguese TV is selected ahead of UFC PPV without broad scraping."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params["action"]
        category_id = request.url.params.get("category_id")
        calls.append((action, category_id))
        if action == "get_live_categories":
            return httpx.Response(200, json=[
                {"category_id": "1", "category_name": "PPV | UFC [EVENT ONLY]"},
                *[
                    {"category_id": str(n), "category_name": "EVENTO " + str(n)}
                    for n in range(2, 220) if n != 97
                ],
                {"category_id": "97", "category_name": "EU | PORTUGAL GERAL"},
            ])
        assert action == "get_live_streams"
        assert category_id == "97"
        return httpx.Response(200, json=[
            {"stream_id": 301, "name": "✦●✦ PORTUGAL HD ✦●✦", "category_id": "97"},
            {"stream_id": 302, "name": "PT| TVI HD", "category_id": "97"},
            {"stream_id": 303, "name": "PT| RTP 1 HD", "category_id": "97"},
            {"stream_id": 304, "name": "PT| SIC HD", "category_id": "97"},
            {"stream_id": 305, "name": "PPV UFC EVENT ONLY", "category_id": "97"},
            {"stream_id": 306, "name": "PT| RTP 2 HD", "category_id": "97"},
            {"stream_id": 307, "name": "PT| RTP 3 HD", "category_id": "97"},
            {"stream_id": 308, "name": "PT| CANAL LOCAL", "category_id": "97"},
        ])

    adapter = XtreamCatalogClient(
        resolver=public_resolver,
        transport_factory=lambda _target: httpx.MockTransport(handler),
    )
    categories, items = adapter.preview_live(
        dns_link="http://stream.example.com", username="demo",
        password="test-only-password",
    )
    assert categories == [{"id": "97", "name": "EU | PORTUGAL GERAL"}]
    assert [item["title"] for item in items] == [
        "PT| RTP 1 HD", "PT| RTP 2 HD", "PT| SIC HD",
        "PT| TVI HD", "PT| RTP 3 HD",
    ]
    assert calls == [("get_live_categories", None), ("get_live_streams", "97")]
    assert "test-only-password" not in str(categories) + str(items)


def test_preview_never_substitutes_ufc_events_for_portuguese_channels(public_resolver):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params["action"]
        category_id = request.url.params.get("category_id")
        calls.append((action, category_id))
        if action == "get_live_categories":
            return httpx.Response(200, json=[
                {"category_id": "1", "category_name": "PPV | UFC"},
                {"category_id": "97", "category_name": "EU | PORTUGAL GERAL"},
            ])
        if category_id == "97":
            return httpx.Response(200, json=[
                {"stream_id": 2, "name": "PT| RTP 1 HD", "category_id": "97"},
            ])
        raise AssertionError("UFC category must not be queried")

    adapter = XtreamCatalogClient(
        resolver=public_resolver,
        transport_factory=lambda _target: httpx.MockTransport(handler),
    )
    categories, items = adapter.preview_live(
        dns_link="http://stream.example.com", username="demo",
        password="test-only-password",
    )
    assert categories[0]["name"] == "EU | PORTUGAL GERAL"
    assert items[0]["title"] == "PT| RTP 1 HD"
    assert calls == [("get_live_categories", None), ("get_live_streams", "97")]




def test_category_paging_never_crosses_subscriber_entitlement(settings, session_factory):
    future = datetime.now(UTC) + timedelta(days=9)
    with session_factory() as db:
        for number, username in ((1, "sub-a"), (2, "sub-b")):
            db.add(SubscriptionMapping(
                mega_subscription_id=800 + number, username=username,
                dns_link="http://origin.example.com", dns_link_samsung_lg=None,
                expiring_at=future, last_synced_at=datetime.now(UTC),
            ))
        db.commit()
        ids = {name: db.query(SubscriptionMapping).filter_by(username=name).one().id
               for name in ("sub-a", "sub-b")}
    rows = {
        801: SimpleNamespace(
            id=801, username="sub-a", password=SecretStr("secret-a"),
            expiring_at=future, dns_link="http://origin.example.com",
            dns_link_for_samsung_lg=None,
        ),
        802: SimpleNamespace(
            id=802, username="sub-b", password=SecretStr("secret-b"),
            expiring_at=future, dns_link="http://origin.example.com",
            dns_link_for_samsung_lg=None,
        ),
    }
    mega, xtream, catalog = FakeMega(rows), FakeXtream(), FakeCatalog()
    app = create_app(settings)
    app.state.session_factory = session_factory
    app.state.mega_client_factory = lambda: mega
    app.state.xtream_client_factory = lambda: xtream
    app.state.catalog_client_factory = lambda: catalog
    client = TestClient(app)
    tokens = {name: issue_session_token(ids[name], settings)[0]
              for name in ("sub-a", "sub-b")}

    def post(user, route, body):
        return client.post(
            route, headers={"Authorization": "Bearer " + tokens[user]}, json=body,
        )

    for name, category_id, title in (("sub-a", "10", "Canal Azul"),
                                    ("sub-b", "20", "Canal Verde")):
        categories = post(name, "/v1/catalog/categories",
                          {"kind": "live", "platform": "android"})
        assert categories.status_code == 200
        assert categories.json()["categories"] == [
            {"id": category_id, "name": "Categoria particular"}]
        assert categories.json()["items"] == []
        items = post(name, "/v1/catalog/category-items",
                     {"kind": "live", "category_id": category_id, "platform": "android"})
        assert items.status_code == 200
        assert items.json()["categories"] == []
        assert items.json()["items"][0]["title"] == title
        assert items.headers["Cache-Control"] == "no-store"
        assert "secret-" not in items.text and "http" not in items.text
    before = len(catalog.calls)
    denied = post("sub-a", "/v1/catalog/category-items",
                  {"kind": "live", "category_id": "20"})
    assert denied.status_code == 401
    # A forbidden category is rejected after the owner's category list, no item call.
    assert len(catalog.calls) == before + 1
    assert post("sub-a", "/v1/catalog/category-items",
                {"kind": "live", "category_id": "../bad"}).status_code == 422
    assert post("sub-a", "/v1/catalog/category-items",
                {"kind": "live", "category_id": "10", "username": "sub-b"}).status_code == 422
    assert client.post("/v1/catalog/categories", json={"kind": "live"}).status_code == 401
    assert client.post("/v1/catalog/category-items", json={
        "kind": "live", "category_id": "10"}).status_code == 401
    assert post("sub-a", "/v1/catalog/categories", {
        "kind": "live", "platform": "invalid"}).status_code == 422
    rows[801].password = SecretStr("")
    assert post("sub-a", "/v1/catalog/categories", {"kind": "live"}).status_code == 503


def test_pinned_per_category_catalog_filter_and_sanitization(public_resolver):
    requests = []
    def handler(req: httpx.Request) -> httpx.Response:
        action = req.url.params["action"]
        category = req.url.params.get("category_id")
        requests.append((action, category))
        assert req.headers["Connection"] == "close"
        assert req.headers["Accept-Encoding"] == "identity"
        if action == "get_live_categories":
            return httpx.Response(200, json=[
                {"category_id": "10", "category_name": "Portugal"},
                {"category_id": "11", "category_name": "Desporto"},
            ])
        if action == "get_live_streams":
            assert category == "10"
            return httpx.Response(200, json=[
                {"stream_id": 42, "name": "Canal português", "category_id": "10",
                 "direct_source": "http://unsafe.example/hidden", "password": "secret"},
                {"stream_id": 43, "name": "Estrangeiro", "category_id": "11"},
            ])
        raise AssertionError("The backend fetched an unbounded global listing")

    adapter = XtreamCatalogClient(
        resolver=public_resolver,
        transport_factory=lambda _target: httpx.MockTransport(handler),
    )
    kwargs = {"dns_link": "http://stream.example.com",
              "username": "synthetic", "password": "synthetic-only"}
    cats = adapter.list_categories(**kwargs, kind=CatalogKind.LIVE)
    assert cats == [{"id": "10", "name": "Portugal"},
                    {"id": "11", "name": "Desporto"}]
    selected = adapter.list_category_items(
        **kwargs, kind=CatalogKind.LIVE, category_id="10",
    )
    assert selected == [{
        "id": "42", "title": "Canal português",
        "category_id": "10", "container_extension": None,
    }]
    assert requests == [("get_live_categories", None),
                        ("get_live_streams", "10")]
    assert "password" not in str(selected) and "http" not in str(selected)
    try:
        adapter.list_category_items(
            **kwargs, kind=CatalogKind.LIVE, category_id="../other",
        )
    except CatalogUnavailable:
        pass
    else:
        raise AssertionError("Unsafe category identifier reached the provider")
