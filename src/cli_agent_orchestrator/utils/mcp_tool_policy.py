"""Closed CAO MCP surface profiles; these never grant operation authority."""

from types import MappingProxyType

MCP_TOOL_PROFILE_ENV = "CAO_INTERNAL_MCP_TOOL_PROFILE"
MCP_TOOL_PROFILE_CONFIG = "cao_tool_profile"

INTERACTION_TOOLS = frozenset(
    {"cao_ask_user", "cao_wait_for_user_answers", "cao_acknowledge_user_answers"}
)
TOOL_PROFILES = MappingProxyType(
    {
        "interaction": INTERACTION_TOOLS,
        "collaboration": INTERACTION_TOOLS
        | {
            "send_message",
            "list_siblings",
            "cao_list_interactions",
            "cao_get_interaction",
            "cao_escalate_interaction",
        },
    }
)


def resolve_tool_profile(value: object) -> frozenset[str]:
    """Reject malformed/unknown selection without echoing profile input."""
    if not isinstance(value, str) or value not in TOOL_PROFILES:
        raise ValueError("CAO MCP tool profile must be interaction or collaboration")
    return TOOL_PROFILES[value]
