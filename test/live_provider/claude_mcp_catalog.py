"""Model-free Claude streaming controls; caller owns sandbox/process retirement.

Wire contract: anthropics/claude-agent-sdk-python, _internal/query.py.
Only initialize and mcp_status are sent. No user turns or callback permissions.
This module does not launch a CLI, read credentials or activate a diagnostic.
"""

from __future__ import annotations

import json
import os
import selectors
import time


def control_request(method: str, request_id: str) -> dict:
    if method not in {"initialize", "mcp_status"}:
        raise ValueError("catalog_only_method_required")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("invalid_control_id")
    request = {"subtype": method}
    if method == "initialize":
        request["hooks"] = None
    return {"type": "control_request", "request_id": request_id, "request": request}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_control_key")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("invalid_control_constant")


def decode_frame(raw: bytes) -> dict:
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("invalid_control_json") from error
    if not isinstance(value, dict):
        raise ValueError("invalid_control_frame")
    return value


def control_result(frame: dict, request_id: str) -> dict | None:
    # The CLI can emit its startup inventory before a control reply. Never
    # mistake that informational event for a successful correlated response.
    if frame.get("type") == "system" and frame.get("subtype") == "init":
        return None
    if frame.get("type") != "control_response":
        raise ValueError("unexpected_native_catalog_event")
    response = frame.get("response")
    if not isinstance(response, dict) or response.get("request_id") != request_id:
        raise ValueError("uncorrelated_native_control")
    if response.get("subtype") != "success" or not isinstance(response.get("response"), dict):
        raise ValueError("native_control_failed")
    return response["response"]


def tool_names(status: dict, server: str) -> frozenset[str]:
    entries = status.get("mcpServers")
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError("native_catalog_server_scope")
    entry = entries[0]
    if not isinstance(entry, dict) or entry.get("name") != server:
        raise ValueError("native_catalog_server_scope")
    if entry.get("status") != "connected":
        raise ValueError("native_catalog_not_connected")
    tools = entry.get("tools")
    if not isinstance(tools, list):
        raise ValueError("native_catalog_tools_missing")
    names = []
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else None
        if not isinstance(name, str) or not name or len(name) > 256:
            raise ValueError("native_catalog_tool_shape")
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError("native_catalog_duplicate_tool")
    return frozenset(names)


class ControlClient:
    """Bounded control I/O over an already-owned process; never starts a turn.

    Closing this reader closes its selector only. The launcher must close stdin,
    retire the exact owned process group and verify physical exit in a finally.
    """

    def __init__(self, process, *, timeout=180, max_bytes=4 * 1024 * 1024):
        if not 0 < timeout <= 180 or not 0 < max_bytes <= 4 * 1024 * 1024:
            raise ValueError("invalid_control_limits")
        self.process = process
        self.deadline = time.monotonic() + timeout
        self.max_bytes = max_bytes
        self.received = 0
        self.buffer = b""
        self.serial = 0
        self.selector = selectors.DefaultSelector()
        self.selector.register(process.stdout, selectors.EVENT_READ)

    def rpc(self, method: str) -> dict:
        request_id = f"catalog-{self.serial + 1}"
        request = control_request(method, request_id)
        if time.monotonic() >= self.deadline:
            raise TimeoutError("native_control_deadline")
        self.serial += 1
        self.process.stdin.write((json.dumps(request) + "\n").encode())
        self.process.stdin.flush()
        while time.monotonic() < self.deadline:
            while b"\n" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\n", 1)
                if not raw.strip():
                    continue
                result = control_result(decode_frame(raw), request_id)
                if result is not None:
                    return result
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise TimeoutError("native_control_deadline")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise ValueError("native_control_eof")
            self.received += len(chunk)
            if self.received > self.max_bytes:
                raise ValueError("native_control_input_limit")
            self.buffer += chunk
        raise TimeoutError("native_control_deadline")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.selector.close()
