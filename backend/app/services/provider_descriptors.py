"""Signed provider-issued route descriptors, never stored URLs or credentials.

A descriptor records only the observed route family for one listed asset. The
current caller must already authorize that asset/account/DNS before hydrating
it with credentials held in RAM. No network access or authorization cache.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote, urlsplit

from app.services.media_origin import MediaAsset

_MAX_BYTES = 4 * 1024 * 1024
_MAX_ITEMS = 32768
_FILE = re.compile(r"[0-9a-f]{64}\.json\Z")
_ID = re.compile(r"[0-9]{1,64}\Z")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class ProviderDescriptors:
    def __init__(self, directory, key: bytes, *, ttl_seconds=86400,
                 max_accounts=2, clock=time.time):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("Private descriptor key unavailable")
        if not 1 <= ttl_seconds <= 86400 or not 1 <= max_accounts <= 2:
            raise ValueError("Invalid private descriptor limits")
        self._root = Path(directory)
        self._key = hmac.digest(key, b"vivoapk.provider-descriptors.v1", "sha256")
        self._ttl, self._max, self._clock = ttl_seconds, max_accounts, clock
        self._check_directory()

    def _check_directory(self):
        if not self._root.is_absolute() or ".." in self._root.parts:
            raise ValueError("Unsafe private descriptor directory")
        for parent in (*reversed(self._root.parents), self._root):
            if parent.is_symlink():
                raise ValueError("Unsafe private descriptor directory")
        self._root.mkdir(mode=0o700, parents=False, exist_ok=True)
        info = self._root.stat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077):
            raise ValueError("Private descriptor directory permissions invalid")

    def _scope(self, identity):
        return hmac.digest(self._key, b"scope\x00" + identity.encode(), "sha256").hex()

    def _signature(self, body):
        return hmac.digest(self._key, b"body\x00" + _json(body), "sha256").hex()

    def read(self, identity, source):
        """Return only the requested asset, after MAC/expiry/scope validation."""
        try:
            self._check_directory()
            scope = self._scope(identity)
            fd = os.open(self._root / (scope + ".json"),
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.geteuid() or info.st_mode & 0o077
                        or not 0 < info.st_size <= _MAX_BYTES):
                    return None
                raw = stream.read(_MAX_BYTES + 1)
                if len(raw) != info.st_size:
                    return None
            outer = json.loads(raw)
            if not isinstance(outer, dict) or set(outer) != {"body", "signature"}:
                return None
            body, signature = outer["body"], outer["signature"]
            if (not isinstance(body, dict) or not isinstance(signature, str)
                    or not hmac.compare_digest(signature, self._signature(body))
                    or set(body) != {"version", "scope", "issued", "expires", "items"}
                    or body["version"] != 1 or body["scope"] != scope):
                return None
            issued, expires = body["issued"], body["expires"]
            if (type(issued) is not int or type(expires) is not int
                    or not 0 < expires - issued <= 86400
                    or not issued <= self._clock() < expires):
                return None
            items = body["items"]
            if not isinstance(items, dict) or not 0 < len(items) <= _MAX_ITEMS:
                return None
            descriptor = items.get(source.asset.asset_id)
            if (not isinstance(descriptor, list) or len(descriptor) != 2
                    or type(descriptor[0]) is not int or descriptor[0] not in (0, 1)
                    or type(descriptor[1]) is not bool):
                return None
            assigned = urlsplit(source.origin)
            authority = assigned.netloc
            if descriptor[1] and assigned.port is None:
                authority += ":" + ("443" if assigned.scheme == "https" else "80")
            user = quote(source.username, safe="")
            password = quote(source.password.get_secret_value(), safe="")
            path = (f"/{user}/{password}/{source.asset.asset_id}" if descriptor[0] == 0
                    else f"/live/{user}/{password}/{source.asset.asset_id}.ts")
            url = f"{assigned.scheme}://{authority}{path}"
            return source.with_provider_live_url(url)._request_url()
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            # A damaged/stale descriptor is never authorization or a stale URL
            # fallback. The ordinary authenticated metadata lookup can run.
            return None

    def write(self, identity, source, urls):
        """Persist only route enums for URLs actually issued to this account."""
        temporary = None
        try:
            self._check_directory()
            items = {}
            assigned = urlsplit(source.origin)
            for asset, url in urls.items():
                if not isinstance(asset, str) or not _ID.fullmatch(asset):
                    continue
                if len(items) >= _MAX_ITEMS:
                    break
                try:
                    candidate = replace(source, asset=MediaAsset(
                        source.asset.kind, source.asset.category_id, asset),
                        _provider_live_url=None)
                    candidate.with_provider_live_url(url)
                    parsed = urlsplit(url)
                    items[asset] = [int(parsed.path.startswith("/live/")),
                                    parsed.port is not None and assigned.port is None]
                except (ValueError, TypeError):
                    continue
            if not items:
                return False
            issued = int(self._clock())
            scope = self._scope(identity)
            body = {"version": 1, "scope": scope, "issued": issued,
                    "expires": issued + self._ttl, "items": items}
            raw = _json({"body": body, "signature": self._signature(body)})
            if len(raw) > _MAX_BYTES:
                return False
            path = self._root / (scope + ".json")
            # The private single-worker backend owns this directory; bounded
            # account slots, no caller paths or unbounded disk accumulation.
            known = []
            for entry in self._root.iterdir():
                if not _FILE.fullmatch(entry.name):
                    continue
                info = entry.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.geteuid() or info.st_mode & 0o077):
                    continue
                known.append((info.st_mtime_ns, entry))
                if len(known) > 64:
                    return False
            others = sorted((pair for pair in known if pair[1] != path), key=lambda p: p[0])
            while len(others) >= self._max:
                _, old = others.pop(0)
                old.unlink()
            fd, temporary = tempfile.mkstemp(prefix=".provider-descriptors-", dir=self._root)
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
            return True
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return False
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
