"""Provider-side interaction observations and protected MCP config overlays.

Native provider screens are not question payloads. The default adapter may
emit a manual-only signal for a provider-reported wait, with an opaque digest
of the bounded visible viewport. It never copies terminal prose, credential
fields, or guessed options into the interaction ledger.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.utils.mcp_resolution import (
    CAO_MCP_SERVER_COMMAND,
    CAO_MCP_SERVER_MODULE,
)
from cli_agent_orchestrator.utils.mcp_tool_policy import (
    MCP_TOOL_FILTER_ENV,
    MCP_TOOL_PROFILE_CONFIG,
    MCP_TOOL_PROFILE_ENV,
    encode_tool_filter,
    resolve_tool_profile,
)

CAO_INTERACTION_CAPABILITY_FILE = "CAO_INTERACTION_CAPABILITY_FILE"
REQUIRED_EXPLICIT_INTERACTION_TOOLS = frozenset(
    {
        "cao_ask_user",
        "cao_wait_for_user_answers",
        "cao_acknowledge_user_answers",
    }
)
MAX_NATIVE_SCREEN_LINES = 80
MAX_NATIVE_SCREEN_LINE_CHARS = 512


@dataclass(frozen=True)
class NativeInteractionObservation:
    """A bounded provider observation; actionable answer data is opt-in only."""

    observation: str
    source_fingerprint: str | None = None
    dialog_fingerprint: str | None = None
    dialog_version: str | None = None
    dialog_kind: str = "unknown"
    prompt: str | None = None
    options: tuple[dict[str, Any], ...] = ()
    answer_capability: str = "none"
    manual_reason: str | None = None
    exact_target: tuple[str, str, str] | None = None
    observed_at: str | None = None

    @classmethod
    def none(cls) -> "NativeInteractionObservation":
        return cls(observation="none")

    @classmethod
    def manual_signal(
        cls,
        *,
        screen_lines: Sequence[str],
        terminal_id: str,
        session_name: str,
        window_name: str,
    ) -> "NativeInteractionObservation | None":
        """Create a private-content-free notice from one current viewport.

        The screen itself is never retained. Its digest is only an identity
        token used to notice when the live dialog changes while the coarse
        provider status remains WAITING_USER_ANSWER.
        """
        fingerprint = native_screen_fingerprint(screen_lines)
        if fingerprint is None:
            return None
        return cls(
            observation="manual_signal",
            source_fingerprint=fingerprint,
            dialog_fingerprint=fingerprint,
            dialog_version=fingerprint,
            dialog_kind="unknown",
            prompt=None,
            options=(),
            answer_capability="none",
            manual_reason=(
                "The provider is waiting for manual input in its terminal. "
                "CAO cannot safely identify or submit this native response."
            ),
            exact_target=(terminal_id, session_name, window_name),
            observed_at=datetime.now(timezone.utc).isoformat(),
        )


def native_screen_fingerprint(screen_lines: Sequence[str]) -> str | None:
    """Hash a bounded visible viewport without retaining terminal content."""
    lines = [
        line[:MAX_NATIVE_SCREEN_LINE_CHARS].rstrip()
        for line in list(screen_lines)[-MAX_NATIVE_SCREEN_LINES:]
        if isinstance(line, str)
    ]
    if not any(line.strip() for line in lines):
        return None
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def overlay_interaction_capability_file(
    mcp_servers: Mapping[str, Any] | None,
    capability_file: str | None,
    *,
    interaction_pull_flow_only: bool = False,
    managed_tool_profiles: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Protect the internal capability-file env on every MCP config entry.

    A profile cannot choose the capability file. The path is installed only
    on the bundled CAO MCP entry, and profile-supplied copies are removed from
    every other entry. Input mappings are copied and never mutated.
    """
    if not isinstance(mcp_servers, Mapping):
        return {}

    result: dict[str, Any] = {}
    for name, raw_config in mcp_servers.items():
        if not isinstance(name, str) or not isinstance(raw_config, Mapping):
            result[name] = raw_config
            continue
        config = dict(raw_config)
        has_tool_profile = MCP_TOOL_PROFILE_CONFIG in config
        tool_profile = config.pop(MCP_TOOL_PROFILE_CONFIG, None)
        raw_env = config.get("env")
        env = dict(raw_env) if isinstance(raw_env, Mapping) else {}
        raw_environment = config.get("environment")
        environment = dict(raw_environment) if isinstance(raw_environment, Mapping) else {}
        uses_environment_field = isinstance(config.get("command"), (list, tuple)) or isinstance(
            raw_environment, Mapping
        )
        env.pop(CAO_INTERACTION_CAPABILITY_FILE, None)
        environment.pop(CAO_INTERACTION_CAPABILITY_FILE, None)
        env.pop(_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV, None)
        environment.pop(_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV, None)
        env.pop(MCP_TOOL_PROFILE_ENV, None)
        environment.pop(MCP_TOOL_PROFILE_ENV, None)
        env.pop(MCP_TOOL_FILTER_ENV, None)
        environment.pop(MCP_TOOL_FILTER_ENV, None)
        bundled = is_bundled_cao_mcp_entry(name, config)
        if has_tool_profile and not bundled:
            raise ValueError("CAO MCP tool profile requires a canonical bundled server")
        if (
            has_tool_profile
            and isinstance(config.get("command"), str)
            and isinstance(raw_environment, Mapping)
        ):
            # Claude/Codex consume `env`, whereas OpenCode's array-command
            # format consumes `environment`. Do not silently launch a text
            # command after placing its restriction in a field it ignores.
            raise ValueError("CAO MCP tool profile requires env for a text command")
        if bundled:
            if has_tool_profile:
                resolve_tool_profile(tool_profile)
            if has_tool_profile or (isinstance(capability_file, str) and capability_file):
                # A bare profile command is only a declaration of intent. Pin
                # its actual launch target to CAO's canonical module before
                # attaching a server-private capability path; otherwise a
                # same-named executable earlier on PATH could receive it.
                if config.get("command") == CAO_MCP_SERVER_COMMAND:
                    config["command"] = sys.executable
                    config["args"] = ["-m", CAO_MCP_SERVER_MODULE]
                elif config.get("command") == [CAO_MCP_SERVER_COMMAND]:
                    config["command"] = [sys.executable, "-m", CAO_MCP_SERVER_MODULE]
                    config.pop("args", None)
                target_env = environment if uses_environment_field else env
                if has_tool_profile:
                    target_env[MCP_TOOL_PROFILE_ENV] = tool_profile
                    if (
                        isinstance(config.get("command"), (list, tuple))
                        and config.get("enabled_tools") is not None
                    ):
                        target_env[MCP_TOOL_FILTER_ENV] = encode_tool_filter(
                            config.pop("enabled_tools"), tool_profile
                        )
                if isinstance(capability_file, str) and capability_file:
                    target_env[CAO_INTERACTION_CAPABILITY_FILE] = capability_file
                if (
                    interaction_pull_flow_only
                    and capability_file
                    and not (
                        has_tool_profile
                        and tool_profile != "interaction"
                        and managed_tool_profiles is not None
                        and managed_tool_profiles.get(name) == tool_profile
                    )
                ):
                    # Only an exact bound managed selection supersedes the
                    # legacy restriction; unrelated children retain it.
                    target_env[_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV] = "1"
        if "env" in config or env:
            config["env"] = env
        if "environment" in config or environment:
            config["environment"] = environment
        result[name] = config
    return result


def is_bundled_cao_mcp_entry(server_name: str, config: Mapping[str, Any]) -> bool:
    """Recognize only the actual CAO server launch forms.

    Server names and arbitrary argument substrings are profile-controlled and
    cannot authorize the protected capability path. A bare bundled declaration
    is accepted only without additional arguments because the protected overlay
    rewrites it to the canonical module invocation. Already-resolved console
    scripts must be the actual non-symlink sibling of the current interpreter;
    arbitrary PATH entries are not trusted. The module form must be the exact
    approved interpreter invocation with no trailing arguments.
    """
    command = config.get("command")
    args = config.get("args", []) or []
    if not isinstance(args, (list, tuple)):
        return False
    if isinstance(command, (list, tuple)):
        if args:
            return False
        command = list(command)
        if command == [CAO_MCP_SERVER_COMMAND]:
            return True
        if command == [sys.executable, "-m", CAO_MCP_SERVER_MODULE]:
            return True
        script_name = CAO_MCP_SERVER_COMMAND + (".exe" if sys.platform == "win32" else "")
        sibling_script_list = Path(sys.executable).with_name(script_name)
        if sibling_script_list.is_symlink() or not sibling_script_list.is_file():
            return False
        try:
            candidate: Path | None = Path(command[0]) if len(command) == 1 else None
            return candidate is not None and candidate.resolve(
                strict=True
            ) == sibling_script_list.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return False
    if not isinstance(command, str):
        return False
    if command == CAO_MCP_SERVER_COMMAND:
        return not args

    script_name = CAO_MCP_SERVER_COMMAND + (".exe" if sys.platform == "win32" else "")
    installed_script = Path(sys.executable).with_name(script_name)
    sibling_script: Path | None = (
        installed_script
        if not installed_script.is_symlink() and installed_script.is_file()
        else None
    )
    try:
        candidate_path = Path(command)
        if sibling_script is not None and candidate_path.resolve(
            strict=True
        ) == sibling_script.resolve(strict=True):
            return True
    except (OSError, RuntimeError, ValueError):
        pass

    return command == sys.executable and list(args) == ["-m", CAO_MCP_SERVER_MODULE]


def exposes_required_interaction_tools(config: Mapping[str, Any]) -> bool:
    """Check any explicit MCP tool filters preserve the required pull flow.

    Codex's ``enabled_tools`` is an allowlist. With no allowlist the MCP server
    exposes its normal tool catalog; with a list, all three operations required
    to ask, wait, and acknowledge must remain available. A malformed explicit
    allowlist fails closed so the source capability is not advertised.
    """
    enabled_tools = config.get("enabled_tools")
    if enabled_tools is None:
        return True
    if not isinstance(enabled_tools, (list, tuple)) or any(
        not isinstance(tool, str) for tool in enabled_tools
    ):
        return False
    return REQUIRED_EXPLICIT_INTERACTION_TOOLS.issubset(set(enabled_tools))


def observe_manual_native_wait(
    status: TerminalStatus,
    screen_lines: Sequence[str],
    *,
    terminal_id: str,
    session_name: str,
    window_name: str,
) -> NativeInteractionObservation | None:
    """Default provider-owned signal; status never turns text into an answer."""
    if status != TerminalStatus.WAITING_USER_ANSWER:
        return None
    return NativeInteractionObservation.manual_signal(
        screen_lines=screen_lines,
        terminal_id=terminal_id,
        session_name=session_name,
        window_name=window_name,
    )
