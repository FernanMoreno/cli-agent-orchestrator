"""One original, funded worker owns the root control channel for its lifetime."""

from __future__ import annotations

import asyncio
import concurrent.futures
import math
import os
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import NoReturn, SupportsIndex, TypeVar

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cli_agent_orchestrator.clients.funded_control_session import FundedControlSession
from cli_agent_orchestrator.clients.postgres import Connection
from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    storage_close_uncertainty,
)
from cli_agent_orchestrator.models.admission_control import (
    AdmissionDiagnostics,
    AdmissionDiagnosticsV2,
)
from cli_agent_orchestrator.models.sql_root_genesis import SignedRootSqlOpening
from cli_agent_orchestrator.services.admission_bootstrap import (
    ResourceBootstrap,
    VerifiedResourceAttestation,
)
from cli_agent_orchestrator.services.admission_diagnostics import diagnostics_response
from cli_agent_orchestrator.services.admission_monitor import (
    AdmissionMonitor,
    MonitorReason,
    MonitorResult,
    ResourceSampler,
)
from cli_agent_orchestrator.services.admission_policy import ResourceAmounts
from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSampler
from cli_agent_orchestrator.services.admission_settings import AdmissionPolicyLoader
from cli_agent_orchestrator.services.delivery_admission import (
    CommandAdmissionAttempt,
    CommandReservation,
    CommandThread,
    CommandThreadStartUncertain,
    DeliveryAdmission,
    DeliveryReservation,
    StorageControlPermit,
)
from cli_agent_orchestrator.services.root_sql_control import root_sql_control_channel

_ContextValue = TypeVar("_ContextValue")


@contextmanager
def _witness_exit(
    manager: AbstractContextManager[_ContextValue], witness: Callable[[], None]
) -> Iterator[_ContextValue]:
    """Record an exit only after the original context manager returns cleanly."""
    value = manager.__enter__()
    try:
        yield value
    except BaseException:
        suppressed = manager.__exit__(*sys.exc_info())
        witness()
        if not suppressed:
            raise
    else:
        manager.__exit__(None, None, None)
        witness()


class OriginalControlUnavailable(StorageAdmissionUnavailable):
    """Fixed failure for an unavailable or uncertain original control owner."""


@dataclass(frozen=True)
class OriginalControlConfig:
    bootstrap: ResourceBootstrap
    owner: DeliveryAdmission
    control: StorageControlPermit
    certificate: SignedRootSqlOpening
    trusted_keys: Mapping[str, Ed25519PublicKey]
    policy_loader: AdmissionPolicyLoader
    scope_id: str
    command_footprint: ResourceAmounts
    clock: Callable[[], float]
    resource_factory: Callable[[VerifiedResourceAttestation], ResourceSampler] | None = None

    def __post_init__(self) -> None:
        if (
            type(self.bootstrap) is not ResourceBootstrap
            or type(self.owner) is not DeliveryAdmission
            or type(self.control) is not StorageControlPermit
            or not self.control.belongs_to(self.owner)
            or type(self.certificate) is not SignedRootSqlOpening
            or type(self.policy_loader) is not AdmissionPolicyLoader
            or not isinstance(self.scope_id, str)
            or not self.scope_id.strip()
            or type(self.command_footprint) is not ResourceAmounts
            or self.command_footprint.memory_bytes <= 0
            or self.command_footprint.cpu_millicores <= 0
            or self.command_footprint.storage_slots != 0
            or not callable(self.clock)
            or (self.resource_factory is not None and not callable(self.resource_factory))
            or not isinstance(self.trusted_keys, Mapping)
        ):
            raise OriginalControlUnavailable()
        keys = dict(self.trusted_keys)
        if any(
            type(key_id) is not str or not isinstance(key, Ed25519PublicKey)
            for key_id, key in keys.items()
        ):
            raise OriginalControlUnavailable()
        object.__setattr__(self, "trusted_keys", MappingProxyType(keys))


@dataclass(frozen=True)
class _Refresh:
    reservation: CommandReservation
    result: concurrent.futures.Future[MonitorResult]


class OriginalControlOwner:
    """A single synchronous native worker and a resource-bounded refresh mailbox."""

    def __init__(self, config: OriginalControlConfig, *, startup: CommandReservation):
        if type(config) is not OriginalControlConfig or type(startup) is not CommandReservation:
            raise OriginalControlUnavailable()
        owner = config.owner
        with owner._guard:
            if (
                startup._owner is not owner
                or startup._state != "reserved"
                or owner._commands.get(startup._key) is not startup
                or owner._pending_commands.get(startup._key) is not startup
                or startup._footprint.memory_bytes <= 0
                or startup._footprint.cpu_millicores <= 0
                or startup._footprint.storage_slots != 0
            ):
                raise OriginalControlUnavailable()
        self._config = config
        self._startup = startup
        self._pid = os.getpid()
        self._guard = threading.Condition(threading.RLock())
        self._queue: deque[_Refresh] = deque()
        self._unretired_messages: list[_Refresh] = []
        self._ready: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._worker: CommandThread[None] | None = None
        self._start_attempted = False
        self._start_uncertain = False
        self._no_worker_proven = False
        self._launch_confirmed = threading.Event()
        self._stop_requested = False
        self._terminal_error: BaseException | None = None
        self._monitor: AdmissionMonitor | None = None
        self._control_lease: DeliveryReservation | None = None
        self._channel: Connection | None = None
        self._active_refresh = False
        self._channel_entered = False
        self._session_entered = False
        self._session_exited = False
        self._channel_closed = False
        self._joined = False

    def __copy__(self) -> NoReturn:
        raise TypeError("original control ownership cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise TypeError("original control ownership cannot be copied")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("original control ownership cannot be serialized")

    def _check_process(self) -> None:
        if self._pid != os.getpid():
            raise OriginalControlUnavailable()

    def _mark_no_worker_retired_locked(self) -> None:
        if self._worker is not None or self._startup.state != "released":
            raise OriginalControlUnavailable()
        self._no_worker_proven = True
        self._joined = True
        self._session_exited = True
        self._channel_closed = True

    @property
    def retired(self) -> bool:
        self._check_process()
        with self._guard:
            worker = self._worker
            return bool(
                self._joined
                and (worker is not None or self._no_worker_proven)
                and self._startup.state == "released"
                and self._session_exited
                and self._channel_closed
                and (self._monitor is None or self._monitor.retired)
                and not self._queue
                and not self._unretired_messages
                and not self._active_refresh
                and not storage_close_uncertainty()
            )

    def start(self) -> None:
        """Make the one native start attempt using the exact startup reservation."""
        self._check_process()
        with self._guard:
            if self._start_attempted or self._stop_requested:
                raise OriginalControlUnavailable()
            self._start_attempted = True
        try:
            worker = self._startup._launch_thread(self._run_worker, clock=self._config.clock)
        except CommandThreadStartUncertain as error:
            with self._guard:
                self._worker = error.worker
                self._start_uncertain = True
            error.worker._future.add_done_callback(self._worker_completed)
            self._fail(error)
            # The worker may already have entered Thread._run and be waiting at
            # this gate. The uncertainty flag is sticky before it can continue.
            self._launch_confirmed.set()
            raise OriginalControlUnavailable() from None
        except BaseException as error:
            self._fail(error)
            if self._startup.state == "reserved":
                self._startup.cancel_before_start()
            with self._guard:
                if self._worker is None and self._startup.state == "released":
                    self._mark_no_worker_retired_locked()
            raise OriginalControlUnavailable() from None
        if worker is None:
            self._startup.cancel_before_start()
            self._fail(StorageAdmissionUnavailable())
            with self._guard:
                if self._startup.state == "released":
                    self._no_worker_proven = True
            raise OriginalControlUnavailable() from None
        with self._guard:
            self._worker = worker
        worker._future.add_done_callback(self._worker_completed)
        self._launch_confirmed.set()

    async def wait_ready(self) -> None:
        self._check_process()
        with self._guard:
            if not self._start_attempted:
                raise OriginalControlUnavailable()
        try:
            await asyncio.shield(asyncio.wrap_future(self._ready))
        except asyncio.CancelledError:
            raise
        except OriginalControlUnavailable:
            raise
        except BaseException:
            raise OriginalControlUnavailable() from None

    async def refresh(self) -> MonitorResult:
        self._check_process()
        await self.wait_ready()
        result: concurrent.futures.Future[MonitorResult] = concurrent.futures.Future()
        with self._guard:
            if self._stop_requested or self._terminal_error is not None:
                return MonitorResult("stopping")
            now = self._config.clock()
            attempt: CommandAdmissionAttempt = self._config.owner.try_reserve_command(
                footprint=self._config.command_footprint, now=now
            )
            reservation = attempt.reservation
            if reservation is None:
                return MonitorResult(attempt.reason)
            message = _Refresh(reservation, result)
            self._queue.append(message)
            worker = self._worker
            try:
                dispatched = worker is not None and reservation._dispatch_to_thread(
                    worker, now=self._config.clock()
                )
            except BaseException:
                self._queue.remove(message)
                reservation.cancel_before_start()
                raise OriginalControlUnavailable() from None
            if not dispatched:
                self._queue.remove(message)
                reservation.cancel_before_start()
                try:
                    return MonitorResult(self._dispatch_reason(reservation))
                except BaseException:
                    raise OriginalControlUnavailable() from None
            # The record becomes visible only after its pending token is started
            # and linked to this exact worker under both ownership locks.
            try:
                self._guard.notify()
            except BaseException:
                # Publication and start are already committed. Keep the exact
                # payload and charge; the actor's timed control poll will observe
                # it without replay or a second notification attempt.
                pass
        return await asyncio.shield(asyncio.wrap_future(result))

    def diagnostics(self, *, version: int = 1) -> AdmissionDiagnostics | AdmissionDiagnosticsV2:
        self._check_process()
        with self._guard:
            monitor = self._monitor
        if monitor is None:
            raise OriginalControlUnavailable()
        try:
            now = self._config.clock()
            diagnostic = monitor.diagnostic(now=now)
            return diagnostics_response(diagnostic, now=now, version=version)
        except OriginalControlUnavailable:
            raise

    async def aclose(self, timeout: float | None = None) -> None:
        self._check_process()
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("close timeout must be finite and nonnegative")
        with self._guard:
            self._stop_requested = True
            worker = self._worker
            self._guard.notify_all()
            if not self._start_attempted:
                if self._startup.state == "reserved":
                    self._startup.cancel_before_start()
                if self._startup.state == "released":
                    self._mark_no_worker_retired_locked()
                stop_before_start = True
            else:
                stop_before_start = False
            start_uncertain = self._start_uncertain
            no_worker_proven = self._no_worker_proven
        self._config.owner.stop_accepting()
        if stop_before_start:
            if no_worker_proven:
                return
            raise OriginalControlUnavailable()
        if no_worker_proven and worker is None:
            with self._guard:
                self._mark_no_worker_retired_locked()
            return
        if worker is None:
            raise OriginalControlUnavailable()

        async def wait_for_retirement() -> None:
            failure: BaseException | None = None
            if start_uncertain:
                try:
                    await worker.await_result()
                except asyncio.CancelledError:
                    raise
                except BaseException:
                    # A start acknowledgement remains uncertain. Only the exact
                    # worker's positive return and join below can retire its charge.
                    pass
            else:
                try:
                    await worker.await_result()
                except asyncio.CancelledError:
                    raise
                except BaseException as error:
                    failure = error
            while True:
                with self._guard:
                    if self._joined:
                        break
                if worker.join(timeout=0):
                    with self._guard:
                        self._joined = True
                    break
                await asyncio.sleep(0)
            if failure is not None and not start_uncertain:
                raise failure

        if timeout is None:
            await wait_for_retirement()
        else:
            await asyncio.wait_for(wait_for_retirement(), timeout=timeout)

    def _dispatch_reason(self, reservation: CommandReservation) -> MonitorReason:
        owner = self._config.owner
        snapshot = owner.snapshot(now=self._config.clock())
        if not snapshot.accepting:
            return "stopping"
        if reservation._generation != snapshot.generation:
            return "generation_changed"
        return snapshot.reason

    def _worker_completed(self, future: concurrent.futures.Future[None]) -> None:
        if future.cancelled():
            error: BaseException = StorageAdmissionUnavailable()
        else:
            error = future.exception() or StorageAdmissionUnavailable()
        with self._guard:
            if not self._ready.done():
                self._terminal_error = error
                self._ready.set_exception(OriginalControlUnavailable())
            if not self._channel_entered and not storage_close_uncertainty():
                self._session_exited = True
                self._channel_closed = True

    def _fail(self, error: BaseException) -> None:
        with self._guard:
            if self._terminal_error is None:
                self._terminal_error = error
            self._stop_requested = True
            if not self._ready.done():
                self._ready.set_exception(OriginalControlUnavailable())
            self._guard.notify_all()

    def _run_worker(self) -> None:
        self._launch_confirmed.wait()
        with self._guard:
            if self._start_uncertain or self._stop_requested:
                return
        try:
            self._run_control()
        except BaseException as error:
            self._fail(error)
            self._reject_queued(primary_error=error)
            self._config.owner.stop_accepting()
            self._hold_unretired_messages()
            if not self._channel_entered and not storage_close_uncertainty():
                with self._guard:
                    self._session_exited = True
                    self._channel_closed = True
            elif not self._channel_closed:
                threading.Event().wait()
            raise

    def _run_control(self) -> None:
        config = self._config
        root_channel = root_sql_control_channel(
            config.bootstrap,
            config.owner,
            config.control,
            config.certificate,
            trusted_keys=config.trusted_keys,
            clock=config.clock,
        )
        with _witness_exit(root_channel, self._mark_channel_closed) as channel:
            with self._guard:
                self._channel = channel
                self._channel_entered = True
            try:
                with config.owner._guard:
                    control_lease = config.owner._control_lease
                    if (
                        control_lease is None
                        or config.owner._leases.get(control_lease._key) is not control_lease
                    ):
                        raise StorageAdmissionUnavailable()
                with self._guard:
                    self._control_lease = control_lease
                self._run_session(channel)
            except BaseException as error:
                # Resolve startup failure and retire all actor-owned messages
                # before waiting for external started participants.
                self._fail(error)
                self._reject_queued(primary_error=error)
                raise
            finally:
                self._stop_and_drain()

    def _run_session(self, channel: Connection) -> None:
        config = self._config
        with _witness_exit(FundedControlSession(channel), self._mark_session_exited) as session:
            with self._guard:
                self._session_entered = True
            postgres = PostgresPressureSampler(clock=config.clock)
            if config.resource_factory is not None:
                monitor = AdmissionMonitor(
                    config.owner,
                    policy_loader=config.policy_loader,
                    storage_control=config.control,
                    bootstrap=config.bootstrap,
                    postgres=postgres,
                    scope_id=config.scope_id,
                    clock=config.clock,
                    control_session=session,
                    resource_factory=config.resource_factory,
                )
            else:
                monitor = AdmissionMonitor(
                    config.owner,
                    policy_loader=config.policy_loader,
                    storage_control=config.control,
                    bootstrap=config.bootstrap,
                    postgres=postgres,
                    scope_id=config.scope_id,
                    clock=config.clock,
                    control_session=session,
                )
            with self._guard:
                self._monitor = monitor
            monitor.refresh()
            if not self._ready.done():
                self._ready.set_result(None)
            self._serve(monitor)

    def _stop_and_drain(self) -> None:
        monitor = self._monitor
        if monitor is not None:
            monitor.stop_accepting()
        self._config.owner.stop_accepting()
        self._hold_unretired_messages()
        worker = self._worker
        if worker is None or not self._config.owner._wait_for_command_drain(
            self._startup, worker, self._control_lease
        ):
            self._fail(StorageAdmissionUnavailable())
            threading.Event().wait()

    def _mark_session_exited(self) -> None:
        with self._guard:
            self._session_exited = True

    def _mark_channel_closed(self) -> None:
        channel = self._channel
        control_lease = self._control_lease
        owner = self._config.owner
        raw = getattr(channel, "raw", None)
        raw_closed = getattr(raw, "closed", None) is True
        with owner._guard:
            own_lease_released = bool(
                control_lease is not None
                and owner._control_lease is not control_lease
                and owner._leases.get(control_lease._key) is not control_lease
            )
        if not raw_closed or not own_lease_released or storage_close_uncertainty():
            self._fail(StorageAdmissionUnavailable())
            # Context-manager return does not prove native close. Retain the exact
            # worker and startup charge when raw closure or its own lease release
            # cannot be observed. This is a sticky uncertainty hold; it makes no
            # claim of local custody or reconciliation of the native handle.
            threading.Event().wait()
        with self._guard:
            self._channel_closed = True
            if not self._session_entered:
                self._session_exited = True

    def _serve(self, monitor: AdmissionMonitor) -> None:
        next_control_cycle = self._control_cycle_deadline(monitor)
        while True:
            with self._guard:
                periodic = False
                while not self._stop_requested:
                    now = self._config.clock()
                    remaining = next_control_cycle - now
                    if remaining <= 0:
                        periodic = True
                        break
                    if self._queue:
                        break
                    self._guard.wait(timeout=remaining)
                if self._stop_requested:
                    stopping = True
                else:
                    stopping = False
                    message = None if periodic else self._queue.popleft()
                    self._active_refresh = True
            if stopping:
                self._reject_queued()
                return
            if periodic:
                cycle_failure: BaseException | None = None
                try:
                    # The original positive startup reservation remains started
                    # for this worker's lifetime and funds retained control work.
                    monitor.refresh()
                    next_control_cycle = self._control_cycle_deadline(monitor)
                except BaseException as error:
                    cycle_failure = error
                with self._guard:
                    self._active_refresh = False
                if cycle_failure is not None:
                    self._fail(cycle_failure)
                    self._reject_queued()
                    raise cycle_failure
                continue
            if message is None:
                missing_message_failure = OriginalControlUnavailable()
                self._fail(missing_message_failure)
                self._reject_queued()
                raise missing_message_failure
            failure: BaseException | None = None
            retirement_uncertain = False
            result: MonitorResult | None = None
            try:
                result = self._run_refresh(monitor, message)
            except BaseException as error:
                failure = error
            if result is None and failure is None:
                failure = OriginalControlUnavailable()
            # Snapshot/admission share this guard: released cost and the private
            # reply become observable together. Its only callback schedules the
            # shielded waiter; no application callback or SQL runs here.
            with self._config.owner._guard:
                try:
                    finished = message.reservation.finish()
                except BaseException as error:
                    retirement_uncertain = True
                    if failure is None:
                        failure = error
                else:
                    if not finished:
                        retirement_uncertain = True
                        if failure is None:
                            failure = OriginalControlUnavailable()
                if not retirement_uncertain and not message.result.done():
                    if failure is not None:
                        message.result.set_exception(failure)
                    elif result is not None:
                        message.result.set_result(result)
            with self._guard:
                self._active_refresh = False
            if failure is not None:
                if retirement_uncertain:
                    self._remember_unretired_message(message)
                self._fail(failure)
                if not message.result.done():
                    message.result.set_exception(failure)
                self._reject_queued(primary_error=failure)
                raise failure

    def _run_refresh(self, monitor: AdmissionMonitor, message: _Refresh) -> MonitorResult:
        worker = self._worker
        if worker is None or not message.reservation._check_dispatched(
            worker, now=self._config.clock()
        ):
            return MonitorResult(self._dispatch_reason(message.reservation))
        return monitor.refresh()

    def _complete_rejected(self, message: _Refresh, result: MonitorResult) -> None:
        with self._config.owner._guard:
            if not message.reservation.finish():
                raise StorageAdmissionUnavailable()
            if not message.result.done():
                message.result.set_result(result)

    def _remember_unretired_message(self, message: _Refresh) -> None:
        with self._guard:
            if not any(held is message for held in self._unretired_messages):
                self._unretired_messages.append(message)

    def _hold_unretired_messages(self) -> None:
        with self._guard:
            unretired = bool(self._unretired_messages)
            primary = self._terminal_error
        if unretired:
            self._fail(primary or StorageAdmissionUnavailable())
            # Keep the exact worker and every failed message/token strongly
            # reachable until positive retirement is possible; there is no
            # safe cross-thread refund for an actor command.
            threading.Event().wait()

    def _reject_queued(self, *, primary_error: BaseException | None = None) -> None:
        with self._guard:
            queued = tuple(self._queue)
            self._queue.clear()
        first_error: BaseException | None = None
        for message in queued:
            try:
                self._complete_rejected(message, MonitorResult("stopping"))
            except BaseException as error:
                if first_error is None:
                    first_error = error
                try:
                    physically_released = message.reservation.state == "released"
                except BaseException as state_error:
                    physically_released = False
                    if first_error is None:
                        first_error = state_error
                if not physically_released:
                    self._remember_unretired_message(message)
                if not message.result.done():
                    try:
                        message.result.set_exception(primary_error or error)
                    except BaseException as result_error:
                        if first_error is None:
                            first_error = result_error
        if first_error is not None:
            self._fail(primary_error or first_error)

    def _control_cycle_deadline(self, monitor: AdmissionMonitor) -> float:
        interval = monitor._interval
        now = self._config.clock()
        if (
            isinstance(interval, bool)
            or not isinstance(interval, (int, float))
            or not math.isfinite(interval)
            or interval <= 0
            or isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
        ):
            raise OriginalControlUnavailable()
        return float(now + interval)
