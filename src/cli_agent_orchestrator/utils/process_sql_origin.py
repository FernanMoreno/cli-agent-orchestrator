"""Process-local SQL attribution for supervised servers; never closure authority."""

from __future__ import annotations

import os
import threading
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
    guard: threading.Lock = field(default_factory=threading.Lock)
    successors: dict[int, _State] = field(default_factory=dict, repr=False)


_STATE = _State(os.getpid())


def _after_fork() -> None:
    global _STATE
    # Never touch the parent's possibly-held mutex or preserve its attribution.
    _STATE = _State(os.getpid(), enabled=_STATE.enabled)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def current_sql_application_name() -> str:
    """Latch opt-in before consuming the inherited FD; other processes stay legacy.

    The PID check precedes every inherited lock, including native-fork paths
    which did not run Python's at-fork callback. The fixed state is per process,
    not a cache growing with advertised agents, stores or logical lifetimes.
    """
    global _STATE
    pid = os.getpid()
    state = _STATE
    if state.pid != pid:
        # Elect one child state atomically even if multiple threads retained
        # the same old PID snapshot. No inherited Python mutex is acquired.
        # The discarded old state is not retained by its successor: O(1)
        # persistent state, not a PID/agent history cache.
        state = state.successors.setdefault(pid, _State(pid, enabled=state.enabled))
        _STATE = state
    with state.guard:
        state.enabled = state.enabled or RESOURCE_BINDING_FD_ENV in os.environ
        if not state.enabled:
            return "caos"
        if state.name is None:
            state.name = SQL_ORIGIN_PREFIX + uuid4().hex
        return state.name
