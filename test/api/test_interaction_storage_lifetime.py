"""HTTP cancellation must not detach a service worker that still owns SQL."""

import asyncio
from test.services.test_delivery_admission import create, observe
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from cli_agent_orchestrator.api.interactions import _invoke, interaction_capabilities
from cli_agent_orchestrator.services.interactions.service import InteractionService
from cli_agent_orchestrator.services.storage_operation_admission import StorageOperationAdmission

pytestmark = pytest.mark.offline_database


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["invoke", "capabilities"])
async def test_cancelled_interaction_http_waiter_joins_its_physical_service_body(surface):
    owner = create()
    assert observe(owner, 1)
    port = StorageOperationAdmission()
    port.bind_admission(owner, clock=lambda: 10, retirement=lambda: True)
    port.start_accepting()
    entered, release, finished = Event(), Event(), Event()

    def blocked(*args, **kwargs):
        try:
            with port.operation():
                entered.set()
                assert release.wait(10)
                return {"done": True}
        finally:
            finished.set()

    if surface == "invoke":
        service = SimpleNamespace(storage_admission=port, blocked=blocked)
        operation = _invoke(service, "blocked")
    else:
        service = Mock(spec=InteractionService)
        service.storage_admission = port
        service.authenticate_agent = blocked
        app = FastAPI()
        app.state.interaction_service = service
        request = Request(
            {
                "type": "http",
                "app": app,
                "method": "GET",
                "path": "/interaction-capabilities",
                "query_string": b"",
                "headers": [(b"x-cao-interaction-capability", b"synthetic-capability")],
            }
        )
        operation = interaction_capabilities(request)
    waiter = asyncio.create_task(operation)
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        waiter.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not waiter.done()
        assert owner.snapshot(now=10).inflight == 1
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 10)
        result = await asyncio.gather(waiter, return_exceptions=True)
        port.stop_accepting()
    assert isinstance(result[0], asyncio.CancelledError)
    assert owner.snapshot(now=10).inflight == 0
