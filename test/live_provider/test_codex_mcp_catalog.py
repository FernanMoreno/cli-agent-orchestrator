"""Offline contracts for native catalog-only inspection; no CLI or database."""

import json
import time
from pathlib import Path
from test.live_provider import codex_mcp_catalog as catalog

import pytest
from jsonschema import ValidationError

pytestmark = pytest.mark.offline_database


def client_with_frames(tmp_path, frames, schema=None):
    client = catalog.CatalogClient.__new__(catalog.CatalogClient)
    client.deadline = time.monotonic() + 5
    client.serial = 0
    client.schema_root = tmp_path
    path = tmp_path / catalog.SCHEMAS["initialize"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema or {"type": "object"}))
    client.buffer = b"".join((json.dumps(frame) + "\n").encode() for frame in frames)
    client.sent = []
    client.send = client.sent.append
    return client


def test_catalog_client_never_allows_model_turns(tmp_path):
    client = client_with_frames(tmp_path, [])
    with pytest.raises(ValueError, match="catalog_only_method"):
        client.rpc("turn/start", {"prompt": "PRIVATE"})
    assert client.sent == []


def test_native_request_uses_generated_schema_before_sending(tmp_path):
    client = client_with_frames(tmp_path, [], {"type": "object", "required": ["clientInfo"]})
    with pytest.raises(ValidationError):
        client.rpc("initialize", {})
    assert client.sent == []


def test_native_replies_are_correlated_and_unknown_requests_are_refused(tmp_path):
    frames = [
        {"id": 9, "method": "command/execute", "params": {"text": "PRIVATE"}},
        {"id": 2, "result": {"other": True}},
        {"id": 1, "result": {"accepted": True}},
    ]
    client = client_with_frames(tmp_path, frames)
    assert client.rpc("initialize", {}) == {"accepted": True}
    assert client.sent[0] == {"id": 1, "method": "initialize", "params": {}}
    assert client.sent[1]["error"]["code"] == -32601
    assert "PRIVATE" not in str(client.sent)


@pytest.mark.parametrize(
    "frame", [[], {"id": True, "result": {}}, {"id": 1, "error": {"code": True}}, {"id": 1}]
)
def test_malformed_native_frames_fail_closed(tmp_path, frame):
    client = client_with_frames(tmp_path, [frame])
    with pytest.raises(ValueError):
        client.rpc("initialize", {})


def test_native_rpc_errors_do_not_expose_private_messages(tmp_path):
    client = client_with_frames(
        tmp_path, [{"id": 1, "error": {"code": -32602, "message": "PRIVATE"}}]
    )
    with pytest.raises(catalog.NativeRpcError) as failure:
        client.rpc("initialize", {})
    assert failure.value.code == -32602
    assert "PRIVATE" not in str(failure.value)


def test_native_catalog_command_keeps_production_filters_and_containment(tmp_path):
    command = catalog.native_overrides(
        "codex --profile private -c 'mcp_servers.cao.enabled_tools=[]'", tmp_path, "/private/codex"
    )
    assert command[:4] == ["/private/codex", "app-server", "--listen", "stdio://"]
    assert "mcp_servers.cao.enabled_tools=[]" in command
    assert all(
        f"features.{feature}=false" in command for feature in ("apps", "plugins", "shell_tool")
    )
    assert "exec" not in command and "--yolo" not in command


def test_catalog_root_admission_precedes_auth_or_process_access(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog.journey, "_codex_auth_path", lambda: pytest.fail("auth accessed"))
    tmp_path.chmod(0o700)
    (tmp_path / "cases").mkdir()
    with pytest.raises(ValueError, match="fresh_catalog_root"):
        catalog.run(tmp_path)


def test_disabled_tool_refusal_requires_exact_native_cause(monkeypatch):
    tool = "cao_wait_for_user_answers"
    message = f"tool '{tool}' is disabled for MCP server '{catalog.SERVER}'"

    def refused(*args):
        raise catalog.NativeRpcError(-32603, message)

    monkeypatch.setattr(catalog, "_call", refused)
    assert catalog._denied_call(None, "thread", tool, {}) == "native_client"
    for code, text in ((-32603, "server failed"), (-32602, message), (-32603, message + " extra")):

        def unexpected(*args):
            raise catalog.NativeRpcError(code, text)

        monkeypatch.setattr(catalog, "_call", unexpected)
        with pytest.raises(catalog.NativeRpcError):
            catalog._denied_call(None, "thread", tool, {})


def test_generic_mcp_error_does_not_establish_policy_denial(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "_call",
        lambda *args: {
            "isError": True,
            "content": [{"type": "text", "text": "handler validation failed"}],
        },
    )
    assert catalog._denied_call(None, "thread", "cao_ask_user", {}) is None
