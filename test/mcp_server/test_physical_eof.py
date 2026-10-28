"""Real descriptor and wire accounting controls; no provider login or database."""

import json
import os
import signal

import pytest

from cli_agent_orchestrator.mcp_server.physical_eof import (
    GuardError,
    ObservedPipe,
    WireState,
    guard_from_environment,
)

pytestmark = pytest.mark.offline_database


def frame(value):
    return (json.dumps({"jsonrpc": "2.0", **value}) + "\n").encode()


def test_reply_is_pending_until_real_flush_and_physical_eof_is_required():
    state = WireState()
    state.feed_input(frame({"id": 1, "method": "ping"}))
    state.feed_output(frame({"id": 1, "result": {}}))
    state.input_eof()
    with pytest.raises(GuardError):
        state.verify_normal()
    state.output_flushed()
    state.verify_normal()
    assert state.snapshot()["pending_client"] == 0
    assert state.snapshot()["physical_eof"] is True


def test_both_rpc_directions_are_independently_owned():
    state = WireState()
    state.feed_input(frame({"id": 1, "method": "ping"}))
    state.feed_output(frame({"id": 1, "method": "ping"}))
    state.feed_input(frame({"id": 1, "result": {}}))
    state.feed_output(frame({"id": 1, "result": {}}))
    state.output_flushed()
    state.input_eof()
    state.verify_normal()


@pytest.mark.parametrize(
    "data",
    [
        b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
        b"not-json\n",
        b"\xff\n",
        b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}\n',
        b'{"jsonrpc":"2.0","id":true,"method":"ping"}\n',
    ],
)
def test_invalid_or_partial_frames_cannot_be_hidden_by_sdk_logging(data):
    state = WireState()
    with pytest.raises(GuardError):
        state.feed_input(data)
        state.input_eof()
        state.verify_normal()


def test_unknown_notification_preserves_execution_but_refuses_normal_close():
    state = WireState()
    state.feed_input(frame({"method": "notifications/unknown", "params": {"text": "PRIVATE"}}))
    state.input_eof()
    with pytest.raises(GuardError) as error:
        state.verify_normal()
    assert "PRIVATE" not in str(error.value)
    assert "PRIVATE" not in json.dumps(state.snapshot())


def test_known_initialized_notification_is_not_unfinished_work():
    state = WireState()
    state.feed_input(frame({"method": "notifications/initialized"}))
    state.input_eof()
    state.verify_normal()


def test_signal_does_not_manufacture_eof_or_restart_deadline(monkeypatch):
    import cli_agent_orchestrator.mcp_server.physical_eof as module

    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    state = WireState()
    state.request_stop(signal.SIGINT)
    now[0] = 101
    state.request_stop(signal.SIGTERM)
    assert state.remaining() == 5
    assert state.snapshot()["physical_eof"] is False
    now[0] = 107
    with pytest.raises(GuardError):
        state.remaining()


def test_opt_in_is_explicit_and_invalid_selector_fails_closed():
    assert guard_from_environment({}) is None
    with pytest.raises(GuardError):
        guard_from_environment({"CAO_INTERNAL_MCP_PHYSICAL_EOF": "unexpected"})


def test_observed_input_preserves_bytes_requires_kernel_eof_and_restores_flags():
    read, write = os.pipe()
    state = WireState()
    pipe = ObservedPipe(read, inbound=True, state=state)
    data = frame({"method": "notifications/initialized"})
    try:
        os.write(write, data)
        assert pipe.read1(4096) == data
        assert state.snapshot()["physical_eof"] is False
        os.close(write)
        write = None
        assert pipe.read1(4096) == b""
        state.verify_normal()
    finally:
        pipe.close()
        assert os.get_blocking(read) is True
        os.close(read)
        if write is not None:
            os.close(write)


def test_observed_output_commits_reply_only_after_actual_flush():
    read, write = os.pipe()
    state = WireState()
    state.feed_input(frame({"id": 1, "method": "ping"}))
    pipe = ObservedPipe(write, inbound=False, state=state)
    data = frame({"id": 1, "result": {}})
    try:
        assert pipe.write(data) == len(data)
        assert os.read(read, 4096) == data
        assert state.snapshot()["pending_client"] == 1
        pipe.flush()
        assert state.snapshot()["pending_client"] == 0
        state.input_eof()
        state.verify_normal()
    finally:
        pipe.close()
        os.close(read)
        os.close(write)


def test_zero_length_read_does_not_manufacture_kernel_eof():
    read, write = os.pipe()
    state = WireState()
    pipe = ObservedPipe(read, inbound=True, state=state)
    try:
        assert pipe.read1(0) == b""
        assert state.snapshot()["physical_eof"] is False
    finally:
        pipe.close()
        os.close(read)
        os.close(write)


def test_partial_descriptor_acquisition_retires_its_exact_duplicate(monkeypatch):
    import cli_agent_orchestrator.mcp_server.physical_eof as module

    read, write = os.pipe()
    duplicated = []
    original = os.dup

    def duplicate(fd):
        result = original(fd)
        duplicated.append(result)
        return result

    def fail_pipe(*args):
        raise OSError("controlled_descriptor_exhaustion")

    monkeypatch.setattr(module.os, "dup", duplicate)
    monkeypatch.setattr(module.os, "pipe2", fail_pipe)
    try:
        with pytest.raises(OSError):
            ObservedPipe(read, inbound=True, state=WireState())
        assert len(duplicated) == 1
        with pytest.raises(OSError):
            os.fstat(duplicated[0])
        assert os.get_blocking(read) is True
    finally:
        os.close(read)
        os.close(write)


def test_unknown_runtime_is_refused_only_when_opt_in_is_present(monkeypatch):
    import cli_agent_orchestrator.mcp_server.physical_eof as module

    monkeypatch.setattr(module, "version", lambda name: "unsupported")
    assert module.guard_from_environment({}) is None
    with pytest.raises(GuardError, match="unsupported_stdio_runtime"):
        module.guard_from_environment({module.ENABLE_ENV: "1"})


@pytest.mark.parametrize("kind", ["file", "tty"])
def test_opt_in_refuses_non_pipe_stdio_without_changing_flags(tmp_path, kind):
    if kind == "file":
        fd = os.open(tmp_path / "regular", os.O_CREAT | os.O_RDWR, 0o600)
        other = None
    else:
        fd, other = os.openpty()
    try:
        before = os.get_blocking(fd)
        with pytest.raises(GuardError, match="stdio_pipe_required"):
            ObservedPipe(fd, inbound=True, state=WireState())
        assert os.get_blocking(fd) is before
    finally:
        os.close(fd)
        if other is not None:
            os.close(other)


def test_existing_process_timer_is_not_stolen(monkeypatch):
    import cli_agent_orchestrator.mcp_server.physical_eof as module

    guard = module.PhysicalEofGuard()
    monkeypatch.setattr(module.signal, "getitimer", lambda _: (1.0, 0.0))
    called = []
    with pytest.raises(GuardError, match="existing_process_timer"):
        guard.run(lambda: called.append(True))
    assert not called


def test_failed_frame_observation_marks_transport_unsafe(monkeypatch):
    state = WireState()

    def failing(raw, *, inbound):
        raise RuntimeError("private-observer-failure")

    monkeypatch.setattr(state, "_frame", failing)
    with pytest.raises(RuntimeError):
        state.feed_input(frame({"id": 1, "method": "ping"}))
    assert state.snapshot()["unsafe"] == "frame_observer_failed"


def test_fatal_frame_starts_one_bounded_close_without_physical_eof(monkeypatch):
    import cli_agent_orchestrator.mcp_server.physical_eof as module

    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    state = WireState()
    with pytest.raises(GuardError):
        state.feed_input(b"invalid\n")
    assert state.remaining() == 6.0
    assert not state.physical_eof
    now[0] = 101.0
    state.begin_close()
    assert state.remaining() == 5.0


def test_expired_deadline_cannot_be_silently_lost_in_loop_callback(monkeypatch):
    import cli_agent_orchestrator.mcp_server.physical_eof as module

    guard = module.PhysicalEofGuard()
    guard.run_active = True
    guard.state.deadline = 0.0

    def expired(status):
        raise SystemExit(status)

    monkeypatch.setattr(module.os, "_exit", expired)
    with pytest.raises(SystemExit) as error:
        guard._arm_process_deadline()
    assert error.value.code == 70
