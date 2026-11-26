"""Activate only the independently verified native release, through Ferrari.

The existing backend checkout, unit files, launcher and database are not
replaced. The approved staged backend and FFmpeg are selected by two
drop-ins. The coordinated transaction journals all nine destinations.
A successful service postflight is not real subscriber playback certification.
"""
from __future__ import annotations

import grp
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import secrets
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from xml.etree import ElementTree as ET

import native_release_host as host
import native_release_preflight as pf
import native_release_stage as stage
import native_release_coordinated as coord
from native_release_transaction import Blocked, COMPONENTS, atomic_write, checked_directory, digest, read_regular, sync_directory

APPROVED_SOURCE = "0994990c024b159eebd44528041746f557596d27"
APPROVED_BUNDLE = "54ba20844f871177869d8359a43bfd2003ca59a963e7a26b2c120cca2a52a3da"
APPROVED_REHEARSAL = "45802135112"
ENCODING = host.JELLY / "config/encoding.xml"
BASELINE_FILE = host.STATE / (APPROVED_SOURCE + "-host-baseline.json")
UNITS = (pf.SERVER, pf.BACKEND)


def require(value, reason):
    if not value:
        raise Blocked(reason)


def root_dir(path, mode, private=False):
    checked_directory(path.parent)
    if not os.path.lexists(path):
        path.mkdir(mode=mode)
        sync_directory(path.parent)
    checked_directory(path)
    st = path.stat()
    require(st.st_uid == 0 and not st.st_mode & (0o077 if private else 0o022),
            "activation_directory_not_trusted")


def pin():
    require(stage.SOURCE_SHA == APPROVED_SOURCE and stage.BUNDLE_SHA == APPROVED_BUNDLE
            and tuple(COMPONENTS) == tuple(pf.BASELINE)
            and set(host.TARGETS) == set(coord.NAMES), "activation_pin_mismatch")


def admin_fingerprint():
    # Keep the administrator's stable identity/authentication fields and
    # permissions entirely on the machine. Runtime activity timestamps and
    # failure counters are not identity. No row, password or hash is emitted.
    db = host.JELLY / "data/data/jellyfin.db"
    with sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True, timeout=5) as con:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        matches = []
        for table in tables:
            if "user" not in table.lower():
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            cols = [r[1] for r in con.execute("PRAGMA table_info(" + quoted + ")")]
            for col in cols:
                if col.lower() not in ("username", "name"):
                    continue
                qc = '"' + col.replace('"', '""') + '"'
                for row in con.execute("SELECT * FROM " + quoted + " WHERE " + qc +
                                       "=? COLLATE NOCASE", ("pink-admin",)):
                    fields = dict(zip(cols, row))
                    matches.append((table, fields))
        require(len(matches) == 1, "activation_admin_identity_missing")
        table, fields = matches[0]
        stable = {k: v for k, v in fields.items() if k.lower() not in (
            "lastactivitydate", "lastlogindate", "invalidloginattemptcount")}
        identifier = next((v for k, v in fields.items() if k.lower() == "id"), None)
        require(identifier is not None, "activation_admin_id_missing")
        permissions = []
        for t in tables:
            if "permission" not in t.lower():
                continue
            quoted = '"' + t.replace('"', '""') + '"'
            cols = [r[1] for r in con.execute("PRAGMA table_info(" + quoted + ")")]
            user_col = next((c for c in cols if c.lower() == "userid"), None)
            if user_col:
                qc = '"' + user_col.replace('"', '""') + '"'
                rows = con.execute("SELECT * FROM " + quoted + " WHERE " + qc + "=?", (identifier,))
                permissions.append((t, sorted(repr(tuple(row)) for row in rows)))
        return hashlib.sha256(repr((table, sorted(stable.items()), sorted(permissions))).encode()).hexdigest()


def encoder_display():
    if not ENCODING.exists():
        return ""
    raw, _ = read_regular(ENCODING, 2*1024**2)
    return ET.fromstring(raw).findtext("EncoderAppPathDisplay", "").strip()


class LocalHttp(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=4)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.path))


def request(sock, method, path, body=None, headers=None):
    conn = LocalHttp(sock)
    try:
        data = None if body is None else json.dumps(body).encode("ascii")
        conn.request(method, path, body=data, headers={
            **({"Content-Type":"application/json"} if body is not None else {}),
            **(headers or {})})
        res = conn.getresponse()
        raw = res.read(2*1024**2+1)
        require(len(raw) <= 2*1024**2, "activation_http_response_bound")
        return res.status, raw
    finally:
        conn.close()


def ctl(*args):
    result = subprocess.run(["/usr/bin/systemctl", *args], timeout=10,
                            capture_output=True, env={"PATH":"/usr/bin:/bin","LANG":"C.UTF-8"})
    require(result.returncode == 0, "activation_systemctl_failed")


def wait_until(predicate, seconds, reason):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.25)
    raise Blocked(reason)


def stopped(unit):
    return (pf.prop(unit, "ActiveState") in ("inactive", "failed")
            and pf.prop(unit, "MainPID") == "0"
            and pf.prop(unit, "ControlPID") == "0"
            and not pf.prop(unit, "ControlGroup"))


def ready(unit, path, endpoint):
    return pf.active(unit) and pf.prop(unit, "SubState") == "running" and pf.socket_ok(path, endpoint)


def stop_pair():
    for unit in UNITS:
        ctl("stop", "--no-block", unit)
        wait_until(lambda: stopped(unit), 25, "activation_service_did_not_stop")


def start_pair():
    ctl("start", "--no-block", pf.BACKEND)
    wait_until(lambda: ready(pf.BACKEND, host.ROOT/"run/backend.sock", "/openapi.json"),
               24, "activation_backend_not_ready")
    ctl("start", "--no-block", pf.SERVER)
    wait_until(lambda: ready(pf.SERVER, host.JELLY/"run/jellyfin.sock", "/System/Info/Public"),
               40, "activation_jellyfin_not_ready")


def same_protected(baseline):
    require(all(pf.active(unit) for unit in pf.PROTECTED)
            and not any(pf.active(unit) for unit in pf.RETIRED)
            and all(pf.prop(unit,"MainPID")==pid for unit,pid in baseline["protected_pids"].items()),
            "activation_protected_service_changed")
    require(admin_fingerprint() == baseline["admin"], "activation_admin_changed")


def new_health(baseline):
    same_protected(baseline)
    verify_runtime()
    key, info = read_regular(host.KEY, 1024)
    require(stat.S_IMODE(info.st_mode)==0o400 and
            info.st_uid==pwd.getpwnam("jellyfinpink").pw_uid,
            "activation_media_key_permissions")
    env, env_info = read_regular(host.MEDIA_ENV, 4096)
    require(env_info.st_uid==0 and stat.S_IMODE(env_info.st_mode)==0o600
            and env==host.private_env(key.decode("ascii")), "activation_media_env_mismatch")
    require(host.as_service("jellyfinpink","www-data",["/usr/bin/test","-r",str(host.KEY)])
            and not host.as_service("www-data","www-data",["/usr/bin/test","-r",str(host.KEY)]),
            "activation_media_key_access")
    backend = host.ROOT / "run/backend.sock"
    jelly = host.JELLY / "run/jellyfin.sock"
    status, raw = request(backend,"GET","/openapi.json")
    require(status==200 and not any(p.startswith("/_internal/media/") for p in json.loads(raw)["paths"]),
            "activation_private_schema_exposed")
    routes = {
        "/_internal/media/hls/open":{"category_id":"1","asset_id":"1","platform":"android"},
        "/_internal/media/file/open":{"category_id":"1","asset_id":"1","kind":"vod","platform":"android"},
    }
    for route, body in routes.items():
        require(request(backend,"POST",route,body)[0]==403, "activation_missing_key_not_denied")
        headers={"X-Vivoapk-Media-Key":key.decode("ascii")}
        require(request(backend,"POST",route,body,headers)[0]==401,
                "activation_missing_bearer_not_denied")
        headers["Authorization"]="Bearer synthetic-invalid"
        require(request(backend,"POST",route,body,headers)[0] in (401,404),
                "activation_invalid_bearer_not_denied")
    require(request(backend,"POST","/v1/catalog/categories",{"kind":"live"})[0]==401,
            "activation_catalog_not_protected")
    fake="00000000000000000000000000000001"
    for method,path,body in (
        ("POST","/Items/"+fake+"/PlaybackInfo",{}),
        ("GET","/Videos/"+fake+"/master.m3u8",None),
    ):
        require(request(jelly,method,path,body)[0] in (401,403,404),
                "activation_unauthenticated_jellyfin_media_not_denied")
    require(pf.socket_ok(jelly,"/System/Info/Public"), "activation_public_info_unhealthy")


def verify_runtime():
    require(pf.prop(pf.BACKEND,"WorkingDirectory")==str(host.CHECKOUT/"backend")
            and str(host.CHECKOUT/"backend") in pf.prop(pf.BACKEND,"ExecStart")
            and str(host.CHECKOUT/"infra/secure_runtime_socket.py") in pf.prop(pf.BACKEND,"ExecStartPost")
            and str(host.MEDIA_ENV) in pf.prop(pf.BACKEND,"EnvironmentFiles"),
            "activation_backend_selection_mismatch")
    bp=int(pf.prop(pf.BACKEND,"MainPID"))
    sp=int(pf.prop(pf.SERVER,"MainPID"))
    require(min(bp,sp)>0 and Path("/proc",str(bp),"cwd").resolve()==host.CHECKOUT/"backend",
            "activation_running_backend_cwd_mismatch")
    maps=(Path("/proc",str(sp),"maps")).read_text()
    components=pf.inspect_components(maps)
    require(all(p["mapped_exactly"] and p["sha256"]==stage.CANDIDATE[n] for n,p in components.items()),
            "activation_loaded_assembly_mismatch")
    require(pf.prop(pf.SERVER,"PrivateNetwork")=="yes"
            and Path("/proc",str(sp),"ns/net").stat().st_ino!=Path("/proc/self/ns/net").stat().st_ino
            and pf.prop(pf.SERVER,"MemoryMax")=="1677721600"
            and all(pf.prop(u,"ProtectSystem")=="strict" and pf.prop(u,"NoNewPrivileges")=="yes"
                    for u in UNITS), "activation_isolation_changed")
    require(encoder_display()==str(host.FFMPEG), "activation_ffmpeg_selection_mismatch")
    host.verify_stage()
    nginx=subprocess.run(["nginx","-T"],capture_output=True,text=True,timeout=10)
    require(nginx.returncode==0 and str(host.ROOT/"run/backend.sock") not in nginx.stdout+nginx.stderr,
            "activation_backend_proxy_exposed")


def changes():
    key=secrets.token_hex(32)
    original={n: read_regular(host.TARGETS[n]) for n in coord.NAMES if n not in coord.NEW_ONLY}
    payload={n:read_regular(host.BUNDLE/n)[0] for n in COMPONENTS}
    payload["plugin.xml"]=coord.set_xml_flag(original["plugin.xml"][0],"true")
    payload["backend-dropin"],payload["server-dropin"]=host.unit_text()
    payload["media-key"]=key.encode("ascii")
    payload["media-env"]=host.private_env(key)
    result=[]
    for name in coord.NAMES:
        if name in original:
            before,st=original[name]
            old=digest(before)
            if name in COMPONENTS:
                require(old==pf.BASELINE[name][1] and digest(payload[name])==stage.CANDIDATE[name],
                        "activation_independent_dll_pin_mismatch")
            mode,uid,gid=stat.S_IMODE(st.st_mode),st.st_uid,st.st_gid
        else:
            require(not os.path.lexists(host.TARGETS[name]), "activation_destination_exists")
            old=None
            mode,uid,gid=0o644,0,0
            if name=="media-key":
                mode,uid,gid=0o400,pwd.getpwnam("jellyfinpink").pw_uid,grp.getgrnam("www-data").gr_gid
            elif name=="media-env":
                mode=0o600
        result.append(coord.Change(name,old,payload[name],mode,uid,gid))
    return result


def baseline_record():
    return {"source":APPROVED_SOURCE,"admin":admin_fingerprint(),"encoder":encoder_display(),
            "protected_pids":{u:pf.prop(u,"MainPID") for u in ("nginx.service","postgresql.service")}}


def save_baseline(baseline):
    require(not os.path.lexists(BASELINE_FILE), "activation_baseline_exists")
    atomic_write(BASELINE_FILE,json.dumps(baseline,sort_keys=True).encode("ascii"),0o600,0,0)


def load_baseline():
    raw,info=read_regular(BASELINE_FILE,16384)
    require(info.st_uid==0 and not info.st_mode&0o077, "activation_baseline_permissions")
    value=json.loads(raw)
    require(value["source"]==APPROVED_SOURCE and set(value)=={"source","admin","encoder","protected_pids"}
            and set(value["protected_pids"])=={"nginx.service","postgresql.service"},
            "activation_baseline_identity")
    return value


def execute(action):
    pin()
    require(os.geteuid()==0,"activation_root_required")
    report={"phase":"coordinated-"+action,"source":APPROVED_SOURCE,
            "rehearsal":APPROVED_REHEARSAL,"installed":False,
            "real_subscriber_playback_certified":False,"activation_allowed":True}
    if action=="activate":
        host.check_host()
        root_dir(host.STATE.parent,0o700)
        root_dir(host.STATE,0o700,private=True)
        for name in ("backend-dropin","server-dropin"):
            root_dir(host.TARGETS[name].parent,0o755)
        baseline=baseline_record()
        candidate=changes()
        save_baseline(baseline)
    elif action=="rollback":
        baseline=load_baseline()
        candidate=None
    else:
        raise Blocked("activation_unsupported_action")
    first_stop=action=="activate"
    failure=None

    def stop():
        nonlocal first_stop,failure
        try:
            if first_stop:
                require(pf.ffmpeg_count(pf.prop(pf.SERVER,"ControlGroup"))==0,
                        "activation_playback_started_before_stop")
                first_stop=False
            stop_pair()
        except Exception as exc:
            if failure is None:
                failure=str(exc) if isinstance(exc,Blocked) else type(exc).__name__
            raise

    def reload():
        nonlocal failure
        try:
            require(all(stopped(u) for u in UNITS),"activation_reload_while_running")
            ctl("daemon-reload")
        except Exception as exc:
            if failure is None:
                failure=str(exc) if isinstance(exc,Blocked) else type(exc).__name__
            raise

    def start():
        nonlocal failure
        try:
            start_pair()
        except Exception as exc:
            if failure is None:
                failure=str(exc) if isinstance(exc,Blocked) else type(exc).__name__
            raise

    def health(side):
        nonlocal failure
        try:
            if side=="old":
                same_protected(baseline)
                require(pf.inspect()["baseline_pass"],"activation_original_baseline_failed")
                require(encoder_display()==baseline["encoder"],"activation_original_encoder_changed")
            else:
                new_health(baseline)
        except Exception as exc:
            if failure is None:
                failure=str(exc) if isinstance(exc,Blocked) else type(exc).__name__
            raise

    tx=coord.CoordinatedTransaction(host.TARGETS,host.STATE,APPROVED_SOURCE,stop,reload,start,health)
    result=tx.activate(candidate) if action=="activate" else tx.rollback()
    report.update(result=result,installed=result=="committed",rollback_verified=result in (
        "rolled_back","rolled_back_after_failure"),failure=failure,
        services_healthy=all(pf.active(u) for u in pf.PROTECTED),
        protected_pids_preserved=all(pf.prop(u,"MainPID")==v for u,v in baseline["protected_pids"].items()))
    if report["installed"]:
        report.update(four_loaded_dlls_verified=True,backend_selection_verified=True,
            ffmpeg_selection_verified=True,private_route_denial_cases=6,
            jellyfin_unauthenticated_denied=True,admin_preserved=True,
            private_network_preserved=True)
    return report


def main():
    action=sys.argv[1] if len(sys.argv)==2 else ""
    if action not in ("activate","rollback"):
        print(json.dumps({"installed":False,"reason":"activation_unsupported_action"}),flush=True)
        return 1
    # SIGTERM is a recoverable interruption while the transaction is active.
    # A hard SIGKILL or host crash still requires the durable journal recovery.
    def interrupt(_sig,_frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupt)
    try:
        result=execute(action)
        print(json.dumps(result,sort_keys=True),flush=True)
        return 0 if (result["installed"] or (action=="rollback" and result["rollback_verified"])) else 1
    except (Exception,KeyboardInterrupt) as exc:
        print(json.dumps({"phase":"coordinated-"+action,"installed":False,
            "reason":str(exc) if isinstance(exc,(Blocked,pf.Blocked)) else type(exc).__name__,
            "journal_present":(host.STATE/APPROVED_SOURCE/"journal.json").is_file(),
            "real_subscriber_playback_certified":False},sort_keys=True),flush=True)
        return 1


if __name__=="__main__":
    sys.exit(main())
