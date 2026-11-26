"""Synthetic-only tests of the explicit authorized upstream HTTP compatibility policy."""
import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.schemas import CatalogKind, EntitlementState
from app.services.media_body import MediaReadError, open_media_body
from app.services.media_origin import AuthorizedMediaOrigin, MediaAsset
from app.services.private_hls import PrivateHlsSession
from test_media_origin import FakePool, FakeResponse, public_dns
from test_private_hls import Origin, TS, handles, media, response

HTTP = "http://media.example.com:8080"
ROOT = HTTP + "/live/fixture-user/fixture-password/101.m3u8"


def _source(kind=CatalogKind.LIVE):
    return AuthorizedMediaOrigin(
        MediaAsset(kind, "10", "101"), HTTP,
        "fixture-user", SecretStr("fixture-password"),
        "m3u8" if kind is CatalogKind.LIVE else "mp4",
    )


def _factory(upstream):
    def factory(target):
        assert target.origin == HTTP
        assert target.port == 8080
        return upstream.factory(target)
    return factory


def _hls(upstream, *, allowed=False, state=None, resolver=public_dns):
    state = [EntitlementState.ACTIVE] if state is None else state
    return PrivateHlsSession(
        _source(), entitlement=lambda: state[0], allow_authorized_http=allowed,
        resolver=resolver, pool_factory=_factory(upstream),
    )


def test_http_policy_is_off_by_default_and_private_only(settings):
    assert settings.private_media_allow_authorized_http is False
    with pytest.raises(ValidationError):
        Settings(**{**settings.model_dump(), "private_hls_enabled": False,
                    "private_media_allow_authorized_http": True})


def test_http_hls_explicit_opt_in_and_revocation():
    upstream = Origin({
        ROOT: lambda: response(media()),
        HTTP + "/muxed/s10.ts?private=fixture-secret": lambda: response(TS, b"video/mp2t"),
    })
    with pytest.raises(MediaReadError):
        _hls(upstream)
    assert upstream.calls == []
    state = [EntitlementState.ACTIVE]
    player = _hls(upstream, allowed=True, state=state)
    playlist = player.refresh()
    assert "http" not in playlist and "fixture" not in playlist
    assert player.read_segment(handles(playlist)[0]) == TS
    assert len(upstream.calls) == 2
    state[0] = EntitlementState.DISABLED
    with pytest.raises(MediaReadError):
        player.read_segment(handles(playlist)[0])
    assert len(upstream.calls) == 2
    assert all(p.closed and p.response.closed for p in upstream.pools)


@pytest.mark.parametrize("uri", [
    "https://media.example.com:8080/muxed/s10.ts",
    "http://media.example.com:8081/muxed/s10.ts",
    "http://other.example.com:8080/muxed/s10.ts",
    "http://127.0.0.1:8080/muxed/s10.ts",
    "//media.example.com:8080/muxed/s10.ts",
])
def test_http_hls_rejects_cross_origin_and_scheme_changes(uri):
    manifest = media(count=1).replace(b"/muxed/s10.ts?private=fixture-secret", uri.encode())
    upstream = Origin({ROOT: lambda: response(manifest)})
    with pytest.raises(MediaReadError):
        _hls(upstream, allowed=True).refresh()
    assert upstream.calls == [ROOT]


def test_http_hls_rejects_redirect_and_private_dns():
    upstream = Origin({ROOT: lambda: response(b"", status=302, headers=[
        (b"Location", b"http://other.example.com/private"),
    ])})
    with pytest.raises(MediaReadError):
        _hls(upstream, allowed=True, resolver=lambda *_: ["127.0.0.1"]).refresh()
    assert upstream.calls == []
    with pytest.raises(MediaReadError):
        _hls(upstream, allowed=True).refresh()
    assert upstream.calls == [ROOT]


def test_http_file_exact_range_opt_in_and_pinning():
    source = _source(CatalogKind.VOD)
    pool = FakePool(FakeResponse(206, [
        (b"Content-Type", b"video/mp4"), (b"Content-Length", b"6"),
        (b"Content-Range", b"bytes 8-13/100"),
    ]))

    def factory(target):
        assert target.origin == HTTP and target.port == 8080
        assert target.addresses == ("93.184.216.34",)
        return pool

    with pytest.raises(MediaReadError):
        with open_media_body(source, resolver=public_dns, pool_factory=factory):
            pass
    assert pool.calls == []
    with open_media_body(source, range_header="bytes=8-13", allow_authorized_http=True,
                         resolver=public_dns, pool_factory=factory) as body:
        assert b"".join(body.chunks) == b"abcdef"
    assert len(pool.calls) == 1 and pool.closed
    assert pool.calls[0][1] == source._request_url()


def test_http_file_rejects_redirect_and_private_dns():
    source = _source(CatalogKind.VOD)
    pool = FakePool(FakeResponse(302, [(b"Location", b"http://other.example.com/private")]))
    with pytest.raises(MediaReadError):
        with open_media_body(source, allow_authorized_http=True,
                             resolver=lambda *_: ["127.0.0.1"], pool_factory=lambda _: pool):
            pass
    assert pool.calls == []
    with pytest.raises(MediaReadError):
        with open_media_body(source, allow_authorized_http=True,
                             resolver=public_dns, pool_factory=lambda _: pool):
            pass
    assert len(pool.calls) == 1 and pool.closed
    assert pool.response.reads == 0


@pytest.mark.parametrize("allowed", [False, True])
def test_private_route_factories_use_only_server_policy(settings, allowed):
    from fastapi import FastAPI
    from app.private_media_api import install_private_hls_routes

    configured = Settings(**{
        **settings.model_dump(), "private_hls_enabled": True,
        "private_media_allow_authorized_http": allowed,
        "private_media_key": "synthetic-private-media-key-for-http-policy",
    })
    app = FastAPI()
    install_private_hls_routes(app, configured)
    if allowed:
        app.state.private_hls_factory(_source()).close()
    else:
        with pytest.raises(MediaReadError):
            app.state.private_hls_factory(_source())
    file_player = app.state.private_file_factory(_source(CatalogKind.VOD))
    assert file_player._reader.keywords["allow_authorized_http"] is allowed
    file_player.close()
