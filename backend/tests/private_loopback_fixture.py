from contextlib import contextmanager
"""Disposable backend-over-UDS/actual C# loopback/FFmpeg integration gate.

Generate two lawful synthetic HLS assets, optionally from raw MPEG-TS over a
local pipe; no real provider request, credentials,
public listener, VPS operation, or Jellyfin installation. The test compiles
the same adapter source as the plugin but does not register it in Jellyfin.
"""
import http.server
import json
from collections import Counter
import os
import pathlib
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import uvicorn
from pydantic import SecretStr
from sqlalchemy import event, select
from sqlalchemy.engine import make_url

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("SESSION_SIGNING_KEY", "fixture-signing-key-for-loopback-only")

from app.config import Settings
from app.db import Base
from app.main import create_app
from app.models import AuthRateWindow, SubscriptionMapping
from app.schemas import EntitlementState
from app.security import issue_session_token
from app.services.private_hls import PrivateHlsSession
from app.services.private_file import PrivateFileSession
from app.services.media_body import open_media_body
from test_catalog_privacy import FakeCatalog, FakeMega, FakeXtream
from test_media_origin import MediaCatalogue, public_dns
from test_private_file_api import RangePool
from test_private_hls import ORIGIN, Origin, response


def generate(ffmpeg, folder, suffix, frequency):
    folder.mkdir()
    video = "testsrc2=size=160x90:rate=25" if suffix == "a" else "smptebars=size=160x90:rate=25"
    # Only generated test patterns and tones. Exercise the gap between a raw
    # MPEG-TS input and the already certified private HLS/socket adapter.
    # The test has no network input, provider credentials or fallback behavior.
    if os.environ.get("VIVOAPK_LOOPBACK_TS_INPUT") == "1":
        raw = subprocess.run([
            ffmpeg, "-hide_banner", "-nostdin", "-v", "error",
            "-f", "lavfi", "-i", video,
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000",
            "-t", "6", "-c:v", "mpeg2video", "-threads:v", "1",
            "-b:v", "380k", "-g", "25", "-c:a", "mp2", "-b:a", "128k",
            "-muxrate", "600k", "-f", "mpegts", "pipe:1",
        ], check=True, capture_output=True, timeout=30).stdout
        assert 188 * 3 <= len(raw) <= 2 * 1024 * 1024 and len(raw) % 188 == 0
        assert all(raw[i] == 0x47 for i in range(0, len(raw), 188))
        inputs = ["-protocol_whitelist", "pipe", "-f", "mpegts", "-i", "pipe:0",
                  "-map", "0:v:0", "-map", "0:a:0"]
        input_args = {"input": raw}
        print("VIVOAPK_PRIVATE_LOOPBACK_SYNTHETIC_TS_INPUT=PASS", flush=True)
    else:
        inputs = ["-f", "lavfi", "-i", video,
                  "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000"]
        input_args = {}
    subprocess.run([
        ffmpeg, "-hide_banner", "-nostdin", "-v", "error", *inputs,
        "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-threads:v", "1",
        "-pix_fmt", "yuv420p", "-g", "50", "-keyint_min", "50", "-sc_threshold", "0",
        "-c:a", "aac", "-threads:a", "1", "-f", "hls", "-hls_time", "2",
        "-hls_list_size", "0", "-hls_segment_filename", str(folder / "s%03d.ts"),
        str(folder / "index.m3u8"),
    ], check=True, capture_output=True, timeout=30, **input_args)
    playlist = (folder / "index.m3u8").read_text()
    segments = {p.name: p.read_bytes() for p in folder.glob("*.ts")}
    assert len(segments) == 3 and "#EXT-X-ENDLIST" in playlist
    for name in segments:
        playlist = playlist.replace("\n" + name + "\n", f"\n/muxed/{suffix}/{name}\n")
    return playlist.encode(), segments


class ConcurrentOrigin(Origin):
    """Prove overlap at the first actual segment request from each fixture.
    The barrier is outside the DB transaction and is never used in production.
    It makes accidental serialization of the two FFmpeg inputs a test failure.
    """
    def __init__(self, routes):
        super().__init__(routes)
        self._lock = threading.Lock()
        self._first = set()
        self._barrier = None
        self.overlaps = 0

    def begin_round(self):
        with self._lock:
            self._first.clear()
            self._barrier = threading.Barrier(2, timeout=12)
            self.overlaps = 0

    def segment(self, suffix, body):
        with self._lock:
            first = suffix not in self._first
            if first:
                self._first.add(suffix)
            barrier = self._barrier
        if first:
            assert barrier is not None
            barrier.wait()
            with self._lock:
                self.overlaps += 1
        return response(body, b"video/mp2t")


def generate_file(ffmpeg, path, suffix, frequency, *, timeline_marker=False):
    # Real MP4 with moov at the end, deliberately >4 MiB. Use deterministic
    # generated video and audio only. No download or provider content.
    video = ("testsrc2=size=640x360:rate=25" if suffix == "a" else
             "testsrc2=size=640x360:rate=25,negate")
    if timeline_marker:
        # Four distinct, stable luminance codes in the center of the moving
        # image. The two owners have opposite timelines. A resize, H264
        # re-encode or limited/full range conversion must not erase the code.
        colors = ("black", "0x555555", "0xaaaaaa", "white")
        if suffix == "b":
            colors = tuple(reversed(colors))
        for index, color in enumerate(colors):
            video += (f",drawbox=x=160:y=90:w=320:h=180:color={color}:t=fill:"
                      f"enable='gte(t,{index*3})*lt(t,{(index+1)*3})'")
    subprocess.run([
        ffmpeg, "-hide_banner", "-nostdin", "-v", "error",
        "-f", "lavfi", "-i", video,
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000",
        "-t", "12", "-c:v", "libx264", "-preset", "ultrafast", "-threads:v", "1",
        "-pix_fmt", "yuv420p", "-crf", "0", "-g", "50", "-keyint_min", "50",
        "-sc_threshold", "0", "-c:a", "aac", "-threads:a", "1", str(path),
    ], check=True, capture_output=True, timeout=30)
    # An ordinary MP4 'free' box at the end makes the multi-window byte
    # contract deterministic without forcing an expensive encode.
    import struct
    minimum = 16 * 1024 * 1024 + 8192
    size = path.stat().st_size
    if size < minimum:
        padding = minimum - size
        with path.open("ab") as f:
            f.write(struct.pack(">I4s", padding, b"free"))
            f.write(bytes(padding - 8))
    assert path.stat().st_size > 4 * 1024 * 1024 + 1024
    return path.read_bytes()


def add_teletext_track(raw):
    """Add a typed ancillary PID to a generated two-stream MPEG-TS fixture."""
    assert len(raw) % 188 == 0
    pid = 0x102
    packets = [raw[i:i + 188] for i in range(0, len(raw), 188)]
    assert all(packet[0] == 0x47 for packet in packets)
    assert all(((packet[1] & 31) << 8 | packet[2]) != pid for packet in packets)

    def crc(data):
        value = 0xFFFFFFFF
        for byte in data:
            value ^= byte << 24
            for _ in range(8):
                value = ((value << 1) ^ (0x04C11DB7 if value & 0x80000000 else 0)) & 0xFFFFFFFF
        return value

    pts = 126000
    timestamp = bytes((0x21 | ((pts >> 29) & 14), (pts >> 22) & 255,
                       ((pts >> 14) & 254) | 1, (pts >> 7) & 255,
                       ((pts << 1) & 254) | 1))
    payload = b'\x10\x03\x2c\x07\xe4' + bytes([0x80]) * 42
    pes_body = b'\x84\x80\x05' + timestamp + payload
    pes = b'\x00\x00\x01\xbd' + len(pes_body).to_bytes(2, 'big') + pes_body
    adaptation = 183 - len(pes)
    caption = bytes((0x47, 0x40 | (pid >> 8), pid & 255, 0x30, adaptation, 0))
    caption += b'\xff' * (adaptation - 1) + pes
    assert len(caption) == 188

    # Private PES + teletext descriptor (language, page type/magazine, page).
    extra = bytes((0x06, 0xE0 | (pid >> 8), pid & 255, 0xF0, 7, 0x56, 5))
    extra += b'por\x11\x00'
    output = []
    changed = 0
    for packet in packets:
        control = (packet[3] >> 4) & 3
        offset = 4 + (1 + packet[4] if control == 3 else 0)
        if control in (1, 3) and packet[1] & 0x40 and offset < 183:
            start = offset + 1 + packet[offset]
            if start + 3 < 188 and packet[start] == 0x02:
                length = ((packet[start + 1] & 15) << 8) | packet[start + 2]
                section = packet[start:start + 3 + length]
                assert len(section) == 3 + length and crc(section) == 0
                updated = bytearray(section[:-4] + extra)
                updated[1] = (updated[1] & 0xF0) | ((length + len(extra)) >> 8)
                updated[2] = (length + len(extra)) & 255
                updated += crc(updated).to_bytes(4, 'big')
                assert crc(updated) == 0 and start + len(updated) <= 188
                packet = packet[:start] + updated + b'\xff' * (188 - start - len(updated))
                changed += 1
        output.append(packet)
        if changed == 1 and caption is not None:
            output.append(caption)
            caption = None
    assert changed > 0 and caption is None
    return b''.join(output)



def verify_native_ts_probe(ffmpeg, assembly, base):
    """Local generated raw MPEG-TS, no provider or backend media request."""
    ffprobe = shutil.which("ffprobe")
    assert ffprobe is not None
    raw = subprocess.run([
        ffmpeg, "-hide_banner", "-nostdin", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", "12", "-c:v", "mpeg2video", "-threads:v", "1",
        "-b:v", "380k", "-g", "25", "-c:a", "mp2", "-b:a", "128k",
        "-muxrate", "600k", "-f", "mpegts", "pipe:1",
    ], check=True, capture_output=True, timeout=30).stdout
    raw = add_teletext_track(raw)
    assert 188 * 3 <= len(raw) <= 2 * 1024 * 1024 and len(raw) % 188 == 0
    route = "/vivoapk/" + secrets.token_hex(24) + "/stream.ts"
    stopped = threading.Event()
    observations = Counter()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            observations["requests"] += 1
            if self.path != route:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            # No Content-Length; keep the test transport open until the probe
            # has enough metadata or the harness tears down the fixture.
            self.connection.settimeout(5)
            try:
                while not stopped.is_set():
                    for offset in range(0, len(raw), 188 * 64):
                        if stopped.is_set():
                            return
                        self.wfile.write(raw[offset:offset + 188 * 64])
                        time.sleep(.005)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        config = base / "native-ts-probe.json"
        config.write_text(json.dumps({
            "ffprobe": ffprobe,
            "ts_probe_uri": f"http://127.0.0.1:{server.server_port}{route}",
        }))
        result = subprocess.run(["dotnet", str(assembly), str(config), "ts-probe"],
                                capture_output=True, text=True, timeout=35)
        for line in (result.stdout + "\n" + result.stderr).splitlines():
            if line.startswith("VIVOAPK_PRIVATE_LOOPBACK_"):
                print(line, flush=True)
        assert result.returncode == 0, "Native MPEG-TS metadata probe failed"
        assert observations["requests"] == 1, "Unadmitted path was requested"
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive(), "Synthetic TS fixture did not stop"


def main():
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    assembly = pathlib.Path(os.environ["VIVOAPK_LOOPBACK_HARNESS"]).resolve(strict=True)
    # Fail closed instead of quietly exercising SQLite's single shared
    # connection. This database exists only in the disposable CI job.
    database_url = os.environ["VIVOAPK_LOOPBACK_DATABASE_URL"]
    db_url = make_url(database_url)
    assert db_url.drivername == "postgresql+psycopg"
    assert db_url.host == "127.0.0.1" and db_url.port == 5432
    assert db_url.database == "vivoapk_loopback_ci" and db_url.username == "vivoapk_fixture"
    with tempfile.TemporaryDirectory(prefix="vivoapk-loopback-") as temp:
        base = pathlib.Path(temp)
        verify_native_ts_probe(ffmpeg, assembly, base)
        routes, accounts, tokens = {}, {}, {}
        upstream = ConcurrentOrigin(routes)
        settings = Settings(
            app_env="test", database_url=database_url,
            session_signing_key=secrets.token_urlsafe(32),
            private_hls_enabled=True, private_media_key=secrets.token_urlsafe(32),
            session_ttl_seconds=30,
        )
        app = create_app(settings)
        dbfactory = app.state.session_factory
        engine = dbfactory.kw["bind"]
        assert engine.dialect.name == "postgresql"
        Base.metadata.create_all(engine)
        # Count only fixed exception/status classes; no SQL, parameters, URLs,
        # subscriber identities, headers, request bodies or credentials.
        observations = Counter()
        observation_lock = threading.Lock()

        @event.listens_for(engine, "handle_error")
        def record_db_failure(context):
            with observation_lock:
                observations["db_errors"] += 1
                name = type(context.original_exception).__name__
                if name in {"UniqueViolation", "DeadlockDetected", "SerializationFailure",
                            "OperationalError", "IntegrityError", "InterfaceError"}:
                    observations["db_" + name] += 1
                else:
                    observations["db_other"] += 1

        @app.middleware("http")
        async def observe_response(request, call_next):
            route = request.url.path.rsplit("/", 1)[-1]
            if route not in {"open", "playlist", "segment", "close", "renew", "read"}:
                route = "other"
            try:
                result = await call_next(request)
            except Exception:
                with observation_lock:
                    observations[route + "_500"] += 1
                raise
            with observation_lock:
                observations[route + "_" + str(result.status_code)] += 1
            return result

        print("VIVOAPK_PRIVATE_LOOPBACK_DATABASE=POSTGRESQL", flush=True)
        future = datetime.now(UTC) + timedelta(days=3)
        with dbfactory() as db:
            for i, suffix in enumerate(("a", "b"), 1):
                playlist, segments = generate(ffmpeg, base / suffix, suffix, 330 + i * 220)
                user, password = f"sub-{suffix}", f"fixture-password-{suffix}"
                root = ORIGIN + f"/live/{user}/{password}/101.m3u8"
                routes[root] = lambda p=playlist: response(p)
                for name, data in segments.items():
                    routes[ORIGIN + f"/muxed/{suffix}/{name}"] = (
                        lambda b=data, s=suffix: upstream.segment(s, b)
                    )
                mapping = SubscriptionMapping(
                    mega_subscription_id=900 + i, username=user, dns_link=ORIGIN,
                    dns_link_samsung_lg=ORIGIN, expiring_at=future,
                    last_synced_at=datetime.now(UTC),
                )
                db.add(mapping)
                db.flush()
                tokens[suffix] = mapping.id
                accounts[900 + i] = SimpleNamespace(
                    id=900 + i, username=user, password=SecretStr(password),
                    expiring_at=future, dns_link=ORIGIN, dns_link_for_samsung_lg=ORIGIN,
                )
            db.commit()
        app.state.mega_client_factory = lambda: FakeMega(accounts)
        app.state.xtream_client_factory = FakeXtream
        app.state.catalog_client_factory = FakeCatalog
        app.state.private_hls_factory = lambda source: PrivateHlsSession(
            source, entitlement=lambda: EntitlementState.UNAVAILABLE,
            resolver=public_dns, pool_factory=upstream.factory,
        )
        socket_path = str(base / "backend.sock")
        server = uvicorn.Server(uvicorn.Config(app, uds=socket_path,
                                               log_config=None, access_log=False,
                                               workers=1))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(.05)
            assert server.started and pathlib.Path(socket_path).is_socket()
            config = base / "fixture.json"
            data = {"socket": socket_path, "key": settings.private_media_key.get_secret_value(),
                    "ffmpeg": ffmpeg}
            for suffix, ident in tokens.items():
                data[suffix], _ = issue_session_token(ident, settings)
            fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as out:
                json.dump(data, out)
            for trial in range(1, 4):
                for suffix, ident in tokens.items():
                    data[suffix], _ = issue_session_token(ident, settings)
                config.write_text(json.dumps(data))
                upstream.begin_round()
                with observation_lock:
                    observations.clear()
                result = subprocess.run(["dotnet", str(assembly), str(config)],
                                        capture_output=True, text=True, timeout=100)
                for line in (result.stdout + "\n" + result.stderr).splitlines():
                    if line.startswith("VIVOAPK_PRIVATE_LOOPBACK_"):
                        print(line, flush=True)
                with observation_lock:
                    observed = dict(observations)
                print("VIVOAPK_PRIVATE_LOOPBACK_ROUND_OBSERVATION=" + json.dumps({
                    "round": trial, "overlapping_origins": upstream.overlaps,
                    "active_backend_sessions": len(app.state.private_hls_registry._entries),
                    "statuses": observed,
                }, sort_keys=True), flush=True)
                assert result.returncode == 0, "Private loopback harness failed"
                assert upstream.overlaps == 2, "No proved overlap between origin requests"
                assert not any(k.endswith(("_429", "_500", "_503")) or k.startswith("db_")
                               for k in observed), "Backend/DB admission failure"
                assert not app.state.private_hls_registry._entries, "Unreleased backend session"
                assert all(pool.closed and pool.response.closed for pool in upstream.pools)
                assert all(url in routes for url in upstream.calls)
                print(f"VIVOAPK_PRIVATE_LOOPBACK_ROUND={trial}:PASS", flush=True)
            with dbfactory() as db:
                buckets = db.scalars(select(AuthRateWindow)).all()
                global_bucket = next(b for b in buckets if b.bucket_key == "c:global")
                individual = [b for b in buckets if b.bucket_key.startswith("c:u:")]
                assert len(individual) == 2
                assert global_bucket.attempts == sum(b.attempts for b in individual)
                assert all(b.attempts > 0 for b in individual)
            print("VIVOAPK_PRIVATE_LOOPBACK_POSTGRES_ADMISSION_ACCOUNTING=PASS", flush=True)
            # Separate lifecycle round: one original account token and one
            # scoped grant expire in real wall-clock time while A stays active.
            # Keep the same loopback lease, then decode A again; B is reaped
            # without an HTTP request to its session.
            with dbfactory() as db:
                db.query(AuthRateWindow).delete()
                db.commit()
            for suffix, ident in tokens.items():
                data[suffix], _ = issue_session_token(ident, settings)
            config.write_text(json.dumps(data))
            upstream.begin_round()
            with observation_lock:
                observations.clear()
            result = subprocess.run(["dotnet", str(assembly), str(config), "lifecycle"],
                                    capture_output=True, text=True, timeout=110)
            for line in (result.stdout + "\n" + result.stderr).splitlines():
                if line.startswith("VIVOAPK_PRIVATE_LOOPBACK_"):
                    print(line, flush=True)
            with observation_lock:
                lifecycle = dict(observations)
            print("VIVOAPK_PRIVATE_LOOPBACK_LIFECYCLE_OBSERVATION=" + json.dumps({
                "statuses": lifecycle, "active_backend_sessions": len(app.state.private_hls_registry._entries),
                "overlapping_origins": upstream.overlaps,
            }, sort_keys=True), flush=True)
            assert result.returncode == 0, "Private loopback lifecycle failed"
            assert lifecycle.get("renew_200", 0) >= 2
            assert upstream.overlaps == 2
            assert not app.state.private_hls_registry._entries
            assert all(pool.closed and pool.response.closed for pool in upstream.pools)
            print("VIVOAPK_PRIVATE_LOOPBACK_REAL_TTL_AND_ABANDONMENT=PASS", flush=True)
            # Same running backend/socket/registry and exact C# adapter:
            # actual random-access finite media after all HLS regression gates.
            with dbfactory() as db:
                db.query(AuthRateWindow).delete()
                db.commit()
            movie, episode = base / "movie.mp4", base / "episode.mp4"
            finite = RangePool({
                ORIGIN + "/movie/sub-a/fixture-password-a/501.mp4":
                    generate_file(ffmpeg, movie, "a", 550),
                ORIGIN + "/series/sub-b/fixture-password-b/90.mp4":
                    generate_file(ffmpeg, episode, "b", 770),
            })
            app.state.catalog_client_factory = MediaCatalogue
            delayed_prefixes = set()
            @contextmanager
            def finite_body(src, **kwargs):
                # A slow first window must keep the next read small; later
                # fast reads must recover the existing 4 MiB ceiling.
                identity = src._request_url()
                if (kwargs.get("range_header") == "bytes=0-524287"
                        and identity not in delayed_prefixes):
                    delayed_prefixes.add(identity)
                    time.sleep(1.1)
                with open_media_body(src, resolver=public_dns,
                                     pool_factory=finite.factory, **kwargs) as body:
                    yield body
            app.state.private_file_factory = lambda source: PrivateFileSession(
                source, body_reader=finite_body)
            for suffix, ident in tokens.items():
                data[suffix], _ = issue_session_token(ident, settings)
            data["movie_reference"], data["episode_reference"] = str(movie), str(episode)
            config.write_text(json.dumps(data))
            with observation_lock:
                observations.clear()
            result = subprocess.run(["dotnet", str(assembly), str(config), "file"],
                                    capture_output=True, text=True, timeout=110)
            for line in (result.stdout + "\n" + result.stderr).splitlines():
                if line.startswith("VIVOAPK_PRIVATE_LOOPBACK_"):
                    print(line, flush=True)
            with observation_lock:
                files_observed = dict(observations)
            print("VIVOAPK_PRIVATE_LOOPBACK_FILE_OBSERVATION=" + json.dumps({
                "statuses": files_observed,
                "active_backend_sessions": len(app.state.private_hls_registry._entries),
                "origin_range_reads": len(finite.calls),
                "max_range_bytes": max((end - start + 1 for _, start, end, _ in finite.calls), default=0),
                "nonzero_range_reads": sum(start > 0 for _, start, _, _ in finite.calls),
            }, sort_keys=True), flush=True)
            assert result.returncode == 0, "Finite media harness failed"
            assert files_observed.get("open_200") == 2 and files_observed.get("close_204") == 2
            assert files_observed.get("read_206", 0) >= 12
            assert not any(k.endswith(("_429", "_500", "_503")) or k.startswith("db_")
                           for k in files_observed), "Finite backend/DB admission failure"
            assert not app.state.private_hls_registry._entries
            assert all(pool.closed and pool.response.closed for pool in finite.pools)
            assert all(url in finite.files and 0 <= start <= end
                       and end - start + 1 <= 4 * 1024 * 1024
                       for url, start, end, _ in finite.calls)
            assert any(start >= 4 * 1024 * 1024 for _, start, _, _ in finite.calls)
            assert len(delayed_prefixes) == 2
            for identity in finite.files:
                reads = [(start, end-start+1) for url, start, end, _ in finite.calls
                         if url == identity and end-start+1 > 1]
                assert reads[0] == (0, 512 * 1024)
                assert any(size == 4 * 1024 * 1024 for _, size in reads)
                assert any(start == 512 * 1024 and size == 512 * 1024
                           for start, size in reads)
            print("VIVOAPK_PRIVATE_LOOPBACK_FINITE_SLOW_PREFIX_AND_FAST_BULK=PASS", flush=True)
            print("VIVOAPK_PRIVATE_LOOPBACK_FILE_BACKEND_UDS_AND_CLEANUP=PASS", flush=True)
            print("VIVOAPK_PRIVATE_LOOPBACK_BACKEND_UDS_AND_CLEANUP=PASS", flush=True)
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            assert not worker.is_alive(), "Disposable backend did not stop"
            engine.dispose()


if __name__ == "__main__":
    main()
