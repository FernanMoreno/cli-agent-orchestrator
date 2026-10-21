"""Owned process execution does not borrow the shared asyncio executor."""

import asyncio
import contextvars
import threading

import pytest

from cli_agent_orchestrator.services import work_blocking
from cli_agent_orchestrator.services.work_blocking import run_owned_blocking


@pytest.mark.asyncio
async def test_owned_blocking_preserves_context_result_and_worker_exception():
    trace_id = contextvars.ContextVar("work_blocking_trace_id")
    token = trace_id.set("trace-from-admitted-work")
    result = object()
    error = RuntimeError("backend failure")

    try:
        assert await run_owned_blocking(trace_id.get) == "trace-from-admitted-work"
        assert await run_owned_blocking(lambda: result) is result

        def fail():
            raise error

        with pytest.raises(RuntimeError) as raised:
            await run_owned_blocking(fail)
        assert raised.value is error
    finally:
        trace_id.reset(token)


@pytest.mark.asyncio
async def test_owned_blocking_keeps_default_executor_available_while_worker_waits():
    worker_started = threading.Event()
    release_worker = threading.Event()
    worker_finished = threading.Event()
    control_started = threading.Event()

    def blocked_worker():
        worker_started.set()
        if not release_worker.wait(timeout=10):
            raise TimeoutError("test worker release barrier was not opened")
        worker_finished.set()
        return "backend-result"

    def default_executor_control():
        control_started.set()
        return "control-progressed"

    worker = asyncio.create_task(run_owned_blocking(blocked_worker))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(worker_started.wait, 3), timeout=4)
        control_result = await asyncio.wait_for(
            asyncio.to_thread(default_executor_control), timeout=4
        )

        assert control_result == "control-progressed"
        assert control_started.is_set()
        assert not worker.done()
    finally:
        release_worker.set()
        if not worker.done():
            await asyncio.gather(worker, return_exceptions=True)

    assert await worker == "backend-result"
    assert worker_finished.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_owned_blocking_preserves_cancellation_count_while_draining(cancel_count):
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    worker_started = threading.Event()
    release_worker = threading.Event()
    worker_finished = threading.Event()
    error = RuntimeError("backend failure after cancellation")

    def blocked_failure():
        worker_started.set()
        if not release_worker.wait(timeout=10):
            raise TimeoutError("test worker release barrier was not opened")
        worker_finished.set()
        raise error

    worker = asyncio.create_task(run_owned_blocking(blocked_failure))
    cancellation_count = getattr(worker, "cancelling", None)
    try:
        assert await asyncio.wait_for(asyncio.to_thread(worker_started.wait, 3), timeout=4)
        for requested_count in range(1, cancel_count + 1):
            worker.cancel()
            await asyncio.sleep(0)
            if callable(cancellation_count):
                assert cancellation_count() == requested_count
        assert not worker.done()

        release_worker.set()
        with pytest.raises(asyncio.CancelledError):
            await worker
        if callable(cancellation_count):
            assert cancellation_count() == cancel_count
        await asyncio.sleep(0)

        assert worker_finished.is_set()
        assert unhandled == []
    finally:
        release_worker.set()
        if not worker.done():
            await asyncio.gather(worker, return_exceptions=True)
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_owned_blocking_preserves_external_cancel_after_timeout_during_drain():
    timeout_factory = getattr(asyncio, "timeout", None)
    if not callable(timeout_factory):
        pytest.skip("asyncio.timeout is available from Python 3.11")

    loop = asyncio.get_running_loop()
    trace_id = contextvars.ContextVar("work_blocking_timeout_trace_id")
    token = trace_id.set("timeout-worker-context")
    worker_started = threading.Event()
    release_worker = threading.Event()
    worker_finished = threading.Event()
    worker_context = []
    timeout_context = timeout_factory(None)

    def blocked_worker():
        worker_context.append(trace_id.get())
        worker_started.set()
        if not release_worker.wait(timeout=10):
            raise TimeoutError("timeout test worker release barrier was not opened")
        worker_finished.set()
        return "backend-result"

    async def operation():
        async with timeout_context:
            return await run_owned_blocking(blocked_worker)

    operation_task = asyncio.create_task(operation())
    try:
        assert await asyncio.wait_for(asyncio.to_thread(worker_started.wait, 3), timeout=4)

        timeout_context.reschedule(loop.time())
        while not timeout_context.expired():
            await asyncio.sleep(0)
        # Let the timeout cancellation enter the helper's drain before delivering
        # the distinct caller cancellation.
        await asyncio.sleep(0)

        timeout_cancel_count = operation_task.cancelling()
        assert not operation_task.done()
        assert not worker_finished.is_set()

        operation_task.cancel()
        await asyncio.sleep(0)
        combined_cancel_count = operation_task.cancelling()

        release_worker.set()
        try:
            await operation_task
        except asyncio.CancelledError:
            outcome = "CancelledError"
        except TimeoutError:
            outcome = "TimeoutError"
        else:
            outcome = "returned"

        assert timeout_cancel_count >= 1
        assert combined_cancel_count >= timeout_cancel_count + 1
        assert outcome == "CancelledError"
        assert operation_task.cancelling() >= 1
        assert worker_finished.is_set()
        assert worker_context == ["timeout-worker-context"]
    finally:
        release_worker.set()
        if not operation_task.done():
            await asyncio.gather(operation_task, return_exceptions=True)
        trace_id.reset(token)


@pytest.mark.asyncio
async def test_owned_blocking_does_not_require_task_uncancel_api(monkeypatch):
    trace_id = contextvars.ContextVar("work_blocking_legacy_task_trace")
    token = trace_id.set("legacy-task-context")
    worker_started = threading.Event()
    release_worker = threading.Event()
    worker_finished = threading.Event()
    worker_threads = []
    worker_context = []

    class LegacyTaskProxy:
        """Model a supported asyncio Task with no Python 3.11 cancellation APIs."""

    def blocked_worker():
        worker_threads.append(threading.current_thread())
        worker_context.append(trace_id.get())
        worker_started.set()
        if not release_worker.wait(timeout=10):
            raise TimeoutError("legacy task worker release barrier was not opened")
        worker_finished.set()
        return "backend-result"

    def legacy_current_task():
        return LegacyTaskProxy()

    original_current_task = work_blocking.asyncio.current_task
    worker = None
    monkeypatch.setattr(work_blocking.asyncio, "current_task", legacy_current_task)
    try:
        worker = asyncio.create_task(run_owned_blocking(blocked_worker))
        assert await asyncio.to_thread(worker_started.wait, 3)

        worker.cancel()
        await asyncio.sleep(0)
        assert not worker.done()
        assert not worker_finished.is_set()

        release_worker.set()
        with pytest.raises(asyncio.CancelledError):
            await worker

        assert worker_finished.is_set()
        assert worker_context == ["legacy-task-context"]
        assert len(worker_threads) == 1
        assert not worker_threads[0].is_alive()
    finally:
        monkeypatch.setattr(work_blocking.asyncio, "current_task", original_current_task)
        release_worker.set()
        if worker is not None:
            await asyncio.gather(worker, return_exceptions=True)
        trace_id.reset(token)


@pytest.mark.asyncio
async def test_owned_blocking_shuts_down_executor_when_submission_fails(monkeypatch):
    class _FailingExecutor:
        def __init__(self, *, max_workers, thread_name_prefix):
            assert max_workers == 1
            assert thread_name_prefix == "cao-work-execution"
            self.shutdown_arguments = None

        def submit(self, _function):
            raise RuntimeError("worker thread could not be started")

        def shutdown(self, *, wait, cancel_futures):
            self.shutdown_arguments = (wait, cancel_futures)

    executor = _FailingExecutor(max_workers=1, thread_name_prefix="cao-work-execution")
    monkeypatch.setattr(work_blocking, "ThreadPoolExecutor", lambda **_kwargs: executor)

    with pytest.raises(RuntimeError, match="could not be started"):
        await run_owned_blocking(lambda: None)

    assert executor.shutdown_arguments == (True, True)
