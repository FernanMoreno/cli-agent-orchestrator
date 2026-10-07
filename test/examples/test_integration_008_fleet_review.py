"""Assembled fleet regressions discovered in source-only review."""

import json
from test.examples.test_integration_008_fleet_panel import panel

import httpx
from fastapi.testclient import TestClient


def test_plugins_namespace_is_available_on_selected_registered_node(panel, monkeypatch):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=httpx.ByteStream(b'{"plugins":[]}'),
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        panel["main"].httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    with TestClient(panel["main"].app) as client:
        response = client.get(
            "/nodes/worker/plugins", headers={"Authorization": "Bearer panel-secret"}
        )
    assert response.status_code == 200
    assert seen[0].url.path == "/plugins"
    assert seen[0].headers["authorization"] == "Bearer node-secret"


def test_one_missing_node_credential_does_not_blank_fleet(panel, monkeypatch):
    monkeypatch.setattr(
        panel["config"],
        "load_machines",
        lambda: [
            {
                "name": "bad",
                "label": "Bad",
                "host": "bad.internal",
                "port": 9889,
                "token_env": "MISSING_REVIEW_TOKEN",
            },
            {
                "name": "good",
                "label": "Good",
                "host": "good.internal",
                "port": 9889,
                "token_env": "NODE_SECRET",
            },
        ],
    )
    monkeypatch.delenv("MISSING_REVIEW_TOKEN", raising=False)

    def respond(request):
        body = [] if request.url.path == "/sessions" else {"components": {}}
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=httpx.ByteStream(json.dumps(body).encode()),
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        panel["main"].httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    with TestClient(panel["main"].app, raise_server_exceptions=False) as client:
        response = client.get("/api/fleet", headers={"Authorization": "Bearer panel-secret"})
    assert response.status_code == 200
    machines = {machine["name"]: machine for machine in response.json()["machines"]}
    assert machines["bad"]["online"] is False
    assert machines["bad"]["error"] == "RuntimeError"
    assert machines["good"]["online"] is True


def test_missing_credential_proxy_is_actionable_without_private_path(panel, monkeypatch):
    monkeypatch.setattr(
        panel["config"],
        "load_machines",
        lambda: [
            {
                "name": "worker",
                "host": "worker.internal",
                "port": 9889,
                "token_file": "/private/review/nonexistent-secret",
            },
        ],
    )
    with TestClient(panel["main"].app, raise_server_exceptions=False) as client:
        response = client.get(
            "/nodes/worker/health", headers={"Authorization": "Bearer panel-secret"}
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "node_credential_unavailable"
    assert "/private/" not in response.text
    assert "panel-secret" not in response.text


def test_control_byte_in_node_credential_is_rejected_before_transport(panel, monkeypatch, tmp_path):
    secret = tmp_path / "node.token"
    secret.write_text("node-secret\x00invalid")
    monkeypatch.setattr(
        panel["config"],
        "load_machines",
        lambda: [
            {"name": "worker", "host": "worker.internal", "port": 9889, "token_file": str(secret)},
        ],
    )
    original = httpx.AsyncClient
    monkeypatch.setattr(
        panel["main"].httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, stream=httpx.ByteStream(b"{}"))
            ),
            **kwargs,
        ),
    )
    with TestClient(panel["main"].app, raise_server_exceptions=False) as client:
        response = client.get(
            "/nodes/worker/health", headers={"Authorization": "Bearer panel-secret"}
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "node_credential_unavailable"
    assert "node-secret" not in response.text


import asyncio
from types import SimpleNamespace

import pytest
from starlette.requests import Request


@pytest.mark.asyncio
async def test_cancelled_proxy_connect_releases_owned_http_client(panel, monkeypatch):
    class Client:
        closed = False

        def build_request(self, *args, **kwargs):
            return None

        async def send(self, *args, **kwargs):
            raise asyncio.CancelledError()

        async def aclose(self):
            self.closed = True

    upstream_client = Client()
    monkeypatch.setattr(panel["main"].httpx, "AsyncClient", lambda **kwargs: upstream_client)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/nodes/worker/health",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
        },
        receive,
    )
    with pytest.raises(asyncio.CancelledError):
        await panel["main"].node_proxy("worker", "health", request)
    assert upstream_client.closed is True


@pytest.mark.asyncio
async def test_browser_disappearing_after_upstream_attach_releases_socket(panel, monkeypatch):
    class Socket:
        closed = False

        async def close(self):
            self.closed = True

    socket = Socket()

    async def connect(*args, **kwargs):
        return socket

    monkeypatch.setattr(panel["main"].websockets, "connect", connect)

    async def reject_accept():
        raise RuntimeError("browser disconnected before accept")

    browser = SimpleNamespace(
        headers={"authorization": "Bearer panel-secret"}, cookies={}, accept=reject_accept
    )
    with pytest.raises(RuntimeError, match="browser disconnected"):
        await panel["main"].node_terminal_ws(browser, "worker", "t1")
    assert socket.closed is True
