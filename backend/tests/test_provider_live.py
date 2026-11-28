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



def test_private_locator_cache_reuses_other_channel_metadata_not_authorization(source):
    from app.services.provider_live import ProviderLiveLocatorCache
    cache = ProviderLiveLocatorCache()
    pool = Pool(Response(b"#EXTM3U\nhttps://assigned.example.com/owner/fixture-secret/101\n"
                         b"https://assigned.example.com/owner/fixture-secret/102\n"))
    kwargs = dict(resolver=lambda *_: ["93.184.216.34"], pool_factory=lambda _: pool,
                  locator_cache=cache)
    assert lookup_provider_ts_source(source, **kwargs)._request_url().endswith("/101")
    second = replace(source, asset=MediaAsset(CatalogKind.LIVE, "10", "102"))
    assert lookup_provider_ts_source(second, **kwargs)._request_url().endswith("/102")
    assert len(pool.requests) == 1  # Only one provider-issued playlist fetch.
    foreign = replace(second, username="foreign")
    with pytest.raises(MediaReadError):
        lookup_provider_ts_source(foreign, **kwargs)
    assert len(pool.requests) == 2


def test_private_locator_cache_expiry_has_no_stale_fallback_on_denial(source):
    from app.services.provider_live import ProviderLiveLocatorCache
    now = [0.0]
    cache = ProviderLiveLocatorCache(ttl_seconds=5, clock=lambda: now[0])
    pool = Pool(Response(b"#EXTM3U\nhttps://assigned.example.com/owner/fixture-secret/101\n"))
    kwargs = dict(resolver=lambda *_: ["93.184.216.34"], pool_factory=lambda _: pool,
                  locator_cache=cache)
    lookup_provider_ts_source(source, **kwargs)
    now[0] = 6
    denied = Response(b"private denied body", status=403)
    pool.response = denied
    with pytest.raises(MediaReadError):
        lookup_provider_ts_source(source, **kwargs)
    assert denied.reads == 0 and len(pool.requests) == 2


def test_cached_locator_still_rejects_foreign_origin_and_changed_credentials(source):
    from app.services.provider_live import ProviderLiveLocatorCache
    cache = ProviderLiveLocatorCache()
    pool = Pool(Response(b"#EXTM3U\nhttps://assigned.example.com/owner/fixture-secret/101\n"
                         b"https://foreign.example.com/owner/fixture-secret/102\n"))
    kwargs = dict(resolver=lambda *_: ["93.184.216.34"], pool_factory=lambda _: pool,
                  locator_cache=cache)
    lookup_provider_ts_source(source, **kwargs)
    other = replace(source, asset=MediaAsset(CatalogKind.LIVE, "10", "102"))
    with pytest.raises(MediaReadError):
        lookup_provider_ts_source(other, **kwargs)
    changed = replace(source, password=SecretStr("changed-secret"))
    with pytest.raises(MediaReadError):
        lookup_provider_ts_source(changed, **kwargs)
    assert len(pool.requests) == 2  # Foreign cached asset cannot open any media.


def test_cached_locator_requires_explicit_http_policy_on_every_hit(source):
    from app.services.provider_live import ProviderLiveLocatorCache
    cache = ProviderLiveLocatorCache()
    source = replace(source, origin="http://assigned.example.com")
    pool = Pool(Response(b"#EXTM3U\nhttp://assigned.example.com/owner/fixture-secret/101\n"))
    kwargs = dict(resolver=lambda *_: ["93.184.216.34"], pool_factory=lambda _: pool,
                  locator_cache=cache)
    lookup_provider_ts_source(source, allow_authorized_http=True, **kwargs)
    with pytest.raises(MediaReadError):
        lookup_provider_ts_source(source, **kwargs)
    assert len(pool.requests) == 1
