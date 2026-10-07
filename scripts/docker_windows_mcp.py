#!/usr/bin/env python3
"""Relay only registered Windows MCP commands over an owner-only Unix socket."""

import json
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
from pathlib import Path

SHUTDOWN_GRACE = 5


def terminate(process):
    """Reap the process group even when a child ignores normal EOF/termination."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=SHUTDOWN_GRACE)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # A completed parent can still have children retaining inherited pipes.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def authorized(arguments, allowed):
    return (
        isinstance(arguments, list)
        and all(isinstance(a, str) for a in arguments)
        and arguments in allowed
    )


def receive(connection, size):
    data = bytearray()
    while len(data) < size:
        part = connection.recv(size - len(data))
        if not part:
            raise EOFError("bridge connection closed")
        data.extend(part)
    return bytes(data)


def handle(connection, config):
    process = None
    lock = threading.Lock()

    def send(kind, data):
        with lock:
            connection.sendall(struct.pack("!BI", kind, len(data)) + data)

    try:
        uid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[
            1
        ]
        if uid != os.getuid():
            raise ValueError("bridge owner mismatch")
        connection.settimeout(10)
        size = struct.unpack("!I", receive(connection, 4))[0]
        if size > 16384:
            raise ValueError("bridge request too large")
        arguments = json.loads(receive(connection, size))
        if not authorized(arguments, config["arguments"]):
            raise ValueError("unregistered MCP command")
        connection.settimeout(None)
        process = subprocess.Popen(
            [config["executable"], *arguments],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        def input_stream():
            try:
                while part := connection.recv(65536):
                    process.stdin.write(part)
                    process.stdin.flush()
            except (OSError, ValueError):
                pass
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=SHUTDOWN_GRACE)
                except subprocess.TimeoutExpired:
                    terminate(process)

        def output_stream(stream, kind):
            try:
                while part := os.read(stream.fileno(), 65536):
                    send(kind, part)
            except OSError:
                terminate(process)

        threading.Thread(target=input_stream, daemon=True).start()
        outputs = [
            threading.Thread(target=output_stream, args=(process.stdout, 1)),
            threading.Thread(target=output_stream, args=(process.stderr, 2)),
        ]
        for thread in outputs:
            thread.start()
        result = process.wait()
        for thread in outputs:
            thread.join(timeout=SHUTDOWN_GRACE)
        if any(thread.is_alive() for thread in outputs):
            terminate(process)
            for thread in outputs:
                thread.join(timeout=SHUTDOWN_GRACE)
        send(3, str(result).encode())
    except (OSError, ValueError, EOFError):
        try:
            send(2, b"Windows MCP bridge rejected or lost the connection.\n")
            send(3, b"1")
        except OSError:
            pass
    finally:
        if process:
            terminate(process)
        connection.close()


def serve(root):
    if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError("private bridge directory required")
    config = json.loads((root / "commands.json").read_text())
    path = root / "mcp.sock"
    path.unlink(missing_ok=True)
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    path.chmod(0o600)
    listener.listen(16)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            connection, _ = listener.accept()
            threading.Thread(target=handle, args=(connection, config), daemon=True).start()
    finally:
        listener.close()
        path.unlink(missing_ok=True)


def client(arguments):
    path = os.environ.get("CAO_WINDOWS_MCP_SOCKET")
    if not path:
        raise RuntimeError("Windows MCP relay is not configured")
    connection = socket.socket(socket.AF_UNIX)
    connection.connect(path)
    body = json.dumps(arguments).encode()
    connection.sendall(struct.pack("!I", len(body)) + body)

    def stdin():
        try:
            while part := os.read(sys.stdin.fileno(), 65536):
                connection.sendall(part)
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    threading.Thread(target=stdin, daemon=True).start()
    try:
        while True:
            kind, size = struct.unpack("!BI", receive(connection, 5))
            if size > 65536:
                raise ValueError("invalid relay frame")
            body = receive(connection, size)
            if kind == 3:
                return int(body)
            stream = sys.stdout.buffer if kind == 1 else sys.stderr.buffer
            stream.write(body)
            stream.flush()
    finally:
        connection.close()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--serve"]:
        serve(Path(sys.argv[2]))
    else:
        sys.exit(client(sys.argv[1:]))
