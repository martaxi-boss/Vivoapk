#!/usr/bin/env python3
"""Ferrari read-only native release inventory. No activation or secret reads.

Run this exact reviewed script under the existing bounded Ferrari privileged
inspection, not as a resident daemon. Report fixed fields and reason codes.
The baseline is the independently observed VPS identity (Ferrari r85).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys

ROOT = Path("/srv/projects/Vivoapk")
JELLY = Path("/srv/projects/jellyfin-server")
SOURCE = ROOT / "source"
BACKEND = "vivoapk-backend.service"
SERVER = "jellyfin-pink.service"
PROTECTED = (BACKEND, SERVER, "nginx.service", "postgresql.service")
RETIRED = ("pink-iptv-backend.service", "pink-vpn.service")
EXPECTED_BACKEND = "74b6a02be199a29c305058d2875063eb46c1b61d"
BASELINE = {
    "Jellyfin.Api.dll": (
        "releases/12.4/jellyfin/Jellyfin.Api.dll",
        "6a960ae661b4cd9a902395889892217fecb18a9eca1ed724a7deeb2067964fe5"),
    "Jellyfin.LiveTv.dll": (
        "releases/12.4/jellyfin/Jellyfin.LiveTv.dll",
        "23a22e68fbcb00b71ad3cc7434ba8efee2f8a8239595f0ccd0a575b3049a311d"),
    "Jellyfin.Plugin.VivoapkMegaAuth.dll": (
        "data/plugins/VivoAPK Mega OTT Auth_0.1.0.0/Jellyfin.Plugin.VivoapkMegaAuth.dll",
        "60630b0ab43d4414739f86b047ed4d62358de8c587b878f1aeb6320e0fa813c2"),
}


class Blocked(Exception):
    pass


def cmd(args, timeout=12):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    if p.returncode:
        raise Blocked("read_only_command_failed")
    return p.stdout.strip()


def prop(service, name):
    return cmd(["systemctl", "show", service, "-p", name, "--value"])


def active(service):
    return prop(service, "ActiveState") == "active"


def socket_ok(path, endpoint):
    if path.is_symlink() or not path.is_socket():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(5)
            conn.connect(str(path))
            conn.sendall(("GET " + endpoint + " HTTP/1.1\r\nHost: localhost\r\n"
                          "Connection: close\r\n\r\n").encode("ascii"))
            return conn.recv(256).split(b"\r\n", 1)[0].startswith(b"HTTP/1.1 200 ")
    except OSError:
        return False


def component(path):
    # Same strict path/regular-file contract as the release transaction.
    for parent in (*reversed(path.parents), path.parent):
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise Blocked("unsafe_component_directory")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        initial = os.fstat(stream.fileno())
        if (not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1
                or not 0 < initial.st_size <= 64 * 1024 * 1024):
            raise Blocked("unsafe_component_file")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        final = os.fstat(stream.fileno())
        if (initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns) != (
                final.st_size, final.st_mtime_ns, final.st_ctime_ns):
            raise Blocked("component_changed_while_reading")
        return {"sha256": digest, "bytes": initial.st_size,
                "mode": stat.S_IMODE(initial.st_mode)}


def inspect_components(maps, root=JELLY):
    observed = {}
    for name, (relative, expected) in BASELINE.items():
        target = root / relative
        matching = set()
        for line in maps.splitlines():
            fields = line.split(None, 5)
            if len(fields) == 6:
                path = fields[5]
                if path.endswith(" (deleted)"):
                    path = path.removesuffix(" (deleted)")
                    if Path(path).name == name:
                        raise Blocked("deleted_component_still_mapped")
                if Path(path).name == name:
                    matching.add(path)
        record = component(target)
        record["mapped_exactly"] = matching == {str(target)}
        record["matches_baseline"] = record["sha256"] == expected
        observed[name] = record
    return observed


def known_checkout_status(status):
    # Match the existing deployment guard's narrow generated-file exclusions.
    for entry in status.splitlines():
        if not entry:
            continue
        path = entry[3:]
        if (entry.startswith("?? ") and not any(p in (".", "..") for p in path.split("/"))
                and (path.startswith("jellyfin-auth/Vivoapk.Jellyfin.Auth/bin/")
                     or path.startswith("jellyfin-auth/Vivoapk.Jellyfin.Auth/obj/"))):
            continue
        return False
    return True


def ffmpeg_count(cgroup):
    if cgroup != "/system.slice/" + SERVER:
        raise Blocked("unexpected_jellyfin_control_group")
    root = Path("/sys/fs/cgroup") / cgroup.lstrip("/")
    pids = set()
    # Only the known service's cgroup; no process argument or environment read.
    for file in root.rglob("cgroup.procs"):
        if file.is_symlink():
            raise Blocked("unsafe_cgroup_file")
        pids.update(int(x) for x in file.read_text().split())
        if len(pids) > 1024:
            raise Blocked("cgroup_process_bound")
    count = 0
    for pid in pids:
        try:
            if (Path("/proc") / str(pid) / "comm").read_text().strip().startswith("ffmpeg"):
                count += 1
        except FileNotFoundError:
            continue
    return count


def assess(report):
    reasons = []
    for key in ("root", "services_healthy", "retired_services_inactive",
                "same_pids", "private_network", "private_namespace",
                "backend_socket_ok", "jellyfin_socket_ok", "backend_revision_matches",
                "source_remote_matches", "checkout_clean", "backend_unit_matches"):
        if report.get(key) is not True:
            reasons.append(key)
    if report.get("free_bytes", 0) < 6 * 1024**3:
        reasons.append("insufficient_free_space")
    if report.get("ffmpeg_processes") != 0:
        reasons.append("active_or_unknown_playback")
    if set(report.get("components", {})) != set(BASELINE) or any(
            not p.get("matches_baseline") or not p.get("mapped_exactly")
            for p in report.get("components", {}).values()):
        reasons.append("component_identity_mismatch")
    return reasons


def inspect():
    if os.geteuid() != 0:
        raise Blocked("read_only_root_required")
    before = {s: active(s) for s in PROTECTED}
    pids = {s: int(prop(s, "MainPID")) for s in (SERVER, BACKEND)}
    if min(pids.values()) <= 0:
        raise Blocked("required_main_pid_missing")
    process = Path("/proc") / str(pids[SERVER])
    components = inspect_components((process / "maps").read_text())
    git = ["git", "-c", "safe.directory=" + str(SOURCE), "-C", str(SOURCE)]
    sha = cmd(git + ["rev-parse", "HEAD"])
    if not re.fullmatch("[0-9a-f]{40}", sha):
        raise Blocked("invalid_backend_revision")
    remote = cmd(git + ["remote", "get-url", "origin"])
    clean = known_checkout_status(cmd(git + ["status", "--porcelain=v1",
                                             "--untracked-files=all"]))
    # Read only selected unit metadata; no ExecStart, EnvironmentFile contents,
    # proc environment, subscriber data or secrets are emitted/read.
    unit_matches = (
        prop(BACKEND, "User") == "vivoapksvc"
        and prop(BACKEND, "WorkingDirectory") == str(SOURCE / "backend")
        and prop(SERVER, "User") == "jellyfinpink"
    )
    report = {
        "root": True,
        "components": components,
        "services_healthy": all(before.values()),
        "retired_services_inactive": not any(active(s) for s in RETIRED),
        "private_network": prop(SERVER, "PrivateNetwork") == "yes",
        "private_namespace": (process / "ns/net").stat().st_ino !=
                             Path("/proc/self/ns/net").stat().st_ino,
        "backend_socket_ok": socket_ok(ROOT / "run/backend.sock", "/openapi.json"),
        "jellyfin_socket_ok": socket_ok(JELLY / "run/jellyfin.sock", "/System/Info/Public"),
        "backend_revision": sha,
        "backend_revision_matches": sha == EXPECTED_BACKEND,
        "source_remote_matches": remote == "https://github.com/martaxi-boss/Vivoapk.git",
        "checkout_clean": clean,
        "backend_unit_matches": unit_matches,
        "free_bytes": shutil.disk_usage(ROOT).free,
        "ffmpeg_processes": ffmpeg_count(prop(SERVER, "ControlGroup")),
    }
    report["same_pids"] = all(int(prop(s, "MainPID")) == pid for s, pid in pids.items())
    report["services_healthy"] &= all(active(s) for s in PROTECTED)
    report["baseline_blockers"] = assess(report)
    report["baseline_pass"] = not report["baseline_blockers"]
    report["activation_allowed"] = False
    report["activation_blockers"] = [
        "no_approved_regular_native_playback_release",
        "coordinated_backend_configuration_and_ffmpeg_rollback_not_certified",
        "authorized_real_client_playback_not_certified",
    ]
    report["scope"] = "read_only_no_install_no_restart_no_secret_read"
    return report


def main():
    try:
        report = inspect()
    except (Blocked, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"baseline_pass": False, "activation_allowed": False,
                          "error": str(exc) if isinstance(exc, Blocked) else type(exc).__name__,
                          "scope": "read_only_no_install_no_restart"}), flush=True)
        return 1
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["baseline_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
