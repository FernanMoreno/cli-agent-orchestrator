"""Expanded catalogs never replace independent operation and target authority."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from cli_agent_orchestrator.mcp_server import server
from cli_agent_orchestrator.mcp_server.tool_profiles import ToolProfileMiddleware
from cli_agent_orchestrator.utils.mcp_tool_policy import TOOL_PROFILES

BASE = TOOL_PROFILES["collaboration"]
EXPECTED = {
    "coordination": BASE
    | {
        "list_local_cao_peers",
        "assign_local_cao_task",
        "get_local_cao_task",
        "cancel_local_cao_task",
    },
    "supervision": BASE | {"inspect_terminal_turn", "verify_terminal_turn", "cancel_terminal_turn"},
    "knowledge": BASE | {"load_skill", "memory_recall"},
    "knowledge_writer": BASE | {"load_skill", "memory_recall", "memory_store", "report_outcome"},
    "elastic_worker": BASE | {"complete_assignment"},
}
pytestmark = pytest.mark.offline_database


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", list(EXPECTED))
async def test_every_registered_name_is_classified_and_direct_dispatch_matches(profile):
    policy = ToolProfileMiddleware(profile)
    names = {t.name for t in await server.mcp.local_provider.list_tools()}
    assert EXPECTED[profile] <= names
    called = []

    async def dispatch(ctx):
        called.append(ctx.message.name)
        return "called"

    for name in names | {"future_unknown", "evil___assign"}:
        ctx = SimpleNamespace(message=SimpleNamespace(name=name))
        if name in EXPECTED[profile]:
            assert await policy.on_call_tool(ctx, dispatch) == "called"
        else:
            with pytest.raises(ToolError):
                await policy.on_call_tool(ctx, dispatch)
    assert set(called) == EXPECTED[profile]


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", list(EXPECTED))
async def test_actual_transport_catalog_matches_expanded_profile(profile):
    audit = FastMCP("expanded-profile")
    audit.mount(server.mcp)
    audit.add_middleware(ToolProfileMiddleware(profile))
    async with Client(audit) as client:
        assert {t.name for t in await client.list_tools()} == EXPECTED[profile]
        assert await client.list_resources() == []
        assert await client.list_resource_templates() == []
        assert await client.list_prompts() == []
        for denied in {
            "handoff",
            "assign",
            "delete_terminal",
            "memory_forget",
            "workflow_run",
            "future_unknown",
        }:
            with pytest.raises(ToolError):
                await client.call_tool(denied, {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["inspect_terminal_turn", "verify_terminal_turn", "cancel_terminal_turn"]
)
@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"id": "ccccdddd", "caller_id": "other"},
        {"id": "badc0ffe", "caller_id": "aaaabbbb"},
    ],
)
async def test_bound_caller_cannot_operate_on_unrelated_or_unknown_turn(
    monkeypatch, name, metadata
):
    monkeypatch.setenv("CAO_TERMINAL_ID", "aaaabbbb")
    monkeypatch.setattr(server, "_tool_denied_reason", lambda *a, **kw: None)

    def lookup(path, **kwargs):
        if metadata is None:
            raise ValueError("private lookup failure")
        return metadata

    monkeypatch.setattr(server.mcp_utils, "get_json", lookup)
    inspect = Mock(return_value={"success": True})
    action = Mock(return_value={"success": True})
    monkeypatch.setattr(server, "_inspect_turn_impl", inspect)
    monkeypatch.setattr(server, "_turn_action_impl", action)
    fn = getattr(server, name)
    result = await fn("ccccdddd", *([] if name == "inspect_terminal_turn" else ["a" * 32]))
    assert result["success"] is False
    assert "private" not in result["error"]
    inspect.assert_not_called()
    action.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["inspect_terminal_turn", "verify_terminal_turn", "cancel_terminal_turn"]
)
@pytest.mark.parametrize("target", ["aaaabbbb", "ccccdddd"])
async def test_turn_authority_allows_self_or_direct_child_and_preserves_generation(
    monkeypatch, name, target
):
    monkeypatch.setenv("CAO_TERMINAL_ID", "aaaabbbb")
    monkeypatch.setattr(server, "_tool_denied_reason", lambda *a, **kw: None)
    monkeypatch.setattr(
        server.mcp_utils, "get_json", lambda *a, **kw: {"id": target, "caller_id": "aaaabbbb"}
    )
    inspect = Mock(return_value={"success": True})
    action = Mock(return_value={"success": True})
    monkeypatch.setattr(server, "_inspect_turn_impl", inspect)
    monkeypatch.setattr(server, "_turn_action_impl", action)
    result = await getattr(server, name)(
        target, *([] if name == "inspect_terminal_turn" else ["a" * 32])
    )
    assert result["success"] is True
    if name == "inspect_terminal_turn":
        inspect.assert_called_once_with(target)
    else:
        action.assert_called_once_with(
            target, "a" * 32, "verify" if name.startswith("verify") else "cancel"
        )


@pytest.mark.parametrize(
    "name", ["inspect_terminal_turn", "verify_terminal_turn", "cancel_terminal_turn"]
)
def test_turn_server_denial_precedes_target_lookup(monkeypatch, name):
    import asyncio

    monkeypatch.setenv("CAO_TERMINAL_ID", "aaaabbbb")
    gate = Mock(return_value="server permission denied")
    lookup = Mock(side_effect=AssertionError("lookup after denial"))
    monkeypatch.setattr(server, "_tool_denied_reason", gate)
    monkeypatch.setattr(server.mcp_utils, "get_json", lookup)
    result = asyncio.run(
        getattr(server, name)("ccccdddd", *([] if name == "inspect_terminal_turn" else ["a" * 32]))
    )
    assert result == {"success": False, "error": "server permission denied"}
    lookup.assert_not_called()


@pytest.mark.parametrize(
    "caller,target",
    [("bad", "ccccdddd"), ("aaaabbbb", "../private"), ("aaaabbbb", ""), ("aaaabbbb", None)],
)
def test_malformed_turn_identity_cannot_start_lookup(monkeypatch, caller, target):
    monkeypatch.setenv("CAO_TERMINAL_ID", caller)
    lookup = Mock(side_effect=AssertionError("lookup after invalid identity"))
    monkeypatch.setattr(server.mcp_utils, "get_json", lookup)
    assert server._turn_target_denied_reason(target)
    lookup.assert_not_called()


@pytest.mark.parametrize("context", [None, {}, {"cwd": ""}])
def test_local_coordination_keeps_assign_local_identity_and_project_gate(monkeypatch, context):
    gate = Mock(return_value=None)
    monkeypatch.setattr(server, "_tool_denied_reason", gate)
    monkeypatch.setattr(server, "_current_terminal_id", lambda: "aaaabbbb")
    monkeypatch.setattr(server, "_get_terminal_context_from_env", lambda **kw: context)
    caller, cwd, denied = server._local_peer_caller_context()
    assert caller is None and cwd is None and denied
    gate.assert_called_once_with("assign", local_only=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing", ["CAO_ELASTIC_WORKER_ID", "CAO_ELASTIC_BROKER_URL", "CAO_ELASTIC_RELEASE_TOKEN"]
)
async def test_elastic_profile_cannot_complete_without_configured_worker(monkeypatch, missing):
    for key, value in {
        "CAO_ELASTIC_WORKER_ID": "worker",
        "CAO_ELASTIC_BROKER_URL": "http://broker.invalid",
        "CAO_ELASTIC_RELEASE_TOKEN": "synthetic-test-token",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing)
    send = Mock(side_effect=AssertionError("unconfigured worker must not deliver"))
    release = Mock(side_effect=AssertionError("unconfigured worker must not release"))
    monkeypatch.setattr(server, "_send_message_impl", send)
    monkeypatch.setattr(server.requests, "post", release)
    result = await server.complete_assignment("synthetic result")
    assert result["success"] is False
    send.assert_not_called()
    release.assert_not_called()


@pytest.mark.parametrize("profile", list(EXPECTED))
def test_expanded_native_preflight_requires_each_profile_specific_tool(tmp_path, profile):
    from test.providers.test_mcp_profile_preflight import _config, _validate

    from cli_agent_orchestrator.utils.mcp_tool_policy import MCP_TOOL_PROFILE_ENV

    expected = EXPECTED[profile]
    missing = sorted(expected - BASE)[0]
    environment = {MCP_TOOL_PROFILE_ENV: profile}
    _validate(_config(tmp_path, environment, expected))
    with pytest.raises(ValueError, match="grant"):
        _validate(_config(tmp_path, environment, expected - {missing}))
