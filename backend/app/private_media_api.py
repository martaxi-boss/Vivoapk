"""Opt-in private HLS routes on the existing single-worker backend.

Open requires a valid account JWT and an independent service key. Every later
operation requires that service key AND the exact per-session opaque grant.
Grants rotate before expiry; never issue or renew an account-wide JWT here.
Credentials and grant digests remain in RAM; no public route or activation.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from threading import Lock
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.clients.catalog import CatalogUnavailable
from app.config import Settings
from app.db import get_db
from app.schemas import CatalogKind, ClientPlatform, EntitlementState
from app.services.catalog import CatalogDenied, CatalogService, mapping_id_for_token
from app.services.media_body import MediaReadError, open_media_body
from app.services.media_origin import AuthorizedMediaOrigin, MediaAsset
from app.services.private_hls import PrivateHlsSession
from app.services.private_ts import PrivateTsSession
from app.services.private_file import MAX_FILE_BYTES, MAX_WINDOW_BYTES, PrivateFileSession
from app.services.private_subscription_cache import private_mega_client

_NO_STORE = {"Cache-Control": "no-store"}
_MAX_SESSIONS = 2
_IDLE_SECONDS = 90
_SWEEP_SECONDS = 5


class OpenLiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: str = Field(pattern=r"^[0-9]{1,64}$")
    asset_id: str = Field(pattern=r"^[0-9]{1,64}$")
    platform: ClientPlatform


class OpenFileRequest(OpenLiveRequest):
    kind: Literal["vod", "series"]
    series_id: str | None = Field(default=None, pattern=r"^[0-9]{1,64}$")


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32}$")


class SegmentRequest(SessionRequest):
    segment_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32}$")


class FileReadRequest(SessionRequest):
    start: int = Field(strict=True, ge=0, lt=MAX_FILE_BYTES)
    length: int = Field(strict=True, ge=1, le=MAX_WINDOW_BYTES)


@dataclass(slots=True, repr=False)
class _Entry:
    owner: int
    platform: ClientPlatform
    seen: float
    busy: bool = True
    source: AuthorizedMediaOrigin | None = None
    player: PrivateHlsSession | PrivateTsSession | PrivateFileSession | None = None
    media_kind: Literal["hls", "file"] = "hls"
    grant_digest: bytes = b""
    grant_expires: float = 0.0


def _http(status: int) -> HTTPException:
    return HTTPException(status_code=status, detail="Private media unavailable",
                         headers=_NO_STORE)


def _digest(grant: str) -> bytes:
    return hashlib.sha256(grant.encode("utf-8")).digest()


class PrivateHlsRegistry:
    """Bounded, single-worker, RAM-only registry. Busy operations are not
    deleted by a sweeper. Every operation also checks expiration under lock.
    A rotation is atomic with respect to the entry's exclusive borrow.
    """
    def __init__(self, ttl: int) -> None:
        self._ttl = ttl
        self._entries: dict[str, _Entry] = {}
        self._lock = Lock()

    @staticmethod
    def _dispose(entry: _Entry) -> None:
        if entry.player is not None:
            entry.player.close()
        entry.player = None
        entry.source = None
        entry.grant_digest = b""
        entry.grant_expires = 0.0

    def _prune(self) -> None:
        now = time.monotonic()
        for key, entry in tuple(self._entries.items()):
            if not entry.busy and (now - entry.seen >= _IDLE_SECONDS
                                   or now >= entry.grant_expires):
                del self._entries[key]
                self._dispose(entry)

    def sweep(self) -> None:
        with self._lock:
            self._prune()

    async def run_sweeper(self) -> None:
        while True:
            await asyncio.sleep(_SWEEP_SECONDS)
            self.sweep()

    def shutdown(self) -> None:
        # ASGI lifespan shutdown follows request draining.
        with self._lock:
            for key, entry in tuple(self._entries.items()):
                if not entry.busy:
                    del self._entries[key]
                    self._dispose(entry)

    def rotate(self, entry: _Entry) -> dict:
        # Only called inside an exclusive reserve or borrow.
        grant = secrets.token_urlsafe(32)
        entry.grant_digest = _digest(grant)
        entry.grant_expires = time.monotonic() + self._ttl
        return {"media_token": grant, "valid_for_seconds": self._ttl}

    @contextmanager
    def reserve(self, owner: int, platform: ClientPlatform,
                media_kind: Literal["hls", "file"] = "hls") -> Iterator[tuple[str, _Entry]]:
        with self._lock:
            self._prune()
            if (len(self._entries) >= _MAX_SESSIONS
                    or any(e.owner == owner for e in self._entries.values())):
                raise _http(429)
            key = secrets.token_urlsafe(24)
            if key in self._entries:
                raise _http(503)
            entry = _Entry(owner, platform, time.monotonic(), media_kind=media_kind)
            self._entries[key] = entry
        committed = False
        try:
            yield key, entry
            committed = (entry.source is not None and entry.player is not None
                         and bool(entry.grant_digest) and time.monotonic() < entry.grant_expires)
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

    def _match(self, key: str, grant: str, media_kind: Literal["hls", "file"]) -> _Entry:
        self._prune()
        entry = self._entries.get(key)
        if (entry is None or entry.media_kind != media_kind or len(grant) != 43 or not entry.grant_digest
                or not hmac.compare_digest(entry.grant_digest, _digest(grant))
                or time.monotonic() >= entry.grant_expires):
            raise _http(404)
        if entry.busy:
            raise _http(429)
        if entry.player is None or entry.source is None:
            raise _http(404)
        return entry

    @contextmanager
    def borrow(self, key: str, grant: str,
               media_kind: Literal["hls", "file"] = "hls") -> Iterator[_Entry]:
        with self._lock:
            entry = self._match(key, grant, media_kind)
            entry.busy = True
        success = False
        try:
            yield entry
            success = True
        finally:
            with self._lock:
                if self._entries.get(key) is entry:
                    entry.busy = False
                    if success and time.monotonic() < entry.grant_expires:
                        entry.seen = time.monotonic()
                    else:
                        self._entries.pop(key, None)
                        self._dispose(entry)

    def close(self, key: str, grant: str, media_kind: Literal["hls", "file"] = "hls") -> None:
        with self._lock:
            entry = self._match(key, grant, media_kind)
            del self._entries[key]
            self._dispose(entry)


def install_private_hls_routes(app: FastAPI, settings: Settings) -> None:
    registry = PrivateHlsRegistry(settings.session_ttl_seconds)
    app.state.private_hls_registry = registry
    app.state.private_hls_factory = lambda source: PrivateHlsSession(
        source, entitlement=lambda: EntitlementState.UNAVAILABLE,
        allow_authorized_http=settings.private_media_allow_authorized_http,
    )
    app.state.private_ts_factory = lambda source: PrivateTsSession(
        source, entitlement=lambda: EntitlementState.UNAVAILABLE,
        allow_authorized_http=settings.private_media_allow_authorized_http,
        allow_provider_redirects=settings.private_media_follow_provider_redirects,
    )
    app.state.private_file_factory = lambda source: PrivateFileSession(
        source, body_reader=partial(
            open_media_body, allow_authorized_http=settings.private_media_allow_authorized_http,
        ),
    )

    def credential(request: Request) -> str:
        key = settings.private_media_key
        keys = request.headers.getlist("x-vivoapk-media-key")
        auth = request.headers.getlist("authorization")
        if (key is None or len(keys) != 1 or len(keys[0]) > 1024
                or not hmac.compare_digest(key.get_secret_value().encode(), keys[0].encode())):
            raise _http(403)
        if len(auth) != 1 or not auth[0].startswith("Bearer ") or len(auth[0]) > 4120:
            raise _http(401)
        return auth[0][7:]

    def catalogue(request: Request, db: Session) -> CatalogService:
        return CatalogService(
            db, settings, mega_client_factory=lambda: private_mega_client(request.app),
            xtream_client_factory=request.app.state.xtream_client_factory,
            catalog_client_factory=request.app.state.catalog_client_factory,
        )

    def entitlement(service: CatalogService, entry: _Entry):
        def verify() -> EntitlementState:
            source = entry.source
            if source is None or time.monotonic() >= entry.grant_expires:
                return EntitlementState.UNAVAILABLE
            try:
                origin, username, password = service._authorize_mapping(
                    mapping_id=entry.owner, platform=entry.platform,
                    required_live_format=(
                        source.extension if source.asset.kind is CatalogKind.LIVE else None
                    ),
                )
            except CatalogDenied:
                return EntitlementState.DISABLED
            except (CatalogUnavailable, HTTPException):
                return EntitlementState.UNAVAILABLE
            if (origin != source.origin or username != source.username
                    or not hmac.compare_digest(password.encode(),
                                               source.password.get_secret_value().encode())
                    or time.monotonic() >= entry.grant_expires):
                return EntitlementState.DISABLED
            return EntitlementState.ACTIVE
        return verify

    @app.post("/_internal/media/hls/open", include_in_schema=False)
    def open_live(
        payload: OpenLiveRequest, request: Request, token: str = Depends(credential),
        db: Session = Depends(get_db),
    ):
        try:
            owner = mapping_id_for_token(token, settings)
        except CatalogDenied:
            raise _http(401) from None
        with registry.reserve(owner, payload.platform) as (key, entry):
            try:
                entry.source = catalogue(request, db).authorize_media_origin(
                    token=token, asset=MediaAsset(
                        CatalogKind.LIVE, payload.category_id, payload.asset_id,
                    ), platform=payload.platform, live_format=settings.private_live_input_format,
                )
                factory = (request.app.state.private_ts_factory if entry.source.extension == "ts"
                           else request.app.state.private_hls_factory)
                entry.player = factory(entry.source)
                grant = registry.rotate(entry)
            except CatalogDenied:
                raise _http(404) from None
            except (CatalogUnavailable, MediaReadError, ValueError):
                raise _http(503) from None
        return JSONResponse({"session_id": key, **grant,
                             "input_format": entry.source.extension}, headers=_NO_STORE)

    @app.post("/_internal/media/hls/renew", include_in_schema=False)
    def renew(payload: SessionRequest, request: Request, grant: str = Depends(credential),
              db: Session = Depends(get_db)):
        with registry.borrow(payload.session_id, grant) as entry:
            # Never renew an expired/replayed grant or a different asset.
            # Recheck current subscription and authoritative DNS/credentials.
            if entitlement(catalogue(request, db), entry)() is not EntitlementState.ACTIVE:
                raise _http(404)
            rotated = registry.rotate(entry)
        return JSONResponse(rotated, headers=_NO_STORE)

    @app.post("/_internal/media/hls/playlist", include_in_schema=False)
    def playlist(
        payload: SessionRequest, request: Request, grant: str = Depends(credential),
        db: Session = Depends(get_db),
    ):
        with registry.borrow(payload.session_id, grant) as entry:
            if not isinstance(entry.player, PrivateHlsSession):
                raise _http(404)
            try:
                body = entry.player.refresh(
                    entitlement=entitlement(catalogue(request, db), entry),
                )
            except MediaReadError:
                raise _http(404) from None
        return Response(body, media_type="application/vnd.apple.mpegurl", headers=_NO_STORE)

    @app.post("/_internal/media/hls/segment", include_in_schema=False)
    def segment(
        payload: SegmentRequest, request: Request, grant: str = Depends(credential),
        db: Session = Depends(get_db),
    ):
        with registry.borrow(payload.session_id, grant) as entry:
            if not isinstance(entry.player, PrivateHlsSession):
                raise _http(404)
            try:
                body = entry.player.read_segment(
                    payload.segment_id,
                    entitlement=entitlement(catalogue(request, db), entry),
                )
            except MediaReadError:
                raise _http(404) from None
        return Response(body, media_type="video/mp2t", headers=_NO_STORE)

    def ts_operation(action: str, payload: SessionRequest, request: Request,
                     grant: str, db: Session):
        with registry.borrow(payload.session_id, grant) as entry:
            if not isinstance(entry.player, PrivateTsSession):
                raise _http(404)
            verify = entitlement(catalogue(request, db), entry)
            try:
                if action == "reset":
                    entry.player.reset(entitlement=verify)
                    return Response(status_code=204, headers=_NO_STORE)
                body = (entry.player.probe(entitlement=verify) if action == "probe"
                        else entry.player.read(entitlement=verify))
            except MediaReadError:
                raise _http(404) from None
        return Response(body, media_type="video/mp2t", headers=_NO_STORE)

    @app.post("/_internal/media/hls/ts-probe", include_in_schema=False)
    def ts_probe(payload: SessionRequest, request: Request, grant: str = Depends(credential),
                 db: Session = Depends(get_db)):
        return ts_operation("probe", payload, request, grant, db)

    @app.post("/_internal/media/hls/ts-read", include_in_schema=False)
    def ts_read(payload: SessionRequest, request: Request, grant: str = Depends(credential),
                db: Session = Depends(get_db)):
        return ts_operation("read", payload, request, grant, db)

    @app.post("/_internal/media/hls/ts-reset", include_in_schema=False)
    def ts_reset(payload: SessionRequest, request: Request, grant: str = Depends(credential),
                 db: Session = Depends(get_db)):
        return ts_operation("reset", payload, request, grant, db)

    @app.post("/_internal/media/hls/close", include_in_schema=False)
    def close(payload: SessionRequest, grant: str = Depends(credential)):
        registry.close(payload.session_id, grant)
        return Response(status_code=204, headers=_NO_STORE)


    # Finite media stays on the existing opt-in private service, using the same
    # account JWT admission, independent media key, grant lifecycle and shared
    # limit. No external URL or raw provider data is accepted or returned.
    @app.post("/_internal/media/file/open", include_in_schema=False)
    def open_file(payload: OpenFileRequest, request: Request, token: str = Depends(credential),
                  db: Session = Depends(get_db)):
        try:
            asset = MediaAsset(CatalogKind(payload.kind), payload.category_id,
                               payload.asset_id, payload.series_id)
        except ValueError:
            raise _http(422) from None
        try:
            owner = mapping_id_for_token(token, settings)
        except CatalogDenied:
            raise _http(401) from None
        with registry.reserve(owner, payload.platform, "file") as (key, entry):
            try:
                service = catalogue(request, db)
                entry.source = service.authorize_media_origin(
                    token=token, asset=asset, platform=payload.platform,
                )
                entry.player = request.app.state.private_file_factory(entry.source)
                grant = registry.rotate(entry)
                info = entry.player.probe(entitlement=entitlement(service, entry))
            except CatalogDenied:
                raise _http(404) from None
            except (CatalogUnavailable, MediaReadError, ValueError):
                raise _http(503) from None
        return JSONResponse({"session_id": key, **grant, "length": info.length,
                             "content_type": info.content_type}, headers=_NO_STORE)

    @app.post("/_internal/media/file/read", include_in_schema=False)
    def read_file(payload: FileReadRequest, request: Request, grant: str = Depends(credential),
                  db: Session = Depends(get_db)):
        with registry.borrow(payload.session_id, grant, "file") as entry:
            assert isinstance(entry.player, PrivateFileSession)
            try:
                window = entry.player.read(payload.start, payload.length,
                    entitlement=entitlement(catalogue(request, db), entry))
            except MediaReadError:
                raise _http(404) from None
        return Response(window.data, status_code=206, media_type=window.content_type,
                        headers={**_NO_STORE, "Accept-Ranges": "bytes",
                                 "Content-Range": f"bytes {window.start}-{window.end}/{window.total}"})

    @app.post("/_internal/media/file/renew", include_in_schema=False)
    def renew_file(payload: SessionRequest, request: Request, grant: str = Depends(credential),
                   db: Session = Depends(get_db)):
        with registry.borrow(payload.session_id, grant, "file") as entry:
            if entitlement(catalogue(request, db), entry)() is not EntitlementState.ACTIVE:
                raise _http(404)
            rotated = registry.rotate(entry)
        return JSONResponse(rotated, headers=_NO_STORE)

    @app.post("/_internal/media/file/close", include_in_schema=False)
    def close_file(payload: SessionRequest, grant: str = Depends(credential)):
        registry.close(payload.session_id, grant, "file")
        return Response(status_code=204, headers=_NO_STORE)


