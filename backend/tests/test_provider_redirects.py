"""Synthetic provider-issued redirects; no real provider network."""
from contextlib import contextmanager

import pytest
from pydantic import SecretStr

from app.schemas import CatalogKind, EntitlementState
from app.services.media_body import MediaReadError
from app.services.media_origin import AuthorizedMediaOrigin, MediaAsset
from app.services.media_redirect import provider_redirect_url
from app.services.private_ts import PrivateTsSession
from test_media_origin import FakePool, FakeResponse
from test_private_hls import ORIGIN, TS

ROOT = ORIGIN + "/live/fixture-user/fixture-password/101.ts"
EDGE = "https://edge.example.com:8443/issued/101?token=fixture-not-real"


def redirect(url, status=302):
    return FakeResponse(status, [(b"Location", url.encode()),
                                 (b"Set-Cookie", b"must-not-forward=fixture")],
                        [b"redirect-body-must-not-be-read"])


def fixture_reader(routes, *, resolver=lambda *_: ["93.184.216.34"]):
    pools, targets, calls = [], [], []

    def factory(target):
        # No two upstream connections during a redirect hand-off.
        assert all(p.closed for p in pools)
        targets.append(target)

        class Pool(FakePool):
            @contextmanager
            def stream(self, method, url, **kwargs):
                calls.append(url)
                assert kwargs["headers"] == [
                    (b"Accept", b"video/mp2t"), (b"Accept-Encoding", b"identity"),
                    (b"User-Agent", b"Vivoapk/1.0"),
                ]
                self.response = routes[url]
                with super().stream(method, url, **kwargs) as value:
                    yield value

        pool = Pool(None)
        pools.append(pool)
        return pool

    source = AuthorizedMediaOrigin(MediaAsset(CatalogKind.LIVE, "10", "101"), ORIGIN,
                                   "fixture-user", SecretStr("fixture-password"), "ts")
    reader = PrivateTsSession(source, entitlement=lambda: EntitlementState.ACTIVE,
                              allow_provider_redirects=True,
                              resolver=resolver, pool_factory=factory)
    return reader, pools, targets, calls


def accepted():
    return FakeResponse(200, [(b"Content-Type", b"video/mp2t")], [TS * 1000] * 32)


def test_issued_public_edge_is_pinned_and_previous_connection_closed_before_follow():
    first = redirect(EDGE)
    reader, pools, targets, calls = fixture_reader({ROOT: first, EDGE: accepted()})
    assert reader.probe()
    assert calls == [ROOT, EDGE]
    assert targets[1].hostname == "edge.example.com" and targets[1].port == 8443
    assert targets[1].addresses == ("93.184.216.34",)
    assert first.reads == 0
    assert all(p.closed for p in pools)
    reader.close()


@pytest.mark.parametrize("target", [
    "https://127.0.0.1/secret", "https://169.254.169.254/latest",
    "https://[::1]/private", "https://localhost/private",
    "file:///etc/passwd", "ftp://edge.example.com/private",
    "http://edge.example.com/downgrade", "https://user:secret@edge.example.com/path",
    ROOT, "https://edge.example.com/../path",
])
def test_unsafe_or_cyclic_redirect_closes_without_a_second_request(target):
    first = redirect(target)
    reader, pools, _targets, calls = fixture_reader({ROOT: first})
    with pytest.raises(MediaReadError):
        reader.probe()
    assert calls == [ROOT] and first.reads == 0 and all(p.closed for p in pools)
    for action in (reader.probe, reader.read, reader.reset):
        with pytest.raises(MediaReadError):
            action()
    assert calls == [ROOT]


def test_rebinding_private_address_is_rejected_before_edge_connection():
    reader, pools, targets, calls = fixture_reader(
        {ROOT: redirect(EDGE)},
        resolver=lambda host, _: ["127.0.0.1" if host == "edge.example.com" else "93.184.216.34"],
    )
    with pytest.raises(MediaReadError):
        reader.probe()
    assert calls == [ROOT] and len(targets) == 1 and pools[0].closed


@pytest.mark.parametrize("status", [401, 403])
def test_edge_authorization_denial_is_terminal_without_body_retry_or_fallback(status):
    denied = FakeResponse(status, [], [b"must-not-read"])
    reader, pools, _targets, calls = fixture_reader({ROOT: redirect(EDGE), EDGE: denied})
    with pytest.raises(MediaReadError):
        reader.probe()
    assert calls == [ROOT, EDGE] and denied.reads == 0 and all(p.closed for p in pools)
    for action in (reader.probe, reader.read, reader.reset):
        with pytest.raises(MediaReadError):
            action()
    assert calls == [ROOT, EDGE]


def test_maximum_two_issued_hops_and_no_redirect_body_read():
    third = "https://third.example.com/issued"
    fourth = "https://fourth.example.com/never"
    responses = {ROOT: redirect(EDGE), EDGE: redirect(third, 307), third: redirect(fourth, 308)}
    reader, pools, _targets, calls = fixture_reader(responses)
    with pytest.raises(MediaReadError):
        reader.probe()
    assert calls == [ROOT, EDGE, third]
    assert all(r.reads == 0 for r in responses.values()) and all(p.closed for p in pools)


def test_long_opaque_issued_location_is_allowed_but_not_other_headers_or_tls_downgrade():
    location = "https://edge.example.com/issued?token=" + "x" * 700
    assert provider_redirect_url(ROOT, [(b"Location", location.encode())],
                                 allow_authorized_http=False) == location
    with pytest.raises(MediaReadError):
        provider_redirect_url(ROOT, [(b"Location", b"http://edge.example.com/path")],
                              allow_authorized_http=True)
    with pytest.raises(MediaReadError):
        provider_redirect_url(ROOT, [(b"Location", b"/one"), (b"Location", b"/two")],
                              allow_authorized_http=False)


def test_redirect_policy_requires_explicit_private_ts_and_provider_source(settings):
    from app.config import Settings

    assert settings.private_media_follow_provider_redirects is False
    values = {**settings.model_dump(), "private_hls_enabled": True,
              "private_media_key": "fixture-private-media-key-distinct-32-characters",
              "private_live_input_format": "ts", "private_live_use_provider_playlist": True,
              "private_media_follow_provider_redirects": True}
    assert Settings(**values).private_media_follow_provider_redirects
    for change in ({"private_live_use_provider_playlist": False},
                   {"private_live_input_format": "m3u8"}):
        with pytest.raises(ValueError):
            Settings(**{**values, **change})
