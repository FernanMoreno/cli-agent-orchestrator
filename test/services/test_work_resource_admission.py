"""A resource refusal precedes Work intent; physical completion owns release."""

import asyncio
from dataclasses import replace
from test.integration.test_work_dispatch import context  # noqa: F401
from test.services.test_work_inbox import _managed_module, _request, paired_context
from threading import Event
from types import MappingProxyType

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services.work_admission import WorkAdmission

pytestmark = pytest.mark.postgres


def managed(context, slots, *, physical_storage_slots=None):
    from cli_agent_orchestrator.services.admission_policy import (
        AdmissionPolicy,
        CapacityObservation,
    )
    from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
    from cli_agent_orchestrator.services.inbox_service import InboxService

    owner = DeliveryAdmission(AdmissionPolicy())
    memory = 80 * 1024 * 1024 * max(1, slots)
    physical_slots = 2 * slots if physical_storage_slots is None else physical_storage_slots
    assert owner.observe(
        CapacityObservation(10.0, memory, memory, 16000, 16000, physical_slots),
        now=10.0,
        generation=owner.generation,
    )
    assert owner.snapshot(now=10.0).limit == min(slots, physical_slots)
    inbox = InboxService(admission=owner, clock=lambda: 10.0)
    context.service = WorkAdmission(
        context.repo,
        backends={"test": context.backend},
        delivery_adapters=_managed_module().managed_inbox_delivery_adapters(
            context.repo, delivery_service=inbox
        ),
        resource_admission=owner,
        resource_clock=lambda: 10.0,
    )
    coordinator = _managed_module().WorkInboxCoordinator(context.service)
    return coordinator, owner


@pytest.mark.asyncio
async def test_resource_refusal_keeps_planned_order_and_retained_managed_row(paired_context):
    coordinator, owner = managed(paired_context, 0)
    receipt = coordinator.admit(**_request(paired_context, "resource-denied"))
    before = paired_context.repo.get_work(receipt.work_item_id)
    assert await paired_context.service.dispatch_registered_next() is None
    assert paired_context.repo.get_work(receipt.work_item_id) == before
    assert before["attempts"][-1]["state"] == "planned"
    assert database.get_inbox_messages("receiver", status=None)[0].status.value == "reconcile"
    assert paired_context.backend.effects == []
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
async def test_work_and_inbox_share_receiver_and_recover_without_duplicate(paired_context):
    coordinator, owner = managed(paired_context, 2)
    receipt = coordinator.admit(**_request(paired_context, "resource-shared"))
    earlier = owner.try_reserve("receiver", now=10.0).reservation
    assert earlier is not None and earlier.start()
    try:
        assert await paired_context.service.dispatch_registered_next() is None
        assert (
            paired_context.repo.get_work(receipt.work_item_id)["attempts"][-1]["state"] == "planned"
        )
        assert paired_context.backend.effects == []
    finally:
        earlier.finish()
    assert (await paired_context.service.dispatch_registered_next())["id"] == receipt.work_item_id
    assert len(paired_context.backend.effects) == 1
    assert owner.snapshot(now=10.0).inflight == 0
    assert await paired_context.service.dispatch_registered_next() is None
    assert len(paired_context.backend.effects) == 1


@pytest.mark.asyncio
async def test_prepared_handoff_keeps_capacity_until_delivery_and_receipt_finish(
    paired_context, monkeypatch
):
    coordinator, owner = managed(paired_context, 1)
    coordinator.admit(**_request(paired_context, "resource-held"))
    prepared = await paired_context.service.prepare_registered_dispatch()
    assert prepared is not None and owner.snapshot(now=10.0).started == 1
    assert owner.try_reserve("other", now=10.0).reservation is None
    from cli_agent_orchestrator.services.work_service import WorkService

    original = WorkService._record_delivery

    def record(self, *args, **kwargs):
        assert owner.snapshot(now=10.0).started == 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(WorkService, "_record_delivery", record)
    await paired_context.service.deliver_registered_dispatch(prepared)
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
async def test_cancelled_adapter_waits_for_physical_effect_before_resource_release(
    paired_context, monkeypatch
):
    coordinator, owner = managed(paired_context, 1)
    receipt = coordinator.admit(**_request(paired_context, "resource-cancel"))
    adapter = paired_context.service.deliveries.adapters[("inbox", 2)]
    entered, release = Event(), Event()

    async def send(*args):
        def physical():
            entered.set()
            assert release.wait(15), "controlled physical worker was not released"
            assert owner.snapshot(now=10.0).started == 1

        await asyncio.to_thread(physical)
        return await adapter.send(*args)

    monkeypatch.setattr(
        paired_context.service.deliveries,
        "adapters",
        MappingProxyType(
            {
                **paired_context.service.deliveries.adapters,
                ("inbox", 2): replace(adapter, send=send),
            }
        ),
    )
    task = asyncio.create_task(paired_context.service.dispatch_registered_next())
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        await asyncio.sleep(0)
        assert owner.snapshot(now=10.0).started == 1
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert owner.snapshot(now=10.0).inflight == 0
    assert paired_context.repo.get_work(receipt.work_item_id)["state"] == "reconcile"
    assert len(paired_context.backend.effects) == 1


@pytest.mark.asyncio
async def test_busy_work_head_does_not_hide_other_receiver(paired_context):
    coordinator, owner = managed(paired_context, 2)
    with database.SessionLocal() as session:
        session.add(
            database.TerminalModel(
                id="other",
                tmux_session="other-session",
                tmux_window="other-window",
                provider="mock_cli",
            )
        )
        session.commit()
    first = coordinator.admit(**_request(paired_context, "fair-first"))
    second = coordinator.admit(
        **{**_request(paired_context, "fair-second"), "receiver_id": "other"}
    )
    busy = owner.try_reserve("receiver", now=10.0).reservation
    assert busy is not None and busy.start()
    try:
        sent = await paired_context.service.dispatch_registered_next()
        assert sent is not None and sent["id"] == second.work_item_id
        assert (
            paired_context.repo.get_work(first.work_item_id)["attempts"][-1]["state"] == "planned"
        )
        assert [effect[:2] for effect in paired_context.backend.effects] == [
            ("other-session", "other-window")
        ]
    finally:
        busy.finish()
    assert (await paired_context.service.dispatch_registered_next())["id"] == first.work_item_id
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_resource_bound_unregistered_callback_requires_registered_target(
    paired_context, asynchronous
):
    from cli_agent_orchestrator.clients.work_repository import WorkConflict

    coordinator, owner = managed(paired_context, 2)
    receipt = coordinator.admit(**_request(paired_context, "no-ad-hoc"))
    before = paired_context.repo.get_work(receipt.work_item_id)

    def send(*_):
        pytest.fail("unregistered callback has no resource-bound terminal target")

    with pytest.raises(WorkConflict, match="registered"):
        if asynchronous:
            await paired_context.service.dispatch_next_async(send)
        else:
            paired_context.service.dispatch_next(send)
    assert paired_context.repo.get_work(receipt.work_item_id) == before
    assert owner.snapshot(now=10.0).inflight == 0
    assert paired_context.backend.effects == []


@pytest.mark.asyncio
async def test_failed_work_commit_retires_preparation_resource(paired_context, monkeypatch):
    coordinator, owner = managed(paired_context, 1)
    receipt = coordinator.admit(**_request(paired_context, "failed-commit"))
    before = paired_context.repo.get_work(receipt.work_item_id)

    def fail(*_a, **_k):
        assert owner.snapshot(now=10.0).started == 1
        raise RuntimeError("controlled-commit-failure")

    monkeypatch.setattr(paired_context.service, "_commit_dispatch", fail)
    with pytest.raises(RuntimeError, match="controlled-commit-failure"):
        await paired_context.service.dispatch_registered_next()
    assert paired_context.repo.get_work(receipt.work_item_id) == before
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
async def test_abandoned_handoff_retains_resource_through_physical_reconcile(
    paired_context, monkeypatch
):
    from cli_agent_orchestrator.services.work_service import WorkService

    coordinator, owner = managed(paired_context, 1)
    coordinator.admit(**_request(paired_context, "abandoned"))
    prepared = await paired_context.service.prepare_registered_dispatch()
    entered, release = Event(), Event()
    original = WorkService._reconcile

    def reconcile(self, *args, **kwargs):
        entered.set()
        assert release.wait(15), "controlled reconciliation not released"
        assert owner.snapshot(now=10.0).started == 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(WorkService, "_reconcile", reconcile)
    task = asyncio.create_task(paired_context.service.abandon_registered_dispatch(prepared))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert owner.snapshot(now=10.0).started == 1
    finally:
        release.set()
        await paired_context.service.wait_for_dispatch_cleanup()
    assert owner.snapshot(now=10.0).inflight == 0
    assert paired_context.backend.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["stale_v2", "legacy_callback", "transactional_callback"])
async def test_unavailable_target_does_not_abort_other_resource_candidate(
    paired_context, monkeypatch, kind
):
    from cli_agent_orchestrator.services.work_contract import ContractConflict

    coordinator, owner = managed(paired_context, 2)
    with database.SessionLocal() as session:
        session.add(
            database.TerminalModel(
                id="other",
                tmux_session="other-session",
                tmux_window="other-window",
                provider="mock_cli",
            )
        )
        session.commit()
    first = coordinator.admit(**_request(paired_context, "invalid-first"))
    second = coordinator.admit(
        **{**_request(paired_context, "valid-second"), "receiver_id": "other"}
    )
    before = paired_context.repo.get_work(first.work_item_id)
    if kind == "stale_v2":
        with database.SessionLocal() as session:
            terminal = session.query(database.TerminalModel).filter_by(id="receiver").one()
            terminal.tmux_window = "changed-target"
            session.commit()
    else:
        adapter = paired_context.service.deliveries.adapters[("inbox", 2)]

        def unavailable(payload):
            if payload.inbox_id == first.inbox_id:
                raise ContractConflict("legacy target must be reconciled")
            return adapter.terminal_target(payload)

        def unavailable_in_transaction(connection, payload):
            if payload.inbox_id == first.inbox_id:
                raise ContractConflict("transactional target must be reconciled")
            return adapter.transactional_terminal_target(connection, payload)

        monkeypatch.setattr(
            paired_context.service.deliveries,
            "adapters",
            MappingProxyType(
                {
                    **paired_context.service.deliveries.adapters,
                    ("inbox", 2): (
                        replace(
                            adapter, terminal_target=unavailable, transactional_terminal_target=None
                        )
                        if kind == "legacy_callback"
                        else replace(
                            adapter, transactional_terminal_target=unavailable_in_transaction
                        )
                    ),
                }
            ),
        )
    sent = await paired_context.service.dispatch_registered_next()
    assert sent is not None and sent["id"] == second.work_item_id
    assert paired_context.repo.get_work(first.work_item_id) == before
    assert [effect[:2] for effect in paired_context.backend.effects] == [
        ("other-session", "other-window")
    ]
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("transactional", [False, True])
async def test_unexpected_target_store_failure_is_not_ignored(
    paired_context, monkeypatch, transactional
):
    coordinator, owner = managed(paired_context, 1)
    receipt = coordinator.admit(**_request(paired_context, "unexpected-target"))
    before = paired_context.repo.get_work(receipt.work_item_id)
    adapter = paired_context.service.deliveries.adapters[("inbox", 2)]

    def broken(_):
        raise OSError("controlled-store-lookup-failure")

    def broken_in_transaction(connection, payload):
        raise OSError("controlled-store-lookup-failure")

    monkeypatch.setattr(
        paired_context.service.deliveries,
        "adapters",
        MappingProxyType(
            {
                **paired_context.service.deliveries.adapters,
                ("inbox", 2): (
                    replace(adapter, transactional_terminal_target=broken_in_transaction)
                    if transactional
                    else replace(
                        adapter, terminal_target=broken, transactional_terminal_target=None
                    )
                ),
            }
        ),
    )
    with pytest.raises(OSError, match="controlled-store-lookup-failure"):
        await paired_context.service.dispatch_registered_next()
    assert paired_context.repo.get_work(receipt.work_item_id) == before
    assert paired_context.backend.effects == []
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
@pytest.mark.skipif(
    not hasattr(asyncio, "eager_task_factory"), reason="stdlib eager factory requires Python3.12+"
)
async def test_failed_preparation_task_handoff_cannot_commit_unowned_resource(
    paired_context, monkeypatch
):
    coordinator, owner = managed(paired_context, 1)
    receipt = coordinator.admit(**_request(paired_context, "prepare-factory"))
    before = paired_context.repo.get_work(receipt.work_item_id)
    tasks = []

    def create(coroutine, **kwargs):
        task = asyncio.eager_task_factory(asyncio.get_running_loop(), coroutine, **kwargs)
        tasks.append(task)
        raise RuntimeError("controlled-preparation-task-handoff")

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "create_task", create)
        try:
            with pytest.raises(RuntimeError, match="controlled-preparation-task-handoff"):
                await paired_context.service.prepare_registered_dispatch()
            # Join exactly this test factory's tasks before observing state. No
            # cleanup/reconciliation is allowed to repair the asserted snapshot.
            await asyncio.gather(*tasks, return_exceptions=True)
            assert owner.snapshot(now=10.0).inflight == 0
            assert paired_context.repo.get_work(receipt.work_item_id) == before
        finally:
            await asyncio.gather(*tasks, return_exceptions=True)
            if owner.snapshot(now=10.0).inflight:
                for task in tasks:
                    paired_context.service._abandon_preparation(task)
                await paired_context.service.wait_for_dispatch_cleanup()
    assert paired_context.backend.effects == []


@pytest.mark.asyncio
async def test_failed_preparation_awaiter_retains_physical_worker_and_abandonment(
    paired_context, monkeypatch
):
    coordinator, owner = managed(paired_context, 1)
    receipt = coordinator.admit(**_request(paired_context, "prepare-wrapper"))
    entered, release = Event(), Event()
    original_prepare = paired_context.service._prepare_dispatch
    original_create, original_shield = asyncio.create_task, asyncio.shield
    tasks = []
    calls = 0

    def physical(**kwargs):
        prepared = original_prepare(**kwargs)
        entered.set()
        assert release.wait(15), "controlled prepared worker not released"
        return prepared

    def create(coroutine, **kwargs):
        task = original_create(coroutine, **kwargs)
        tasks.append(task)
        return task

    def shield(awaitable):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("controlled-preparation-await-wrapper")
        return original_shield(awaitable)

    monkeypatch.setattr(paired_context.service, "_prepare_dispatch", physical)
    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "create_task", create)
        patch.setattr(asyncio, "shield", shield)
        caller = original_create(paired_context.service.prepare_registered_dispatch())
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            await asyncio.sleep(0)
            assert owner.snapshot(now=10.0).started == 1
            assert not caller.done()
        finally:
            release.set()
            with pytest.raises(RuntimeError, match="controlled-preparation-await-wrapper"):
                await caller
            await asyncio.gather(*tasks, return_exceptions=True)
            if owner.snapshot(now=10.0).inflight:
                for task in tasks:
                    paired_context.service._abandon_preparation(task)
            await paired_context.service.wait_for_dispatch_cleanup()
    assert owner.snapshot(now=10.0).inflight == 0
    assert paired_context.repo.get_work(receipt.work_item_id)["state"] == "reconcile"
    assert paired_context.backend.effects == []


@pytest.mark.parametrize("phase", ["commit", "callback", "native_worker", "receipt", "abandon"])
@pytest.mark.asyncio
async def test_work_physical_stages_share_resource_owner(paired_context, monkeypatch, phase):
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.services.work_service import WorkService

    coordinator, owner = managed(paired_context, 1)
    receipt = coordinator.admit(**_request(paired_context, f"physical-{phase}"))
    seen = []

    def physical_probe():
        assert owner.snapshot(now=10.0).started == 1
        with connect(paired_context.repo.target, readonly=True) as connection:
            assert connection.execute("SELECT 42").fetchone()[0] == 42
            assert owner.snapshot(now=10.0).storage_connection_loans == (
                2 if phase == "commit" else 1
            )
            seen.append(phase)

    if phase == "commit":
        original = paired_context.service._commit_dispatch

        def commit(*args, **kwargs):
            physical_probe()
            return original(*args, **kwargs)

        monkeypatch.setattr(paired_context.service, "_commit_dispatch", commit)
    elif phase in {"callback", "native_worker"}:
        adapter = paired_context.service.deliveries.adapters[("inbox", 2)]

        async def send(*args):
            if phase == "native_worker":
                from cli_agent_orchestrator.services import terminal_service

                with args[3].backend_scope():
                    await terminal_service._await_provider_operation(physical_probe)
            else:
                physical_probe()
            return await adapter.send(*args)

        monkeypatch.setattr(
            paired_context.service.deliveries,
            "adapters",
            MappingProxyType(
                {
                    **paired_context.service.deliveries.adapters,
                    ("inbox", 2): replace(adapter, send=send),
                }
            ),
        )
    else:
        name = "_record_delivery" if phase == "receipt" else "_reconcile"
        original = getattr(WorkService, name)

        def record(self, *args, **kwargs):
            physical_probe()
            return original(self, *args, **kwargs)

        monkeypatch.setattr(WorkService, name, record)
    if phase == "abandon":
        prepared = await paired_context.service.prepare_registered_dispatch()
        assert prepared is not None
        await paired_context.service.abandon_registered_dispatch(prepared)
        await paired_context.service.wait_for_dispatch_cleanup()
        assert paired_context.backend.effects == []
    else:
        result = await paired_context.service.dispatch_registered_next()
        assert result is not None and result["id"] == receipt.work_item_id
        assert len(paired_context.backend.effects) == 1
    assert seen == [phase]
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
async def test_work_insufficient_complete_session_footprint_keeps_planned(
    paired_context, monkeypatch
):
    coordinator, owner = managed(paired_context, 1, physical_storage_slots=1)
    receipt = coordinator.admit(**_request(paired_context, "one-physical-slot"))
    before = paired_context.repo.get_work(receipt.work_item_id)

    def forbidden(*args, **kwargs):
        pytest.fail("an insufficient physical footprint reached Work commit")

    monkeypatch.setattr(paired_context.service, "_commit_dispatch", forbidden)
    assert await paired_context.service.dispatch_registered_next() is None
    assert paired_context.repo.get_work(receipt.work_item_id) == before
    assert before["attempts"][-1]["state"] == "planned"
    assert owner.snapshot(now=10.0).inflight == 0
    assert paired_context.backend.effects == []


@pytest.mark.parametrize("drift", ["engine", "target", "store_uuid"])
def test_transactional_target_rechecks_pinned_store_without_another_checkout(
    paired_context, monkeypatch, drift
):
    from test.postgres.fixtures import make_orm_engine
    from uuid import uuid4

    from cli_agent_orchestrator.clients import work_repository
    from cli_agent_orchestrator.clients.work_repository import WorkConflict

    coordinator, owner = managed(paired_context, 1)
    receipt = coordinator.admit(**_request(paired_context, f"target-drift-{drift}"))
    before = paired_context.repo.get_work(receipt.work_item_id)
    adapter = paired_context.service.deliveries.adapters[("inbox", 2)]
    payload = _managed_module().ManagedInboxTargetPayload(
        inbox_id=receipt.inbox_id,
        terminal_id="receiver",
        tmux_session="managed-session",
        tmux_window="managed-window",
    )
    expected = ("receiver", "managed-session", "managed-window")
    replacement_engine = None
    try:
        with paired_context.repo.connection() as connection:
            assert adapter.transactional_terminal_target(connection, payload) == expected
            with monkeypatch.context() as patch:
                if drift == "engine":
                    replacement_engine = make_orm_engine(paired_context.repo.target)
                    patch.setattr(database, "engine", replacement_engine)
                elif drift == "target":
                    changed = replace(paired_context.repo.target, store_id=str(uuid4()))
                    patch.setattr(database, "main_target", lambda: changed)
                else:
                    # Controlled metadata corruption at the read boundary. The
                    # real immutable store UUID is never modified in PostgreSQL.
                    original = work_repository._stored_inbox_context

                    def changed_context(actual_connection):
                        return replace(original(actual_connection), store_uuid=uuid4().hex)

                    patch.setattr(work_repository, "_stored_inbox_context", changed_context)
                with pytest.raises(WorkConflict):
                    adapter.transactional_terminal_target(connection, payload)
            assert adapter.transactional_terminal_target(connection, payload) == expected
    finally:
        if replacement_engine is not None:
            replacement_engine.dispose()
    assert paired_context.repo.get_work(receipt.work_item_id) == before
    assert paired_context.backend.effects == []
    assert owner.snapshot(now=10.0).inflight == 0
