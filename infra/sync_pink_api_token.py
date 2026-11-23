#!/usr/bin/env python3
"""One-time, root-only PINK -> VivoAPK Mega token alignment.

Never print credentials, usernames, hostnames or account details. The PINK
source remains read-only. The VivoAPK files are backed up privately, updated
atomically, and only the VivoAPK backend is restarted. An unauthorized or
unhealthy destination is restored from backup automatically.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shlex
import socket
import stat
import subprocess
import sys

PINK = Path("/srv/projects/pink-iptv/private/systemd-env")
COFRE = Path("/root/project-secrets/VIVOAPK")
SOURCE = PINK / "secrets.env"
SOURCE_BASE = PINK / "backend.env"
AUTH = COFRE / "backend-auth.env"
RUNTIME = COFRE / "backend-runtime.env"
BACKUPS = COFRE / "credential-backups"
VIVO_SERVICE = "vivoapk-backend.service"
PINK_SERVICE = "pink-iptv-backend.service"
SOCKET = Path("/srv/projects/Vivoapk/run/backend.sock")

class Blocked(RuntimeError):
    pass

def env_value(blob: str, key: str) -> str:
    matches = []
    for line in blob.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, value = line.split("=", 1)
        if k.startswith("export "):
            k = k[7:].strip()
        if k == key:
            try:
                words = shlex.split(value)
            except ValueError as exc:
                raise Blocked("invalid_env_quoting") from exc
            if len(words) != 1:
                raise Blocked("invalid_env_value")
            matches.append(words[0])
    if len(matches) != 1 or not matches[0]:
        raise Blocked("missing_or_ambiguous_"+key.lower())
    return matches[0]

def replace_token(blob: str, token: str) -> str:
    if not token or any(c in token for c in ("\x00", "\r", "\n")):
        raise Blocked("invalid_token")
    lines = blob.splitlines(keepends=True)
    found = 0
    for i, line in enumerate(lines):
        if line.lstrip().startswith("MEGA_OTT_API_TOKEN="):
            found += 1
            lines[i] = "MEGA_OTT_API_TOKEN=" + shlex.quote(token) + "\n"
    if found != 1:
        raise Blocked("target_token_field_not_unique")
    return "".join(lines)

def checked_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise Blocked("unsafe_secret_file_permissions")
    return path.read_text()

def cmd(args: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("command_unavailable") from exc

def is_active(service: str) -> bool:
    return cmd(["systemctl", "is-active", "--quiet", service], timeout=8).returncode == 0

def runtime_token(service: str) -> str:
    p = cmd(["systemctl", "show", "--property=MainPID", "--value", service], timeout=8)
    if p.returncode != 0:
        raise Blocked("cannot_inspect_service")
    try:
        pid = int(p.stdout.strip())
    except ValueError as exc:
        raise Blocked("invalid_service_pid") from exc
    if pid < 2:
        raise Blocked("service_pid_not_running")
    vals = [
        part.split(b"=", 1)[1].decode()
        for part in (Path("/proc")/str(pid)/"environ").read_bytes().split(bytes([0]))
        if part.startswith(b"MEGA_OTT_API_TOKEN=")
    ]
    if len(vals) != 1 or not vals[0]:
        raise Blocked("service_missing_effective_token")
    return vals[0]

def valid_mega_list(base: str, token: str) -> bool:
    import httpx  # the VivoAPK private Python environment supplies httpx
    if not base.startswith("https://") or not token:
        return False
    with httpx.Client(
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        timeout=12, follow_redirects=False, trust_env=False
    ) as client:
        try:
            r = client.get(base.rstrip("/")+"/v1/subscriptions", params={"page": "1", "per_page": "1"})
            return r.status_code == 200 and "json" in r.headers.get("content-type", "").lower()
        except httpx.RequestError:
            return False

def api_socket_ok() -> bool:
    if not SOCKET.is_socket():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(str(SOCKET))
            s.sendall(b"GET /openapi.json HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            return b"HTTP/1.1 200 OK" in s.recv(256)
    except OSError:
        return False

def write_atomic(path: Path, content: bytes) -> None:
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(8) + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise Blocked("temp_name_collision")
    descriptor = os.open(str(temporary), os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as writer:
            writer.write(content)
            writer.flush()
            os.fsync(writer.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()

def backup_file(path: Path, backup: Path) -> None:
    descriptor = os.open(str(backup), os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as writer:
        writer.write(path.read_bytes())
        writer.flush()
        os.fsync(writer.fileno())

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "apply"))
    args = parser.parse_args()
    state = {"action":"pink_api_key_alignment","mode":args.mode,"pink_modified":False}
    backups = {}
    try:
        if os.geteuid() != 0:
            raise Blocked("root_required")
        if (not COFRE.is_dir() or COFRE.is_symlink() or
                stat.S_IMODE(COFRE.stat().st_mode) != 0o700):
            raise Blocked("cofre_not_private")
        for service in (PINK_SERVICE, "pink-vpn.service", "jellyfin-pink.service",
                        "nginx.service", "postgresql.service", VIVO_SERVICE):
            if not is_active(service):
                raise Blocked("protected_service_unhealthy")
        source = checked_file(SOURCE)
        pink_base = env_value(checked_file(SOURCE_BASE), "MEGA_OTT_API_BASE")
        token = env_value(source, "MEGA_OTT_API_TOKEN")
        current = runtime_token(PINK_SERVICE)
        if token != current:
            raise Blocked("pink_file_not_running_token")
        auth = checked_file(AUTH)
        runtime = checked_file(RUNTIME)
        if env_value(auth, "MEGA_OTT_API_BASE").rstrip("/") != pink_base.rstrip("/"):
            raise Blocked("auth_api_base_conflict")
        if env_value(runtime, "MEGA_OTT_API_BASE").rstrip("/") != pink_base.rstrip("/"):
            raise Blocked("runtime_api_base_conflict")
        if not valid_mega_list(pink_base, token):
            raise Blocked("pink_original_key_not_authorized_for_subscription_listing")
        state["pink_api_list_authorized"] = True
        state["was_already_equal"] = (env_value(auth, "MEGA_OTT_API_TOKEN") == token
                                      and env_value(runtime, "MEGA_OTT_API_TOKEN") == token
                                      and runtime_token(VIVO_SERVICE) == token)
        if args.mode == "check":
            state["status"] = "ready" if not state["was_already_equal"] else "already_aligned"
            state["changed"] = False
            print(json.dumps(state,sort_keys=True))
            return 0
        if state["was_already_equal"]:
            state["status"]="already_aligned"
            state["changed"]=False
            print(json.dumps(state,sort_keys=True))
            return 0
        if BACKUPS.exists() and (BACKUPS.is_symlink() or not BACKUPS.is_dir()):
            raise Blocked("backup_path_invalid")
        BACKUPS.mkdir(mode=0o700,exist_ok=True)
        os.chmod(BACKUPS,0o700)
        stamp=secrets.token_hex(8)
        for path in (AUTH,RUNTIME):
            backup=BACKUPS/(stamp+"-"+path.name)
            backup_file(path,backup)
            backups[path]=backup
        try:
            write_atomic(AUTH,replace_token(auth,token).encode())
            write_atomic(RUNTIME,replace_token(runtime,token).encode())
            if cmd(["systemctl","restart",VIVO_SERVICE],timeout=30).returncode!=0:
                raise Blocked("vivo_restart_failed")
            if not is_active(VIVO_SERVICE) or not api_socket_ok():
                raise Blocked("vivo_health_failed")
            if runtime_token(VIVO_SERVICE)!=token:
                raise Blocked("vivo_effective_key_not_aligned")
            if not valid_mega_list(pink_base,token):
                raise Blocked("vivo_api_validation_failed")
            for service in (PINK_SERVICE,"pink-vpn.service","jellyfin-pink.service",
                            "nginx.service","postgresql.service"):
                if not is_active(service):
                    raise Blocked("protected_service_changed")
        except Exception:
            for dest,backup in backups.items():
                write_atomic(dest,backup.read_bytes())
            cmd(["systemctl","restart",VIVO_SERVICE],timeout=30)
            raise Blocked("apply_failed_rolled_back")
        state.update({"status":"applied_verified","changed":True,
                      "vivo_effective_key_matches_pink":True,
                      "vivo_api_listing_authorized":True,
                      "private_backups_created":len(backups)==2,
                      "jellyfin_modified":False,"pink_service_preserved":True})
        print(json.dumps(state,sort_keys=True))
        return 0
    except (Blocked, OSError, ValueError) as exc:
        state["status"]="blocked"
        state["reason"]=str(exc) if isinstance(exc,Blocked) else "runtime_error"
        state["changed"]="unknown" if backups else False
        print(json.dumps(state,sort_keys=True))
        return 1

if __name__ == "__main__":
    sys.exit(main())
