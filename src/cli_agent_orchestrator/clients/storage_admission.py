"""Private physical-worker storage port; clients never import service owners."""

import asyncio
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

_WorkerResult = TypeVar("_WorkerResult")


class StorageConnectionLoan(Protocol):
    def finish(self) -> bool: ...


class StorageConnectionOwner(Protocol):
    @property
    def storage_process_current(self) -> bool: ...

    def try_acquire_storage_connection(self, *, now: float) -> StorageConnectionLoan | None: ...


class StorageAdmissionUnavailable(RuntimeError):
    """Fixed, non-secret failure; optional memory must not swallow this denial."""

    def __init__(self) -> None:
        super().__init__("Storage resources are unavailable for the admitted operation")


def _current_task() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


@dataclass(repr=False)
class _StorageDenial:
    pid: int = field(default_factory=os.getpid)
    denied: bool = False
    guard: threading.Lock = field(default_factory=threading.Lock)

    def fail(self) -> None:
        if self.pid != os.getpid():
            self.denied = True
            return
        with self.guard:
            self.denied = True

    def failed(self) -> bool:
        if self.pid != os.getpid():
            return True
        with self.guard:
            return self.denied


@dataclass(repr=False)
class _PhysicalScope:
    owner: StorageConnectionOwner
    clock: Callable[[], float]
    pid: int = field(default_factory=os.getpid)
    thread: int = field(default_factory=threading.get_ident)
    task: object | None = field(default_factory=_current_task)
    active: bool = True
    _denial: _StorageDenial = field(default_factory=_StorageDenial)
    guard: threading.Lock = field(default_factory=threading.Lock)
    _handoffs: set[object] = field(default_factory=set)

    def fail(self) -> None:
        if self.pid != os.getpid():
            self._denial.denied = True  # Child copy only; never touch an inherited lock.
            return
        self._denial.fail()

    def failed(self) -> bool:
        if self.pid != os.getpid():
            return True
        return self._denial.failed()

    def is_current(self) -> bool:
        if self.pid != os.getpid():
            return False
        with self.guard:
            return (
                self.active
                and self.thread == threading.get_ident()
                and self.task is _current_task()
            )

    def deactivate(self) -> None:
        if self.pid != os.getpid():
            self.active = False
            return
        with self.guard:
            self.active = False
            self._handoffs.clear()


_CURRENT: ContextVar[_PhysicalScope | None] = ContextVar("cao_physical_storage_scope", default=None)


@contextmanager
def physical_storage_scope(
    owner: StorageConnectionOwner | None,
    *,
    clock: Callable[[], float],
    _denial: _StorageDenial | None = None,
) -> Iterator[None]:
    """Bind inside an admitted physical worker, never around its async waiter.

    A copied Context is not a physical-worker authorization. A trusted owner
    explicitly binds each next physical worker during a handoff. Nested use by
    the same worker/owner preserves its sticky denial until that worker exits.
    """
    current = _CURRENT.get()
    if owner is None:
        yield
        return
    if owner.storage_process_current is not True:
        note_storage_denial()
        raise StorageAdmissionUnavailable()
    if current is not None and current.owner is owner and current.is_current():
        yield
        return
    scope = _PhysicalScope(owner, clock)
    if _denial is not None:
        scope._denial = _denial
    token = _CURRENT.set(scope)
    try:
        yield
    finally:
        scope.deactivate()
        _CURRENT.reset(token)


def acquire_storage_connection() -> StorageConnectionLoan | None:
    """Must precede physical checkout/open, including setup and pre-ping.

    None means explicitly unbound legacy execution, never a bound refusal.
    A caller returns a loan only after actual physical return/close is proved.
    The reserved closing credit may still be used to record a prior denial.
    """
    scope = _CURRENT.get()
    if not _PROCESS_STORAGE_CLOSES.process_current:
        if scope is not None:
            scope.fail()
        raise StorageAdmissionUnavailable()
    if scope is None:
        return None
    if not scope.is_current():
        scope.fail()
        raise StorageAdmissionUnavailable()
    try:
        loan = scope.owner.try_acquire_storage_connection(now=scope.clock())
    except Exception:
        scope.fail()
        raise StorageAdmissionUnavailable() from None
    if loan is None:
        scope.fail()
        raise StorageAdmissionUnavailable()
    return loan


def storage_admission_failed() -> bool:
    scope = _CURRENT.get()
    return scope is not None and scope.failed()


def check_storage_admission() -> None:
    """Fence a transport effect or result after optional code swallowed denial."""
    scope = _CURRENT.get()
    if scope is not None and storage_close_uncertainty():
        scope.fail()
    if storage_admission_failed():
        raise StorageAdmissionUnavailable()


@dataclass(frozen=True, eq=False, repr=False)
class _StorageWorkerHandoff:
    """One exact transfer, used only by a harness which joins its real worker."""

    _parent: _PhysicalScope

    def run(self, function: Callable[..., _WorkerResult], *args, **kwargs) -> _WorkerResult:
        parent = self._parent
        if parent.pid != os.getpid() or not _PROCESS_STORAGE_CLOSES.process_current:
            raise StorageAdmissionUnavailable()
        with parent.guard:
            if not parent.active or self not in parent._handoffs:
                parent.fail()
                raise StorageAdmissionUnavailable()
            parent._handoffs.remove(self)
            if parent.failed():
                raise StorageAdmissionUnavailable()
        with physical_storage_scope(parent.owner, clock=parent.clock, _denial=parent._denial):
            try:
                check_storage_admission()
                result = function(*args, **kwargs)
                if parent.failed():
                    note_storage_denial()
                check_storage_admission()
                return result
            except StorageAdmissionUnavailable:
                note_storage_denial()
                raise
            finally:
                if storage_admission_failed():
                    parent.fail()


def capture_storage_worker() -> _StorageWorkerHandoff | None:
    """Capture inside the authorized caller, before submitting an owned worker.

    The caller must join that worker before its parent scope/lease retires.
    Absence preserves legacy execution; stale/copied scopes cannot fall back.
    """
    scope = _CURRENT.get()
    if scope is None:
        return None
    if scope.pid != os.getpid() or not _PROCESS_STORAGE_CLOSES.process_current:
        scope._denial.denied = True  # Foreign process: never acquire an inherited lock.
        raise StorageAdmissionUnavailable()
    with scope.guard:
        if (
            not scope.active
            or scope.thread != threading.get_ident()
            or scope.task is not _current_task()
            or scope.failed()
        ):
            scope.fail()
            raise StorageAdmissionUnavailable()
        handoff = _StorageWorkerHandoff(scope)
        scope._handoffs.add(handoff)
        return handoff


@dataclass(frozen=True)
class StorageCloseResult:
    closed: int
    pending: int | None
    process_current: bool

    @property
    def clean(self) -> bool:
        return self.process_current and self.pending == 0


@dataclass(repr=False)
class _PendingClose:
    identity: object
    close: Callable[[], None] | None
    closed: Callable[[], bool]
    loans: dict[int, StorageConnectionLoan] = field(default_factory=dict)


class StorageCloseRegistry:
    """Keep exact physical owners; neither elapsed time nor retry retires them.

    The process registry persists across controller reconstruction. Separate
    instances are useful for isolated protocol tests without resetting live
    process uncertainty. No close callback may open replacement connections.
    """

    def __init__(self) -> None:
        self._pid = os.getpid()
        self._guard = threading.RLock()
        self._retry_guard = threading.Lock()
        self._pending: dict[int, _PendingClose] = {}
        self._loan_identities: dict[int, object] = {}

    @property
    def process_current(self) -> bool:
        return self._pid == os.getpid()

    @property
    def pending_count(self) -> int | None:
        if not self.process_current:
            return None
        with self._guard:
            return len(self._pending)

    def has_pending(self) -> bool:
        return not self.process_current or self.pending_count != 0

    def has_loan(self, loan: StorageConnectionLoan) -> bool:
        if not self.process_current:
            return True
        with self._guard:
            return id(loan) in self._loan_identities

    def has_identity(self, identity: object) -> bool:
        if not self.process_current:
            return True
        with self._guard:
            record = self._pending.get(id(identity))
            return record is not None and record.identity is identity

    def retain(
        self,
        identity: object,
        *,
        close: Callable[[], None] | None,
        closed: Callable[[], bool],
        loan: StorageConnectionLoan | None,
    ) -> None:
        if not self.process_current:
            raise StorageAdmissionUnavailable()
        if identity is None:
            raise ValueError("physical storage identity is required")
        with self._guard:
            key = id(identity)
            record = self._pending.get(key)
            if record is None:
                record = _PendingClose(identity, close, closed)
                self._pending[key] = record
            elif record.identity is not identity:
                raise RuntimeError("Storage retirement identity changed")
            if loan is not None:
                prior_identity = self._loan_identities.get(id(loan))
                if prior_identity is not None and prior_identity is not identity:
                    raise RuntimeError("One storage loan cannot own two physical handles")
                record.loans[id(loan)] = loan
                self._loan_identities[id(loan)] = identity

    @staticmethod
    def _confirmed(record: _PendingClose) -> bool:
        try:
            return record.closed() is True
        except Exception:
            return False

    def retry_owned(self) -> StorageCloseResult:
        if not self.process_current:
            return StorageCloseResult(0, None, False)
        if not self._retry_guard.acquire(blocking=False):
            return StorageCloseResult(0, self.pending_count, True)
        completed = 0
        try:
            with self._guard:
                records = tuple(self._pending.values())
            for record in records:
                if not self._confirmed(record) and record.close is not None:
                    try:
                        record.close()
                    except Exception:
                        pass  # Keep the owner and omit private driver diagnostics.
                if not self._confirmed(record):
                    continue
                with self._guard:
                    if self._pending.get(id(record.identity)) is not record:
                        continue
                    loans = tuple(record.loans.values())
                # Never take an admission-owner lock while holding this registry.
                # The physical witness precedes token retirement, not vice versa.
                for loan in loans:
                    loan.finish()
                with self._guard:
                    if self._pending.get(id(record.identity)) is record:
                        for loan in loans:
                            if record.loans.get(id(loan)) is loan:
                                del record.loans[id(loan)]
                                self._loan_identities.pop(id(loan), None)
                        # A concurrent retain may have added another exact owner.
                        if not record.loans:
                            del self._pending[id(record.identity)]
                            completed += 1
            return StorageCloseResult(completed, self.pending_count, self.process_current)
        finally:
            self._retry_guard.release()


_PROCESS_STORAGE_CLOSES = StorageCloseRegistry()
_FORK_STORAGE_DETACHER: Callable[[], bool] | None = None
_INHERITED_STORAGE_CLOSES: list[StorageCloseRegistry] = []


def _register_storage_fork_detacher(detacher: Callable[[], bool]) -> None:
    """Driver setup registers before any product PostgreSQL handle can open."""
    global _FORK_STORAGE_DETACHER
    if not callable(detacher):
        raise ValueError("storage fork detacher must be callable")
    if _FORK_STORAGE_DETACHER is not None and _FORK_STORAGE_DETACHER is not detacher:
        raise RuntimeError("storage fork detacher is already registered")
    _FORK_STORAGE_DETACHER = detacher


def _after_storage_fork_child() -> None:
    global _PROCESS_STORAGE_CLOSES
    inherited = _PROCESS_STORAGE_CLOSES
    if inherited.process_current:
        return  # Calling the hook in its own process cannot clear uncertainty.
    try:
        if _FORK_STORAGE_DETACHER is not None and _FORK_STORAGE_DETACHER() is not True:
            return
    except BaseException:
        return  # Foreign registry keeps even unbound child opens closed.
    # Retain copied owners without taking their locks or releasing their tokens.
    # The old objects remain foreign; only the child's new registry is current.
    _INHERITED_STORAGE_CLOSES.append(inherited)
    _PROCESS_STORAGE_CLOSES = StorageCloseRegistry()
    # Keep _CURRENT: an inherited scope is still foreign, never legacy fallback.


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_storage_fork_child)


def storage_close_uncertainty() -> bool:
    return _PROCESS_STORAGE_CLOSES.has_pending()


def retry_storage_closes() -> StorageCloseResult:
    return _PROCESS_STORAGE_CLOSES.retry_owned()


def retain_storage_close(
    identity: object,
    *,
    close: Callable[[], None] | None,
    closed: Callable[[], bool],
    loan: StorageConnectionLoan | None,
) -> None:
    scope = _CURRENT.get()
    if scope is not None:
        scope.fail()
    _PROCESS_STORAGE_CLOSES.retain(identity, close=close, closed=closed, loan=loan)


def storage_close_owns_loan(loan: StorageConnectionLoan) -> bool:
    return _PROCESS_STORAGE_CLOSES.has_loan(loan)


def note_storage_denial() -> None:
    scope = _CURRENT.get()
    if scope is not None:
        scope.fail()


def storage_close_tracks_handle(identity: object) -> bool:
    return _PROCESS_STORAGE_CLOSES.has_identity(identity)


def storage_scope_present() -> bool:
    return _CURRENT.get() is not None
