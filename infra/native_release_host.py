"""Pinned host preparation/rehearsal for the coordinated native release.

No activation entry point. The rehearsal uses the actual verified staged
DLLs and copies of the current installed DLLs/configuration in a private
disposable directory. All unit commands in the rehearsal are simulated.
"""
from __future__ import annotations

import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from xml.etree import ElementTree as ET

import native_release_preflight as pf
import native_release_stage as stage
import native_release_coordinated as coord
from native_release_transaction import COMPONENTS, Blocked, read_regular, digest, checked_directory

ROOT = pf.ROOT
JELLY = pf.JELLY
STAGE = stage.STAGE
BUNDLE = STAGE / "bundle-verified"
CHECKOUT = STAGE / "backend/source"
FFMPEG = STAGE / "ffmpeg/runtime/ffmpeg"
FFPROBE = FFMPEG.with_name("ffprobe")
PLUGIN = JELLY / "data/plugins/configurations/Jellyfin.Plugin.VivoapkMegaAuth.xml"
KEY = ROOT / "run/private-media.key"
VAULT = Path("/root/project-secrets/VIVOAPK")
MEDIA_ENV = VAULT / "native-media.env"
STATE = ROOT / "backups/native-coordinated"
BACKEND_DROPIN = Path("/etc/systemd/system/vivoapk-backend.service.d/90-vivoapk-native-release.conf")
SERVER_DROPIN = Path("/etc/systemd/system/jellyfin-pink.service.d/90-vivoapk-native-release.conf")
LAUNCH_SHA = "fa539dd28fc073108ce0f7e9ba6ddb07d93d593fa5df22a3b45e7d38e815ab7c"
TARGETS = {
    **{name: JELLY / rel for name, rel in COMPONENTS.items()},
    "plugin.xml": PLUGIN, "backend-dropin": BACKEND_DROPIN,
    "server-dropin": SERVER_DROPIN, "media-key": KEY, "media-env": MEDIA_ENV,
}


def require(ok, reason):
    if not ok:
        raise Blocked(reason)


def as_service(user, group, argv):
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    p = subprocess.run(["/usr/sbin/runuser", "-u", user, "-g", group, "--", *argv],
                       env=env, capture_output=True, timeout=15)
    return p.returncode == 0


def file_digest(path, limit=512*1024**2):
    checked_directory(path.parent)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as f:
        before = os.fstat(f.fileno())
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit,
                "host_file_size_or_type")
        sha = hashlib.file_digest(f, "sha256").hexdigest()
        after = os.fstat(f.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                "host_file_changed")
        return sha


def verify_stage():
    for path in (STAGE.parent, STAGE, BUNDLE, CHECKOUT, STAGE / "ffmpeg"):
        checked_directory(path)
        require(path.stat().st_uid == 0 and not path.stat().st_mode & 0o022,
                "host_stage_not_root_controlled")
    for name, sha in {**stage.CANDIDATE, "candidate-manifest.json": stage.MANIFEST_SHA}.items():
        require(file_digest(BUNDLE / name, 8*1024**2) == sha, "host_bundle_hash_mismatch")
    require(file_digest(FFMPEG) == stage.FFMPEG_SHA and
            file_digest(FFPROBE) == stage.FFPROBE_SHA, "host_ffmpeg_hash_mismatch")
    git = ["git", "-c", "safe.directory=" + str(CHECKOUT), "-C", str(CHECKOUT)]
    require(pf.cmd(git + ["rev-parse", "HEAD"]) == stage.SOURCE_SHA and
            not pf.cmd(git + ["status", "--porcelain=v1", "--untracked-files=all"]),
            "host_staged_backend_changed")


def unit_text():
    # Retain original units and existing runtime env file. Explicit app-dir
    # avoids an editable installation importing the old backend checkout.
    app = CHECKOUT / "backend"
    venv = ROOT / ".venv/bin"
    backend = (
        "[Service]\n"
        f"WorkingDirectory={app}\n"
        "Environment=PYTHONDONTWRITEBYTECODE=1\n"
        f"EnvironmentFile={MEDIA_ENV}\n"
        "ExecStart=\n"
        f"ExecStart={venv}/uvicorn --app-dir {app} app.main:app --uds {ROOT}/run/backend.sock --workers 1\n"
        "ExecStartPost=\n"
        f"ExecStartPost={venv}/python {CHECKOUT}/infra/secure_runtime_socket.py\n"
    ).encode("ascii")
    # Jellyfin 12.4 reads FfmpegPathKey=ffmpeg from JELLYFIN_ environment
    # variables before falling back to encoding.xml. Its pinned launcher
    # has no overriding --ffmpeg command-line option.
    server = (
        "[Service]\n"
        "Environment=VIVOAPK_NATIVE_PRIVATE_PLAYBACK=1\n"
        f"Environment=VIVOAPK_PRIVATE_MEDIA_SOCKET={ROOT}/run/backend.sock\n"
        f"Environment=VIVOAPK_PRIVATE_MEDIA_KEY_FILE={KEY}\n"
        f"Environment=VIVOAPK_PRIVATE_FFPROBE={FFPROBE}\n"
        f"Environment=JELLYFIN_ffmpeg={FFMPEG}\n"
    ).encode("ascii")
    return backend, server


def private_env(key):
    require(len(key) == 64 and all(c in "0123456789abcdef" for c in key),
            "host_invalid_local_media_key")
    return ("PRIVATE_HLS_ENABLED=true\nPRIVATE_MEDIA_KEY=" + key + "\n").encode("ascii")


def check_host():
    first = pf.inspect()
    require(first["baseline_pass"], "host_baseline_not_safe")
    verify_stage()
    require(file_digest(JELLY / "start.sh", 65536) == LAUNCH_SHA,
            "host_launcher_changed")
    require(pf.prop(pf.SERVER, "User") == "jellyfinpink" and
            pf.prop(pf.SERVER, "Group") == "www-data" and
            str(JELLY/"start.sh") in pf.prop(pf.SERVER, "ExecStart"),
            "host_jellyfin_unit_changed")
    require(pf.prop(pf.BACKEND, "User") == "vivoapksvc" and
            pf.prop(pf.BACKEND, "Group") == "vivoapksvc" and
            "--workers 1" in pf.prop(pf.BACKEND, "ExecStart") and
            str(ROOT/"run/backend.sock") in pf.prop(pf.BACKEND, "ExecStart") and
            str(pf.SOURCE/"infra/secure_runtime_socket.py") in pf.prop(pf.BACKEND, "ExecStartPost") and
            str(VAULT/"backend-runtime.env") in pf.prop(pf.BACKEND, "EnvironmentFiles"),
            "host_backend_unit_changed")
    for service in (pf.BACKEND, pf.SERVER):
        require(pf.prop(service, "ProtectSystem") == "strict" and
                pf.prop(service, "NoNewPrivileges") == "yes", "host_unit_hardening_changed")
    require(pf.prop(pf.SERVER, "PrivateNetwork") == "yes" and
            pf.prop(pf.SERVER, "MemoryMax") == "1677721600", "host_resource_policy_changed")
    for name in coord.NEW_ONLY:
        require(not os.path.lexists(TARGETS[name]), "host_new_destination_exists")
    require(not os.path.lexists(STATE/stage.SOURCE_SHA), "host_existing_transaction")
    env = (VAULT/"backend-runtime.env").lstat()
    require(stat.S_ISREG(env.st_mode) and env.st_uid == 0 and not env.st_mode & 0o077,
            "host_existing_env_not_private")
    cfg, _ = read_regular(PLUGIN, 2*1024**2)
    xml = ET.fromstring(cfg)
    require(all(xml.findtext(k, "").strip().lower() == "true"
                for k in ("Enabled", "PilotLiveChannels", "CategoryNavigation")) and
            all(xml.findtext(k, "").strip().lower() != "true"
                for k in ("FullLiveCatalog", "ProvisionPrivateLibraries",
                          "NativePrivatePlayback", "NativeServerPlaybackLab")),
            "host_plugin_config_changed")
    # Match the actual service's effective Group, rather than the passwd
    # primary group of jellyfinpink. No chmod, chgrp or permission expansion.
    require(as_service("jellyfinpink", "www-data", ["/usr/bin/test", "-x", str(ROOT/"run")]) and
            as_service("jellyfinpink", "www-data", ["/usr/bin/test", "-w", str(ROOT/"run/backend.sock")]),
            "host_effective_service_socket_access")
    require(as_service("jellyfinpink", "www-data", [str(FFMPEG), "-version"]) and
            as_service("jellyfinpink", "www-data", [str(FFPROBE), "-version"]),
            "host_effective_ffmpeg_access")
    require(as_service("vivoapksvc", "vivoapksvc",
            ["/usr/bin/test", "-r", str(CHECKOUT/"backend/app/main.py")]),
            "host_effective_backend_access")
    nginx = subprocess.run(["nginx", "-T"], capture_output=True, text=True, timeout=12)
    require(nginx.returncode == 0 and str(ROOT/"run/backend.sock") not in nginx.stdout+nginx.stderr,
            "host_backend_socket_exposed_or_unknown")
    return first


def rehearsal():
    first = check_host()
    pids = {unit: pf.prop(unit, "MainPID") for unit in pf.PROTECTED}
    originals = {name: read_regular(TARGETS[name])[0]
                 for name in coord.NAMES if name not in coord.NEW_ONLY}
    payloads = {name: read_regular(BUNDLE/name)[0] for name in COMPONENTS}
    payloads["plugin.xml"] = coord.set_xml_flag(originals["plugin.xml"], "true")
    payloads["backend-dropin"], payloads["server-dropin"] = unit_text()
    # Synthetic one-time key, used only in the disposable rehearsal.
    key = secrets.token_hex(32)
    payloads["media-key"] = key.encode("ascii")
    payloads["media-env"] = private_env(key)
    events = []
    with tempfile.TemporaryDirectory(prefix="coordinated-rehearsal-", dir=STAGE) as tmp:
        root = Path(tmp)
        live, state = root/"live", root/"state"
        live.mkdir()
        state.mkdir(mode=0o700)
        targets = {name: live/name for name in coord.NAMES}
        uid, gid = os.geteuid(), os.getegid()
        changes = []
        for name in coord.NAMES:
            before = originals.get(name)
            mode = 0o600 if name in ("media-key", "media-env") else 0o640
            if before is not None:
                targets[name].write_bytes(before)
                targets[name].chmod(mode)
            changes.append(coord.Change(name, None if before is None else digest(before),
                    payloads[name], mode, uid, gid))
        state_flag = {"running": True}

        def stop():
            events.extend(("stop-jellyfin", "stop-backend"))
            state_flag["running"] = False

        def reload():
            require(not state_flag["running"], "rehearsal_reload_while_running")
            events.append("reload")

        def start():
            events.extend(("start-backend", "start-jellyfin"))
            state_flag["running"] = True

        def health(side):
            require(state_flag["running"], "rehearsal_service_not_running")
            events.append("health-"+side)
            if side == "new":
                # Deliberate postflight failure after installing every
                # file; exercise automatic restoration of exact originals.
                require(all(targets[n].read_bytes() == payloads[n] for n in coord.NAMES),
                        "rehearsal_installed_bytes_wrong")
                raise RuntimeError("synthetic_postflight_failure")

        tx = coord.CoordinatedTransaction(targets, state, stage.SOURCE_SHA, stop, reload, start, health)
        require(tx.activate(changes) == "rolled_back_after_failure", "rehearsal_rollback_not_completed")
        require(all(targets[n].read_bytes() == data for n, data in originals.items()) and
                all(not os.path.lexists(targets[n]) for n in coord.NEW_ONLY),
                "rehearsal_originals_not_restored")
        require(events == ["health-old", "stop-jellyfin", "stop-backend", "reload",
            "start-backend", "start-jellyfin", "health-new", "stop-jellyfin",
            "stop-backend", "reload", "start-backend", "start-jellyfin", "health-old"],
            "rehearsal_service_order_wrong")
        require(json.loads((tx.save/"journal.json").read_text())["phase"]=="rolled_back",
                "rehearsal_journal_not_restored")
        # Test access with the real identities but with a non-secret sample.
        # The fixture parent must be traversable by both tested identities.
        access = STAGE / ("permission-probe-" + secrets.token_hex(8))
        access.mkdir(mode=0o755)
        try:
            test_key = access/"private-media.key"
            test_key.write_bytes(b"synthetic-not-a-real-media-key")
            os.chown(test_key, pwd.getpwnam("jellyfinpink").pw_uid, grp.getgrnam("www-data").gr_gid)
            test_key.chmod(0o400)
            require(as_service("jellyfinpink", "www-data", ["/usr/bin/test", "-r", str(test_key)]),
                    "rehearsal_service_key_not_readable")
            require(not as_service("www-data", "www-data", ["/usr/bin/test", "-r", str(test_key)]),
                    "rehearsal_web_user_can_read_key")
        finally:
            shutil.rmtree(access)
    last = pf.inspect()
    require(last["baseline_pass"] and first["components"] == last["components"] and
            all(pf.prop(unit, "MainPID") == pid for unit, pid in pids.items()),
            "host_production_changed")
    return {"phase": "coordinated-rehearsal", "activation_allowed": False,
            "rehearsal_pass": True, "real_candidate_file_bytes": True,
            "automatic_rollback": True, "nine_file_changes": len(coord.NAMES),
            "service_controls_simulated": True, "production_unchanged": True,
            "effective_service_socket_access": True, "private_key_permissions": True,
            "disposable_files_removed": True}


def main():
    try:
        require(sys.argv[1:] == ["rehearse"], "only_rehearsal_is_approved")
        print(json.dumps(rehearsal(), sort_keys=True), flush=True)
        return 0
    except (Blocked, pf.Blocked, OSError, ValueError, RuntimeError,
            subprocess.TimeoutExpired, ET.ParseError) as exc:
        print(json.dumps({"phase": "coordinated-rehearsal", "activation_allowed": False,
              "rehearsal_pass": False,
              "reason": str(exc) if isinstance(exc, (Blocked, pf.Blocked)) else type(exc).__name__},
              sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
