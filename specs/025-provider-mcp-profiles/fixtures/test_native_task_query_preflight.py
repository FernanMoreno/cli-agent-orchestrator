"""Execute the source-derived observer before a native task-query run."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastmcp import FastMCP
from mcp import types

RUNNER_PATH = Path(__file__).with_name("run_native_v2_acceptance.py")
SPEC = importlib.util.spec_from_file_location("native_task_query_candidate", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)
TOOL = "get_local_cao_task"
CALLER = "a1b2c3d4"
TASK = "11111111-1111-4111-8111-111111111111"
NONCE = "b" * 32


def test_derived_task_observer_keeps_late_issuance_and_protected_caller(tmp_path):
    source_path = RUNNER_PATH.resolve().parents[3] / "test/live_provider/opencode_mcp_observer.py"
    derived = runner._derive_private_mcp_observer_source(
        source_path.read_bytes(),
        sender_catalog=runner.COORDINATION_SENDER_TOOLS,
        expected_message_bodies=tuple(body for _, body in runner.PHASES),
        expected_safe_result_tool=TOOL,
    )
    path = tmp_path / "derived_observer.py"
    path.write_text(derived)
    spec = importlib.util.spec_from_file_location("unregistered_derived_task_observer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Match the actual native fixture's loader without sys.modules registration.
    spec.loader.exec_module(module)
    server = FastMCP("task-query-preflight")

    @server.tool()
    async def get_local_cao_task(task_id: str) -> dict:
        assert task_id == TASK
        return {
            "success": True,
            "task_id": task_id,
            "requester_terminal_id": CALLER,
            "state": "succeeded",
            "result": {"state": "succeeded", "output": "PRIVATE_FIXTURE_OUTPUT"},
        }

    binding = tmp_path / "binding.json"
    environment = {
        "CAO_OPENCODE_MCP_OBSERVER": "1",
        "CAO_OPENCODE_MCP_OBSERVER_PATH": str(tmp_path / "native-events"),
        "CAO_OPENCODE_MCP_OBSERVER_NONCE": NONCE,
        "CAO_OPENCODE_MCP_SAFE_RESULT_TOOL": TOOL,
        "CAO_OPENCODE_MCP_SAFE_TASK_BINDING_PATH": str(binding),
        "CAO_TERMINAL_ID": CALLER,
    }
    observer = module.install_protocol_observer_from_env(server, environment)
    assert not binding.exists()
    binding.write_text(
        json.dumps(
            {"nonce": NONCE, "task_id": TASK, "requester_terminal_id": CALLER, "state": "succeeded"}
        )
    )
    binding.chmod(0o600)

    async def dispatch():
        return await server._mcp_server.request_handlers[types.CallToolRequest](
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(name=TOOL, arguments={"task_id": TASK}),
            )
        )

    try:
        response = asyncio.run(dispatch())
    finally:
        observer.close()
    ledger = tmp_path / ("native-events-" + CALLER)
    summary = module.read_protocol_observations(
        ledger, expected_nonce=NONCE, expected_safe_result_tool=TOOL
    )
    assert response.root.isError is False
    call = summary["mcp_tool_calls"][0]
    assert call["safe_result_success"] is True
    for flag in (
        "argument_matches",
        "id_matches",
        "requester_matches",
        "state_matches",
        "result_state_matches",
    ):
        assert call["safe_result_task_" + flag] is True
    assert all(
        value not in ledger.read_text() for value in (TASK, CALLER, "PRIVATE_FIXTURE_OUTPUT")
    )


@pytest.mark.parametrize("selection", ["unknown_tool", True, [], {}])
def test_derived_observer_rejects_unsupported_safe_result_selection(selection):
    source_path = RUNNER_PATH.resolve().parents[3] / "test/live_provider/opencode_mcp_observer.py"
    with pytest.raises(runner.GateError, match="observer_source_binding_shape_changed"):
        runner._derive_private_mcp_observer_source(
            source_path.read_bytes(),
            sender_catalog=runner.COORDINATION_SENDER_TOOLS,
            expected_message_bodies=tuple(body for _, body in runner.PHASES),
            expected_safe_result_tool=selection,
        )


def _load_derived(tmp_path, selection):
    source_path = RUNNER_PATH.resolve().parents[3] / "test/live_provider/opencode_mcp_observer.py"
    derived = runner._derive_private_mcp_observer_source(
        source_path.read_bytes(),
        sender_catalog=runner.COORDINATION_SENDER_TOOLS,
        expected_message_bodies=tuple(body for _, body in runner.PHASES),
        expected_safe_result_tool=selection,
    )
    path = tmp_path / "legacy_observer.py"
    path.write_text(derived)
    spec = importlib.util.spec_from_file_location("unregistered_legacy_observer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("selection", [None, "list_local_cao_peers"])
def test_derived_observer_executes_legacy_modes(tmp_path, selection):
    module = _load_derived(tmp_path, selection)
    server = FastMCP("legacy-preflight")

    @server.tool()
    async def list_local_cao_peers() -> dict:
        return {"success": True, "peers": []}

    observer = module.install_protocol_observer_from_env(
        server,
        {
            "CAO_OPENCODE_MCP_OBSERVER": "1",
            "CAO_OPENCODE_MCP_OBSERVER_PATH": str(tmp_path / "native-events"),
            "CAO_OPENCODE_MCP_OBSERVER_NONCE": NONCE,
            "CAO_TERMINAL_ID": CALLER,
        },
    )

    async def dispatch():
        return await server._mcp_server.request_handlers[types.CallToolRequest](
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(name="list_local_cao_peers", arguments={}),
            )
        )

    try:
        response = asyncio.run(dispatch())
    finally:
        observer.close()
    summary = module.read_protocol_observations(
        tmp_path / ("native-events-" + CALLER),
        expected_nonce=NONCE,
        expected_safe_result_tool=selection,
    )
    assert response.root.isError is False
    call = summary["mcp_tool_calls"][0]
    assert call["status"] == "completed" and call["error_present"] is False
    if selection is None:
        assert "safe_result_success" not in call
    else:
        assert call["safe_result_success"] is True
        assert call["safe_result_peer_count"] == 0


def test_derived_task_observer_refuses_missing_binding_selector(tmp_path):
    module = _load_derived(tmp_path, TOOL)
    with pytest.raises(RuntimeError, match="mcp_observer_task_binding_invalid"):
        module.install_protocol_observer_from_env(
            FastMCP("missing-binding-preflight"),
            {
                "CAO_OPENCODE_MCP_OBSERVER": "1",
                "CAO_OPENCODE_MCP_OBSERVER_PATH": str(tmp_path / "native-events"),
                "CAO_OPENCODE_MCP_OBSERVER_NONCE": NONCE,
                "CAO_OPENCODE_MCP_SAFE_RESULT_TOOL": TOOL,
                "CAO_TERMINAL_ID": CALLER,
            },
        )
    assert not (tmp_path / ("native-events-" + CALLER)).exists()
