"""Compatibility re-exports for the production OpenCode-v2 TEXT reader."""

from cli_agent_orchestrator.utils.opencode_v2_store import (
    AGENT_RE,
    CANDIDATE_CAP,
    PART_CAP,
    ROW_CAP,
    TEXT_CAP,
    TOKEN_RE,
    ProbeError,
    _authorizer,
    _native_parts,
)

__all__ = [
    "AGENT_RE",
    "CANDIDATE_CAP",
    "PART_CAP",
    "ROW_CAP",
    "TEXT_CAP",
    "TOKEN_RE",
    "ProbeError",
    "_authorizer",
    "_native_parts",
]
