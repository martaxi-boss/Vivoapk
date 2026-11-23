"""Read bounded Xtream catalogue metadata from the account's approved public DNS.

No playback URLs, customer passwords, DNS origins or credentials leave this module.
The existing pinned outbound transport prevents private-network and DNS-rebinding hops.
"""
import re
from collections.abc import Callable
from typing import Any

import httpx

from app.outbound_transport import PinnedHTTPTransport
from app.schemas import CatalogKind
from app.url_safety import (
    ResolvedOutboundTarget,
    Resolver,
    UnsafeOutboundURL,
    player_api_url,
    resolve_dns_link_target,
    system_resolver,
)

_MAX_JSON_BYTES = 8 * 1024 * 1024
_MAX_ITEMS = 20000
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_EXTENSION = re.compile(r"^[A-Za-z0-9]{1,12}$")
_ACTIONS = {
    CatalogKind.LIVE: ("get_live_categories", "get_live_streams", "stream_id"),
    CatalogKind.VOD: ("get_vod_categories", "get_vod_streams", "stream_id"),
    CatalogKind.SERIES: ("get_series_categories", "get_series", "series_id"),
}


class CatalogUnavailable(RuntimeError):
    """A provider catalogue cannot be trusted or reached."""


def _clean_id(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    return text if _ID.fullmatch(text) else None


def _clean_title(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if any(ord(c) < 32 for c in value):
        return None
    text = value.strip()
    if not text or len(text) > 250:
        return None
    return text


def _parse_categories(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or len(raw) > _MAX_ITEMS:
        raise CatalogUnavailable("Invalid provider categories")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        item_id = _clean_id(entry.get("category_id"))
        name = _clean_title(entry.get("category_name"))
        if item_id is not None and name is not None and item_id not in seen:
            result.append({"id": item_id, "name": name})
            seen.add(item_id)
    return result


def _parse_items(raw: Any, kind: CatalogKind, id_field: str) -> list[dict[str, str | None]]:
    if not isinstance(raw, list) or len(raw) > _MAX_ITEMS:
        raise CatalogUnavailable("Invalid provider items")
    result: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        item_id = _clean_id(entry.get(id_field))
        title = _clean_title(entry.get("name"))
        if item_id is None or title is None or item_id in seen:
            continue
        category_id = _clean_id(entry.get("category_id"))
        extension = entry.get("container_extension") if kind is CatalogKind.VOD else None
        if extension is not None and (
            not isinstance(extension, str) or not _EXTENSION.fullmatch(extension)
        ):
            extension = None
        result.append({
            "id": item_id,
            "title": title,
            "category_id": category_id,
            "container_extension": extension,
        })
        seen.add(item_id)
    return result


class XtreamCatalogClient:
    """Fetch only safe display metadata using a host/IP-pinned connection."""

    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        transport_factory: Callable[[ResolvedOutboundTarget], httpx.BaseTransport] | None = None,
    ) -> None:
        self._resolver = resolver
        self._transport_factory = transport_factory

    @staticmethod
    def _read(client: httpx.Client, origin: str, credentials: dict[str, str], action: str) -> Any:
        try:
            response = client.get(
                player_api_url(origin),
                params={**credentials, "action": action},
            )
            # Applies to injected test transports as well as the bounded production one.
            if response.status_code != 200 or len(response.content) > _MAX_JSON_BYTES:
                raise CatalogUnavailable("Provider catalogue unavailable")
            return response.json()
        except (httpx.RequestError, ValueError):
            # Do not render upstream exceptions: their URLs may embed credentials.
            raise CatalogUnavailable("Provider catalogue unavailable") from None

    def list(
        self,
        *,
        dns_link: str,
        username: str,
        password: str,
        kind: CatalogKind,
    ) -> tuple[list[dict[str, str]], list[dict[str, str | None]]]:
        if not username or not password or not isinstance(kind, CatalogKind):
            raise CatalogUnavailable("Incomplete catalogue authorization")
        try:
            target = resolve_dns_link_target(dns_link, resolver=self._resolver)
        except (UnsafeOutboundURL, ValueError, RuntimeError):
            raise CatalogUnavailable("Provider destination denied") from None

        transport = (
            self._transport_factory(target)
            if self._transport_factory is not None
            else PinnedHTTPTransport(target, max_response_bytes=_MAX_JSON_BYTES)
        )
        categories_action, items_action, id_field = _ACTIONS[kind]
        credentials = {"username": username, "password": password}
        with httpx.Client(
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "Vivoapk/1.0"},
            timeout=httpx.Timeout(12),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            categories = self._read(client, target.origin, credentials, categories_action)
            items = self._read(client, target.origin, credentials, items_action)
        return _parse_categories(categories), _parse_items(items, kind, id_field)
