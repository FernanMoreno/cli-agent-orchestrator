"""Closed CAO MCP surface profiles; these never grant operation authority."""

import json
from types import MappingProxyType

MCP_TOOL_PROFILE_ENV = "CAO_INTERNAL_MCP_TOOL_PROFILE"
MCP_TOOL_PROFILE_CONFIG = "cao_tool_profile"
MCP_TOOL_FILTER_ENV = "CAO_INTERNAL_MCP_ENABLED_TOOLS"
MAX_TOOL_FILTER_LENGTH = 4096

INTERACTION_TOOLS = frozenset(
    {"cao_ask_user", "cao_wait_for_user_answers", "cao_acknowledge_user_answers"}
)
COLLABORATION_TOOLS = INTERACTION_TOOLS | {
    "send_message",
    "list_siblings",
    "cao_list_interactions",
    "cao_get_interaction",
    "cao_escalate_interaction",
}
KNOWLEDGE_TOOLS = COLLABORATION_TOOLS | {"load_skill", "memory_recall"}
TOOL_PROFILES = MappingProxyType(
    {
        "interaction": INTERACTION_TOOLS,
        "collaboration": COLLABORATION_TOOLS,
        "coordination": COLLABORATION_TOOLS
        | {
            "list_local_cao_peers",
            "assign_local_cao_task",
            "get_local_cao_task",
            "cancel_local_cao_task",
        },
        "supervision": COLLABORATION_TOOLS
        | {
            "inspect_terminal_turn",
            "verify_terminal_turn",
            "cancel_terminal_turn",
        },
        "knowledge": KNOWLEDGE_TOOLS,
        "knowledge_writer": KNOWLEDGE_TOOLS | {"memory_store", "report_outcome"},
        "elastic_worker": COLLABORATION_TOOLS | {"complete_assignment"},
    }
)


def resolve_tool_profile(value: object) -> frozenset[str]:
    """Reject malformed/unknown selection without echoing profile input."""
    if not isinstance(value, str) or value not in TOOL_PROFILES:
        raise ValueError("CAO MCP tool profile must be a supported profile name")
    return TOOL_PROFILES[value]


def encode_tool_filter(value: object, profile: object) -> str:
    """Translate an explicit client list into a bounded, exact profile subset."""
    if not isinstance(value, (list, tuple)) or any(not isinstance(t, str) for t in value):
        raise ValueError("CAO MCP enabled_tools must be a list of strings")
    allowed = resolve_tool_profile(profile).intersection(value)
    return json.dumps(sorted(allowed), separators=(",", ":"))


def resolve_tool_filter(value: object, profile: object) -> frozenset[str]:
    """Reject malformed internal policy before the MCP server starts serving."""
    profile_tools = resolve_tool_profile(profile)
    if not isinstance(value, str) or len(value) > MAX_TOOL_FILTER_LENGTH:
        raise ValueError("Invalid CAO MCP tool filter")
    try:
        names = json.loads(value)
    except (ValueError, TypeError):
        raise ValueError("Invalid CAO MCP tool filter") from None
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("Invalid CAO MCP tool filter")
    tools = frozenset(names)
    if not tools.issubset(profile_tools):
        raise ValueError("Invalid CAO MCP tool filter")
    return tools
