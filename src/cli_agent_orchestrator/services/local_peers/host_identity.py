"""Stable private host identity for the user-wide local peer registry."""

from __future__ import annotations

import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path


class HostIdentityError(RuntimeError):
    """The private host identity file is missing, unsafe, or corrupt."""


@dataclass(frozen=True)
class HostIdentity:
    host_id: str
    owner_uid: int


def current_host_identity(peer_dir: Path) -> HostIdentity:
    """Read or atomically create the owner-only stable identity for this host."""
    directory = Path(peer_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory_info = directory.lstat()
        if (
            stat.S_ISLNK(directory_info.st_mode)
            or not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != os.geteuid()
            or stat.S_IMODE(directory_info.st_mode) != 0o700
        ):
            raise HostIdentityError("Local peer identity directory is not private")
        path = directory / "host-id"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            pass
        else:
            identifier = str(uuid.uuid4())
            with os.fdopen(descriptor, "w", encoding="ascii") as output:
                output.write(identifier + "\n")
                output.flush()
                os.fsync(output.fileno())
            directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

        read_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, read_flags)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > 64
            ):
                raise HostIdentityError("Local peer host identity is not private")
            with os.fdopen(descriptor, "r", encoding="ascii") as stream:
                descriptor = -1
                value = stream.read(65).strip()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        parsed = uuid.UUID(value)
        if str(parsed) != value:
            raise HostIdentityError("Local peer host identity is invalid")
        return HostIdentity(host_id=value, owner_uid=os.geteuid())
    except HostIdentityError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise HostIdentityError("Local peer host identity is unavailable") from exc
