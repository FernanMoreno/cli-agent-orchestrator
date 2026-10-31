"""A resource runtime joins every bound service before physical retirement."""

import asyncio
from dataclasses import replace
from test.postgres.interactions import postgres_interaction_store
from test.services.test_admission_monitor import fixture, warm
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime
from cli_agent_orchestrator.services.inbox_service import InboxService
from cli_agent_orchestrator.services.storage_operation_admission import StorageOperationAdmission

pytestmark = pytest.mark.postgres


def runtime(store, *, inbox=None):
    f = fixture()
    f.repository.resolve_target = lambda: store.target

    async def dormant():
        await asyncio.Event().wait()

    f.monitor._run = dormant
    value = InboxAdmissionRuntime(
        inbox if inbox is not None else InboxService(clock=lambda: f.clock[0]),
        monitor=f.monitor,
        bootstrap=f.bootstrap,
        clock=lambda: f.clock[0],
        drain_timeout_s=10,
    )
    return SimpleNamespace(f=f, value=value, port=store.service.storage_admission)


def test_runtime_rejects_foreign_main_store_before_registering_port(postgres_interaction_store):
    store = postgres_interaction_store
    r = runtime(store)
    foreign = replace(store.target, store_id=str(uuid4()))
    with pytest.raises(ValueError, match="configured main target"):
        r.value.add_storage_participant(r.port, target=foreign)
    assert r.value._storage_participants == []
    r.value.add_storage_participant(r.port, target=store.target)
    with pytest.raises(ValueError, match="already registered"):
        r.value.add_storage_participant(r.port, target=store.target)
    assert r.value._storage_participants == [r.port]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["error", "cancel"])
async def test_runtime_joins_all_drains_and_keeps_bindings_after_failure(
    postgres_interaction_store, monkeypatch, mode
):
    store = postgres_interaction_store
    r = runtime(store)
    second = StorageOperationAdmission()
    r.value.add_storage_participant(r.port, target=store.target)
    r.value.add_storage_participant(second, target=store.target)
    await r.value.start()
    entered, release = asyncio.Event(), asyncio.Event()
    stopping = None

    async def first_drain(*, timeout_s):
        if mode == "error":
            raise OSError("injected participant drain failure")

    async def second_drain(*, timeout_s):
        entered.set()
        await release.wait()

    try:
        with monkeypatch.context() as patch:
            patch.setattr(r.port, "drain", first_drain)
            patch.setattr(second, "drain", second_drain)
            stopping = asyncio.create_task(r.value.stop())
            try:
                await asyncio.wait_for(entered.wait(), 10)
                if mode == "cancel":
                    stopping.cancel()
                    await asyncio.sleep(0)
                assert not stopping.done()
                assert not r.value.retired
                release.set()
                if mode == "error":
                    with pytest.raises(OSError, match="participant drain"):
                        await stopping
                else:
                    with pytest.raises(asyncio.CancelledError):
                        await stopping
                assert not r.value.retired
                for port in (r.port, second):
                    with pytest.raises(RuntimeError, match="must retire"):
                        port.bind_admission(None)
            finally:
                release.set()
                if stopping is not None:
                    await asyncio.gather(stopping, return_exceptions=True)
        await r.value.stop()
        assert r.value.retired
        for port in (r.port, second):
            assert not port._accepting
            port.bind_admission(None)
    finally:
        release.set()
        await r.value.stop()


@pytest.mark.asyncio
async def test_runtime_waits_for_real_service_sql_before_retiring(
    postgres_interaction_store, monkeypatch
):
    store = postgres_interaction_store
    r = runtime(store)
    r.value.add_storage_participant(r.port, target=store.target)
    await r.value.start()
    warm(r.f)
    entered, release, closed = Event(), Event(), Event()
    original_stop = r.port.stop_accepting

    def close_port():
        original_stop()
        closed.set()

    monkeypatch.setattr(r.port, "stop_accepting", close_port)

    def query():
        with store.service._read() as reader:
            assert r.f.owner.snapshot(now=r.f.clock[0]).storage_connection_loans == 1
            entered.set()
            assert release.wait(15)
            assert reader.execute("SELECT 54").fetchone()[0] == 54

    worker = asyncio.create_task(asyncio.to_thread(query))
    stopping = None
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        stopping = asyncio.create_task(r.value.stop())
        assert await asyncio.to_thread(closed.wait, 10)
        await asyncio.sleep(0)
        assert not stopping.done()
        assert not r.value.retired
        with pytest.raises(StorageAdmissionUnavailable):
            with store.service._read():
                pytest.fail("closed participant opened new SQL")
        assert r.f.owner.snapshot(now=r.f.clock[0]).inflight == 1
        release.set()
        await worker
        await stopping
        assert r.value.retired
        assert r.f.owner.snapshot(now=r.f.clock[0]).inflight == 0
    finally:
        release.set()
        await asyncio.gather(
            *(task for task in (worker, stopping) if task is not None), return_exceptions=True
        )
        await r.value.stop()


@pytest.mark.asyncio
async def test_retired_runtime_cannot_close_rebound_service_or_inbox(postgres_interaction_store):
    store = postgres_interaction_store
    old = runtime(store)
    old.value.add_storage_participant(old.port, target=store.target)
    await old.value.start()
    await old.value.stop()
    assert old.value.retired
    current = runtime(store, inbox=old.value.inbox)
    current.value.add_storage_participant(current.port, target=store.target)
    await current.value.start()
    warm(current.f)
    try:
        assert current.port is old.port
        old.value.close_admission()
        assert current.port._accepting
        assert current.value.inbox._accepting_operations
        assert current.f.owner.snapshot(now=current.f.clock[0]).accepting
        assert current.value.available
        with store.service._read() as reader:
            assert reader.execute("SELECT 55").fetchone()[0] == 55
        assert current.f.owner.snapshot(now=current.f.clock[0]).inflight == 0
    finally:
        await current.value.stop()
        await old.value.stop()
