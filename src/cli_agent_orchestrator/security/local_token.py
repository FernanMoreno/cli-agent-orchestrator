"""Read an operator-owned renewable credential; authorization remains at the API."""

import os
import stat
from pathlib import Path
from typing import Optional

_MAX_TOKEN_BYTES = 16384


def read_local_token_file(filename: str) -> Optional[str]:
    """Read the current private regular file without caching or following its symlink."""
    path = Path(filename)
    if os.name != "posix" or not path.is_absolute():
        return None
    try:
        parent = path.parent.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o077
        ):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as reader:
            info = os.fstat(reader.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
                or not 0 < info.st_size <= _MAX_TOKEN_BYTES
            ):
                return None
            payload = reader.read(_MAX_TOKEN_BYTES + 1)
        if len(payload) > _MAX_TOKEN_BYTES:
            return None
        token = payload.decode("ascii").strip()
        if not token or any(ord(character) <= 32 or ord(character) == 127 for character in token):
            return None
        return token
    except (OSError, ValueError, UnicodeError):
        return None
