"""Synthetic Anthropic adapter for the shared native cancellation lifecycle.

No operator credentials or external inference; caller owns the one-shot sandbox.
"""

import hashlib
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from test.live_provider.claude_client_denial import text_stream
from test.live_provider.native_cancel_adapters import CLAUDE
from test.live_provider.native_cancel_support import (
    isolated_wrapper,
    receipt_from_request,
    save,
    sha,
)
from urllib.parse import urlsplit

PORT = 43871


def text_blocks(content):
    if isinstance(content, str):
        return [content]
    assert isinstance(content, list)
    return [b["text"] for b in content if b.get("type") == "text"]


def primary_request(body):
    users = [m for m in body.get("messages", []) if m.get("role") == "user"]
    if not users:
        return False
    return any(
        CLAUDE.primary_instruction in t for t in text_blocks(body.get("system", []))
    ) and any(
        t.startswith(CLAUDE.prompt_prefix)
        and "Do not quote or emit that receipt before the task is complete" in t
        for t in text_blocks(users[-1].get("content", ""))
    )


def primary_receipt(body):
    assert primary_request(body)
    assert body.get("stream") is True
    assert body.get("model") == CLAUDE.model
    assert body.get("tools", []) == [], "restricted_profile_offered_tools"
    return receipt_from_request(body, prompt_prefix=CLAUDE.prompt_prefix)


def event(kind, **fields):
    value = {"type": kind, **fields}
    return f"event: {kind}\ndata: {json.dumps(value)}\n\n".encode()


def stream_start(model):
    return event(
        "message_start",
        message={
            "id": "msg_caos_cancel",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 0},
        },
    ) + event("content_block_start", index=0, content_block={"type": "text", "text": ""})


def stream_delta(text):
    return event("content_block_delta", index=0, delta={"type": "text_delta", "text": text})


def stream_end():
    return (
        event("content_block_stop", index=0)
        + event(
            "message_delta",
            delta={"stop_reason": "end_turn", "stop_sequence": None},
            usage={"output_tokens": 1},
        )
        + event("message_stop")
    )


def wrapper(root, args):
    def validate(values):
        assert values[values.index("--model") + 1] == CLAUDE.model
        assert "--dangerously-skip-permissions" not in values
        assert values[values.index("--permission-mode") + 1] == "default"

    def environment(env):
        env.update(
            ANTHROPIC_API_KEY="synthetic-local-control",
            ANTHROPIC_BASE_URL=f"http://127.0.0.1:{PORT}",
            CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
            CLAUDE_CONFIG_DIR=str(Path(env["HOME"]) / ".claude"),
            DISABLE_TELEMETRY="1",
            DISABLE_ERROR_REPORTING="1",
        )
        return env

    isolated_wrapper(
        root,
        args,
        gateway=__file__,
        native_path="/tmp/claude-native",
        prepare_environment=environment,
        validate_args=validate,
    )


def backend(args):
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    assert [name for _, name in socket.if_nameindex()] == ["lo"]
    assert not Path("/home/felni/.claude/.credentials.json").exists()
    assert not (Path.home() / ".claude/.credentials.json").exists()
    entry = Path("/tmp/evidence")
    ledger = dict(
        requests=0,
        primary_requests=0,
        ticks=0,
        stream_open=False,
        errors=[],
        loopback_only=True,
        operator_home_masked=True,
        binary_sha256=sha("/tmp/claude-native"),
        tty=list(os.get_terminal_size(0)),
        network_namespace=os.readlink("/proc/self/ns/net"),
    )
    lock = threading.RLock()

    def record(**updates):
        with lock:
            ledger.update(updates)
            save(entry / "backend.json", ledger)

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def respond(self, data, content_type="application/json"):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def chunk(self, payload):
            self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
            self.wfile.flush()

        def send_error(self, code, message=None, explain=None):
            # Include unsupported methods/routes in the quiet-replacement witness.
            record(
                requests=ledger["requests"] + 1,
                errors=[*ledger["errors"], "unexpected_native_method"],
            )
            return super().send_error(code, message, explain)

        def do_POST(self):
            try:
                self.connection.settimeout(5)
                length = int(self.headers.get("Content-Length", "-1"))
                assert 0 < length <= 1024 * 1024
                raw = self.rfile.read(length)
                assert len(raw) == length
                body = json.loads(raw)
                with lock:
                    ordinal = ledger["requests"] + 1
                    assert ordinal <= 64, "native_request_budget"
                    save(entry / f"request-{ordinal:03d}.json", body)
                    record(requests=ordinal)
                route = urlsplit(self.path).path
                if route == "/v1/messages/count_tokens":
                    self.respond(b'{"input_tokens":1}')
                    return
                assert route == "/v1/messages", "unexpected_native_route"
                model = body["model"]
                assert isinstance(model, str)
                if not primary_request(body):
                    record(auxiliary_requests=ledger.get("auxiliary_requests", 0) + 1)
                    if body.get("stream") is True:
                        self.respond(text_stream("Local control", model=model), "text/event-stream")
                    else:
                        self.respond(
                            json.dumps(
                                {
                                    "id": "msg_aux",
                                    "type": "message",
                                    "role": "assistant",
                                    "model": model,
                                    "content": [{"type": "text", "text": "Local control"}],
                                    "stop_reason": "end_turn",
                                    "stop_sequence": None,
                                    "usage": {"input_tokens": 1, "output_tokens": 1},
                                }
                            ).encode()
                        )
                    return
                receipt = primary_receipt(body)
                assert ledger["primary_requests"] == 0, "duplicate_native_input"
                record(
                    primary_requests=1,
                    builtin_tool_count=0,
                    stream_open=True,
                    receipt_sha256=hashlib.sha256(receipt.encode()).hexdigest(),
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.chunk(stream_start(model))
                deadline = time.monotonic() + 180
                while not (entry / "release").exists():
                    assert time.monotonic() < deadline, "stream_budget_expired"
                    tick = ledger["ticks"] + 1
                    self.chunk(stream_delta(f"{CLAUDE.progress_prefix}{tick:04d}\n\n"))
                    record(ticks=tick, last_progress=time.monotonic())
                    time.sleep(0.5)
                self.chunk(stream_delta("\nT223 finished\n" + receipt + "\n"))
                self.chunk(stream_end())
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
                record(stream_open=False, finished=True)
            except (BrokenPipeError, ConnectionResetError):
                record(stream_open=False, disconnected=True)
            except BaseException as error:
                record(errors=[*ledger["errors"], type(error).__name__ + ":" + str(error)[:120]])
                raise

    record()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    process = subprocess.Popen(["/tmp/claude-native", *args])
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
