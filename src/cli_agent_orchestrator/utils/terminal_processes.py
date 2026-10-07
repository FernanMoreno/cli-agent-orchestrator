"""Linux cleanup for processes inheriting a terminal's exact tmux context.

This is lifecycle ownership, not a sandbox. A process deliberately scrubbing
its environment is not identifiable with this contract. Never signal by name
or reusable PID; pin with pidfd and recheck procfs before signalling.
"""

import os
import select
import signal
import sys
import time
from contextlib import contextmanager
from pathlib import Path


def _identity(pid, terminal_id, socket_path):
    root = Path("/proc") / str(pid)
    try:
        if root.stat().st_uid != os.getuid():
            return None
        stat = (root / "stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        environment = dict(
            part.split(b"=", 1)
            for part in (root / "environ").read_bytes().split(b"\0")
            if b"=" in part
        )
        if environment.get(b"CAO_TERMINAL_ID") != terminal_id.encode():
            return None
        inherited_socket = environment.get(b"TMUX", b"").rsplit(b",", 2)[0]
        if inherited_socket != os.fsencode(socket_path):
            return None
        return stat[19]  # field 22, after pid and parenthesized comm
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None


def _wait_all(handles, timeout):
    pending = set(handles)
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        exited, _, _ = select.select(list(pending), [], [], max(0, deadline - time.monotonic()))
        pending.difference_update(exited)
    return pending


def _pin_processes(terminal_id, socket_path):
    handles = []
    if not sys.platform.startswith("linux") or not socket_path:
        return handles
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            pid = int(entry.name)
            identity = _identity(pid, terminal_id, socket_path)
            if identity is None:
                continue
            try:
                fd = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            if _identity(pid, terminal_id, socket_path) != identity:
                os.close(fd)
                continue
            handles.append(fd)
        return handles
    except BaseException:
        for fd in handles:
            os.close(fd)
        raise


def _stop_handles(handles):
    for fd in handles:
        try:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
        except ProcessLookupError:
            pass
    pending = _wait_all(handles, 0.25)
    for fd in pending:
        try:
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if _wait_all(pending, 1):
        raise RuntimeError("terminal process exit could not be confirmed")


@contextmanager
def pinned_terminal_cleanup(terminal_id, socket_path):
    """Pin old processes before respawn; never signal the replacement shell."""
    handles = _pin_processes(terminal_id, socket_path)
    try:
        yield
    finally:
        try:
            _stop_handles(handles)
        finally:
            for fd in handles:
                os.close(fd)


def stop_terminal_processes(terminal_id, socket_path):
    """Stop only same-user inherited processes; prove exit within a fixed bound."""
    deadline = time.monotonic() + 3
    while True:
        handles = _pin_processes(terminal_id, socket_path)
        if not handles:
            return
        try:
            _stop_handles(handles)
        finally:
            for fd in handles:
                os.close(fd)
        if time.monotonic() >= deadline:
            raise RuntimeError("terminal process cleanup exceeded its deadline")
