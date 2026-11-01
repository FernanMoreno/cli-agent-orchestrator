"""Private per-invocation native lifecycle, separate from the joined Python body.

Backends declare that they observe the whole native lifecycle, mark the first
possible native start, retain exact handles, and confirm positive closure.
Unknown/custom backends that passed the effect gate remain charged without a
positive close. Lifecycle hooks are private trusted-server composition: every
backend must pass the protected gate before a managed native effect. Skipping
that gate violates the existing execution authorization contract.
No PID, TTL, exception text or callback completion is a closure witness.
"""

import contextvars
import os
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.services.delivery_admission import (
    DeliveryReservation,
    PhysicalEffectReservation,
)


@dataclass(eq=False)
class _NativeEffect:
    token: PhysicalEffectReservation | None
    pid: int
    thread: int
    observed: bool = False
    gated: bool = False
    attempted: bool = False
    closed: bool = False
    unobserved_start: bool = False

    def current(self) -> None:
        if self.pid != os.getpid() or self.thread != threading.get_ident():
            raise StorageAdmissionUnavailable()

    def gate(self) -> None:
        self.current()
        self.gated = True

    def mark_unobserved_start(self) -> None:
        # A captured marker may add an obligation from a retained helper thread.
        # It can never release resources, authorize work or move to another PID.
        if self.pid != os.getpid():
            raise StorageAdmissionUnavailable()
        if self.token is not None and not self.token._mark_unobserved_start():
            return
        self.unobserved_start = True
        self.attempted = True
        self.closed = False


_EFFECT: contextvars.ContextVar[_NativeEffect | None] = contextvars.ContextVar(
    "cao_work_native_effect", default=None
)
_START_MARKER: contextvars.ContextVar[Callable[[], None] | None] = contextvars.ContextVar(
    "cao_native_start_marker", default=None
)


def capture_native_start_marker() -> Callable[[], None]:
    effect = _EFFECT.get()
    if effect is None:
        return lambda: None
    effect.current()
    return effect.mark_unobserved_start


@contextmanager
def native_start_marker_scope(marker: Callable[[], None]) -> Iterator[None]:
    captured = _START_MARKER.set(marker)
    try:
        yield
    finally:
        _START_MARKER.reset(captured)


def mark_native_start_unobserved() -> None:
    marker = _START_MARKER.get()
    if marker is not None:
        marker()
        return
    effect = _EFFECT.get()
    if effect is not None:
        # A copied context carries no positive thread authority. Adding a
        # constraint still targets only its exact live token and original PID.
        effect.mark_unobserved_start()


@contextmanager
def physical_effect_scope(lease: DeliveryReservation | None) -> Iterator[_NativeEffect]:
    token = lease.hold_physical_effect() if lease is not None else None
    effect = _NativeEffect(token, os.getpid(), threading.get_ident())
    marker = _EFFECT.set(effect)
    try:
        yield effect
    finally:
        _EFFECT.reset(marker)
        effect.current()
        if (
            token is not None
            and not effect.unobserved_start
            and (
                effect.closed
                or (effect.observed and not effect.attempted)
                or (not effect.gated and not effect.attempted)
            )
        ):
            if not token.finish() and effect.closed:
                try:
                    token._register_late()
                except Exception:
                    pass  # A failed registration never masks the body or frees its charge.


def observe_native_effect() -> None:
    effect = _EFFECT.get()
    if effect is not None:
        effect.current()
        effect.observed = True


def begin_native_effect() -> None:
    effect = _EFFECT.get()
    if effect is not None:
        effect.current()
        effect.attempted = True
        effect.closed = False


def retain_native_effect_owner(native_owner: object, closed: Callable[[], bool]) -> None:
    effect = _EFFECT.get()
    if effect is not None:
        effect.current()
        effect.attempted = True
        effect.closed = False
        if effect.token is not None:
            effect.token.retain(native_owner, closed)


def confirm_native_effect_closed() -> None:
    effect = _EFFECT.get()
    if effect is not None:
        effect.current()
        if effect.token is None:
            if not effect.unobserved_start:
                effect.closed = True
            return
        owner = effect.token._owner()
        with owner._guard:
            # A helper may have installed the token obligation before updating
            # the origin scope's flags. Never overwrite that concurrent marker.
            if not effect.unobserved_start and not effect.token._unobserved_starts:
                effect.closed = True


def observe_process_close(process: subprocess.Popen[bytes]) -> Callable[[], bool]:
    """Pin the exact native object, rather than a reassigned caller variable."""
    return lambda: process.poll() is not None


def observe_thread_close(thread: threading.Thread) -> Callable[[], bool]:
    # A start attempt can fail before its acknowledgement. An unstarted object
    # is not a terminal witness; the exact thread must have acquired identity.
    return lambda: thread.ident is not None and not thread.is_alive()
