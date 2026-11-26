"""Bounded private random-access file sessions for authorized VOD and episodes.

No public URL, disk cache, provider credential or arbitrary upstream request is
accepted. Reads reuse the pinned body transport (HTTPS by default) and authenticate before
and after each bounded byte window. The backend's existing session registry
serializes operations and applies the same capacity to live and finite media.
This is not a Jellyfin/FFmpeg delivery adapter yet.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.schemas import CatalogKind, EntitlementState
from app.services.media_body import MediaReadError, open_media_body
from app.services.media_origin import AuthorizedMediaOrigin

MAX_WINDOW_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024**3
_RANGE = re.compile(r"bytes ([0-9]{1,18})-([0-9]{1,18})/([0-9]{1,18})\Z")


@dataclass(frozen=True, slots=True)
class FileInfo:
    length: int
    content_type: str


@dataclass(frozen=True, slots=True)
class FileWindow:
    start: int
    end: int
    total: int
    content_type: str
    data: bytes


class PrivateFileSession:
    def __init__(self, source: AuthorizedMediaOrigin, *, body_reader=open_media_body):
        if source.asset.kind not in (CatalogKind.VOD, CatalogKind.SERIES):
            raise MediaReadError("Finite media required")
        self._source: AuthorizedMediaOrigin | None = source
        self._reader = body_reader
        self._info: FileInfo | None = None
        self._validator: str | None = None

    def close(self) -> None:
        self._source = None
        self._info = None
        self._validator = None

    @staticmethod
    def _require(entitlement: Callable[[], EntitlementState]) -> None:
        if entitlement() is not EntitlementState.ACTIVE:
            raise MediaReadError("Private media unavailable")

    def _window(self, start: int, end: int, entitlement):
        self._require(entitlement)
        if self._source is None:
            raise MediaReadError("Private media unavailable")
        expected_bytes = end - start + 1
        strong = self._validator if self._validator and not self._validator.startswith("W/") else None
        with self._reader(self._source, range_header=f"bytes={start}-{end}",
                          if_match=strong, max_bytes=expected_bytes, max_seconds=30) as body:
            match = _RANGE.fullmatch(body.content_range or "")
            if match is None:
                raise MediaReadError("Missing finite media range")
            first, last, total = map(int, match.groups())
            if (first != start or last != end or not 0 <= first <= last < total <= MAX_FILE_BYTES
                    or body.content_length != expected_bytes):
                raise MediaReadError("Mismatched finite media range")
            if self._info is not None and (total != self._info.length
                    or body.content_type != self._info.content_type or body.etag != self._validator):
                raise MediaReadError("Private media representation changed")
            # Never accumulate more than a bounded window. The lower reader
            # also enforces exact framing, TLS for HTTPS, DNS pinning and a deadline.
            data = bytearray()
            for chunk in body.chunks:
                if len(data) + len(chunk) > expected_bytes:
                    raise MediaReadError("Private media window exceeds bound")
                data.extend(chunk)
            if len(data) != expected_bytes:
                raise MediaReadError("Incomplete finite media range")
            validator = body.etag
            mime = body.content_type
        self._require(entitlement)
        return FileWindow(start, end, total, mime, bytes(data)), validator

    def probe(self, *, entitlement: Callable[[], EntitlementState]) -> FileInfo:
        if self._info is not None or self._source is None:
            raise MediaReadError("Private media unavailable")
        # A one-byte GET establishes exact range support and file length.
        # A provider that ignores Range or redirects is not supported.
        window, validator = self._window(0, 0, entitlement)
        self._info = FileInfo(window.total, window.content_type)
        self._validator = validator
        return self._info

    def read(self, start: int, length: int, *, entitlement: Callable[[], EntitlementState]) -> FileWindow:
        if (self._info is None or self._source is None or type(start) is not int
                or type(length) is not int or not 0 <= start < self._info.length
                or not 1 <= length <= MAX_WINDOW_BYTES):
            raise MediaReadError("Invalid finite media window")
        end = min(start + length, self._info.length) - 1
        window, _ = self._window(start, end, entitlement)
        return window
