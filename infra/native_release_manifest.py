"""Record exactly the integrated ordinary candidate. Never authorizes activation."""
import hashlib
import json
from pathlib import Path
import re
import sys

UPSTREAM = "83d0d0e7f76e23e2e085c8ff67d28b772d35967d"
ARCHIVE_SHA256 = "1df84d04c4322902dba81c290508cdf3d89524d210b9fe03154bea6c4e3bfbb6"
ARCHIVE_URL = ("https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/v8.1.3-2/"
               "jellyfin-ffmpeg_8.1.3-2_portable_linux64-gpl.tar.xz")
FIELDS = {
    "Jellyfin.Api.dll": ("PATCHED_API_SHA256",
        "6a960ae661b4cd9a902395889892217fecb18a9eca1ed724a7deeb2067964fe5"),
    "Jellyfin.LiveTv.dll": ("PATCHED_LIVETV_SHA256",
        "23a22e68fbcb00b71ad3cc7434ba8efee2f8a8239595f0ccd0a575b3049a311d"),
    "MediaBrowser.Controller.dll": ("PATCHED_CONTROLLER_SHA256",
        "ee148a49831f97c5dea8d3e96a6920c2cc7bc11ca9508fb9f437cd02ad978985"),
    "Jellyfin.Plugin.VivoapkMegaAuth.dll": ("REGULAR_PLUGIN_SHA256",
        "60630b0ab43d4414739f86b047ed4d62358de8c587b878f1aeb6320e0fa813c2"),
}

def sha256(path):
    if path.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise ValueError("missing or unsafe candidate file")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def build(bundle, evidence, ffmpeg, ffprobe, source, run):
    if not re.fullmatch(r"[0-9a-f]{40}", source) or not run.isdecimal():
        raise ValueError("invalid candidate identity")
    values = {}
    for line in evidence.read_text().splitlines():
        key, value = line.split("=", 1)
        if key in values and values[key] != value:
            raise ValueError("conflicting CI evidence")
        values[key] = value
    if values.get("SOURCE_COMMIT") != source or values.get("COMBINED_LAB_GATE_EXIT") != "0":
        raise ValueError("integrated candidate not passed")
    if {p.name for p in bundle.iterdir()} != set(FIELDS) | {"meta.json"}:
        raise ValueError("unexpected candidate file set")
    result = {}
    for name, (field, original) in FIELDS.items():
        path = bundle / name
        digest = sha256(path)
        if values.get(field) != digest or digest == original:
            raise ValueError("candidate digest mismatch")
        result[name] = {"sha256": digest, "bytes": path.stat().st_size,
                        "expected_installed_sha256": original}
    if values.get("SYNTHETIC_PLUGIN_SHA256") in (None, result[
            "Jellyfin.Plugin.VivoapkMegaAuth.dll"]["sha256"]):
        raise ValueError("ordinary/fixture artifact boundary not proven")
    ffmpeg_hash = sha256(ffmpeg)
    if values.get("FFMPEG_SHA256") != ffmpeg_hash:
        raise ValueError("FFmpeg changed after testing")
    return {"schema": 1, "scope": "candidate_only_no_activation_approval",
            "source": source, "backend_candidate": source, "ci_run": run,
            "jellyfin_version": "12.4", "upstream": UPSTREAM,
            "components": result, "plugin_metadata_sha256": sha256(bundle / "meta.json"),
            "ffmpeg": {"archive_url": ARCHIVE_URL, "archive_sha256": ARCHIVE_SHA256,
                       "ffmpeg_sha256": ffmpeg_hash, "ffprobe_sha256": sha256(ffprobe)},
            "activation_allowed": False}

if __name__ == "__main__":
    bundle, evidence, ffmpeg, ffprobe = map(Path, sys.argv[1:5])
    result = build(bundle, evidence, ffmpeg, ffprobe, *sys.argv[5:])
    with (bundle / "candidate-manifest.json").open("x") as output:
        json.dump(result, output, sort_keys=True, indent=2)
        output.write("\n")
    print("VIVOAPK_EXACT_REGULAR_CANDIDATE_MANIFEST=PASS", flush=True)
