#!/usr/bin/env python3
"""Guarded one-account VivoAPK native channel pilot (no media relay service).

preflight: verify existing services and checkouts, NO writes.
activate: backup old backend revision, plugin DLL and XML; stage pinned git,
          compile, restart ONLY vivoapk-backend and jellyfin-pink, opt-in pilot;
          automatic rollback on failed postflight.
rollback: restore saved source revision, plugin DLL and XML.
Runs ONLY by explicit Ferrari job over the existing GitHub->VPS bridge.
Secrets never enter argv, GitHub, stdout or configuration changes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from xml.etree import ElementTree

ROOT = Path("/srv/projects/Vivoapk")
SOURCE = ROOT / "source"
JELLY = Path("/srv/projects/jellyfin-server")
PLUGIN = JELLY / "data/plugins/VivoAPK Mega OTT Auth_0.1.0.0"
DLL = PLUGIN / "Jellyfin.Plugin.VivoapkMegaAuth.dll"
CONF = JELLY / "data/plugins/configurations/Jellyfin.Plugin.VivoapkMegaAuth.xml"
ADMIN_DB = JELLY / "data/data/jellyfin.db"
VIVO = "vivoapk-backend.service"
JELLY_SERVICE = "jellyfin-pink.service"
OTHER = ("nginx.service", "postgresql.service")
# PINK backend and VPN were removed; never restart or require them.
RETIRED = ("pink-iptv-backend.service", "pink-vpn.service")
BACKUP_ROOT = ROOT / "backups/live-channel-pilot"
PILOT_MARKER = "VIVOAPK_LIVE_PILOT_V1"
REMOTE = "https://github.com/martaxi-boss/Vivoapk.git"

class Rejected(Exception):
    pass

def run(argv, *, timeout=60, env=None):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    if p.returncode:
        raise Rejected("command_failed")
    return p.stdout.strip()

def active(name):
    return subprocess.run(["systemctl", "is-active", "--quiet", name], timeout=8).returncode == 0

def local_ok(path, endpoint):
    if not path.is_socket():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(4)
            sock.connect(str(path))
            sock.sendall(("GET " + endpoint + " HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n").encode())
            return b"200 OK" in sock.recv(220).split(b"\r\n", 1)[0]
    except OSError:
        return False

def admin_digest():
    if not ADMIN_DB.is_file():
        raise Rejected("admin_db_missing")
    con = sqlite3.connect("file:" + str(ADMIN_DB) + "?mode=ro", uri=True, timeout=5)
    try:
        matches = []
        for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if "user" not in table.lower():
                continue
            escaped = table.replace('"', '""')
            cols = [c[1] for c in con.execute('PRAGMA table_info("' + escaped + '")')]
            for col in cols:
                if col.lower() not in ("username", "name"):
                    continue
                ec = col.replace('"', '""')
                for row in con.execute('SELECT * FROM "' + escaped + '" WHERE "' + ec + '" = ? COLLATE NOCASE', ("pink-admin",)):
                    matches.append((table, cols, row))
        if len(matches) != 1:
            raise Rejected("admin_identity_unverified")
        return hashlib.sha256(repr(matches).encode()).hexdigest()
    finally:
        con.close()

def preflight(expected):
    if os.geteuid() != 0:
        raise Rejected("root_required")
    if not re.fullmatch("[0-9a-f]{40}", expected):
        raise Rejected("full_pinned_sha_required")
    if not SOURCE.is_dir() or not (SOURCE / ".git").is_dir() or SOURCE.is_symlink():
        raise Rejected("source_missing")
    if not PLUGIN.is_dir() or PLUGIN.is_symlink() or DLL.is_symlink() or not DLL.is_file():
        raise Rejected("plugin_missing")
    if CONF.is_symlink() or not CONF.is_file():
        raise Rejected("config_missing")
    if not active(VIVO) or not active(JELLY_SERVICE) or any(not active(n) for n in OTHER):
        raise Rejected("protected_services_unhealthy")
    if any(active(n) for n in RETIRED):
        raise Rejected("retired_pink_service_reactivated")
    if run(["git", "-C", str(SOURCE), "remote", "get-url", "origin"]) != REMOTE:
        raise Rejected("unexpected_source_remote")
    # Existing .NET builds leave untracked bin/obj outputs inside the project.
    # Preserve them, but reject ALL tracked edits or unknown untracked paths.
    # Never run git clean or force an unreviewed checkout in production.
    worktree = run(["git", "-C", str(SOURCE), "status", "--porcelain=v1",
                    "--untracked-files=all"])
    generated_prefixes = (
        "jellyfin-auth/Vivoapk.Jellyfin.Auth/bin/",
        "jellyfin-auth/Vivoapk.Jellyfin.Auth/obj/",
    )
    for entry in worktree.splitlines():
        if not entry:
            continue
        path = entry[3:]
        if (entry.startswith("?? ") and
                not any(part in ("..", ".") for part in path.split("/")) and
                any(path.startswith(prefix) for prefix in generated_prefixes)):
            continue
        raise Rejected("dirty_production_checkout")
    if shutil.disk_usage(ROOT).free < 6 * 1024**3:
        raise Rejected("low_disk_space")
    if not local_ok(ROOT / "run/backend.sock", "/openapi.json") or not local_ok(JELLY / "run/jellyfin.sock", "/System/Info/Public"):
        raise Rejected("existing_socket_unhealthy")
    cfg = ElementTree.parse(CONF).getroot()
    enabled = cfg.find("Enabled")
    if enabled is None or enabled.text is None or enabled.text.strip().lower() != "true":
        raise Rejected("auth_plugin_not_enabled")
    original = run(["git", "-C", str(SOURCE), "rev-parse", "HEAD"])
    digest = admin_digest()
    return original, digest

def atomically_copy(source, target):
    temp = target.with_name(target.name + ".vivo-tmp-" + str(os.getpid()))
    if temp.exists() or temp.is_symlink():
        raise Rejected("unsafe_temp_file")
    try:
        current = target.stat() if target.is_file() else None
        shutil.copy2(source, temp)
        if current is not None:
            os.chown(temp, current.st_uid, current.st_gid)
            os.chmod(temp, current.st_mode & 0o777)
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()

def backup_at(sha):
    return BACKUP_ROOT / sha[:20]

def health(expected_preview):
    if not active(VIVO) or not active(JELLY_SERVICE) or any(not active(n) for n in OTHER):
        raise Rejected("postflight_service_unhealthy")
    if any(active(n) for n in RETIRED):
        raise Rejected("retired_pink_service_reactivated")
    if not local_ok(ROOT / "run/backend.sock", "/openapi.json"):
        raise Rejected("postflight_backend_failed")
    if not local_ok(JELLY / "run/jellyfin.sock", "/System/Info/Public"):
        raise Rejected("postflight_jellyfin_failed")
    # Validate that the source actually includes the preview API implementation.
    main = (SOURCE / "backend/app/main.py").read_text()
    if expected_preview and '/v1/catalog/preview' not in main:
        raise Rejected("preview_backend_source_missing")

def _restore(backup, prior_sha):
    # Only return changed components to their old state; no DB, PINK or global deletion.
    run(["systemctl", "stop", JELLY_SERVICE], timeout=40)
    run(["systemctl", "stop", VIVO], timeout=40)
    run(["git", "-C", str(SOURCE), "checkout", "--detach", "-f", prior_sha], timeout=75)
    atomically_copy(backup / "plugin.dll", DLL)
    atomically_copy(backup / "plugin-config.xml", CONF)
    run(["systemctl", "start", VIVO], timeout=50)
    run(["systemctl", "start", JELLY_SERVICE], timeout=50)
    for _ in range(50):
        try:
            health(expected_preview=False)
            return
        except Rejected:
            time.sleep(1)
    raise Rejected("rollback_services_unhealthy")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "activate", "rollback"))
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    result = {"task": "VivoAPK real native live-channel pilot", "mode": args.mode, "changed": False}
    old_sha = None
    prior_admin = None
    save = backup_at(args.sha)
    try:
        if args.mode == "rollback":
            # Emergency rollback works even when the updated service is down.
            if os.geteuid() != 0 or not re.fullmatch("[0-9a-f]{40}", args.sha):
                raise Rejected("rollback_requires_root_and_sha")
            marker = save / PILOT_MARKER
            if marker.is_symlink() or not marker.is_file():
                raise Rejected("backup_marker_missing")
            prior = marker.read_text().strip()
            if not re.fullmatch("[0-9a-f]{40}", prior):
                raise Rejected("invalid_backup_marker")
            if not (save / "plugin.dll").is_file() or not (save / "plugin-config.xml").is_file():
                raise Rejected("backup_components_missing")
            _restore(save, prior)
            result["status"] = "rolled_back"
            result["changed"] = True
            print(json.dumps(result, sort_keys=True))
            return 0

        old_sha, prior_admin = preflight(args.sha)
        result["original_revision"] = old_sha[:12]
        if args.mode == "preflight":
            result["status"] = "pass"
            print(json.dumps(result, sort_keys=True))
            return 0
        if save.exists() or save.is_symlink():
            raise Rejected("existing_backup_requires_review")
        # One exact public source commit, no mutable floating branch.
        run(["git", "-C", str(SOURCE), "fetch", "--no-tags", "--depth", "1", "origin", args.sha], timeout=110)
        got = run(["git", "-C", str(SOURCE), "rev-parse", "FETCH_HEAD"])
        if got != args.sha:
            raise Rejected("fetched_sha_mismatch")
        save.mkdir(parents=True, mode=0o700)
        os.chmod(save, 0o700)
        shutil.copy2(DLL, save / "plugin.dll")
        shutil.copy2(CONF, save / "plugin-config.xml")
        (save / PILOT_MARKER).write_text(old_sha)
        os.chmod(save / PILOT_MARKER, 0o600)
        try:
            run(["git", "-C", str(SOURCE), "checkout", "--detach", args.sha], timeout=70)
            project = SOURCE / "jellyfin-auth/Vivoapk.Jellyfin.Auth/Vivoapk.Jellyfin.Auth.csproj"
            cache = ROOT / "jellyfin-auth/build-cache"
            cache.mkdir(parents=True, exist_ok=True)
            home = cache / "dotnet-home"
            nuget = cache / "nuget"
            home.mkdir(parents=True, exist_ok=True)
            nuget.mkdir(parents=True, exist_ok=True)
            env = {**os.environ, "DOTNET_CLI_HOME": str(home), "NUGET_PACKAGES": str(nuget),
                   "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
            run(["dotnet", "restore", str(project)], env=env, timeout=160)
            run(["dotnet", "build", str(project), "-c", "Release", "--no-restore"], env=env, timeout=160)
            compiled = project.parent / "bin/Release/net10.0/Jellyfin.Plugin.VivoapkMegaAuth.dll"
            if not compiled.is_file() or compiled.stat().st_size < 6000:
                raise Rejected("compiled_plugin_missing")
            run(["systemctl", "stop", JELLY_SERVICE], timeout=45)
            run(["systemctl", "stop", VIVO], timeout=45)
            atomically_copy(compiled, DLL)
            xml = ElementTree.parse(CONF)
            root = xml.getroot()
            flag = root.find("PilotLiveChannels")
            if flag is None:
                flag = ElementTree.SubElement(root, "PilotLiveChannels")
            flag.text = "true"
            temp = save / "new-config.xml"
            xml.write(temp, encoding="utf-8", xml_declaration=True)
            atomically_copy(temp, CONF)
            run(["systemctl", "start", VIVO], timeout=55)
            run(["systemctl", "start", JELLY_SERVICE], timeout=55)
            for _ in range(60):
                try:
                    health(expected_preview=True)
                    break
                except Rejected:
                    time.sleep(1)
            else:
                raise Rejected("pilot_postflight_timeout")
            if admin_digest() != prior_admin:
                raise Rejected("admin_changed_during_pilot")
            result["status"] = "pilot_enabled"
            result["changed"] = True
            result["pilot_user_cap"] = 1
            result["stream_proxy_installed"] = False
            result["pink_modified"] = False
        except Exception:
            _restore(save, old_sha)
            result["status"] = "rolled_back_after_failure"
            result["changed"] = False
            result["reason"] = "pilot_activation_failed"
    except (Rejected, OSError, subprocess.TimeoutExpired) as exc:
        result["status"] = "blocked"
        result["reason"] = str(exc) if isinstance(exc, Rejected) else type(exc).__name__
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") in ("pass", "pilot_enabled", "rolled_back") else 1

if __name__ == "__main__":
    sys.exit(main())
