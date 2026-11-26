"""Private continuous TS input; no decoder, disk cache or format fallback.

Only a server-authorized TS source may be admitted. Probe and playback use
sequential connections, never two simultaneous provider connections. Every
bounded operation checks entitlement before reading and before delivery.
An authorization denial permanently closes this player; reset cannot retry it.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import ExitStack
from threading import Lock


from app.schemas import CatalogKind, EntitlementState
from app.services.media_body import MediaReadError, _default_pool, _header, _require_media_transport
from app.services.media_origin import AuthorizedMediaOrigin
from app.url_safety import resolve_dns_link_target, system_resolver

WINDOW_BYTES = 188 * 8192
PROBE_BYTES = 188 * 2048
_LOG = logging.getLogger(__name__)


class PrivateTsSession:
    def __init__(self, source: AuthorizedMediaOrigin, *,
                 entitlement: Callable[[], EntitlementState],
                 allow_authorized_http: bool = False, resolver=system_resolver,
                 pool_factory=_default_pool) -> None:
        if source.asset.kind is not CatalogKind.LIVE or source.extension != "ts":
            raise MediaReadError("Continuous TS source unavailable")
        _require_media_transport(source, allow_authorized_http=allow_authorized_http)
        self._source = source
        self._entitlement = entitlement
        self._resolver = resolver
        self._pool_factory = pool_factory
        self._lock = Lock()
        self._stack: ExitStack | None = None
        self._iterator = None
        self._pending = bytearray()
        self._closed = False
        self._probed = False
        self._started = False

    def _disconnect(self) -> None:
        stack, self._stack = self._stack, None
        self._iterator = None
        self._pending.clear()
        if stack is not None:
            stack.close()

    def _deny(self) -> None:
        self._closed = True
        self._source = None
        self._disconnect()
        raise MediaReadError("Continuous TS unavailable")

    def _check(self, entitlement) -> None:
        if self._closed:
            self._deny()
        try:
            active = (entitlement or self._entitlement)() is EntitlementState.ACTIVE
        except Exception:
            active = False
        if not active:
            self._deny()

    def _connect(self) -> None:
        target = resolve_dns_link_target(self._source.origin, resolver=self._resolver)
        stack = ExitStack()
        self._stack = stack
        pool = self._pool_factory(target)
        stack.callback(pool.close)
        response = stack.enter_context(pool.stream(
            "GET", self._source._request_url(),
            headers=[(b"Accept", b"video/mp2t"), (b"Accept-Encoding", b"identity")],
            extensions={"timeout": {"connect": 5.0, "read": 8.0,
                                     "write": 5.0, "pool": 5.0}},
        ))
        if response.status in (401, 403):
            _LOG.warning("private_ts_origin_denied status=%d", response.status)
            # No body, Location, WWW-Authenticate or provider message is read.
            self._deny()
        if (response.status != 200
                or (_header(response.headers, b"content-type") or "").lower()
                    not in {"video/mp2t", "application/octet-stream"}
                or _header(response.headers, b"content-range") is not None
                or _header(response.headers, b"content-encoding") not in (None, "identity")
                or _header(response.headers, b"content-length") is not None):
            self._deny()
        self._iterator = iter(response.iter_stream())

    def _window(self, cap: int) -> bytes:
        if self._iterator is None:
            self._connect()
        started = time.monotonic()
        body = bytearray()
        while len(body) < cap:
            if self._pending:
                take = min(cap - len(body), len(self._pending))
                body.extend(self._pending[:take])
                del self._pending[:take]
            aligned = len(body) // 188 * 188
            if aligned >= 188 * 3 and (len(body) >= cap or time.monotonic() - started >= 1):
                self._pending[:0] = body[aligned:]
                del body[aligned:]
                break
            try:
                block = next(self._iterator)
            except StopIteration:
                self._deny()
            if (not isinstance(block, bytes) or not block or len(block) > WINDOW_BYTES
                    or time.monotonic() - started > 15):
                self._deny()
            self._pending.extend(block)
        if (not body or len(body) % 188 or any(
                body[n] != 0x47 or body[n + 3] & 0xC0
                for n in range(0, len(body), 188))):
            # No encrypted/scrambled input or resynchronization guesswork.
            self._deny()
        return bytes(body)

    def _operation(self, entitlement, *, probe: bool) -> bytes:
        with self._lock:
            try:
                self._check(entitlement)
                if probe:
                    if self._probed or self._started:
                        self._deny()
                else:
                    if not self._probed:
                        self._deny()
                    self._started = True
                body = self._window(PROBE_BYTES if probe else WINDOW_BYTES)
                self._check(entitlement)
                if probe:
                    self._probed = True
                    self._disconnect()
                return body
            except MediaReadError:
                self._disconnect()
                raise
            except Exception:
                self._closed = True
                self._source = None
                self._disconnect()
                raise MediaReadError("Continuous TS read unavailable") from None

    def probe(self, *, entitlement=None) -> bytes:
        return self._operation(entitlement, probe=True)

    def read(self, *, entitlement=None) -> bytes:
        return self._operation(entitlement, probe=False)

    def reset(self, *, entitlement=None) -> None:
        # Native encoder restart/reconnect may close an accepted stream. It
        # never changes input contract or reopens an authorization denial.
        with self._lock:
            self._check(entitlement)
            self._disconnect()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._source = None
            self._disconnect()
