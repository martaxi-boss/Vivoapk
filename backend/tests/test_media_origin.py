"""Synthetic media authorization and streaming contract; no provider network."""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpcore
import pytest
from pydantic import SecretStr

from app.models import SubscriptionMapping
from app.schemas import CatalogKind, ClientPlatform
from app.security import issue_session_token
from app.services.catalog import CatalogDenied, CatalogService
from app.services.media_body import MediaReadError, open_media_body
from app.services.media_origin import AuthorizedMediaOrigin, MediaAsset
from test_catalog_privacy import FakeCatalog, FakeMega, FakeXtream


class MediaCatalogue(FakeCatalog):
    def list_category_items(self, **kwargs):
        rows = super().list_category_items(**kwargs)
        if kwargs["kind"] is CatalogKind.VOD:
            rows[0]["id"] = "501"
            rows[0]["container_extension"] = "mp4"
        return rows


@pytest.fixture
def accounts(settings, session_factory):
    future = datetime.now(UTC) + timedelta(days=10)
    rows = {}
    tokens = {}
    with session_factory() as db:
        for index, suffix in enumerate(("a", "b"), 1):
            mapping = SubscriptionMapping(
                mega_subscription_id=900 + index, username=f"sub-{suffix}",
                dns_link=f"https://origin-{index}.example.com",
                dns_link_samsung_lg=f"https://tv-{index}.example.com",
                expiring_at=future, last_synced_at=datetime.now(UTC),
            )
            db.add(mapping)
            db.flush()
            tokens[suffix], _ = issue_session_token(mapping.id, settings)
            rows[900 + index] = SimpleNamespace(
                id=900 + index, username=mapping.username,
                password=SecretStr(f"fixture-secret-{suffix}"),
                expiring_at=future, dns_link=mapping.dns_link,
                dns_link_for_samsung_lg=mapping.dns_link_samsung_lg,
            )
        db.commit()
    xtream = FakeXtream()
    with session_factory() as db:
        service = CatalogService(
            db, settings, mega_client_factory=lambda: FakeMega(rows),
            xtream_client_factory=lambda: xtream, catalog_client_factory=MediaCatalogue,
        )
        yield service, tokens, xtream


def test_media_origins_are_authoritative_and_account_scoped(accounts):
    svc, tokens, _ = accounts
    a = svc.authorize_media_origin(
        token=tokens["a"], asset=MediaAsset(CatalogKind.LIVE, "10", "101"),
        platform=ClientPlatform.ANDROID,
    )
    b = svc.authorize_media_origin(
        token=tokens["b"], asset=MediaAsset(CatalogKind.LIVE, "20", "101"),
        platform=ClientPlatform.TIZEN,
    )
    assert a.extension == b.extension == "m3u8"
    assert a.origin == "https://origin-1.example.com"
    assert b.origin == "https://tv-2.example.com"
    assert a._request_url().endswith("/live/sub-a/fixture-secret-a/101.m3u8")
    assert b._request_url().endswith("/live/sub-b/fixture-secret-b/101.m3u8")
    assert "fixture-secret" not in repr(a) and "origin-1" not in repr(a)
    with pytest.raises(CatalogDenied):
        svc.authorize_media_origin(
            token=tokens["a"], asset=MediaAsset(CatalogKind.LIVE, "20", "101"),
            platform=ClientPlatform.ANDROID,
        )


def test_media_membership_and_expiration(accounts):
    svc, tokens, xtream = accounts
    movie = svc.authorize_media_origin(
        token=tokens["a"], asset=MediaAsset(CatalogKind.VOD, "10", "501"),
        platform=ClientPlatform.IOS,
    )
    assert movie.extension == "mp4"
    episode = svc.authorize_media_origin(
        token=tokens["a"], asset=MediaAsset(CatalogKind.SERIES, "20", "90", "301"),
        platform=ClientPlatform.WINDOWS,
    )
    assert episode._request_url().endswith("/series/sub-a/fixture-secret-a/90.mp4")
    with pytest.raises(CatalogDenied):
        svc.authorize_media_origin(
            token=tokens["a"], asset=MediaAsset(CatalogKind.SERIES, "20", "90", "401"),
            platform=ClientPlatform.WINDOWS,
        )
    xtream.status = "DISABLED"
    with pytest.raises(CatalogDenied):
        svc.authorize_media_origin(
            token=tokens["a"], asset=MediaAsset(CatalogKind.VOD, "10", "501"),
            platform=ClientPlatform.IOS,
        )


@pytest.mark.parametrize("value", ["../1", "1/2", "x", "", "1%2f2"])
def test_asset_rejects_path_input(value):
    with pytest.raises(ValueError, match="identity"):
        MediaAsset(CatalogKind.VOD, "10", value)


@pytest.fixture
def source():
    return AuthorizedMediaOrigin(
        MediaAsset(CatalogKind.VOD, "10", "501"), "https://media.example.com",
        "fixture-user", SecretStr("fixture-password"), "mp4",
    )


class FakeResponse:
    def __init__(self, status=200, headers=None, blocks=None):
        self.status = status
        self.headers = headers if headers is not None else [
            (b"Content-Type", b"video/mp4"), (b"Content-Length", b"6")
        ]
        self.blocks = [b"abc", b"def"] if blocks is None else blocks
        self.reads = 0
        self.closed = False

    def iter_stream(self):
        for block in self.blocks:
            self.reads += 1
            if isinstance(block, Exception):
                raise block
            yield block


class FakePool:
    def __init__(self, response):
        self.response = response
        self.closed = False
        self.calls = []

    @contextmanager
    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        try:
            yield self.response
        finally:
            self.response.closed = True

    def close(self):
        self.closed = True


def public_dns(_host, _port):
    return ["93.184.216.34"]


def test_lazy_body_and_close_on_partial_read(source):
    response = FakeResponse()
    pool = FakePool(response)
    def factory(target):
        assert target.addresses == ("93.184.216.34",)
        assert target.hostname == "media.example.com"
        return pool
    with open_media_body(source, resolver=public_dns, pool_factory=factory) as body:
        assert body.content_type == "video/mp4"
        assert response.reads == 0
        assert next(body.chunks) == b"abc"
        assert response.reads == 1
        assert b"fixture-password" not in repr(body).encode()
    assert response.closed and pool.closed


def test_valid_range(source):
    response = FakeResponse(206, [
        (b"Content-Type", b"video/mp4"), (b"Content-Length", b"6"),
        (b"Content-Range", b"bytes 8-13/100"),
    ])
    pool = FakePool(response)
    with open_media_body(source, range_header="bytes=8-13", resolver=public_dns,
                         pool_factory=lambda _: pool) as body:
        assert b"".join(body.chunks) == b"abcdef"
        assert body.content_range == "bytes 8-13/100"
    assert (b"Range", b"bytes=8-13") in pool.calls[0][2]["headers"]
    assert pool.closed and response.closed


@pytest.mark.parametrize("response,range_header", [
    (FakeResponse(302, [(b"Location", b"https://other.example/private")]), None),
    (FakeResponse(200, [(b"Content-Type", b"application/vnd.apple.mpegurl")]), None),
    (FakeResponse(200, [(b"Content-Type", b"video/mp4"), (b"Content-Encoding", b"gzip")]), None),
    (FakeResponse(200), "bytes=8-13"),
    (FakeResponse(206, [(b"Content-Type", b"video/mp4"), (b"Content-Length", b"6"),
                        (b"Content-Range", b"bytes 0-5/100")]), "bytes=8-13"),
])
def test_untrusted_response_fails_before_body(source, response, range_header):
    pool = FakePool(response)
    with pytest.raises(MediaReadError):
        with open_media_body(source, range_header=range_header,
                             resolver=public_dns, pool_factory=lambda _: pool):
            pytest.fail("untrusted response was accepted")
    assert response.reads == 0 and response.closed and pool.closed
    assert len(pool.calls) == 1


def test_bounds_and_sanitized_upstream_error(source):
    response = FakeResponse(headers=[(b"Content-Type", b"video/mp4")],
                            blocks=[b"abc", b"def"])
    pool = FakePool(response)
    with pytest.raises(MediaReadError, match="bound"):
        with open_media_body(source, max_bytes=4, resolver=public_dns,
                             pool_factory=lambda _: pool) as body:
            list(body.chunks)
    assert pool.closed and response.closed
    response = FakeResponse(blocks=[httpcore.ReadError("fixture-password")])
    pool = FakePool(response)
    with pytest.raises(MediaReadError) as error:
        with open_media_body(source, resolver=public_dns, pool_factory=lambda _: pool) as body:
            list(body.chunks)
    assert "fixture-password" not in str(error.value)
    assert pool.closed and response.closed


def test_refuse_private_dns_plaintext_and_manifest_before_connect(source):
    def forbidden(_target):
        pytest.fail("outbound connection was attempted")
    with pytest.raises(MediaReadError):
        with open_media_body(source, resolver=lambda *_: ["127.0.0.1"],
                             pool_factory=forbidden):
            pass
    for candidate in (
        AuthorizedMediaOrigin(source.asset, "http://media.example.com",
                              source.username, source.password, "mp4"),
        AuthorizedMediaOrigin(MediaAsset(CatalogKind.LIVE, "10", "101"),
                              source.origin, source.username, source.password, "m3u8"),
    ):
        with pytest.raises(MediaReadError):
            with open_media_body(candidate, resolver=public_dns, pool_factory=forbidden):
                pass
