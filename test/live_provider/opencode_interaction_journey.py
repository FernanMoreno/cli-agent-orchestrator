"""Opt-in OpenCode v2 interaction journey through CAO's production mini path.

This private acceptance candidate starts one disposable CAO interaction API and
PostgreSQL store, then creates a real terminal through
`terminal_service.create_terminal()`. That production call creates the session,
installs the source capability, and awaits `OpenCodeCliProvider.initialize()`;
the harness delivers its task through the normal `terminal_service.send_input()`
path. It answers two fixed questions through the public CAO HTTP route and
checks the persisted acknowledgement state.

No live model request runs unless both `--run-model` and an explicit `--model`
are supplied. Provider output, prompts, answers, auth material, and raw errors
stay in the owner-only temporary run directory. The directory is removed after
successful cleanup and preserved with a failure receipt if an owned process or
resource cannot be stopped.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from test.live_provider import interaction_cli_journey as common
from test.live_provider import opencode_http_tool_offer_observer, opencode_mcp_observer
from test.live_provider import opencode_model_catalog as model_catalog
from typing import Any

RUN_BUDGET_SECONDS = 75.0
NO_TOOL_OBSERVATION_GRACE_SECONDS = 5.0
NATIVE_MCP_TIMEOUT = {"startup": 60_000, "catalog": 60_000, "execution": 65_000}
OPENCODE_INTERACTION_TOOL_NAMES = (
    "cao-mcp-server_cao_ask_user",
    "cao-mcp-server_cao_wait_for_user_answers",
    "cao-mcp-server_cao_acknowledge_user_answers",
)
_SCOPE_ATTESTATION_KEYS = (
    "run_root_private",
    "cao_home_is_run_child",
    "agent_store_is_cao_child",
    "config_is_run_private",
    "bindings_active",
    "bindings_restored",
)
_PRIVATE_CONSTANT_PATH_NAMES = (
    "CAO_ENV_FILE",
    "DB_DIR",
    "LOCK_DIR",
    "AGENT_CONTEXT_DIR",
    "SKILLS_DIR",
    "AGENT_PLUGINS_DIR",
    "AGENT_PLUGIN_DATA_DIR",
    "GRAPH_EXPORT_ROOT_DEFAULT",
    "DATABASE_FILE",
    "LOCAL_PEER_INSTANCE_ID_FILE",
    "LOCAL_PEER_PRIVATE_KEY_FILE",
    "MEMORY_BASE_DIR",
    "WORKFLOW_SPEC_DIR",
    "WORKFLOW_SCRIPT_SCRATCH_DIR",
)
_OPENCODE_EVENT_TYPES = frozenset(
    {
        "step_start",
        "step_finish",
        "text",
        "tool",
        "tool_use",
        "tool_result",
        "reasoning",
        "error",
        "session.error",
    }
)
_OPENCODE_ITEM_TYPES = frozenset(
    {"step-start", "step-finish", "text", "tool", "reasoning", "error"}
)
_OPENCODE_ERROR_NAME_CATEGORIES = {
    "ProviderAuthError": "provider_auth_rejected",
    "GatewayAuthenticationError": "provider_auth_rejected",
    "NeedsAuthError": "provider_auth_required",
    "ProviderModelNotFoundError": "provider_model_unavailable",
    "GatewayModelNotFoundError": "provider_model_unavailable",
    "UnresolvedProviderVariablesError": "provider_configuration_missing",
    "ConfigError": "provider_configuration_rejected",
    "InvalidConfigurationError": "provider_configuration_rejected",
    "ContentFilterError": "provider_refusal",
    "AgentNotFoundError": "agent_unavailable",
    "APIError": "provider_api_error",
    "APIConnectionError": "provider_connection_error",
    "APIConnectionTimeoutError": "provider_connection_timeout",
}
_OPENCODE_FAILURE_CATEGORIES = frozenset(_OPENCODE_ERROR_NAME_CATEGORIES.values()) | frozenset(
    {
        "tool_unavailable",
        "no_tools_available",
        "tool_calling_unavailable",
        "tool_permission_denied",
        "provider_rate_limited",
    }
)
_OPENCODE_ERROR_MESSAGE_MARKERS = {
    "tool_unavailable": (
        "unknown tool",
        "tool not found",
        "no such tool",
        "tool is unavailable",
    ),
    "no_tools_available": (
        "no tools available",
        "no enabled tools",
        "tool list is empty",
    ),
    "tool_calling_unavailable": (
        "tool calling is not supported",
        "tool use is not supported",
        "does not support tool calls",
        "does not support tool use",
    ),
    "tool_permission_denied": ("tool execution denied", "permission denied for tool"),
    "provider_rate_limited": ("rate limit", "too many requests", "status code 429"),
    "provider_refusal": ("content_filter", "content filter", "safety refusal"),
}
_USAGE_FIELDS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    }
)
_ISOLATED_MCP_ENV_NAMES = frozenset(
    {
        "CAO_API_HOST",
        "CAO_API_PORT",
        "CAO_AUTH_MODE",
        "CAO_DATABASE_CONFIG_FILE",
        "CAO_INTERACTION_CAPABILITY_FILE",
        "CAO_INTERNAL_MCP_TOOL_PROFILE",
        "CAO_INTERNAL_MCP_ENABLED_TOOLS",
        "CAO_INTERNAL_INTERACTION_PULL_FLOW_ONLY",
        "CAO_HOME_DIR",
        "CAO_INTERACTION_CAPABILITY_FILE",
        "CAO_MCP_RESTART_BARRIER_DIR",
        "CAO_OPENCODE_MCP_OBSERVER",
        "CAO_OPENCODE_MCP_OBSERVER_NONCE",
        "CAO_OPENCODE_MCP_OBSERVER_PATH",
        "CAO_OPENCODE_MCP_SOURCE_ROOT",
        "CAO_TERMINAL_ID",
        "HOME",
        "LANG",
        "PATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONPATH",
        "TERM",
        "TMPDIR",
        "XDG_DATA_HOME",
    }
)


@dataclass(frozen=True, order=True)
class _ProcessIdentity:
    pid: int
    uid: int
    start_time: int
    process_group: int
    session_id: int


@dataclass(frozen=True, order=True)
class _TmuxSocketIdentity:
    device: int
    inode: int
    uid: int
    mode: int


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise RuntimeError("private_directory_required")
    return path


def _private_scope_is_exact(
    root: Path,
    cao_home: Path,
    local_agent_store: Path,
    *,
    bound_cao_home: Path,
    bound_local_agent_store: Path,
    bound_profile_store: Path,
    environment: dict[str, str],
) -> bool:
    """Prove the run-scoped CAO bindings without retaining process globals.

    The live runner may inspect a receipt after this journey has restored the
    imported modules.  Checking those restored globals is therefore a race in
    the harness, not evidence about the provider.  This predicate runs while
    the private bindings are active and returns only a boolean-safe fact.
    """
    try:
        expected = {
            "CAO_HOME_DIR": str(cao_home),
            "CAO_DATABASE_CONFIG_FILE": str(cao_home / "database-config.json"),
        }
        if any(environment.get(name) != value for name, value in expected.items()):
            return False
        if (
            Path(bound_cao_home) != cao_home
            or Path(bound_local_agent_store) != local_agent_store
            or Path(bound_profile_store) != local_agent_store
        ):
            return False
        for path, expected_parent in (
            (root, None),
            (cao_home, root),
            (local_agent_store, cao_home),
        ):
            metadata = path.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
            ):
                return False
            if expected_parent is not None and path.parent != expected_parent:
                return False
        return True
    except (OSError, TypeError, ValueError):
        return False


def _private_scope_attestation(
    root: Path,
    cao_home: Path,
    local_agent_store: Path,
    config_dir: Path,
    *,
    bound_cao_home: Path,
    bound_local_agent_store: Path,
    bound_profile_store: Path,
    environment: dict[str, str],
    bound_private_paths: dict[str, Path] | None = None,
) -> dict[str, bool]:
    """Return a content-free, closed set of run-scope facts."""

    def private_directory(path: Path, *, parent: Path | None = None) -> bool:
        try:
            metadata = path.lstat()
            return (
                stat.S_ISDIR(metadata.st_mode)
                and not stat.S_ISLNK(metadata.st_mode)
                and metadata.st_uid == os.getuid()
                and not metadata.st_mode & 0o077
                and (parent is None or path.parent == parent)
            )
        except OSError:
            return False

    active_bindings = _private_scope_is_exact(
        root,
        cao_home,
        local_agent_store,
        bound_cao_home=bound_cao_home,
        bound_local_agent_store=bound_local_agent_store,
        bound_profile_store=bound_profile_store,
        environment=environment,
    )
    private_paths_bound = True
    if bound_private_paths is not None:
        try:
            private_paths_bound = all(
                path == cao_home or cao_home in path.parents
                for path in bound_private_paths.values()
            )
        except (TypeError, ValueError):
            private_paths_bound = False
    return {
        "run_root_private": private_directory(root),
        "cao_home_is_run_child": private_directory(cao_home, parent=root)
        and cao_home.name == "cao",
        "agent_store_is_cao_child": private_directory(local_agent_store, parent=cao_home)
        and local_agent_store.name == "agent-store",
        "config_is_run_private": private_directory(config_dir, parent=root)
        and config_dir.name == "opencode-config",
        "bindings_active": active_bindings and private_paths_bound,
        "bindings_restored": False,
    }


def _scope_attestation_is_valid(receipt: dict[str, Any], provider: str) -> bool:
    """Validate the closed scope contract used by an external live runner."""
    return common._scope_attestation_is_valid(receipt, provider)


def _exact_interaction_permissions(permissions: Any) -> bool:
    """Require exactly the deny-all baseline and the three direct MCP grants."""
    expected = [
        {"action": "*", "resource": "*", "effect": "deny"},
        *[
            {"action": name, "resource": "*", "effect": "allow"}
            for name in OPENCODE_INTERACTION_TOOL_NAMES
        ],
    ]
    return permissions == expected


def _private_opencode_root() -> Path:
    """Create a short owner-only run root so the private tmux socket can bind."""
    parent = Path("/tmp")
    parent_info = parent.lstat()
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent.is_symlink()
        or parent_info.st_uid != 0
        or not parent_info.st_mode & stat.S_ISVTX
    ):
        raise RuntimeError("unsafe_private_temp_parent")
    root = Path(tempfile.mkdtemp(prefix="co-", dir=parent))
    root_info = root.lstat()
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or root.is_symlink()
        or root_info.st_uid != os.getuid()
        or root_info.st_mode & 0o077
    ):
        raise RuntimeError("private_directory_required")
    root_identity = (
        root_info.st_dev,
        root_info.st_ino,
        root_info.st_uid,
        root_info.st_gid,
        root_info.st_mode,
    )
    socket_path = root / "tmux-tmp" / f"tmux-{os.getuid()}" / "default"
    if len(os.fsencode(socket_path.absolute())) >= 108:
        current = root.lstat()
        current_identity = (
            current.st_dev,
            current.st_ino,
            current.st_uid,
            current.st_gid,
            current.st_mode,
        )
        if (
            stat.S_ISDIR(current.st_mode)
            and not root.is_symlink()
            and current_identity == root_identity
        ):
            shutil.rmtree(root)
        raise RuntimeError("private_tmux_socket_path_too_long")
    return root


def _source_auth_path(*, original_home: Path, environment: dict[str, str]) -> Path:
    profile_home = environment.get("CAO_PRIVATE_PROFILE_HOME", "").strip()
    try:
        staged_profile = (
            bool(profile_home) and Path(profile_home).resolve() == original_home.resolve()
        )
    except OSError:
        staged_profile = False
    data_home_value = environment.get("XDG_DATA_HOME", "").strip()
    if staged_profile or not data_home_value:
        data_home = original_home / ".local/share"
    else:
        data_home = Path(data_home_value).expanduser()
    return data_home / "opencode" / "auth.json"


def _source_credential_db_path(*, original_home: Path, environment: dict[str, str]) -> Path:
    """Return the operator OpenCode v2 credential database without mutating it."""
    profile_home = environment.get("CAO_PRIVATE_PROFILE_HOME", "").strip()
    try:
        staged_profile = (
            bool(profile_home) and Path(profile_home).resolve() == original_home.resolve()
        )
    except OSError:
        staged_profile = False
    data_home_value = environment.get("XDG_DATA_HOME", "").strip()
    if staged_profile or not data_home_value:
        data_home = original_home / ".local/share"
    else:
        data_home = Path(data_home_value).expanduser()
    return data_home / "opencode" / "opencode.db"


def _source_stat(path: Path) -> tuple[int, int, int, int, int, int, int, int]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise RuntimeError("private_auth_source_required")
        identity = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            metadata.st_mode,
        )
        path_metadata = path.lstat()
        path_identity = (
            path_metadata.st_dev,
            path_metadata.st_ino,
            path_metadata.st_uid,
            path_metadata.st_gid,
            path_metadata.st_size,
            path_metadata.st_mtime_ns,
            path_metadata.st_ctime_ns,
            path_metadata.st_mode,
        )
        if path_identity != identity:
            raise RuntimeError("private_auth_source_changed")
        return identity
    finally:
        os.close(descriptor)


def _credential_state_stat(path: Path) -> tuple[int, int, int, int, int, int, int, int] | None:
    """Return owner/identity metadata for a regular provider state file."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("private_credential_state_required") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("private_credential_state_required")
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            metadata.st_mode,
        )
    finally:
        os.close(descriptor)


def _stage_auth_file(
    source: Path, destination: Path
) -> tuple[int, int, int, int, int, int, int, int]:
    """Copy the existing login into this run's private XDG tree without mutation."""
    before = _source_stat(source)
    _private_directory(destination.parent.parent)
    _private_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise RuntimeError("private_auth_destination_not_empty")
    source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(source_descriptor)
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_uid,
            opened.st_gid,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
            opened.st_mode,
        )
        if opened_identity != before:
            raise RuntimeError("private_auth_source_changed")
        with os.fdopen(source_descriptor, "rb") as reader:
            source_descriptor = -1
            descriptor = os.open(
                destination,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb") as writer:
                    shutil.copyfileobj(reader, writer)
                    writer.flush()
                    os.fsync(writer.fileno())
            except BaseException:
                try:
                    destination.unlink()
                except OSError:
                    pass
                raise
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
    os.chmod(destination, 0o600)
    after = _source_stat(source)
    if before != after:
        destination.unlink(missing_ok=True)
        raise RuntimeError("private_auth_source_changed")
    return before


def _stage_private_state_file(source: Path, destination: Path) -> bool:
    """Copy a regular provider state file into the owner-only run directory."""
    try:
        source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RuntimeError("OpenCode credential state cannot be staged safely") from exc
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        source_metadata = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_metadata.st_mode):
            raise RuntimeError("OpenCode credential state must be a regular file")
        destination_descriptor = os.open(
            destination,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(source_descriptor, "rb") as reader:
                source_descriptor = -1
                with os.fdopen(destination_descriptor, "wb") as writer:
                    destination_descriptor = -1
                    shutil.copyfileobj(reader, writer)
                    writer.flush()
                    os.fsync(writer.fileno())
        finally:
            if destination_descriptor >= 0:
                os.close(destination_descriptor)
        os.chmod(destination, 0o600)
    except BaseException:
        try:
            destination.unlink()
        except OSError:
            pass
        raise
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
    return True


def _initialize_private_git_repository(workdir: Path) -> None:
    """Create a real, local-only Git repository without touching user config."""
    _private_directory(workdir)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workdir.parent / "private-home"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "CAO live interaction test",
        "GIT_AUTHOR_EMAIL": "cao-live-test@example.invalid",
        "GIT_COMMITTER_NAME": "CAO live interaction test",
        "GIT_COMMITTER_EMAIL": "cao-live-test@example.invalid",
    }
    _private_directory(Path(env["HOME"]))
    commands = (
        ["git", "init", "--quiet", "--initial-branch=main", str(workdir)],
        [
            "git",
            "-C",
            str(workdir),
            "-c",
            "commit.gpgsign=false",
            "config",
            "user.name",
            env["GIT_AUTHOR_NAME"],
        ],
        [
            "git",
            "-C",
            str(workdir),
            "-c",
            "commit.gpgsign=false",
            "config",
            "user.email",
            env["GIT_AUTHOR_EMAIL"],
        ],
    )
    for command in commands:
        subprocess.run(command, check=True, capture_output=True, timeout=8, env=env)
    marker = workdir / "README.md"
    marker.write_text("Private CAO provider acceptance repository.\n", encoding="utf-8")
    marker.chmod(0o600)
    subprocess.run(
        ["git", "-C", str(workdir), "add", "README.md"],
        check=True,
        capture_output=True,
        timeout=8,
        env=env,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(workdir),
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "private acceptance fixture",
        ],
        check=True,
        capture_output=True,
        timeout=8,
        env=env,
    )
    if not (workdir / ".git").is_dir():
        raise RuntimeError("real_git_repository_required")


def _agent_source(agent_name: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agent_name) is None:
        raise ValueError("bounded_agent_name_required")
    permissions = "".join(
        f"  {name}: true\n"
        for name in (
            "cao-mcp-server_cao_ask_user",
            "cao-mcp-server_cao_wait_for_user_answers",
            "cao-mcp-server_cao_acknowledge_user_answers",
        )
    )
    tool_names = ", ".join(OPENCODE_INTERACTION_TOOL_NAMES)
    return (
        "---\n"
        "description: bounded CAO live interaction acceptance\n"
        "mode: primary\n"
        "permission:\n"
        '  "*": false\n'
        f"{permissions}"
        "---\n"
        f"Use only these three explicitly granted CAO interaction MCP tools: {tool_names}. "
        "Ask the two questions from the task, wait for the human's answers, acknowledge "
        "the exact returned receipts, then stop. Do not call any other tool.\n"
    )


def _version_text(output: str) -> str:
    for line in output.splitlines():
        clean = line.strip()
        if re.fullmatch(r"opencode v2\.[0-9]+(?:\.[0-9]+)?(?:[-+][A-Za-z0-9._-]+)?", clean):
            return clean[:100]
    return "unknown"


def _opencode_prompt(operation_key: str) -> str:
    """Use the exact three tool names exposed by the generated v2 agent."""
    prompt = common._prompt(operation_key)
    # Rewrite the bare names before replacing the generic seven-tool sentence.
    # The exposed names contain those bare names as suffixes; doing this in the
    # opposite order would prefix them a second time and make the model call
    # tools that are not present in the MCP catalog.
    for short_name, exposed_name in zip(
        common.INTERACTION_TOOLS[:3], OPENCODE_INTERACTION_TOOL_NAMES
    ):
        prompt = prompt.replace(short_name, exposed_name)
    prompt = prompt.replace(
        "The only tools you may use are the seven CAO interaction MCP tools exposed to you. "
        "Do not call any other tool or perform any external action.",
        "The only tools you may use are these three exact OpenCode tools: "
        + ", ".join(OPENCODE_INTERACTION_TOOL_NAMES)
        + ". Do not call any other tool or perform any external action.",
    )
    return prompt


def _validate_model_id(model: str | None) -> str:
    if (
        not isinstance(model, str)
        or model != common.OPENCODE_INTERACTION_MODEL
        or re.fullmatch(r"[A-Za-z0-9_.-]{1,80}/[A-Za-z0-9_./#-]{1,120}", model) is None
    ):
        raise ValueError("explicit_provider_model_required")
    return model


def _headless_command(executable: str, model: str, agent_name: str, prompt: str) -> list[str]:
    selected_model = _validate_model_id(model)
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agent_name) is None:
        raise ValueError("bounded_agent_name_required")
    return [
        executable,
        "run",
        "--standalone",
        "--model",
        selected_model,
        "--agent",
        agent_name,
        "--format",
        "json",
        prompt,
    ]


async def _create_and_send_native_terminal(
    terminal_service: Any,
    *,
    terminal_id: str,
    profile_name: str,
    session_name: str,
    working_directory: Path,
    model: str,
    creation_principal: Any,
    prompt: str,
    on_created: Any,
    on_phase: Any | None = None,
) -> tuple[Any, int]:
    """Create through CAO's initialized provider path, then deliver one turn."""
    original_generator = terminal_service.generate_terminal_id
    terminal_service.generate_terminal_id = lambda: terminal_id
    try:
        if on_phase is not None:
            on_phase("native_create")
        terminal = await terminal_service.create_terminal(
            provider="opencode_cli",
            agent_profile=profile_name,
            session_name=session_name,
            new_session=True,
            working_directory=str(working_directory),
            allowed_tools=["@cao-mcp-server"],
            model=model,
            defer_init=False,
            creation_principal=creation_principal,
        )
        on_created(terminal)
        if on_phase is not None:
            on_phase("native_send_input")
        sequence = await asyncio.to_thread(terminal_service.send_input, terminal.id, prompt)
        if type(sequence) is not int or sequence < 1:
            raise RuntimeError("native_terminal_input_sequence_invalid")
        return terminal, sequence
    finally:
        terminal_service.generate_terminal_id = original_generator


def _publish_agui_native_deadline_before_send(
    *,
    barrier_directory: Path | None,
    existing_deadline: float | None,
) -> float:
    """Publish the AG-UI native budget once after initialization, before input."""
    if existing_deadline is not None:
        raise RuntimeError("agui_restart_deadline_already_published")
    if not isinstance(barrier_directory, Path):
        raise RuntimeError("mcp_restart_barrier_unavailable")
    from test.live_provider.interaction_opencode_mcp_gate import set_journey_deadline

    deadline = time.monotonic() + RUN_BUDGET_SECONDS
    set_journey_deadline(barrier_directory, deadline)
    return deadline


def _native_mcp_server(
    *,
    cao_home: Path,
    mcp_home: Path,
    mcp_xdg_data_home: Path,
    tmp_dir: Path,
    api_port: int,
    terminal_id: str,
    install_restart_barrier: bool = False,
    protocol_observation_path: Path | None = None,
    protocol_observation_nonce: str | None = None,
) -> dict[str, Any]:
    """Build the canonical bundled MCP entry without provider credentials."""
    source_pythonpath = common._source_pythonpath()
    if install_restart_barrier:
        source_pythonpath = os.pathsep.join(
            (str(Path(__file__).resolve().parent), source_pythonpath)
        )
    environment = {
        "HOME": str(mcp_home),
        "XDG_DATA_HOME": str(mcp_xdg_data_home),
        "TMPDIR": str(tmp_dir),
        "PYTHONPATH": source_pythonpath,
        "PYTHONDONTWRITEBYTECODE": "1",
        "CAO_HOME_DIR": str(cao_home),
        "CAO_DATABASE_CONFIG_FILE": str(cao_home / "database-config.json"),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": str(api_port),
        "CAO_TERMINAL_ID": terminal_id,
        "CAO_AUTH_MODE": "off",
    }
    if install_restart_barrier:
        environment["CAO_MCP_RESTART_BARRIER_DIR"] = str(cao_home.parent / "mcp-response-barrier")
    if (protocol_observation_path is None) != (protocol_observation_nonce is None):
        raise ValueError("mcp_observer_binding_incomplete")
    if protocol_observation_path is not None and protocol_observation_nonce is not None:
        if not install_restart_barrier:
            raise ValueError("mcp_observer_source_bound_hook_required")
        environment["CAO_OPENCODE_MCP_OBSERVER"] = "1"
        environment["CAO_OPENCODE_MCP_OBSERVER_PATH"] = str(protocol_observation_path)
        environment["CAO_OPENCODE_MCP_OBSERVER_NONCE"] = protocol_observation_nonce
        environment["CAO_OPENCODE_MCP_SOURCE_ROOT"] = str(Path(__file__).resolve().parents[2])
    return {
        "type": "local",
        "command": [sys.executable, "-m", "cli_agent_orchestrator.mcp_server.server"],
        "environment": environment,
        "disabled": False,
        "codemode": False,
        "timeout": NATIVE_MCP_TIMEOUT,
    }


def _cleanup_native_terminal(
    terminal_service: Any,
    terminal_id: str | None,
    failures: set[str],
) -> bool:
    """Delete the exact owned terminal and retain evidence when teardown fails."""
    if terminal_id is None:
        return True

    def delete_owned_terminal() -> None:
        if terminal_service.delete_terminal(terminal_id) is not True:
            raise RuntimeError("native_terminal_delete_unconfirmed")

    return common._cleanup_action(failures, "terminal_cleanup_failed", delete_owned_terminal)


def _capture_private_tmux_socket_identity(socket_path: Path) -> _TmuxSocketIdentity:
    """Capture the exact owner-only socket node while its tmux server is live."""
    if not socket_path.is_absolute():
        raise RuntimeError("private_tmux_socket_identity_invalid")
    try:
        status = socket_path.lstat()
    except OSError as error:
        raise RuntimeError("private_tmux_socket_identity_unavailable") from error
    if not stat.S_ISSOCK(status.st_mode) or status.st_uid != os.getuid():
        raise RuntimeError("private_tmux_socket_identity_invalid")
    return _TmuxSocketIdentity(
        device=status.st_dev,
        inode=status.st_ino,
        uid=status.st_uid,
        mode=stat.S_IMODE(status.st_mode),
    )


def _tmux_socket_path_registered(socket_path: Path) -> bool:
    """Return whether Linux still registers the exact path as a live Unix socket."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError("private_process_identity_unsupported")
    try:
        rows = Path("/proc/net/unix").read_text(encoding="utf-8").splitlines()[1:]
    except OSError as error:
        raise RuntimeError("private_tmux_socket_table_unavailable") from error
    expected_path = str(socket_path.absolute())
    for row in rows:
        fields = row.split(maxsplit=7)
        if len(fields) == 8 and fields[7] == expected_path:
            return True
    return False


def _unlink_receipted_stale_tmux_socket(
    socket_path: Path, socket_identity: _TmuxSocketIdentity | None
) -> None:
    """Remove a receipted stale socket from the run's private socket directory.

    The parent directory is trusted to be private, mode 0700, and owned by this
    UID. Rechecking the socket identity immediately before path-based unlink
    narrows the replacement window; it does not eliminate a same-UID TOCTOU race.
    """
    if socket_identity is None:
        raise RuntimeError("private_tmux_socket_identity_unavailable")
    try:
        parent_status = socket_path.parent.lstat()
    except OSError as error:
        raise RuntimeError("private_tmux_socket_parent_unavailable") from error
    if (
        not stat.S_ISDIR(parent_status.st_mode)
        or parent_status.st_uid != os.getuid()
        or parent_status.st_mode & 0o077
    ):
        raise RuntimeError("private_tmux_socket_parent_invalid")

    def check_receipt() -> bool:
        try:
            status = socket_path.lstat()
        except (FileNotFoundError, ProcessLookupError):
            return False
        if (
            not stat.S_ISSOCK(status.st_mode)
            or status.st_dev != socket_identity.device
            or status.st_ino != socket_identity.inode
            or status.st_uid != socket_identity.uid
            or stat.S_IMODE(status.st_mode) != socket_identity.mode
            or status.st_uid != os.getuid()
        ):
            raise RuntimeError("private_tmux_socket_identity_changed")
        return True

    if not check_receipt():
        return
    if _tmux_socket_path_registered(socket_path):
        raise RuntimeError("private_tmux_socket_still_registered")
    # Rechecking narrows the replacement window; unlink remains path-based.
    if check_receipt():
        socket_path.unlink()


def _cleanup_private_tmux_server(
    tmux_client: Any,
    tmux_tmpdir: Path,
    failures: set[str],
    *,
    terminal_id: str,
    server_identity: _ProcessIdentity | None,
    owned_processes: set[_ProcessIdentity],
    socket_identity: _TmuxSocketIdentity | None = None,
) -> bool:
    """Stop the pinned private server and prove its run-owned processes exited."""
    expected_socket = (tmux_tmpdir / f"tmux-{os.getuid()}" / "default").absolute()
    configured_socket = getattr(tmux_client.server, "socket_path", None)
    if not isinstance(configured_socket, (str, os.PathLike)):
        failures.add("tmux_socket_identity_unavailable")
        return False
    socket_path = Path(configured_socket)
    if not socket_path.is_absolute() or socket_path != expected_socket:
        failures.add("tmux_socket_identity_mismatch")
        return False
    if server_identity is None:
        # With no early pin there is no safe birth-time cutoff. Scan the full
        # same-UID marker set, then fail closed without adopting a late owner.
        try:
            _terminal_tagged_processes(terminal_id, socket_path, server_identity=None)
        except Exception:
            failures.add("tmux_process_identity_unavailable")
            return False
        failures.add("tmux_process_identity_unavailable")
        return False
    if socket_identity is None:
        failures.add("tmux_process_identity_unavailable")
        return False

    try:
        try:
            discovered = _find_tmux_server_identity(socket_path)
        except RuntimeError as error:
            if str(error) == "private_tmux_socket_owner_unknown":
                discovered = None
            else:
                raise
        if discovered is None:
            if _process_identity_alive(server_identity):
                raise RuntimeError("private_tmux_server_still_running")
            if not _wait_process_identities_gone(owned_processes, timeout=3.0):
                raise RuntimeError("private_terminal_descendant_still_running")
            groups = {(item.process_group, item.session_id) for item in owned_processes}
            groups.add((server_identity.process_group, server_identity.session_id))
            if _processes_in_groups(groups):
                raise RuntimeError("private_terminal_process_group_still_running")
            if _terminal_tagged_processes(
                terminal_id, socket_path, server_identity=server_identity
            ):
                raise RuntimeError("private_terminal_process_still_running")
            if os.path.lexists(socket_path):
                _unlink_receipted_stale_tmux_socket(socket_path, socket_identity)
            return True
        if discovered != server_identity:
            raise RuntimeError("private_tmux_server_identity_changed")
        if _capture_private_tmux_socket_identity(socket_path) != socket_identity:
            raise RuntimeError("private_tmux_socket_identity_changed")
        owned_processes.update(
            _owned_process_snapshot_for_cleanup(server_identity, terminal_id, socket_path)
        )
    except Exception:
        failures.add("tmux_process_identity_unavailable")
        return False

    def kill_exact_server() -> None:
        current_socket_identity = _capture_private_tmux_socket_identity(socket_path)
        current_server_identity = _find_tmux_server_identity(socket_path)
        if current_socket_identity != socket_identity:
            raise RuntimeError("private_tmux_socket_identity_changed")
        if current_server_identity != server_identity:
            raise RuntimeError("private_tmux_server_identity_changed")
        if not _process_identity_alive(server_identity):
            raise RuntimeError("private_tmux_server_identity_changed")
        result = tmux_client.server.cmd("kill-server")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not _process_identity_alive(server_identity):
                break
            time.sleep(0.05)
        if _process_identity_alive(server_identity):
            raise RuntimeError("private_tmux_server_process_still_running")
        if result.returncode != 0:
            raise RuntimeError("private_tmux_server_kill_unconfirmed")
        if not _wait_process_identities_gone(owned_processes, timeout=3.0):
            raise RuntimeError("private_terminal_descendant_still_running")
        groups = {(item.process_group, item.session_id) for item in owned_processes}
        groups.add((server_identity.process_group, server_identity.session_id))
        if _processes_in_groups(groups):
            raise RuntimeError("private_terminal_process_group_still_running")
        if _terminal_tagged_processes(terminal_id, socket_path, server_identity=server_identity):
            raise RuntimeError("private_terminal_process_still_running")
        if os.path.lexists(socket_path):
            _unlink_receipted_stale_tmux_socket(socket_path, socket_identity)

    return common._cleanup_action(failures, "tmux_server_cleanup_failed", kill_exact_server)


def _cleanup_failed_tmux_bootstrap(
    tmux_client: Any,
    tmux_tmpdir: Path,
    failures: set[str],
    *,
    server_identity: _ProcessIdentity | None,
    socket_identity: _TmuxSocketIdentity | None,
) -> bool:
    """Stop only the exact pinned server when bootstrap inventory failed."""
    failures.add("tmux_process_inventory_unavailable")
    expected_socket = (tmux_tmpdir / f"tmux-{os.getuid()}" / "default").absolute()
    configured_socket = getattr(tmux_client.server, "socket_path", None)
    if not isinstance(configured_socket, (str, os.PathLike)):
        failures.add("tmux_process_identity_unavailable")
        return False
    socket_path = Path(configured_socket)
    if not socket_path.is_absolute() or socket_path != expected_socket:
        failures.add("tmux_process_identity_unavailable")
        return False
    if server_identity is None or socket_identity is None:
        failures.add("tmux_process_identity_unavailable")
        return False

    def stop_exact_bootstrap_server() -> None:
        # This path is reached only before terminal creation. Inventory did not
        # complete, so never scan or signal descendants; prove and stop only the
        # process/socket pair captured before the failed inventory operation.
        if _capture_private_tmux_socket_identity(socket_path) != socket_identity:
            raise RuntimeError("private_tmux_socket_identity_changed")
        if _find_tmux_server_identity(socket_path) != server_identity:
            raise RuntimeError("private_tmux_server_identity_changed")
        if not _process_identity_alive(server_identity):
            raise RuntimeError("private_tmux_server_identity_changed")

        # Repeat the identity check immediately before issuing the path-scoped
        # tmux command. A replaced server or socket is preserved fail-closed.
        if _capture_private_tmux_socket_identity(socket_path) != socket_identity:
            raise RuntimeError("private_tmux_socket_identity_changed")
        if _find_tmux_server_identity(socket_path) != server_identity:
            raise RuntimeError("private_tmux_server_identity_changed")
        if not _process_identity_alive(server_identity):
            raise RuntimeError("private_tmux_server_identity_changed")

        result = tmux_client.server.cmd("kill-server")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not _process_identity_alive(server_identity):
                break
            time.sleep(0.05)
        if _process_identity_alive(server_identity):
            raise RuntimeError("private_tmux_server_process_still_running")
        if result.returncode != 0:
            raise RuntimeError("private_tmux_server_kill_unconfirmed")
        if os.path.lexists(socket_path):
            _unlink_receipted_stale_tmux_socket(socket_path, socket_identity)

    return common._cleanup_action(failures, "provider_cleanup_failed", stop_exact_bootstrap_server)


def _start_and_pin_private_tmux_server(
    tmux_client: Any,
    socket_path: Path,
) -> tuple[_ProcessIdentity, _TmuxSocketIdentity]:
    """Start a marker-free server and return its exact process/socket pins."""
    if "TMUX" in os.environ or "CAO_TERMINAL_ID" in os.environ:
        raise RuntimeError("private_tmux_bootstrap_environment_invalid")
    configured_socket = getattr(tmux_client.server, "socket_path", None)
    if not isinstance(configured_socket, (str, os.PathLike)):
        raise RuntimeError("private_tmux_socket_identity_unavailable")
    if Path(configured_socket) != socket_path or not socket_path.is_absolute():
        raise RuntimeError("private_tmux_socket_identity_mismatch")

    # Start without creating a terminal session and set exit-empty off in the
    # same tmux invocation. Capture the owner before terminal children exist.
    start_server = getattr(tmux_client, "_set_server_exit_empty_off", None)
    if not callable(start_server) or start_server() is not True:
        raise RuntimeError("private_tmux_server_start_unconfirmed")
    socket_identity = _capture_private_tmux_socket_identity(socket_path)
    server_identity = _find_tmux_server_identity(socket_path)
    if server_identity is None or not _process_identity_alive(server_identity):
        raise RuntimeError("private_tmux_server_owner_unknown")

    # Return the first positive pair before any fallible descendant inventory.
    # The caller stores these pins in its cleanup state immediately.
    return server_identity, socket_identity


def _snapshot_and_recheck_private_tmux_server(
    server_identity: _ProcessIdentity,
    socket_identity: _TmuxSocketIdentity,
    socket_path: Path,
    *,
    terminal_id: str,
) -> set[_ProcessIdentity]:
    """Inventory run-owned processes and reject owner/socket replacement."""
    owned_processes = _owned_process_snapshot(server_identity, terminal_id, socket_path)
    # Refuse a server or socket replacement during the ownership snapshot.
    if _capture_private_tmux_socket_identity(socket_path) != socket_identity:
        raise RuntimeError("private_tmux_socket_identity_changed")
    if _find_tmux_server_identity(socket_path) != server_identity:
        raise RuntimeError("private_tmux_server_identity_changed")
    if not _process_identity_alive(server_identity):
        raise RuntimeError("private_tmux_server_identity_changed")
    return owned_processes


def _owned_process_snapshot_for_cleanup(
    server_identity: _ProcessIdentity,
    terminal_id: str,
    socket_path: Path,
) -> set[_ProcessIdentity]:
    """Retry cleanup inventory across short-lived /proc entry races."""
    last_error: RuntimeError | None = None
    for attempt in range(3):
        try:
            return _owned_process_snapshot(server_identity, terminal_id, socket_path)
        except RuntimeError as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.05 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _process_info(
    pid: int, *, proc_root: Path = Path("/proc")
) -> tuple[_ProcessIdentity, int, str] | None:
    process = proc_root / str(pid)
    try:
        uid = process.stat().st_uid
        stat_data = (process / "stat").read_bytes()
        close = stat_data.rfind(b")")
        if close < 0:
            raise RuntimeError("process_stat_unreadable")
        fields = stat_data[close + 2 :].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    if len(fields) < 20:
        raise RuntimeError("process_stat_unreadable")
    try:
        state = fields[0].decode("ascii")
        parent_pid = _proc_stat_decimal(fields[1])
        process_group = _proc_stat_decimal(fields[2])
        session_id = _proc_stat_decimal(fields[3])
        start_time = _proc_stat_decimal(fields[19])
    except (IndexError, UnicodeDecodeError, ValueError) as error:
        raise RuntimeError("process_stat_unreadable") from error
    return (
        _ProcessIdentity(pid, uid, start_time, process_group, session_id),
        parent_pid,
        state,
    )


def _proc_stat_decimal(value: bytes) -> int:
    """Parse a required non-negative stat field using ASCII digits only."""
    if not value or any(byte < ord("0") or byte > ord("9") for byte in value):
        raise ValueError("invalid numeric process stat field")
    return int(value)


def _find_tmux_server_identity(
    socket_path: Path,
    *,
    proc_root: Path = Path("/proc"),
    unix_socket_table: Path = Path("/proc/net/unix"),
) -> _ProcessIdentity | None:
    """Find the same-user tmux process holding this exact private socket."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError("private_process_identity_unsupported")
    expected_path = str(socket_path.absolute())
    matches: set[str] = set()
    try:
        rows = unix_socket_table.read_text(encoding="utf-8").splitlines()[1:]
    except OSError as error:
        raise RuntimeError("private_tmux_socket_table_unavailable") from error
    for row in rows:
        fields = row.split(maxsplit=7)
        if len(fields) == 8 and fields[7] == expected_path:
            matches.add(fields[6])
    if not matches:
        if socket_path.exists():
            raise RuntimeError("private_tmux_socket_owner_unknown")
        return None
    if len(matches) != 1:
        raise RuntimeError("private_tmux_socket_inode_ambiguous")
    socket_link = f"socket:[{next(iter(matches))}]"
    owners: list[_ProcessIdentity] = []
    try:
        entries = tuple(proc_root.iterdir())
    except OSError as error:
        raise RuntimeError("private_process_table_unavailable") from error
    for entry in entries:
        if not entry.name.isdigit():
            continue
        info = _process_info(int(entry.name), proc_root=proc_root)
        if info is None or info[0].uid != os.getuid():
            continue
        try:
            comm = (entry / "comm").read_text(encoding="utf-8").strip()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as error:
            raise RuntimeError("private_process_table_unreadable") from error
        if not comm.startswith("tmux"):
            continue
        try:
            descriptors = tuple((entry / "fd").iterdir())
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as error:
            raise RuntimeError("private_process_table_unreadable") from error
        for descriptor in descriptors:
            try:
                if os.readlink(descriptor) == socket_link:
                    owners.append(info[0])
                    break
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError as error:
                raise RuntimeError("private_process_table_unreadable") from error
    unique_owners = set(owners)
    if len(unique_owners) > 1:
        raise RuntimeError("private_tmux_server_owner_ambiguous")
    return next(iter(unique_owners), None)


def _owned_process_snapshot(
    server: _ProcessIdentity,
    terminal_id: str,
    socket_path: Path,
    *,
    proc_root: Path = Path("/proc"),
) -> set[_ProcessIdentity]:
    """Pin server descendants and any detached process carrying this run's id."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError("private_process_identity_unsupported")
    processes: dict[int, tuple[_ProcessIdentity, int, str]] = {}
    try:
        entries = tuple(proc_root.iterdir())
    except OSError as error:
        raise RuntimeError("private_process_table_unavailable") from error
    for entry in entries:
        if entry.name.isdigit():
            info = _process_info(int(entry.name), proc_root=proc_root)
            if info is not None and info[0].uid == os.getuid():
                processes[info[0].pid] = info
    if processes.get(server.pid, (None, None, None))[0] != server:
        raise RuntimeError("private_tmux_server_identity_changed")

    descendants: set[int] = {server.pid}
    changed = True
    while changed:
        changed = False
        for pid, (_, parent_pid, _) in processes.items():
            if pid not in descendants and parent_pid in descendants:
                descendants.add(pid)
                changed = True

    result = {processes[pid][0] for pid in descendants if pid != server.pid and pid in processes}
    encoded_terminal_id = terminal_id.encode("utf-8")
    encoded_socket = os.fsencode(str(socket_path.absolute()))
    for pid, (identity, _, _) in processes.items():
        if pid in descendants:
            continue
        # A process that predates this exact server cannot be owned by this run.
        # Avoid reading unrelated same-UID environments while retaining strict
        # checks for every process that could have joined the run afterward.
        # Re-read stat before using the cutoff so a reused PID cannot inherit
        # the old process's exclusion.
        if identity.start_time < server.start_time:
            current = _process_info(pid, proc_root=proc_root)
            if current is None:
                continue
            if current[0] != identity:
                raise RuntimeError("private_process_identity_changed")
            continue
        if processes[pid][2] == "Z":
            current = _process_info(pid, proc_root=proc_root)
            if current is None:
                continue
            if current[0] != identity or current[2] != "Z":
                raise RuntimeError("private_process_identity_changed")
            continue
        try:
            raw_environment = (proc_root / str(pid) / "environ").read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as error:
            raise RuntimeError("private_process_environment_unreadable") from error
        environment = dict(
            field.split(b"=", 1) for field in raw_environment.split(b"\0") if b"=" in field
        )
        tmux_context = environment.get(b"TMUX", b"").rsplit(b",", 2)[0]
        if (
            environment.get(b"CAO_TERMINAL_ID") == encoded_terminal_id
            or tmux_context == encoded_socket
        ):
            result.add(identity)
    return result


def _process_identity_alive(identity: _ProcessIdentity) -> bool:
    info = _process_info(identity.pid)
    return info is not None and info[0] == identity and info[2] != "Z"


def _wait_process_identities_gone(identities: set[_ProcessIdentity], *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if not any(_process_identity_alive(identity) for identity in identities):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _processes_in_groups(groups: set[tuple[int, int]]) -> set[_ProcessIdentity]:
    if not groups:
        return set()
    result: set[_ProcessIdentity] = set()
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError as error:
        raise RuntimeError("private_process_table_unavailable") from error
    for entry in entries:
        if not entry.name.isdigit():
            continue
        info = _process_info(int(entry.name))
        if (
            info is not None
            and info[0].uid == os.getuid()
            and (
                info[0].process_group,
                info[0].session_id,
            )
            in groups
        ):
            result.add(info[0])
    return result


def _terminal_tagged_processes(
    terminal_id: str,
    socket_path: Path,
    *,
    proc_root: Path = Path("/proc"),
    server_identity: _ProcessIdentity | None = None,
) -> set[_ProcessIdentity]:
    """Find detached same-user processes with this terminal or socket marker."""
    result: set[_ProcessIdentity] = set()
    encoded_terminal_id = terminal_id.encode("utf-8")
    encoded_socket = os.fsencode(str(socket_path.absolute()))
    try:
        entries = tuple(proc_root.iterdir())
    except OSError as error:
        raise RuntimeError("private_process_table_unavailable") from error
    for entry in entries:
        if not entry.name.isdigit():
            continue
        info = _process_info(int(entry.name), proc_root=proc_root)
        if info is None or info[0].uid != os.getuid():
            continue
        # Only an exact captured server identity provides a safe lower bound.
        # Without it, keep the full scan and fail closed on unreadable state.
        if server_identity is not None and info[0].start_time < server_identity.start_time:
            current = _process_info(info[0].pid, proc_root=proc_root)
            if current is None:
                continue
            if current[0] != info[0]:
                raise RuntimeError("private_process_identity_changed")
            continue
        if info[2] == "Z":
            current = _process_info(info[0].pid, proc_root=proc_root)
            if current is None:
                continue
            if current[0] != info[0] or current[2] != "Z":
                raise RuntimeError("private_process_identity_changed")
            continue
        try:
            raw_environment = (entry / "environ").read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as error:
            raise RuntimeError("private_process_environment_unreadable") from error
        environment = dict(
            field.split(b"=", 1) for field in raw_environment.split(b"\0") if b"=" in field
        )
        tmux_context = environment.get(b"TMUX", b"").rsplit(b",", 2)[0]
        if (
            environment.get(b"CAO_TERMINAL_ID") == encoded_terminal_id
            or tmux_context == encoded_socket
        ):
            result.add(info[0])
    return result


def _private_cli_environment(
    *,
    root: Path,
    cao_home: Path,
    config_dir: Path,
    data_home: Path,
    api_port: int,
    source_pythonpath: str,
    path_value: str,
    tmux_tmpdir: Path,
    http_tool_offer_nonce: str | None = None,
) -> dict[str, str]:
    home = _private_directory(root / "private-home")
    values = {
        "HOME": str(home),
        "PATH": path_value,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TERM": "xterm-256color",
        "TMPDIR": str(_private_directory(root / "tmp")),
        "TMUX_TMPDIR": str(tmux_tmpdir),
        "PYTHONPATH": source_pythonpath,
        "PYTHONDONTWRITEBYTECODE": "1",
        "CAO_HOME_DIR": str(cao_home),
        "CAO_DATABASE_CONFIG_FILE": str(cao_home / "database-config.json"),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": str(api_port),
        "CAO_AUTH_MODE": "off",
        "CAO_TERMINAL_BACKEND": "tmux",
        "OPENCODE_CONFIG": str(config_dir / "opencode.json"),
        "OPENCODE_CONFIG_DIR": str(config_dir),
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "XDG_CONFIG_HOME": str(_private_directory(root / "xdg-config")),
        "XDG_DATA_HOME": str(data_home),
        "XDG_CACHE_HOME": str(_private_directory(root / "xdg-cache")),
        "XDG_STATE_HOME": str(_private_directory(root / "xdg-state")),
    }
    if http_tool_offer_nonce is not None:
        if re.fullmatch(r"[0-9a-f]{32}", http_tool_offer_nonce) is None:
            raise RuntimeError("private_observer_nonce_invalid")
        values["CAO_OPENCODE_HTTP_TOOL_OFFER_NONCE"] = http_tool_offer_nonce
    # An allowlist intentionally omits provider API keys and unrelated operator env.
    return values


def _bind_private_runtime_paths(
    *,
    cao_home: Path,
    modules: list[Any],
    module_bindings: list[tuple[Any, str, Any]],
) -> tuple[Path, Path, Path]:
    """Rebind imported runtime path constants to this run's private tree.

    CAO's constants are intentionally read once at import time.  A live
    harness imports the production modules before it can create its disposable
    ``CAO_HOME_DIR``; changing only ``constants.CAO_HOME_DIR`` therefore leaves
    FIFO and log modules holding the operator path.  That is unsafe for the
    isolation contract and, on WSL ``9p`` mounts, named-pipe creation fails
    with ``EOPNOTSUPP``.  Record every binding so the caller restores it in the
    normal reverse-order cleanup path.
    """
    private_log_dir = _private_directory(cao_home / "logs")
    private_terminal_log_dir = _private_directory(private_log_dir / "terminal")
    private_fifo_dir = _private_directory(cao_home / "fifos")
    replacements = {
        "LOG_DIR": private_log_dir,
        "TERMINAL_LOG_DIR": private_terminal_log_dir,
        "FIFO_DIR": private_fifo_dir,
    }
    seen: set[tuple[int, str]] = set()
    for module in modules:
        if module is None:
            continue
        for name, replacement in replacements.items():
            binding = (id(module), name)
            if binding in seen or not hasattr(module, name):
                continue
            seen.add(binding)
            module_bindings.append((module, name, getattr(module, name)))
            setattr(module, name, replacement)
    return private_log_dir, private_terminal_log_dir, private_fifo_dir


def _bind_private_constants_paths(
    *,
    constants_module: Any,
    source_cao_home: Path,
    cao_home: Path,
    module_bindings: list[tuple[Any, str, Any]],
) -> dict[str, Path]:
    """Rebase every CAO path constant that was derived from the operator home."""
    bound: dict[str, Path] = {}
    for name in _PRIVATE_CONSTANT_PATH_NAMES:
        value = getattr(constants_module, name, None)
        if not isinstance(value, Path):
            continue
        try:
            relative = value.relative_to(source_cao_home)
        except ValueError:
            continue
        replacement = cao_home / relative
        module_bindings.append((constants_module, name, value))
        setattr(constants_module, name, replacement)
        bound[name] = replacement
    return bound


def _replace_process_environment(values: dict[str, str]) -> dict[str, str]:
    """Replace the entire harness environment before tmux can inherit it."""
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()):
        raise ValueError("private_process_environment_invalid")
    previous = dict(os.environ)
    os.environ.clear()
    os.environ.update(values)
    return previous


def _restore_process_environment(previous: dict[str, str]) -> None:
    os.environ.clear()
    os.environ.update(previous)


def _pin_tmux_client_to_private_server(tmux_client: Any, tmux_tmpdir: Path) -> Path:
    """Bind libtmux to an absolute, fresh private socket before its first command."""
    from cli_agent_orchestrator.clients.tmux_transport import BoundedTmuxServer

    socket_parent = tmux_tmpdir / f"tmux-{os.getuid()}"
    socket_path = (socket_parent / "default").absolute()
    if len(os.fsencode(socket_path)) >= 108:
        raise RuntimeError("private_tmux_socket_path_too_long")
    _private_directory(socket_parent)
    if socket_path.exists() or socket_path.is_symlink():
        raise RuntimeError("private_tmux_socket_not_empty")
    tmux_client.server = BoundedTmuxServer(socket_path=str(socket_path), config_file="/dev/null")
    configured = Path(tmux_client.server.socket_path)
    if not configured.is_absolute() or configured != socket_path:
        raise RuntimeError("private_tmux_socket_not_pinned")
    return socket_path


def _isolated_mcp_command(
    environment: dict[str, str],
    executable: str,
    arguments: list[str],
    *,
    env_executable: str | None = None,
) -> list[str]:
    """Launch the MCP child with only its explicit private service environment."""
    if not os.path.isabs(executable):
        raise ValueError("absolute_mcp_executable_required")
    unknown = set(environment) - _ISOLATED_MCP_ENV_NAMES
    if unknown:
        raise ValueError("mcp_environment_not_allowlisted")
    if any(
        not isinstance(key, str) or not isinstance(value, str) or "\x00" in value
        for key, value in environment.items()
    ):
        raise ValueError("invalid_mcp_environment")
    env_path = env_executable or shutil.which("env", path="/usr/bin:/bin")
    if not env_path or not os.path.isabs(env_path):
        raise RuntimeError("absolute_env_executable_required")
    assignments = [f"{key}={environment[key]}" for key in sorted(environment)]
    return [env_path, "-i", *assignments, executable, *arguments]


def _isolate_mcp_server(server: dict[str, Any]) -> None:
    """Replace inherited-env MCP launch with a bounded `env -i` command."""
    command = server.get("command")
    environment = server.get("environment")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and "\x00" not in part for part in command)
        or not isinstance(environment, dict)
    ):
        raise ValueError("mcp_server_configuration_invalid")
    server["command"] = _isolated_mcp_command(environment, command[0], command[1:])
    server["environment"] = {}


def _overlay_native_mcp(
    servers: dict[str, Any],
    capability_file: str | None,
    overlay: Any,
    *,
    interaction_pull_flow_only: bool = False,
) -> dict[str, Any]:
    """Apply CAO's protected capability overlay without changing recognition shape."""
    if not isinstance(capability_file, str) or not capability_file:
        raise RuntimeError("native_interaction_capability_required")
    result = overlay(
        servers,
        capability_file,
        interaction_pull_flow_only=interaction_pull_flow_only,
    )
    server = result.get("cao-mcp-server") if isinstance(result, dict) else None
    if not isinstance(server, dict):
        raise RuntimeError("native_cao_mcp_server_missing")
    from cli_agent_orchestrator.providers.mcp_interactions import (
        is_bundled_cao_mcp_entry,
    )

    if not is_bundled_cao_mcp_entry("cao-mcp-server", server):
        raise RuntimeError("native_cao_mcp_server_not_canonical")
    environment = server.get("environment")
    if (
        not isinstance(environment, dict)
        or environment.get("CAO_INTERACTION_CAPABILITY_FILE") != capability_file
    ):
        raise RuntimeError("native_interaction_capability_not_forwarded")
    from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV

    marker = environment.get(_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV)
    if marker != ("1" if interaction_pull_flow_only else None):
        raise RuntimeError("native_interaction_tool_scope_not_forwarded")
    return result


def _isolate_native_mcp_for_v2_writer(source_config: dict[str, Any]) -> dict[str, Any]:
    """Isolate the harness's canonical MCP child in a deep-copied v2 config."""
    isolated_config = copy.deepcopy(source_config)
    native_mcp = isolated_config.get("mcp")
    servers = native_mcp.get("servers") if isinstance(native_mcp, dict) else None
    if not isinstance(servers, dict):
        raise RuntimeError("native_mcp_server_configuration_invalid")
    server = servers.get("cao-mcp-server")
    if not isinstance(server, dict):
        raise RuntimeError("native_cao_mcp_server_missing")
    from cli_agent_orchestrator.providers.mcp_interactions import (
        is_bundled_cao_mcp_entry,
    )

    if not is_bundled_cao_mcp_entry("cao-mcp-server", server):
        raise RuntimeError("native_cao_mcp_server_not_canonical")
    environment = server.get("environment")
    capability_file = (
        environment.get("CAO_INTERACTION_CAPABILITY_FILE")
        if isinstance(environment, dict)
        else None
    )
    if not isinstance(capability_file, str) or not capability_file:
        raise RuntimeError("native_interaction_capability_required")
    _isolate_mcp_server(server)
    if "CAO_INTERACTION_CAPABILITY_FILE=" + capability_file not in server["command"]:
        raise RuntimeError("native_interaction_capability_not_forwarded")
    return isolated_config


def _write_v2_configuration_with_isolated_native_mcp(
    writer: Any,
    destination: Path,
    *,
    source_agents: Path,
    source_config: dict[str, Any],
    session_id: str,
    source_auth: Path | None = None,
    source_credential_db: Path | None = None,
    selected_model: str | None = None,
    selected_agent_source: tuple[str, str] | None = None,
) -> Any:
    """Isolate only the serialized child config, after provider discovery."""
    isolated_config = _isolate_native_mcp_for_v2_writer(source_config)
    return writer(
        destination,
        source_agents=source_agents,
        source_config=isolated_config,
        session_id=session_id,
        source_auth=source_auth,
        source_credential_db=source_credential_db,
        selected_model=selected_model,
        selected_agent_source=selected_agent_source,
    )


def _provider_env_command(provider_env: Path, command: list[str]) -> list[str]:
    if (
        provider_env.is_symlink()
        or not provider_env.is_file()
        or provider_env.stat().st_mode & 0o077
    ):
        raise RuntimeError("private_provider_env_required")
    return [
        "/bin/bash",
        "-c",
        'set +x; . "$1"; shift; exec "$@"',
        "cao-opencode-private-env",
        str(provider_env),
        *command,
    ]


def _opencode_error_summary(event: dict[str, Any]) -> tuple[dict[str, int], int]:
    """Classify only fixed OpenCode error names/messages; never return their text."""
    names: dict[str, int] = {}
    categories: dict[str, int] = {}
    pending: list[tuple[Any, int]] = [(event, 0)]
    visited = 0
    allowed_nested_fields = {
        "error",
        "cause",
        "data",
        "originalError",
        "providerError",
        "response",
    }
    while pending and visited < 64:
        value, depth = pending.pop()
        visited += 1
        if depth > 6:
            continue
        if isinstance(value, dict):
            name = value.get("name")
            if isinstance(name, str) and name in _OPENCODE_ERROR_NAME_CATEGORIES:
                names[name] = names.get(name, 0) + 1
                category = _OPENCODE_ERROR_NAME_CATEGORIES[name]
                categories[category] = categories.get(category, 0) + 1
            for key, nested in value.items():
                if key in allowed_nested_fields:
                    pending.append((nested, depth + 1))
                elif key in {"message", "error_description", "reason"} and isinstance(nested, str):
                    lowered = nested.lower()
                    for category, markers in _OPENCODE_ERROR_MESSAGE_MARKERS.items():
                        if any(marker in lowered for marker in markers):
                            categories[category] = categories.get(category, 0) + 1
            status = value.get("status")
            if status == 429:
                categories["provider_rate_limited"] = categories.get("provider_rate_limited", 0) + 1
            elif status in {401, 403}:
                categories["provider_auth_rejected"] = (
                    categories.get("provider_auth_rejected", 0) + 1
                )
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value[:16])
    return names, categories


def _opencode_event_summary(path: Path) -> dict[str, Any]:
    events: dict[str, int] = {}
    items: dict[str, int] = {}
    usage: dict[str, int] = {}
    tool_names: set[str] = set()
    unexpected_tool_name_count = 0
    known_error_names: dict[str, int] = {}
    failure_categories: dict[str, int] = {}
    unclassified_error_event_count = 0
    step_started = 0
    step_finished = 0
    required_tools = set(common.INTERACTION_TOOLS[:3])
    try:
        stream = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        stream = None
    if stream is not None:
        with stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(event, dict):
                    continue
                event_type = event.get("type")
                if event_type in _OPENCODE_EVENT_TYPES:
                    events[event_type] = events.get(event_type, 0) + 1
                if event_type == "step_start":
                    step_started += 1
                elif event_type == "step_finish":
                    step_finished += 1
                if event_type in {"error", "session.error"} or event.get("error") is not None:
                    names, categories = _opencode_error_summary(event)
                    for name, count in names.items():
                        known_error_names[name] = known_error_names.get(name, 0) + count
                    for category, count in categories.items():
                        failure_categories[category] = failure_categories.get(category, 0) + count
                    if not names and not categories:
                        unclassified_error_event_count += 1
                part = event.get("part")
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type in _OPENCODE_ITEM_TYPES:
                    items[part_type] = items.get(part_type, 0) + 1
                if part_type == "tool":
                    name = part.get("tool")
                    matched = False
                    if isinstance(name, str):
                        for tool in common.INTERACTION_TOOLS:
                            if name in {
                                tool,
                                "cao-mcp-server_" + tool,
                                "cao_mcp_server_" + tool,
                            }:
                                tool_names.add(tool)
                                matched = True
                                break
                    if not matched:
                        unexpected_tool_name_count += 1
                    state = part.get("state")
                    if isinstance(state, dict):
                        status = state.get("status")
                        if status in {"denied", "rejected"}:
                            failure_categories["tool_permission_denied"] = (
                                failure_categories.get("tool_permission_denied", 0) + 1
                            )
                        if status == "error":
                            names, categories = _opencode_error_summary(state)
                            for error_name, count in names.items():
                                known_error_names[error_name] = (
                                    known_error_names.get(error_name, 0) + count
                                )
                            for category, count in categories.items():
                                failure_categories[category] = (
                                    failure_categories.get(category, 0) + count
                                )
                tokens = part.get("tokens")
                if not isinstance(tokens, dict):
                    continue
                for source, destination in (
                    ("input", "input_tokens"),
                    ("output", "output_tokens"),
                    ("reasoning", "reasoning_tokens"),
                ):
                    value = tokens.get(source)
                    if (
                        isinstance(value, int)
                        and not isinstance(value, bool)
                        and 0 <= value <= 10**12
                    ):
                        usage[destination] = usage.get(destination, 0) + value
                cache = tokens.get("cache")
                if isinstance(cache, dict):
                    for source, destination in (
                        ("read", "cache_read_tokens"),
                        ("write", "cache_write_tokens"),
                    ):
                        value = cache.get(source)
                        if (
                            isinstance(value, int)
                            and not isinstance(value, bool)
                            and 0 <= value <= 10**12
                        ):
                            usage[destination] = usage.get(destination, 0) + value
    if step_started and not step_finished:
        failure_categories["provider_step_incomplete"] = 1
    # A missing tool call alone is not evidence that the model lacks tools.
    # Catalog availability is reported only from the separate MCP preflight.
    return {
        "provider_event_counts": dict(sorted(events.items())),
        "provider_item_type_counts": dict(sorted(items.items())),
        "model_usage_counts": dict(sorted(usage.items())),
        "mcp_tool_names": sorted(tool_names),
        "unexpected_tool_name_count": unexpected_tool_name_count,
        "opencode_error_name_counts": dict(sorted(known_error_names.items())),
        "opencode_failure_category_counts": dict(sorted(failure_categories.items())),
        "unclassified_error_event_count": unclassified_error_event_count,
        "tool_calling_observed": bool(tool_names & required_tools),
    }


def _opencode_event_receipt_fields(events: dict[str, Any]) -> dict[str, Any]:
    """Project the fixed event-summary fields into the public journey result."""
    return {
        "provider_event_counts": events["provider_event_counts"],
        "provider_item_type_counts": events["provider_item_type_counts"],
        "model_usage_counts": events["model_usage_counts"],
        "mcp_tool_names": events["mcp_tool_names"],
        "opencode_error_name_counts": events["opencode_error_name_counts"],
        "opencode_failure_category_counts": events["opencode_failure_category_counts"],
        "unclassified_error_event_count": events["unclassified_error_event_count"],
        "unexpected_tool_name_count": events["unexpected_tool_name_count"],
        "tool_calling_observed": events["tool_calling_observed"],
    }


def _mcp_observer_receipt_fields(
    *,
    observation_path: Path | None,
    expected_nonce: str | None,
    agent_tool_scope_exact: bool | None,
) -> dict[str, Any]:
    """Read actual server-dispatch observations, never infer calls from config."""
    if observation_path is None or expected_nonce is None:
        observations = opencode_mcp_observer._empty_summary()
    else:
        observations = opencode_mcp_observer.read_protocol_observations(
            observation_path,
            expected_nonce=expected_nonce,
        )
    result = {
        "mcp_observer_status": observations["observer_status"],
        "mcp_catalog_observation_status": observations["mcp_catalog_observation_status"],
        "mcp_catalog_tool_names": observations["mcp_catalog_tool_names"],
        "mcp_catalog_unexpected_tool_count": observations["mcp_catalog_unexpected_tool_count"],
        "mcp_catalog_observation_count": observations["mcp_catalog_observation_count"],
        "mcp_tool_observation_status": observations["mcp_tool_observation_status"],
        "mcp_tool_unexpected_call_count": observations["mcp_tool_unexpected_call_count"],
        "observed_interaction_tools": observations["observed_interaction_tools"],
        "mcp_tool_calls": observations["mcp_tool_calls"],
        "mcp_observer_shutdown_observed": observations.get("mcp_observer_shutdown_observed")
        is True,
    }
    if isinstance(agent_tool_scope_exact, bool):
        result["agent_tool_scope_exact"] = agent_tool_scope_exact
    return result


def _native_tui_stage(provider: Any, output: str) -> str:
    """Reduce the provider's current screen status to a fixed safe category."""
    try:
        status = provider.get_status(output)
    except Exception:
        return "unknown"
    value = getattr(status, "value", status)
    if value in {"idle", "completed"}:
        return "idle"
    if value == "processing":
        return "running"
    return "unknown"


def _native_turn_stage(provider: Any, output: str) -> str:
    """Distinguish an unsubmitted composer from a completed no-tool turn."""
    try:
        status = provider.get_status(output)
    except Exception:
        return "unknown"
    value = getattr(status, "value", status)
    if value == "processing":
        return "running"
    if value == "completed":
        return "completed_turn"
    if value == "idle":
        # Mini v2 can render the submitted prompt with its idle footer for a
        # short interval while the Enter/paste boundary settles.  IDLE alone
        # is therefore insufficient evidence that no turn was sent.  Keep the
        # probe alive until the prompt disappears, a provider turn completes,
        # or the bounded journey expires.
        if getattr(provider, "_cli_major", None) == 2 and re.search(r"(?m)^\s*›\s+\S", output):
            return "prompt_pending_submission"
        return "idle_no_turn"
    if value == "waiting_user_answer":
        return "waiting_user_answer"
    if value == "error":
        return "error"
    return "unknown"


def _classify_no_tool_result(
    *, native_turn_stage: str, question_count: int, timed_out: bool
) -> str:
    """Classify a no-question turn without exposing provider text."""
    if question_count:
        return "question_created_but_not_acknowledged"
    if native_turn_stage == "idle_no_turn":
        return "prompt_not_submitted"
    if native_turn_stage == "completed_turn":
        return "model_completed_without_tool"
    if native_turn_stage == "error":
        return "provider_error"
    if timed_out:
        return "timed_out_before_question"
    return "cli_exited_without_ack"


def _provider_final_state(provider: Any, output: str) -> str:
    """Return only the production provider's closed status enum for one buffer."""
    from cli_agent_orchestrator.models.terminal import TerminalStatus

    try:
        status = provider.get_status(output)
    except Exception:
        return TerminalStatus.UNKNOWN.name
    return status.name if isinstance(status, TerminalStatus) else TerminalStatus.UNKNOWN.name


def _provider_final_state_is_complete(status: str) -> bool:
    return status in {"IDLE", "COMPLETED"}


def _stable_question_identity_projection(
    identities: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    stable_fields = ("group_id", "version", "terminal_id", "incarnation_id")
    return {
        question_id: {field: identity[field] for field in stable_fields}
        for question_id, identity in identities.items()
    }


def _final_agui_snapshot_checks(
    snapshot: dict[str, Any],
    expected: dict[str, dict[str, Any]],
    before_identity: dict[str, dict[str, Any]] | None,
    rows: list[Any],
) -> tuple[dict[str, dict[str, Any]], bool, bool, bool]:
    """Validate final acknowledged state while keeping identity separate from lifecycle."""
    from test.live_provider.interaction_agui_restart import snapshot_identity_map

    final_identity = snapshot_identity_map(snapshot)
    identity_stable = (
        before_identity is not None
        and _stable_question_identity_projection(final_identity) == expected
        and _stable_question_identity_projection(before_identity) == expected
    )
    acknowledged = common._snapshot_matches_questions(
        snapshot,
        expected,
        lifecycle="answered",
        delivery_state="agent_acknowledged",
    )
    ledger_stable = _question_ledger_matches(
        rows,
        expected,
        lifecycle="answered",
        delivery_state="agent_acknowledged",
    )
    return final_identity, acknowledged, identity_stable, ledger_stable


def _completion_allowed_before_deadline(
    *,
    success_conditions: bool,
    timed_out: bool,
    deadline: float,
    observed_at: float,
    status_observed_at: float | None,
) -> bool:
    return (
        success_conditions
        and not timed_out
        and observed_at < deadline
        and (status_observed_at is None or status_observed_at < deadline)
    )


def _native_mcp_log_diagnostics(log_path: Path | None) -> tuple[bool, str]:
    """Read the private startup log, returning only a fixed status and boolean."""
    if log_path is None:
        return False, "not_configured"
    try:
        startup_log = log_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False, "missing"
    except OSError:
        return False, "unreadable"
    connected = (
        re.search(r'\bmessage="mcp connected"\s+server=cao-mcp-server(?:\s|$)', startup_log)
        is not None
    )
    return connected, "connected" if connected else "no_connected_marker"


def _terminate(process: subprocess.Popen[Any]) -> bool:
    return common._terminate_owned_process_group(process)


def _forbidden_interaction_content() -> tuple[str, ...]:
    return tuple(
        [question["prompt"] for question in common.QUESTION_SPECS]
        + [
            option["label"]
            for question in common.QUESTION_SPECS
            for option in question.get("options", [])
        ]
        + [
            value
            for answer in common.HUMAN_ANSWERS.values()
            for value in (answer.get("text"), *answer.get("option_ids", []))
            if isinstance(value, str)
        ]
    )


def _private_file_identity(path: Path) -> tuple[int, int, int, int]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError("database_target_identity_unavailable") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise RuntimeError("database_target_identity_invalid")
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns


def _question_ledger_matches(
    rows: list[Any],
    expected: dict[str, dict[str, Any]],
    *,
    lifecycle: str,
    delivery_state: str | None,
) -> bool:
    if len(rows) != len(expected):
        return False
    actual: dict[str, dict[str, Any]] = {}
    for row in rows:
        question_id = row["question_id"]
        if not isinstance(question_id, str) or question_id in actual:
            return False
        actual[question_id] = {
            "group_id": row["group_id"],
            "version": int(row["version"]),
            "terminal_id": row["terminal_id"],
            "incarnation_id": row["incarnation_id"],
        }
        if row["lifecycle"] != lifecycle or row["state"] != delivery_state:
            return False
    return actual == expected


def _question_ledger_identity_matches(rows: list[Any], expected: dict[str, dict[str, Any]]) -> bool:
    """Compare durable question identity while allowing normal state changes."""
    if len(rows) != len(expected):
        return False
    actual: dict[str, dict[str, Any]] = {}
    for row in rows:
        question_id = row["question_id"]
        if not isinstance(question_id, str) or question_id in actual:
            return False
        try:
            version = int(row["version"])
        except (TypeError, ValueError):
            return False
        actual[question_id] = {
            "group_id": row["group_id"],
            "version": version,
            "terminal_id": row["terminal_id"],
            "incarnation_id": row["incarnation_id"],
        }
    return actual == expected


def _restart_api_worker_with_pending_questions(
    *,
    api_worker: Any,
    api_worker_spec: Any,
    expected_questions: dict[str, dict[str, Any]],
    forbidden_content: tuple[str, ...],
    terminal_id: str,
    tmux_socket_path: Path,
    tmux_server_identity: _ProcessIdentity,
    database_config_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Replace one API worker between strict pending snapshots, fencing all other state."""
    from test.live_provider.interaction_agui_restart import snapshot_identity_map

    worker_process = api_worker.process
    if worker_process is None or worker_process.poll() is not None:
        raise RuntimeError("api_worker_not_running_before_restart")
    worker_info = _process_info(worker_process.pid)
    if worker_info is None or worker_info[0].uid != os.geteuid():
        raise RuntimeError("api_worker_identity_unavailable")
    database_identity_before = _private_file_identity(database_config_path)
    native_processes_before = _terminal_tagged_processes(
        terminal_id,
        tmux_socket_path,
        server_identity=tmux_server_identity,
    )
    if not native_processes_before or not all(
        _process_identity_alive(identity) for identity in native_processes_before
    ):
        raise RuntimeError("native_provider_process_identity_unavailable")

    before_snapshot = common._read_agui_snapshot(
        api_worker_spec.base_url,
        forbidden_content=forbidden_content,
    )
    if not common._snapshot_matches_questions(
        before_snapshot,
        expected_questions,
        lifecycle="open",
        delivery_state=None,
    ):
        raise RuntimeError("agui_snapshot_identity_mismatch")
    before_identity = snapshot_identity_map(before_snapshot)

    api_worker.restart()

    restarted_process = api_worker.process
    if restarted_process is None or restarted_process.poll() is not None:
        raise RuntimeError("api_worker_not_running_after_restart")
    restarted_info = _process_info(restarted_process.pid)
    if (
        restarted_info is None
        or restarted_info[0].uid != os.geteuid()
        or restarted_info[0] == worker_info[0]
    ):
        raise RuntimeError("api_worker_replacement_identity_invalid")
    native_processes_after = _terminal_tagged_processes(
        terminal_id,
        tmux_socket_path,
        server_identity=tmux_server_identity,
    )
    if (
        native_processes_after != native_processes_before
        or not all(_process_identity_alive(identity) for identity in native_processes_after)
        or _private_file_identity(database_config_path) != database_identity_before
    ):
        raise RuntimeError("native_runtime_changed_during_api_restart")

    after_snapshot = common._read_agui_snapshot(
        api_worker_spec.base_url,
        forbidden_content=forbidden_content,
    )
    if not common._snapshot_matches_questions(
        after_snapshot,
        expected_questions,
        lifecycle="open",
        delivery_state=None,
    ):
        raise RuntimeError("agui_restart_identity_changed")
    after_identity = snapshot_identity_map(after_snapshot)
    if before_identity != after_identity:
        raise RuntimeError("agui_restart_identity_changed")
    return before_identity, after_identity


def run(
    *,
    model: str | None,
    run_model: bool,
    agui_restart: bool = False,
) -> dict[str, Any]:
    """Run only after an explicit model name and explicit release flag are supplied."""
    if not run_model:
        return {"provider": "opencode_cli", "result": "model_run_not_approved"}
    try:
        selected_model = _validate_model_id(model)
    except ValueError:
        return {"provider": "opencode_cli", "result": "explicit_model_required"}

    phase = "setup"
    try:
        root = _private_opencode_root()
    except Exception as exc:
        return {
            "provider": "opencode_cli",
            "result": "harness_error",
            "phase": phase,
            "error_type": type(exc).__name__,
        }
    original_home = common.ORIGINAL_HOME
    source_auth: Path | None = None
    source_credential_db: Path | None = None
    source_auth_before: tuple[int, int, int, int, int, int, int, int] | None = None
    source_auth_after: tuple[int, int, int, int, int, int, int, int] | None = None
    source_operator_state_before: dict[Path, tuple[int, int, int, int, int, int, int, int]] = {}
    server = None
    server_thread: threading.Thread | None = None
    listener: socket.socket | None = None
    api_worker = None
    api_worker_stdout = None
    api_worker_stderr = None
    api_worker_spec = None
    api_worker_restarts = 0
    agui_restart_completed = False
    agui_snapshot_count = 0
    agui_identity_stable: bool | None = None
    agui_projection_redacted: bool | None = None
    agui_final_acknowledged: bool | None = None
    agui_ledger_identity_stable: bool | None = None
    native_provider_processes_stable: bool | None = None
    database_target_stable: bool | None = None
    agui_observation_diagnostics: dict[str, Any] | None = None
    agui_exact_ack_observed = False
    agui_observation_complete = False
    agui_before_identity: dict[str, Any] | None = None
    native_provider_final_state: str | None = None
    native_provider_final_state_observed_at: float | None = None
    native_turn_stage = "unknown"
    native_turn_observed = False
    deadline: float | None = None
    expected_agui_group_id: str | None = None
    expected_agui_questions: dict[str, dict[str, Any]] = {}
    mcp_barrier_directory: Path | None = None
    mcp_barrier_resolved = False
    http_tool_offer_nonce = os.urandom(16).hex() if agui_restart else None
    http_tool_offer_directory: Path | None = None
    database_context = None
    database_context_entered = False
    database_module = None
    terminal_service_module = None
    terminal_id: str | None = None
    terminal = None
    tmux_cleanup_attempted = False
    tmux_process_inventory_complete = False
    tmux_client = None
    tmux_tmpdir: Path | None = None
    tmux_socket_path: Path | None = None
    tmux_server_identity: _ProcessIdentity | None = None
    tmux_socket_identity: _TmuxSocketIdentity | None = None
    tmux_owned_processes: set[_ProcessIdentity] = set()
    backend_registry = None
    interaction_service_module = None
    terminal_holder: dict[str, Any] = {}
    module_bindings: list[tuple[Any, str, Any]] = []
    receipt_holder: list[dict[str, Any]] = []
    native_mcp_log: Path | None = None
    mcp_protocol_observation: Path | None = None
    mcp_protocol_nonce: str | None = None
    agent_tool_scope_exact: bool | None = None
    private_scope_verified: bool | None = None
    scope_attestation: dict[str, bool] | None = None

    def mark_phase(next_phase: str) -> None:
        nonlocal deadline, phase
        phase = next_phase
        if next_phase == "native_send_input" and agui_restart:
            deadline = _publish_agui_native_deadline_before_send(
                barrier_directory=mcp_barrier_directory,
                existing_deadline=deadline,
            )

    def remember_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
        if private_scope_verified is not None:
            receipt.setdefault("private_scope_verified", private_scope_verified)
        if scope_attestation is not None:
            receipt.setdefault("scope_attestation", dict(scope_attestation))
        receipt_holder[:] = [receipt]
        return receipt

    def operator_state_unchanged() -> bool:
        """Verify every operator credential state file that this run staged."""
        nonlocal source_auth_after
        if not source_operator_state_before:
            return False
        try:
            for path, before in source_operator_state_before.items():
                after = _credential_state_stat(path)
                if path == source_auth:
                    source_auth_after = after
                if after != before:
                    return False
        except Exception:
            return False
        return True

    original_environment = dict(os.environ)
    try:
        cao_home = _private_directory(root / "cao")
        tmp_dir = _private_directory(root / "tmp")
        private_home = _private_directory(root / "private-home")
        tmux_tmpdir = _private_directory(root / "tmux-tmp")
        xdg_config = _private_directory(root / "xdg-config")
        xdg_cache = _private_directory(root / "xdg-cache")
        xdg_state = _private_directory(root / "xdg-state")
        opencode_data_home = _private_directory(root / "opencode-data")
        opencode_auth_directory = _private_directory(opencode_data_home / "opencode")
        mcp_xdg_data_home = _private_directory(root / "mcp-xdg-data")
        if agui_restart:
            mcp_barrier_directory = _private_directory(root / "mcp-response-barrier")
        workdir = root / "work"
        workdir.mkdir(mode=0o700)
        _initialize_private_git_repository(workdir)
        if agui_restart and http_tool_offer_nonce is not None:
            http_tool_offer_directory = opencode_http_tool_offer_observer.install_project_plugin(
                working_directory=workdir,
                source_path=Path(__file__).with_name("opencode_http_tool_offer_observer.js"),
                nonce=http_tool_offer_nonce,
            )
        source_auth = _source_auth_path(original_home=original_home, environment=dict(os.environ))
        source_credential_db = _source_credential_db_path(
            original_home=original_home,
            environment=dict(os.environ),
        )
        source_auth_available = source_auth.is_file() and not source_auth.is_symlink()
        source_credential_db_available = (
            source_credential_db.is_file() and not source_credential_db.is_symlink()
        )
        if not source_auth_available and not source_credential_db_available:
            return remember_receipt(
                {
                    "provider": "opencode_cli",
                    "result": "provider_auth_unavailable",
                    "phase": phase,
                }
            )
        private_auth: Path | None = None
        if source_auth_available:
            private_auth = root / "staged-auth" / "opencode" / "auth.json"
            source_auth_before = _stage_auth_file(
                source_auth,
                private_auth,
            )
            source_operator_state_before[source_auth] = source_auth_before
        if source_credential_db_available:
            for state_path in (
                source_credential_db,
                Path(str(source_credential_db) + "-wal"),
                Path(str(source_credential_db) + "-shm"),
            ):
                state_identity = _credential_state_stat(state_path)
                if state_identity is not None:
                    source_operator_state_before[state_path] = state_identity
            from cli_agent_orchestrator.providers.opencode_cli import _copy_private_state_bundle

            _copy_private_state_bundle(source_credential_db, opencode_auth_directory)
        if private_auth is not None:
            auth_link = opencode_auth_directory / "auth.json"
            # Keep the private staging tree free of links.  The v2 DB is
            # authoritative when present; this regular copy remains available
            # for legacy fallback.
            _stage_auth_file(private_auth, auth_link)
        os.environ.update(
            {
                "HOME": str(private_home),
                "TMPDIR": str(tmp_dir),
                "TERM": "xterm-256color",
                "TMUX_TMPDIR": str(tmux_tmpdir),
                "PYTHONPATH": common._source_pythonpath(),
                "CAO_TERMINAL_BACKEND": "tmux",
                "CAO_HOME_DIR": str(cao_home),
                "CAO_DATABASE_CONFIG_FILE": str(cao_home / "database-config.json"),
                "CAO_API_HOST": "127.0.0.1",
                "CAO_AUTH_MODE": "off",
                "OPENCODE_CONFIG": str(root / "opencode-config" / "opencode.json"),
                "OPENCODE_CONFIG_DIR": str(root / "opencode-config"),
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "XDG_CONFIG_HOME": str(xdg_config),
                "XDG_DATA_HOME": str(opencode_data_home),
                "XDG_CACHE_HOME": str(xdg_cache),
                "XDG_STATE_HOME": str(xdg_state),
            }
        )
        for key in tuple(os.environ):
            if key.endswith("_API_KEY") or key in {
                "CODEX_HOME",
                "CAO_PRIVATE_PROFILE_HOME",
                "CAO_AUTH_LOCAL_TOKEN",
                "CAO_AUTH_LOCAL_TOKEN_FILE",
                "CAO_AUTH_ISSUER",
                "CAO_AUTH_JWKS_URI",
                "CAO_AUTH_AUDIENCE",
            }:
                os.environ.pop(key, None)
        os.environ.pop("TMUX", None)
        from test.postgres.fixtures import isolated_live_main_target

        database_context = isolated_live_main_target(cao_home)
        target = database_context.__enter__()
        database_context_entered = True

        import httpx
        import uvicorn
        from fastapi import FastAPI

        from cli_agent_orchestrator import constants
        from cli_agent_orchestrator.api.interactions import router
        from cli_agent_orchestrator.clients import database

        database_module = database
        from cli_agent_orchestrator.clients.interaction_repository import (
            InteractionRepository,
        )
        from cli_agent_orchestrator.providers.opencode_cli import (
            resolve_opencode_runtime,
        )
        from cli_agent_orchestrator.security.auth import (
            SCOPE_ADMIN,
            SCOPE_READ,
            SCOPE_WRITE,
            _verified_principal,
            get_current_principal,
            local_operator_principal,
        )
        from cli_agent_orchestrator.services import fifo_reader, terminal_service

        terminal_service_module = terminal_service
        import cli_agent_orchestrator.services.interactions.service as interaction_service_module
        from cli_agent_orchestrator.backends import registry as backend_registry
        from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
        from cli_agent_orchestrator.clients.tmux import TmuxClient
        from cli_agent_orchestrator.services.interactions.service import (
            InteractionService,
        )
        from cli_agent_orchestrator.utils import agent_profiles, opencode_config, opencode_v2

        # All CAO and OpenCode paths are bound to this private run before the
        # production service/provider imports or creates a tmux process.
        opencode_config_dir = _private_directory(root / "opencode-config")
        agents_dir = _private_directory(opencode_config_dir / "agents")
        local_agent_store = _private_directory(cao_home / "agent-store")
        source_cao_home = Path(constants.CAO_HOME_DIR)
        module_bindings.extend(
            [
                (constants, "CAO_HOME_DIR", constants.CAO_HOME_DIR),
                (constants, "LOCAL_AGENT_STORE_DIR", constants.LOCAL_AGENT_STORE_DIR),
                (constants, "OPENCODE_CONFIG_DIR", constants.OPENCODE_CONFIG_DIR),
                (constants, "OPENCODE_AGENTS_DIR", constants.OPENCODE_AGENTS_DIR),
                (constants, "OPENCODE_CONFIG_FILE", constants.OPENCODE_CONFIG_FILE),
                (
                    agent_profiles,
                    "LOCAL_AGENT_STORE_DIR",
                    agent_profiles.LOCAL_AGENT_STORE_DIR,
                ),
                (
                    opencode_config,
                    "OPENCODE_CONFIG_DIR",
                    opencode_config.OPENCODE_CONFIG_DIR,
                ),
                (
                    opencode_config,
                    "OPENCODE_CONFIG_FILE",
                    opencode_config.OPENCODE_CONFIG_FILE,
                ),
            ]
        )
        bound_private_paths = _bind_private_constants_paths(
            constants_module=constants,
            source_cao_home=source_cao_home,
            cao_home=cao_home,
            module_bindings=module_bindings,
        )
        constants.CAO_HOME_DIR = cao_home
        constants.LOCAL_AGENT_STORE_DIR = local_agent_store
        constants.OPENCODE_CONFIG_DIR = opencode_config_dir
        constants.OPENCODE_AGENTS_DIR = agents_dir
        constants.OPENCODE_CONFIG_FILE = opencode_config_dir / "opencode.json"
        runtime_path_modules: list[Any] = [constants, terminal_service, fifo_reader]
        for module_name in (
            "cli_agent_orchestrator.backends.work_backend",
            "cli_agent_orchestrator.services.cleanup_service",
            "cli_agent_orchestrator.services.log_writer",
            "cli_agent_orchestrator.services.turn_recovery_service",
            "cli_agent_orchestrator.services.work_admission",
            "cli_agent_orchestrator.utils.logging",
        ):
            module = sys.modules.get(module_name)
            if module is not None:
                runtime_path_modules.append(module)
        _bind_private_runtime_paths(
            cao_home=cao_home,
            modules=runtime_path_modules,
            module_bindings=module_bindings,
        )
        for name in ("LOG_DIR", "TERMINAL_LOG_DIR", "FIFO_DIR"):
            value = getattr(constants, name, None)
            if isinstance(value, Path):
                bound_private_paths[name] = value
        agent_profiles.LOCAL_AGENT_STORE_DIR = local_agent_store
        opencode_config.OPENCODE_CONFIG_DIR = opencode_config_dir
        opencode_config.OPENCODE_CONFIG_FILE = constants.OPENCODE_CONFIG_FILE
        scope_attestation = _private_scope_attestation(
            root,
            cao_home,
            local_agent_store,
            opencode_config_dir,
            bound_cao_home=constants.CAO_HOME_DIR,
            bound_local_agent_store=constants.LOCAL_AGENT_STORE_DIR,
            bound_profile_store=agent_profiles.LOCAL_AGENT_STORE_DIR,
            environment=os.environ,
            bound_private_paths=bound_private_paths,
        )
        private_scope_verified = all(
            scope_attestation[key] for key in _SCOPE_ATTESTATION_KEYS if key != "bindings_restored"
        )
        if not private_scope_verified:
            raise RuntimeError("private_run_scope_invalid")
        import cli_agent_orchestrator.providers.opencode_cli as opencode_provider_module

        provider_class = opencode_provider_module.OpenCodeCliProvider
        original_provider_initialize = provider_class.initialize

        async def tracked_provider_initialize(provider_instance: Any) -> bool:
            nonlocal native_mcp_log
            mark_phase("native_initialize")
            try:
                return await original_provider_initialize(provider_instance)
            finally:
                provider_data_home = getattr(provider_instance, "_v2_data_home", None)
                if isinstance(provider_data_home, Path):
                    native_mcp_log = provider_data_home / "mcp-startup.log"

        module_bindings.append((provider_class, "initialize", original_provider_initialize))
        provider_class.initialize = tracked_provider_initialize
        production_overlay = opencode_provider_module.overlay_interaction_capability_file
        module_bindings.append(
            (
                opencode_provider_module,
                "overlay_interaction_capability_file",
                production_overlay,
            )
        )

        def overlay_native_mcp_for_harness(
            servers: dict[str, Any],
            capability_file: str | None,
            *,
            interaction_pull_flow_only: bool = False,
        ) -> dict[str, Any]:
            return _overlay_native_mcp(
                servers,
                capability_file,
                production_overlay,
                interaction_pull_flow_only=interaction_pull_flow_only,
            )

        opencode_provider_module.overlay_interaction_capability_file = (
            overlay_native_mcp_for_harness
        )
        production_v2_writer = opencode_v2.write_v2_configuration
        module_bindings.append((opencode_v2, "write_v2_configuration", production_v2_writer))

        def isolated_v2_writer(
            destination: Path,
            *,
            source_agents: Path,
            source_config: dict[str, Any],
            session_id: str,
            source_auth: Path | None = None,
            source_credential_db: Path | None = None,
            selected_model: str | None = None,
            selected_agent_source: tuple[str, str] | None = None,
        ) -> Any:
            return _write_v2_configuration_with_isolated_native_mcp(
                production_v2_writer,
                destination,
                source_agents=source_agents,
                source_config=source_config,
                session_id=session_id,
                source_auth=source_auth,
                source_credential_db=source_credential_db,
                selected_model=selected_model,
                selected_agent_source=selected_agent_source,
            )

        opencode_v2.write_v2_configuration = isolated_v2_writer

        module_bindings.extend(
            [
                (
                    opencode_provider_module,
                    "CAO_HOME_DIR",
                    opencode_provider_module.CAO_HOME_DIR,
                ),
                (
                    opencode_provider_module,
                    "OPENCODE_CONFIG_DIR",
                    opencode_provider_module.OPENCODE_CONFIG_DIR,
                ),
                (
                    opencode_provider_module,
                    "OPENCODE_AGENTS_DIR",
                    opencode_provider_module.OPENCODE_AGENTS_DIR,
                ),
                (
                    opencode_provider_module,
                    "OPENCODE_CONFIG_FILE",
                    opencode_provider_module.OPENCODE_CONFIG_FILE,
                ),
            ]
        )
        opencode_provider_module.CAO_HOME_DIR = cao_home
        opencode_provider_module.OPENCODE_CONFIG_DIR = opencode_config_dir
        opencode_provider_module.OPENCODE_AGENTS_DIR = agents_dir
        opencode_provider_module.OPENCODE_CONFIG_FILE = constants.OPENCODE_CONFIG_FILE

        terminal_id = "oc" + os.urandom(6).hex()
        session_name = "cao-live-" + os.urandom(6).hex()
        profile_name = "live-opencode-" + os.urandom(5).hex()
        operation_key = "live-opencode-" + os.urandom(12).hex()
        prompt = _opencode_prompt(operation_key)
        database.init_db()
        service = InteractionService(InteractionRepository(target))
        service.initialize()
        module_bindings.append(
            (
                interaction_service_module,
                "_DEFAULT_SERVICE",
                interaction_service_module._DEFAULT_SERVICE,
            )
        )
        interaction_service_module._DEFAULT_SERVICE = service
        ack_diagnostics = None if agui_restart else common._instrument_ack_identity_service(service)

        owner = (
            local_operator_principal()
            if agui_restart
            else _verified_principal(
                "https://live-interaction.invalid",
                "live-opencode-owner",
                [SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN],
                "jwt",
            )
        )
        if agui_restart:
            port = common._free_loopback_port()
            os.environ["CAO_API_PORT"] = str(port)
        else:
            app = FastAPI()
            app.state.interaction_service = service
            app.dependency_overrides[get_current_principal] = lambda: owner
            app.include_router(router)

            @app.get("/terminals/{requested_terminal_id}")
            def terminal_context(requested_terminal_id: str):
                current_terminal = terminal_holder.get("terminal")
                if requested_terminal_id != terminal_id or current_terminal is None:
                    return {"error": "not_found"}
                return {
                    "id": terminal_id,
                    "session_name": current_terminal.session_name,
                    "provider": "opencode_cli",
                    "allowed_tools": ["@cao-mcp-server"],
                }

            @app.get("/terminals/{requested_terminal_id}/working-directory")
            def working_directory(requested_terminal_id: str):
                if requested_terminal_id != terminal_id or "terminal" not in terminal_holder:
                    return {"error": "not_found"}
                return {"working_directory": str(workdir)}

            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            os.environ["CAO_API_PORT"] = str(port)
            server = uvicorn.Server(
                uvicorn.Config(app, log_level="critical", lifespan="off", access_log=False)
            )
            server_thread = threading.Thread(
                target=lambda: asyncio.run(server.serve(sockets=[listener])), daemon=True
            )
            server_thread.start()
            for _ in range(300):
                if server.started:
                    break
                time.sleep(0.01)
            if not server.started:
                return remember_receipt(
                    {
                        "provider": "opencode_cli",
                        "result": "api_start_failed",
                        "phase": phase,
                    }
                )

        phase = "version_probe"
        executable, runtime_environment = resolve_opencode_runtime()
        os.environ["PATH"] = runtime_environment.get("PATH", "/usr/bin:/bin")
        version_run = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            env={
                "PATH": runtime_environment.get("PATH", "/usr/bin:/bin"),
                "HOME": str(private_home),
            },
        )
        version = _version_text(version_run.stdout or version_run.stderr)
        if not version.startswith("opencode v2."):
            return remember_receipt(
                {
                    "provider": "opencode_cli",
                    "version": version,
                    "result": "unsupported_cli_version",
                    "phase": phase,
                }
            )

        if agui_restart:
            from test.live_provider.interaction_agui_restart import (
                ApiWorkerController,
                build_api_worker_spec,
                open_private_log,
            )

            for private_directory in (
                root / "home",
                root / "tmp",
                root / "tmux",
                root / "xdg-cache",
                root / "xdg-config",
            ):
                _private_directory(private_directory)
            worker_parent_environment = dict(os.environ)
            worker_parent_environment["PATH"] = runtime_environment.get("PATH", "/usr/bin:/bin")
            api_worker_spec = build_api_worker_spec(
                source_root=Path(__file__).resolve().parents[2],
                run_root=root,
                python_executable=sys.executable,
                port=port,
                parent_env=worker_parent_environment,
            )
            api_worker_stdout = open_private_log(root / "api-worker.stdout.log")
            api_worker_stderr = open_private_log(root / "api-worker.stderr.log")
            api_worker = ApiWorkerController(
                api_worker_spec,
                stdout=api_worker_stdout,
                stderr=api_worker_stderr,
            )
            phase = "api_worker_start"
            api_worker.start()

        agent_path = agents_dir / f"{profile_name}.md"
        agent_path.write_text(_agent_source(profile_name), encoding="utf-8")
        agent_path.chmod(0o600)
        common._install_profile(root, profile_name, "opencode_cli", port, terminal_id)
        mcp_child_home = _private_directory(root / "mcp-home")
        if agui_restart:
            mcp_protocol_observation = root / "mcp-protocol-observations.jsonl"
            mcp_protocol_nonce = os.urandom(16).hex()
        source_server = _native_mcp_server(
            cao_home=cao_home,
            mcp_home=mcp_child_home,
            mcp_xdg_data_home=mcp_xdg_data_home,
            tmp_dir=tmp_dir,
            api_port=port,
            terminal_id=terminal_id,
            install_restart_barrier=agui_restart,
            protocol_observation_path=mcp_protocol_observation,
            protocol_observation_nonce=mcp_protocol_nonce,
        )
        opencode_config.write_config(
            {
                "$schema": "https://opencode.ai/config.json",
                "mcp": {
                    "timeout": NATIVE_MCP_TIMEOUT,
                    "servers": {"cao-mcp-server": source_server},
                },
            }
        )
        constants.OPENCODE_CONFIG_FILE.chmod(0o600)
        backend_module_bindings = [
            (backend_registry, "_backend", backend_registry._backend),
        ]
        module_bindings.extend(backend_module_bindings)
        private_cli_environment = _private_cli_environment(
            root=root,
            cao_home=cao_home,
            config_dir=opencode_config_dir,
            data_home=opencode_data_home,
            api_port=port,
            source_pythonpath=common._source_pythonpath(),
            path_value=runtime_environment.get("PATH", "/usr/bin:/bin"),
            tmux_tmpdir=tmux_tmpdir,
            http_tool_offer_nonce=http_tool_offer_nonce,
        )
        _replace_process_environment(private_cli_environment)
        tmux_client = TmuxClient()
        tmux_socket_path = _pin_tmux_client_to_private_server(tmux_client, tmux_tmpdir)
        tmux_cleanup_attempted = True
        tmux_server_identity, tmux_socket_identity = _start_and_pin_private_tmux_server(
            tmux_client,
            tmux_socket_path,
        )
        tmux_owned_processes = _snapshot_and_recheck_private_tmux_server(
            tmux_server_identity,
            tmux_socket_identity,
            tmux_socket_path,
            terminal_id=terminal_id,
        )
        tmux_process_inventory_complete = True
        backend_registry.set_backend(TmuxBackend(client=tmux_client))
        database.init_db()

        original_default_service = interaction_service_module._DEFAULT_SERVICE
        if original_default_service is not service:
            interaction_service_module._DEFAULT_SERVICE = service

        if agui_restart:
            if mcp_barrier_directory is None:
                raise RuntimeError("mcp_restart_barrier_unavailable")
        started = time.monotonic()
        terminal_service_module = terminal_service
        terminal, turn_sequence = asyncio.run(
            _create_and_send_native_terminal(
                terminal_service,
                terminal_id=terminal_id,
                profile_name=profile_name,
                session_name=session_name,
                working_directory=workdir,
                model=selected_model,
                creation_principal=owner,
                prompt=prompt,
                on_created=lambda created: terminal_holder.__setitem__("terminal", created),
                on_phase=mark_phase,
            )
        )
        phase = "post_send"
        terminal_service_module = terminal_service
        provider_manager = terminal_service.provider_manager
        provider_instance = provider_manager.get_provider(terminal_id)
        from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider

        if not isinstance(provider_instance, OpenCodeCliProvider):
            raise RuntimeError("production_opencode_provider_not_registered")
        if provider_instance._cli_major != 2 or provider_instance._model != selected_model:
            raise RuntimeError("production_opencode_profile_mismatch")
        provider_data_home = getattr(provider_instance, "_v2_data_home", None)
        provider_config_dir = getattr(provider_instance, "_v2_config_dir", None)
        native_mcp_log = (
            provider_data_home / "mcp-startup.log" if isinstance(provider_data_home, Path) else None
        )
        if not isinstance(provider_config_dir, Path):
            raise RuntimeError("native_opencode_config_unavailable")
        generated = json.loads((provider_config_dir / "opencode.json").read_text(encoding="utf-8"))
        permissions = generated["agents"][profile_name]["permissions"]
        generated_mcp_server = generated["mcp"]["servers"].get("cao-mcp-server")
        if (
            not _exact_interaction_permissions(permissions)
            or not isinstance(generated_mcp_server, dict)
            or generated_mcp_server.get("codemode") is not False
        ):
            agent_tool_scope_exact = False
            shape_error = "agent_tool_scope_mismatch"
        else:
            agent_tool_scope_exact = True
            shape_error = None
        phase = "interaction_wait"
        if deadline is None:
            if agui_restart:
                raise RuntimeError("agui_restart_deadline_not_published")
            deadline = time.monotonic() + RUN_BUDGET_SECONDS
        answers_sent: set[str] = set()
        no_tool_probe_after = time.monotonic() + NO_TOOL_OBSERVATION_GRACE_SECONDS
        last_no_tool_probe = 0.0
        while time.monotonic() < deadline and shape_error is None:
            with service.repository.read_connection() as connection:
                rows = connection.execute(
                    "SELECT q.question_id,q.version,q.group_id,q.terminal_id,q.incarnation_id,"
                    "q.client_key,q.kind,q.options_json,q.required,q.lifecycle,d.state "
                    "FROM interaction_questions q "
                    "LEFT JOIN interaction_answers a ON a.question_id=q.question_id "
                    "LEFT JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                    "WHERE q.terminal_id=%s AND q.source_kind='explicit' "
                    "ORDER BY q.created_at,q.question_id",
                    (terminal_id,),
                ).fetchall()
            if rows:
                by_key = {row["client_key"]: row for row in rows}
                if len(rows) != len(common.QUESTION_SPECS) or set(by_key) != set(
                    common.HUMAN_ANSWERS
                ):
                    shape_error = "unexpected_question_shape"
                    break
                group_ids = {row["group_id"] for row in rows}
                if len(group_ids) != 1:
                    shape_error = "unexpected_question_group"
                    break
                group_id = next(iter(group_ids))
                if not isinstance(group_id, str) or not group_id:
                    shape_error = "unexpected_question_group"
                    break
                expected_agui_group_id = group_id
                if agui_restart:
                    incarnation_id = getattr(terminal, "session_incarnation_id", None)
                    if not isinstance(incarnation_id, str) or not incarnation_id:
                        shape_error = "agui_restart_identity_changed"
                        break
                    current_agui_questions = {
                        row["question_id"]: {
                            "group_id": row["group_id"],
                            "version": int(row["version"]),
                            "terminal_id": row["terminal_id"],
                            "incarnation_id": row["incarnation_id"],
                        }
                        for row in rows
                    }
                    if not expected_agui_questions:
                        expected_agui_questions = current_agui_questions
                    elif not _question_ledger_identity_matches(rows, expected_agui_questions):
                        shape_error = "agui_restart_identity_changed"
                        break
                    if not agui_restart_completed:
                        if any(
                            row["terminal_id"] != terminal_id
                            or row["incarnation_id"] != incarnation_id
                            or row["lifecycle"] != "open"
                            or row["state"] is not None
                            for row in rows
                        ) or not _question_ledger_matches(
                            rows,
                            expected_agui_questions,
                            lifecycle="open",
                            delivery_state=None,
                        ):
                            shape_error = "agui_restart_identity_changed"
                            break
                        from test.live_provider.interaction_agui_restart import (
                            snapshot_identity_map,
                        )
                        from test.live_provider.interaction_opencode_mcp_gate import (
                            barrier_reached,
                            release_barrier,
                        )

                        if (
                            api_worker is None
                            or api_worker_spec is None
                            or mcp_barrier_directory is None
                            or tmux_socket_path is None
                            or tmux_server_identity is None
                        ):
                            raise RuntimeError("api_worker_or_barrier_not_configured")
                        if not barrier_reached(mcp_barrier_directory):
                            time.sleep(min(0.08, max(0.0, deadline - time.monotonic())))
                            continue
                        before_identity, after_identity = (
                            _restart_api_worker_with_pending_questions(
                                api_worker=api_worker,
                                api_worker_spec=api_worker_spec,
                                expected_questions=expected_agui_questions,
                                forbidden_content=_forbidden_interaction_content(),
                                terminal_id=terminal_id,
                                tmux_socket_path=tmux_socket_path,
                                tmux_server_identity=tmux_server_identity,
                                database_config_path=cao_home / "database-config.json",
                            )
                        )
                        agui_before_identity = before_identity
                        api_worker_restarts += 1
                        agui_snapshot_count += 2
                        agui_identity_stable = before_identity == after_identity
                        agui_projection_redacted = True
                        native_provider_processes_stable = True
                        database_target_stable = api_worker_spec.env.get(
                            "CAO_DATABASE_CONFIG_FILE"
                        ) == str(cao_home / "database-config.json")
                        if not agui_identity_stable or database_target_stable is not True:
                            shape_error = "agui_restart_identity_changed"
                            break
                        # The replacement worker already proved the same
                        # pending snapshot through its fresh HTTP process. A
                        # second repeatable-read connection in this runner can
                        # briefly start on the old PostgreSQL snapshot while
                        # the terminated worker's connection is being closed.
                        # Re-open a bounded number of snapshots before calling
                        # this a ledger identity failure; a persistent
                        # lifecycle/version change still fails closed.
                        agui_ledger_identity_stable = False
                        for ledger_attempt in range(5):
                            with service.repository.read_connection() as connection:
                                pending_rows_after = connection.execute(
                                    "SELECT q.question_id,q.version,q.group_id,q.terminal_id,"
                                    "q.incarnation_id,q.lifecycle,d.state "
                                    "FROM interaction_questions q "
                                    "LEFT JOIN interaction_answers a ON a.question_id=q.question_id "
                                    "LEFT JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                                    "WHERE q.terminal_id=%s AND q.source_kind='explicit' "
                                    "ORDER BY q.created_at,q.question_id",
                                    (terminal_id,),
                                ).fetchall()
                            agui_ledger_identity_stable = _question_ledger_matches(
                                pending_rows_after,
                                expected_agui_questions,
                                lifecycle="open",
                                delivery_state=None,
                            )
                            if agui_ledger_identity_stable or ledger_attempt == 4:
                                break
                            time.sleep(0.05)
                        if not agui_ledger_identity_stable:
                            shape_error = "agui_restart_identity_changed"
                            break
                        release_barrier(mcp_barrier_directory)
                        mcp_barrier_resolved = True
                        agui_restart_completed = True
                for key, response in common.HUMAN_ANSWERS.items():
                    if key in answers_sent:
                        continue
                    row = by_key[key]
                    if not row["required"] or row["kind"] != common.QUESTION_KINDS[key]:
                        shape_error = "unexpected_question_contract"
                        break
                    if response["kind"] == "options":
                        offered = {
                            option.get("id")
                            for option in json.loads(row["options_json"])
                            if isinstance(option, dict)
                        }
                        if not set(response["option_ids"]) <= offered:
                            shape_error = "fixed_choice_not_offered"
                            break
                    answer = httpx.post(
                        f"http://127.0.0.1:{port}/interactions/{row['question_id']}/answers",
                        headers={
                            "If-Match": f"\"{row['version']}\"",
                            "Idempotency-Key": f"human-opencode-{key}-fixed",
                        },
                        json={"expected_version": row["version"], "response": response},
                        timeout=5,
                        trust_env=False,
                    )
                    if answer.status_code not in {200, 201}:
                        shape_error = f"human_answer_http_{answer.status_code}"
                        break
                    answers_sent.add(key)
                if shape_error:
                    break
                if answers_sent == set(common.HUMAN_ANSWERS):
                    with service.repository.read_connection() as connection:
                        current_states = connection.execute(
                            "SELECT d.state FROM interaction_questions q "
                            "LEFT JOIN interaction_answers a ON a.question_id=q.question_id "
                            "LEFT JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                            "WHERE q.terminal_id=%s AND q.source_kind='explicit' "
                            "ORDER BY q.created_at,q.question_id",
                            (terminal_id,),
                        ).fetchall()
                    if current_states and all(
                        row["state"] == "agent_acknowledged" for row in current_states
                    ):
                        break
            if not rows and time.monotonic() >= no_tool_probe_after:
                now = time.monotonic()
                if now - last_no_tool_probe >= 0.5:
                    last_no_tool_probe = now
                    try:
                        probe_output = backend_registry.get_backend().get_history(
                            terminal.session_name, terminal.name, tail_lines=400
                        )
                    except Exception:
                        probe_output = ""
                    native_turn_stage = _native_turn_stage(provider_instance, probe_output)
                    # Once the prompt disappears, an idle frame is ambiguous:
                    # the provider may still be completing a slow turn. Wait
                    # for positive completion/error evidence or the deadline.
                    if native_turn_stage in {"completed_turn", "error"}:
                        break
            time.sleep(0.08)
        phase = "receipt_collection"
        timed_out = shape_error is None and time.monotonic() >= deadline
        backend = backend_registry.get_backend()
        try:
            terminal_output = backend.get_history(
                terminal.session_name, terminal.name, tail_lines=400
            )
        except Exception:
            terminal_output = ""
        native_turn_stage = _native_turn_stage(provider_instance, terminal_output)
        native_turn_observed = native_turn_stage in {"running", "completed_turn"}
        native_tui_stage = _native_tui_stage(provider_instance, terminal_output)
        if agui_restart:
            from cli_agent_orchestrator.models.terminal import TerminalStatus

            if terminal_service.provider_manager.get_provider(terminal_id) is provider_instance:
                native_provider_final_state = _provider_final_state(
                    provider_instance,
                    terminal_output,
                )
            else:
                native_provider_final_state = TerminalStatus.UNKNOWN.name
            native_provider_final_state_observed_at = time.monotonic()
            while (
                native_provider_final_state == TerminalStatus.PROCESSING.name
                and time.monotonic() < deadline
            ):
                time.sleep(min(0.08, max(0.0, deadline - time.monotonic())))
                try:
                    terminal_output = backend.get_history(
                        terminal.session_name,
                        terminal.name,
                        tail_lines=400,
                    )
                except Exception:
                    terminal_output = ""
                if (
                    terminal_service.provider_manager.get_provider(terminal_id)
                    is not provider_instance
                ):
                    native_provider_final_state = TerminalStatus.UNKNOWN.name
                    native_provider_final_state_observed_at = time.monotonic()
                    break
                native_provider_final_state = _provider_final_state(
                    provider_instance,
                    terminal_output,
                )
                native_provider_final_state_observed_at = time.monotonic()
            if shape_error is None and not _provider_final_state_is_complete(
                native_provider_final_state
            ):
                if (
                    native_provider_final_state == TerminalStatus.PROCESSING.name
                    and time.monotonic() >= deadline
                ):
                    timed_out = True
                else:
                    shape_error = "native_provider_not_complete"
        native_mcp_ready, native_mcp_log_status = _native_mcp_log_diagnostics(native_mcp_log)

        with service.repository.read_connection() as connection:
            rows = connection.execute(
                "SELECT q.question_id,q.version,q.group_id,q.terminal_id,q.incarnation_id,"
                "q.client_key,q.kind,q.lifecycle,d.state "
                "FROM interaction_questions q "
                "LEFT JOIN interaction_answers a ON a.question_id=q.question_id "
                "LEFT JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                "WHERE q.terminal_id=%s AND q.source_kind='explicit' ORDER BY q.created_at,q.question_id",
                (terminal_id,),
            ).fetchall()
        if agui_restart:
            if agui_restart_completed and api_worker_spec is not None:
                final_snapshot = common._read_agui_snapshot(
                    api_worker_spec.base_url,
                    forbidden_content=_forbidden_interaction_content(),
                )
                agui_snapshot_count += 1
                (
                    _final_identity,
                    agui_final_acknowledged,
                    final_identity_stable,
                    final_ledger_stable,
                ) = _final_agui_snapshot_checks(
                    final_snapshot,
                    expected_agui_questions,
                    agui_before_identity,
                    rows,
                )
                agui_identity_stable = agui_identity_stable is True and final_identity_stable
                agui_projection_redacted = agui_projection_redacted is True
                agui_ledger_identity_stable = (
                    agui_ledger_identity_stable is True and final_ledger_stable
                )
            if expected_agui_group_id is not None and expected_agui_questions:
                agui_observation_diagnostics = common._agui_observation_diagnostics(
                    root / "interaction-observations.jsonl",
                    expected_agui_group_id,
                    expected_agui_questions,
                )
            if isinstance(agui_observation_diagnostics, dict):
                checks = agui_observation_diagnostics.get("ack_checks", [])
                agui_exact_ack_observed = any(
                    isinstance(check, dict)
                    and check.get("group_matches_created") is True
                    and check.get("group_matches_waited") is True
                    and check.get("question_versions_match_created") is True
                    and check.get("question_versions_match_returned") is True
                    and check.get("receipt_hashes_match_returned") is True
                    and check.get("ack_succeeded") is True
                    and check.get("ack_count") == len(common.HUMAN_ANSWERS)
                    for check in checks
                )
                agui_observation_complete = (
                    agui_observation_diagnostics.get("create_group_seen") is True
                    and agui_observation_diagnostics.get("created_group_matches_database") is True
                    and agui_observation_diagnostics.get("created_questions_match_database") is True
                    and agui_observation_diagnostics.get("ready_wait_matches_database") is True
                )
        success_conditions = (
            len(rows) == len(common.QUESTION_SPECS)
            and {row["client_key"] for row in rows} == set(common.HUMAN_ANSWERS)
            and all(row["state"] == "agent_acknowledged" for row in rows)
            and answers_sent == set(common.HUMAN_ANSWERS)
            and turn_sequence >= 1
            and native_mcp_ready
            and (
                not agui_restart
                or (
                    agui_restart_completed
                    and api_worker_restarts == 1
                    and agui_snapshot_count == 3
                    and agui_identity_stable is True
                    and agui_projection_redacted is True
                    and agui_final_acknowledged is True
                    and agui_ledger_identity_stable is True
                    and agui_exact_ack_observed is True
                    and agui_observation_complete is True
                    and native_provider_processes_stable is True
                    and database_target_stable is True
                    and _provider_final_state_is_complete(native_provider_final_state or "")
                )
            )
        )
        timed_out = timed_out or (shape_error is None and time.monotonic() >= deadline)
        acknowledged = _completion_allowed_before_deadline(
            success_conditions=success_conditions,
            timed_out=timed_out,
            deadline=deadline,
            observed_at=time.monotonic(),
            status_observed_at=(native_provider_final_state_observed_at if agui_restart else None),
        )
        if acknowledged:
            result_code = "completed_and_acknowledged"
        elif shape_error:
            result_code = shape_error
        elif rows and timed_out:
            result_code = "timed_out_after_question"
        elif rows:
            result_code = "question_created_but_not_acknowledged"
        else:
            result_code = _classify_no_tool_result(
                native_turn_stage=native_turn_stage,
                question_count=len(rows),
                timed_out=timed_out,
            )
        failure_diagnostics = common._failure_diagnostic_flags(terminal_output)
        if result_code == "completed_and_acknowledged":
            # A successful interaction has positive evidence for every MCP
            # phase. Do not carry a stale error frame from an earlier TUI
            # render into the public receipt as if the accepted turn failed.
            failure_diagnostics = {name: False for name in common._FAILURE_DIAGNOSTIC_NAMES}
        operator_auth_unchanged = operator_state_unchanged()
        return remember_receipt(
            {
                "provider": "opencode_cli",
                "version": version,
                "result": result_code,
                "phase": "complete" if acknowledged else phase,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "execution_path": "terminal_service.create_terminal+send_input",
                "native_mini_initialized": True,
                "native_mcp_ready": native_mcp_ready,
                "agent_tool_scope_exact": agent_tool_scope_exact,
                "native_tui_stage": native_tui_stage,
                "native_turn_stage": native_turn_stage,
                "native_turn_observed": native_turn_observed,
                "native_mcp_log_status": native_mcp_log_status,
                "turn_sequence": turn_sequence,
                "question_count": len(rows),
                "question_kinds": sorted(row["kind"] for row in rows),
                "question_lifecycles": sorted(row["lifecycle"] for row in rows),
                "human_answers_posted": len(answers_sent),
                "delivery_states": [row["state"] for row in rows],
                "failure_diagnostics": failure_diagnostics,
                "ack_identity_diagnostics": (
                    agui_observation_diagnostics if agui_restart else ack_diagnostics()
                ),
                "agui_restart_enabled": agui_restart,
                "api_worker_restarts": api_worker_restarts,
                "agui_snapshot_count": agui_snapshot_count,
                "agui_identity_stable": agui_identity_stable,
                "agui_projection_redacted": agui_projection_redacted,
                "agui_final_acknowledged": agui_final_acknowledged,
                "agui_ledger_identity_stable": agui_ledger_identity_stable,
                "agui_observation_complete": agui_observation_complete,
                "agui_exact_ack_observed": agui_exact_ack_observed,
                "native_provider_processes_stable": native_provider_processes_stable,
                "database_target_stable": database_target_stable,
                "native_provider_final_state": native_provider_final_state,
                "native_provider_status_before_deadline": (
                    native_provider_final_state_observed_at is not None
                    and native_provider_final_state_observed_at < deadline
                    if agui_restart
                    else None
                ),
                "native_provider_state_source": (
                    "tmux_live_pane_via_production_provider" if agui_restart else None
                ),
                "native_provider_exit_status": None,
                "native_provider_exit_status_observed": False,
                "provider_home_isolated": os.environ.get("HOME") == str(private_home),
                "host_filesystem_read_only": False,
                "raw_provider_output_printed": False,
                "operator_auth_unchanged": operator_auth_unchanged,
                "real_git_repository": (workdir / ".git").is_dir(),
            }
        )
    except FileNotFoundError as exc:
        receipt = {
            "provider": "opencode_cli",
            "result": "cli_unavailable",
            "phase": phase,
            "error_type": type(exc).__name__,
        }
        if native_mcp_log is not None:
            native_mcp_ready, native_mcp_log_status = _native_mcp_log_diagnostics(native_mcp_log)
            receipt.update(
                {
                    "native_mcp_ready": native_mcp_ready,
                    "native_mcp_log_status": native_mcp_log_status,
                }
            )
        return remember_receipt(receipt)
    except Exception as exc:
        receipt = {
            "provider": "opencode_cli",
            "result": "harness_error",
            "phase": phase,
            "error_type": type(exc).__name__,
        }
        if native_mcp_log is not None:
            native_mcp_ready, native_mcp_log_status = _native_mcp_log_diagnostics(native_mcp_log)
            receipt.update(
                {
                    "native_mcp_ready": native_mcp_ready,
                    "native_mcp_log_status": native_mcp_log_status,
                }
            )
        return remember_receipt(receipt)
    finally:
        cleanup_failures: set[str] = set()
        terminal_cleanup_ok = True
        if (
            agui_restart
            and not mcp_barrier_resolved
            and mcp_barrier_directory is not None
            and terminal_holder.get("terminal") is not None
        ):
            from test.live_provider.interaction_opencode_mcp_gate import release_barrier

            barrier_aborted = common._cleanup_action(
                cleanup_failures,
                "mcp_restart_barrier_abort_failed",
                lambda: release_barrier(mcp_barrier_directory, abort=True),
            )
            if barrier_aborted:
                mcp_barrier_resolved = True
        if terminal_holder.get("terminal") is not None and terminal_service_module is not None:
            terminal_cleanup_ok = _cleanup_native_terminal(
                terminal_service_module,
                terminal_id,
                cleanup_failures,
            )
        tmux_cleanup_ok = True
        if tmux_cleanup_attempted and tmux_client is not None and tmux_tmpdir is not None:
            if not tmux_process_inventory_complete:
                cleanup_failures.add("tmux_process_inventory_unavailable")
            if (
                not tmux_process_inventory_complete
                and tmux_server_identity is not None
                and tmux_socket_identity is not None
            ):
                tmux_cleanup_ok = _cleanup_failed_tmux_bootstrap(
                    tmux_client,
                    tmux_tmpdir,
                    cleanup_failures,
                    server_identity=tmux_server_identity,
                    socket_identity=tmux_socket_identity,
                )
            else:
                tmux_cleanup_ok = _cleanup_private_tmux_server(
                    tmux_client,
                    tmux_tmpdir,
                    cleanup_failures,
                    terminal_id=terminal_id or "",
                    server_identity=tmux_server_identity,
                    owned_processes=tmux_owned_processes,
                    socket_identity=tmux_socket_identity,
                )
        if server is not None:
            common._cleanup_action(
                cleanup_failures,
                "api_server_not_stopped",
                lambda: setattr(server, "should_exit", True),
            )
        if server_thread is not None:

            def join_server() -> None:
                server_thread.join(timeout=3)
                if server_thread.is_alive():
                    raise RuntimeError("api_server_still_running")

            common._cleanup_action(cleanup_failures, "api_server_not_stopped", join_server)
        if listener is not None:
            common._cleanup_action(cleanup_failures, "api_listener_close_failed", listener.close)
        api_worker_stopped = True
        if api_worker is not None:
            api_worker_stopped = common._cleanup_action(
                cleanup_failures,
                "api_worker_not_stopped",
                api_worker.stop,
            )
            if api_worker.process is not None:
                api_worker_stopped = False
                cleanup_failures.add("api_worker_not_stopped")
        for api_worker_log in (api_worker_stdout, api_worker_stderr):
            if api_worker_log is not None:
                common._cleanup_action(
                    cleanup_failures,
                    "api_worker_log_close_failed",
                    api_worker_log.close,
                )
        process_stopped = (
            terminal_cleanup_ok
            and tmux_cleanup_ok
            and api_worker_stopped
            and (not tmux_cleanup_attempted or tmux_process_inventory_complete)
        )
        if (
            "api_server_not_stopped" in cleanup_failures
            or "api_worker_not_stopped" in cleanup_failures
        ):
            process_stopped = False
        if database_module is not None:
            common._cleanup_action(
                cleanup_failures,
                "database_engine_dispose_failed",
                database_module.engine.dispose,
            )
        if database_context is not None:
            if database_context_entered:
                common._cleanup_action(
                    cleanup_failures,
                    "database_target_close_failed",
                    lambda: database_context.__exit__(None, None, None),
                )
        if receipt_holder and mcp_protocol_observation is not None:
            try:
                receipt_holder[0].update(
                    _mcp_observer_receipt_fields(
                        observation_path=mcp_protocol_observation,
                        expected_nonce=mcp_protocol_nonce,
                        agent_tool_scope_exact=agent_tool_scope_exact,
                    )
                )
            except Exception:
                receipt_holder[0].update(
                    _mcp_observer_receipt_fields(
                        observation_path=None,
                        expected_nonce=None,
                        agent_tool_scope_exact=agent_tool_scope_exact,
                    )
                )
        if receipt_holder and agui_restart:
            try:
                offer_summary = (
                    opencode_http_tool_offer_observer.read_observation(
                        http_tool_offer_directory,
                        expected_nonce=http_tool_offer_nonce,
                    )
                    if http_tool_offer_directory is not None
                    else opencode_http_tool_offer_observer._unobserved()
                )
            except Exception:
                offer_summary = opencode_http_tool_offer_observer._unobserved(status="incomplete")
            receipt_holder[0]["opencode_http_tool_offer"] = offer_summary
        for module, name, value in reversed(module_bindings):
            common._cleanup_action(
                cleanup_failures,
                "module_binding_restore_failed",
                lambda module=module, name=name, value=value: setattr(module, name, value),
            )
        bindings_restored = all(
            getattr(module, name, object()) == value for module, name, value in module_bindings
        )
        if scope_attestation is not None:
            scope_attestation["bindings_restored"] = bindings_restored
            if receipt_holder:
                receipt_holder[0]["scope_attestation"] = dict(scope_attestation)
        common._cleanup_action(
            cleanup_failures,
            "environment_restore_failed",
            lambda: _restore_process_environment(original_environment),
        )
        operator_auth_unchanged: bool | None = None
        if source_operator_state_before:
            operator_auth_unchanged = operator_state_unchanged()
            if not operator_auth_unchanged:
                cleanup_failures.add("operator_auth_changed")
        temporary_files_removed = common._remove_private_run_root(
            root, process_stopped=process_stopped, failures=cleanup_failures
        )
        if receipt_holder:
            receipt_holder[0].update(
                {
                    "temporary_files_removed": temporary_files_removed,
                    "cleanup_status": ("complete" if not cleanup_failures else "incomplete"),
                    "cleanup_failure_categories": sorted(cleanup_failures),
                }
            )
            if operator_auth_unchanged is not None:
                receipt_holder[0]["operator_auth_unchanged"] = operator_auth_unchanged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default=None,
        help="explicit OpenCode provider/model id; no fallback is used",
    )
    parser.add_argument(
        "--run-model",
        action="store_true",
        help="explicitly authorize the one bounded model journey",
    )
    args = parser.parse_args()
    catalog = None
    if args.run_model and args.model == common.OPENCODE_INTERACTION_MODEL:
        catalog = model_catalog.check_model(args.model)
        if catalog["eligible"] is not True:
            print(
                json.dumps(
                    {
                        "provider": "opencode_cli",
                        "result": "model_catalog_rejected",
                        "requested_model": common.OPENCODE_INTERACTION_MODEL,
                        "model_catalog": catalog,
                        "raw_provider_output_printed": False,
                    },
                    sort_keys=True,
                )
            )
            return 1
    run_result = run(model=args.model, run_model=args.run_model)
    if args.run_model and args.model == common.OPENCODE_INTERACTION_MODEL:
        # This exact literal comes from the harness invocation, not provider output.
        run_result["requested_model"] = common.OPENCODE_INTERACTION_MODEL
    result = common._safe_provider_receipt(run_result)
    if catalog is not None:
        result["model_catalog"] = catalog
    result["raw_provider_output_printed"] = False
    print(json.dumps(result, sort_keys=True))
    successful_journey = (
        result.get("result") == "completed_and_acknowledged"
        and common._provider_cleanup_succeeded(result, require_auth_unchanged=True)
        and common.validate_scope_attestation(result, "opencode_cli")
    )
    return 0 if successful_journey else 1


if __name__ == "__main__":
    raise SystemExit(main())
