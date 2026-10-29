"""Synthetic OpenAI stream for native cancellation controls, never external inference.

Only the private, source-pinned T220 wrapper calls ``wrapper``. Each attempt
runs its real CLI and this endpoint in an isolated loopback-only namespace.
"""

import hashlib
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from test.live_provider.native_cancel_support import (
    isolated_wrapper,
    receipt_from_request,
    save,
    sha,
)

MODEL = "opencode-go/glm-5.3-flash"
PORT = 43871  # Separate network namespace for every native process.
PRIMARY_INSTRUCTION = "T220_NATIVE_PRIMARY_INSTRUCTION"


def primary_request(body):
    users = [m for m in body.get("messages", []) if m.get("role") == "user"]
    if not users or body.get("stream") is not True:
        return False
    text = users[-1].get("content", "")
    if isinstance(text, list):
        text = "\n".join(p["text"] for p in text if p.get("type") == "text")
    system = [
        m.get("content", "")
        for m in body.get("messages", [])
        if m.get("role") in {"system", "developer"}
    ]
    return (
        isinstance(text, str)
        and text.startswith("T220 controlled native turn")
        and "Do not quote or emit that receipt before the task is complete" in text
        and any(PRIMARY_INSTRUCTION in str(content) for content in system)
    )


def wrapper(root, args):
    def validate(values):
        assert values[:2] == ["mini", "--standalone"]
        assert values[values.index("--model") + 1] == MODEL

    def environment(env):
        for key, value in os.environ.items():
            if key.startswith("OPENCODE_") or key.startswith("XDG_"):
                if key not in {
                    "OPENCODE_AUTH_CONTENT",
                    "OPENCODE_CONFIG_CONTENT",
                    "OPENCODE_API_KEY",
                }:
                    env[key] = value
        env["OPENCODE_API_KEY"] = "synthetic-local-control"
        # Diagnostic display only; model/server permissions remain unchanged.
        cli = json.loads(env.get("OPENCODE_CLI_CONFIG_CONTENT", "{}"))
        cli.setdefault("mini", {})["tools"] = "show"
        env["OPENCODE_CLI_CONFIG_CONTENT"] = json.dumps(cli)
        return env

    isolated_wrapper(
        root,
        args,
        gateway=__file__,
        native_path="/tmp/opencode-native",
        prepare_environment=environment,
        validate_args=validate,
    )


def backend(args):
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    assert [name for _, name in socket.if_nameindex()] == ["lo"]
    assert not Path("/home/felni/.codex/auth.json").exists()
    entry = Path("/tmp/evidence")
    ledger = {
        "requests": 0,
        "primary_requests": 0,
        "ticks": 0,
        "stream_open": False,
        "loopback_only": True,
        "operator_home_masked": True,
        "binary_sha256": sha("/tmp/opencode-native"),
        "tty": list(os.get_terminal_size(0)),
        "errors": [],
        "mini_tools_display": json.loads(os.environ["OPENCODE_CLI_CONFIG_CONTENT"])["mini"][
            "tools"
        ],
    }
    lock = threading.RLock()

    def record(**updates):
        with lock:
            ledger.update(updates)
            save(entry / "backend.json", ledger)

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def start_sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

        def sse_bytes(self, payload):
            self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
            self.wfile.flush()

        def end_sse(self):
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

        def do_POST(self):
            try:
                length = int(self.headers["Content-Length"])
                assert 0 < length <= 1024 * 1024
                body = json.loads(self.rfile.read(length))
                with lock:
                    ordinal = ledger["requests"] + 1
                    assert ordinal <= 64, "native_request_budget"
                    save(entry / f"request-{ordinal:03d}.json", body)
                    record(requests=ordinal)
                assert isinstance(body["model"], str)
                # Auxiliary title/summary requests are not a native user turn.
                primary = primary_request(body)
                if not primary:
                    with lock:
                        models = dict(ledger.get("auxiliary_models", {}))
                        models[body["model"]] = models.get(body["model"], 0) + 1
                        record(
                            auxiliary_requests=ledger.get("auxiliary_requests", 0) + 1,
                            auxiliary_models=models,
                        )
                    if body.get("stream") is True:
                        self.start_sse()
                        chunk = {
                            "id": "aux",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": body["model"],
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"role": "assistant", "content": "Local control"},
                                    "finish_reason": "stop",
                                }
                            ],
                        }
                        self.sse_bytes(
                            ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
                        )
                        self.end_sse()
                        return
                    payload = json.dumps(
                        {
                            "id": "aux",
                            "object": "chat.completion",
                            "model": body["model"],
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {"role": "assistant", "content": "Local control"},
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 1,
                                "completion_tokens": 1,
                                "total_tokens": 2,
                            },
                        }
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                receipt = receipt_from_request(body)
                assert body["model"] == "glm-5.3-flash", "unexpected_primary_model"
                assert ledger["primary_requests"] == 0, "duplicate_native_input"
                names = sorted(t["function"]["name"] for t in body.get("tools", []))
                assert not names, "display_setting_must_not_expand_model_tools"
                record(
                    primary_requests=1,
                    builtin_tool_count=len(names),
                    receipt_sha256=hashlib.sha256(receipt.encode()).hexdigest(),
                    stream_open=True,
                )
                self.start_sse()

                def chunk(text, finish=None, *, role=None):
                    delta = {"content": text}
                    if role is not None:
                        delta["role"] = role
                    value = {
                        "id": "t220",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": body["model"],
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    }
                    self.sse_bytes(("data: " + json.dumps(value) + "\n\n").encode())

                # The bundled SDK gates live content.delta events on role.
                # A completed result alone did not exercise this boundary.
                chunk("", role="assistant")
                deadline = time.monotonic() + 180
                while not (entry / "release").exists():
                    assert time.monotonic() < deadline, "stream_budget_expired"
                    tick = ledger["ticks"] + 1
                    # Mini commits only stable top-level Markdown blocks.
                    # A single newline keeps one paragraph open until EOF.
                    chunk(f"T220-progress-{tick:04d}\n\n")
                    record(ticks=tick, last_progress=time.monotonic())
                    time.sleep(0.5)
                chunk("\nT220 finished\n" + receipt + "\n")
                chunk("", "stop")
                self.sse_bytes(b"data: [DONE]\n\n")
                self.end_sse()
                record(stream_open=False, finished=True)
            except (BrokenPipeError, ConnectionResetError):
                record(stream_open=False, disconnected=True)
            except BaseException as error:
                record(errors=[*ledger["errors"], type(error).__name__ + ":" + str(error)[:120]])
                raise

    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    config_path = Path(os.environ["OPENCODE_CONFIG"])
    config = json.loads(config_path.read_text())
    assert config.get("mcp", {}).get("servers", {}) == {}
    assert (
        config["providers"]["opencode-go"]["settings"]["baseURL"] == f"http://127.0.0.1:{PORT}/v1"
    )
    record(
        config_source_sha256=sha(config_path), network_namespace=os.readlink("/proc/self/ns/net")
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    process = subprocess.Popen(["/tmp/opencode-native", *args])
    try:
        raise SystemExit(process.wait())
    finally:
        server.shutdown()
        server.server_close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    assert sys.argv[1] == "backend"
    backend(sys.argv[2:])
