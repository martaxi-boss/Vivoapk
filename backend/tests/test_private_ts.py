"""Lawful generated/fake TS only; authorization denial is never retried."""
import pytest
from pydantic import SecretStr

from app.schemas import CatalogKind, EntitlementState
from app.services.media_body import MediaReadError
from app.services.media_origin import AuthorizedMediaOrigin, MediaAsset
from app.services.private_ts import PrivateTsSession, WINDOW_BYTES
from test_media_origin import FakeResponse, public_dns
from test_private_hls import ORIGIN, Origin, TS

ROOT = ORIGIN + "/live/fixture-user/fixture-password/101.ts"


def response():
    return FakeResponse(200, [(b"Content-Type", b"video/mp2t")],
                        [TS * 1000] * 32)


def player(upstream, state, origin=ORIGIN, **kwargs):
    source = AuthorizedMediaOrigin(MediaAsset(CatalogKind.LIVE, "10", "101"),
                                   origin, "fixture-user", SecretStr("fixture-password"), "ts")
    return PrivateTsSession(source, entitlement=lambda: state[0],
                            resolver=public_dns, pool_factory=upstream.factory, **kwargs)


def test_probe_playback_reuse_reset_and_cleanup():
    upstream = Origin({ROOT: response})
    reader = player(upstream, [EntitlementState.ACTIVE])
    assert reader.probe().startswith(TS)
    assert upstream.pools[0].closed
    first = reader.read()
    assert 0 < len(first) <= WINDOW_BYTES and len(first) % 188 == 0
    assert reader.read() == first
    assert upstream.calls == [ROOT, ROOT]  # Sequential probe + one persistent stream.
    assert not upstream.pools[-1].closed
    reader.reset()
    assert upstream.pools[-1].closed
    assert reader.read() == first
    assert upstream.calls == [ROOT] * 3
    reader.close()
    assert all(p.closed and p.response.closed for p in upstream.pools)


@pytest.mark.parametrize("status", [401, 403])
def test_denial_closes_without_body_or_format_fallback(status, caplog):
    denied = FakeResponse(status, [(b"Content-Type", b"text/html")], [b"private-body"])
    upstream = Origin({ROOT: lambda: denied})
    reader = player(upstream, [EntitlementState.ACTIVE])
    with pytest.raises(MediaReadError):
        reader.probe()
    assert denied.reads == 0 and denied.closed
    assert caplog.messages == [f"private_ts_origin_denied status={status}"]
    for action in (reader.probe, reader.read, reader.reset):
        with pytest.raises(MediaReadError):
            action()
    assert upstream.calls == [ROOT]


def test_revocation_only_retires_affected_player():
    upstream = Origin({ROOT: response})
    state = [EntitlementState.ACTIVE]
    a, b = player(upstream, state), player(upstream, [EntitlementState.ACTIVE])
    a.probe()
    b.probe()
    a.read()
    state[0] = EntitlementState.EXPIRED
    before = len(upstream.calls)
    with pytest.raises(MediaReadError):
        a.read()
    assert len(upstream.calls) == before
    assert b.read()
    a.close()
    b.close()


@pytest.mark.parametrize("headers,blocks", [
    ([(b"Content-Type", b"video/mp2t")], [b"not-ts" * 1000]),
    ([(b"Content-Type", b"text/html")], [TS]),
    ([(b"Content-Type", b"video/mp2t"), (b"Content-Length", b"1234")], [TS]),
    ([(b"Content-Type", b"video/mp2t")], [(b"\x47\x00\x00\x80" + b"\x00" * 184) * 4]),
])
def test_invalid_or_scrambled_stream_is_closed(headers, blocks):
    upstream = Origin({ROOT: lambda: FakeResponse(200, headers, blocks)})
    reader = player(upstream, [EntitlementState.ACTIVE])
    with pytest.raises(MediaReadError):
        reader.probe()
    assert upstream.pools[0].closed


def test_redirect_is_never_followed_and_default_http_denied():
    upstream = Origin({ROOT: lambda: FakeResponse(302, [(b"Location", b"https://other")], [])})
    reader = player(upstream, [EntitlementState.ACTIVE])
    with pytest.raises(MediaReadError):
        reader.probe()
    assert upstream.calls == [ROOT]
    with pytest.raises(MediaReadError):
        player(upstream, [EntitlementState.ACTIVE], origin="http://media.example.com")


def test_mid_read_revocation_drops_window_before_delivery():
    upstream = Origin({ROOT: response})
    reader = player(upstream, [EntitlementState.ACTIVE])
    calls = []
    def verify():
        calls.append(1)
        return EntitlementState.ACTIVE if len(calls) == 1 else EntitlementState.DISABLED
    with pytest.raises(MediaReadError):
        reader.probe(entitlement=verify)
    assert all(p.closed for p in upstream.pools)
