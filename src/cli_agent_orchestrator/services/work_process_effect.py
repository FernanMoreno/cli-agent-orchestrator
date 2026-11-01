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

    def current(self) -> None:
        if self.pid != os.getpid() or self.thread != threading.get_ident():
            raise StorageAdmissionUnavailable()

    def gate(self) -> None:
        self.current()
        self.gated = True


_EFFECT: contextvars.ContextVar[_NativeEffect | None] = contextvars.ContextVar(
    "cao_work_native_effect", default=None
)


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
        if token is not None and (
            effect.closed
            or (effect.observed and not effect.attempted)
            or (not effect.gated and not effect.attempted)
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
        effect.closed = True


def observe_process_close(process: subprocess.Popen[bytes]) -> Callable[[], bool]:
    """Pin the exact native object, rather than a reassigned caller variable."""
    return lambda: process.poll() is not None


def observe_thread_close(thread: threading.Thread) -> Callable[[], bool]:
    # A start attempt can fail before its acknowledgement. An unstarted object
    # is not a terminal witness; the exact thread must have acquired identity.
    return lambda: thread.ident is not None and not thread.is_alive()
