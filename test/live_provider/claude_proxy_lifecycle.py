"""Private, bounded transport observations; never a substitute for acceptance."""

import json
import os
import stat
import time
from pathlib import Path
from test.live_provider.claude_mcp_catalog import decode_frame

EVENTS = frozenset(
    {
        "proxy_started",
        "requested_eof",
        "eof_not_quiescent",
        "eof_late_input",
        "eof_cli_closed_early",
        "worker_requested_eof",
        "worker_mcp_closed",
        "child_spawned",
        "stdin_eof",
        "stdout_eof",
        "signal",
        "abort_keyboard_interrupt",
        "abort_system_exit",
        "abort_error",
        "cleanup_sigterm",
        "cleanup_sigkill",
        "child_wait_normal",
        "child_wait_cleanup",
        "normal_closed",
        "proxy_finished",
        "worker_close_started",
        "worker_stdin_closed",
        "worker_cli_exit",
        "worker_close_cutoff",
    }
)
STATUS_EVENTS = {"signal", "child_wait_normal", "child_wait_cleanup", "worker_cli_exit"}


def _validate(row):
    if (
        not isinstance(row, dict)
        or not isinstance(row.get("event"), str)
        or row["event"] not in EVENTS
    ):
        raise ValueError("lifecycle_event_invalid")
    fields = {"event", "at_ns"} | ({"status"} if row["event"] in STATUS_EVENTS else set())
    if set(row) != fields or type(row["at_ns"]) is not int or row["at_ns"] < 0:
        raise ValueError("lifecycle_shape_invalid")
    if "status" in row:
        status = row["status"]
        if type(status) is not int or not -64 <= status <= 255:
            raise ValueError("lifecycle_status_invalid")
        if row["event"] == "signal" and not 1 <= status <= 64:
            raise ValueError("lifecycle_signal_invalid")


class LifecycleAudit:
    def __init__(self, path, root):
        path, root = Path(path), Path(root).resolve()
        if path.parent.resolve() != root or path.is_symlink():
            raise ValueError("lifecycle_path_invalid")
        self.fd = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_APPEND, 0o600
        )
        self.count = 0

    def event(self, name, *, status=None):
        row = {"event": name, "at_ns": time.monotonic_ns()}
        if status is not None:
            row["status"] = status
        _validate(row)
        if self.count >= 64:
            raise ValueError("lifecycle_event_limit")
        self.count += 1
        data = (json.dumps(row, separators=(",", ":")) + "\n").encode()
        if os.write(self.fd, data) != len(data):
            raise ValueError("lifecycle_write_incomplete")

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def read_lifecycle(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("lifecycle_file_invalid")
        data = stream.read(16385)
    if not data or len(data) > 16384 or not data.endswith(b"\n"):
        raise ValueError("lifecycle_file_incomplete")
    lines = data.splitlines()
    if len(lines) > 64:
        raise ValueError("lifecycle_event_limit")
    rows = [decode_frame(line) for line in lines]
    previous = -1
    for row in rows:
        _validate(row)
        if row["at_ns"] < previous:
            raise ValueError("lifecycle_order_invalid")
        previous = row["at_ns"]
    return rows
