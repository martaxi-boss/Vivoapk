"""Private backend HLS API contract, no real provider or network access."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.main import create_app
from app.models import SubscriptionMapping
from app.schemas import EntitlementState
from app.security import issue_session_token
from app.services.private_hls import PrivateHlsSession
from test_catalog_privacy import FakeCatalog, FakeMega, FakeXtream
from test_media_origin import public_dns
from test_private_hls import ORIGIN, TS, Origin, handles, media, response

KEY = "fixture-private-media-key-for-contract-only"
PATH = "/_internal/media/hls/"


@pytest.fixture
def private_client(settings, session_factory):
    configured = Settings(**{
        **settings.model_dump(), "private_hls_enabled": True,
        "private_media_key": KEY,
    })
    future = datetime.now(UTC) + timedelta(days=10)
    rows, tokens = {}, {}
    with session_factory() as db:
        for index, suffix in enumerate(("a", "b"), 1):
            m = SubscriptionMapping(
                mega_subscription_id=900 + index, username=f"sub-{suffix}",
                dns_link=ORIGIN, dns_link_samsung_lg=ORIGIN,
                expiring_at=future, last_synced_at=datetime.now(UTC),
            )
            db.add(m)
            db.flush()
            tokens[suffix], _ = issue_session_token(m.id, configured)
            rows[900 + index] = SimpleNamespace(
                id=900 + index, username=m.username,
                password=SecretStr(f"fixture-password-{suffix}"),
                expiring_at=future, dns_link=ORIGIN, dns_link_for_samsung_lg=ORIGIN,
            )
        db.commit()
    xtream = FakeXtream()
    app = create_app(configured)
    app.state.session_factory = session_factory
    app.state.mega_client_factory = lambda: FakeMega(rows)
    app.state.xtream_client_factory = lambda: xtream
    app.state.catalog_client_factory = FakeCatalog
    upstream = Origin({
        ORIGIN + "/live/sub-a/fixture-password-a/101.m3u8": lambda: response(media()),
        ORIGIN + "/live/sub-b/fixture-password-b/101.m3u8": lambda: response(media()),
        ORIGIN + "/muxed/s10.ts?private=fixture-secret": lambda: response(TS, b"video/mp2t"),
    })
    app.state.private_hls_factory = lambda source: PrivateHlsSession(
        source, entitlement=lambda: EntitlementState.UNAVAILABLE,
        resolver=public_dns, pool_factory=upstream.factory,
    )
    with TestClient(app) as client:
        grants = {}
        def post(action, suffix="a", payload=None, key=KEY, credential=None):
            token = (tokens[suffix] if action == "open"
                     else grants.get(suffix, tokens[suffix]))
            result = client.post(PATH + action, json=payload or {}, headers={
                "X-Vivoapk-Media-Key": key,
                "Authorization": "Bearer " + (credential if credential is not None else token),
            })
            if result.status_code == 200 and action in {"open", "renew"}:
                grants[suffix] = result.json()["media_token"]
            return result
        yield client, post, upstream, xtream, rows


def request_for(suffix):
    return {"category_id": "10" if suffix == "a" else "20",
            "asset_id": "101", "platform": "android" if suffix == "a" else "tizen"}


def test_private_hls_disabled_by_default_and_key_required(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.post(PATH + "open", json=request_for("a")).status_code == 404
    with pytest.raises(ValidationError):
        Settings(**{**settings.model_dump(), "private_hls_enabled": True})
    with pytest.raises(ValidationError):
        Settings(**{**settings.model_dump(), "private_hls_enabled": True,
                    "private_media_key": settings.session_signing_key})


def test_open_playlist_segment_isolation_and_explicit_close(private_client):
    client, post, upstream, _, _ = private_client
    assert post("open", payload=request_for("a"), key="wrong-key").status_code == 403
    assert post("open", payload=request_for("b")).status_code == 404
    assert client.post(PATH + "open", json=request_for("a"),
                       headers={"X-Vivoapk-Media-Key": KEY}).status_code == 401
    assert upstream.calls == []
    a = post("open", payload=request_for("a"))
    b = post("open", suffix="b", payload=request_for("b"))
    assert a.status_code == b.status_code == 200
    sa, sb = a.json()["session_id"], b.json()["session_id"]
    assert sa != sb and "fixture-password" not in a.text
    assert post("open", payload=request_for("a")).status_code == 429
    assert post("playlist", suffix="b", payload={"session_id": sa}).status_code == 404
    assert upstream.calls == []
    pa = post("playlist", payload={"session_id": sa})
    pb = post("playlist", suffix="b", payload={"session_id": sb})
    assert pa.status_code == pb.status_code == 200
    assert pa.headers["Cache-Control"] == "no-store"
    assert "fixture-password" not in pa.text and "media.example.com" not in pa.text
    ha = handles(pa.text)[0]
    hb = handles(pb.text)[0]
    assert ha != hb
    calls = len(upstream.calls)
    assert post("segment", suffix="b", payload={"session_id": sa,
                "segment_id": ha}).status_code == 404
    assert len(upstream.calls) == calls
    segment = post("segment", payload={"session_id": sa, "segment_id": ha})
    assert segment.status_code == 200 and segment.content == TS
    assert segment.headers["Cache-Control"] == "no-store"
    assert post("close", suffix="b", payload={"session_id": sa}).status_code == 404
    assert post("close", payload={"session_id": sa}).status_code == 204
    assert post("playlist", payload={"session_id": sa}).status_code == 404
    assert post("segment", suffix="b", payload={"session_id": sb,
                "segment_id": hb}).content == TS
    assert post("close", suffix="b", payload={"session_id": sb}).status_code == 204


def test_revocation_and_origin_change_fail_closed(private_client):
    _, post, upstream, xtream, rows = private_client
    result = post("open", payload=request_for("a"))
    assert result.status_code == 200
    sid = result.json()["session_id"]
    playlist = post("playlist", payload={"session_id": sid})
    assert playlist.status_code == 200
    handle = handles(playlist.text)[0]
    count = len(upstream.calls)
    xtream.status = "DISABLED"
    assert post("segment", payload={"session_id": sid, "segment_id": handle}).status_code == 404
    assert len(upstream.calls) == count
    xtream.status = "SUCCESS"
    assert post("playlist", payload={"session_id": sid}).status_code == 404
    sid = post("open", payload=request_for("a")).json()["session_id"]
    rows[901].password = SecretStr("changed-fixture-password")
    assert post("playlist", payload={"session_id": sid}).status_code == 404
    assert len(upstream.calls) == count


def test_rotating_grant_cannot_be_replayed_or_open_other_assets(private_client):
    client, post, upstream, xtream, _ = private_client
    a = post("open", payload=request_for("a")).json()
    b = post("open", suffix="b", payload=request_for("b")).json()
    sa, sb = a["session_id"], b["session_id"]
    old = a["media_token"]
    assert a["valid_for_seconds"] == 300 and len(old) == 43
    assert post("renew", suffix="b", payload={"session_id": sa}).status_code == 404
    assert post("open", payload=request_for("a"), credential=old).status_code == 401
    assert client.post("/v1/catalog/list", json={"kind": "live", "platform": "android"},
                       headers={"Authorization": "Bearer " + old}).status_code == 401
    rotated = post("renew", payload={"session_id": sa})
    assert rotated.status_code == 200 and rotated.headers["Cache-Control"] == "no-store"
    new = rotated.json()["media_token"]
    assert old != new and "session_token" not in rotated.json()
    assert post("renew", payload={"session_id": sa}, credential=old).status_code == 404
    assert post("playlist", payload={"session_id": sa}, credential=old).status_code == 404
    playlist = post("playlist", payload={"session_id": sa})
    assert playlist.status_code == 200
    assert post("segment", payload={"session_id": sa,
                 "segment_id": handles(playlist.text)[0]}).content == TS
    calls = len(upstream.calls)
    xtream.status = "DISABLED"
    assert post("renew", payload={"session_id": sa}).status_code == 404
    assert len(upstream.calls) == calls
    xtream.status = "SUCCESS"
    assert post("playlist", payload={"session_id": sa}).status_code == 404
    assert post("playlist", suffix="b", payload={"session_id": sb}).status_code == 200
    assert post("close", suffix="b", payload={"session_id": sb}).status_code == 204


def test_periodic_sweep_releases_expired_grant_without_request(private_client, monkeypatch):
    # Run the exact async sweep loop with a short fixture interval; no API
    # request is made after advancing the entry's monotonic deadline.
    client, post, _, _, _ = private_client
    a = post("open", payload=request_for("a")).json()
    b = post("open", suffix="b", payload=request_for("b")).json()
    registry = client.app.state.private_hls_registry
    sa, sb = a["session_id"], b["session_id"]
    with registry._lock:
        entry = registry._entries[sa]
        entry.grant_expires = time.monotonic() - 1
    monkeypatch.setattr("app.private_media_api._SWEEP_SECONDS", 0.01)

    async def check():
        task = asyncio.create_task(registry.run_sweeper())
        try:
            for _ in range(100):
                with registry._lock:
                    if sa not in registry._entries:
                        break
                await asyncio.sleep(.01)
            with registry._lock:
                assert sa not in registry._entries and sb in registry._entries
                assert entry.source is None and entry.player is None and not entry.grant_digest
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    asyncio.run(check())
    assert post("renew", payload={"session_id": sa}).status_code == 404
    assert post("playlist", suffix="b", payload={"session_id": sb}).status_code == 200
    assert post("close", suffix="b", payload={"session_id": sb}).status_code == 204

@pytest.mark.parametrize(("advertised", "status"), [
    (None, 503),
    (frozenset(), 404),
    (frozenset({"ts"}), 404),
])
def test_live_requires_advertised_hls_before_open(private_client, advertised, status):
    client, post, upstream, xtream, _ = private_client
    xtream.allowed_output_formats = advertised
    rejected = post("open", payload=request_for("a"))
    assert rejected.status_code == status
    assert upstream.calls == []
    assert not client.app.state.private_hls_registry._entries
    assert "fixture" not in rejected.text


def test_live_format_change_revokes_existing_grant(private_client):
    client, post, upstream, xtream, _ = private_client
    opened = post("open", payload=request_for("a"))
    assert opened.status_code == 200
    sid = opened.json()["session_id"]
    assert post("playlist", payload={"session_id": sid}).status_code == 200
    before = len(upstream.calls)
    xtream.allowed_output_formats = frozenset({"ts"})
    assert post("renew", payload={"session_id": sid}).status_code == 404
    assert post("playlist", payload={"session_id": sid}).status_code == 404
    assert len(upstream.calls) == before
    assert not client.app.state.private_hls_registry._entries
