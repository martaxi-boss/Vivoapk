"""Exercise actual private file/HLS/TS requests against synthetic origins."""
from pydantic import SecretStr

from app.schemas import CatalogKind, EntitlementState
from app.services.media_body import open_media_body
from app.services.media_origin import AuthorizedMediaOrigin, MediaAsset
from app.services.private_ts import PrivateTsSession
from test_media_origin import FakePool, FakeResponse, public_dns
from test_private_hls import ORIGIN, ROOT, TS, Origin, handles, media, response, session


def assert_truthful_identity(pools):
    requests = [call for pool in pools for call in pool.calls]
    assert requests
    for method, _url, kwargs in requests:
        assert method == "GET"
        headers = kwargs["headers"]
        assert [v for k, v in headers if k.lower() == b"user-agent"] == [b"Vivoapk/1.0"]
        assert (b"Accept-Encoding", b"identity") in headers
        assert not any(k.lower() in {b"referer", b"cookie", b"authorization"} for k, _ in headers)


def test_file_reader_identifies_actual_product_without_forwarding_account_headers():
    source = AuthorizedMediaOrigin(MediaAsset(CatalogKind.VOD, "10", "501"), ORIGIN,
                                   "fixture-user", SecretStr("fixture-password"), "mp4")
    pool = FakePool(FakeResponse())
    with open_media_body(source, resolver=public_dns, pool_factory=lambda _: pool) as body:
        assert b"".join(body.chunks) == b"abcdef"
    assert_truthful_identity([pool])
    assert pool.closed


def test_manifest_and_segment_use_same_fixed_product_identity():
    upstream = Origin({
        ROOT: lambda: response(media()),
        ORIGIN + "/muxed/s10.ts?private=fixture-secret": lambda: response(TS, b"video/mp2t"),
    })
    reader = session(upstream, [EntitlementState.ACTIVE])
    try:
        playlist = reader.refresh()
        assert reader.read_segment(handles(playlist)[0]) == TS
    finally:
        reader.close()
    assert len(upstream.calls) == 2
    assert_truthful_identity(upstream.pools)


def test_ts_probe_and_persistent_playback_use_same_fixed_product_identity():
    root = ORIGIN + "/live/fixture-user/fixture-password/101.ts"
    upstream = Origin({root: lambda: FakeResponse(
        200, [(b"Content-Type", b"video/mp2t")], [TS * 1000] * 32,
    )})
    source = AuthorizedMediaOrigin(MediaAsset(CatalogKind.LIVE, "10", "101"), ORIGIN,
                                   "fixture-user", SecretStr("fixture-password"), "ts")
    reader = PrivateTsSession(source, entitlement=lambda: EntitlementState.ACTIVE,
                              resolver=public_dns, pool_factory=upstream.factory)
    try:
        assert reader.probe()
        assert reader.read()
        assert reader.read()
        assert len(upstream.calls) == 2
        assert_truthful_identity(upstream.pools)
    finally:
        reader.close()
    assert all(p.closed for p in upstream.pools)
