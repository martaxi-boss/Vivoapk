"""Synthetic private HLS origin/segment safety and lifecycle tests."""
import httpcore
import pytest
from pydantic import SecretStr

from app.schemas import CatalogKind, EntitlementState
from app.services.media_body import MediaReadError
from app.services.media_origin import AuthorizedMediaOrigin, MediaAsset
from app.services.private_hls import PrivateHlsSession
from test_media_origin import FakePool, FakeResponse, public_dns

MIME = b"application/vnd.apple.mpegurl"
TS = (b"\x47" + b"\x00" * 187) * 4
ORIGIN = "https://media.example.com"
ROOT = ORIGIN + "/live/fixture-user/fixture-password/101.m3u8"
VARIANT = ORIGIN + "/muxed/index.m3u8"


def media(seq=10, count=3, end=False):
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:4",
             f"#EXT-X-MEDIA-SEQUENCE:{seq}"]
    for n in range(seq, seq + count):
        lines.extend(["#EXTINF:4.0,private title", f"/muxed/s{n}.ts?private=fixture-secret"])
    if end:
        lines.append("#EXT-X-ENDLIST")
    return ("\n".join(lines) + "\n").encode()


def response(body, mime=MIME, status=200, headers=None):
    return FakeResponse(status, headers if headers is not None else [
        (b"Content-Type", mime), (b"Content-Length", str(len(body)).encode()),
    ], [body])


class Origin:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.pools = []

    def factory(self, target):
        assert target.hostname == "media.example.com"
        assert target.addresses == ("93.184.216.34",)
        outer = self

        class Pool(FakePool):
            def stream(self, method, url, **kwargs):
                outer.calls.append(url)
                self.response = outer.routes[url]()
                return super().stream(method, url, **kwargs)

        pool = Pool(None)
        self.pools.append(pool)
        return pool


def session(origin, state, source_origin=ORIGIN, resolver=public_dns):
    src = AuthorizedMediaOrigin(
        MediaAsset(CatalogKind.LIVE, "10", "101"), source_origin,
        "fixture-user", SecretStr("fixture-password"), "m3u8",
    )
    return PrivateHlsSession(src, entitlement=lambda: state[0],
                             resolver=resolver, pool_factory=origin.factory)


def handles(playlist):
    return [line.split("/", 1)[1][:-3] for line in playlist.splitlines()
            if line.startswith("segment/")]


def test_master_selection_rewrite_and_segment_isolation():
    master = (b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nlow.m3u8\n"
              b'#EXT-X-STREAM-INF:BANDWIDTH=2500000,CODECS="avc1.64001f,mp4a.40.2"\n'
              b"/muxed/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=10000000\nlarge.m3u8\n")
    upstream = Origin({
        ROOT: lambda: response(master), VARIANT: lambda: response(media()),
        ORIGIN + "/muxed/s10.ts?private=fixture-secret": lambda: response(TS, b"video/mp2t"),
    })
    state = [EntitlementState.ACTIVE]
    a, b = session(upstream, state), session(upstream, state)
    first = a.refresh()
    assert "#EXT-X-MEDIA-SEQUENCE:10" in first
    for forbidden in ("fixture", "media.example.com", "private", "/muxed", "http"):
        assert forbidden not in first
    assert upstream.calls == [ROOT, VARIANT]
    other = b.refresh()
    assert not set(handles(first)) & set(handles(other))
    calls = len(upstream.calls)
    with pytest.raises(MediaReadError):
        b.read_segment(handles(first)[0])
    assert len(upstream.calls) == calls
    assert a.read_segment(handles(first)[0]) == TS
    assert all(pool.closed and pool.response.closed for pool in upstream.pools)
    a.close()
    with pytest.raises(MediaReadError):
        a.refresh()
    assert b.refresh() == other


def test_sliding_window_retains_recent_handles_and_expires_old_ones():
    current = [10]
    upstream = Origin({
        ROOT: lambda: response(media(current[0])),
        ORIGIN + "/muxed/s10.ts?private=fixture-secret": lambda: response(TS, b"video/mp2t"),
    })
    player = session(upstream, [EntitlementState.ACTIVE])
    old = handles(player.refresh())
    current[0] = 11
    new = handles(player.refresh())
    assert old[1:] == new[:2]
    assert player.read_segment(old[0]) == TS
    current[0] = 20
    player.refresh()
    count = len(upstream.calls)
    with pytest.raises(MediaReadError):
        player.read_segment(old[0])
    assert len(upstream.calls) == count
    current[0] = 19
    with pytest.raises(MediaReadError):
        player.refresh()


@pytest.mark.parametrize("uri", [
    "https://evil.example/s.ts", "//evil.example/s.ts", "http://media.example.com/s.ts",
    "https://media.example.com:444/s.ts", "https://user@media.example.com/s.ts",
    "/muxed/../private.ts", "/muxed/%2e%2e/private.ts", "/muxed/%2fprivate.ts",
    "/muxed/s.ts#fragment", "file:///etc/passwd", r"/muxed/\private.ts",
])
def test_unsafe_playlist_segment_rejected_before_network(uri):
    body = ("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\n" + uri + "\n").encode()
    upstream = Origin({ROOT: lambda: response(body)})
    player = session(upstream, [EntitlementState.ACTIVE])
    with pytest.raises(MediaReadError):
        player.refresh()
    assert upstream.calls == [ROOT]
    assert upstream.pools[0].closed


@pytest.mark.parametrize("tag", [
    '#EXT-X-KEY:METHOD=AES-128,URI="https://media.example.com/key"',
    '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="key"',
    '#EXT-X-MAP:URI="init.mp4"', "#EXT-X-BYTERANGE:100@0",
    '#EXT-X-PART:DURATION=0.3,URI="part.ts"', "#EXT-X-SESSION-KEY:METHOD=AES-128",
])
def test_unsupported_manifest_features_fail_closed(tag):
    body = ("#EXTM3U\n#EXT-X-TARGETDURATION:4\n" + tag + "\n#EXTINF:4,\ns.ts\n").encode()
    upstream = Origin({ROOT: lambda: response(body)})
    with pytest.raises(MediaReadError):
        session(upstream, [EntitlementState.ACTIVE]).refresh()
    assert upstream.calls == [ROOT]


def test_revocation_before_and_during_segment_no_leak():
    state = [EntitlementState.ACTIVE]
    def revoked_response():
        state[0] = EntitlementState.EXPIRED
        return response(TS, b"video/mp2t")
    upstream = Origin({
        ROOT: lambda: response(media()),
        ORIGIN + "/muxed/s10.ts?private=fixture-secret": revoked_response,
    })
    player = session(upstream, state)
    handle = handles(player.refresh())[0]
    with pytest.raises(MediaReadError, match="unavailable"):
        player.read_segment(handle)
    assert upstream.pools[-1].closed and upstream.pools[-1].response.closed
    count = len(upstream.calls)
    state[0] = EntitlementState.ACTIVE
    with pytest.raises(MediaReadError):
        player.refresh()
    assert len(upstream.calls) == count
    state[0] = EntitlementState.UNAVAILABLE
    with pytest.raises(MediaReadError):
        session(upstream, state).refresh()
    assert len(upstream.calls) == count


def test_redirect_private_dns_and_plaintext_rejected():
    upstream = Origin({ROOT: lambda: response(b"", status=302, headers=[
        (b"Location", b"https://other.example/fixture-password"),
    ])})
    with pytest.raises(MediaReadError) as err:
        session(upstream, [EntitlementState.ACTIVE]).refresh()
    assert "fixture-password" not in str(err.value)
    assert upstream.calls == [ROOT]
    with pytest.raises(MediaReadError):
        session(upstream, [EntitlementState.ACTIVE],
                resolver=lambda *_: ["127.0.0.1"]).refresh()
    with pytest.raises(MediaReadError):
        session(upstream, [EntitlementState.ACTIVE], source_origin="http://media.example.com")
    assert upstream.calls == [ROOT]


@pytest.mark.parametrize("bad_response", [
    lambda: response(b"not a transport stream", b"video/mp2t"),
    lambda: response(TS, b"text/html"),
    lambda: response(TS, b"video/mp2t", headers=[
        (b"Content-Type", b"video/mp2t"), (b"Content-Length", b"99999999"),
    ]),
    lambda: FakeResponse(headers=[(b"Content-Type", b"video/mp2t")],
                         blocks=[httpcore.ReadError("fixture-password")]),
])
def test_segment_response_bounds_and_cleanup(bad_response):
    upstream = Origin({
        ROOT: lambda: response(media()),
        ORIGIN + "/muxed/s10.ts?private=fixture-secret": bad_response,
    })
    player = session(upstream, [EntitlementState.ACTIVE])
    handle = handles(player.refresh())[0]
    with pytest.raises(MediaReadError) as err:
        player.read_segment(handle)
    assert "fixture-password" not in str(err.value)
    assert upstream.pools[-1].closed and upstream.pools[-1].response.closed


def test_window_bound_and_conflicting_sequence():
    body = [media(count=49)]
    upstream = Origin({ROOT: lambda: response(body[0])})
    player = session(upstream, [EntitlementState.ACTIVE])
    with pytest.raises(MediaReadError):
        player.refresh()
    body[0] = media(end=True)
    assert "#EXT-X-ENDLIST" in player.refresh()
    body[0] = media().replace(b"s10.ts", b"other.ts")
    with pytest.raises(MediaReadError):
        player.refresh()
