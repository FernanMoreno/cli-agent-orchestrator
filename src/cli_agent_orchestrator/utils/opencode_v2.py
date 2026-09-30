"""Translate installed CAO OpenCode agents into private per-terminal v2 config."""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
from pathlib import Path

import frontmatter

_ACTIONS = {"bash": "shell", "task": "subagent", "write": "edit", "patch": "edit"}


def _rules(permissions):
    if permissions is None:
        return []
    if not isinstance(permissions, dict):
        raise ValueError("OpenCode v1 permissions must be an explicit mapping")
    rules = []
    seen = {}
    for name, value in permissions.items():
        action = _ACTIONS.get(name, name)
        effect = ("allow" if value else "deny") if type(value) is bool else value
        if effect not in ("allow", "deny", "ask"):
            raise ValueError("unsupported OpenCode permission shape")
        if action in seen and seen[action] != effect:
            raise ValueError("conflicting permissions for one OpenCode v2 action")
        seen[action] = effect
        rule = {"action": action, "resource": "*", "effect": effect}
        if rule not in rules:
            rules.append(rule)
    return rules


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
    selected_model: str | None = None,
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
    if "servers" in native_mcp:
        config["mcp"] = copy.deepcopy(native_mcp)
    else:
        for name, server in native_mcp.items():
            server = copy.deepcopy(server)
            # V1 exposed native MCP tools; preserve that surface and the
            # existing server_tool permission patterns instead of Code Mode.
            server.setdefault("codemode", False)
            enabled = server.pop("enabled", True)
            if type(enabled) is not bool:
                raise ValueError("OpenCode MCP enable flag must be boolean")
            if not enabled:
                server["disabled"] = True
            config["mcp"]["servers"][name] = server
    for path in source_agents.glob("*.md"):
        agent = frontmatter.loads(path.read_text(encoding="utf-8"))
        options = source_config.get("agent", {}).get(path.stem, {})
        rules = _rules(agent.metadata.get("permission"))
        # V1's global tools gate is separate from agent permission rules.
        # Apply it after base agent permissions and before explicit grants.
        rules.extend(_rules(source_config.get("tools")))
        rules.extend(_rules(options.get("tools")))
        config["agents"][path.stem] = {
            "description": agent.metadata.get("description", path.stem),
            "mode": agent.metadata.get("mode", "all"),
            "system": agent.content,
            "permissions": rules,
        }
        if agent.metadata.get("model"):
            config["agents"][path.stem]["model"] = agent.metadata["model"]
    _private_write(destination / "opencode.json", json.dumps(config, indent=2) + "\n")
    # V2's SQLite migration does not import Go records in every installed build.
    # Transport the existing operator Go login via a private env file; never put
    # its value on the terminal command line or in an emitted launch command.
    environment = ""
    if source_auth is not None and source_auth.is_file():
        auth = json.loads(source_auth.read_text(encoding="utf-8")).get("opencode-go", {})
        if auth.get("type") == "api" and isinstance(auth.get("key"), str) and auth["key"]:
            environment = "export OPENCODE_API_KEY=" + shlex.quote(auth["key"]) + "\n"
    _private_write(destination / "provider.env", environment)
    return destination
