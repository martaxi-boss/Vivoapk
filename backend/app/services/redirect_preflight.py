"""Isolated redirect preflight for explicitly authorized HTTPS media.

This is not a provider integration or a public endpoint. No network requests,
credentials, provider discovery or media I/O are performed here. The caller
must supply a trusted, current grant and a configured origin. A successful
decision is not proof of client compatibility or revocation at the origin.
"""
from dataclasses import dataclass, field
from datetime import datetime
import re
from urllib.parse import urlsplit

from app.url_safety import UnsafeOutboundURL, validate_dns_link


class RedirectDenied(ValueError):
    """Do not include destinations or identity details in this exception."""


@dataclass(frozen=True)
class AuthorizedMediaGrant:
    account_id: str = field(repr=False)
    asset_id: str
    expires_at: datetime
    active: bool
    url: str = field(repr=False)


@dataclass(frozen=True)
class RedirectDecision:
    status: int
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    body: bytes = b""


def preflight_redirect(
    grant: AuthorizedMediaGrant,
    *,
    authenticated_account: str,
    requested_asset: str,
    approved_origin: str,
    now: datetime,
) -> RedirectDecision:
    """Accept only an exact, preauthorized fixture asset on a trusted origin.

    No generic redirect target from client input, no username/password paths,
    no raw query string or tokens. An actual provider adapter requires its own
    documented, authorized URL and origin-side expiry contract before use.
    """
    if (
        not grant.active
        or not authenticated_account
        or grant.account_id != authenticated_account
        or grant.asset_id != requested_asset
        or now.tzinfo is None
        or grant.expires_at.tzinfo is None
        or grant.expires_at <= now
    ):
        raise RedirectDenied("Playback not authorized")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", requested_asset):
        raise RedirectDenied("Invalid asset")
    try:
        if not grant.url.isascii() or not approved_origin.isascii():
            raise ValueError()
        if any(ord(c) <= 32 or ord(c) >= 127 for c in grant.url + approved_origin):
            raise ValueError()
        if any(c in grant.url for c in ("?", "#", "%", "\\", "@")):
            raise ValueError()
        parsed = urlsplit(grant.url)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise ValueError()
        origin = f"https://{parsed.hostname}"
        approved = validate_dns_link(approved_origin)
        if approved != origin or parsed.netloc not in (parsed.hostname, parsed.hostname + ":443"):
            raise ValueError()
        # Only a known, noncredential-bearing fixture path is accepted.
        if parsed.path not in {
            f"/media/{requested_asset}.mp4",
            f"/media/{requested_asset}.m3u8",
        }:
            raise ValueError()
    except (ValueError, UnsafeOutboundURL):
        raise RedirectDenied("Playback destination rejected") from None
    return RedirectDecision(
        status=302,
        headers=(
            ("Location", grant.url),
            ("Cache-Control", "no-store"),
            ("Referrer-Policy", "no-referrer"),
            ("Content-Length", "0"),
        ),
    )
