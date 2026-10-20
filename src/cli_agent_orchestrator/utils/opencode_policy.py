"""Installation-consistency envelopes for CAO-managed native OpenCode agents.

These digests bind configuration, not operator authenticity. Enforcement is
provided only by CAO's private v2 launch, never by direct external CLI use.
"""

import base64
import copy
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

import frontmatter

from cli_agent_orchestrator.utils.mcp_resolution import CAO_MCP_SERVER_MODULE
from cli_agent_orchestrator.utils.mcp_tool_policy import (
    MCP_TOOL_PROFILE_CONFIG,
    resolve_tool_profile,
)

NATIVE_POLICY_PREFIX = "<!-- cao-native-mcp-policy:"
MAX_ENCODED_POLICY = 16_384
_ERROR = "OpenCode native MCP policy is incomplete or stale; reinstall the agent"


@dataclass(frozen=True)
class NativeToolPolicy:
    agent_id: str
    state: str
    profiles: Mapping[str, str]
    binding: str | None


def _profiles(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(_ERROR)
    result = {}
    for name, selection in value.items():
        if not isinstance(name, str) or not name or any(ord(c) < 32 for c in name):
            raise ValueError(_ERROR)
        resolve_tool_profile(selection)
        result[name] = selection
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_ERROR)
        result[key] = value
    return result


def parse_native_agent(text: str, agent_id: str):
    """Return the bare native payload and an optional strictly parsed envelope."""
    agent = frontmatter.loads(text)
    if NATIVE_POLICY_PREFIX not in agent.content:
        return agent, None
    first, separator, body = agent.content.partition("\n")
    if (
        not first.startswith(NATIVE_POLICY_PREFIX)
        or not first.endswith(" -->")
        or NATIVE_POLICY_PREFIX in body
    ):
        raise ValueError(_ERROR)
    encoded = first[len(NATIVE_POLICY_PREFIX) : -4]
    if not encoded or len(encoded) > MAX_ENCODED_POLICY:
        raise ValueError(_ERROR)
    try:
        raw = base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
        data = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or set(data) != {
            "version",
            "agent_id",
            "state",
            "profiles",
            "binding",
        }:
            raise ValueError(_ERROR)
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError(_ERROR)
        if data["agent_id"] != agent_id or data["state"] not in ("pending", "ready"):
            raise ValueError(_ERROR)
        profiles = _profiles(data["profiles"])
        binding = data["binding"]
        if data["state"] == "pending":
            if binding is not None:
                raise ValueError(_ERROR)
        elif not isinstance(binding, str) or re.fullmatch(r"[0-9a-f]{64}", binding) is None:
            raise ValueError(_ERROR)
    except (ValueError, TypeError, UnicodeError, KeyError) as exc:
        raise ValueError(_ERROR) from exc
    agent.content = body if separator else ""
    return agent, NativeToolPolicy(agent_id, data["state"], MappingProxyType(profiles), binding)


def _servers(config: Mapping[str, Any]) -> dict:
    mcp = config.get("mcp", {}) or {}
    if not isinstance(mcp, dict):
        raise ValueError(_ERROR)
    return mcp["servers"] if isinstance(mcp.get("servers"), dict) else mcp


def _binding(agent, agent_id: str, profiles: Mapping[str, str], config: Mapping[str, Any]) -> str:
    servers = _servers(config)
    bound = {}
    for name in profiles:
        server = servers.get(name)
        if (
            not isinstance(server, dict)
            or server.get("type") != "local"
            or "url" in server
            or server.get("enabled") is False
            or server.get("disabled") is True
        ):
            raise ValueError(_ERROR)
        bound[name] = server
    agents = config.get("agent", {}) or {}
    if not isinstance(agents, dict):
        raise ValueError(_ERROR)
    options = agents.get(agent_id, {}) or {}
    if not isinstance(options, dict):
        raise ValueError(_ERROR)
    try:
        payload = json.dumps(
            {
                "agent_id": agent_id,
                "agent": {"metadata": agent.metadata, "content": agent.content},
                "profiles": dict(profiles),
                "servers": bound,
                "agent_tools": options.get("tools"),
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(_ERROR) from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_native_agent(
    agent,
    agent_id: str,
    profiles: Mapping[str, str],
    *,
    source_config: Mapping[str, Any] | None = None,
) -> str:
    """Render pending, or ready when the completed shared snapshot is supplied."""
    if NATIVE_POLICY_PREFIX in agent.content:
        raise ValueError("OpenCode native policy prefix is reserved")
    # Bind exactly the payload obtained after native frontmatter serialization.
    bare = frontmatter.loads(frontmatter.dumps(agent))
    selections = _profiles(profiles)
    data = {
        "version": 1,
        "agent_id": agent_id,
        "state": "pending" if source_config is None else "ready",
        "profiles": selections,
        "binding": (
            None if source_config is None else _binding(bare, agent_id, selections, source_config)
        ),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    if len(encoded) > MAX_ENCODED_POLICY:
        raise ValueError(_ERROR)
    bare.content = f"{NATIVE_POLICY_PREFIX}{encoded} -->\n{bare.content}"
    rendered: str = frontmatter.dumps(bare)
    return rendered


def validate_native_binding(agent, policy: NativeToolPolicy, config: Mapping[str, Any]) -> None:
    if policy.state != "ready" or policy.binding != _binding(
        agent, policy.agent_id, policy.profiles, config
    ):
        raise ValueError(_ERROR)


def apply_native_profiles(config: dict, policy: NativeToolPolicy) -> dict:
    """Rebase already-bound children in a private copy, before protected overlay."""
    result = copy.deepcopy(config)
    servers = _servers(result)
    for name, selection in policy.profiles.items():
        server = dict(servers[name])
        server["command"] = [sys.executable, "-m", CAO_MCP_SERVER_MODULE]
        server.pop("args", None)
        server[MCP_TOOL_PROFILE_CONFIG] = selection
        servers[name] = server
    return result
