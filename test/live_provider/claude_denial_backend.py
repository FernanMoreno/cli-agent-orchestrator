"""Bounded loopback Anthropic fixture; launch only inside the caller's sandbox."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from test.live_provider.claude_client_denial import check_offer, text_stream, tool_stream
from test.live_provider.claude_mcp_catalog import decode_frame
from urllib.parse import urlsplit


class DenialBackend:
    def __init__(self, *, excluded, permitted, call_id, nonce, arguments):
        self.excluded = excluded
        self.permitted = permitted
        self.call_id = call_id
        self.nonce = nonce
        self.arguments = arguments
        self.primary = None
        self.followup_error = False
        self.requests = 0
        self.auxiliary = 0
        self.errors = []
        self.lock = threading.Lock()
        self.server = None
        self.thread = None

    def response(self, path, body):
        """Deterministic exchange, also directly exercised by offline tests."""
        with self.lock:
            self.requests += 1
            if self.requests > 16:
                raise ValueError("synthetic_request_limit")
            payload = decode_frame(body)
            route = urlsplit(path).path
            if route == "/v1/messages/count_tokens":
                return "application/json", b'{"input_tokens":1}'
            if route != "/v1/messages":
                raise ValueError("synthetic_route_invalid")
            model = payload.get("model", "synthetic")
            if not isinstance(model, str):
                raise ValueError("synthetic_model_invalid")
            try:
                check_offer(
                    payload, excluded=self.excluded, permitted=self.permitted, nonce=self.nonce
                )
            except ValueError:
                # Auxiliary requests cannot advance or satisfy the primary proof.
                self.auxiliary += 1
                if payload.get("stream") is True:
                    return "text/event-stream", text_stream("auxiliary control", model=model)
                return (
                    "application/json",
                    json.dumps(
                        {
                            "id": "msg_caos_aux",
                            "type": "message",
                            "role": "assistant",
                            "model": model,
                            "content": [{"type": "text", "text": "auxiliary control"}],
                            "stop_reason": "end_turn",
                            "stop_sequence": None,
                            "usage": {"input_tokens": 1, "output_tokens": 1},
                        }
                    ).encode(),
                )
            if self.primary is None:
                self.primary = payload
                return "text/event-stream", tool_stream(
                    self.excluded, self.call_id, self.arguments, model=model
                )
            matches = [
                b
                for m in payload["messages"]
                if m.get("role") == "user" and isinstance(m.get("content"), list)
                for b in m["content"]
                if isinstance(b, dict)
                and b.get("type") == "tool_result"
                and b.get("tool_use_id") == self.call_id
            ]
            if len(matches) != 1 or matches[0].get("is_error") is not True or self.followup_error:
                raise ValueError("synthetic_followup_invalid")
            self.followup_error = True
            return "text/event-stream", text_stream("control complete", model=model)

    def start(self):
        if self.server is not None:
            raise ValueError("synthetic_server_already_started")
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                try:
                    self.connection.settimeout(5)
                    length = int(self.headers.get("Content-Length", "-1"))
                    if not 0 < length <= 1024 * 1024:
                        raise ValueError("synthetic_body_limit")
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise ValueError("synthetic_body_incomplete")
                    content_type, value = backend.response(self.path, body)
                    self.send_response(200)
                except Exception:
                    backend.errors.append("synthetic_request_failed")
                    content_type, value = (
                        "application/json",
                        b'{"error":{"type":"invalid_request_error","message":"synthetic_request_failed"}}',
                    )
                    self.send_response(400)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(value)))
                self.end_headers()
                self.wfile.write(value)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise ValueError("synthetic_server_not_retired")
