#!/usr/bin/env python3
"""Stage an independently pinned candidate. No production activation.

Only project-local new directories, no system package installs, no edits to
the live checkout, service configuration, secret vault or database. Each
phase uses a separate bounded Ferrari request. Failed stages are retained
for explicit review; do not overwrite them or claim readiness.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

import native_release_preflight as pf

SOURCE_SHA = "0994990c024b159eebd44528041746f557596d27"
ORIGINAL_SHA = "74b6a02be199a29c305058d2875063eb46c1b61d"
REPO = "https://github.com/martaxi-boss/Vivoapk.git"
STAGE = pf.ROOT / "native-release-stage" / SOURCE_SHA
VENV = pf.ROOT / ".venv/bin/python"
ARCHIVE_URL = ("https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/v8.1.3-2/"
               "jellyfin-ffmpeg_8.1.3-2_portable_linux64-gpl.tar.xz")
ARCHIVE_SIZE = 60657368
ARCHIVE_SHA = "1df84d04c4322902dba81c290508cdf3d89524d210b9fe03154bea6c4e3bfbb6"
FFMPEG_SHA = "c03a86655431dd0bc2412f0c98c9712445b63c17daee3a9a6e4008c962a56ad1"
FFPROBE_SHA = "72733c695f2445f61f79a7b6b68a02a05523c032f28f63649cdfdb2df8cb0622"
BUNDLE_SHA = "54ba20844f871177869d8359a43bfd2003ca59a963e7a26b2c120cca2a52a3da"
BUNDLE_BYTES = 758717
MANIFEST_SHA = "f7cf404d154c3d376770cb7131a2dbf1077c09d887e561908f0ae7c570b155a5"
CANDIDATE = {
    "Jellyfin.Api.dll": "1d6e8bd140b2e52a479299da859f4c6e3e1cf73320f1880f00c6291b21fdf620",
    "Jellyfin.LiveTv.dll": "29ec1b3ccbe5b87a390218a56520de80999571e2ce11c1c3546bcf0df499d3d7",
    "MediaBrowser.Controller.dll": "9a91fcb5e0a34a819760f0ec8809f3f24209e6ac77acfbb3cb29ed6ef67a98be",
    "Jellyfin.Plugin.VivoapkMegaAuth.dll": "96450ba026e76b868bdb6ba5fb93c91b2038f8d8c79137af25baa6af367888cb",
    "meta.json": "253dd2f2e9b2516a4b754874d03ae86d7d27d84e008f93f3a3bd3bfaf8ad4873",
}
ENV = {"PATH": "/usr/bin:/bin", "HOME": "/root", "LANG": "C.UTF-8",
       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
       "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}


class Blocked(Exception):
    pass


def require(value, reason):
    if not value:
        raise Blocked(reason)


def run(argv, timeout=25, *, cwd=None, user=None, env=None, text=True):
    effective = dict(ENV if env is None else env)
    if user is not None:
        record = pwd.getpwnam(user)
        effective["HOME"] = record.pw_dir
        # Run tested candidate executables as their actual restricted user,
        # not root. Do not inherit production environment/credentials.
        argv = ["/usr/sbin/runuser", "-u", user, "--", *argv]
    try:
        p = subprocess.run(argv, cwd=cwd, env=effective, capture_output=True,
                           text=text, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("stage_command_" + type(exc).__name__) from None
    if p.returncode:
        raise Blocked("stage_command_failed_" + Path(argv[0]).name)
    return p.stdout


def digest(path):
    require(path.is_file() and not path.is_symlink(), "stage_file_missing_or_symlink")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def trusted_directory(path):
    for parent in (*reversed(path.parents), path):
        require(stat.S_ISDIR(parent.lstat().st_mode), "stage_unsafe_directory")
    info = path.stat()
    require(info.st_uid == 0 and not info.st_mode & 0o022, "stage_directory_not_root_controlled")


def new_phase(name):
    for p in (STAGE.parent, STAGE):
        if not os.path.lexists(p):
            p.mkdir(mode=0o755)
        trusted_directory(p)
    phase = STAGE / name
    require(not os.path.lexists(phase), "existing_stage_requires_review")
    phase.mkdir(mode=0o755)
    trusted_directory(phase)
    return phase


def safe_unpack(archive, destination):
    with tarfile.open(archive, "r:xz") as tf:
        members = tf.getmembers()
        require(0 < len(members) <= 4096, "unexpected_archive_member_count")
        require(sum(m.size for m in members if m.isfile()) <= 512 * 1024**2,
                "unexpected_archive_size")
        for member in members:
            require(not member.isdev() and not member.isfifo(), "unsafe_archive_special_file")
        # Python 3.12+ data filter rejects external paths/links, special
        # entries and dangerous mode bits. Archive hash has been checked.
        tf.extractall(destination, members=members, filter="data")


BACKEND_PROBE = r"""
import ast
import importlib.metadata as md
import json
from pathlib import Path
import sys
import tomllib

base = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(base))
project = tomllib.loads((base / "pyproject.toml").read_text())
for pin in project["project"]["dependencies"]:
    name, expected = pin.split("==")
    package = name.split("[", 1)[0]
    assert md.version(package) == expected, "dependency_pin_mismatch"
    if name == "psycopg[binary]":
        assert md.version("psycopg-binary") == expected
for file in (base / "app").rglob("*.py"):
    ast.parse(file.read_bytes(), filename=file.name)
# app.main constructs its default app at import time. Supply a synthetic,
# in-memory configuration before importing it, not the live environment.
import os
os.environ.update(APP_ENV="test", DATABASE_URL="sqlite+pysqlite:///:memory:",
                  SESSION_SIGNING_KEY="synthetic-signing-key-" + "x" * 32,
                  PRIVATE_HLS_ENABLED="false")
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
import app.main
assert Path(app.main.__file__).resolve().is_relative_to(base)

def forbidden(*args, **kwargs):
    raise AssertionError("synthetic denial reached upstream")

def make(enabled):
    settings = Settings(app_env="test", database_url="sqlite+pysqlite:///:memory:",
        session_signing_key="synthetic-signing-key-" + "x" * 32,
        private_hls_enabled=enabled, private_media_key="synthetic-media-key-" + "y" * 32)
    app = create_app(settings)
    app.state.mega_client_factory = forbidden
    app.state.xtream_client_factory = forbidden
    app.state.catalog_client_factory = forbidden
    return app

routes = {
    "/_internal/media/hls/open": {"category_id":"1","asset_id":"1","platform":"android"},
    "/_internal/media/file/open": {"category_id":"1","asset_id":"1","kind":"vod","platform":"android"},
    "/_internal/media/hls/renew": {"session_id":"A"*32},
    "/_internal/media/hls/playlist": {"session_id":"A"*32},
    "/_internal/media/hls/segment": {"session_id":"A"*32,"segment_id":"B"*32},
    "/_internal/media/hls/close": {"session_id":"A"*32},
    "/_internal/media/file/read": {"session_id":"A"*32,"start":0,"length":128},
    "/_internal/media/file/renew": {"session_id":"A"*32},
    "/_internal/media/file/close": {"session_id":"A"*32},
}
with TestClient(make(False)) as client:
    for route, data in routes.items():
        assert client.post(route, json=data).status_code == 404
app = make(True)
with TestClient(app) as client:
    key = "synthetic-media-key-" + "y"*32
    public = client.get("/openapi.json")
    assert public.status_code == 200 and not any(r in public.json()["paths"] for r in routes)
    for route, data in routes.items():
        assert client.post(route, json=data).status_code == 403
        assert client.post(route, json=data, headers={"X-Vivoapk-Media-Key":key}).status_code == 401
        response = client.post(route, json=data, headers={
            "X-Vivoapk-Media-Key":key,"Authorization":"Bearer synthetic-invalid"})
        assert response.status_code in (401,404), "invalid_grant_accepted"
    assert not app.state.private_hls_registry._entries
    assert client.post("/v1/catalog/categories", json={"kind":"live"}).status_code == 401
print(json.dumps({"dependency_pins_match":True,"source_imported":True,
    "private_routes_disabled_by_default":True,"private_routes_denial_cases":len(routes)*3,
    "routes_hidden_from_openapi":True,"no_upstream_or_real_database":True,
    "catalog_denial_preserved":True,"no_leases":True}))
"""


def stage_backend():
    dest = new_phase("backend")
    checkout = dest / "source"
    checkout.mkdir(mode=0o755)
    run(["git", "init", "--quiet", str(checkout)])
    run(["git", "-C", str(checkout), "fetch", "--no-tags", "--depth", "1", REPO, SOURCE_SHA],
        timeout=40)
    require(run(["git", "-C", str(checkout), "rev-parse", "FETCH_HEAD"]).strip() == SOURCE_SHA,
            "stage_source_identity_mismatch")
    run(["git", "-C", str(checkout), "checkout", "--quiet", "--detach", SOURCE_SHA], timeout=15)
    require(run(["git", "-C", str(checkout), "rev-parse", "HEAD"]).strip() == SOURCE_SHA,
            "staged_checkout_identity_mismatch")
    return verify_staged_backend()


def verify_staged_backend():
    # Reuse the already fetched, root-controlled exact checkout after a
    # failed synthetic probe; do not refetch or overwrite its contents.
    checkout = STAGE / "backend/source"
    for directory in (STAGE.parent, STAGE, STAGE / "backend", checkout):
        trusted_directory(directory)
    require(run(["git", "-C", str(checkout), "rev-parse", "HEAD"]).strip() == SOURCE_SHA,
            "staged_checkout_identity_mismatch")
    require(not run(["git", "-C", str(checkout), "status", "--porcelain=v1",
                     "--untracked-files=all"]).strip(), "staged_checkout_not_clean")
    # Verify exact dependency declaration and migration/model tree against
    # the installed source, without touching the installed checkout.
    installed = ["git", "-c", "safe.directory=" + str(pf.SOURCE), "-C", str(pf.SOURCE)]
    candidate = ["git", "-C", str(checkout)]
    for rel in ("backend/pyproject.toml", "backend/alembic", "backend/alembic.ini", "backend/app/models.py"):
        a = run(installed + ["ls-tree", ORIGINAL_SHA, "--", rel]).strip()
        b = run(candidate + ["ls-tree", SOURCE_SHA, "--", rel]).strip()
        require(bool(a) and a == b, "backend_dependency_or_schema_tree_changed")
    require(VENV.is_file(), "backend_venv_missing")
    result = json.loads(run([str(VENV), "-I", "-B", "-c", BACKEND_PROBE,
        str(checkout / "backend")], timeout=20, user="vivoapksvc",
        cwd=checkout / "backend"))
    require(all(result[k] is True for k in (
        "dependency_pins_match", "source_imported", "private_routes_disabled_by_default",
        "routes_hidden_from_openapi", "no_upstream_or_real_database", "catalog_denial_preserved",
        "no_leases")) and result["private_routes_denial_cases"] == 27, "backend_probe_failed")
    return {"backend_staged": True, "source": SOURCE_SHA,
            "dependencies_and_migration_tree_unchanged": True, "probe": result}


def stage_ffmpeg():
    dest = new_phase("ffmpeg")
    archive = dest / "official.tar.xz"
    run(["curl", "-fLsS", "--proto", "=https", "--proto-redir", "=https",
         "--max-time", "45", "--max-filesize", str(ARCHIVE_SIZE), "-o", str(archive), ARCHIVE_URL],
        timeout=50)
    require(archive.stat().st_size == ARCHIVE_SIZE and digest(archive) == ARCHIVE_SHA,
            "ffmpeg_official_archive_mismatch")
    payload = dest / "runtime"
    payload.mkdir(mode=0o755)
    safe_unpack(archive, payload)
    encoders = list(payload.rglob("ffmpeg"))
    require(len(encoders) == 1, "ffmpeg_not_unique")
    ffmpeg = encoders[0]
    ffprobe = ffmpeg.with_name("ffprobe")
    require(digest(ffmpeg) == FFMPEG_SHA and digest(ffprobe) == FFPROBE_SHA,
            "ffmpeg_candidate_hash_mismatch")
    # Preserve only the reviewed portable runtime, not the duplicate archive.
    archive.unlink()
    user = "jellyfinpink"
    version = run([str(ffmpeg), "-version"], user=user).splitlines()[0]
    probe_version = run([str(ffprobe), "-version"], user=user).splitlines()[0]
    require("8.1.3" in version and "8.1.3" in probe_version, "unexpected_ffmpeg_version")
    with tempfile.TemporaryDirectory(prefix="probe-", dir=dest) as name:
        tmp = Path(name)
        u = pwd.getpwnam(user)
        os.chown(tmp, u.pw_uid, u.pw_gid)
        sample = tmp / "synthetic.ts"
        run([str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
             "-f", "lavfi", "-i", "sine=frequency=600:sample_rate=48000",
             "-t", "2", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-f", "mpegts", str(sample)], user=user, timeout=12)
        report = json.loads(run([str(ffprobe), "-v", "error", "-show_entries",
            "stream=codec_type,codec_name", "-of", "json", str(sample)], user=user))
        codecs = {(s["codec_type"], s["codec_name"]) for s in report["streams"]}
        require({("video", "h264"), ("audio", "aac")} <= codecs, "ffmpeg_probe_codec_mismatch")
        frames = run([str(ffmpeg), "-nostdin", "-v", "error", "-i", str(sample),
            "-map", "0:v:0", "-map", "0:a:0", "-f", "framemd5", "pipe:1"],
            user=user, timeout=12)
        video = sum(line.startswith("0,") for line in frames.splitlines())
        audio = sum(line.startswith("1,") for line in frames.splitlines())
        require(min(video, audio) >= 40, "ffmpeg_sample_decode_failed")
    return {"ffmpeg_staged": True, "archive_hash_verified": True,
            "encoder_hash_verified": True, "probe_hash_verified": True,
            "ffmpeg": str(ffmpeg), "ffprobe": str(ffprobe), "video_frames": video,
            "audio_frames": audio, "restricted_user": user, "synthetic_sample_removed": True}


def stage_bundle():
    # Use the existing authenticated GitHub CLI only if already available.
    # Do not install gh, retrieve tokens, publish signed URLs, or create
    # another transport. An unavailable CLI is an explicit staging blocker.
    gh = shutil.which("gh", path="/usr/bin:/bin:/usr/local/bin")
    require(gh is not None, "authenticated_artifact_transfer_unavailable")
    dest = new_phase("bundle")
    with tempfile.TemporaryDirectory(prefix="download-", dir=dest) as name:
        tmp = Path(name)
        u = pwd.getpwnam("ubuntu")
        os.chown(tmp, u.pw_uid, u.pw_gid)
        run([gh, "run", "download", "45787708538", "--repo", "martaxi-boss/Vivoapk",
             "--name", "vivoapk-native-regular-candidate-" + SOURCE_SHA, "--dir", name],
            user="ubuntu", timeout=35)
        expected = set(CANDIDATE) | {"candidate-manifest.json"}
        require({p.name for p in tmp.iterdir()} == expected, "candidate_bundle_file_set")
        for file in expected:
            path = tmp / file
            require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 8*1024**2,
                    "unsafe_candidate_file")
            with path.open("rb") as inp, (dest / file).open("xb") as out:
                shutil.copyfileobj(inp, out)
            (dest / file).chmod(0o644)
        for file, expected_hash in CANDIDATE.items():
            require(digest(dest / file) == expected_hash, "candidate_bundle_digest_mismatch")
        manifest = json.loads((dest / "candidate-manifest.json").read_text())
        require(manifest["source"] == SOURCE_SHA and manifest["ci_run"] == "45787708538"
                and manifest["activation_allowed"] is False
                and all(manifest["components"][file]["sha256"] == CANDIDATE[file]
                        for file in CANDIDATE if file.endswith(".dll")), "candidate_manifest_mismatch")
    return {"bundle_staged": True, "exact_ci_artifact": True, "four_dll_hashes_verified": True}


def verified_bundle_bytes(archive):
    # The existing Ferrari input channel transports the exact artifact;
    # never trust a bundle's own approval or fetch a mutable release.
    require(archive.is_file() and not archive.is_symlink()
            and archive.stat().st_size == BUNDLE_BYTES
            and digest(archive) == BUNDLE_SHA, "candidate_input_archive_mismatch")
    expected = set(CANDIDATE) | {"candidate-manifest.json"}
    with zipfile.ZipFile(archive) as z:
        entries = z.infolist()
        require(len(entries) == len(expected) and {e.filename for e in entries} == expected,
                "candidate_bundle_file_set")
        require(sum(e.file_size for e in entries) <= 8 * 1024**2,
                "candidate_bundle_size")
        for e in entries:
            mode = e.external_attr >> 16
            require(not e.is_dir() and not stat.S_ISLNK(mode)
                    and 0 < e.file_size <= 8 * 1024**2, "unsafe_candidate_file")
        require(z.testzip() is None, "candidate_bundle_crc")
        files = {name: z.read(name) for name in expected}
    pins = {**CANDIDATE, "candidate-manifest.json": MANIFEST_SHA}
    for name, expected_hash in pins.items():
        require(hashlib.sha256(files[name]).hexdigest() == expected_hash,
                "candidate_bundle_digest_mismatch")
    manifest = json.loads(files["candidate-manifest.json"])
    require(manifest["source"] == SOURCE_SHA and manifest["backend_candidate"] == SOURCE_SHA
            and manifest["ci_run"] == "45787708538"
            and manifest["activation_allowed"] is False
            and all(manifest["components"][file]["sha256"] == CANDIDATE[file]
                    and manifest["components"][file]["bytes"] == len(files[file])
                    for file in CANDIDATE if file.endswith(".dll")), "candidate_manifest_mismatch")
    return files


def stage_bundle_input(archive):
    files = verified_bundle_bytes(archive)
    # Keep the failed, empty CLI attempt for review; no overwriting.
    dest = new_phase("bundle-verified")
    for name, data in files.items():
        with (dest / name).open("xb") as stream:
            stream.write(data)
        (dest / name).chmod(0o644)
    require(all(digest(dest / name) == hashlib.sha256(data).hexdigest()
                for name, data in files.items()), "staged_bundle_digest_mismatch")
    return {"bundle_staged": True, "exact_ci_artifact": True,
            "archive_sha256": BUNDLE_SHA, "four_dll_hashes_verified": True,
            "manifest_hash_verified": True, "no_new_credentials": True}


def main():
    phase = sys.argv[1] if len(sys.argv) >= 2 else ""
    actions = {"backend": stage_backend, "ffmpeg": stage_ffmpeg, "bundle": stage_bundle,
               "backend-recheck": verify_staged_backend}
    if phase == "bundle-input" and len(sys.argv) == 3:
        actions[phase] = lambda: stage_bundle_input(Path(sys.argv[2]))
    report = {"phase": phase, "activation_allowed": False, "scope": "isolated_stage_only"}
    try:
        require(phase in actions and (len(sys.argv) == 2 or phase == "bundle-input"),
                "unsupported_stage_phase")
        initial = pf.inspect()
        require(initial["baseline_pass"], "live_baseline_not_safe")
        pids = {unit: pf.prop(unit, "MainPID") for unit in pf.PROTECTED}
        try:
            report.update(actions[phase]())
        finally:
            final = pf.inspect()
            report["production_unchanged"] = (final["baseline_pass"]
                and initial["components"] == final["components"]
                and initial["backend_revision"] == final["backend_revision"]
                and all(pf.prop(unit, "MainPID") == pid for unit, pid in pids.items()))
        require(report["production_unchanged"], "production_identity_changed")
        report["stage_pass"] = True
    except (Blocked, pf.Blocked, OSError, ValueError, KeyError,
            subprocess.TimeoutExpired, tarfile.TarError, zipfile.BadZipFile) as exc:
        report["stage_pass"] = False
        report["reason"] = str(exc) if isinstance(exc, (Blocked, pf.Blocked)) else type(exc).__name__
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["stage_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
