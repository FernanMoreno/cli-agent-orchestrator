"""Owner-funded asynchronous composition for one private Root deployment."""

from __future__ import annotations

import asyncio
import math
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, TypeVar, cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget, connect
from cli_agent_orchestrator.clients.storage_admission import storage_close_uncertainty
from cli_agent_orchestrator.models.admission_control import MonitorDiagnosticReason
from cli_agent_orchestrator.models.admission_domains import canonical_id
from cli_agent_orchestrator.models.root_deployment_control import (
    RootDeploymentPhase,
    RootDeploymentStatus,
)
from cli_agent_orchestrator.models.sql_root_genesis import RootSqlOpeningIntent
from cli_agent_orchestrator.models.sql_root_subject import RootSqlSubject
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_policy import ResourceAmounts, validate_clock
from cli_agent_orchestrator.services.delivery_admission import (
    CommandReservation,
    CommandThread,
    CommandThreadStartUncertain,
    DeliveryAdmission,
    DeliveryReservation,
    StorageControlPermit,
)
from cli_agent_orchestrator.services.root_control_deployment import (
    RootControlDeployment,
    RootControlDeploymentUnavailable,
)

_REASONS: set[str] = {
    "capacity_available",
    "resource_exhausted",
    "unknown_capacity",
    "stale_observation",
    "future_observation",
    "receiver_busy",
    "stopping",
    "recovering",
    "policy_unavailable",
    "authority_unavailable",
    "generation_changed",
    "probe_busy",
    "monitor_failed",
    "duplicate_observation",
    "authority_changing",
    "authority_uncertain",
}


_FutureValue = TypeVar("_FutureValue")


class RootDeploymentRuntimeUnavailable(RuntimeError):
    """Fixed, non-secret denial retaining any uncertain native owner."""

    def __init__(self) -> None:
        super().__init__("Root deployment runtime is unavailable")


class RootDeploymentOperatorMismatch(PermissionError):
    """The runtime requires a verified administrator with its pinned identity."""

    def __init__(self) -> None:
        super().__init__("Root deployment requires its authorized administrator")


@dataclass(frozen=True, slots=True)
class RootDeploymentConfig:
    """Trusted process composition; no field is accepted from a control request."""

    bootstrap: ResourceBootstrap = field(repr=False)
    owner: DeliveryAdmission = field(repr=False)
    control: StorageControlPermit = field(repr=False)
    target: DatabaseTarget = field(repr=False)
    argv: Sequence[str] = field(repr=False)
    env: Mapping[str, str] = field(repr=False)
    key_supplier: Callable[[], tuple[str, Ed25519PrivateKey]] = field(repr=False)
    worker_footprint: ResourceAmounts = field(repr=False)
    child_footprint: ResourceAmounts = field(repr=False)
    command_footprint: ResourceAmounts = field(repr=False)
    startup_footprint: ResourceAmounts = field(repr=False)
    refresh_footprint: ResourceAmounts = field(repr=False)
    clock: Callable[[], float] = field(repr=False)
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if (
            type(self.bootstrap) is not ResourceBootstrap
            or type(self.owner) is not DeliveryAdmission
            or type(self.control) is not StorageControlPermit
            or not self.control.belongs_to(self.owner)
            or type(self.target) is not DatabaseTarget
            or self.target.scope != "main"
            or isinstance(self.argv, (str, bytes))
            or not isinstance(self.argv, Sequence)
            or isinstance(self.env, (str, bytes))
            or not isinstance(self.env, Mapping)
            or not callable(self.key_supplier)
            or not callable(self.clock)
        ):
            raise ValueError("Root deployment configuration is invalid")
        arguments = tuple(self.argv)
        environment = dict(self.env)
        if (
            not arguments
            or any(type(value) is not str or not value or "\0" in value for value in arguments)
            or any(
                type(key) is not str
                or not key
                or "=" in key
                or "\0" in key
                or type(value) is not str
                or "\0" in value
                for key, value in environment.items()
            )
        ):
            raise ValueError("Root deployment configuration is invalid")
        timeout = self.timeout_s
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout > 30
        ):
            raise ValueError("Root deployment configuration is invalid")
        for footprint in (
            self.worker_footprint,
            self.child_footprint,
            self.command_footprint,
            self.startup_footprint,
            self.refresh_footprint,
        ):
            if (
                type(footprint) is not ResourceAmounts
                or footprint.memory_bytes <= 0
                or footprint.cpu_millicores <= 0
                or footprint.storage_slots != 0
            ):
                raise ValueError("Root deployment configuration is invalid")
        object.__setattr__(self, "argv", arguments)
        object.__setattr__(self, "env", MappingProxyType(environment))
        object.__setattr__(self, "timeout_s", float(timeout))


@dataclass(slots=True)
class _Command:
    kind: Literal["launch", "activate", "refresh", "stop"]
    reservation: CommandReservation
    values: tuple[str, ...]
    future: Future[RootDeploymentStatus]


class RootDeploymentRuntime:
    """One funded worker, original connection and owner-bound deployment handle."""

    __slots__ = (
        "_config",
        "_pid",
        "_guard",
        "_condition",
        "_admission_gate",
        "_phase",
        "_reason",
        "_operator_id",
        "_operator_principal",
        "_launch_attempted",
        "_installation_claimed",
        "_busy",
        "_closing",
        "_pending",
        "_active_command",
        "_shutdown_future",
        "_shutdown_requested",
        "_shutdown_active",
        "_shutdown_failed",
        "_shutdown_timeout_s",
        "_worker",
        "_worker_reservation",
        "_worker_start_gate",
        "_worker_go_gate",
        "_worker_abort_before_connect",
        "_worker_exit_requested",
        "_worker_scope_entered",
        "_worker_scope_failed",
        "_worker_retired",
        "_cleanup_proven",
        "_unrefunded_terminal",
        "_control_lease",
        "_control_lease_retired",
        "_connection",
        "_connection_open_failed",
        "_connection_closed",
        "_deployment",
        "_child_closed",
        "_subject",
        "_journal_id",
        "_intent_id",
        "_session_id",
    )

    def __init__(self, config: RootDeploymentConfig) -> None:
        if type(config) is not RootDeploymentConfig:
            raise TypeError("Root deployment requires trusted configuration")
        self._config = config
        self._pid = os.getpid()
        self._guard = threading.Lock()
        self._condition = threading.Condition(self._guard)
        self._admission_gate = threading.Lock()
        self._phase: RootDeploymentPhase = "idle"
        self._reason: MonitorDiagnosticReason | None = None
        self._operator_id: str | None = None
        self._operator_principal: object | None = None
        self._launch_attempted = False
        self._installation_claimed = False
        self._busy = False
        self._closing = False
        self._pending: _Command | None = None
        self._active_command: _Command | None = None
        # One bounded shutdown completion is allocated before worker start.
        self._shutdown_future: Future[RootDeploymentStatus] = Future()
        self._shutdown_requested = False
        self._shutdown_active = False
        self._shutdown_failed = False
        self._shutdown_timeout_s = self._config.timeout_s
        self._worker: CommandThread[None] | None = None
        self._worker_reservation: CommandReservation | None = None
        self._worker_start_gate = threading.Event()
        self._worker_go_gate = threading.Event()
        self._worker_abort_before_connect = False
        self._worker_exit_requested = False
        self._worker_scope_entered = False
        self._worker_scope_failed = False
        self._worker_retired = False
        self._cleanup_proven = False
        self._unrefunded_terminal = False
        self._control_lease: DeliveryReservation | None = None
        self._control_lease_retired = True
        self._connection: Connection | None = None
        self._connection_open_failed = False
        self._connection_closed = True
        self._deployment: RootControlDeployment | None = None
        self._child_closed = True
        self._subject: RootSqlSubject | None = None
        self._journal_id: str | None = None
        self._intent_id: str | None = None
        self._session_id: str | None = None

    def __copy__(self):
        raise TypeError("Root deployment runtime ownership cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("Root deployment runtime ownership cannot be copied")

    def __reduce_ex__(self, protocol):
        raise TypeError("Root deployment runtime ownership cannot be serialized")

    @property
    def installable(self) -> bool:
        if self._pid != os.getpid():
            return False
        with self._guard:
            return bool(
                not self._installation_claimed
                and not self._launch_attempted
                and not self._closing
                and self._phase == "idle"
                and self._worker is None
            )

    def claim_installation(self) -> None:
        """Atomically bind this pristine runtime to one trusted app instance."""
        self._check_pid()
        with self._admission_gate, self._condition:
            if (
                self._installation_claimed
                or self._launch_attempted
                or self._phase != "idle"
                or self._worker is not None
                or self._closing
            ):
                raise RootDeploymentRuntimeUnavailable()
            self._installation_claimed = True

    def status(self, *, principal: object) -> RootDeploymentStatus:
        self._authorize(principal)
        with self._guard:
            return self._status_locked()

    async def launch(self, *, principal: object) -> RootDeploymentStatus:
        actor = self._authorize(principal)
        with self._admission_gate:
            command = self._start_launch(actor, principal)
        return await self._await_job(command.future)

    def _start_launch(self, actor: str, principal: object) -> _Command:
        with self._condition:
            if self._operator_id is not None and self._operator_id != actor:
                raise RootDeploymentOperatorMismatch()
            if self._launch_attempted or self._phase != "idle" or self._busy or self._closing:
                raise RootDeploymentRuntimeUnavailable()
            self._operator_id = actor
            self._operator_principal = principal
            self._launch_attempted = True
            self._busy = True
            self._phase = "launching"
            self._reason = None

        worker_reservation: CommandReservation | None = None
        launch_reservation: CommandReservation | None = None
        try:
            worker_reservation = self._reserve(self._config.worker_footprint)
            if worker_reservation is None:
                raise RootDeploymentRuntimeUnavailable()
            launch_reservation = self._reserve(self._config.command_footprint)
            if launch_reservation is None:
                worker_reservation.cancel_before_start()
                raise RootDeploymentRuntimeUnavailable()
            with self._guard:
                self._worker_reservation = worker_reservation
            try:
                worker = worker_reservation._launch_thread(
                    self._worker_main, clock=self._config.clock
                )
            except CommandThreadStartUncertain as error:
                worker = error.worker
                with self._condition:
                    self._worker = worker
                    self._worker_abort_before_connect = True
                    self._phase = "unavailable"
                    self._reason = "authority_unavailable"
                    self._busy = False
                    self._worker_start_gate.set()
                    self._worker_go_gate.set()
                    self._condition.notify_all()
                launch_reservation.cancel_before_start()
                raise RootDeploymentRuntimeUnavailable() from None
            except BaseException:
                launch_reservation.cancel_before_start()
                worker_reservation.cancel_before_start()
                with self._condition:
                    self._worker_reservation = None
                    self._phase = "unavailable"
                    self._reason = "authority_unavailable"
                    self._busy = False
                    self._condition.notify_all()
                raise RootDeploymentRuntimeUnavailable() from None
            if worker is None:
                launch_reservation.cancel_before_start()
                worker_reservation.cancel_before_start()
                with self._condition:
                    self._worker_reservation = None
                    self._phase = "unavailable"
                    self._reason = "authority_unavailable"
                    self._busy = False
                raise RootDeploymentRuntimeUnavailable()
            with self._condition:
                self._worker = worker
                self._worker_start_gate.set()
                self._condition.notify_all()
                pending_empty = self._pending is None
            if not pending_empty:
                launch_reservation.cancel_before_start()
                with self._condition:
                    self._worker_exit_requested = True
                    self._phase = "unavailable"
                    self._reason = "authority_uncertain"
                    self._busy = False
                    self._worker_go_gate.set()
                    self._condition.notify_all()
                raise RootDeploymentRuntimeUnavailable()
            try:
                dispatched = launch_reservation._dispatch_to_thread(
                    worker, now=self._config.clock()
                )
            except BaseException:
                dispatched = False
            if not dispatched:
                launch_reservation.cancel_before_start()
                with self._condition:
                    self._worker_exit_requested = True
                    self._phase = "unavailable"
                    self._reason = "authority_unavailable"
                    self._busy = False
                    self._worker_go_gate.set()
                    self._condition.notify_all()
                raise RootDeploymentRuntimeUnavailable()
            command = _Command("launch", launch_reservation, (), Future())
            with self._condition:
                self._pending = command
                self._condition.notify_all()
                self._worker_go_gate.set()
            return command
        except RootDeploymentRuntimeUnavailable:
            with self._condition:
                if self._busy and self._pending is None and self._active_command is None:
                    self._busy = False
                    if self._phase == "launching":
                        self._phase = "unavailable"
                        self._reason = "authority_unavailable"
                    self._condition.notify_all()
            raise
        except BaseException:
            with self._condition:
                if self._busy and self._pending is None and self._active_command is None:
                    self._busy = False
                    if self._phase == "launching":
                        self._phase = "unavailable"
                        self._reason = "authority_unavailable"
                    self._condition.notify_all()
            raise RootDeploymentRuntimeUnavailable() from None

    async def activate(
        self,
        *,
        principal: object,
        journal_id: str,
        intent_id: str,
        session_id: str,
    ) -> RootDeploymentStatus:
        self._authorize(principal)
        identifiers = self._canonical_identifiers(journal_id, intent_id, session_id)
        command = self._begin_command(
            "activate", allowed={"awaiting_authority"}, values=identifiers
        )
        return await self._await_job(command.future)

    async def refresh(self, *, principal: object) -> RootDeploymentStatus:
        self._authorize(principal)
        command = self._begin_command("refresh", allowed={"ready"})
        return await self._await_job(command.future)

    async def stop(self, *, principal: object) -> RootDeploymentStatus:
        self._authorize(principal)
        command = self._begin_command("stop", allowed={"ready"})
        return await self._await_job(command.future)

    async def aclose(self, timeout: float = 30) -> None:
        self._check_pid()
        if not self._valid_timeout(timeout, allow_zero=True, maximum=30):
            raise RootDeploymentRuntimeUnavailable()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        with self._admission_gate, self._condition:
            if self._worker_retired:
                self._phase = "closed"
                return
            # Latch before waiting. This bounded close flag is covered by the
            # already charged worker footprint; shutdown asks for no new grant.
            self._closing = True
            worker = self._worker
            if worker is None:
                reservation = self._worker_reservation
                if reservation is not None and reservation.state == "started":
                    raise RootDeploymentRuntimeUnavailable()
                if reservation is not None and reservation.state == "reserved":
                    reservation.cancel_before_start()
                self._phase = "closed"
                self._reason = None
                return
            active_future: Future[RootDeploymentStatus] | None = None
            if self._busy:
                if self._active_command is not None:
                    active_future = self._active_command.future
                elif self._pending is not None:
                    active_future = self._pending.future
                else:
                    raise RootDeploymentRuntimeUnavailable()
            self._shutdown_timeout_s = min(self._config.timeout_s, float(timeout))
            if (
                not self._unrefunded_terminal
                and not worker._returned.is_set()
                and worker._thread.is_alive()
            ):
                if not self._shutdown_active:
                    self._shutdown_failed = False
                    self._shutdown_requested = True
                self._condition.notify_all()

        if active_future is not None:
            try:
                await self._await_job_until(active_future, deadline)
            except RootDeploymentRuntimeUnavailable:
                with self._guard:
                    if self._busy:
                        raise
            with self._guard:
                if self._busy:
                    raise RootDeploymentRuntimeUnavailable()

        if self._unrefunded_terminal:
            await self._await_worker_return(worker, deadline)
            self._refresh_control_lease_retired()
            raise RootDeploymentRuntimeUnavailable()

        if (
            not self._is_exit_requested()
            and not worker._returned.is_set()
            and worker._thread.is_alive()
        ):
            try:
                await self._await_shutdown(deadline)
            except RootDeploymentRuntimeUnavailable:
                if self._unrefunded_terminal:
                    await self._await_worker_return(worker, deadline)
                    self._refresh_control_lease_retired()
                raise

        if not worker._returned.is_set() and not worker._thread.is_alive():
            # An attempted thread start without return evidence is uncertain.
            raise RootDeploymentRuntimeUnavailable()
        await self._await_worker_return(worker, deadline)
        self._refresh_control_lease_retired()
        with self._guard:
            safe_to_join = (
                self._connection_closed
                and self._child_closed
                and self._control_lease_retired
                and self._active_command is None
            )
        safe_to_join = safe_to_join and not storage_close_uncertainty()
        if self._unrefunded_terminal:
            raise RootDeploymentRuntimeUnavailable()
        if not safe_to_join or not worker.join(timeout=0):
            raise RootDeploymentRuntimeUnavailable()
        with self._condition:
            self._worker_retired = True
            self._phase = "closed"
            self._reason = None
            self._busy = False
            self._condition.notify_all()

    def _authorize(self, principal: object) -> str:
        # The current verified administrator is checked before clocks,
        # reservations, target resolution, SQL or process operations.
        try:
            actor = self._config.bootstrap._administrator(principal)
        except PermissionError:
            raise RootDeploymentOperatorMismatch() from None
        self._check_pid()
        with self._guard:
            if self._operator_id is not None and actor != self._operator_id:
                raise RootDeploymentOperatorMismatch()
        return actor

    def _check_pid(self) -> None:
        if self._pid != os.getpid():
            raise RootDeploymentRuntimeUnavailable()

    def _status_locked(self) -> RootDeploymentStatus:
        return RootDeploymentStatus(
            phase=self._phase,
            subject=self._subject,
            journal_id=self._journal_id,
            intent_id=self._intent_id,
            session_id=self._session_id,
            reason=self._reason,
        )

    def _reserve(self, footprint: ResourceAmounts) -> CommandReservation | None:
        try:
            now = self._config.clock()
            validate_clock(now)
            attempt = self._config.owner.try_reserve_command(footprint=footprint, now=now)
            return attempt.reservation
        except BaseException:
            return None

    def _begin_command(
        self,
        kind: Literal["activate", "refresh", "stop"],
        *,
        allowed: set[str],
        values: tuple[str, ...] = (),
    ) -> _Command:
        self._check_pid()
        with self._admission_gate:
            with self._condition:
                if (
                    self._closing
                    or self._busy
                    or self._phase not in allowed
                    or self._worker is None
                ):
                    raise RootDeploymentRuntimeUnavailable()
                self._busy = True
            return self._dispatch_internal(kind, values=values)

    def _dispatch_internal(
        self,
        kind: Literal["activate", "refresh", "stop"],
        *,
        values: tuple[str, ...] = (),
    ) -> _Command:
        reservation = self._reserve(self._config.command_footprint)
        if reservation is None:
            with self._condition:
                self._busy = False
                self._condition.notify_all()
            raise RootDeploymentRuntimeUnavailable()
        with self._guard:
            worker = self._worker
            pending_empty = self._pending is None
        if not pending_empty:
            reservation.cancel_before_start()
            with self._condition:
                self._busy = False
                self._condition.notify_all()
            raise RootDeploymentRuntimeUnavailable()
        if worker is None:
            reservation.cancel_before_start()
            with self._condition:
                self._busy = False
            raise RootDeploymentRuntimeUnavailable()
        try:
            dispatched = reservation._dispatch_to_thread(worker, now=self._config.clock())
        except BaseException:
            dispatched = False
        if not dispatched:
            reservation.cancel_before_start()
            with self._condition:
                self._busy = False
                self._condition.notify_all()
            raise RootDeploymentRuntimeUnavailable()
        command = _Command(kind, reservation, values, Future())
        with self._condition:
            self._pending = command
            self._condition.notify_all()
        return command

    async def _await_job(self, future: Future[RootDeploymentStatus]) -> RootDeploymentStatus:
        wrapped = asyncio.wrap_future(future)
        self._consume_late_exception(wrapped)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            raise
        except RootDeploymentRuntimeUnavailable:
            raise
        except BaseException:
            raise RootDeploymentRuntimeUnavailable() from None

    async def _await_job_until(
        self, future: Future[RootDeploymentStatus], deadline: float
    ) -> RootDeploymentStatus:
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        wrapped = asyncio.wrap_future(future)
        self._consume_late_exception(wrapped)
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout=remaining)
        except asyncio.CancelledError:
            raise
        except RootDeploymentRuntimeUnavailable:
            raise
        except BaseException:
            raise RootDeploymentRuntimeUnavailable() from None

    async def _await_shutdown(self, deadline: float) -> None:
        loop = asyncio.get_running_loop()
        while not self._shutdown_future.done():
            with self._guard:
                failed = self._shutdown_failed and not self._shutdown_active
            if failed:
                raise RootDeploymentRuntimeUnavailable()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RootDeploymentRuntimeUnavailable()
            await asyncio.sleep(min(0.01, remaining))
        await self._await_job(self._shutdown_future)

    async def _await_worker_return(self, worker: CommandThread[None], deadline: float) -> None:
        loop = asyncio.get_running_loop()
        if not worker._returned.is_set():
            remaining = max(0.0, deadline - loop.time())
            wrapped = asyncio.wrap_future(worker._future)
            self._consume_late_exception(wrapped)
            try:
                await asyncio.wait_for(asyncio.shield(wrapped), timeout=remaining)
            except asyncio.CancelledError:
                raise
            except BaseException:
                if not worker._returned.is_set():
                    raise RootDeploymentRuntimeUnavailable() from None
        while worker._thread.is_alive() or not worker._returned.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RootDeploymentRuntimeUnavailable()
            await asyncio.sleep(min(0.01, remaining))

    @staticmethod
    def _consume_late_exception(future: asyncio.Future[_FutureValue]) -> None:
        def consume(completed: asyncio.Future[_FutureValue]) -> None:
            if not completed.cancelled():
                completed.exception()

        future.add_done_callback(consume)

    def _worker_main(self) -> None:
        self._worker_start_gate.wait()
        with self._condition:
            if self._worker_abort_before_connect:
                self._worker_exit_requested = True
                self._connection_closed = True
                self._child_closed = True
                self._control_lease_retired = True
                self._condition.notify_all()
                if self._closing and not self._shutdown_future.done():
                    self._shutdown_future.set_result(self._status_locked())
                return
        self._worker_go_gate.wait()
        if self._is_exit_requested() and self._connection_closed and self._child_closed:
            return

        config = self._config
        try:
            with config.control.operation(clock=config.clock):
                with config.owner._guard:
                    lease = config.owner._control_lease
                    lease_valid = bool(
                        lease is not None
                        and config.owner._leases.get(lease._key) is lease
                        and lease._state == "started"
                    )
                with self._condition:
                    self._worker_scope_entered = True
                    self._control_lease = lease
                    self._control_lease_retired = False
                    self._connection_closed = False
                    self._connection_open_failed = False
                if not lease_valid:
                    raise RuntimeError("control lease is unavailable")
                try:
                    selected_target = config.target.migration_target()
                    connection = connect(selected_target)
                    if type(connection) is not Connection:
                        raise RuntimeError("original connection is invalid")
                    # Retain the exact native handle before any validation that
                    # can fail, so trusted shutdown can close this same object.
                    with self._condition:
                        self._connection = connection
                        self._connection_closed = False
                    if connection.target.identity != selected_target.identity:
                        raise RuntimeError("original connection target changed")
                    connection._check_funded_worker()
                except BaseException:
                    with self._condition:
                        self._connection_open_failed = True
                        if self._connection is None:
                            self._connection_closed = not storage_close_uncertainty()
                        else:
                            self._connection_closed = False
                        self._reason = "authority_unavailable"
                        self._phase = "unavailable"
                        self._condition.notify_all()

                while True:
                    command, do_shutdown, timeout_s = self._take_worker_action()
                    if command is not None:
                        self._run_command(command)
                    elif do_shutdown:
                        self._perform_shutdown(timeout_s)
                    elif self._can_worker_exit_without_command():
                        break
                    else:
                        with self._condition:
                            self._condition.wait(timeout=0.1)
                    if self._can_worker_exit_without_command():
                        break
            self._refresh_control_lease_retired()
            if not self._control_lease_retired:
                self._recovery_loop()
        except BaseException:
            with self._condition:
                self._worker_scope_failed = True
                self._phase = "unavailable"
                self._reason = "authority_uncertain"
                self._condition.notify_all()
                entered = self._worker_scope_entered
                pending = self._pending
                self._pending = None
                if pending is not None:
                    self._active_command = pending
            if pending is not None:
                self._reject_command_on_worker(pending)
            if not entered:
                with self._condition:
                    self._connection_closed = True
                    self._child_closed = True
                    self._control_lease_retired = True
                    self._worker_exit_requested = True
                    self._busy = False
                    self._condition.notify_all()
                    if self._closing and not self._shutdown_future.done():
                        self._shutdown_future.set_result(self._status_locked())
                return
            # The original operation scope is no longer valid. Recovery only
            # permits physical shutdown and waits for exact close-lease proof.
            self._recovery_loop()

    def _take_worker_action(
        self,
    ) -> tuple[_Command | None, bool, float]:
        with self._condition:
            while True:
                if self._pending is not None:
                    command = self._pending
                    self._pending = None
                    self._active_command = command
                    return command, False, 0.0
                if (
                    self._closing
                    and self._shutdown_requested
                    and not self._shutdown_active
                    and self._active_command is None
                ):
                    self._shutdown_requested = False
                    self._shutdown_active = True
                    self._shutdown_failed = False
                    return None, True, self._shutdown_timeout_s
                if self._worker_exit_requested:
                    return None, False, 0.0
                self._condition.wait()

    def _perform_shutdown(self, timeout_s: float) -> None:
        error = False
        try:
            self._run_shutdown(timeout_s)
        except BaseException:
            error = True
        with self._condition:
            self._shutdown_active = False
            if error:
                self._phase = "unavailable"
                self._reason = "authority_uncertain"
                self._shutdown_failed = True
            else:
                self._shutdown_failed = False
                status = self._status_locked()
                if not self._shutdown_future.done():
                    self._shutdown_future.set_result(status)
            self._condition.notify_all()

    def _recovery_loop(self) -> None:
        while True:
            self._refresh_control_lease_retired()
            with self._condition:
                if (
                    self._worker_exit_requested
                    and self._connection_closed
                    and self._child_closed
                    and self._control_lease_retired
                    and self._active_command is None
                ):
                    return
            command, do_shutdown, timeout_s = self._take_worker_action()
            if command is not None:
                self._reject_command_on_worker(command)
            elif do_shutdown:
                self._perform_shutdown(timeout_s)
            else:
                with self._condition:
                    self._condition.wait(timeout=0.1)

    def _reject_command_on_worker(self, command: _Command) -> None:
        # Ledger retirement and the private reply are one observable step.
        with self._config.owner._guard:
            try:
                finished = command.reservation.finish()
            except BaseException:
                finished = False
            with self._condition:
                self._phase = "unavailable"
                self._reason = "authority_uncertain"
                if finished is True:
                    self._active_command = None
                    self._busy = False
                else:
                    self._active_command = command
                    self._busy = True
                self._condition.notify_all()
            command.future.set_exception(RootDeploymentRuntimeUnavailable())

    def _take_command(self) -> _Command | None:
        with self._condition:
            while self._pending is None and not self._worker_exit_requested:
                self._condition.wait()
            if self._pending is None:
                return None
            command = self._pending
            self._pending = None
            self._active_command = command
            return command

    def _run_command(self, command: _Command) -> None:
        error = False
        try:
            worker = self._worker
            if worker is None or not command.reservation._check_dispatched(
                worker, now=self._config.clock()
            ):
                raise RootDeploymentRuntimeUnavailable()
            if (
                self._worker_scope_failed
                or self._connection_open_failed
                or self._connection is None
            ):
                raise RootDeploymentRuntimeUnavailable()
            if command.kind == "launch":
                self._run_launch()
            elif command.kind == "activate":
                self._run_activate(command)
            elif command.kind == "refresh":
                self._run_refresh()
            elif command.kind == "stop":
                self._run_stop()
            else:
                raise RootDeploymentRuntimeUnavailable()
        except RootDeploymentRuntimeUnavailable:
            error = True
            with self._condition:
                if self._reason is None:
                    self._reason = "authority_unavailable"
                if self._phase not in {"closed", "stopping"}:
                    self._phase = "unavailable"
        except BaseException:
            error = True
            with self._condition:
                self._phase = "unavailable"
                self._reason = "authority_uncertain"

        # Snapshot/admission must not see a refunded command whose reply is
        # still pending. The private Future only schedules the shielded waiter.
        with self._config.owner._guard:
            try:
                finished = command.reservation.finish()
            except BaseException:
                finished = False
            if finished is not True:
                # Keep the exact payload and active slot. A failed finish is not a
                # refund, so no later command or worker join may pass this point.
                with self._condition:
                    self._phase = "unavailable"
                    self._reason = "authority_uncertain"
                    self._active_command = command
                    self._busy = True
                    self._condition.notify_all()
                command.future.set_exception(RootDeploymentRuntimeUnavailable())
                return

            with self._condition:
                self._active_command = None
                self._busy = False
                status = self._status_locked()
                self._condition.notify_all()
            if error:
                command.future.set_exception(RootDeploymentRuntimeUnavailable())
            else:
                command.future.set_result(status)

    def _run_launch(self) -> None:
        channel = self._connection
        if channel is None:
            raise RootDeploymentRuntimeUnavailable()
        now = self._config.clock()
        validate_clock(now)
        attempt = self._config.owner.try_reserve_command(
            footprint=self._config.child_footprint, now=now
        )
        reservation = attempt.reservation
        if reservation is None:
            raise RootDeploymentRuntimeUnavailable()
        try:
            deployment = RootControlDeployment.launch(
                bootstrap=self._config.bootstrap,
                channel=channel,
                principal=self._principal(),
                reservation=reservation,
                argv=self._config.argv,
                env=self._config.env,
                clock=self._config.clock,
                binding_timeout_s=self._config.timeout_s,
            )
        except RootControlDeploymentUnavailable as error:
            if error.deployment is not None:
                self._retain_deployment(error.deployment)
            else:
                reservation.cancel_before_start()
            with self._condition:
                self._phase = "unavailable"
                self._reason = "authority_unavailable"
            raise RootDeploymentRuntimeUnavailable() from None
        except BaseException:
            reservation.cancel_before_start()
            with self._condition:
                self._phase = "unavailable"
                self._reason = "authority_uncertain"
            raise RootDeploymentRuntimeUnavailable() from None
        if not isinstance(deployment, RootControlDeployment):
            reservation.cancel_before_start()
            raise RootDeploymentRuntimeUnavailable()
        self._retain_deployment(deployment)
        if self._subject is None:
            with self._condition:
                self._phase = "unavailable"
                self._reason = "authority_uncertain"
            raise RootDeploymentRuntimeUnavailable()
        with self._condition:
            self._phase = "awaiting_authority"
            self._reason = None

    def _retain_deployment(self, deployment: RootControlDeployment) -> None:
        if not isinstance(deployment, RootControlDeployment):
            return
        # Save the exact owner-bound handle before touching any accessor. A
        # descriptor failure must not make a live child disappear from the
        # runtime's shutdown and retention path.
        with self._condition:
            if self._deployment is not deployment:
                self._deployment = deployment
                self._child_closed = False
        subject = deployment.subject
        record = deployment.record
        with self._condition:
            if type(subject) is RootSqlSubject:
                self._subject = subject
            if type(record) is RootSqlOpeningIntent:
                self._journal_id = record.journal_id
                self._intent_id = record.intent_id
                self._session_id = record.session_id

    def _run_activate(self, command: _Command) -> None:
        deployment = self._deployment
        if deployment is None:
            raise RootDeploymentRuntimeUnavailable()
        with self._condition:
            self._phase = "activating"
            self._reason = None
        journal_id, intent_id, session_id = command.values
        try:
            deployment.activate(
                journal_id=journal_id,
                intent_id=intent_id,
                session_id=session_id,
                key_supplier=self._config.key_supplier,
                startup_footprint=self._config.startup_footprint,
                refresh_footprint=self._config.refresh_footprint,
                timeout_s=self._config.timeout_s,
            )
        except BaseException:
            self._retain_deployment(deployment)
            with self._condition:
                self._phase = "unavailable"
                self._reason = (
                    "authority_uncertain"
                    if self._journal_id is not None
                    else "authority_unavailable"
                )
            raise RootDeploymentRuntimeUnavailable() from None
        self._retain_deployment(deployment)
        with self._condition:
            self._journal_id = journal_id
            self._intent_id = intent_id
            self._session_id = session_id
            self._phase = "ready"
            self._reason = None

    def _run_refresh(self) -> None:
        deployment = self._deployment
        if deployment is None:
            raise RootDeploymentRuntimeUnavailable()
        try:
            reason = deployment.refresh(timeout_s=self._config.timeout_s)
        except BaseException:
            self._retain_deployment(deployment)
            with self._condition:
                self._phase = "unavailable"
                self._reason = "monitor_failed"
            raise RootDeploymentRuntimeUnavailable() from None
        with self._condition:
            self._phase = "ready"
            self._reason = self._checked_reason(reason)

    def _run_stop(self) -> None:
        deployment = self._deployment
        if deployment is None:
            raise RootDeploymentRuntimeUnavailable()
        with self._condition:
            self._phase = "stopping"
            self._reason = None
        try:
            deployment.stop(timeout_s=self._config.timeout_s)
        except BaseException:
            self._retain_deployment(deployment)
            with self._condition:
                self._phase = "unavailable"
                self._reason = "authority_uncertain"
            raise RootDeploymentRuntimeUnavailable() from None
        self._retain_deployment(deployment)
        # Root STOP is only an acknowledgement; physical retirement is aclose.
        with self._condition:
            self._phase = "stopping"
            self._reason = None

    def _run_shutdown(self, timeout_s: float) -> None:
        deployment = self._deployment
        timeout = min(self._config.timeout_s, max(0.0, timeout_s))
        if deployment is not None and not self._child_closed:
            # Only the monotonic issuer-side flag proves no activation packet
            # could have been sent. Cached status can lag a failed launch or a
            # lost ACK; unknown/attempted activation keeps the full join wait.
            preactivation = getattr(deployment, "_activation_attempted", None) is False
            with self._condition:
                self._phase = "stopping"
                self._reason = None
            try:
                # The cached phase may lag the private child's actual state.
                # A best-effort stop is safe in every phase: Root rejects an
                # inapplicable stop without granting work or hiding join
                # evidence, while a live child may still need the request.
                deployment.stop(timeout_s=timeout)
            except BaseException:
                # Stop failure says nothing about child exit. Retain and
                # continue to exact process retirement below.
                pass
            try:
                # Always consume a positive process-join witness before any
                # cleanup poisons that path. An unactivated Root gets a
                # nonblocking join before EOF rescue.
                joined = deployment.join(0.0 if preactivation else timeout)
            except BaseException:
                joined = False
            if joined is not True:
                cleanup_proven = False
                try:
                    cleanup_proven = deployment.cleanup(timeout if preactivation else 0.0) is True
                except BaseException:
                    pass
                if cleanup_proven:
                    # Cleanup proves that the exact child exited and its
                    # native handles closed. It deliberately does not release
                    # the child reservation, so remember the charge debt and
                    # allow only positively witnessed SQL/scope closure.
                    with self._condition:
                        self._child_closed = True
                        self._cleanup_proven = True
                    try:
                        self._retain_deployment(deployment)
                    except BaseException:
                        # Descriptive metadata cannot revoke this exact
                        # physical-close witness or prevent SQL cleanup. The
                        # same handle and its unrefunded charges stay retained.
                        pass
                    # Continue below to close the original SQL connection.
                else:
                    self._retain_deployment(deployment)
                    with self._condition:
                        self._phase = "unavailable"
                        self._reason = "authority_uncertain"
                    raise RootDeploymentRuntimeUnavailable()
            else:
                with self._condition:
                    self._child_closed = True
                    self._phase = "stopping"
                    self._reason = None
        elif deployment is None:
            with self._condition:
                self._child_closed = True

        if self._connection is not None and not self._close_original_connection():
            with self._condition:
                self._phase = "unavailable"
                self._reason = "authority_uncertain"
            raise RootDeploymentRuntimeUnavailable()
        if self._connection is None and storage_close_uncertainty():
            with self._condition:
                self._phase = "unavailable"
                self._reason = "authority_uncertain"
            raise RootDeploymentRuntimeUnavailable()
        with self._condition:
            self._connection_closed = True
            self._worker_exit_requested = True
            if self._cleanup_proven:
                # This is physical closure only. Do not call CommandThread.join
                # and do not report a retired or closed runtime: both native
                # command reservations remain charged for trusted review.
                self._unrefunded_terminal = True
                self._phase = "unavailable"
                self._reason = "authority_uncertain"
            else:
                self._phase = "stopping"
                self._reason = None
            self._condition.notify_all()
        if self._unrefunded_terminal:
            raise RootDeploymentRuntimeUnavailable()

    def _close_original_connection(self) -> bool:
        connection = self._connection
        if connection is None:
            return not storage_close_uncertainty()
        try:
            connection.close()
        except BaseException:
            # Close errors remain sticky unless native and ledger evidence
            # positively prove retirement.
            pass
        try:
            closed = (
                getattr(connection.raw, "closed", None) is True
                and connection._storage_loan is None
                and connection._storage_pid == os.getpid()
                and not storage_close_uncertainty()
            )
        except BaseException:
            closed = False
        if closed:
            with self._condition:
                self._connection_closed = True
        return closed

    def _refresh_control_lease_retired(self) -> bool:
        self._check_pid()
        owner = self._config.owner
        lease = self._control_lease
        try:
            with owner._guard:
                if lease is None:
                    retired = owner._control_lease is None
                else:
                    retired = bool(
                        owner._control_lease is not lease
                        and owner._leases.get(lease._key) is not lease
                        and lease._state == "released"
                        and not lease._storage_connections
                    )
        except BaseException:
            retired = False
        retired = retired and not storage_close_uncertainty()
        with self._guard:
            self._control_lease_retired = retired
        return retired

    def _can_worker_exit_without_command(self) -> bool:
        with self._guard:
            return bool(
                self._worker_exit_requested
                and self._connection_closed
                and self._child_closed
                and self._active_command is None
            )

    def _exit_requested_needs_local_close(self) -> bool:
        with self._guard:
            return bool(
                self._worker_exit_requested
                and self._deployment is None
                and not self._connection_closed
            )

    def _is_exit_requested(self) -> bool:
        with self._guard:
            return self._worker_exit_requested

    def _principal(self) -> object:
        with self._guard:
            principal = self._operator_principal
        if principal is None:
            raise RootDeploymentRuntimeUnavailable()
        return principal

    @staticmethod
    def _canonical_identifiers(
        journal_id: str, intent_id: str, session_id: str
    ) -> tuple[str, str, str]:
        try:
            values = (canonical_id(journal_id), canonical_id(intent_id), canonical_id(session_id))
        except (TypeError, ValueError):
            raise RootDeploymentRuntimeUnavailable() from None
        if values != (journal_id, intent_id, session_id):
            raise RootDeploymentRuntimeUnavailable()
        return values

    @staticmethod
    def _checked_reason(value: object) -> MonitorDiagnosticReason | None:
        if isinstance(value, str) and value in _REASONS:
            return cast(MonitorDiagnosticReason, value)
        return None

    @staticmethod
    def _valid_timeout(value: object, *, allow_zero: bool = False, maximum: float = 30.0) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            return (
                math.isfinite(value)
                and (value >= 0 if allow_zero else value > 0)
                and value <= maximum
            )
        except OverflowError:
            return False
