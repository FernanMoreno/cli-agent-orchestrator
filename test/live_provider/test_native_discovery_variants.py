"""Literal discovery arguments and independent sets at both native boundaries."""

import json
from test.live_provider.native_read_bridge import allowed_read_path, native_operation_result
from test.live_provider.test_native_scoped_discovery import CALL, CALLER, ROW, packet

import pytest

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize(
    "variant,query,status,count",
    [
        ("cross_session", {"cross_session": True}, 200, 2),
        ("depth_one", {"depth": 1}, 200, 2),
        ("depth_clamp", {"depth": 999}, 200, 1),
        ("depth_zero", {"depth": 0}, 422, 0),
        ("marker_denied", {"cross_session": True}, None, 0),
    ],
)
def test_closed_variants_bind_query_status_and_count(variant, query, status, count):
    from test.live_provider.native_discovery_cases import discovery_case

    case = discovery_case(variant)
    assert case["query"] == query and case["status"] == status and len(case["labels"]) == count


def test_query_path_is_exact_and_default_cannot_admit_queries():
    from test.live_provider.native_discovery_cases import discovery_path

    path = discovery_path(CALLER, "cross_session")
    assert path == f"/terminals/{CALLER}/siblings?cross_session=true"
    assert allowed_read_path("GET", path, CALLER, variant="cross_session")
    assert not allowed_read_path("GET", path, CALLER)
    for wrong in [
        path + "&depth=1",
        path + "&cross_session=true",
        path.replace(CALLER, "ffffffff"),
    ]:
        assert not allowed_read_path("GET", wrong, CALLER, variant="cross_session")
    with pytest.raises(ValueError):
        discovery_path(CALLER, "unknown")


def test_variant_expectation_is_independent_and_exact_for_two_peers():
    from test.live_provider.native_discovery_cases import build_discovery_expectation

    records = [
        {**ROW, "metadata": {"fixture": "matching"}},
        {**ROW, "id": "3333cccc", "metadata": {"fixture": "wrong-group"}},
        {**ROW, "id": "4444dddd", "metadata": {"fixture": "other-session"}},
    ]
    value = build_discovery_expectation(CALLER, records, "cross_session")
    rows = [x for x in records if x["id"] != "3333cccc"]
    assert (
        native_operation_result(packet(rows), CALL, "authorized", expectation=value)["siblings"]
        == rows
    )
    with pytest.raises(ValueError):
        native_operation_result(packet([records[0]]), CALL, "authorized", expectation=value)


def test_zero_depth_business_rejection_must_be_specific():
    import http.client

    body = packet([])
    body["messages"][0]["content"][0]["content"] = json.dumps(
        {
            "success": False,
            "error": f"Failed to list siblings: 422 Client Error: {http.client.responses[422]} for url: http://127.0.0.1:12345/terminals/1111aaaa/siblings?depth=0",
        }
    )
    assert native_operation_result(body, CALL, "invalid")["success"] is False
    body["messages"][0]["content"][0]["content"] = json.dumps(
        {"success": False, "error": "connection refused"}
    )
    with pytest.raises(ValueError):
        native_operation_result(body, CALL, "invalid")


def test_real_proxy_preserves_422_body_and_production_error_shape(tmp_path, monkeypatch):
    import hashlib
    import http.client
    import os
    import socket
    from test.live_provider import native_read_bridge as bridge
    from types import SimpleNamespace

    import requests

    raw = b'{"detail":[{"type":"greater_than_equal","loc":["query","depth"]}]}'

    class Response:
        status_code = 422

        def __enter__(self):
            self.raw = SimpleNamespace(read=lambda _limit: raw)
            return self

        def __exit__(self, *_args):
            pass

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, url, **kwargs):
            assert url.endswith(f"/terminals/{CALLER}/siblings?depth=0")
            return Response()

    monkeypatch.setattr(bridge.requests, "Session", Client)
    for name in list(os.environ):
        if name.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("CAO_NATIVE_DISCOVERY_VARIANT", "depth_zero")
    socket_path = tmp_path / "read.sock"
    monkeypatch.setattr(bridge, "NATIVE_SOCKET", str(socket_path))
    with socket.socket() as selected:
        selected.bind(("127.0.0.1", 0))
        port = selected.getsockname()[1]
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "cao-mcp-server": {
                        "env": {"CAO_TERMINAL_ID": CALLER, "CAO_API_PORT": str(port)}
                    }
                }
            }
        )
    )
    with bridge.OwnedReadBridge(socket_path, "http://127.0.0.1:9999") as broker:
        broker.bind_caller(CALLER, variant="depth_zero")
        server, thread = bridge.start_namespace_read_proxy(["--mcp-config", str(config)])
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            path = f"/terminals/{CALLER}/siblings?depth=0"
            connection.request("GET", path)
            response = connection.getresponse()
            assert response.status == 422 and response.read() == raw
            assert broker.response_bodies == [raw]
            assert broker.rows[0]["body_sha256"] == hashlib.sha256(raw).hexdigest()
            actual = requests.Response()
            actual.status_code = response.status
            actual.reason = response.reason
            actual.url = f"http://127.0.0.1:{port}" + path
            actual._content = raw
            with pytest.raises(requests.HTTPError) as raised:
                actual.raise_for_status()
            from cli_agent_orchestrator.mcp_server.server import _extract_error_detail

            error = "Failed to list siblings: " + _extract_error_detail(actual, str(raised.value))
            body = packet([])
            body["messages"][0]["content"][0]["content"] = json.dumps(
                {"success": False, "error": error}
            )
            assert native_operation_result(body, CALL, "invalid")["success"] is False
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=8)
            assert not thread.is_alive()
