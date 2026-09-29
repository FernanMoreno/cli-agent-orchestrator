"""The static Docker supervisor relays worker MCP over private stdio."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_SUPERVISOR_SOURCE = _ROOT / "src/cli_agent_orchestrator/backends/docker_work_supervisor.c"


def _compile(source: str, output: Path) -> None:
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("a C compiler is required to verify the static Docker supervisor")
    source_path = output.with_suffix(".c")
    source_path.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [compiler, "-static", "-O2", "-Wall", "-Wextra", "-Werror", str(source_path), "-o", str(output)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def _read_frame(stream, prefix: bytes) -> bytes:
    header = stream.readline(96)
    assert header.startswith(prefix), header
    size = int(header[len(prefix) :].strip())
    payload = stream.read(size)
    assert len(payload) == size
    assert stream.read(1) == b"\n"
    return payload


def _supervisor_command(binary: Path, worker: Path, *, mcp: bool) -> list[str]:
    return [
        str(binary),
        "--protocol=1",
        "--worker",
        str(worker),
        "--argv0",
        "/worker",
        "--uid",
        str(os.getuid()),
        "--gid",
        str(os.getgid()),
        *( ["--mcp"] if mcp else [] ),
    ]


def test_docker_supervisor_gates_worker_and_preserves_private_mcp_fd3(tmp_path):
    """The work effect starts only after GO and the MCP request never uses container network."""
    assert _SUPERVISOR_SOURCE.is_file(), "Docker's static Work supervisor is missing"
    supervisor = tmp_path / "cao-work-supervisor"
    worker = tmp_path / "worker"
    _compile(
        """
        #include <stdlib.h>
        #include <unistd.h>
        int main(void) {
            char input[32];
            ssize_t count;
            while ((count = read(STDIN_FILENO, input, sizeof(input))) > 0) {
                if (write(STDOUT_FILENO, input, (size_t)count) != count) return 1;
            }
            return 0;
        }
        """,
        worker,
    )
    compiler = shutil.which("cc")
    assert compiler is not None
    built = subprocess.run(
        [
            compiler,
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(_SUPERVISOR_SOURCE),
            "-DCAO_SUPERVISOR_SOURCE_SHA256=\"test\"",
            "-o",
            str(supervisor),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert built.returncode == 0, built.stderr

    process = subprocess.Popen(
        _supervisor_command(supervisor, worker, mcp=False),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    process.stdin.write(b"CAO-INPUT/1 6\nhello\n\n")
    process.stdin.flush()
    ready = json.loads(_read_frame(process.stdout, b"CAO-READY/1 "))
    assert ready["protocol"] == 1
    assert ready["mcp_enabled"] is False
    assert ready["worker_uid"] == os.getuid()
    assert ready["worker_gid"] == os.getgid()
    process.stdin.write(b"CAO-GO/1\n")
    process.stdin.flush()

    output = bytearray()
    while True:
        header = process.stdout.readline(96)
        if header.startswith(b"CAO-EXIT/1 "):
            assert int(header.removeprefix(b"CAO-EXIT/1 ").strip()) == 0
            break
        assert header.startswith(b"CAO-OUT/1 stdout "), header
        size = int(header.split()[-1])
        output.extend(process.stdout.read(size))
        assert process.stdout.read(1) == b"\n"
    assert process.wait(timeout=5) == 0
    assert bytes(output) == b"hello\n"
    assert process.stderr.read() == b""


def test_docker_supervisor_relays_only_bounded_mcp_frames(tmp_path):
    """A worker's fd 3 JSON-RPC request is bridged over Docker attach stdio."""
    assert _SUPERVISOR_SOURCE.is_file(), "Docker's static Work supervisor is missing"
    supervisor = tmp_path / "cao-work-supervisor"
    worker = tmp_path / "worker"
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("a C compiler is required to verify the static Docker supervisor")
    request = b'{"jsonrpc":"2.0","id":"r1","method":"tools/call","params":{"name":"cao.work.child","arguments":{}}}\n'
    _compile(
        """
        #include <stdlib.h>
        #include <unistd.h>
        int main(void) {
            const char request[] = "{\\\"jsonrpc\\\":\\\"2.0\\\",\\\"id\\\":\\\"r1\\\",\\\"method\\\":\\\"tools/call\\\",\\\"params\\\":{\\\"name\\\":\\\"cao.work.child\\\",\\\"arguments\\\":{}}}\\n";
            const char response[] = "{\\\"jsonrpc\\\":\\\"2.0\\\",\\\"id\\\":\\\"r1\\\",\\\"result\\\":{\\\"content\\\":[]}}\\n";
            const char *descriptor = getenv("CAO_WORK_MCP_FD");
            if (!descriptor || descriptor[0] != '3' || descriptor[1] != '\\0') return 20;
            if (write(3, request, sizeof(request) - 1) != sizeof(request) - 1) return 21;
            char buffer[512];
            ssize_t count = read(3, buffer, sizeof(buffer));
            if (count != (ssize_t)(sizeof(response) - 1)) return 22;
            if (write(1, buffer, (size_t)count) != count) return 23;
            return 0;
        }
        """,
        worker,
    )
    built = subprocess.run(
        [
            compiler,
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(_SUPERVISOR_SOURCE),
            "-DCAO_SUPERVISOR_SOURCE_SHA256=\"test\"",
            "-o",
            str(supervisor),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert built.returncode == 0, built.stderr

    process = subprocess.Popen(
        _supervisor_command(supervisor, worker, mcp=True),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    process.stdin.write(b"CAO-INPUT/1 0\n\n")
    process.stdin.flush()
    ready = json.loads(_read_frame(process.stdout, b"CAO-READY/1 "))
    assert ready["mcp_enabled"] is True
    process.stdin.write(b"CAO-GO/1\n")
    process.stdin.flush()

    mcp_request = _read_frame(process.stderr, b"CAO-MCP/1 ")
    assert mcp_request == request
    response = b'{"jsonrpc":"2.0","id":"r1","result":{"content":[]}}\n'
    process.stdin.write(f"CAO-MCP/1 {len(response)}\n".encode() + response + b"\n")
    process.stdin.flush()
    output = _read_frame(process.stdout, b"CAO-OUT/1 stdout ")
    assert output == response
    assert process.stdout.readline(96) == b"CAO-EXIT/1 0\n"
    assert process.wait(timeout=5) == 0
    assert process.stderr.read() == b""
