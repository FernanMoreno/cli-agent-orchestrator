"""Translate installed CAO OpenCode agents into private per-terminal v2 config."""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
from pathlib import Path

import frontmatter

from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
from cli_agent_orchestrator.utils.mcp_tool_policy import (
    INTERACTION_TOOLS,
    MCP_TOOL_PROFILE_ENV,
    resolve_tool_profile,
)
from cli_agent_orchestrator.utils.opencode_policy import NATIVE_POLICY_PREFIX
from cli_agent_orchestrator.utils.path_validation import validate_path_component

_ACTIONS = {"bash": "shell", "task": "subagent", "write": "edit", "patch": "edit"}


def _normalized_mcp_server_name(server_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", server_name)


def _rules(permissions):
    if permissions is None:
        return []
    if not isinstance(permissions, dict):
        raise ValueError("OpenCode v1 permissions must be an explicit mapping")
    rules = []
    seen = {}
    for name, value in permissions.items():
        if not isinstance(name, str) or not name:
            raise ValueError("OpenCode v1 permission action must be a non-empty string")
        action = _ACTIONS.get(name, name)
        # OpenCode v1 also permits resource-scoped rules, for example
        # ``bash: {"git *": "allow"}``.  Preserve that scope in v2 instead
        # of rejecting an unrelated profile and preventing every v2 terminal
        # from starting.  Flat booleans/effects retain the v1 ``*`` resource.
        if isinstance(value, dict):
            scoped_values = value.items()
        else:
            scoped_values = (
                ("*", ("allow" if value else "deny") if type(value) is bool else value),
            )
        for resource, effect in scoped_values:
            if not isinstance(resource, str) or not resource:
                raise ValueError("OpenCode v1 permission resource must be a non-empty string")
            if type(effect) is bool:
                effect = "allow" if effect else "deny"
            if effect not in ("allow", "deny", "ask"):
                raise ValueError("unsupported OpenCode permission shape")
            key = (action, resource)
            if key in seen and seen[key] != effect:
                raise ValueError("conflicting permissions for one OpenCode v2 action")
            seen[key] = effect
            rule = {"action": action, "resource": resource, "effect": effect}
            if rule not in rules:
                rules.append(rule)
    return rules


def _normalize_mcp_actions(rules, server_names):
    """Translate V1 MCP tool selectors to OpenCode v2's normalized names.

    V1 grants stored by CAO use selectors such as ``cao-mcp-server*``. V2
    identifies an MCP tool as ``<server>_<tool>`` and preserves hyphens in
    both names while replacing other unsupported characters with underscores.
    """
    normalized = []
    ordered_names = sorted(server_names, key=len, reverse=True)
    for rule in rules:
        action = rule["action"]
        for server_name in ordered_names:
            normalized_server = _normalized_mcp_server_name(server_name)
            if action == server_name + "*":
                action = normalized_server + "_*"
                break
            prefix = server_name + "_"
            if action.startswith(prefix):
                tool_pattern = action[len(prefix) :]
                tool_pattern = re.sub(r"[^A-Za-z0-9_?*-]", "_", tool_pattern)
                action = normalized_server + "_" + tool_pattern
                break
        normalized.append({**rule, "action": action})
    return normalized


def validate_v2_mcp_configuration(
    destination: Path, *, agent_name: str, server_name: str | None
) -> None:
    """Fail before launch when the selected OpenCode agent cannot use CAO MCP.

    OpenCode v2 separates an MCP server declaration from the per-agent tool
    grant.  A valid server process with no grant leaves the model in a normal
    idle turn, which is indistinguishable from a provider that simply chose
    not to call a tool until the phase timeout expires.  This bounded local
    check turns that configuration error into an actionable initialization
    failure without inspecting prompts, provider output, or credentials.
    """
    if server_name is None:
        return
    if not agent_name:
        raise ValueError("OpenCode v2 CAO interaction requires a native agent profile")
    if not isinstance(server_name, str) or not server_name:
        raise ValueError("OpenCode v2 CAO MCP server name is invalid")
    path = destination / "opencode.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("OpenCode v2 generated configuration is unreadable") from None
    if not isinstance(config, dict):
        raise ValueError("OpenCode v2 generated configuration must be an object")
    mcp = config.get("mcp")
    servers = mcp.get("servers") if isinstance(mcp, dict) else None
    if not isinstance(servers, dict):
        raise ValueError("OpenCode v2 CAO MCP server declaration is missing")
    normalized_by_name: dict[str, str] = {}
    for raw_name in servers:
        if not isinstance(raw_name, str):
            continue
        normalized_name = _normalized_mcp_server_name(raw_name)
        previous = normalized_by_name.get(normalized_name)
        if previous is not None and previous != raw_name:
            raise ValueError("OpenCode v2 MCP server names are ambiguous")
        normalized_by_name[normalized_name] = raw_name
    server = servers.get(server_name)
    if not isinstance(server, dict):
        raise ValueError("OpenCode v2 CAO MCP server declaration is missing")
    if server.get("disabled") is True or server.get("enabled") is False:
        raise ValueError("OpenCode v2 CAO MCP server is disabled")
    command = server.get("command")
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part for part in command)
    ):
        raise ValueError("OpenCode v2 CAO MCP server command is invalid")
    if server.get("codemode") is not False:
        # Code Mode hides MCP tools behind ``execute``; the per-tool grants
        # below would then never expose the CAO interaction surface.
        raise ValueError("OpenCode v2 CAO MCP server must disable codemode")

    environment = server.get("environment", {})
    if not isinstance(environment, dict):
        raise ValueError("OpenCode v2 MCP environment must be an object")
    ignored_env = server.get("env", {})
    if isinstance(ignored_env, dict) and (
        MCP_TOOL_PROFILE_ENV in ignored_env
        or _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV in ignored_env
    ):
        raise ValueError("OpenCode v2 MCP policy must use environment")
    required_tools = INTERACTION_TOOLS
    if MCP_TOOL_PROFILE_ENV in environment:
        required_tools = resolve_tool_profile(environment[MCP_TOOL_PROFILE_ENV])
    if environment.get(_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV) == "1":
        required_tools = required_tools & INTERACTION_TOOLS

    agents = config.get("agents")
    agent = agents.get(agent_name) if isinstance(agents, dict) else None
    permissions = agent.get("permissions") if isinstance(agent, dict) else None
    if not isinstance(permissions, list):
        raise ValueError(f"OpenCode v2 agent {agent_name!r} has no CAO MCP grant")
    normalized_server_name = _normalized_mcp_server_name(server_name)

    # OpenCode evaluates the last matching rule per canonical tool name.
    # Every required interaction tool must resolve to allow: a partial grant
    # or a later wildcard deny leaves the model unable to complete the
    # ask/wait/acknowledge protocol and the phase only times out.
    for tool in sorted(required_tools):
        if _effective_permission(permissions, normalized_server_name + "_" + tool) != "allow":
            raise ValueError(f"OpenCode v2 agent {agent_name!r} has no CAO MCP grant")


def _wildcard_matches(pattern: str, name: str) -> bool:
    """Match OpenCode permission wildcards: ``*`` any run, ``?`` one char."""
    expression = "".join(
        ".*" if char == "*" else "." if char == "?" else re.escape(char) for char in pattern
    )
    return re.fullmatch(expression, name, flags=re.DOTALL) is not None


def _effective_permission(permissions: list, tool_name: str, *, resource: str = "*") -> str | None:
    """Resolve the v2 decision for one action/resource pair.

    OpenCode applies the last rule whose action and resource patterns match.
    CAO's direct MCP tools use ``"*"`` as their resource, so a rule scoped to
    another resource cannot grant or revoke that tool.
    """
    effective: str | None = None
    for rule in permissions:
        if not isinstance(rule, dict) or not isinstance(rule.get("action"), str):
            continue
        effect = rule.get("effect")
        if effect not in {"allow", "deny", "ask"}:
            continue
        if not _wildcard_matches(rule["action"], tool_name):
            continue
        rule_resource = rule.get("resource")
        if not isinstance(rule_resource, str) or not _wildcard_matches(rule_resource, resource):
            continue
        effective = effect
    return effective


def _private_write(path, data):
    if path.is_symlink():
        raise ValueError("private OpenCode config cannot be a symbolic link")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as writer:
        writer.write(data)


def write_v2_configuration(
    destination: Path,
    *,
    source_agents: Path,
    source_config: dict,
    session_id: str,
    source_auth: Path | None = None,
    source_credential_db: Path | None = None,
    selected_model: str | None = None,
    selected_agent_source: tuple[str, str] | None = None,
):
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id) is None:
        raise ValueError("bounded OpenCode terminal identity required")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (
        destination.is_symlink()
        or destination.stat().st_uid != os.getuid()
        or destination.stat().st_mode & 0o077
    ):
        raise ValueError("private OpenCode config directory required")
    config = {
        "$schema": "https://opencode.ai/config.json",
        "update": "disable",
        "snapshots": False,
        "agents": {},
        "mcp": {"servers": {}},
        "providers": copy.deepcopy(source_config.get("providers", {})),
    }
    # Go requires conversation affinity; no credential is stored in this config.
    go = config["providers"].setdefault("opencode-go", {})
    go.setdefault("headers", {}).update(
        {"x-opencode-session": "cao-" + session_id, "User-Agent": "opencode/2.0.18"}
    )
    if selected_model and selected_model.startswith("opencode-go/"):
        # An explicitly selected Go model need not be in v2's installed catalog.
        # Keep exactly that operator selection; never substitute a default model.
        model_id = selected_model.removeprefix("opencode-go/")
        if not model_id or any(char.isspace() for char in model_id):
            raise ValueError("invalid explicitly selected OpenCode Go model")
        go.setdefault("env", ["OPENCODE_API_KEY"])
        go.setdefault("package", "@opencode/ai/providers/openai-compatible")
        go.setdefault("settings", {}).setdefault("baseURL", "https://opencode.ai/zen/go/v1")
        go.setdefault("models", {}).setdefault(model_id, {})
    native_mcp = source_config.get("mcp", {})
    if not isinstance(native_mcp, dict):
        raise ValueError("OpenCode MCP configuration must be an object")
    from cli_agent_orchestrator.providers.mcp_interactions import is_bundled_cao_mcp_entry

    if "servers" in native_mcp:
        config["mcp"] = copy.deepcopy(native_mcp)
        mcp_servers = native_mcp.get("servers", {})
        if not isinstance(mcp_servers, dict):
            raise ValueError("OpenCode MCP servers must be an object")
        copied_servers = config["mcp"].get("servers")
        if not isinstance(copied_servers, dict):
            raise ValueError("OpenCode MCP servers must be an object")
        for name, server in copied_servers.items():
            if not isinstance(name, str) or not isinstance(server, dict):
                raise ValueError("OpenCode MCP server configuration must be an object")
            # CAO's interaction tools are granted per tool name; Code Mode
            # would route them through ``execute`` instead.
            if is_bundled_cao_mcp_entry(name, server):
                server["codemode"] = False
    else:
        mcp_servers = native_mcp
        for name, server in native_mcp.items():
            server = copy.deepcopy(server)
            # V1 exposed native MCP tools; preserve that surface and the
            # existing server_tool permission patterns instead of Code Mode.
            if not isinstance(server, dict):
                raise ValueError("OpenCode MCP server configuration must be an object")
            if is_bundled_cao_mcp_entry(name, server):
                server["codemode"] = False
            else:
                server.setdefault("codemode", False)
            enabled = server.pop("enabled", True)
            if type(enabled) is not bool:
                raise ValueError("OpenCode MCP enable flag must be boolean")
            if not enabled:
                server["disabled"] = True
            config["mcp"]["servers"][name] = server
    mcp_server_names = set(mcp_servers) if isinstance(mcp_servers, dict) else set()
    agent_paths = {path.stem: path for path in source_agents.glob("*.md")}
    if selected_agent_source is not None:
        selected_name, _ = selected_agent_source
        validate_path_component(selected_name, "OpenCode agent identity")
        agent_paths.setdefault(selected_name, source_agents / f"{selected_name}.md")
    for name, path in agent_paths.items():
        if selected_agent_source is not None and name == selected_agent_source[0]:
            text = selected_agent_source[1]
        else:
            text = path.read_text(encoding="utf-8")
        agent = frontmatter.loads(text)
        if NATIVE_POLICY_PREFIX in agent.content:
            if selected_agent_source is not None and name == selected_agent_source[0]:
                raise ValueError("OpenCode native policy must be validated before translation")
            # Another managed agent requires its own launch policy. It cannot
            # inherit this terminal's child surface through an agent switch.
            continue
        options = source_config.get("agent", {}).get(path.stem, {})
        rules = _rules(agent.metadata.get("permission"))
        # V1's global tools gate is separate from agent permission rules.
        # Apply it after base agent permissions and before explicit grants.
        rules.extend(_rules(source_config.get("tools")))
        rules.extend(_rules(options.get("tools")))
        rules = _normalize_mcp_actions(rules, mcp_server_names)
        config["agents"][path.stem] = {
            "description": agent.metadata.get("description", path.stem),
            "mode": agent.metadata.get("mode", "all"),
            "system": agent.content,
            "permissions": rules,
        }
        if agent.metadata.get("model"):
            config["agents"][path.stem]["model"] = agent.metadata["model"]
    _private_write(destination / "opencode.json", json.dumps(config, indent=2) + "\n")
    # OpenCode v2 reads the operator credential store from the private data
    # snapshot prepared by the provider.  Only older installations without
    # that store need the legacy JSON fallback.
    environment = ""
    if source_credential_db is None or not (
        source_credential_db.is_file() or source_credential_db.is_symlink()
    ):
        if source_auth is not None and source_auth.is_file() and not source_auth.is_symlink():
            try:
                auth = json.loads(source_auth.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                auth = {}
            provider = auth.get("opencode-go") if isinstance(auth, dict) else None
            if isinstance(provider, dict) and provider.get("type") in {"api", "key"}:
                key = provider.get("key")
                if isinstance(key, str) and key:
                    environment = "export OPENCODE_API_KEY=" + shlex.quote(key) + "\n"
    _private_write(destination / "provider.env", environment)
    return destination
