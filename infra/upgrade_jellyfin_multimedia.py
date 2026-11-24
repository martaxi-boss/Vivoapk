#!/usr/bin/env python3
"""Jellyfin-only VivoAPK multimedia upgrade via Ferrari. No backend restart."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
from xml.etree import ElementTree as ET

ROOT = Path("/srv/projects/Vivoapk")
SOURCE = ROOT / "source"
JELLY = Path("/srv/projects/jellyfin-server")
DLL = JELLY / "data/plugins/VivoAPK Mega OTT Auth_0.1.0.0/Jellyfin.Plugin.VivoapkMegaAuth.dll"
XML = JELLY / "data/plugins/configurations/Jellyfin.Plugin.VivoapkMegaAuth.xml"
STAGED = ROOT / "jellyfin-auth/multimedia"
BACKUPS = ROOT / "backups/jellyfin-multimedia"
BACKEND_SHA = "74b6a02be199a29c305058d2875063eb46c1b61d"

spec = importlib.util.spec_from_file_location(
    "guarded_pilot", SOURCE / "infra/upgrade_live_preview.py")
if spec is None or spec.loader is None:
    raise RuntimeError("existing_guarded_upgrade_not_found")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class Blocked(Exception):
    pass


def preflight(sha):
    prior, admin = guard.preflight(sha)
    if prior != BACKEND_SHA:
        raise Blocked("backend_version_requires_review")
    if not XML.is_file() or XML.is_symlink() or not DLL.is_file() or DLL.is_symlink():
        raise Blocked("existing_plugin_missing")
    owners = ET.parse(XML).getroot().find("ChannelSlotOwners")
    if owners is not None and len(owners) not in (0, 64):
        raise Blocked("unknown_channel_slots")
    return prior, admin


def stage(sha):
    preflight(sha)
    dest = STAGED / sha
    if dest.exists() or dest.is_symlink():
        raise Blocked("stage_exists_review")
    guard.run(["git", "-C", str(SOURCE), "fetch", "--no-tags",
               "--depth", "1", "origin", sha], timeout=140)
    if guard.run(["git", "-C", str(SOURCE), "rev-parse", "FETCH_HEAD"]) != sha:
        raise Blocked("source_sha_mismatch")
    changes = guard.run(["git", "-C", str(SOURCE), "diff", "--name-only",
                         "HEAD", sha, "--", "backend/"])
    if changes.strip():
        raise Blocked("backend_changes_forbidden")
    dest.mkdir(parents=True, exist_ok=False)
    checkout = dest / "checkout"
    guard.run(["git", "-C", str(SOURCE), "worktree", "add", "--detach",
               str(checkout), sha], timeout=100)
    project = checkout / "jellyfin-auth/Vivoapk.Jellyfin.Auth/Vivoapk.Jellyfin.Auth.csproj"
    tests = checkout / "jellyfin-auth/Vivoapk.Jellyfin.Auth.Tests/Vivoapk.Jellyfin.Auth.Tests.csproj"
    for path in (project, tests):
        if not path.is_file():
            raise Blocked("plugin_source_missing")
    cache = ROOT / "jellyfin-auth/build-cache"
    home, nuget = cache / "dotnet-home", cache / "nuget"
    home.mkdir(parents=True, exist_ok=True)
    nuget.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "DOTNET_CLI_HOME": str(home),
           "NUGET_PACKAGES": str(nuget),
           "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
           "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
    guard.run(["dotnet", "restore", str(project)], timeout=240, env=env)
    guard.run(["dotnet", "build", str(project), "-c", "Release",
               "--no-restore"], timeout=240, env=env)
    guard.run(["dotnet", "run", "--project", str(tests),
               "-c", "Release"], timeout=240, env=env)
    compiled = project.parent / "bin/Release/net10.0" / DLL.name
    if not compiled.is_file() or compiled.stat().st_size < 7000:
        raise Blocked("binary_missing")
    shutil.copy2(compiled, dest / DLL.name)
    return {"status": "staged", "backend_unchanged": True,
            "dll_sha256": hashlib.sha256(compiled.read_bytes()).hexdigest()}


def backdir(sha):
    return BACKUPS / sha[:24]


def rollback(sha):
    place = backdir(sha)
    if not (place / "old.dll").is_file() or not (place / "old.xml").is_file():
        raise Blocked("backup_missing")
    guard.run(["systemctl", "stop", guard.JELLY_SERVICE], timeout=45)
    guard.atomically_copy(place / "old.dll", DLL)
    guard.atomically_copy(place / "old.xml", XML)
    guard.run(["systemctl", "start", guard.JELLY_SERVICE], timeout=65)
    for _ in range(65):
        try:
            guard.health(expected_preview=False)
            return {"status": "rolled_back", "jellyfin_healthy": True,
                    "backend_untouched": True}
        except guard.Rejected:
            time.sleep(1)
    raise Blocked("rollback_needs_review")


def activate(sha):
    _, admin_before = preflight(sha)
    compiled = STAGED / sha / DLL.name
    if not compiled.is_file() or compiled.is_symlink():
        raise Blocked("staged_plugin_missing")
    place = backdir(sha)
    if place.exists() or place.is_symlink():
        raise Blocked("backup_exists_review")
    place.mkdir(parents=True, mode=0o700)
    os.chmod(place, 0o700)
    shutil.copy2(DLL, place / "old.dll")
    shutil.copy2(XML, place / "old.xml")
    for path in place.iterdir():
        os.chmod(path, 0o600)
    try:
        tree = ET.parse(XML)
        root = tree.getroot()
        for name, value in (("Enabled", "true"),
                            ("PilotLiveChannels", "true"),
                            ("CategoryNavigation", "true"),
                            ("FullLiveCatalog", "false"),
                            ("ProvisionPrivateLibraries", "false")):
            field = root.find(name)
            if field is None:
                field = ET.SubElement(root, name)
            field.text = value
        modified = place / "new.xml"
        tree.write(modified, encoding="utf-8", xml_declaration=True)
        guard.run(["systemctl", "stop", guard.JELLY_SERVICE], timeout=45)
        guard.atomically_copy(compiled, DLL)
        guard.atomically_copy(modified, XML)
        guard.run(["systemctl", "start", guard.JELLY_SERVICE], timeout=65)
        for _ in range(75):
            try:
                guard.health(expected_preview=True)
                break
            except guard.Rejected:
                time.sleep(1)
        else:
            raise Blocked("postflight_socket_unhealthy")
        if guard.admin_digest() != admin_before:
            raise Blocked("admin_identity_modified")
        root = ET.parse(XML).getroot()
        if (root.findtext("CategoryNavigation", "").lower() != "true" or
                root.findtext("PilotLiveChannels", "").lower() != "true" or
                root.findtext("FullLiveCatalog", "").lower() != "false"):
            raise Blocked("plugin_configuration_invalid")
        if hashlib.sha256(compiled.read_bytes()).digest() != hashlib.sha256(
                DLL.read_bytes()).digest():
            raise Blocked("installed_binary_mismatch")
        return {"status": "plugin_activated", "jellyfin_healthy": True,
                "backend_untouched": True, "admin_preserved": True,
                "stream_proxy_installed": False, "pink_modified": False}
    except (Blocked, guard.Rejected, OSError, ET.ParseError):
        result = rollback(sha)
        result["status"] = "rolled_back_after_failure"
        return result


def verify(sha):
    compiled = STAGED / sha / DLL.name
    if not compiled.is_file() or not DLL.is_file():
        raise Blocked("binary_unavailable")
    guard.health(expected_preview=True)
    if (hashlib.sha256(compiled.read_bytes()).digest() !=
            hashlib.sha256(DLL.read_bytes()).digest()):
        raise Blocked("plugin_binary_mismatch")
    if ET.parse(XML).getroot().findtext("CategoryNavigation", "").lower() != "true":
        raise Blocked("category_navigation_not_enabled")
    return {"status": "verified", "jellyfin_healthy": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "stage", "activate",
                                          "verify", "rollback"))
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    report = {"task": "vivoapk-native-jellyfin-multimedia",
              "mode": args.mode, "changed": False}
    try:
        if args.mode == "preflight":
            preflight(args.sha)
            report["status"] = "preflight_pass"
        elif args.mode == "stage":
            report.update(stage(args.sha))
        elif args.mode == "activate":
            report.update(activate(args.sha))
            report["changed"] = report["status"] == "plugin_activated"
        elif args.mode == "verify":
            report.update(verify(args.sha))
        else:
            report.update(rollback(args.sha))
            report["changed"] = True
    except (guard.Rejected, Blocked, OSError, ET.ParseError, ValueError) as exc:
        report["status"] = "blocked"
        report["reason"] = str(exc) if isinstance(exc, (guard.Rejected, Blocked)) else type(exc).__name__
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["status"] in ("preflight_pass", "staged", "plugin_activated",
                                     "verified", "rolled_back", "rolled_back_after_failure") else 1


if __name__ == "__main__":
    sys.exit(main())
