"""Disposable real private backend, with synthetic catalogue and generated HLS."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import uvicorn
from pydantic import SecretStr
from sqlalchemy.engine import make_url

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("SESSION_SIGNING_KEY", "fixture-signing-key-native-private-test")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/tests"))
from app.config import Settings
from app.db import Base
from app.main import create_app
from app.models import SubscriptionMapping
from app.schemas import CatalogKind, EntitlementState
from app.security import issue_session_token
from app.services.private_hls import PrivateHlsSession
from app.services.private_file import PrivateFileSession
from app.services.media_body import open_media_body
from test_catalog_privacy import FakeCatalog, FakeMega, FakeXtream
from test_media_origin import public_dns
from test_private_hls import ORIGIN, Origin, response
from test_private_file_api import RangePool
from private_loopback_fixture import generate_file

SOCKET = Path("/srv/projects/Vivoapk/run/private-hls-fixture.sock")
KEY = SOCKET.with_suffix(".key")


class Catalogue(FakeCatalog):
    def list_category_items(self, **kwargs):
        rows = super().list_category_items(**kwargs)
        kind = kwargs["kind"]
        rows[0]["id"] = "42" if kind is CatalogKind.LIVE else "501" if kind is CatalogKind.VOD else "601"
        if kind is CatalogKind.VOD:
            rows[0]["container_extension"] = "mp4"
        return rows

    def list_categories(self, **kwargs):
        kind = kwargs["kind"]
        if kind is CatalogKind.LIVE:
            return super().list_categories(**kwargs)
        a = kwargs["username"] == "sub-a"
        return [{"id": ("110" if a else "120") if kind is CatalogKind.VOD
                 else ("210" if a else "220"), "name": "Synthetic category"}]

    def series_detail(self, **kwargs):
        return {"series_id": kwargs["series_id"], "title": "Synthetic show",
                "seasons": [{"id": "1", "title": "Season 1"}],
                "episodes": [{"id": "701", "season_id": "1", "title": "Episode 1",
                              "episode_number": "1", "container_extension": "mp4"}]}


class PrivateBackend:
    def __init__(self, base, statuses, live=False, finite=False):
        self.server = self.thread = None
        self.tokens = {}
        self.routes = {}
        self.file_routes = {}
        self.finite = finite
        assert not (live and finite)
        self.accounts = {}
        self.statuses = statuses
        database_url = os.environ["VIVOAPK_NATIVE_PRIVATE_DATABASE_URL"]
        database = make_url(database_url)
        assert database.drivername == "postgresql+psycopg"
        assert database.host == "127.0.0.1" and database.port == 5432
        assert database.database == "vivoapk_native_private_ci" and database.username == "vivoapk_fixture"
        self.live_windows = {}
        settings = Settings(
            app_env="test", database_url=database_url,
            session_signing_key=secrets.token_urlsafe(32),
            private_hls_enabled=True, private_media_key=secrets.token_urlsafe(32),
            session_ttl_seconds=300,
        )
        self.app = create_app(settings)
        dbfactory = self.app.state.session_factory
        self.engine = dbfactory.kw["bind"]
        assert self.engine.dialect.name == "postgresql"
        Base.metadata.create_all(self.engine)
        future = datetime.now(UTC) + timedelta(days=3)
        with dbfactory() as db:
            for i, suffix in enumerate(("a", "b"), 1):
                username, password = f"sub-{suffix}", f"fixture-private-password-{suffix}"
                if finite:
                    path = base / ("private-native-" + suffix + ".mp4")
                    data = generate_file("ffmpeg", path, suffix, 330+i*220)
                    # Native source metadata is a fixture contract. Check it
                    # with the same official FFprobe used by Jellyfin.
                    import json
                    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams",
                        "-show_format", "-of", "json", str(path)], capture_output=True,
                        text=True, check=True, timeout=20)
                    info = json.loads(probe.stdout)
                    streams = info["streams"]
                    assert len(streams) == 2
                    assert streams[0]["codec_name"] == "h264"
                    assert (streams[0]["width"], streams[0]["height"]) == (640, 360)
                    assert streams[0]["avg_frame_rate"] == "25/1"
                    assert streams[1]["codec_name"] == "aac" and streams[1]["channels"] == 1
                    assert streams[1]["sample_rate"] == "48000"
                    assert abs(float(info["format"]["duration"]) - 12) < .1
                    route = (f"/movie/{username}/{password}/501.mp4" if suffix == "a"
                             else f"/series/{username}/{password}/701.mp4")
                    self.file_routes[ORIGIN + route] = data
                else:
                    folder = base / ("private-source-" + suffix)
                    folder.mkdir()
                    video = "testsrc2=size=160x90:rate=25" if suffix == "a" else "smptebars=size=160x90:rate=25"
                    subprocess.run([
                        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
                        "-f", "lavfi", "-i", video,
                        "-f", "lavfi", "-i", f"sine=frequency={330+i*220}:sample_rate=48000",
                        "-t", "240" if live else "15", "-c:v", "mpeg2video", "-g", "25", "-threads:v", "1",
                        "-b:v", "380k", "-c:a", "mp2", "-ac", "2", "-b:a", "128k",
                        "-f", "hls", "-hls_time", "3", "-hls_list_size", "0",
                        "-hls_segment_filename", str(folder / "s%03d.ts"), str(folder / "index.m3u8"),
                    ], capture_output=True, check=True, timeout=30)
                    playlist = (folder / "index.m3u8").read_text()
                    segments = {p.name: p.read_bytes() for p in folder.glob("*.ts")}
                    assert len(segments) >= 3 and "#EXT-X-ENDLIST" in playlist
                    for name, body in segments.items():
                        playlist = playlist.replace("\n"+name+"\n", f"\n/muxed/{suffix}/{name}\n")
                        self.routes[ORIGIN + f"/muxed/{suffix}/{name}"] = (
                            lambda b=body: response(b, b"video/mp2t"))
                    root = ORIGIN + f"/live/{username}/{password}/42.m3u8"
                    if live:
                        # Release pre-generated, uniquely timestamped TS segments
                        # at broadcast pace. No ENDLIST or finite VOD playlist.
                        # 240 seconds of material is more than the test's horizon.
                        names = sorted(segments)
                        assert len(names) >= 78
                        self.live_windows[suffix] = {"names": names, "started": None, "sequence": 0}
                        self.routes[root] = lambda s=suffix: self.live_manifest(s)
                    else:
                        self.routes[root] = lambda p=playlist.encode(): response(p)
                mapping = SubscriptionMapping(
                    mega_subscription_id=900+i, username=username, dns_link=ORIGIN,
                    dns_link_samsung_lg=ORIGIN, expiring_at=future, last_synced_at=datetime.now(UTC),
                )
                db.add(mapping)
                db.flush()
                self.tokens[suffix], _ = issue_session_token(mapping.id, settings)
                self.accounts[900+i] = SimpleNamespace(
                    id=900+i, username=username, password=SecretStr(password),
                    expiring_at=future, dns_link=ORIGIN, dns_link_for_samsung_lg=ORIGIN,
                )
            db.commit()
        self.upstream = Origin(self.routes)
        self.file_upstream = RangePool(self.file_routes)
        class Xtream(FakeXtream):
            def authenticate(inner, **kwargs):
                label = "synthetic-mega-user" if kwargs["username"] == "sub-a" else "synthetic-mega-user-b"
                state = statuses[label]
                return SimpleNamespace(code="SUCCESS" if state == "ACTIVE" else state,
                                       account_expires_at=None)
        self.app.state.mega_client_factory = lambda: FakeMega(self.accounts)
        self.app.state.xtream_client_factory = Xtream
        self.app.state.catalog_client_factory = Catalogue
        self.app.state.private_hls_factory = lambda source: PrivateHlsSession(
            source, entitlement=lambda: EntitlementState.UNAVAILABLE,
            resolver=public_dns, pool_factory=self.upstream.factory,
        )
        if finite:
            self.app.state.private_file_factory = lambda source: PrivateFileSession(
                source, body_reader=lambda src, **kwargs: open_media_body(
                    src, resolver=public_dns, pool_factory=self.file_upstream.factory, **kwargs))
        if SOCKET.exists() or KEY.exists():
            raise RuntimeError("Disposable media fixture paths already exist")
        fd = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(settings.private_media_key.get_secret_value())
        self.server = uvicorn.Server(uvicorn.Config(
            self.app, uds=str(SOCKET), log_config=None, access_log=False, workers=1))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        if not self.server.started or not SOCKET.is_socket():
            self.close()
            raise RuntimeError("Private fixture backend not listening")

    def live_manifest(self, suffix):
        window = self.live_windows[suffix]
        if window["started"] is None:
            window["started"] = time.monotonic()
        count = 3 + int((time.monotonic() - window["started"]) / 3)
        if count >= len(window["names"]):
            raise RuntimeError("Synthetic live fixture exhausted")
        first = max(0, count - 8)
        window["sequence"] = first
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:3",
                 f"#EXT-X-MEDIA-SEQUENCE:{first}"]
        for name in window["names"][first:count]:
            lines.extend(["#EXTINF:3.000,", f"/muxed/{suffix}/{name}"])
        return response(("\n".join(lines) + "\n").encode())

    def count(self):
        return len(self.app.state.private_hls_registry._entries)

    def check_cleanup(self):
        assert self.count() == 0
        if self.finite:
            calls = self.file_upstream.calls
            assert len(calls) > 4
            assert {url for url, _, _, _ in calls} == set(self.file_routes)
            assert all(0 <= start <= end and end-start+1 <= 4*1024*1024
                       for _, start, end, _ in calls)
            assert any(start > 0 for _, start, _, _ in calls)
            assert all(p.closed and p.response.closed for p in self.file_upstream.pools)
            import json
            print("JELLYFIN_PRIVATE_NATIVE_FILE_ORIGIN_OBSERVATION=" + json.dumps({
                "range_reads": len(calls),
                "nonzero_ranges": sum(start > 0 for _, start, _, _ in calls),
                "max_read_bytes": max(end-start+1 for _, start, end, _ in calls),
                "active_backend_sessions": self.count(),
            }), flush=True)
        else:
            assert self.upstream.calls and all(url in self.routes for url in self.upstream.calls)
            assert all(p.closed and p.response.closed for p in self.upstream.pools)

    def close(self):
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=10)
        SOCKET.unlink(missing_ok=True)
        KEY.unlink(missing_ok=True)
        if getattr(self, "engine", None) is not None:
            Base.metadata.drop_all(self.engine)
            self.engine.dispose()
