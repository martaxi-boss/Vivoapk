"""Disposable real Jellyfin 12.4 integration test. No Mega OTT accounts used."""
import hashlib
import http.client
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import threading
import time
from urllib.parse import urlencode, urljoin, urlsplit, parse_qsl, urlunsplit, unquote
from urllib.request import urlopen

PLUGIN_ID = "e7a31bad-3643-4da2-834e-559c39cc90b1"
MOCK = Path("/srv/projects/Vivoapk/run/backend.sock")
CATEGORY_MODE = os.environ.get("VIVOAPK_CI_CATEGORY_MODE") == "1"
SERVER_PLAYBACK = os.environ.get("VIVOAPK_CI_SERVER_PLAYBACK") == "1"
LONG_CACHE = os.environ.get("VIVOAPK_CI_LONG_CACHE") == "1"
if LONG_CACHE and not SERVER_PLAYBACK:
    raise RuntimeError("Long cache test requires the disposable native playback lab")
if SERVER_PLAYBACK and not CATEGORY_MODE:
    raise RuntimeError("Native lab requires category mode")

class BackendHandler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(1500)
        parts = line.split(b" ")
        if len(parts) < 3 or parts[0] != b"POST":
            return
        route = parts[1]
        if route not in (b"/v1/session/resolve", b"/_internal/entitlement/status",
                          b"/v1/catalog/preview", b"/v1/catalog/list",
                          b"/v1/catalog/categories", b"/v1/catalog/category-items",
                          b"/v1/catalog/series"):
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
        if route in (b"/v1/catalog/categories", b"/v1/catalog/category-items",
                     b"/v1/catalog/series"):
            if route == b"/v1/catalog/category-items":
                self.server.category_item_calls += 1
            auth = headers.get(b"authorization", b"")
            is_a = auth == b"Bearer ci-session-a"
            is_b = auth == b"Bearer ci-session-b"
            name = "Azul" if is_a else "Verde"
            kind = request.get("kind", "series" if route == b"/v1/catalog/series" else "live")
            if kind == "live":
                ids = ("10", "11") if is_a else ("20", "21")
                categories = [
                    {"id": ids[0], "name": "Portugal " + name},
                    {"id": ids[1], "name": "Desporto " + name},
                ]
            elif kind == "vod":
                ids = ("110",) if is_a else ("120",)
                categories = [{"id": ids[0], "name": "Filmes " + name}]
            elif kind == "series":
                ids = ("210",) if is_a else ("220",)
                categories = [{"id": ids[0], "name": "Séries " + name}]
            else:
                ids, categories = (), []
            if not (is_a or is_b):
                response_code = b"401 Unauthorized"
                body = b'{"detail":"Unauthorized"}'
            elif route == b"/v1/catalog/categories":
                response_code = b"200 OK"
                body = json.dumps({
                    "kind": kind, "categories": categories, "items": []
                }).encode()
            elif request.get("category_id") not in ids:
                response_code = b"401 Unauthorized"
                body = b'{"detail":"Unauthorized"}'
            elif route == b"/v1/catalog/series":
                if request.get("series_id") != "601":
                    response_code = b"401 Unauthorized"
                    body = b'{"detail":"Unauthorized"}'
                else:
                    response_code = b"200 OK"
                    body = json.dumps({
                        "series_id": "601", "title": "Série " + name,
                        "seasons": [{"id": "1", "title": "Temporada 1"}],
                        "episodes": [{"id": "701", "season_id": "1",
                                      "title": "Episódio " + name,
                                      "episode_number": "1",
                                      "container_extension": "mp4"}],
                    }).encode()
            else:
                response_code = b"200 OK"
                category_id = request["category_id"]
                if kind == "live":
                    expanded = [{
                        "id": str(42+i),
                        "title": "Canal CI " + name + ("" if i == 0 else " " + str(i)),
                        "category_id": category_id, "container_extension": None}
                        for i in range(6 if category_id in ("10", "20") else 1)]
                elif kind == "vod":
                    expanded = [{"id": "501", "title": "Filme " + name,
                                 "category_id": category_id,
                                 "container_extension": "mp4"}]
                else:
                    expanded = [{"id": "601", "title": "Série " + name,
                                 "category_id": category_id,
                                 "container_extension": None}]
                body = json.dumps({
                    "kind": kind, "categories": [], "items": expanded
                }).encode()
        elif route in (b"/v1/catalog/preview", b"/v1/catalog/list"):
            auth = headers.get(b"authorization", b"")
            if auth not in (b"Bearer ci-session-a", b"Bearer ci-session-b"):
                response_code = b"401 Unauthorized"
                body = b'{"detail":"Unauthorized"}'
            else:
                name = "Canal CI Azul" if auth.endswith(b"-a") else "Canal CI Verde"
                response_code = b"200 OK"
                expanded = [{"id": str(42 + i),
                             "title": name if i == 0 else name + " " + str(i),
                             "category_id": "10", "container_extension": None}
                            for i in range(6 if route == b"/v1/catalog/list" else 1)]
                body = json.dumps({
                    "kind": "live", "categories": [{"id": "10", "name": "Teste"}],
                    "items": expanded
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
                and request.get("platform") in (
                    ("tizen",) if request.get("username") == "synthetic-mega-user-b"
                    else ("android", "android_tv", "ios", "windows", "tizen", "webos"))
            )
            response_code = b"200 OK"
            body = json.dumps({
                "code": "SUCCESS" if valid else "INVALID_CREDENTIALS",
                "session_token": ("ci-session-b" if request.get("username") ==
                                  "synthetic-mega-user-b" else "ci-session-a") if valid else None,
                "xtream_base_url": (
                    self.server.provider_origin_tv if request.get("platform") in ("tizen", "webos")
                    else self.server.provider_origin) if valid else None,
            }).encode()
        self.wfile.write(b"HTTP/1.1 " + response_code
                         + b"\r\nContent-Type: application/json\r\n"
                         + b"Content-Length: " + str(len(body)).encode()
                         + b"\r\nConnection: close\r\n\r\n" + body)

class BackendServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class SyntheticHlsHandler(http.server.BaseHTTPRequestHandler):
    """Test-only provider, outside Jellyfin. No real subscriber media."""
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.requests += 1
        if self.path == "/fixture/native.ts" and SERVER_PLAYBACK:
            payload = (self.server.media_root / "native.ts").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if LONG_CACHE:
                # The previous uniformly paced fixture timed out waiting for a
                # native segment while FFmpeg was still reading. Supply a finite
                # initial burst for native probing (analyzeduration=200s in
                # the observed Jellyfin invocation), then keep the remaining
                # generated bytes paced at the fixture's mux rate. Never use
                # this handler for a live or third-party source.
                burst = min(len(payload), 220 * 75000)
                sent = 0
                started = time.monotonic()
                tail_started = None
                try:
                    for offset in range(0, len(payload), 15000):
                        chunk = payload[offset:offset + 15000]
                        self.wfile.write(chunk)
                        sent += len(chunk)
                        self.server.long_fixture_bytes_sent = sent
                        if sent >= burst:
                            if tail_started is None:
                                tail_started = time.monotonic()
                                print("JELLYFIN_NATIVE_LONG_FIXTURE_INITIAL_BURST=" + json.dumps({
                                    "bytes": sent, "elapsed_seconds": round(tail_started - started, 2),
                                    "fixture_bytes": len(payload)}), flush=True)
                            time.sleep(max(0, tail_started + (sent - burst) / 75000 - time.monotonic()))
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    print("JELLYFIN_NATIVE_LONG_FIXTURE_TRANSFER_END=" + json.dumps({
                        "bytes_sent": sent, "elapsed_seconds": round(time.monotonic() - started, 2),
                        "complete": sent == len(payload)}), flush=True)
            else:
                self.wfile.write(payload)
            return
        if self.path.startswith("/live/") and self.path.endswith(".m3u8"):
            self.send_response(302)
            self.send_header("Location", "/authorized/42.m3u8")
            self.end_headers()
            return
        name = Path(self.path).name
        if ((self.path.startswith("/movie/") and name == "501.mp4")
                or (self.path.startswith("/series/") and name == "701.mp4")):
            target = self.server.media_root / "fixture.mp4"
        else:
            target = self.server.media_root / (
                "fixture.m3u8" if name == "42.m3u8" else name)
        if (name not in ("42.m3u8", "part000.ts", "part001.ts", "part002.ts",
                         "501.mp4", "701.mp4") or not target.is_file()):
            self.send_error(404)
            return
        payload = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         "application/vnd.apple.mpegurl" if name.endswith(".m3u8")
                         else "video/mp4" if name.endswith(".mp4") else "video/mp2t")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

class Connection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=45 if SERVER_PLAYBACK else 8)
        self.path = str(path)
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(45 if SERVER_PLAYBACK else 8)
        self.sock.connect(self.path)

_SESSION_TEST_DEVICES = {}

def request(method, uri, data=None, token=None, client_name="VivoAPK-CI"):
    def clean(value):
        return "".join(ch for ch in str(value).lower() if ch.isascii() and ch.isalnum())[:48]
    identity = (data or {}).get("Username", "") if (
        uri == "/Users/AuthenticateByName" and isinstance(data, dict)) else ""
    new_device = "vivoapk-ci-" + clean(client_name) + ("-" + clean(identity) if identity else "")
    device_id = _SESSION_TEST_DEVICES.get(token, new_device)
    conn = Connection(DATAROOT / "run/jellyfin.sock")
    headers = {
        "Content-Type": "application/json", "Accept": "application/json",
        "Authorization": 'MediaBrowser Client="' + client_name + '", Device="CI", DeviceId="' + device_id + '", Version="1.0.0"'
    }
    if uri.startswith("/Startup/"):
        headers.pop("Authorization", None)
    if token:
        headers["Authorization"] = 'MediaBrowser Client="' + client_name + '", Device="CI", DeviceId="' + device_id + '", Version="1.0.0", Token="' + token + '"'
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
    if (uri == "/Users/AuthenticateByName" and code == 200
            and isinstance(parsed, dict) and parsed.get("AccessToken")):
        _SESSION_TEST_DEVICES[parsed["AccessToken"]] = device_id
    return code, parsed

def native_bytes(uri, token=None):
    conn = Connection(DATAROOT / "run/jellyfin.sock")
    headers = {"Accept": "*/*"}
    if token:
        headers["X-Emby-Token"] = token
        headers["Authorization"] = ('MediaBrowser Client="VivoAPK-CI", Device="CI", '
            'DeviceId="' + _SESSION_TEST_DEVICES.get(token, "vivoapk-ci-invalid") + '", Version="1.0.0", Token="' + token + '"')
    conn.request("GET", uri, headers=headers)
    response = conn.getresponse()
    result = (response.status, response.read(8 * 1024 * 1024),
              response.getheader("X-Vivoapk-Lab-Guard"))
    conn.close()
    return result


def native_route(current, relative, item_id):
    parsed = urlsplit(urljoin("http://jellyfin.local" + current, relative))
    parts = parsed.path.lower().split("/")
    if (parsed.scheme != "http" or parsed.netloc != "jellyfin.local"
            or len(parts) < 4 or parts[1] != "videos"
            or parts[2].replace("-", "") != item_id.lower().replace("-", "")):
        raise RuntimeError("Media route escaped disposable Jellyfin")
    return urlunsplit(("", "", parsed.path, parsed.query, ""))


def is_auth_query_key(key):
    canonical = "".join(c for c in key.lower() if c.isascii() and c.isalnum())
    return canonical in ("apikey", "accesstoken", "token", "xembytoken", "xmediabrowsertoken")


def strip_playback_credentials(uri):
    u = urlsplit(uri)
    return urlunsplit(("", "", u.path,
        urlencode([(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
                   if not is_auth_query_key(k)]), ""))


def safe_fixture_route(uri, owner_token):
    safe = strip_playback_credentials(uri)
    # Never print session values; a negative request must not retain its owner credential.
    if owner_token in unquote(safe):
        raise RuntimeError("Synthetic owner credential survived route sanitization")
    return safe


def native_query_fixture(uri, token, owner_token):
    safe = safe_fixture_route(uri, owner_token)
    u = urlsplit(safe)
    return urlunsplit(("", "", u.path,
        urlencode(parse_qsl(u.query, keep_blank_values=True) + [("ApiKey", token)]), ""))


def require_denied_response(response, stage, identity):
    status, body, guard = response
    if status not in (401, 403, 404) or guard != "denied" or body:
        raise RuntimeError("Native route denial failed: " + json.dumps({
            "stage": stage, "identity": identity, "status": status,
            "guard": guard, "response_bytes": len(body)}))


def require_native_query_owner(uri, owner_token, stage):
    status, body, guard = native_bytes(native_query_fixture(uri, owner_token, owner_token))
    if status != 200 or guard != "allowed":
        raise RuntimeError("Native query-auth owner failed: " + json.dumps({
            "stage": stage, "status": status, "guard": guard}))
    return body


def require_native_denial(uri, owner_token, foreign_token, stage):
    safe = safe_fixture_route(uri, owner_token)
    for name, denied_token in (("anonymous", None), ("foreign", foreign_token),
                               ("invalid", "synthetic-invalid-token")):
        response = native_bytes(safe, denied_token)
        require_denied_response(response, stage, name)
        if denied_token:
            require_denied_response(native_bytes(native_query_fixture(safe, denied_token, owner_token)),
                                    stage, name + "_query")
        print("JELLYFIN_NATIVE_ROUTE_DENIAL=" + json.dumps({
            "stage": stage, "identity": name, "status": response[0], "guard": response[2]}))


def require_native_owner(uri, owner_token, stage):
    status, body, guard = native_bytes(safe_fixture_route(uri, owner_token), owner_token)
    if status != 200 or guard != "allowed":
        raise RuntimeError("Native owner request failed: " + json.dumps({
            "stage": stage, "status": status, "guard": guard,
            "response_bytes": len(body)}))
    return body


def transcode_cache_files(base):
    """Inspect only the disposable server's native transcode folders."""
    base = base.resolve()
    files = {}
    for parent in (base / "cache", base / "data"):
        for folder in parent.rglob("transcodes"):
            if folder.is_symlink() or not folder.is_dir() or not folder.resolve().is_relative_to(base):
                raise RuntimeError("Unexpected synthetic transcode cache location")
            for candidate in folder.rglob("*"):
                if candidate.is_symlink():
                    raise RuntimeError("Unexpected symlink in synthetic transcode cache")
                if candidate.is_file() and candidate.suffix.lower() in (".ts", ".m3u8", ".m4s", ".mp4", ".aac"):
                    try:
                        files[candidate] = candidate.stat().st_size
                    except FileNotFoundError:
                        # Native cleaner may remove a segment between enumeration and stat.
                        continue
    return files


def verify_native_cache_cleanup(base, item_id, info, source, route, owner_token, foreign_token,
                                baseline, input_digest):
    created = {p: size for p, size in transcode_cache_files(base).items() if p not in baseline}
    if not any(p.suffix.lower() == ".ts" and size >= 188 for p, size in created.items()):
        raise RuntimeError("No native MPEG-TS cache files observed before stop")
    # Real Jellyfin playback session negotiated earlier. No arbitrary file
    # deletion and no new daemon: invoke the ordinary client playback-stop API.
    session = info.get("PlaySessionId") or next(
        (v for k, v in parse_qsl(urlsplit(route).query) if k.lower() == "playsessionid"), None)
    if not isinstance(session, str) or not session:
        raise RuntimeError("Missing native play session for cache lifecycle check")
    code, _ = request("POST", "/Sessions/Playing/Stopped", {
        "ItemId": item_id, "PlaySessionId": session,
        "MediaSourceId": source.get("Id"), "PositionTicks": 20000000,
    }, token=owner_token)
    if code not in (200, 204):
        raise RuntimeError("Native playback stop failed: HTTP " + str(code))
    for _ in range(50):
        remaining = {p: size for p, size in transcode_cache_files(base).items() if p not in baseline}
        if not remaining:
            break
        time.sleep(0.4)
    if remaining:
        raise RuntimeError("Native playback stop left synthetic transcode files: " +
                           json.dumps({"files": len(remaining), "bytes": sum(remaining.values())}))
    if hashlib.sha256((base / "synthetic-provider/native.ts").read_bytes()).hexdigest() != input_digest:
        raise RuntimeError("Native cache cleanup changed the authorized synthetic source")
    if request("GET", "/Users/Me", token=foreign_token)[0] != 200:
        raise RuntimeError("Native cache cleanup affected the other account")
    print("JELLYFIN_NATIVE_SERVER_CACHE_CLEANUP=" + json.dumps({
        "generated_files": len(created), "generated_bytes": sum(created.values()),
        "remaining_files": len(remaining), "native_stop_status": code,
        "synthetic_source_unchanged": True, "other_session_preserved": True,
        "scope": "disposable synthetic playback stop, not long running or abandoned sessions"
    }, sort_keys=True))
    print("JELLYFIN_NATIVE_SERVER_CACHE_CLEANUP_ON_STOP=PASS")


def native_cache_delta(base, baseline):
    return {p: size for p, size in transcode_cache_files(base).items() if p not in baseline}


def parse_timed_segments(body):
    result = []
    duration = None
    for raw in body.decode().splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0])
        elif line and not line.startswith("#") and duration is not None:
            if not 0 < duration <= 30:
                raise RuntimeError("Unexpected synthetic segment duration")
            result.append((duration, line))
            duration = None
    return result


def verify_long_cache(base, item_id, master_route, owner_token, foreign_token,
                      baseline, input_digest, provider, info, source):
    # Only the generated finite loopback fixture. No provider/customer stream.
    # Match an ordinary player's start/progress reports, not only its HLS GETs.
    session = info.get("PlaySessionId") or next(
        (v for k, v in parse_qsl(urlsplit(master_route).query)
         if k.lower() == "playsessionid"), None)
    if not isinstance(session, str) or not session:
        raise RuntimeError("No native play session for long cache fixture")

    print("JELLYFIN_NATIVE_LONG_SOURCE_METADATA=" + json.dumps({
        "runtime_ticks": source.get("RunTimeTicks"),
        "protocol": source.get("Protocol"),
        "container": source.get("Container"),
        "infinite": source.get("IsInfiniteStream"),
        "master_session_matches": any(k.lower() == "playsessionid" and v == session
            for k, v in parse_qsl(urlsplit(master_route).query)),
    }, sort_keys=True), flush=True)

    def report_progress(endpoint, seconds):
        status, _ = request("POST", endpoint, {
            "ItemId": item_id, "MediaSourceId": source.get("Id"),
            "PlaySessionId": session, "PositionTicks": int(seconds * 10000000),
            "PlayMethod": "Transcode", "IsPaused": False,
        }, token=owner_token)
        if status not in (200, 204):
            raise RuntimeError("Synthetic client playback report rejected: HTTP " + str(status))

    begin = time.monotonic()
    requests_before = provider.requests
    require_native_denial(master_route, owner_token, foreign_token, "long_master")
    body = require_native_owner(master_route, owner_token, "long_master")
    if not body.startswith(b"#EXTM3U"):
        raise RuntimeError("Long native fixture master was not HLS")
    media_route = master_route
    if b"#EXT-X-STREAM-INF" in body:
        candidates = [x.strip() for x in body.decode().splitlines()
                      if x.strip() and not x.startswith("#")]
        if not candidates:
            raise RuntimeError("Long native fixture had no media playlist")
        media_route = native_route(master_route, candidates[0], item_id)
    body = require_native_owner(media_route, owner_token, "long_playlist")
    timed = parse_timed_segments(body)
    if not timed:
        raise RuntimeError("Long native fixture had no timed segments")
    report_progress("/Sessions/Playing", 0)
    served = 0.0
    fetched = 0
    early = set()
    peak = 0
    max_files = 0
    last_route = None
    segment_timing = []
    for duration, relative in timed:
        if served >= 70:
            break
        if time.monotonic() - begin > 100:
            raise RuntimeError("Long native fixture exceeded bounded read interval")
        route = native_route(media_route, relative, item_id)
        query = {k.lower(): v for k, v in parse_qsl(urlsplit(route).query)}
        def read_ticks(key):
            value = query.get(key, "")
            return int(value) if value.isascii() and value.isdecimal() and len(value) <= 18 else None
        segment_timing.append({"runtime_ticks": read_ticks("runtimeticks"),
            "length_ticks": read_ticks("actualsegmentlengthticks"),
            "session_matches": query.get("playsessionid") == session})
        try:
            data = require_native_owner(route, owner_token, "long_segment")
        except TimeoutError as exc:
            observed = native_cache_delta(base, baseline)
            raise RuntimeError("Timed out waiting for a synthetic segment: " + json.dumps({
                "segments_already_served": fetched,
                "cache_files": len(observed), "cache_bytes": sum(observed.values()),
                "fixture_bytes_sent": getattr(provider, "long_fixture_bytes_sent", 0),
                "elapsed_seconds": round(time.monotonic() - begin, 2)})) from exc
        if len(data) < 188 or data[0] != 0x47:
            raise RuntimeError("Long native fixture segment was not MPEG-TS")
        served += duration
        fetched += 1
        last_route = route
        report_progress("/Sessions/Playing/Progress", served)
        observed = native_cache_delta(base, baseline)
        peak = max(peak, sum(observed.values()))
        max_files = max(max_files, len(observed))
        if fetched == 1:
            early = {p for p in observed if p.suffix.lower() == ".ts"}
        if peak > 32 * 1024 * 1024:
            raise RuntimeError("Synthetic cache exceeded the 32 MiB test limit")
    print("JELLYFIN_NATIVE_LONG_SEGMENT_TIMING=" + json.dumps({
        "count": len(segment_timing), "first": segment_timing[:1],
        "last": segment_timing[-1:],
        "missing_timing": sum(x["runtime_ticks"] is None or x["length_ticks"] is None
                              for x in segment_timing),
        "matching_sessions": sum(x["session_matches"] for x in segment_timing),
    }, sort_keys=True), flush=True)
    if served < 60 or not early or fetched < 5:
        raise RuntimeError("Insufficient sequential media for cache window test")
    # Give the native cleaner multiple observation opportunities while a
    # normal playback session is still active. Keep the two acceptance gates
    # independent: rolling retention failure must not hide idle cleanup.
    rolling = {}
    observed_begin = time.monotonic()
    for i in range(100):
        rolling = native_cache_delta(base, baseline)
        peak = max(peak, sum(rolling.values()))
        max_files = max(max_files, len(rolling))
        if peak > 32 * 1024 * 1024:
            raise RuntimeError("Synthetic cache exceeded the 32 MiB test limit")
        if early.difference(rolling) and any(p.suffix.lower() == ".ts" for p in rolling):
            break
        if i % 20 == 0:
            report_progress("/Sessions/Playing/Progress", served)
        time.sleep(0.5)
    removed_early = len(early.difference(rolling))
    rolling_pass = bool(removed_early and any(p.suffix.lower() == ".ts" for p in rolling))
    print("JELLYFIN_NATIVE_LONG_CACHE_ROLLING_STATUS=" + json.dumps({
        "passed": rolling_pass, "early_files_observed": len(early),
        "early_files_removed": removed_early, "files_remaining": len(rolling),
        "seconds_observed": round(time.monotonic() - observed_begin, 1),
        "media_seconds_served": round(served, 3),
        "native_progress_reports": True,
    }, sort_keys=True), flush=True)
    require_native_denial(last_route, owner_token, foreign_token, "long_cached_segment")
    # Final owner request proves current segment still plays. Then cease all
    # playback requests: do NOT send a stop event or manually remove files.
    data = require_native_owner(last_route, owner_token, "long_current_segment")
    if len(data) < 188 or data[0] != 0x47:
        raise RuntimeError("Current segment unavailable after rolling deletion")
    at_disconnect = native_cache_delta(base, baseline)
    if not at_disconnect:
        raise RuntimeError("No native cache existed before simulated disconnect")
    print("JELLYFIN_NATIVE_LONG_CACHE_ROLLING=" + json.dumps({
        "rolling_pass": rolling_pass,
        "fixture_duration_seconds": 360, "served_media_seconds": round(served, 3),
        "segments_served": fetched, "early_segments_removed": removed_early,
        "peak_sampled_cache_bytes": peak, "peak_sampled_file_count": max_files,
        "cache_files_at_disconnect": len(at_disconnect),
        "test_limit_bytes": 32 * 1024 * 1024,
        "scope": "finite synthetic HLS fixture, sampled cache, not an infinite broadcast"
    }, sort_keys=True), flush=True)
    disconnected_at = time.monotonic()
    remaining = at_disconnect
    for _ in range(100):
        time.sleep(1)
        remaining = native_cache_delta(base, baseline)
        if not remaining:
            break
        peak = max(peak, sum(remaining.values()))
        if peak > 32 * 1024 * 1024:
            raise RuntimeError("Abandoned synthetic cache exceeded test limit")
    if remaining:
        # Keep the automatic idle gate failed. Separately verify whether the
        # ordinary native stop can reclaim this abandoned/completed fixture.
        # This is evidence for a future owner/session-scoped lifecycle fix,
        # not automatic cleanup and not a production policy.
        failure = {"files": len(remaining), "bytes": sum(remaining.values()),
                   "fixture_bytes_sent": getattr(provider, "long_fixture_bytes_sent", 0),
                   "fixture_total_bytes": (base / "synthetic-provider/native.ts").stat().st_size,
                   "idle_seconds": round(time.monotonic() - disconnected_at, 1)}
        print("JELLYFIN_NATIVE_LONG_IDLE_CLEANUP=FAIL " + json.dumps(failure, sort_keys=True), flush=True)
        try:
            verify_native_cache_cleanup(base, item_id, info, source, last_route,
                                        owner_token, foreign_token, baseline, input_digest)
        except RuntimeError as exc:
            print("JELLYFIN_NATIVE_EXPLICIT_RECOVERY_AFTER_IDLE=FAIL", flush=True)
            raise RuntimeError("Automatic idle and explicit native recovery both failed: " +
                               json.dumps(failure, sort_keys=True)) from exc
        print("JELLYFIN_NATIVE_EXPLICIT_RECOVERY_AFTER_IDLE=PASS", flush=True)
        raise RuntimeError("Automatic idle cleanup failed; explicit recovery passed: " +
                           json.dumps(failure, sort_keys=True))
    if provider.requests <= requests_before:
        raise RuntimeError("Synthetic media origin was not contacted")
    if hashlib.sha256((base / "synthetic-provider/native.ts").read_bytes()).hexdigest() != input_digest:
        raise RuntimeError("Synthetic source changed")
    if request("GET", "/Users/Me", token=foreign_token)[0] != 200:
        raise RuntimeError("Other active fixture session was affected")
    print("JELLYFIN_NATIVE_LONG_CACHE_ABANDONED=" + json.dumps({
        "idle_cleanup_seconds": round(time.monotonic() - disconnected_at, 1),
        "remaining_files": 0, "peak_sampled_cache_bytes": peak,
        "playback_stop_sent": False, "source_unchanged": True,
        "other_session_preserved": True
    }, sort_keys=True), flush=True)
    print("JELLYFIN_NATIVE_LONG_IDLE_CLEANUP=PASS", flush=True)
    if not rolling_pass:
        raise RuntimeError("Native rolling cache deletion not observed; independent idle cleanup passed")
    print("JELLYFIN_NATIVE_LONG_CACHE_AND_IDLE_CLEANUP=PASS", flush=True)


def verify_parallel_cache_recovery(base, first_item, first_owner, first_token,
                                   second_item, second_owner, second_token, input_digest):
    """Two real overlapping FFmpeg jobs on this disposable synthetic server."""
    if not LONG_CACHE or not SERVER_PLAYBACK or not second_item or not second_owner:
        raise RuntimeError("Parallel cache test requires two synthetic accounts and long lab")
    if first_owner == second_owner or first_item == second_item:
        raise RuntimeError("Parallel fixture accounts/items are not distinct")
    baseline = transcode_cache_files(base)

    def start(item, owner, token, client):
        profile = {
            "Name": "CI parallel native HLS",
            "MaxStreamingBitrate": 2000000, "MaxStaticBitrate": 2000000,
            "DirectPlayProfiles": [],
            "TranscodingProfiles": [{
                "Type": "Video", "Container": "ts", "Protocol": "hls",
                "VideoCodec": "h264", "AudioCodec": "aac", "Context": "Streaming",
            }],
        }
        code, info = request("POST", "/Items/" + item + "/PlaybackInfo?UserId=" + owner,
            {"DeviceProfile": profile, "AutoOpenLiveStream": True,
             "EnableDirectPlay": False, "EnableDirectStream": False,
             "EnableTranscoding": True, "AllowVideoStreamCopy": False,
             "AllowAudioStreamCopy": False, "MaxStreamingBitrate": 2000000},
            token=token, client_name=client)
        sources = [x for x in (info or {}).get("MediaSources", [])
                   if x.get("SupportsTranscoding") is True and x.get("TranscodingUrl")]
        session = (info or {}).get("PlaySessionId")
        if code != 200 or len(sources) != 1 or not session:
            raise RuntimeError("Parallel native profile negotiation failed")
        source = sources[0]
        route = native_route("/", source["TranscodingUrl"], item)
        body = require_native_owner(route, token, "parallel_master")
        if not body.startswith(b"#EXTM3U"):
            raise RuntimeError("Parallel fixture has no HLS master")
        if b"#EXT-X-STREAM-INF" in body:
            variants = [x.strip() for x in body.decode().splitlines()
                        if x.strip() and not x.startswith("#")]
            if not variants:
                raise RuntimeError("Parallel fixture has no media playlist")
            route = native_route(route, variants[0], item)
            body = require_native_owner(route, token, "parallel_playlist")
        segments = parse_timed_segments(body)
        if len(segments) < 40:
            raise RuntimeError("Parallel fixture has insufficient media duration")
        state = {"item": item, "owner": owner, "token": token, "client": client,
                 "info": info, "source": source, "session": session,
                 "route": route, "segments": segments, "index": 0, "seconds": 0.0}
        report(state, "/Sessions/Playing")
        return state

    def report(state, endpoint):
        code, _ = request("POST", endpoint, {
            "ItemId": state["item"], "MediaSourceId": state["source"].get("Id"),
            "PlaySessionId": state["session"],
            "PositionTicks": int(state["seconds"] * 10000000),
            "PlayMethod": "Transcode", "IsPaused": False,
        }, token=state["token"], client_name=state["client"])
        if code not in (200, 204):
            raise RuntimeError("Parallel client playback report rejected: HTTP " + str(code))

    def consume(state):
        duration, relative = state["segments"][state["index"]]
        route = native_route(state["route"], relative, state["item"])
        query = {k.lower(): v for k, v in parse_qsl(urlsplit(route).query)}
        if query.get("playsessionid") != state["session"]:
            raise RuntimeError("Parallel media route has a different play session")
        payload = require_native_owner(route, state["token"], "parallel_segment")
        if len(payload) < 188 or payload[0] != 0x47:
            raise RuntimeError("Parallel native segment unavailable")
        state["last_route"] = route
        state["seconds"] += duration
        state["index"] += 1
        report(state, "/Sessions/Playing/Progress")

    def find_playlist(excluded):
        candidates = [p for p in native_cache_delta(base, excluded)
                      if p.suffix.lower() == ".m3u8" and len(p.stem) == 32
                      and all(c in "0123456789abcdef" for c in p.stem.lower())]
        if len(candidates) != 1:
            raise RuntimeError("Cannot prove a unique native playlist for parallel job")
        return candidates[0]

    def namespace(playlist):
        return {p: size for p, size in native_cache_delta(base, baseline).items()
                if p.parent == playlist.parent and (p == playlist or
                    (p.stem.startswith(playlist.stem) and
                     p.stem[len(playlist.stem):].isascii() and
                     p.stem[len(playlist.stem):].isdecimal()))}

    def ffmpeg_identity(playlist):
        # Linux disposable runner only. Read process metadata; never log argv,
        # send a signal, or touch processes/files outside the fixture.
        expected = os.fsencode(str(playlist))
        found = []
        for proc in Path("/proc").iterdir():
            if not proc.name.isdecimal():
                continue
            try:
                args = (proc / "cmdline").read_bytes().split(bytes([0]))
                if not args or Path(os.fsdecode(args[0])).name != "ffmpeg" or expected not in args:
                    continue
                start_ticks = (proc / "stat").read_text().rsplit(") ", 1)[1].split()[19]
                found.append((proc.name, start_ticks))
            except (OSError, ValueError, IndexError):
                continue
        return found

    a = start(first_item, first_owner, first_token, "VivoAPK-CI")
    for _ in range(4):
        consume(a)
    pa = find_playlist(baseline)
    before_b = transcode_cache_files(base)
    b = start(second_item, second_owner, second_token, "Jellyfin for Tizen")
    for _ in range(2):
        consume(b)
    pb = find_playlist(before_b)
    if pa == pb or a["session"] == b["session"]:
        raise RuntimeError("Parallel native jobs share cache or play session")
    ids_a, ids_b = ffmpeg_identity(pa), ffmpeg_identity(pb)
    if len(ids_a) != 1 or len(ids_b) != 1 or ids_a == ids_b:
        raise RuntimeError("Two simultaneous native FFmpeg processes not proven")
    if not namespace(pa) or not namespace(pb):
        raise RuntimeError("Parallel job cache namespaces not both observed")
    print("JELLYFIN_NATIVE_PARALLEL_TWO_PROCESSES=PASS", flush=True)
    require_native_denial(a["last_route"], first_token, second_token, "parallel_a")
    require_native_denial(b["last_route"], second_token, first_token, "parallel_b")
    abandoned_at = time.monotonic()
    peak = sum(native_cache_delta(base, baseline).values())
    # A receives no further media/progress and never sends a stop. B reads
    # fresh sequential media and real progress at approximately playback pace.
    while time.monotonic() - abandoned_at < 110:
        if not namespace(pa) and not ffmpeg_identity(pa):
            break
        if ffmpeg_identity(pb) != ids_b:
            raise RuntimeError("Second native FFmpeg job stopped or restarted during first recovery")
        time.sleep(2.8)
        consume(b)
        peak = max(peak, sum(native_cache_delta(base, baseline).values()))
        if peak > 64 * 1024 * 1024:
            raise RuntimeError("Parallel synthetic cache exceeded 64 MiB test bound")
    if namespace(pa) or ffmpeg_identity(pa):
        raise RuntimeError("Abandoned first job was not reclaimed during second playback")
    if ffmpeg_identity(pb) != ids_b or not namespace(pb):
        raise RuntimeError("Second job did not survive first recovery")
    prior_index = b["index"]
    consume(b)
    if b["index"] != prior_index + 1 or ffmpeg_identity(pb) != ids_b:
        raise RuntimeError("Second job failed to continue after first recovery")
    print("JELLYFIN_NATIVE_PARALLEL_ABANDONED_RECOVERY=" + json.dumps({
        "distinct_jobs": True, "first_remaining_files": 0,
        "first_process_absent": True, "second_process_unchanged": True,
        "second_segments_served": b["index"], "second_media_seconds": b["seconds"],
        "first_stop_sent": False, "peak_sampled_cache_bytes": peak,
        "observed_seconds": round(time.monotonic() - abandoned_at, 1),
    }, sort_keys=True), flush=True)
    verify_native_cache_cleanup(base, second_item, b["info"], b["source"],
                                b["last_route"], second_token, first_token, baseline, input_digest)
    print("JELLYFIN_NATIVE_PARALLEL_RECOVERY_ISOLATION=PASS", flush=True)


def verify_native_ffmpeg(item_id, owner, owner_token, foreign_token, base, provider, mock,
                         second_item=None, second_owner=None):
    cache_baseline = transcode_cache_files(base)
    input_digest = hashlib.sha256((base / "synthetic-provider/native.ts").read_bytes()).hexdigest()
    profile = {
        "Name": "CI server HLS H264 AAC",
        "MaxStreamingBitrate": 2000000, "MaxStaticBitrate": 2000000,
        "DirectPlayProfiles": [],
        "TranscodingProfiles": [{
            "Type": "Video", "Container": "ts", "Protocol": "hls",
            "VideoCodec": "h264", "AudioCodec": "aac", "Context": "Streaming",
        }],
    }
    code, info = request("POST", "/Items/" + item_id + "/PlaybackInfo?UserId=" + owner,
        {"DeviceProfile": profile, "AutoOpenLiveStream": True,
         "EnableDirectPlay": False, "EnableDirectStream": False, "EnableTranscoding": True,
         "AllowVideoStreamCopy": False, "AllowAudioStreamCopy": False,
         "MaxStreamingBitrate": 2000000}, token=owner_token)
    sources = (info or {}).get("MediaSources", [])
    ready = [s for s in sources if s.get("SupportsTranscoding") is True
             and s.get("SupportsDirectPlay") is False and s.get("TranscodingUrl")]
    if code != 200 or len(ready) != 1:
        raise RuntimeError("Native server profile not negotiated: " + json.dumps({
            "status": code, "error": (info or {}).get("ErrorCode"),
            "source_count": len(sources), "ready_count": len(ready)}))
    route = native_route("/", ready[0]["TranscodingUrl"], item_id)
    if LONG_CACHE:
        verify_long_cache(base, item_id, route, owner_token, foreign_token,
                          cache_baseline, input_digest, provider, info, ready[0])
        verify_parallel_cache_recovery(base, item_id, owner, owner_token,
                                       second_item, second_owner, foreign_token, input_digest)
        return
    # Report only alias key names, never a URI, identifier or token value.
    print("JELLYFIN_NATIVE_ROUTE_AUTH_QUERY_KEYS=" + json.dumps(sorted(
        {k for k, v in parse_qsl(urlsplit(route).query, keep_blank_values=True)
         if is_auth_query_key(k)})))
    master_route = route
    before = provider.requests
    require_native_denial(route, owner_token, foreign_token, "master_before_owner")
    query_route = native_query_fixture(route, owner_token, owner_token)
    require_denied_response(native_bytes(query_route, foreign_token), "master", "mixed_credentials")
    separator = "&" if "?" in query_route else "?"
    duplicate = query_route + separator + urlencode({"ApiKey": "synthetic-invalid-token"})
    require_denied_response(native_bytes(duplicate), "master", "duplicate_credentials")
    if provider.requests != before:
        raise RuntimeError("Denied requests reached the synthetic provider")
    body = require_native_owner(route, owner_token, "master")
    if not body.startswith(b"#EXTM3U") or not require_native_query_owner(
            route, owner_token, "master").startswith(b"#EXTM3U"):
        raise RuntimeError("Native HLS master was not a playlist")
    require_native_denial(route, owner_token, foreign_token, "master_after_owner")
    lines = [x.strip() for x in body.decode().splitlines() if x.strip()]
    if any(x.startswith("#EXT-X-STREAM-INF") for x in lines):
        media = [x for x in lines if not x.startswith("#")]
        if not media:
            raise RuntimeError("Master playlist has no media playlist")
        route = native_route(route, media[0], item_id)
        require_native_denial(route, owner_token, foreign_token, "playlist_before_owner")
        body = require_native_owner(route, owner_token, "playlist")
        if not body.startswith(b"#EXTM3U") or not require_native_query_owner(
                route, owner_token, "playlist").startswith(b"#EXTM3U"):
            raise RuntimeError("Native media playlist was not HLS")
        require_native_denial(route, owner_token, foreign_token, "playlist_after_owner")
    segments = [x.strip() for x in body.decode().splitlines()
                if x.strip() and not x.startswith("#")]
    if not segments:
        raise RuntimeError("Native playlist has no segments")
    out = base / "server-encoded-output.ts"
    first_segment = native_route(route, segments[0], item_id)
    with out.open("wb") as dest:
        for relative in segments[:2]:
            segment = native_route(route, relative, item_id)
            require_native_denial(segment, owner_token, foreign_token, "segment_before_owner")
            content = require_native_owner(segment, owner_token, "segment")
            if len(content) < 188 or content[0] != 0x47:
                raise RuntimeError("Native MPEG-TS segment unavailable")
            dest.write(content)
            query_content = require_native_query_owner(segment, owner_token, "segment")
            if query_content != content:
                raise RuntimeError("Native query-auth segment differs from header-auth segment")
            require_native_denial(segment, owner_token, foreign_token, "segment_after_owner")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type,codec_name", "-of", "json", str(out)],
                           capture_output=True, text=True, check=True, timeout=30)
    observed = {(s["codec_type"], s["codec_name"]) for s in json.loads(probe.stdout)["streams"]}
    if not {("video", "h264"), ("audio", "aac")}.issubset(observed):
        raise RuntimeError("Jellyfin output codecs were not H264/AAC")
    decoded = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error",
                              "-i", str(out), "-t", "2", "-map", "0:v:0", "-map", "0:a:0",
                              "-f", "framemd5", "pipe:1"], capture_output=True,
                             text=True, check=True, timeout=30)
    video = sum(x.startswith("0,") for x in decoded.stdout.splitlines())
    audio = sum(x.startswith("1,") for x in decoded.stdout.splitlines())
    if video < 20 or audio < 20 or provider.requests <= before:
        raise RuntimeError("Native output did not contain decoded video and audio")
    print("JELLYFIN_NATIVE_SERVER_H264_AAC=" + json.dumps({
        "input_video": "mpeg2video", "input_audio": "mp2",
        "output_video": "h264", "output_audio": "aac",
        "decoded_video_frames": video, "decoded_audio_frames": audio,
        "provider_requests_during_native_playback": provider.requests - before,
        "scope": "synthetic only, native server FFmpeg, not physical clients",
    }, sort_keys=True))
    out.unlink()
    print("JELLYFIN_NATIVE_SERVER_TRANSCODE_VIDEO_AUDIO=PASS")
    print("JELLYFIN_NATIVE_SERVER_FOREIGN_AND_ANONYMOUS_DENIAL=PASS")
    print("JELLYFIN_NATIVE_SERVER_QUERY_AND_HEADER_AUTH=PASS")

    # Verify the already-created HLS route and cached segment are denied
    # immediately when the private entitlement endpoint reports non-active.
    # The test does not wait for a scheduled task or create a new account.
    for state in ("EXPIRED", "DISABLED", "UNAVAILABLE"):
        mock.statuses["synthetic-mega-user"] = state
        count_before = provider.requests
        for label, uri in (("master", master_route), ("cached_segment", first_segment)):
            require_denied_response(native_bytes(safe_fixture_route(uri, owner_token), owner_token),
                                    label, state + "_header")
            require_denied_response(native_bytes(native_query_fixture(uri, owner_token, owner_token)),
                                    label, state + "_query")
        if provider.requests != count_before:
            raise RuntimeError("Revoked request reached synthetic media origin")
        code, _ = request("GET", "/Users/Me", token=foreign_token)
        if code != 200:
            raise RuntimeError("Other active fixture session was affected by denial")
        print("JELLYFIN_NATIVE_SERVER_ENTITLEMENT_DENIAL=" + state)
    mock.statuses["synthetic-mega-user"] = "ACTIVE"
    if not require_native_query_owner(master_route, owner_token, "renewed_master").startswith(b"#EXTM3U"):
        raise RuntimeError("Renewed fixture could not access own media")
    if require_native_owner(first_segment, owner_token, "renewed_segment")[0] != 0x47:
        raise RuntimeError("Renewed fixture could not access its own segment")
    print("JELLYFIN_NATIVE_SERVER_ACTIVE_SESSION_RESTORED=PASS")
    verify_native_cache_cleanup(base, item_id, info, ready[0], master_route,
                                owner_token, foreign_token, cache_baseline, input_digest)


def request_png(uri, *, token):
    """Read only local disposable Jellyfin PNG bytes, never a provider URL."""
    conn = Connection(DATAROOT / "run/jellyfin.sock")
    headers = {
        "Accept": "image/png",
        "Authorization": ('MediaBrowser Client="VivoAPK-CI", Device="CI", '
                          'DeviceId="' + _SESSION_TEST_DEVICES.get(token, "vivoapk-ci-vivoapkci") + '", Version="1.0.0", Token="' + token + '"'),
        "X-Emby-Token": token,
    }
    conn.request("GET", uri, headers=headers)
    response = conn.getresponse()
    content_type = response.getheader("Content-Type") or ""
    payload = response.read(65536)
    status = response.status
    conn.close()
    return status, content_type, payload


def main():
    global DATAROOT
    base = Path(os.environ["RUNNER_TEMP"]) / (
        "vivo-jellyfin-12.4-native-long-cache-smoke" if LONG_CACHE else
        "vivo-jellyfin-12.4-native-ffmpeg-smoke" if SERVER_PLAYBACK else
        "vivo-jellyfin-12.4-category-smoke" if CATEGORY_MODE
        else "vivo-jellyfin-12.4-smoke")
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
    mock.category_item_calls = 0
    synthetic_media = base / "synthetic-provider"
    synthetic_media.mkdir()
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=600:sample_rate=48000",
        "-t", "3", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p",
        "-g", "25", "-sc_threshold", "0", "-c:a", "aac",
        "-f", "hls", "-hls_time", "1", "-hls_list_size", "0",
        "-hls_segment_filename", str(synthetic_media / "part%03d.ts"),
        str(synthetic_media / "fixture.m3u8"),
    ], check=True, timeout=30)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=48000",
        "-t", "2", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-movflags", "+faststart",
        str(synthetic_media / "fixture.mp4"),
    ], check=True, timeout=30)
    if SERVER_PLAYBACK:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=650:sample_rate=48000",
            "-t", "360" if LONG_CACHE else "15", "-c:v", "mpeg2video", "-g", "25", "-threads", "1",
            "-c:a", "mp2", "-ac", "2", "-b:a", "128k",
            *(["-b:v", "380k", "-maxrate", "380k", "-bufsize", "760k",
               "-muxrate", "600k"] if LONG_CACHE else []),
            "-f", "mpegts", str(synthetic_media / "native.ts"),
        ], check=True, timeout=120 if LONG_CACHE else 30)
    provider = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SyntheticHlsHandler)
    provider.media_root = synthetic_media
    provider.requests = 0
    provider.long_fixture_bytes_sent = 0
    mock.provider_origin = "http://127.0.0.1:" + str(provider.server_port)
    mock.provider_origin_tv = "http://localhost:" + str(provider.server_port)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
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
    if SERVER_PLAYBACK:
        cmd.extend(["--ffmpeg", shutil.which("ffmpeg")])
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
        if LONG_CACHE:
            # Jellyfin v12.4 caches named configuration keys case-sensitively,
            # although its XML paths and store lookup are case-insensitive.
            # Use the exact key read by GetEncodingOptions / the segment cleaner.
            code, options = request("GET", "/System/Configuration/encoding", token=token)
            if code != 200 or not isinstance(options, dict) or "EnableSegmentDeletion" not in options:
                raise RuntimeError("Native encoding settings unavailable")
            options.update({"EnableSegmentDeletion": True, "SegmentKeepSeconds": 20,
                            "EnableThrottling": False})
            code, _ = request("POST", "/System/Configuration/encoding", options, token=token)
            if code not in (200, 204):
                raise RuntimeError("Disposable native encoding settings rejected")
            code, actual = request("GET", "/System/Configuration/encoding", token=token)
            if code != 200 or not actual.get("EnableSegmentDeletion") or actual.get("SegmentKeepSeconds") != 20:
                raise RuntimeError("Disposable native encoding settings not verified")
            print("JELLYFIN_NATIVE_LONG_CACHE_CONFIG=PASS", flush=True)
        media_policy_fields = ("EnablePlaybackRemuxing",
                               "EnableVideoPlaybackTranscoding",
                               "EnableAudioPlaybackTranscoding",
                               "ForceRemoteSourceTranscoding")
        no_download_share = ("EnableContentDownloading", "EnablePublicSharing")
        admin_media_policy_before = {key: admin["User"].get("Policy", {}).get(key)
                                     for key in media_policy_fields}
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
                          {"Enabled": True, "ProvisionPrivateLibraries": True, "MonitorEntitlements": True,
                           "PilotLiveChannels": True, "FullLiveCatalog": not CATEGORY_MODE,
                           "CategoryNavigation": CATEGORY_MODE,
                           "NativeServerPlaybackLab": SERVER_PLAYBACK}, token=token)
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
            "Username": "synthetic-mega-user-b", "Pw": "synthetic-mega-password-b"},
            client_name="Jellyfin for Tizen")
        if code != 200 or not customer_b or not customer_b.get("AccessToken"):
            raise RuntimeError("Synthetic second subscription rejected")
        account_a = customer["User"]["Id"]
        account_b = customer_b["User"]["Id"]
        if account_a.replace("-", "") == account_b.replace("-", ""):
            raise RuntimeError("Two synthetic customers share Jellyfin identity")
        # The channel-to-owner binding is written by the plugin itself, and
        # contains only GUIDs — no provider password or direct-play URL.
        from xml.etree import ElementTree
        configs = [p for root in (DATAROOT, base / "config")
                   for p in root.rglob("Jellyfin.Plugin.VivoapkMegaAuth.xml")
                   if p.is_file()]
        if len(configs) != 1:
            raise RuntimeError("Private channel reservation XML not persisted uniquely")
        config = ElementTree.parse(configs[0]).getroot()
        reserved = config.find("ChannelSlotOwners")
        owners = [x.text.replace("-", "").lower() for x in reserved
                  if x.text and x.text.strip()] if reserved is not None else []
        if len(owners) != 2 or set(owners) != {account_a.replace("-", "").lower(),
                                                account_b.replace("-", "").lower()}:
            raise RuntimeError("Jellyfin channel slots not durably reserved for owners")
        print("JELLYFIN_SYNTHETIC_CHANNEL_SLOT_OWNERS_PERSISTED=PASS")

        for customer_id in (account_a, account_b):
            code, profile = request("GET", "/Users/" + customer_id, token=token)
            if code != 200 or not isinstance(profile, dict):
                raise RuntimeError("Cannot read VivoAPK subscriber policy")
            expected = {k: SERVER_PLAYBACK for k in media_policy_fields[:3]}
            expected.update({k: False for k in media_policy_fields[3:] + no_download_share})
            if any(profile.get("Policy", {}).get(k) is not value for k, value in expected.items()):
                raise RuntimeError("Unexpected subscriber media policy")
        code, admin_after = request("GET", "/Users/" + admin["User"]["Id"],
                                    token=token)
        if code != 200 or any(
            admin_after.get("Policy", {}).get(k) != old_value
            for k, old_value in admin_media_policy_before.items()
        ):
            raise RuntimeError("Mega plugin altered Jellyfin admin media policy")
        print("JELLYFIN_SERVER_FFMPEG_USER_POLICY_AND_ADMIN=PASS" if SERVER_PLAYBACK
              else "JELLYFIN_MEGA_DIRECT_ONLY_USER_POLICY_AND_ADMIN=PASS")

        # EXPERIMENT: stage two concurrent account catalogues and probe native
        # Jellyfin ChannelManager/PlaybackInfo authorization across both.
        # No production deployment or real Mega credentials.
        code, channels_a = request("GET", "/Channels?UserId=" + account_a,
                                   token=customer["AccessToken"])
        if code != 200 or not isinstance(channels_a, dict):
            raise RuntimeError("Native pilot channel list unavailable")
        pilot_a = [x for x in channels_a.get("Items", [])
                   if x.get("Name", "").startswith("VivoAPK — Conteúdos ")]
        if len(pilot_a) != 1:
            raise RuntimeError("Owner cannot see own native preview TV channel")
        code, channels_b = request("GET", "/Channels?UserId=" + account_b,
                                   token=customer_b["AccessToken"], client_name="Jellyfin for Tizen")
        if code != 200 or not isinstance(channels_b, dict):
            raise RuntimeError("Second account channel list unavailable")
        pilot_b = [x for x in channels_b.get("Items", [])
                   if x.get("Name", "").startswith("VivoAPK — Conteúdos ")]
        if len(pilot_b) != 1:
            raise RuntimeError("Second subscribed account cannot see own preview: "
                               + json.dumps({"status": code, "own_count": len(pilot_b),
                                             "visible_channels": [x.get("Name") for x in (channels_b or {}).get("Items", [])]},
                                            sort_keys=True))
        channel_id = pilot_a[0]["Id"]
        if channel_id == pilot_b[0]["Id"]:
            raise RuntimeError("Two subscribers still share the same internal ChannelId")
        if CATEGORY_MODE:
            if mock.category_item_calls != 0:
                raise RuntimeError("Provider category streams loaded during login")
            # Jellyfin's GetLatestChannelItems eagerly refreshes one folder
            # level. Only our TV root and category metadata may be refreshed:
            # it MUST NOT fetch all 453 underlying category item lists.
            code, _ = request(
                "GET", "/Channels/Items/Latest?UserId=" + account_a
                + "&ChannelIds=" + channel_id,
                token=customer["AccessToken"])
            if code != 200 or mock.category_item_calls != 0:
                raise RuntimeError("Jellyfin eagerly downloaded all category items")
            print("JELLYFIN_SYNTHETIC_NO_EAGER_GLOBAL_CHANNEL_DOWNLOAD=PASS")
        for image_type in ("Primary", "Thumb"):
            image_code, mime, art = request_png(
                "/Items/" + channel_id + "/Images/" + image_type,
                token=customer["AccessToken"])
            if (image_code != 200 or not mime.startswith("image/png")
                    or not art.startswith(b"\x89PNG\r\n\x1a\n")):
                raise RuntimeError("Native pilot cover unavailable in real Jellyfin")
        print("JELLYFIN_NATIVE_PILOT_PRIMARY_THUMB_PNG=PASS")
        code, pilot_items = request(
            "GET", "/Channels/" + channel_id + "/Items?UserId=" + account_a,
            token=customer["AccessToken"])
        if code != 200 or not isinstance(pilot_items, dict) or not pilot_items.get("Items"):
            raise RuntimeError("Pilot channel did not produce visible media items")
        if CATEGORY_MODE:
            root_folders_a = pilot_items["Items"]
            if {x.get("Name") for x in root_folders_a} != {
                    "TV ao vivo", "Filmes", "Séries"}:
                raise RuntimeError("Native multimedia roots not exposed for A")
            code, pilot_items = request(
                "GET", "/Channels/" + channel_id + "/Items?UserId=" + account_a
                + "&FolderId=" + next(x["Id"] for x in root_folders_a
                                      if x.get("Name") == "TV ao vivo"),
                token=customer["AccessToken"])
            if code != 200 or not (pilot_items or {}).get("Items"):
                raise RuntimeError("Live TV root did not expose authorized categories for A")
            folders_a = pilot_items["Items"]
            if len(folders_a) != 2 or {x.get("Name") for x in folders_a} != {
                    "Portugal Azul", "Desporto Azul"}:
                observed = [{"name": x.get("Name"), "type": x.get("Type"),
                             "is_folder": x.get("IsFolder")} for x in folders_a]
                raise RuntimeError("Authorized Live categories did not become folders: "
                                   + json.dumps(observed, sort_keys=True))
            folder_id_a = next(x["Id"] for x in folders_a
                               if x.get("Name") == "Portugal Azul")
            code, pilot_items = request(
                "GET", "/Channels/" + channel_id + "/Items?UserId=" + account_a
                + "&FolderId=" + folder_id_a,
                token=customer["AccessToken"])
            if code != 200 or not isinstance(pilot_items, dict):
                raise RuntimeError("Cannot browse native category folder A")
            if mock.category_item_calls < 1:
                raise RuntimeError("Opening a category did not call backend category-items")
            print("JELLYFIN_SYNTHETIC_LAZY_FETCH_ONLY_ON_FOLDER_OPEN=PASS")
            print("JELLYFIN_SYNTHETIC_NATIVE_CATEGORY_FOLDERS_A=PASS")
        if (pilot_items["Items"][0].get("Name") != "Canal CI Azul"
                or len(pilot_items["Items"]) != 6):
            raise RuntimeError("Authorized Live category did not return six media items")
        pilot_item_id = pilot_items["Items"][0]["Id"]
        code, pilot_items_b = request(
            "GET", "/Channels/" + pilot_b[0]["Id"] + "/Items?UserId=" + account_b,
            token=customer_b["AccessToken"])
        if code != 200 or not isinstance(pilot_items_b, dict):
            raise RuntimeError("Second account cannot browse own channels")
        if CATEGORY_MODE:
            root_folders_b = pilot_items_b["Items"]
            if {x.get("Name") for x in root_folders_b} != {
                    "TV ao vivo", "Filmes", "Séries"}:
                raise RuntimeError("Native multimedia roots not exposed for B")
            code, pilot_items_b = request(
                "GET", "/Channels/" + pilot_b[0]["Id"] + "/Items?UserId=" + account_b
                + "&FolderId=" + next(x["Id"] for x in root_folders_b
                                      if x.get("Name") == "TV ao vivo"),
                token=customer_b["AccessToken"])
            if code != 200 or not (pilot_items_b or {}).get("Items"):
                raise RuntimeError("Live TV root did not expose authorized categories for B")
            folders_b = pilot_items_b["Items"]
            if len(folders_b) != 2 or {x.get("Name") for x in folders_b} != {
                    "Portugal Verde", "Desporto Verde"}:
                observed = [{"name": x.get("Name"), "type": x.get("Type"),
                             "is_folder": x.get("IsFolder")} for x in folders_b]
                raise RuntimeError("Native category listing crossed subscribers: "
                                   + json.dumps(observed, sort_keys=True))
            folder_id_b = next(x["Id"] for x in folders_b
                               if x.get("Name") == "Portugal Verde")
            code, pilot_items_b = request(
                "GET", "/Channels/" + pilot_b[0]["Id"] + "/Items?UserId=" + account_b
                + "&FolderId=" + folder_id_b,
                token=customer_b["AccessToken"])
            if code != 200 or not isinstance(pilot_items_b, dict):
                raise RuntimeError("Cannot browse native category folder B")
            code, foreign_folder = request(
                "GET", "/Channels/" + channel_id + "/Items?UserId=" + account_a
                + "&FolderId=" + folder_id_b,
                token=customer["AccessToken"])
            if code == 200 and (foreign_folder or {}).get("Items"):
                raise RuntimeError("CROSS_ACCOUNT_CATEGORY_FOLDER_BYPASS")
            print("JELLYFIN_SYNTHETIC_NATIVE_CATEGORY_FOLDERS_B=PASS")
            print("JELLYFIN_SYNTHETIC_CATEGORY_FOLDER_ISOLATION=PASS")
        names_b = [item.get("Name") for item in pilot_items_b.get("Items", [])]
        if (len(names_b) != 6 or names_b[0] != "Canal CI Verde"
                or "Canal CI Azul" in names_b):
            raise RuntimeError("MULTITENANT_CATALOG_LEAK_OR_CACHE_CROSSOVER")
        item_id_b = pilot_items_b["Items"][0]["Id"]
        if pilot_item_id == item_id_b:
            raise RuntimeError("Multiuser plugin indexed same Jellyfin item ID for both customers")
        if SERVER_PLAYBACK:
            verify_native_ffmpeg(pilot_item_id, account_a, customer["AccessToken"],
                                 customer_b["AccessToken"], base, provider, mock,
                                 second_item=item_id_b, second_owner=account_b)
            log.flush()
            for folder in (DATAROOT, base / "logs"):
                for candidate in folder.rglob("*"):
                    if (candidate.is_file() and not candidate.is_symlink()
                            and candidate.stat().st_size <= 8 * 1024 * 1024
                            and b"synthetic-mega-password" in candidate.read_bytes()):
                        raise RuntimeError("Native lab wrote fixture password to logs/storage")
            print("JELLYFIN_NATIVE_SERVER_NO_FIXTURE_PASSWORD_IN_STORAGE_OR_LOGS=PASS")
            return
        for foreign_user, foreign_token, victim_item in (
            (account_b, customer_b["AccessToken"], pilot_item_id),
            (account_a, customer["AccessToken"], item_id_b),
        ):
            code, _ = request(
                "GET", "/Users/" + foreign_user + "/Items/" + victim_item,
                token=foreign_token)
            if code == 200:
                raise RuntimeError("MULTITENANT_ITEM_ID_BYPASS: foreign account opened item")
            code, info = request(
                "POST", "/Items/" + victim_item + "/PlaybackInfo?UserId=" + foreign_user,
                {}, token=foreign_token)
            if code == 200:
                raise RuntimeError("MULTITENANT_PLAYBACKINFO_BYPASS: foreign account opened PlaybackInfo")
        # Also reject a stolen or guessed victim UserId in API query parameters;
        # being logged in as B must never grant A's channel or media.
        for victim_user, victim_channel, victim_item, attacker_token in (
            (account_a, channel_id, pilot_item_id, customer_b["AccessToken"]),
            (account_b, pilot_b[0]["Id"], item_id_b, customer["AccessToken"]),
        ):
            code, response = request(
                "GET", "/Channels/" + victim_channel + "/Items?UserId=" + victim_user,
                token=attacker_token)
            if code == 200 and (response or {}).get("Items"):
                raise RuntimeError("MULTITENANT_USERID_SPOOFING_LEAK: foreign channel listing")
            code, response = request(
                "POST", "/Items/" + victim_item + "/PlaybackInfo?UserId=" + victim_user,
                {}, token=attacker_token)
            if code == 200 and (response or {}).get("MediaSources"):
                raise RuntimeError("MULTITENANT_USERID_SPOOFING_PLAYBACK: foreign source")
        if CATEGORY_MODE:
            def open_group(owner_id, owner_token, provider_channel, root_folders,
                           root_name, category_name, client_name):
                root_id = next(x["Id"] for x in root_folders
                               if x.get("Name") == root_name)
                status, categories = request(
                    "GET", "/Channels/" + provider_channel + "/Items?UserId="
                    + owner_id + "&FolderId=" + root_id,
                    token=owner_token, client_name=client_name)
                if status != 200 or not categories or not categories.get("Items"):
                    raise RuntimeError("Multimedia category root not browseable")
                folder_id = next(x["Id"] for x in categories["Items"]
                                 if x.get("Name") == category_name)
                status, entries = request(
                    "GET", "/Channels/" + provider_channel + "/Items?UserId="
                    + owner_id + "&FolderId=" + folder_id,
                    token=owner_token, client_name=client_name)
                if status != 200 or not entries or not entries.get("Items"):
                    raise RuntimeError("Multimedia category items not browseable")
                return entries["Items"]

            multimedia = []
            for who, owner, own_token, channel, roots, label, client_name in (
                ("a", account_a, customer["AccessToken"], channel_id,
                 root_folders_a, "Azul", "Jellyfin Android"),
                ("b", account_b, customer_b["AccessToken"], pilot_b[0]["Id"],
                 root_folders_b, "Verde", "Jellyfin for Tizen"),
            ):
                movies = open_group(
                    owner, own_token, channel, roots,
                    "Filmes", "Filmes " + label, client_name)
                if len(movies) != 1 or movies[0].get("Name") != "Filme " + label:
                    raise RuntimeError("Subscriber movie catalogue leaked or missing")
                movie_id = movies[0]["Id"]
                series = open_group(
                    owner, own_token, channel, roots,
                    "Séries", "Séries " + label, client_name)
                if len(series) != 1 or series[0].get("Name") != "Série " + label:
                    raise RuntimeError("Subscriber series catalogue leaked or missing")
                show_id = series[0]["Id"]
                status, seasons = request(
                    "GET", "/Channels/" + channel + "/Items?UserId=" + owner
                    + "&FolderId=" + show_id,
                    token=own_token, client_name=client_name)
                if status != 200 or len((seasons or {}).get("Items", [])) != 1:
                    raise RuntimeError("Cannot browse authorized series seasons")
                season_id = seasons["Items"][0]["Id"]
                status, episodes = request(
                    "GET", "/Channels/" + channel + "/Items?UserId=" + owner
                    + "&FolderId=" + season_id,
                    token=own_token, client_name=client_name)
                if status != 200 or len((episodes or {}).get("Items", [])) != 1:
                    raise RuntimeError("Cannot browse authorized series episodes")
                episode_id = episodes["Items"][0]["Id"]
                if episodes["Items"][0].get("Name") != "Episódio " + label:
                    raise RuntimeError("Wrong subscriber episode metadata")
                for video_id, expected_kind, ending in (
                    (movie_id, "movie", "/501.mp4"),
                    (episode_id, "series", "/701.mp4"),
                ):
                    status, info = request(
                        "POST", "/Items/" + video_id + "/PlaybackInfo?UserId=" + owner,
                        {}, token=own_token, client_name=client_name)
                    sources = info.get("MediaSources", []) if isinstance(info, dict) else []
                    if status != 200 or len(sources) != 1:
                        raise RuntimeError("Private multimedia playback source not available")
                    source = sources[0]
                    provider_url = str(source.get("Path") or "")
                    if ("/" + expected_kind + "/" not in provider_url or
                            not provider_url.endswith(ending) or
                            source.get("SupportsDirectPlay") is not True or
                            source.get("SupportsTranscoding") is not False or
                            bool(source.get("TranscodingUrl")) or
                            str(source.get("TranscodingSubProtocol") or "").lower() == "hls"):
                        raise RuntimeError("Invalid direct-only MP4 source for subscriber")
                    # Download only synthetic test media direct from local provider,
                    # NOT through Jellyfin and NEVER log credential-bearing URLs.
                    with urlopen(provider_url, timeout=8) as stream:
                        payload = stream.read(2 * 1024 * 1024)
                    if b"ftyp" not in payload[:32] or len(payload) < 1000:
                        raise RuntimeError("Synthetic direct MP4 media corrupt")
                multimedia.append((who, movie_id, episode_id))
            for _, movie_id, episode_id in multimedia:
                other_id = account_b if _ == "a" else account_a
                other_token = customer_b["AccessToken"] if _ == "a" else customer["AccessToken"]
                for victim_id in (movie_id, episode_id):
                    code, _ = request(
                        "GET", "/Users/" + other_id + "/Items/" + victim_id,
                        token=other_token)
                    if code == 200:
                        raise RuntimeError("MULTIMEDIA_CROSS_SUBSCRIBER_ITEM_BYPASS")
                    code, leaked = request(
                        "POST", "/Items/" + victim_id + "/PlaybackInfo?UserId=" + other_id,
                        {}, token=other_token)
                    if code == 200 and (leaked or {}).get("MediaSources"):
                        raise RuntimeError("MULTIMEDIA_CROSS_SUBSCRIBER_SOURCE_BYPASS")
            print("JELLYFIN_SYNTHETIC_MOVIE_SERIES_EPISODE_FOLDERS=PASS")
            print("JELLYFIN_SYNTHETIC_DIRECT_MP4_FROM_PROVIDER=PASS")
            print("JELLYFIN_SYNTHETIC_MULTIMEDIA_CROSS_USER_DENIED=PASS")
        print("JELLYFIN_SYNTHETIC_USERID_SPOOFING_DENIED=PASS")
        if CATEGORY_MODE:
            print("JELLYFIN_SYNTHETIC_LAZY_CATEGORY_6_CHANNELS_PER_ACCOUNT=PASS")
        else:
            print("JELLYFIN_SYNTHETIC_FULL_LIVE_6_CHANNELS_PER_ACCOUNT=PASS")
        print("JELLYFIN_SYNTHETIC_TWO_ACTIVE_CHANNEL_ACCOUNTS_ISOLATED=PASS")
        print("JELLYFIN_SYNTHETIC_PILOT_DIRECT_ACCESS_ISOLATED=PASS")
        # Prove actual PROCESS restart reuses the same ChannelId slots without
        # reassigning existing indexed items to another subscriber. Test-only.
        prior_channel_a, prior_channel_b = channel_id, pilot_b[0]["Id"]
        previous_item_a, previous_item_b = pilot_item_id, item_id_b
        jellyfin.terminate()
        try:
            jellyfin.wait(timeout=12)
        except subprocess.TimeoutExpired:
            jellyfin.kill()
            jellyfin.wait(timeout=6)
        jellyfin = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                status, info = request("GET", "/System/Info/Public")
                if status == 200 and (info or {}).get("Version", "").startswith("12.4."):
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.3)
        else:
            raise RuntimeError("Jellyfin failed to restart after persisting slot owner bindings")
        status, renewed_admin = request("POST", "/Users/AuthenticateByName",
                                       {"Username": "ci-admin",
                                        "Pw": "synthetic-admin-password"})
        if status != 200 or not renewed_admin or not renewed_admin.get("AccessToken"):
            raise RuntimeError("Admin failed after server process restart")
        token = renewed_admin["AccessToken"]
        code, customer = request("POST", "/Users/AuthenticateByName",
                                 {"Username": "synthetic-mega-user",
                                  "Pw": "synthetic-mega-password"})
        code_b, customer_b = request("POST", "/Users/AuthenticateByName",
                                     {"Username": "synthetic-mega-user-b",
                                      "Pw": "synthetic-mega-password-b"},
                                     client_name="Jellyfin for Tizen")
        if (code != 200 or code_b != 200 or not customer or not customer_b or
                customer["User"]["Id"] != account_a or
                customer_b["User"]["Id"] != account_b):
            raise RuntimeError("Client identities not recovered after Jellyfin restart")
        code, restarted_channels_a = request(
            "GET", "/Channels?UserId=" + account_a,
            token=customer["AccessToken"])
        code_b, restarted_channels_b = request(
            "GET", "/Channels?UserId=" + account_b,
            token=customer_b["AccessToken"], client_name="Jellyfin for Tizen")
        id_list_a = [x.get("Id") for x in (restarted_channels_a or {}).get("Items", [])
                     if x.get("Name", "").startswith("VivoAPK — Conteúdos ")]
        id_list_b = [x.get("Id") for x in (restarted_channels_b or {}).get("Items", [])
                     if x.get("Name", "").startswith("VivoAPK — Conteúdos ")]
        if (code != 200 or code_b != 200 or
                id_list_a != [prior_channel_a] or id_list_b != [prior_channel_b]):
            raise RuntimeError("SUBSCRIBER_CHANNEL_RESERVATION_CHANGED_AFTER_RESTART")
        for foreign, credential, old_item in (
            (account_b, customer_b["AccessToken"], previous_item_a),
            (account_a, customer["AccessToken"], previous_item_b),
        ):
            status, _ = request("GET", "/Users/" + foreign + "/Items/" + old_item,
                                token=credential)
            if status == 200:
                raise RuntimeError("STALE_CHANNEL_ITEM_EXPOSED_AFTER_RESTART")
            status, response = request(
                "POST", "/Items/" + old_item + "/PlaybackInfo?UserId=" + foreign,
                {}, token=credential)
            if status == 200 and (response or {}).get("MediaSources"):
                raise RuntimeError("STALE_CHANNEL_PLAYBACK_EXPOSED_AFTER_RESTART")
        code, recent_items_a = request(
            "GET", "/Channels/" + prior_channel_a + "/Items?UserId=" + account_a,
            token=customer["AccessToken"])
        if code != 200 or not (recent_items_a or {}).get("Items"):
            raise RuntimeError("Subscriber A did not regain channel catalog after restart")
        if CATEGORY_MODE:
            code, recent_categories_a = request(
                "GET", "/Channels/" + prior_channel_a + "/Items?UserId=" + account_a
                + "&FolderId=" + next(
                    x["Id"] for x in recent_items_a["Items"] if x.get("Name") == "TV ao vivo"),
                token=customer["AccessToken"])
            if code != 200 or not (recent_categories_a or {}).get("Items"):
                raise RuntimeError("Cannot recover A categories after restart")
            code, recent_items_a = request(
                "GET", "/Channels/" + prior_channel_a + "/Items?UserId=" + account_a
                + "&FolderId=" + next(
                    x["Id"] for x in recent_categories_a["Items"]
                    if x.get("Name") == "Portugal Azul"),
                token=customer["AccessToken"])
            if code != 200 or not (recent_items_a or {}).get("Items"):
                raise RuntimeError("Subscriber A category unavailable after restart")
        pilot_item_id = recent_items_a["Items"][0]["Id"]
        code, recent_items_b = request(
            "GET", "/Channels/" + prior_channel_b + "/Items?UserId=" + account_b,
            token=customer_b["AccessToken"])
        if code != 200 or not (recent_items_b or {}).get("Items"):
            raise RuntimeError("Subscriber B did not regain channel catalog after restart")
        if CATEGORY_MODE:
            code, recent_categories_b = request(
                "GET", "/Channels/" + prior_channel_b + "/Items?UserId=" + account_b
                + "&FolderId=" + next(
                    x["Id"] for x in recent_items_b["Items"] if x.get("Name") == "TV ao vivo"),
                token=customer_b["AccessToken"])
            if code != 200 or not (recent_categories_b or {}).get("Items"):
                raise RuntimeError("Cannot recover B categories after restart")
            code, recent_items_b = request(
                "GET", "/Channels/" + prior_channel_b + "/Items?UserId=" + account_b
                + "&FolderId=" + next(
                    x["Id"] for x in recent_categories_b["Items"]
                    if x.get("Name") == "Portugal Verde"),
                token=customer_b["AccessToken"])
            if code != 200 or not (recent_items_b or {}).get("Items"):
                raise RuntimeError("Subscriber B category unavailable after restart")
            print("JELLYFIN_SYNTHETIC_CATEGORY_FOLDER_AFTER_RESTART=PASS")
        item_id_b = recent_items_b["Items"][0]["Id"]
        if pilot_item_id == item_id_b:
            raise RuntimeError("Channels merged media IDs after process restart")
        print("JELLYFIN_SYNTHETIC_RESTART_CHANNEL_OWNER_STABLE=PASS")
        print("JELLYFIN_SYNTHETIC_RESTART_STALE_ITEMS_DENIED=PASS")
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
        media_sources = owner_media["MediaSources"]
        def validate_live_variants(sources, label):
            # Same direct provider path, different metadata negotiation for
            # native MPEG-TS and Web HLS. Neither has a Jellyfin relay.
            if len(sources) != 2:
                raise RuntimeError("Live source variant count changed: " + label)
            by_container = {str(x.get("Container") or "").lower(): x for x in sources}
            if set(by_container) != {"mpegts", "mp4"}:
                raise RuntimeError("Live source variants missing: " + label)
            native, web = by_container["mpegts"], by_container["mp4"]
            if (str(native.get("Path") or "") != str(web.get("Path") or "")
                    or not str(native.get("Path") or "").split("?", 1)[0].endswith(".m3u8")
                    or native.get("Id") == web.get("Id")
                    or not str(web.get("Id") or "").startswith("hls:")
                    or any(str(x.get("TranscodingSubProtocol") or "").lower() != "hls"
                           or x.get("SupportsTranscoding") is not False
                           or bool(x.get("TranscodingUrl"))
                           or x.get("Protocol") != "Http"
                           for x in sources)):
                raise RuntimeError("Live source variants unsafe or inconsistent: " + label)
            return by_container

        live_by_container = validate_live_variants(media_sources, "owner")
        # Regression: Jellyfin 12.4 globally caches ChannelMediaSourceInfo by
        # ExternalId, not by user or channel. Both mock subscriptions intentionally
        # share the same live stream ID: the source must remain account-bound.
        if CATEGORY_MODE:
            code_b, other_media = request(
                "POST", "/Items/" + item_id_b + "/PlaybackInfo?UserId=" + account_b,
                {}, token=customer_b["AccessToken"], client_name="Jellyfin for Tizen")
            other_sources = other_media.get("MediaSources", []) if isinstance(other_media, dict) else []
            if code_b != 200:
                raise RuntimeError("Second subscriber missing direct-play source")
            validate_live_variants(other_sources, "subscriber-b")
            url_a = str(media_sources[0].get("Path") or "")
            url_b = str(other_sources[0].get("Path") or "")
            if (url_a == url_b
                    or "/synthetic-mega-user/synthetic-mega-password/" not in url_a
                    or "/synthetic-mega-user-b/synthetic-mega-password-b/" not in url_b):
                raise RuntimeError("MULTITENANT_SOURCE_CACHE_CROSSOVER")
            print("JELLYFIN_SYNTHETIC_SOURCE_CACHE_ACCOUNT_ISOLATION=PASS")
        if CATEGORY_MODE:
            # The same subscriber signs in from Android and Tizen concurrently.
            # Two authoritative provider origins must not share a Jellyfin
            # global MediaSourceInfo cache entry or invalidate either login.
            tv_status, same_tv = request(
                "POST", "/Users/AuthenticateByName",
                {"Username": "synthetic-mega-user",
                 "Pw": "synthetic-mega-password"},
                client_name="Jellyfin for Tizen")
            if tv_status != 200 or not isinstance(same_tv, dict) or (
                    same_tv.get("User") or {}).get("Id") != account_a:
                raise RuntimeError("Second-platform login lost subscriber identity")
            tv_token = same_tv["AccessToken"]
            status, tv_channels = request(
                "GET", "/Channels?UserId=" + account_a,
                token=tv_token, client_name="Jellyfin for Tizen")
            tv_own = [x for x in (tv_channels or {}).get("Items", [])
                      if x.get("Name", "").startswith("VivoAPK — Conteúdos ")]
            if status != 200 or len(tv_own) != 1 or tv_own[0]["Id"] == channel_id:
                raise RuntimeError("Same subscriber TV source-family not separated: " + json.dumps({"status": status, "tv_visible": len(tv_own), "channels": [x.get("Name") for x in (tv_channels or {}).get("Items", [])], "same_channel": len(tv_own)==1 and tv_own[0].get("Id")==channel_id}, sort_keys=True))
            tv_channel_id = tv_own[0]["Id"]
            def tv_items(folder=None):
                suffix = ("&FolderId=" + folder) if folder else ""
                return request("GET", "/Channels/" + tv_channel_id
                               + "/Items?UserId=" + account_a + suffix,
                               token=tv_token, client_name="Jellyfin for Tizen")
            status, tv_roots = tv_items()
            if status != 200 or not tv_roots:
                raise RuntimeError("TV source-family catalog inaccessible")
            tv_root = next((x["Id"] for x in tv_roots.get("Items", [])
                            if x.get("Name") == "TV ao vivo"), None)
            if not tv_root:
                raise RuntimeError("TV root missing on same subscription")
            status, tv_cats = tv_items(tv_root)
            tv_category = next((x["Id"] for x in (tv_cats or {}).get("Items", [])
                                if x.get("Name") == "Portugal Azul"), None)
            if status != 200 or not tv_category:
                raise RuntimeError("TV categories missing after family selection")
            status, tv_list = tv_items(tv_category)
            if status != 200 or not (tv_list or {}).get("Items"):
                raise RuntimeError("TV category items unavailable")
            tv_video_id = tv_list["Items"][0]["Id"]
            status, tv_media = request(
                "POST", "/Items/" + tv_video_id + "/PlaybackInfo?UserId=" + account_a,
                {}, token=tv_token, client_name="Jellyfin for Tizen")
            tv_sources = tv_media.get("MediaSources", []) if isinstance(tv_media, dict) else []
            if status != 200:
                raise RuntimeError("TV source-family HLS media missing")
            validate_live_variants(tv_sources, "tv-family")
            original_path = str(media_sources[0].get("Path") or "")
            tv_path = str(tv_sources[0].get("Path") or "")
            if (not original_path.startswith(mock.provider_origin + "/live/")
                    or not tv_path.startswith(mock.provider_origin_tv + "/live/")
                    or original_path == tv_path):
                raise RuntimeError("Same-account general/TV provider origins mixed")
            status, back_to_general = request(
                "POST", "/Items/" + pilot_item_id
                + "/PlaybackInfo?UserId=" + account_a,
                {}, token=customer["AccessToken"], client_name="Jellyfin Android")
            recovered_sources = ((back_to_general or {}).get("MediaSources", [])
                                 if isinstance(back_to_general, dict) else [])
            if status != 200:
                raise RuntimeError("TV login overwrote concurrent Android media origin")
            validate_live_variants(recovered_sources, "general-after-tv")
            if recovered_sources[0].get("Path") != original_path:
                raise RuntimeError("TV login overwrote concurrent Android media origin")
            print("JELLYFIN_SYNTHETIC_SAME_ACCOUNT_GENERAL_TV_ORIGIN_ISOLATED=PASS")
            # All official Jellyfin client families must choose exactly one
            # source family for the same Mega identity, without duplicates.
            for client, family, selected_item, provider_base, expected_channel in (
                ("Jellyfin Android TV", "general", pilot_item_id,
                 mock.provider_origin, channel_id),
                ("Jellyfin for iOS", "general", pilot_item_id,
                 mock.provider_origin, channel_id),
                ("Jellyfin Media Player Windows", "general", pilot_item_id,
                 mock.provider_origin, channel_id),
                ("Jellyfin WebOS LG TV", "tv", tv_video_id,
                 mock.provider_origin_tv, tv_channel_id),
                ("Jellyfin for Tizen", "tv", tv_video_id,
                 mock.provider_origin_tv, tv_channel_id),
                ("Jellyfin Android", "general", pilot_item_id,
                 mock.provider_origin, channel_id),
            ):
                login_code, login_data = request(
                    "POST", "/Users/AuthenticateByName",
                    {"Username": "synthetic-mega-user",
                     "Pw": "synthetic-mega-password"}, client_name=client)
                if (login_code != 200 or not login_data
                        or login_data["User"]["Id"] != account_a):
                    raise RuntimeError("Cross-platform Mega sign-in failed: " + family)
                fresh_token = login_data["AccessToken"]
                browse_code, visible = request(
                    "GET", "/Channels?UserId=" + account_a,
                    token=fresh_token, client_name=client)
                owned = [x for x in (visible or {}).get("Items", [])
                         if x.get("Name", "").startswith("VivoAPK — Conteúdos ")]
                if (browse_code != 200 or len(owned) != 1
                        or owned[0]["Id"] != expected_channel):
                    raise RuntimeError("Client chose wrong Jellyfin channel family: " + family)
                play_code, play_data = request(
                    "POST", "/Items/" + selected_item + "/PlaybackInfo?UserId=" + account_a,
                    {}, token=fresh_token, client_name=client)
                selected = (play_data or {}).get("MediaSources", [])
                if play_code != 200:
                    raise RuntimeError("Client chose wrong direct HLS source origin: " + family)
                validate_live_variants(selected, family)
                if not str(selected[0].get("Path") or "").startswith(provider_base + "/live/"):
                    raise RuntimeError("Client chose wrong direct HLS source origin: " + family)
            print("JELLYFIN_SYNTHETIC_ALL_SIX_PLATFORM_ORIGINS=PASS")
        # Metadata-only diagnostics; NEVER print source URLs or credentials.
        print("JELLYFIN_HLS_MEDIA_FLAGS=" + json.dumps({
            "count": len(media_sources),
            "sources": [{
                "Container": source.get("Container"),
                "Protocol": source.get("Protocol"),
                "DirectPlay": source.get("SupportsDirectPlay"),
                "DirectStream": source.get("SupportsDirectStream"),
                "Transcoding": source.get("SupportsTranscoding"),
                "HasTranscodingUrl": bool(source.get("TranscodingUrl")),
                "HlsPath": str(source.get("Path") or "").lower().split("?", 1)[0].endswith(".m3u8"),
                "HlsProtocol": str(source.get("TranscodingSubProtocol") or "").lower() == "hls",
            } for source in media_sources],
        }, sort_keys=True), flush=True)
        # One HLS URL, with the actual segment container described separately.
        if len(media_sources) != 2 or any(
            source.get("SupportsDirectPlay") is not True
            or source.get("SupportsDirectStream") is not False
            or source.get("SupportsTranscoding") is not False
            or bool(source.get("TranscodingUrl"))
            or str(source.get("TranscodingSubProtocol") or "").lower() != "hls"
            for source in media_sources
        ):
            raise RuntimeError("Jellyfin still offers media forwarding or lacks HLS metadata")
        print("JELLYFIN_DIRECT_ONLY_PLAYBACKINFO=PASS")
        # Jellyfin Android 2.7.3 routes remote HTTP direct-play sources
        # through its HLS parser even if the path ends in MPEG-TS.
        # A real Mega OTT HLS playlist must be provided instead.
        if set(live_by_container) != {"mpegts", "mp4"}:
            raise RuntimeError("Jellyfin missing native and Web HLS direct sources")
        print("JELLYFIN_MEGA_HTTP_DIRECT_HLS_SOURCE=PASS")
        # Jellyfin Web isHls() selects hls.js only when the source
        # TranscodingSubProtocol is HLS (or Container is HLS). We must keep
        # Container=mpegts for the native device profile and signal the
        # manifest separately, with zero transcoding permissions or URL.
        if any(
            str(source.get("TranscodingSubProtocol") or "").lower() != "hls"
            or source.get("SupportsTranscoding") is not False
            or bool(source.get("TranscodingUrl"))
            for source in media_sources
        ):
            raise RuntimeError("Jellyfin Web would misclassify remote HLS as MPEG-TS")
        print("JELLYFIN_WEB_HLS_MANIFEST_METADATA_DIRECT_ONLY=PASS")
        # Match the native Android client: it advertises media containers and
        # codecs, not the playlist filename extension. An empty request body
        # bypasses this negotiation and cannot detect an unusable source.
        android_profile = {
            "Name": "Jellyfin Android native H264 AAC",
            "MaxStreamingBitrate": 120000000,
            "MaxStaticBitrate": 120000000,
            "DirectPlayProfiles": [{
                "Type": "Video", "Container": "mpegts",
                "VideoCodec": "h264,hevc,mpeg2video",
                "AudioCodec": "aac,mp3,ac3,eac3,mp2",
            }, {
                "Type": "Video", "Container": "mp4",
                "VideoCodec": "h264,hevc", "AudioCodec": "aac,mp3,ac3,eac3",
            }],
            "TranscodingProfiles": [{
                "Type": "Video", "Container": "ts", "Protocol": "hls",
                "VideoCodec": "h264", "AudioCodec": "aac", "Context": "Streaming",
            }],
        }
        code, android_media = request(
            "POST", "/Items/" + pilot_item_id + "/PlaybackInfo?UserId=" + account_a,
            {"DeviceProfile": android_profile, "AutoOpenLiveStream": True,
             "MaxStreamingBitrate": 120000000}, token=customer["AccessToken"])
        sources = (android_media or {}).get("MediaSources", [])
        print("JELLYFIN_ANDROID_PROFILE_NEGOTIATION=" + json.dumps({
            "status": code,
            "error": (android_media or {}).get("ErrorCode"),
            "sources": [{k: source.get(k) for k in (
                "Container", "SupportsDirectPlay", "SupportsDirectStream",
                "SupportsTranscoding", "TranscodeReasons")}
                for source in sources],
        }, sort_keys=True), flush=True)
        if code != 200:
            raise RuntimeError("Native Android device profile not available")
        android_variants = validate_live_variants(sources, "android-device-profile")
        if android_variants["mpegts"].get("SupportsDirectPlay") is not True:
            raise RuntimeError("Native Android device profile cannot direct-play pilot")
        print("JELLYFIN_ANDROID_PROFILE_DIRECT_PLAY=PASS")
        # Verify browser HLS-only device-profile negotiation separately from
        # the native MPEG-TS profile. HLS.js requires a manifest hint, while
        # a browser may not advertise MPEG-TS direct playback at all.
        web_hls_profile = {
            "Name": "Jellyfin Web HLS-only metadata diagnostic",
            "MaxStreamingBitrate": 120000000,
            "MaxStaticBitrate": 120000000,
            "DirectPlayProfiles": [{
                "Type": "Video", "Container": "hls",
                "VideoCodec": "h264", "AudioCodec": "aac",
            }],
            "TranscodingProfiles": [{
                "Type": "Video", "Container": "ts", "Protocol": "hls",
                "VideoCodec": "h264", "AudioCodec": "aac", "Context": "Streaming",
            }],
        }
        web_code, web_media = request(
            "POST", "/Items/" + pilot_item_id + "/PlaybackInfo?UserId=" + account_a,
            {"DeviceProfile": web_hls_profile, "AutoOpenLiveStream": True,
             "MaxStreamingBitrate": 120000000}, token=customer["AccessToken"])
        web_sources = (web_media or {}).get("MediaSources", [])
        print("JELLYFIN_WEB_HLS_ONLY_PROFILE_DIAGNOSTIC=" +
              json.dumps({
                  "http_status": web_code,
                  "error": (web_media or {}).get("ErrorCode"),
                  "sources": [{
                      "container": source.get("Container"),
                      "direct_play": source.get("SupportsDirectPlay"),
                      "hls_protocol": str(source.get("TranscodingSubProtocol") or "").lower() == "hls",
                      "relay_url": bool(source.get("TranscodingUrl")),
                      "transcoding": source.get("SupportsTranscoding"),
                  } for source in web_sources],
              }, sort_keys=True), flush=True)
        if web_code != 200:
            raise RuntimeError("Browser HLS negotiation failed")
        web_variants = validate_live_variants(web_sources, "web-hls-device-profile")
        # Jellyfin 12.4 refuses DirectPlay of Container=hls by design.
        # Its HLS.js browser also advertises MP4 decoder capabilities,
        # allowing the same actual HLS URL to negotiate without transcoding.
        if any(src.get("SupportsDirectPlay") is True for src in web_sources):
            raise RuntimeError("HLS-only profile unexpectedly bypassed Jellyfin manifest guard")
        print("JELLYFIN_WEB_HLS_ONLY_SERVER_GUARD=EXPECTED")
        web_compat_profile = {
            **web_hls_profile,
            "Name": "Jellyfin Web HLS.js with MP4 decoder capability",
            "DirectPlayProfiles": web_hls_profile["DirectPlayProfiles"] + [{
                "Type": "Video", "Container": "mp4",
                "VideoCodec": "h264", "AudioCodec": "aac",
            }],
        }
        web_compat_code, web_compat_media = request(
            "POST", "/Items/" + pilot_item_id + "/PlaybackInfo?UserId=" + account_a,
            {"DeviceProfile": web_compat_profile, "AutoOpenLiveStream": True,
             "MaxStreamingBitrate": 120000000}, token=customer["AccessToken"])
        web_compat_sources = (web_compat_media or {}).get("MediaSources", [])
        if web_compat_code != 200:
            raise RuntimeError("Jellyfin Web-compatible profile negotiation failed")
        web_compat_variants = validate_live_variants(web_compat_sources, "web-mp4-hlsjs")
        if (web_compat_variants["mp4"].get("SupportsDirectPlay") is not True
                or any(src.get("SupportsTranscoding") is True or src.get("TranscodingUrl")
                       for src in web_compat_sources)):
            raise RuntimeError("Jellyfin Web HLS.js cannot negotiate remote provider direct play")
        print("JELLYFIN_WEB_HLS_JS_MP4_DIRECT_PLAY=PASS")
        # Reproduce the Jellyfin Android 2.7.3 automatic-quality behavior.
        # The official app can send a 1.5 Mbps ceiling even when the user
        # selected "Automatic". With all server-side forwarding prohibited,
        # the only acceptable outcome is remote DirectPlay or an explicit
        # unsupported result; never hide a transcode or video proxy.
        low_profile = {**android_profile,
                       "MaxStreamingBitrate": 1500000,
                       "MaxStaticBitrate": 1500000}
        low_code, low_media = request(
            "POST", "/Items/" + pilot_item_id + "/PlaybackInfo?UserId=" + account_a,
            {"DeviceProfile": low_profile, "AutoOpenLiveStream": True,
             "MaxStreamingBitrate": 1500000}, token=customer["AccessToken"])
        low_sources = (low_media or {}).get("MediaSources", [])
        if low_code != 200 or not isinstance(low_media, dict):
            raise RuntimeError("Automatic quality source negotiation unavailable")
        # Report the actual negotiated methods before applying safety gates.
        # A TranscodingSubProtocol value alone can be present even when
        # SupportsTranscoding is false and no transcoding URL was allocated.
        print("JELLYFIN_ANDROID_AUTOMATIC_BITRATE_METHODS=" +
              json.dumps([{
                  "direct_play": s.get("SupportsDirectPlay"),
                  "direct_stream": s.get("SupportsDirectStream"),
                  "transcoding": s.get("SupportsTranscoding"),
                  "transcoding_url_present": bool(s.get("TranscodingUrl")),
                  "reasons": s.get("TranscodeReasons"),
              } for s in low_sources], sort_keys=True), flush=True)
        if any(s.get("SupportsTranscoding") is True or s.get("TranscodingUrl")
               for s in low_sources):
            raise RuntimeError("Low-bitrate negotiation exposed Jellyfin video forwarding")
        print("JELLYFIN_ANDROID_AUTOMATIC_BITRATE_DIAGNOSTIC=" +
              json.dumps({
                  "http_status": low_code,
                  "max_streaming_bitrate": 1500000,
                  "source_count": len(low_sources),
                  "supports_direct_play": [
                      bool(s.get("SupportsDirectPlay")) for s in low_sources],
                  "transcoding_disabled": all(
                      s.get("SupportsTranscoding") is False for s in low_sources),
                  "media_traverses_jellyfin": False,
                  "real_provider_used": False,
              }, sort_keys=True), flush=True)
        # Decode the exact URL returned by real Jellyfin. Requests go straight
        # to the separate synthetic provider, including its HLS redirect.
        source_url = sources[0].get("Path", "")
        if not source_url.startswith(mock.provider_origin + "/live/"):
            raise RuntimeError("Playback URL no longer points directly to provider")
        decoded = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", source_url,
            "-t", "2", "-map", "0:v:0", "-map", "0:a:0",
            "-f", "framemd5", "pipe:1",
        ], capture_output=True, text=True, timeout=30)
        frames = [line for line in decoded.stdout.splitlines()
                  if line and not line.startswith("#")]
        video_frames = sum(line.startswith("0,") for line in frames)
        audio_frames = sum(line.startswith("1,") for line in frames)
        if (decoded.returncode or video_frames < 40 or audio_frames < 20
                or provider.requests < 3):
            raise RuntimeError("Direct HLS fixture failed: " + json.dumps({"decoder_exit": decoded.returncode, "video_frames": video_frames, "audio_frames": audio_frames, "provider_requests": provider.requests}, sort_keys=True))
        print("JELLYFIN_RETURNED_HLS_DECODE=" + json.dumps({
            "video_frames": video_frames, "audio_frames": audio_frames,
            "provider_requests": provider.requests, "jellyfin_media_requests": 0,
            "real_provider": False,
        }, sort_keys=True))
        print("JELLYFIN_RETURNED_HLS_VIDEO_AUDIO_DIRECT=PASS")
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
            "Username": "synthetic-mega-user-b", "Pw": "synthetic-mega-password-b"},
            client_name="Jellyfin for Tizen")
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
        provider.shutdown()
        provider.server_close()
        log.close()
        if MOCK.exists():
            MOCK.unlink()
        if monitor_key_path.exists():
            monitor_key_path.unlink()

if __name__ == "__main__":
    main()
