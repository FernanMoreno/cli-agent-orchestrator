"""Startup cancellation must join its initializer even before resource binding."""

import asyncio
from test.api.test_admission_lifespan import wired_server
from test.api.test_work_launch_composition import composed_server
from threading import Event

import pytest

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.services.interactions.service import InteractionService


@pytest.mark.asyncio
async def test_cancelled_startup_joins_interaction_initializer(wired_server, monkeypatch):
    entered, release, finished = Event(), Event(), Event()

    def blocked(self):
        try:
            with self.storage_admission.lifetime():
                entered.set()
                assert release.wait(15)
        finally:
            finished.set()

    monkeypatch.setattr(InteractionService, "initialize", blocked)

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
