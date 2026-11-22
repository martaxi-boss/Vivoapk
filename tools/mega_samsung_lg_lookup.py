#!/usr/bin/env python3
"""Read-only Mega OTT subscription lookup for a native Samsung/LG Vivo Player.

The official Mega OTT API token must come from a private environment variable.
Never run this tool with a real token through a public GitHub Actions request.
No activation, subscription modification or Vivo Player portal operation occurs.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_MEGA_API_BASE = "https://megaott.net/api"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class InteropError(RuntimeError):
    pass


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_api_base(url: str) -> str:
    url = url.strip()
    p = urllib.parse.urlsplit(url)
    if (p.scheme != "https" or not p.hostname or p.username is not None
            or p.password is not None or p.query or p.fragment):
        raise InteropError("Mega API origin must be HTTPS with no embedded credentials")
    return url.rstrip("/")


def validate_provider_origin(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise InteropError("No Samsung/LG Xtream origin was returned")
    p = urllib.parse.urlsplit(url)
    if (p.scheme not in ("http", "https") or not p.hostname
            or p.username is not None or p.password is not None
            or p.query or p.fragment or p.path not in ("", "/")):
        raise InteropError("Invalid Samsung/LG Xtream origin")
    try:
        _ = p.port
    except ValueError as exc:
        raise InteropError("Invalid Xtream port") from exc
    return url.rstrip("/")


def extract_native_tv_config(payload: Any, subscription_id: int) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise InteropError("Mega response is not an object")
    if type(payload.get("id")) is not int or payload["id"] != subscription_id:
        raise InteropError("Mega subscription ID mismatch")
    if str(payload.get("type", "")).casefold() != "m3u":
        raise InteropError("Mega subscription is not M3U/Xtream-compatible")
    username = payload.get("username")
    if not isinstance(username, str) or not username.strip():
        raise InteropError("Mega subscription username missing")
    # No fallback to dns_link: that is the Android/Windows URL in PINK's contract.
    native = validate_provider_origin(payload.get("dns_link_for_samsung_lg"))
    return {"platform": "Samsung/LG native TV",
            "xtream_server_url": native, "xtream_username": username}


def lookup(subscription_id: int, token: str, api_base: str) -> dict[str, str]:
    if subscription_id <= 0:
        raise InteropError("Invalid subscription ID")
    if not token or not token.strip() or token.strip() == "change-me":
        raise InteropError("Private Mega API token is unavailable")
    api_base = validate_api_base(api_base)
    endpoint = f"{api_base}/v1/subscriptions/{subscription_id}"
    request = urllib.request.Request(
        endpoint,
        method="GET",
        headers={"Accept": "application/json",
                 "Authorization": "Bearer " + token,
                 "User-Agent": "Vivoapk-Mega-ReadOnly/1.0"},
    )
    opener = urllib.request.build_opener(NoRedirects())
    try:
        with opener.open(request, timeout=12.0) as response:
            if response.status != 200:
                raise InteropError(f"Mega API refused retrieval: HTTP {response.status}")
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise InteropError("Mega API response exceeded size limit")
    except urllib.error.HTTPError as exc:
        raise InteropError(f"Mega API refused retrieval: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InteropError("Mega API not reachable over trusted HTTPS") from None
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise InteropError("Mega API returned invalid JSON") from None
    return extract_native_tv_config(payload, subscription_id)


def save_private(path: Path, config: dict[str, str]) -> None:
    # Never overwrite or use the repository as a destination for live credentials.
    if path.is_symlink() or path.exists():
        raise InteropError("Output file already exists; refusing overwrite")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise InteropError("Choose an existing private output directory")
    payload = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode()
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def self_test() -> dict[str, Any]:
    fixture = {
        "id": 12345,
        "type": "M3U",
        "username": "fake_user",
        "password": "fake_password_never_used",
        "dns_link": "https://android.example.org:9443",
        "dns_link_for_samsung_lg": "https://native-tv.example.org:9443",
    }
    result = extract_native_tv_config(fixture, 12345)
    assert result["xtream_server_url"] == "https://native-tv.example.org:9443"
    assert result["xtream_username"] == "fake_user"
    assert "password" not in result
    checks = 3

    for mutation in (
        {"dns_link_for_samsung_lg": None},
        {"dns_link_for_samsung_lg": "https://user:pass@native-tv.example.org"},
        {"dns_link_for_samsung_lg": "https://native-tv.example.org/?token=abc"},
        {"type": "OTHER"},
        {"id": 12346},
        {"username": ""},
    ):
        payload = {**fixture, **mutation}
        try:
            extract_native_tv_config(payload, 12345)
        except InteropError:
            checks += 1
        else:
            raise AssertionError("Unsafe Mega response unexpectedly accepted")

    for bad_url in ("http://megaott.net/api",
                    "https://user:password@megaott.net/api",
                    "https://megaott.net/api?password=secret"):
        try:
            validate_api_base(bad_url)
        except InteropError:
            checks += 1
        else:
            raise AssertionError("Unsafe Mega API URL unexpectedly accepted")

    assert validate_api_base(DEFAULT_MEGA_API_BASE) == DEFAULT_MEGA_API_BASE
    checks += 1
    return {"self_test": "passed", "checks": checks,
            "network_access": False, "real_credentials_used": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--subscription-id", type=int)
    parser.add_argument("--out-private", type=Path,
                        help="Write provider origin and username to a local 0600 JSON file, outside any public repo")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test()))
        return 0
    if not args.out_private:
        parser.error("--out-private is required for private Mega lookup")
    try:
        config = lookup(
            args.subscription_id,
            os.environ.get("MEGA_OTT_API_TOKEN", ""),
            os.environ.get("MEGA_OTT_API_BASE", DEFAULT_MEGA_API_BASE),
        )
        save_private(args.out_private, config)
    except (InteropError, OSError) as exc:
        print("Vivoapk Mega lookup failed: " + str(exc), file=sys.stderr)
        return 2
    print("Mega Samsung/LG configuration retrieved into a private local file (0600).")
    print("No Mega token, subscription ID, provider URL or customer password printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
