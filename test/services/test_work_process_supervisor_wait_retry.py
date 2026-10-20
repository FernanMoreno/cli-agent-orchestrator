"""Hermetic regressions for the pidfd/Popen wait boundary (no processes or DB)."""

from __future__ import annotations

import subprocess
import threading
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import work_process_supervisor as supervisor_module

pytestmark = pytest.mark.offline_database


class _ScriptedMonitor:
    def __init__(
        self,
        *,
        result: int = -9,
        first_error: BaseException | None = None,
        later_error: BaseException | None = None,
        on_later_wait=None,
    ) -> None:
        self.result = result
        self.first_error = first_error
        self.later_error = later_error
        self.on_later_wait = on_later_wait
        self.calls: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(timeout)
        if len(self.calls) == 1:
            assert timeout == 0
            if self.first_error is not None:
                raise self.first_error
            raise subprocess.TimeoutExpired("synthetic-monitor", timeout=0)

        if self.on_later_wait is not None:
            self.on_later_wait(timeout)
        if self.later_error is not None:
            raise self.later_error
        return self.result


def _assert_lock_available_to_another_thread(lock) -> None:
    acquired: list[bool] = []
    completed = threading.Event()

    def contend() -> None:
        got_lock = lock.acquire(timeout=0.5)
        acquired.append(got_lock)
        if got_lock:
            lock.release()
        completed.set()

    thread = threading.Thread(target=contend)
    thread.start()
    assert completed.wait(timeout=1), "lock probe thread did not finish"
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert acquired == [True], "positive monitor wait ran while supervisor lock was held"


@pytest.fixture
def case_factory(monkeypatch):
    closed: list[tuple[str, int]] = []

    def close_pidfd(attempt) -> None:
        if attempt._pidfd is not None:
            closed.append(("pidfd", attempt._pidfd))
            attempt._pidfd = None

    def close_monitor_pidfd(attempt) -> None:
        if attempt._monitor_pidfd is not None:
            closed.append(("monitor_pidfd", attempt._monitor_pidfd))
            attempt._monitor_pidfd = None

    monkeypatch.setattr(
        supervisor_module.WorkProcessSupervisor,
        "_close_pidfd",
        staticmethod(close_pidfd),
    )
    monkeypatch.setattr(
        supervisor_module.WorkProcessSupervisor,
        "_close_monitor_pidfd",
        staticmethod(close_monitor_pidfd),
    )

    def make_case(
        monitor: _ScriptedMonitor | None = None,
        *,
        owned: bool = True,
        pidfd: int | None = 52,
        monitor_pidfd: int | None = 51,
        readable: bool | None = True,
    ):
        supervisor = supervisor_module.WorkProcessSupervisor()
        attempt = supervisor_module.WorkProcessAttempt(
            command=("/bin/true",),
            monitor_pid=4312,
            _monitor=None,
            _state=supervisor_module.WorkProcessState.RUNNING,
            _pidfd=pidfd,
            _monitor_pidfd=monitor_pidfd,
            _identity=(
                supervisor_module.WorkProcessIdentity(
                    boot_id="synthetic-boot",
                    monitor_pid=4312,
                    monitor_start_time_ticks=1,
                    init_pid=4313,
                    init_start_time_ticks=1,
                    init_parent_pid=4312,
                    pid_namespace=(1, 1),
                    net_namespace=(1, 2),
                    ipc_namespace=(1, 3),
                    monitor_argv=("unshare",),
                )
                if owned
                else None
            ),
        )
        supervisor._attempt = attempt
        monkeypatch.setattr(attempt, "_monitor", monitor)
        monkeypatch.setattr(supervisor, "_pidfd_is_readable", lambda _fd: readable)
        return supervisor, attempt

    return make_case, closed


def _install_clock(monkeypatch, values: list[float]):
    ticks = iter(values)
    calls: list[float] = []

    def monotonic() -> float:
        value = next(ticks)
        calls.append(value)
        return value

    # Replace this module's reference, never time.monotonic process-wide.
    monkeypatch.setattr(supervisor_module, "time", SimpleNamespace(monotonic=monotonic))
    return calls


def test_retry_uses_fresh_remaining_deadline_and_releases_global_lock(monkeypatch, case_factory):
    make_case, closed = case_factory
    supervisor_holder = {}
    monitor = _ScriptedMonitor(
        on_later_wait=lambda _timeout: _assert_lock_available_to_another_thread(
            supervisor_holder["supervisor"]._lock
        )
    )
    supervisor, attempt = make_case(monitor)
    supervisor_holder["supervisor"] = supervisor
    _install_clock(monkeypatch, [100.0, 103.0, 103.25])

    state = supervisor.wait(attempt, timeout=8.0)

    assert state is supervisor_module.WorkProcessState.TERMINATED
    assert attempt.returncode == -9
    assert attempt.reason is None
    assert monitor.calls == [0, 4.75]
    assert closed == [("pidfd", 52), ("monitor_pidfd", 51)]


def test_retry_is_skipped_if_scheduler_pause_consumed_original_deadline(monkeypatch, case_factory):
    make_case, closed = case_factory
    monitor = _ScriptedMonitor()
    supervisor, attempt = make_case(monitor)
    # Deadline is 108. The first check sees 5 seconds; the pre-wait check sees
    # 109, so no positive wait may begin after the caller's budget expires.
    clock_calls = _install_clock(monkeypatch, [100.0, 103.0, 109.0])

    state = supervisor.wait(attempt, timeout=8.0)

    assert state is supervisor_module.WorkProcessState.UNCERTAIN
    assert monitor.calls == [0]
    assert attempt.reason == ("monitor pidfd became readable before its child status was waitable")
    assert clock_calls == [100.0, 103.0, 109.0]
    assert closed == []


def test_timeout_zero_remains_nonblocking(monkeypatch, case_factory):
    make_case, _closed = case_factory
    monitor = _ScriptedMonitor()
    supervisor, attempt = make_case(monitor)
    _install_clock(monkeypatch, [100.0, 100.0])

    state = supervisor.wait(attempt, timeout=0)

    assert state is supervisor_module.WorkProcessState.UNCERTAIN
    assert monitor.calls == [0]


def test_no_deadline_keeps_unbounded_wait_semantics(monkeypatch, case_factory):
    make_case, _closed = case_factory
    monitor = _ScriptedMonitor()
    supervisor, attempt = make_case(monitor)
    monkeypatch.setattr(
        supervisor_module,
        "time",
        SimpleNamespace(monotonic=lambda: pytest.fail("None timeout used a deadline")),
    )
    monitor.on_later_wait = lambda timeout: _assert_lock_available_to_another_thread(
        supervisor._lock
    )

    state = supervisor.wait(attempt, timeout=None)

    assert state is supervisor_module.WorkProcessState.TERMINATED
    assert monitor.calls == [0, None]


@pytest.mark.parametrize("at_positive_wait", [False, True])
def test_monitor_errors_remain_uncertain(monkeypatch, case_factory, at_positive_wait):
    make_case, _closed = case_factory
    error = OSError("synthetic monitor error")
    monitor = _ScriptedMonitor(
        first_error=None if at_positive_wait else error,
        later_error=error if at_positive_wait else None,
    )
    supervisor, attempt = make_case(monitor)
    if at_positive_wait:
        _install_clock(monkeypatch, [100.0, 103.0, 103.25])
    else:
        _install_clock(monkeypatch, [100.0])

    state = supervisor.wait(attempt, timeout=8.0)

    assert state is supervisor_module.WorkProcessState.UNCERTAIN
    assert "could not reap unshare monitor" in (attempt.reason or "")
    assert monitor.calls == ([0, 4.75] if at_positive_wait else [0])


def test_positive_wait_timeout_remains_uncertain(monkeypatch, case_factory):
    make_case, _closed = case_factory
    monitor = _ScriptedMonitor(
        later_error=subprocess.TimeoutExpired("synthetic-monitor", timeout=4.75)
    )
    supervisor, attempt = make_case(monitor)
    _install_clock(monkeypatch, [100.0, 103.0, 103.25])

    state = supervisor.wait(attempt, timeout=8.0)

    assert state is supervisor_module.WorkProcessState.UNCERTAIN
    assert monitor.calls == [0, 4.75]


@pytest.mark.parametrize(
    ("owned", "pidfd", "monitor_pidfd", "readable", "reason_fragment"),
    [
        (False, 52, 51, True, "unverified pidfd ownership"),
        (True, 52, 51, None, "pidfd poll error"),
        (True, None, 51, True, "pidfds are required"),
        (True, 52, None, True, "pidfds are required"),
    ],
)
def test_unproven_ownership_or_pidfds_never_reach_monitor_wait(
    case_factory, owned, pidfd, monitor_pidfd, readable, reason_fragment
):
    make_case, _closed = case_factory
    monitor = _ScriptedMonitor()
    supervisor, attempt = make_case(
        monitor,
        owned=owned,
        pidfd=pidfd,
        monitor_pidfd=monitor_pidfd,
        readable=readable,
    )

    state = supervisor.wait(attempt, timeout=1.0)

    assert state is supervisor_module.WorkProcessState.UNCERTAIN
    assert reason_fragment in (attempt.reason or "")
    assert monitor.calls == []


@pytest.mark.parametrize("changed", ["monitor", "pidfd", "monitor_pidfd"])
def test_rebound_monitor_or_pidfd_stays_uncertain(monkeypatch, case_factory, changed):
    make_case, closed = case_factory
    monitor = _ScriptedMonitor()
    supervisor, attempt = make_case(monitor)

    def rebind(_timeout) -> None:
        if changed == "monitor":
            attempt._monitor = _ScriptedMonitor()
        elif changed == "pidfd":
            attempt._pidfd = 99
        else:
            attempt._monitor_pidfd = 98

    monitor.on_later_wait = rebind
    _install_clock(monkeypatch, [100.0, 103.0, 103.25])

    state = supervisor.wait(attempt, timeout=8.0)

    assert state is supervisor_module.WorkProcessState.UNCERTAIN
    assert attempt.reason == "monitor ownership changed while awaiting child status"
    assert closed == []


def test_reattached_attempt_without_popen_uses_pidfd_proof(monkeypatch, case_factory):
    make_case, closed = case_factory
    supervisor, attempt = make_case(
        monitor=None,
        readable=True,
    )
    attempt._identity = None
    attempt._external_process_identity = {"reattached": True}
    _install_clock(monkeypatch, [100.0])

    state = supervisor.wait(attempt, timeout=0)

    assert state is supervisor_module.WorkProcessState.TERMINATED
    assert attempt.reason is None
    assert closed == [("pidfd", 52), ("monitor_pidfd", 51)]
