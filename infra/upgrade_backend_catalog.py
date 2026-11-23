#!/usr/bin/env python3
"""Guarded VivoAPK backend-only upgrade, invoked by the original Ferrari bridge.

No Jellyfin restarts, no admin operations, no media changes, no credentials read.
Uses one exact Git SHA and can roll back backend code/service if health fails.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path("/srv/projects/Vivoapk")
SOURCE = ROOT / "source"
SOCKET = ROOT / "run/backend.sock"
REMOTE = "https://github.com/martaxi-boss/Vivoapk.git"
JELLYFIN_SOCK = Path("/srv/projects/jellyfin-server/run/jellyfin.sock")
VENV = ROOT / ".venv/bin/python"
VIVO = "vivoapk-backend.service"
JELLY = "jellyfin-pink.service"
PROTECTED = ("nginx.service", "postgresql.service")
RETIRED = ("pink-iptv-backend.service", "pink-vpn.service")
BACKUPS = ROOT / "backups/catalog-backend"
SHA = re.compile(r"^[0-9a-f]{40}$")


class Blocked(Exception):
    pass


def cmd(argv: list[str], timeout: int = 70) -> str:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("subprocess_" + type(exc).__name__) from None
    if p.returncode != 0:
        # Do not print stderr: external URLs can contain private details.
        raise Blocked("command_failed_" + argv[0].rsplit("/", 1)[-1])
    return p.stdout.strip()


def active(name: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", name],
                          timeout=7).returncode == 0


def socket_code(sock: Path, method: str, route: str, payload: str = "") -> int:
    if sock.is_symlink() or not sock.is_socket():
        return 0
    try:
        body = payload.encode("ascii")
        req = (f"{method} {route} HTTP/1.1\r\nHost: localhost\r\n"
               f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
               "Connection: close\r\n\r\n").encode("ascii") + body
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(5)
            conn.connect(str(sock))
            conn.sendall(req)
            line = conn.recv(256).split(b"\r\n", 1)[0].split(b" ")
        return int(line[1]) if len(line) > 1 else 0
    except (OSError, ValueError, IndexError):
        return 0


def healthy(*, require_categories: bool) -> bool:
    if any(not active(s) for s in (VIVO, JELLY) + PROTECTED):
        return False
    if any(active(s) for s in RETIRED):
        return False
    if socket_code(SOCKET, "GET", "/openapi.json") != 200:
        return False
    if socket_code(JELLYFIN_SOCK, "GET", "/System/Info/Public") != 200:
        return False
    if require_categories:
        # Valid bodies without bearer must fail CLOSED (401, not 404/200).
        for path, body in (
            ("/v1/catalog/categories", '{"kind":"live"}'),
            ("/v1/catalog/category-items", '{"kind":"live","category_id":"1"}'),
            ("/v1/catalog/series", '{"category_id":"1","series_id":"1"}'),
            ("/v1/catalog/epg", '{"category_id":"1","stream_id":"1"}'),
        ):
            if socket_code(SOCKET, "POST", path, body) != 401:
                return False
    return True


def preflight(sha: str) -> str:
    if os.geteuid() != 0:
        raise Blocked("root_required")
    if SHA.fullmatch(sha) is None:
        raise Blocked("full_pin_required")
    if (SOURCE.is_symlink() or not (SOURCE / ".git").is_dir()
            or not VENV.is_file() or not SOURCE.is_dir()):
        raise Blocked("backend_source_or_runtime_missing")
    if cmd(["git", "-C", str(SOURCE), "remote", "get-url", "origin"]) != REMOTE:
        raise Blocked("unexpected_repo_remote")
    status = cmd(["git", "-C", str(SOURCE), "status",
                  "--porcelain=v1", "--untracked-files=all"])
    for row in status.splitlines():
        if not row:
            continue
        path = row[3:]
        harmless = (row.startswith("?? ") and ".." not in path.split("/")
                    and ("/__pycache__/" in path or path.endswith(".pyc")
                         or path.startswith("jellyfin-auth/Vivoapk.Jellyfin.Auth/bin/")
                         or path.startswith("jellyfin-auth/Vivoapk.Jellyfin.Auth/obj/")))
        if not harmless:
            raise Blocked("dirty_checkout_review_required")
    if shutil.disk_usage(ROOT).free < 5 * 1024**3:
        raise Blocked("low_disk_space")
    if not healthy(require_categories=False):
        raise Blocked("protected_services_unhealthy")
    prior = cmd(["git", "-C", str(SOURCE), "rev-parse", "HEAD"])
    if SHA.fullmatch(prior) is None:
        raise Blocked("prior_commit_unverified")
    if prior == sha:
        raise Blocked("already_at_expected_revision")
    return prior


def backup_path(sha: str) -> Path:
    return BACKUPS / sha[:24]


def old_revision(sha: str) -> str:
    marker = backup_path(sha) / "original.sha"
    if marker.is_symlink() or not marker.is_file():
        raise Blocked("rollback_marker_missing")
    prior = marker.read_text(encoding="ascii").strip()
    if SHA.fullmatch(prior) is None:
        raise Blocked("rollback_sha_invalid")
    return prior


def rollback(sha: str) -> dict:
    if os.geteuid() != 0 or SHA.fullmatch(sha) is None:
        raise Blocked("rollback_pin_or_permissions_invalid")
    prior = old_revision(sha)
    cmd(["git", "-C", str(SOURCE), "checkout", "--detach", "--force", prior], timeout=80)
    cmd(["systemctl", "restart", VIVO], timeout=50)
    for _ in range(35):
        if healthy(require_categories=False):
            return {"status": "rolled_back", "prior_revision": prior,
                    "backend_healthy": True, "jellyfin_healthy": True}
        time.sleep(1)
    raise Blocked("rollback_backend_unhealthy")


def activate(sha: str) -> dict:
    prior = preflight(sha)
    target = backup_path(sha)
    if target.exists() or target.is_symlink():
        raise Blocked("existing_backup_requires_review")

    # Fetch only pinned Git code. Never accept a floating branch or arbitrary
    # user-supplied URL/command, and never alter the root secret vault.
    cmd(["git", "-C", str(SOURCE), "fetch", "--no-tags", "--depth", "1",
         "origin", sha], timeout=145)
    if cmd(["git", "-C", str(SOURCE), "rev-parse", "FETCH_HEAD"]) != sha:
        raise Blocked("fetched_commit_mismatch")
    for relative in ("backend/app/main.py", "backend/app/services/catalog.py",
                     "backend/app/auth_rate_limit.py"):
        data = cmd(["git", "-C", str(SOURCE), "show",
                    sha + ":" + relative], timeout=20)
        if len(data) < 100:
            raise Blocked("missing_required_backend_source")
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.mkdir(mode=0o700)
    os.chmod(target, 0o700)
    marker = target / "original.sha"
    with marker.open("x", encoding="ascii") as out:
        out.write(prior + "\n")
    os.chmod(marker, 0o600)
    try:
        cmd(["git", "-C", str(SOURCE), "checkout", "--detach", "--force", sha], timeout=90)
        cmd([str(VENV), "-m", "compileall", "-q",
             str(SOURCE / "backend/app")], timeout=40)
        cmd(["systemctl", "restart", VIVO], timeout=60)
        for _ in range(42):
            if healthy(require_categories=True):
                return {"status": "backend_upgraded", "target_revision": sha,
                        "prior_revision": prior, "backend_healthy": True,
                        "jellyfin_healthy": True, "catalog_routes_fail_closed": True,
                        "services_restarted": [VIVO],
                        "stream_proxy_added": False, "pink_modified": False}
            time.sleep(1)
        raise Blocked("upgrade_health_gate_failed")
    except (Blocked, OSError, ValueError):
        try:
            result = rollback(sha)
            result["status"] = "rolled_back_after_failed_upgrade"
            return result
        except Blocked:
            raise Blocked("upgrade_failed_and_rollback_requires_manual_review") from None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "activate", "rollback", "verify"))
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    report: dict = {"task": "vivoapk-backend-catalog-upgrade", "mode": args.mode,
                    "changed": False}
    try:
        if args.mode == "preflight":
            prior = preflight(args.sha)
            report.update({"status": "preflight_pass", "prior_revision": prior,
                           "backend_healthy": True, "jellyfin_healthy": True})
        elif args.mode == "activate":
            report.update(activate(args.sha))
            report["changed"] = report["status"] == "backend_upgraded"
        elif args.mode == "rollback":
            report.update(rollback(args.sha))
            report["changed"] = True
        else:
            report.update({"status": "verified" if healthy(require_categories=True)
                           else "blocked", "backend_healthy": healthy(require_categories=True)})
    except Blocked as exc:
        report.update({"status": "blocked", "reason": str(exc)})
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["status"] in ("preflight_pass", "backend_upgraded",
                                     "rolled_back", "rolled_back_after_failed_upgrade",
                                     "verified") else 1


if __name__ == "__main__":
    sys.exit(main())
