"""Validate provider-issued redirects without forwarding customer headers."""
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.services.media_body import MediaReadError
from app.url_safety import validate_dns_link


def provider_redirect_url(base: str, headers, *, allow_authorized_http: bool) -> str:
    locations = [v for k, v in headers if k.lower() == b"location"]
    if len(locations) != 1 or not isinstance(locations[0], bytes):
        raise MediaReadError("Provider redirect unavailable")
    try:
        raw = locations[0].decode("ascii").strip()
        if (not raw or len(raw) > 4096 or "\\" in raw
                or any(ord(c) < 33 or ord(c) > 126 for c in raw)):
            raise ValueError("invalid location")
        relative = urlsplit(raw)
        if relative.fragment or relative.username is not None:
            raise ValueError("invalid location")
        if any(s in {".", ".."} for s in relative.path.split("/")):
            raise ValueError("invalid location")
        url = urljoin(base, raw)
        target, previous = urlsplit(url), urlsplit(base)
        if (target.scheme not in {"http", "https"} or not target.hostname
                or target.username is not None or target.fragment or target.port == 0
                or (target.scheme == "http" and not allow_authorized_http)
                or (previous.scheme == "https" and target.scheme != "https")):
            raise ValueError("invalid target")
        # DNS resolution and pinning are repeated at the actual connection.
        validate_dns_link(urlunsplit((target.scheme, target.netloc, "", "", "")))
        return url
    except (ValueError, UnicodeError):
        raise MediaReadError("Provider redirect unavailable") from None
