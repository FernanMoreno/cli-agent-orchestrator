"""Process-local SQL attribution for supervised servers; never closure authority."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import uuid4

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
    guard: threading.Lock = field(default_factory=threading.Lock)
    successors: dict[int, _State] = field(default_factory=dict, repr=False)


_STATE = _State(os.getpid())


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
def sql_process_opening() -> Iterator[None]:
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
    try:
        yield
    except BaseException:
        uncertain = True
        raise
    finally:
        # A child cannot retire its parent's attempt or touch an inherited lock.
        # Its at-fork/PID successor already retained inherited uncertainty.
        if controlled and os.getpid() == state.pid:
            with state.guard:
                if uncertain:
                    state.unconfirmed += 1
                # at-fork reads without this mutex. Publish uncertainty first:
                # a snapshot may overcount, but must never lose the attempt.
                state.opening -= 1
