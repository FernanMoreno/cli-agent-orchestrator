"""Explicit private diagnostic EOF; never evidence of native automatic shutdown."""

from __future__ import annotations

import io
import json
import os
import select
import stat
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from message_mcp_observer import _open_private_audit_file

ENABLE_ENV = "CAO_SPEC023_MCP_GRACEFUL_EOF"
REQUEST_MARKER = b"SPEC023_REQUEST_QUIESCENT_EOF\n"


class ShutdownError(RuntimeError):
    """Fixed refusal without serializing request, tool or process data."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ShutdownError("shutdown_artifact_ambiguous")
        result[key] = value
    return result


def _private_bytes(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size > limit
        ):
            raise ShutdownError("shutdown_artifact_invalid")
        return os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)


def _publish(path: Path, payload: bytes) -> None:
    # The fixed private pending name reserves this publication. Recheck after
    # reservation: another publisher may have completed between the first
    # absence check and our exclusive create. Readers only see the final name.
    if os.path.lexists(path):
        raise ShutdownError("shutdown_artifact_exists")
    pending = path.with_name("." + path.name + ".pending")
    descriptor = _open_private_audit_file(pending)
    published = False
    try:
        if os.path.lexists(path):
            raise ShutdownError("shutdown_artifact_exists")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ShutdownError("shutdown_artifact_write_failed")
            view = view[written:]
        os.fsync(descriptor)
        os.rename(pending, path)
        published = True
    finally:
        try:
            if not published:
                pending.unlink(missing_ok=True)
        finally:
            os.close(descriptor)


def _birth(pid: int) -> int:
    return int(Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[19])


class ControlledInput(io.RawIOBase):
    """Own a duplicate descriptor and let the SDK decoder observe requested EOF."""

    def __init__(self, descriptor: int, should_stop, *, on_late_input=None):
        super().__init__()
        self._descriptor = os.dup(descriptor)
        self._should_stop = should_stop
        self._on_late_input = on_late_input

    def readable(self):
        return True

    def readinto(self, buffer):
        if not buffer:
            return 0
        while not self._should_stop():
            if not select.select([self._descriptor], [], [], 0.05)[0]:
                continue
            chunk = os.read(self._descriptor, len(buffer))
            buffer[: len(chunk)] = chunk
            return len(chunk)
        if select.select([self._descriptor], [], [], 0)[0] and os.read(self._descriptor, 1):
            if self._on_late_input is not None:
                self._on_late_input()
            raise ShutdownError("shutdown_late_input")
        return 0

    def close(self):
        if not self.closed:
            os.close(self._descriptor)
        super().close()


class _Quiescence:
    def __init__(self, audit_path: Path):
        self.request_path = Path(str(audit_path) + ".eof-request")
        self.lock = threading.Lock()
        self.active = 0
        self.stopping = False
        self.failed = False

    def refuse_late_input(self):
        with self.lock:
            self.failed = True
        raise ShutdownError("shutdown_late_input")

    def should_stop(self):
        try:
            payload = _private_bytes(self.request_path, len(REQUEST_MARKER))
        except FileNotFoundError:
            return False
        except BaseException:
            self.failed = True
            raise ShutdownError("shutdown_request_invalid") from None
        with self.lock:
            if payload != REQUEST_MARKER or self.active:
                self.failed = True
                raise ShutdownError("shutdown_not_quiescent")
            self.stopping = True
            return True


@contextmanager
def controlled_stdio(server, audit_path: Path, *, stdin_fd: int = 0):
    """Scope the transport change inside the already source-validated run hook."""
    import anyio
    import fastmcp.server.mixins.transport as transport

    control = _Quiescence(audit_path)
    original_call = server.call_tool
    original_transport = transport.stdio_server
    raw = ControlledInput(stdin_fd, control.should_stop, on_late_input=control.refuse_late_input)
    reader = io.TextIOWrapper(io.BufferedReader(raw), encoding="utf-8", errors="replace")

    async def tracked_call(*args, **kwargs):
        with control.lock:
            if control.stopping:
                control.failed = True
                raise ShutdownError("shutdown_late_dispatch")
            control.active += 1
        try:
            return await original_call(*args, **kwargs)
        finally:
            with control.lock:
                control.active -= 1

    @asynccontextmanager
    async def diagnostic_transport():
        async with original_transport(stdin=anyio.wrap_file(reader)) as streams:
            yield streams

    try:
        audit = audit_path.lstat()
        _private_bytes(audit_path, 4096)
        ready = {
            "version": 1,
            "pid": os.getpid(),
            "uid": os.geteuid(),
            "birth": _birth(os.getpid()),
            "audit_device": audit.st_dev,
            "audit_inode": audit.st_ino,
        }
        server.call_tool = tracked_call
        transport.stdio_server = diagnostic_transport
        _publish(Path(str(audit_path) + ".eof-ready"), json.dumps(ready).encode())
        yield control
        if control.failed:
            raise ShutdownError("shutdown_finalization_refused")
    finally:
        server.call_tool = original_call
        transport.stdio_server = original_transport
        reader.close()


def request_shutdown(audit_path: Path, *, deadline: float) -> bool:
    """Bind child birth and pidfd before requesting EOF; observe actual exit."""
    descriptor = -1
    try:
        ready = json.loads(
            _private_bytes(Path(str(audit_path) + ".eof-ready"), 1024),
            object_pairs_hook=_unique_object,
        )
        if not isinstance(ready, dict) or set(ready) != {
            "version",
            "pid",
            "uid",
            "birth",
            "audit_device",
            "audit_inode",
        }:
            return False
        if any(type(value) is not int or value < 0 for value in ready.values()):
            return False
        audit = audit_path.lstat()
        pid = ready["pid"]
        if (
            ready["version"] != 1
            or pid <= 0
            or ready["uid"] != os.geteuid()
            or ready["audit_device"] != audit.st_dev
            or ready["audit_inode"] != audit.st_ino
            or Path(f"/proc/{pid}").stat().st_uid != os.geteuid()
            or _birth(pid) != ready["birth"]
            or time.monotonic() >= deadline
        ):
            return False
        descriptor = os.pidfd_open(pid)
        if _birth(pid) != ready["birth"] or time.monotonic() >= deadline:
            return False
        _publish(Path(str(audit_path) + ".eof-request"), REQUEST_MARKER)
        remaining = deadline - time.monotonic()
        return remaining > 0 and bool(select.select([descriptor], [], [], remaining)[0])
    except (OSError, ValueError, RuntimeError):
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)
