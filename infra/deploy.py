#!/usr/bin/env python3
"""Controlled first installation of Vivoapk; preflight is read-only.

Authoritative source: martaxi-boss/Vivoapk. First install only: never overwrite
an existing project, PostgreSQL role/database, systemd unit or private vault.
Does not change PINK, Jellyfin, Nginx, certificates or network firewall.
The Jellyfin authentication/catalog plugin is a separate, unimplemented step.
"""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit

REPO = "https://github.com/martaxi-boss/Vivoapk.git"
ROOT = Path("/srv/projects/Vivoapk")
SOURCE = ROOT / "source"
VENV = ROOT / ".venv"
RUN_DIR = ROOT / "run"
SOCKET = RUN_DIR / "backend.sock"
VAULT = Path("/root/project-secrets/VIVOAPK")
AUTH = VAULT / "backend-auth.env"
CSV = VAULT / "subscriber-mappings.csv"
RUNTIME = VAULT / "backend-runtime.env"
UNIT = Path("/etc/systemd/system/vivoapk-backend.service")
SERVICE = "vivoapk-backend.service"
FIELDS = ("mega_subscription_id", "username", "dns_link", "dns_link_samsung_lg", "expiring_at", "last_synced_at")

class Blocked(Exception):
    pass

def reject(reason):
    raise Blocked(reason)

def run(args, *, data=None, env=None, cwd=None, timeout=45):
    try:
        p = subprocess.run(args, input=data, capture_output=True, env=env, cwd=cwd, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        reject("command_unavailable_or_timed_out")
    if p.returncode != 0:
        reject("required_command_failed")
    return p.stdout.decode(errors="replace").strip()

def active(name):
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", name],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

def config():
    if not AUTH.is_file() or AUTH.is_symlink():
        reject("auth_source_missing")
    result = {}
    for line in AUTH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            reject("auth_format_invalid")
        k, v = line.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            reject("auth_format_invalid")
        try:
            fields = shlex.split(v)
        except ValueError:
            reject("auth_format_invalid")
        if len(fields) != 1:
            reject("auth_format_invalid")
        result[k] = fields[0]
    for k in ("SESSION_SIGNING_KEY", "MEGA_OTT_API_TOKEN", "MEGA_OTT_API_BASE"):
        if not result.get(k):
            reject("auth_missing_required_key")
    if len(result["SESSION_SIGNING_KEY"].encode()) < 32:
        reject("invalid_signing_key")
    api = urlsplit(result["MEGA_OTT_API_BASE"])
    if api.scheme != "https" or not api.hostname or api.username or api.password or api.query or api.fragment:
        reject("invalid_api_origin")
    return result

def snapshot():
    if not CSV.is_file() or CSV.is_symlink() or CSV.stat().st_size > 4_000_000:
        reject("mapping_snapshot_unavailable")
    raw = CSV.read_bytes()
    try:
        d = csv.DictReader(io.StringIO(raw.decode()))
        if tuple(d.fieldnames or ()) != FIELDS:
            reject("mapping_schema_mismatch")
        rows = list(d)
    except (UnicodeDecodeError, csv.Error):
        reject("mapping_schema_invalid")
    if len(rows) > 10000:
        reject("too_many_mappings")
    ids, users = set(), set()
    for row in rows:
        try:
            i = int(row["mega_subscription_id"])
        except (ValueError, TypeError):
            reject("invalid_subscription_id")
        if i in ids or not row["username"] or row["username"] in users:
            reject("duplicate_subscription_identity")
        ids.add(i)
        users.add(row["username"])
        for key in ("dns_link", "dns_link_samsung_lg"):
            value = row.get(key, "")
            if not value and key == "dns_link":
                reject("missing_generic_dns")
            if value:
                try:
                    u = urlsplit(value)
                except ValueError:
                    reject("malformed_provider_origin")
                if (u.scheme not in ("http", "https") or not u.hostname
                    or u.username or u.password or u.query or u.fragment or u.path not in ("", "/")):
                    reject("malformed_provider_origin")
    return raw, len(rows), sum(bool(r["dns_link_samsung_lg"]) for r in rows)

def pg(sql):
    return run(["sudo", "-n", "-u", "postgres", "psql", "-X", "-A", "-t",
                "-v", "ON_ERROR_STOP=1", "-d", "postgres"], data=sql.encode(), timeout=20)

def preflight():
    if os.geteuid() != 0:
        reject("root_required")
    if ROOT.exists() or ROOT.is_symlink() or UNIT.exists() or UNIT.is_symlink():
        reject("existing_project_or_service_requires_manual_review")
    if RUNTIME.exists() or RUNTIME.is_symlink():
        reject("existing_private_runtime_requires_manual_review")
    if not VAULT.is_dir() or stat.S_IMODE(VAULT.stat().st_mode) != 0o700:
        reject("vault_permissions_invalid")
    for p in (AUTH, CSV):
        if stat.S_IMODE(p.stat().st_mode) != 0o600:
            reject("vault_secret_permissions_invalid")
    for s in ("jellyfin-pink.service", "pink-iptv-backend.service", "pink-vpn.service",
              "nginx.service", "postgresql.service"):
        if not active(s):
            reject("protected_service_not_active")
    if shutil.disk_usage("/srv/projects").free < 6 * 1024**3:
        reject("insufficient_disk")
    if pg("SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname='vivoapk_svc') THEN 'yes' ELSE 'no' END;\n") != "no":
        reject("database_role_exists")
    if pg("SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_database WHERE datname='vivoapk') THEN 'yes' ELSE 'no' END;\n") != "no":
        reject("database_exists")

def exclusive(path, payload, mode=0o600):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w") as f:
        f.write(payload)
    os.chmod(path, mode)

def quoted(value):
    if any(c in value for c in ("\x00", "\r", "\n")):
        reject("unsupported_secret_characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

def install(sha, auth, raw, count, tv):
    if not sha or not re.fullmatch(r"[0-9a-f]{40}", sha):
        reject("invalid_pinned_commit")
    ROOT.mkdir(mode=0o755)
    run(["git", "clone", "--quiet", "--depth", "1", "--branch", "main", REPO, str(SOURCE)], timeout=120)
    actual = run(["git", "-C", str(SOURCE), "rev-parse", "HEAD"])
    if actual != sha:
        reject("source_commit_mismatch_no_deploy")
    run(["python3", "-m", "venv", str(VENV)], timeout=55)
    run([str(VENV/"bin/pip"), "install", "--disable-pip-version-check",
         "--no-cache-dir", "--no-input", "-e", str(SOURCE/"backend")], timeout=240)
    if subprocess.run(["id", "-u", "vivoapksvc"], capture_output=True).returncode != 0:
        run(["useradd", "--system", "--user-group", "--home-dir", str(ROOT),
             "--shell", "/usr/sbin/nologin", "--no-create-home", "vivoapksvc"])
    RUN_DIR.mkdir(mode=0o2770)
    run(["chown", "vivoapksvc:www-data", str(RUN_DIR)])
    os.chmod(RUN_DIR, 0o2770)
    password = secrets.token_urlsafe(36)
    # Password is provided via stdin, never as a command line arg or log.
    pg("CREATE ROLE vivoapk_svc LOGIN PASSWORD '" + password + "' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;\n")
    pg("CREATE DATABASE vivoapk OWNER vivoapk_svc;\n")
    db_url = "postgresql+psycopg://vivoapk_svc:" + password + "@127.0.0.1:5432/vivoapk"
    env = {"APP_ENV": "production", "DATABASE_URL": db_url,
           "MEGA_OTT_API_TOKEN": auth["MEGA_OTT_API_TOKEN"],
           "MEGA_OTT_API_BASE": auth["MEGA_OTT_API_BASE"],
           "SESSION_SIGNING_KEY": auth["SESSION_SIGNING_KEY"],
           "SESSION_TTL_SECONDS": auth.get("SESSION_TTL_SECONDS", "300")}
    exclusive(RUNTIME, "".join(k + "=" + quoted(v) + "\n" for k, v in env.items()))
    run([str(VENV/"bin/alembic"), "upgrade", "head"], cwd=SOURCE/"backend",
        env={**os.environ, **env}, timeout=90)
    pg_env = {**os.environ, "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "8"}
    common = ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1",
              "-U", "vivoapk_svc", "-d", "vivoapk"]
    query = "COPY public.subscription_mappings (" + ",".join(FIELDS) + ") FROM STDIN WITH CSV HEADER"
    run(common + ["-c", query], data=raw, env=pg_env, timeout=40)
    counts = run(common + ["-t", "-A", "-c",
                 "SELECT COUNT(*),COUNT(dns_link_samsung_lg) FROM public.subscription_mappings"],
                 env=pg_env, timeout=12)
    if counts != str(count) + "|" + str(tv):
        reject("subscriber_import_inconsistent")
    unit = (f"[Unit]\nDescription=VivoAPK Mega OTT API (private, no media)\n"
            f"After=network-online.target postgresql.service\nRequires=postgresql.service\n"
            f"[Service]\nType=simple\nUser=vivoapksvc\nGroup=vivoapksvc\n"
            f"UMask=0007\nWorkingDirectory={SOURCE}/backend\n"
            f"EnvironmentFile={RUNTIME}\n"
            f"ExecStart={VENV}/bin/uvicorn app.main:app --uds {SOCKET} --workers 1\n"
            f"ExecStartPost={VENV}/bin/python {SOURCE}/infra/secure_runtime_socket.py\n"
            f"Restart=on-failure\nRestartSec=5\nNoNewPrivileges=true\nPrivateTmp=true\n"
            f"ProtectHome=true\nProtectSystem=strict\nProtectKernelTunables=true\n"
            f"ProtectControlGroups=true\nReadWritePaths={RUN_DIR}\n"
            f"[Install]\nWantedBy=multi-user.target\n")
    exclusive(UNIT, unit, 0o644)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", SERVICE], timeout=40)
    ready = False
    for _ in range(25):
        if SOCKET.is_socket():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(2)
                    sock.connect(str(SOCKET))
                    sock.sendall(b"GET /openapi.json HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                    if b"HTTP/1.1 200 OK" in sock.recv(256) and active(SERVICE):
                        ready = True
                        break
            except OSError:
                pass
        time.sleep(1)
    if not ready:
        reject("vivoapk_private_service_unhealthy")
    return {"status": "installed_private_backend", "sha": sha, "root": str(ROOT),
            "service": SERVICE, "socket": str(SOCKET), "accounts": count,
            "native_tv_dns": tv, "public_route_created": False, "jellyfin_plugin_installed": False,
            "old_services_preserved": True}

def main():
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("mode", choices=("preflight", "install"))
    a.add_argument("--sha")
    opts = a.parse_args()
    try:
        auth = config()
        raw, count, tv = snapshot()
        preflight()
        result = ({"status": "preflight_pass", "root": str(ROOT), "accounts": count,
                   "native_tv_dns": tv, "changes": False}
                  if opts.mode == "preflight" else install(opts.sha, auth, raw, count, tv))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Blocked, OSError) as exc:
        slug = str(exc) if isinstance(exc, Blocked) else "os_error"
        print(json.dumps({"status": "blocked", "reason": slug,
                          "existing_services_not_intentionally_modified": True}, sort_keys=True))
        return 1

if __name__ == "__main__":
    sys.exit(main())
