"""The server starts and stops the registered Work dispatcher with its lifespan."""

import asyncio
from test.api.test_work_launch_composition import composed_server

import pytest

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.backends import work_registry
from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.services import work_launch_gateway


@pytest.mark.offline_database
@pytest.mark.asyncio
async def test_registered_work_dispatcher_polls_once_and_stops_on_cancellation():
    calls = []
    called = asyncio.Event()

    class Gateway:
        async def dispatch_registered_next(self):
            calls.append("dispatch")
            called.set()
            return None

    task = asyncio.create_task(main._run_registered_work_dispatcher(Gateway()))
    try:
        await asyncio.wait_for(called.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert calls == ["dispatch"]


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

        async def block_dispatch():
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        gateway.dispatch_registered_next = block_dispatch
        return gateway

    monkeypatch.setattr(work_registry, "WORK_BACKENDS", {"test-backend": backend})
    monkeypatch.setattr(work_launch_gateway, "build_durable_launch_gateway", build_gateway)
    monkeypatch.setattr(main, "_recover_t097_proxy_effects_at_startup", lambda: None)

    async with main.lifespan(main.app):
        await asyncio.wait_for(started.wait(), timeout=2)
        assert not stopped.is_set()

    assert built == [(composed_server.repository.target.identity, {"test-backend": backend})]
    assert stopped.is_set()
