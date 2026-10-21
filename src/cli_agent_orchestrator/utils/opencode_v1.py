"""Private native-v1 configuration; never translate v2 permission arrays silently."""

import asyncio
import copy
import json
import os
import re
import secrets
import signal
import sys
from pathlib import Path

import frontmatter

from cli_agent_orchestrator.utils.opencode_policy import NATIVE_POLICY_PREFIX
from cli_agent_orchestrator.utils.opencode_v2 import (
    _normalized_mcp_server_name,
    _private_write,
    _wildcard_matches,
)
from cli_agent_orchestrator.utils.path_validation import validate_path_component

_NATIVE_MCP_FIELDS = {
    "local": {"type", "command", "cwd", "environment", "enabled", "timeout"},
    "remote": {"type", "url", "headers", "oauth", "enabled", "timeout"},
}


async def _stop_auth_export(process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), 3.0)
    except asyncio.TimeoutError:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        await asyncio.wait_for(process.wait(), 3.0)


async def read_v1_active_auth(
    snapshot: Path,
    *,
    executable: str,
    selected_model: str | None,
    path_value: str,
    timeout: float,
) -> dict | None:
    """Ask the native CLI to export only the selected key from an owned copy.

    CAO never opens a vendor database driver. Empty native session stores keep
    legacy auth behavior; a nonempty current store cannot select an old key.
    """
    if (
        snapshot.is_symlink()
        or not snapshot.is_dir()
        or snapshot.stat().st_uid != os.getuid()
        or snapshot.stat().st_mode & 0o077
    ):
        raise RuntimeError("OpenCode v1 credential snapshot is not private")
    provider = None
    if isinstance(selected_model, str) and "/" in selected_model:
        candidate, model = selected_model.split("/", 1)
        if model and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", candidate):
            provider = candidate
    condition = f"integration_id = '{provider}' AND active = 1" if provider else "1 = 0"
    query = (
        "SELECT 'summary' AS kind, COUNT(*) AS total, NULL AS value FROM credential "
        "UNION ALL SELECT 'selected' AS kind, NULL AS total, "
        "CASE WHEN LENGTH(value) <= 65536 THEN value ELSE NULL END AS value "
        f"FROM (SELECT value FROM credential WHERE {condition} LIMIT 2)"
    )
    for name in ("home", "config", "data", "cache", "state", "tmp"):
        (snapshot / name).mkdir(mode=0o700)
    environment = {
        "PATH": path_value,
        "LANG": "C.UTF-8",
        "HOME": str(snapshot / "home"),
        "OPENCODE_TEST_HOME": str(snapshot / "home"),
        "XDG_CONFIG_HOME": str(snapshot / "config"),
        "XDG_DATA_HOME": str(snapshot / "data"),
        "XDG_CACHE_HOME": str(snapshot / "cache"),
        "XDG_STATE_HOME": str(snapshot / "state"),
        "TMPDIR": str(snapshot / "tmp"),
        "OPENCODE_CONFIG_DIR": str(snapshot / "config"),
        "OPENCODE_DB": str(snapshot / "opencode.db"),
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
    }
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "db",
            "--format",
            "json",
            query,
            env=environment,
            cwd=str(snapshot),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        output, error = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.CancelledError:
        raise
    except (OSError, asyncio.TimeoutError) as exc:
        raise RuntimeError("OpenCode v1 credential export failed or timed out") from exc
    finally:
        if process is not None:
            await _stop_auth_export(process)
    if process.returncode != 0:
        # This is the only recognized old-store failure, not an auth fallback
        # for a malformed, inaccessible or unsupported current store.
        if re.search(rb"\bno such table:\s*credential(?:\r?\n|$)", error):
            return None
        raise RuntimeError("OpenCode v1 credential store export is invalid")
    try:
        if len(output) > 1024 * 1024:
            raise ValueError()
        rows = json.loads(output)
        if (
            not isinstance(rows, list)
            or not 1 <= len(rows) <= 3
            or any(not isinstance(row, dict) for row in rows)
            or any(set(row) != {"kind", "total", "value"} for row in rows)
        ):
            raise ValueError()
        summary = [row for row in rows if row["kind"] == "summary"]
        selected = [row for row in rows if row["kind"] == "selected"]
        if (
            len(summary) != 1
            or len(summary) + len(selected) != len(rows)
            or type(summary[0]["total"]) is not int
            or summary[0]["total"] < len(selected)
            or summary[0]["value"] is not None
            or any(row["total"] is not None for row in selected)
        ):
            raise ValueError()
        if summary[0]["total"] == 0:
            return None
        if provider is None:
            raise RuntimeError("OpenCode v1 current credentials require an explicit model")
        if len(selected) != 1 or not isinstance(selected[0]["value"], str):
            raise ValueError()
        value = json.loads(selected[0]["value"])
        if isinstance(value, dict) and value.get("type") == "oauth":
            raise RuntimeError("OpenCode v1 active OAuth credential migration is unsupported")
        if (
            not isinstance(value, dict)
            or value.get("type") != "key"
            or not isinstance(value.get("key"), str)
            or not value["key"]
        ):
            raise ValueError()
        return {provider: {"type": "api", "key": value["key"]}}
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError(
            "OpenCode v1 selected active credential is invalid or ambiguous"
        ) from exc


def prepare_v1_mcp_readiness(directory: Path) -> dict[Path, str]:
    """Bind client discovery to fresh, owned native-v1 launch state."""
    from cli_agent_orchestrator.providers.mcp_interactions import is_bundled_cao_mcp_entry
    from cli_agent_orchestrator.utils.mcp_readiness import READY_FILE_ENV, READY_TOKEN_ENV

    if (
        directory.is_symlink()
        or not directory.is_dir()
        or directory.stat().st_uid != os.getuid()
        or directory.stat().st_mode & 0o077
    ):
        raise ValueError("private OpenCode config directory required")
    path = directory / "opencode.json"
    config = json.loads(path.read_text())
    signals: dict[Path, str] = {}
    signal_root = directory / "mcp-ready"
    for index, (name, server) in enumerate(config["mcp"].items()):
        # Even disabled/foreign children cannot retain profile-forged signals.
        for field in ("env", "environment"):
            if field in server:
                server[field].pop(READY_FILE_ENV, None)
                server[field].pop(READY_TOKEN_ENV, None)
        if server.get("enabled") is False or not is_bundled_cao_mcp_entry(name, server):
            continue
        if not signals:
            signal_root.mkdir(mode=0o700)
        ready_path = signal_root / f"{index}.json"
        token = secrets.token_hex(32)
        if server.get("command") == ["cao-mcp-server"]:
            server["command"] = [sys.executable, "-m", "cli_agent_orchestrator.mcp_server.server"]
        server.setdefault("environment", {}).update(
            {READY_FILE_ENV: str(ready_path), READY_TOKEN_ENV: token}
        )
        signals[ready_path] = token
    _private_write(path, json.dumps(config, indent=2) + "\n")
    return signals


def _native_mcp_permissions(permissions: dict, server_names) -> dict:
    result = {}
    for action, value in permissions.items():
        normalized = action
        for server_name in sorted(server_names, key=len, reverse=True):
            name = _normalized_mcp_server_name(server_name)
            if name == server_name:
                continue
            if action == server_name + "*":
                normalized = name + "_*"
                break
            if action.startswith(server_name + "_"):
                normalized = (
                    name + "_" + re.sub(r"[^A-Za-z0-9_?*-]", "_", action[len(server_name) + 1 :])
                )
                break
        if normalized in result:
            raise ValueError("OpenCode v1 MCP permission names are ambiguous")
        result[normalized] = value
    return result


def _merge_permissions(target: dict[str, object], values: object, *, tools: bool = False) -> None:
    if values is None:
        return
    if isinstance(values, str) and not tools:
        values = {"*": values}
    if not isinstance(values, dict):
        raise ValueError("OpenCode v1 permissions must use native rules")
    for name, value in values.items():
        if not isinstance(name, str) or not name:
            raise ValueError("OpenCode v1 permission action is invalid")
        if tools:
            if type(value) is not bool:
                raise ValueError("OpenCode v1 tools must be boolean")
            value = "allow" if value else "deny"
            if name in ("write", "edit", "patch"):
                name = "edit"
        elif isinstance(value, dict):
            if any(
                not isinstance(resource, str) or effect not in ("allow", "deny", "ask")
                for resource, effect in value.items()
            ):
                raise ValueError("OpenCode v1 resource permission is invalid")
        elif value not in ("allow", "deny", "ask"):
            raise ValueError("OpenCode v1 permission effect is invalid")
        # Materialize precedence in the dictionary order that native v1 evaluates.
        target.pop(name, None)
        target[name] = copy.deepcopy(value)


def write_v1_configuration(
    destination: Path,
    *,
    source_config: dict[str, object],
    selected_agent_source: tuple[str, str],
    session_id: str,
    selected_model: str | None = None,
    capability_file: str | None = None,
    interaction_pull_flow_only: bool = False,
) -> Path:
    """Freeze one selected native agent without exposing the shared agent catalog."""
    name, text = selected_agent_source
    validate_path_component(name, "OpenCode agent identity")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id) is None:
        raise ValueError("bounded OpenCode terminal identity required")
    if not isinstance(source_config, dict) or any(
        key in source_config
        for key in ("agents", "providers", "permissions", "plugins", "warming", "mode")
    ):
        raise ValueError("OpenCode v1 requires native configuration fields")
    agent = frontmatter.loads(text)
    if NATIVE_POLICY_PREFIX in agent.content or "permissions" in agent.metadata:
        raise ValueError("OpenCode v1 cannot consume unvalidated policy or v2 permissions")
    options = source_config.get("agent", {})
    if options is None:
        options = {}
    if not isinstance(options, dict) or not isinstance(options.get(name, {}), dict):
        raise ValueError("OpenCode v1 agent configuration must be an object")
    options = options.get(name, {})
    entry = {**copy.deepcopy(agent.metadata), **copy.deepcopy(options)}
    if "permissions" in entry:
        raise ValueError("OpenCode v1 requires native permission rules")
    permission: dict[str, object] = {}
    _merge_permissions(permission, agent.metadata.get("tools"), tools=True)
    _merge_permissions(permission, options.get("tools"), tools=True)
    _merge_permissions(permission, agent.metadata.get("permission"))
    _merge_permissions(permission, options.get("permission"))
    if entry.get("mode", "all") not in ("all", "primary", "subagent"):
        raise ValueError("OpenCode v1 native agent mode is invalid")
    global_permission: dict[str, object] = {}
    _merge_permissions(global_permission, source_config.get("tools"), tools=True)
    _merge_permissions(global_permission, source_config.get("permission"))
    entry.pop("tools", None)
    entry["permission"] = permission
    entry["prompt"] = agent.content
    mcp = source_config.get("mcp", {})
    if mcp is None:
        mcp = {}
    if not isinstance(mcp, dict):
        raise ValueError("OpenCode MCP configuration must be an object")
    nested = mcp.get("servers")
    is_wrapper = isinstance(nested, dict) and nested.get("type") not in ("local", "remote")
    if is_wrapper and set(mcp) != {"servers"}:
        raise ValueError("OpenCode v1 cannot preserve v2 MCP global options")
    servers = nested if is_wrapper else mcp
    if not isinstance(servers, dict):
        raise ValueError("OpenCode MCP servers must be an object")
    native_servers = {}
    normalized_names: dict[str, str] = {}
    for server_name, raw in servers.items():
        if not isinstance(server_name, str) or not isinstance(raw, dict):
            raise ValueError("OpenCode MCP server configuration must be an object")
        normalized = _normalized_mcp_server_name(server_name)
        if normalized in normalized_names and normalized_names[normalized] != server_name:
            raise ValueError("OpenCode v1 MCP server names are ambiguous")
        normalized_names[normalized] = server_name
        server = copy.deepcopy(raw)
        if "codemode" in server and server.pop("codemode") is not False:
            raise ValueError("OpenCode v1 requires direct MCP tools")
        enabled = server.get("enabled", True)
        disabled = server.pop("disabled", False)
        if type(enabled) is not bool or type(disabled) is not bool:
            raise ValueError("OpenCode MCP enable flags must be boolean")
        server["enabled"] = enabled and not disabled
        if isinstance(server.get("timeout"), dict):
            raise ValueError("OpenCode v1 requires native MCP timeout configuration")
        kind = server.get("type")
        # CAO metadata is consumed by the protected overlay below, then the
        # resulting native entry must satisfy the original strict field set.
        policy_fields = {"cao_tool_profile", "enabled_tools"} if kind == "local" else set()
        if (
            not isinstance(kind, str)
            or kind not in _NATIVE_MCP_FIELDS
            or set(server) - (_NATIVE_MCP_FIELDS[kind] | policy_fields)
        ):
            raise ValueError("OpenCode v1 requires native MCP server fields")
        if kind == "local":
            command = server.get("command")
            if (
                not isinstance(command, list)
                or not command
                or any(not isinstance(part, str) or not part for part in command)
            ):
                raise ValueError("OpenCode v1 MCP command is invalid")
            if "cwd" in server and not isinstance(server["cwd"], str):
                raise ValueError("OpenCode v1 MCP working directory is invalid")
        elif not isinstance(server.get("url"), str):
            raise ValueError("OpenCode v1 remote MCP URL is invalid")
        for field in ("environment", "headers"):
            if field in server and (
                not isinstance(server[field], dict)
                or any(
                    not isinstance(k, str) or not isinstance(v, str)
                    for k, v in server[field].items()
                )
            ):
                raise ValueError("OpenCode v1 MCP environment/headers must contain strings")
        if "timeout" in server and (type(server["timeout"]) is not int or server["timeout"] <= 0):
            raise ValueError("OpenCode v1 MCP timeout must be a positive integer")
        if "oauth" in server:
            oauth = server["oauth"]
            if oauth is not False:
                if not isinstance(oauth, dict) or set(oauth) - {
                    "clientId",
                    "clientSecret",
                    "scope",
                    "callbackPort",
                    "redirectUri",
                }:
                    raise ValueError("OpenCode v1 MCP OAuth fields are invalid")
                for key, value in oauth.items():
                    if key == "callbackPort":
                        if type(value) is not int or not 1 <= value <= 65535:
                            raise ValueError("OpenCode v1 MCP OAuth callback port is invalid")
                    elif not isinstance(value, str):
                        raise ValueError("OpenCode v1 MCP OAuth values must be strings")
        if "cao_tool_profile" in server and server.get("enabled_tools") is None:
            server.pop("enabled_tools", None)
        native_servers[server_name] = server
    from cli_agent_orchestrator.providers.mcp_interactions import (
        overlay_interaction_capability_file,
    )

    native_servers = overlay_interaction_capability_file(
        native_servers, capability_file, interaction_pull_flow_only=interaction_pull_flow_only
    )
    for server in native_servers.values():
        if set(server) - _NATIVE_MCP_FIELDS[server["type"]]:
            raise ValueError("OpenCode v1 requires native MCP server fields")
    entry["permission"] = _native_mcp_permissions(permission, native_servers)
    global_permission = _native_mcp_permissions(global_permission, native_servers)
    result = copy.deepcopy(source_config)
    result.pop("tools", None)
    if "tools" in source_config or "permission" in source_config:
        result["permission"] = global_permission
    result.update(
        {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "share": "disabled",
            "snapshot": False,
            "agent": {name: entry},
            "mcp": native_servers,
        }
    )
    if selected_model is not None:
        result["model"] = selected_model
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (
        destination.is_symlink()
        or destination.stat().st_uid != os.getuid()
        or destination.stat().st_mode & 0o077
    ):
        raise ValueError("private OpenCode config directory required")
    _private_write(destination / "opencode.json", json.dumps(result, indent=2) + "\n")
    return destination


def validate_v1_mcp_configuration(
    destination: Path,
    *,
    agent_name: str,
    server_name: str | None,
    require_interaction: bool = False,
) -> None:
    """Check native v1 global/agent grants for the exact protected surface."""
    from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
    from cli_agent_orchestrator.providers.mcp_interactions import is_bundled_cao_mcp_entry
    from cli_agent_orchestrator.utils.mcp_tool_policy import (
        INTERACTION_TOOLS,
        MCP_TOOL_FILTER_ENV,
        MCP_TOOL_PROFILE_ENV,
        resolve_tool_filter,
        resolve_tool_profile,
    )

    if server_name is None:
        return
    try:
        config = json.loads((destination / "opencode.json").read_text(encoding="utf-8"))
        server = config["mcp"][server_name]
        agent = config["agent"][agent_name]
        if (
            not isinstance(server, dict)
            or server.get("type") != "local"
            or server.get("enabled") is False
            or set(server) - _NATIVE_MCP_FIELDS["local"]
            or not is_bundled_cao_mcp_entry(server_name, server)
            or not isinstance(agent, dict)
        ):
            raise ValueError("OpenCode v1 CAO MCP server/agent is unavailable")
        environment = server.get("environment", {})
        if not isinstance(environment, dict):
            raise ValueError("OpenCode v1 MCP environment must be an object")
        profile = environment.get(MCP_TOOL_PROFILE_ENV)
        required = (
            INTERACTION_TOOLS
            if profile is None and require_interaction
            else resolve_tool_profile(profile)
        )
        if MCP_TOOL_FILTER_ENV in environment:
            required = resolve_tool_filter(environment[MCP_TOOL_FILTER_ENV], profile)
        if environment.get(_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV) == "1":
            required = required & INTERACTION_TOOLS
        if require_interaction and not INTERACTION_TOOLS.issubset(required):
            raise ValueError("OpenCode v1 interaction channel requires all three interaction tools")
        rules: list[tuple[str, object]] = []
        for permissions in (config.get("permission"), agent.get("permission")):
            normalized: dict[str, object] = {}
            _merge_permissions(normalized, permissions)
            rules.extend(normalized.items())
        prefix = _normalized_mcp_server_name(server_name) + "_"
        for tool in required:
            effective = None
            for action, value in rules:
                if not _wildcard_matches(action, prefix + tool):
                    continue
                scoped = value.items() if isinstance(value, dict) else [("*", value)]
                for resource, effect in scoped:
                    if _wildcard_matches(resource, "*"):
                        effective = effect
            if effective != "allow":
                raise ValueError(f"OpenCode v1 agent {agent_name!r} has no CAO MCP grant")
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError("OpenCode v1 generated MCP configuration is invalid") from exc
