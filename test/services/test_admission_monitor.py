"""Fresh authority, resource recovery and physical probe retirement compose."""

import asyncio
import os
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.models.resource_envelope import (
    EnvelopeClaims,
    ResourceBinding,
    ResourceEnvelopeRecord,
)
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy, CapacityObservation
from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSample
from cli_agent_orchestrator.services.admission_resources import ResourceSample, StorageObservation
from cli_agent_orchestrator.services.admission_settings import AdmissionPolicyLoader
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.offline_database
MIB = 1024 * 1024


def fixture():
    from cli_agent_orchestrator.services.admission_monitor import AdmissionMonitor

    clock = [10.0]
    binding = ResourceBinding(
        instance_id="fa909c12-4645-4605-ab7c-9ac5f4c8125a",
        process_generation="395141ce-9910-43f6-b4c4-9fe21790309e",
        process_pid=os.getpid(),
        process_started_at=1.0,
        store_id="d18bcbac-9c97-4408-baf0-24f7e2331486",
        target_identity_sha256="a" * 64,
        mount_ns_device=1,
        mount_ns_inode=2,
        cgroup_ns_device=1,
        cgroup_ns_inode=3,
        resource_view_sha256="b" * 64,
    )
    current = [binding]
    record = ResourceEnvelopeRecord(
        binding=binding,
        claims=EnvelopeClaims(4096 * MIB, 16000, 12, True, False),
        revision=1,
        active=True,
        actor_principal_id="principal-" + "a" * 64,
    )
    repository = SimpleNamespace(
        latest=record,
        resolve_target=lambda: object(),
        read_latest=lambda target, instance: repository.latest,
    )
    bootstrap = ResourceBootstrap(
        repository=repository,
        binding_supplier=lambda target: current[0],
        instance_supplier=lambda: current[0].instance_id,
        clock=lambda: clock[0],
    )
    policy = {"version": 1, "policy": {}}
    owner = DeliveryAdmission(AdmissionPolicy())

    class Postgres:
        def __init__(self):
            self.slots = 12
            self.calls = 0
            self.after = lambda: None
            self.policies = []

        def configure(self, policy):
            self.policies.append(policy)
            return True

        def sample(self):
            self.calls += 1
            self.after()
            return PostgresPressureSample(
                StorageObservation(clock[0], self.slots), "pressure_observed"
            )

    class Resources:
        def __init__(self):
            self.resets = 0
            self.coverage = "known"
            self.after = lambda: None

        def reset_history(self):
            self.resets += 1

        def sample(self, *, storage):
            self.after()
            return ResourceSample(
                CapacityObservation(
                    clock[0],
                    4096 * MIB,
                    4096 * MIB,
                    16000,
                    16000,
                    storage.available_slots,
                    self.coverage,
                ),
                (),
            )

    postgres = Postgres()
    resources = Resources()
    created = []

    def factory(attestation):
        created.append(attestation)
        return resources

    monitor = AdmissionMonitor(
        owner,
        policy_loader=AdmissionPolicyLoader(source=lambda: policy),
        bootstrap=bootstrap,
        postgres=postgres,
        scope_id="test-node/source",
        local_storage_capacity=lambda: 12,
        clock=lambda: clock[0],
        resource_factory=factory,
    )
    return SimpleNamespace(**locals())


def tick(f, delta=1.0):
    f.clock[0] += delta
    return f.monitor.refresh()


def warm(f):
    assert f.monitor.refresh().reason == "recovering"
    assert tick(f).reason == "recovering"
    assert tick(f).reason == "capacity_available"


def test_growth_needs_distinct_healthy_samples_and_pressure_reduces_immediately():
    f = fixture()
    warm(f)
    assert f.owner.snapshot(now=f.clock[0]).available_slots == 12
    f.postgres.slots = 2
    assert tick(f).reason == "capacity_available"
    assert f.owner.snapshot(now=f.clock[0]).available_slots == 2
    f.postgres.slots = 8
    assert tick(f).reason == "recovering"
    assert tick(f).reason == "recovering"
    assert f.owner.snapshot(now=f.clock[0]).available_slots == 2
    assert tick(f).reason == "capacity_available"
    assert f.owner.snapshot(now=f.clock[0]).available_slots == 8
    assert len(f.created) == 1


def test_duplicate_cycle_cannot_count_as_additional_recovery_sample():
    f = fixture()
    f.monitor.refresh()
    f.monitor.refresh()
    f.monitor.refresh()
    assert f.owner.snapshot(now=f.clock[0]).available_slots == 0
    assert tick(f).reason == "recovering"
    assert tick(f).reason == "capacity_available"


def test_revocation_during_probe_discards_sample_and_retires_unstarted_handoffs():
    f = fixture()
    warm(f)
    queued = f.owner.try_reserve("queued", now=f.clock[0]).reservation
    assert queued is not None
    f.postgres.after = lambda: setattr(
        f.repository, "latest", replace(f.record, active=False, revision=2)
    )
    assert tick(f).reason == "authority_unavailable"
    assert not queued.start()
    assert f.owner.try_reserve("new", now=f.clock[0]).reservation is None


def test_binding_drift_during_resource_read_is_not_published():
    f = fixture()
    f.resources.after = lambda: f.current.__setitem__(0, replace(f.binding, mount_ns_inode=99))
    assert f.monitor.refresh().reason == "authority_unavailable"
    assert f.owner.snapshot(now=f.clock[0]).available_slots == 0


def test_owner_generation_changed_during_io_cannot_receive_late_sample():
    f = fixture()
    f.postgres.after = lambda: f.owner.reconfigure(
        AdmissionPolicy(memory_per_delivery_bytes=32 * MIB), expected_generation=f.owner.generation
    )
    assert f.monitor.refresh().reason == "generation_changed"
    assert f.owner.try_reserve("late", now=f.clock[0]).reason == "unknown_capacity"


def test_long_gap_resets_cpu_history_and_restarts_recovery():
    f = fixture()
    warm(f)
    assert tick(f, delta=4.0).reason == "recovering"
    assert f.resources.resets == 1
    assert f.owner.snapshot(now=f.clock[0]).available_slots == 0
    assert tick(f).reason == "recovering"
    assert tick(f).reason == "capacity_available"


def test_slow_authority_recheck_cannot_freshen_old_resource_measurements():
    f = fixture()
    f.resources.after = lambda: f.clock.__setitem__(0, f.clock[0] + 4.0)
    assert f.monitor.refresh().reason == "stale_observation"
    assert f.owner.snapshot(now=f.clock[0]).available_slots == 0


def test_pg_sampler_is_preserved_across_policy_reload_and_stage_release_is_immediate():
    f = fixture()
    warm(f)
    lease = f.owner.try_reserve("running", now=f.clock[0]).reservation
    assert lease is not None and lease.start()
    before = f.owner.snapshot(now=f.clock[0]).available_slots
    assert lease.release_storage()
    assert f.owner.snapshot(now=f.clock[0]).available_slots == before + 1
    f.policy["policy"]["postgres_reserve_percent"] = 30
    assert tick(f).reason == "recovering"
    assert f.postgres.policies[-1].reserve_percent == 30
    assert f.postgres.calls == 4
    assert lease.finish()


@pytest.mark.asyncio
async def test_stop_waits_for_physical_probe_after_runner_cancellation():
    f = fixture()
    entered, release, retired = Event(), Event(), Event()

    def held_probe():
        entered.set()
        assert release.wait(timeout=10)
        retired.set()

    f.postgres.after = held_probe
    await f.monitor.start()
    assert await asyncio.to_thread(entered.wait, 10)
    stop = asyncio.create_task(f.monitor.stop())
    await asyncio.sleep(0)
    assert not stop.done()
    assert not f.owner.snapshot(now=f.clock[0]).accepting
    release.set()
    await asyncio.wait_for(stop, timeout=10)
    assert retired.is_set()
    assert f.monitor.refresh().reason == "stopping"


def test_future_resource_component_is_rejected_before_timestamps_are_combined():
    f = fixture()
    original = f.resources.sample

    def future(*, storage):
        sample = original(storage=storage)
        return replace(
            sample, observation=replace(sample.observation, observed_at=f.clock[0] + 100)
        )

    f.resources.sample = future
    assert f.monitor.refresh().reason == "future_observation"
    assert f.owner.snapshot(now=f.clock[0]).available_slots == 0


def test_pressure_reduction_retires_unstarted_handoffs_but_keeps_physical_work():
    f = fixture()
    warm(f)
    running = f.owner.try_reserve("running", now=f.clock[0]).reservation
    queued = f.owner.try_reserve("queued", now=f.clock[0]).reservation
    assert running is not None and running.start()
    assert queued is not None
    f.postgres.slots = 0
    assert tick(f).reason == "resource_exhausted"
    assert not queued.start()
    assert f.owner.snapshot(now=f.clock[0]).started == 1
    assert running.finish()


@pytest.mark.asyncio
async def test_stop_retires_failed_probe_and_reports_original_error():
    f = fixture()
    failed = Event()

    def fail():
        failed.set()
        raise RuntimeError("controlled-programming-failure")

    f.postgres.after = fail
    await f.monitor.start()
    assert await asyncio.to_thread(failed.wait, 10)
    with pytest.raises(RuntimeError, match="controlled-programming-failure"):
        await f.monitor.stop()
    f.postgres.after = lambda: None
    await f.monitor.start()
    await f.monitor.stop()


@pytest.mark.asyncio
async def test_stop_joins_a_synchronous_refresh_owned_by_another_caller():
    f = fixture()
    entered, release = Event(), Event()

    def hold():
        entered.set()
        assert release.wait(10)

    f.postgres.after = hold
    worker = asyncio.create_task(asyncio.to_thread(f.monitor.refresh))
    assert await asyncio.to_thread(entered.wait, 10)
    stop = asyncio.create_task(f.monitor.stop())
    await asyncio.sleep(0)
    premature = stop.done()
    release.set()
    await asyncio.wait_for(worker, 10)
    await asyncio.wait_for(stop, 10)
    assert not premature


@pytest.mark.asyncio
async def test_cancelled_stop_keeps_probe_owned_and_a_later_stop_can_join_it():
    f = fixture()
    entered, release = Event(), Event()

    def hold():
        entered.set()
        assert release.wait(10)

    f.postgres.after = hold
    await f.monitor.start()
    assert await asyncio.to_thread(entered.wait, 10)
    stop = asyncio.create_task(f.monitor.stop())
    await asyncio.sleep(0)
    stop.cancel()
    await asyncio.sleep(0)
    try:
        with pytest.raises(RuntimeError, match="retire"):
            await f.monitor.start()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await stop
    await asyncio.wait_for(f.monitor.stop(), 10)


def test_resource_reduction_keeps_oldest_pending_handoffs_that_still_fit():
    f = fixture()
    warm(f)
    oldest = f.owner.try_reserve("oldest", now=f.clock[0]).reservation
    newest = f.owner.try_reserve("newest", now=f.clock[0]).reservation
    f.postgres.slots = 1
    tick(f)
    assert oldest is not None and oldest.start()
    assert newest is not None and not newest.start()
    assert oldest.finish()


@pytest.mark.asyncio
async def test_refresh_paused_before_acquire_cannot_cross_a_stop_and_restart():
    from threading import Lock

    f = fixture()
    entered, release = Event(), Event()
    lock = Lock()

    class PausedLock:
        def acquire(self, *, blocking):
            entered.set()
            assert release.wait(10)
            return lock.acquire(blocking=blocking)

        def release(self):
            lock.release()

        def locked(self):
            return lock.locked()

    async def dormant_runner():
        await asyncio.Event().wait()

    f.monitor._refresh_guard = PausedLock()
    f.monitor._run = dormant_runner
    worker = asyncio.create_task(asyncio.to_thread(f.monitor.refresh))
    assert await asyncio.to_thread(entered.wait, 10)
    try:
        await f.monitor.stop()
        await f.monitor.start()
    finally:
        release.set()
    result = await asyncio.wait_for(worker, 10)
    await f.monitor.stop()
    assert result.reason == "stopping"
    assert f.postgres.calls == 0
