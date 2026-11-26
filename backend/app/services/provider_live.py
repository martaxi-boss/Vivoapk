"""Resolve a single authorized TS locator from the assigned provider's playlist.

Metadata lookup only: never open a playlist entry, follow redirects, guess a
legacy URL or retry a denied source. Credentials/URLs stay in backend memory.
"""
from __future__ import annotations

import time
from contextlib import closing
from urllib.parse import unquote, urlencode, urlsplit

from app.schemas import CatalogKind
from app.services.media_body import MediaReadError, _default_pool, _header, _require_media_transport
from app.services.media_origin import AuthorizedMediaOrigin
from app.url_safety import resolve_dns_link_target, system_resolver

_CAP = 8 * 1024 * 1024
_LINE_CAP = 16384


def lookup_provider_ts_source(
    source: AuthorizedMediaOrigin, *, allow_authorized_http: bool = False,
    resolver=system_resolver, pool_factory=_default_pool,
) -> AuthorizedMediaOrigin:
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
                for block in response.iter_stream():
                    received += len(block)
                    if received > _CAP or time.monotonic() - start > 65:
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
                        if not line or line.startswith("#"):
                            continue
                        last = unquote(urlsplit(line).path.rsplit("/", 1)[-1])
                        if last in (source.asset.asset_id, source.asset.asset_id + ".ts"):
                            # Constructor enforces exact credentials/asset and same
                            # scheme, hostname and effective port as selected DNS.
                            return source.with_provider_live_url(line)
                    if len(pending) > _LINE_CAP:
                        raise MediaReadError("Invalid provider playlist line")
                # A matching final line without LF is also a complete URL.
                if header and pending:
                    line = pending.removesuffix(b"\r").decode("utf-8", errors="strict")
                    return source.with_provider_live_url(line)
        raise MediaReadError("Authorized asset absent from provider playlist")
    except MediaReadError:
        raise
    except Exception:
        # Upstream exceptions/URLs may contain credentials. Never chain them.
        raise MediaReadError("Provider source metadata unavailable") from None
