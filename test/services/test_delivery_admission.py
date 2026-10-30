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
