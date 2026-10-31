"""Finite startup maintenance must finish before resource runtime retirement."""

import asyncio
from test.api.test_admission_lifespan import wired_server
from test.api.test_work_launch_composition import composed_server
from threading import Event

import pytest

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.services import inbox_admission_runtime as module


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["old_data", "memory", "workflow_retention"])
async def test_normal_shutdown_joins_finite_startup_maintenance(wired_server, monkeypatch, stage):
    entered, release, finished = Event(), Event(), Event()
    serving, leave, closing = asyncio.Event(), asyncio.Event(), asyncio.Event()
    events = []

    def blocked():
        entered.set()
        try:
            assert release.wait(15)
        finally:
            events.append("worker_finished")
            finished.set()

    if stage == "memory":

        async def memory():
            await asyncio.to_thread(blocked)

        monkeypatch.setattr(main, "cleanup_expired_memories", memory)
    else:
        name = "cleanup_old_data" if stage == "old_data" else "_sweep_workflow_runs_at_startup"
        monkeypatch.setattr(main, name, blocked)

    close = module.InboxAdmissionRuntime.close_admission
    stop = module.InboxAdmissionRuntime.stop

    def record_close(runtime):
        close(runtime)
        closing.set()

    def record_stop(runtime):
        # Record synchronously before retire_admission_runtime creates its task.
        # No sleeps or guessed scheduling interval establishes the order.
        events.append("retirement_entered")
        return stop(runtime)

    monkeypatch.setattr(module.InboxAdmissionRuntime, "close_admission", record_close)
    monkeypatch.setattr(module.InboxAdmissionRuntime, "stop", record_stop)

    async def serve():
        async with main.lifespan(main.app):
            serving.set()
            await leave.wait()

    task = asyncio.create_task(serve())
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        await asyncio.wait_for(serving.wait(), 10)
        leave.set()
        await asyncio.wait_for(closing.wait(), 10)
        assert "retirement_entered" not in events
        assert not task.done()
        assert not wired_server.captured[0].retired
        assert not finished.is_set()
    finally:
        leave.set()
        release.set()
        result = await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 10)
    assert result == [None]
    assert events.index("worker_finished") < events.index("retirement_entered")
    assert wired_server.captured[0].retired


@pytest.mark.asyncio
async def test_failed_startup_joins_running_maintenance_without_cancelling_its_worker_waiter(
    wired_server, monkeypatch
):
    entered, release, finished = Event(), Event(), Event()
    closing = asyncio.Event()
    worker_waiters, events = [], []
    failure = RuntimeError("controlled-startup-with-active-maintenance")

    def blocked():
        entered.set()
        try:
            assert release.wait(15)
        finally:
            events.append("worker_finished")
            finished.set()

    async def traced_to_thread(function, *args, **kwargs):
        if function is blocked:
            worker_waiters.append(asyncio.current_task())
        return await asyncio.to_thread(function, *args, **kwargs)

    async def fail_composition(*args):
        assert await asyncio.to_thread(entered.wait, 10)
        raise failure

    close = module.InboxAdmissionRuntime.close_admission
    stop = module.InboxAdmissionRuntime.stop

    def record_close(runtime):
        close(runtime)
        closing.set()

    def record_stop(runtime):
        events.append("retirement_entered")
        return stop(runtime)

    monkeypatch.setattr(main, "cleanup_old_data", blocked)
    monkeypatch.setattr(main, "_initialize_managed_workflow_runtime", fail_composition)
    monkeypatch.setattr(wired_server.tracker, "to_thread", traced_to_thread)
    monkeypatch.setattr(module.InboxAdmissionRuntime, "close_admission", record_close)
    monkeypatch.setattr(module.InboxAdmissionRuntime, "stop", record_stop)

    async def serve():
        async with main.lifespan(main.app):
            pytest.fail("controlled failed startup cannot yield")

    startup = asyncio.create_task(serve())
    try:
        await asyncio.wait_for(closing.wait(), 10)
        assert len(worker_waiters) == 1
        # Cancellation was already requested for startup tasks before the
        # lifespan yielded to its cleanup owner. The physical waiter must stay
        # alive and uncancelled until the blocked callback ends.
        assert any(
            not task.done() and task.cancelling() > 0
            for source, task in wired_server.tracker.created
            if source.cr_code.co_name == "maintain"
        )
        assert worker_waiters[0].cancelling() == 0
        assert not worker_waiters[0].done()
        assert not startup.done()
        assert not finished.is_set()
    finally:
        release.set()
        result = await asyncio.gather(startup, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 10)
    assert result == [failure]
    assert events.index("worker_finished") < events.index("retirement_entered")
    assert wired_server.captured[0].retired


@pytest.mark.asyncio
async def test_failed_startup_before_first_task_step_never_invokes_maintenance_factory(
    wired_server, monkeypatch
):
    factories = []
    failure = RuntimeError("controlled-cold-startup-failure")

    async def memory():
        pytest.fail("unstarted maintenance body must not run")

    def factory():
        factories.append("created")
        return memory()

    def fail_backend():
        raise failure

    monkeypatch.setattr(main, "cleanup_expired_memories", factory)
    monkeypatch.setattr(main, "get_backend", fail_backend)
    with pytest.raises(RuntimeError) as caught:
        async with main.lifespan(main.app):
            pytest.fail("controlled failed startup cannot yield")
    assert caught.value is failure
    assert factories == []
    assert all(task.done() for task in wired_server.tracker.tasks)
    assert wired_server.captured[0].retired
