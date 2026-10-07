"""The MCP-to-API hop for peer work stays on this computer."""

import pytest

from cli_agent_orchestrator.mcp_server import utils


def test_local_peer_api_request_uses_direct_loopback_transport(monkeypatch):
    observed = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def request(self, method, url, **kwargs):
            observed.update(method=method, url=url, trust_env=self.trust_env, **kwargs)
            return Response()

    monkeypatch.setattr(utils, "API_BASE_URL", "http://localhost:9889")
    monkeypatch.setattr(utils.requests, "Session", Session)

    result = utils.local_get_json("/local-coordination/agent/peers", project_path="/tmp/work")

    assert result == {"ok": True}
    assert observed["url"] == "http://127.0.0.1:9889/local-coordination/agent/peers"
    assert observed["trust_env"] is False


def test_local_peer_api_request_rejects_non_loopback_api_host(monkeypatch):
    monkeypatch.setattr(utils, "API_BASE_URL", "http://192.0.2.10:9889")

    with pytest.raises(ValueError, match="loopback"):
        utils.local_get_json("/local-coordination/agent/peers")


@pytest.mark.asyncio
async def test_mcp_returns_persisted_cancelled_receipt(monkeypatch):
    from cli_agent_orchestrator.mcp_server import server

    receipt = {
        "task_id": "cancel-task",
        "state": "cancelled",
        "result": {"state": "cancelled", "terminal_id": "deadbeef"},
    }
    monkeypatch.setattr(
        server, "_local_peer_caller_context", lambda: ("a1b2c3d4", "/project", None)
    )
    monkeypatch.setattr(server.mcp_utils, "local_get_json", lambda *args, **kwargs: receipt)
    monkeypatch.setattr(server.mcp_utils, "local_post_body_json", lambda *args, **kwargs: receipt)
    assert await server.get_local_cao_task("cancel-task") == {"success": True, **receipt}
    assert await server.cancel_local_cao_task("cancel-task") == {"success": True, **receipt}
