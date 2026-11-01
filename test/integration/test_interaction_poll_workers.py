"""Every bounded interaction poll owns its synchronous PostgreSQL worker."""

import threading
from contextlib import contextmanager
from test.postgres.interactions import postgres_interaction_store, register_interaction_source

import pytest

from cli_agent_orchestrator.services.work_blocking import run_owned_blocking

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["required", "native", "answers"])
async def test_interaction_poll_never_runs_transaction_on_event_loop(
    postgres_interaction_store, monkeypatch, kind
):
    store = postgres_interaction_store
    source = await run_owned_blocking(register_interaction_source, store)
    group = await run_owned_blocking(
        store.service.create_group,
        source.token,
        {"questions": [{"client_key": "poll", "kind": "text", "prompt": "Choose a value."}]},
        operation_key="worker-poll-regression-0001",
    )
    status = await run_owned_blocking(
        store.service.source_status, source.terminal_id, source.incarnation_id
    )
    loop_thread = threading.get_ident()
    transaction = store.service._transaction
    observed = []

    @contextmanager
    def owned_transaction():
        observed.append(threading.get_ident())
        assert threading.get_ident() != loop_thread, "PostgreSQL poll ran on the event loop"
        with transaction() as connection:
            yield connection

    with monkeypatch.context() as patch:
        patch.setattr(store.service, "_transaction", owned_transaction)
        if kind == "required":
            result = await store.service.wait_for_required(
                source.terminal_id, source.incarnation_id, timeout_seconds=0
            )
            assert result.state == "waiting"
        elif kind == "native":
            result = await store.service.wait_for_native_manual(
                source.terminal_id,
                source.incarnation_id,
                timeout_seconds=0,
                expected_generation=status.current_generation,
            )
            assert result.state == "waiting"
        else:
            result = await store.service.wait_for_answers(source.token, group.group_id, 0)
            assert result.state == "pending"
    assert observed
    assert store.service._transaction == transaction


@pytest.mark.asyncio
async def test_cancelled_poll_waits_for_actual_postgresql_transaction_close(
    postgres_interaction_store, monkeypatch
):
    import asyncio

    store = postgres_interaction_store
    source = await run_owned_blocking(register_interaction_source, store)
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    transaction = store.service._transaction

    @contextmanager
    def held_transaction():
        try:
            with transaction() as connection:
                entered.set()
                assert release.wait(10), "transaction barrier was not released"
                yield connection
        finally:
            closed.set()

    task = None
    with monkeypatch.context() as patch:
        patch.setattr(store.service, "_transaction", held_transaction)
        try:
            task = asyncio.create_task(
                store.service.wait_for_required(
                    source.terminal_id, source.incarnation_id, timeout_seconds=0
                )
            )
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert not closed.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert closed.is_set()
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
    assert store.service._transaction == transaction
