"""Native Jellyfin DLL release transaction, intentionally not an activator.

This module has no CLI, service commands, provider credentials or networking.
The future Ferrari adapter must supply root-owned paths, independently reviewed
release pins, guarded service controls and health/admin checks. A JSON package
cannot approve itself. The regular production release is not yet approved.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Callable

# Fixed installed locations, established by the read-only VPS inventory.
# Only these existing files are in scope. No configuration, database,
# service unit, backend source or FFmpeg distribution is modified here.
COMPONENTS = {
    "Jellyfin.Api.dll": "releases/12.4/jellyfin/Jellyfin.Api.dll",
    "Jellyfin.LiveTv.dll": "releases/12.4/jellyfin/Jellyfin.LiveTv.dll",
    "MediaBrowser.Controller.dll": "releases/12.4/jellyfin/MediaBrowser.Controller.dll",
    "Jellyfin.Plugin.VivoapkMegaAuth.dll":
        "data/plugins/VivoAPK Mega OTT Auth_0.1.0.0/Jellyfin.Plugin.VivoapkMegaAuth.dll",
}
MAX_DLL = 64 * 1024 * 1024
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")


class Blocked(Exception):
    """Fixed reason code; do not expose data, credentials or private paths."""


@dataclass(frozen=True)
class Pin:
    name: str
    installed: str
    candidate: str


@dataclass(frozen=True)
class Release:
    source: str
    backend: str
    pins: tuple[Pin, ...]


# This is intentionally empty. A future, reviewed change must pin the exact
# non-lab artifacts and compatible backend before a production adapter can
# obtain an approval. Never import approvals from the staged bundle.
APPROVED_PRODUCTION: tuple[Release, ...] = ()


def approved_release(source: str) -> Release:
    found = [r for r in APPROVED_PRODUCTION if r.source == source]
    if len(found) != 1:
        raise Blocked("no_approved_regular_production_release")
    return found[0]


def validate_release(release: Release) -> None:
    if not SHA40.fullmatch(release.source) or not SHA40.fullmatch(release.backend):
        raise Blocked("unverified_source_or_backend_revision")
    if tuple(p.name for p in release.pins) != tuple(COMPONENTS):
        raise Blocked("component_set_mismatch")
    for p in release.pins:
        if not SHA64.fullmatch(p.installed) or not SHA64.fullmatch(p.candidate):
            raise Blocked("invalid_component_digest")
        if p.installed == p.candidate:
            raise Blocked("unchanged_component_in_transaction")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_directory(path: Path) -> None:
    # The adapter must hold exclusive ownership of these directories during
    # activation; reject existing symlink components instead of resolving them.
    if not path.is_absolute() or ".." in path.parts:
        raise Blocked("unsafe_directory")
    for parent in (*reversed(path.parents), path):
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise Blocked("non_directory_or_symlink")


def read_regular(path: Path, limit: int = MAX_DLL) -> tuple[bytes, os.stat_result]:
    checked_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= limit:
            raise Blocked("unsafe_or_oversized_file")
        data = stream.read(limit + 1)
        final = os.fstat(stream.fileno())
        if len(data) != info.st_size or final.st_mtime_ns != info.st_mtime_ns:
            raise Blocked("file_changed_while_reading")
    return data, info


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes, mode: int, uid: int, gid: int) -> None:
    checked_directory(path.parent)
    if path.is_symlink():
        raise Blocked("symlink_destination")
    fd, temp = tempfile.mkstemp(prefix=".vivoapk-transaction-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            current = os.fstat(stream.fileno())
            if (current.st_uid, current.st_gid) != (uid, gid):
                os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        if os.path.lexists(temp):
            os.unlink(temp)


class Transaction:
    """Trusted adapter only; tests use disposable directories and fake services.

    The stop callback must return only after Jellyfin has stopped. The start
    callback must start only Jellyfin; the health callback must check the
    exact installed binaries, private network, backend/protected services,
    and unchanged admin identity. The adapter, not a bundle, owns these.
    """
    def __init__(self, jelly: Path, stage: Path, state: Path, release: Release,
                 stop: Callable[[], None], start: Callable[[], None],
                 health: Callable[[], None]):
        validate_release(release)
        self.jelly, self.stage, self.state = jelly, stage, state
        self.release = release
        self.stop, self.start, self.health = stop, start, health
        self.save = state / release.source
        for root in (jelly, stage, state):
            checked_directory(root)
        if len({jelly, stage, state}) != 3:
            raise Blocked("transaction_roots_overlap")
        if any(a.is_relative_to(b) for a in (jelly, stage, state)
               for b in (jelly, stage, state) if a != b):
            raise Blocked("transaction_roots_overlap")
        if state.stat().st_uid != os.geteuid() or state.stat().st_mode & 0o077:
            raise Blocked("transaction_state_not_private")

    def target(self, pin: Pin) -> Path:
        return self.jelly / COMPONENTS[pin.name]

    def identity(self) -> dict:
        return {"source": self.release.source, "backend": self.release.backend,
                "components": [{"name": p.name, "installed": p.installed,
                                "candidate": p.candidate} for p in self.release.pins]}

    @contextmanager
    def lock(self):
        checked_directory(self.state)
        fd = os.open(self.state / "native-release.lock",
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise Blocked("unsafe_transaction_lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Blocked("another_transaction_running") from None
            yield
        finally:
            os.close(fd)

    def check(self, candidate: bool = False) -> None:
        for pin in self.release.pins:
            raw, _ = read_regular(self.target(pin))
            if digest(raw) != (pin.candidate if candidate else pin.installed):
                raise Blocked("installed_component_digest_mismatch")

    def stage_bytes(self, pin: Pin) -> bytes:
        raw, _ = read_regular(self.stage / pin.name)
        if digest(raw) != pin.candidate:
            raise Blocked("staged_component_digest_mismatch")
        return raw

    def preflight(self) -> None:
        """Read-only preflight; no lock/journal or service change."""
        if os.path.lexists(self.save):
            raise Blocked("existing_transaction_requires_review")
        self.check()
        for pin in self.release.pins:
            self.stage_bytes(pin)
        self.health()

    def write_journal(self, phase: str, metadata: dict) -> None:
        payload = {**self.identity(), "phase": phase, "metadata": metadata}
        atomic_write(self.save / "journal.json",
                     json.dumps(payload, sort_keys=True).encode("ascii"),
                     0o600, os.geteuid(), os.getegid())

    def read_journal(self) -> tuple[str, dict]:
        checked_directory(self.save)
        info = self.save.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise Blocked("unsafe_backup_directory")
        raw, st = read_regular(self.save / "journal.json", 65536)
        if st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise Blocked("unsafe_journal")
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != set(self.identity()) | {"phase", "metadata"}:
            raise Blocked("invalid_journal")
        if any(data[k] != v for k, v in self.identity().items()):
            raise Blocked("journal_release_mismatch")
        phase, metadata = data["phase"], data["metadata"]
        if phase not in ("prepared", "installing", "restoring", "committed", "rolled_back"):
            raise Blocked("unknown_transaction_phase")
        if not isinstance(metadata, dict) or set(metadata) != set(COMPONENTS):
            raise Blocked("invalid_backup_metadata")
        for entry in metadata.values():
            if (not isinstance(entry, dict) or set(entry) != {"mode", "uid", "gid"}
                    or any(type(entry[k]) is not int for k in entry)
                    or not 0 <= entry["mode"] <= 0o777
                    or min(entry["uid"], entry["gid"]) < 0):
                raise Blocked("invalid_backup_metadata")
        return phase, metadata

    def prepare(self) -> dict:
        self.save.mkdir(mode=0o700)
        sync_directory(self.state)
        metadata = {}
        for pin in self.release.pins:
            old, info = read_regular(self.target(pin))
            if digest(old) != pin.installed or info.st_mode & 0o7000:
                raise Blocked("installed_component_changed")
            self.stage_bytes(pin)
            metadata[pin.name] = {"mode": stat.S_IMODE(info.st_mode),
                                  "uid": info.st_uid, "gid": info.st_gid}
            atomic_write(self.save / pin.name, old, 0o600, os.geteuid(), os.getegid())
        # Durable journal and all verified backups exist BEFORE stop/replace.
        self.write_journal("prepared", metadata)
        return metadata

    def backup_bytes(self, pin: Pin) -> bytes:
        raw, st = read_regular(self.save / pin.name)
        if (st.st_uid != os.geteuid() or st.st_mode & 0o077
                or digest(raw) != pin.installed):
            raise Blocked("backup_digest_or_permissions_mismatch")
        return raw

    def restore_locked(self) -> str:
        phase, metadata = self.read_journal()
        if phase not in ("prepared", "installing", "restoring"):
            raise Blocked("transaction_not_recoverable")
        for pin in self.release.pins:
            self.backup_bytes(pin)
            current, _ = read_regular(self.target(pin))
            if digest(current) not in (pin.installed, pin.candidate):
                raise Blocked("unrelated_component_change_requires_review")
        self.write_journal("restoring", metadata)
        self.stop()
        for pin in self.release.pins:
            current, _ = read_regular(self.target(pin))
            if digest(current) not in (pin.installed, pin.candidate):
                raise Blocked("unrelated_component_change_requires_review")
            atomic_write(self.target(pin), self.backup_bytes(pin), **metadata[pin.name])
        self.check()
        self.start()
        self.health()
        self.write_journal("rolled_back", metadata)
        return "rolled_back"

    def activate(self) -> str:
        with self.lock():
            self.preflight()
            metadata = self.prepare()
            try:
                self.write_journal("installing", metadata)
                self.stop()
                # Recheck ALL files after stop, before modifying any.
                self.check()
                staged = {p.name: self.stage_bytes(p) for p in self.release.pins}
                for pin in self.release.pins:
                    atomic_write(self.target(pin), staged[pin.name], **metadata[pin.name])
                self.check(candidate=True)
                self.start()
                self.health()
                self.check(candidate=True)
                self.write_journal("committed", metadata)
                return "committed"
            except (Exception, KeyboardInterrupt):
                # A failed rollback is NOT reported as success. Keep the
                # durable journal and backups for an explicit recovery.
                try:
                    self.restore_locked()
                except Exception:
                    raise Blocked("activation_and_rollback_need_review") from None
                return "rolled_back_after_failure"

    def recover_interrupted(self) -> str:
        """Explicit recovery after process termination; never a resident agent."""
        with self.lock():
            return self.restore_locked()
