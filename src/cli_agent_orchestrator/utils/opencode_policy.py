"""Installation-consistency envelopes for CAO-managed native OpenCode agents.

These digests bind configuration, not operator authenticity. Enforcement is
provided only by CAO's validated private native launch, never by direct CLI use.
"""

import base64
import copy
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

import frontmatter

from cli_agent_orchestrator.utils.mcp_resolution import CAO_MCP_SERVER_MODULE
from cli_agent_orchestrator.utils.mcp_tool_policy import (
    MCP_TOOL_PROFILE_CONFIG,
    resolve_tool_filter,
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
    filters: Mapping[str, frozenset[str]] = field(default_factory=lambda: MappingProxyType({}))
    format_version: int = 1


@dataclass(frozen=True)
class NativeAgentSnapshot:
    agent_id: str
    text: str
    policy: NativeToolPolicy | None


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


def _filters(value: Any, profiles: Mapping[str, str]) -> dict[str, frozenset[str]]:
    if not isinstance(value, Mapping):
        raise ValueError(_ERROR)
    result = {}
    for name, names in value.items():
        if name not in profiles or not isinstance(names, list):
            raise ValueError(_ERROR)
        try:
            result[name] = resolve_tool_filter(json.dumps(names), profiles[name])
        except (ValueError, TypeError):
            raise ValueError(_ERROR) from None
    return result


def _filter_payload(filters: Mapping[str, frozenset[str]]) -> dict[str, list[str]]:
    return {name: sorted(names) for name, names in filters.items()}


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
        if not isinstance(data, dict) or type(data.get("version")) is not int:
            raise ValueError(_ERROR)
        version = data["version"]
        if version not in (1, 2, 3) or set(data) != (
            {"version", "agent_id", "state", "profiles", "binding"}
            | ({"filters"} if version in (2, 3) else set())
        ):
            raise ValueError(_ERROR)
        if data["agent_id"] != agent_id or data["state"] not in ("pending", "ready"):
            raise ValueError(_ERROR)
        profiles = _profiles(data["profiles"])
        filters = _filters(data["filters"], profiles) if version in (2, 3) else {}
        if version == 2 and not filters:
            raise ValueError(_ERROR)
        binding = data["binding"]
        if data["state"] == "pending":
            if binding is not None:
                raise ValueError(_ERROR)
        elif not isinstance(binding, str) or re.fullmatch(r"[0-9a-f]{64}", binding) is None:
            raise ValueError(_ERROR)
    except (ValueError, TypeError, UnicodeError, KeyError) as exc:
        raise ValueError(_ERROR) from exc
    agent.content = body if separator else ""
    return agent, NativeToolPolicy(
        agent_id,
        data["state"],
        MappingProxyType(profiles),
        binding,
        MappingProxyType(filters),
        format_version=version,
    )


def _servers(config: Mapping[str, Any]) -> dict:
    mcp = config.get("mcp", {}) or {}
    if not isinstance(mcp, dict):
        raise ValueError(_ERROR)
    return mcp["servers"] if isinstance(mcp.get("servers"), dict) else mcp


def _binding(
    agent,
    agent_id: str,
    profiles: Mapping[str, str],
    config: Mapping[str, Any],
    filters: Mapping[str, frozenset[str]] | None = None,
    *,
    preserve_permission_order: bool = False,
) -> str:
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
                **({"filters": _filter_payload(filters)} if filters else {}),
                **(
                    {
                        "permission_order": {
                            "native_permission": _ordered_policy_value(
                                agent.metadata.get("permission")
                            ),
                            "native_tools": _ordered_policy_value(agent.metadata.get("tools")),
                            "global_permission": _ordered_policy_value(config.get("permission")),
                            "global_tools": _ordered_policy_value(config.get("tools")),
                            "agent_permission": _ordered_policy_value(options.get("permission")),
                            "agent_tools": _ordered_policy_value(options.get("tools")),
                        }
                    }
                    if preserve_permission_order
                    else {}
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(_ERROR) from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ordered_policy_value(value: Any) -> Any:
    """Encode mapping order in arrays before canonical JSON sorts object keys."""
    if isinstance(value, Mapping):
        return {"mapping": [[name, _ordered_policy_value(item)] for name, item in value.items()]}
    if isinstance(value, list):
        return {"sequence": [_ordered_policy_value(item) for item in value]}
    return value


def _canonical_mapping_order(value: Any) -> bool:
    if isinstance(value, Mapping):
        try:
            if list(value) != sorted(value):
                return False
        except TypeError:
            return False
        return all(_canonical_mapping_order(item) for item in value.values())
    if isinstance(value, list):
        return all(_canonical_mapping_order(item) for item in value)
    return True


def _ambiguous_legacy_tools(value: Any) -> bool:
    """Old hashes cannot attest mixed wildcard precedence in shared tool gates."""
    if value is None:
        return False
    if not isinstance(value, Mapping):
        return True
    effects = set()
    wildcard = False
    for name, effect in value.items():
        if not isinstance(name, str):
            return True
        wildcard |= "*" in name or "?" in name
        if type(effect) is bool:
            effect = "allow" if effect else "deny"
        if not isinstance(effect, str) or effect not in ("allow", "deny", "ask"):
            return True
        effects.add(effect)
    return wildcard and len(effects) > 1


def render_native_agent(
    agent,
    agent_id: str,
    profiles: Mapping[str, str],
    *,
    source_config: Mapping[str, Any] | None = None,
    filters: Mapping[str, list[str]] | None = None,
    preserve_permission_order: bool = False,
) -> str:
    """Render pending, or ready when the completed shared snapshot is supplied."""
    if NATIVE_POLICY_PREFIX in agent.content:
        raise ValueError("OpenCode native policy prefix is reserved")
    if type(preserve_permission_order) is not bool:
        raise ValueError(_ERROR)
    # Bind exactly the payload obtained after native frontmatter serialization.
    bare = frontmatter.loads(frontmatter.dumps(agent, sort_keys=not preserve_permission_order))
    selections = _profiles(profiles)
    selected_filters = _filters(filters if filters is not None else {}, selections)
    data: dict[str, Any] = {
        "version": 3 if preserve_permission_order else (2 if selected_filters else 1),
        "agent_id": agent_id,
        "state": "pending" if source_config is None else "ready",
        "profiles": selections,
        "binding": (
            None
            if source_config is None
            else _binding(
                bare,
                agent_id,
                selections,
                source_config,
                selected_filters,
                preserve_permission_order=preserve_permission_order,
            )
        ),
    }
    if selected_filters or preserve_permission_order:
        data["filters"] = _filter_payload(selected_filters)
    encoded = base64.urlsafe_b64encode(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    if len(encoded) > MAX_ENCODED_POLICY:
        raise ValueError(_ERROR)
    bare.content = f"{NATIVE_POLICY_PREFIX}{encoded} -->\n{bare.content}"
    rendered: str = frontmatter.dumps(bare, sort_keys=not preserve_permission_order)
    return rendered


def validate_native_binding(agent, policy: NativeToolPolicy, config: Mapping[str, Any]) -> None:
    if type(policy.format_version) is not int or policy.format_version not in (1, 2, 3):
        raise ValueError(_ERROR)
    if policy.format_version < 3:
        # The legacy renderer sorted native metadata; a different order cannot
        # be a valid legacy render. Shared mixed wildcard gates were never
        # order-bound and require reinstall instead of guessing prior intent.
        agents = config.get("agent", {}) or {}
        if not isinstance(agents, Mapping):
            raise ValueError(_ERROR)
        options = agents.get(policy.agent_id, {}) or {}
        if (
            any(
                not _canonical_mapping_order(agent.metadata.get(name))
                for name in ("permission", "tools")
            )
            or _ambiguous_legacy_tools(config.get("tools"))
            or not isinstance(options, Mapping)
            or _ambiguous_legacy_tools(options.get("tools"))
        ):
            raise ValueError(_ERROR)
    if policy.state != "ready" or policy.binding != _binding(
        agent,
        policy.agent_id,
        policy.profiles,
        config,
        policy.filters,
        preserve_permission_order=policy.format_version == 3,
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
        if name in policy.filters:
            server["enabled_tools"] = sorted(policy.filters[name])
        servers[name] = server
    return result
