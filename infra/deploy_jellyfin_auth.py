#!/usr/bin/env python3
"""Ferrari-managed Jellyfin 12.4 VivoAPK auth deployment.

Modes:
  stage --sha GIT_SHA     Obtain an exact GitHub revision and build/test the plugin
  install --sha GIT_SHA   Snapshot Jellyfin and install the plugin disabled
  activate --sha GIT_SHA Activate authentication only, protecting the admin identity
  verify --sha GIT_SHA   Read-only postflight
  rollback --sha GIT_SHA Restore saved Jellyfin state, leave PINK/Vivo backend intact

No customer credentials, API tokens, media or provider DNS are read or logged.
No root/global package installation; the existing .NET SDK uses per-project caches.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import grp
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
from xml.etree import ElementTree as ET

ROOT = Path("/srv/projects/Vivoapk")
SOURCE = ROOT / "source"
JELLY = Path("/srv/projects/jellyfin-server")
PLUGIN_NAME = "VivoAPK Mega OTT Auth"
PLUGIN_GUID = "e7a31bad-3643-4da2-834e-559c39cc90b1"
DLL = "Jellyfin.Plugin.VivoapkMegaAuth.dll"
VERSION = "0.1.0.0"
PLUGIN = JELLY / "data/plugins" / (PLUGIN_NAME + "_" + VERSION)
CONF = JELLY / "data/plugins/configurations/Jellyfin.Plugin.VivoapkMegaAuth.xml"
JELLY_SOCKET = JELLY / "run/jellyfin.sock"
VIVO_SOCKET = ROOT / "run/backend.sock"
SERVICE = "jellyfin-pink.service"
OTHERS = ("pink-iptv-backend.service", "pink-vpn.service", "vivoapk-backend.service", "nginx.service", "postgresql.service")
SOURCE_REMOTE = "https://github.com/martaxi-boss/Vivoapk.git"
ADMIN_NAME = "pink-admin"
DB = JELLY / "data/data/jellyfin.db"
ROOT_RELEASES = ROOT / "jellyfin-auth/releases"
ROOT_BACKUPS = ROOT / "backups/jellyfin-auth"

class Blocked(Exception):
    pass

def fail(reason: str):
    raise Blocked(reason)

def cmd(argv, *, timeout=45, env=None, cwd=None):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("command_unavailable_or_timeout") from exc
    if p.returncode:
        raise Blocked("command_failed")
    return p.stdout.strip()

def active(name: str) -> bool:
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", name],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=8).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

def socket_http_ok(path: Path, uri: str) -> bool:
    if not path.is_socket():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(4)
            sock.connect(str(path))
            sock.sendall(("GET " + uri + " HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n").encode())
            return b"200 OK" in sock.recv(280).split(b"\r\n", 1)[0]
    except OSError:
        return False

def ensure_health():
    for s in (SERVICE,) + OTHERS:
        if not active(s):
            fail("required_service_inactive")
    if not socket_http_ok(JELLY_SOCKET, "/System/Info/Public"):
        fail("jellyfin_socket_unhealthy")
    if not socket_http_ok(VIVO_SOCKET, "/openapi.json"):
        fail("vivoapk_socket_unhealthy")
    isolation = cmd(["systemctl", "show", SERVICE, "-p", "PrivateNetwork", "--value"])
    if isolation != "yes":
        fail("jellyfin_network_isolation_missing")
    if shutil.disk_usage("/srv/projects").free < 5 * 2**30:
        fail("insufficient_free_space")
    if not DB.is_file():
        fail("jellyfin_database_missing")

def sha_ok(sha):
    if not re.fullmatch(r"[a-f0-9]{40}", sha or ""):
        fail("full_git_sha_required")
    return sha

def release_dir(sha):
    return ROOT_RELEASES / sha[:16]

def backup_dir(sha):
    return ROOT_BACKUPS / sha[:16]

def check_paths():
    if os.geteuid() != 0:
        fail("root_required")
    if not SOURCE.is_dir() or not (SOURCE / ".git").exists():
        fail("vivoapk_source_git_missing")
    if PLUGIN.is_symlink() or CONF.is_symlink():
        fail("suspicious_plugin_or_config_symlink")
    if not (JELLY / "data/plugins").is_dir():
        fail("jellyfin_plugin_directory_missing")
    if not (JELLY / "data/plugins/configurations").is_dir():
        fail("jellyfin_plugin_config_directory_missing")
    if JELLY.is_symlink() or ROOT.is_symlink():
        fail("unsafe_project_path")
    if not pwd.getpwnam("jellyfinpink"):
        fail("jellyfin_user_unavailable")

def plugin_snapshot():
    if not PLUGIN.is_dir():
        return False
    if not (PLUGIN / DLL).is_file() or not (PLUGIN / "meta.json").is_file():
        fail("unexpected_partial_plugin_install")
    meta = json.loads((PLUGIN / "meta.json").read_text())
    if meta.get("guid") != PLUGIN_GUID or meta.get("targetAbi") != "12.4.0.0":
        fail("wrong_installed_plugin_manifest")
    return True

def admin_fingerprint():
    """Only a one-way digest of the local admin row; no hash or account data printed."""
    conn = sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True, timeout=6)
    try:
        tables = [x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        found = []
        for table in tables:
            if "user" not in table.casefold():
                continue
            esc = table.replace('"', '""')
            cols = [x[1] for x in conn.execute('PRAGMA table_info("' + esc + '")')]
            for col in cols:
                if col.casefold() not in ("username", "name"):
                    continue
                c = col.replace('"', '""')
                rows = conn.execute('SELECT * FROM "'+esc+'" WHERE "'+c+'" = ? COLLATE NOCASE',
                                    (ADMIN_NAME,)).fetchall()
                if rows:
                    found.append((table, cols, rows))
        if len(found) != 1 or len(found[0][2]) != 1:
            fail("cannot_certify_existing_admin_identity")
        return hashlib.sha256(repr(found).encode()).hexdigest()
    finally:
        conn.close()

def plugin_log_loaded():
    try:
        output = cmd(["journalctl", "-u", SERVICE, "--since", "-12 minutes",
                      "-n", "700", "--no-pager", "-o", "cat"], timeout=15)
    except Blocked:
        return False
    return ("Loaded plugin: "+PLUGIN_NAME in output or
            "Loaded assembly Jellyfin.Plugin.VivoapkMegaAuth" in output)

def api_ready(timeout=65):
    for _ in range(int(timeout*2)):
        if active(SERVICE) and socket_http_ok(JELLY_SOCKET, "/System/Info/Public"):
            return True
        time.sleep(0.5)
    return False

def write_owned(path: Path, raw: bytes, mode=0o600):
    if path.exists() or path.is_symlink():
        fail("refuse_overwrite_existing_file")
    f = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(f, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.chmod(path, mode)
    os.chown(path, pwd.getpwnam("jellyfinpink").pw_uid, grp.getgrnam("www-data").gr_gid)

def stage(sha):
    ensure_health()
    if plugin_snapshot():
        fail("plugin_already_installed")
    if cmd(["git", "-C", str(SOURCE), "remote", "get-url", "origin"]) != SOURCE_REMOTE:
        fail("source_remote_mismatch")
    if cmd(["git", "-C", str(SOURCE), "status", "--porcelain"]):
        fail("vivo_source_worktree_not_clean")
    # Upstream is fetched by complete commit ID; no scripts from remote are executed.
    cmd(["git", "-C", str(SOURCE), "fetch", "--no-tags", "--depth", "1", "origin", sha], timeout=120)
    previous_tree = cmd(["git", "-C", str(SOURCE), "rev-parse", "HEAD:backend"])
    proposed_tree = cmd(["git", "-C", str(SOURCE), "rev-parse", sha+":backend"])
    if previous_tree != proposed_tree:
        fail("backend_changes_requires_separate_deployment")
    if cmd(["git", "-C", str(SOURCE), "rev-parse", "HEAD"]) != sha:
        cmd(["git", "-C", str(SOURCE), "checkout", "--detach", sha], timeout=50)
    if cmd(["git", "-C", str(SOURCE), "rev-parse", "HEAD"]) != sha:
        fail("source_revision_mismatch")
    project = SOURCE / "jellyfin-auth/Vivoapk.Jellyfin.Auth/Vivoapk.Jellyfin.Auth.csproj"
    test = SOURCE / "jellyfin-auth/Vivoapk.Jellyfin.Auth.Tests/Vivoapk.Jellyfin.Auth.Tests.csproj"
    manifest_path = SOURCE / "jellyfin-auth/Vivoapk.Jellyfin.Auth/meta.json"
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get("guid") != PLUGIN_GUID or manifest.get("targetAbi") != "12.4.0.0"
            or manifest.get("name") != PLUGIN_NAME or manifest.get("version") != VERSION):
        fail("invalid_source_manifest")
    if not (SOURCE / "jellyfin-auth/Vivoapk.Jellyfin.Auth/PluginConfiguration.cs").read_text().count("Enabled { get; set; } = false;"):
        fail("plugin_must_default_disabled")
    cache = ROOT / "jellyfin-auth/build-cache"
    home = cache / "dotnet-home"
    packages = cache / "nuget"
    home.mkdir(parents=True, exist_ok=True)
    packages.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"DOTNET_CLI_HOME":str(home), "NUGET_PACKAGES":str(packages),
                "DOTNET_SKIP_FIRST_TIME_EXPERIENCE":"1",
                "DOTNET_CLI_TELEMETRY_OPTOUT":"1"})
    cmd(["dotnet", "restore", str(project)], env=env, timeout=210)
    cmd(["dotnet", "build", str(project), "-c", "Release", "--no-restore",
         "-p:ContinuousIntegrationBuild=true"], env=env, timeout=190)
    cmd(["dotnet", "run", "--project", str(test), "--configuration", "Release"], env=env, timeout=200)
    built = SOURCE / "jellyfin-auth/Vivoapk.Jellyfin.Auth/bin/Release/net10.0" / DLL
    if not built.is_file() or built.stat().st_size < 5000:
        fail("compiled_plugin_missing")
    release = release_dir(sha)
    if release.exists() or release.is_symlink():
        fail("release_already_exists")
    release.mkdir(parents=True, mode=0o755)
    shutil.copyfile(built, release / DLL)
    shutil.copyfile(manifest_path, release / "meta.json")
    os.chmod(release / DLL, 0o644)
    os.chmod(release / "meta.json", 0o644)
    return {"status":"staged","source_git_sha":sha,"plugin_dll_sha256":hashlib.sha256((release/DLL).read_bytes()).hexdigest(),
            "synthetic_tests":"passed","global_packages_installed":False,"jellyfin_modified":False}

def do_backup(sha):
    location=backup_dir(sha)
    if location.exists() or location.is_symlink():
        fail("prior_backup_requires_manual_review")
    location.mkdir(parents=True, mode=0o700)
    os.chmod(location, 0o700)
    archive = location / "jellyfin-data-config.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(JELLY/"data", arcname="data")
        tar.add(JELLY/"config", arcname="config")
    os.chmod(archive, 0o600)
    if archive.stat().st_size == 0:
        fail("empty_jellyfin_backup")
    return location

def restore(sha):
    back = backup_dir(sha)/"jellyfin-data-config.tar.gz"
    if back.is_symlink() or not back.is_file():
        fail("rollback_snapshot_unavailable")
    try:
        cmd(["systemctl","stop",SERVICE],timeout=35)
    except Blocked:
        pass
    for path in (JELLY/"data", JELLY/"config"):
        if path.is_symlink() or not path.is_dir():
            fail("rollback_target_invalid")
        shutil.rmtree(path)
    with tarfile.open(back,"r:gz") as tar:
        # Only paths data/ and config/ from our own root-only backup.
        for item in tar.getmembers():
            if not (item.name == "data" or item.name.startswith("data/") or
                    item.name == "config" or item.name.startswith("config/")):
                fail("backup_member_outside_roots")
        tar.extractall(JELLY, filter="data")
    cmd(["systemctl","start",SERVICE],timeout=45)
    if not api_ready():
        fail("rollback_jellyfin_not_healthy")
    for service in OTHERS:
        if not active(service):
            fail("rollback_protected_service_inactive")
    return {"status":"rollback_restored","jellyfin_healthy":True,
            "pink_modified":False,"vivo_backend_modified":False}

def install(sha):
    ensure_health()
    if plugin_snapshot() or CONF.exists():
        fail("existing_plugin_or_configuration")
    release = release_dir(sha)
    if not (release / DLL).is_file() or not (release / "meta.json").is_file():
        fail("staged_release_missing")
    if cmd(["git","-C",str(SOURCE),"rev-parse","HEAD"])!=sha:
        fail("compiled_release_source_mismatch")
    if (backup_dir(sha)).exists():
        fail("backup_already_present")
    before_admin = admin_fingerprint()
    cmd(["systemctl","stop",SERVICE],timeout=35)
    try:
        do_backup(sha)
        PLUGIN.mkdir(mode=0o755)
        uid=pwd.getpwnam("jellyfinpink").pw_uid
        gid=grp.getgrnam("www-data").gr_gid
        os.chown(PLUGIN,uid,gid)
        shutil.copyfile(release/DLL,PLUGIN/DLL)
        shutil.copyfile(release/"meta.json",PLUGIN/"meta.json")
        for name in (DLL,"meta.json"):
            os.chown(PLUGIN/name,uid,gid)
            os.chmod(PLUGIN/name,0o644)
        cmd(["systemctl","start",SERVICE],timeout=40)
        if not api_ready(75) or not plugin_log_loaded():
            fail("plugin_not_loaded_or_jellyfin_unhealthy")
        if admin_fingerprint()!=before_admin:
            fail("admin_database_identity_modified")
        if CONF.exists():
            # Default must be false before activation.
            found=ET.parse(CONF).getroot().findtext("Enabled")
            if found is not None and found.strip().lower() not in ("false","0"):
                fail("unsafe_plugin_default_state")
        for service in OTHERS:
            if not active(service):
                fail("existing_service_changed")
        return {"status":"installed_disabled_verified","plugin_loaded":True,
                "jellyfin_healthy":True,"admin_row_unchanged":True,
                "plugin_enabled":False,"pink_modified":False}
    except Exception:
        if (backup_dir(sha)/"jellyfin-data-config.tar.gz").is_file():
            restore(sha)
        else:
            cmd(["systemctl","start",SERVICE],timeout=45)
        fail("install_failed_rolled_back")

def activate(sha):
    ensure_health()
    if cmd(["git","-C",str(SOURCE),"rev-parse","HEAD"])!=sha:
        fail("source_revision_mismatch")
    if not plugin_snapshot():
        fail("plugin_missing")
    if not (backup_dir(sha)/"jellyfin-data-config.tar.gz").is_file():
        fail("backup_required_before_activation")
    if hashlib.sha256((PLUGIN/DLL).read_bytes()).digest()!=hashlib.sha256((release_dir(sha)/DLL).read_bytes()).digest():
        fail("deployed_plugin_binary_not_matching_release")
    if CONF.exists():
        try:
            flag=ET.parse(CONF).getroot().findtext("Enabled")
        except ET.ParseError:
            fail("plugin_config_invalid")
        if flag and flag.strip().lower() in ("true","1"):
            fail("already_active_refuse_repeat")
    before_admin=admin_fingerprint()
    # A cold-boot config avoids changing the credentials of any existing user.
    cmd(["systemctl","stop",SERVICE],timeout=35)
    try:
        if CONF.exists():
            CONF.unlink()
        write_owned(CONF,b'<?xml version="1.0" encoding="utf-8"?>\n<PluginConfiguration><Enabled>true</Enabled></PluginConfiguration>\n')
        cmd(["systemctl","start",SERVICE],timeout=40)
        if not api_ready(75) or not plugin_log_loaded():
            fail("plugin_activate_health_failed")
        if ET.parse(CONF).getroot().findtext("Enabled","").lower()!="true":
            fail("plugin_enable_config_not_persisted")
        if admin_fingerprint()!=before_admin:
            fail("admin_identity_changed")
        for service in OTHERS:
            if not active(service):
                fail("protected_service_no_longer_active")
        return {"status":"plugin_activated_healthy","enabled_config":True,
                "plugin_loaded":True,"admin_row_unchanged":True,
                "real_mega_login_tested":False,"per_user_catalogue_available":False,
                "pink_modified":False}
    except Exception:
        restore(sha)
        fail("activation_failed_rolled_back")

def verify(sha):
    check_paths()
    ensure_health()
    if not plugin_snapshot():
        fail("plugin_missing")
    if hashlib.sha256((PLUGIN/DLL).read_bytes()).hexdigest()!=hashlib.sha256((release_dir(sha)/DLL).read_bytes()).hexdigest():
        fail("plugin_release_checksum_mismatch")
    enabled=CONF.exists() and ET.parse(CONF).getroot().findtext("Enabled","").lower() in ("true","1")
    return {"status":"installed_verified","plugin_enabled":enabled,"jellyfin_socket_http200":True,
            "vivo_socket_http200":True,"admin_identity_present":bool(admin_fingerprint()),
            "plugin_loaded_recently":plugin_log_loaded(),"protected_services_active":True,
            "live_customer_login_proved":False,"catalogue_not_implemented":True}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode",choices=("preflight","stage","install","activate","verify","rollback"))
    p.add_argument("--sha",required=True)
    args=p.parse_args()
    state={"operation":"jellyfin-vivo-auth-deployment","mode":args.mode}
    try:
        sha=sha_ok(args.sha)
        check_paths()
        if args.mode=="preflight":
            ensure_health()
            state.update({"status":"preflight_pass","source_sha":sha,"changes":False,
                          "plugin_preexisting":plugin_snapshot(),"admin_record_present":bool(admin_fingerprint())})
        elif args.mode=="stage":state.update(stage(sha))
        elif args.mode=="install":state.update(install(sha))
        elif args.mode=="activate":state.update(activate(sha))
        elif args.mode=="verify":state.update(verify(sha))
        else:state.update(restore(sha))
        print(json.dumps(state,sort_keys=True))
        return 0
    except (Blocked,OSError,ValueError,sqlite3.Error,ET.ParseError) as exc:
        reason=str(exc) if isinstance(exc,Blocked) else type(exc).__name__
        state.update({"status":"blocked","reason":reason,"protected_services_intentionally_unchanged":args.mode in ("preflight","stage","verify")})
        print(json.dumps(state,sort_keys=True))
        return 1

if __name__=="__main__":
    sys.exit(main())
