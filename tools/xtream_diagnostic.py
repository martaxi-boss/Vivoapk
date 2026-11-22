#!/usr/bin/env python3
"""Read-only Xtream Codes compatibility diagnostic for an authorized test host.

Does not send usernames, passwords, tokens, MAC addresses or Device Keys.
Does not discover ports, perform scans, or bypass authentication.
Python standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


def validate_url(value: str) -> str:
    value = value.strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Expected an explicit http:// or https:// host URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Do not put credentials in the URL")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("Use only the server origin, without path or query")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid port number") from exc
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "", "", "")
    ).rstrip("/")


def is_xtream_payload(value: object) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("user_info"), dict)
        and isinstance(value.get("server_info"), dict)
    )


def probe_endpoint(url: str, timeout: float = 6.0) -> dict:
    result = {"path": urllib.parse.urlsplit(url).path or "/"}
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "VivoAPK-Authorized-Interop-Check/1.0",
            "Accept": "application/json,text/html",
        },
        method="GET",
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    except (TimeoutError, OSError, ssl.SSLError) as error:
        result["network_error"] = type(error).__name__
        return result
    try:
        with response:
            result["http_status"] = response.status
            result["content_type"] = response.headers.get("Content-Type", "")[:100]
            result["basic_auth_requested"] = bool(
                response.headers.get("WWW-Authenticate")
            )
            data = response.read(65536)
            result["observed_bytes"] = len(data)
            result["body_sha256_prefix"] = hashlib.sha256(data).hexdigest()[:16]
            if data.lstrip().startswith((b"{", b"[")):
                try:
                    payload = json.loads(data)
                except (UnicodeDecodeError, ValueError):
                    result["json_valid"] = False
                else:
                    result["json_valid"] = True
                    result["xtream_signature"] = is_xtream_payload(payload)
                    result["json_top_level_keys"] = (
                        sorted(payload.keys())[:20] if isinstance(payload, dict) else []
                    )
    except (TimeoutError, OSError) as error:
        result["read_error"] = type(error).__name__
    return result


def inspect(base_url: str) -> dict:
    base = validate_url(base_url)
    parsed = urllib.parse.urlsplit(base)
    result = {
        "server_origin": base,
        "mode": "unauthenticated-read-only",
        "credentials_sent": False,
        "checks": [],
    }
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        result["dns_resolves"] = True
        result["unique_dns_address_count"] = len(
            {record[4][0] for record in addresses}
        )
    except OSError as error:
        result["dns_resolves"] = False
        result["dns_error"] = type(error).__name__
        result["conclusion"] = "dns_unavailable"
        return result
    result["checks"].append(probe_endpoint(base + "/"))
    api = probe_endpoint(base + "/player_api.php")
    result["checks"].append(api)
    result["conclusion"] = (
        "xtream_signature_observed" if api.get("xtream_signature")
        else "not_confirmed_without_credentials"
    )
    if api.get("http_status") in (401, 403):
        result["note"] = "HTTP 401/403 alone cannot establish Xtream compatibility."
    return result


def self_test() -> dict:
    assert validate_url("http://localhost:8080/") == "http://localhost:8080"
    assert validate_url("https://example.org") == "https://example.org"
    for invalid in (
        "ftp://example.org",
        "https://user:secret@example.org/",
        "https://example.org/player_api.php",
        "https://example.org/?password=secret",
    ):
        try:
            validate_url(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe URL unexpectedly accepted")
    assert is_xtream_payload({"user_info": {}, "server_info": {}})
    assert not is_xtream_payload({"message": "Forbidden"})
    return {"self_test": "passed", "checks": 8}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="An authorized server origin; no credentials")
    group.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        output = self_test() if args.self_test else inspect(args.url)
    except ValueError as error:
        print(json.dumps({"error": str(error)}))
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
