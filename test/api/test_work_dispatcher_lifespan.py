"""The server starts and stops the registered Work dispatcher with its lifespan."""

import asyncio
import contextvars
import threading
from test.api.test_work_launch_composition import composed_server
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.backends import work_registry
from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients.work_repository import WorkConflict
from cli_agent_orchestrator.services import work_admission, work_launch_gateway
from cli_agent_orchestrator.services.work_admission import (
    WorkAdmission,
    _PreparedRegisteredDispatch,
)


@pytest.mark.offline_database
@pytest.mark.asyncio
async def test_registered_work_dispatcher_starts_capacity_peers_before_first_finishes(caplog):
    prepare_calls = 0
    admitted = 0
    active_preparations = 0
    maximum_active_preparations = 0
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    second_release = asyncio.Event()
    capacity_refused = asyncio.Event()
    third_finished = asyncio.Event()
    runtime_closed = asyncio.Event()
    active_deliveries = 0
    maximum_active_deliveries = 0
    execute_started = threading.Event()
    execute_release = threading.Event()
    execute_finished = threading.Event()
    port_closed = asyncio.Event()

    class Port:
        def execute_process(self):
            execute_started.set()
            try:
                execute_release.wait(timeout=5)
            finally:
                execute_finished.set()

        def close(self):
            assert execute_finished.is_set()
            port_closed.set()

    port = Port()

    class Gateway:
        async def prepare_registered_dispatch(self):
            nonlocal prepare_calls, admitted, active_preparations, maximum_active_preparations
            active_preparations += 1
            maximum_active_preparations = max(maximum_active_preparations, active_preparations)
            try:
                prepare_calls += 1
                await asyncio.sleep(0)
                if active_deliveries >= 2:
                    capacity_refused.set()
                    return None
                if admitted >= 3:
                    return None
                admitted += 1
                return admitted
            finally:
                active_preparations -= 1

        async def deliver_registered_dispatch(self, prepared):
            nonlocal active_deliveries, maximum_active_deliveries
            active_deliveries += 1
            maximum_active_deliveries = max(maximum_active_deliveries, active_deliveries)
            try:
                if prepared == 1:
                    first_started.set()
                    await asyncio.to_thread(port.execute_process)
                if prepared == 2:
                    second_started.set()
                    await second_release.wait()
                    raise RuntimeError("backend secret must not be logged")
                if prepared == 3:
                    third_finished.set()
            finally:
                active_deliveries -= 1
                if prepared == 1:
                    port.close()

        async def wait_for_registered_dispatch_cleanup(self):
            assert execute_finished.is_set()
            runtime_closed.set()

    task = asyncio.create_task(main._run_registered_work_dispatcher(Gateway()))
    try:
        await asyncio.wait_for(first_started.wait(), timeout=2)
        assert await asyncio.to_thread(execute_started.wait, 2)
        await asyncio.wait_for(second_started.wait(), timeout=2)
        await asyncio.wait_for(capacity_refused.wait(), timeout=2)
        assert admitted == 2
        assert maximum_active_deliveries == 2
        second_release.set()
        await asyncio.wait_for(third_finished.wait(), timeout=2)
        for _ in range(10):
            if "Durable Work delivery failed" in caplog.text:
                break
            await asyncio.sleep(0)
        assert "Durable Work delivery failed" in caplog.text
        assert "backend secret" not in caplog.text
        assert maximum_active_preparations == 1
        assert prepare_calls >= 3
        assert admitted == 3
        assert not execute_finished.is_set()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # Repeated shutdown cancellation must not abandon the join.
        await asyncio.sleep(0)
        if hasattr(task, "cancelling"):
            assert task.cancelling() >= 2
        assert not task.done()
        assert not execute_finished.is_set()
        assert not port_closed.is_set()
        assert not runtime_closed.is_set()
        execute_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert admitted == 3
    assert execute_finished.is_set()
    assert port_closed.is_set()
    assert runtime_closed.is_set()


@pytest.mark.asyncio
async def test_registered_work_dispatcher_backs_off_when_no_work_is_eligible():
    calls = 0
    second_poll = asyncio.Event()

    class Gateway:
        async def prepare_registered_dispatch(self):
            nonlocal calls
            calls += 1
            if calls == 2:
                second_poll.set()
            return None

        async def deliver_registered_dispatch(self, prepared):
            pytest.fail("no work was prepared")

    task = asyncio.create_task(main._run_registered_work_dispatcher(Gateway()))
    try:
        await asyncio.sleep(0.05)
        assert calls == 1
        await asyncio.wait_for(second_poll.wait(), timeout=0.7)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_dispatcher_abandons_handoff_if_delivery_task_creation_fails():
    loop = asyncio.get_running_loop()
    original_factory = loop.get_task_factory()
    handoff = object()
    prepared = False
    abandoned = asyncio.Event()
    drained = asyncio.Event()
    created_coroutines = []
    abandoned_handoffs = []

    def fail_delivery_task_factory(loop, coroutine, context=None):
        if getattr(coroutine, "cr_code", None) is not None and (
            coroutine.cr_code.co_name == "deliver_prepared"
        ):
            created_coroutines.append(coroutine)
            raise RuntimeError("task factory detail must not be logged")
        if original_factory is not None:
            if context is None:
                return original_factory(loop, coroutine)
            return original_factory(loop, coroutine, context=context)
        if context is None:
            return asyncio.tasks.Task(coroutine, loop=loop)
        return asyncio.tasks.Task(coroutine, loop=loop, context=context)

    class Gateway:
        async def prepare_registered_dispatch(self):
            nonlocal prepared
            if prepared:
                return None
            prepared = True
            loop.set_task_factory(fail_delivery_task_factory)
            return handoff

        async def deliver_registered_dispatch(self, _prepared):
            pytest.fail("delivery must not start when task creation fails")

        async def abandon_registered_dispatch(self, failed_handoff):
            abandoned_handoffs.append(failed_handoff)
            loop.set_task_factory(original_factory)
            abandoned.set()

        async def wait_for_registered_dispatch_cleanup(self):
            drained.set()

    task = asyncio.create_task(main._run_registered_work_dispatcher(Gateway()))
    try:
        await asyncio.wait_for(abandoned.wait(), timeout=2)
        assert abandoned_handoffs == [handoff]
        assert len(created_coroutines) == 1
        assert created_coroutines[0].cr_frame is None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert drained.is_set()
    finally:
        loop.set_task_factory(original_factory)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for coroutine in created_coroutines:
            coroutine.close()


@pytest.mark.asyncio
async def test_dispatcher_abandons_delivery_cancelled_before_first_step_during_shutdown():
    loop = asyncio.get_running_loop()
    original_factory = loop.get_task_factory()
    handoff = SimpleNamespace(claimed=False)
    prepared = False
    reconciliation = loop.create_future()
    reconciliation_started = asyncio.Event()
    reconciliation_finished = asyncio.Event()
    delivery_coroutines = []
    delivery_calls = []
    abandon_calls = []
    drain_calls = []

    class Port:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    handoff.port = Port()
    reconciliation.add_done_callback(lambda _future: reconciliation_finished.set())

    def cancel_unstarted_delivery_factory(actual_loop, coroutine, context=None):
        if getattr(getattr(coroutine, "cr_code", None), "co_name", None) == ("deliver_prepared"):
            delivery_coroutines.append(coroutine)
            if context is None:
                delivery = asyncio.tasks.Task(coroutine, loop=actual_loop)
            else:
                delivery = asyncio.tasks.Task(
                    coroutine,
                    loop=actual_loop,
                    context=context,
                )
            delivery.cancel()
            return delivery
        if original_factory is not None:
            if context is None:
                return original_factory(actual_loop, coroutine)
            return original_factory(actual_loop, coroutine, context=context)
        if context is None:
            return asyncio.tasks.Task(coroutine, loop=actual_loop)
        return asyncio.tasks.Task(coroutine, loop=actual_loop, context=context)

    class Gateway:
        async def prepare_registered_dispatch(self):
            nonlocal prepared
            if prepared:
                return None
            prepared = True
            loop.set_task_factory(cancel_unstarted_delivery_factory)
            return handoff

        async def deliver_registered_dispatch(self, actual_handoff):
            delivery_calls.append(actual_handoff)
            actual_handoff.claimed = True
            return {"delivered": True}

        async def abandon_registered_dispatch(self, actual_handoff):
            assert actual_handoff is handoff
            assert not actual_handoff.claimed
            actual_handoff.claimed = True
            actual_handoff.port.close()
            abandon_calls.append(actual_handoff)
            reconciliation_started.set()
            await asyncio.shield(reconciliation)

        async def wait_for_registered_dispatch_cleanup(self):
            drain_calls.append(True)
            if not reconciliation.done():
                await asyncio.shield(reconciliation)

    task = asyncio.create_task(main._run_registered_work_dispatcher(Gateway()))
    stop = None
    try:
        await asyncio.wait_for(reconciliation_started.wait(), timeout=2)
        assert handoff.claimed
        assert handoff.port.close_calls == 1
        assert not delivery_calls
        assert abandon_calls == [handoff]
        assert not reconciliation_finished.is_set()

        stop = asyncio.create_task(main._stop_registered_work_dispatcher(task))
        await asyncio.sleep(0)
        assert not stop.done()
        assert not reconciliation_finished.is_set()

        reconciliation.set_result(None)
        await asyncio.wait_for(stop, timeout=2)
    finally:
        if not reconciliation.done():
            reconciliation.set_result(None)
        loop.set_task_factory(original_factory)
        if stop is not None and not stop.done():
            await asyncio.gather(stop, return_exceptions=True)
        elif not task.done():
            await main._stop_registered_work_dispatcher(task)
        for coroutine in delivery_coroutines:
            coroutine.close()

    assert handoff.claimed
    assert handoff.port.close_calls == 1
    assert abandon_calls == [handoff]
    assert not delivery_calls
    assert reconciliation_finished.is_set()
    assert drain_calls == [True]
    assert task.done()
    assert len(delivery_coroutines) == 1
    assert delivery_coroutines[0].cr_frame is None


@pytest.mark.asyncio
async def test_abandon_registered_handoff_closes_and_joins_reconciliation(monkeypatch):
    port_closed = threading.Event()
    reconcile_started = threading.Event()
    reconcile_release = threading.Event()
    reconcile_finished = threading.Event()
    repository = object()
    sent = object()
    trace_id = contextvars.ContextVar("registered_abandon_trace_id")
    trace_token = trace_id.set("registered-abandon-context")

    class Port:
        def close(self):
            port_closed.set()

    class FakeWorkService:
        def __init__(self, actual_repository, *, origins):
            assert actual_repository is repository
            assert origins is None

        def _reconcile(self, actual_sent, *, actor_id):
            assert actual_sent is sent
            assert actor_id == "principal"
            assert trace_id.get() == "registered-abandon-context"
            assert port_closed.is_set()
            reconcile_started.set()
            assert reconcile_release.wait(timeout=5)
            reconcile_finished.set()
            return {"id": "work-item"}

    monkeypatch.setattr(work_admission, "WorkService", FakeWorkService)
    admission = object.__new__(WorkAdmission)
    admission.repository = repository
    admission.origins = None
    admission._pending_abandonments = set()
    prepared = _PreparedRegisteredDispatch(
        admission,
        SimpleNamespace(principal_id="principal"),
        Port(),
        sent,
    )
    task = asyncio.create_task(admission.abandon_registered_dispatch(prepared))
    try:
        assert await asyncio.to_thread(reconcile_started.wait, 2)
        assert port_closed.is_set()
        assert prepared.claimed
        assert not reconcile_finished.is_set()
        assert admission._pending_abandonments
        with pytest.raises(WorkConflict):
            await admission.deliver_registered_dispatch(prepared)

        task.cancel()
        await asyncio.sleep(0)
        assert task.cancelled()
        assert any(not cleanup.done() for cleanup in admission._pending_abandonments)
        reconcile_release.set()
        await admission.wait_for_dispatch_cleanup()
        assert reconcile_finished.is_set()
    finally:
        reconcile_release.set()
        await asyncio.gather(task, return_exceptions=True)
        await admission.wait_for_dispatch_cleanup()
        trace_id.reset(trace_token)


@pytest.mark.asyncio
async def test_dispatch_preparation_cleanup_is_joined_through_repeated_cancellation():
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def abandon_preparation():
        cleanup_started.set()
        await cleanup_release.wait()
        cleanup_finished.set()

    admission = object.__new__(WorkAdmission)
    cleanup = asyncio.create_task(abandon_preparation())
    admission._pending_abandonments = {cleanup}
    drain = asyncio.create_task(admission.wait_for_dispatch_cleanup())
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=2)
        await asyncio.sleep(0)
        drain.cancel()
        await asyncio.sleep(0)
        drain.cancel()
        await asyncio.sleep(0)
        assert not drain.done()
        if hasattr(drain, "cancelling"):
            assert drain.cancelling() >= 2
        assert not cleanup_finished.is_set()
        cleanup_release.set()
        await drain
        assert cleanup_finished.is_set()
    finally:
        cleanup_release.set()
        await asyncio.gather(drain, cleanup, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_factory", [False, True])
async def test_cancelled_preparation_retains_worker_if_cleanup_task_creation_fails(
    monkeypatch, legacy_factory
):
    loop = asyncio.get_running_loop()
    previous_factory = loop.get_task_factory()
    original_factory = previous_factory
    if legacy_factory:

        def original_factory(actual_loop, coroutine):
            return asyncio.tasks.Task(coroutine, loop=actual_loop)

    trace_context = contextvars.ContextVar("dispatch_abandonment_trace", default=None)
    context_token = trace_context.set("admitted-work-context")
    worker_started = threading.Event()
    worker_release = threading.Event()
    worker_finished = threading.Event()
    port_closed = threading.Event()
    reconciled = threading.Event()
    handoff = (SimpleNamespace(principal_id="principal"), None, {"state": "sent"})
    adapter_calls = []
    created_workers = []
    rejected_cleanup_coroutines = []
    fail_cleanup_creation = False

    class Port:
        def close(self):
            assert worker_finished.is_set()
            port_closed.set()

    port = Port()
    handoff = (handoff[0], port, handoff[2])

    def prepare(*, registered_only):
        assert registered_only is True
        worker_started.set()
        if not worker_release.wait(timeout=5):
            raise TimeoutError("preparation worker release barrier was not opened")
        worker_finished.set()
        return handoff

    class FakeWorkService:
        def __init__(self, _repository, *, origins):
            assert origins is None

        def _reconcile(self, sent, *, actor_id):
            assert sent is handoff[2]
            assert actor_id == "principal"
            assert trace_context.get() == "admitted-work-context"
            assert port_closed.is_set()
            assert worker_finished.is_set()
            reconciled.set()

    monkeypatch.setattr(work_admission, "WorkService", FakeWorkService)
    admission = object.__new__(WorkAdmission)
    admission.repository = object()
    admission.origins = None
    admission._pending_abandonments = set()
    admission._prepare_dispatch = prepare

    def task_factory(actual_loop, coroutine, context=None):
        nonlocal fail_cleanup_creation
        name = getattr(getattr(coroutine, "cr_code", None), "co_name", None)
        if fail_cleanup_creation and name == "_abandon_preparation":
            rejected_cleanup_coroutines.append(coroutine)
            raise RuntimeError("cleanup task factory failure")
        if original_factory is not None:
            task = (
                original_factory(actual_loop, coroutine)
                if context is None
                else original_factory(actual_loop, coroutine, context=context)
            )
        elif context is None:
            task = asyncio.tasks.Task(coroutine, loop=actual_loop)
        else:
            task = asyncio.tasks.Task(coroutine, loop=actual_loop, context=context)
        if name == "to_thread":
            created_workers.append(task)
        return task

    loop.set_task_factory(task_factory)
    dispatch = asyncio.create_task(admission._prepare_dispatch_async(registered_only=True))
    outcome = None
    try:
        assert await asyncio.wait_for(asyncio.to_thread(worker_started.wait, 2), timeout=3)
        fail_cleanup_creation = True
        dispatch.cancel()
        await asyncio.sleep(0)
        assert not dispatch.done()
        assert admission._pending_abandonments
        assert not port_closed.is_set()
        dispatch.cancel()
        try:
            await dispatch
        except BaseException as error:
            outcome = error
        assert isinstance(outcome, asyncio.CancelledError)
        assert admission._pending_abandonments
        assert not worker_finished.is_set()
        drain = asyncio.create_task(admission.wait_for_dispatch_cleanup())
        await asyncio.sleep(0)
        drain.cancel()
        await asyncio.sleep(0)
        drain.cancel()
        await asyncio.sleep(0)
        assert not drain.done()
        assert not port_closed.is_set()
        worker_release.set()
        await drain
    finally:
        worker_release.set()
        if not dispatch.done():
            dispatch.cancel()
            await asyncio.gather(dispatch, return_exceptions=True)
        if created_workers:
            await asyncio.gather(*created_workers, return_exceptions=True)
        if "drain" in locals() and not drain.done():
            await asyncio.gather(drain, return_exceptions=True)
        await admission.wait_for_dispatch_cleanup()
        for coroutine in rejected_cleanup_coroutines:
            coroutine.close()
        loop.set_task_factory(previous_factory)
        trace_context.reset(context_token)
        monkeypatch.undo()

    assert (
        isinstance(outcome, asyncio.CancelledError)
        and not rejected_cleanup_coroutines
        and port_closed.is_set()
        and reconciled.is_set()
        and not adapter_calls
        and not admission._pending_abandonments
    ), "cancelled preparation lost its committed handoff when cleanup task creation failed"


@pytest.mark.asyncio
async def test_lifespan_cancels_registered_dispatcher_on_shutdown(composed_server, monkeypatch):
    started = asyncio.Event()
    stopped = asyncio.Event()
    backend = TmuxBackend()

    real_builder = work_launch_gateway.build_durable_launch_gateway
    built = []

    def build_gateway(repository, *, backends):
        gateway = real_builder(repository, backends=backends)
        built.append((repository.target.identity, backends))

        async def block_preparation():
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        gateway.prepare_registered_dispatch = block_preparation
        return gateway

    monkeypatch.setattr(work_registry, "WORK_BACKENDS", {"test-backend": backend})
    monkeypatch.setattr(work_launch_gateway, "build_durable_launch_gateway", build_gateway)
    monkeypatch.setattr(main, "_recover_t097_proxy_effects_at_startup", lambda: None)

    async with main.lifespan(main.app):
        await asyncio.wait_for(started.wait(), timeout=2)
        assert not stopped.is_set()

    assert built == [(composed_server.repository.target.identity, {"test-backend": backend})]
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_preparation_preserves_external_cancellation_after_timeout():
    timeout_factory = getattr(asyncio, "timeout", None)
    if not callable(timeout_factory):
        pytest.skip("asyncio.timeout is available from Python 3.11")
    loop = asyncio.get_running_loop()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    admission = object.__new__(WorkAdmission)
    admission._pending_abandonments = set()

    def prepare(*, registered_only):
        started.set()
        if not release.wait(timeout=10):
            raise TimeoutError("preparation test release barrier was not opened")
        finished.set()
        return None

    admission._prepare_dispatch = prepare
    timeout = timeout_factory(None)

    async def operation():
        async with timeout:
            await admission._prepare_dispatch_async()

    task = asyncio.create_task(operation())
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 3), timeout=4)
        timeout.reschedule(loop.time())
        while not timeout.expired():
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert admission._pending_abandonments
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        try:
            await task
        except asyncio.CancelledError:
            outcome = "CancelledError"
        except TimeoutError:
            outcome = "TimeoutError"
        else:
            outcome = "returned"
        assert outcome == "CancelledError"
        assert task.cancelling() >= 1
        assert not finished.is_set()
        assert admission._pending_abandonments
        release.set()
        await admission.wait_for_dispatch_cleanup()
        assert finished.is_set()
        assert not admission._pending_abandonments
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await admission.wait_for_dispatch_cleanup()


@pytest.mark.asyncio
async def test_dispatcher_stop_preserves_callers_cancellation_count():
    started, release = asyncio.Event(), asyncio.Event()

    async def owned_dispatcher():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            raise

    dispatcher = asyncio.create_task(owned_dispatcher())
    await started.wait()
    stop = asyncio.create_task(main._stop_registered_work_dispatcher(dispatcher))
    try:
        await asyncio.sleep(0)
        stop.cancel()
        await asyncio.sleep(0)
        stop.cancel()
        await asyncio.sleep(0)
        assert not stop.done()
        release.set()
        await stop
        if hasattr(stop, "cancelling"):
            assert stop.cancelling() >= 2
        assert dispatcher.cancelled()
    finally:
        release.set()
        await asyncio.gather(stop, dispatcher, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["deliver_registered_dispatch", "abandon_registered_dispatch"])
async def test_gateway_refuses_incomplete_dispatch_provider_before_admission(missing):
    calls = []

    async def prepare():
        calls.append("prepare")
        return object()

    async def unused(_handoff):
        pytest.fail("incomplete provider must not receive a handoff")

    methods = {
        "prepare_registered_dispatch": prepare,
        "deliver_registered_dispatch": unused,
        "abandon_registered_dispatch": unused,
    }
    methods.pop(missing)
    gateway = work_launch_gateway.DurableLaunchGateway(SimpleNamespace(**methods))
    with pytest.raises(work_launch_gateway.DurableLaunchGatewayError) as raised:
        await gateway.prepare_registered_dispatch()
    assert raised.value.code == "launch_runtime_unavailable"
    assert calls == []
