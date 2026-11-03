"""Offline lifecycle tests for the original funded control owner."""

from __future__ import annotations

import asyncio
import copy
import inspect
import os
import pickle
import threading
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cli_agent_orchestrator.clients.postgres.connection import FundedSqlCommitUncertain
from cli_agent_orchestrator.models.sql_root_genesis import SignedRootSqlOpening
from cli_agent_orchestrator.services import delivery_admission
from cli_agent_orchestrator.services import original_control_owner as module
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_monitor import MonitorResult
from cli_agent_orchestrator.services.admission_policy import (
    AdmissionPolicy,
    CapacityObservation,
    ResourceAmounts,
)
from cli_agent_orchestrator.services.admission_settings import AdmissionPolicyLoader
from cli_agent_orchestrator.services.delivery_admission import (
    CommandReservation,
    CommandThreadStartUncertain,
    DeliveryAdmission,
    StorageAdmissionUnavailable,
)
from cli_agent_orchestrator.services.original_control_owner import (
    OriginalControlConfig,
    OriginalControlOwner,
    OriginalControlUnavailable,
)

STARTUP = ResourceAmounts(2, 2, 0)
COMMAND = ResourceAmounts(1, 1, 0)


def _owner(memory: int = 200, cpu: int = 200) -> tuple[DeliveryAdmission, object]:
    policy = AdmissionPolicy(
        memory_per_delivery_bytes=1,
        cpu_per_delivery_millicores=1,
        memory_reserve_percent=0,
        cpu_reserve_percent=0,
        max_sample_age_s=5.0,
    )
    owner, control = DeliveryAdmission.with_storage_control(policy)
    owner._budget = ResourceAmounts(memory, cpu, 0)
    accepted = owner.observe(
        CapacityObservation(
            observed_at=1.0,
            memory_available_bytes=memory,
            memory_limit_bytes=max(memory, 1),
            cpu_available_millicores=cpu,
            cpu_limit_millicores=max(cpu, 1),
            storage_available_slots=0,
            coverage="known",
        ),
        now=1.0,
        generation=owner.generation,
    )
    assert accepted
    return owner, control


def _config(
    owner: DeliveryAdmission,
    control: object,
    *,
    clock=lambda: 1.0,
) -> OriginalControlConfig:
    certificate = object.__new__(SignedRootSqlOpening)
    return OriginalControlConfig(
        bootstrap=ResourceBootstrap(),
        owner=owner,
        control=control,
        certificate=certificate,
        trusted_keys={},
        policy_loader=AdmissionPolicyLoader(source=lambda: {}),
        scope_id="unit-test",
        command_footprint=COMMAND,
        clock=clock,
    )


def _reserve(owner: DeliveryAdmission, footprint: ResourceAmounts = STARTUP):
    attempt = owner.try_reserve_command(footprint=footprint, now=1.0)
    assert attempt.reservation is not None
    return attempt.reservation


def _task() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class _Harness:
    def __init__(self) -> None:
        self.guard = threading.Lock()
        self.events: list[tuple[object, ...]] = []
        self.refresh_calls: list[tuple[threading.Thread, object | None, object]] = []
        self.root_calls = 0
        self.session_calls = 0
        self.postgres_calls = 0
        self.monitor_calls = 0
        self.monitor_instances: list[object] = []
        self.call_hooks: dict[int, tuple[object, threading.Event]] = {}
        self.refresh_events: dict[int, object] = {}
        self.errors: dict[int, BaseException] = {}
        self.root_error: BaseException | None = None
        self.owner: DeliveryAdmission | None = None
        self.clock = lambda: 1.0

    def note(self, *entry: object) -> None:
        with self.guard:
            self.events.append(entry)

    def root_context(
        self,
        bootstrap: object,
        owner: DeliveryAdmission,
        control: object,
        certificate: object,
        *,
        trusted_keys: object,
        clock,
    ):
        harness = self

        @contextmanager
        def context():
            if harness.root_error is not None:
                raise harness.root_error
            with harness.guard:
                harness.root_calls += 1
            channel = _Harness.Channel()
            try:
                with control.operation(clock=clock):
                    harness.note("root_enter", threading.current_thread(), _task(), channel)
                    try:
                        yield channel
                    finally:
                        harness.note("root_exit", threading.current_thread(), _task(), channel)
            finally:
                channel.raw.closed = True

        return context()

    class Session:
        def __init__(self, harness: _Harness, channel: object):
            self.harness = harness
            self.channel = channel

        def __enter__(self):
            self.harness.session_calls += 1
            self.harness.note("session_enter", threading.current_thread(), _task(), self.channel)
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.harness.note("session_exit", threading.current_thread(), _task(), self.channel)
            return False

    class Channel:
        def __init__(self):
            self.raw = SimpleNamespace(closed=False)

    class Monitor:
        def __init__(self, harness: _Harness, owner: DeliveryAdmission, **kwargs: object):
            self.harness = harness
            self.owner = owner
            self.session = kwargs["control_session"]
            self.stopped = False
            self._interval = 2.5
            self.publish_on_refresh = False
            self.harness.monitor_instances.append(self)

        def refresh(self) -> MonitorResult:
            with self.harness.guard:
                self.harness.monitor_calls += 1
                call = self.harness.monitor_calls
                self.harness.refresh_calls.append(
                    (threading.current_thread(), _task(), self.session)
                )
            hook = self.harness.call_hooks.get(call)
            if hook is not None:
                entered_callback, release = hook
                entered_callback()
                release.wait()
            error = self.harness.errors.get(call)
            if error is not None:
                raise error
            if self.publish_on_refresh:
                now = self.harness.clock()
                self.owner.observe(
                    CapacityObservation(
                        observed_at=now,
                        memory_available_bytes=200,
                        memory_limit_bytes=200,
                        cpu_available_millicores=200,
                        cpu_limit_millicores=200,
                        storage_available_slots=0,
                        coverage="known",
                    ),
                    now=now,
                    generation=self.owner.generation,
                )
            event = self.harness.refresh_events.get(call)
            if event is not None:
                event()
            return MonitorResult("capacity_available", published=True)

        def diagnostic(self, *, now: float) -> object:
            return SimpleNamespace(
                call=self.harness.monitor_calls,
                at=now,
                ledger=self.owner.diagnostic(now=now),
            )

        def stop_accepting(self) -> None:
            self.stopped = True

        @property
        def retired(self) -> bool:
            return self.stopped

    class Postgres:
        def __init__(self, *, clock):
            return None

    def postgres(self, *, clock):
        self.postgres_calls += 1
        return _Harness.Postgres(clock=clock)

    def install(self, test: unittest.TestCase) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(patch.object(module, "root_sql_control_channel", self.root_context))
        stack.enter_context(
            patch.object(
                module,
                "FundedControlSession",
                lambda channel: _Harness.Session(self, channel),
            )
        )
        stack.enter_context(
            patch.object(
                module,
                "AdmissionMonitor",
                lambda owner, **kwargs: _Harness.Monitor(self, owner, **kwargs),
            )
        )
        stack.enter_context(patch.object(module, "PostgresPressureSampler", self.postgres))
        stack.enter_context(
            patch.object(
                module, "diagnostics_response", lambda value, *, now, version: (version, value)
            )
        )
        test.addCleanup(stack.close)
        return stack


class OriginalControlOwnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.harness = _Harness()
        self.harness.install(self)
        self.services: list[OriginalControlOwner] = []

    def make_service(
        self,
        *,
        clock=lambda: 1.0,
        memory: int = 200,
        cpu: int = 200,
    ) -> OriginalControlOwner:
        owner, control = _owner(memory, cpu)
        self.harness.owner = owner
        self.harness.clock = clock
        service = OriginalControlOwner(
            _config(owner, control, clock=clock), startup=_reserve(owner)
        )
        self.services.append(service)
        return service

    async def close_service(self, service: OriginalControlOwner) -> None:
        await service.aclose(timeout=3.0)

    def test_import_origin_and_closed_typed_surface(self) -> None:
        root = Path(__file__).resolve().parents[2]
        expected = root / "src/cli_agent_orchestrator/services/original_control_owner.py"
        self.assertEqual(Path(module.__file__).resolve(), expected.resolve())
        self.assertFalse(hasattr(OriginalControlOwner, "submit"))
        self.assertEqual(
            tuple(inspect.signature(OriginalControlOwner.refresh).parameters), ("self",)
        )

    async def test_foreign_process_is_rejected_before_actor_locks(self) -> None:
        service = self.make_service()

        class InheritedLock:
            def __enter__(self):
                raise AssertionError("foreign process touched inherited actor lock")

            def __exit__(self, *args):
                return False

        pid = os.getpid()
        with patch.object(service, "_guard", InheritedLock()):
            with patch.object(delivery_admission.os, "getpid", return_value=pid + 1):
                for name, operation in (
                    ("retired", lambda: service.retired),
                    ("start", service.start),
                    ("diagnostics", service.diagnostics),
                ):
                    with self.subTest(method=name), self.assertRaises(OriginalControlUnavailable):
                        operation()
                for name, operation in (
                    ("wait_ready", service.wait_ready),
                    ("refresh", service.refresh),
                    ("aclose", service.aclose),
                ):
                    with self.subTest(method=name), self.assertRaises(OriginalControlUnavailable):
                        await operation()
        await service.aclose(timeout=3.0)

    async def test_controller_cannot_be_copied_into_a_second_facade(self) -> None:
        service = self.make_service()
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.subTest(operation=operation.__name__), self.assertRaises(TypeError):
                operation(service)
        await service.aclose(timeout=3.0)

    def test_constructor_rejects_missing_and_wrong_owner_startup(self) -> None:
        owner, control = _owner()
        with self.assertRaises(OriginalControlUnavailable):
            OriginalControlOwner(_config(owner, control), startup=None)

        other_owner, _ = _owner()
        with self.assertRaises(OriginalControlUnavailable):
            OriginalControlOwner(_config(owner, control), startup=_reserve(other_owner))
        self.assertEqual(self.harness.root_calls, 0)

    def test_context_exit_without_raw_close_witness_cannot_retire_startup(self) -> None:
        owner, control = _owner()
        startup = _reserve(owner)
        service = OriginalControlOwner(_config(owner, control), startup=startup)
        service._channel = SimpleNamespace(raw=SimpleNamespace(closed=False))
        with patch.object(threading.Event, "wait", side_effect=RuntimeError("sticky hold")):
            with self.assertRaisesRegex(RuntimeError, "sticky hold"):
                service._mark_channel_closed()
        self.assertFalse(service._channel_closed)
        self.assertEqual(startup.state, "reserved")
        self.assertEqual(owner.snapshot(now=1.0).command_inflight, 1)

    def test_dispatch_rejects_unstarted_and_already_returned_workers(self) -> None:
        owner, _ = _owner()
        startup = _reserve(owner)
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("not started")):
            with self.assertRaises(CommandThreadStartUncertain) as caught:
                startup._launch_thread(lambda: None, clock=lambda: 1.0)
        unstarted = caught.exception.worker
        message = _reserve(owner, COMMAND)
        self.assertFalse(message._dispatch_to_thread(unstarted, now=1.0))
        self.assertEqual(message.state, "reserved")
        self.assertTrue(message.cancel_before_start())

        returned_owner, _ = _owner()
        returned_startup = _reserve(returned_owner)
        worker = returned_startup._launch_thread(lambda: None, clock=lambda: 1.0)
        self.assertIsNotNone(worker)
        self.assertTrue(worker._returned.wait(timeout=2.0))
        returned_message = _reserve(returned_owner, COMMAND)
        self.assertFalse(returned_message._dispatch_to_thread(worker, now=1.0))
        self.assertEqual(returned_message.state, "reserved")
        self.assertTrue(returned_message.cancel_before_start())
        self.assertTrue(worker.join(timeout=2.0))

    async def test_unknown_and_shared_unready_startup_never_open_sql(self) -> None:
        for shared in (False, True):
            with self.subTest(shared=shared):
                owner, control = _owner()
                startup = _reserve(owner)
                service = OriginalControlOwner(_config(owner, control), startup=startup)
                if shared:
                    owner.require_shared_admission()
                else:
                    owner.invalidate(expected_generation=owner.generation)
                with self.assertRaises(OriginalControlUnavailable):
                    service.start()
                with self.assertRaises(OriginalControlUnavailable):
                    await asyncio.wait_for(service.wait_ready(), timeout=1.0)
                self.assertEqual(self.harness.root_calls, 0)
                self.assertFalse(service.retired)
                await service.aclose(timeout=1.0)
                self.assertTrue(service.retired)

    async def test_close_before_start_is_idempotently_retired_and_prevents_start(self) -> None:
        service = self.make_service()
        await service.aclose(timeout=1.0)
        self.assertTrue(service.retired)
        await service.aclose(timeout=1.0)
        with self.assertRaises(OriginalControlUnavailable):
            service.start()
        self.assertEqual(self.harness.root_calls, 0)

    async def test_launch_clock_failure_proves_no_worker_and_can_close(self) -> None:
        def failed_clock():
            raise ValueError("clock unavailable before native launch")

        for clock in (failed_clock, lambda: float("nan")):
            with self.subTest(clock=clock):
                service = self.make_service(clock=clock)
                with self.assertRaises(OriginalControlUnavailable):
                    service.start()
                self.assertIsNone(service._worker)
                self.assertEqual(service._startup.state, "released")
                await service.aclose(timeout=1.0)
                self.assertTrue(service.retired)
                self.assertEqual(self.harness.root_calls, 0)

    async def test_concurrent_and_repeated_close_share_exact_join_witness(self) -> None:
        service = self.make_service()
        service.start()
        await service.wait_ready()
        closes = await asyncio.gather(
            service.aclose(timeout=2.0),
            service.aclose(timeout=2.0),
            return_exceptions=True,
        )
        self.assertEqual(closes, [None, None])
        self.assertTrue(service.retired)
        await service.aclose(timeout=0.1)
        self.assertTrue(service.retired)

    async def test_initial_monitor_failure_resolves_ready_before_external_drain(self) -> None:
        service = self.make_service()
        owner = service._config.owner
        external = _reserve(owner, COMMAND)
        self.assertTrue(external.start(now=1.0))
        failure = StorageAdmissionUnavailable()
        self.harness.errors[1] = failure
        loop = asyncio.get_running_loop()
        draining = asyncio.Event()
        original_drain = owner._wait_for_command_drain

        def observe_drain(startup, worker, control_lease):
            loop.call_soon_threadsafe(draining.set)
            return original_drain(startup, worker, control_lease)

        with patch.object(owner, "_wait_for_command_drain", observe_drain):
            service.start()
            try:
                await asyncio.wait_for(draining.wait(), timeout=2.0)
                with self.assertRaises(OriginalControlUnavailable):
                    await asyncio.wait_for(service.wait_ready(), timeout=0.5)
                with self.assertRaises(TimeoutError):
                    await service.aclose(timeout=0.01)
                self.assertFalse(service.retired)
                self.assertEqual(owner.snapshot(now=1.0).command_inflight, 2)
            finally:
                self.assertTrue(external.finish())
                try:
                    await service.aclose(timeout=2.0)
                except StorageAdmissionUnavailable:
                    pass
        self.assertTrue(service.retired)
        self.assertTrue(any(entry[0] == "root_exit" for entry in self.harness.events))

    async def test_actor_loop_clock_error_rejects_all_messages_before_drain(self) -> None:
        loop = asyncio.get_running_loop()
        fail_clock = threading.Event()
        service_ref: dict[str, OriginalControlOwner] = {}
        clock_error = RuntimeError("actor clock failed")

        def clock() -> float:
            service = service_ref.get("service")
            if (
                fail_clock.is_set()
                and service is not None
                and threading.current_thread() is service._worker._thread
            ):
                raise clock_error
            return 1.0

        service = self.make_service(clock=clock)
        service_ref["service"] = service
        service.start()
        await service.wait_ready()
        entered = asyncio.Event()
        release = threading.Event()
        queued = asyncio.Event()
        self.harness.call_hooks[2] = (
            lambda: loop.call_soon_threadsafe(entered.set),
            release,
        )
        original_dispatch = CommandReservation._dispatch_to_thread
        dispatches = 1  # The active request was dispatched before instrumentation.

        def tracked_dispatch(reservation, worker, *, now):
            nonlocal dispatches
            accepted = original_dispatch(reservation, worker, now=now)
            if accepted:
                dispatches += 1
                if dispatches == 3:
                    loop.call_soon_threadsafe(queued.set)
            return accepted

        drain_seen = asyncio.Event()
        bad_drain = asyncio.Event()
        drained_reservations: list[CommandReservation] = []
        original_drain = service._config.owner._wait_for_command_drain

        def verify_drain(startup, worker, control_lease):
            with service._guard:
                empty = not service._queue
            released = all(reservation.state == "released" for reservation in drained_reservations)
            loop.call_soon_threadsafe(drain_seen.set)
            if not empty or not released:
                loop.call_soon_threadsafe(bad_drain.set)
                raise AssertionError("external drain began before actor messages retired")
            return original_drain(startup, worker, control_lease)

        active = asyncio.create_task(service.refresh())
        queued_requests: list[asyncio.Task[MonitorResult]] = []
        try:
            await asyncio.wait_for(entered.wait(), timeout=2.0)
            with patch.object(CommandReservation, "_dispatch_to_thread", tracked_dispatch):
                queued_requests = [
                    asyncio.create_task(service.refresh()),
                    asyncio.create_task(service.refresh()),
                ]
                await asyncio.wait_for(queued.wait(), timeout=2.0)
            with service._guard:
                messages = tuple(service._queue)
            drained_reservations.extend(message.reservation for message in messages)
            self.assertEqual(len(drained_reservations), 2)
            with patch.object(service._config.owner, "_wait_for_command_drain", verify_drain):
                fail_clock.set()
                release.set()
                self.assertEqual((await active).reason, "capacity_available")
                results = await asyncio.gather(*queued_requests)
                self.assertTrue(all(result.reason == "stopping" for result in results))
                await asyncio.wait_for(drain_seen.wait(), timeout=2.0)
                self.assertFalse(bad_drain.is_set())
                self.assertIs(service._terminal_error, clock_error)
                with self.assertRaises(RuntimeError) as caught:
                    await service.aclose(timeout=2.0)
                self.assertIs(caught.exception, clock_error)
        finally:
            release.set()
            if queued_requests:
                for request in queued_requests:
                    if not request.done():
                        request.cancel()
                await asyncio.gather(*queued_requests, return_exceptions=True)
            if not service.retired:
                try:
                    await service.aclose(timeout=2.0)
                except BaseException:
                    pass

    async def test_rejection_continues_after_unretirable_message_and_retains_it(self) -> None:
        loop = asyncio.get_running_loop()
        fail_clock = threading.Event()
        service_ref: dict[str, OriginalControlOwner] = {}
        clock_error = RuntimeError("actor clock failed")

        def clock() -> float:
            service = service_ref.get("service")
            if (
                fail_clock.is_set()
                and service is not None
                and threading.current_thread() is service._worker._thread
            ):
                raise clock_error
            return 1.0

        service = self.make_service(clock=clock)
        service_ref["service"] = service
        service.start()
        await service.wait_ready()
        entered = asyncio.Event()
        release = threading.Event()
        queued = asyncio.Event()
        self.harness.call_hooks[2] = (
            lambda: loop.call_soon_threadsafe(entered.set),
            release,
        )
        original_dispatch = CommandReservation._dispatch_to_thread
        dispatches = 1  # The active request was dispatched before instrumentation.

        def tracked_dispatch(reservation, worker, *, now):
            nonlocal dispatches
            accepted = original_dispatch(reservation, worker, now=now)
            if accepted:
                dispatches += 1
                if dispatches == 3:
                    loop.call_soon_threadsafe(queued.set)
            return accepted

        active = asyncio.create_task(service.refresh())
        queued_requests: list[asyncio.Task[MonitorResult]] = []
        hold_seen = asyncio.Event()
        drain_seen = threading.Event()
        original_finish = CommandReservation.finish

        def fail_one_finish(reservation):
            if reservation is failed_reservation[0]:
                raise RuntimeError("accepted message retirement is uncertain")
            return original_finish(reservation)

        failed_reservation: list[CommandReservation] = []

        def hold_uncertain(actor) -> None:
            loop.call_soon_threadsafe(hold_seen.set)
            raise RuntimeError("test observed sticky message retention")

        def unexpected_drain(startup, worker, control_lease):
            drain_seen.set()
            raise AssertionError("actor drained external work with an unretired payload")

        try:
            await asyncio.wait_for(entered.wait(), timeout=2.0)
            with patch.object(CommandReservation, "_dispatch_to_thread", tracked_dispatch):
                queued_requests = [
                    asyncio.create_task(service.refresh()),
                    asyncio.create_task(service.refresh()),
                ]
                await asyncio.wait_for(queued.wait(), timeout=2.0)
            with service._guard:
                messages = tuple(service._queue)
            failed_reservation.append(messages[0].reservation)
            self.assertEqual(len(messages), 2)
            with (
                patch.object(CommandReservation, "finish", fail_one_finish),
                patch.object(
                    OriginalControlOwner, "_hold_unretired_messages", hold_uncertain, create=True
                ),
                patch.object(service._config.owner, "_wait_for_command_drain", unexpected_drain),
            ):
                fail_clock.set()
                release.set()
                self.assertEqual((await active).reason, "capacity_available")
                with self.assertRaises(RuntimeError) as first_error:
                    await asyncio.wait_for(queued_requests[0], timeout=2.0)
                self.assertIs(first_error.exception, clock_error)
                self.assertEqual((await queued_requests[1]).reason, "stopping")
                await asyncio.wait_for(hold_seen.wait(), timeout=2.0)
                self.assertFalse(drain_seen.is_set())
                self.assertEqual(len(service._unretired_messages), 1)
                self.assertIs(service._unretired_messages[0].reservation, failed_reservation[0])
                self.assertEqual(failed_reservation[0].state, "started")
                with self.assertRaises(RuntimeError):
                    await service.aclose(timeout=2.0)
                self.assertEqual(service._config.owner.snapshot(now=1.0).command_inflight, 1)
                self.assertFalse(service.retired)
        finally:
            release.set()
            if queued_requests:
                for request in queued_requests:
                    if not request.done():
                        request.cancel()
                await asyncio.gather(*queued_requests, return_exceptions=True)
            if not service._joined:
                try:
                    await service.aclose(timeout=2.0)
                except BaseException:
                    pass

    async def test_start_failure_and_preentry_rejection_resolve_readiness(self) -> None:
        service = self.make_service()
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("unknown start")):
            with self.assertRaises(OriginalControlUnavailable):
                service.start()
        self.assertIsNotNone(service._worker)
        self.assertFalse(service.retired)
        with self.assertRaises(OriginalControlUnavailable):
            await asyncio.wait_for(service.wait_ready(), timeout=1.0)
        self.assertEqual(self.harness.root_calls, 0)
        with self.assertRaises(TimeoutError):
            await service.aclose(timeout=0.01)
        self.assertEqual(service._config.owner.snapshot(now=1.0).command_inflight, 1)
        self.assertFalse(service._worker._thread.is_alive())

        owner, control = _owner()
        now = [1.0]
        pending = _reserve(owner)
        rejected = OriginalControlOwner(
            _config(owner, control, clock=lambda: now[0]), startup=pending
        )
        original_start = threading.Thread.start

        def invalidate_before_entry(thread: threading.Thread) -> None:
            now[0] = 7.0
            original_start(thread)

        with patch.object(threading.Thread, "start", invalidate_before_entry):
            rejected.start()
        with self.assertRaises(OriginalControlUnavailable):
            await asyncio.wait_for(rejected.wait_ready(), timeout=2.0)
        self.assertEqual(self.harness.root_calls, 0)
        self.assertFalse(rejected.retired)
        self.assertEqual(owner.snapshot(now=now[0]).command_inflight, 1)
        with self.assertRaises(StorageAdmissionUnavailable):
            await rejected.aclose(timeout=2.0)
        self.assertTrue(rejected.retired)

    async def test_start_ack_lost_after_native_entry_is_aborted_and_joined(self) -> None:
        service = self.make_service()
        original_start = threading.Thread.start
        attempted_threads: list[threading.Thread] = []

        def start_then_lose_ack(thread: threading.Thread) -> None:
            attempted_threads.append(thread)
            original_start(thread)
            raise RuntimeError("native thread started but acknowledgement was lost")

        worker = None
        with patch.object(OriginalControlOwner, "_run_control", lambda actor: None):
            try:
                with patch.object(threading.Thread, "start", start_then_lose_ack):
                    with self.assertRaises(OriginalControlUnavailable):
                        service.start()
                worker = service._worker
                self.assertIsNotNone(worker)
                self.assertEqual(attempted_threads, [worker._thread])
                self.assertEqual(self.harness.root_calls, 0)
                with self.assertRaises(OriginalControlUnavailable):
                    service.start()
                self.assertEqual(attempted_threads, [worker._thread])
                await service.aclose(timeout=2.0)
                self.assertTrue(worker._returned.is_set())
                self.assertTrue(service.retired)
                self.assertEqual(service._config.owner.snapshot(now=1.0).command_inflight, 0)
                self.assertEqual(self.harness.root_calls, 0)
            finally:
                if worker is None:
                    worker = service._worker
                if worker is not None and worker._thread.ident is not None:
                    service._launch_confirmed.set()
                    worker._future.result(timeout=2.0)
                    if not service._joined:
                        self.assertTrue(worker.join(timeout=2.0))

    async def test_one_worker_session_and_monitor_handle_more_than_twenty_messages(self) -> None:
        service = self.make_service(memory=100, cpu=100)
        service.start()
        await asyncio.wait_for(service.wait_ready(), timeout=2.0)

        for _ in range(25):
            result = await service.refresh()
            self.assertEqual(result.reason, "capacity_available")
        results = await asyncio.gather(*(service.refresh() for _ in range(25)))
        self.assertTrue(all(result.reason == "capacity_available" for result in results))
        self.assertEqual(self.harness.root_calls, 1)
        self.assertEqual(self.harness.session_calls, 1)
        self.assertEqual(self.harness.postgres_calls, 1)
        self.assertEqual(len(self.harness.monitor_instances), 1)
        self.assertEqual(len(self.harness.refresh_calls), 51)
        worker = service._worker
        self.assertIsNotNone(worker)
        self.assertTrue(
            all(
                thread is worker._thread and task is None
                for thread, task, _ in self.harness.refresh_calls
            )
        )
        self.assertTrue(
            all(
                session is self.harness.monitor_instances[0].session
                for _, _, session in self.harness.refresh_calls
            )
        )
        self.assertEqual(service.diagnostics(version=2)[0], 2)
        self.assertEqual(service._config.owner.snapshot(now=1.0).command_inflight, 1)
        await self.close_service(service)
        self.assertTrue(service.retired)

    async def test_cancelled_waiter_and_invalidation_do_not_refund_started_message(self) -> None:
        service = self.make_service()
        service.start()
        await service.wait_ready()
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        finished = asyncio.Event()
        release = threading.Event()
        self.harness.call_hooks[2] = (lambda: loop.call_soon_threadsafe(entered.set), release)
        original_finish = CommandReservation.finish

        def tracked_finish(reservation):
            completed = original_finish(reservation)
            if reservation._actor_thread is service._worker:
                loop.call_soon_threadsafe(finished.set)
            return completed

        try:
            with patch.object(CommandReservation, "finish", tracked_finish):
                request = asyncio.create_task(service.refresh())
                await asyncio.wait_for(entered.wait(), timeout=2.0)
                self.assertEqual(service._config.owner.snapshot(now=1.0).command_inflight, 2)
                self.assertTrue(
                    service._config.owner.invalidate(
                        expected_generation=service._config.owner.generation
                    )
                )
                self.assertEqual(service._config.owner.snapshot(now=1.0).command_inflight, 2)
                request.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await request
                release.set()
                next_result = await service.refresh()
                self.assertIn(next_result.reason, {"unknown_capacity", "capacity_available"})
                await asyncio.wait_for(finished.wait(), timeout=2.0)
                self.assertEqual(service._config.owner.snapshot(now=1.0).command_inflight, 1)
        finally:
            release.set()
        await self.close_service(service)

    async def test_generation_staleness_and_capacity_are_rechecked_before_sql(self) -> None:
        for action in ("generation", "stale", "capacity"):
            with self.subTest(action=action):
                now = [1.0]
                service = self.make_service(clock=lambda: now[0])
                service.start()
                await service.wait_ready()
                calls_before = self.harness.monitor_calls
                owner = service._config.owner
                loop = asyncio.get_running_loop()
                checked = asyncio.Event()
                release = threading.Event()
                original = OriginalControlOwner._run_refresh

                def gated(actor, monitor, message):
                    loop.call_soon_threadsafe(checked.set)
                    release.wait()
                    return original(actor, monitor, message)

                with patch.object(OriginalControlOwner, "_run_refresh", gated):
                    request = asyncio.create_task(service.refresh())
                    try:
                        await asyncio.wait_for(checked.wait(), timeout=2.0)
                        if action == "generation":
                            owner.invalidate(expected_generation=owner.generation)
                        elif action == "stale":
                            now[0] = 7.0
                        else:
                            self.assertTrue(
                                owner.observe(
                                    CapacityObservation(
                                        observed_at=2.0,
                                        memory_available_bytes=0,
                                        memory_limit_bytes=1,
                                        cpu_available_millicores=0,
                                        cpu_limit_millicores=1,
                                        storage_available_slots=0,
                                        coverage="known",
                                    ),
                                    now=2.0,
                                    generation=owner.generation,
                                )
                            )
                        release.set()
                        await request
                    finally:
                        release.set()
                # Staleness may trigger one retained cycle; this command never enters monitor SQL.
                expected_calls = calls_before + int(action == "stale")
                self.assertEqual(self.harness.monitor_calls, expected_calls)
                await self.close_service(service)

    async def test_terminal_monitor_error_is_not_replayed(self) -> None:
        service = self.make_service()
        self.harness.errors[2] = StorageAdmissionUnavailable()
        service.start()
        await service.wait_ready()
        with self.assertRaises(StorageAdmissionUnavailable):
            await service.refresh()
        result = await service.refresh()
        self.assertEqual(result.reason, "stopping")
        self.assertEqual(self.harness.monitor_calls, 2)
        with self.assertRaises(StorageAdmissionUnavailable):
            await service.aclose(timeout=2.0)
        self.assertEqual(self.harness.root_calls, 1)
        self.assertTrue(any(entry[0] == "root_exit" for entry in self.harness.events))

    async def test_periodic_control_cycle_recovers_stale_capacity_using_startup_charge(
        self,
    ) -> None:
        now = [1.0]
        service = self.make_service(clock=lambda: now[0])
        service.start()
        await service.wait_ready()
        owner = service._config.owner
        monitor = self.harness.monitor_instances[0]
        monitor.publish_on_refresh = True
        now[0] = 7.0
        self.assertEqual(owner.snapshot(now=now[0]).reason, "stale_observation")
        self.assertIsNone(owner.try_reserve_command(footprint=COMMAND, now=now[0]).reservation)
        loop = asyncio.get_running_loop()
        refreshed = asyncio.Event()
        self.harness.refresh_events[2] = lambda: loop.call_soon_threadsafe(refreshed.set)
        with service._guard:
            service._guard.notify_all()
        try:
            await asyncio.wait_for(refreshed.wait(), timeout=2.0)
            snapshot = owner.snapshot(now=now[0])
            self.assertEqual(snapshot.sample_age_s, 0.0)
            self.assertIn(snapshot.reason, {"capacity_available", "resource_exhausted"})
            self.assertEqual(snapshot.command_inflight, 1)
            self.assertEqual(snapshot.command_started, 1)
            self.assertEqual(self.harness.monitor_calls, 2)

            self.assertTrue(owner.invalidate(expected_generation=owner.generation))
            self.assertEqual(owner.snapshot(now=now[0]).reason, "unknown_capacity")
            now[0] = 9.5
            recovered = asyncio.Event()
            self.harness.refresh_events[3] = lambda: loop.call_soon_threadsafe(recovered.set)
            with service._guard:
                service._guard.notify_all()
            await asyncio.wait_for(recovered.wait(), timeout=2.0)
            snapshot = owner.snapshot(now=now[0])
            self.assertEqual(snapshot.sample_age_s, 0.0)
            self.assertIn(snapshot.reason, {"capacity_available", "resource_exhausted"})
            self.assertEqual(snapshot.command_inflight, 1)
            self.assertEqual(snapshot.command_started, 1)
            self.assertEqual(self.harness.monitor_calls, 3)
        finally:
            await self.close_service(service)

    async def test_diagnostics_reads_live_ledger_during_and_after_work(self) -> None:
        service = self.make_service()
        service.start()
        await service.wait_ready()
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release = threading.Event()
        self.harness.call_hooks[2] = (
            lambda: loop.call_soon_threadsafe(entered.set),
            release,
        )
        request = asyncio.create_task(service.refresh())
        try:
            await asyncio.wait_for(entered.wait(), timeout=2.0)
            calls = self.harness.monitor_calls
            during = service.diagnostics(version=2)[1]
            self.assertEqual(during.ledger.snapshot.command_inflight, 2)
            self.assertEqual(self.harness.monitor_calls, calls)
        finally:
            release.set()
            await request
            await self.close_service(service)
        after = service.diagnostics(version=2)[1]
        self.assertEqual(after.ledger.snapshot.command_inflight, 0)
        self.assertFalse(after.ledger.snapshot.accepting)

    async def test_commit_uncertainty_is_preserved_for_active_request(self) -> None:
        service = self.make_service()
        sentinel = FundedSqlCommitUncertain("funded SQL commit is uncertain")
        self.harness.errors[2] = sentinel
        service.start()
        await service.wait_ready()
        try:
            with self.assertRaises(FundedSqlCommitUncertain) as caught:
                await service.refresh()
            self.assertIs(caught.exception, sentinel)
            self.assertEqual(self.harness.monitor_calls, 2)
            self.assertEqual((await service.refresh()).reason, "stopping")
            with self.assertRaises(FundedSqlCommitUncertain):
                await service.aclose(timeout=2.0)
            self.assertEqual(self.harness.monitor_calls, 2)
        finally:
            if not service.retired:
                try:
                    await service.aclose(timeout=2.0)
                except BaseException:
                    pass

    async def test_snapshot_cannot_observe_retired_command_with_pending_reply(self) -> None:
        service = self.make_service()
        service.start()
        await service.wait_ready()
        original_finish = CommandReservation.finish
        original_message = module._Refresh
        messages = []
        observers = []
        observed_ready = []
        observed_before_publish = []

        def capture_message(*args):
            message = original_message(*args)
            messages.append(message)
            return message

        def finish(reservation):
            result = original_finish(reservation)
            if reservation is not service._startup:
                observed = threading.Event()

                def observe():
                    service._config.owner.snapshot(now=1.0)
                    observed_ready.append(messages[0].result.done())
                    observed.set()

                observer = threading.Thread(target=observe)
                observers.append(observer)
                observer.start()
                observed_before_publish.append(observed.wait(0.1))
            return result

        try:
            with patch.object(module, "_Refresh", side_effect=capture_message):
                with patch.object(CommandReservation, "finish", finish):
                    self.assertTrue((await service.refresh()).published)
            for observer in observers:
                observer.join(timeout=2.0)
                self.assertFalse(observer.is_alive())
            self.assertEqual(observed_before_publish, [False])
            self.assertEqual(observed_ready, [True])
        finally:
            await service.aclose(timeout=2.0)

    async def test_dispatch_reason_failure_keeps_single_cleanup_and_fixed_error(self) -> None:
        service = self.make_service()
        service.start()
        await service.wait_ready()
        try:
            with patch.object(CommandReservation, "_dispatch_to_thread", return_value=False):
                with patch.object(service, "_dispatch_reason", side_effect=ValueError("clock")):
                    with self.assertRaises(OriginalControlUnavailable):
                        await service.refresh()
            self.assertFalse(service._queue)
            self.assertEqual(service._config.owner.snapshot(now=1.0).command_inflight, 1)
        finally:
            await service.aclose(timeout=2.0)

    async def test_notify_failure_keeps_published_request_owned_until_reply(self) -> None:
        now = [1.0]
        service = self.make_service(clock=lambda: now[0])
        service.start()
        await service.wait_ready()
        owner = service._config.owner
        notification_failed = asyncio.Event()
        loop = asyncio.get_running_loop()
        original_notify = service._guard.notify
        first = True

        def fail_first_notification(n: int = 1) -> None:
            nonlocal first
            if first:
                first = False
                loop.call_soon_threadsafe(notification_failed.set)
                raise RuntimeError("notification failed after publication")
            original_notify(n)

        request = None
        try:
            with patch.object(service._guard, "notify", side_effect=fail_first_notification):
                request = asyncio.create_task(service.refresh())
                await asyncio.wait_for(notification_failed.wait(), timeout=2.0)
                self.assertFalse(request.done())
                self.assertEqual(owner.snapshot(now=now[0]).command_inflight, 2)
                request_call = asyncio.Event()
                self.harness.refresh_events[3] = lambda: loop.call_soon_threadsafe(request_call.set)
                now[0] = 3.5
                with service._guard:
                    service._guard.notify_all()
                await asyncio.wait_for(request_call.wait(), timeout=2.0)
                result = await asyncio.wait_for(request, timeout=2.0)
            self.assertEqual(result.reason, "capacity_available")
            self.assertEqual(self.harness.monitor_calls, 3)
            self.assertEqual(owner.snapshot(now=now[0]).command_inflight, 1)
        finally:
            if request is not None and not request.done():
                request.cancel()
            await self.close_service(service)

    async def test_close_timeout_and_cancellation_retain_owner_until_external_work_finishes(
        self,
    ) -> None:
        service = self.make_service()
        service.start()
        await service.wait_ready()
        owner = service._config.owner
        external = _reserve(owner, COMMAND)
        self.assertTrue(external.start(now=1.0))
        loop = asyncio.get_running_loop()
        waiting = asyncio.Event()
        caller_waiting = asyncio.Event()
        original = owner._wait_for_command_drain
        original_await_result = delivery_admission.CommandThread.await_result

        def observe_drain(startup, worker, control_lease):
            loop.call_soon_threadsafe(waiting.set)
            return original(startup, worker, control_lease)

        async def observe_result(worker):
            if worker is service._worker:
                loop.call_soon_threadsafe(caller_waiting.set)
            return await original_await_result(worker)

        with (
            patch.object(owner, "_wait_for_command_drain", observe_drain),
            patch.object(delivery_admission.CommandThread, "await_result", observe_result),
        ):
            with self.assertRaises(TimeoutError):
                await service.aclose(timeout=0.0)
            await asyncio.wait_for(waiting.wait(), timeout=2.0)
            self.assertFalse(service.retired)
            self.assertFalse(any(entry[0] == "root_exit" for entry in self.harness.events))
            self.assertEqual(owner.snapshot(now=1.0).command_inflight, 2)
            closing = asyncio.create_task(service.aclose())
            await asyncio.wait_for(caller_waiting.wait(), timeout=2.0)
            closing.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await closing
        self.assertFalse(service.retired)
        self.assertFalse(any(entry[0] == "root_exit" for entry in self.harness.events))
        self.assertTrue(external.finish())
        await self.close_service(service)
        self.assertTrue(service.retired)


if __name__ == "__main__":
    unittest.main()
