"""Diagnostic-only, bounded metadata at actual physical-EOF guard seams.

No stdout, packet contents, IDs, signal substitution or synthetic EOF. Failed
observations are unavailable evidence and never change the wrapped operation.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path
from test.live_provider.claude_mcp_catalog import decode_frame
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

MAX_ROWS = 128
MAX_BYTES = 128 * 1024
EVENTS = Literal[
    "physical_eof",
    "local_input_closed",
    "stop_signal",
    "exit_requested",
    "lifespan_entered",
    "lifespan_ready",
    "lifespan_returned",
    "lifespan_raised",
    "server_main_returned",
    "server_main_raised",
]
UNSAFE = frozenset(
    {
        "wake_failed",
        "shutdown_deadline",
        "invalid_frame",
        "duplicate_rpc_id",
        "unobserved_notification",
        "unbound_rpc_response",
        "frame_observer_failed",
        "partial_input_frame",
        "input_io_failed",
        "input_close_failed",
        "output_io_failed",
    }
)


class GuardSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    physical_eof: StrictBool
    local_input_closed: StrictBool = False
    pending_client: StrictInt = Field(ge=0)
    pending_server: StrictInt = Field(ge=0)
    input_tail_bytes: StrictInt = Field(ge=0)
    output_tail_bytes: StrictInt = Field(ge=0)
    unflushed_replies: StrictInt = Field(ge=0)
    output_busy: StrictInt = Field(ge=0)
    input_frames: StrictInt = Field(ge=0)
    output_frames: StrictInt = Field(ge=0)
    first_signal: Literal[2, 15] | None
    signal_count: StrictInt = Field(ge=0)
    unsafe: str | None

    @model_validator(mode="after")
    def fixed_failure(self):
        if self.unsafe is not None and self.unsafe not in UNSAFE | {"other"}:
            raise ValueError("transition_failure_code_invalid")
        return self


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event: EVENTS
    at_ns: StrictInt = Field(ge=0)
    state: GuardSnapshot | None
    status: StrictInt | None = Field(default=None, ge=-255, le=255)

    @model_validator(mode="after")
    def status_contract(self):
        needed = self.event in {"stop_signal", "exit_requested"}
        if ("status" in self.model_fields_set) != needed or (needed and self.status is None):
            raise ValueError("transition_status_invalid")
        if self.event == "stop_signal" and self.status not in {2, 15}:
            raise ValueError("transition_signal_invalid")
        return self


def _regular_private(fd: int) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("transition_file_invalid")


def read_transitions(path: Path) -> list[dict[str, Any]]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _regular_private(fd)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_BYTES or (raw and not raw.endswith(b"\n")):
        raise ValueError("transition_bounds_invalid")
    lines = raw.splitlines()
    if len(lines) > MAX_ROWS:
        raise ValueError("transition_bounds_invalid")
    try:
        return [
            Transition.model_validate(decode_frame(line)).model_dump(exclude_unset=True)
            for line in lines
        ]
    except (ValueError, TypeError):
        raise ValueError("transition_evidence_invalid") from None


class GuardTransitions:
    def __init__(self, guard: Any, path: Path, root: Path, *, max_rows: int = MAX_ROWS):
        if not 1 <= max_rows <= MAX_ROWS:
            raise ValueError("transition_limit_invalid")
        root = Path(root)
        if root.is_symlink() or path.is_symlink() or path.parent.resolve() != root.resolve():
            raise ValueError("transition_path_invalid")
        info = root.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("transition_root_invalid")
        self.guard, self.path = guard, path
        self.max_rows = max_rows
        self.normal_limit = max_rows - 1 if max_rows > 1 else 1
        self.lock = threading.RLock()
        self.normal_rows = self.normal_bytes = 0
        self.exit_recorded = self.failed = False
        self.fd = -1

    def _append(self, data: bytes) -> bool:
        try:
            return os.write(self.fd, data) == len(data)
        except OSError:
            return False

    def _state(self) -> dict[str, Any] | None:
        if not self.guard.state.lock.acquire(blocking=False):
            return None
        try:
            original = self.guard.state.snapshot()
            value = {key: original[key] for key in GuardSnapshot.model_fields}
            unsafe = value["unsafe"]
            value["unsafe"] = unsafe if unsafe is None or unsafe in UNSAFE else "other"
            return GuardSnapshot.model_validate(value).model_dump()
        finally:
            self.guard.state.lock.release()

    def event(self, name: str, *, status: int | None = None) -> bool:
        # A fatal signal can interrupt this same thread inside the ordinary
        # journal writer. Reserve one row and avoid all locks/snapshots/models.
        if name == "exit_requested":
            try:
                if self.exit_recorded or (self.max_rows == 1 and self.normal_rows):
                    self.failed = True
                    return False
                self.exit_recorded = True
                if type(status) is not int or not -255 <= status <= 255:
                    self.failed = True
                    return False
                data = (
                    b'{"event":"exit_requested","at_ns":'
                    + str(time.monotonic_ns()).encode("ascii")
                    + b',"state":null,"status":'
                    + str(status).encode("ascii")
                    + b"}\n"
                )
                ok = self.fd >= 0 and self._append(data)
                self.failed |= not ok
                return ok
            except BaseException:
                self.failed = True
                return False
        if not self.lock.acquire(blocking=False):
            self.failed = True
            return False
        try:
            if self.fd < 0 or self.normal_rows >= self.normal_limit:
                self.failed = True
                return False
            row = {"event": name, "at_ns": time.monotonic_ns(), "state": self._state()}
            if name == "stop_signal":
                row["status"] = status
            data = (
                Transition.model_validate(row).model_dump_json(exclude_unset=True) + "\n"
            ).encode()
            if self.normal_bytes + len(data) > MAX_BYTES - 512:
                self.failed = True
                return False
            self.normal_rows += 1
            self.normal_bytes += len(data)
            ok = self._append(data)
            self.failed |= not ok
            return ok
        except Exception:
            self.failed = True
            return False
        finally:
            self.lock.release()

    def __enter__(self) -> GuardTransitions:
        if self.fd >= 0:
            raise ValueError("transition_already_active")
        self.fd = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_APPEND,
            0o600,
        )
        self.original_eof = self.guard.state.input_eof
        self.original_stop = self.guard.state.request_stop
        self.original_local_close = self.guard.state.mark_local_input_closed
        self.original_exit = os._exit

        def eof():
            try:
                return self.original_eof()
            finally:
                self.event("physical_eof")

        def stop(signum):
            try:
                return self.original_stop(signum)
            finally:
                self.event("stop_signal", status=signum)

        def local_close():
            try:
                return self.original_local_close()
            finally:
                self.event("local_input_closed")

        def exiting(status):
            try:
                self.event("exit_requested", status=status)
            finally:
                self.original_exit(status)

        self.guard.state.input_eof = eof
        self.guard.state.request_stop = stop
        self.guard.state.mark_local_input_closed = local_close
        os._exit = exiting
        return self

    def __exit__(self, *_):
        self.guard.state.input_eof = self.original_eof
        self.guard.state.request_stop = self.original_stop
        self.guard.state.mark_local_input_closed = self.original_local_close
        os._exit = self.original_exit
        fd, self.fd = self.fd, -1
        if fd >= 0:
            os.close(fd)


def observe_owned_identity(identity: dict[str, Any]) -> dict[str, Any]:
    """Observe birth/UID and real kernel zombie status; never reap a process."""
    if (
        any(type(identity.get(k)) is not int or identity[k] <= 0 for k in ("pid", "birth"))
        or type(identity.get("uid")) is not int
        or identity["uid"] != os.geteuid()
    ):
        raise ValueError("transition_owned_identity_invalid")
    proc = Path("/proc") / str(identity["pid"])
    try:
        uid = proc.stat().st_uid
        fields = (proc / "stat").read_text().rpartition(")")[2].split()
        if int(fields[19]) != identity["birth"]:
            return {"state": "absent"}
        if uid != identity["uid"]:
            return {"state": "unobserved"}
        if fields[0] == "Z":
            wait_status = int(fields[49])
            return {
                "state": "zombie",
                "kernel_wait_status": wait_status,
                "exit_status": os.waitstatus_to_exitcode(wait_status),
            }
        return {"state": "alive"}
    except (FileNotFoundError, ProcessLookupError):
        return {"state": "absent"}
    except (OSError, ValueError, IndexError):
        return {"state": "unobserved"}
