"""Opt-in byte-preserving stdio proxy with correlated MCP dispatch counters."""

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from test.live_provider.claude_mcp_catalog import decode_frame
from test.live_provider.claude_proxy_lifecycle import LifecycleAudit
from test.live_provider.claude_requested_eof import RequestedEof


class McpWitness:
    def __init__(self):
        self.pending = {}
        self.counts = {"initialize": 0, "tools/list": 0, "tools/call": 0, "closed_cleanly": False}

    def frame(self, raw, *, outbound):
        frame = decode_frame(raw)
        identity = frame.get("id")
        valid_id = type(identity) in (str, int)
        if not outbound:
            method = frame.get("method")
            if method == "tools/call":
                self.counts[method] += 1
            if method in {"initialize", "tools/list"}:
                if not valid_id or identity in self.pending or len(self.pending) >= 128:
                    raise ValueError("mcp_request_identity_invalid")
                self.pending[identity] = method
        elif "method" not in frame and valid_id and identity in self.pending:
            method = self.pending.pop(identity)
            result = frame.get("result")
            if "error" in frame or not isinstance(result, dict):
                raise ValueError("mcp_observation_failed")
            if method == "tools/list" and not isinstance(result.get("tools"), list):
                raise ValueError("mcp_list_result_invalid")
            if method == "initialize" and not isinstance(result.get("serverInfo"), dict):
                raise ValueError("mcp_initialize_result_invalid")
            self.counts[method] += 1


def _write(path, counts):
    temporary = path.with_suffix(".pending")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(counts, out)
    os.replace(temporary, path)


def main():
    if os.environ.get("CAO_SPEC025_DENIAL_PROXY") != "1":
        raise ValueError("denial_proxy_not_enabled")
    path = Path(os.environ["CAO_SPEC025_DENIAL_WITNESS"])
    root = Path(os.environ["CAO_SPEC025_DENIAL_ROOT"]).resolve()
    if path.parent.resolve() != root or path.is_symlink():
        raise ValueError("denial_witness_path_invalid")
    if os.environ.get("CAO_SPEC025_DENIAL_EOF_FILE"):
        launch_fd = os.open(
            root / "eof-proxy-launches",
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.write(launch_fd, b"proxy_launch\n")
        finally:
            os.close(launch_fd)
    if path.exists():
        raise ValueError("denial_witness_path_invalid")
    if not sys.argv[1:]:
        raise ValueError("denial_proxy_command_missing")
    eof_path = os.environ.get("CAO_SPEC025_DENIAL_EOF_FILE")
    eof_token = os.environ.get("CAO_SPEC025_DENIAL_EOF_TOKEN")
    if bool(eof_path) != bool(eof_token):
        raise ValueError("eof_configuration_incomplete")
    eof_control = RequestedEof(eof_path, eof_token, root) if eof_path else None
    eof_requested = False
    cli_eof = False
    witness = McpWitness()
    _write(path, witness.counts)

    lifecycle_path = os.environ.get("CAO_SPEC025_DENIAL_LIFECYCLE")
    audit = LifecycleAudit(lifecycle_path, root) if lifecycle_path else None

    def event(name, **fields):
        if audit is not None:
            audit.event(name, **fields)

    def stopped(signum, frame):
        event("signal", status=signum)
        if signum == signal.SIGINT:
            signal.default_int_handler(signum, frame)
        raise SystemExit(1)

    signal.signal(signal.SIGTERM, stopped)
    signal.signal(signal.SIGINT, stopped)
    child = None
    selector = None
    buffers = {"request": b"", "response": b""}
    deadline = time.monotonic() + 180
    received = 0
    try:
        event("proxy_started")
        child = subprocess.Popen(
            sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0
        )
        event("child_spawned")
        selector = selectors.DefaultSelector()
        selector.register(sys.stdin.buffer, selectors.EVENT_READ, "request")
        selector.register(child.stdout, selectors.EVENT_READ, "response")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("denial_proxy_deadline")
            for key, _ in selector.select(min(remaining, 1)):
                direction = key.data
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    event("stdin_eof" if direction == "request" else "stdout_eof")
                    selector.unregister(key.fileobj)
                    if buffers[direction]:
                        raise ValueError("denial_proxy_partial_frame")
                    if direction == "request":
                        cli_eof = True
                        if eof_requested:
                            event("eof_cli_closed_early")
                            raise ValueError("eof_cli_closed_early")
                        child.stdin.close()
                    if direction == "response" and eof_requested:
                        if selector.select(0):
                            event("eof_late_input")
                            raise ValueError("eof_late_input")
                        selector.unregister(sys.stdin.buffer)
                    continue
                if direction == "request" and eof_requested:
                    event("eof_late_input")
                    raise ValueError("eof_late_input")
                received += len(data)
                if received > 4 * 1024 * 1024:
                    raise ValueError("denial_proxy_input_limit")
                buffers[direction] += data
                while b"\n" in buffers[direction]:
                    line, buffers[direction] = buffers[direction].split(b"\n", 1)
                    if line.strip():
                        if eof_control is not None:
                            eof_control.observe(
                                decode_frame(line), outbound=direction == "response"
                            )
                        witness.frame(line, outbound=direction == "response")
                        _write(path, witness.counts)
                    stream = child.stdin if direction == "request" else sys.stdout.buffer
                    stream.write(line + b"\n")
                    stream.flush()
            if eof_control is not None and not eof_requested and eof_control.requested():
                if cli_eof:
                    event("eof_cli_closed_early")
                    raise ValueError("eof_cli_closed_early")
                if (
                    not eof_control.quiet()
                    or witness.pending
                    or any(buffers.values())
                    or witness.counts["tools/call"]
                ):
                    event("eof_not_quiescent")
                    raise ValueError("eof_not_quiescent")
                if selector.select(0):
                    continue
                child.stdin.close()
                eof_requested = True
                event("requested_eof")
        if eof_control is not None and not eof_requested:
            raise ValueError("eof_request_not_observed")
        status = child.wait(timeout=max(0.1, deadline - time.monotonic()))
        event("child_wait_normal", status=status)
        if eof_control is not None and not eof_control.quiet():
            event("eof_not_quiescent")
            raise ValueError("eof_unsettled_rpc")
        if status == 0 and not witness.pending:
            witness.counts["closed_cleanly"] = True
            _write(path, witness.counts)
            event("normal_closed")
        return status
    except BaseException as error:
        event(
            "abort_keyboard_interrupt"
            if isinstance(error, KeyboardInterrupt)
            else "abort_system_exit" if isinstance(error, SystemExit) else "abort_error"
        )
        raise
    finally:
        try:
            if selector is not None:
                selector.close()
            if child is not None:
                if child.poll() is None:
                    event("cleanup_sigterm")
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        event("cleanup_sigkill")
                        child.kill()
                        child.wait(timeout=5)
                event("child_wait_cleanup", status=child.wait(timeout=5))
                for stream in [child.stdin, child.stdout]:
                    if stream and not stream.closed:
                        stream.close()
            event("proxy_finished")
        finally:
            if audit is not None:
                audit.close()


if __name__ == "__main__":
    raise SystemExit(main())
