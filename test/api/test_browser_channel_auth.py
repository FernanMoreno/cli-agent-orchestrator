"""Real SQLite revocation retires silent streams and prevents subsequent input."""

import asyncio

import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api import browser_auth_routes
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.browser_auth import BrowserAuthService


def make_app(tmp_path):
    app = FastAPI()
    principal = auth._verified_principal("urn:cao:test", "operator", ["cao:write"], "jwt")
    service = BrowserAuthService(
        tmp_path / "auth.sqlite3",
        {
            "id": principal.id,
            "issuer": principal.issuer,
            "subject": principal.subject,
            "kind": "jwt",
            "scopes": ["cao:write"],
        },
        {"installation_id": "test"},
    )
    service.create_account("operator", "a long private passphrase")
    app.state.browser_auth = service
    app.state.browser_auth_config = {
        "enabled": True,
        "installation_id": "test",
        "canonical_origin": "http://127.0.0.1:9889",
        "transport_policy": "loopback_http",
    }
    app.include_router(browser_auth_routes.router)
    return app, service


def test_silent_websocket_retires_when_sqlite_session_is_revoked(tmp_path):
    assert hasattr(browser_auth_routes, "BrowserSessionLifetimeMiddleware")
    app, service = make_app(tmp_path)
    app.add_middleware(browser_auth_routes.BrowserSessionLifetimeMiddleware)

    @app.websocket("/socket")
    async def socket(ws: WebSocket):
        await ws.accept()
        await ws.receive_text()
        await ws.send_text("effect")

    with TestClient(
        app,
        base_url="http://127.0.0.1:9889",
        client=("127.0.0.1", 50000),
        headers={"Origin": "http://127.0.0.1:9889", "X-CAO-Browser": "1"},
    ) as client:
        response = client.post(
            "/auth/login",
            json={
                "username": "operator",
                "password": "a long private passphrase",
                "remember": True,
            },
        )
        assert response.status_code == 200
        secret = client.cookies.get("cao_browser_test")
        with client.websocket_connect("ws://127.0.0.1:9889/socket") as ws:
            service.logout(secret)
            from starlette.websockets import WebSocketDisconnect

            with pytest.raises(WebSocketDisconnect) as caught:
                ws.receive_text()
            assert caught.value.code == 4401


@pytest.mark.asyncio
async def test_silent_stream_revocation_cancels_generator_without_replaying(tmp_path):
    assert hasattr(browser_auth_routes, "BrowserSessionLifetimeMiddleware")
    app, service = make_app(tmp_path)
    secret, _ = service.login("operator", "a long private passphrase", True, "local")
    stopped = asyncio.Event()

    @app.get("/stream")
    async def stream(request: Request):
        async def data():
            try:
                yield b"event: hello\n\n"
                await asyncio.sleep(100)
            finally:
                stopped.set()

        return StreamingResponse(data(), media_type="text/event-stream")

    middleware = browser_auth_routes.BrowserSessionLifetimeMiddleware(app)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/stream",
        "raw_path": b"/stream",
        "query_string": b"",
        "root_path": "",
        "server": ("127.0.0.1", 9889),
        "client": ("127.0.0.1", 50000),
        "app": app,
        "headers": [
            (b"host", b"127.0.0.1:9889"),
            (b"origin", b"http://127.0.0.1:9889"),
            (b"cookie", f"cao_browser_test={secret}".encode()),
        ],
    }
    emitted = []

    async def receive():
        await asyncio.sleep(100)

    async def send(message):
        emitted.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            service.logout(secret)

    await asyncio.wait_for(middleware(scope, receive, send), timeout=4)
    assert stopped.is_set()
    bodies = [m.get("body") for m in emitted if m["type"] == "http.response.body" and m.get("body")]
    assert bodies == [b"event: hello\n\n"]


@pytest.mark.asyncio
async def test_initial_websocket_storage_outage_is_retryable(tmp_path, monkeypatch):
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

    app, service = make_app(tmp_path)
    secret, _ = service.login("operator", "a long private passphrase", True, "local")

    def unavailable(*args, **kwargs):
        raise BrowserAuthError("auth_unavailable", 503)

    monkeypatch.setattr(service, "identity", unavailable)
    messages = []
    scope = {
        "type": "websocket",
        "scheme": "ws",
        "path": "/socket",
        "query_string": b"",
        "server": ("127.0.0.1", 9889),
        "client": ("127.0.0.1", 50000),
        "app": app,
        "headers": [
            (b"host", b"127.0.0.1:9889"),
            (b"origin", b"http://127.0.0.1:9889"),
            (b"cookie", f"cao_browser_test={secret}".encode()),
        ],
    }

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)

    await browser_auth_routes.BrowserSessionLifetimeMiddleware(app)(scope, receive, send)
    assert messages[0]["type"] == "websocket.close"
    assert messages[0]["code"] == 1013
