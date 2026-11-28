"""Resolve a single authorized TS locator from the assigned provider's playlist.

Metadata lookup only: never open a playlist entry, follow redirects, guess a
legacy URL or retry a denied source. Credentials/URLs stay in backend memory; optional signed disk descriptors contain only route enums.
"""
from __future__ import annotations

import time
import hashlib
import json
from dataclasses import dataclass, field
from threading import Lock
from contextlib import closing
from urllib.parse import unquote, urlencode, urlsplit

from app.schemas import CatalogKind
from app.services.media_body import MediaReadError, _default_pool, _header, _require_media_transport
from app.services.media_origin import AuthorizedMediaOrigin
from app.url_safety import resolve_dns_link_target, system_resolver

_CAP = 8 * 1024 * 1024
_LINE_CAP = 16384



@dataclass(repr=False)
class _LocatorSlot:
    gate: object = field(default_factory=Lock)
    users: int = 0
    until: float = 0.0
    urls: dict[str, str] = field(default_factory=dict)


class ProviderLiveLocatorCache:
    """Private provider-issued metadata only; never an authorization cache.

    The caller must still authorize the current owner/category/asset and current
    credentials/DNS before each lookup. URLs remain RAM-only for at most five
    minutes. Every hit is revalidated against that exact authorized source.
    """
    def __init__(self, *, ttl_seconds=300.0, max_accounts=2, clock=time.monotonic,
                 descriptors=None):
        if not 0 < ttl_seconds <= 300 or not 1 <= max_accounts <= 2:
            raise ValueError("Invalid private locator cache bounds")
        self._ttl, self._max, self._clock = ttl_seconds, max_accounts, clock
        self._lock = Lock()
        self._slots = {}
        self._descriptors = descriptors

    def resolve(self, source, loader, *, allow_authorized_http):
        identity = json.dumps([source.origin, source.username,
                               source.password.get_secret_value(), allow_authorized_http])
        key = hashlib.sha256(identity.encode()).digest()
        with self._lock:
            for old, item in tuple(self._slots.items()):
                if not item.users and item.until <= self._clock():
                    del self._slots[old]
            slot = self._slots.get(key)
            if slot is None:
                if len(self._slots) >= self._max:
                    victim = next((k for k, v in self._slots.items() if not v.users), None)
                    if victim is not None:
                        del self._slots[victim]
                if len(self._slots) < self._max:
                    slot = self._slots[key] = _LocatorSlot()
            if slot is not None:
                slot.users += 1
        if slot is None:
            return loader()[0]  # No unbounded cache allocation under contention.
        try:
            with slot.gate:
                url = slot.urls.get(source.asset.asset_id)
                if self._clock() < slot.until and url is not None:
                    # A cached provider string cannot change owner/asset/DNS.
                    return source.with_provider_live_url(url)._request_url()
                if self._descriptors is not None:
                    remembered = self._descriptors.read(identity, source)
                    if remembered is not None:
                        slot.urls[source.asset.asset_id] = remembered
                        slot.until = self._clock() + self._ttl
                        return remembered
                slot.urls = {}
                slot.until = 0
                selected, urls = loader()
                slot.urls = urls
                slot.until = self._clock() + self._ttl
                if self._descriptors is not None:
                    self._descriptors.write(identity, source, urls)
                return selected
        finally:
            with self._lock:
                slot.users -= 1


def lookup_provider_ts_source(
    source: AuthorizedMediaOrigin, *, allow_authorized_http: bool = False,
    resolver=system_resolver, pool_factory=_default_pool,
    locator_cache: ProviderLiveLocatorCache | None = None,
) -> AuthorizedMediaOrigin:
    # Checks also run on cache hits; no format or transport-policy bypass.
    if source.asset.kind is not CatalogKind.LIVE or source.extension != "ts":
        raise MediaReadError("Advertised TS source unavailable")
    _require_media_transport(source, allow_authorized_http=allow_authorized_http)
    def load():
        return _lookup_provider_ts_source(source, allow_authorized_http=allow_authorized_http,
                                         resolver=resolver, pool_factory=pool_factory,
                                         collect=locator_cache is not None)
    try:
        url = (locator_cache.resolve(source, load, allow_authorized_http=allow_authorized_http)
               if locator_cache is not None else load()[0])
        return source.with_provider_live_url(url)
    except MediaReadError:
        raise
    except Exception:
        raise MediaReadError("Provider source metadata unavailable") from None

def _lookup_provider_ts_source(
    source: AuthorizedMediaOrigin, *, allow_authorized_http: bool = False,
    resolver=system_resolver, pool_factory=_default_pool, collect=False,
) -> tuple[str, dict[str, str]]:
    if source.asset.kind is not CatalogKind.LIVE or source.extension != "ts":
        raise MediaReadError("Advertised TS source unavailable")
    _require_media_transport(source, allow_authorized_http=allow_authorized_http)
    endpoint = source.origin.rstrip("/") + "/get.php?" + urlencode({
        "username": source.username, "password": source.password.get_secret_value(),
        "type": "m3u_plus", "output": "ts",
    })
    start = time.monotonic()
    try:
        target = resolve_dns_link_target(source.origin, resolver=resolver)
        with closing(pool_factory(target)) as pool:
            with pool.stream(
                "GET", endpoint,
                headers=[(b"Accept", b"audio/x-mpegurl, application/x-mpegurl, text/plain"),
                         (b"Accept-Encoding", b"identity"), (b"User-Agent", b"Vivoapk/1.0")],
                extensions={"timeout": {"connect": 5.0, "read": 45.0,
                                         "write": 5.0, "pool": 5.0}},
            ) as response:
                if (response.status != 200
                        or _header(response.headers, b"content-encoding") not in (None, "identity")
                        or _header(response.headers, b"content-range") is not None):
                    raise MediaReadError("Provider source metadata unavailable")
                pending = bytearray()
                received = 0
                header = False
                selected = None
                locators = {}
                ambiguous = set()
                stored = 0
                def record(line):
                    nonlocal selected, stored, collect
                    if not line or line.startswith("#"):
                        return
                    last = unquote(urlsplit(line).path.rsplit("/", 1)[-1])
                    asset = last.removesuffix(".ts")
                    if not asset.isascii() or not asset.isdigit() or not 1 <= len(asset) <= 64:
                        return
                    if asset == source.asset.asset_id:
                        source.with_provider_live_url(line)
                        if selected is not None and selected != line:
                            raise MediaReadError("Ambiguous provider asset")
                        selected = line
                    if collect and asset not in ambiguous:
                        prior = locators.get(asset)
                        if prior is not None and prior != line:
                            locators.pop(asset, None)
                            ambiguous.add(asset)
                        elif prior is None:
                            stored += len(line.encode())
                            if len(locators) >= 32768 or stored > 4 * 1024 * 1024:
                                # Positive metadata only: misses always reload. A bounded
                                # partial index is safe and never authorizes a missing asset.
                                collect = False
                            else:
                                locators[asset] = line
                for block in response.iter_stream():
                    received += len(block)
                    if received > _CAP or time.monotonic() - start > 65:
                        if selected is not None:
                            return selected, locators
                        raise MediaReadError("Provider source metadata exceeds bounds")
                    pending.extend(block)
                    while b"\n" in pending:
                        raw, _, pending = pending.partition(b"\n")
                        if len(raw) > _LINE_CAP:
                            raise MediaReadError("Invalid provider playlist line")
                        line = raw.removesuffix(b"\r").decode("utf-8", errors="strict")
                        if not header:
                            if line != "#EXTM3U" and not line.startswith("#EXTM3U "):
                                raise MediaReadError("Invalid provider playlist header")
                            header = True
                            continue
                        record(line)
                        if selected is not None and not collect:
                            return selected, locators
                    if len(pending) > _LINE_CAP:
                        raise MediaReadError("Invalid provider playlist line")
                # A matching final line without LF is also a complete URL.
                if header and pending:
                    line = pending.removesuffix(b"\r").decode("utf-8", errors="strict")
                    record(line)
                if selected is not None:
                    return selected, locators
        raise MediaReadError("Authorized asset absent from provider playlist")
    except MediaReadError:
        raise
    except Exception:
        # Upstream exceptions/URLs may contain credentials. Never chain them.
        raise MediaReadError("Provider source metadata unavailable") from None
