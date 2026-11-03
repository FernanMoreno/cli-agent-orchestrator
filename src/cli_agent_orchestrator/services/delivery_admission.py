"""Process-local reservation ownership, independent of delivery or authority."""

import asyncio
import math
import os
import queue
import subprocess
import threading
import weakref
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass, field
from heapq import merge
from itertools import count
from typing import Generic, Literal, NoReturn, Protocol, SupportsIndex, TypeVar, cast
from uuid import UUID

from cli_agent_orchestrator.clients.storage_admission import (
    _CURRENT,
    StorageAdmissionUnavailable,
    check_storage_admission,
    physical_storage_scope,
    storage_close_uncertainty,
)
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.resource_generation import (
    ProcessExitObservation,
    is_process_exit_observation,
)
from cli_agent_orchestrator.models.root_control_activation import (
    VerifiedComputeStartupBasis,
    VerifiedComputeStartupSample,
    VerifiedRootControlActivation,
    _issue_verified_compute_startup_basis,
    is_verified_compute_startup_basis,
    is_verified_compute_startup_sample,
    is_verified_root_control_activation,
)
from cli_agent_orchestrator.models.sql_process_origin import is_sql_process_application_name
from cli_agent_orchestrator.models.sql_root_subject import RootSqlSubject
from cli_agent_orchestrator.services.admission_policy import (
    AdmissionDecision,
    AdmissionPolicy,
    CapacityObservation,
    CapacityReason,
    ComputeObservation,
    ResourceAmounts,
    compute_capacity,
    evaluate_capacity,
    validate_clock,
)
from cli_agent_orchestrator.services.resource_process_supervisor import (
    ResourceProcessSupervisor,
    ResourceProcessUnproven,
)

AdmissionReason = CapacityReason | Literal["receiver_busy", "stopping"]
ReservationState = Literal["reserved", "started", "released"]
_T = TypeVar("_T")


def _current_async_task() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _valid_command_timeout(value: object, *, optional: bool = False) -> bool:
    if value is None:
        return optional
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


class SharedStorageCoordinator(Protocol):
    _owner: "DeliveryAdmission"

    def prepare(self, lease: "DeliveryReservation") -> None: ...

    def owns_prepared_storage(self, lease: object) -> bool: ...


@dataclass(frozen=True)
class AdmissionAttempt:
    reservation: "DeliveryReservation | None"
    reason: AdmissionReason


@dataclass(frozen=True)
class StorageAdmissionAttempt:
    reservation: "StorageReservation | None"
    reason: AdmissionReason


@dataclass(frozen=True, eq=False)
class CommandAdmissionAttempt:
    reservation: "CommandReservation | None"
    reason: AdmissionReason


@dataclass(frozen=True)
class AdmissionSnapshot:
    accepting: bool
    generation: int
    inflight: int
    reserved: int
    started: int
    limit: int
    available_slots: int
    debt: int
    reason: AdmissionReason
    sample_age_s: float | None
    committed: ResourceAmounts
    resource_debt: ResourceAmounts
    storage_connection_loans: int = 0
    # Retained admitted credits include reserved handoffs not yet started.
    storage_completion_credits: int = 0
    storage_retirement_pending: int = 0
    storage_only_inflight: int = 0
    storage_only_started: int = 0
    control_storage_slots: int = 0
    control_inflight: int = 0
    control_connection_loans: int = 0
    command_inflight: int = 0
    command_started: int = 0

    @property
    def delivery_inflight(self) -> int:
        return self.inflight - self.storage_only_inflight - self.command_inflight

    @property
    def delivery_started(self) -> int:
        return self.started - self.storage_only_started - self.command_started


@dataclass(frozen=True, eq=False, init=False, repr=False)
class CommandReservation:
    """One exact RAM/CPU charge for a server-selected command worker."""

    _owner: "DeliveryAdmission"
    _key: int
    _generation: int
    _footprint: ResourceAmounts
    _state: ReservationState
    _start_pid: int | None
    _start_thread: threading.Thread | None
    _start_task: object | None
    _thread_worker: object | None
    _process_worker: "CommandProcess | None"
    _actor_thread: "CommandThread[None] | None"
    _startup_basis: VerifiedComputeStartupBasis | None

    def __init__(
        self,
        owner: "DeliveryAdmission",
        key: int,
        generation: int,
        footprint: ResourceAmounts,
        *,
        startup_basis: VerifiedComputeStartupBasis | None = None,
    ) -> None:
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_key", key)
        object.__setattr__(self, "_generation", generation)
        object.__setattr__(self, "_footprint", footprint)
        object.__setattr__(self, "_state", "reserved")
        object.__setattr__(self, "_start_pid", None)
        object.__setattr__(self, "_start_thread", None)
        object.__setattr__(self, "_start_task", None)
        object.__setattr__(self, "_thread_worker", None)
        object.__setattr__(self, "_process_worker", None)
        object.__setattr__(self, "_actor_thread", None)
        object.__setattr__(self, "_startup_basis", startup_basis)

    def __copy__(self) -> NoReturn:
        raise TypeError("command reservation is not transferable")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise TypeError("command reservation is not transferable")

    def __reduce__(self) -> NoReturn:
        raise TypeError("command reservation is not serializable")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("command reservation is not serializable")

    @property
    def footprint(self) -> ResourceAmounts:
        return self._footprint

    @property
    def state(self) -> ReservationState:
        with self._owner._guard:
            return self._state

    def _fits_locked(self, now: float) -> bool:
        owner = self._owner
        if self._startup_basis is not None:
            basis = self._startup_basis
            if not is_verified_compute_startup_basis(basis) or not basis.belongs_to(
                owner, generation=owner._generation
            ):
                return False
            memory_budget, cpu_budget = basis.budget
            capacity = compute_capacity(
                owner._policy,
                basis.observation,
                now=now,
                budget_memory_bytes=memory_budget,
                budget_cpu_millicores=cpu_budget,
            )
            if capacity is None:
                return False
            other_committed = owner._committed - self._footprint
            return (
                self._footprint.memory_bytes
                <= max(0, capacity.memory_bytes - other_committed.memory_bytes)
                and self._footprint.cpu_millicores
                <= max(0, capacity.cpu_millicores - other_committed.cpu_millicores)
                and self._footprint.storage_slots == 0
            )
        decision = owner._current_decision(now, charged=False)
        if decision.reason not in {"capacity_available", "resource_exhausted"}:
            return False
        other_committed = owner._committed - self._footprint
        remaining = decision.resource_capacity.excess_over(other_committed)
        return owner._command_fits(self._footprint, remaining)

    def _pending_start_locked(self, now: float) -> bool:
        owner = self._owner
        return (
            self._state == "reserved"
            and owner._accepting
            and self._generation == owner._generation
            and owner._commands.get(self._key) is self
            and owner._pending_commands.get(self._key) is self
            and self._fits_locked(now)
        )

    def _mark_started_locked(self, thread: threading.Thread, task: object | None) -> None:
        object.__setattr__(self, "_state", "started")
        object.__setattr__(self, "_start_pid", os.getpid())
        object.__setattr__(self, "_start_thread", thread)
        object.__setattr__(self, "_start_task", task)
        object.__setattr__(self, "_actor_thread", None)
        self._owner._pending_commands.pop(self._key)
        self._owner._command_started += 1

    def _dispatch_to_thread(self, worker: "CommandThread[None]", *, now: float) -> bool:
        """Bind a pending command to one already-started internal actor thread."""
        validate_clock(now)
        owner = self._owner
        if owner._pid != os.getpid() or type(worker) is not CommandThread:
            return False
        if (
            not worker._start_complete.is_set()
            or not worker._start_confirmed.is_set()
            or not worker._thread.is_alive()
            or worker._returned.is_set()
        ):
            return False
        with owner._guard:
            if (
                worker._reservation._owner is not owner
                or worker._pid != os.getpid()
                or not worker._registered_locked()
                or worker._returned.is_set()
                or self._process_worker is not None
                or not self._pending_start_locked(now)
            ):
                return False
            self._mark_started_locked(worker._thread, None)
            object.__setattr__(self, "_actor_thread", worker)
            return True

    def _check_dispatched(self, worker: "CommandThread[None]", *, now: float) -> bool:
        """Recheck a started actor command immediately before its first effect."""
        validate_clock(now)
        owner = self._owner
        if (
            owner._pid != os.getpid()
            or type(worker) is not CommandThread
            or worker._pid != os.getpid()
            or worker._thread is not threading.current_thread()
            or _current_async_task() is not None
            or not worker._thread.is_alive()
        ):
            return False
        with owner._guard:
            return bool(
                worker._registered_locked()
                and not worker._returned.is_set()
                and self._state == "started"
                and owner._commands.get(self._key) is self
                and self._actor_thread is worker
                and self._process_worker is None
                and self._start_pid == os.getpid()
                and self._start_thread is worker._thread
                and self._start_task is None
                and self._generation == owner._generation
                and owner._accepting
                and self._fits_locked(now)
            )

    def start(self, *, now: float) -> bool:
        validate_clock(now)
        owner = self._owner
        if owner._pid != os.getpid():
            return False
        with owner._guard:
            if not self._pending_start_locked(now):
                return False
            self._mark_started_locked(threading.current_thread(), _current_async_task())
            return True

    def _launch_thread(
        self, target: Callable[[], _T], *, clock: Callable[[], float]
    ) -> "CommandThread[_T] | None":
        """Trusted internal handoff only; never an HTTP/MCP callback interface."""
        owner = self._owner
        if owner._pid != os.getpid():
            return None
        if not callable(target) or not callable(clock):
            raise ValueError("command thread requires an internal target and clock")
        now = clock()
        validate_clock(now)
        with owner._guard:
            if not self._pending_start_locked(now):
                return None
            worker = CommandThread(self, target, clock)
            object.__setattr__(self, "_thread_worker", worker)
            self._mark_started_locked(worker._thread, None)
        try:
            worker._thread.start()  # No physical start or wait under the ledger guard.
            worker._start_confirmed.set()
        except BaseException:
            raise CommandThreadStartUncertain(worker) from None
        finally:
            worker._start_complete.set()
        return worker

    def _launch_process(
        self,
        argv: Sequence[str],
        *,
        expected_store_id: str,
        expected_target_identity_sha256: str,
        env: Mapping[str, str] | None = None,
        root_control_channel: bool = False,
        clock: Callable[[], float],
    ) -> "CommandProcess | None":
        """Fund and transfer this command token to one exact owned child."""
        owner = self._owner
        if owner._pid != os.getpid():
            return None
        if type(root_control_channel) is not bool:
            raise ValueError("command process Root control selector must be boolean")
        if not callable(clock):
            raise ValueError("command process requires an internal clock")
        if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence):
            raise ValueError("command process requires explicit argv")
        arguments = tuple(argv)
        if not arguments or any(
            not isinstance(value, str) or not value or "\0" in value for value in arguments
        ):
            raise ValueError("command process requires explicit argv")
        try:
            store_id = str(UUID(expected_store_id))
        except (TypeError, ValueError, AttributeError):
            raise ValueError("command process requires a canonical store id") from None
        if (
            not isinstance(expected_target_identity_sha256, str)
            or len(expected_target_identity_sha256) != 64
            or any(value not in "0123456789abcdef" for value in expected_target_identity_sha256)
        ):
            raise ValueError("command process requires a canonical target digest")
        if env is not None and not isinstance(env, Mapping):
            raise ValueError("command process environment must be a mapping")
        environment = None if env is None else dict(env)
        if environment is not None and any(
            not isinstance(key, str) or not isinstance(value, str) or "\0" in key or "\0" in value
            for key, value in environment.items()
        ):
            raise ValueError("command process environment must contain text values")
        now = clock()
        validate_clock(now)
        with owner._guard:
            if (
                self._startup_basis is not None
                or self._footprint.memory_bytes <= 0
                or self._footprint.cpu_millicores <= 0
                or self._footprint.storage_slots != 0
                or self._thread_worker is not None
                or self._actor_thread is not None
                or self._process_worker is not None
                or not self._pending_start_locked(now)
            ):
                return None
            worker = CommandProcess._create(
                self,
                store_id=store_id,
                target_digest=expected_target_identity_sha256,
            )
            object.__setattr__(self, "_process_worker", worker)
            self._mark_started_locked(threading.current_thread(), _current_async_task())
        supervisor: ResourceProcessSupervisor | None = None
        try:
            if root_control_channel:
                supervisor = ResourceProcessSupervisor.launch(
                    arguments,
                    expected_store_id=store_id,
                    expected_target_identity_sha256=expected_target_identity_sha256,
                    env=environment,
                    first_sql_grant=False,
                    sql_broker=False,
                    root_control_channel=True,
                )
            else:
                supervisor = ResourceProcessSupervisor.launch(
                    arguments,
                    expected_store_id=store_id,
                    expected_target_identity_sha256=expected_target_identity_sha256,
                    env=environment,
                    first_sql_grant=False,
                    sql_broker=False,
                )
            if type(supervisor) is not ResourceProcessSupervisor:
                raise TypeError("command process launch returned an unowned supervisor")
            worker._attach_supervisor(supervisor)
            if not worker._supervisor_context_valid:
                raise ResourceProcessUnproven("command process supervisor context changed")
        except BaseException as error:
            attached = getattr(error, "__dict__", {}).get("resource_process_owner")
            retained = (
                supervisor
                if type(supervisor) is ResourceProcessSupervisor
                else attached if type(attached) is ResourceProcessSupervisor else None
            )
            if retained is not None:
                try:
                    if worker._supervisor is not retained:
                        worker._attach_supervisor(retained)
                    else:
                        worker._supervisor_context_valid = bool(
                            retained._owner_pid == worker._pid
                            and retained._owner_thread is worker._thread
                        )
                except BaseException:
                    # Keep the exact owner even if acknowledgement or attachment failed.
                    object.__setattr__(worker, "_supervisor", retained)
                    try:
                        context_valid = bool(
                            retained._owner_pid == worker._pid
                            and retained._owner_thread is worker._thread
                        )
                    except BaseException:
                        context_valid = False
                    object.__setattr__(worker, "_supervisor_context_valid", context_valid)
            raise CommandProcessStartUncertain(worker, error) from error
        return worker

    def cancel_before_start(self) -> bool:
        owner = self._owner
        if owner._pid != os.getpid():
            return False
        with owner._guard:
            if self._state != "reserved":
                return False
            return owner._release_command_locked(self)

    def finish(self) -> bool:
        owner = self._owner
        if self._process_worker is not None:
            return False
        actor = self._actor_thread
        if actor is not None:
            if (
                self._state != "started"
                or self._thread_worker is not None
                or self._start_pid != os.getpid()
                or owner._pid != os.getpid()
                or actor._pid != os.getpid()
                or actor._thread is not threading.current_thread()
                or self._start_thread is not actor._thread
                or self._start_task is not None
                or _current_async_task() is not None
            ):
                return False
            with owner._guard:
                if not actor._registered_locked() or self._state != "started":
                    return False
                return owner._release_command_locked(self, finish_started=True)
        elif (
            self._state != "started"
            or self._thread_worker is not None
            or self._start_pid != os.getpid()
            or owner._pid != os.getpid()
            or self._start_thread is not threading.current_thread()
            or self._start_task is not _current_async_task()
        ):
            return False
        with owner._guard:
            if self._state != "started":
                return False
            return owner._release_command_locked(self, finish_started=True)


class CommandThreadStartUncertain(RuntimeError, Generic[_T]):
    """A start attempt failed; the exact handle and its charge remain owned."""

    def __init__(self, worker: "CommandThread[_T]") -> None:
        super().__init__("command thread start is unconfirmed")
        self.worker = worker


_COMMAND_PROCESS_FACTORY = object()


class CommandProcessStartUncertain(RuntimeError):
    """Popen acknowledgement was uncertain; the transferred charge is sticky."""

    def __init__(self, worker: "CommandProcess", cause: BaseException) -> None:
        super().__init__("command process start is unconfirmed")
        self.worker = worker
        self.cause = cause


class CommandProcess:
    """Owner-bound lifetime of one exact child; no descendant or SQL-close proof."""

    __slots__ = (
        "_reservation",
        "_pid",
        "_thread",
        "_task",
        "_store_id",
        "_target_digest",
        "_supervisor",
        "_supervisor_context_valid",
        "_binding_attempted",
        "_binding",
        "_sql_application_name",
        "_observation",
        "_operation_guard",
        "_terminal_uncertainty",
        "_root_activation_attempted",
        "_factory",
    )
    _reservation: CommandReservation
    _pid: int
    _thread: threading.Thread
    _task: object | None
    _store_id: str
    _target_digest: str
    _supervisor: ResourceProcessSupervisor | None
    _supervisor_context_valid: bool
    _binding_attempted: bool
    _binding: ResourceBinding | None
    _sql_application_name: str | None
    _observation: ProcessExitObservation | None
    _operation_guard: threading.Lock
    _terminal_uncertainty: bool
    _root_activation_attempted: bool
    _factory: object

    def __init__(self, *, _factory: object) -> None:
        if _factory is not _COMMAND_PROCESS_FACTORY:
            raise TypeError("command processes must be launched by a reservation")

    @classmethod
    def _create(
        cls,
        reservation: CommandReservation,
        *,
        store_id: str,
        target_digest: str,
    ) -> "CommandProcess":
        value = object.__new__(cls)
        value._reservation = reservation
        value._pid = os.getpid()
        value._thread = threading.current_thread()
        value._task = _current_async_task()
        value._store_id = store_id
        value._target_digest = target_digest
        value._supervisor = None
        value._supervisor_context_valid = False
        value._binding_attempted = False
        value._binding = None
        value._sql_application_name = None
        value._observation = None
        value._operation_guard = threading.Lock()
        value._terminal_uncertainty = False
        value._root_activation_attempted = False
        value._factory = _COMMAND_PROCESS_FACTORY
        return value

    def __copy__(self) -> NoReturn:
        raise TypeError("command process ownership is not transferable")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise TypeError("command process ownership is not transferable")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("command process ownership is not serializable")

    def _check_owner(self) -> None:
        if (
            self._factory is not _COMMAND_PROCESS_FACTORY
            or self._pid != os.getpid()
            or self._thread is not threading.current_thread()
            or self._task is not _current_async_task()
        ):
            raise StorageAdmissionUnavailable()

    def _registered_locked(self) -> bool:
        reservation = self._reservation
        return (
            reservation._owner._commands.get(reservation._key) is reservation
            and reservation._process_worker is self
            and reservation._state == "started"
            and reservation._start_pid == self._pid
            and reservation._start_thread is self._thread
            and reservation._start_task is self._task
        )

    def _registered(self) -> bool:
        self._check_owner()
        owner = self._reservation._owner
        with owner._guard:
            return self._registered_locked()

    def _attach_supervisor(self, supervisor: ResourceProcessSupervisor) -> None:
        if type(supervisor) is not ResourceProcessSupervisor:
            raise TypeError("command process requires its exact supervisor")
        if self._supervisor is not None and self._supervisor is not supervisor:
            raise ResourceProcessUnproven("command process supervisor cannot be replaced")
        self._supervisor = supervisor
        self._supervisor_context_valid = bool(
            supervisor._owner_pid == self._pid and supervisor._owner_thread is self._thread
        )

    @property
    def has_native_owner(self) -> bool:
        """Whether this exact handle retains its supervisor; no process-state proof."""
        self._check_owner()
        return self._supervisor is not None

    def _root_supervisor(self) -> ResourceProcessSupervisor:
        supervisor = self._supervisor
        if (
            supervisor is None
            or not self._supervisor_context_valid
            or supervisor._owner_pid != self._pid
            or supervisor._owner_thread is not self._thread
            or not getattr(supervisor, "_root_control_enabled", False)
            or supervisor._root_channel is None
            or supervisor._root_channel.fileno() < 0
            or self._terminal_uncertainty
            or self._binding is None
            or self._sql_application_name is None
        ):
            raise ResourceProcessUnproven("command Root control is unavailable")
        return supervisor

    def root_subject(self) -> RootSqlSubject:
        """Return the parent-authenticated child subject as descriptive data."""
        self._check_owner()
        if not self._registered():
            raise StorageAdmissionUnavailable()
        if not self._operation_guard.acquire(blocking=False):
            raise ResourceProcessUnproven("command process operation is already active")
        try:
            supervisor = self._root_supervisor()
            supervisor._validate_root_control()
            binding = self._binding
            label = self._sql_application_name
            identity = supervisor._identity
            if binding is None or label is None or identity is None:
                raise ResourceProcessUnproven("command Root subject is unavailable")
            subject = RootSqlSubject(
                binding,
                label,
                identity.boot_id,
                identity.ticks,
                identity.pid_ns,
            )
            self._check_owner()
            if (
                not self._registered()
                or self._binding is not binding
                or self._sql_application_name != label
                or supervisor._identity is not identity
            ):
                raise ResourceProcessUnproven("command Root subject changed")
            return subject
        finally:
            self._operation_guard.release()

    def _retain_root_close_uncertainty(self, supervisor: ResourceProcessSupervisor) -> None:
        if getattr(supervisor, "_root_close_uncertain", False) is not False:
            self._terminal_uncertainty = True

    def _abort_root_after_failure(
        self, supervisor: ResourceProcessSupervisor, primary: BaseException
    ) -> None:
        self._retain_root_close_uncertainty(supervisor)
        try:
            supervisor.close_root_control()
        except BaseException as error:
            self._terminal_uncertainty = True
            add_note = getattr(primary, "add_note", None)
            if callable(add_note):
                add_note("Root control channel close remains uncertain.")
            _ = error
        finally:
            self._retain_root_close_uncertainty(supervisor)

    def root_activate(self, packet: bytes, timeout_s: float = 30) -> None:
        self._check_owner()
        if type(packet) is not bytes or not packet:
            raise ValueError("command Root activation packet must be bytes")
        if not _valid_command_timeout(timeout_s):
            raise ValueError("command Root activation timeout must be finite and nonnegative")
        if not self._registered():
            raise StorageAdmissionUnavailable()
        if not self._operation_guard.acquire(blocking=False):
            raise ResourceProcessUnproven("command process operation is already active")
        supervisor: ResourceProcessSupervisor | None = None
        try:
            supervisor = self._root_supervisor()
            if self._root_activation_attempted:
                raise ResourceProcessUnproven("command Root activation is single use")
            self._root_activation_attempted = True
            supervisor.root_activate(packet, timeout_s=timeout_s)
        except BaseException as error:
            if supervisor is not None:
                self._abort_root_after_failure(supervisor, error)
            raise
        finally:
            self._operation_guard.release()

    def root_refresh(self, timeout_s: float = 30) -> str:
        self._check_owner()
        if not _valid_command_timeout(timeout_s):
            raise ValueError("command Root refresh timeout must be finite and nonnegative")
        if not self._registered():
            raise StorageAdmissionUnavailable()
        if not self._operation_guard.acquire(blocking=False):
            raise ResourceProcessUnproven("command process operation is already active")
        supervisor: ResourceProcessSupervisor | None = None
        try:
            supervisor = self._root_supervisor()
            return supervisor.root_refresh(timeout_s=timeout_s)
        except BaseException as error:
            if supervisor is not None:
                self._abort_root_after_failure(supervisor, error)
            raise
        finally:
            self._operation_guard.release()

    def root_stop(self, timeout_s: float = 30) -> None:
        self._check_owner()
        if not _valid_command_timeout(timeout_s):
            raise ValueError("command Root stop timeout must be finite and nonnegative")
        if not self._registered():
            raise StorageAdmissionUnavailable()
        if not self._operation_guard.acquire(blocking=False):
            raise ResourceProcessUnproven("command process operation is already active")
        supervisor: ResourceProcessSupervisor | None = None
        try:
            supervisor = self._root_supervisor()
            supervisor.root_stop(timeout_s=timeout_s)
        except BaseException as error:
            if supervisor is not None:
                self._abort_root_after_failure(supervisor, error)
            raise
        finally:
            self._operation_guard.release()

    def root_abort(self) -> None:
        """Close/fail only Root RPC; retain the exact child and funded charge."""
        self._check_owner()
        if not self._operation_guard.acquire(blocking=False):
            raise ResourceProcessUnproven("command process operation is already active")
        try:
            supervisor = self._supervisor
            if supervisor is None:
                return
            if (
                not self._supervisor_context_valid
                or supervisor._owner_pid != self._pid
                or supervisor._owner_thread is not self._thread
            ):
                raise ResourceProcessUnproven("command Root owner is unavailable")
            try:
                supervisor.close_root_control()
            except BaseException:
                self._terminal_uncertainty = True
                raise
            finally:
                self._retain_root_close_uncertainty(supervisor)
        finally:
            self._operation_guard.release()

    def receive_binding(self, *, timeout_s: float = 30) -> ResourceBinding:
        self._check_owner()
        if not _valid_command_timeout(timeout_s):
            raise ValueError("command process binding timeout must be finite and nonnegative")
        if not self._registered():
            raise StorageAdmissionUnavailable()
        if not self._operation_guard.acquire(blocking=False):
            raise ResourceProcessUnproven("command process operation is already active")
        try:
            supervisor = self._supervisor
            if supervisor is None or self._binding_attempted or self._terminal_uncertainty:
                raise ResourceProcessUnproven("command process binding is unavailable")
            if (
                not self._supervisor_context_valid
                or supervisor._owner_pid != self._pid
                or supervisor._owner_thread is not self._thread
                or supervisor._binding_failed is not False
                or supervisor._binding is not None
            ):
                self._terminal_uncertainty = True
                raise ResourceProcessUnproven("command process binding owner is unavailable")
            self._binding_attempted = True
            try:
                binding = supervisor.receive_binding(timeout_s=timeout_s)
                label = supervisor.sql_application_name
                if (
                    type(binding) is not ResourceBinding
                    or supervisor._binding is not binding
                    or supervisor._binding_failed is not False
                    or binding.store_id != self._store_id
                    or binding.target_identity_sha256 != self._target_digest
                    or not is_sql_process_application_name(label)
                ):
                    raise ResourceProcessUnproven("command process binding is unproven")
                self._check_owner()
                if not self._registered():
                    raise StorageAdmissionUnavailable()
                self._binding = binding
                self._sql_application_name = label
                return binding
            except BaseException:
                self._terminal_uncertainty = True
                raise
        finally:
            self._operation_guard.release()

    def send_signal(self, signum: int) -> None:
        self._check_owner()
        if not self._operation_guard.acquire(blocking=False):
            raise ResourceProcessUnproven("command process operation is already active")
        try:
            supervisor = self._supervisor
            if supervisor is None or not self._supervisor_context_valid or not self._registered():
                raise ResourceProcessUnproven("command process supervisor is unavailable")
            # Poison blocks proof/release, but cleanup may still signal this exact child.
            supervisor.send_signal(signum)
        finally:
            self._operation_guard.release()

    def cleanup(self, timeout_s: float | None = None) -> bool:
        """Join/close this child without returning its command reservation.

        None performs a nonblocking observation. Cleanup is allowed after
        uncertainty so the same retained child can still be stopped safely.
        """
        self._check_owner()
        if not _valid_command_timeout(timeout_s, optional=True):
            raise ValueError("command process cleanup timeout must be finite and nonnegative")
        if not self._operation_guard.acquire(blocking=False):
            return False
        try:
            supervisor = self._supervisor
            if (
                supervisor is None
                or not self._supervisor_context_valid
                or supervisor._owner_pid != self._pid
                or supervisor._owner_thread is not self._thread
            ):
                return False
            # Once native cleanup starts, normal join can never refund this token.
            self._terminal_uncertainty = True
            if getattr(supervisor, "_root_control_enabled", False):
                supervisor.close_root_control()
            try:
                supervisor.wait(timeout_s=0 if timeout_s is None else timeout_s)
            except subprocess.TimeoutExpired:
                return False
            supervisor.close()
            try:
                channel = supervisor._channel
                broker_channel = supervisor._broker_channel
                root_channel = getattr(supervisor, "_root_channel", None)
                return bool(
                    supervisor._closed is True
                    and supervisor._exited is True
                    and supervisor._pidfd is None
                    and supervisor._owner_pid == self._pid
                    and supervisor._owner_thread is self._thread
                    and channel.fileno() == -1
                    and (broker_channel is None or broker_channel.fileno() == -1)
                    and (root_channel is None or root_channel.fileno() == -1)
                )
            except BaseException:
                self._terminal_uncertainty = True
                raise
        finally:
            self._operation_guard.release()

    def join(self, timeout_s: float | None = None) -> bool:
        self._check_owner()
        if not _valid_command_timeout(timeout_s, optional=True):
            raise ValueError("command process join timeout must be finite and nonnegative")
        if not self._operation_guard.acquire(blocking=False):
            return False
        try:
            if self._terminal_uncertainty:
                return False
            supervisor = self._supervisor
            binding = self._binding
            label = self._sql_application_name
            if supervisor is None or binding is None or label is None or not self._registered():
                return False
            self._retain_root_close_uncertainty(supervisor)
            if self._terminal_uncertainty:
                return False
            if (
                not self._supervisor_context_valid
                or supervisor._owner_pid != self._pid
                or supervisor._owner_thread is not self._thread
                or supervisor._binding_failed is not False
                or supervisor._binding is not binding
                or supervisor._sql_application_name != label
                or supervisor._pidfd is None
            ):
                self._terminal_uncertainty = True
                return False
            try:
                observation = supervisor.wait(timeout_s=0 if timeout_s is None else timeout_s)
            except subprocess.TimeoutExpired:
                return False
            except BaseException:
                self._terminal_uncertainty = True
                raise
            try:
                valid_observation = is_process_exit_observation(observation)
                if valid_observation:
                    verified_observation = cast(ProcessExitObservation, observation)
                    valid_observation = bool(
                        verified_observation is supervisor.observation
                        and verified_observation.binding is binding
                        and verified_observation.sql_application_name == label
                        and supervisor._exited is True
                        and supervisor._binding_failed is False
                        and supervisor._binding is binding
                        and supervisor._sql_application_name == label
                        and supervisor._owner_pid == self._pid
                        and supervisor._owner_thread is self._thread
                    )
            except BaseException:
                self._terminal_uncertainty = True
                raise
            if not valid_observation:
                self._terminal_uncertainty = True
                return False
            verified_observation = cast(ProcessExitObservation, observation)
            try:
                supervisor.close()
            except BaseException:
                self._terminal_uncertainty = True
                raise
            try:
                channel = supervisor._channel
                broker_channel = supervisor._broker_channel
                native_closed = bool(
                    supervisor._closed is True
                    and supervisor._pidfd is None
                    and supervisor._owner_pid == self._pid
                    and supervisor._owner_thread is self._thread
                    and supervisor._binding_failed is False
                    and supervisor._binding is binding
                    and supervisor._sql_application_name == label
                    and channel.fileno() == -1
                    and (broker_channel is None or broker_channel.fileno() == -1)
                    and (
                        getattr(supervisor, "_root_channel", None) is None
                        or supervisor._root_channel.fileno() == -1
                    )
                    and supervisor.observation is verified_observation
                    and getattr(supervisor, "_root_close_uncertain", False) is False
                )
            except BaseException:
                self._terminal_uncertainty = True
                raise
            if not native_closed:
                self._terminal_uncertainty = True
                return False
            owner = self._reservation._owner
            try:
                self._check_owner()
                with owner._guard:
                    if (
                        not self._registered_locked()
                        or self._supervisor is not supervisor
                        or not self._supervisor_context_valid
                        or self._binding is not binding
                        or self._sql_application_name != label
                        or getattr(supervisor, "_root_close_uncertain", False) is not False
                    ):
                        self._terminal_uncertainty = True
                        return False
                    released = owner._release_command_locked(self._reservation, finish_started=True)
            except BaseException:
                self._terminal_uncertainty = True
                raise
            if not released:
                self._terminal_uncertainty = True
                return False
            self._observation = verified_observation
            return True
        finally:
            self._operation_guard.release()


class CommandThread(Generic[_T]):
    """Retained native worker; its result is separate from its joined exit."""

    def __init__(
        self, reservation: CommandReservation, target: Callable[[], _T], clock: Callable[[], float]
    ) -> None:
        self._reservation = reservation
        self._target = target
        self._clock = clock
        self._pid = os.getpid()
        self._future: Future[_T] = Future()
        self._start_complete = threading.Event()
        self._start_confirmed = threading.Event()
        self._returned = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=False)

    def __copy__(self) -> NoReturn:
        raise TypeError("command thread ownership cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise TypeError("command thread ownership cannot be copied")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("command thread ownership cannot be serialized")

    def _registered_locked(self) -> bool:
        reservation = self._reservation
        return (
            reservation._owner._commands.get(reservation._key) is reservation
            and reservation._thread_worker is self
            and reservation._process_worker is None
            and reservation._state == "started"
            and reservation._start_pid == self._pid
            and reservation._start_thread is self._thread
            and reservation._start_task is None
        )

    def _run(self) -> None:
        try:
            if self._pid != os.getpid() or self._thread is not threading.current_thread():
                raise StorageAdmissionUnavailable()
            now = self._clock()
            validate_clock(now)
            reservation = self._reservation
            owner = reservation._owner
            with owner._guard:
                if (
                    not self._registered_locked()
                    or not owner._accepting
                    or reservation._generation != owner._generation
                    or not reservation._fits_locked(now)
                ):
                    raise StorageAdmissionUnavailable()
            self._future.set_result(self._target())
        except BaseException as error:
            self._future.set_exception(error)
        finally:
            owner = self._reservation._owner
            with owner._guard:
                self._returned.set()

    def result(self, timeout: float | None = None) -> _T:
        if self._pid != os.getpid() or self._thread is threading.current_thread():
            raise StorageAdmissionUnavailable()
        return self._future.result(timeout=timeout)

    async def await_result(self) -> _T:
        if self._pid != os.getpid() or self._thread is threading.current_thread():
            raise StorageAdmissionUnavailable()
        wrapped = asyncio.wrap_future(self._future)

        def retrieve_late_exception(completed: asyncio.Future[_T]) -> None:
            if not completed.cancelled():
                completed.exception()

        wrapped.add_done_callback(retrieve_late_exception)
        return await asyncio.shield(wrapped)

    def join(self, timeout: float | None = None) -> bool:
        if timeout is not None and (
            isinstance(timeout, bool) or not math.isfinite(timeout) or timeout < 0
        ):
            raise ValueError("command join timeout must be finite and nonnegative")
        if (
            self._pid != os.getpid()
            or self._thread is threading.current_thread()
            or not self._start_complete.is_set()
        ):
            return False
        owner = self._reservation._owner
        with owner._guard:
            if not self._registered_locked():
                return False
        try:
            self._thread.join(timeout=timeout)
        except RuntimeError:
            return False  # Failed start without positive entry/exit evidence.
        if not self._returned.is_set() or self._thread.is_alive():
            return False
        with owner._guard:
            if not self._registered_locked():
                return False
            return owner._release_command_locked(self._reservation, finish_started=True)


@dataclass(frozen=True)
class AdmissionLedgerDiagnostic:
    """One coherent local basis; never a grant or a count of live agents."""

    snapshot: AdmissionSnapshot
    policy: AdmissionPolicy
    budget: ResourceAmounts | None
    observation: CapacityObservation | None
    ceiling: ResourceAmounts | None
    decision: AdmissionDecision


@dataclass(frozen=True, eq=False)
class DeliveryRetirementHold:
    """Exact local hold, consumed only after trusted shared retirement.

    A notification is not evidence. Only the lease owner establishes physical
    readiness; no callback, SQL or wait runs while it holds the ledger guard.
    The caller must reconcile uncertain shared results before calling finish.
    """

    _lease: "DeliveryReservation"
    _signal: threading.Event = field(default_factory=threading.Event, init=False)
    _cleanup_leases: set["DeliveryReservation"] = field(default_factory=set, init=False)

    def _ready_locked(self, owner: "DeliveryAdmission") -> bool:
        lease = self._lease
        return (
            lease._owner is owner
            and owner._leases.get(lease._key) is lease
            and lease._retirement_hold is self
            and lease._state == "started"
            and lease._retirement_requested
            and lease._retirement_ready
            and lease._storage_stage is None
            and not lease._storage_connections
            and not lease._physical_effects
        )

    @property
    def physical_ready(self) -> bool:
        owner = self._lease._owner
        with owner._guard:
            return self._ready_locked(owner)

    def finish(self) -> bool:
        owner = self._lease._owner
        with owner._guard:
            if not self._ready_locked(owner) or self._cleanup_leases:
                return False
            self._lease._retirement_hold = None
            return owner._release_locked(self._lease)


@dataclass(frozen=True)
class PhysicalCloseResult:
    pending: int
    busy: bool = False

    @property
    def clean(self) -> bool:
        return self.pending == 0 and not self.busy


@dataclass(frozen=True, eq=False)
class PhysicalEffectReservation:
    """Exact native owner; thread completion never confirms this token."""

    _lease: "DeliveryReservation"
    _owners: list[tuple[object, Callable[[], bool]]] = field(
        default_factory=list, init=False, repr=False
    )
    _unobserved_starts: set[object] = field(default_factory=set, init=False, repr=False)

    def __copy__(self) -> NoReturn:
        raise TypeError("physical effect ownership is not transferable")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise TypeError("physical effect ownership is not transferable")

    def __reduce__(self) -> NoReturn:
        raise TypeError("physical effect ownership is not transferable")

    def _owner(self) -> "DeliveryAdmission":
        owner = self._lease._owner
        if owner._pid != os.getpid():
            raise StorageAdmissionUnavailable()
        return owner

    def retain(self, native_owner: object, closed: Callable[[], bool]) -> None:
        """Retain a nonblocking native observer; it must perform no SQL or Work effect."""
        owner = self._owner()
        with owner._guard:
            if self not in self._lease._physical_effects:
                raise StorageAdmissionUnavailable()
            if not callable(closed):
                raise ValueError("native owner requires a closure observer")
            if not any(value[0] is native_owner for value in self._owners):
                self._owners.append((native_owner, closed))

    def _mark_unobserved_start(self) -> bool:
        """Add a sticky obligation; artifact cleanup cannot prove this helper closed."""
        owner = self._owner()
        with owner._guard:
            lease = self._lease
            if owner._leases.get(lease._key) is not lease or self not in lease._physical_effects:
                return False
            if not self._unobserved_starts:
                marker = object()
                self._unobserved_starts.add(marker)
                self._owners.append((marker, lambda: False))
            owner._physical_closes.discard(self)
            return True

    def _register_late(self) -> None:
        """Trusted scope only, after its positive full native closure proof."""
        owner = self._owner()
        with owner._guard:
            lease = self._lease
            if (
                owner._leases.get(lease._key) is lease
                and self in lease._physical_effects
                and not self._unobserved_starts
            ):
                owner._physical_closes.add(self)

    def finish(self) -> bool:
        owner = self._owner()
        lease = self._lease
        with owner._guard:
            if owner._leases.get(lease._key) is not lease or self not in lease._physical_effects:
                return False
            if self._unobserved_starts:
                return False
            owners = tuple(self._owners)
        # Kernel/native observations run outside the admission guard.
        try:
            if any(closed() is not True for _, closed in owners):
                return False
        except Exception:
            return False
        with owner._guard:
            if owner._leases.get(lease._key) is not lease or self not in lease._physical_effects:
                return False
            if len(self._owners) != len(owners) or any(
                a is not b for a, b in zip(self._owners, owners)
            ):
                return False
            lease._physical_effects.remove(self)
            owner._physical_closes.discard(self)
            self._owners.clear()
            if (
                lease._retirement_requested
                and not lease._physical_effects
                and not lease._storage_connections
                and lease._storage_stage is None
            ):
                owner._release_locked(lease)
            return True


@dataclass(frozen=True, eq=False)
class StorageStageReservation:
    """One physical storage stage; copies cannot retire the registered owner."""

    _lease: "DeliveryReservation"

    def finish(self) -> bool:
        lease = self._lease
        owner = lease._owner
        with owner._guard:
            if (
                lease._state != "started"
                or owner._leases.get(lease._key) is not lease
                or lease._storage_stage is not self
            ):
                return False
            lease._storage_stage = None
            lease._release_storage_locked()
            if lease._retirement_requested:
                owner._release_locked(lease)
            return True


@dataclass(frozen=True, eq=False)
class StorageConnectionReservation:
    """An exact physical loan; the initial closing credit stays reserved."""

    _lease: "DeliveryReservation"
    _additional: bool
    _control_connection: weakref.ReferenceType[object] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def finish(self) -> bool:
        lease = self._lease
        owner = lease._owner
        with owner._guard:
            if (
                lease._state != "started"
                or owner._leases.get(lease._key) is not lease
                or self not in lease._storage_connections
            ):
                return False
            lease._storage_connections.remove(self)
            owner._storage_connection_loans -= 1
            if self._additional:
                owner._additional_storage_connections -= 1
                unit = ResourceAmounts(storage_slots=1)
                owner._committed = owner._committed - unit
                lease._remaining = lease._remaining - unit
            if lease._retirement_requested and not lease._storage_connections:
                owner._release_locked(lease)
            return True


class DeliveryReservation:
    """A handoff can be cancelled only before one physical worker starts it."""

    def __init__(
        self, owner: "DeliveryAdmission", key: int, receiver: str | None, footprint: ResourceAmounts
    ):
        self._owner = owner
        self._key = key
        self._receiver = receiver
        self._state: ReservationState = "reserved"
        self._footprint = footprint
        self._remaining = footprint
        self._storage_stage: StorageStageReservation | None = None
        self._storage_connections: set[StorageConnectionReservation] = set()
        self._physical_effects: set[PhysicalEffectReservation] = set()
        self._retirement_requested = False
        self._retirement_hold: DeliveryRetirementHold | None = None
        self._retirement_ready = False

    @property
    def storage_process_current(self) -> bool:
        return self._owner._pid == os.getpid()

    @property
    def storage_control_current(self) -> bool:
        return False

    def owns_storage_connection(self, loan: object) -> bool:
        if not self.storage_process_current or type(loan) is not StorageConnectionReservation:
            return False
        owner = self._owner
        with owner._guard:
            return (
                self._state == "started"
                and owner._leases.get(self._key) is self
                and loan._lease is self
                and loan in self._storage_connections
            )

    def bind_control_sql_connection(self, loan: object, connection: object) -> bool:
        if not self.storage_process_current or type(loan) is not StorageConnectionReservation:
            return False
        owner = self._owner
        with owner._guard:
            if not self.storage_control_current or not self.owns_storage_connection(loan):
                return False
            reference = loan._control_connection
            if reference is not None:
                return False  # Capture is one-shot, even for the original facade.
            object.__setattr__(loan, "_control_connection", weakref.ref(connection))
            return True

    def owns_control_sql_connection(self, loan: object, connection: object) -> bool:
        if not self.storage_process_current or type(loan) is not StorageConnectionReservation:
            return False
        owner = self._owner
        with owner._guard:
            return (
                self.storage_control_current
                and self.owns_storage_connection(loan)
                and loan._control_connection is not None
                and loan._control_connection() is connection
            )

    @property
    def footprint(self) -> ResourceAmounts:
        return self._footprint

    def prepare_storage_scope(self) -> None:
        """Trusted physical-worker hook; control SQL must not prepare recursively."""
        owner = self._owner
        with owner._guard:
            if owner._control_lease is self:
                return
            if (
                owner._leases.get(self._key) is not self
                or self._state != "started"
                or self._retirement_requested
            ):
                raise StorageAdmissionUnavailable()
            coordinator = owner._shared_coordinator
            if owner._shared_required and (not owner._shared_ready or coordinator is None):
                raise StorageAdmissionUnavailable()
        if coordinator is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise StorageAdmissionUnavailable()  # SQL belongs in the owned worker.
        coordinator.prepare(self)

    @property
    def state(self) -> ReservationState:
        with self._owner._guard:
            return self._state

    def start(self) -> bool:
        owner = self._owner
        with owner._guard:
            if (
                self._state != "reserved"
                or not owner._accepting
                or owner._leases.get(self._key) is not self
            ):
                return False
            self._state = "started"
            owner._pending.pop(self._key)
            owner._started += 1
            if isinstance(self, StorageReservation):
                owner._storage_only_started += 1
            return True

    def hold_retirement(self) -> DeliveryRetirementHold:
        """Register before shared preparation; an uncertain commit must stay held."""
        owner = self._owner
        with owner._guard:
            if (
                self._state != "started"
                or owner._leases.get(self._key) is not self
                or self._retirement_requested
                or self._retirement_hold is not None
                or self._storage_connections
                or self._storage_stage is not None
                or owner._control_lease is self
            ):
                raise RuntimeError("retirement hold requires an exact started data lease")
            hold = DeliveryRetirementHold(self)
            self._retirement_hold = hold
            return hold

    def hold_physical_effect(self) -> PhysicalEffectReservation:
        """Private native backend hook, registered before possible external start."""
        owner = self._owner
        if owner._pid != os.getpid():
            raise StorageAdmissionUnavailable()
        with owner._guard:
            if (
                owner._leases.get(self._key) is not self
                or self._state != "started"
                or self._retirement_requested
                or owner._control_lease is self
            ):
                raise StorageAdmissionUnavailable()
            token = PhysicalEffectReservation(self)
            self._physical_effects.add(token)
            return token

    def cancel_before_start(self) -> bool:
        with self._owner._guard:
            if self._state != "reserved":
                return False
            return self._owner._release_locked(self)

    def finish(self) -> bool:
        """Called by the synchronous worker's finally, never its awaiter."""
        with self._owner._guard:
            if self._state == "released":
                return False
            if self._state != "started":
                raise RuntimeError("delivery reservation has not started")
            if self._retirement_hold is not None or self._physical_effects:
                return self.finish_after_storage()
            if self._storage_stage is not None or self._storage_connections:
                raise RuntimeError("active storage stage must finish physically first")
            return self._owner._release_locked(self)

    def finish_after_storage(self) -> bool:
        """Physical body is done; retain costs until its exact loans retire.

        This is a handoff to already owned physical cleanup, not a timeout or
        cancellation proof. No new loan is allowed after the handoff. A failed
        close must keep its strong handle/token in the close registry; only a
        later physical witness lets the last token release this invocation.
        """
        owner = self._owner
        with owner._guard:
            if (
                owner._leases.get(self._key) is not self
                or self._state == "released"
                or self._retirement_requested
            ):
                return False
            if self._state != "started":
                raise RuntimeError("delivery reservation has not started")
            self._retirement_requested = True
            owner._storage_retirement_pending += 1
            if (
                self._storage_stage is not None
                or self._storage_connections
                or self._physical_effects
            ):
                return False
            return owner._release_locked(self)

    def try_acquire_storage_connection(self, *, now: float) -> StorageConnectionReservation | None:
        """Borrow an existing closing credit, or charge one additional connection.

        Reusing an already admitted credit never creates new allowance, including
        during drain or after a stale observation. Additional physical loans need
        a fresh decision and cannot grow after admission closes. The borrower must
        retain its exact token through setup, SQL, reset and physical return/close.
        """
        validate_clock(now)
        owner = self._owner
        with owner._guard:
            if (
                self._state != "started"
                or owner._leases.get(self._key) is not self
                or self._storage_stage is not None
                or self._retirement_requested
            ):
                return None
            if (
                self._retirement_hold is not None
                and len(self._storage_connections) >= self._footprint.storage_slots
            ):
                return None  # No unaccounted growth beyond the mapped footprint.
            additional = self._remaining.storage_slots == len(self._storage_connections)
            if additional:
                if not owner._accepting:
                    return None
                decision = owner._current_decision(now)
                if (
                    decision.reason not in ("capacity_available", "resource_exhausted")
                    or decision.resource_capacity.storage_slots <= owner._committed.storage_slots
                ):
                    return None
            connection = StorageConnectionReservation(self, additional)
            if additional:
                unit = ResourceAmounts(storage_slots=1)
                owner._committed = owner._committed + unit
                self._remaining = self._remaining + unit
            self._storage_connections.add(connection)
            owner._storage_connection_loans += 1
            if additional:
                owner._additional_storage_connections += 1
            return connection

    def take_storage_stage(self, *, now: float) -> StorageStageReservation | None:
        """Claim already charged storage or reserve storage alone for reentry.

        This does not start SQL or wait for capacity. The physical caller owns
        the token through completion, including uncertain cleanup; an awaiter
        cannot release it. Memory, CPU and receiver ownership remain charged.
        """
        validate_clock(now)
        owner = self._owner
        with owner._guard:
            if (
                self._state != "started"
                or owner._leases.get(self._key) is not self
                or self._storage_stage is not None
                or self._storage_connections
                or self._retirement_requested
            ):
                return None
            needs_storage = not self._remaining.storage_slots
            if needs_storage:
                if self._retirement_hold is not None and not self._footprint.storage_slots:
                    return None
                if not owner._accepting:
                    return None
                decision = owner._current_decision(now)
                if (
                    decision.reason not in ("capacity_available", "resource_exhausted")
                    or decision.resource_capacity.storage_slots <= owner._committed.storage_slots
                ):
                    return None
            stage = StorageStageReservation(self)
            if needs_storage:
                unit = ResourceAmounts(storage_slots=1)
                owner._committed = owner._committed + unit
                self._remaining = self._remaining + unit
            self._storage_stage = stage
            return stage

    def release_storage(self) -> bool:
        """Physical storage-stage completion, never cancellation of its awaiter."""
        owner = self._owner
        with owner._guard:
            if (
                self._state != "started"
                or owner._leases.get(self._key) is not self
                or self._storage_stage is not None
                or self._storage_connections
                or not self._remaining.storage_slots
            ):
                return False
            return self._release_storage_locked()

    def _release_storage_locked(self) -> bool:
        released = ResourceAmounts(storage_slots=self._remaining.storage_slots)
        self._owner._committed = self._owner._committed - released
        self._remaining = self._remaining - released
        return True


class StorageReservation(DeliveryReservation):
    """Storage operation using the common physical lifecycle, without a receiver."""

    def __init__(self, owner: "DeliveryAdmission", key: int, footprint: ResourceAmounts):
        super().__init__(owner, key, None, footprint)


class _StorageControlReservation(StorageReservation):
    """Already started infrastructure credit, excluded from normal data costs."""

    def __init__(
        self,
        owner: "DeliveryAdmission",
        key: int,
        footprint: ResourceAmounts,
        completion: DeliveryRetirementHold | None = None,
    ):
        super().__init__(owner, key, footprint)
        self._completion_hold = completion

    @property
    def storage_control_current(self) -> bool:
        if not self.storage_process_current:
            return False
        owner = self._owner
        with owner._guard:
            return (
                owner._control_lease is self
                and owner._leases.get(self._key) is self
                and self._state == "started"
            )

    def try_acquire_storage_connection(self, *, now: float) -> StorageConnectionReservation | None:
        validate_clock(now)
        owner = self._owner
        with owner._guard:
            if (
                owner._control_lease is not self
                or owner._leases.get(self._key) is not self
                or self._state != "started"
                or self._retirement_requested
                or self._storage_connections
            ):
                return None
            loan = StorageConnectionReservation(self, False)
            self._storage_connections.add(loan)
            owner._storage_connection_loans += 1
            return loan

    def reserve_child_sql_session(
        self, reservation: object, *, now: float
    ) -> StorageConnectionReservation:
        """Borrow only a prepaid data unit, never the control lane's credit."""
        validate_clock(now)
        owner = self._owner
        if not self.storage_process_current:
            raise StorageAdmissionUnavailable()
        # Coordinator checks take coordinator -> owner locks, as prepare does.
        # Never invoke that boundary while holding the owner guard.
        coordinator = owner._shared_coordinator
        if owner._shared_required and (
            coordinator is None or not coordinator.owns_prepared_storage(reservation)
        ):
            raise StorageAdmissionUnavailable()
        with owner._guard:
            if type(reservation) not in (StorageReservation, DeliveryReservation) or not isinstance(
                reservation, DeliveryReservation
            ):
                raise StorageAdmissionUnavailable()
            if (
                not self.storage_control_current
                or self._retirement_requested
                or not owner._accepting
                or not owner.owns_normal_storage(reservation)
                or reservation._retirement_hold is None
                or reservation._storage_stage is not None
                or reservation._remaining.storage_slots <= len(reservation._storage_connections)
            ):
                raise StorageAdmissionUnavailable()
            decision = owner._current_decision(now)
            if (
                decision.reason not in ("capacity_available", "resource_exhausted")
                or decision.resource_debt.storage_slots
            ):
                raise StorageAdmissionUnavailable()
            loan = StorageConnectionReservation(reservation, False)
            reservation._storage_connections.add(loan)
            owner._storage_connection_loans += 1
        return loan

    def owns_child_sql_session(self, reservation: object, loan: object) -> bool:
        owner = self._owner
        if not self.storage_process_current:
            return False
        coordinator = owner._shared_coordinator
        if owner._shared_required and (
            coordinator is None or not coordinator.owns_prepared_storage(reservation)
        ):
            return False
        with owner._guard:
            return (
                self.storage_control_current
                and owner._accepting
                and owner.owns_normal_storage(reservation)
                and isinstance(reservation, DeliveryReservation)
                and reservation.owns_storage_connection(loan)
            )

    def take_storage_stage(self, *, now: float) -> StorageStageReservation | None:
        validate_clock(now)
        return None  # Control cannot convert its infrastructure credit into a data stage.


class StorageControlPermit:
    """Private server wiring capability; exact identity, never a capacity claim."""

    def __init__(self, owner: "DeliveryAdmission"):
        self._owner = owner
        self._lane_gate = threading.Lock()
        self._lane_owner: int | None = None

    def belongs_to(self, owner: "DeliveryAdmission") -> bool:
        return self._owner is owner and owner._control_permit is self

    def check_current_operation(self) -> None:
        """Validate this exact already-active physical control scope; create none."""
        scope = _CURRENT.get()
        if scope is None or scope.logical or not scope.is_current():
            raise StorageAdmissionUnavailable()
        owner = self._owner
        with owner._guard:
            lease = owner._control_lease
            if (
                not self.belongs_to(owner)
                or self._lane_owner != threading.get_ident()
                or scope.owner is not lease
                or lease is None
                or not lease.storage_control_current
            ):
                raise StorageAdmissionUnavailable()

    @contextmanager
    def _serial(self, *, wait: bool) -> Iterator[int]:
        owner = self._owner
        with owner._guard:  # Reject a child before touching inherited lane locks.
            if type(wait) is not bool:
                raise ValueError("control wait must be boolean")
            if not self.belongs_to(owner) or self._lane_owner == threading.get_ident():
                raise StorageAdmissionUnavailable()
            generation = owner._generation
        if not self._lane_gate.acquire(blocking=wait):
            raise StorageAdmissionUnavailable()
        try:
            with owner._guard:
                self._lane_owner = threading.get_ident()
            yield generation
        finally:
            with owner._guard:
                self._lane_owner = None
            self._lane_gate.release()

    @contextmanager
    def operation(
        self,
        *,
        clock: Callable[[], float],
        retirement: DeliveryRetirementHold | None = None,
        wait: bool = False,
    ) -> Iterator[None]:
        owner = self._owner
        validate_clock(clock())
        with self._serial(wait=wait) as generation:
            with owner._guard:
                completion = type(
                    retirement
                ) is DeliveryRetirementHold and retirement._ready_locked(owner)
                if (
                    not self.belongs_to(owner)
                    or (not owner._accepting and not completion)
                    or (owner._generation != generation and not completion)
                    or (retirement is not None and not completion)
                    or owner._control_lease is not None
                    or storage_close_uncertainty()
                ):
                    raise StorageAdmissionUnavailable()
                key = next(owner._sequence)
                lease = _StorageControlReservation(owner, key, ResourceAmounts(), retirement)
                if retirement is not None:
                    retirement._cleanup_leases.add(lease)
                lease._state = "started"
                owner._control_lease = lease
                owner._leases[key] = lease
                owner._started += 1
                owner._storage_only_inflight += 1
                owner._storage_only_started += 1
            try:
                with physical_storage_scope(lease, clock=clock):
                    try:
                        yield
                    finally:
                        check_storage_admission()
            finally:
                lease.finish_after_storage()


class DeliveryAdmission:
    """One ledger shared by local entry paths; not a cluster reservation.

    All outstanding reservations are charged against a fresh resource decision.
    This is conservative when measured headroom already includes started work.
    Re-evaluating an old sample cannot add its capacity to this ledger again.
    """

    def __init__(self, policy: AdmissionPolicy, *, budget: ResourceAmounts | None = None):
        self._validate_configuration(policy, budget)
        self._policy = policy
        self._budget = budget
        self._control_permit: StorageControlPermit | None = None
        self._control_lease: _StorageControlReservation | None = None
        self._committed = ResourceAmounts()
        self._pid = os.getpid()
        self._process_guard = threading.RLock()
        self._command_condition = threading.Condition(self._process_guard)
        self._leases: dict[int, DeliveryReservation] = {}
        self._retirement_queue: queue.SimpleQueue[DeliveryRetirementHold | None] = (
            queue.SimpleQueue()
        )
        self._shared_required = False
        self._shared_ready = False
        self._shared_coordinator: SharedStorageCoordinator | None = None
        self._pending: dict[int, DeliveryReservation] = {}
        self._commands: dict[int, CommandReservation] = {}
        self._pending_commands: dict[int, CommandReservation] = {}
        self._receivers: dict[str, int] = {}
        self._sequence = count()
        self._started = 0
        self._command_started = 0
        self._storage_only_inflight = 0
        self._storage_only_started = 0
        self._storage_connection_loans = 0
        self._additional_storage_connections = 0
        self._storage_retirement_pending = 0
        self._physical_closes: set[PhysicalEffectReservation] = set()
        self._physical_retry_guard = threading.Lock()
        self._accepting = True
        self._generation = 0
        self._observation: CapacityObservation | None = None
        self._observation_ceiling: ResourceAmounts | None = None
        self._decision = AdmissionDecision(0, "unknown_capacity")

    @classmethod
    def with_storage_control(
        cls, policy: AdmissionPolicy
    ) -> tuple["DeliveryAdmission", StorageControlPermit]:
        """Trusted process activation, before binding its consumers."""
        owner = cls(policy)
        permit = StorageControlPermit(owner)
        owner._control_permit = permit
        return owner, permit

    @property
    def control_storage_slots(self) -> int:
        with self._guard:
            return int(self._control_permit is not None)

    @property
    def _guard(self) -> threading.RLock:
        if self._pid != os.getpid():
            raise StorageAdmissionUnavailable()
        return self._process_guard

    @property
    def shared_required(self) -> bool:
        with self._guard:
            return self._shared_required

    @property
    def shared_ready(self) -> bool:
        with self._guard:
            return self._shared_ready and self._accepting

    def require_shared_admission(self) -> None:
        """Configuration closes data; it never issues resource authority."""
        with self._guard:
            if self._shared_required:
                return
            if any(lease is not self._control_lease for lease in self._leases.values()):
                raise RuntimeError("normal operations must retire before shared activation")
            self._shared_required = True
            self._shared_ready = False

    def bind_shared_admission(self, coordinator: SharedStorageCoordinator) -> None:
        if coordinator._owner is not self:
            raise ValueError("shared coordinator belongs to another owner")
        with self._guard:
            if (
                not self._shared_required
                or not self._accepting
                or any(lease is not self._control_lease for lease in self._leases.values())
                or self._shared_coordinator not in (None, coordinator)
            ):
                raise RuntimeError("shared admission cannot replace a live owner")
            self._shared_coordinator = coordinator
            self._shared_ready = True

    @staticmethod
    def _validate_configuration(policy: AdmissionPolicy, budget: ResourceAmounts | None) -> None:
        if not isinstance(policy, AdmissionPolicy) or (
            budget is not None and not isinstance(budget, ResourceAmounts)
        ):
            raise ValueError("admission configuration must use validated policy and amounts")

    def reconfigure(
        self,
        policy: AdmissionPolicy,
        *,
        expected_generation: int,
        budget: ResourceAmounts | None = None,
    ) -> int | None:
        """Fence old I/O and retire handoffs; running costs remain unchanged.

        Omitting a budget retains the current allowance, never removes it.
        The caller must capture expected_generation before configuration I/O.
        """
        self._validate_configuration(policy, budget)
        if (
            isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 0
        ):
            raise ValueError("generation must be a nonnegative integer")
        with self._guard:
            if not self._accepting or expected_generation != self._generation:
                return None
            candidate_budget = self._budget if budget is None else budget
            if policy == self._policy and candidate_budget == self._budget:
                return self._generation
            self._policy = policy
            self._budget = candidate_budget
            self._generation += 1
            self._observation = None
            self._observation_ceiling = None
            for lease in tuple(self._pending.values()):
                self._release_locked(lease)
            for command in tuple(self._pending_commands.values()):
                self._release_command_locked(command)
            return self._generation

    def invalidate(self, *, expected_generation: int) -> bool:
        """Discard authority/resource knowledge without retiring started work."""
        if isinstance(expected_generation, bool) or not isinstance(expected_generation, int):
            raise ValueError("generation must be an integer")
        with self._guard:
            if not self._accepting or expected_generation != self._generation:
                return False
            self._generation += 1
            self._observation = None
            self._observation_ceiling = None
            for lease in tuple(self._pending.values()):
                self._release_locked(lease)
            for command in tuple(self._pending_commands.values()):
                self._release_command_locked(command)
            return True

    @property
    def generation(self) -> int:
        with self._guard:
            return self._generation

    def observe(
        self,
        observation: CapacityObservation,
        *,
        now: float,
        generation: int,
        ceiling: ResourceAmounts | None = None,
    ) -> bool:
        validate_clock(now)
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise ValueError("generation must be an integer")
        if ceiling is not None and not isinstance(ceiling, ResourceAmounts):
            raise ValueError("observation ceiling must use validated amounts")
        with self._guard:
            if not self._accepting or generation != self._generation:
                return False
            decision = evaluate_capacity(self._policy, observation, now=now, budget=self._budget)
            if decision.reason == "future_observation":
                # A corrupt future timestamp must not block later valid samples.
                return False
            if (
                self._observation is not None
                and observation.observed_at <= self._observation.observed_at
            ):
                return False
            self._observation = observation
            self._observation_ceiling = ceiling
            self._decision = decision
            # Newest unstarted handoffs yield first under pressure. Started
            # work is never scanned or retired by a resource observation. A
            # zero-storage command does not yield to storage-only debt.
            if self._current_decision(now).resource_debt != ResourceAmounts():
                pending: Iterator[tuple[int, DeliveryReservation | CommandReservation]] = merge(
                    reversed(tuple(self._pending.items())),
                    reversed(tuple(self._pending_commands.items())),
                    key=lambda entry: entry[0],
                    reverse=True,
                )
                for _, reservation in pending:
                    debt = self._current_decision(now).resource_debt
                    if debt == ResourceAmounts():
                        break
                    if isinstance(reservation, CommandReservation):
                        if not self._overlaps_debt(reservation._footprint, debt):
                            continue
                        self._release_command_locked(reservation)
                    else:
                        self._release_locked(reservation)
            return True

    def _current_decision(
        self, now: float, *, charged: bool = True, close_uncertain: bool | None = None
    ) -> AdmissionDecision:
        committed = self._committed if charged else ResourceAmounts()
        if (
            (self._shared_required and not self._shared_ready)
            or self._observation is None
            or (storage_close_uncertainty() if close_uncertain is None else close_uncertain)
        ):
            return AdmissionDecision(0, "unknown_capacity", resource_debt=committed)
        budget = self._budget
        if self._observation_ceiling is not None:
            ceiling = self._observation_ceiling
            budget = (
                ceiling
                if budget is None
                else ResourceAmounts(
                    min(budget.memory_bytes, ceiling.memory_bytes),
                    min(budget.cpu_millicores, ceiling.cpu_millicores),
                    min(budget.storage_slots, ceiling.storage_slots),
                )
            )
        # Re-evaluation checks freshness and subtracts immutable committed costs.
        return evaluate_capacity(
            self._policy, self._observation, now=now, committed=committed, budget=budget
        )

    @staticmethod
    def _command_fits(footprint: ResourceAmounts, remaining: ResourceAmounts) -> bool:
        return (
            footprint.memory_bytes <= remaining.memory_bytes
            and footprint.cpu_millicores <= remaining.cpu_millicores
            and footprint.storage_slots == 0
        )

    @staticmethod
    def _overlaps_debt(footprint: ResourceAmounts, debt: ResourceAmounts) -> bool:
        return (
            (footprint.memory_bytes > 0 and debt.memory_bytes > 0)
            or (footprint.cpu_millicores > 0 and debt.cpu_millicores > 0)
            or (footprint.storage_slots > 0 and debt.storage_slots > 0)
        )

    def owns_normal_storage(self, lease: object) -> bool:
        """Exact registered normal lease; a control credit cannot serve data SQL."""
        with self._guard:
            return (
                isinstance(lease, DeliveryReservation)
                and lease._owner is self
                and self._leases.get(lease._key) is lease
                and lease is not self._control_lease
                and lease._state == "started"
                and not lease._retirement_requested
            )

    def can_reserve(self, receiver: str, *, now: float) -> bool:
        """Advisory eligibility only; an effect still needs try_reserve and start."""
        validate_clock(now)
        if not isinstance(receiver, str) or not receiver.strip():
            raise ValueError("receiver must be a nonempty identity")
        with self._guard:
            return bool(
                self._accepting
                and receiver not in self._receivers
                and self._current_decision(now).available_slots
            )

    def try_reserve_command(
        self, *, footprint: ResourceAmounts, now: float
    ) -> CommandAdmissionAttempt:
        """Reserve a server-selected command's positive RAM/CPU footprint.

        This token charges only RAM and CPU; the actor separately retains its
        funded control connection. It grants no storage or shared-prep authority,
        so work needing either uses the normal data reservation path.
        """
        validate_clock(now)
        if (
            type(footprint) is not ResourceAmounts
            or footprint.memory_bytes <= 0
            or footprint.cpu_millicores <= 0
            or footprint.storage_slots != 0
        ):
            raise ValueError("command footprint requires exact positive RAM/CPU and zero storage")
        with self._guard:
            if not self._accepting:
                return CommandAdmissionAttempt(None, "stopping")
            decision = self._current_decision(now)
            if decision.reason not in {"capacity_available", "resource_exhausted"}:
                return CommandAdmissionAttempt(None, decision.reason)
            remaining = decision.resource_capacity.excess_over(self._committed)
            if not self._command_fits(footprint, remaining):
                return CommandAdmissionAttempt(None, "resource_exhausted")
            key = next(self._sequence)
            reservation = CommandReservation(self, key, self._generation, footprint)
            self._committed = self._committed + footprint
            self._commands[key] = reservation
            self._pending_commands[key] = reservation
            return CommandAdmissionAttempt(reservation, "capacity_available")

    def _reserve_root_control_startup(
        self,
        activation: VerifiedRootControlActivation,
        *,
        compute_sample: VerifiedComputeStartupSample,
        now: float,
    ) -> CommandAdmissionAttempt:
        """Private one-use startup reservation; never publishes storage capacity."""
        validate_clock(now)
        if (
            not is_verified_root_control_activation(activation)
            or not is_verified_compute_startup_sample(compute_sample)
            or compute_sample.activation is not activation
            or compute_sample.envelope != activation.activation.envelope
            or type(compute_sample.observation) is not ComputeObservation
            or self._pid != os.getpid()
        ):
            return CommandAdmissionAttempt(None, "unknown_capacity")
        observation = compute_sample.observation
        footprint_record = activation.activation.startup_footprint
        footprint = ResourceAmounts(
            footprint_record.memory_bytes,
            footprint_record.cpu_millicores,
            footprint_record.storage_slots,
        )
        with self._guard:
            if (
                not self._accepting
                or self._control_permit is None
                or self._shared_required
                or self._committed != ResourceAmounts()
                or self._commands
                or self._leases
                or not activation._bind_startup_owner(self)
            ):
                return CommandAdmissionAttempt(None, "unknown_capacity")
            claims = activation.activation.envelope.claims
            capacity = compute_capacity(
                self._policy,
                observation,
                now=now,
                budget_memory_bytes=claims.memory_budget_bytes,
                budget_cpu_millicores=claims.cpu_budget_millicores,
            )
            if capacity is None:
                return CommandAdmissionAttempt(None, "unknown_capacity")
            if (
                footprint.memory_bytes > capacity.memory_bytes
                or footprint.cpu_millicores > capacity.cpu_millicores
            ):
                return CommandAdmissionAttempt(None, "resource_exhausted")
            basis = _issue_verified_compute_startup_basis(
                compute_sample,
                owner=self,
                generation=self._generation,
            )
            key = next(self._sequence)
            reservation = CommandReservation(
                self,
                key,
                self._generation,
                footprint,
                startup_basis=basis,
            )
            self._committed = self._committed + footprint
            self._commands[key] = reservation
            self._pending_commands[key] = reservation
            return CommandAdmissionAttempt(reservation, "capacity_available")

    def try_reserve(self, receiver: str, *, now: float, storage_slots: int = 1) -> AdmissionAttempt:
        """Reserve the trusted operation's complete minimum physical footprint.

        Internal callers declare required overlapping storage stages. This is
        consumption per operation, never a global delivery concurrency limit.
        """
        validate_clock(now)
        ResourceAmounts(storage_slots=storage_slots)
        if storage_slots == 0:
            raise ValueError("storage_slots must be an integer >= 1")
        if not isinstance(receiver, str) or not receiver.strip():
            raise ValueError("receiver must be a nonempty identity")
        with self._guard:
            if not self._accepting:
                return AdmissionAttempt(None, "stopping")
            if receiver in self._receivers:
                return AdmissionAttempt(None, "receiver_busy")
            decision = self._current_decision(now)
            if not decision.available_slots:
                return AdmissionAttempt(None, decision.reason)
            if (
                self._committed.storage_slots + storage_slots
                > decision.resource_capacity.storage_slots
            ):
                return AdmissionAttempt(None, "resource_exhausted")
            key = next(self._sequence)
            footprint = ResourceAmounts(
                self._policy.memory_per_delivery_bytes,
                self._policy.cpu_per_delivery_millicores,
                storage_slots,
            )
            lease = DeliveryReservation(self, key, receiver, footprint)
            self._committed = self._committed + footprint
            self._leases[key] = lease
            self._pending[key] = lease
            self._receivers[receiver] = key
            return AdmissionAttempt(lease, "capacity_available")

    def try_reserve_storage(self, *, now: float, storage_slots: int = 1) -> StorageAdmissionAttempt:
        """Charge a trusted storage operation against this same resource ledger.

        CPU/RAM delivery headroom does not grant or prevent a storage-only
        operation. Fresh authority and its full storage quantities still apply.
        """
        validate_clock(now)
        footprint = ResourceAmounts(storage_slots=storage_slots)
        if storage_slots == 0:
            raise ValueError("storage_slots must be an integer >= 1")
        with self._guard:
            if not self._accepting:
                return StorageAdmissionAttempt(None, "stopping")
            decision = self._current_decision(now)
            if decision.reason not in {"capacity_available", "resource_exhausted"}:
                return StorageAdmissionAttempt(None, decision.reason)
            if (
                self._committed.storage_slots + storage_slots
                > decision.resource_capacity.storage_slots
            ):
                return StorageAdmissionAttempt(None, "resource_exhausted")
            key = next(self._sequence)
            lease = StorageReservation(self, key, footprint)
            self._committed = self._committed + footprint
            self._leases[key] = lease
            self._pending[key] = lease
            self._storage_only_inflight += 1
            return StorageAdmissionAttempt(lease, "capacity_available")

    def _release_command_locked(
        self, command: CommandReservation, *, finish_started: bool = False
    ) -> bool:
        if self._commands.get(command._key) is not command:
            return False
        if command._state == "reserved":
            if finish_started or self._pending_commands.get(command._key) is not command:
                return False
            self._pending_commands.pop(command._key)
        elif command._state == "started":
            if not finish_started:
                return False
            self._command_started -= 1
        else:
            return False
        self._committed = self._committed - command._footprint
        self._commands.pop(command._key)
        object.__setattr__(command, "_state", "released")
        self._command_condition.notify_all()
        return True

    def _release_locked(self, lease: DeliveryReservation) -> bool:
        if self._leases.get(lease._key) is not lease:
            return False
        if lease._physical_effects:
            return False
        if lease._storage_connections or lease._storage_stage is not None:
            raise RuntimeError("active storage stage must finish physically first")
        if lease._retirement_hold is not None:
            if not lease._retirement_requested:
                raise RuntimeError("held retirement requires physical body completion")
            if not lease._retirement_ready:
                self._retirement_queue.put(lease._retirement_hold)
            lease._retirement_ready = True
            lease._retirement_hold._signal.set()  # Notification only; no external callback.
            return False
        completion = (
            lease._completion_hold if isinstance(lease, _StorageControlReservation) else None
        )
        if completion is not None and lease not in completion._cleanup_leases:
            raise RuntimeError("control completion ownership is unavailable")
        if lease._retirement_requested:
            self._storage_retirement_pending -= 1
        if lease._state == "started":
            self._started -= 1
            if isinstance(lease, StorageReservation):
                self._storage_only_started -= 1
        if isinstance(lease, StorageReservation):
            self._storage_only_inflight -= 1
        if self._control_lease is lease:
            self._control_lease = None
        self._committed = self._committed - lease._remaining
        lease._remaining = ResourceAmounts()
        self._leases.pop(lease._key)
        self._pending.pop(lease._key, None)
        self._command_condition.notify_all()
        if lease._receiver is not None and self._receivers.get(lease._receiver) == lease._key:
            self._receivers.pop(lease._receiver)
        lease._state = "released"
        if completion is not None:
            completion._cleanup_leases.remove(lease)
        return True

    def retry_physical_closes(self) -> PhysicalCloseResult:
        """Observe only this owner's positively confirmed late native closes.

        Built-in observers are nonblocking native-handle checks. Neither callbacks
        nor native I/O run under the admission guard; no new effect or SQL runs.
        Unknown starts/trees have never entered this exact-owner registry.
        """
        self._guard  # Reject a fork before touching an inherited retry lock.
        if not self._physical_retry_guard.acquire(blocking=False):
            with self._guard:
                return PhysicalCloseResult(len(self._physical_closes), busy=True)
        try:
            with self._guard:
                tokens = tuple(self._physical_closes)
            # Only finish removes a registered token, and it always discards this
            # set under the same owner guard; a lease cannot bypass that retirement.
            for token in tokens:
                try:
                    token.finish()
                except Exception:
                    pass  # Preserve the exact token and charge on observation failure.
            with self._guard:
                return PhysicalCloseResult(len(self._physical_closes))
        finally:
            self._physical_retry_guard.release()

    def wait_retirement_ready(self, *, timeout_s: float) -> DeliveryRetirementHold | None:
        validate_clock(timeout_s)
        self._guard  # PID validation before touching inherited queue locks.
        try:
            return self._retirement_queue.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def wake_retirement_worker(self) -> None:
        self._guard
        self._retirement_queue.put(None)

    def stop_accepting(self) -> None:
        with self._guard:
            self._accepting = False
            for lease in tuple(self._pending.values()):
                self._release_locked(lease)
            for command in tuple(self._pending_commands.values()):
                self._release_command_locked(command)

    def _wait_for_command_drain(
        self,
        startup: CommandReservation,
        worker: CommandThread[None],
        control_lease: DeliveryReservation | None,
    ) -> bool:
        """Wait on this actor thread until only its startup charge remains."""
        if (
            type(startup) is not CommandReservation
            or type(worker) is not CommandThread
            or startup._owner is not self
            or startup._thread_worker is not worker
            or startup._start_pid != os.getpid()
            or startup._start_thread is not threading.current_thread()
            or startup._start_task is not _current_async_task()
            or worker._thread is not threading.current_thread()
            or (
                control_lease is not None
                and (
                    not isinstance(control_lease, DeliveryReservation)
                    or control_lease._owner is not self
                )
            )
        ):
            return False
        with self._command_condition:
            while self._commands.get(startup._key) is startup:
                exact_control_lease = (
                    control_lease is not None
                    and self._control_lease is control_lease
                    and self._leases.get(control_lease._key) is control_lease
                )
                external_leases = len(self._leases) - int(exact_control_lease)
                if not external_leases and len(self._commands) == 1:
                    break
                self._command_condition.wait()
            return (
                self._commands.get(startup._key) is startup
                and len(self._commands) == 1
                and (
                    not self._leases
                    or (
                        control_lease is not None
                        and self._control_lease is control_lease
                        and self._leases.get(control_lease._key) is control_lease
                        and len(self._leases) == 1
                    )
                )
            )

    def start_accepting(self) -> None:
        with self._guard:
            if self._leases or self._commands:
                raise RuntimeError("active delivery reservations must drain before restart")
            self._generation += 1
            self._observation = None
            self._observation_ceiling = None
            self._decision = AdmissionDecision(0, "unknown_capacity")
            self._accepting = True

    def snapshot(self, *, now: float) -> AdmissionSnapshot:
        validate_clock(now)
        with self._guard:
            return self._snapshot_basis_locked(now)[0]

    def _snapshot_basis_locked(self, now: float) -> tuple[AdmissionSnapshot, AdmissionDecision]:
        """Linearize the external close flag once with the guarded ledger basis."""
        uncertain = storage_close_uncertainty()
        decision = self._current_decision(now, close_uncertain=uncertain)
        limit = (
            self._current_decision(now, charged=False, close_uncertain=uncertain).available_slots
            if self._accepting
            else 0
        )
        inflight = len(self._leases) + len(self._commands)
        started = self._started + self._command_started
        reason: AdmissionReason = decision.reason
        if not self._accepting:
            reason = "stopping"
        debt = decision.resource_debt
        if not self._accepting:
            debt = self._committed
        age = now - self._observation.observed_at if self._observation else None
        snapshot = AdmissionSnapshot(
            accepting=self._accepting,
            generation=self._generation,
            inflight=inflight,
            reserved=inflight - started,
            started=started,
            limit=limit,
            available_slots=decision.available_slots if self._accepting else 0,
            debt=max(
                (debt.memory_bytes + self._policy.memory_per_delivery_bytes - 1)
                // self._policy.memory_per_delivery_bytes,
                (debt.cpu_millicores + self._policy.cpu_per_delivery_millicores - 1)
                // self._policy.cpu_per_delivery_millicores,
                debt.storage_slots,
            ),
            reason=reason,
            sample_age_s=age if age is not None and age >= 0 else None,
            committed=self._committed,
            resource_debt=debt,
            storage_connection_loans=self._storage_connection_loans,
            storage_completion_credits=(
                self._committed.storage_slots
                - self._additional_storage_connections
                + int(self._control_lease is not None)
            ),
            storage_retirement_pending=self._storage_retirement_pending,
            storage_only_inflight=self._storage_only_inflight,
            storage_only_started=self._storage_only_started,
            control_storage_slots=int(self._control_permit is not None),
            control_inflight=int(self._control_lease is not None),
            control_connection_loans=(
                len(self._control_lease._storage_connections) if self._control_lease else 0
            ),
            command_inflight=len(self._commands),
            command_started=self._command_started,
        )
        return snapshot, decision

    def diagnostic(self, *, now: float) -> AdmissionLedgerDiagnostic:
        """Copy immutable quantities and O(1) counters under the same guard."""
        validate_clock(now)
        with self._guard:
            snapshot, decision = self._snapshot_basis_locked(now)
            return AdmissionLedgerDiagnostic(
                snapshot=snapshot,
                policy=self._policy,
                budget=self._budget,
                observation=self._observation,
                ceiling=self._observation_ceiling,
                decision=decision,
            )
