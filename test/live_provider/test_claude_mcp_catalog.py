"""Offline Claude control contracts; real owned pipes, no models or database."""

import json
import subprocess
import sys
from test.live_provider import claude_mcp_catalog as catalog

import pytest

pytestmark = pytest.mark.offline_database


def response(payload, request_id="catalog-1"):
    return {
        "type": "control_response",
        "response": {"subtype": "success", "request_id": request_id, "response": payload},
    }


def test_only_catalog_requests_can_be_sent():
    assert catalog.control_request("initialize", "catalog-1")["request"] == {
        "subtype": "initialize",
        "hooks": None,
    }
    assert catalog.control_request("mcp_status", "catalog-2")["request"] == {
        "subtype": "mcp_status"
    }
    for method in ["user", "can_use_tool", "mcp_message", "interrupt", ""]:
        with pytest.raises(ValueError):
            catalog.control_request(method, "catalog-1")


@pytest.mark.parametrize(
    "raw", [b"[]", b'{"type":"system","type":"control_response"}', b'{"x":NaN}', b"\xff", b"{"]
)
def test_invalid_json_frames_are_rejected(raw):
    with pytest.raises(ValueError):
        catalog.decode_frame(raw)


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "assistant"},
        {"type": "result"},
        {"type": "control_request", "request_id": "native", "request": {"subtype": "can_use_tool"}},
        {
            "type": "control_response",
            "response": {"subtype": "error", "request_id": "catalog-1", "error": "PRIVATE"},
        },
        response({}, True),
        response({}, "other"),
        response([]),
    ],
)
def test_execution_or_invalid_control_is_not_accepted(frame):
    with pytest.raises(ValueError) as error:
        catalog.control_result(frame, "catalog-1")
    assert "PRIVATE" not in str(error.value)


def test_catalog_requires_connected_exact_server_and_explicit_tools():
    status = {"mcpServers": [{"name": "cao", "status": "connected", "tools": [{"name": "ask"}]}]}
    assert catalog.tool_names(status, "cao") == frozenset({"ask"})
    status["mcpServers"][0]["tools"] = []
    assert catalog.tool_names(status, "cao") == frozenset()
    status["mcpServers"][0].pop("tools")
    with pytest.raises(ValueError):
        catalog.tool_names(status, "cao")


@pytest.mark.parametrize(
    "servers",
    [
        [],
        [{"name": "cao", "status": "failed", "tools": []}],
        [{"name": "cao", "status": "pending", "tools": []}],
        [{"name": "cao", "status": "connected", "tools": [{"name": "x"}, {"name": "x"}]}],
        [
            {"name": "cao", "status": "connected", "tools": []},
            {"name": "decoy", "status": "connected", "tools": []},
        ],
    ],
)
def test_missing_pending_duplicate_and_foreign_catalogs_fail(servers):
    with pytest.raises(ValueError):
        catalog.tool_names({"mcpServers": servers}, "cao")


def test_fragmented_real_pipe_and_correlated_request():
    child = """import sys,json
for line in sys.stdin:
 r=json.loads(line)
 msg={"type":"control_response","response":{"subtype":"success","request_id":r["request_id"],"response":{"ok":True}}}
 raw=json.dumps(msg)+"\\n"
 for c in raw:
  sys.stdout.write(c);sys.stdout.flush()
"""
    with subprocess.Popen(
        [sys.executable, "-u", "-c", child], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    ) as process:
        with catalog.ControlClient(process, timeout=5) as client:
            assert client.rpc("initialize") == {"ok": True}
            assert client.rpc("mcp_status") == {"ok": True}
        process.stdin.close()
        process.wait(timeout=5)


@pytest.mark.parametrize(
    "body,expected",
    [
        ("print('x'*2048,flush=True)", "native_control_input_limit"),
        ("import time;time.sleep(3)", "native_control_deadline"),
        ("pass", "native_control_eof"),
    ],
)
def test_oversized_or_silent_real_child_is_bounded(body, expected):
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", body], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    try:
        with catalog.ControlClient(process, timeout=0.2, max_bytes=1024) as client:
            with pytest.raises((ValueError, TimeoutError), match=expected):
                client.rpc("initialize")
    finally:
        process.kill()
        process.communicate(timeout=5)
