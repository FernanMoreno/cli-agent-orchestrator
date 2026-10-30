"""Provider publication waits must yield the loop and retain real-worker ownership."""

import asyncio
import contextvars
import threading
from test.services.test_terminal_service_inbox_registration import create_mocks

import pytest

from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services import terminal_service as module

pytestmark = pytest.mark.asyncio


async def checkpoint():
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    loop.call_soon(future.set_result, None)
    await future


async def test_terminal_creation_provider_wait_does_not_block_event_loop(create_mocks):
    mocks = create_mocks
    loop = asyncio.get_running_loop()
    entered, release, heartbeat = threading.Event(), threading.Event(), threading.Event()
    observed = {}
    provider = mocks.provider_manager.create_provider.return_value

    def create(*_args, **_kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return provider

    def observer():
        try:
            assert entered.wait(timeout=5)
            loop.call_soon_threadsafe(heartbeat.set)
            observed["loop_progressed_before_provider_return"] = heartbeat.wait(timeout=0.5)
        finally:
            release.set()

    mocks.provider_manager.create_provider.side_effect = create
    watcher = threading.Thread(target=observer)
    watcher.start()
    try:
        await module.create_terminal(
            provider="claude_code", agent_profile="developer", session_name="cao-test-session"
        )
    finally:
        release.set()
        watcher.join(timeout=5)
    assert not watcher.is_alive()
    assert observed["loop_progressed_before_provider_return"], "provider wait blocked the API loop"


@pytest.mark.offline_database
async def test_provider_worker_inherits_context_and_returns_exact_result():
    marker = contextvars.ContextVar("provider_operation_test")
    token = marker.set("request-context")
    result = object()

    def operation(*, expected):
        assert marker.get() == expected
        return result

    try:
        assert (
            await module._await_provider_operation(operation, expected="request-context") is result
        )
    finally:
        marker.reset(token)


@pytest.mark.offline_database
@pytest.mark.parametrize("worker_fails", [False, True])
async def test_repeated_cancellation_waits_for_real_provider_worker(worker_fails):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release, finished = threading.Event(), threading.Event()

    def operation():
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(timeout=5)
            if worker_fails:
                raise ValueError("provider failed after caller cancellation")
        finally:
            finished.set()

    task = asyncio.create_task(module._await_provider_operation(operation))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        await checkpoint()
        assert not task.done()
        task.cancel()
        await checkpoint()
        assert not task.done()
        assert not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert finished.is_set()


@pytest.mark.offline_database
async def test_provider_worker_error_identity_is_preserved():
    failure = ValueError("controlled provider failure")

    def operation():
        raise failure

    with pytest.raises(ValueError) as raised:
        await module._await_provider_operation(operation)
    assert raised.value is failure


async def test_cancel_during_rollback_finishes_cleanup_before_deleting_row(
    create_mocks, monkeypatch
):
    mocks = create_mocks
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    steps = []
    provider = mocks.provider_manager.create_provider.return_value

    async def fail_initialize():
        raise RuntimeError("controlled provider initialization failure")

    def cleanup(*_args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=5)
        steps.append("cleanup")
        return True

    provider.initialize.side_effect = fail_initialize
    mocks.provider_manager.cleanup_provider.side_effect = cleanup
    monkeypatch.setattr(
        module, "_remove_interaction_capability_files", lambda _id: steps.append("caps")
    )
    mocks.generate_terminal_id.return_value = "ab12cd34"
    mocks.db_create_terminal.side_effect = database.create_terminal
    mocks.backend.kill_window.return_value = True
    mocks.backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.ABSENT
    )
    monkeypatch.setattr(module, "get_terminal_metadata", database.get_terminal_metadata)
    monkeypatch.setattr(
        module, "delete_terminal_row", lambda *_args, **_kwargs: steps.append("row")
    )
    task = asyncio.create_task(
        module.create_terminal(
            provider="claude_code", agent_profile="developer", session_name="cao-test-session"
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        await checkpoint()
        assert not task.done()
        assert steps == []
        task.cancel()
        await checkpoint()
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert steps == ["cleanup", "caps", "row"]
