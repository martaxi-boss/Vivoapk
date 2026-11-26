"""Provider-issued source selection: synthetic metadata, never external media."""
from contextlib import contextmanager
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import SecretStr

from app.schemas import CatalogKind
from app.services.media_body import MediaReadError
from app.services.media_origin import AuthorizedMediaOrigin, MediaAsset
from app.services.provider_live import lookup_provider_ts_source


@pytest.fixture
def source():
    return AuthorizedMediaOrigin(MediaAsset(CatalogKind.LIVE, "10", "101"),
                                 "https://assigned.example.com", "owner",
                                 SecretStr("fixture-secret"), "ts")


class Response:
    def __init__(self, body, status=200):
        self.status = status
        self.headers = []
        self.body = body
        self.reads = 0

    def iter_stream(self):
        self.reads += 1
        for offset in range(0, len(self.body), 13):
            yield self.body[offset:offset + 13]


class Pool:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False

    @contextmanager
    def stream(self, method, url, **kwargs):
        self.requests.append((method, url))
        yield self.response

    def close(self):
        self.closed = True


def lookup(source, response):
    pool = Pool(response)
    result = lookup_provider_ts_source(
        source, resolver=lambda *_: ["93.184.216.34"], pool_factory=lambda _: pool,
    )
    return result, pool


@pytest.mark.parametrize("tail", ["\n", ""])
def test_uses_exact_advertised_same_origin_default_port_without_opening_media(source, tail):
    url = "https://assigned.example.com:443/owner/fixture-secret/101"
    result, pool = lookup(source, Response(("#EXTM3U\n#EXTINF:-1,RTP1\n" + url + tail).encode()))
    assert result._request_url() == url
    assert result.origin == source.origin  # entitlement/DNS selection is unchanged
    assert source._request_url().endswith("/live/owner/fixture-secret/101.ts")
    assert len(pool.requests) == 1 and pool.closed
    method, request = pool.requests[0]
    assert method == "GET" and urlsplit(request).path == "/get.php"
    assert parse_qs(urlsplit(request).query) == {
        "username": ["owner"], "password": ["fixture-secret"],
        "type": ["m3u_plus"], "output": ["ts"],
    }
    assert "fixture-secret" not in repr(result)


@pytest.mark.parametrize("url", [
    "https://other.example.com/owner/fixture-secret/101",
    "http://assigned.example.com/owner/fixture-secret/101",
    "https://assigned.example.com:444/owner/fixture-secret/101",
    "https://assigned.example.com:0/owner/fixture-secret/101",
    "https://assigned.example.com/foreign/fixture-secret/101",
    "https://assigned.example.com/owner/wrong-secret/101",
    "https://assigned.example.com/owner/fixture-secret/102",
    "https://assigned.example.com/live/owner/fixture-secret/101.m3u8",
    "https://assigned.example.com/owner/fixture-secret/101?token=unexpected",
    "https://assigned.example.com/owner/fixture-secret/101#fragment",
    "https://assigned.example.com@127.0.0.1/owner/fixture-secret/101",
    "https://assigned.example.com/owner/fixture-secret/../101",
])
def test_locator_cannot_change_authorized_origin_identity_or_asset(source, url):
    with pytest.raises(ValueError):
        source.with_provider_live_url(url)


@pytest.mark.parametrize("status", [301, 302, 401, 403, 500])
def test_metadata_denial_closes_without_reading_body_or_retry(source, status):
    response = Response(b"must not read", status)
    pool = Pool(response)
    with pytest.raises(MediaReadError):
        lookup_provider_ts_source(source, resolver=lambda *_: ["93.184.216.34"],
                                  pool_factory=lambda _: pool)
    assert response.reads == 0 and len(pool.requests) == 1 and pool.closed


def test_matching_item_cannot_jump_to_an_unassigned_host(source):
    pool = Pool(Response(b"#EXTM3U\nhttps://other.example.com/owner/fixture-secret/101\n"))
    with pytest.raises(MediaReadError):
        lookup_provider_ts_source(source, resolver=lambda *_: ["93.184.216.34"],
                                  pool_factory=lambda _: pool)
    assert len(pool.requests) == 1 and pool.closed


def test_unknown_or_missing_item_has_no_constructed_legacy_fallback(source):
    for body in (b"<html>not a playlist</html>\n", b"#EXTM3U\n#EXTINF:-1,Other\n",
                 b"#EXTM3U\nhttps://assigned.example.com/owner/fixture-secret/102\n"):
        with pytest.raises(MediaReadError):
            lookup(source, Response(body))
    with pytest.raises(MediaReadError):
        lookup_provider_ts_source(replace(source, extension="m3u8"))


def test_http_still_requires_existing_explicit_transport_policy(source):
    source = replace(source, origin="http://assigned.example.com")
    with pytest.raises(MediaReadError):
        lookup_provider_ts_source(source)
