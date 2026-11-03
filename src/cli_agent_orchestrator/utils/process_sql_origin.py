"""Process-local SQL attribution for supervised servers; never closure authority."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.sql_opening_intent import SqlOpeningPreparationUncertain
from cli_agent_orchestrator.models.sql_process_origin import (
    RESOURCE_BINDING_FD_ENV,
    SQL_ORIGIN_PREFIX,
)
from cli_agent_orchestrator.models.sql_startup_permission import (
    SQL_BROKER_FD_ENV,
    SQL_ROOT_GENESIS_ENV,
    SQL_STARTUP_PERMISSION_ENV,
    SqlBrokerPermissionRecord,
    SqlStartupPermissionRecord,
    VerifiedSqlStartupPermission,
    sql_startup_binding_sha256,
    sql_startup_destination_sha256,
    sql_startup_target_identity_sha256,
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
    startup_required: bool = False
    startup_failed: bool = False
    startup_spent: bool = False
    startup_grant: _StartupOpening | None = None
    root_required: bool = False
    root_control_activation_spent: bool = False
    ever_admitted: bool = False
    broker: _SqlBroker | None = None
    broker_installed: bool = False
    broker_failed: bool = False
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
        startup_required=state.startup_required,
        startup_failed=state.startup_failed,
        startup_spent=state.startup_required,
        root_required=state.root_required,
        # A fork cannot inherit an unspent activation as a second consumer.
        root_control_activation_spent=True,
        ever_admitted=state.ever_admitted,
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


def _latch_sql_mode(state: _State) -> None:
    root = os.environ.get(SQL_ROOT_GENESIS_ENV)
    if root is not None:
        # Root configuration is restrictive only; it never issues a grant.
        state.root_required = True
        state.startup_required = True
        if (
            root != "1"
            or SQL_STARTUP_PERMISSION_ENV in os.environ
            or SQL_BROKER_FD_ENV in os.environ
        ):
            state.startup_failed = True
    marker = os.environ.get(SQL_STARTUP_PERMISSION_ENV)
    if marker is not None or SQL_BROKER_FD_ENV in os.environ:
        # Fork reads without the mutex. Publish the restrictive fact first.
        state.startup_required = True
        if marker != "1":
            state.startup_failed = True
    state.enabled = state.enabled or state.startup_required or RESOURCE_BINDING_FD_ENV in os.environ


# Latch before a caller can clean its environment after importing the runtime.
# This creates no authority; an absent authenticated permission remains closed.
_latch_sql_mode(_STATE)


class SqlStartupPermissionUnavailable(RuntimeError):
    """A physical opening has no valid paid startup permission; no fallback."""


class SqlBrokerRequestRefused(SqlStartupPermissionUnavailable):
    """A local request was refused before any broker transmission."""


class SqlBrokerUnavailable(SqlStartupPermissionUnavailable, SqlOpeningPreparationUncertain):
    """A broker exchange did not establish permission; retain its request identity."""


@dataclass(frozen=True, eq=False, repr=False)
class _SqlBroker:
    initial: SqlStartupPermissionRecord
    binding: ResourceBinding
    pid: int
    request: Callable[[str, object | None], SqlBrokerPermissionRecord]
    close: Callable[[], None]
    validate_process: Callable[[], None]

    def prepare(self, path: str, destination: object | None) -> _StartupOpening:
        if self.pid != os.getpid():
            raise SqlStartupPermissionUnavailable("SQL broker belongs to another process")
        thread, task = threading.current_thread(), _recording_task()
        try:
            self.validate_process()
            record = self.request(path, destination)
            if (
                type(record) is not SqlBrokerPermissionRecord
                or record.startup_intent_id != self.initial.intent_id
                or record.journal_id != self.initial.journal_id
                or record.subject_binding_sha256 != self.initial.subject_binding_sha256
                or record.sql_application_name != self.initial.sql_application_name
            ):
                raise SqlBrokerUnavailable("SQL broker returned an unrelated permission")
            result = _StartupOpening(
                record, self.binding, self.pid, thread, task, self.validate_process
            )
            result.check_worker()
            return result
        except SqlBrokerRequestRefused:
            raise
        except BaseException:
            state = _process_state()
            with state.guard:
                if state.broker is self:
                    state.broker_failed = True
                    state.broker = None
            try:
                self.close()
            except BaseException:
                pass
            raise


def _install_sql_broker(
    permission: VerifiedSqlStartupPermission,
    request: Callable[[str, object | None], SqlBrokerPermissionRecord],
    *,
    close: Callable[[], None],
) -> None:
    if (
        type(permission) is not VerifiedSqlStartupPermission
        or not callable(request)
        or not callable(close)
    ):
        raise SqlStartupPermissionUnavailable("SQL broker requires a live authenticated issuer")
    permission.check()
    permission.validate_process()
    state = _process_state()
    broker = _SqlBroker(
        permission.record,
        permission.binding,
        os.getpid(),
        request,
        close,
        permission.validate_process,
    )
    with state.guard:
        if (
            not state.startup_required
            or state.startup_failed
            or state.sealed
            or state.startup_spent
            or state.startup_grant is None
            or state.broker_installed
            or state.broker_failed
            or state.startup_grant.record is not permission.record
        ):
            raise SqlStartupPermissionUnavailable("SQL broker cannot replace its original issuer")
        state.broker_installed = True
        state.broker = broker


def close_sql_broker() -> None:
    """Close transport only; never certify physical retirement or refund a charge."""
    state = _process_state()
    with state.guard:
        broker = state.broker
        state.broker_failed = True
        state.broker = None
    if broker is not None:
        broker.close()


@dataclass(frozen=True, eq=False, repr=False)
class _StartupOpening:
    record: SqlStartupPermissionRecord
    binding: ResourceBinding
    pid: int
    thread: threading.Thread
    task: object | None
    validate_process: Callable[[], None]

    def check_worker(self) -> None:
        if (
            self.pid != os.getpid()
            or self.thread is not threading.current_thread()
            or self.task is not _recording_task()
        ):
            raise SqlStartupPermissionUnavailable("startup permission belongs to another worker")

    def check_process(self) -> None:
        self.check_worker()
        try:
            self.validate_process()
        except (OSError, ValueError, RuntimeError):
            raise SqlStartupPermissionUnavailable("startup process view is unavailable") from None
        self.check_worker()

    def check_destination(self, path: str, destination: object | None) -> None:
        try:
            if (
                path != self.record.path
                or sql_startup_destination_sha256(destination) != self.record.destination_sha256
                or getattr(destination, "scope", None) != self.record.destination_scope
                or sql_startup_target_identity_sha256(destination)
                != self.binding.target_identity_sha256
                or str(UUID(getattr(destination, "identity")[-1])) != self.binding.store_id
            ):
                raise ValueError("startup destination mismatch")
        except (ValueError, TypeError, AttributeError):
            raise SqlStartupPermissionUnavailable(
                "startup permission destination is unavailable"
            ) from None


def _install_sql_startup_permission(permission: VerifiedSqlStartupPermission) -> None:
    if type(permission) is not VerifiedSqlStartupPermission:
        raise SqlStartupPermissionUnavailable("startup permission requires an authenticated issuer")
    permission.claim()
    permission.validate_process()
    state = _process_state()
    name = current_sql_application_name()
    if (
        permission.record.subject_binding_sha256 != sql_startup_binding_sha256(permission.binding)
        or permission.record.sql_application_name != name
    ):
        raise SqlStartupPermissionUnavailable("startup permission belongs to another process")
    grant = _StartupOpening(
        permission.record,
        permission.binding,
        os.getpid(),
        threading.current_thread(),
        _recording_task(),
        permission.validate_process,
    )
    with state.guard:
        _latch_sql_mode(state)
        if (
            not state.startup_required
            or state.startup_failed
            or state.startup_spent
            or state.startup_grant is not None
            or state.sealed
        ):
            raise SqlStartupPermissionUnavailable("startup permission cannot be replaced")
        state.startup_grant = grant


def _deny_sql_startup_permission() -> None:
    state = _process_state()
    with state.guard:
        state.startup_required = True
        state.startup_failed = True
        state.enabled = True


def _check_root_sql_startup_pending() -> None:
    """Require a fresh root lifetime, never infer absence from this local flag."""
    state = _process_state()
    with state.guard:
        _latch_sql_mode(state)
        if (
            not state.root_required
            or state.startup_failed
            or state.ever_admitted
            or state.startup_spent
            or state.startup_grant is not None
            or state.broker_installed
            or state.sealed
        ):
            raise SqlStartupPermissionUnavailable("root SQL startup is unavailable")


def _claim_root_control_activation() -> None:
    """Consume the independent pre-SQL compute activation once in this process."""
    state = _process_state()
    with state.guard:
        _latch_sql_mode(state)
        if (
            not state.root_required
            or state.startup_failed
            or state.startup_spent
            or state.startup_grant is not None
            or state.broker_installed
            or state.sealed
            or state.ever_admitted
            or state.root_control_activation_spent
        ):
            raise SqlStartupPermissionUnavailable("root control activation is unavailable")
        state.root_control_activation_spent = True


def _install_root_sql_startup_permission(permission: VerifiedSqlStartupPermission) -> None:
    if type(permission) is not VerifiedSqlStartupPermission:
        raise SqlStartupPermissionUnavailable("root SQL requires an authenticated issuer")
    _check_root_sql_startup_pending()
    _install_sql_startup_permission(permission)


def current_sql_application_name() -> str:
    """Latch opt-in; PID resolution always precedes an inherited mutex."""
    state = _process_state()
    with state.guard:
        _latch_sql_mode(state)
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
        _latch_sql_mode(state)
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
    startup_task = (
        _recording_task()
        if (
            state.startup_required
            or SQL_STARTUP_PERMISSION_ENV in os.environ
            or SQL_ROOT_GENESIS_ENV in os.environ
        )
        else None
    )
    grant = None
    broker = None
    with state.guard:
        _latch_sql_mode(state)
        controlled = state.enabled
        if controlled:
            if state.sealed:
                raise SqlProcessOpeningsSealed("terminal process refused a new SQL opening")
            if state.startup_required:
                if state.startup_failed:
                    raise SqlStartupPermissionUnavailable("physical SQL startup is refused")
                if state.startup_spent:
                    broker = state.broker
                    if broker is None or state.broker_failed:
                        raise SqlStartupPermissionUnavailable(
                            "physical SQL has no startup permission"
                        )
                else:
                    grant = state.startup_grant
                    if (
                        grant is None
                        or grant.pid != os.getpid()
                        or grant.thread is not threading.current_thread()
                        or grant.task is not startup_task
                    ):
                        raise SqlStartupPermissionUnavailable(
                            "physical SQL has no startup permission"
                        )
                    state.startup_spent = True
            state.opening += 1
        # Legacy openings are tracked only as an irreversible eligibility fact.
        # This neither inventories backends nor changes their ordinary lifetime.
        state.ever_admitted = True
    uncertain = False
    entered_driver = False
    registration_uncertain = False
    try:
        if broker is not None:
            grant = broker.prepare(path, destination)
        if grant is not None:
            grant.check_destination(path, destination)
            grant.check_process()
        recorder = _RECORDER.get()
        if recorder is not None:
            if _RECORDING.get():
                raise SqlOpeningRecorderUnavailable("Recursive SQL opening registration is refused")
            token = _RECORDING.set(True)
            try:
                recorder.prepare(path, destination)
            finally:
                _RECORDING.reset(token)
        if grant is not None:
            grant.check_worker()
            grant.check_destination(path, destination)
            grant.check_process()
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
