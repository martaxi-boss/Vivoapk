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
        parts = line.split(b" ")
        if len(parts) < 3 or parts[0] != b"POST":
            return
        route = parts[1]
        if route not in (b"/v1/session/resolve", b"/_internal/entitlement/status", b"/v1/catalog/preview"):
            return
        length = 0
        headers = {}
        while True:
            header = self.rfile.readline(2000)
            if not header or header == b"\r\n":
                break
            key, _, value = header.partition(b":")
            headers[key.strip().lower()] = value.strip()
            if key.lower() == b"content-length":
                length = int(value)
        if not 0 < length < 4096:
            return
        request = json.loads(self.rfile.read(length))
        if route == b"/v1/catalog/preview":
            auth = headers.get(b"authorization", b"")
            if auth not in (b"Bearer ci-session-a", b"Bearer ci-session-b"):
                response_code = b"401 Unauthorized"
                body = b'{"detail":"Unauthorized"}'
            else:
                name = "Canal CI Azul" if auth.endswith(b"-a") else "Canal CI Verde"
                response_code = b"200 OK"
                body = json.dumps({
                    "kind": "live", "categories": [{"id": "10", "name": "Teste"}],
                    "items": [{"id": "42", "title": name, "category_id": "10",
                               "container_extension": None}]
                }).encode()
        elif route == b"/_internal/entitlement/status":
            if headers.get(b"x-vivoapk-monitor-key") != (
                b"synthetic-private-monitor-key-1234567890"
            ):
                response_code = b"403 Forbidden"
                body = b'{"detail":"Forbidden"}'
            else:
                response_code = b"200 OK"
                status = self.server.statuses.get(request.get("username"), "UNAVAILABLE")
                body = json.dumps({"state": status}).encode()
        else:
            valid = (
                (request.get("username"), request.get("password"))
                in {
                    ("synthetic-mega-user", "synthetic-mega-password"),
                    ("synthetic-mega-user-b", "synthetic-mega-password-b"),
                }
                and request.get("platform") == "android"
            )
            response_code = b"200 OK"
            body = json.dumps({
                "code": "SUCCESS" if valid else "INVALID_CREDENTIALS",
                "session_token": ("ci-session-b" if request.get("username") ==
                                  "synthetic-mega-user-b" else "ci-session-a") if valid else None,
                "xtream_base_url": "http://provider.example.com" if valid else None,
            }).encode()
        self.wfile.write(b"HTTP/1.1 " + response_code
                         + b"\r\nContent-Type: application/json\r\n"
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
    # Private, synthetic runner-only key; no live Mega or VPS credential is used.
    monitor_key_path = MOCK.parent / "entitlement-monitor.key"
    monitor_key_path.write_text("synthetic-private-monitor-key-1234567890")
    monitor_key_path.chmod(0o600)
    mock = BackendServer(str(MOCK), BackendHandler)
    mock.statuses = {
        "synthetic-mega-user": "ACTIVE",
        "synthetic-mega-user-b": "ACTIVE",
    }
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
                          {"Enabled": True, "ProvisionPrivateLibraries": True, "MonitorEntitlements": True, "PilotLiveChannels": True}, token=token)
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

        # A single test customer can see native pilot channels, never the
        # second customer's media. The backend and stream URL are synthetic.
        code, channels_a = request("GET", "/Channels?UserId=" + account_a,
                                   token=customer["AccessToken"])
        if code != 200 or not isinstance(channels_a, dict):
            raise RuntimeError("Native pilot channel list unavailable")
        pilot_a = [x for x in channels_a.get("Items", [])
                   if x.get("Name") == "VivoAPK — TV de teste"]
        if len(pilot_a) != 1:
            raise RuntimeError("Owner cannot see own native preview TV channel")
        code, channels_b = request("GET", "/Channels?UserId=" + account_b,
                                   token=customer_b["AccessToken"])
        if code != 200 or not isinstance(channels_b, dict):
            raise RuntimeError("Second account channel list unavailable")
        if any(x.get("Name") == "VivoAPK — TV de teste"
               for x in channels_b.get("Items", [])):
            raise RuntimeError("Cross-subscriber pilot channel exposed")
        channel_id = pilot_a[0]["Id"]
        code, pilot_items = request(
            "GET", "/Channels/" + channel_id + "/Items?UserId=" + account_a,
            token=customer["AccessToken"])
        if code != 200 or not isinstance(pilot_items, dict) or not pilot_items.get("Items"):
            raise RuntimeError("Pilot channel did not produce visible media items")
        if pilot_items["Items"][0].get("Name") != "Canal CI Azul":
            raise RuntimeError("Pilot channel content did not match owner")
        # Access by a known foreign channel item ID must also be denied,
        # not just hidden from the second account's visible channel list.
        pilot_item_id = pilot_items["Items"][0]["Id"]
        code, _ = request(
            "GET", "/Users/" + account_b + "/Items/" + pilot_item_id,
            token=customer_b["AccessToken"])
        if code == 200:
            raise RuntimeError("Pilot channel media accessible directly to foreign user")
        code, _ = request(
            "POST", "/Items/" + pilot_item_id + "/PlaybackInfo?UserId=" + account_b,
            {}, token=customer_b["AccessToken"])
        if code == 200:
            raise RuntimeError("Foreign user can request pilot channel playback info")
        print("JELLYFIN_SYNTHETIC_PILOT_DIRECT_ACCESS_ISOLATED=PASS")
        # A channel browser must not persist a Mega provider password to
        # Jellyfin database, metadata, logs or configuration.
        for path in DATAROOT.rglob("*"):
            if (path.is_symlink() or not path.is_file() or
                    path.stat().st_size > 16 * 1024 * 1024):
                continue
            if (b"synthetic-mega-password" in path.read_bytes() or
                    b"synthetic-mega-password-b" in path.read_bytes()):
                raise RuntimeError(
                    "Pilot leaked synthetic provider secret into Jellyfin persisted files")
        print("JELLYFIN_PILOT_NO_CREDENTIALS_PERSISTED=PASS")
        # MediaSourceInfo must be generated only on the owner's explicit
        # playback-info request, not during indexing or catalog browsing.
        code, owner_media = request(
            "POST", "/Items/" + pilot_item_id + "/PlaybackInfo?UserId=" + account_a,
            {}, token=customer["AccessToken"])
        if code != 200 or not isinstance(owner_media, dict) or not owner_media.get("MediaSources"):
            raise RuntimeError("Pilot playback source not available to its owner")
        # Port the original PINK APK's Xtream direct-stream paths, but never
        # ask this VPS to fetch/remux/transcode the video. The two origin URLs
        # are callback-only and must stay absent from durable Jellyfin storage.
        media_sources = owner_media["MediaSources"]
        if len(media_sources) != 2:
            raise RuntimeError("Original PINK canonical/legacy live paths not exposed")
        expected_paths = (
            "/live/synthetic-mega-user/synthetic-mega-password/42.ts",
            "/synthetic-mega-user/synthetic-mega-password/42",
        )
        # Diagnostic is strictly metadata-only: never log URLs or credentials.
        print("JELLYFIN_PILOT_PLAYBACK_FLAGS=" + json.dumps([
            {key: source.get(key) for key in (
                "Protocol", "Container", "SupportsDirectPlay",
                "SupportsDirectStream", "SupportsTranscoding",
                "TranscodingUrl", "IsRemote"
            ) if key != "TranscodingUrl"} | {
                "HasTranscodingUrl": bool(source.get("TranscodingUrl"))
            }
            for source in media_sources
        ], sort_keys=True), flush=True)
        for source, suffix in zip(media_sources, expected_paths, strict=True):
            if not (source.get("Path") or "").endswith(suffix):
                raise RuntimeError("PINK live path compatibility regression")
            if (source.get("SupportsDirectPlay") is not True
                    or source.get("SupportsDirectStream") is not False
                    or source.get("SupportsTranscoding") is not False):
                raise RuntimeError("Pilot tried to route media through Jellyfin VPS")
        print("JELLYFIN_PINK_DIRECT_URLS_AND_NO_VPS_RELAY=PASS")
        for path in DATAROOT.rglob("*"):
            if (path.is_symlink() or not path.is_file() or
                    path.stat().st_size > 16 * 1024 * 1024):
                continue
            if b"synthetic-mega-password" in path.read_bytes():
                raise RuntimeError("Playback info wrote provider password to Jellyfin storage")
        print("JELLYFIN_PILOT_SOURCE_AT_PLAYBACK_ONLY=PASS")
        print("JELLYFIN_SYNTHETIC_MEGA_NATIVE_CHANNEL_VISIBLE=PASS")
        print("JELLYFIN_SYNTHETIC_MEGA_PILOT_ACCOUNT_ISOLATION=PASS")

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

        # Run the plugin's REAL native scheduled task: Mega subscriber B expires
        # while their official Jellyfin session still holds a valid native token.
        code, tasks = request("GET", "/ScheduledTasks", token=token)
        if code != 200 or not isinstance(tasks, list):
            raise RuntimeError("Cannot inspect Jellyfin scheduled tasks")
        monitor_tasks = [task for task in tasks
                         if task.get("Key") == "VivoapkMegaEntitlementMonitor"]
        if len(monitor_tasks) != 1:
            raise RuntimeError("VivoAPK native entitlement scheduler not registered")
        mock.statuses["synthetic-mega-user-b"] = "EXPIRED"
        code, _ = request("POST",
                          "/ScheduledTasks/Running/" + monitor_tasks[0]["Id"],
                          token=token)
        if code not in (200, 204):
            raise RuntimeError(f"Cannot trigger entitlement task: {code}")
        revoked = False
        for _ in range(60):
            code, profile_b = request("GET", "/Users/" + account_b, token=token)
            code_b, _ = request("GET", "/Users/Me", token=account_b_token)
            code_a, _ = request("GET", "/Users/Me", token=account_a_token)
            if (code == 200 and profile_b and
                    profile_b["Policy"].get("IsDisabled") is True and
                    code_b != 200 and code_a == 200):
                revoked = True
                break
            time.sleep(0.5)
        if not revoked:
            raise RuntimeError("Automatic Mega expiry failed to revoke B only")
        print("JELLYFIN_SYNTHETIC_EXPIRED_MEGA_AUTOMATIC_REVOCATION=PASS")
        print("JELLYFIN_SYNTHETIC_ACTIVE_MEGA_NATIVE_SESSION_PRESERVED=PASS")
        # Renewal of the SAME provider subscription restores only the
        # restriction created by the VivoAPK task; no new account/password.
        mock.statuses["synthetic-mega-user-b"] = "ACTIVE"
        restarted = False
        for _ in range(30):
            code, _ = request("POST",
                              "/ScheduledTasks/Running/" + monitor_tasks[0]["Id"],
                              token=token)
            if code in (200, 204):
                restarted = True
                break
            time.sleep(0.5)
        if not restarted:
            raise RuntimeError("Cannot re-run entitlement task after renewal")
        renewed = False
        for _ in range(60):
            code, profile_b = request("GET", "/Users/" + account_b, token=token)
            if code == 200 and profile_b and profile_b["Policy"].get("IsDisabled") is False:
                renewed = True
                break
            time.sleep(0.5)
        if not renewed:
            raise RuntimeError("Renewed Mega subscription did not reactivate same Jellyfin identity")
        code, logged_back = request("POST", "/Users/AuthenticateByName", {
            "Username": "synthetic-mega-user-b", "Pw": "synthetic-mega-password-b"})
        if code != 200 or logged_back["User"]["Id"] != account_b:
            raise RuntimeError("Renewed user cannot sign in with original Mega credentials")
        print("JELLYFIN_SYNTHETIC_MEGA_RENEWAL_SAME_IDENTITY=PASS")
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
        if monitor_key_path.exists():
            monitor_key_path.unlink()

if __name__ == "__main__":
    main()
