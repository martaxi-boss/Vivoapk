"""Disposable real Jellyfin 12.4 integration test. No Mega OTT accounts used."""
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import threading
import time

PLUGIN_ID = "e7a31bad-3643-4da2-834e-559c39cc90b1"
MOCK = Path("/srv/projects/Vivoapk/run/backend.sock")

class BackendHandler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(1500)
        if b"POST /v1/session/resolve " not in line:
            return
        length = 0
        while True:
            header = self.rfile.readline(2000)
            if not header or header == b"\r\n":
                break
            if header.lower().startswith(b"content-length:"):
                length = int(header.partition(b":")[2])
        if not 0 < length < 4096:
            return
        request = json.loads(self.rfile.read(length))
        valid = (request.get("username") == "synthetic-mega-user"
                 and request.get("password") == "synthetic-mega-password"
                 and request.get("platform") == "android")
        body = json.dumps({"code": "SUCCESS" if valid else "INVALID_CREDENTIALS"}).encode()
        self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         + b"Content-Length: " + str(len(body)).encode()
                         + b"\r\nConnection: close\r\n\r\n" + body)

class BackendServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

class Connection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=8)
        self.path = str(path)
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(8)
        self.sock.connect(self.path)

def request(method, uri, data=None, token=None):
    conn = Connection(DATAROOT / "run/jellyfin.sock")
    headers = {
        "Content-Type": "application/json", "Accept": "application/json",
        "Authorization": 'MediaBrowser Client="VivoAPK-CI", Device="CI", DeviceId="vivoapk-ci", Version="1.0.0"'
    }
    if uri.startswith("/Startup/"):
        headers.pop("Authorization", None)
    if token:
        headers["Authorization"] = 'MediaBrowser Client="VivoAPK-CI", Device="CI", DeviceId="vivoapk-ci", Version="1.0.0", Token="' + token + '"'
        headers["X-Emby-Token"] = token
    conn.request(method, uri, body=json.dumps(data) if data is not None else None, headers=headers)
    response = conn.getresponse()
    payload = response.read(200000)
    code = response.status
    conn.close()
    try:
        parsed = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        parsed = None
    return code, parsed

def main():
    global DATAROOT
    base = Path(os.environ["RUNNER_TEMP"]) / "vivo-jellyfin-12.4-smoke"
    DATAROOT = base / "data"
    for p in [DATAROOT, DATAROOT / "run", base / "cache", base / "config", base / "logs"]:
        p.mkdir(parents=True, exist_ok=True)
    artifact = Path("jellyfin-auth/Vivoapk.Jellyfin.Auth")
    plugin_path = DATAROOT / "plugins" / "VivoAPK Mega OTT Auth_0.1.0.0"
    plugin_path.mkdir(parents=True)
    shutil.copy2(artifact / "bin/Release/net10.0/Jellyfin.Plugin.VivoapkMegaAuth.dll", plugin_path)
    shutil.copy2(artifact / "meta.json", plugin_path)

    MOCK.parent.mkdir(parents=True, exist_ok=True)
    if MOCK.exists():
        MOCK.unlink()
    mock = BackendServer(str(MOCK), BackendHandler)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    env = os.environ.copy()
    env.update({
        "JELLYFIN_Kestrel__Socket": "true",
        "JELLYFIN_Kestrel__SocketPath": str(DATAROOT / "run/jellyfin.sock"),
        "JELLYFIN_Kestrel__SocketPermissions": "0660"
    })
    log = (base / "logs/jellyfin-launch.log").open("w")
    cmd = [
        os.environ["JELLYFIN_TEST_BINARY"], "--datadir", str(DATAROOT),
        "--cachedir", str(base / "cache"), "--configdir", str(base / "config"),
        "--logdir", str(base / "logs"), "--webdir", os.environ["JELLYFIN_TEST_WEB"], "--service"
    ]
    jellyfin = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        running = False
        for _ in range(70):
            if jellyfin.poll() is not None:
                break
            try:
                code, info = request("GET", "/System/Info/Public")
                if code == 200 and info and str(info.get("Version", "")).startswith("12.4."):
                    running = True
                    break
            except (OSError, http.client.HTTPException, ValueError):
                pass
            time.sleep(1)
        if not running:
            raise RuntimeError("Jellyfin 12.4 failed to start")
        # Follow the same Jellyfin startup order proven on the private VPS.
        request("GET", "/Startup/User")
        code, _ = request("POST", "/Startup/User",
                          {"Name": "ci-admin", "Password": "synthetic-admin-password"})
        if code not in (200, 204):
            raise RuntimeError(f"Admin setup rejected with HTTP {code}")
        code, _ = request("POST", "/Startup/Complete", {})
        if code not in (200, 204):
            raise RuntimeError(f"Startup wizard rejected with HTTP {code}")
        code, admin = request("POST", "/Users/AuthenticateByName",
                              {"Username": "ci-admin", "Pw": "synthetic-admin-password"})
        if code != 200 or not isinstance(admin, dict) or not admin.get("AccessToken"):
            raise RuntimeError("Administrator login not working")
        token = admin["AccessToken"]
        code, plugins = request("GET", "/Plugins", token=token)
        if code != 200 or not isinstance(plugins, list):
            raise RuntimeError(f"Plugin inventory failed: HTTP {code}; response type {type(plugins).__name__}; admin={bool(admin.get('User', {}).get('Policy', {}).get('IsAdministrator'))}")
        if not any(str(item.get("Id", item.get("id", ""))).lower() == PLUGIN_ID for item in plugins):
            visible = [{"name": item.get("Name", item.get("name")), "status": item.get("Status", item.get("status")), "id_present": bool(item.get("Id", item.get("id")))} for item in plugins[:10]]
            raise RuntimeError(f"VivoAPK plugin not loaded; visible plugins: {visible}")
        print("JELLYFIN_12_4_PLUGIN_LOADED=PASS")
        print("JELLYFIN_LOCAL_ADMIN_PRESERVED=PASS")

        # CI TEST ONLY: enable the otherwise disabled auth plugin in disposable Jellyfin.
        code, _ = request("POST", "/Plugins/" + PLUGIN_ID + "/Configuration",
                          {"Enabled": True}, token=token)
        if code not in (200, 204):
            raise RuntimeError("VivoAPK plugin test configuration rejected")
        code, customer = request("POST", "/Users/AuthenticateByName", {
            "Username": "synthetic-mega-user", "Pw": "synthetic-mega-password"})
        if code != 200 or not isinstance(customer, dict) or not customer.get("AccessToken"):
            raise RuntimeError("Synthetic Mega OTT auth / automatic identity creation failed")
        print("JELLYFIN_SYNTHETIC_MEGA_LOGIN=PASS")
        code, _ = request("POST", "/Users/AuthenticateByName", {
            "Username": "synthetic-mega-user", "Pw": "wrong"})
        if code == 200:
            raise RuntimeError("Invalid synthetic Mega password accepted")
        print("JELLYFIN_MEGA_INVALID_PASSWORD_DENIED=PASS")
        code, _ = request("POST", "/Users/AuthenticateByName", {
            "Username": "ci-admin", "Pw": "synthetic-admin-password"})
        if code != 200:
            raise RuntimeError("Administrator was affected")
        print("JELLYFIN_ADMIN_AFTER_MEGA_PLUGIN=PASS")
    except Exception:
        log.flush()
        recent = (base / "logs/jellyfin-launch.log").read_text(errors="replace").splitlines()[-35:]
        print("Disposible CI Jellyfin errors:\n" + "\n".join(recent))
        raise
    finally:
        jellyfin.terminate()
        try:
            jellyfin.wait(timeout=10)
        except subprocess.TimeoutExpired:
            jellyfin.kill()
        mock.shutdown()
        mock.server_close()
        log.close()
        if MOCK.exists():
            MOCK.unlink()

if __name__ == "__main__":
    main()
