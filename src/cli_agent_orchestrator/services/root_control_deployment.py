"""Two-phase owner-funded launch and activation of the private Root CLI."""

from __future__ import annotations

import asyncio
import math
import os
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import NoReturn, SupportsIndex

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.root_control_activation import (
    ROOT_CONTROL_ACTIVATION_MAX_BYTES,
    RootControlActivation,
)
from cli_agent_orchestrator.models.sql_root_genesis import RootSqlOpeningIntent
from cli_agent_orchestrator.models.sql_startup_permission import (
    sql_startup_target_identity_sha256,
)
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_policy import ResourceAmounts, validate_clock
from cli_agent_orchestrator.services.delivery_admission import (
    CommandProcess,
    CommandProcessStartUncertain,
    CommandReservation,
    DeliveryAdmission,
)
from cli_agent_orchestrator.services.root_control_activation import (
    RootControlActivationUnavailableAfterCommit,
    issue_root_control_activation,
)
from cli_agent_orchestrator.services.root_sql_control import RootSqlSubject


def _valid_timeout(value: object, *, optional: bool = False) -> bool:
    if value is None:
        return optional
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _current_async_task() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class RootControlDeploymentUnavailable(StorageAdmissionUnavailable):
    """Fixed failure that retains a safe owner after a process may have started."""

    def __init__(
        self,
        deployment: RootControlDeployment | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__()
        self.args = ("root control deployment is unavailable",)
        self.deployment = deployment
        self.cause = cause


class RootControlDeployment:
    """Owner-bound two-phase Root launch with no public supervisor or socket."""

    __slots__ = (
        "_bootstrap",
        "_channel",
        "_principal",
        "_target",
        "_reservation",
        "_process",
        "_subject",
        "_record",
        "_activation_attempted",
        "_active",
        "_operation_guard",
        "_pid",
        "_thread",
        "_task",
    )

    def __init__(
        self,
        *,
        bootstrap: ResourceBootstrap,
        channel: Connection,
        principal: object,
        target: DatabaseTarget,
        reservation: CommandReservation,
        process: CommandProcess | None,
    ) -> None:
        self._bootstrap = bootstrap
        self._channel = channel
        self._principal = principal
        self._target = target
        self._reservation = reservation
        self._process = process
        self._subject: RootSqlSubject | None = None
        self._record: RootSqlOpeningIntent | None = None
        self._activation_attempted = False
        self._active = False
        self._operation_guard = threading.Lock()
        self._pid = os.getpid()
        self._thread = threading.current_thread()
        self._task = _current_async_task()

    def __copy__(self) -> NoReturn:
        raise TypeError("root control deployment ownership cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise TypeError("root control deployment ownership cannot be copied")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("root control deployment ownership cannot be serialized")

    def _check_owner(self) -> None:
        if (
            self._pid != os.getpid()
            or self._thread is not threading.current_thread()
            or self._task is not _current_async_task()
        ):
            raise RootControlDeploymentUnavailable(self)

    @contextmanager
    def _operation(self) -> Iterator[None]:
        """Serialize owner controls and reject synchronous callback reentry."""
        self._check_owner()
        if not self._operation_guard.acquire(blocking=False):
            raise RootControlDeploymentUnavailable(self)
        try:
            yield
        finally:
            self._operation_guard.release()

    @property
    def subject(self) -> RootSqlSubject | None:
        self._check_owner()
        return self._subject

    @property
    def has_native_owner(self) -> bool:
        self._check_owner()
        process = self._process
        if process is None:
            process = self._registered_process_worker(self._reservation)
            if process is not None:
                self._process = process
        return process is not None and process.has_native_owner

    @property
    def record(self) -> RootSqlOpeningIntent | None:
        self._check_owner()
        return self._record

    @classmethod
    def launch(
        cls,
        *,
        bootstrap: ResourceBootstrap,
        channel: Connection,
        principal: object,
        reservation: CommandReservation,
        argv: Sequence[str],
        env: Mapping[str, str] | None,
        clock: Callable[[], float],
        binding_timeout_s: float = 30,
    ) -> RootControlDeployment:
        """Verify issuer authority/funding before transferring a pending charge."""
        if type(bootstrap) is not ResourceBootstrap:
            raise RootControlDeploymentUnavailable()

        # This is deliberately the first application-authority check. A
        # rejected principal cannot trigger target/configuration or process I/O.
        bootstrap._administrator(principal)

        if (
            type(channel) is not Connection
            or type(reservation) is not CommandReservation
            or not callable(clock)
            or not _valid_timeout(binding_timeout_s)
            or isinstance(argv, (str, bytes))
            or not isinstance(argv, Sequence)
            or (env is not None and not isinstance(env, Mapping))
        ):
            raise RootControlDeploymentUnavailable()
        arguments = tuple(argv)
        environment = None if env is None else dict(env)
        if (
            not arguments
            or any(type(value) is not str or not value or "\0" in value for value in arguments)
            or (
                environment is not None
                and any(
                    type(key) is not str or type(value) is not str or "\0" in key or "\0" in value
                    for key, value in environment.items()
                )
            )
        ):
            raise RootControlDeploymentUnavailable()

        # Validate the caller's exact live funded connection and configured
        # target before any Popen attempt. This check performs no SQL.
        channel._check_funded_worker()
        target = bootstrap._repository.resolve_target()
        if (
            type(target) is not DatabaseTarget
            or target.scope != "main"
            or not bootstrap.matches_primary_target(target)
            or channel.target.identity != target.identity
            or channel.target.store_id != target.store_id
        ):
            raise RootControlDeploymentUnavailable()
        channel._check_funded_worker()

        now = clock()
        validate_clock(now)
        owner = reservation._owner
        with owner._guard:
            footprint = reservation._footprint
            if (
                type(owner) is not DeliveryAdmission
                or reservation._state != "reserved"
                or owner._commands.get(reservation._key) is not reservation
                or owner._pending_commands.get(reservation._key) is not reservation
                or reservation._startup_basis is not None
                or footprint.memory_bytes <= 0
                or footprint.cpu_millicores <= 0
                or footprint.storage_slots != 0
                or reservation._thread_worker is not None
                or reservation._process_worker is not None
                or reservation._actor_thread is not None
                or not reservation._pending_start_locked(now)
            ):
                raise RootControlDeploymentUnavailable()

        # Retain the safe wrapper before the launch call. _launch_process may
        # have registered its exact worker before a late native acknowledgement
        # raises; this object can then carry that worker out with the failure.
        deployment = cls(
            bootstrap=bootstrap,
            channel=channel,
            principal=principal,
            target=target,
            reservation=reservation,
            process=None,
        )
        # argv/env are trusted internal deployment inputs; they do not attest
        # executable identity or add a native executable verification claim.
        try:
            process = reservation._launch_process(
                arguments,
                expected_store_id=target.store_id,
                expected_target_identity_sha256=sql_startup_target_identity_sha256(target),
                env=environment,
                clock=clock,
                root_control_channel=True,
            )
        except CommandProcessStartUncertain as error:
            deployment._process = error.worker
            cls._best_effort_abort(error.worker)
            raise RootControlDeploymentUnavailable(deployment, error.cause) from error
        except BaseException as error:
            # Preserve the primary failure, but recover a worker already
            # registered by the reservation before allowing it to escape.
            worker = cls._registered_process_worker(reservation)
            if worker is None:
                raise
            deployment._process = worker
            cls._best_effort_abort(worker)
            raise RootControlDeploymentUnavailable(deployment, error) from error

        if process is None:
            worker = cls._registered_process_worker(reservation)
            if worker is not None:
                deployment._process = worker
                cls._best_effort_abort(worker)
                raise RootControlDeploymentUnavailable(deployment)
            raise RootControlDeploymentUnavailable()
        if type(process) is not CommandProcess:
            worker = cls._registered_process_worker(reservation)
            if worker is None:
                raise RootControlDeploymentUnavailable()
            deployment._process = worker
            cls._best_effort_abort(worker)
            raise RootControlDeploymentUnavailable(deployment)
        deployment._process = process
        try:
            binding = process.receive_binding(timeout_s=binding_timeout_s)
            subject = process.root_subject()
            if (
                type(binding) is not ResourceBinding
                or type(subject) is not RootSqlSubject
                or subject.binding is not binding
            ):
                raise RootControlDeploymentUnavailable()
            deployment._subject = subject
            return deployment
        except BaseException as error:
            cls._best_effort_abort(process)
            if isinstance(error, RootControlDeploymentUnavailable):
                cause = error.cause
            else:
                cause = error
            raise RootControlDeploymentUnavailable(deployment, cause) from error

    def activate(
        self,
        *,
        journal_id: str,
        intent_id: str,
        session_id: str,
        key_supplier: Callable[[], tuple[str, Ed25519PrivateKey]],
        startup_footprint: ResourceAmounts,
        refresh_footprint: ResourceAmounts,
        timeout_s: float = 30,
    ) -> None:
        """Issue once from existing operator funding, then deliver once to Root."""
        with self._operation():
            self._activate_locked(
                journal_id=journal_id,
                intent_id=intent_id,
                session_id=session_id,
                key_supplier=key_supplier,
                startup_footprint=startup_footprint,
                refresh_footprint=refresh_footprint,
                timeout_s=timeout_s,
            )

    def _activate_locked(
        self,
        *,
        journal_id: str,
        intent_id: str,
        session_id: str,
        key_supplier: Callable[[], tuple[str, Ed25519PrivateKey]],
        startup_footprint: ResourceAmounts,
        refresh_footprint: ResourceAmounts,
        timeout_s: float,
    ) -> None:
        if self._activation_attempted or self._subject is None or not _valid_timeout(timeout_s):
            raise RootControlDeploymentUnavailable(self)
        process = self._require_process()

        # Revalidate the authority and original funded channel before the
        # issuer can inspect target/configuration, request a key or use SQL.
        try:
            self._bootstrap._administrator(self._principal)
            self._check_funded_target()
            live_subject = process.root_subject()
            if live_subject != self._subject:
                raise RootControlDeploymentUnavailable(self)
        except BaseException as error:
            if isinstance(error, RootControlDeploymentUnavailable):
                raise
            raise RootControlDeploymentUnavailable(self, error) from error

        # The attempt is sticky before entering the issuer. Any later exception
        # may follow a durable commit or an ambiguous acknowledgement.
        self._activation_attempted = True
        try:
            packet = issue_root_control_activation(
                self._bootstrap,
                self._channel,
                principal=self._principal,
                journal_id=journal_id,
                intent_id=intent_id,
                session_id=session_id,
                subject=self._subject,
                key_supplier=key_supplier,
                startup_footprint=startup_footprint,
                refresh_footprint=refresh_footprint,
            )
        except BaseException as error:
            if isinstance(error, RootControlActivationUnavailableAfterCommit):
                record = error.record
                if type(record) is RootSqlOpeningIntent:
                    self._record = record
            self._best_effort_abort(process)
            raise RootControlDeploymentUnavailable(self, error) from error

        try:
            if (
                type(packet) is not bytes
                or not packet
                or len(packet) > ROOT_CONTROL_ACTIVATION_MAX_BYTES
            ):
                raise ValueError("activation packet is invalid")
            activation = RootControlActivation.from_bytes(packet)
            record = activation.certificate.record
            if type(record) is RootSqlOpeningIntent:
                self._record = record
            if (
                type(record) is not RootSqlOpeningIntent
                or record.journal_id != journal_id
                or record.intent_id != intent_id
                or record.session_id != session_id
                or record.subject != self._subject.binding
                or record.subject_sql_application_name != self._subject.sql_application_name
            ):
                raise ValueError("activation record does not match the retained subject")
        except BaseException as error:
            self._best_effort_abort(process)
            raise RootControlDeploymentUnavailable(self, error) from error

        try:
            process.root_activate(packet, timeout_s=timeout_s)
        except BaseException as error:
            self._active = False
            # RPC failure is not proof about child exit. Keep the same process
            # handle and committed record; root_abort closes only this channel.
            self._best_effort_abort(process)
            raise RootControlDeploymentUnavailable(self, error) from error
        self._active = True

    def _check_funded_target(self) -> None:
        self._channel._check_funded_worker()
        target = self._bootstrap._repository.resolve_target()
        if (
            type(target) is not DatabaseTarget
            or target.scope != "main"
            or not self._bootstrap.matches_primary_target(target)
            or target.identity != self._target.identity
            or target.store_id != self._target.store_id
            or self._channel.target.identity != self._target.identity
            or self._channel.target.store_id != self._target.store_id
        ):
            raise RootControlDeploymentUnavailable(self)
        self._channel._check_funded_worker()

    @staticmethod
    def _registered_process_worker(reservation: CommandReservation) -> CommandProcess | None:
        owner = reservation._owner
        try:
            with owner._guard:
                worker = reservation._process_worker
                if (
                    type(worker) is CommandProcess
                    and worker._reservation is reservation
                    and owner._commands.get(reservation._key) is reservation
                    and reservation._state == "started"
                    and reservation._start_pid == os.getpid()
                    and reservation._start_thread is threading.current_thread()
                    and reservation._start_task is _current_async_task()
                ):
                    return worker
        except BaseException:
            # Keep launch's original exception. A forked or otherwise untrusted
            # context must not touch the inherited guard or claim the worker.
            return None
        return None

    def _require_process(self) -> CommandProcess:
        self._check_owner()
        process = self._process
        if process is None:
            process = self._registered_process_worker(self._reservation)
            if process is not None:
                self._process = process
        if process is None:
            raise RootControlDeploymentUnavailable(self)
        return process

    @staticmethod
    def _best_effort_abort(process: CommandProcess) -> None:
        try:
            process.root_abort()
        except BaseException:
            # The same retained CommandProcess still owns any native resources.
            pass

    def refresh(self, *, timeout_s: float = 30) -> str:
        with self._operation():
            if not self._active or not _valid_timeout(timeout_s):
                raise RootControlDeploymentUnavailable(self)
            process = self._require_process()
            try:
                return process.root_refresh(timeout_s=timeout_s)
            except BaseException as error:
                self._active = False
                raise RootControlDeploymentUnavailable(self, error) from error

    def stop(self, *, timeout_s: float = 30) -> None:
        with self._operation():
            if not self._active or not _valid_timeout(timeout_s):
                raise RootControlDeploymentUnavailable(self)
            process = self._require_process()
            try:
                process.root_stop(timeout_s=timeout_s)
            except BaseException as error:
                self._active = False
                raise RootControlDeploymentUnavailable(self, error) from error
            self._active = False

    def join(self, timeout_s: float | None = None) -> bool:
        with self._operation():
            if not _valid_timeout(timeout_s, optional=True):
                raise RootControlDeploymentUnavailable(self)
            joined = self._require_process().join(timeout_s)
            if joined:
                self._active = False
            return joined

    def send_signal(self, signum: int) -> None:
        with self._operation():
            if isinstance(signum, bool) or not isinstance(signum, int):
                raise RootControlDeploymentUnavailable(self)
            self._require_process().send_signal(signum)

    def cleanup(self, timeout_s: float | None = None) -> bool:
        with self._operation():
            if not _valid_timeout(timeout_s, optional=True):
                raise RootControlDeploymentUnavailable(self)
            # CommandProcess.cleanup closes the private endpoint before waiting
            # and never returns its started reservation as a cleanup side effect.
            try:
                result = self._require_process().cleanup(timeout_s)
            finally:
                self._active = False
            return result
