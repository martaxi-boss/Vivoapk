"""Synthetic finite media admission and random-access delivery, no provider IO."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from app.models import SubscriptionMapping
from app.schemas import CatalogKind, EntitlementState
from app.security import issue_session_token
from app.services.media_body import MediaReadError, open_media_body
from app.services.media_origin import AuthorizedMediaOrigin, MediaAsset
from app.services.private_file import MAX_WINDOW_BYTES, PrivateFileSession
from app.services.private_hls import PrivateHlsSession
from test_catalog_privacy import FakeMega, FakeXtream
from test_media_origin import FakeResponse, MediaCatalogue, public_dns
from test_private_hls import ORIGIN, TS, Origin, media, response

KEY = "fixture-private-media-key-for-file-contract-only"
FILE = "/_internal/media/file/"
HLS = "/_internal/media/hls/"
MOVIE = b"synthetic movie file 0123456789"
EPISODE = b"synthetic episode file 9876543210"


class RangePool:
    def __init__(self, files):
        self.files = files
        self.calls = []
        self.pools = []
        self.bad = None

    def factory(self, target):
        assert target.hostname == "media.example.com"
        assert target.addresses == ("93.184.216.34",)
        pool = self.Connection(self)
        self.pools.append(pool)
        return pool

    class Connection:
        def __init__(self, origin):
            self.origin = origin
            self.closed = False
            self.response = None

        def close(self):
            self.closed = True

        def stream(self, method, url, **kwargs):
            from contextlib import contextmanager

            @contextmanager
            def opened():
                assert method == "GET" and url in self.origin.files
                headers = dict(kwargs["headers"])
                start, end = map(int, headers[b"Range"].decode().removeprefix("bytes=").split("-"))
                self.origin.calls.append((url, start, end, headers.get(b"If-Match")))
                data = self.origin.files[url]
                total = len(data)
                body = data[start:end + 1]
                mime = b"video/mp4"
                tag = b'"fixture-v1"'
                status = 206
                range_value = f"bytes {start}-{end}/{total}".encode()
                if self.origin.bad == "wrong_total":
                    range_value = f"bytes {start}-{end}/{total + 1}".encode()
                if self.origin.bad == "short_range":
                    body = body[:-1]
                    range_value = f"bytes {start}-{end - 1}/{total}".encode()
                if self.origin.bad == "changed_tag":
                    tag = b'"fixture-v2"'
                if self.origin.bad == "ignored_range":
                    status = 200
                if self.origin.bad == "redirect":
                    status = 302
                if self.origin.bad == "missing_tag":
                    tag = None
                headers = [(b"Content-Type", mime), (b"Content-Length", str(len(body)).encode()),
                           (b"Content-Range", range_value)]
                if tag is not None:
                    headers.append((b"ETag", tag))
                self.response = FakeResponse(status, headers, [body])
                try:
                    yield self.response
                finally:
                    self.response.closed = True
            return opened()


@pytest.fixture
def finite(settings, session_factory):
    configured = Settings(**{**settings.model_dump(), "private_hls_enabled": True,
                             "private_media_key": KEY})
    future = datetime.now(UTC) + timedelta(days=10)
    rows, tokens = {}, {}
    with session_factory() as db:
        for i, suffix in enumerate(("a", "b"), 1):
            mapping = SubscriptionMapping(mega_subscription_id=900+i, username=f"sub-{suffix}",
                dns_link=ORIGIN, dns_link_samsung_lg=ORIGIN, expiring_at=future,
                last_synced_at=datetime.now(UTC))
            db.add(mapping)
            db.flush()
            tokens[suffix], _ = issue_session_token(mapping.id, configured)
            rows[900+i] = SimpleNamespace(id=900+i, username=mapping.username,
                password=SecretStr(f"fixture-password-{suffix}"), expiring_at=future,
                dns_link=ORIGIN, dns_link_for_samsung_lg=ORIGIN)
        db.commit()
    xtream = FakeXtream(rows)
    app = create_app(configured)
    app.state.session_factory = session_factory
    app.state.mega_client_factory = lambda: FakeMega(rows)
    app.state.xtream_client_factory = lambda: xtream
    app.state.catalog_client_factory = MediaCatalogue
    origin = RangePool({
        ORIGIN + "/movie/sub-a/fixture-password-a/501.mp4": MOVIE,
        ORIGIN + "/series/sub-b/fixture-password-b/90.mp4": EPISODE,
    })
    upstream = Origin({
        ORIGIN + "/live/sub-b/fixture-password-b/101.m3u8": lambda: response(media()),
        ORIGIN + "/muxed/s10.ts?private=fixture-secret": lambda: response(TS, b"video/mp2t"),
    })
    def reader(source, **kwargs):
        return open_media_body(source, resolver=public_dns, pool_factory=origin.factory, **kwargs)
    app.state.private_file_factory = lambda source: PrivateFileSession(source, body_reader=reader)
    app.state.private_hls_factory = lambda source: PrivateHlsSession(source,
        entitlement=lambda: EntitlementState.UNAVAILABLE, resolver=public_dns,
        pool_factory=upstream.factory)
    with TestClient(app) as client:
        def post(route, payload, token=None, key=KEY, suffix="a"):
            return client.post(route, json=payload, headers={"X-Vivoapk-Media-Key": key,
                "Authorization": "Bearer " + (tokens[suffix] if token is None else token)})
        yield client, post, origin, xtream, rows


def movie_request():
    return {"kind": "vod", "category_id": "10", "asset_id": "501", "platform": "ios"}


def episode_request():
    return {"kind": "series", "category_id": "20", "asset_id": "90",
            "series_id": "401", "platform": "webos"}


def open_movie(post):
    result = post(FILE + "open", movie_request())
    assert result.status_code == 200
    return result.json()


def read(post, opened, start, length, **kwargs):
    return post(FILE + "read", {"session_id": opened["session_id"],
        "start": start, "length": length}, token=opened["media_token"], **kwargs)


def close(post, opened):
    return post(FILE + "close", {"session_id": opened["session_id"]}, token=opened["media_token"])


def test_file_routes_disabled_without_private_media_flag(settings):
    with TestClient(create_app(settings)) as client:
        assert client.post(FILE + "open", json=movie_request()).status_code == 404


def test_movies_episodes_seek_and_shared_capacity(finite):
    client, post, origin, _, _ = finite
    assert post(FILE + "open", movie_request(), key="wrong").status_code == 403
    assert post(FILE + "open", episode_request()).status_code == 404
    assert post(FILE + "open", {**movie_request(), "series_id": "301"}).status_code == 422
    assert post(FILE + "open", {**movie_request(), "url": "https://untrusted.example"}).status_code == 422
    assert origin.calls == []
    a = open_movie(post)
    b_response = post(FILE + "open", episode_request(), suffix="b")
    assert b_response.status_code == 200
    b = b_response.json()
    assert a["length"] == len(MOVIE) and b["length"] == len(EPISODE)
    assert a["content_type"] == b["content_type"] == "video/mp4"
    assert "fixture-password" not in str(a) and "media.example.com" not in str(b)
    assert a["media_token"] != b["media_token"]
    # Both used exactly a one-byte range; never downloaded the entire file.
    assert [(x[1], x[2]) for x in origin.calls] == [(0, 0), (0, 0)]
    assert post(HLS + "open", {"category_id": "20", "asset_id": "101",
                             "platform": "tizen"}, suffix="b").status_code == 429
    before = len(origin.calls)
    assert post(FILE + "read", {"session_id": a["session_id"], "start": 0,
        "length": 1}, token=b["media_token"]).status_code == 404
    assert post(HLS + "playlist", {"session_id": a["session_id"]},
                token=a["media_token"]).status_code == 404
    assert post(HLS + "close", {"session_id": a["session_id"]},
                token=a["media_token"]).status_code == 404
    assert len(origin.calls) == before
    for opened, data in ((a, MOVIE), (b, EPISODE)):
        for start, size in ((8, 5), (0, 3), (len(data)-2, 16)):
            result = read(post, opened, start, size)
            assert result.status_code == 206
            end = min(start+size, len(data))-1
            assert result.content == data[start:end+1]
            assert result.headers["content-range"] == f"bytes {start}-{end}/{len(data)}"
            assert result.headers["cache-control"] == "no-store"
            assert "etag" not in result.headers
    assert all(call[3] == b'"fixture-v1"' for call in origin.calls[2:])
    assert all(pool.closed and pool.response.closed for pool in origin.pools)
    assert close(post, a).status_code == 204
    assert read(post, a, 0, 1).status_code == 404
    assert read(post, b, 0, 2).content == EPISODE[:2]
    assert close(post, b).status_code == 204
    assert not client.app.state.private_hls_registry._entries


def test_live_and_file_share_registry_and_grant_types(finite):
    client, post, _, _, _ = finite
    live = post(HLS + "open", {"category_id": "20", "asset_id": "101",
                               "platform": "tizen"}, suffix="b").json()
    a = open_movie(post)
    assert len(client.app.state.private_hls_registry._entries) == 2
    assert post(FILE + "open", episode_request(), suffix="b").status_code == 429
    assert post(FILE + "renew", {"session_id": live["session_id"]},
                token=live["media_token"]).status_code == 404
    assert post(HLS + "playlist", {"session_id": live["session_id"]},
                token=live["media_token"]).status_code == 200
    assert read(post, a, 2, 4).content == MOVIE[2:6]
    assert close(post, a).status_code == 204
    assert post(HLS + "close", {"session_id": live["session_id"]},
                token=live["media_token"]).status_code == 204


@pytest.mark.parametrize("bad", ["wrong_total", "short_range", "changed_tag",
                                 "ignored_range", "redirect", "missing_tag"])
def test_bad_provider_range_closes_grant_without_leak(finite, bad):
    client, post, origin, _, _ = finite
    a = open_movie(post)
    origin.bad = bad
    result = read(post, a, 2, 4)
    assert result.status_code == 404 and "fixture-password" not in result.text
    assert not client.app.state.private_hls_registry._entries
    assert all(pool.closed and pool.response.closed for pool in origin.pools)


def test_renew_revocation_and_credential_change(finite):
    client, post, origin, xtream, rows = finite
    a = open_movie(post)
    old = a["media_token"]
    result = post(FILE + "renew", {"session_id": a["session_id"]}, token=old)
    assert result.status_code == 200
    a["media_token"] = result.json()["media_token"]
    assert old != a["media_token"]
    assert post(FILE + "read", {"session_id": a["session_id"], "start": 0,
                               "length": 1}, token=old).status_code == 404
    before = len(origin.calls)
    xtream.status = "DISABLED"
    assert read(post, a, 0, 1).status_code == 404
    assert len(origin.calls) == before
    xtream.status = "SUCCESS"
    a = open_movie(post)
    before = len(origin.calls)
    rows[901].password = SecretStr("changed-fixture-password")
    assert read(post, a, 0, 1).status_code == 404
    assert len(origin.calls) == before
    assert not client.app.state.private_hls_registry._entries


def test_bounds_and_no_access_after_close(finite):
    _, post, origin, _, _ = finite
    a = open_movie(post)
    before = len(origin.calls)
    for start, size in ((-1, 1), (0, 0), (0, MAX_WINDOW_BYTES+1), (True, 1), (1, "2")):
        assert read(post, a, start, size).status_code == 422
    assert len(origin.calls) == before
    assert read(post, a, 0, 1).content == MOVIE[:1]
    assert close(post, a).status_code == 204


def test_optional_validator_and_post_read_revocation():
    source = AuthorizedMediaOrigin(MediaAsset(CatalogKind.VOD, "10", "501"),
        ORIGIN, "sub-a", SecretStr("fixture-password-a"), "mp4")
    origin = RangePool({source._request_url(): MOVIE})
    origin.bad = "missing_tag"
    player = PrivateFileSession(source, body_reader=lambda src, **kwargs:
        open_media_body(src, resolver=public_dns, pool_factory=origin.factory, **kwargs))
    active = lambda: EntitlementState.ACTIVE
    assert player.probe(entitlement=active).length == len(MOVIE)
    assert player.read(3, 4, entitlement=active).data == MOVIE[3:7]
    assert all(call[3] is None for call in origin.calls)
    states = iter((EntitlementState.ACTIVE, EntitlementState.DISABLED))
    with pytest.raises(MediaReadError):
        player.read(0, 4, entitlement=lambda: next(states))
    assert all(p.closed and p.response.closed for p in origin.pools)
    player.close()
    with pytest.raises(MediaReadError):
        player.read(0, 1, entitlement=active)

