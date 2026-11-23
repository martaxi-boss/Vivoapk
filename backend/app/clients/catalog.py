"""Read bounded Xtream catalogue metadata from the account's approved public DNS.

No playback URLs, customer passwords, DNS origins or credentials leave this module.
The existing pinned outbound transport prevents private-network and DNS-rebinding hops.
"""
from __future__ import annotations

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
    if isinstance(value, bool) or not isinstance(value, str | int):
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
    def _read(
        client: httpx.Client,
        origin: str,
        credentials: dict[str, str],
        action: str,
        *,
        extra: dict[str, str] | None = None,
    ) -> Any:
        try:
            response = client.get(
                player_api_url(origin),
                params={**credentials, "action": action, **(extra or {})},
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
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "User-Agent": "Vivoapk/1.0",
            },
            timeout=httpx.Timeout(12),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            categories = self._read(client, target.origin, credentials, categories_action)
            items = self._read(client, target.origin, credentials, items_action)
        return _parse_categories(categories), _parse_items(items, kind, id_field)


    def preview_live(
        self, *, dns_link: str, username: str, password: str
    ) -> tuple[list[dict[str, str]], list[dict[str, str | None]]]:
        """Fetch a bounded single-category live TV preview, never the full lineup."""
        if not username or not password:
            raise CatalogUnavailable("Incomplete catalogue authorization")
        try:
            target = resolve_dns_link_target(dns_link, resolver=self._resolver)
        except (UnsafeOutboundURL, ValueError, RuntimeError):
            raise CatalogUnavailable("Provider destination denied") from None
        transport = (
            self._transport_factory(target) if self._transport_factory is not None
            else PinnedHTTPTransport(target, max_response_bytes=_MAX_JSON_BYTES)
        )
        credentials = {"username": username, "password": password}
        with httpx.Client(
            transport=transport,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "User-Agent": "Vivoapk/1.0",
            },
            timeout=httpx.Timeout(12),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            categories = _parse_categories(self._read(
                client, target.origin, credentials, "get_live_categories",
            ))
            if not categories:
                return [], []
            # The first authorized category was validated against a REAL provider.
            # Try only one category to avoid huge account-wide responses at login.
            category = categories[0]
            items = _parse_items(self._read(
                client, target.origin, credentials, "get_live_streams",
                extra={"category_id": category["id"]},
            ), CatalogKind.LIVE, "stream_id")
        return [category], [
            item for item in items if item["category_id"] in (category["id"], None)
        ][:5]

    def _detail_request(
        self,
        *,
        dns_link: str,
        username: str,
        password: str,
        action: str,
        extra: dict[str, str],
    ) -> Any:
        """Fetch one bounded provider response without a URL supplied by the caller."""
        if not username or not password:
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
        with httpx.Client(
            transport=transport,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "User-Agent": "Vivoapk/1.0",
            },
            timeout=httpx.Timeout(12),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            return self._read(
                client,
                target.origin,
                {"username": username, "password": password},
                action,
                extra=extra,
            )

    def series_detail(
        self, *, dns_link: str, username: str, password: str, series_id: str
    ) -> dict[str, Any]:
        """A series is authorized by the caller's upstream list before this method."""
        if _clean_id(series_id) != series_id:
            raise CatalogUnavailable("Invalid series identifier")
        payload = self._detail_request(
            dns_link=dns_link, username=username, password=password,
            action="get_series_info", extra={"series_id": series_id},
        )
        if not isinstance(payload, dict) or not any(
            key in payload for key in ("info", "seasons", "episodes")
        ):
            raise CatalogUnavailable("Invalid series detail response")
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        seasons_data = payload.get("seasons") or []
        episodes_data = payload.get("episodes") or {}
        if not isinstance(seasons_data, list) or not isinstance(episodes_data, dict | list):
            raise CatalogUnavailable("Invalid series detail structure")
        if len(seasons_data) > _MAX_ITEMS or len(episodes_data) > _MAX_ITEMS:
            raise CatalogUnavailable("Series detail too large")
        seasons: list[dict[str, str]] = []
        known: set[str] = set()
        for item in seasons_data:
            if not isinstance(item, dict):
                continue
            season_id = _clean_id(item.get("season_number")) or _clean_id(item.get("id"))
            if season_id is None or season_id in known:
                continue
            title = _clean_title(item.get("name")) or ("Temporada " + season_id)
            seasons.append({"id": season_id, "title": title})
            known.add(season_id)
        rows = (
            [(str(season), ep) for season, episodes in episodes_data.items()
             if isinstance(episodes, list) for ep in episodes]
            if isinstance(episodes_data, dict)
            else [("", ep) for ep in episodes_data]
        )
        if len(rows) > _MAX_ITEMS:
            raise CatalogUnavailable("Too many episodes")
        episodes: list[dict[str, str | None]] = []
        seen: set[str] = set()
        for grouped_season, item in rows:
            if not isinstance(item, dict):
                continue
            episode_id = _clean_id(item.get("id")) or _clean_id(item.get("episode_id"))
            season_id = (
                _clean_id(grouped_season) or _clean_id(item.get("season"))
                or _clean_id(item.get("season_number"))
            )
            if episode_id is None or season_id is None or episode_id in seen:
                continue
            number = _clean_id(item.get("episode_num")) or _clean_id(item.get("episode_number"))
            title = (
                _clean_title(item.get("title")) or _clean_title(item.get("name"))
                or (("Episódio " + number) if number is not None else "Episódio")
            )
            extension = item.get("container_extension")
            if not isinstance(extension, str) or not _EXTENSION.fullmatch(extension):
                extension = None
            episodes.append({
                "id": episode_id, "season_id": season_id,
                "episode_number": number, "title": title,
                "container_extension": extension,
            })
            seen.add(episode_id)
            if season_id not in known:
                seasons.append({"id": season_id, "title": "Temporada " + season_id})
                known.add(season_id)
        return {
            "series_id": series_id,
            "title": _clean_title(info.get("name")),
            "seasons": seasons,
            "episodes": episodes,
        }

    def short_epg(
        self, *, dns_link: str, username: str, password: str,
        stream_id: str, limit: int
    ) -> dict[str, Any]:
        """Return only a bounded display guide; no URLs or identity fields."""
        if _clean_id(stream_id) != stream_id or not 1 <= limit <= 20:
            raise CatalogUnavailable("Invalid EPG parameters")
        payload = self._detail_request(
            dns_link=dns_link, username=username, password=password,
            action="get_short_epg", extra={"stream_id": stream_id, "limit": str(limit)},
        )
        if not isinstance(payload, dict) or "epg_listings" not in payload:
            raise CatalogUnavailable("Invalid EPG response")
        raw = payload["epg_listings"]
        if raw is None:
            raw = []
        if not isinstance(raw, list) or len(raw) > 100:
            raise CatalogUnavailable("Invalid EPG items")
        result: list[dict[str, str | int | None]] = []
        for item in raw[:limit]:
            if not isinstance(item, dict):
                continue
            title = _clean_title(item.get("title"))
            if title is None:
                continue
            # Timestamps are numeric seconds; never place arbitrary upstream
            # values or links in the response.
            def seconds(value: Any) -> int | None:
                if isinstance(value, bool) or not isinstance(value, int | str):
                    return None
                try:
                    n = int(value)
                except ValueError:
                    return None
                return n if 0 < n < 20000000000 else None

            result.append({
                "title": title,
                "start_timestamp": seconds(item.get("start_timestamp")),
                "end_timestamp": seconds(item.get("stop_timestamp"))
                or seconds(item.get("end_timestamp")),
            })
        return {"stream_id": stream_id, "programmes": result}
