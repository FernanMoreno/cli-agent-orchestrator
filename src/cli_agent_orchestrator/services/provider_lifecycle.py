"""Process-local startup/retirement admission; no I/O under the registry lock."""

import logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Callable, Concatenate, ParamSpec

logger = logging.getLogger(__name__)
_RetirementParams = ParamSpec("_RetirementParams")


@dataclass(eq=False)
class StartupOwner:
    terminal_id: str
    uncertain: bool = False


@dataclass(eq=False)
class _Retirement:
    owner: object
    refs: int = 1


_guard = threading.Lock()
_startups: dict[str, StartupOwner] = {}
_retirements: dict[str, _Retirement] = {}
_startup_owner: ContextVar[StartupOwner | None] = ContextVar("provider_startup_owner", default=None)
_retirement_owner: ContextVar[object | None] = ContextVar("provider_retirement_owner", default=None)


def reserve_startup(terminal_id: str, *, replace_retired: bool = False) -> StartupOwner:
    with _guard:
        retiring = _retirements.get(terminal_id)
        transferred = (
            replace_retired
            and retiring is not None
            and retiring.owner is _retirement_owner.get()
            and retiring.refs == 1
        )
        if terminal_id in _startups or (retiring is not None and not transferred):
            raise RuntimeError("terminal startup or retirement is already active")
        owner = StartupOwner(terminal_id)
        _startups[terminal_id] = owner
        return owner


def finish_startup(owner: StartupOwner | None) -> None:
    if owner is None:
        return
    with _guard:
        if not owner.uncertain and _startups.get(owner.terminal_id) is owner:
            del _startups[owner.terminal_id]


def retain_uncertain_startup(owner: StartupOwner | None) -> None:
    if owner is None:
        return
    with _guard:
        if _startups.get(owner.terminal_id) is owner:
            owner.uncertain = True
    logger.warning(
        "Terminal %s startup outcome uncertain; retaining local ownership",
        owner.terminal_id,
    )


@contextmanager
def startup_context(owner: StartupOwner | None):
    token = _startup_owner.set(owner)
    try:
        yield
    finally:
        _startup_owner.reset(token)


@contextmanager
def retirement_scope(terminal_id: str):
    """Logical exclusion, with no mutex held across the caller's teardown.

    A certain startup's own rollback may reenter. Ambiguous cancellation cannot
    authorize even owner cleanup; the current row remains a reconciliation handle.
    Retirements also reenter in their copied context, without removing outer state.
    """
    startup = _startup_owner.get()
    retirement = _retirement_owner.get()
    joined = None
    with _guard:
        active = _startups.get(terminal_id)
        retiring = _retirements.get(terminal_id)
        if active is not None:
            admitted = active is startup and not active.uncertain
        elif retiring is not None:
            admitted = retiring.owner is retirement
            if admitted:
                retiring.refs += 1
                joined = retiring
        else:
            retirement = retirement or object()
            joined = _Retirement(retirement)
            _retirements[terminal_id] = joined
            admitted = True
    token = _retirement_owner.set(retirement)
    try:
        yield admitted
    finally:
        _retirement_owner.reset(token)
        if joined is not None:
            with _guard:
                joined.refs -= 1
                if joined.refs == 0 and _retirements.get(terminal_id) is joined:
                    del _retirements[terminal_id]


def guard_retirement(
    function: Callable[Concatenate[str, _RetirementParams], bool],
) -> Callable[Concatenate[str, _RetirementParams], bool]:
    """Protect direct runtime/row helpers as well as their enclosing callers."""

    @wraps(function)
    def wrapped(
        terminal_id: str, *args: _RetirementParams.args, **kwargs: _RetirementParams.kwargs
    ) -> bool:
        with retirement_scope(terminal_id) as admitted:
            if not admitted:
                logger.warning(
                    "Terminal %s startup is still owned; retirement deferred", terminal_id
                )
                return False
            return function(terminal_id, *args, **kwargs)

    return wrapped
