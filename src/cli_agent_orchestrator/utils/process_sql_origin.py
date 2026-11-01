"""Process-local SQL attribution for supervised servers; never closure authority."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import uuid4

from cli_agent_orchestrator.models.sql_opening_intent import SqlOpeningPreparationUncertain
from cli_agent_orchestrator.models.sql_process_origin import (
    RESOURCE_BINDING_FD_ENV,
    SQL_ORIGIN_PREFIX,
)


@dataclass
class _State:
    pid: int
    enabled: bool = False
    name: str | None = None
    sealed: bool = False
    opening: int = 0
    unconfirmed: int = 0
    registration_unconfirmed: int = 0
    guard: threading.Lock = field(default_factory=threading.Lock)
    successors: dict[int, _State] = field(default_factory=dict, repr=False)


_STATE = _State(os.getpid())


class SqlOpeningRecorderUnavailable(RuntimeError):
    """Recording failed before TCP; recursive recording must never be bypassed."""


def _recording_task() -> object | None:
    # Legacy/unrecorded openings do not pay for importing asyncio here.
    import asyncio

    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


@dataclass(frozen=True, eq=False, repr=False)
class _SqlRecorder:
    callback: Callable[[str, object | None], object]
    pid: int
    thread: threading.Thread
    task: object | None

    def _check_worker(self) -> None:
        if (
            self.pid != os.getpid()
            or self.thread is not threading.current_thread()
            or self.task is not _recording_task()
        ):
            raise SqlOpeningRecorderUnavailable("SQL recorder belongs to another physical worker")

    def prepare(self, path: str, destination: object | None) -> None:
        self._check_worker()
        if self.callback(path, destination) is not None:
            raise SqlOpeningRecorderUnavailable("SQL recorder did not acknowledge its commit")
        self._check_worker()


_RECORDER: ContextVar[_SqlRecorder | None] = ContextVar("cao_sql_opening_recorder", default=None)
_RECORDING: ContextVar[bool] = ContextVar("cao_sql_opening_recording", default=False)


@contextmanager
def sql_opening_recorder(callback: Callable[[str, object | None], object]) -> Iterator[None]:
    if not callable(callback) or _RECORDER.get() is not None:
        raise SqlOpeningRecorderUnavailable("SQL recorders cannot shadow an active recorder")
    token = _RECORDER.set(
        _SqlRecorder(callback, os.getpid(), threading.current_thread(), _recording_task())
    )
    try:
        yield
    finally:
        _RECORDER.reset(token)


def _after_fork() -> None:
    global _STATE
    # Never touch the parent's possibly-held mutex or preserve its attribution.
    _STATE = _successor(_STATE, os.getpid())


def _successor(state: _State, pid: int) -> _State:
    return _State(
        pid,
        enabled=state.enabled,
        sealed=state.sealed,
        # A fork during an opening may inherit an untracked libpq socket.
        # Preserve uncertainty, never treat a new local counter as absence.
        unconfirmed=state.unconfirmed + state.opening,
        registration_unconfirmed=state.registration_unconfirmed,
    )


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _process_state() -> _State:
    global _STATE
    pid = os.getpid()
    state = _STATE
    if state.pid != pid:
        # Elect one child state atomically even if multiple threads retained
        # the same old PID snapshot. No inherited Python mutex is acquired.
        # The discarded old state is not retained by its successor: O(1)
        # persistent state, not a PID/agent history cache.
        state = state.successors.setdefault(pid, _successor(state, pid))
        _STATE = state
    return state


def current_sql_application_name() -> str:
    """Latch opt-in; PID resolution always precedes an inherited mutex."""
    state = _process_state()
    with state.guard:
        state.enabled = state.enabled or RESOURCE_BINDING_FD_ENV in os.environ
        if not state.enabled:
            return "caos"
        if state.name is None:
            state.name = SQL_ORIGIN_PREFIX + uuid4().hex
        return state.name


class SqlProcessOpeningsSealed(RuntimeError):
    """The terminal process refused this physical attempt before driver entry."""


def seal_sql_process_openings() -> bool:
    """Irreversibly refuse later attempts; never await or certify session closure."""
    state = _process_state()
    with state.guard:
        state.enabled = state.enabled or RESOURCE_BINDING_FD_ENV in os.environ
        if not state.enabled:
            return False
        state.sealed = True
        return True


@contextmanager
def sql_process_opening(
    *, path: str = "native", destination: object | None = None
) -> Iterator[None]:
    """Admit before TCP without holding the mutex during the driver call.

    Calls admitted before the fence may finish afterward. These local counters
    neither inventory server backends nor authorize resource retirement.
    """
    state = _process_state()
    with state.guard:
        state.enabled = state.enabled or RESOURCE_BINDING_FD_ENV in os.environ
        controlled = state.enabled
        if controlled:
            if state.sealed:
                raise SqlProcessOpeningsSealed("terminal process refused a new SQL opening")
            state.opening += 1
    uncertain = False
    entered_driver = False
    registration_uncertain = False
    try:
        recorder = _RECORDER.get()
        if recorder is not None:
            if _RECORDING.get():
                raise SqlOpeningRecorderUnavailable("Recursive SQL opening registration is refused")
            token = _RECORDING.set(True)
            try:
                recorder.prepare(path, destination)
            finally:
                _RECORDING.reset(token)
        entered_driver = True
        yield
    except BaseException as failure:
        uncertain = entered_driver
        registration_uncertain = not entered_driver and isinstance(
            failure, SqlOpeningPreparationUncertain
        )
        raise
    finally:
        # A child cannot retire its parent's attempt or touch an inherited lock.
        # Its at-fork/PID successor already retained inherited uncertainty.
        if controlled and os.getpid() == state.pid:
            with state.guard:
                if registration_uncertain:
                    state.registration_unconfirmed += 1
                if uncertain:
                    state.unconfirmed += 1
                # at-fork reads without this mutex. Publish uncertainty first:
                # a snapshot may overcount, but must never lose the attempt.
                state.opening -= 1
