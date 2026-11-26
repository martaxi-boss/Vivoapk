"""Private, bounded HLS reader for an already authorized live asset.

No public route, new listener, disk cache, decryption or provider access at
import time. Only clear, muxed MPEG-TS playlists on the exact authorized origin
are supported (HTTPS by default, HTTP only by explicit server policy). Other HLS features and redirects fail closed. Output
contains session-local random handles instead of any upstream URL.
"""
from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from threading import Lock
from urllib.parse import urljoin, urlsplit

import httpcore

from app.schemas import CatalogKind, EntitlementState
from app.services.media_body import MediaReadError, _default_pool, _header, _require_media_transport
from app.services.media_origin import AuthorizedMediaOrigin
from app.url_safety import (
    DNSResolutionFailure,
    ResolvedOutboundTarget,
    Resolver,
    UnsafeOutboundURL,
    resolve_dns_link_target,
    system_resolver,
)

_MANIFEST_CAP = 128 * 1024
_SEGMENT_CAP = 8 * 1024 * 1024
_MAX_WINDOW = 48
_GRACE = 8
_GRACE_SECONDS = 45
_MAX_SEQUENCE = (1 << 53) - 1
_MAX_BANDWIDTH = 8_000_000
_MANIFEST_TYPES = {
    "application/vnd.apple.mpegurl", "application/x-mpegurl",
    "audio/mpegurl", "audio/x-mpegurl",
}
_SEGMENT_TYPES = {"video/mp2t", "application/octet-stream"}
_INT = re.compile(r"[0-9]{1,16}\Z")
_DURATION = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,6})?\Z")
_HANDLE = re.compile(r"[A-Za-z0-9_-]{32}\Z")
_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"\r\n]*"|[^,"\s]+)(?:,|\Z)')
_BAD_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})|%(?:2e|2f|5c|25|00|0a|0d)", re.I)


def _reject() -> MediaReadError:
    return MediaReadError("Unsupported or unsafe private HLS response")


def _origin_key(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        raise _reject()
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise _reject()
    return parsed.scheme, parsed.hostname.casefold(), port


def _safe_uri(base: str, raw: str, origin: str) -> str:
    # Never use an address provided by a Jellyfin client. Only the authoritative
    # root or a URI discovered in a validated upstream playlist reaches here.
    if (not raw or len(raw) > 4096 or "\\" in raw
            or any(ord(c) < 33 or ord(c) > 126 for c in raw)):
        raise _reject()
    try:
        relative = urlsplit(raw)
        if (relative.fragment or raw.startswith("//") or relative.username is not None
                or (relative.scheme and not relative.netloc)
                or any(part in {".", ".."} for part in relative.path.split("/"))
                or _BAD_PERCENT.search(relative.path)):
            raise _reject()
        result = urljoin(base, raw)
        parsed = urlsplit(result)
        if (_origin_key(result) != _origin_key(origin) or not parsed.path.startswith("/")
                or len(result) > 4096 or parsed.fragment
                or _BAD_PERCENT.search(parsed.path)):
            raise _reject()
        return result
    except ValueError:
        raise _reject() from None


def _integer(value: str, *, limit: int = _MAX_SEQUENCE) -> int:
    if not _INT.fullmatch(value):
        raise _reject()
    result = int(value)
    if result > limit:
        raise _reject()
    return result


def _lines(payload: bytes) -> list[str]:
    if not payload or len(payload) > _MANIFEST_CAP:
        raise _reject()
    try:
        lines = payload.decode("utf-8", errors="strict").split("\n")
    except UnicodeError:
        raise _reject() from None
    lines = [line.removesuffix("\r") for line in lines]
    if (len(lines) > 512 or lines[0] != "#EXTM3U"
            or any(len(line) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in line)
                   for line in lines)):
        raise _reject()
    return [line for line in lines[1:] if line]


def _attributes(value: str) -> dict[str, str]:
    result = {}
    offset = 0
    while offset < len(value):
        match = _ATTR.match(value, offset)
        if match is None or match[1] in result or len(result) >= 12:
            raise _reject()
        result[match[1]] = match[2]
        offset = match.end()
    if not result or value.endswith(","):
        raise _reject()
    return result


def _master_variant(lines: list[str], base: str, origin: str) -> str:
    choices = []
    pending = None
    metadata = set()
    for line in lines:
        if pending is not None:
            if line.startswith("#"):
                raise _reject()
            choices.append((pending, _safe_uri(base, line, origin)))
            pending = None
            if len(choices) > 16:
                raise _reject()
        elif line.startswith("#EXT-X-STREAM-INF:"):
            attrs = _attributes(line.split(":", 1)[1])
            # No external audio/subtitle groups, encryption, iframe playlists
            # or alternate-rendition URLs. FFmpeg receives one muxed variant.
            allowed = {"BANDWIDTH", "AVERAGE-BANDWIDTH", "RESOLUTION", "FRAME-RATE",
                       "CODECS", "VIDEO-RANGE", "CLOSED-CAPTIONS"}
            if set(attrs) - allowed or attrs.get("CLOSED-CAPTIONS", "NONE") != "NONE":
                raise _reject()
            pending = _integer(attrs.get("BANDWIDTH", ""), limit=100_000_000)
            if pending < 1:
                raise _reject()
        elif line.startswith("#EXT-X-VERSION:"):
            if "version" in metadata or not 1 <= _integer(line.split(":", 1)[1], limit=7):
                raise _reject()
            metadata.add("version")
        elif line == "#EXT-X-INDEPENDENT-SEGMENTS":
            if line in metadata:
                raise _reject()
            metadata.add(line)
        else:
            raise _reject()
    viable = [choice for choice in choices if choice[0] <= _MAX_BANDWIDTH]
    if pending is not None or not viable:
        raise _reject()
    return max(viable, key=lambda choice: choice[0])[1]


@dataclass(frozen=True, slots=True, repr=False)
class _Segment:
    sequence: int
    duration: Decimal
    uri: str
    discontinuity: bool


@dataclass(frozen=True, slots=True, repr=False)
class _Window:
    sequence: int
    target: int
    discontinuity_sequence: int
    ended: bool
    segments: tuple[_Segment, ...]


def _media_window(lines: list[str], base: str, origin: str) -> _Window:
    values: dict[str, int] = {}
    entries: list[_Segment] = []
    pending: Decimal | None = None
    discontinuity = ended = False
    for line in lines:
        if ended:
            raise _reject()
        if pending is not None:
            if line.startswith("#"):
                raise _reject()
            sequence = values.get("MEDIA-SEQUENCE", 0) + len(entries)
            if sequence > _MAX_SEQUENCE or len(entries) >= _MAX_WINDOW:
                raise _reject()
            entries.append(_Segment(sequence, pending, _safe_uri(base, line, origin),
                                    discontinuity))
            pending = None
            discontinuity = False
            continue
        if line.startswith("#EXTINF:"):
            duration, sep, _title = line.split(":", 1)[1].partition(",")
            if not sep or not _DURATION.fullmatch(duration):
                raise _reject()
            pending = Decimal(duration)
            if not 0 < pending <= 120:
                raise _reject()
        elif line.startswith("#EXT-X-"):
            tag, sep, value = line[7:].partition(":")
            if tag in {"VERSION", "TARGETDURATION", "MEDIA-SEQUENCE", "DISCONTINUITY-SEQUENCE"}:
                if not sep or entries or discontinuity or tag in values:
                    raise _reject()
                limit = 120 if tag == "TARGETDURATION" else 7 if tag == "VERSION" else _MAX_SEQUENCE
                values[tag] = _integer(value, limit=limit)
                if tag in {"TARGETDURATION", "VERSION"} and values[tag] == 0:
                    raise _reject()
            elif tag == "DISCONTINUITY" and not sep and not discontinuity:
                discontinuity = True
            elif tag == "ENDLIST" and not sep and not discontinuity:
                ended = True
            elif tag == "INDEPENDENT-SEGMENTS" and not sep and not entries:
                if tag in values:
                    raise _reject()
                values[tag] = 1
            elif tag == "KEY" and sep and value == "METHOD=NONE":
                # No key acquisition or encrypted/DRM playback.
                pass
            elif tag == "PROGRAM-DATE-TIME" and sep and len(value) <= 80:
                # Optional display metadata is never forwarded.
                pass
            elif tag == "PLAYLIST-TYPE" and sep and value in {"EVENT", "VOD"} and not entries:
                if tag in values:
                    raise _reject()
                values[tag] = 1
            else:
                raise _reject()
        else:
            raise _reject()
    target = values.get("TARGETDURATION", 0)
    if (pending is not None or discontinuity or not entries or not target
            or any(e.duration > Decimal(target) + Decimal("0.5") for e in entries)):
        raise _reject()
    return _Window(values.get("MEDIA-SEQUENCE", 0), target,
                   values.get("DISCONTINUITY-SEQUENCE", 0), ended, tuple(entries))


@dataclass(slots=True, repr=False)
class _HandleEntry:
    segment: _Segment
    handle: str
    seen: float


class PrivateHlsSession:
    """One account/asset only. Callers must bind this object to the verified
    Jellyfin owner and private transport; handles alone are not authorization.
    No public endpoint is registered. Each request rechecks entitlement via
    the injected private callback. A future integration needs global admission
    bounds and a real provider-policy/transport review.
    """
    def __init__(
        self,
        source: AuthorizedMediaOrigin,
        *,
        entitlement: Callable[[], EntitlementState],
        allow_authorized_http: bool = False,
        resolver: Resolver = system_resolver,
        pool_factory: Callable[[ResolvedOutboundTarget], httpcore.ConnectionPool] = _default_pool,
    ) -> None:
        if source.asset.kind is not CatalogKind.LIVE or source.extension != "m3u8":
            raise _reject()
        _require_media_transport(source, allow_authorized_http=allow_authorized_http)
        self._source = source
        self._entitlement = entitlement
        self._resolver = resolver
        self._pool_factory = pool_factory
        self._root = _safe_uri(source.origin, source._request_url(), source.origin)
        self._variant: str | None = None
        self._entries: dict[int, _HandleEntry] = {}
        self._sequence: int | None = None
        self._lock = Lock()
        self._closed = False

    def _deny(self) -> None:
        self._closed = True
        self._entries.clear()
        self._variant = None
        self._source = None  # type: ignore[assignment]
        self._root = ""
        raise MediaReadError("Private HLS session unavailable")

    def _check(self, entitlement: Callable[[], EntitlementState] | None = None) -> None:
        if self._closed:
            self._deny()
        try:
            active = (entitlement or self._entitlement)() is EntitlementState.ACTIVE
        except Exception:
            active = False
        if not active:
            self._deny()

    def _fetch(self, uri: str, *, manifest: bool) -> bytes:
        uri = _safe_uri(self._root, uri, self._source.origin)
        cap = _MANIFEST_CAP if manifest else _SEGMENT_CAP
        types = _MANIFEST_TYPES if manifest else _SEGMENT_TYPES
        accept = b"application/vnd.apple.mpegurl, application/x-mpegurl" if manifest else b"video/mp2t"
        begin = time.monotonic()
        try:
            target = resolve_dns_link_target(self._source.origin, resolver=self._resolver)
            with closing(self._pool_factory(target)) as pool:
                with pool.stream(
                    "GET", uri,
                    headers=[(b"Accept", accept), (b"Accept-Encoding", b"identity")],
                    extensions={"timeout": {"connect": 5.0, "read": 5.0,
                                             "write": 5.0, "pool": 5.0}},
                ) as response:
                    # No redirect, upstream URL or arbitrary HTTP headers are
                    # forwarded, including Set-Cookie, Location and auth headers.
                    if response.status != 200:
                        raise _reject()
                    mime = _header(response.headers, b"content-type")
                    encoding = _header(response.headers, b"content-encoding")
                    if (mime is None or mime.lower() not in types
                            or (encoding is not None and encoding.lower() != "identity")
                            or _header(response.headers, b"content-range") is not None):
                        raise _reject()
                    length = _header(response.headers, b"content-length")
                    expected = _integer(length, limit=cap) if length is not None else None
                    body = bytearray()
                    for block in response.iter_stream():
                        if (not isinstance(block, bytes) or len(body) + len(block) > cap
                                or time.monotonic() - begin > 15):
                            raise _reject()
                        body.extend(block)
                    if (not body or (expected is not None and len(body) != expected)
                            or time.monotonic() - begin > 15):
                        raise _reject()
                    return bytes(body)
        except (UnsafeOutboundURL, DNSResolutionFailure, httpcore.NetworkError,
                httpcore.TimeoutException, httpcore.ProtocolError, ValueError, OSError):
            raise MediaReadError("Private HLS read unavailable") from None

    def refresh(self, *, entitlement: Callable[[], EntitlementState] | None = None) -> str:
        with self._lock:
            self._check(entitlement)
            uri = self._variant or self._root
            lines = _lines(self._fetch(uri, manifest=True))
            if any(line.startswith("#EXT-X-STREAM-INF:") for line in lines):
                if self._variant is not None:
                    raise _reject()
                uri = _master_variant(lines, uri, self._source.origin)
                self._check(entitlement)
                lines = _lines(self._fetch(uri, manifest=True))
                variant = uri
            else:
                variant = self._variant
            window = _media_window(lines, uri, self._source.origin)
            self._check(entitlement)
            if self._sequence is not None and window.sequence < self._sequence:
                raise _reject()
            now = time.monotonic()
            kept = {
                seq: entry for seq, entry in self._entries.items()
                if window.sequence - _GRACE <= seq < window.sequence
                and now - entry.seen <= _GRACE_SECONDS
            }
            result = [
                "#EXTM3U", "#EXT-X-VERSION:3",
                f"#EXT-X-TARGETDURATION:{window.target}",
                f"#EXT-X-MEDIA-SEQUENCE:{window.sequence}",
                f"#EXT-X-DISCONTINUITY-SEQUENCE:{window.discontinuity_sequence}",
            ]
            for segment in window.segments:
                previous = self._entries.get(segment.sequence)
                if previous is not None and previous.segment != segment:
                    raise _reject()
                handle = previous.handle if previous is not None else secrets.token_urlsafe(24)
                if segment.discontinuity:
                    result.append("#EXT-X-DISCONTINUITY")
                result.extend([f"#EXTINF:{segment.duration:f},", f"segment/{handle}.ts"])
                kept[segment.sequence] = _HandleEntry(segment, handle, now)
            if len(kept) > _MAX_WINDOW + _GRACE:
                raise _reject()
            if window.ended:
                result.append("#EXT-X-ENDLIST")
            self._entries, self._sequence, self._variant = kept, window.sequence, variant
            return "\n".join(result) + "\n"

    def read_segment(self, handle: str, *,
                     entitlement: Callable[[], EntitlementState] | None = None) -> bytes:
        with self._lock:
            self._check(entitlement)
            if not isinstance(handle, str) or not _HANDLE.fullmatch(handle):
                raise _reject()
            entry = next((e for e in self._entries.values() if e.handle == handle), None)
            if entry is None or time.monotonic() - entry.seen > _GRACE_SECONDS:
                raise _reject()
            body = self._fetch(entry.segment.uri, manifest=False)
            # TS packet boundaries, not merely an upstream Content-Type claim.
            if len(body) < 188 * 3 or len(body) % 188 or any(
                    body[i] != 0x47 for i in range(0, len(body), 188)):
                raise _reject()
            self._check(entitlement)
            return body

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._entries.clear()
            self._variant = None
            self._source = None  # type: ignore[assignment]
            self._root = ""
