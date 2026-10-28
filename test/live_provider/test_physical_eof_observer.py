"""Actual guard transitions, transparent failure/exit and content-free evidence."""

import os
import signal
import threading
from test.live_provider.physical_eof_observer import GuardTransitions, read_transitions

import pytest

from cli_agent_orchestrator.mcp_server.physical_eof import ObservedPipe, PhysicalEofGuard

pytestmark = pytest.mark.offline_database


def test_real_pipe_eof_and_stop_are_distinct_and_original_hooks_restore(tmp_path):
    tmp_path.chmod(0o700)
    guard = PhysicalEofGuard()
    original_eof = guard.state.input_eof
    original_stop = guard.state.request_stop
    original_exit = os._exit
    read, write = os.pipe()
    pipe = ObservedPipe(read, inbound=True, state=guard.state)
    try:
        with GuardTransitions(guard, tmp_path / "events.jsonl", tmp_path) as audit:
            guard.state.request_stop(signal.SIGINT)
            assert not guard.state.physical_eof
            os.close(write)
            write = None
            assert pipe.read1(1) == b""
            audit.event("lifespan_returned")
        rows = read_transitions(tmp_path / "events.jsonl")
        assert [r["event"] for r in rows] == ["stop_signal", "physical_eof", "lifespan_returned"]
        assert rows[0]["state"]["physical_eof"] is False
        assert rows[1]["state"]["physical_eof"] is True
        assert rows[0]["status"] == signal.SIGINT
        assert not audit.failed
        assert guard.state.input_eof == original_eof
        assert guard.state.request_stop == original_stop
        assert os._exit is original_exit
    finally:
        pipe.close()
        os.close(read)
        if write is not None:
            os.close(write)


def test_original_exception_identity_and_requested_exit_are_preserved(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    guard = PhysicalEofGuard()
    error = RuntimeError("PRIVATE ORIGINAL EXCEPTION")

    def stopped(_):
        raise error

    guard.state.request_stop = stopped
    exits = []

    def exiting(status):
        exits.append(status)
        raise SystemExit(status)

    monkeypatch.setattr(os, "_exit", exiting)
    with GuardTransitions(guard, tmp_path / "events.jsonl", tmp_path):
        with pytest.raises(RuntimeError) as caught:
            guard.state.request_stop(signal.SIGTERM)
        assert caught.value is error
        with pytest.raises(SystemExit) as caught:
            os._exit(70)
        assert caught.value.code == 70
    rows = read_transitions(tmp_path / "events.jsonl")
    assert [r["event"] for r in rows] == ["stop_signal", "exit_requested"]
    assert exits == [70]
    assert "PRIVATE" not in (tmp_path / "events.jsonl").read_text()
    assert os._exit is exiting


def test_whitelist_never_serializes_unknown_snapshot_content(tmp_path):
    tmp_path.chmod(0o700)
    guard = PhysicalEofGuard()
    guard.state.fail("PRIVATE UNSAFE VALUE")
    with GuardTransitions(guard, tmp_path / "events.jsonl", tmp_path) as audit:
        audit.event("lifespan_entered")
    assert read_transitions(tmp_path / "events.jsonl")[0]["state"]["unsafe"] == "other"
    assert "PRIVATE" not in (tmp_path / "events.jsonl").read_text()
    assert (tmp_path / "events.jsonl").stat().st_mode & 0o777 == 0o600


def test_snapshot_lock_contention_does_not_delay_exit_hook(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    guard = PhysicalEofGuard()
    entered, release = threading.Event(), threading.Event()

    def holder():
        with guard.state.lock:
            entered.set()
            assert release.wait(5)

    thread = threading.Thread(target=holder)

    def exiting(status):
        raise SystemExit(status)

    monkeypatch.setattr(os, "_exit", exiting)
    try:
        with GuardTransitions(guard, tmp_path / "events.jsonl", tmp_path):
            thread.start()
            assert entered.wait(5)
            with pytest.raises(SystemExit):
                os._exit(70)
            assert thread.is_alive()
        assert read_transitions(tmp_path / "events.jsonl")[0]["state"] is None
    finally:
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_audit_limit_or_write_failure_does_not_change_actual_stop(tmp_path):
    tmp_path.chmod(0o700)
    guard = PhysicalEofGuard()
    with GuardTransitions(guard, tmp_path / "events.jsonl", tmp_path, max_rows=1) as audit:
        guard.state.request_stop(signal.SIGINT)
        guard.state.request_stop(signal.SIGTERM)
        assert audit.failed
        assert guard.state.signal_count == 2
    assert len(read_transitions(tmp_path / "events.jsonl")) == 1


def test_partial_private_write_is_failed_evidence_not_a_changed_original(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    guard = PhysicalEofGuard()
    with GuardTransitions(guard, tmp_path / "events.jsonl", tmp_path) as audit:
        monkeypatch.setattr(audit, "_append", lambda _: False)
        guard.state.request_stop(signal.SIGTERM)
        assert audit.failed
        assert guard.state.signal_count == 1


@pytest.mark.parametrize("raw", [b'{"event":"exit_requested"}\n', b"PRIVATE\n", b"{}", b"{}\n{}\n"])
def test_reader_refuses_invalid_or_partial_evidence(tmp_path, raw):
    path = tmp_path / "bad.jsonl"
    path.write_bytes(raw)
    path.chmod(0o600)
    with pytest.raises(ValueError):
        read_transitions(path)


@pytest.mark.parametrize("mode", ["arm_expired", "process_timer", "watchdog"])
def test_real_process_exit_paths_retain_fixed_private_evidence(tmp_path, mode):
    import subprocess
    import sys
    from pathlib import Path

    tmp_path.chmod(0o700)
    repo = Path(__file__).resolve().parents[2]
    script = """import asyncio,os,signal,sys,time
from pathlib import Path
from cli_agent_orchestrator.mcp_server.physical_eof import PhysicalEofGuard
from test.live_provider.physical_eof_observer import GuardTransitions
root=Path(sys.argv[1]);mode=sys.argv[2];guard=PhysicalEofGuard()
def expired():
    guard.state.deadline=time.monotonic()-1
    guard._arm_process_deadline()
def timer():
    guard.state.begin_close()
    guard._arm_process_deadline()
    time.sleep(30)
async def watched():
    async with guard.lifespan(object()):
        os.kill(os.getpid(),signal.SIGINT)
        await asyncio.sleep(0.05)
        assert sys.stdin.buffer.read1(1)==b''
        await asyncio.sleep(0.05)
        # Exercise the existing in-loop fallback independently of the OS timer.
        # The real six-second deadline is unchanged.
        signal.setitimer(signal.ITIMER_REAL,0)
        await asyncio.sleep(30)
with GuardTransitions(guard,root/'events.jsonl',root):
    guard.run(expired if mode=='arm_expired' else timer if mode=='process_timer' else lambda:asyncio.run(watched()))
raise RuntimeError('exit path returned unexpectedly')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), mode],
        input=b"",
        capture_output=True,
        timeout=30,
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "CAO_HOME_DIR": str(tmp_path / "cao"),
            "PYTHONPATH": str(repo / "src") + ":" + str(repo),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
        },
    )
    assert result.returncode == 70, "actual guard exit not observed; inspect private child evidence"
    assert result.stdout == b""
    rows = read_transitions(tmp_path / "events.jsonl")
    exits = [r for r in rows if r["event"] == "exit_requested"]
    assert len(exits) == 1 and exits[0]["status"] == 70
    assert exits[0]["state"] is None
    if mode == "watchdog":
        stops = [r for r in rows if r["event"] == "stop_signal"]
        eof = [r for r in rows if r["event"] == "physical_eof"]
        assert len(stops) == len(eof) == 1
        assert stops[0]["status"] == signal.SIGINT
        assert eof[0]["state"]["physical_eof"] is True
        elapsed = (exits[0]["at_ns"] - stops[0]["at_ns"]) / 1e9
        assert 5.8 <= elapsed <= 6.5


def test_invalid_structured_record_has_content_free_error(tmp_path):
    path = tmp_path / "private.jsonl"
    path.write_text('{"event":"PRIVATE_INPUT_MARKER","at_ns":1,"state":null}\n')
    path.chmod(0o600)
    with pytest.raises(ValueError) as caught:
        read_transitions(path)
    assert str(caught.value) == "transition_evidence_invalid"


def test_actual_owned_zombie_kernel_status_and_reaping_are_distinct():
    import time
    from pathlib import Path
    from test.live_provider.physical_eof_observer import observe_owned_identity

    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(write)
        os.read(read, 1)
        os._exit(7)
    os.close(read)
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
        identity = {"pid": pid, "uid": os.geteuid(), "birth": int(fields[19])}
        assert observe_owned_identity(identity) == {"state": "alive"}
        os.write(write, b"x")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            observed = observe_owned_identity(identity)
            if observed["state"] == "zombie":
                break
            time.sleep(0.01)
        assert observed == {"state": "zombie", "kernel_wait_status": 7 << 8, "exit_status": 7}
        assert os.waitpid(pid, 0) == (pid, 7 << 8)
        pid = None
        assert observe_owned_identity(identity) == {"state": "absent"}
    finally:
        os.close(write)
        if pid is not None:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
