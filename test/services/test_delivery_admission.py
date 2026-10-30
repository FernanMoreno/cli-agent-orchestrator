"""One owner accounts for reserved and physically started delivery work."""

import importlib
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.offline_database

MIB = 1024 * 1024


def create():
    policy = importlib.import_module("cli_agent_orchestrator.services.admission_policy")
    controller = importlib.import_module("cli_agent_orchestrator.services.delivery_admission")
    return controller.DeliveryAdmission(policy.AdmissionPolicy())


def observe(owner, slots, *, at=10.0, now=None, generation=None):
    policy = importlib.import_module("cli_agent_orchestrator.services.admission_policy")
    return owner.observe(
        policy.CapacityObservation(
            observed_at=at,
            memory_available_bytes=4096 * MIB,
            memory_limit_bytes=4096 * MIB,
            cpu_available_millicores=16000,
            cpu_limit_millicores=16000,
            storage_available_slots=slots,
        ),
        now=at if now is None else now,
        generation=owner.generation if generation is None else generation,
    )


def reserve(owner, receiver, *, now=10.0):
    result = owner.try_reserve(receiver, now=now)
    assert result.reservation is not None, result.reason
    return result.reservation


def test_more_than_four_distinct_receivers_share_observed_budget():
    owner = create()
    observe(owner, 12)
    leases = [reserve(owner, f"receiver-{i}") for i in range(12)]
    assert owner.snapshot(now=10.0).reserved == 12
    assert owner.try_reserve("overflow", now=10.0).reason == "resource_exhausted"
    for lease in leases:
        assert lease.start()
    status = owner.snapshot(now=10.0)
    assert (status.reserved, status.started, status.available_slots) == (0, 12, 0)
    for lease in leases:
        assert lease.finish()
    assert owner.snapshot(now=10.0).inflight == 0


def test_same_receiver_stays_exclusive_while_others_can_progress():
    owner = create()
    observe(owner, 8)
    first = reserve(owner, "receiver")
    assert owner.try_reserve("receiver", now=10.0).reason == "receiver_busy"
    other = reserve(owner, "another")
    assert first.cancel_before_start()
    replacement = reserve(owner, "receiver")
    assert other.start() and replacement.start()
    assert other.finish() and replacement.finish()


def test_cancelled_awaiter_cannot_release_physically_started_work():
    owner = create()
    observe(owner, 1)
    lease = reserve(owner, "receiver")
    assert lease.start()
    assert not lease.cancel_before_start()
    assert owner.try_reserve("another", now=10.0).reservation is None
    assert owner.snapshot(now=10.0).started == 1
    assert lease.finish()
    assert not lease.finish()
    assert reserve(owner, "another").cancel_before_start()


def test_cancel_before_start_prevents_a_late_worker_from_starting():
    owner = create()
    observe(owner, 1)
    lease = reserve(owner, "receiver")
    assert lease.cancel_before_start()
    assert not lease.start()
    assert not lease.cancel_before_start()
    assert owner.snapshot(now=10.0).inflight == 0


def test_finish_requires_physical_start():
    owner = create()
    observe(owner, 1)
    lease = reserve(owner, "receiver")
    with pytest.raises(RuntimeError, match="not started"):
        lease.finish()
    assert owner.snapshot(now=10.0).reserved == 1
    assert lease.cancel_before_start()


def test_new_pressure_stops_admission_and_keeps_debt_until_real_finish():
    owner = create()
    observe(owner, 3)
    leases = [reserve(owner, str(i)) for i in range(3)]
    assert all(lease.start() for lease in leases)
    observe(owner, 0, at=11.0)
    status = owner.snapshot(now=11.0)
    assert (status.inflight, status.debt, status.available_slots) == (3, 3, 0)
    assert owner.try_reserve("fourth", now=11.0).reservation is None
    leases[0].finish()
    assert owner.snapshot(now=11.0).debt == 2
    observe(owner, 4, at=12.0)
    additional = [reserve(owner, str(i), now=12.0) for i in (3, 4)]
    assert owner.try_reserve("fifth", now=12.0).reservation is None
    for lease in additional:
        lease.cancel_before_start()
    for lease in leases[1:]:
        lease.finish()


def test_reusing_one_observation_never_mints_more_capacity():
    owner = create()
    observe(owner, 3)
    leases = [reserve(owner, str(i)) for i in range(3)]
    assert all(lease.start() for lease in leases)
    assert not observe(owner, 3, now=10.5)
    assert owner.try_reserve("new", now=10.5).reservation is None
    assert not observe(owner, 50, at=9.0, now=10.5)
    assert owner.try_reserve("new", now=10.5).reservation is None
    for lease in leases:
        lease.finish()


def test_stale_observation_stops_new_work_without_cancelling_active():
    owner = create()
    observe(owner, 2)
    first = reserve(owner, "receiver")
    assert first.start()
    assert owner.try_reserve("another", now=13.01).reason == "stale_observation"
    assert owner.snapshot(now=13.01).started == 1
    assert first.finish()


def test_stop_retires_unstarted_reservations_but_drains_started_work():
    owner = create()
    observe(owner, 2)
    first = reserve(owner, "started")
    queued = reserve(owner, "queued")
    assert first.start()
    owner.stop_accepting()
    status = owner.snapshot(now=10.0)
    assert not status.accepting
    assert (status.started, status.reserved) == (1, 0)
    assert not queued.start()
    assert owner.try_reserve("late", now=10.0).reason == "stopping"
    with pytest.raises(RuntimeError, match="active"):
        owner.start_accepting()
    assert first.finish()
    owner.start_accepting()
    assert owner.try_reserve("unknown", now=11.0).reason == "unknown_capacity"
    observe(owner, 1, at=11.0)
    replacement = reserve(owner, "started", now=11.0)
    assert not first.finish()
    assert owner.snapshot(now=11.0).reserved == 1
    assert replacement.cancel_before_start()


def test_parallel_callers_cannot_exceed_the_combined_budget():
    owner = create()
    observe(owner, 7)
    with ThreadPoolExecutor(max_workers=16) as pool:
        attempts = list(pool.map(lambda i: owner.try_reserve(str(i), now=10.0), range(64)))
    admitted = [a.reservation for a in attempts if a.reservation is not None]
    assert len(admitted) == owner.snapshot(now=10.0).inflight == 7
    for lease in admitted:
        assert lease.cancel_before_start()


def test_parallel_callers_share_one_receiver_reservation():
    owner = create()
    observe(owner, 12)
    with ThreadPoolExecutor(max_workers=16) as pool:
        attempts = list(pool.map(lambda _: owner.try_reserve("same", now=10.0), range(32)))
    admitted = [a.reservation for a in attempts if a.reservation is not None]
    assert len(admitted) == 1
    assert all(a.reason == "receiver_busy" for a in attempts if a.reservation is None)
    assert admitted[0].cancel_before_start()


def test_one_reservation_can_physically_start_only_once():
    owner = create()
    observe(owner, 1)
    lease = reserve(owner, "same")
    with ThreadPoolExecutor(max_workers=8) as pool:
        starts = list(pool.map(lambda _: lease.start(), range(32)))
    assert sum(starts) == 1
    assert lease.finish()


@pytest.mark.parametrize("receiver", ["", "  ", None, 1])
def test_invalid_receiver_cannot_reserve_capacity(receiver):
    owner = create()
    observe(owner, 3)
    with pytest.raises(ValueError):
        owner.try_reserve(receiver, now=10.0)
    assert owner.snapshot(now=10.0).inflight == 0


def test_old_sampler_generation_cannot_repopulate_a_restarted_owner():
    owner = create()
    old_generation = owner.generation
    observe(owner, 1)
    owner.stop_accepting()
    owner.start_accepting()
    assert owner.generation != old_generation
    assert not observe(owner, 50, at=11.0, generation=old_generation)
    assert owner.try_reserve("receiver", now=11.0).reason == "unknown_capacity"
    assert observe(owner, 1, at=11.0)
    lease = reserve(owner, "receiver", now=11.0)
    assert lease.cancel_before_start()


def test_future_sample_cannot_poison_the_freshness_watermark():
    owner = create()
    assert not observe(owner, 50, at=1000.0, now=10.0)
    assert observe(owner, 1, at=10.0)
    lease = reserve(owner, "receiver")
    assert lease.cancel_before_start()


def test_policy_reload_preserves_original_cost_and_operator_budget():
    from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy, ResourceAmounts
    from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

    owner = DeliveryAdmission(AdmissionPolicy(), budget=ResourceAmounts(128 * MIB, 4000, 20))
    observe(owner, 20)
    old = [reserve(owner, str(i)) for i in range(2)]
    assert all(lease.start() for lease in old)
    previous = owner.generation
    assert (
        owner.reconfigure(
            AdmissionPolicy(memory_per_delivery_bytes=32 * MIB), expected_generation=previous
        )
        == previous + 1
    )
    assert not observe(owner, 20, at=11.0, generation=previous)
    assert observe(owner, 20, at=11.0)
    assert owner.try_reserve("blocked", now=11.0).reservation is None
    assert old[0].footprint.memory_bytes == 64 * MIB
    assert old[0].finish()
    new = [reserve(owner, str(i), now=11.0) for i in (2, 3)]
    assert owner.snapshot(now=11.0).committed.memory_bytes == 128 * MIB
    assert owner.try_reserve("overflow", now=11.0).reservation is None
    assert all(lease.cancel_before_start() for lease in new)
    assert old[1].finish()
    assert owner.snapshot(now=11.0).committed == ResourceAmounts()


def test_storage_stage_release_keeps_receiver_memory_and_cpu_until_finish():
    from cli_agent_orchestrator.services.admission_policy import ResourceAmounts

    owner = create()
    observe(owner, 4)
    first = [reserve(owner, str(i)) for i in range(4)]
    assert not first[0].release_storage()
    for lease in first:
        assert lease.start()
        assert lease.release_storage()
        assert not lease.release_storage()
    assert owner.try_reserve("0", now=10.0).reason == "receiver_busy"
    second = [reserve(owner, str(i)) for i in range(4, 8)]
    assert all(lease.start() for lease in second)
    status = owner.snapshot(now=10.0)
    assert (status.inflight, status.available_slots, status.debt) == (8, 0, 0)
    assert status.committed == ResourceAmounts(512 * MIB, 2000, 4)
    assert all(lease.finish() for lease in first + second)
    replacement = reserve(owner, "0")
    assert not first[0].release_storage()
    assert owner.snapshot(now=10.0).committed == ResourceAmounts(64 * MIB, 250, 1)
    assert replacement.cancel_before_start()


def test_reconfigure_cancels_only_unstarted_and_rejects_stale_generation():
    from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy, ResourceAmounts

    owner = create()
    observe(owner, 4)
    running = reserve(owner, "running")
    queued = reserve(owner, "queued")
    assert running.start()
    previous = owner.generation
    policy = AdmissionPolicy(memory_per_delivery_bytes=32 * MIB)
    assert owner.reconfigure(policy, expected_generation=previous) == previous + 1
    assert not queued.start()
    assert (
        owner.reconfigure(AdmissionPolicy(), budget=ResourceAmounts(), expected_generation=previous)
        is None
    )
    assert owner.snapshot(now=10.0).committed == ResourceAmounts(64 * MIB, 250, 1)
    assert owner.try_reserve("new", now=10.0).reason == "unknown_capacity"
    assert observe(owner, 4, at=11.0)
    assert owner.snapshot(now=11.0).started == 1
    assert running.finish()


def test_budget_reduction_reports_quantity_debt_until_original_lease_finishes():
    from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy, ResourceAmounts

    owner = create()
    observe(owner, 4)
    lease = reserve(owner, "running")
    assert lease.start()
    owner.reconfigure(
        AdmissionPolicy(memory_per_delivery_bytes=32 * MIB),
        budget=ResourceAmounts(16 * MIB, 100, 0),
        expected_generation=owner.generation,
    )
    observe(owner, 4, at=11.0)
    status = owner.snapshot(now=11.0)
    assert status.resource_debt == ResourceAmounts(48 * MIB, 150, 1)
    assert status.debt == 2
    assert status.available_slots == 0
    assert lease.finish()
    assert owner.snapshot(now=11.0).resource_debt == ResourceAmounts()


def test_concurrent_stage_and_final_completion_release_each_quantity_exactly_once():
    from threading import Barrier

    from cli_agent_orchestrator.services.admission_policy import ResourceAmounts

    owner = create()
    observe(owner, 4)
    leases = [reserve(owner, str(i)) for i in range(4)]
    assert all(lease.start() for lease in leases)
    barrier = Barrier(8)

    def complete(lease, storage):
        barrier.wait(timeout=10)
        return lease.release_storage() if storage else lease.finish()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(complete, lease, storage) for lease in leases for storage in (True, False)
        ]
        results = [future.result(timeout=10) for future in futures]
    assert all(results[i] for i in (1, 3, 5, 7))
    assert owner.snapshot(now=10.0).committed == ResourceAmounts()
    assert owner.snapshot(now=10.0).inflight == 0


def test_identical_policy_reload_keeps_valid_sample_and_pending_handoffs():
    from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy

    owner = create()
    observe(owner, 2)
    lease = reserve(owner, "queued")
    generation = owner.generation
    assert owner.reconfigure(AdmissionPolicy(), expected_generation=generation) == generation
    assert lease.start()
    assert owner.snapshot(now=10.0).available_slots == 1
    assert lease.finish()


def test_concurrent_policy_updates_have_only_one_generation_winner():
    from threading import Barrier

    from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy

    owner = create()
    observe(owner, 4)
    lease = reserve(owner, "running")
    assert lease.start()
    generation = owner.generation
    barrier = Barrier(2)

    def update(cost):
        barrier.wait(timeout=10)
        return owner.reconfigure(
            AdmissionPolicy(memory_per_delivery_bytes=cost * MIB), expected_generation=generation
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, (16, 32)))
    assert results.count(None) == 1
    assert results.count(generation + 1) == 1
    assert owner.snapshot(now=10.0).started == 1
    assert lease.finish()
