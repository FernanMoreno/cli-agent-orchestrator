"""Storage phases retain the invocation's memory, CPU and receiver ownership."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from test.services.test_delivery_admission import create, observe, reserve
from threading import Event

import pytest

from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy, CapacityObservation
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.offline_database


def running(owner, receiver="receiver"):
    lease = reserve(owner, receiver)
    assert lease.start()
    return lease


def test_owned_storage_phase_keeps_other_costs_and_rejects_stale_release():
    owner = create()
    observe(owner, 1)
    lease = running(owner)
    footprint = lease.footprint
    first = lease.take_storage_stage(now=10.0)
    assert first is not None
    assert lease.take_storage_stage(now=10.0) is None
    assert lease.release_storage() is False
    with pytest.raises(RuntimeError, match="storage stage"):
        lease.finish()
    assert first.finish() is True
    snapshot = owner.snapshot(now=10.0)
    assert snapshot.started == 1 and snapshot.committed.storage_slots == 0
    assert snapshot.committed.memory_bytes == footprint.memory_bytes
    assert snapshot.committed.cpu_millicores == footprint.cpu_millicores
    assert owner.try_reserve("receiver", now=10.0).reason == "receiver_busy"
    other = running(owner, "other")
    assert lease.take_storage_stage(now=10.0) is None
    other.finish()
    second = lease.take_storage_stage(now=10.0)
    assert second is not None and second is not first
    assert first.finish() is False and replace(second).finish() is False
    assert owner.snapshot(now=10.0).committed == footprint
    assert second.finish() and not second.finish()
    assert lease.finish()
    assert owner.snapshot(now=10.0).inflight == 0


def test_storage_reentry_does_not_require_free_memory_or_cpu_for_another_delivery():
    owner = DeliveryAdmission(AdmissionPolicy(memory_reserve_percent=0, cpu_reserve_percent=0))
    policy = AdmissionPolicy()
    owner.observe(
        CapacityObservation(
            10.0,
            policy.memory_per_delivery_bytes,
            policy.memory_per_delivery_bytes,
            policy.cpu_per_delivery_millicores,
            policy.cpu_per_delivery_millicores,
            1,
        ),
        now=10.0,
        generation=owner.generation,
    )
    lease = running(owner)
    first = lease.take_storage_stage(now=10.0)
    assert first.finish()
    assert owner.snapshot(now=10.0).available_slots == 0
    second = lease.take_storage_stage(now=10.0)
    assert second is not None
    assert owner.snapshot(now=10.0).committed == lease.footprint
    second.finish()
    lease.finish()


@pytest.mark.parametrize("condition", ["stale", "unknown", "pressure", "stopping", "ceiling"])
def test_storage_reentry_denial_preserves_started_invocation(condition):
    owner = create()
    observe(owner, 1)
    lease = running(owner)
    stage = lease.take_storage_stage(now=10.0)
    assert stage.finish()
    now = 10.0
    if condition == "stale":
        now = 20.0
    elif condition == "unknown":
        owner.invalidate(expected_generation=owner.generation)
    elif condition == "pressure":
        observe(owner, 0, at=11.0)
        now = 11.0
    elif condition == "stopping":
        owner.stop_accepting()
    else:
        from cli_agent_orchestrator.services.admission_policy import ResourceAmounts

        owner.observe(
            CapacityObservation(11.0, 2**32, 2**32, 16000, 16000, 10),
            now=11.0,
            generation=owner.generation,
            ceiling=ResourceAmounts(2**32, 16000, 0),
        )
        now = 11.0
    before = owner.snapshot(now=now)
    assert lease.take_storage_stage(now=now) is None
    after = owner.snapshot(now=now)
    assert after.committed == before.committed
    assert after.started == 1 and after.inflight == 1
    lease.finish()


def test_cancelled_waiter_cannot_retire_a_live_storage_worker():
    owner = create()
    observe(owner, 1)
    lease = running(owner)
    stage = lease.take_storage_stage(now=10.0)
    entered, release = Event(), Event()

    def physical():
        entered.set()
        assert release.wait(10)
        return stage.finish()

    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(physical)
        try:
            assert entered.wait(5)
            assert future.cancel() is False
            assert lease.release_storage() is False
            with pytest.raises(RuntimeError, match="storage stage"):
                lease.finish()
            assert owner.snapshot(now=10.0).committed == lease.footprint
        finally:
            release.set()
        assert future.result(timeout=5)
    assert lease.finish()


def test_parallel_storage_claim_has_one_physical_owner():
    owner = create()
    observe(owner, 1)
    lease = running(owner)
    with ThreadPoolExecutor(max_workers=8) as workers:
        stages = list(workers.map(lambda _: lease.take_storage_stage(now=10.0), range(8)))
    claimed = [stage for stage in stages if stage is not None]
    assert len(claimed) == 1
    assert owner.snapshot(now=10.0).committed == lease.footprint
    assert claimed[0].finish()
    lease.finish()


def test_unstarted_or_finished_invocation_cannot_claim_storage_stage():
    owner = create()
    observe(owner, 1)
    lease = reserve(owner, "receiver")
    assert lease.take_storage_stage(now=10.0) is None
    assert lease.cancel_before_start()
    assert lease.take_storage_stage(now=10.0) is None
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.parametrize("deliveries", [1, 4, 9, 20])
def test_storage_capacity_is_not_a_limit_on_started_deliveries(deliveries):
    owner = create()
    observe(owner, 1)
    leases = []
    for index in range(deliveries):
        lease = running(owner, f"receiver-{index}")
        leases.append(lease)
        stage = lease.take_storage_stage(now=10.0)
        assert stage is not None
        assert owner.snapshot(now=10.0).committed.storage_slots == 1
        assert stage.finish()
    snapshot = owner.snapshot(now=10.0)
    assert snapshot.started == deliveries
    assert snapshot.committed.memory_bytes == deliveries * leases[0].footprint.memory_bytes
    assert snapshot.committed.cpu_millicores == deliveries * leases[0].footprint.cpu_millicores
    assert snapshot.committed.storage_slots == 0
    for index, lease in enumerate(leases):
        assert owner.try_reserve(f"receiver-{index}", now=10.0).reason == "receiver_busy"
        stage = lease.take_storage_stage(now=10.0)
        assert stage is not None and stage.finish()
        assert lease.finish()
    assert owner.snapshot(now=10.0).inflight == 0


def test_existing_storage_can_retire_after_authority_loss_without_new_capacity():
    owner = create()
    observe(owner, 1)
    lease = running(owner)
    owner.invalidate(expected_generation=owner.generation)
    owner.stop_accepting()
    stage = lease.take_storage_stage(now=10.0)
    assert stage is not None and stage.finish()
    assert lease.take_storage_stage(now=10.0) is None
    assert lease.finish()


@pytest.mark.parametrize("transition", ["invalidate", "reconfigure", "stop"])
def test_active_stage_retires_after_generation_or_lifecycle_change(transition):
    owner = create()
    observe(owner, 1)
    lease = running(owner)
    stage = lease.take_storage_stage(now=10.0)
    assert stage is not None
    if transition == "invalidate":
        owner.invalidate(expected_generation=owner.generation)
    elif transition == "reconfigure":
        owner.reconfigure(
            AdmissionPolicy(memory_per_delivery_bytes=128 * 1024 * 1024),
            expected_generation=owner.generation,
        )
    else:
        owner.stop_accepting()
    assert owner.snapshot(now=10.0).committed == lease.footprint
    assert stage.finish() and not stage.finish()
    assert owner.snapshot(now=10.0).committed.storage_slots == 0
    assert lease.take_storage_stage(now=10.0) is None
    assert lease.finish()
    assert owner.snapshot(now=10.0).inflight == 0
