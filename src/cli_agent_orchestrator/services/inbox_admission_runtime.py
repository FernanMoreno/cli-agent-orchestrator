"""Trusted operational wiring; owns physical control I/O independently of HTTP waiters."""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Generic, Protocol, TypeVar
from uuid import uuid4

from cli_agent_orchestrator.models.admission_control import AdmissionDiagnostics
from cli_agent_orchestrator.models.resource_envelope import (
    EnvelopeClaims,
    ResourceEnvelopeRecord,
    envelope_integer,
)
from cli_agent_orchestrator.services.admission_bootstrap import AttestationUpdate, ResourceBootstrap
from cli_agent_orchestrator.services.admission_diagnostics import diagnostics_response
from cli_agent_orchestrator.services.admission_monitor import (
    AdmissionMonitor,
    AuthorityMutationState,
)
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSampler
from cli_agent_orchestrator.services.admission_settings import (
    AdmissionPolicyLoader,
    read_private_operator_policy,
)
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

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
    ):
        self.inbox = inbox
        self.monitor = monitor
        self.bootstrap = bootstrap
        self._clock = clock
        self._drain_timeout = drain_timeout_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._closing = False
        self._bound = False
        self._retired = False
        self._call: _ControlCall[Any] | None = None
        monitor.set_on_change(inbox.notify_admission_changed)

    @property
    def clock(self) -> Callable[[], float]:
        return self._clock

    @property
    def retired(self) -> bool:
        return self._retired

    @property
    def available(self) -> bool:
        return self._started and not self._closing and not self._retired

    async def start(self) -> None:
        if self._started or self._closing or self._retired:
            raise AdmissionRuntimeUnavailable("admission runtime cannot restart")
        self._loop = asyncio.get_running_loop()
        self.inbox.bind_admission(
            self.monitor.owner, clock=self._clock, retirement=lambda: self.retired
        )
        self._bound = True
        self.inbox.start_accepting()
        try:
            await self.monitor.start()
        except BaseException:
            self.inbox.stop_accepting()
            raise
        self._started = True

    def close_admission(self) -> None:
        self._closing = True
        if self._bound:
            self.inbox.stop_accepting()
        self.monitor.stop_accepting()

    def _check_request(self, principal: object) -> None:
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
                    return callback()
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

    def diagnostics(self, *, principal: object) -> AdmissionDiagnostics:
        """Read only cached process state, including while authority I/O is busy."""
        ResourceBootstrap._administrator(principal)
        if not self.available or asyncio.get_running_loop() is not self._loop:
            raise AdmissionRuntimeUnavailable("admission runtime is unavailable")

        now = self._clock()
        return diagnostics_response(self.monitor.diagnostic(now=now), now=now)

    async def registered(self, *, principal: object) -> ResourceEnvelopeRecord:
        return await self._execute(
            principal,
            lambda: self.bootstrap.registered_current(principal=principal),
            mutating=False,
        )

    async def stop(self) -> None:
        if self._retired:
            return
        self.close_admission()
        failure: Exception | None = None
        try:
            await self.monitor.stop()
        except Exception as error:
            failure = error
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
        if self._bound:
            timeout = self._drain_timeout
            if timeout is None:
                from cli_agent_orchestrator.services.settings_service import get_server_settings

                settings = get_server_settings()
                timeout = float(
                    max(settings["provider_init_timeout"], settings["mcp_request_timeout"])
                )
            await self.inbox.drain(timeout_s=timeout)
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


def runtime_from_environment(
    inbox: AdmissionParticipant,
    *,
    bootstrap_factory: Callable[[], ResourceBootstrap] | None = None,
) -> InboxAdmissionRuntime | None:
    if "CAO_ADMISSION_POLICY_FILE" not in os.environ:
        return None
    value = os.environ["CAO_ADMISSION_POLICY_FILE"]
    path = Path(value)
    if not value.strip() or not path.is_absolute():
        raise ValueError("admission policy requires an absolute private source path")
    from cli_agent_orchestrator.security.auth import is_auth_enabled

    if not is_auth_enabled():
        raise ValueError("resource admission requires configured operator authentication")
    owner = DeliveryAdmission(AdmissionPolicy())
    bootstrap = bootstrap_factory() if bootstrap_factory is not None else ResourceBootstrap()
    monitor = AdmissionMonitor(
        owner,
        policy_loader=AdmissionPolicyLoader(source=lambda: read_private_operator_policy(path)),
        bootstrap=bootstrap,
        postgres=_PROCESS_PRESSURE,
        scope_id=f"{uuid4()}:{path}",
        authority_state=_PROCESS_AUTHORITY,
    )
    return InboxAdmissionRuntime(inbox, monitor=monitor, bootstrap=bootstrap)
