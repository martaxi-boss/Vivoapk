"""Coordinated native release transaction. No production service adapter or CLI.

The trusted host supplies the fixed destinations, independent release pin,
prepared bytes, and callbacks. All recovery data stays on the host; no
secret values are written to Git, diagnostics or the journal. The journal
records hashes, permissions and phases, not payloads. The backend checkout
and FFmpeg runtime remain staged; systemd drop-ins select them together.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat
from xml.etree import ElementTree as ET

from native_release_transaction import (
    Blocked, SHA40, SHA64, atomic_write, checked_directory, digest,
    read_regular, sync_directory,
)

NAMES = (
    "Jellyfin.Api.dll", "Jellyfin.LiveTv.dll", "MediaBrowser.Controller.dll",
    "Jellyfin.Plugin.VivoapkMegaAuth.dll", "plugin.xml",
    "backend-dropin", "server-dropin", "media-key", "media-env",
)
NEW_ONLY = frozenset(("backend-dropin", "server-dropin", "media-key", "media-env"))
FLAG = "NativePrivatePlayback"


def current(path):
    checked_directory(path.parent)
    if not os.path.lexists(path):
        return None, None
    return read_regular(path)


def details(data, info):
    if data is None:
        return None
    if info.st_mode & 0o7000:
        raise Blocked("unsafe_file_permissions")
    return {"sha": digest(data), "mode": stat.S_IMODE(info.st_mode),
            "uid": info.st_uid, "gid": info.st_gid}


def permissions(meta):
    return {k: meta[k] for k in ("mode", "uid", "gid")}


def validate_metadata(value):
    if (not isinstance(value, dict) or set(value) != {"sha", "mode", "uid", "gid"}
            or not isinstance(value["sha"], str) or not SHA64.fullmatch(value["sha"])
            or any(type(value[k]) is not int for k in ("mode", "uid", "gid"))
            or not 0 <= value["mode"] <= 0o777 or min(value["uid"], value["gid"]) < 0):
        raise Blocked("coordinated_metadata_invalid")


def xml_flag(raw):
    root = ET.fromstring(raw)
    if len(root.findall(FLAG)) > 1:
        raise Blocked("duplicate_private_playback_flag")
    node = root.find(FLAG)
    if node is None:
        return None
    value = (node.text or "").strip().lower()
    if value not in ("true", "false"):
        raise Blocked("invalid_private_playback_flag")
    return value


def set_xml_flag(raw, flag):
    root = ET.fromstring(raw)
    xml_flag(raw)
    node = root.find(FLAG)
    if flag is None:
        if node is not None:
            root.remove(node)
    else:
        if node is None:
            node = ET.SubElement(root, FLAG)
        node.text = flag
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


@dataclass(frozen=True)
class Change:
    name: str
    old_sha: str | None
    data: bytes
    mode: int
    uid: int
    gid: int


class CoordinatedTransaction:
    """Coordinated file changes with strict service ordering and recovery.

    No subprocess or service management in this class. The trusted adapter
    must verify actual stops and starts, retain exclusive deployment control,
    check health/admin/network/encoder, and pin the exact release.
    """

    def __init__(self, targets, state: Path, source: str, stop, reload, start, health):
        if tuple(targets) != NAMES or not SHA40.fullmatch(source):
            raise Blocked("coordinated_identity_invalid")
        self.targets = dict(targets)
        self.state, self.source = state, source
        self.save = state / source
        self.stop, self.reload, self.start, self.health = stop, reload, start, health
        checked_directory(state)
        if state.stat().st_uid != os.geteuid() or state.stat().st_mode & 0o077:
            raise Blocked("coordinated_state_not_private")
        if len(set(targets.values())) != len(targets):
            raise Blocked("coordinated_duplicate_targets")
        for path in targets.values():
            checked_directory(path.parent)
            if path.is_relative_to(state) or state.is_relative_to(path):
                raise Blocked("coordinated_targets_overlap_state")

    @contextmanager
    def lock(self):
        checked_directory(self.state)
        fd = os.open(self.state / "coordinated.lock",
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                    or st.st_uid != os.geteuid() or st.st_mode & 0o077):
                raise Blocked("coordinated_lock_invalid")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Blocked("coordinated_already_running") from None
            yield
        finally:
            os.close(fd)

    def write_journal(self, phase, records):
        atomic_write(self.save / "journal.json",
            json.dumps({"source": self.source, "phase": phase,
                        "records": records}, sort_keys=True).encode("ascii"),
            0o600, os.geteuid(), os.getegid())

    def backup(self, name, side, meta):
        raw, info = read_regular(self.save / side / name)
        if (info.st_uid != os.geteuid() or info.st_mode & 0o077
                or digest(raw) != meta["sha"]):
            raise Blocked("coordinated_backup_invalid")
        return raw

    def load(self):
        checked_directory(self.save)
        if self.save.stat().st_uid != os.geteuid() or self.save.stat().st_mode & 0o077:
            raise Blocked("coordinated_save_not_private")
        raw, st = read_regular(self.save / "journal.json", 65536)
        if st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise Blocked("coordinated_journal_permissions")
        data = json.loads(raw)
        if set(data) != {"source", "phase", "records"} or data["source"] != self.source:
            raise Blocked("coordinated_journal_identity")
        if data["phase"] not in ("prepared", "installing", "restoring", "committed", "rolled_back"):
            raise Blocked("coordinated_journal_phase")
        records = data["records"]
        if set(records) != set(NAMES):
            raise Blocked("coordinated_journal_names")
        for name, item in records.items():
            if set(item) != {"old", "new"} or (item["old"] is None) != (name in NEW_ONLY):
                raise Blocked("coordinated_journal_record")
            for side in ("old", "new"):
                meta = item[side]
                if meta is not None:
                    validate_metadata(meta)
                    self.backup(name, side, meta)
        return data["phase"], records

    def prepare(self, changes):
        if tuple(c.name for c in changes) != NAMES:
            raise Blocked("coordinated_change_set")
        if os.path.lexists(self.save):
            raise Blocked("coordinated_existing_transaction")
        self.health("old")
        snapshots, records = {}, {}
        for c in changes:
            raw, info = current(self.targets[c.name])
            old = details(raw, info)
            if (old is None) != (c.name in NEW_ONLY) or (None if old is None else old["sha"]) != c.old_sha:
                raise Blocked("coordinated_original_changed")
            if not isinstance(c.data, bytes) or not 0 < len(c.data) <= 64 * 1024**2:
                raise Blocked("coordinated_candidate_bytes")
            new = {"sha": digest(c.data), "mode": c.mode, "uid": c.uid, "gid": c.gid}
            validate_metadata(new)
            if old and permissions(old) != permissions(new):
                raise Blocked("coordinated_original_permissions")
            if c.name == "plugin.xml" and (xml_flag(raw) not in (None, "false")
                    or c.data != set_xml_flag(raw, "true")):
                raise Blocked("coordinated_xml_not_exact_flag_edit")
            if c.name in ("media-key", "media-env") and c.mode & 0o077:
                raise Blocked("coordinated_secret_permissions")
            snapshots[c.name] = (raw, c.data)
            records[c.name] = {"old": old, "new": new}
        self.save.mkdir(mode=0o700)
        sync_directory(self.state)
        for side in ("old", "new"):
            (self.save / side).mkdir(mode=0o700)
        sync_directory(self.save)
        for name, (old, new) in snapshots.items():
            for side, raw in (("old", old), ("new", new)):
                if raw is not None:
                    atomic_write(self.save / side / name, raw, 0o600,
                                 os.geteuid(), os.getegid())
        self.write_journal("prepared", records)
        return records

    def check(self, records, side, *, after_start=False):
        for name, item in records.items():
            raw, info = current(self.targets[name])
            observed = details(raw, info)
            expected = item[side]
            if name == "plugin.xml" and after_start:
                if observed is None or permissions(observed) != permissions(expected):
                    raise Blocked("coordinated_xml_permissions_changed")
                flag = xml_flag(raw)
                if (side == "new" and flag != "true") or (side == "old" and flag not in (None, "false")):
                    raise Blocked("coordinated_xml_flag_changed")
            elif observed != expected:
                raise Blocked("coordinated_file_identity_mismatch")

    def plan_restore(self, records):
        # Verify ALL destinations before any replacement. For immutable
        # files refuse unrelated edits. The plugin writes slot ownership
        # itself; on rollback preserve its runtime fields and revert only
        # our opt-in flag if serialization changed the file.
        actions = {}
        for name, item in records.items():
            path = self.targets[name]
            raw, st = current(path)
            now = details(raw, st)
            old, new = item["old"], item["new"]
            backup = None if old is None else self.backup(name, "old", old)
            if name == "plugin.xml" and now not in (old, new):
                if now is None or permissions(now) != permissions(old):
                    raise Blocked("coordinated_xml_unrelated_change")
                if xml_flag(raw) not in (None, "false", "true"):
                    raise Blocked("coordinated_xml_unrelated_change")
                backup = set_xml_flag(raw, xml_flag(backup))
            elif now not in (old, new):
                raise Blocked("coordinated_unrelated_change")
            actions[name] = (backup, old)
        return actions

    def restore_locked(self):
        phase, records = self.load()
        if phase == "rolled_back":
            raise Blocked("coordinated_already_restored")
        self.plan_restore(records)
        self.write_journal("restoring", records)
        self.stop()  # adapter: Jellyfin, then backend; confirmed stopped
        actions = self.plan_restore(records)
        for name, (raw, old) in actions.items():
            path = self.targets[name]
            if raw is None:
                if os.path.lexists(path):
                    path.unlink()
                    sync_directory(path.parent)
            else:
                atomic_write(path, raw, **permissions(old))
        self.check(records, "old", after_start=True)
        self.reload()
        self.start()  # adapter: backend, then Jellyfin
        self.health("old")
        self.check(records, "old", after_start=True)
        self.write_journal("rolled_back", records)
        return "rolled_back"

    def activate(self, changes):
        with self.lock():
            records = self.prepare(changes)
            try:
                self.write_journal("installing", records)
                self.stop()
                self.check(records, "old")
                for name in NAMES:
                    new = records[name]["new"]
                    raw = self.backup(name, "new", new)
                    atomic_write(self.targets[name], raw, **permissions(new))
                self.check(records, "new")
                self.reload()
                self.start()
                self.health("new")
                self.check(records, "new", after_start=True)
                self.write_journal("committed", records)
                return "committed"
            except (Exception, KeyboardInterrupt):
                try:
                    self.restore_locked()
                except Exception:
                    raise Blocked("coordinated_recovery_requires_review") from None
                return "rolled_back_after_failure"

    def rollback(self):
        with self.lock():
            return self.restore_locked()
