"""Private media origin contract. Not an HTTP endpoint or production relay.

Reuse the authoritative, account-scoped catalogue resolver. These objects stay
in backend memory; no origin/password is returned to a Jellyfin client or
placed into an FFmpeg command line. The initial bounded body reader is for
non-manifest media only; HLS, redirects, live entitlement renewal, and actual
delivery through the isolated Jellyfin runtime are separate acceptance gates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from urllib.parse import quote, urlsplit

from pydantic import SecretStr

from app.schemas import CatalogKind
from app.url_safety import validate_dns_link

_ID = re.compile(r"[0-9]{1,64}\Z")
_FILE_EXTENSIONS = frozenset({"mp4", "m4v", "mkv", "mov", "avi", "webm", "ts", "mpg", "mpeg"})


@dataclass(frozen=True, slots=True)
class MediaAsset:
    kind: CatalogKind
    category_id: str
    asset_id: str
    series_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CatalogKind):
            raise ValueError("Invalid media kind")
        for value in (self.category_id, self.asset_id):
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise ValueError("Invalid media identity")
        if self.kind is CatalogKind.SERIES:
            if not isinstance(self.series_id, str) or not _ID.fullmatch(self.series_id):
                raise ValueError("Invalid series identity")
        elif self.series_id is not None:
            raise ValueError("Unexpected series identity")


def _component(value: str) -> str:
    # Disallow ambiguous path delimiters, encoded separators and dot segments.
    # No provider-specific normalization or credential reconstruction.
    if (not value or len(value) > 512 or value in {".", ".."}
            or any(c in "/\\%" or ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("Invalid private origin component")
    return quote(value, safe="")


@dataclass(frozen=True, slots=True, repr=False)
class AuthorizedMediaOrigin:
    asset: MediaAsset
    origin: str
    username: str
    password: SecretStr
    extension: str | None
    _provider_live_url: SecretStr | None = None

    def __post_init__(self) -> None:
        validate_dns_link(self.origin)
        _component(self.username)
        _component(self.password.get_secret_value())
        if self.asset.kind is CatalogKind.LIVE:
            if self.extension not in {"m3u8", "ts"}:
                raise ValueError("Unverified live media format")
        elif self.extension not in _FILE_EXTENSIONS:
            raise ValueError("Unverified media format")
        if self._provider_live_url is not None:
            if (self.asset.kind is not CatalogKind.LIVE or self.extension != "ts"
                    or not isinstance(self._provider_live_url, SecretStr)):
                raise ValueError("Invalid advertised live source")
            raw = self._provider_live_url.get_secret_value()
            if (len(raw) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in raw)
                    or "\\" in raw or "?" in raw or "#" in raw):
                raise ValueError("Invalid advertised live source")
            parsed, assigned = urlsplit(raw), urlsplit(self.origin)
            def key(p):
                return p.scheme, p.hostname, p.port or (443 if p.scheme == "https" else 80)
            user, password = _component(self.username), _component(self.password.get_secret_value())
            paths = {f"/{user}/{password}/{self.asset.asset_id}",
                     f"/live/{user}/{password}/{self.asset.asset_id}.ts"}
            if (parsed.username is not None or parsed.port == 0 or key(parsed) != key(assigned)
                    or parsed.path not in paths):
                raise ValueError("Advertised source does not match authorized asset")

    def with_provider_live_url(self, url: str) -> AuthorizedMediaOrigin:
        # Only the internal, authenticated playlist lookup supplies this value.
        return replace(self, _provider_live_url=SecretStr(url))

    def __repr__(self) -> str:
        return "AuthorizedMediaOrigin(<private>)"

    def _request_url(self) -> str:
        if self._provider_live_url is not None:
            return self._provider_live_url.get_secret_value()
        folder = {
            CatalogKind.LIVE: "live", CatalogKind.VOD: "movie", CatalogKind.SERIES: "series"
        }[self.asset.kind]
        return (
            self.origin.rstrip("/") + "/" + folder + "/" + _component(self.username)
            + "/" + _component(self.password.get_secret_value())
            + "/" + self.asset.asset_id + "." + str(self.extension)
        )

