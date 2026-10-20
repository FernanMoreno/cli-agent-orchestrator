"""Explicit MCP policy must reach the real v2 initialization preflight."""

import copy
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.utils import opencode_config
from cli_agent_orchestrator.utils.mcp_tool_policy import MCP_TOOL_PROFILE_ENV, TOOL_PROFILES

pytestmark = [pytest.mark.offline_database, pytest.mark.asyncio]


@pytest.fixture
def launch(tmp_path, monkeypatch):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    monkeypatch.setattr(opencode_cli, "CAO_HOME_DIR", tmp_path / "cao")
    monkeypatch.setattr(opencode_cli, "OPENCODE_AGENTS_DIR", agents)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "synthetic-data"))
    monkeypatch.delenv("OPENCODE_DB", raising=False)
    monkeypatch.setattr(
        opencode_cli,
        "resolve_opencode_runtime",
        lambda: ("/opt/bin/opencode", {"PATH": "/usr/bin:/bin"}),
    )
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 2)
    monkeypatch.setattr(opencode_cli, "wait_for_shell", AsyncMock(return_value=True))
    backend = MagicMock()
    monkeypatch.setattr(opencode_cli, "get_backend", lambda: backend)
    instance = opencode_cli.OpenCodeCliProvider("test1234", "test", "window", "reviewer")
    monkeypatch.setattr(instance, "_wait_for_initial_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(instance, "_wait_for_v2_cao_mcp_ready", AsyncMock(return_value=True))
    yield instance, backend, tmp_path / "cao"
    instance.cleanup()


def configure(monkeypatch, layout, servers, grants):
    source = {
        "mcp": {"servers": servers} if layout == "nested" else servers,
        "agent": {"reviewer": {"tools": grants}},
    }
    monkeypatch.setattr(opencode_config, "read_config", lambda: source)
    return source


def profiled(**extra):
    return {
        "type": "local",
        "command": ["cao-mcp-server"],
        "cao_tool_profile": "collaboration",
        **extra,
    }


@pytest.mark.parametrize("layout", ["nested", "flat"])
@pytest.mark.parametrize("granted", [[], sorted(TOOL_PROFILES["interaction"])])
async def test_explicit_profile_rejects_missing_grants_without_interaction_opt_in(
    launch, monkeypatch, layout, granted
):
    instance, backend, cao_home = launch
    source = configure(
        monkeypatch,
        layout,
        {"cao": profiled()},
        {"cao_" + tool: True for tool in granted},
    )
    before = copy.deepcopy(source)
    with pytest.raises(ValueError, match="has no CAO MCP grant"):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance._owned_v2_config_dir is None
    assert not list((cao_home / "opencode-v2").glob("terminal-*"))
    assert source == before
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"


@pytest.mark.parametrize("layout", ["nested", "flat"])
async def test_complete_profile_grants_do_not_advertise_unrequested_interaction(
    launch, monkeypatch, layout
):
    instance, backend, _ = launch
    source = configure(
        monkeypatch,
        layout,
        {"cao": profiled()},
        {"cao_" + tool: True for tool in TOOL_PROFILES["collaboration"]},
    )
    before = copy.deepcopy(source)
    assert await instance.initialize() is True
    generated = json.loads((instance._v2_config_dir / "opencode.json").read_text())
    assert generated["mcp"]["servers"]["cao"]["environment"][MCP_TOOL_PROFILE_ENV] == (
        "collaboration"
    )
    backend.send_keys.assert_called_once()
    assert source == before
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"
    assert instance._interaction_preflight_ok is False
    instance._wait_for_v2_cao_mcp_ready.assert_awaited_once()


@pytest.mark.parametrize("layout", ["nested", "flat"])
async def test_every_profiled_child_requires_its_own_grant(launch, monkeypatch, layout):
    instance, backend, _ = launch
    configure(
        monkeypatch,
        layout,
        {"first": profiled(), "second": profiled()},
        {"first_" + tool: True for tool in TOOL_PROFILES["collaboration"]},
    )
    with pytest.raises(ValueError, match="has no CAO MCP grant"):
        await instance.initialize()
    backend.send_keys.assert_not_called()


@pytest.mark.parametrize("layout", ["nested", "flat"])
async def test_profiled_text_command_is_rejected_before_launch(launch, monkeypatch, layout):
    instance, backend, _ = launch
    configure(
        monkeypatch,
        layout,
        {"cao": profiled(command="cao-mcp-server")},
        {"cao*": True},
    )
    with pytest.raises(ValueError, match="CAO MCP server command is invalid"):
        await instance.initialize()
    backend.send_keys.assert_not_called()


@pytest.mark.parametrize("layout", ["nested", "flat"])
async def test_profiled_launch_rejects_ambiguous_server_names(launch, monkeypatch, layout):
    instance, backend, _ = launch
    configure(
        monkeypatch,
        layout,
        {"cao.mcp": profiled(), "cao_mcp": {"command": ["other-server"]}},
        {"cao.mcp*": True},
    )
    with pytest.raises(ValueError, match="server names are ambiguous"):
        await instance.initialize()
    backend.send_keys.assert_not_called()


@pytest.mark.parametrize("layout", ["nested", "flat"])
async def test_explicit_profile_requires_a_selected_agent(launch, monkeypatch, layout):
    instance, backend, _ = launch
    instance._agent_profile = ""
    configure(monkeypatch, layout, {"cao": profiled()}, {"cao*": True})
    with pytest.raises(ValueError, match="requires a native agent profile"):
        await instance.initialize()
    backend.send_keys.assert_not_called()


@pytest.mark.parametrize("layout", ["nested", "flat"])
@pytest.mark.parametrize("disabled", [{"enabled": False}, {"disabled": True}])
async def test_disabled_profiled_child_requires_no_active_grant(
    launch, monkeypatch, layout, disabled
):
    instance, backend, _ = launch
    configure(monkeypatch, layout, {"cao": profiled(**disabled)}, {})
    assert await instance.initialize() is True
    backend.send_keys.assert_called_once()
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"


@pytest.mark.parametrize("layout", ["nested", "flat"])
async def test_unprofiled_child_preserves_no_opt_in_behavior(launch, monkeypatch, layout):
    instance, backend, _ = launch
    configure(monkeypatch, layout, {"cao": {"type": "local", "command": ["cao-mcp-server"]}}, {})
    assert await instance.initialize() is True
    backend.send_keys.assert_called_once()
    assert instance._interaction_preflight_ok is False
