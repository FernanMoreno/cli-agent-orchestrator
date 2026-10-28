"""Opt-in diagnostic EOF intent; independent of authentication and permissions."""

import json
import os
import re
import stat
import time
from pathlib import Path
from test.live_provider.claude_mcp_catalog import decode_frame
from test.live_provider.claude_proxy_lifecycle import LifecycleAudit, read_lifecycle


def _token(token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", token):
        raise ValueError("eof_token_invalid")


def request_eof(path, token):
    """Publish one complete request atomically, without replacing an incumbent."""
    _token(token)
    path = Path(path)
    temporary = path.with_suffix(".preparing")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump({"schema": "caos.mcp-eof-request.v1", "token": token}, out)
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink()


class RequestedEof:
    def __init__(self, path, token, root):
        _token(token)
        self.path = Path(path)
        if (
            self.path.parent.resolve() != Path(root).resolve()
            or self.path.exists()
            or self.path.is_symlink()
        ):
            raise ValueError("eof_request_path_invalid")
        self.token = token
        self.pending = {False: set(), True: set()}

    def requested(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return False
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError("eof_request_file_invalid")
            data = stream.read(1025)
        if not data or len(data) > 1024:
            raise ValueError("eof_request_size_invalid")
        value = decode_frame(data)
        if value != {"schema": "caos.mcp-eof-request.v1", "token": self.token}:
            raise ValueError("eof_request_invalid")
        return True

    def observe(self, frame, *, outbound):
        identity = frame.get("id")
        if "method" in frame and "id" not in frame:
            return
        if "id" not in frame:
            raise ValueError("eof_rpc_frame_invalid")
        if type(identity) not in (str, int):
            raise ValueError("eof_rpc_identity_invalid")
        if "method" in frame:
            pending = self.pending[outbound]
            if identity in pending or len(pending) >= 128:
                raise ValueError("eof_rpc_identity_invalid")
            pending.add(identity)
        else:
            pending = self.pending[not outbound]
            if identity not in pending:
                raise ValueError("eof_rpc_response_unbound")
            pending.remove(identity)

    def quiet(self):
        return not any(self.pending.values())


def _normal_witness(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("eof_witness_file_invalid")
        data = stream.read(1025)
    if len(data) > 1024:
        raise ValueError("eof_witness_size_invalid")
    value = decode_frame(data)
    if set(value) != {"initialize", "tools/list", "tools/call", "closed_cleanly"}:
        raise ValueError("eof_witness_invalid")
    if any(
        type(value[k]) is not int or value[k] < 0
        for k in ["initialize", "tools/list", "tools/call"]
    ):
        raise ValueError("eof_witness_invalid")
    if type(value["closed_cleanly"]) is not bool or value["tools/call"] != 0:
        raise ValueError("eof_witness_invalid")
    return value


def _normal_ledger(path):
    rows = read_lifecycle(path)
    names = [r["event"] for r in rows]
    expected = [
        "proxy_started",
        "child_spawned",
        "requested_eof",
        "stdout_eof",
        "child_wait_normal",
        "normal_closed",
        "child_wait_cleanup",
        "proxy_finished",
    ]
    if names != expected[: len(names)]:
        raise ValueError("eof_transport_not_normal")
    for row in rows:
        if row["event"] in {"child_wait_normal", "child_wait_cleanup"} and row["status"] != 0:
            raise ValueError("eof_child_exit_not_zero")
    return names == expected


def _single_launch(root):
    fd = os.open(root / "eof-proxy-launches", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if (
            stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600
            or stream.read(64) != b"proxy_launch\n"
        ):
            raise ValueError("eof_proxy_restart_observed")


def close_mcp_then_cli(process, root, token, *, timeout=6):
    """Request genuine MCP EOF, then close CLI input under one original budget."""
    if type(timeout) not in (int, float) or not 0 < timeout <= 6:
        raise ValueError("eof_shutdown_budget_invalid")
    root = Path(root)
    deadline = time.monotonic() + timeout
    witness_path, ledger = root / "mcp-witness.json", root / "proxy-lifecycle.jsonl"
    with LifecycleAudit(root / "worker-lifecycle.jsonl", root) as audit:
        audit.event("worker_close_started")
        request_eof(root / "mcp-eof-request.json", token)
        audit.event("worker_requested_eof")
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("eof_shutdown_deadline")
            witness = _normal_witness(witness_path)
            if witness["closed_cleanly"] and _normal_ledger(ledger):
                break
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        _single_launch(root)
        audit.event("worker_mcp_closed")
        process.stdin.close()
        audit.event("worker_stdin_closed")
        status = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        audit.event("worker_cli_exit", status=status)
        if status != 0 or time.monotonic() > deadline:
            raise ValueError("eof_cli_shutdown_failed")
        if not _normal_ledger(ledger) or not _normal_witness(witness_path)["closed_cleanly"]:
            raise ValueError("eof_final_observation_invalid")
        _single_launch(root)
        audit.event("worker_close_cutoff")
    return witness
