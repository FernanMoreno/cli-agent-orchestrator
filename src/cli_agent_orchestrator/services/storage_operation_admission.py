"""Trusted service lifecycle and short normal SQL scopes, independent of providers."""

import asyncio
import math
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import NoReturn

from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    _current_storage_owner,
    check_storage_admission,
    note_storage_denial,
    physical_storage_scope,
)
from cli_agent_orchestrator.services.delivery_admission import (
    DeliveryAdmission,
    DeliveryReservation,
)
from cli_agent_orchestrator.services.work_blocking import run_owned_async


def _deny() -> NoReturn:
    note_storage_denial()
    raise StorageAdmissionUnavailable()


class StorageOperationAdmission:
    """One service binding; idle logical calls retain identity, never SQL credits."""

    def __init__(self) -> None:
        self._pid = os.getpid()
        self._process_guard = threading.RLock()
        self._admission: DeliveryAdmission | None = None
        self._clock: Callable[[], float] = time.monotonic
        self._retirement: Callable[[], bool] | None = None
        self._accepting = True  # Explicitly unbound legacy construction.
        self._active = 0
        self._idle = threading.Event()
        self._idle.set()
        self._epoch = 0
        self._context: ContextVar[int | None] = ContextVar(
            "cao_service_storage_epoch", default=None
        )

    @property
    def _guard(self) -> threading.RLock:
        if self._pid != os.getpid():
            _deny()
        return self._process_guard

    def bind_admission(
        self,
        admission: DeliveryAdmission | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        retirement: Callable[[], bool] | None = None,
    ) -> None:
        with self._guard:
            if self._retirement is not None and self._retirement() is not True:
                raise RuntimeError(
                    "previous service resource runtime must retire before replacement"
                )
            if self._active:
                raise RuntimeError("service operations must drain before replacement")
            if self._admission is not None and self._admission.snapshot(now=self._clock()).inflight:
                raise RuntimeError(
                    "previous physical resource owners must retire before replacement"
                )
            if admission is not None:
                _ = admission.generation  # PID ownership precedes publication of the binding.
            self._accepting = False
            self._admission = admission
            self._clock = clock
            self._retirement = retirement
            self._epoch += 1

    def start_accepting(self) -> None:
        with self._guard:
            if self._active:
                raise RuntimeError("service lifetimes must drain before restart")
            self._epoch += 1
            self._accepting = True

    def stop_accepting(self) -> None:
        with self._guard:
            self._accepting = False

    def notify_admission_changed(self, terminal_id: str | None = None) -> None:
        pass  # Request/poll callers retry; this port owns no wake queue.

    @staticmethod
    def _normal_parent(current: object) -> bool:
        return isinstance(current, DeliveryReservation) and current._owner.owns_normal_storage(
            current
        )

    @contextmanager
    def lifetime(self) -> Iterator[None]:
        """Keep a logical caller attached across idle awaits without reserving SQL."""
        with self._activity(allow_owned_completion=False):
            yield

    @contextmanager
    def _activity(self, *, allow_owned_completion: bool) -> Iterator[None]:
        with self._guard:
            captured = self._context.get()
            if captured is not None and captured != self._epoch:
                _deny()
            if not self._accepting:
                current = _current_storage_owner() if allow_owned_completion else None
                owned = (
                    self._normal_parent(current)
                    if self._admission is None
                    else self._admission.owns_normal_storage(current)
                )
                if not allow_owned_completion or not owned:
                    _deny()
            self._active += 1
            self._idle.clear()
            token = self._context.set(self._epoch)
        try:
            yield
        finally:
            self._context.reset(token)
            with self._guard:
                self._active -= 1
                if not self._active:
                    self._idle.set()

    @contextmanager
    def operation(self) -> Iterator[None]:
        """Bind inside the actual SQL worker; close before poll sleeps or stream yields."""
        with self._activity(allow_owned_completion=True):
            with self._guard:
                owner, clock = self._admission, self._clock
            current = _current_storage_owner()
            if owner is None:
                if current is not None and not self._normal_parent(current):
                    note_storage_denial()
                    _deny()
                try:
                    yield
                finally:
                    check_storage_admission()
                return
            if current is not None:
                if not owner.owns_normal_storage(current):
                    note_storage_denial()
                    _deny()
                try:
                    yield
                finally:
                    check_storage_admission()
                return
            with self._guard:
                # Stop cannot race a new physical grant; an already owned
                # normal parent above keeps only its original completion rights.
                if not self._accepting:
                    _deny()
                lease = owner.try_reserve_storage(now=clock()).reservation
                if lease is None:
                    _deny()
                if not lease.start():
                    lease.cancel_before_start()
                    _deny()
            try:
                with physical_storage_scope(lease, clock=clock):
                    try:
                        yield
                    finally:
                        check_storage_admission()
            finally:
                lease.finish_after_storage()

    async def drain(self, *, timeout_s: float) -> None:
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or timeout_s <= 0
        ):
            raise ValueError("service drain timeout must be positive and finite")
        with self._guard:
            if self._accepting:
                raise RuntimeError("service admission must stop before draining")
            if not self._active:
                return
        if not await run_owned_async(asyncio.to_thread(self._idle.wait, timeout_s)):
            raise RuntimeError("physical service operations have not drained")
