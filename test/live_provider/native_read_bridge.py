"""Finite test-only read bridge to one owned real API; never a fake Backplane."""

import base64
import contextlib
import hashlib
import http.server
import json
import os
import re
import socket
import socketserver
import stat
import struct
import threading
from pathlib import Path

import requests

MAX_BODY = 65536
NATIVE_SOCKET = "/tmp/cao-read-bridge.sock"
MCP_NAME = "mcp__cao-mcp-server__list_siblings"


def allowed_read_path(method, path, terminal_id):
    return (
        method == "GET"
        and isinstance(path, str)
        and isinstance(terminal_id, str)
        and re.fullmatch(r"[0-9a-f]{8}", terminal_id) is not None
        and path in {f"/terminals/{terminal_id}", f"/terminals/{terminal_id}/siblings"}
    )


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def native_operation_result(body, call_id, mode):
    """Read only a single correlated native tool_result; never prose success."""
    found = []
    for message in body.get("messages", []):
        for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                if message.get("role") != "user":
                    raise ValueError("tool_result_role")
                found.append(block)
    if not found:
        return None
    if (
        len(found) != 1
        or found[0].get("tool_use_id") != call_id
        or found[0].get("is_error", False) is not False
    ):
        raise ValueError("tool_result_identity_or_native_error")
    content = found[0].get("content")
    if isinstance(content, list):
        if not content or any(
            not isinstance(x, dict) or x.get("type") != "text" or not isinstance(x.get("text"), str)
            for x in content
        ):
            raise ValueError("tool_result_content")
        content = "\n".join(x["text"] for x in content)
    if not isinstance(content, str) or len(content.encode()) > MAX_BODY:
        raise ValueError("tool_result_size")
    result = json.loads(content, object_pairs_hook=_pairs)
    if not isinstance(result, dict):
        raise ValueError("tool_result_object")
    if mode == "authorized":
        if result.get("success") is not True or result.get("siblings") != []:
            raise ValueError("expected_empty_authorized_discovery")
    elif mode == "denied":
        if (
            result.get("success") is not False
            or not isinstance(result.get("error"), str)
            or "is not granted the 'discovery' tool" not in result["error"]
        ):
            raise ValueError("expected_discovery_marker_denial")
    else:
        raise ValueError("unknown_operation_mode")
    return result


class OwnedReadBridge:
    """One server thread, eight admitted reads maximum, immutable owned API URL."""

    def __init__(self, path, api_url):
        from urllib.parse import urlsplit

        parsed = urlsplit(api_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("owned_loopback_api_required")
        self.path = Path(path)
        parent = self.path.parent.lstat()
        if (
            not self.path.is_absolute()
            or len(os.fsencode(self.path)) >= 108
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
        ):
            raise ValueError("private_short_socket_path_required")
        self.api_url = api_url
        self.terminal_id = None
        self.rows = []
        self.attempts = []
        self.thread = None
        self.server = None

    def bind_caller(self, terminal_id):
        if not re.fullmatch(r"[0-9a-f]{8}", terminal_id) or self.terminal_id not in (
            None,
            terminal_id,
        ):
            raise ValueError("caller_binding_invalid")
        self.terminal_id = terminal_id

    def forward(self, request):
        if len(self.attempts) >= 8:
            raise ValueError("read_bridge_budget")
        admitted = (
            isinstance(request, dict)
            and set(request) == {"method", "path"}
            and allowed_read_path(request["method"], request["path"], self.terminal_id)
        )
        self.attempts.append({"admitted": admitted})
        if not admitted:
            return {
                "status": 403,
                "body": base64.b64encode(b'{"detail":"bridge_scope_denied"}').decode(),
            }
        with requests.Session() as client:
            client.trust_env = False
            with client.get(
                self.api_url + request["path"], timeout=5, allow_redirects=False, stream=True
            ) as response:
                body = response.raw.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise ValueError("read_bridge_response_limit")
                status = response.status_code
        self.rows.append(
            {
                "method": "GET",
                "path": request["path"],
                "status": status,
                "body_sha256": hashlib.sha256(body).hexdigest(),
            }
        )
        return {"status": status, "body": base64.b64encode(body).decode()}

    def __enter__(self):
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(2)
                peer = struct.unpack(
                    "3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                )
                if peer[1] != os.getuid():
                    return
                raw = self.rfile.readline(4097)
                if len(raw) > 4096 or not raw.endswith(b"\n"):
                    return
                try:
                    response = owner.forward(json.loads(raw, object_pairs_hook=_pairs))
                except Exception:
                    response = {
                        "status": 502,
                        "body": base64.b64encode(b'{"detail":"bridge_unavailable"}').decode(),
                    }
                self.wfile.write(json.dumps(response).encode() + b"\n")

        self.server = socketserver.UnixStreamServer(str(self.path), Handler)
        try:
            self.path.chmod(0o600)
            self.identity = (self.path.stat().st_dev, self.path.stat().st_ino)
            self.thread = threading.Thread(
                target=lambda: self.server.serve_forever(poll_interval=0.05),
                name="native-owned-read",
            )
            self.thread.start()
        except BaseException:
            self.server.server_close()
            if self.path.exists():
                self.path.unlink()
            raise
        return self

    def __exit__(self, *_args):
        if self.server is None:
            return
        if self.thread is not None and self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=8)
        if self.thread is not None and self.thread.is_alive():
            raise RuntimeError("read_bridge_not_retired")
        if self.path.exists():
            info = self.path.lstat()
            if (info.st_dev, info.st_ino) != self.identity:
                raise RuntimeError("read_bridge_path_replaced")
            self.path.unlink()


def start_namespace_read_proxy(args):
    """The production MCP sees its normal API URL on this namespace's loopback."""
    config = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())["mcpServers"][
        "cao-mcp-server"
    ]
    env = config["env"]
    proxy_keys = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
    proxy_keys |= {key.lower() for key in proxy_keys}
    if any(key in os.environ or key in env for key in proxy_keys):
        raise ValueError("native_proxy_environment_not_empty")
    caller = env["CAO_TERMINAL_ID"]
    port = int(env["CAO_API_PORT"])

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if (
                not allowed_read_path("GET", self.path, caller)
                or self.headers.get("Content-Length", "0") != "0"
                or self.headers.get("Transfer-Encoding") is not None
            ):
                self.send_error(403)
                return
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(8)
                connection.connect(NATIVE_SOCKET)
                connection.sendall(
                    json.dumps({"method": "GET", "path": self.path}).encode() + b"\n"
                )
                with connection.makefile("rb") as stream:
                    raw = stream.readline(131073)
            if len(raw) > 131072 or not raw.endswith(b"\n"):
                raise ValueError("bridge_frame_limit")
            response = json.loads(raw, object_pairs_hook=_pairs)
            body = base64.b64decode(response["body"], validate=True)
            if (
                len(body) > MAX_BODY
                or type(response["status"]) is not int
                or not 100 <= response["status"] <= 599
            ):
                raise ValueError("bridge_response_invalid")
            self.send_response(response["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    return server, thread
