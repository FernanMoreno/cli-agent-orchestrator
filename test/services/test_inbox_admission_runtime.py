"""Operational ownership must span API cancellation and physical authority I/O."""

import asyncio
import importlib
from dataclasses import replace
from test.services.test_admission_monitor import fixture, warm
from threading import Event
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.security.auth import _verified_principal
from cli_agent_orchestrator.services.inbox_service import InboxService

pytestmark = pytest.mark.offline_database


def operational_fixture():
    from cli_agent_orchestrator.clients.admission_bootstrap_repository import (
        EnvelopeRevisionConflict,
    )

    f = fixture()

    def append(target, *, binding, claims, expected_revision, principal_id, active=True):
        latest = f.repository.latest
        revision = latest.revision if latest is not None else 0
        if revision != expected_revision:
            raise EnvelopeRevisionConflict("revision conflict")
        f.repository.latest = replace(
            f.record,
            binding=binding,
            claims=claims,
            revision=revision + 1,
            active=active,
            actor_principal_id=principal_id,
        )
        return f.repository.latest

    f.repository.append = append
    return f


def runtime():
    api = importlib.import_module("cli_agent_orchestrator.services.inbox_admission_runtime")
    f = operational_fixture()
    inbox = InboxService(clock=lambda: f.clock[0])
    value = api.InboxAdmissionRuntime(
        inbox,
        monitor=f.monitor,
        bootstrap=f.bootstrap,
        clock=lambda: f.clock[0],
        drain_timeout_s=10,
    )
    return value, f, inbox


def principal():
    return _verified_principal("urn:caos:test", "operator", ["cao:admin"], "jwt")


@pytest.mark.parametrize("value", ["", "relative.json", "   "])
def test_present_invalid_activation_never_falls_back_to_unbound(value, monkeypatch):
    api = importlib.import_module("cli_agent_orchestrator.services.inbox_admission_runtime")
    monkeypatch.setenv("CAO_ADMISSION_POLICY_FILE", value)
    with pytest.raises(ValueError):
        api.runtime_from_environment(InboxService())


def test_absent_activation_preserves_compatibility(monkeypatch):
    api = importlib.import_module("cli_agent_orchestrator.services.inbox_admission_runtime")
    monkeypatch.delenv("CAO_ADMISSION_POLICY_FILE", raising=False)
    assert api.runtime_from_environment(InboxService()) is None


@pytest.mark.asyncio
async def test_runtime_binds_owner_and_rejects_replacement_until_retired():
    value, f, inbox = runtime()
    await value.start()
    try:
        assert inbox._admission is f.owner
        with pytest.raises(RuntimeError, match="retir"):
            inbox.bind_admission(None)
    finally:
        await value.stop()
    assert value.retired
    inbox.bind_admission(None)
    assert inbox._admission is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["context", "thread"])
async def test_control_setup_failure_before_start_cannot_leak_token_or_future(monkeypatch, stage):
    api = importlib.import_module("cli_agent_orchestrator.services.inbox_admission_runtime")
    value, f, _ = runtime()
    await value.start()

    def fail(*_a, **_k):
        raise RuntimeError("controlled-prestart-setup")

    try:
        with monkeypatch.context() as patch:
            if stage == "context":
                patch.setattr(api, "contextvars", SimpleNamespace(copy_context=fail))
            else:
                patch.setattr(api, "threading", SimpleNamespace(Thread=fail))
            with pytest.raises(RuntimeError, match="controlled-prestart-setup"):
                await value.attest(
                    principal=principal(), claims=f.record.claims, expected_revision=1
                )
        assert (await value.registered(principal=principal())).revision == 1
        assert f.monitor.refresh().reason != "authority_changing"
    finally:
        # This injection proves no worker was created. Retire only those unused
        # test resources on a failing candidate so teardown itself cannot hang.
        call = value._call
        if call is not None and call.future.cancel():
            call.finished.set()
        for token in tuple(f.monitor._authority_tokens):
            f.monitor.cancel_authority_change(token)
        await value.stop()


@pytest.mark.asyncio
async def test_cancelled_operator_awaiter_cannot_end_mutation_or_runtime_early():
    value, f, _ = runtime()
    entered, release = Event(), Event()
    original = f.repository.append

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    f.repository.append = held
    await value.start()
    request = asyncio.create_task(
        value.attest(principal=principal(), claims=f.record.claims, expected_revision=1)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert f.monitor.refresh().reason == "authority_changing"
        stop = asyncio.create_task(value.stop())
        await asyncio.sleep(0)
        assert not stop.done() and not value.retired
        release.set()
        await asyncio.wait_for(stop, 10)
        assert f.repository.latest.revision == 2
        assert value.retired
    finally:
        release.set()
        await value.stop()


def test_authority_pending_gate_fences_old_capacity_before_physical_write():
    f = fixture()
    warm(f)
    token = f.monitor.begin_authority_change()
    assert f.owner.snapshot(now=f.clock[0]).available_slots == 0
    assert f.monitor.refresh().reason == "authority_changing"
    with f.monitor.authority_change(token):
        assert f.owner.try_reserve("denied", now=f.clock[0]).reservation is None
    assert f.owner.snapshot(now=f.clock[0]).available_slots == 0


def test_uncertain_write_remains_closed_without_automatic_retry():
    f = fixture()
    token = f.monitor.begin_authority_change()
    with pytest.raises(OSError):
        with f.monitor.authority_change(token):
            raise OSError("private-writer-canary")
    assert f.monitor.refresh().reason == "authority_uncertain"
    with pytest.raises(RuntimeError, match="uncertain"):
        f.monitor.begin_authority_change()


def test_expected_conflict_does_not_turn_into_unknown_commit():
    from cli_agent_orchestrator.clients.admission_bootstrap_repository import (
        EnvelopeRevisionConflict,
    )

    f = fixture()
    token = f.monitor.begin_authority_change()
    with pytest.raises(EnvelopeRevisionConflict):
        with f.monitor.authority_change(token):
            raise EnvelopeRevisionConflict("controlled")
    token = f.monitor.begin_authority_change()
    with f.monitor.authority_change(token):
        pass


def test_idle_pool_cache_is_capacity_while_active_retired_and_opening_are_charged():
    from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
        LegacyMemoryAuditConnectionManager,
    )

    pool = LegacyMemoryAuditConnectionManager()
    pool._entries = {i: SimpleNamespace(leased=False, retired=False) for i in range(4)}
    pool._physical_count = 4
    try:
        assert pool.admission_slots() == 4
        pool._entries[0].leased = True
        pool._entries[1].retired = True
        assert pool.admission_slots() == 2
        pool._closed = True
        assert pool.admission_slots() is None
    finally:
        pool._entries.clear()
        pool._physical_count = 0


@pytest.mark.asyncio
async def test_monitor_notifies_after_background_cycle():
    f = fixture()
    signal = Event()
    f.monitor.set_on_change(signal.set)
    await f.monitor.start()
    try:
        assert await asyncio.to_thread(signal.wait, 5)
    finally:
        await f.monitor.stop()


@pytest.mark.asyncio
async def test_runtime_cannot_retire_shared_resource_lease_still_owned_by_work():
    from cli_agent_orchestrator.services.inbox_admission_runtime import AdmissionRuntimeUnavailable

    value, f, _ = runtime()
    await value.start()
    warm(f)
    lease = f.monitor.owner.try_reserve("work-physical", now=f.clock[0]).reservation
    assert lease is not None and lease.start()
    try:
        with pytest.raises(AdmissionRuntimeUnavailable, match="physical delivery reservations"):
            await value.stop()
        assert not value.retired
        assert f.monitor.owner.snapshot(now=f.clock[0]).inflight == 1
    finally:
        lease.finish()
        await value.stop()
    assert value.retired


@pytest.mark.parametrize("deliveries", [5, 20, 40])
def test_runtime_factory_does_not_use_audit_pool_as_global_delivery_capacity(
    deliveries, monkeypatch, tmp_path
):
    api = importlib.import_module("cli_agent_orchestrator.services.inbox_admission_runtime")
    audit = importlib.import_module(
        "cli_agent_orchestrator.services.legacy_memory_audit_repository"
    )
    auth = importlib.import_module("cli_agent_orchestrator.security.auth")
    f = fixture()
    slots = 2 * deliveries
    f.repository.latest = replace(
        f.record, claims=replace(f.record.claims, storage_budget_slots=slots + 1)
    )
    f.postgres.slots = slots
    calls = []
    pool = SimpleNamespace(admission_slots=lambda: 4)

    def audit_manager():
        calls.append("audit domain queried")
        return pool

    create_monitor = api.AdmissionMonitor

    def monitor(owner, **kwargs):
        return create_monitor(owner, **kwargs, clock=lambda: f.clock[0], resource_factory=f.factory)

    monkeypatch.setenv("CAO_ADMISSION_POLICY_FILE", str(tmp_path / "operator.json"))
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(audit, "_legacy_memory_audit_manager", audit_manager)
    monkeypatch.setattr(api, "read_private_operator_policy", lambda path: f.policy)
    monkeypatch.setattr(api, "_PROCESS_PRESSURE", f.postgres)
    monkeypatch.setattr(api, "AdmissionMonitor", monitor)
    value = api.runtime_from_environment(InboxService(), bootstrap_factory=lambda: f.bootstrap)
    assert value is not None
    assert value.bootstrap is f.bootstrap
    f.monitor = value.monitor
    f.owner = value.monitor.owner
    leases = []
    try:
        warm(f)
        for index in range(deliveries):
            lease = f.owner.try_reserve(str(index), now=f.clock[0], storage_slots=2).reservation
            assert lease is not None
            leases.append(lease)
        assert calls == []
        assert f.owner.snapshot(now=f.clock[0]).delivery_inflight == deliveries
        assert f.owner.try_reserve("excess", now=f.clock[0], storage_slots=2).reservation is None
    finally:
        for lease in leases:
            assert lease.cancel_before_start()


@pytest.mark.parametrize("detached", [False, True])
def test_forked_runtime_pressure_owner_requires_confirmed_detachment(monkeypatch, detached):
    from test.clients.test_storage_admission import fork_with_held_locks

    from cli_agent_orchestrator.clients import storage_admission

    api = importlib.import_module("cli_agent_orchestrator.services.inbox_admission_runtime")
    parent = api._PROCESS_PRESSURE
    if not detached:
        monkeypatch.setattr(storage_admission, "_FORK_STORAGE_DETACHER", lambda: False)

    def child():
        assert storage_admission._PROCESS_STORAGE_CLOSES.process_current is detached
        if detached:
            assert api._PROCESS_PRESSURE is not parent
            assert api._PROCESS_PRESSURE.retire_owned() is True
        else:
            assert api._PROCESS_PRESSURE is parent
        assert parent.retire_owned() is False

    fork_with_held_locks(child, [parent._guard])
    assert api._PROCESS_PRESSURE is parent
    assert parent.retire_owned() is True


def test_pressure_fork_hook_cannot_replace_its_current_process_owner():
    api = importlib.import_module("cli_agent_orchestrator.services.inbox_admission_runtime")
    parent = api._PROCESS_PRESSURE
    with parent._guard:
        api._after_pressure_fork_child()
    assert api._PROCESS_PRESSURE is parent
