"""Launch tool syntax accepts a permitted MCP name without changing identity syntax."""

import pytest

from cli_agent_orchestrator.services.work_launch_runtime import LaunchIntent


def test_launch_intent_preserves_explicit_mcp_tool_token():
    intent = LaunchIntent(
        agent_profile="worker",
        session_name="work-session",
        message="Read only",
        allowed_tools=("@cao-mcp-server", "knowledge.read", "@cao-mcp-server"),
    )
    assert intent.allowed_tools == ("@cao-mcp-server", "knowledge.read")


@pytest.mark.parametrize(
    "tool", ["@", "@@cao-mcp-server", "@cao mcp", "@cao\nserver", "@" + "a" * 128]
)
def test_launch_intent_rejects_malformed_mcp_tool_tokens(tool):
    with pytest.raises(ValueError):
        LaunchIntent("worker", "work-session", "Read only", (tool,))


@pytest.mark.parametrize("field", ["agent_profile", "session_name"])
def test_mcp_prefix_does_not_expand_other_launch_identities(field):
    values = dict(agent_profile="worker", session_name="work-session", message="Read only")
    values[field] = "@cao-mcp-server"
    with pytest.raises(ValueError):
        LaunchIntent(**values)
