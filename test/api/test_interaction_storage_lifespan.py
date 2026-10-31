"""Startup cancellation must join its initializer even before resource binding."""

import asyncio
from test.api.test_admission_lifespan import wired_server
from test.api.test_work_launch_composition import composed_server
from threading import Event

import pytest

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.services.interactions.service import InteractionService


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["interaction", "proxy_recovery"])
async def test_cancelled_startup_joins_interaction_initializer(wired_server, monkeypatch, stage):
    entered, release, finished = Event(), Event(), Event()

    def blocked(self):
        try:
            with self.storage_admission.lifetime():
                entered.set()
                assert release.wait(15)
        finally:
            finished.set()

    if stage == "interaction":
        monkeypatch.setattr(InteractionService, "initialize", blocked)
    else:
        monkeypatch.setattr(
            main,
            "_recover_t097_proxy_effects_at_startup",
            lambda: blocked(main.app.state.interaction_service),
        )

    async def serve():
        async with main.lifespan(main.app):
            pytest.fail("controlled initialization must not yield startup")

    startup = asyncio.create_task(serve())
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        startup.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not startup.done()
        assert wired_server.captured == []  # The actual owner still begins later.
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 10)
        result = await asyncio.gather(startup, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_fails", [False, True])
async def test_cancelled_startup_joins_projection_before_followup_owners(monkeypatch, worker_fails):
    """The waiter cannot release a projector still inside its physical callback."""
    from types import SimpleNamespace

    from fastapi import FastAPI

    entered, release, finished = Event(), Event(), Event()
    app = FastAPI()

    def project():
        try:
            entered.set()
            assert release.wait(15)
            if worker_fails:
                raise RuntimeError("controlled-projection-failure")
        finally:
            finished.set()

    projector = SimpleNamespace(project_pending_at_startup=project)

    def compose(*args):
        app.state.workflow_step_projector = projector
        app.state.work_workflow_origins = SimpleNamespace(plans=object())
        return projector

    monkeypatch.setattr(main, "_compose_managed_workflow_runtime", compose)
    startup = asyncio.create_task(main._initialize_managed_workflow_runtime(app, None, None))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert not finished.is_set()
        startup.cancel("first startup cancellation")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        startup.cancel("second startup cancellation")
        await asyncio.sleep(0)
        assert not startup.done()
        assert app.state.workflow_step_projector is projector
        assert not hasattr(app.state, "workflow_continuation_driver")
        assert not hasattr(app.state, "workflow_coordinator")
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 10)
        result = await asyncio.gather(startup, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert not hasattr(app.state, "workflow_continuation_driver")
    assert not hasattr(app.state, "workflow_coordinator")
