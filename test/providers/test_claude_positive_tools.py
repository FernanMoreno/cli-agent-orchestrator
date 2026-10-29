"""T224: new native tools cannot escape an explicit CAO tool allowlist."""

import shlex

import pytest

from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.providers import claude_code as module
from cli_agent_orchestrator.utils.tool_mapping import get_allowed_tools

pytestmark = pytest.mark.offline_database
# Names actually observed in T223 preflight-r1, not a hypothetical future catalogue.
OBSERVED_UNMAPPED = {
    "AskUserQuestion",
    "CronCreate",
    "CronDelete",
    "CronList",
    "EnterPlanMode",
    "EnterWorktree",
    "ExitPlanMode",
    "ExitWorktree",
    "ListAgents",
    "ReportFindings",
    "ScheduleWakeup",
    "SendMessage",
    "Skill",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "Workflow",
}


def command(monkeypatch, tmp_path, allowed, profile=None):
    monkeypatch.setattr(module, "CAO_HOME_DIR", tmp_path)
    p = module.ClaudeCodeProvider("1234abcd", "session", "window", allowed_tools=allowed)
    return shlex.split(p._build_claude_command(profile))


@pytest.mark.parametrize(
    "allowed",
    [
        [],
        ["@cao-mcp-server"],
        ["fs_read"],
        ["fs_list"],
        ["fs_*"],
        ["execute_bash"],
        ["web_fetch"],
        ["unknown-capability"],
    ],
)
def test_restricted_native_availability_is_a_positive_selection_including_empty(
    monkeypatch, tmp_path, allowed
):
    args = command(monkeypatch, tmp_path, allowed)
    assert args.count("--tools") == 1
    selected = args[args.index("--tools") + 1]
    expected = get_allowed_tools("claude_code", allowed)
    assert selected == ",".join(expected)
    assert not (set(selected.split(",")) & OBSERVED_UNMAPPED)
    if not expected:
        assert selected == ""  # Keep a real empty argv value through shlex, not missing/default.
    assert "--disallowedTools" in args


@pytest.mark.parametrize("allowed", [None, ["*"]])
def test_unrestricted_native_selection_remains_cli_default(monkeypatch, tmp_path, allowed):
    args = command(monkeypatch, tmp_path, allowed)
    assert "--tools" not in args and "--disallowedTools" not in args


def test_native_availability_does_not_replace_cao_mcp_policy(monkeypatch, tmp_path):
    import json
    from pathlib import Path

    from cli_agent_orchestrator.utils.mcp_tool_policy import (
        MCP_TOOL_FILTER_ENV,
        MCP_TOOL_PROFILE_ENV,
    )

    profile = AgentProfile(
        name="restricted",
        description="control",
        mcpServers={
            "cao": {
                "command": "cao-mcp-server",
                "cao_tool_profile": "collaboration",
                "enabled_tools": ["list_siblings"],
            }
        },
    )
    args = command(monkeypatch, tmp_path, ["@cao-mcp-server"], profile)
    assert args[args.index("--tools") + 1] == ""
    config = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())["mcpServers"]["cao"]
    assert config["env"][MCP_TOOL_PROFILE_ENV] == "collaboration"
    assert config["env"][MCP_TOOL_FILTER_ENV]
    assert "--strict-mcp-config" in args
