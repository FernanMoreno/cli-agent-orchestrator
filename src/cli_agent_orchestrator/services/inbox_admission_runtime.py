"""Trusted operational wiring; owns physical control I/O independently of HTTP waiters."""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
import time
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Generic, Literal, Protocol, TypeVar, overload
from uuid import uuid4

from cli_agent_orchestrator.clients.admission_policy_repository import (
    AdmissionPolicyRepository,
    PolicyRevisionConflict,
)
from cli_agent_orchestrator.clients.postgres import DatabaseTarget
from cli_agent_orchestrator.clients.storage_admission import storage_process_current
from cli_agent_orchestrator.models.admission_control import (
    AdmissionDiagnostics,
    AdmissionDiagnosticsV2,
)
from cli_agent_orchestrator.models.admission_domains import (
    SharedAbortReceipt,
    SharedDebtCursor,
    SharedDebtPage,
    SharedReleaseReceipt,
)
from cli_agent_orchestrator.models.admission_policy_control import (
    AdmissionPolicyHistory,
    AdmissionPolicyRecord,
    AdmissionPolicyRevision,
    AdmissionPolicyState,
    AdmissionPolicyUpdate,
    AdmissionPolicyValues,
)
from cli_agent_orchestrator.models.resource_envelope import (
    EnvelopeClaims,
    ResourceEnvelopeRecord,
    envelope_integer,
)
from cli_agent_orchestrator.models.resource_generation import GenerationSeal
from cli_agent_orchestrator.services.admission_bootstrap import AttestationUpdate, ResourceBootstrap
from cli_agent_orchestrator.services.admission_diagnostics import diagnostics_response
from cli_agent_orchestrator.services.admission_domains import (
    ResourceRecoveryRepository,
    abort_preparing_for_admin,
    release_witnessed_for_admin,
)
from cli_agent_orchestrator.services.admission_monitor import (
    AdmissionMonitor,
    AuthorityMutationState,
)
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSampler
from cli_agent_orchestrator.services.admission_settings import (
    AdmissionPolicyLoader,
    configured_policy_loader,
    parse_operator_policy,
    read_private_operator_policy,
)
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.shared_delivery_admission import (
    SharedDeliveryCoordinator,
    SharedDeliveryDrainIncomplete,
    SharedResourceRepository,
    authorize_shared_delivery,
)

T = TypeVar("T")


class AdmissionParticipant(Protocol):
    """Trusted process lifecycle port, not an administrator or a capacity claim."""

    def bind_admission(
        self,
        admission: DeliveryAdmission | None,
        *,
        clock: Callable[[], float],
        retirement: Callable[[], bool] | None,
    ) -> None: ...

    def notify_admission_changed(self, terminal_id: str | None = None) -> None: ...

    def start_accepting(self) -> None: ...

    def stop_accepting(self) -> None: ...

    async def drain(self, *, timeout_s: float) -> None: ...


class AdmissionRuntimeUnavailable(RuntimeError):
    """Control runtime is stopped, busy, or has not retired safely."""


@dataclass
class _ControlCall(Generic[T]):
    future: Future[T] = field(default_factory=Future)
    finished: threading.Event = field(default_factory=threading.Event)


class InboxAdmissionRuntime:
    """Shared process control; retains its public Inbox name for compatibility."""

    def __init__(
        self,
        inbox: AdmissionParticipant,
        *,
        monitor: AdmissionMonitor,
        bootstrap: ResourceBootstrap,
        clock: Callable[[], float] = time.monotonic,
        drain_timeout_s: float | None = None,
        shared_repository: SharedResourceRepository | None = None,
        recovery_repository: ResourceRecoveryRepository | None = None,
        policy_repository: AdmissionPolicyRepository | None = None,
        policy_file: Path | None = None,
    ):
        if recovery_repository is not None and shared_repository is None:
            raise ValueError("resource recovery requires shared admission composition")
        self._policy_repository = policy_repository
        self._policy_file = policy_file
        self._pid = os.getpid()
        self._terminal_closing = False
        self.inbox = inbox
        self.monitor = monitor
        self.bootstrap = bootstrap
        self._clock = clock
        self._drain_timeout = drain_timeout_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._closing = False
        self._storage_participants: list[AdmissionParticipant] = []
        self._bound_storage_participants: list[AdmissionParticipant] = []
        self._bound = False
        self._retired = False
        self._call: _ControlCall[Any] | None = None
        # Explicit trusted composition; absent means no shared admission at all.
        self._shared_repository = shared_repository
        self._recovery_repository = recovery_repository
        self._shared: SharedDeliveryCoordinator | None = None
        if shared_repository is not None:
            monitor.owner.require_shared_admission()
        monitor.set_on_change(inbox.notify_admission_changed)

    def _check_process(self) -> None:
        if self._pid != os.getpid():
            raise AdmissionRuntimeUnavailable("admission runtime belongs to another process")

    @property
    def clock(self) -> Callable[[], float]:
        return self._clock

    @property
    def retired(self) -> bool:
        return self._retired

    @property
    def available(self) -> bool:
        return (
            self._started and not self._closing and not self._terminal_closing and not self._retired
        )

    def add_storage_participant(
        self, participant: AdmissionParticipant, *, target: DatabaseTarget
    ) -> None:
        """Trusted composition only, before binding any process participant."""
        self._check_process()
        if self._bound or self._started or self._closing or self._retired:
            raise AdmissionRuntimeUnavailable("storage participants must be wired before start")
        if not self.bootstrap.matches_primary_target(target):
            raise ValueError("storage participant requires the configured main target")
        if participant is self.inbox or any(
            item is participant for item in self._storage_participants
        ):
            raise ValueError("storage participant is already registered")
        self._storage_participants.append(participant)

    async def start(self) -> None:
        self._check_process()
        if self._started or self._closing or self._retired:
            raise AdmissionRuntimeUnavailable("admission runtime cannot restart")
        self._loop = asyncio.get_running_loop()
        if self._shared_repository is not None:
            # Close normal data before any participant can admit it.
            self.monitor.owner.require_shared_admission()
        self.inbox.bind_admission(
            self.monitor.owner, clock=self._clock, retirement=lambda: self.retired
        )
        self._bound = True
        try:
            for participant in self._storage_participants:
                participant.bind_admission(
                    self.monitor.owner, clock=self._clock, retirement=lambda: self.retired
                )
                self._bound_storage_participants.append(participant)
            self.inbox.start_accepting()
            for participant in self._bound_storage_participants:
                participant.start_accepting()
            await self.monitor.start()
        except BaseException:
            self.inbox.stop_accepting()
            for participant in self._bound_storage_participants:
                participant.stop_accepting()
            raise
        self._started = True

    def close_admission(self) -> None:
        self._check_process()
        if self._retired:
            return  # Shared participants may already belong to a newer lifetime.
        self._closing = True
        if self._bound:
            self.inbox.stop_accepting()
        for participant in self._bound_storage_participants:
            participant.stop_accepting()
        self.monitor.stop_accepting()
        shared = self._shared
        if shared is not None:
            shared.stop_accepting()

    def _check_request(self, principal: object) -> None:
        self._check_process()
        ResourceBootstrap._administrator(principal)
        if not self.available or asyncio.get_running_loop() is not self._loop:
            raise AdmissionRuntimeUnavailable("admission runtime is unavailable")
        if self._call is not None and not self._call.finished.is_set():
            raise AdmissionRuntimeUnavailable("resource control is busy")

    async def _execute(self, principal: object, callback: Callable[[], T], *, mutating: bool) -> T:
        self._check_request(principal)
        token = self.monitor.begin_authority_change() if mutating else None
        call: _ControlCall[T] | None = None
        try:
            owned_call: _ControlCall[T] = _ControlCall()
            call = owned_call
            self._call = call
            context = contextvars.copy_context()

            def operation() -> T:
                if token is None:
                    if self._closing:
                        raise AdmissionRuntimeUnavailable("admission runtime is stopped")
                    with self.monitor.authority_read():
                        return self.monitor.control_call(callback)
                with self.monitor.authority_change(token):
                    return callback()

            def worker(owned: _ControlCall[T] = owned_call) -> None:
                if not owned.future.set_running_or_notify_cancel():
                    return  # A proven pre-start cancellation cannot perform SQL.
                try:
                    result = context.run(operation)
                except BaseException as error:
                    owned.finished.set()
                    owned.future.set_exception(error)
                else:
                    owned.finished.set()
                    owned.future.set_result(result)

            thread = threading.Thread(target=worker, name="cao-admission-control")
            thread.start()
        except BaseException:
            if call is None or call.future.cancel():
                if token is not None:
                    self.monitor.cancel_authority_change(token)
                if call is not None:
                    call.finished.set()
            # If cancellation lost, physical work is running: retain its Future
            # and barrier even though the HTTP caller cannot observe its result.
            raise
        assert call is not None
        wrapped = asyncio.wrap_future(call.future)
        wrapped.add_done_callback(lambda future: None if future.cancelled() else future.exception())
        return await asyncio.shield(wrapped)

    def _policy_control(self) -> AdmissionPolicyRepository:
        if self._policy_repository is None:
            raise AdmissionRuntimeUnavailable("persistent policy control is unavailable")
        return self._policy_repository

    async def policy_status(self, *, principal: object) -> AdmissionPolicyState:
        def read() -> AdmissionPolicyState:
            repository = self._policy_control()
            return AdmissionPolicyState(
                persisted=repository.read_latest(repository.resolve_target()),
                applied_revision=self.monitor.applied_policy_revision,
            )

        return await self._execute(principal, read, mutating=False)

    async def policy_history(
        self, *, principal: object, limit: int = 50, before_revision: int | None = None
    ) -> AdmissionPolicyHistory:
        def read() -> AdmissionPolicyHistory:
            repository = self._policy_control()
            return repository.history(
                repository.resolve_target(), limit=limit, before_revision=before_revision
            )

        return await self._execute(principal, read, mutating=False)

    async def policy_update(
        self, *, principal: object, body: AdmissionPolicyUpdate
    ) -> AdmissionPolicyRecord:
        actor = ResourceBootstrap._administrator(principal)
        if type(body) is not AdmissionPolicyUpdate:
            raise ValueError("invalid policy update")

        def write() -> AdmissionPolicyRecord:
            repository = self._policy_control()
            return repository.append(
                repository.resolve_target(),
                policy=body.policy,
                expected_revision=body.expected_revision,
                principal_id=actor,
            )

        return await self._execute(principal, write, mutating=True)

    async def policy_revoke(
        self, *, principal: object, body: AdmissionPolicyRevision
    ) -> AdmissionPolicyRecord:
        actor = ResourceBootstrap._administrator(principal)
        if type(body) is not AdmissionPolicyRevision:
            raise ValueError("invalid policy revision")

        def write() -> AdmissionPolicyRecord:
            repository = self._policy_control()
            return repository.revoke(
                repository.resolve_target(),
                expected_revision=body.expected_revision,
                principal_id=actor,
            )

        return await self._execute(principal, write, mutating=True)

    async def policy_seed(
        self, *, principal: object, body: AdmissionPolicyRevision
    ) -> AdmissionPolicyRecord:
        if type(body) is not AdmissionPolicyRevision or body.expected_revision != 0:
            ResourceBootstrap._administrator(principal)
            raise PolicyRevisionConflict("policy seed requires an empty history")

        def read_seed() -> AdmissionPolicyValues:
            self._policy_control()
            if self._policy_file is None:
                raise AdmissionRuntimeUnavailable("policy bootstrap source is unavailable")
            configuration = parse_operator_policy(read_private_operator_policy(self._policy_file))
            return AdmissionPolicyValues(
                **asdict(configuration.policy),
                postgres_reserve_percent=configuration.postgres.reserve_percent,
                postgres_statement_timeout_ms=configuration.postgres.statement_timeout_ms,
                sample_interval_s=configuration.sample_interval_s,
                healthy_samples_before_growth=configuration.healthy_samples_before_growth,
            )

        # Validate the immutable seed snapshot before entering the mutation barrier.
        policy = await self._execute(principal, read_seed, mutating=False)
        return await self.policy_update(
            principal=principal, body=AdmissionPolicyUpdate(expected_revision=0, policy=policy)
        )

    async def attest(
        self, *, principal: object, claims: EnvelopeClaims, expected_revision: int
    ) -> AttestationUpdate:
        envelope_integer(expected_revision, minimum=0)
        if expected_revision == 2**63 - 1 or type(claims) is not EnvelopeClaims:
            raise ValueError("invalid resource authority request")
        return await self._execute(
            principal,
            lambda: self.bootstrap.attest_current(
                principal=principal, claims=claims, expected_revision=expected_revision
            ),
            mutating=True,
        )

    async def revoke(self, *, principal: object, expected_revision: int) -> AttestationUpdate:
        envelope_integer(expected_revision, minimum=0)
        if expected_revision == 2**63 - 1:
            raise ValueError("invalid resource authority revision")
        return await self._execute(
            principal,
            lambda: self.bootstrap.revoke_current(
                principal=principal, expected_revision=expected_revision
            ),
            mutating=True,
        )

    @overload
    def diagnostics(self, *, principal: object, version: Literal[2]) -> AdmissionDiagnosticsV2: ...

    @overload
    def diagnostics(
        self, *, principal: object, version: Literal[1] = 1
    ) -> AdmissionDiagnostics: ...

    @overload
    def diagnostics(
        self, *, principal: object, version: int
    ) -> AdmissionDiagnostics | AdmissionDiagnosticsV2: ...

    def diagnostics(
        self, *, principal: object, version: int = 1
    ) -> AdmissionDiagnostics | AdmissionDiagnosticsV2:
        """Read only cached process state, including while authority I/O is busy."""
        ResourceBootstrap._administrator(principal)
        if not self.available or asyncio.get_running_loop() is not self._loop:
            raise AdmissionRuntimeUnavailable("admission runtime is unavailable")

        self._check_process()
        now = self._clock()
        snapshot = self.monitor.diagnostic(now=now)
        if type(version) is int and version == 1:
            return diagnostics_response(snapshot, now=now)
        return diagnostics_response(snapshot, now=now, version=version)

    async def registered(self, *, principal: object) -> ResourceEnvelopeRecord:
        return await self._execute(
            principal,
            lambda: self.bootstrap.registered_current(principal=principal),
            mutating=False,
        )

    async def activate_shared(self, *, principal: object) -> None:
        """Authorize this owner's shared delivery inside the existing control scope."""
        ResourceBootstrap._administrator(principal)  # Before repository, binding or SQL.
        repository = self._shared_repository
        control = self.monitor.storage_control
        if repository is None or control is None:
            raise AdmissionRuntimeUnavailable("shared admission is not configured")
        owner = self.monitor.owner
        self._check_request(principal)
        if self._shared is not None and owner.shared_ready:
            return

        def activate() -> None:
            coordinator = self._shared
            if coordinator is None:
                coordinator = authorize_shared_delivery(
                    principal=principal,
                    repository=repository,
                    owner=owner,
                    control=control,
                    retirement=lambda: self.retired,
                    on_retired=self.inbox.notify_admission_changed,
                    clock=self._clock,
                )
                # Retained before binding: a cancelled waiter or stop never loses it.
                self._shared = coordinator
            elif not coordinator.accepting:
                raise AdmissionRuntimeUnavailable("shared admission has stopped")
            try:
                if self._closing:
                    raise AdmissionRuntimeUnavailable("admission runtime is stopped")
                owner.bind_shared_admission(coordinator)
            except BaseException:
                coordinator.stop_accepting()
                raise

        await self._execute(principal, activate, mutating=False)

    async def outstanding_resources(
        self, *, principal: object, after: SharedDebtCursor | None = None, limit: int = 100
    ) -> SharedDebtPage:
        self._check_request(principal)
        repository = self._recovery_repository
        shared = self._shared
        owner = self.monitor.owner
        if repository is None or shared is None or not owner.shared_ready or not shared.accepting:
            raise AdmissionRuntimeUnavailable("shared resource recovery is unavailable")

        def read() -> SharedDebtPage:
            if self._closing or not owner.shared_ready or not shared.accepting:
                raise AdmissionRuntimeUnavailable("shared resource recovery is stopped")
            return repository.outstanding_resources(
                after=after, limit=limit, expected_binding=shared.coverage.binding
            )

        return await self._execute(principal, read, mutating=False)

    async def abort_preparing(
        self, *, principal: object, action_id: str, operation_id: str, expected_revision: int
    ) -> SharedAbortReceipt:
        self._check_request(principal)  # Auth before resolving either repository port.
        repository = self._recovery_repository
        shared = self._shared
        owner = self.monitor.owner
        if repository is None or shared is None or not owner.shared_ready or not shared.accepting:
            raise AdmissionRuntimeUnavailable("shared resource recovery is unavailable")

        def abort() -> SharedAbortReceipt:
            if self._closing or not owner.shared_ready or not shared.accepting:
                raise AdmissionRuntimeUnavailable("shared resource recovery is stopped")
            return abort_preparing_for_admin(
                repository,
                principal=principal,
                action_id=action_id,
                operation_id=operation_id,
                expected_revision=expected_revision,
                expected_binding=shared.coverage.binding,
            )

        receipt = await self._execute(principal, abort, mutating=False)
        self.inbox.notify_admission_changed()
        return receipt

    async def release_witnessed(
        self, *, principal: object, action_id: str, operation_id: str, expected_revision: int
    ) -> SharedReleaseReceipt:
        self._check_request(principal)  # Auth before resolving either repository port.
        repository = self._recovery_repository
        shared = self._shared
        owner = self.monitor.owner
        if repository is None or shared is None or not owner.shared_ready or not shared.accepting:
            raise AdmissionRuntimeUnavailable("shared resource recovery is unavailable")

        def abort() -> SharedReleaseReceipt:
            if self._closing or not owner.shared_ready or not shared.accepting:
                raise AdmissionRuntimeUnavailable("shared resource recovery is stopped")
            return release_witnessed_for_admin(
                repository,
                principal=principal,
                action_id=action_id,
                operation_id=operation_id,
                expected_revision=expected_revision,
                expected_binding=shared.coverage.binding,
            )

        receipt = await self._execute(principal, abort, mutating=False)
        self.inbox.notify_admission_changed()
        return receipt

    async def _join_control_call(self) -> None:
        call = self._call
        if call is not None and not call.future.cancelled():
            wrapped = asyncio.wrap_future(call.future)
            wrapped.add_done_callback(
                lambda future: None if future.cancelled() else future.exception()
            )
            try:
                await asyncio.shield(wrapped)
            except Exception:
                # The control response owns this result. Uncertain effects stay
                # latched in the process authority state, never auto-cleared.
                pass
        if call is not None and not call.finished.is_set():
            raise AdmissionRuntimeUnavailable("physical resource control has not retired")

    async def seal_terminal_generation(self) -> GenerationSeal | None:
        """Quiesce shared producers, then append a still-funded terminal fence.

        The caller quiesces its transport first and performs full stop in finally.
        Participant.stop_accepting also stops the owner, so it must follow this
        last control transaction. A seal is never a physical closure witness.
        """
        self._check_process()
        repository = self._shared_repository
        if repository is None or not self._started or self._retired:
            return None  # No activated shared producer; no seal or closure claimed.
        if asyncio.get_running_loop() is not self._loop or self._closing:
            raise AdmissionRuntimeUnavailable("terminal resource control is unavailable")
        if not self.bootstrap.terminal_generation:
            raise PermissionError("resource participant has a reusable lifecycle")
        self._terminal_closing = True  # Reject new operator calls before joining an accepted one.
        if self._shared is not None:
            self._shared.stop_accepting()

        from cli_agent_orchestrator.services.work_blocking import run_owned_async

        async def seal() -> GenerationSeal | None:
            await self._join_control_call()
            # A late activation may have published its exact original coverage.
            shared = self._shared
            if shared is None:
                return None
            shared.stop_accepting()
            authority = self.bootstrap.issue_terminal_generation_authority(shared.coverage.binding)
            control = self.monitor.storage_control
            if control is None:
                raise AdmissionRuntimeUnavailable("terminal resource control is not funded")

            def funded() -> GenerationSeal:
                with self.monitor.authority_read():
                    with control.operation(clock=self._clock, wait=True):
                        return repository.seal_current_generation(authority)

            return await asyncio.to_thread(funded)

        return await run_owned_async(seal())

    def _resolve_drain_timeout(self) -> float:
        timeout = self._drain_timeout
        if timeout is None:
            from cli_agent_orchestrator.services.settings_service import get_server_settings

            settings = get_server_settings()
            timeout = float(max(settings["provider_init_timeout"], settings["mcp_request_timeout"]))
        return timeout

    async def stop(self) -> None:
        self._check_process()
        if self._retired:
            return
        self.close_admission()
        failure: Exception | None = None
        try:
            await self.monitor.stop()
        except Exception as error:
            failure = error
        await self._join_control_call()
        # Re-read after the join: a late activation may have published it.
        shared = self._shared
        if shared is not None:
            shared.stop_accepting()
        from cli_agent_orchestrator.services.work_blocking import run_owned_async

        await run_owned_async(asyncio.to_thread(self.monitor.owner.retry_physical_closes))
        timeout = self._resolve_drain_timeout() if self._bound or shared is not None else 0.0
        if self._bound:

            async def drain_participants() -> None:
                results = await asyncio.gather(
                    self.inbox.drain(timeout_s=timeout),
                    *(
                        participant.drain(timeout_s=timeout)
                        for participant in self._bound_storage_participants
                    ),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, BaseException):
                        raise result

            # Join every participant even if another one fails or the waiter
            # is cancelled; no lease/binding is erased by a partial drain.
            await run_owned_async(drain_participants())
        if shared is not None:
            # After participants joined, before storage retirement. An incomplete
            # drain keeps the reference and retired=False for a later stop.
            try:
                await run_owned_async(asyncio.to_thread(shared.drain, timeout))
            except SharedDeliveryDrainIncomplete:
                raise AdmissionRuntimeUnavailable(
                    "shared delivery cleanup has not retired"
                ) from failure
        from cli_agent_orchestrator.clients.storage_admission import retry_storage_closes

        def retire_storage() -> None:
            # Participants have joined their physical bodies. Retry only the
            # process registry's already requested exact closes, once per stop.
            native_result = self.monitor.owner.retry_physical_closes()
            result = retry_storage_closes()
            sampler_retired = self.monitor.retire_storage()
            if not native_result.clean or not result.clean or sampler_retired is not True:
                raise AdmissionRuntimeUnavailable("physical storage cleanup has not retired")

        # A cancelled waiter never detaches the close callback or frees its
        # retained loan; a subsequent stop may observe/retry the same owner.
        await run_owned_async(asyncio.to_thread(retire_storage))
        if self.monitor.owner.snapshot(now=self._clock()).inflight:
            raise AdmissionRuntimeUnavailable("physical delivery reservations have not retired")
        if not self.monitor.retired:
            raise AdmissionRuntimeUnavailable("physical admission monitor has not retired")
        self._retired = True
        if failure is not None:
            raise failure


# These process-owned objects survive API lifespan reconstruction. No new
# instance may erase an uncertain metrics connection or operator-write outcome.
_PROCESS_PRESSURE = PostgresPressureSampler()
_PROCESS_AUTHORITY = AuthorityMutationState()
_INHERITED_PRESSURE: list[PostgresPressureSampler] = []


def _after_pressure_fork_child() -> None:
    global _PROCESS_PRESSURE
    if _PROCESS_PRESSURE.process_current or not storage_process_current():
        return
    # Driver detachment and the child registry already succeeded. Retain old
    # owners without touching their locks/handles or retiring parent tokens.
    _INHERITED_PRESSURE.append(_PROCESS_PRESSURE)
    _PROCESS_PRESSURE = PostgresPressureSampler()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_pressure_fork_child)


def runtime_from_environment(
    inbox: AdmissionParticipant,
    *,
    bootstrap_factory: Callable[[], ResourceBootstrap] | None = None,
) -> InboxAdmissionRuntime | None:
    mode = os.environ.get("CAO_ADMISSION_MODE", "local")
    if mode not in {"local", "shared"}:
        raise ValueError("admission mode must be local or shared")
    selected = os.environ.get("CAO_ADMISSION_POLICY_STORE", "file")
    if selected not in {"file", "postgres"}:
        raise ValueError("invalid admission policy store")
    value = os.environ.get("CAO_ADMISSION_POLICY_FILE")
    path = None if value is None else Path(value)
    if path is not None and (not value or not value.strip() or not path.is_absolute()):
        raise ValueError("admission policy requires an absolute private source path")
    if selected == "file" and path is None:
        if mode == "shared":
            raise ValueError("shared admission requires a private operator policy")
        return None
    from cli_agent_orchestrator.security.auth import is_auth_enabled

    if not is_auth_enabled():
        raise ValueError("resource admission requires configured operator authentication")
    if not _PROCESS_PRESSURE.process_current:
        raise AdmissionRuntimeUnavailable("process storage ownership is unavailable")
    owner, storage_control = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    bootstrap = bootstrap_factory() if bootstrap_factory is not None else ResourceBootstrap()
    if selected == "file":
        assert path is not None
        loader = AdmissionPolicyLoader(source=lambda: read_private_operator_policy(path))
    else:
        loader = configured_policy_loader(path)
    monitor = AdmissionMonitor(
        owner,
        policy_loader=loader,
        storage_control=storage_control,
        bootstrap=bootstrap,
        postgres=_PROCESS_PRESSURE,
        scope_id=f"{uuid4()}:{path}",
        authority_state=_PROCESS_AUTHORITY,
    )
    # Deployment selection is not enrollment or authority. Construction errors
    # abort startup; shared mode must never quietly return a local-only owner.
    repository = bootstrap.shared_repository() if mode == "shared" else None
    if loader.persistent:
        return InboxAdmissionRuntime(
            inbox,
            monitor=monitor,
            bootstrap=bootstrap,
            shared_repository=repository,
            recovery_repository=repository,
            policy_repository=loader.repository,
            policy_file=path,
        )
    return InboxAdmissionRuntime(
        inbox,
        monitor=monitor,
        bootstrap=bootstrap,
        shared_repository=repository,
        recovery_repository=repository,
    )
