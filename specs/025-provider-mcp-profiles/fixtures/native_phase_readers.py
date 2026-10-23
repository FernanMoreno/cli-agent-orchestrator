"""Bounded read-only adapters for a verified owned API and tmux server."""

import errno
import json
import math
import os
import re
import selectors
import socket
import subprocess
import time
from urllib.parse import urlsplit


def _remaining(stop):
    if type(stop) not in (int, float) or not math.isfinite(stop):
        raise ValueError("reader_deadline")
    remaining = stop - time.monotonic()
    if remaining <= 0:
        raise ValueError("reader_deadline")
    return remaining


def capture_bytes(argv, stop, max_bytes):
    """Read and reap only this capture helper, never the native agent/server."""
    _remaining(stop)
    if type(max_bytes) is not int or not 1 <= max_bytes <= 65537:
        raise ValueError("capture_byte_limit")
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    selector = None
    try:
        selector = selectors.DefaultSelector()
        data = bytearray()
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = stop - time.monotonic()
            if remaining <= 0:
                raise ValueError("capture_deadline")
            for key, _ in selector.select(min(0.05, remaining)):
                part = os.read(key.fd, min(4096, max_bytes + 1 - len(data)))
                if not part:
                    selector.unregister(key.fileobj)
                    continue
                data.extend(part)
                if len(data) > max_bytes:
                    raise ValueError("capture_byte_limit")
        process.wait(timeout=max(0.001, stop - time.monotonic()))
        if time.monotonic() > stop:
            raise ValueError("capture_deadline")
        if process.returncode != 0:
            raise ValueError("capture_exit")
        return bytes(data)
    finally:
        try:
            if selector is not None:
                selector.close()
        finally:
            try:
                if process.poll() is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                process.wait(timeout=1)
            finally:
                process.stdout.close()


def http_json_bytes(api, path, stop, wire_limit):
    """Nonblocking owned JSON HTTP/1.x with an absolute deadline, not inactivity timeout."""
    parsed = urlsplit(api)
    request = (
        f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{parsed.port}\r\nAccept: application/json\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
    ).encode("ascii")
    _remaining(stop)
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection,
        selectors.DefaultSelector() as selector,
    ):
        connection.setblocking(False)
        result = connection.connect_ex(("127.0.0.1", parsed.port))
        if result not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY):
            raise ValueError("http_transport")
        selector.register(connection, selectors.EVENT_WRITE)
        sent = 0
        header = bytearray()
        body = bytearray()
        length = None
        while True:
            events = selector.select(_remaining(stop))
            if not events:
                raise ValueError("reader_deadline")
            for _, event in events:
                _remaining(stop)
                if event & selectors.EVENT_WRITE:
                    if connection.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
                        raise ValueError("http_transport")
                    try:
                        sent += connection.send(request[sent:])
                    except BlockingIOError:
                        continue
                    if sent == len(request):
                        selector.modify(connection, selectors.EVENT_READ)
                    continue
                capacity = 16384 + 4 - len(header) if length is None else length + 1 - len(body)
                try:
                    part = connection.recv(min(4096, max(1, capacity)))
                except BlockingIOError:
                    continue
                if not part:
                    raise ValueError("http_eof")
                if length is None:
                    header.extend(part)
                    marker = header.find(b"\r\n\r\n")
                    if marker < 0:
                        if len(header) > 16384:
                            raise ValueError("http_header_limit")
                        continue
                    if marker > 16384:
                        raise ValueError("http_header_limit")
                    lines = bytes(header[:marker]).split(b"\r\n")
                    status = lines[0].split(b" ")
                    if (
                        len(status) < 2
                        or status[0] not in (b"HTTP/1.1", b"HTTP/1.0")
                        or status[1] != b"200"
                    ):
                        raise ValueError("http_status")
                    values = {}
                    for line in lines[1:]:
                        key, separator, value = line.partition(b":")
                        key = key.lower().strip()
                        if not separator or key in values:
                            raise ValueError("http_framing")
                        values[key] = value.strip()
                    encoded_length = values.get(b"content-length", b"")
                    if (
                        b"transfer-encoding" in values
                        or values.get(b"content-encoding", b"identity") != b"identity"
                        or not encoded_length.isdigit()
                    ):
                        raise ValueError("http_framing")
                    length = int(encoded_length)
                    if length > wire_limit:
                        raise ValueError("http_body_limit")
                    body.extend(header[marker + 4 :])
                    header.clear()
                else:
                    body.extend(part)
                if len(body) > length:
                    raise ValueError("http_framing")
                if len(body) == length:
                    _remaining(stop)
                    return bytes(body)


class OwnedPhaseReaders:
    def __init__(self, api_url, socket_path, terminals, verify):
        parsed = urlsplit(api_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or not parsed.port
            or not callable(verify)
        ):
            raise ValueError("reader_scope")
        self._api = api_url
        self._socket = str(socket_path)
        self._verify = verify
        self._terminals = {}
        self._turns = {}
        for terminal, scope in terminals.items():
            if (
                not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", terminal)
                or set(scope) != {"session", "pane"}
                or any(
                    type(v) is not str or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", v) is None
                    for v in scope.values()
                )
            ):
                raise ValueError("reader_scope")
            self._terminals[terminal] = dict(scope)

    def close(self):
        self._api = self._socket = self._verify = None
        self._terminals = {}
        self._turns = {}

    def _check(self, terminal, stop):
        _remaining(stop)
        if terminal not in self._terminals:
            raise ValueError("reader_scope")
        if self._verify() is not True:
            raise ValueError("reader_ownership")
        return self._terminals[terminal]

    def _json(self, terminal, stop, max_bytes, output=False):
        self._check(terminal, stop)
        if type(max_bytes) is not int or not 1 <= max_bytes <= 65537:
            raise ValueError("http_body_limit")
        wire_limit = 6 * max_bytes + 4096
        path = "/terminals/" + terminal + ("/output?mode=last" if output else "")
        payload = http_json_bytes(self._api, path, stop, wire_limit)
        self._check(terminal, stop)
        try:
            value = json.loads(payload)
        except (ValueError, UnicodeError):
            raise ValueError("http_json") from None
        if type(value) is not dict:
            raise ValueError("http_json")
        return value

    def read_state(self, terminal, stop):
        value = self._json(terminal, stop, 65537)
        scope = self._terminals[terminal]
        if (value.get("id"), value.get("session_name"), value.get("name")) != (
            terminal,
            scope["session"],
            scope["pane"],
        ):
            raise ValueError("terminal_binding")
        state = dict(
            http_status=200,
            status=value.get("status"),
            turn_sequence=value.get("turn_sequence"),
            turn_completed=value.get("turn_completed"),
        )
        self._turns[terminal] = (state["status"], state["turn_sequence"], state["turn_completed"])
        return state

    def read_turn(self, terminal, stop):
        self._check(terminal, stop)
        payload = http_json_bytes(self._api, "/terminals/" + terminal + "/turn", stop, 8192)
        self._check(terminal, stop)
        value = json.loads(payload)
        if type(value) is not dict or value.get("terminal_id") != terminal:
            raise ValueError("terminal_turn_binding")
        return {
            key: value.get(key) for key in ("generation", "state", "attempts", "allowed_actions")
        }

    def _frame_turn(self, terminal, stop, expected=None):
        if expected is None:
            expected = self._turns.get(terminal)
        if (
            type(expected) is not tuple
            or len(expected) != 3
            or type(expected[2]) is not int
            or expected[0] != "completed"
            or type(expected[1]) is not int
            or expected[1] <= 0
            or expected[1] != expected[2]
        ):
            raise ValueError("terminal_not_completed")
        self.read_state(terminal, stop)
        if self._turns.get(terminal) != expected:
            raise ValueError("terminal_turn_changed")
        return expected

    def read_output(self, terminal, stop, *, max_bytes):
        expected = self._frame_turn(terminal, stop)
        value = self._json(terminal, stop, max_bytes, output=True)
        text = value.get("output")
        if (
            value.get("mode") != "last"
            or type(text) is not str
            or len(text.encode("utf-8")) > max_bytes
        ):
            raise ValueError("output_shape")
        self._frame_turn(terminal, stop, expected)
        return text

    def capture_viewport(self, terminal, stop, *, max_bytes):
        expected = self._frame_turn(terminal, stop)
        scope = self._check(terminal, stop)
        value = capture_bytes(
            [
                "/usr/bin/tmux",
                "-S",
                self._socket,
                "capture-pane",
                "-p",
                "-t",
                scope["session"] + ":" + scope["pane"],
            ],
            stop,
            max_bytes,
        )
        self._check(terminal, stop)
        self._frame_turn(terminal, stop, expected)
        return value.decode("utf-8", errors="strict")
