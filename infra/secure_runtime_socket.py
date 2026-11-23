#!/usr/bin/env python3
"""Harden the EXISTING VivoAPK Uvicorn Unix socket after bind, without a new service.

Called by ExecStartPost as vivoapksvc. Does not touch Jellyfin, credentials,
other sockets, media, Nginx, PostgreSQL or any other project.
"""
import grp
import os
from pathlib import Path
import stat
import time

SOCKET = Path("/srv/projects/Vivoapk/run/backend.sock")


def secure_socket(
    path: Path = SOCKET, *, expected_group: str = "www-data", max_wait: float = 8.0
) -> None:
    directory = path.parent
    if directory.is_symlink():
        raise PermissionError("runtime_directory_symlink")
    parent = directory.lstat()
    group_id = grp.getgrnam(expected_group).gr_gid
    if (not stat.S_ISDIR(parent.st_mode)
            or parent.st_gid != group_id
            or stat.S_IMODE(parent.st_mode) & 0o007
            or not parent.st_mode & stat.S_ISGID):
        raise PermissionError("unsafe_runtime_directory")
    limit = time.monotonic() + max_wait
    while True:
        if path.is_symlink():
            raise PermissionError("socket_symlink_refused")
        try:
            current = path.lstat()
        except FileNotFoundError:
            if time.monotonic() >= limit:
                raise TimeoutError("socket_not_bound")
            time.sleep(0.1)
            continue
        if (not stat.S_ISSOCK(current.st_mode)
                or current.st_uid != os.geteuid()
                or current.st_gid != group_id):
            raise PermissionError("unexpected_socket_identity")
        os.chmod(path, 0o660, follow_symlinks=False)
        checked = path.lstat()
        if (not stat.S_ISSOCK(checked.st_mode)
                or checked.st_uid != current.st_uid
                or checked.st_gid != current.st_gid
                or stat.S_IMODE(checked.st_mode) != 0o660):
            raise PermissionError("socket_hardening_not_verified")
        return


if __name__ == "__main__":
    secure_socket()
    print("VIVOAPK_PRIVATE_SOCKET_MODE_0660=PASS")
