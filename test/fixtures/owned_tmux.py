"""Bounded helpers for a fixture-owned private tmux server."""

from __future__ import annotations

import os
import re
import select
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def ensure_private_directory(path: Path, *, must_exist: bool) -> None:
    """Require or create one absolute, owner-only directory without following links."""
    if not path.is_absolute() or ".." in path.parts:
        raise OSError("private_directory_invalid")
    if must_exist:
        try:
            metadata = path.lstat()
        except OSError:
            raise OSError("private_directory_invalid") from None
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise OSError("private_directory_invalid")
    else:
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
        path.chmod(0o700)


def create_private_tmux_tmpdir() -> Path:
    """Create an owner-only short root for one exact tmux socket."""
    root: Path | None = None
    try:
        root = Path(tempfile.mkdtemp(prefix="s023-", dir="/tmp"))
        root.chmod(0o700)
        ensure_private_directory(root, must_exist=True)
        socket_path = root / f"tmux-{os.getuid()}" / "default"
        if len(os.fsencode(socket_path)) >= 100:
            raise OSError("private_tmux_socket_path_too_long")
        return root
    except OSError:
        if root is not None:
            try:
                shutil.rmtree(root)
            except OSError:
                pass
        raise OSError("private_tmux_root_unavailable") from None


def _read_process_identity(pid: int) -> tuple[int, str] | None:
    """Read an owned process UID and Linux start time without exposing argv."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        return None
    proc = Path("/proc") / str(pid)
    try:
        metadata = proc.stat()
        if metadata.st_uid != os.geteuid():
            return None
        status_lines = (proc / "status").read_text(encoding="ascii").splitlines()
        uid_line = next(line for line in status_lines if line.startswith("Uid:"))
        effective_uid = int(uid_line.split()[2])
        stat_line = (proc / "stat").read_text(encoding="ascii")
        closing_paren = stat_line.rfind(")")
        if closing_paren < 0:
            return None
        fields = stat_line[closing_paren + 1 :].split()
        starttime = fields[19]
        if effective_uid != os.geteuid() or not starttime.isdecimal():
            return None
        return effective_uid, starttime
    except (OSError, StopIteration, ValueError, IndexError):
        return None


def _pidfd_exited(pidfd: int) -> bool:
    try:
        readable, _, _ = select.select([pidfd], [], [], 0)
        return bool(readable)
    except (OSError, ValueError):
        return False


def _private_tmux_command(
    runner: Any,
    *,
    tmux_binary: str,
    socket_path: Path,
    root: Path,
    home: Path,
    command: tuple[str, ...],
) -> subprocess.CompletedProcess[str] | None:
    try:
        result = runner(
            [tmux_binary, "-S", str(socket_path), *command],
            cwd=home,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(home),
                "TMUX": "",
                "TMUX_TMPDIR": str(root),
                "TMPDIR": str(root),
            },
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except BaseException:
        return None
    if result.returncode != 0 or not isinstance(result.stdout, str):
        return None
    return result


def _tmux_socket_identity(socket_path: Path) -> tuple[int, int] | None:
    try:
        metadata = socket_path.lstat()
    except OSError:
        return None
    if (
        not stat.S_ISSOCK(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
    ):
        return None
    return metadata.st_dev, metadata.st_ino


def cleanup_private_tmux_root(
    root: Path | None,
    home: Path,
    tmux_module: Any,
    *,
    owner_stopped: bool,
    command_runner: Any = None,
) -> bool:
    """Remove only this fixture's empty tmux server after its owner has stopped."""
    if root is None:
        return True
    if not owner_stopped:
        return False
    socket_path = root / f"tmux-{os.getuid()}" / "default"
    pidfd = -1
    try:
        ensure_private_directory(root, must_exist=True)
        verdict = tmux_module._tmux_server_liveness(str(socket_path))
        if verdict not in {tmux_module._SERVER_ABSENT, tmux_module._SERVER_ALIVE}:
            return False
        try:
            socket_metadata = socket_path.lstat()
        except FileNotFoundError:
            socket_metadata = None
        if socket_metadata is not None and (
            not stat.S_ISSOCK(socket_metadata.st_mode) or socket_metadata.st_uid != os.geteuid()
        ):
            return False
        if verdict == tmux_module._SERVER_ALIVE:
            if socket_metadata is None:
                return False
            socket_directory_metadata = socket_path.parent.lstat()
            if (
                not stat.S_ISDIR(socket_directory_metadata.st_mode)
                or stat.S_ISLNK(socket_directory_metadata.st_mode)
                or socket_directory_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(socket_directory_metadata.st_mode) != 0o700
            ):
                return False
            tmux_binary = shutil.which("tmux")
            if tmux_binary is None:
                return False
            runner = command_runner or subprocess.run
            socket_identity = (socket_metadata.st_dev, socket_metadata.st_ino)
            pid_result = _private_tmux_command(
                runner,
                tmux_binary=tmux_binary,
                socket_path=socket_path,
                root=root,
                home=home,
                command=("display-message", "-p", "#{pid}"),
            )
            if pid_result is None or re.fullmatch(r"[1-9][0-9]*\n?", pid_result.stdout) is None:
                return False
            server_pid = int(pid_result.stdout.strip())
            process_identity = _read_process_identity(server_pid)
            if process_identity is None or process_identity[0] != os.geteuid():
                return False
            try:
                pidfd = os.pidfd_open(server_pid, 0)
            except (AttributeError, OSError):
                return False
            if _read_process_identity(server_pid) != process_identity:
                return False
            sessions = _private_tmux_command(
                runner,
                tmux_binary=tmux_binary,
                socket_path=socket_path,
                root=root,
                home=home,
                command=("list-sessions", "-F", "#{session_id}"),
            )
            if sessions is None or sessions.stdout.strip():
                return False
            if (
                _tmux_socket_identity(socket_path) != socket_identity
                or tmux_module._tmux_server_liveness(str(socket_path)) != tmux_module._SERVER_ALIVE
                or _pidfd_exited(pidfd)
                or _read_process_identity(server_pid) != process_identity
            ):
                return False
            killed = _private_tmux_command(
                runner,
                tmux_binary=tmux_binary,
                socket_path=socket_path,
                root=root,
                home=home,
                command=("kill-server",),
            )
            if killed is None:
                return False
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not _pidfd_exited(pidfd):
                if _read_process_identity(server_pid) != process_identity:
                    return False
                time.sleep(0.05)
            if not _pidfd_exited(pidfd):
                return False
            while time.monotonic() < deadline:
                verdict = tmux_module._tmux_server_liveness(str(socket_path))
                if verdict == tmux_module._SERVER_ABSENT:
                    break
                if verdict != tmux_module._SERVER_ALIVE:
                    return False
                time.sleep(0.1)
            if verdict != tmux_module._SERVER_ABSENT:
                return False
            if _tmux_socket_identity(socket_path) != socket_identity:
                return False
        if socket_metadata is not None:
            if _tmux_socket_identity(socket_path) != (
                socket_metadata.st_dev,
                socket_metadata.st_ino,
            ):
                return False
            socket_path.unlink()
        shutil.rmtree(root)
        return not root.exists()
    except BaseException:
        return False
    finally:
        if pidfd >= 0:
            try:
                os.close(pidfd)
            except OSError:
                pass
