"""Private pre-wait barrier for the OpenCode API-worker restart test lane."""

from __future__ import annotations

import math
import os
import stat
import time
from pathlib import Path
from typing import Any, Callable

_CREATE_GROUP_PATH = "/agent/interactions/groups"
_READY_NAME = "create-response-ready"
_RELEASE_NAME = "restart-complete"
_ABORT_NAME = "restart-aborted"
_DEADLINE_NAME = "journey-deadline"


def _assert_private_directory(directory: Path) -> None:
    if not directory.is_absolute() or directory.is_symlink():
        raise RuntimeError("unsafe_mcp_restart_barrier")
    try:
        metadata = directory.lstat()
    except OSError as error:
        raise RuntimeError("unsafe_mcp_restart_barrier") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RuntimeError("unsafe_mcp_restart_barrier")


def _write_marker(directory: Path, name: str, value: bytes) -> None:
    _assert_private_directory(directory)
    path = directory / name
    temporary_path = directory / f".{name}.pending"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temporary_path, flags, 0o600)
    except OSError as error:
        raise RuntimeError("mcp_restart_barrier_marker_write_failed") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise RuntimeError("unsafe_mcp_restart_barrier_marker")
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, path, follow_symlinks=False)
        except OSError as error:
            raise RuntimeError("mcp_restart_barrier_marker_publish_failed") from error
    finally:
        os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
    directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_marker(directory: Path, name: str, expected: bytes) -> bool:
    _assert_private_directory(directory)
    path = directory / name
    try:
        before = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise RuntimeError("mcp_restart_barrier_marker_unreadable") from error
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) != 0o600
    ):
        raise RuntimeError("unsafe_mcp_restart_barrier_marker")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise RuntimeError("mcp_restart_barrier_marker_unreadable") from error
    try:
        current = os.fstat(descriptor)
        if (
            current.st_dev != before.st_dev
            or current.st_ino != before.st_ino
            or not stat.S_ISREG(current.st_mode)
            or current.st_uid != os.geteuid()
            or stat.S_IMODE(current.st_mode) != 0o600
        ):
            raise RuntimeError("unsafe_mcp_restart_barrier_marker")
        if os.read(descriptor, len(expected) + 1) != expected:
            raise RuntimeError("invalid_mcp_restart_barrier_marker")
        return True
    finally:
        os.close(descriptor)


def barrier_reached(directory: Path) -> bool:
    """Return whether the real create-group response reached the barrier."""
    return _read_marker(directory, _READY_NAME, b"ready")


def release_barrier(directory: Path, *, abort: bool = False) -> None:
    """Release the real response only after verified restart, or fail it closed."""
    _write_marker(directory, _ABORT_NAME if abort else _RELEASE_NAME, b"abort" if abort else b"ok")


def set_journey_deadline(directory: Path, deadline: float) -> None:
    """Publish the already-running acceptance deadline to the private MCP child."""
    if not isinstance(deadline, (int, float)) or not math.isfinite(deadline) or deadline <= 0:
        raise ValueError("invalid_mcp_restart_barrier_deadline")
    _write_marker(directory, _DEADLINE_NAME, f"{deadline:.9f}".encode("ascii"))


def _read_journey_deadline(directory: Path) -> float | None:
    _assert_private_directory(directory)
    path = directory / _DEADLINE_NAME
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RuntimeError("mcp_restart_barrier_deadline_unreadable") from error
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) != 0o600
    ):
        raise RuntimeError("unsafe_mcp_restart_barrier_deadline")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise RuntimeError("mcp_restart_barrier_deadline_unreadable") from error
    try:
        current = os.fstat(descriptor)
        if (
            current.st_dev != before.st_dev
            or current.st_ino != before.st_ino
            or not stat.S_ISREG(current.st_mode)
            or current.st_uid != os.geteuid()
            or stat.S_IMODE(current.st_mode) != 0o600
        ):
            raise RuntimeError("unsafe_mcp_restart_barrier_deadline")
        raw = os.read(descriptor, 64)
    finally:
        os.close(descriptor)
    try:
        deadline = float(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as error:
        raise RuntimeError("invalid_mcp_restart_barrier_deadline") from error
    if not math.isfinite(deadline) or deadline <= 0:
        raise RuntimeError("invalid_mcp_restart_barrier_deadline")
    return deadline


def install_create_response_barrier(
    mcp_utils: Any,
    directory: Path,
) -> Callable[[], None]:
    """Gate one real create response without changing its args, result, or errors."""
    _assert_private_directory(directory)
    original: Callable[..., Any] = mcp_utils.interaction_post_json

    def guarded(path: str, body: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(path, body, *args, **kwargs)
        if path != _CREATE_GROUP_PATH:
            return result
        _write_marker(directory, _READY_NAME, b"ready")
        while True:
            deadline = _read_journey_deadline(directory)
            if deadline is None:
                raise RuntimeError("mcp_restart_barrier_deadline_missing")
            if time.monotonic() >= deadline:
                raise TimeoutError("mcp_restart_barrier_deadline_exhausted")
            released = _read_marker(directory, _RELEASE_NAME, b"ok")
            aborted = _read_marker(directory, _ABORT_NAME, b"abort")
            if released and aborted:
                raise RuntimeError("mcp_restart_barrier_ambiguous_release")
            if aborted:
                raise RuntimeError("mcp_restart_barrier_aborted")
            if released:
                return result
            time.sleep(0.02)

    mcp_utils.interaction_post_json = guarded

    def restore() -> None:
        mcp_utils.interaction_post_json = original

    return restore
