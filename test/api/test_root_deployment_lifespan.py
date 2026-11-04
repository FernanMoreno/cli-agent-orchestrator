"""The real outer API lifetime owns Root without skipping independent cleanup."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.api.root_deployment_routes import close_root_deployment
from cli_agent_orchestrator.services.root_deployment_runtime import (
    RootDeploymentRuntime,
    RootDeploymentRuntimeUnavailable,
)

pytestmark = pytest.mark.offline_database


def configured():
    app = FastAPI()
    value = Mock(spec=RootDeploymentRuntime)
    value.aclose = AsyncMock()
    app.state.root_deployment_runtime = value
    return app, value


@pytest.mark.asyncio
async def test_root_failure_still_runs_independent_api_shutdown(monkeypatch):
    app, root = configured()
    failure = RootDeploymentRuntimeUnavailable()
    root.aclose.side_effect = failure
    events = []

    @asynccontextmanager
    async def application(selected):
        assert selected is app
        events.append("startup")
        try:
            yield
        finally:
            events.append("independent_shutdown")

    monkeypatch.setattr(main, "_application_lifespan", application)
    with pytest.raises(RootDeploymentRuntimeUnavailable) as caught:
        async with main.lifespan(app):
            pass
    assert caught.value is failure
    assert events == ["startup", "independent_shutdown"]
    root.aclose.assert_awaited_once()
    assert app.state.root_deployment_runtime is root


@pytest.mark.asyncio
async def test_earliest_startup_failure_still_attempts_root_close(monkeypatch):
    app, root = configured()
    failure = RuntimeError("controlled early startup")

    @asynccontextmanager
    async def application(selected):
        raise failure
        yield

    monkeypatch.setattr(main, "_application_lifespan", application)
    with pytest.raises(RuntimeError) as caught:
        async with main.lifespan(app):
            pytest.fail("startup cannot yield")
    assert caught.value is failure
    root.aclose.assert_awaited_once()
    assert not hasattr(app.state, "root_deployment_runtime")


@pytest.mark.asyncio
async def test_primary_api_failure_is_preserved_when_root_close_also_fails(monkeypatch):
    app, root = configured()
    failure = RuntimeError("controlled independent retirement failure")
    root.aclose.side_effect = RootDeploymentRuntimeUnavailable()

    @asynccontextmanager
    async def application(selected):
        yield
        raise failure

    monkeypatch.setattr(main, "_application_lifespan", application)
    with pytest.raises(RuntimeError) as caught:
        async with main.lifespan(app):
            pass
    assert caught.value is failure
    root.aclose.assert_awaited_once()
    assert app.state.root_deployment_runtime is root


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_detach_physical_root_close():
    app, root = configured()
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def close():
        entered.set()
        await release.wait()
        finished.set()

    root.aclose.side_effect = close
    operation = asyncio.create_task(close_root_deployment(app))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
        assert app.state.root_deployment_runtime is root
        assert not finished.is_set()
    finally:
        release.set()
        result = await asyncio.gather(operation, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert finished.is_set()
    assert not hasattr(app.state, "root_deployment_runtime")


@pytest.mark.asyncio
async def test_root_closes_before_independent_api_retirement(monkeypatch):
    app, root = configured()
    events = []

    async def close():
        events.append("root_closed")

    root.aclose.side_effect = close

    @asynccontextmanager
    async def application(selected):
        yield
        events.append("independent_shutdown")

    monkeypatch.setattr(main, "_application_lifespan", application)
    async with main.lifespan(app):
        pass
    assert events == ["root_closed", "independent_shutdown"]
