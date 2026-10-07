"""Parent-side framing, executable integrity and cleanup contracts.

These tests use temporary files, pipes and owned processes. They do not launch
Bubblewrap or claim host sandbox acceptance.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import io
import json
import os
import select
import signal
import socket
import struct
import subprocess
import sys
from contextlib import ExitStack
from test.services.test_work_bubblewrap_staging_args import _minimal_static_elf
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.services import work_bubblewrap_composition as parent
from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable


def _read_pipe(stack, payload=b"", *, keep_writer=False):
    read_fd, write_fd = os.pipe()
    stream = stack.enter_context(os.fdopen(read_fd, "rb", buffering=0))
    if payload:
        os.write(write_fd, payload)
    if keep_writer:
        stack.callback(os.close, write_fd)
    else:
        os.close(write_fd)
    return stream


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"[]",
        b"{",
        b"\xff",
        b'{"child-pid":1,"child-pid":2,"pid-namespace":3}',
        b'{"child-pid":1}',
        b'{"child-pid":true,"pid-namespace":3}',
        b'{"child-pid":0,"pid-namespace":3}',
        b'{"child-pid":1,"pid-namespace":0}',
        b'{"child-pid":1,"pid-namespace":3,"extra":4}',
        b'{"child-pid":1,"pid-namespace":3,"net-namespace":false}',
    ],
)
def test_namespace_info_rejects_ambiguous_or_incomplete_identity(payload):
    with pytest.raises(parent.WorkBubblewrapSetupFailed):
        parent._parse_bwrap_info(payload)


def test_namespace_info_keeps_only_the_identity_pair():
    assert parent._parse_bwrap_info(
        b'{"child-pid":123,"pid-namespace":456,"net-namespace":789}'
    ) == {"child-pid": 123, "pid-namespace": 456}


@pytest.mark.parametrize(
    "payload,match",
    [(b"", "empty"), (b"\xff", "UTF-8"), (b"{} trailing", "extra data")],
)
def test_namespace_pipe_rejects_invalid_framing(payload, match):
    with ExitStack() as stack:
        stream = _read_pipe(stack, payload)
        with pytest.raises(parent.WorkBubblewrapSetupFailed, match=match):
            parent._read_bwrap_info(stream.fileno(), 0.1)


def test_namespace_pipe_reads_complete_json_and_bounds_input(monkeypatch):
    payload = b'  {"child-pid":123,"pid-namespace":456}\n'
    with ExitStack() as stack:
        stream = _read_pipe(stack, payload)
        result = parent._read_bwrap_info(stream.fileno(), 0.1)
        assert parent._parse_bwrap_info(result)["child-pid"] == 123
        monkeypatch.setattr(parent, "_BWRAP_INFO_LIMIT", 8)
        stream = _read_pipe(stack, b"{" + b" " * 12)
        with pytest.raises(parent.WorkBubblewrapSetupFailed, match="size bound"):
            parent._read_bwrap_info(stream.fileno(), 0.1)


def test_namespace_pipe_timeout_and_incomplete_document():
    with ExitStack() as stack:
        stream = _read_pipe(stack, keep_writer=True)
        with pytest.raises(parent.WorkBubblewrapSetupFailed, match="timed out"):
            parent._read_bwrap_info(stream.fileno(), 0.01)
        stream = _read_pipe(stack, b"{")
        incomplete = parent._read_bwrap_info(stream.fileno(), 0.1)
        with pytest.raises(parent.WorkBubblewrapSetupFailed, match="malformed"):
            parent._parse_bwrap_info(incomplete)


@pytest.mark.parametrize(
    "payload,match",
    [
        (b"", "without"),
        (b"unrelated\n", "did not begin"),
        (parent._ACK_PREFIX + b"{}\nextra", "before the parent"),
        (parent._ACK_PREFIX + b"{\n", "malformed"),
        (parent._ACK_PREFIX + b"[]\n", "JSON object"),
    ],
)
def test_ack_pipe_rejects_output_that_cannot_authorize_release(payload, match):
    with ExitStack() as stack:
        process = SimpleNamespace(stdout=_read_pipe(stack, payload), stderr=_read_pipe(stack))
        with pytest.raises(parent._AckFailure, match=match):
            parent._read_ack(process, 0.1)


def test_ack_pipe_returns_one_object_without_worker_output():
    with ExitStack() as stack:
        process = SimpleNamespace(
            stdout=_read_pipe(stack, parent._ACK_PREFIX + b'{"ready":true}\n'),
            stderr=_read_pipe(stack),
        )
        assert parent._read_ack(process, 0.1) == ({"ready": True}, b"", b"")


def test_ack_pipe_preserves_bounded_diagnostics_when_setup_times_out():
    with ExitStack() as stack:
        process = SimpleNamespace(
            stdout=_read_pipe(stack, keep_writer=True), stderr=_read_pipe(stack, b"setup detail")
        )
        with pytest.raises(parent._AckFailure, match="timed out") as caught:
            parent._read_ack(process, 0.01)
        assert caught.value.stderr == b"setup detail"
        assert caught.value.stdout == b""


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_ack_pipe_bounds_both_output_channels(monkeypatch, stream):
    monkeypatch.setattr(parent, "_ACK_LIMIT", 64)
    monkeypatch.setattr(parent, "_STDERR_LIMIT", 64)
    with ExitStack() as stack:
        pipes = {
            name: _read_pipe(
                stack, b"x" * 65 if name == stream else b"", keep_writer=name != stream
            )
            for name in ("stdout", "stderr")
        }
        with pytest.raises(parent._AckFailure, match="bound") as caught:
            parent._read_ack(SimpleNamespace(**pipes), 0.1)
        assert len(getattr(caught.value, stream)) == 64


def test_ack_requires_both_pipes():
    with pytest.raises(parent._AckFailure, match="unavailable"):
        parent._read_ack(SimpleNamespace(stdout=None, stderr=None), 0.1)


@pytest.fixture
def ack_material():
    executable = _minimal_static_elf()
    identity = identify_static_executable("/usr/bin/example", executable)
    payload = {
        "destination": "/exec/worker",
        "sha256_digest": identity.sha256_digest,
        "mode": 0o500,
        "size": len(executable),
        "destination_device": 0,
        "destination_inode": 1,
        "open_fds": [0, 1, 2],
        "landlock_abi": 1,
        "seccomp_mode": 2,
        "unlisted_path_denied": True,
        "unmounted_host_path_absent": True,
        "memfd_create_denied_errno": errno.EPERM,
        "connect_denied_errno": errno.EPERM,
        "proxy_socket_device": None,
        "proxy_socket_inode": None,
    }
    return identity, payload


def test_ack_binds_executable_policy_and_optional_socket(ack_material):
    identity, payload = ack_material
    ack = parent._parse_ack(payload, identity, payload["size"], 123)
    assert (ack.sha256_digest, ack.open_fds, ack.monitor_pid) == (
        identity.sha256_digest,
        (0, 1, 2),
        123,
    )
    payload.update(open_fds=[0, 1, 2, 3], proxy_socket_device=4, proxy_socket_inode=5)
    ack = parent._parse_ack(
        payload, identity, payload["size"], 123, proxy_fd=3, proxy_socket_identity=(4, 5)
    )
    assert (ack.open_fds, ack.proxy_socket_device, ack.proxy_socket_inode) == ((0, 1, 2, 3), 4, 5)


@pytest.mark.parametrize(
    "field,value",
    [
        ("destination", "/other"),
        ("sha256_digest", "0" * 64),
        ("mode", True),
        ("mode", 0o700),
        ("size", 0),
        ("destination_device", -1),
        ("destination_inode", 0),
        ("landlock_abi", 0),
        ("seccomp_mode", 1),
        ("unlisted_path_denied", 1),
        ("unmounted_host_path_absent", False),
        ("memfd_create_denied_errno", 0),
        ("connect_denied_errno", 0),
        ("open_fds", [0, 1, True]),
        ("open_fds", [0, 1, 2, 4]),
        ("proxy_socket_device", 1),
        ("proxy_socket_inode", 1),
    ],
)
def test_ack_rejects_mismatched_release_evidence(ack_material, field, value):
    identity, payload = ack_material
    expected_size = payload["size"]
    payload[field] = value
    with pytest.raises(ValueError):
        parent._parse_ack(payload, identity, expected_size, 123)


def test_ack_rejects_unknown_missing_fields_and_another_socket(ack_material):
    identity, payload = ack_material
    with pytest.raises(ValueError, match="field set"):
        parent._parse_ack({**payload, "extra": 1}, identity, payload["size"], 123)
    missing = dict(payload)
    missing.pop("destination")
    with pytest.raises(ValueError, match="field set"):
        parent._parse_ack(missing, identity, payload["size"], 123)
    payload.update(open_fds=[0, 1, 2, 3], proxy_socket_device=4, proxy_socket_inode=5)
    with pytest.raises(ValueError, match="required child policy"):
        parent._parse_ack(
            payload, identity, payload["size"], 123, proxy_fd=3, proxy_socket_identity=(4, 6)
        )


def test_sealed_copy_keeps_verified_bytes_and_refuses_mutation(tmp_path):
    content = _minimal_static_elf()
    path = tmp_path / "accepted-binary"
    path.write_bytes(content)
    with path.open("rb") as source:
        parent._validate_elf_executable(source.fileno())
        copied = parent._create_sealed_bwrap_copy(
            source.fileno(), hashlib.sha256(content).hexdigest()
        )
        try:
            assert os.pread(copied, len(content), 0) == content
            assert (
                fcntl.fcntl(copied, parent._F_GET_SEALS) & parent._BWRAP_REQUIRED_SEALS
                == parent._BWRAP_REQUIRED_SEALS
            )
            with pytest.raises(OSError):
                os.pwrite(copied, b"changed", 0)
            with pytest.raises(OSError):
                os.ftruncate(copied, 0)
        finally:
            os.close(copied)


def test_digest_failure_closes_the_unaccepted_copy(tmp_path, monkeypatch):
    path = tmp_path / "changed-binary"
    path.write_bytes(_minimal_static_elf())
    descriptors = []
    original = os.memfd_create

    def record(*args, **kwargs):
        descriptor = original(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(parent.os, "memfd_create", record)
    with path.open("rb") as source, pytest.raises(parent.WorkBubblewrapSetupFailed, match="digest"):
        parent._create_sealed_bwrap_copy(source.fileno(), "0" * 64)
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_pinned_source_rejects_changed_bytes_or_removed_name(tmp_path):
    path = tmp_path / "pinned"
    path.write_bytes(_minimal_static_elf())
    with path.open("rb") as source:
        pinned = parent._PinnedBubblewrap(
            path,
            source.fileno(),
            source.fileno(),
            parent._bubblewrap_file_identity(os.fstat(source.fileno())),
        )
        assert pinned.proc_path.endswith(f"/{source.fileno()}")
        parent._revalidate_pinned_bwrap(pinned)
        path.write_bytes(b"modified")
        with pytest.raises(parent.WorkBubblewrapSetupFailed, match="changed"):
            parent._revalidate_pinned_bwrap(pinned)
        path.unlink()
        with pytest.raises(parent.WorkBubblewrapSetupFailed, match="revalidated"):
            parent._revalidate_pinned_bwrap(pinned)


@pytest.mark.parametrize("content", [b"", b"script", bytes(64)])
def test_accepted_binary_requires_an_elf_header(tmp_path, content):
    path = tmp_path / "not-elf"
    path.write_bytes(content)
    with path.open("rb") as source, pytest.raises(parent.WorkBubblewrapSetupFailed, match="ELF"):
        parent._validate_elf_executable(source.fileno())


def test_accepted_elf_requires_an_executable_type(tmp_path):
    content = bytearray(_minimal_static_elf())
    struct.pack_into("<H", content, 16, 1)
    path = tmp_path / "not-executable"
    path.write_bytes(content)
    with path.open("rb") as source, pytest.raises(parent.WorkBubblewrapSetupFailed, match="type"):
        parent._validate_elf_executable(source.fileno())


def test_socket_identity_accepts_a_socket_and_rejects_a_regular_file(tmp_path):
    first, second = socket.socketpair()
    try:
        assert parent._socket_file_identity(first.fileno()) == (
            os.fstat(first.fileno()).st_dev,
            os.fstat(first.fileno()).st_ino,
        )
    finally:
        first.close()
        second.close()
    path = tmp_path / "file"
    path.write_bytes(b"data")
    with (
        path.open("rb") as stream,
        pytest.raises(parent.WorkBubblewrapSetupFailed, match="not a socket"),
    ):
        parent._socket_file_identity(stream.fileno())


def test_owned_process_pair_is_stopped_and_reaped_without_pid_reuse():
    program = (
        "import subprocess,sys\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        "print(child.pid,flush=True)\n"
        "try:\n"
        " sys.stdin.buffer.read()\n"
        " child.wait()\n"
        "finally:\n"
        " if child.poll() is None: child.kill()\n"
        " child.wait()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    child_pidfd = monitor_pidfd = None
    try:
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 5)[0], "owned child startup timed out"
        child_pid = int(process.stdout.readline())
        monitor_pidfd = parent._open_monitor_pidfd(process)
        assert monitor_pidfd is not None
        child_pidfd = os.pidfd_open(child_pid)
        assert parent._pidfd_target(monitor_pidfd) == process.pid
        assert parent._pidfd_target(child_pidfd) == child_pid
        assert parent._wait_pidfd_readable(child_pidfd, 0) is False
        stdout, stderr, confirmed = parent._kill_and_reap(process, child_pidfd, monitor_pidfd)
        assert confirmed is True
        assert stdout == stderr == b""
        assert process.returncode == 0
        assert parent._wait_pidfd_readable(child_pidfd, 0) is True
    finally:
        if child_pidfd is not None:
            try:
                signal.pidfd_send_signal(child_pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        process.wait(timeout=5)
        parent._close_process_pipes(process)
        parent._close_bubblewrap_descriptors(child_pidfd, monitor_pidfd)


def test_cleanup_timeout_cannot_be_reported_as_confirmed():
    process = SimpleNamespace(
        stdin=io.BytesIO(),
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
        returncode=None,
        wait=Mock(side_effect=subprocess.TimeoutExpired("owned monitor", 0)),
    )
    assert parent._kill_and_reap(process, None, None) == (b"", b"", False)
    assert process.wait.call_count == 2
    assert process.stdin.closed and process.stdout.closed and process.stderr.closed


def test_cleanup_requires_the_supervisor_and_attempt_together():
    with pytest.raises(ValueError, match="together"):
        parent._kill_and_reap(SimpleNamespace(), None, None, process_supervisor=object())


def test_setup_failure_keeps_diagnostics_and_cleanup_certainty():
    process = SimpleNamespace(pid=123)
    for confirmed, kind in [
        (True, parent.WorkBubblewrapSetupFailed),
        (False, parent.WorkBubblewrapCleanupUncertain),
    ]:
        error = parent._setup_failure("setup detail", process, b"out", b"err", confirmed)
        assert type(error) is kind
        assert (error.monitor_pid, error.stdout, error.stderr, error.cleanup_confirmed) == (
            123,
            b"out",
            b"err",
            confirmed,
        )


def test_bootstrap_dependencies_are_closed_and_sorted(tmp_path):
    required = {
        "annotated_types",
        "pydantic",
        "pydantic_core",
        "typing_extensions.py",
        "typing_inspection",
    }
    for name in required | {"pydantic-2.dist-info", "unrelated-1.dist-info"}:
        (tmp_path / name).mkdir()
    result = parent._python_bootstrap_dependency_entries(tmp_path)
    assert set(result) == required | {"pydantic-2.dist-info"}
    assert result == tuple(sorted(result))
    (tmp_path / "pydantic").rmdir()
    with pytest.raises(parent.WorkBubblewrapSetupFailed, match="dependency is unavailable"):
        parent._python_bootstrap_dependency_entries(tmp_path)
    with pytest.raises(parent.WorkBubblewrapSetupFailed, match="directory is unavailable"):
        parent._python_bootstrap_dependency_entries(tmp_path / "missing")
