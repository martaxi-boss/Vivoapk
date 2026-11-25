"""Disposable backend-over-UDS/actual C# loopback/FFmpeg integration gate.

Generate two lawful synthetic HLS assets; no real provider request, credentials,
public listener, VPS operation, or Jellyfin installation. The test compiles
the same adapter source as the plugin but does not register it in Jellyfin.
"""
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
from test_catalog_privacy import FakeCatalog, FakeMega, FakeXtream
from test_media_origin import public_dns
from test_private_hls import ORIGIN, Origin, response


def generate(ffmpeg, folder, suffix, frequency):
    folder.mkdir()
    video = "testsrc2=size=160x90:rate=25" if suffix == "a" else "smptebars=size=160x90:rate=25"
    subprocess.run([
        ffmpeg, "-hide_banner", "-nostdin", "-v", "error",
        "-f", "lavfi", "-i", video,
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000",
        "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-threads:v", "1",
        "-pix_fmt", "yuv420p", "-g", "50", "-keyint_min", "50", "-sc_threshold", "0",
        "-c:a", "aac", "-threads:a", "1", "-f", "hls", "-hls_time", "2",
        "-hls_list_size", "0", "-hls_segment_filename", str(folder / "s%03d.ts"),
        str(folder / "index.m3u8"),
    ], check=True, capture_output=True, timeout=30)
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
        routes, accounts, tokens = {}, {}, {}
        upstream = ConcurrentOrigin(routes)
        settings = Settings(
            app_env="test", database_url=database_url,
            session_signing_key=secrets.token_urlsafe(32),
            private_hls_enabled=True, private_media_key=secrets.token_urlsafe(32),
            session_ttl_seconds=300,
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
            if route not in {"open", "playlist", "segment", "close"}:
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
            # Both accounts must still contribute to the shared admission
            # counter. A test that bypasses the real limiter is not acceptable.
            with dbfactory() as db:
                buckets = db.scalars(select(AuthRateWindow)).all()
                global_bucket = next(b for b in buckets if b.bucket_key == "c:global")
                individual = [b for b in buckets if b.bucket_key.startswith("c:u:")]
                assert len(individual) == 2
                assert global_bucket.attempts == sum(b.attempts for b in individual)
                assert all(b.attempts > 0 for b in individual)
            print("VIVOAPK_PRIVATE_LOOPBACK_POSTGRES_ADMISSION_ACCOUNTING=PASS", flush=True)
            print("VIVOAPK_PRIVATE_LOOPBACK_BACKEND_UDS_AND_CLEANUP=PASS", flush=True)
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            assert not worker.is_alive(), "Disposable backend did not stop"
            engine.dispose()


if __name__ == "__main__":
    main()
