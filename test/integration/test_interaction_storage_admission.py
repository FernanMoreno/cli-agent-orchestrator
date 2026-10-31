"""Normal interaction SQL shares resource ownership across HTTP/MCP and waits."""

import asyncio
from contextlib import contextmanager
from test.integration.test_interaction_explicit_flow import (
    interaction_http_context,
)
from test.integration.test_interaction_explicit_flow import (
    test_five_typed_questions_round_trip_through_same_mcp_source_without_terminal_input as typed_round_trip,
)
from test.integration.test_storage_adapters import native_driver_spy
from test.postgres.interactions import postgres_interaction_store
from test.services.test_delivery_admission import create, observe

import pytest

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    physical_storage_scope,
)
from cli_agent_orchestrator.services.interactions.service import InteractionService

pytestmark = pytest.mark.postgres


def bind(service, owner):
    port = service.storage_admission
    port.bind_admission(owner, clock=lambda: 10, retirement=lambda: True)
    port.start_accepting()
    return port


def test_cold_interaction_schema_and_read_use_one_sequential_storage_operation(
    postgres_interaction_store, monkeypatch
):
    import psycopg

    store = postgres_interaction_store
    service = InteractionService(store.repository, work_repository=store.work_repository)
    owner = create()
    assert observe(owner, 1)
    port = bind(service, owner)
    original, opened = psycopg.connect, []

    def physical(*args, **kwargs):
        state = owner.snapshot(now=10)
        assert state.storage_only_inflight == state.storage_connection_loans == 1
        assert state.delivery_inflight == 0
        assert all(raw.closed for raw in opened)
        raw = original(*args, **kwargs)
        opened.append(raw)
        return raw

    try:
        with native_driver_spy(monkeypatch, physical):
            with service._read() as reader:
                assert reader.execute("SELECT 51").fetchone()[0] == 51
        assert len(opened) == 2
        assert all(raw.closed for raw in opened)
        assert owner.snapshot(now=10).inflight == 0
    finally:
        port.stop_accepting()
        for raw in opened:
            if not raw.closed:
                raw.close()


def test_unknown_interaction_capacity_denies_before_cold_schema_sql(
    postgres_interaction_store, monkeypatch
):
    service = InteractionService(postgres_interaction_store.repository)
    owner = create()
    port = bind(service, owner)
    opened = []

    def forbidden(*args, **kwargs):
        opened.append(True)
        pytest.fail("denied transaction opened SQL before admission")

    try:
        with native_driver_spy(monkeypatch, forbidden):
            with pytest.raises(StorageAdmissionUnavailable):
                with service._read():
                    pytest.fail("unknown capacity entered read")
        assert opened == []
        assert not service._schema_ready
        assert owner.snapshot(now=10).inflight == 0
    finally:
        port.stop_accepting()


def test_interaction_sql_reuses_a_normal_two_session_parent(postgres_interaction_store):
    service = postgres_interaction_store.service
    owner = create()
    assert observe(owner, 2)
    port = bind(service, owner)
    lease = owner.try_reserve("normal-input", now=10, storage_slots=2).reservation
    assert lease is not None and lease.start()
    try:
        with physical_storage_scope(lease, clock=lambda: 10):
            with connect(postgres_interaction_store.target, readonly=True):
                assert owner.snapshot(now=10).storage_connection_loans == 1
                with service._read() as reader:
                    assert reader.execute("SELECT 52").fetchone()[0] == 52
                    state = owner.snapshot(now=10)
                    assert state.storage_connection_loans == 2
                    assert state.delivery_inflight == 1 and state.storage_only_inflight == 0
                assert owner.snapshot(now=10).storage_connection_loans == 1
    finally:
        port.stop_accepting()
        assert lease.finish()
    assert owner.snapshot(now=10).inflight == 0


def test_five_question_http_mcp_contract_with_normal_storage_admission(
    interaction_http_context, monkeypatch
):
    import psycopg

    context = interaction_http_context
    owner = create()
    assert observe(owner, 1)
    port = bind(context.service, owner)
    original, observed = psycopg.connect, []

    def physical(*args, **kwargs):
        state = owner.snapshot(now=10)
        assert state.storage_only_inflight == state.storage_connection_loans == 1
        assert state.delivery_inflight == 0
        observed.append(state)
        return original(*args, **kwargs)

    @contextmanager
    def checked(operation):
        # Only this service boundary is accepted here; other route/auth SQL
        # remains a separate gate. Lexical restoration joins each real context.
        with native_driver_spy(monkeypatch, physical):
            with operation() as connection:
                yield connection

    transaction = context.repository.transaction
    read = context.repository.read_connection
    monkeypatch.setattr(context.repository, "transaction", lambda: checked(transaction))
    monkeypatch.setattr(context.repository, "read_connection", lambda: checked(read))
    try:
        typed_round_trip(context, monkeypatch)
        assert observed
        assert owner.snapshot(now=10).inflight == 0
    finally:
        port.stop_accepting()


@pytest.mark.asyncio
async def test_pending_answer_wait_releases_sql_and_cannot_poll_after_stop(
    interaction_http_context, monkeypatch
):
    import psycopg

    from cli_agent_orchestrator.services.interactions import service as module

    context = interaction_http_context
    owner = create()
    assert observe(owner, 1)
    port = bind(context.service, owner)
    group = context.service.create_group(
        context.source_token,
        {"questions": [{"kind": "text", "prompt": "¿Qué comprobamos?", "required": True}]},
        operation_key="normal-admission-wait-0001",
    )
    paused, release = asyncio.Event(), asyncio.Event()
    original, opened = psycopg.connect, []

    class AsyncioPort:
        async def sleep(self, delay):
            assert owner.snapshot(now=10).inflight == 0
            paused.set()
            await release.wait()

        def __getattr__(self, name):
            return getattr(asyncio, name)

    def physical(*args, **kwargs):
        assert owner.snapshot(now=10).storage_connection_loans == 1
        raw = original(*args, **kwargs)
        opened.append(raw)
        return raw

    monkeypatch.setattr(module, "asyncio", AsyncioPort())
    waiter = drain = None
    try:
        with native_driver_spy(monkeypatch, physical):
            waiter = asyncio.create_task(
                context.service.wait_for_answers(
                    context.source_token, group.group_id, timeout_seconds=30
                )
            )
            try:
                await asyncio.wait_for(paused.wait(), 10)
                assert owner.snapshot(now=10).inflight == 0
                assert all(raw.closed for raw in opened)
                with context.service._read() as reader:
                    assert reader.execute("SELECT 53").fetchone()[0] == 53
                before_stop = len(opened)
                port.stop_accepting()
                drain = asyncio.create_task(port.drain(timeout_s=10))
                await asyncio.sleep(0)
                assert not drain.done()
                release.set()
                with pytest.raises(StorageAdmissionUnavailable):
                    await waiter
                await drain
                assert len(opened) == before_stop
                assert owner.snapshot(now=10).inflight == 0
            finally:
                release.set()
                if waiter is not None and not waiter.done():
                    waiter.cancel()
                await asyncio.gather(
                    *(task for task in (waiter, drain) if task is not None), return_exceptions=True
                )
    finally:
        port.stop_accepting()
        for raw in opened:
            if not raw.closed:
                raw.close()


def test_bound_initialize_cannot_verify_schema_before_normal_admission(
    postgres_interaction_store, monkeypatch
):
    import psycopg

    service = InteractionService(postgres_interaction_store.repository)
    owner = create()
    port = bind(service, owner)
    original, opened = psycopg.connect, []

    def physical(*args, **kwargs):
        raw = original(*args, **kwargs)
        opened.append(raw)
        return raw

    try:
        with native_driver_spy(monkeypatch, physical):
            with pytest.raises(StorageAdmissionUnavailable):
                service.initialize()
        assert opened == []
        assert not service._schema_ready
        assert owner.snapshot(now=10).inflight == 0
    finally:
        port.stop_accepting()
        for raw in opened:
            if not raw.closed:
                raw.close()


def test_agent_capability_denial_is_structured_http_without_sql(
    interaction_http_context, monkeypatch
):
    import psycopg

    context = interaction_http_context
    owner = create()
    port = bind(context.service, owner)
    original, opened = psycopg.connect, []

    def physical(*args, **kwargs):
        raw = original(*args, **kwargs)
        opened.append(raw)
        return raw

    try:
        with native_driver_spy(monkeypatch, physical):
            response = context.client.get(
                "/interaction-capabilities",
                headers={"X-CAO-Interaction-Capability": context.source_token},
            )
        assert response.status_code == 503
        assert response.json() == {
            "code": "unavailable",
            "detail": "Interaction service is unavailable.",
            "current_version": None,
        }
        assert opened == []
        assert owner.snapshot(now=10).inflight == 0
    finally:
        port.stop_accepting()
        for raw in opened:
            if not raw.closed:
                raw.close()


@pytest.mark.asyncio
async def test_agui_projection_sql_runs_off_loop_and_is_physically_joined(
    interaction_http_context, monkeypatch
):
    from threading import Event

    import httpx

    from cli_agent_orchestrator.api import main as api_main

    context = interaction_http_context
    owner = create()
    assert observe(owner, 1)
    port = bind(context.service, owner)
    entered, release = Event(), Event()
    original = context.repository.transaction

    @contextmanager
    def paused_transaction():
        with original() as connection:
            assert owner.snapshot(now=10).storage_connection_loans == 1
            entered.set()
            assert release.wait(15)
            yield connection

    class ClosedBus:
        def register(self, overflow_close=False):
            return object()

        def unregister(self, subscription):
            pass

        async def drain(self, subscription):
            if False:
                yield None

    async def authorization(*args, **kwargs):
        async def validate():
            return [api_main.SCOPE_READ]

        return validate

    monkeypatch.setattr(context.repository, "transaction", paused_transaction)
    monkeypatch.setattr(api_main, "_stream_authorization", authorization)
    monkeypatch.setattr(api_main.session_service, "list_sessions", lambda: [])
    monkeypatch.setattr("cli_agent_orchestrator.services.sse_bus.get_bus", lambda: ClosedBus())
    monkeypatch.setenv("CAO_AGUI_ENABLED", "true")
    request = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=context.app), base_url="http://localhost"
        ) as client:
            request = asyncio.create_task(client.get("/agui/v1/stream"))
            try:
                assert await asyncio.to_thread(entered.wait, 10)
                assert not request.done()
                assert owner.snapshot(now=10).inflight == 1
                loop_alive = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_alive.set)
                await asyncio.wait_for(loop_alive.wait(), 1)
                request.cancel()
                await asyncio.sleep(0)
                assert not request.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await request
                assert owner.snapshot(now=10).inflight == 0
            finally:
                release.set()
                if request is not None:
                    await asyncio.gather(request, return_exceptions=True)
    finally:
        release.set()
        port.stop_accepting()
