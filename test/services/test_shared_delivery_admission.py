"""Shared delivery coordinator maps exact local leases to shared metadata charges."""

import copy
import os
import threading
import time
from dataclasses import replace

import pytest

from cli_agent_orchestrator.clients.admission_repository import SharedCommitUncertain
from cli_agent_orchestrator.clients.storage_admission import (
    acquire_storage_connection,
    physical_storage_scope,
)
from cli_agent_orchestrator.models.admission_domains import (
    MetadataCoverage,
    MetadataCredit,
    SharedReservation,
)
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.services import shared_delivery_admission as shared
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy, CapacityObservation
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.offline_database

MIB = 1024 * 1024
DOMAIN = "6f1c1b0e-7f43-4b55-9a8e-4e5d4b8f8a01"


def clock():
    return 10.0


def principal(*, admin=True):
    from cli_agent_orchestrator.security.auth import _verified_principal

    return _verified_principal(
        "urn:caos:shared-test", "operator", ["cao:admin"] if admin else ["cao:read"], "jwt"
    )


def binding(**changes):
    fields = dict(
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
    fields.update(changes)
    return ResourceBinding(**fields)


CREDIT = "0b6f1d0c-3a2e-4e7a-9c1d-2f7b8e9a0c11"


def coverage(bound, credit_id=CREDIT):
    credit = MetadataCredit(
        credit_id=credit_id,
        domain_id=DOMAIN,
        instance_id=bound.instance_id,
        process_generation=bound.process_generation,
        process_pid=bound.process_pid,
        process_started_at=float(bound.process_started_at),
        binding_sha256="c" * 64,
        amount=1,
        revision=1,
    )
    return MetadataCoverage(bound, "d" * 64, (DOMAIN,), (credit,))


class FakeRepo:
    def __init__(self, bound=None, credit_id=CREDIT):
        self.bound = bound or binding()
        self.coverage = coverage(self.bound, credit_id)
        self.ops: dict[str, SharedReservation] = {}
        self.events: list[tuple] = []
        self.uncertain_reserve = False
        self.fail_transitions = 0
        self.lose_release_ack = False
        self.guard = threading.Lock()

    def current_binding(self):
        self.events.append(("binding",))
        return self.bound

    def metadata_coverage_current(self):
        self.events.append(("coverage",))
        if self.coverage is None:
            raise RuntimeError("private: no coverage")
        return self.coverage

    def reserve_for_cost(self, operation_id, cost, *, require_metadata=True):
        assert require_metadata is True
        self.events.append(("reserve", operation_id, cost))
        with self.guard:
            self.ops[operation_id] = SharedReservation(operation_id, 1, "preparing")
        if self.uncertain_reserve:
            raise SharedCommitUncertain("private secret")
        return self.ops[operation_id]

    def read_operation(self, operation_id):
        self.events.append(("read", operation_id))
        return self.ops.get(operation_id)

    def transition(self, operation_id, *, expected_revision, state):
        self.events.append(("transition", operation_id, expected_revision, state))
        with self.guard:
            row = self.ops[operation_id]
            if row.revision == expected_revision + 1 and row.state == state:
                return row
            assert row.revision == expected_revision
            if self.fail_transitions:
                self.fail_transitions -= 1
                raise RuntimeError("private driver failure")
            row = SharedReservation(operation_id, expected_revision + 1, state)
            self.ops[operation_id] = row
        if state == "released" and self.lose_release_ack:
            self.lose_release_ack = False
            raise RuntimeError("ack lost")
        return row

    def sql(self):
        return [e for e in self.events if e[0] in ("reserve", "read", "transition", "coverage")]


@pytest.fixture
def registry(monkeypatch):
    original = shared._REGISTRY
    with monkeypatch.context() as patch:
        isolated = shared._NodeRegistry()
        patch.setattr(shared, "_REGISTRY", isolated)
        yield isolated
    assert shared._REGISTRY is original


def make_owner():
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    owner.observe(
        CapacityObservation(
            observed_at=10.0,
            memory_available_bytes=4096 * MIB,
            memory_limit_bytes=4096 * MIB,
            cpu_available_millicores=16000,
            cpu_limit_millicores=16000,
            storage_available_slots=8,
        ),
        now=10.0,
        generation=owner.generation,
    )
    return owner, permit


def authorize(owner, permit, repo, *, retired=lambda: False, who=None, interval=0.01):
    with permit.operation(clock=clock):
        return shared.authorize_shared_delivery(
            principal=who or principal(),
            repository=repo,
            owner=owner,
            control=permit,
            retirement=retired,
            clock=clock,
            retry_interval_s=interval,
        )


def started(owner, receiver="agent-1"):
    lease = owner.try_reserve(receiver, now=10.0).reservation
    assert lease is not None and lease.start()
    return lease


def until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def shutdown(coordinator):
    coordinator.drain(5.0)
    assert not coordinator.worker_alive


def test_success_charges_exact_cost_and_retires_after_last_loan(registry):
    owner, permit = make_owner()
    repo = FakeRepo()
    coordinator = authorize(owner, permit, repo)
    lease = started(owner)
    coordinator.prepare(lease)
    (reserve,) = [e for e in repo.events if e[0] == "reserve"]
    footprint = lease.footprint
    assert (reserve[2].memory_bytes, reserve[2].cpu_millicores, reserve[2].storage_slots) == (
        footprint.memory_bytes,
        footprint.cpu_millicores,
        footprint.storage_slots,
    )
    op = reserve[1]
    assert repo.ops[op].state == "active"
    coordinator.prepare(lease)  # Reentry: no second SQL.
    assert len([e for e in repo.events if e[0] == "reserve"]) == 1
    with physical_storage_scope(lease, clock=clock):
        loan = acquire_storage_connection()
        assert lease.finish() is False
        time.sleep(0.05)
        assert not [e for e in repo.events if e[0] == "read"]
        assert loan.finish()
    assert until(lambda: coordinator.pending_count == 0)
    assert repo.ops[op] == SharedReservation(op, 4, "released")
    assert owner.snapshot(now=10.0).inflight == 0
    shutdown(coordinator)


def test_ack_lost_preparing_preserves_id_and_never_authorizes(registry):
    owner, permit = make_owner()
    repo = FakeRepo()
    repo.uncertain_reserve = True
    coordinator = authorize(owner, permit, repo)
    lease = started(owner)
    with pytest.raises(shared.SharedDeliveryUnavailable) as raised:
        coordinator.prepare(lease)
    assert raised.value.__cause__ is None and "secret" not in str(raised.value)
    assert coordinator.uncertain_count == 1
    with pytest.raises(shared.SharedDeliveryUnavailable):
        coordinator.prepare(lease)  # No body after uncertain outcome.
    (op,) = repo.ops
    assert len([e for e in repo.events if e[0] == "reserve"]) == 1
    assert lease.finish() is False
    assert until(lambda: coordinator.pending_count == 0)
    assert repo.ops[op].state == "released"
    assert ("read", op) in repo.events
    shutdown(coordinator)


def test_activation_error_is_retained_then_cleaned(registry):
    owner, permit = make_owner()
    repo = FakeRepo()
    repo.fail_transitions = 1
    coordinator = authorize(owner, permit, repo)
    lease = started(owner)
    with pytest.raises(shared.SharedDeliveryUnavailable):
        coordinator.prepare(lease)
    assert coordinator.pending_count == 1
    (op,) = repo.ops
    lease.finish()
    assert until(lambda: coordinator.pending_count == 0)
    assert repo.ops[op] == SharedReservation(op, 2, "released")  # preparing -> released.
    shutdown(coordinator)


def test_lost_release_ack_retries_exact_id(registry):
    owner, permit = make_owner()
    repo = FakeRepo()
    repo.lose_release_ack = True
    coordinator = authorize(owner, permit, repo)
    lease = started(owner)
    coordinator.prepare(lease)
    (op,) = repo.ops
    lease.finish()
    assert until(lambda: coordinator.pending_count == 0)
    released = [e for e in repo.events if e[0] == "transition" and e[3] == "released"]
    assert all(e[1] == op for e in released) and len(released) == 1
    assert ("read", op) in repo.events and repo.ops[op].state == "released"
    shutdown(coordinator)


def test_concurrent_prepare_waits_and_does_not_double_charge(registry):
    owner, permit = make_owner()
    repo = FakeRepo()
    gate, entered = threading.Event(), threading.Event()
    real = repo.reserve_for_cost

    def slow(*args, **kwargs):
        entered.set()
        gate.wait(5)
        return real(*args, **kwargs)

    repo.reserve_for_cost = slow
    coordinator = authorize(owner, permit, repo)
    lease = started(owner)
    first = threading.Thread(target=coordinator.prepare, args=(lease,))
    first.start()
    assert entered.wait(5)
    results = []
    second = threading.Thread(target=lambda: results.append(coordinator.prepare(lease)))
    second.start()
    time.sleep(0.05)
    assert second.is_alive()
    gate.set()
    first.join(5)
    second.join(5)
    assert results == [None]
    assert len(repo.ops) == 1
    lease.finish()
    assert until(lambda: coordinator.pending_count == 0)
    shutdown(coordinator)


def test_stop_admits_cleanup_but_denies_fresh_without_sql(registry):
    owner, permit = make_owner()
    repo = FakeRepo()
    coordinator = authorize(owner, permit, repo)
    lease = started(owner)
    coordinator.prepare(lease)
    fresh = started(owner, "agent-2")
    coordinator.stop_accepting()
    before = len(repo.sql())
    with pytest.raises(shared.SharedDeliveryUnavailable):
        coordinator.prepare(fresh)
    assert len(repo.sql()) == before
    with pytest.raises(shared.SharedDeliveryDrainIncomplete):
        coordinator.drain(0.05)
    assert coordinator.worker_alive and coordinator.pending_count == 1
    lease.finish()
    fresh.finish()
    shutdown(coordinator)
    assert coordinator.pending_count == 0


def test_uncertain_control_close_keeps_hold(registry, monkeypatch):
    owner, permit = make_owner()
    repo = FakeRepo()
    coordinator = authorize(owner, permit, repo)
    lease = started(owner)
    coordinator.prepare(lease)
    with monkeypatch.context() as patch:
        patch.setattr(shared, "storage_close_uncertainty", lambda: True)
        lease.finish()
        time.sleep(0.1)
        assert coordinator.pending_count == 1
        assert not [e for e in repo.events if e[0] == "read"]
        assert owner.snapshot(now=10.0).inflight == 1
    assert until(lambda: coordinator.pending_count == 0)
    shutdown(coordinator)


def test_second_owner_same_binding_denied_before_sql(registry):
    first_owner, first_permit = make_owner()
    repo = FakeRepo()
    coordinator = authorize(first_owner, first_permit, repo)
    owner, permit = make_owner()
    other = FakeRepo()
    with pytest.raises(shared.SharedDeliveryUnavailable):
        authorize(owner, permit, other)
    assert other.sql() == []
    with pytest.raises(shared.SharedDeliveryUnavailable):
        authorize(first_owner, first_permit, FakeRepo())  # One per owner.
    shutdown(coordinator)
    third = FakeRepo()
    with pytest.raises(shared.SharedDeliveryUnavailable):
        authorize(owner, permit, third)  # Drained is not retired without callback.
    assert third.sql() == []


def test_retired_rival_is_reclaimed_only_by_callback(registry):
    first_owner, first_permit = make_owner()
    retired = threading.Event()
    coordinator = authorize(first_owner, first_permit, FakeRepo(), retired=retired.is_set)
    shutdown(coordinator)
    owner, permit = make_owner()
    with pytest.raises(shared.SharedDeliveryUnavailable):
        authorize(owner, permit, FakeRepo())
    retired.set()
    shutdown(authorize(owner, permit, FakeRepo()))


def test_foreign_pid_binding_and_copy_denied(registry):
    owner, permit = make_owner()
    repo = FakeRepo(binding(process_pid=os.getpid() + 1))
    with pytest.raises(shared.SharedDeliveryUnavailable):
        authorize(owner, permit, repo)
    assert repo.sql() == []
    registry._pid = os.getpid() + 1
    with pytest.raises(shared.SharedDeliveryUnavailable):
        registry.current()


def test_binding_change_and_missing_coverage_have_no_fallback(registry):
    owner, permit = make_owner()
    repo = FakeRepo()
    repo.coverage = None
    with pytest.raises(shared.SharedDeliveryUnavailable) as raised:
        authorize(owner, permit, repo)
    assert raised.value.__cause__ is None
    changed = FakeRepo()
    changed.coverage = coverage(binding(resource_view_sha256="e" * 64))
    with pytest.raises(shared.SharedDeliveryUnavailable):
        authorize(owner, permit, changed)
    shutdown(authorize(owner, permit, FakeRepo()))  # Abandoned keys were withdrawn.


def test_forged_grant_actor_and_scope_denied(registry):
    owner, permit = make_owner()
    repo = FakeRepo()
    with pytest.raises(PermissionError):
        authorize(owner, permit, repo, who=principal(admin=False))
    with pytest.raises(PermissionError):
        authorize(owner, permit, repo, who=object())
    assert repo.events == []
    with pytest.raises(shared.SharedDeliveryUnavailable):
        shared.authorize_shared_delivery(  # Outside the exact control scope.
            principal=principal(),
            repository=repo,
            owner=owner,
            control=permit,
            retirement=lambda: False,
        )
    assert repo.events == []
    entry = shared._Entry(owner, permit, lambda: False, ())
    forged = shared._CoverageGrant(repo.coverage, entry)
    with pytest.raises(shared.SharedDeliveryUnavailable):
        shared.SharedDeliveryCoordinator(
            forged, repository=repo, clock=clock, retry_interval_s=0.01
        )
    registry.issue(forged)
    with pytest.raises(TypeError):
        copy.copy(forged)
    with pytest.raises(shared.SharedDeliveryUnavailable):
        shared.SharedDeliveryCoordinator(
            replace(repo.coverage), repository=repo, clock=clock, retry_interval_s=0.01
        )


def test_foreign_lease_denied_before_sql(registry):
    owner, permit = make_owner()
    repo = FakeRepo()
    coordinator = authorize(owner, permit, repo)
    other_owner, _ = make_owner()
    foreign = started(other_owner)
    before = len(repo.sql())
    with pytest.raises(shared.SharedDeliveryUnavailable):
        coordinator.prepare(foreign)
    assert len(repo.sql()) == before
    foreign.finish()
    shutdown(coordinator)


def test_distinct_bindings_in_one_realm_run_in_parallel(registry):
    first_owner, first_permit = make_owner()
    first = authorize(first_owner, first_permit, FakeRepo())
    owner, permit = make_owner()
    other_binding = binding(
        instance_id="1d3e9a52-0d8f-4b8e-8a64-6f7f3b1c2d01",
        process_generation="7a2b4c6d-8e9f-4a1b-9c2d-3e4f5a6b7c8d",
    )
    repo = FakeRepo(other_binding, "5e8a2c1b-7d3f-4e9a-8b1c-2d3e4f5a6b7c")
    second = authorize(owner, permit, repo)  # Same store/target, own binding and credit.
    lease = started(owner)
    second.prepare(lease)
    assert [e[0] for e in repo.sql()] == ["coverage", "reserve", "transition"]
    lease.finish()
    assert until(lambda: second.pending_count == 0)
    third_owner, third_permit = make_owner()
    same_credit = FakeRepo(binding(instance_id="2c4d6e8f-1a3b-4c5d-8e7f-9a0b1c2d3e4f"), CREDIT)
    with pytest.raises(shared.SharedDeliveryUnavailable):
        authorize(third_owner, third_permit, same_credit)  # Same credit, other owner.
    assert [e[0] for e in same_credit.sql()] == ["coverage"]
    shutdown(first)
    shutdown(second)


def test_failed_claim_with_live_control_loan_is_not_abandoned(registry):
    owner, permit = make_owner()
    repo = FakeRepo()
    loans = []

    def leaking():
        loans.append(acquire_storage_connection())
        raise RuntimeError("private: coverage failed")

    repo.metadata_coverage_current = leaking
    with pytest.raises(shared.SharedDeliveryUnavailable):
        authorize(owner, permit, repo)
    assert loans and loans[0] is not None
    retired = threading.Event()
    rival_owner, rival_permit = make_owner()
    rival = FakeRepo()
    with pytest.raises(shared.SharedDeliveryUnavailable):
        authorize(rival_owner, rival_permit, rival, retired=retired.is_set)
    assert rival.sql() == []
    assert loans[0].finish()
    with pytest.raises(shared.SharedDeliveryUnavailable):
        authorize(rival_owner, rival_permit, rival)  # Closed, but Runtime not retired.
    assert rival.sql() == []


def test_failed_claim_rival_reclaims_only_after_runtime_witness(registry):
    owner, permit = make_owner()
    repo = FakeRepo()
    repo.coverage = None
    retired = threading.Event()
    with pytest.raises(shared.SharedDeliveryUnavailable):
        authorize(owner, permit, repo, retired=retired.is_set)
    rival_owner, rival_permit = make_owner()
    with pytest.raises(shared.SharedDeliveryUnavailable):
        authorize(rival_owner, rival_permit, FakeRepo())
    retired.set()
    shutdown(authorize(rival_owner, rival_permit, FakeRepo()))


def test_missing_coverage_retries_same_owner_without_restart(registry):
    owner, permit = make_owner()
    repo = FakeRepo()
    repo.coverage = None
    with pytest.raises(shared.SharedDeliveryUnavailable):
        authorize(owner, permit, repo)
    repo.coverage = coverage(repo.bound)
    coordinator = authorize(owner, permit, repo)
    lease = started(owner)
    coordinator.prepare(lease)
    lease.finish()
    assert until(lambda: coordinator.pending_count == 0)
    shutdown(coordinator)


def test_active_reentry_after_finish_is_denied_without_sql(registry):
    owner, permit = make_owner()
    repo = FakeRepo()
    gate, entered = threading.Event(), threading.Event()
    real = repo.read_operation

    def blocked(op):
        entered.set()
        gate.wait(5)
        return real(op)

    repo.read_operation = blocked
    coordinator = authorize(owner, permit, repo)
    lease = started(owner)
    coordinator.prepare(lease)
    lease.finish()
    assert entered.wait(5)
    before = len(repo.sql())
    with pytest.raises(shared.SharedDeliveryUnavailable):
        coordinator.prepare(lease)  # Retiring lease: no body reentry.
    assert len(repo.sql()) == before
    gate.set()
    assert until(lambda: coordinator.pending_count == 0)
    shutdown(coordinator)


def test_registration_race_creates_one_hold(registry, monkeypatch):
    owner, permit = make_owner()
    repo = FakeRepo()
    coordinator = authorize(owner, permit, repo)
    lease = started(owner)
    real = type(lease).hold_retirement
    gate, entered = threading.Event(), threading.Event()
    calls = []

    def delayed(self):
        calls.append(self)
        if len(calls) == 1:
            entered.set()
            gate.wait(5)
        return real(self)

    results = []

    def run():
        try:
            results.append(coordinator.prepare(lease))
        except Exception as error:  # pragma: no cover - recorded for assertion
            results.append(error)

    with monkeypatch.context() as patch:
        patch.setattr(type(lease), "hold_retirement", delayed)
        first = threading.Thread(target=run)
        first.start()
        assert entered.wait(5)
        second = threading.Thread(target=run)
        second.start()
        time.sleep(0.05)
        gate.set()
        first.join(5)
        second.join(5)
    assert type(lease).hold_retirement is real
    assert results == [None, None]
    assert len(calls) == 1 and len(repo.ops) == 1
    lease.finish()
    assert until(lambda: coordinator.pending_count == 0)
    shutdown(coordinator)


def test_cleanup_consumes_only_ready_holds(registry, monkeypatch):
    from cli_agent_orchestrator.services.delivery_admission import DeliveryRetirementHold

    owner, permit = make_owner()
    repo = FakeRepo()
    coordinator = authorize(owner, permit, repo, interval=30.0)
    leases = [started(owner, f"agent-{n}") for n in range(6)]
    for lease in leases:
        coordinator.prepare(lease)
    real = DeliveryRetirementHold.physical_ready
    seen = []

    def counted(self):
        seen.append(self._lease)
        return real.fget(self)

    with monkeypatch.context() as patch:
        patch.setattr(DeliveryRetirementHold, "physical_ready", property(counted))
        leases[0].finish()
        assert until(lambda: coordinator.pending_count == 5)
        assert seen and all(lease is leases[0] for lease in seen)
    assert DeliveryRetirementHold.physical_ready is real
    for lease in leases[1:]:
        lease.finish()
    assert until(lambda: coordinator.pending_count == 0)
    shutdown(coordinator)


def test_worker_start_failure_keeps_claim_for_exact_retry(registry, monkeypatch):
    owner, permit = make_owner()
    failed = []
    real = threading.Thread.start

    def broken(self):
        failed.append(self._target.__self__)
        raise RuntimeError("cannot start")

    with monkeypatch.context() as patch:
        patch.setattr(shared.threading.Thread, "start", broken)
        with pytest.raises(shared.SharedDeliveryUnavailable):
            authorize(owner, permit, FakeRepo())
    assert threading.Thread.start is real
    (old,) = failed
    coordinator = authorize(owner, permit, FakeRepo())
    lease = started(owner)
    with pytest.raises(shared.SharedDeliveryUnavailable):
        old.prepare(lease)
    coordinator.prepare(lease)
    lease.finish()
    assert until(lambda: coordinator.pending_count == 0)
    shutdown(coordinator)


def test_inherited_registry_denies_issue_and_keeps_strong_grants(registry):
    owner, permit = make_owner()
    entry = shared._Entry(owner, permit, lambda: False, ())
    grant = shared._CoverageGrant(FakeRepo().coverage, entry)
    registry.issue(grant)
    assert registry._grants[id(grant)] is grant  # Strong, identity-bound.
    registry._pid = os.getpid() + 1
    with pytest.raises(shared.SharedDeliveryUnavailable):
        registry.issue(shared._CoverageGrant(FakeRepo().coverage, entry))
    with pytest.raises(shared.SharedDeliveryUnavailable):
        registry.redeem(grant)
    registry._pid = os.getpid()
    assert registry.redeem(grant) and not registry.redeem(grant)


def test_worker_post_start_failure_joins_before_exact_owner_retry(registry, monkeypatch):
    owner, permit = make_owner()
    failed = []
    real_start = threading.Thread.start

    def started_then_failed(thread):
        failed.append(thread._target.__self__)
        real_start(thread)
        raise RuntimeError("failure after actual worker start")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(shared.threading.Thread, "start", started_then_failed)
            with pytest.raises(shared.SharedDeliveryUnavailable):
                authorize(owner, permit, FakeRepo(), interval=30.0)
        (old,) = failed
        assert not old.worker_alive, "failed constructor left a live cleanup consumer"
        coordinator = authorize(owner, permit, FakeRepo())
        try:
            lease = started(owner)
            coordinator.prepare(lease)
            lease.finish()
            assert until(lambda: coordinator.pending_count == 0)
        finally:
            shutdown(coordinator)
    finally:
        for old in failed:
            old.stop_accepting()
            old._worker.join(5)
            assert not old.worker_alive
    assert threading.Thread.start is real_start
