from __future__ import annotations

import importlib.util
import select
import subprocess
from pathlib import Path

import pytest

_TARGET_PATH = Path(__file__).with_name("test_work_bubblewrap_composition.py")
_SPEC = importlib.util.spec_from_file_location("pidfd_selected_tests", _TARGET_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_SELECTED = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SELECTED)


class _FakeProcess:
    pid = 42001

    def __init__(self) -> None:
        self.alive = True
        self.native_ready = False
        self.kill_calls = 0
        self.wait_calls: list[float | None] = []
        self.reaped = False

    def poll(self) -> int | None:
        return None if self.alive else 0

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if self.alive and not self.native_ready:
            raise subprocess.TimeoutExpired(["/bin/true"], timeout)
        self.alive = False
        self.reaped = True
        return 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.alive = False


class _FakePoller:
    def __init__(self, process: _FakeProcess, fd: int, *, ready: bool) -> None:
        self.process = process
        self.fd = fd
        self.ready = ready
        self.registrations: list[tuple[int, int]] = []
        self.timeouts: list[int] = []

    def register(self, fd: int, events: int) -> None:
        self.registrations.append((fd, events))

    def poll(self, timeout_ms: int) -> list[tuple[int, int]]:
        self.timeouts.append(timeout_ms)
        if not self.ready:
            return []
        self.process.native_ready = True
        self.process.alive = False
        return [(self.fd, select.POLLIN)]


class _FakeApi:
    def __init__(self, process: _FakeProcess) -> None:
        self.process = process
        self.calls: list[tuple[int, float]] = []

    def _wait_pidfd_readable(self, pidfd: int, timeout: float) -> bool:
        assert self.process.native_ready, "native readiness must precede the product poll"
        self.calls.append((pidfd, timeout))
        return True


def _install_fakes(monkeypatch: pytest.MonkeyPatch, *, ready: bool, pidfd_error: bool = False):
    process = _FakeProcess()
    fd = 42002
    closed: list[int] = []
    argv: list[list[str]] = []
    pollers: list[_FakePoller] = []
    api = _FakeApi(process)

    def spawn(args):
        argv.append(list(args))
        return process

    def open_pidfd(pid: int) -> int:
        assert pid == process.pid
        if pidfd_error:
            raise OSError("test pidfd-open failure")
        return fd

    def make_poller() -> _FakePoller:
        poller = _FakePoller(process, fd, ready=ready)
        pollers.append(poller)
        return poller

    monkeypatch.setattr(_SELECTED.subprocess, "Popen", spawn)
    monkeypatch.setattr(_SELECTED.os, "pidfd_open", open_pidfd, raising=False)
    monkeypatch.setattr(_SELECTED.os, "close", closed.append)
    monkeypatch.setattr(_SELECTED.select, "poll", make_poller)
    monkeypatch.setattr(_SELECTED, "_composition_api", lambda: api)
    return process, fd, closed, argv, pollers, api


def test_selected_node_proves_native_readiness_before_zero_timeout_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, fd, closed, argv, pollers, api = _install_fakes(monkeypatch, ready=True)

    _SELECTED.test_wait_pidfd_readable_checks_ready_pidfd_with_zero_timeout()

    assert argv == [["/bin/true"]]
    assert pollers[0].registrations == [(fd, select.POLLIN)]
    assert pollers[0].timeouts == [5000]
    assert api.calls == [(fd, 0.0)]
    assert process.kill_calls == 0
    assert process.reaped is True
    assert closed == [fd]


def test_setup_failure_kills_reaps_owned_child_and_closes_opened_pidfd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, fd, closed, _argv, pollers, api = _install_fakes(monkeypatch, ready=False)

    with pytest.raises(AssertionError):
        _SELECTED.test_wait_pidfd_readable_checks_ready_pidfd_with_zero_timeout()

    assert pollers[0].timeouts == [5000]
    assert api.calls == []
    assert process.kill_calls == 1
    assert process.reaped is True
    assert closed == [fd]


def test_pidfd_open_failure_kills_and_reaps_child_created_by_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, _fd, closed, _argv, pollers, api = _install_fakes(
        monkeypatch, ready=True, pidfd_error=True
    )

    with pytest.raises(OSError, match="test pidfd-open failure"):
        _SELECTED.test_wait_pidfd_readable_checks_ready_pidfd_with_zero_timeout()

    assert pollers == []
    assert api.calls == []
    assert process.kill_calls == 1
    assert process.reaped is True
    assert closed == []
