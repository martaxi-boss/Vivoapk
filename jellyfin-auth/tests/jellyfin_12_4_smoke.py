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
from urllib.parse import urlencode

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
        valid = (
            (request.get("username"), request.get("password"))
            in {
                ("synthetic-mega-user", "synthetic-mega-password"),
                ("synthetic-mega-user-b", "synthetic-mega-password-b"),
            }
            and request.get("platform") == "android"
        )
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
        plugin_items = [item for item in plugins if item.get("Name", item.get("name")) == "VivoAPK Mega OTT Auth"]
        if len(plugin_items) != 1:
            visible = [{"name": item.get("Name", item.get("name")), "status": item.get("Status", item.get("status"))} for item in plugins[:10]]
            raise RuntimeError(f"VivoAPK plugin not loaded uniquely; visible plugins: {visible}")
        actual_id = str(plugin_items[0].get("Id", plugin_items[0].get("id", ""))).lower()
        print("JELLYFIN_12_4_PLUGIN_LOADED=PASS")
        guid_matches = actual_id.replace("-", "") == PLUGIN_ID.replace("-", "")
        print("JELLYFIN_PLUGIN_GUID_MATCH=" + ("PASS" if guid_matches else "FAIL"))
        if not guid_matches:
            print("JELLYFIN_PLUGIN_OBSERVED_GUID=" + actual_id)
        print("JELLYFIN_LOCAL_ADMIN_PRESERVED=PASS")

        # CI TEST ONLY: enable the otherwise disabled auth plugin in disposable Jellyfin.
        code, _ = request("POST", "/Plugins/" + actual_id + "/Configuration",
                          {"Enabled": True, "ProvisionPrivateLibraries": True}, token=token)
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

        # CI-only integration: successful Mega login itself creates the isolated
        # Jellyfin libraries; there is no admin playlist/folder setup per account.
        code, customer_b = request("POST", "/Users/AuthenticateByName", {
            "Username": "synthetic-mega-user-b", "Pw": "synthetic-mega-password-b"})
        if code != 200 or not customer_b or not customer_b.get("AccessToken"):
            raise RuntimeError("Synthetic second subscription rejected")
        account_a = customer["User"]["Id"]
        account_b = customer_b["User"]["Id"]
        if account_a.replace("-", "") == account_b.replace("-", ""):
            raise RuntimeError("Two synthetic customers share Jellyfin identity")

        users = {
            "alpha": (account_a, customer["AccessToken"]),
            "beta": (account_b, customer_b["AccessToken"]),
        }
        code, folder_info = request("GET", "/Library/VirtualFolders", token=token)
        if code != 200 or not isinstance(folder_info, list):
            raise RuntimeError("Cannot inspect provisioned user libraries")
        ids = {}
        for owner, (user_id, _) in users.items():
            opaque_id = user_id.replace("-", "").lower()
            name = "VivoAPK-" + opaque_id
            matches = [x for x in folder_info if x.get("Name") == name]
            if len(matches) != 1 or not matches[0].get("ItemId"):
                raise RuntimeError("Account did not get its own automatic native library")
            locations = matches[0].get("Locations") or []
            if len(locations) != 1:
                raise RuntimeError("Provisioned library must have exactly one media location")
            media_dir = Path(locations[0]).resolve()
            if (not media_dir.is_relative_to(DATAROOT.resolve()) or
                    media_dir.parts[-2:] != ("vivoapk-private-libraries", opaque_id)):
                raise RuntimeError("Provisioned location escapes the private account directory")
            ids[owner] = matches[0]["ItemId"]
            code, profile = request("GET", "/Users/" + user_id, token=token)
            if code != 200 or not isinstance(profile, dict) or "Policy" not in profile:
                raise RuntimeError("Cannot read native provisioning policy")
            policy = profile["Policy"]
            if (policy.get("EnableAllFolders") is not False or
                    policy.get("EnabledFolders") != [ids[owner]] or
                    policy.get("EnableAllChannels") is not False or
                    policy.get("EnablePublicSharing") is not False or
                    policy.get("EnableContentDownloading") is not False):
                raise RuntimeError("Auto-provisioning did not restrict native folder access")

            # Only test fixtures; production deliberately starts with EMPTY libraries.
            media_dir.mkdir(parents=True, exist_ok=True)
            media_file = media_dir / ("vivo_ci_" + owner + "_only.mp4")
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=1",
                 "-an", "-c:v", "mpeg4", str(media_file)],
                check=True, timeout=40,
            )
        if ids["alpha"] == ids["beta"]:
            raise RuntimeError("Native account libraries share an ID")
        print("JELLYFIN_SYNTHETIC_AUTO_PROVISIONED_LIBRARIES=PASS")
        print("JELLYFIN_SYNTHETIC_USER_SCOPED_POLICIES=PASS")

        before_count = len(folder_info)
        code, again = request("POST", "/Users/AuthenticateByName", {
            "Username": "synthetic-mega-user", "Pw": "synthetic-mega-password"})
        if code != 200 or not again or again["User"]["Id"] != account_a:
            raise RuntimeError("Reauthentication did not preserve the same identity")
        code, folder_info = request("GET", "/Library/VirtualFolders", token=token)
        if code != 200 or len(folder_info) != before_count:
            raise RuntimeError("Repeated login produced extra Jellyfin libraries")
        print("JELLYFIN_SYNTHETIC_RELOGIN_IDEMPOTENT=PASS")

        code, _ = request("POST", "/Library/Refresh", None, token=token)
        if code not in (200, 204):
            raise RuntimeError("Test-only library scan failed to start")

        def list_movies(customer_token, user_id):
            code, data = request(
                "GET", "/Items?" + urlencode({
                    "userId": user_id,
                    "recursive": "true",
                    "includeItemTypes": "Movie",
                    "limit": 50,
                }), token=customer_token)
            if code != 200 or not isinstance(data, dict):
                raise RuntimeError("User-scoped library listing failed")
            return data.get("Items", [])

        # Jellyfin revokes earlier tokens when the same test device logs in again.
        account_a_token = again["AccessToken"]
        account_b_token = users["beta"][1]
        alpha_id = beta_id = None
        for _ in range(45):
            items_a = list_movies(account_a_token, account_a)
            items_b = list_movies(account_b_token, account_b)
            owners_a = [x for x in items_a if "vivo_ci_alpha" in x.get("Name", "").lower()]
            owners_b = [x for x in items_b if "vivo_ci_beta" in x.get("Name", "").lower()]
            if owners_a and owners_b:
                alpha_id = owners_a[0]["Id"]
                beta_id = owners_b[0]["Id"]
                break
            time.sleep(2)
        if not alpha_id or not beta_id:
            raise RuntimeError("Synthetic media were not indexed in time")
        if any("vivo_ci_beta" in x.get("Name", "").lower() for x in items_a):
            raise RuntimeError("Customer A can see customer B media")
        if any("vivo_ci_alpha" in x.get("Name", "").lower() for x in items_b):
            raise RuntimeError("Customer B can see customer A media")
        print("JELLYFIN_SYNTHETIC_TWO_LIBRARIES_ISOLATED_IN_LISTINGS=PASS")

        for customer_token, forbidden_item in (
            (account_a_token, beta_id), (account_b_token, alpha_id)
        ):
            for endpoint in (
                "/Items/" + forbidden_item,
                "/Items/" + forbidden_item + "/File",
            ):
                code, _ = request("GET", endpoint, token=customer_token)
                if code == 200:
                    raise RuntimeError("Foreign media ID bypasses library permissions")
        print("JELLYFIN_SYNTHETIC_CROSS_ACCOUNT_DIRECT_IDS_DENIED=PASS")

        # Revocation test: disabling the second identity must revoke old tokens.
        code, profile_b = request("GET", "/Users/" + account_b, token=token)
        if code != 200 or not profile_b:
            raise RuntimeError("Cannot re-read second user")
        policy_b = profile_b["Policy"]
        policy_b["IsDisabled"] = True
        code, _ = request("POST", "/Users/" + account_b + "/Policy", policy_b, token=token)
        if code not in (200, 204):
            raise RuntimeError("Cannot disable second test identity")
        code, _ = request("GET", "/Users/Me", token=account_b_token)
        if code == 200:
            raise RuntimeError("Disabled user token was not revoked")
        print("JELLYFIN_SYNTHETIC_DISABLED_USER_REVOKED=PASS")
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
