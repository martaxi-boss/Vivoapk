"""Bounded private media-body reader, not wired to a public route.

Unlike the catalogue transport, this does not buffer the entire response.
TLS verification for HTTPS, public IP pinning, strict single-range response validation
and explicit cleanup are mandatory. Unverified redirects and HLS playlists
fail closed. No actual provider access is enabled by adding this module.
"""
from __future__ import annotations

import re
import ssl
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpcore
import httpx

from app.outbound_transport import PinnedIPNetworkBackend
from app.services.media_origin import AuthorizedMediaOrigin
from app.url_safety import (
    DNSResolutionFailure,
    ResolvedOutboundTarget,
    Resolver,
    UnsafeOutboundURL,
    resolve_dns_link_target,
    system_resolver,
)

_RANGE = re.compile(r"bytes=([0-9]{1,18})-([0-9]{0,18})\Z")
_CONTENT_RANGE = re.compile(r"bytes ([0-9]{1,18})-([0-9]{1,18})/([0-9]{1,18})\Z")
_DECIMAL = re.compile(r"[0-9]{1,18}\Z")
_ALLOWED_TYPES = frozenset({
    "video/mp4", "video/mp2t", "video/x-matroska", "video/webm", "video/quicktime",
    "video/x-msvideo", "video/mpeg",
})


def _media_headers(accept: bytes) -> list[tuple[bytes, bytes]]:
    # Truthful product identification already used by account/catalogue requests.
    # Fixed server-owned value: no caller input or impersonation of another app.
    return [(b"Accept", accept), (b"Accept-Encoding", b"identity"),
            (b"User-Agent", b"Vivoapk/1.0")]


class MediaReadError(RuntimeError):
    """Sanitized failure; never include a request URL or upstream text."""


@dataclass(frozen=True, slots=True)
class MediaBody:
    content_type: str
    content_length: int | None
    content_range: str | None
    chunks: Iterator[bytes]
    etag: str | None = None


def _require_media_transport(source: AuthorizedMediaOrigin, *, allow_authorized_http: bool) -> None:
    # Source is server-owned, resolved from an authenticated provider subscription.
    # HTTP is an explicit compatibility policy, not a TLS downgrade or fallback.
    scheme = urlsplit(source.origin).scheme
    if scheme != "https" and not (scheme == "http" and allow_authorized_http is True):
        raise MediaReadError("Secure media origin not available")


def _default_pool(target: ResolvedOutboundTarget) -> httpcore.ConnectionPool:
    tls = httpx.create_ssl_context(verify=True, trust_env=False)
    if tls.verify_mode == ssl.CERT_NONE or not tls.check_hostname:
        raise MediaReadError("TLS verification unavailable")
    return httpcore.ConnectionPool(
        ssl_context=tls, http1=True, http2=False, retries=0,
        max_connections=1, max_keepalive_connections=0,
        network_backend=PinnedIPNetworkBackend(
            hostname=target.hostname, port=target.port, addresses=target.addresses,
        ),
    )


def _range(value: str | None) -> tuple[int, int | None] | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MediaReadError("Invalid media range")
    match = _RANGE.fullmatch(value)
    if match is None:
        raise MediaReadError("Invalid media range")
    start, end = int(match[1]), int(match[2]) if match[2] else None
    if end is not None and end < start:
        raise MediaReadError("Invalid media range")
    return start, end


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    matches = [value for key, value in headers if key.lower() == name]
    if not matches:
        return None
    if len(matches) != 1:
        raise MediaReadError("Ambiguous media response")
    try:
        value = matches[0].decode("ascii")
    except UnicodeError:
        raise MediaReadError("Invalid media response") from None
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or len(value) > 256:
        raise MediaReadError("Invalid media response")
    return value.strip()


@contextmanager
def open_media_body(
    source: AuthorizedMediaOrigin,
    *,
    range_header: str | None = None,
    allow_authorized_http: bool = False,
    if_match: str | None = None,
    max_bytes: int = 64 * 1024 * 1024,
    max_seconds: float = 60.0,
    resolver: Resolver = system_resolver,
    pool_factory: Callable[[ResolvedOutboundTarget], httpcore.ConnectionPool] = _default_pool,
) -> Iterator[MediaBody]:
    # A bounded primitive for the next integration gate, not an unlimited VOD
    # or live transfer. Duration/byte limits do not certify an OS quota.
    if not 1 <= max_bytes <= 128 * 1024 * 1024 or not 0 < max_seconds <= 120:
        raise MediaReadError("Invalid media read bounds")
    if source.extension == "m3u8":
        raise MediaReadError("Manifest delivery not implemented")
    _require_media_transport(source, allow_authorized_http=allow_authorized_http)
    requested_range = _range(range_header)
    headers = _media_headers(b"video/*")
    if range_header is not None:
        headers.append((b"Range", range_header.encode("ascii")))
    # Only the server-owned, previously observed strong validator is passed
    # by the bounded file reader. Never forward a caller-supplied header.
    if if_match is not None:
        if not re.fullmatch(r'"[\x21\x23-\x7e]{1,240}"', if_match):
            raise MediaReadError("Invalid private media validator")
        headers.append((b"If-Match", if_match.encode("ascii")))
    begin = time.monotonic()
    try:
        target = resolve_dns_link_target(source.origin, resolver=resolver)
        with closing(pool_factory(target)) as pool:
            with pool.stream(
                "GET", source._request_url(), headers=headers,
                extensions={"timeout": {"connect": 5.0, "read": 10.0,
                                         "write": 5.0, "pool": 5.0}},
            ) as response:
                if response.status != (206 if requested_range else 200):
                    raise MediaReadError("Unexpected media response status")
                mime = _header(response.headers, b"content-type")
                if mime is None or mime.lower() not in _ALLOWED_TYPES:
                    raise MediaReadError("Unexpected media content type")
                encoding = _header(response.headers, b"content-encoding")
                if encoding is not None and encoding.lower() != "identity":
                    raise MediaReadError("Unexpected media content encoding")
                length_text = _header(response.headers, b"content-length")
                if length_text is not None and not _DECIMAL.fullmatch(length_text):
                    raise MediaReadError("Invalid media content length")
                length = int(length_text) if length_text is not None else None
                content_range = _header(response.headers, b"content-range")
                validator = _header(response.headers, b"etag")
                if validator is not None and not re.fullmatch(
                        r'(?:W/)?"[\x21\x23-\x7e]{1,240}"', validator):
                    raise MediaReadError("Invalid private media validator")
                if if_match is not None and validator != if_match:
                    raise MediaReadError("Private media representation changed")
                if requested_range:
                    match = _CONTENT_RANGE.fullmatch(content_range or "")
                    if match is None:
                        raise MediaReadError("Missing media content range")
                    start, end, total = map(int, match.groups())
                    expected_start, expected_end = requested_range
                    if (start != expected_start or end < start or end >= total
                            or (expected_end is not None and end > expected_end)
                            or length != end - start + 1):
                        raise MediaReadError("Mismatched media content range")
                elif content_range is not None:
                    raise MediaReadError("Unrequested media content range")
                if length is not None and not 1 <= length <= max_bytes:
                    raise MediaReadError("Media response exceeds bounded read")
                def chunks() -> Iterator[bytes]:
                    received = 0
                    for block in response.iter_stream():
                        if not isinstance(block, bytes):
                            raise MediaReadError("Invalid media body")
                        if not block:
                            continue
                        received += len(block)
                        if (time.monotonic() - begin > max_seconds or received > max_bytes
                                or (length is not None and received > length)):
                            raise MediaReadError("Media read bound exceeded")
                        # Never accumulate a full asset or write it to disk.
                        for offset in range(0, len(block), 65536):
                            yield block[offset:offset + 65536]
                    if not received or (length is not None and received != length):
                        raise MediaReadError("Incomplete media body")

                yield MediaBody(mime.lower(), length, content_range, chunks(), validator)
    except (UnsafeOutboundURL, DNSResolutionFailure, httpcore.NetworkError,
            httpcore.TimeoutException, httpcore.ProtocolError, ValueError, OSError):
        raise MediaReadError("Private media read unavailable") from None
