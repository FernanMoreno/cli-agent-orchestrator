"""Behavioral checks for the deterministic T020 Docker worker executable."""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

WORKER_SOURCE = Path(__file__).with_name("agent-step-worker.c")
WORKER_SOURCE_PIN = Path(__file__).with_name("agent-step-worker.sha256")


@pytest.fixture(scope="module")
def worker_binary(tmp_path_factory):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("a static C compiler is required for the T020 Docker worker")
    executable = tmp_path_factory.mktemp("t020-worker") / "cao-agent-step-worker"
    built = subprocess.run(
        [
            compiler,
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(WORKER_SOURCE),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    return executable


def test_worker_source_matches_the_reviewed_source_digest():
    expected = WORKER_SOURCE_PIN.read_text(encoding="ascii").split()[0]
    actual = hashlib.sha256(WORKER_SOURCE.read_bytes()).hexdigest()

    assert actual == expected


def _run_worker(worker_binary: Path, worker_input: bytes, *, reject_tool: str | None = None):
    worker_socket, proxy_socket = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    requests = []
    proxy_errors = []

    def serve_proxy():
        with proxy_socket:
            stream = proxy_socket.makefile("rb")
            while True:
                line = stream.readline(16_385)
                if not line:
                    return
                try:
                    request = json.loads(line)
                    requests.append(request)
                    name = request["params"]["name"]
                    if name == reject_tool:
                        response = {
                            "jsonrpc": "2.0",
                            "id": request["id"],
                            "error": {"code": -32000, "message": "rejected"},
                        }
                    else:
                        response = {
                            "jsonrpc": "2.0",
                            "id": request["id"],
                            "result": {"content": [{"type": "text", "text": "{}"}]},
                        }
                    proxy_socket.sendall(
                        json.dumps(response, separators=(",", ":")).encode() + b"\n"
                    )
                except (KeyError, TypeError, ValueError, OSError) as error:
                    proxy_errors.append(error)
                    return

    thread = threading.Thread(target=serve_proxy, daemon=True)
    thread.start()
    mcp_fd = worker_socket.fileno()
    try:
        process = subprocess.Popen(
            [str(worker_binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=(mcp_fd,),
            env={"LC_ALL": "C", "CAO_WORK_MCP_FD": str(mcp_fd)},
        )
    finally:
        worker_socket.close()
    try:
        stdout, stderr = process.communicate(worker_input, timeout=10)
    finally:
        thread.join(timeout=3)
        proxy_socket.close()
    assert not thread.is_alive(), "worker did not close its MCP socket"
    assert not proxy_errors, proxy_errors
    return process.returncode, stdout, stderr, requests


def test_worker_acknowledges_then_submits_the_fixed_completed_result(worker_binary):
    code, stdout, stderr, requests = _run_worker(worker_binary, b"frozen snapshot\n\ninput message")

    assert code == 0
    assert stderr == b""
    assert [request["params"]["name"] for request in requests] == [
        "cao.work.task_received",
        "cao.work.submit_result",
    ]
    assert requests[0]["params"]["arguments"] == {}
    assert requests[1]["params"]["arguments"] == {
        "schema_version": 1,
        "status": "completed",
        "output": {"value": "t122-deterministic"},
    }
    assert stdout == b"T122_AGENT_STEP_COMPLETED\n"


def test_worker_can_exit_after_receipt_without_claiming_a_result(worker_binary):
    code, stdout, stderr, requests = _run_worker(
        worker_binary, b"frozen snapshot\n\nT122_OMIT_RESULT"
    )

    assert code == 0
    assert stderr == b""
    assert [request["params"]["name"] for request in requests] == ["cao.work.task_received"]
    assert stdout == b"T122_AGENT_STEP_EXITED_WITHOUT_RESULT\n"


def test_worker_reports_failure_only_after_acknowledging_the_current_attempt(worker_binary):
    code, stdout, stderr, requests = _run_worker(worker_binary, b"frozen snapshot\n\nT122_FAIL")

    assert code == 23
    assert stderr == b""
    assert [request["params"]["name"] for request in requests] == ["cao.work.task_received"]
    assert stdout == b"T122_AGENT_STEP_FAILED\n"


def test_worker_does_not_submit_result_when_receipt_is_rejected(worker_binary):
    code, _stdout, _stderr, requests = _run_worker(
        worker_binary,
        b"frozen snapshot\n\ninput message",
        reject_tool="cao.work.task_received",
    )

    assert code != 0
    assert [request["params"]["name"] for request in requests] == ["cao.work.task_received"]
