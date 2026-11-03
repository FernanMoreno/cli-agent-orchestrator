"""Offline resource-lifecycle tests for trusted command reservations."""

import asyncio
import copy
import os
import pickle
import queue
import select
import signal
import threading
import unittest
from pathlib import Path
from typing import TypeVar

from cli_agent_orchestrator.clients.storage_admission import (
    retain_storage_close,
    retry_storage_closes,
)
from cli_agent_orchestrator.services import delivery_admission
from cli_agent_orchestrator.services.admission_policy import (
    AdmissionPolicy,
    CapacityObservation,
    Coverage,
    ResourceAmounts,
)

_T = TypeVar("_T")


def _required(value: _T | None) -> _T:
    if value is None:
        raise AssertionError("expected a reservation")
    return value


def _policy(*, memory_per_delivery: int = 4, cpu_per_delivery: int = 4) -> AdmissionPolicy:
    return AdmissionPolicy(
        memory_per_delivery_bytes=memory_per_delivery,
        cpu_per_delivery_millicores=cpu_per_delivery,
        memory_reserve_percent=0,
        cpu_reserve_percent=0,
        max_sample_age_s=2.0,
    )


def _observation(
    *,
    at: float,
    memory: int,
    cpu: int,
    storage: int,
    coverage: Coverage = "known",
) -> CapacityObservation:
    return CapacityObservation(
        observed_at=at,
        memory_available_bytes=memory,
        memory_limit_bytes=max(memory, 1),
        cpu_available_millicores=cpu,
        cpu_limit_millicores=max(cpu, 1),
        storage_available_slots=storage,
        coverage=coverage,
    )


def _owner(
    *,
    memory: int = 40,
    cpu: int = 40,
    storage: int = 10,
    policy: AdmissionPolicy | None = None,
    observe: bool = True,
    coverage: Coverage = "known",
) -> delivery_admission.DeliveryAdmission:
    admission_policy = policy or _policy()
    owner = delivery_admission.DeliveryAdmission(
        admission_policy,
        budget=ResourceAmounts(memory, cpu, storage),
    )
    if observe:
        accepted = owner.observe(
            _observation(
                at=1.0,
                memory=memory,
                cpu=cpu,
                storage=storage,
                coverage=coverage,
            ),
            now=1.0,
            generation=owner.generation,
        )
        if coverage == "known":
            assert accepted
    return owner


def _command(
    owner: delivery_admission.DeliveryAdmission,
    footprint: ResourceAmounts = ResourceAmounts(1, 1, 0),
    *,
    now: float = 1.0,
) -> delivery_admission.CommandAdmissionAttempt:
    return owner.try_reserve_command(footprint=footprint, now=now)


class CommandReservationTests(unittest.TestCase):
    def test_import_origin_is_this_checkout(self) -> None:
        root = Path(__file__).resolve().parents[2]
        expected = root / "src/cli_agent_orchestrator/services/delivery_admission.py"
        self.assertEqual(Path(delivery_admission.__file__).resolve(), expected.resolve())

    def test_snapshot_additions_default_for_existing_constructors(self) -> None:
        snapshot = delivery_admission.AdmissionSnapshot(
            accepting=True,
            generation=0,
            inflight=0,
            reserved=0,
            started=0,
            limit=0,
            available_slots=0,
            debt=0,
            reason="unknown_capacity",
            sample_age_s=None,
            committed=ResourceAmounts(),
            resource_debt=ResourceAmounts(),
        )
        self.assertEqual(snapshot.command_inflight, 0)
        self.assertEqual(snapshot.command_started, 0)

    def test_custom_command_cost_uses_resource_quantities_not_delivery_units(self) -> None:
        owner = _owner(
            memory=32,
            cpu=32,
            storage=0,
            policy=_policy(memory_per_delivery=100, cpu_per_delivery=100),
        )
        self.assertEqual(owner.snapshot(now=1.0).available_slots, 0)

        reservations = []
        for _ in range(25):
            attempt = _command(owner)
            self.assertEqual(attempt.reason, "capacity_available")
            self.assertIsNotNone(attempt.reservation)
            reservations.append(attempt.reservation)

        exhausted = _command(owner, ResourceAmounts(8, 8, 0))
        self.assertIsNone(exhausted.reservation)
        self.assertEqual(exhausted.reason, "resource_exhausted")
        snapshot = owner.snapshot(now=1.0)
        self.assertEqual(snapshot.command_inflight, 25)
        self.assertEqual(snapshot.command_started, 0)
        self.assertEqual(snapshot.inflight, 25)
        self.assertEqual(snapshot.reserved, 25)
        self.assertEqual(snapshot.delivery_inflight, 0)

        for reservation in reservations:
            self.assertTrue(_required(reservation).start(now=1.0))
        snapshot = owner.snapshot(now=1.0)
        self.assertEqual(snapshot.command_started, 25)
        self.assertEqual(snapshot.started, 25)
        self.assertEqual(snapshot.delivery_started, 0)
        for reservation in reservations:
            self.assertTrue(_required(reservation).finish())

    def test_component_exhaustion_is_checked_independently(self) -> None:
        owner = _owner(memory=10, cpu=4, storage=10)
        first = _command(owner, ResourceAmounts(2, 3, 0))
        self.assertIsNotNone(first.reservation)
        cpu_short = _command(owner, ResourceAmounts(1, 2, 0))
        self.assertIsNone(cpu_short.reservation)
        self.assertEqual(cpu_short.reason, "resource_exhausted")
        self.assertTrue(_required(first.reservation).cancel_before_start())

        full_memory = _command(owner, ResourceAmounts(10, 1, 0))
        self.assertIsNotNone(full_memory.reservation)
        memory_short = _command(owner, ResourceAmounts(1, 1, 0))
        self.assertIsNone(memory_short.reservation)
        self.assertEqual(memory_short.reason, "resource_exhausted")
        self.assertTrue(_required(full_memory.reservation).cancel_before_start())

    def test_footprint_requires_exact_positive_ram_cpu_and_zero_storage(self) -> None:
        owner = _owner()
        with self.assertRaises(ValueError):
            _command(owner, ResourceAmounts(1, 1, 1))
        with self.assertRaises(ValueError):
            _command(owner, ResourceAmounts(0, 1, 0))
        with self.assertRaises(ValueError):
            _command(owner, ResourceAmounts(1, 0, 0))

        class DerivedAmounts(ResourceAmounts):
            pass

        with self.assertRaises(ValueError):
            _command(owner, DerivedAmounts(1, 1, 0))

    def test_missing_stale_future_partial_and_shared_not_ready_are_denied(self) -> None:
        missing = _owner(observe=False)
        self.assertEqual(_command(missing).reason, "unknown_capacity")

        fresh = _owner()
        self.assertEqual(_command(fresh, now=4.0).reason, "stale_observation")
        self.assertEqual(_command(fresh, now=0.5).reason, "future_observation")

        partial = _owner(coverage="partial")
        self.assertEqual(_command(partial).reason, "unknown_capacity")
        unknown_coverage = _owner(coverage="unknown")
        self.assertEqual(_command(unknown_coverage).reason, "unknown_capacity")

        not_ready = _owner()
        not_ready.require_shared_admission()
        shared_attempt = _command(not_ready)
        self.assertIsNone(shared_attempt.reservation)
        self.assertEqual(shared_attempt.reason, "unknown_capacity")

        fenced_owner = _owner()
        fenced_command = _command(fenced_owner)
        self.assertIsNotNone(fenced_command.reservation)
        fenced_owner.require_shared_admission()
        self.assertFalse(_required(fenced_command.reservation).start(now=1.0))
        self.assertTrue(_required(fenced_command.reservation).cancel_before_start())

        stopping = _owner()
        stopping.stop_accepting()
        stopped_attempt = _command(stopping)
        self.assertIsNone(stopped_attempt.reservation)
        self.assertEqual(stopped_attempt.reason, "stopping")

    def test_close_uncertainty_denies_until_exact_close_is_witnessed(self) -> None:
        owner = _owner()
        identity = object()
        closed = {"value": False}
        retain_storage_close(
            identity,
            close=None,
            closed=lambda: closed["value"],
            loan=None,
        )
        try:
            attempt = _command(owner)
            self.assertIsNone(attempt.reservation)
            self.assertEqual(attempt.reason, "unknown_capacity")
        finally:
            closed["value"] = True
            self.assertEqual(retry_storage_closes().pending, 0)
        self.assertIsNotNone(_command(owner).reservation)

    def test_storage_debt_does_not_block_zero_storage_command_or_yield_it(self) -> None:
        owner = _owner(memory=20, cpu=20, storage=1)
        storage = owner.try_reserve_storage(now=1.0)
        self.assertIsNotNone(storage.reservation)
        storage_reservation = _required(storage.reservation)
        self.assertTrue(storage_reservation.start())

        accepted = owner.observe(
            _observation(at=1.5, memory=20, cpu=20, storage=0),
            now=1.5,
            generation=owner.generation,
        )
        self.assertTrue(accepted)
        self.assertEqual(owner.snapshot(now=1.5).resource_debt, ResourceAmounts(0, 0, 1))
        self.assertEqual(owner.snapshot(now=1.5).available_slots, 0)

        attempt = _command(owner, now=1.5)
        self.assertEqual(attempt.reason, "capacity_available")
        self.assertIsNotNone(attempt.reservation)
        command = _required(attempt.reservation)
        self.assertTrue(command.start(now=1.5))

        self.assertTrue(
            owner.observe(
                _observation(at=1.75, memory=20, cpu=20, storage=0),
                now=1.75,
                generation=owner.generation,
            )
        )
        self.assertEqual(command.state, "started")
        self.assertEqual(owner.snapshot(now=1.75).resource_debt, ResourceAmounts(0, 0, 1))
        self.assertTrue(command.finish())
        self.assertTrue(storage_reservation.finish())

    def test_command_and_delivery_reservations_compete_for_one_budget(self) -> None:
        owner = _owner(memory=10, cpu=10, storage=3)
        data = owner.try_reserve("alice", now=1.0)
        self.assertIsNotNone(data.reservation)
        too_large = _command(owner, ResourceAmounts(7, 7, 0))
        self.assertIsNone(too_large.reservation)
        self.assertEqual(too_large.reason, "resource_exhausted")
        self.assertTrue(_required(data.reservation).cancel_before_start())

        command = _command(owner, ResourceAmounts(7, 7, 0))
        self.assertIsNotNone(command.reservation)
        blocked_data = owner.try_reserve("alice", now=1.0)
        self.assertIsNone(blocked_data.reservation)
        self.assertEqual(blocked_data.reason, "resource_exhausted")
        self.assertTrue(_required(command.reservation).cancel_before_start())

    def test_commands_do_not_claim_or_change_external_receiver_ordering(self) -> None:
        owner = _owner()
        command = _command(owner)
        self.assertIsNotNone(command.reservation)
        first = owner.try_reserve("receiver", now=1.0)
        self.assertIsNotNone(first.reservation)
        duplicate = owner.try_reserve("receiver", now=1.0)
        self.assertIsNone(duplicate.reservation)
        self.assertEqual(duplicate.reason, "receiver_busy")
        self.assertTrue(_required(command.reservation).cancel_before_start())
        self.assertTrue(_required(first.reservation).cancel_before_start())

    def test_prestart_cancel_is_one_shot_and_releases_all_resource_cost(self) -> None:
        owner = _owner(memory=5, cpu=5, storage=2)
        attempt = _command(owner, ResourceAmounts(5, 5, 0))
        self.assertIsNotNone(attempt.reservation)
        self.assertEqual(owner.snapshot(now=1.0).committed, ResourceAmounts(5, 5, 0))
        reservation = _required(attempt.reservation)
        self.assertTrue(reservation.cancel_before_start())
        self.assertFalse(reservation.cancel_before_start())
        self.assertFalse(reservation.finish())
        snapshot = owner.snapshot(now=1.0)
        self.assertEqual(snapshot.committed, ResourceAmounts())
        self.assertEqual(snapshot.command_inflight, 0)

    def test_start_rechecks_freshness_and_generation_without_doublecharging(self) -> None:
        owner = _owner(memory=3, cpu=3, storage=0)
        attempt = _command(owner, ResourceAmounts(3, 3, 0))
        self.assertIsNotNone(attempt.reservation)
        command = _required(attempt.reservation)
        self.assertTrue(command.start(now=1.0))
        self.assertEqual(owner.snapshot(now=1.0).committed, ResourceAmounts(3, 3, 0))
        self.assertTrue(command.finish())

        stale = _command(owner, ResourceAmounts(1, 1, 0))
        self.assertIsNotNone(stale.reservation)
        stale_reservation = _required(stale.reservation)
        self.assertFalse(stale_reservation.start(now=4.0))
        self.assertEqual(stale_reservation.state, "reserved")
        self.assertTrue(stale_reservation.cancel_before_start())

        fenced = _command(owner, ResourceAmounts(1, 1, 0))
        self.assertIsNotNone(fenced.reservation)
        generation = owner.generation
        self.assertTrue(owner.invalidate(expected_generation=generation))
        fenced_reservation = _required(fenced.reservation)
        self.assertFalse(fenced_reservation.start(now=1.0))
        self.assertEqual(fenced_reservation.state, "released")
        self.assertEqual(owner.snapshot(now=1.0).committed, ResourceAmounts())

    def test_pressure_yields_pending_commands_and_data_in_shared_sequence_order(self) -> None:
        owner = _owner(memory=12, cpu=12, storage=3)
        oldest_data = owner.try_reserve("oldest", now=1.0)
        self.assertIsNotNone(oldest_data.reservation)
        middle_command = _command(owner, ResourceAmounts(3, 3, 0))
        self.assertIsNotNone(middle_command.reservation)
        newest_data = owner.try_reserve("newest", now=1.0)
        self.assertIsNotNone(newest_data.reservation)

        self.assertTrue(
            owner.observe(
                _observation(at=1.5, memory=4, cpu=4, storage=1),
                now=1.5,
                generation=owner.generation,
            )
        )
        self.assertEqual(_required(newest_data.reservation).state, "released")
        self.assertEqual(_required(middle_command.reservation).state, "released")
        oldest_reservation = _required(oldest_data.reservation)
        self.assertEqual(oldest_reservation.state, "reserved")
        self.assertTrue(oldest_reservation.cancel_before_start())

    def test_commands_outside_debt_dimensions_survive_pressure_yielding(self) -> None:
        owner = _owner(memory=20, cpu=20, storage=1)
        storage = owner.try_reserve_storage(now=1.0)
        self.assertIsNotNone(storage.reservation)
        storage_reservation = _required(storage.reservation)
        self.assertTrue(storage_reservation.start())
        self.assertTrue(
            owner.observe(
                _observation(at=1.5, memory=20, cpu=20, storage=0),
                now=1.5,
                generation=owner.generation,
            )
        )
        command = _command(owner, now=1.5)
        self.assertIsNotNone(command.reservation)
        self.assertTrue(
            owner.observe(
                _observation(at=1.75, memory=20, cpu=20, storage=0),
                now=1.75,
                generation=owner.generation,
            )
        )
        command_reservation = _required(command.reservation)
        self.assertEqual(command_reservation.state, "reserved")
        self.assertTrue(command_reservation.cancel_before_start())
        self.assertTrue(storage_reservation.finish())

    def test_storage_pressure_yields_older_delivery_but_keeps_newer_zero_storage_command(
        self,
    ) -> None:
        owner = _owner(memory=20, cpu=20, storage=2)
        delivery = owner.try_reserve("pending", now=1.0)
        self.assertIsNotNone(delivery.reservation)
        command = _command(owner, ResourceAmounts(1, 1, 0))
        self.assertIsNotNone(command.reservation)
        storage = owner.try_reserve_storage(now=1.0)
        self.assertIsNotNone(storage.reservation)
        storage_reservation = _required(storage.reservation)
        self.assertTrue(storage_reservation.start())

        self.assertTrue(
            owner.observe(
                _observation(at=1.5, memory=20, cpu=20, storage=1),
                now=1.5,
                generation=owner.generation,
            )
        )
        self.assertEqual(_required(delivery.reservation).state, "released")
        command_reservation = _required(command.reservation)
        self.assertEqual(command_reservation.state, "reserved")
        self.assertEqual(owner.snapshot(now=1.5).resource_debt, ResourceAmounts())
        self.assertTrue(command_reservation.cancel_before_start())
        self.assertTrue(storage_reservation.finish())

    def test_stop_invalidate_and_reconfigure_release_pending_but_keep_started(self) -> None:
        for operation in ("stop", "invalidate", "reconfigure"):
            with self.subTest(operation=operation):
                owner = _owner()
                pending = _command(owner)
                started = _command(owner)
                self.assertIsNotNone(pending.reservation)
                self.assertIsNotNone(started.reservation)
                pending_reservation = _required(pending.reservation)
                started_reservation = _required(started.reservation)
                self.assertTrue(started_reservation.start(now=1.0))
                original_cost = started_reservation.footprint

                if operation == "stop":
                    owner.stop_accepting()
                    with self.assertRaises(RuntimeError):
                        owner.start_accepting()
                elif operation == "invalidate":
                    self.assertTrue(owner.invalidate(expected_generation=owner.generation))
                else:
                    self.assertIsNotNone(
                        owner.reconfigure(
                            _policy(memory_per_delivery=5),
                            expected_generation=owner.generation,
                        )
                    )

                self.assertEqual(pending_reservation.state, "released")
                self.assertEqual(started_reservation.state, "started")
                self.assertEqual(owner.snapshot(now=1.0).committed, original_cost)
                self.assertFalse(started_reservation.cancel_before_start())
                self.assertTrue(started_reservation.finish())
                self.assertEqual(owner.snapshot(now=1.0).committed, ResourceAmounts())
                if operation == "stop":
                    owner.start_accepting()

    def test_finish_refuses_copy_foreign_thread_and_foreign_async_task(self) -> None:
        owner = _owner()
        attempt = _command(owner)
        self.assertIsNotNone(attempt.reservation)
        reservation = _required(attempt.reservation)
        self.assertTrue(reservation.start(now=1.0))
        with self.assertRaises(TypeError):
            copy.copy(reservation)
        with self.assertRaises(TypeError):
            copy.deepcopy(reservation)
        with self.assertRaises(TypeError):
            pickle.dumps(reservation)

        results: queue.Queue[bool] = queue.Queue()
        thread = threading.Thread(target=lambda: results.put(reservation.finish()))
        thread.start()
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertFalse(results.get_nowait())
        self.assertEqual(owner.snapshot(now=1.0).command_started, 1)
        self.assertTrue(reservation.finish())

        async def check_task_affinity() -> None:
            async_owner = _owner()
            async_attempt = _command(async_owner)
            self.assertIsNotNone(async_attempt.reservation)
            async_reservation = _required(async_attempt.reservation)
            self.assertTrue(async_reservation.start(now=1.0))

            async def foreign_finish() -> bool:
                return async_reservation.finish()

            self.assertFalse(await asyncio.create_task(foreign_finish()))
            self.assertTrue(async_reservation.finish())

        asyncio.run(check_task_affinity())

    @unittest.skipUnless(hasattr(os, "fork"), "fork is unavailable")
    def test_finish_refuses_foreign_process_before_inherited_owner_lock(self) -> None:
        owner = _owner()
        attempt = _command(owner)
        self.assertIsNotNone(attempt.reservation)
        reservation = _required(attempt.reservation)
        self.assertTrue(reservation.start(now=1.0))

        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            try:
                child_result = reservation.finish()
                os.write(write_fd, b"0" if child_result is False else b"1")
                os._exit(0)
            except BaseException:
                os.write(write_fd, b"e")
                os._exit(1)

        os.close(write_fd)
        ready, _, _ = select.select([read_fd], [], [], 2.0)
        if not ready:
            os.kill(child, signal.SIGKILL)
        pipe_result = os.read(read_fd, 1) if ready else b"timeout"
        os.close(read_fd)
        _, status = os.waitpid(child, 0)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(pipe_result, b"0")
        self.assertTrue(reservation.finish())

    def test_command_does_not_gain_a_control_storage_loan_or_shared_preparation(self) -> None:
        policy = _policy()
        owner, permit = delivery_admission.DeliveryAdmission.with_storage_control(policy)
        self.assertTrue(
            owner.observe(
                _observation(at=1.0, memory=40, cpu=40, storage=10),
                now=1.0,
                generation=owner.generation,
            )
        )

        with permit.operation(clock=lambda: 1.0):
            attempt = _command(owner)
            self.assertIsNotNone(attempt.reservation)
            reservation = _required(attempt.reservation)
            self.assertEqual(reservation.footprint.storage_slots, 0)
            self.assertFalse(hasattr(reservation, "try_acquire_storage_connection"))
            self.assertFalse(hasattr(reservation, "prepare_storage_scope"))
            self.assertTrue(reservation.start(now=1.0))
            snapshot = owner.snapshot(now=1.0)
            self.assertEqual(snapshot.control_connection_loans, 0)
            self.assertEqual(snapshot.storage_connection_loans, 0)
            self.assertEqual(snapshot.command_inflight, 1)
            self.assertEqual(snapshot.delivery_inflight, 0)
            self.assertTrue(reservation.finish())


if __name__ == "__main__":
    unittest.main()
