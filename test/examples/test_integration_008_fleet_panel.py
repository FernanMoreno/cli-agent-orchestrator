"""Fleet routing owns credentials, namespace and registry snapshots."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "integration008_fleet_panel"


@pytest.fixture
def panel(monkeypatch):
    package = ModuleType(PACKAGE)
    package.__path__ = [str(ROOT / "examples/fleet/panel/app")]
    monkeypatch.setitem(sys.modules, PACKAGE, package)
    modules = {}
    for name in ("config", "client", "main"):
        spec = importlib.util.spec_from_file_location(
            f"{PACKAGE}.{name}", ROOT / f"examples/fleet/panel/app/{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        modules[name] = module
    monkeypatch.setattr(modules["config"], "PANEL_TOKEN", "panel-secret")
    monkeypatch.setattr(
        modules["config"],
        "load_machines",
        lambda: [
            {
                "name": "worker",
                "label": "Worker",
                "host": "worker.internal",
                "port": 9889,
                "token_env": "NODE_SECRET",
            }
        ],
    )
    monkeypatch.setenv("NODE_SECRET", "node-secret")
    return modules


def test_proxy_preserves_turn_outcome_and_uses_only_node_credentials(panel, monkeypatch):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            409,
            headers={"content-type": "application/json"},
            stream=httpx.ByteStream(
                json.dumps(
                    {"detail": {"terminal_id": "t1", "generation": "new", "state": "reconcile"}}
                ).encode()
            ),
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        panel["main"].httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    with TestClient(panel["main"].app) as client:
        response = client.post(
            "/nodes/worker/terminals/t1/turn/verify",
            headers={"Authorization": "Bearer panel-secret"},
            json={"generation": "old"},
        )
    assert response.status_code == 409
    assert response.json()["detail"]["generation"] == "new"
    assert seen[0].headers["authorization"] == "Bearer node-secret"
    assert "cookie" not in seen[0].headers
    assert seen[0].url.host == "worker.internal"


def test_proxy_refuses_unregistered_nodes_and_private_namespaces(panel):
    with TestClient(panel["main"].app) as client:
        headers = {"Authorization": "Bearer panel-secret"}
        assert client.get("/nodes/absent/health", headers=headers).status_code == 404
        assert client.post("/nodes/worker/internal/memory", headers=headers).status_code == 404
        assert client.get("/nodes/worker/health").status_code == 401


def test_registry_rejects_host_path_and_duplicate_node_id(panel):
    config = panel["config"]
    with pytest.raises(ValueError):
        config._parse('{"machines":[{"name":"x","host":"localhost/admin"}]}', "test")
    with pytest.raises(ValueError):
        config._parse(
            '{"machines":[{"name":"x","host":"localhost"},{"name":"x","host":"other"}]}', "test"
        )


@pytest.mark.asyncio
async def test_configmap_failure_retains_last_good_registry(panel, monkeypatch):
    config = panel["config"]
    saved = [{"name": "saved", "host": "worker.internal", "port": 9889}]
    config._snapshot.update(machines=saved, at=1, reads=1)

    async def fail(_client=None):
        raise RuntimeError("temporary outage")

    monkeypatch.setattr(config, "read_configmap", fail)
    assert await config.refresh_configmap() is not None
    assert config._snapshot["machines"] == saved
    assert config._snapshot["reads"] == 1


def test_ws_rejects_foreign_origin_before_attach(panel):
    with TestClient(panel["main"].app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/nodes/worker/terminals/t1/ws",
                headers={
                    "Authorization": "Bearer panel-secret",
                    "Origin": "https://foreign.invalid",
                },
            ):
                pytest.fail("foreign origin attached")


def test_missing_node_credential_refuses_without_forwarding(panel, monkeypatch):
    monkeypatch.delenv("NODE_SECRET")
    with TestClient(panel["main"].app, raise_server_exceptions=False) as client:
        response = client.get(
            "/nodes/worker/health", headers={"Authorization": "Bearer panel-secret"}
        )
    assert response.status_code >= 500


def test_proxy_caps_body_before_network_send(panel, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("oversize request reached node")

    monkeypatch.setattr(panel["main"].httpx, "AsyncClient", forbidden)
    with TestClient(panel["main"].app) as client:
        response = client.post(
            "/nodes/worker/terminals/t1/input",
            headers={"Authorization": "Bearer panel-secret"},
            content=b"x" * (2 * 1024 * 1024 + 1),
        )
    assert response.status_code == 413


def test_ws_uses_selected_node_credential_without_browser_token_query(panel, monkeypatch):
    seen = []

    class Socket:
        async def close(self):
            pass

        async def send(self, frame):
            pass

        def __aiter__(self):
            return self.frames()

        async def frames(self):
            if False:
                yield None

    async def connect(url, **kwargs):
        seen.append((url, kwargs))
        return Socket()

    monkeypatch.setattr(panel["main"].websockets, "connect", connect)
    with TestClient(panel["main"].app) as client:
        with client.websocket_connect(
            "/nodes/worker/terminals/t1/ws?token=panel-secret",
            headers={"Authorization": "Bearer panel-secret", "Origin": "http://testserver"},
        ) as socket:
            try:
                socket.receive_text()
            except Exception:
                pass
    assert seen[0][0] == "ws://worker.internal:9889/terminals/t1/ws"
    assert seen[0][1]["additional_headers"] == {"Authorization": "Bearer node-secret"}


def test_serviceaccount_http_downgrade_is_refused(panel, monkeypatch):
    monkeypatch.setattr(panel["config"], "KUBE_API", "http://cluster.invalid")
    with pytest.raises(RuntimeError, match="HTTPS"):
        panel["config"]._tls_kwargs()
