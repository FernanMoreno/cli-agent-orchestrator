"""Current public terminal APIs preserve remote transport classifications."""

import pytest
from fastapi import HTTPException

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.runtime_channel.registry import (
    RemoteOutcomeUnknownError,
    RuntimeUnavailableError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, expected",
    [(RuntimeUnavailableError("offline"), 503), (RemoteOutcomeUnknownError("uncertain"), 504)],
)
async def test_output_transport_error_has_its_original_http_classification(
    monkeypatch, error, expected
):
    monkeypatch.setattr(
        main.terminal_service, "get_output", lambda *a, **k: (_ for _ in ()).throw(error)
    )
    with pytest.raises(HTTPException) as caught:
        await main.get_terminal_output("1234abcd", main.OutputMode.FULL, ["cao:read"])
    assert caught.value.status_code == expected
    assert caught.value.detail["delivery_may_have_occurred"] is (expected == 504)


def test_actual_websocket_route_authentication_and_epoch_reconnect(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from starlette.websockets import WebSocketDisconnect

    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.clients.runtime_channel_schema import RemoteBase
    from cli_agent_orchestrator.runtime_channel import server
    from cli_agent_orchestrator.runtime_channel.protocol import (
        PROTOCOL_VERSION,
        Hello,
        decode,
        encode,
    )
    from cli_agent_orchestrator.runtime_channel.registry import runtime_registry

    engine = create_engine(
        f'sqlite:///{tmp_path / "central.sqlite"}', connect_args={"check_same_thread": False}
    )
    RemoteBase.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(server, "runtime_token", lambda: "isolated-token")
    app = FastAPI()
    app.include_router(server.router)
    try:
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect) as refused:
                with client.websocket_connect("/runtime/channel"):
                    pass
            assert refused.value.code == 1008
            for epoch in (1, 2):
                with client.websocket_connect(
                    "/runtime/channel", headers={"x-cao-runtime-token": "isolated-token"}
                ) as socket:
                    socket.send_text(
                        encode(
                            Hello(
                                protocol_version=PROTOCOL_VERSION,
                                runtime_id="rt",
                                runtime_incarnation_id="inc",
                            )
                        )
                    )
                    reply = decode(socket.receive_text())
                    assert reply.runtime_incarnation_id == "inc" and reply.connection_epoch == epoch
                    assert runtime_registry.connection("rt").incarnation_id == "inc"
    finally:
        runtime_registry._runtimes.clear()
        runtime_registry._placement.clear()
        engine.dispose()
