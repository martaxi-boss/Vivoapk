"""Disposable backend-over-UDS/actual C# loopback/FFmpeg integration gate.

Generate two lawful synthetic HLS assets; no real provider request, credentials,
public listener, VPS operation, or Jellyfin installation. The test compiles
the same adapter source as the plugin but does not register it in Jellyfin.
"""
import json
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

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("SESSION_SIGNING_KEY", "fixture-signing-key-for-loopback-only")

from app.config import Settings
from app.db import Base
from app.main import create_app
from app.models import SubscriptionMapping
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


def main():
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    assembly = pathlib.Path(os.environ["VIVOAPK_LOOPBACK_HARNESS"]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="vivoapk-loopback-") as temp:
        base = pathlib.Path(temp)
        routes, accounts, tokens = {}, {}, {}
        settings = Settings(
            app_env="test", database_url="sqlite+pysqlite:///:memory:",
            session_signing_key=secrets.token_urlsafe(32),
            private_hls_enabled=True, private_media_key=secrets.token_urlsafe(32),
            session_ttl_seconds=300,
        )
        app = create_app(settings)
        dbfactory = app.state.session_factory
        Base.metadata.create_all(dbfactory.kw["bind"])
        future = datetime.now(UTC) + timedelta(days=3)
        with dbfactory() as db:
            for i, suffix in enumerate(("a", "b"), 1):
                playlist, segments = generate(ffmpeg, base / suffix, suffix, 330 + i * 220)
                user, password = f"sub-{suffix}", f"fixture-password-{suffix}"
                root = ORIGIN + f"/live/{user}/{password}/101.m3u8"
                routes[root] = lambda p=playlist: response(p)
                for name, data in segments.items():
                    routes[ORIGIN + f"/muxed/{suffix}/{name}"] = (
                        lambda b=data: response(b, b"video/mp2t")
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
        upstream = Origin(routes)
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
            result = subprocess.run(["dotnet", str(assembly), str(config)],
                                    capture_output=True, text=True, timeout=100)
            for line in (result.stdout + "\n" + result.stderr).splitlines():
                if line.startswith("VIVOAPK_PRIVATE_LOOPBACK_"):
                    print(line, flush=True)
            assert result.returncode == 0, "Private loopback harness failed"
            assert not app.state.private_hls_registry._entries, "Unreleased backend session"
            assert all(pool.closed and pool.response.closed for pool in upstream.pools)
            assert all(url in routes for url in upstream.calls)
            print("VIVOAPK_PRIVATE_LOOPBACK_BACKEND_UDS_AND_CLEANUP=PASS", flush=True)
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            assert not worker.is_alive(), "Disposable backend did not stop"


if __name__ == "__main__":
    main()
