"""Protected infrastructure uses the same physical owner, never data authority."""

import copy

import pytest

from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    acquire_storage_connection,
)
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy, ResourceAmounts
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.offline_database


def activated():
    return DeliveryAdmission.with_storage_control(AdmissionPolicy())


def test_unknown_capacity_allows_only_exact_control_and_one_physical_loan():
    owner, permit = activated()
    assert owner.try_reserve("receiver", now=10).reservation is None
    assert owner.try_reserve_storage(now=10).reservation is None
    with permit.operation(clock=lambda: 10):
        status = owner.snapshot(now=10)
        assert status.control_storage_slots == 1
        assert status.control_inflight == status.started == status.storage_only_started == 1
        assert status.delivery_inflight == 0
        assert status.committed == ResourceAmounts()
        loan = acquire_storage_connection()
        assert loan is not None
        assert owner.snapshot(now=10).storage_connection_loans == 1
        assert loan.finish()
    assert owner.snapshot(now=10).inflight == 0
    assert owner.try_reserve_storage(now=10).reservation is None


def test_copy_cannot_create_or_retire_control_ownership():
    owner, permit = activated()
    with pytest.raises(StorageAdmissionUnavailable):
        with copy.copy(permit).operation(clock=lambda: 10):
            pytest.fail("copied permit entered")
    with permit.operation(clock=lambda: 10):
        loan = acquire_storage_connection()
        assert loan is not None
        assert not copy.copy(loan).finish()
        lease = loan._lease
        assert not copy.copy(lease).finish_after_storage()
        assert owner.snapshot(now=10).control_inflight == 1
        assert loan.finish()
    assert owner.snapshot(now=10).inflight == 0


def test_failed_close_keeps_control_credit_until_exact_physical_retirement():
    owner, permit = activated()
    with permit.operation(clock=lambda: 10):
        loan = acquire_storage_connection()
        assert loan is not None
    status = owner.snapshot(now=10)
    assert status.control_inflight == status.storage_retirement_pending == 1
    assert status.storage_completion_credits == status.storage_connection_loans == 1
    with pytest.raises(StorageAdmissionUnavailable):
        with permit.operation(clock=lambda: 10):
            pytest.fail("uncertain credit reused")
    owner.stop_accepting()
    assert owner.snapshot(now=10).control_inflight == 1
    with pytest.raises(RuntimeError):
        owner.start_accepting()
    assert loan.finish()
    assert owner.snapshot(now=10).inflight == 0
    with pytest.raises(StorageAdmissionUnavailable):
        with permit.operation(clock=lambda: 10):
            pytest.fail("stopped owner admitted control")


def test_swallowed_second_loan_denial_still_fails_control_result():
    owner, permit = activated()
    with pytest.raises(StorageAdmissionUnavailable):
        with permit.operation(clock=lambda: 10):
            loan = acquire_storage_connection()
            assert loan is not None
            with pytest.raises(StorageAdmissionUnavailable):
                acquire_storage_connection()
            assert loan.finish()
    assert owner.snapshot(now=10).inflight == 0


def test_nested_control_does_not_mint_another_credit():
    owner, permit = activated()
    with permit.operation(clock=lambda: 10):
        with pytest.raises(StorageAdmissionUnavailable):
            with permit.operation(clock=lambda: 10):
                pytest.fail("nested operation admitted")
        assert owner.snapshot(now=10).control_inflight == 1
    assert owner.snapshot(now=10).inflight == 0


def test_foreign_process_rejected_before_inherited_guard(monkeypatch):
    owner, permit = activated()

    class ForbiddenGuard:
        def __enter__(self):
            pytest.fail("foreign process touched inherited guard")

    monkeypatch.setattr(owner, "_process_guard", ForbiddenGuard())
    monkeypatch.setattr(owner, "_pid", -1)
    with pytest.raises(StorageAdmissionUnavailable):
        with permit.operation(clock=lambda: 10):
            pytest.fail("foreign permit admitted")


def protected_monitor():
    from test.services.test_admission_monitor import fixture

    from cli_agent_orchestrator.services.admission_monitor import AdmissionMonitor

    f = fixture(local_storage_capacity=None)
    f.owner, permit = activated()
    f.monitor = AdmissionMonitor(
        f.owner,
        storage_control=permit,
        policy_loader=f.monitor._policy_loader,
        bootstrap=f.bootstrap,
        postgres=f.postgres,
        scope_id="protected-control-test",
        clock=lambda: f.clock[0],
        resource_factory=f.factory,
    )
    return f


@pytest.mark.parametrize(
    "slots, pressure, expected", [(1, 12, 0), (2, 12, 1), (4, 12, 3), (12, 2, 2)]
)
def test_control_reserve_precedes_data_budget_recovery_and_ceiling(slots, pressure, expected):
    from dataclasses import replace
    from test.services.test_admission_monitor import tick

    f = protected_monitor()
    f.repository.latest = replace(
        f.record, claims=replace(f.record.claims, storage_budget_slots=slots)
    )
    f.postgres.slots = pressure
    for _ in range(3):
        result = tick(f)
        assert result.published
    ledger = f.owner.diagnostic(now=f.clock[0])
    assert ledger.budget.storage_slots == slots - 1
    assert ledger.ceiling.storage_slots == expected
    assert ledger.snapshot.available_slots == expected
    assert ledger.snapshot.control_storage_slots == 1
    assert ledger.snapshot.control_inflight == 0


def test_all_three_sql_stages_have_one_registered_control_credit():
    f = protected_monitor()
    stages = []
    load = f.bootstrap.load_current

    def witness(stage):
        state = f.owner.snapshot(now=f.clock[0])
        assert state.control_inflight == 1
        assert state.committed == ResourceAmounts()
        loan = acquire_storage_connection()
        assert loan is not None
        assert f.owner.snapshot(now=f.clock[0]).control_connection_loans == 1
        assert loan.finish()
        stages.append(stage)

    def observed_load():
        witness("authority")
        return load()

    f.bootstrap.load_current = observed_load
    f.postgres.after = lambda: witness("pressure")
    assert f.monitor.refresh().published
    assert stages == ["authority", "pressure", "authority"]
    assert f.owner.snapshot(now=f.clock[0]).inflight == 0


def test_authority_read_serializes_without_invalidating_capacity():
    f = protected_monitor()
    generation = f.owner.generation
    with f.monitor.authority_read():
        assert f.monitor.refresh().reason == "probe_busy"
        f.monitor.control_call(lambda: None)
        assert f.owner.generation == generation
    assert f.monitor.refresh().published


def test_control_reuses_credit_only_after_exact_sequential_close():
    owner, permit = activated()
    with permit.operation(clock=lambda: 10):
        first = acquire_storage_connection()
        assert first is not None and first.finish()
        second = acquire_storage_connection()
        assert second is not None and second is not first
        assert not first.finish()
        assert owner.snapshot(now=10).control_connection_loans == 1
        assert second.finish()
    assert owner.snapshot(now=10).inflight == 0


def test_cached_diagnostic_identifies_active_control_without_data_cost():
    from cli_agent_orchestrator.services.admission_diagnostics import diagnostics_response

    f = protected_monitor()
    with f.monitor._storage_control.operation(clock=lambda: f.clock[0]):
        loan = acquire_storage_connection()
        assert loan is not None
        try:
            response = diagnostics_response(f.monitor.diagnostic(now=f.clock[0]), now=f.clock[0])
            assert response.operations.control_storage_slots == 1
            assert response.operations.control_inflight == 1
            assert response.operations.control_connection_loans == 1
            assert response.operations.inflight_deliveries == 0
            assert response.committed.storage_slots == 0
            assert response.applied_budget is None
        finally:
            assert loan.finish()
    assert f.owner.snapshot(now=f.clock[0]).inflight == 0


@pytest.mark.asyncio
async def test_control_preflight_denial_runs_no_callback_and_does_not_poison_authority(monkeypatch):
    from test.services.test_inbox_admission_runtime import principal

    from cli_agent_orchestrator.services import delivery_admission
    from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime
    from cli_agent_orchestrator.services.inbox_service import InboxService

    f = protected_monitor()

    async def dormant_runner():
        import asyncio

        await asyncio.Event().wait()

    f.monitor._run = dormant_runner
    value = InboxAdmissionRuntime(
        InboxService(clock=lambda: f.clock[0]),
        monitor=f.monitor,
        bootstrap=f.bootstrap,
        clock=lambda: f.clock[0],
        drain_timeout_s=10,
    )
    callbacks = []
    await value.start()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(delivery_admission, "storage_close_uncertainty", lambda: True)
            with pytest.raises(StorageAdmissionUnavailable):
                await value._execute(
                    principal(), lambda: callbacks.append("must not run"), mutating=True
                )
            assert callbacks == []
            assert not f.monitor._authority_state.uncertain
            assert f.owner.snapshot(now=f.clock[0]).control_inflight == 0
        assert await value._execute(principal(), lambda: "committed", mutating=True) == "committed"
        assert not f.monitor._authority_state.uncertain
    finally:
        await value.stop()


@pytest.mark.asyncio
async def test_failure_inside_control_callback_still_latches_uncertain_authority():
    from test.services.test_inbox_admission_runtime import principal

    from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime
    from cli_agent_orchestrator.services.inbox_service import InboxService

    f = protected_monitor()

    async def dormant_runner():
        import asyncio

        await asyncio.Event().wait()

    f.monitor._run = dormant_runner
    value = InboxAdmissionRuntime(
        InboxService(clock=lambda: f.clock[0]),
        monitor=f.monitor,
        bootstrap=f.bootstrap,
        clock=lambda: f.clock[0],
        drain_timeout_s=10,
    )
    callbacks = []

    def uncertain():
        callbacks.append("entered")
        raise OSError("injected post-entry write failure")

    await value.start()
    try:
        with pytest.raises(OSError, match="post-entry"):
            await value._execute(principal(), uncertain, mutating=True)
        assert callbacks == ["entered"]
        assert f.monitor._authority_state.uncertain
        assert f.owner.snapshot(now=f.clock[0]).inflight == 0
    finally:
        await value.stop()
