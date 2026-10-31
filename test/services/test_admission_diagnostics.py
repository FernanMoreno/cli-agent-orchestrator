"""Local diagnostic snapshots preserve accounting and never wait for probe I/O."""

from dataclasses import FrozenInstanceError
from test.services.test_admission_monitor import fixture, warm
from threading import Event, Thread

import pytest
from pydantic import ValidationError

from cli_agent_orchestrator.models.admission_control import ResourceQuantities
from cli_agent_orchestrator.services.admission_diagnostics import diagnostics_response

pytestmark = pytest.mark.offline_database


def response(f, *, now=None):
    now = f.clock[0] if now is None else now
    return diagnostics_response(f.monitor.diagnostic(now=now), now=now)


def test_unknown_basis_does_not_fabricate_capacity_or_probe():
    f = fixture()
    value = response(f)
    assert value.reason == "unknown_capacity" and value.capacity is None
    assert value.sample is None and value.postgres is None and value.resource_probe is None
    assert value.operations.inflight_operations == 0
    assert f.postgres.calls == 0


def test_owner_snapshot_is_immutable_and_never_iterates_live_reservations():
    class NoIteration(dict):
        def __iter__(self):
            raise AssertionError("diagnostic iterated leases")

        def values(self):
            raise AssertionError("diagnostic iterated lease values")

        def items(self):
            raise AssertionError("diagnostic iterated lease items")

    f = fixture()
    warm(f)
    leases = []
    original = f.owner._leases
    try:
        for index in range(3):
            lease = f.owner.try_reserve(str(index), now=f.clock[0], storage_slots=2).reservation
            assert lease is not None and lease.start()
            leases.append(lease)
        f.owner._leases = NoIteration(original)
        captured = f.owner.diagnostic(now=f.clock[0])
        assert captured.snapshot.delivery_inflight == 3
        assert captured.snapshot.committed.storage_slots == 6
        with pytest.raises(FrozenInstanceError):
            captured.budget = None
        assert response(f).operations.started_deliveries == 3
    finally:
        f.owner._leases = original
        for lease in leases:
            assert lease.finish_after_storage()
    assert captured.snapshot.delivery_inflight == 3
    assert response(f).operations.inflight_deliveries == 0


def test_pending_physical_loan_is_reported_until_exact_retirement():
    f = fixture()
    warm(f)
    lease = f.owner.try_reserve("owned", now=f.clock[0], storage_slots=2).reservation
    assert lease is not None and lease.start()
    loan = lease.try_acquire_storage_connection(now=f.clock[0])
    assert loan is not None
    try:
        assert response(f).operations.storage_connection_loans == 1
        assert not lease.finish_after_storage()
        value = response(f)
        assert value.operations.storage_retirement_pending == 1
        assert value.operations.started_deliveries == 1
        assert value.committed.storage_slots == 2
    finally:
        assert loan.finish()
    assert response(f).operations.inflight_operations == 0


def test_read_during_probe_and_generation_change_is_local_and_separately_tagged():
    f = fixture()
    warm(f)
    entered, release = Event(), Event()
    results, failures = [], []

    def held_probe():
        entered.set()
        assert release.wait(15), "probe was not released"

    def run():
        try:
            results.append(f.monitor.refresh())
        except BaseException as error:
            failures.append(error)

    f.postgres.after = held_probe
    f.clock[0] += 1.0
    worker = Thread(target=run)
    worker.start()
    try:
        assert entered.wait(15)
        before = response(f)
        assert before.monitor.sampling
        assert f.owner.invalidate(expected_generation=f.owner.generation)
        during = response(f)
        assert during.capacity is None
        assert during.postgres.generation == before.postgres.generation
        assert not during.postgres.current_generation
    finally:
        release.set()
        worker.join(15)
        assert not worker.is_alive()
    assert failures == []
    assert results[0].reason == "generation_changed"
    assert response(f).reason == "unknown_capacity"
    assert not response(f).postgres.current_generation


def test_future_clock_read_does_not_report_fresh_resource_probe():
    f = fixture()
    warm(f)
    value = response(f, now=1.0)
    assert value.reason == "future_observation" and value.capacity is None
    assert value.sample.age_s is None and not value.sample.within_max_sample_age
    assert value.resource_probe.age_s is None and not value.resource_probe.within_max_sample_age


@pytest.mark.parametrize("value", [True, -1, 1.0, "1"])
def test_quantities_reject_bool_negative_float_and_string(value):
    with pytest.raises(ValidationError):
        ResourceQuantities(memory_bytes=value, cpu_millicores=0, storage_slots=0)


@pytest.mark.parametrize("initial", [False, True])
def test_one_close_uncertainty_read_defines_the_complete_ledger_snapshot(monkeypatch, initial):
    from cli_agent_orchestrator.services import delivery_admission

    f = fixture()
    warm(f)
    reads = []

    def changing_uncertainty():
        reads.append(True)
        return initial if len(reads) == 1 else not initial

    monkeypatch.setattr(delivery_admission, "storage_close_uncertainty", changing_uncertainty)
    captured = f.owner.diagnostic(now=f.clock[0])
    expected = "unknown_capacity" if initial else "capacity_available"
    assert captured.snapshot.reason == captured.decision.reason == expected
    assert captured.snapshot.limit == (0 if initial else 12)
    assert reads == [True]


def test_invalidated_authority_does_not_present_previous_budget_as_current():
    f = fixture()
    warm(f)
    assert response(f).applied_budget.storage_slots == 12
    assert f.owner.invalidate(expected_generation=f.owner.generation)
    value = response(f)
    assert value.reason == "unknown_capacity"
    assert value.applied_budget is None


def test_sampling_configuration_is_tagged_separately_from_current_basis():
    f = fixture()
    assert response(f).sampling_policy is None
    warm(f)
    value = response(f)
    assert value.sampling_policy.current_generation
    assert value.sampling_policy.sample_interval_s == 1.0
    assert value.sampling_policy.healthy_samples_before_growth == 3
    assert value.sampling_policy.postgres_reserve_percent == 20
    assert f.owner.invalidate(expected_generation=f.owner.generation)
    assert not response(f).sampling_policy.current_generation
    assert response(f).applied_budget is None


@pytest.mark.parametrize("configured", [False, True])
def test_sampler_configuration_is_not_reported_before_postgres_confirms(configured):
    f = fixture()
    warm(f)
    entered, release = Event(), Event()
    original = f.postgres.configure
    results, failures = [], []

    def paused(policy):
        entered.set()
        assert release.wait(15), "configuration was not released"
        return original(policy) if configured else False

    def run():
        try:
            results.append(f.monitor.refresh())
        except BaseException as error:
            failures.append(error)

    f.policy["policy"]["postgres_reserve_percent"] = 30
    f.postgres.configure = paused
    f.clock[0] += 1.0
    worker = Thread(target=run)
    worker.start()
    try:
        assert entered.wait(15)
        value = response(f)
        assert value.monitor.sampling
        assert value.sampling_policy.postgres_reserve_percent == 20
        assert value.postgres.reserve_percent == 20
    finally:
        release.set()
        worker.join(15)
        assert not worker.is_alive()
    assert failures == []
    value = response(f)
    assert value.sampling_policy.postgres_reserve_percent == (30 if configured else 20)
    assert results[0].reason == ("recovering" if configured else "probe_busy")


def test_new_sampler_policy_is_separate_from_the_capacity_basis_until_publication():
    f = fixture()
    warm(f)
    assert response(f).sampling_policy.basis_current
    entered, release = Event(), Event()
    results, failures = [], []

    def paused_probe():
        entered.set()
        assert release.wait(15), "probe was not released"

    def run():
        try:
            results.append(f.monitor.refresh())
        except BaseException as error:
            failures.append(error)

    f.policy["policy"]["postgres_reserve_percent"] = 30
    f.postgres.after = paused_probe
    f.clock[0] += 1.0
    worker = Thread(target=run)
    worker.start()
    try:
        assert entered.wait(15)
        value = response(f)
        assert value.sampling_policy.current_generation
        assert value.sampling_policy.postgres_reserve_percent == 30
        assert not value.sampling_policy.basis_current
        assert value.postgres.reserve_percent == 20
        assert value.postgres.current_generation
    finally:
        release.set()
        worker.join(15)
        assert not worker.is_alive()
    assert failures == []
    assert results[0].published
    value = response(f)
    assert value.postgres.reserve_percent == 30
    assert value.sampling_policy.basis_current
