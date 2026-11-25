"""Opt-in private HLS control endpoints on the existing backend socket.

No origin URLs or provider credentials leave this process. Every operation
requires both an independent service key and a valid existing account-scoped
session token. The registry is intentionally bounded for the existing
single-worker Uvicorn deployment; do not enable it in a multi-worker service.
No public Nginx route, Jellyfin loopback adapter, or activation is added here.
"""
from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.clients.catalog import CatalogUnavailable
from app.config import Settings
from app.db import get_db
from app.schemas import CatalogKind, ClientPlatform, EntitlementState
from app.services.catalog import CatalogDenied, CatalogService, mapping_id_for_token
from app.services.media_body import MediaReadError
from app.services.media_origin import AuthorizedMediaOrigin, MediaAsset
from app.services.private_hls import PrivateHlsSession

_NO_STORE = {"Cache-Control": "no-store"}
_MAX_SESSIONS = 2  # Conservative staging gate; not a certified VPS capacity.
_IDLE_SECONDS = 90


class OpenLiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: str = Field(pattern=r"^[0-9]{1,64}$")
    asset_id: str = Field(pattern=r"^[0-9]{1,64}$")
    platform: ClientPlatform


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32}$")


class SegmentRequest(SessionRequest):
    segment_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32}$")


@dataclass(slots=True, repr=False)
class _Entry:
    owner: int
    platform: ClientPlatform
    seen: float
    busy: bool = True
    source: AuthorizedMediaOrigin | None = None
    player: PrivateHlsSession | None = None


def _http(status: int) -> HTTPException:
    return HTTPException(status_code=status, detail="Private media unavailable",
                         headers=_NO_STORE)


class PrivateHlsRegistry:
    """RAM-only session admission, exact owner binding, and idle expiration.

    No upstream I/O under the registry lock. One in-flight operation per session;
    a concurrent operation must retry. Lazy expiry is bounded by two entries,
    and a later operation cannot revive an idle-expired entry.
    """
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = Lock()

    @staticmethod
    def _dispose(entry: _Entry) -> None:
        if entry.player is not None:
            entry.player.close()
        entry.player = None
        entry.source = None

    def _prune(self) -> None:
        now = time.monotonic()
        for key, entry in tuple(self._entries.items()):
            if not entry.busy and now - entry.seen >= _IDLE_SECONDS:
                del self._entries[key]
                self._dispose(entry)

    @contextmanager
    def reserve(self, owner: int, platform: ClientPlatform) -> Iterator[tuple[str, _Entry]]:
        with self._lock:
            self._prune()
            if (len(self._entries) >= _MAX_SESSIONS
                    or any(e.owner == owner for e in self._entries.values())):
                raise _http(429)
            key = secrets.token_urlsafe(24)
            if key in self._entries:
                raise _http(503)
            entry = _Entry(owner, platform, time.monotonic())
            self._entries[key] = entry
        committed = False
        try:
            yield key, entry
            committed = entry.source is not None and entry.player is not None
            if not committed:
                raise _http(503)
        finally:
            with self._lock:
                if committed:
                    entry.busy = False
                    entry.seen = time.monotonic()
                else:
                    self._entries.pop(key, None)
                    self._dispose(entry)

    @contextmanager
    def borrow(self, key: str, owner: int) -> Iterator[_Entry]:
        with self._lock:
            self._prune()
            entry = self._entries.get(key)
            if entry is None or entry.owner != owner:
                raise _http(404)
            if entry.busy:
                raise _http(429)
            if entry.player is None or entry.source is None:
                raise _http(404)
            entry.busy = True
        success = False
        try:
            yield entry
            success = True
        finally:
            with self._lock:
                if self._entries.get(key) is entry:
                    entry.busy = False
                    if success:
                        entry.seen = time.monotonic()
                    else:
                        # Fail closed. A bad/revoked upstream session cannot be
                        # reused; an authorized client can request a new one.
                        self._entries.pop(key, None)
                        self._dispose(entry)

    def close(self, key: str, owner: int) -> None:
        with self._lock:
            self._prune()
            entry = self._entries.get(key)
            if entry is None or entry.owner != owner:
                raise _http(404)
            if entry.busy:
                raise _http(429)
            del self._entries[key]
            self._dispose(entry)


@dataclass(frozen=True, slots=True, repr=False)
class _Identity:
    mapping_id: int
    token: str


def install_private_hls_routes(app: FastAPI, settings: Settings) -> None:
    registry = PrivateHlsRegistry()
    app.state.private_hls_registry = registry
    # Test injection only. No new listening socket or external route.
    app.state.private_hls_factory = lambda source: PrivateHlsSession(
        source, entitlement=lambda: EntitlementState.UNAVAILABLE,
    )

    def identity(request: Request) -> _Identity:
        key = settings.private_media_key
        keys = request.headers.getlist("x-vivoapk-media-key")
        auth = request.headers.getlist("authorization")
        if (key is None or len(keys) != 1 or len(keys[0]) > 1024
                or not hmac.compare_digest(key.get_secret_value().encode(), keys[0].encode())):
            raise _http(403)
        if len(auth) != 1 or not auth[0].startswith("Bearer ") or len(auth[0]) > 4120:
            raise _http(401)
        token = auth[0][7:]
        try:
            return _Identity(mapping_id_for_token(token, settings), token)
        except CatalogDenied:
            raise _http(401) from None

    def catalogue(request: Request, db: Session) -> CatalogService:
        return CatalogService(
            db, settings, mega_client_factory=request.app.state.mega_client_factory,
            xtream_client_factory=request.app.state.xtream_client_factory,
            catalog_client_factory=request.app.state.catalog_client_factory,
        )

    def entitlement(service: CatalogService, caller: _Identity, entry: _Entry):
        def verify() -> EntitlementState:
            # Use the exact mapping and selected platform again, not the generic
            # entitlement check's Android DNS. A changed origin/credential
            # requires a new authorized session; never silently rebind one.
            source = entry.source
            if source is None:
                return EntitlementState.UNAVAILABLE
            try:
                origin, username, password = service._authorize(
                    token=caller.token, platform=entry.platform,
                )
            except CatalogDenied:
                return EntitlementState.DISABLED
            except (CatalogUnavailable, HTTPException):
                return EntitlementState.UNAVAILABLE
            if (origin != source.origin or username != source.username
                    or not hmac.compare_digest(password.encode(),
                                               source.password.get_secret_value().encode())):
                return EntitlementState.DISABLED
            return EntitlementState.ACTIVE
        return verify

    # All opaque identifiers are in the private POST body, not access-log URLs.
    # A service-key header alone is never a grant to another account's session.
    @app.post("/_internal/media/hls/open", include_in_schema=False)
    def open_live(
        payload: OpenLiveRequest, request: Request, caller: _Identity = Depends(identity),
        db: Session = Depends(get_db),
    ):
        with registry.reserve(caller.mapping_id, payload.platform) as (key, entry):
            try:
                source = catalogue(request, db).authorize_media_origin(
                    token=caller.token, asset=MediaAsset(
                        CatalogKind.LIVE, payload.category_id, payload.asset_id,
                    ), platform=payload.platform,
                )
                entry.source = source
                entry.player = request.app.state.private_hls_factory(source)
            except CatalogDenied:
                raise _http(404) from None
            except (CatalogUnavailable, MediaReadError, ValueError):
                raise _http(503) from None
        return Response(
            content='{"session_id":"' + key + '"}', media_type="application/json",
            headers=_NO_STORE,
        )

    @app.post("/_internal/media/hls/playlist", include_in_schema=False)
    def playlist(
        payload: SessionRequest, request: Request, caller: _Identity = Depends(identity),
        db: Session = Depends(get_db),
    ):
        with registry.borrow(payload.session_id, caller.mapping_id) as entry:
            assert entry.player is not None
            try:
                body = entry.player.refresh(
                    entitlement=entitlement(catalogue(request, db), caller, entry),
                )
            except MediaReadError:
                raise _http(404) from None
        return Response(body, media_type="application/vnd.apple.mpegurl", headers=_NO_STORE)

    @app.post("/_internal/media/hls/segment", include_in_schema=False)
    def segment(
        payload: SegmentRequest, request: Request, caller: _Identity = Depends(identity),
        db: Session = Depends(get_db),
    ):
        with registry.borrow(payload.session_id, caller.mapping_id) as entry:
            assert entry.player is not None
            try:
                body = entry.player.read_segment(
                    payload.segment_id,
                    entitlement=entitlement(catalogue(request, db), caller, entry),
                )
            except MediaReadError:
                raise _http(404) from None
        return Response(body, media_type="video/mp2t", headers=_NO_STORE)

    @app.post("/_internal/media/hls/close", include_in_schema=False)
    def close(payload: SessionRequest, caller: _Identity = Depends(identity)):
        registry.close(payload.session_id, caller.mapping_id)
        return Response(status_code=204, headers=_NO_STORE)
