"""Run one bounded interaction journey through an installed provider CLI.

This is an opt-in live acceptance harness, not a pytest test. It starts an
isolated loopback CAO interaction API and PostgreSQL ledger, builds the provider
command with CAO's real provider class, launches the installed CLI once, posts
fixed human answers through the public HTTP route, and verifies MCP return and
acknowledgement for that same invocation. All provider files and raw logs live
under a mode-0700 temporary directory. They are removed after successful
cleanup; if an owned process or resource cannot be stopped, the private run
directory is preserved and the returned receipt reports the failure.

Codex keeps its existing HOME/CODEX_HOME values. Bubblewrap mounts a private
Codex home containing copied auth/config files, then mounts the original
installed `packages` subtree back read-only so the standalone binary remains
available. The host filesystem stays read-only while this run's temporary
tree is writable. Per-invocation config overrides clear unrelated MCP entries
and constrain the CLI with its read-only sandbox, shell disabled, and web
search disabled.
Claude's config home is redirected to the private temp tree and its existing
credential file is copied there with mode 0600.

Run from the repository root with the project environment, for example:

    uv run python -m test.live_provider.interaction_cli_journey codex
    uv run python -m test.live_provider.interaction_cli_journey claude_code
    uv run python -m test.live_provider.interaction_cli_journey opencode_cli --agui-restart

Each provider invocation is bounded to 75 seconds. The actual-provider mode
uses the credentials already configured for that CLI, copied into the isolated
CLI home. It never prints prompt/answer bodies, auth material, raw CLI output,
MCP arguments, or capability tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, TextIO

ORIGINAL_HOME = Path.home()
PROVIDER_BUDGET_SECONDS = 75.0
CODEX_INTERACTION_MODEL = "gpt-6-luna"
CLAUDE_AGUI_RESTART_MODEL = "sonnet"
OPENCODE_INTERACTION_MODEL = "opencode-go/longcat-2.5-preview-free"
_CLAUDE_ARCHIVE_HELPER_SHA256 = "e736a481476db47a5c5c3af29763a38a597bba575422f8599bf6f3011b5f399b"


def _load_pinned_claude_archive_helper(path: Path, expected_sha256: str) -> ModuleType:
    """Execute only helper bytes that match the reviewed source pin."""
    path = Path(path)
    no_follow_flag = getattr(os, "O_NOFOLLOW", None)
    if not path.is_absolute() or not isinstance(no_follow_flag, int) or no_follow_flag == 0:
        raise RuntimeError("Claude archive helper source binding invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow_flag
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("Claude archive helper source binding invalid") from exc
    source_chunks: list[bytes] = []
    digest = hashlib.sha256()
    total_bytes = 0
    try:
        opened_info = os.fstat(descriptor)

        def file_identity(info: os.stat_result) -> tuple[int, ...]:
            return (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )

        if not stat.S_ISREG(opened_info.st_mode) or opened_info.st_size > 2 * 1024 * 1024:
            raise RuntimeError("Claude archive helper source binding invalid")
        while True:
            chunk = os.read(descriptor, 128 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > 2 * 1024 * 1024:
                raise RuntimeError("Claude archive helper source binding invalid")
            source_chunks.append(chunk)
            digest.update(chunk)
        final_info = os.fstat(descriptor)
        if (
            file_identity(opened_info) != file_identity(final_info)
            or digest.hexdigest() != expected_sha256
        ):
            raise RuntimeError("Claude archive helper source binding invalid")
    except OSError as exc:
        raise RuntimeError("Claude archive helper source binding invalid") from exc
    finally:
        os.close(descriptor)

    module = ModuleType("test.live_provider._caos_pinned_claude_auth_archive")
    module.__file__ = str(path)
    module.__package__ = "test.live_provider"
    source = b"".join(source_chunks)
    exec(compile(source, str(path), "exec", dont_inherit=True), module.__dict__)
    module.__dict__["__caos_source_sha256__"] = expected_sha256
    return module


_claude_auth_archive: ModuleType | None = None


def _get_claude_archive_helper() -> ModuleType:
    """Load the Claude-only helper on demand from its pinned source bytes."""
    global _claude_auth_archive
    if _claude_auth_archive is None:
        _claude_auth_archive = _load_pinned_claude_archive_helper(
            Path(__file__).parent / "claude_auth_archive.py",
            _CLAUDE_ARCHIVE_HELPER_SHA256,
        )
    return _claude_auth_archive


_PROVIDER_VERSION_PATTERNS = {
    "codex": re.compile(r"codex-cli [0-9]+\.[0-9]+\.[0-9]+"),
    "claude_code": re.compile(
        r"(?:Claude Code [0-9]+\.[0-9]+\.[0-9]+|[0-9]+\.[0-9]+\.[0-9]+ \(Claude Code\))"
    ),
    "opencode_cli": re.compile(r"opencode v2\.[0-9]+\.[0-9]+"),
}
_SAFE_PROVIDER_PHASES = frozenset(
    {
        "setup",
        "native_create",
        "native_initialize",
        "native_send_input",
        "post_send",
        "interaction_wait",
        "receipt_collection",
        "initialize",
        "version_probe",
        "provider_launch",
        "prepare",
        "spawn",
        "list_tools",
        "list_interactions",
        "create_interaction_probe",
        "list_interactions_after_probe",
        "complete",
        "api_worker_start",
        "agui_snapshot_before_restart",
        "agui_api_restart",
        "agui_snapshot_after_restart",
        "agui_final_snapshot",
    }
)
_SAFE_EXCEPTION_TYPES = frozenset(
    {
        "RuntimeError",
        "TimeoutError",
        "FileNotFoundError",
        "PermissionError",
        "OSError",
        "ValueError",
        "TypeError",
        "ConnectionError",
        "ConnectionRefusedError",
        "HTTPException",
        "OperationalError",
    }
)
_SAFE_CLEANUP_FAILURES = frozenset(
    {
        "provider_process_not_stopped",
        "api_server_not_stopped",
        "api_listener_close_failed",
        "database_engine_dispose_failed",
        "database_target_close_failed",
        "provider_cleanup_failed",
        "tmux_process_identity_unavailable",
        "tmux_process_inventory_unavailable",
        "tmux_socket_identity_mismatch",
        "tmux_socket_identity_unavailable",
        "module_binding_restore_failed",
        "environment_restore_failed",
        "private_files_preserved",
        "private_files_remove_failed",
        "claude_auth_archive_incomplete",
        "operator_auth_changed",
        "operator_auth_stat_failed",
    }
)
_SAFE_NATIVE_TUI_STAGES = frozenset({"idle", "running", "unknown"})
_SAFE_NATIVE_TURN_STAGES = frozenset(
    {
        "idle_no_turn",
        "prompt_pending_submission",
        "completed_turn",
        "running",
        "waiting_user_answer",
        "error",
        "unknown",
    }
)
_SAFE_NATIVE_MCP_LOG_STATUSES = frozenset(
    {"not_configured", "missing", "unreadable", "connected", "no_connected_marker"}
)
_SAFE_SCOPE_ATTESTATION_KEYS = frozenset(
    {
        "run_root_private",
        "cao_home_is_run_child",
        "agent_store_is_cao_child",
        "config_is_run_private",
        "bindings_active",
        "bindings_restored",
    }
)
QUESTION_SPECS = (
    {
        "client_key": "test-color",
        "kind": "text",
        "prompt": "What color should I use for this live test?",
        "required": True,
    },
    {
        "client_key": "test-shade",
        "kind": "single_choice",
        "prompt": "Choose the test shade.",
        "required": True,
        "options": [
            {"id": "sapphire", "label": "Sapphire"},
            {"id": "amber", "label": "Amber"},
        ],
    },
)
HUMAN_ANSWERS = {
    "test-color": {"kind": "text", "text": "blue"},
    "test-shade": {"kind": "options", "option_ids": ["sapphire"]},
}
QUESTION_KINDS = {question["client_key"]: question["kind"] for question in QUESTION_SPECS}
INTERACTION_TOOLS = (
    "cao_ask_user",
    "cao_wait_for_user_answers",
    "cao_acknowledge_user_answers",
    "cao_list_interactions",
    "cao_get_interaction",
    "cao_answer_interaction",
    "cao_escalate_interaction",
)
CLAUDE_ALLOWED_TOOLS = tuple(f"mcp__cao-mcp-server__{name}" for name in INTERACTION_TOOLS)


def _prompt(operation_key: str, *, recover_api_restart: bool = False) -> str:
    questions = [
        {
            "client_key": "test-color",
            "kind": "text",
            "prompt": "What color should I use for this live test?",
            "required": True,
        },
        {
            "client_key": "test-shade",
            "kind": "single_choice",
            "prompt": "Choose the test shade.",
            "required": True,
            "options": [
                {"id": "sapphire", "label": "Sapphire"},
                {"id": "amber", "label": "Amber"},
            ],
        },
    ]
    encoded_questions = json.dumps(questions, ensure_ascii=True, separators=(",", ":"))
    restart_instruction = (
        "The API process has restarted while the same questions are pending. Continue with the "
        "exact group_id returned by cao_ask_user; do not call cao_ask_user again. If "
        "cao_wait_for_user_answers returns an error with code unavailable, repeat that same wait "
        "for the exact group_id with wait_seconds=20 as needed until ready or a terminal state, "
        "but only within the existing 75-second provider-task budget. Stop and fail on any terminal "
        "state, any other error, or when the bound expires; never acknowledge without the ready "
        "answers and their exact receipts.\n\n"
        if recover_api_restart
        else ""
    )
    return f"""Bounded interaction-channel acceptance task. The only tools you may use are the seven CAO interaction MCP tools exposed to you. Do not call any other tool or perform any external action.

Call cao_ask_user exactly once with operation_key={operation_key!r}, the following exact questions value, and wait_for_required=true:
{encoded_questions}

{restart_instruction}After it returns, call cao_wait_for_user_answers for its exact group_id with wait_seconds=20. If state is pending, repeat that wait for the same group. When state is ready and both answers are returned, call cao_acknowledge_user_answers with that exact group_id and every returned question_id, version, and receipt. Never answer on behalf of the human. Finish with a short confirmation."""


def _private_root() -> Path:
    cache = ORIGINAL_HOME / ".cache" / "cao-live-provider"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    if cache.is_symlink():
        raise RuntimeError("unsafe_temp_root")
    os.chmod(cache, 0o700)
    root = Path(tempfile.mkdtemp(prefix="interaction-", dir=cache))
    root.chmod(0o700)
    return root


def _ensure_private_directory(path: Path) -> None:
    """Create or accept only a mode-0700 directory owned by this user."""
    try:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeError("unsafe_private_directory") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RuntimeError("unsafe_private_directory")


def _append_sanitized_evidence(result: dict[str, Any]) -> bool:
    cache = ORIGINAL_HOME / ".cache" / "cao-live-provider"
    if cache.is_symlink():
        return False
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(cache, 0o700)
    path = cache / "sanitized-evidence.jsonl"
    if path.is_symlink():
        return False
    fields = (
        "provider",
        "requested_model",
        "version",
        "result",
        "phase",
        "exit_code",
        "elapsed_seconds",
        "question_count",
        "question_kinds",
        "question_lifecycles",
        "human_answers_posted",
        "delivery_states",
        "agent_tool_scope_exact",
        "observed_interaction_tools",
        "mcp_observer_status",
        "mcp_observer_shutdown_observed",
        "private_scope_verified",
        "mcp_catalog_observation_status",
        "mcp_catalog_tool_names",
        "mcp_catalog_unexpected_tool_count",
        "mcp_catalog_observation_count",
        "mcp_tool_observation_status",
        "mcp_tool_unexpected_call_count",
        "sanitized_failure_signals",
        "provider_home_isolated",
        "host_filesystem_read_only",
        "temporary_files_removed",
        "native_mini_initialized",
        "native_mcp_ready",
        "native_tui_stage",
        "native_mcp_log_status",
        "turn_sequence",
        "cleanup_status",
        "cleanup_failure_categories",
        "total_tool_count",
        "interaction_tools_present",
        "required_tools_present",
        "metadata_tool_present",
        "metadata_call_error",
        "metadata_item_count",
        "configured_mcp_server_names",
        "configured_enabled_tool_names",
        "configured_enabled_tools_are_exact",
        "private_capability_file_available",
        "create_tool_probe_attempted",
        "create_tool_probe_is_error",
        "create_tool_probe_success",
        "create_tool_probe_error_code",
        "metadata_item_count_after_probe",
        "error_type",
        "cost_limit_usd",
        "reported_cost_usd",
        "provider_event_counts",
        "provider_item_type_counts",
        "model_usage_counts",
        "model_usage_records",
        "failure_diagnostics",
        "operator_auth_unchanged",
        "real_git_repository",
        "mcp_tool_calls",
        "ack_identity_diagnostics",
        "agui_restart_enabled",
        "api_worker_restarts",
        "agui_snapshot_count",
        "mcp_catalog_unexpected_tool_count",
        "mcp_catalog_observation_count",
        "mcp_tool_unexpected_call_count",
        "agui_identity_stable",
        "agui_projection_redacted",
        "agui_final_acknowledged",
        "agui_ledger_identity_stable",
        "agui_observation_complete",
        "agui_exact_ack_observed",
        "native_provider_processes_stable",
        "database_target_stable",
        "native_provider_final_state",
        "native_provider_state_source",
        "native_provider_exit_status",
        "native_provider_exit_status_observed",
    )
    safe_result = _safe_provider_receipt(result)
    entry = {key: safe_result[key] for key in fields if key in safe_result}
    entry["time_limit_seconds"] = (
        60 if str(result.get("result", "")).startswith("mcp_stdio_handshake") else 75
    )
    entry["recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
    encoded = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError:
        return False
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "ab", closefd=False) as stream:
            stream.write(encoded)
            stream.flush()
    finally:
        os.close(descriptor)
    return True


def _private_file_identity(metadata: os.stat_result) -> tuple[int, ...]:
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


def _source_file_identity(path: Path, *, require_private: bool = False) -> tuple[int, ...]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise RuntimeError("private_auth_source_unavailable")
    descriptor = os.open(path, os.O_RDONLY | nofollow)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or (require_private and metadata.st_mode & 0o077)
        ):
            raise RuntimeError("private_auth_source_required")
        identity = _private_file_identity(metadata)
        if _private_file_identity(path.lstat()) != identity:
            raise RuntimeError("private_auth_source_changed")
        return identity
    finally:
        os.close(descriptor)


def _copy_private(
    source: Path,
    destination: Path,
    *,
    require_private_source: bool = False,
    identity_capture: list[tuple[int, ...]] | None = None,
) -> bool:
    """Copy one stable regular file into the run tree without following links."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        return False
    try:
        before = source.lstat()
    except FileNotFoundError:
        return False
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or (require_private_source and before.st_mode & 0o077)
    ):
        return False

    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_stat = destination.parent.lstat()
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.getuid()
        or destination.parent.is_symlink()
    ):
        return False
    os.chmod(destination.parent, 0o700)
    if destination.exists() or destination.is_symlink():
        return False

    source_fd = os.open(source, os.O_RDONLY | nofollow)
    destination_fd = -1
    try:
        opened = os.fstat(source_fd)
        expected_identity = _private_file_identity(before)
        if (
            _private_file_identity(opened) != expected_identity
            or not stat.S_ISREG(opened.st_mode)
            or (require_private_source and opened.st_mode & 0o077)
        ):
            raise RuntimeError("private_auth_source_changed")

        destination_fd = os.open(
            destination,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | nofollow,
            0o600,
        )
        os.fchmod(destination_fd, 0o600)
        while True:
            chunk = os.read(source_fd, 64 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise OSError("private_copy_write_failed")
                view = view[written:]
        os.fsync(destination_fd)

        after_fd = os.fstat(source_fd)
        after_path = source.lstat()
        if (
            _private_file_identity(after_fd) != expected_identity
            or _private_file_identity(after_path) != expected_identity
        ):
            raise RuntimeError("private_auth_source_changed")
    except BaseException:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(source_fd)
    if identity_capture is not None:
        identity_capture[:] = [expected_identity]
    return True


def _prepare_codex_home_overlay(
    root: Path, *, auth_identity: list[tuple[int, ...]] | None = None
) -> tuple[Path, Path]:
    """Copy subscription auth/config privately and preserve packages read-only."""
    source = _codex_home_source()
    if not source.is_absolute() or source.is_symlink() or not source.is_dir():
        raise RuntimeError("codex_home_isolation_unavailable")
    source = source.resolve()
    packages = source / "packages"
    if packages.is_symlink() or not packages.is_dir():
        raise RuntimeError("codex_packages_unavailable")

    auth_path = _codex_auth_path(source)
    staged_auth = root / "codex-auth-staging.json"
    has_auth_file = _copy_private(
        auth_path,
        staged_auth,
        require_private_source=True,
        identity_capture=auth_identity,
    )
    if not has_auth_file:
        raise RuntimeError("codex_subscription_auth_unavailable")
    try:
        auth_data = json.loads(staged_auth.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        auth_data = None
    if (
        not isinstance(auth_data, dict)
        or auth_data.get("auth_mode") != "chatgpt"
        or bool(auth_data.get("OPENAI_API_KEY"))
    ):
        staged_auth.unlink(missing_ok=True)
        raise RuntimeError("codex_subscription_auth_unavailable")

    private_home = root / "codex-home"
    private_home.mkdir(mode=0o700)
    (private_home / "packages").mkdir(mode=0o700)
    os.replace(staged_auth, private_home / "auth.json")
    config_path = ORIGINAL_HOME / ".codex" / "config.toml"
    if _private_profile_is_active():
        _copy_private(config_path, private_home / "config.toml")
    else:
        _copy_private(source / "config.toml", private_home / "config.toml")
    return source, private_home


def _operator_home() -> Path:
    value = os.environ.get("HOME", "")
    path = Path(value) if value else ORIGINAL_HOME
    return path if path.is_absolute() else ORIGINAL_HOME


def _codex_home_source() -> Path:
    configured_home = os.environ.get("CODEX_HOME", "").strip()
    if configured_home == "~":
        return _operator_home()
    if configured_home.startswith("~/"):
        return _operator_home() / configured_home[2:]
    if configured_home:
        return Path(configured_home)
    return _operator_home() / ".codex"


def _codex_auth_path(source: Path | None = None) -> Path:
    if _private_profile_is_active():
        return ORIGINAL_HOME / ".codex" / "auth.json"
    return (source or _codex_home_source()) / "auth.json"


def _private_profile_is_active() -> bool:
    profile_home = os.environ.get("CAO_PRIVATE_PROFILE_HOME", "").strip()
    if not profile_home:
        return False
    try:
        return Path(profile_home).resolve() == ORIGINAL_HOME.resolve()
    except OSError:
        return False


def _codex_executable() -> str:
    """Prefer the installed standalone release over a potentially stale shim."""
    source = _codex_home_source()
    candidates: list[Path] = [source / "packages" / "standalone" / "current" / "bin" / "codex"]
    releases = source / "packages" / "standalone" / "releases"
    try:
        candidates.extend(sorted(releases.glob("*/bin/codex"), reverse=True))
    except OSError:
        pass
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.is_file() and os.access(resolved, os.X_OK):
                return str(resolved)
        except OSError:
            continue
    return shutil.which("codex") or "codex"


def _open_private_log(path: Path):
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8")


def _owned_process_group_members(process_group_id: int) -> tuple[int, ...] | None:
    """Return live members of our process session, or None if it cannot be verified."""
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return None
    members: list[int] = []
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text(encoding="ascii").rpartition(")")[2].split()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError):
            return None
        if len(fields) < 4:
            return None
        state = fields[0]
        try:
            group_id, session_id = int(fields[2]), int(fields[3])
        except ValueError:
            return None
        if state != "Z" and group_id == process_group_id and session_id == process_group_id:
            members.append(int(entry.name))
    return tuple(members)


def _terminate_owned_process_group(process: subprocess.Popen[Any]) -> bool:
    """Stop only the verified private session created for one provider invocation."""
    process_group_id = process.pid
    for signal_number, wait_seconds in ((signal.SIGTERM, 1.0), (signal.SIGKILL, 2.0)):
        members = _owned_process_group_members(process_group_id)
        if members is None:
            return False
        if not members:
            return process.poll() is not None
        try:
            os.killpg(process_group_id, signal_number)
        except ProcessLookupError:
            pass
        except OSError:
            return False
        if process.poll() is None:
            try:
                process.wait(timeout=wait_seconds)
            except subprocess.TimeoutExpired:
                pass
            except OSError:
                return False
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            members = _owned_process_group_members(process_group_id)
            if members is None:
                return False
            if not members:
                return process.poll() is not None
            time.sleep(0.025)
    return False


def _cleanup_action(failures: set[str], category: str, action: Callable[[], Any]) -> bool:
    try:
        action()
        return True
    except Exception:
        failures.add(category)
        return False


def _remove_private_run_root(root: Path, *, process_stopped: bool, failures: set[str]) -> bool:
    """Remove private artifacts only after every owned process/resource is closed."""
    try:
        root.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        failures.add("private_files_remove_failed")
        return False
    if process_stopped and not failures:
        try:
            shutil.rmtree(root)
            root.lstat()
        except Exception:
            try:
                root.lstat()
            except FileNotFoundError:
                return True
            except OSError:
                pass
            failures.add("private_files_remove_failed")
            return False
        failures.add("private_files_remove_failed")
        return False
    failures.add("private_files_preserved")
    return False


def _claude_archive_helper_origin_matches_journey(
    helper_module: ModuleType | None = None,
) -> bool:
    try:
        if helper_module is None:
            helper_module = _get_claude_archive_helper()
        if getattr(helper_module, "__caos_source_sha256__", None) != _CLAUDE_ARCHIVE_HELPER_SHA256:
            return False
        helper_path = Path(helper_module.__file__)
        expected_path = Path(__file__).parent / "claude_auth_archive.py"
        helper_info = helper_path.lstat()
        expected_info = expected_path.lstat()
        if not (
            stat.S_ISREG(helper_info.st_mode)
            and not stat.S_ISLNK(helper_info.st_mode)
            and stat.S_ISREG(expected_info.st_mode)
            and not stat.S_ISLNK(expected_info.st_mode)
            and helper_path.samefile(expected_path)
        ):
            return False
        no_follow_flag = getattr(os, "O_NOFOLLOW", None)
        if not isinstance(no_follow_flag, int) or no_follow_flag == 0:
            return False
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow_flag
        descriptor = os.open(helper_path, flags)
        try:
            opened_info = os.fstat(descriptor)

            def file_identity(info: os.stat_result) -> tuple[int, ...]:
                return (
                    info.st_dev,
                    info.st_ino,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )

            if not stat.S_ISREG(opened_info.st_mode) or file_identity(opened_info) != file_identity(
                helper_info
            ):
                return False
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 128 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            final_info = os.fstat(descriptor)
            return (
                file_identity(final_info) == file_identity(opened_info)
                and digest.hexdigest() == _CLAUDE_ARCHIVE_HELPER_SHA256
            )
        finally:
            os.close(descriptor)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _remove_provider_run_root(
    root: Path,
    *,
    provider_name: str,
    process_stopped: bool,
    failures: set[str],
) -> bool:
    """Archive Claude's private run copy before the normal root cleanup."""
    if provider_name == "claude_code" and process_stopped and not failures:
        try:
            helper_module = _get_claude_archive_helper()
            if not _claude_archive_helper_origin_matches_journey(helper_module):
                raise RuntimeError("Claude archive helper origin invalid")
            archive_root = helper_module.prepare_archive_root(
                Path(root).parent / "claude-auth-archive"
            )
            helper_module.archive_private_credential_copy(
                root,
                archive_root,
                process_stopped=process_stopped,
                layout="interaction",
            )
        except Exception:
            failures.add("claude_auth_archive_incomplete")
    return _remove_private_run_root(root, process_stopped=process_stopped, failures=failures)


def _safe_version(
    executable: str,
    env: dict[str, str],
    root: Path,
    work_dir: Path,
    codex_home_overlay: tuple[Path, Path] | None,
) -> str:
    try:
        result = subprocess.run(
            _sandbox_command([executable, "--version"], root, work_dir, codex_home_overlay),
            capture_output=True,
            text=True,
            timeout=8,
            env=env,
            check=False,
        )
        first = (result.stdout or result.stderr).splitlines()
        return first[0][:100] if first else "unknown"
    except Exception:
        return "unknown"


def _source_root() -> Path:
    """Return this candidate checkout's root from the harness source location."""
    return Path(__file__).resolve().parents[2]


def _source_pythonpath() -> str:
    """Return this candidate checkout's absolute source directory."""
    source = (_source_root() / "src").resolve()
    if not source.is_dir() or not (source / "cli_agent_orchestrator").is_dir():
        raise RuntimeError("candidate_source_unavailable")
    return str(source)


def _mcp_child_environment(root: Path, port: int, terminal_id: str) -> dict[str, str]:
    """Build an explicit, credential-free environment for CAO's stdio child."""
    cao_home = root / "cao"
    tmp_dir = root / "tmp"
    private_home = root / "mcp-home"
    cao_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in (cao_home, tmp_dir, private_home):
        if directory.is_symlink():
            raise RuntimeError("unsafe_temp_root")
        directory.chmod(0o700)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TERM": "xterm-256color",
        "TMPDIR": str(tmp_dir),
        "HOME": str(private_home),
        "PYTHONPATH": _source_pythonpath(),
        "PYTHONDONTWRITEBYTECODE": "1",
        "CAO_HOME_DIR": str(cao_home),
        "CAO_DATABASE_CONFIG_FILE": str(cao_home / "database-config.json"),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": str(port),
        "CAO_AUTH_MODE": "off",
        "CAO_TERMINAL_ID": terminal_id,
    }
    # The MCP server needs only its private CAO state; it must not inherit the
    # operator's account homes or provider credentials from the CLI process.
    return env


def _diagnostic_mcp_environment(
    root: Path, port: int, terminal_id: str, server_config: dict[str, Any]
) -> dict[str, str]:
    """Build the diagnostic MCP env without inheriting the provider account."""
    env = _mcp_child_environment(root, port, terminal_id)
    configured_env = server_config.get("env")
    if isinstance(configured_env, dict):
        capability_path = configured_env.get("CAO_INTERACTION_CAPABILITY_FILE")
        if isinstance(capability_path, str) and capability_path:
            env["CAO_INTERACTION_CAPABILITY_FILE"] = capability_path
    return env


def _base_cli_env(root: Path, provider: str) -> dict[str, str]:
    cao_home = root / "cao"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TERM": "xterm-256color",
        "TMPDIR": str(root / "tmp"),
        "PYTHONPATH": _source_pythonpath(),
        "PYTHONDONTWRITEBYTECODE": "1",
        "CAO_HOME_DIR": str(cao_home),
        "CAO_DATABASE_CONFIG_FILE": str(cao_home / "database-config.json"),
    }
    # Preserve the caller's CLI home values. In particular, do not redirect
    # HOME or CODEX_HOME: the sandbox overlays a run-private copy at Codex's
    # existing path, while its source files remain unchanged.
    for key in ("HOME", "CODEX_HOME"):
        if key in os.environ:
            env[key] = os.environ[key]
    Path(env["TMPDIR"]).mkdir(mode=0o700, parents=True, exist_ok=True)
    if provider == "claude_code":
        config_dir = root / "claude-home"
        config_dir.mkdir(mode=0o700)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        configured_claude_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
        source_claude_dir = (
            Path(configured_claude_dir).expanduser()
            if configured_claude_dir
            else ORIGINAL_HOME / ".claude"
        )
        _copy_private(
            source_claude_dir / ".credentials.json",
            config_dir / ".credentials.json",
        )
        for key in (
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
            "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
            "CLAUDE_CODE_SKIP_VERTEX_AUTH",
            "CLAUDE_CODE_SKIP_FOUNDRY_AUTH",
            "CLOUD_ML_REGION",
            "ANTHROPIC_VERTEX_PROJECT_ID",
        ):
            if key in os.environ:
                env[key] = os.environ[key]
    elif provider != "codex":
        raise RuntimeError("unsupported_provider")
    # Subscription credentials are copied into private provider homes. Never
    # pass API-key variables that could silently switch a live run to metered
    # billing, regardless of which provider is being exercised.
    for key in tuple(env):
        if key.endswith("_API_KEY"):
            env.pop(key)
    return env


def _profile_frontmatter(metadata: dict[str, Any]) -> str:
    import yaml

    return "---\n" + yaml.safe_dump(metadata, sort_keys=False) + "---\n"


def _profile_model(provider: str, *, agui_restart: bool) -> str | None:
    if provider == "codex":
        return CODEX_INTERACTION_MODEL
    if provider == "claude_code":
        return CLAUDE_AGUI_RESTART_MODEL if agui_restart else None
    if provider == "opencode_cli":
        # OpenCode selects and validates its model in its native request path.
        # Keep the generated CAO profile model-free so the native selection is
        # not shadowed by a synthetic profile override.
        return None
    raise ValueError("unsupported_provider")


def _command_has_exact_model(command: list[str], model: str) -> bool:
    model_flags = [index for index, token in enumerate(command) if token == "--model"]
    return (
        len(model_flags) == 1
        and model_flags[0] + 1 < len(command)
        and command[model_flags[0] + 1] == model
    )


def _profile_mcp_server(profile: Any) -> dict[str, Any] | None:
    servers = getattr(profile, "mcpServers", None)
    if not isinstance(servers, dict) or set(servers) != {"cao-mcp-server"}:
        return None
    server = servers.get("cao-mcp-server")
    if not isinstance(server, dict) and callable(getattr(server, "model_dump", None)):
        server = server.model_dump(exclude_none=True)
    if not isinstance(server, dict):
        return None
    if (
        server.get("type") != "stdio"
        or server.get("command") != sys.executable
        or server.get("args") != ["-m", "cli_agent_orchestrator.mcp_server.server"]
    ):
        return None
    return server


def _flag_arguments(command: list[str], flag: str) -> list[str] | None:
    positions = [index for index, token in enumerate(command) if token == flag]
    if any(index + 1 >= len(command) for index in positions):
        return None
    return [command[index + 1] for index in positions]


def _codex_tool_scope_exact(server: dict[str, Any], command: list[str]) -> bool:
    if server.get("enabled_tools") != list(INTERACTION_TOOLS):
        return False

    config_values: list[str] = []
    index = 0
    while index < len(command):
        if command[index] == "-c":
            if index + 1 >= len(command):
                return False
            config_values.append(command[index + 1])
            index += 2
            continue
        index += 1

    mcp_values = [value for value in config_values if value.startswith("mcp_servers")]
    if mcp_values.count("mcp_servers={}") != 1:
        return False
    server_names: set[str] = set()
    command_values: list[str] = []
    args_values: list[str] = []
    enabled_tool_values: list[str] = []
    approval_values: list[str] = []
    for value in mcp_values:
        if value == "mcp_servers={}":
            continue
        match = re.fullmatch(r"mcp_servers\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)=(.*)", value)
        if match is None:
            return False
        server_name, field, field_value = match.groups()
        if server_name != "cao-mcp-server":
            return False
        server_names.add(server_name)
        if field == "command":
            command_values.append(field_value)
        elif field == "args":
            args_values.append(field_value)
        elif field == "enabled_tools":
            enabled_tool_values.append(field_value)
        elif field.startswith("tools.") and field.endswith(".approval_mode"):
            approval_values.append(value)
        elif field not in {
            "command",
            "args",
            "cwd",
            "env",
            "env_vars",
            "required",
            "startup_timeout_sec",
            "tool_timeout_sec",
        } and not field.startswith("env."):
            return False

    expected_tools = "[" + ", ".join(json.dumps(tool) for tool in INTERACTION_TOOLS) + "]"
    expected_args = '["-m", "cli_agent_orchestrator.mcp_server.server"]'
    expected_approval_values = [
        f'mcp_servers.cao-mcp-server.tools.{tool}.approval_mode="approve"'
        for tool in INTERACTION_TOOLS
    ]
    if (
        server_names != {"cao-mcp-server"}
        or command_values != [json.dumps(sys.executable)]
        or args_values != [expected_args]
        or enabled_tool_values != [expected_tools]
        or approval_values != expected_approval_values
    ):
        return False

    if _flag_arguments(command, "--sandbox") != ["read-only"]:
        return False
    if _flag_arguments(command, "--disable") != ["shell_tool"]:
        return False
    if config_values.count('approval_policy="never"') != 1:
        return False
    if config_values.count('web_search="disabled"') != 1:
        return False
    if command.count("--ephemeral") != 1 or command.count("--json") != 1:
        return False
    if "--yolo" in command or "--dangerously-bypass-approvals-and-sandbox" in command:
        return False
    return True


def _claude_tool_scope_exact(
    server: dict[str, Any], command: list[str], private_root: Path
) -> bool:
    if "enabled_tools" in server:
        return False
    if _flag_arguments(command, "--tools") != [""]:
        return False
    if _flag_arguments(command, "--permission-mode") != ["default"]:
        return False
    if command.count("--strict-mcp-config") != 1:
        return False
    if any(flag in command for flag in ("--dangerously-skip-permissions", "--agent")):
        return False

    allowed_positions = [index for index, token in enumerate(command) if token == "--allowedTools"]
    if len(allowed_positions) != 1:
        return False
    allowed_tools: list[str] = []
    for token in command[allowed_positions[0] + 1 :]:
        if token.startswith("--"):
            break
        allowed_tools.append(token)
    if allowed_tools != list(CLAUDE_ALLOWED_TOOLS):
        return False

    config_paths = _flag_arguments(command, "--mcp-config")
    if config_paths is None or len(config_paths) != 1:
        return False
    config_path = Path(config_paths[0])
    try:
        if config_path.is_symlink():
            return False
        config_stat = config_path.lstat()
        if not stat.S_ISREG(config_stat.st_mode) or stat.S_IMODE(config_stat.st_mode) != 0o600:
            return False
        resolved_root = private_root.resolve(strict=True)
        resolved_config = config_path.resolve(strict=True)
        if not resolved_config.is_relative_to(resolved_root):
            return False
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(config, dict):
        return False
    actual_servers = config.get("mcpServers")
    if not isinstance(actual_servers, dict) or set(actual_servers) != {"cao-mcp-server"}:
        return False
    actual = actual_servers.get("cao-mcp-server")
    if not isinstance(actual, dict):
        return False
    return (
        actual.get("type") == server.get("type") == "stdio"
        and actual.get("command") == server.get("command") == sys.executable
        and actual.get("args") == server.get("args")
        and actual.get("args") == ["-m", "cli_agent_orchestrator.mcp_server.server"]
    )


def _agent_tool_scope_exact(
    provider: str,
    profile: Any,
    command: list[str],
    private_root: Path,
) -> bool:
    """Prove the generated provider command exposes only the seven journey tools."""
    server = _profile_mcp_server(profile)
    if server is None:
        return False
    if provider == "codex":
        return _codex_tool_scope_exact(server, command)
    if provider == "claude_code":
        return _claude_tool_scope_exact(server, command, private_root)
    return False


def _install_profile(
    root: Path,
    name: str,
    provider: str,
    port: int,
    terminal_id: str,
    *,
    agui_restart: bool = False,
) -> None:
    # Import after CAO_HOME_DIR has been redirected into the private temp tree.
    from cli_agent_orchestrator.constants import LOCAL_AGENT_STORE_DIR

    mcp_entry: dict[str, Any] = {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "cli_agent_orchestrator.mcp_server.server"],
        "env": _mcp_child_environment(root, port, terminal_id),
        "timeout": 65000,
    }
    if provider == "codex":
        mcp_entry["enabled_tools"] = list(INTERACTION_TOOLS)
    metadata: dict[str, Any] = {
        "name": name,
        "description": "Temporary bounded interaction acceptance profile.",
        "provider": provider,
        "system_prompt": "",
        "mcpServers": {"cao-mcp-server": mcp_entry},
    }
    profile_model = _profile_model(provider, agui_restart=agui_restart)
    if profile_model is not None:
        metadata["model"] = profile_model
    if provider == "codex":
        metadata["codexProfile"] = "interaction-safe"
    else:
        metadata["permissionMode"] = "default"
    LOCAL_AGENT_STORE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = LOCAL_AGENT_STORE_DIR / f"{name}.md"
    path.write_text(_profile_frontmatter(metadata), encoding="utf-8")
    path.chmod(0o600)


def _codex_interaction_approval_overrides(server_name: str, tools: tuple[str, ...]) -> list[str]:
    """Auto-approve only the seven scoped CAO question-flow tools for Codex."""
    if server_name != "cao-mcp-server":
        return []
    if any(tool not in INTERACTION_TOOLS for tool in tools):
        raise ValueError("tool is outside the interaction tool allowlist")
    return [
        override
        for tool in tools
        for override in (
            "-c",
            f'mcp_servers.cao-mcp-server.tools.{tool}.approval_mode="approve"',
        )
    ]


def _codex_command(
    provider_command: str,
    work_dir: Path,
    prompt: str,
    executable_override: str | None = None,
) -> list[str]:
    tokens = shlex.split(provider_command)
    if not tokens or Path(tokens[0]).name != "codex":
        raise RuntimeError("provider_command_shape")
    filtered: list[str] = [executable_override or tokens[0]]
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token == "--profile" and i + 1 < len(tokens):
            # CAO's provider command uses the named profile only to avoid its
            # default --yolo path. Do not create or mutate a global profile;
            # the test applies those safety settings as per-run CLI overrides.
            i += 2
            continue
        if token == "--no-alt-screen":
            i += 1
            continue
        if token == "--disable" and i + 1 < len(tokens) and tokens[i + 1] == "shell_snapshot":
            i += 2
            continue
        if token == "-c" and i + 1 < len(tokens):
            value = tokens[i + 1]
            match = re.fullmatch(r"developer_instructions=\$\(cat ([^()]*)\)", value)
            if match:
                instructions_path = Path(match.group(1))
                if instructions_path.is_symlink() or not instructions_path.is_file():
                    raise RuntimeError("provider_prompt_file_missing")
                value = "developer_instructions=" + instructions_path.read_text(encoding="utf-8")
            filtered.extend((token, value))
            i += 2
            continue
        filtered.append(token)
        i += 1
    return [
        filtered[0],
        "exec",
        "-c",
        "mcp_servers={}",
        *filtered[1:],
        "--ephemeral",
        "--json",
        "--sandbox",
        "read-only",
        "--disable",
        "shell_tool",
        "-c",
        'approval_policy="never"',
        *_codex_interaction_approval_overrides("cao-mcp-server", INTERACTION_TOOLS),
        "-c",
        'web_search="disabled"',
        "--skip-git-repo-check",
        "--cd",
        str(work_dir),
        prompt,
    ]


def _claude_command(provider_command: str, prompt: str) -> list[str]:
    if "; " not in provider_command:
        raise RuntimeError("provider_command_shape")
    command = provider_command.split("; ", 1)[1]
    tokens = shlex.split(command)
    # The provider builder prefixes its native command with scoped MCP timeout
    # assignments. The same values are installed directly in the child env.
    while tokens and tokens[0].startswith("MCP_TIMEOUT="):
        tokens.pop(0)
    while tokens and tokens[0].startswith("MCP_CONNECT_TIMEOUT_MS="):
        tokens.pop(0)
    if not tokens or Path(tokens[0]).name != "claude":
        raise RuntimeError("provider_command_shape")
    return [
        *tokens,
        "--print",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--output-format",
        "stream-json",
        "--verbose",
        "--tools",
        "",
        "--allowedTools",
        *CLAUDE_ALLOWED_TOOLS,
        "--max-budget-usd",
        "1.00",
        prompt,
    ]


def _sandbox_command(
    command: list[str],
    root: Path,
    work_dir: Path,
    codex_home_overlay: tuple[Path, Path] | None = None,
) -> list[str]:
    """Run the provider with the host filesystem read-only except this run.

    The caller's HOME and CODEX_HOME values remain unchanged. A mount
    namespace makes the host read-only. Codex gets private auth/config files,
    with its installed packages subtree re-exposed read-only. The private run
    tree remains writable for the MCP subprocess and temp files.
    """
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("read_only_sandbox_unavailable")
    wrapped = [
        bwrap,
        "--die-with-parent",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--bind",
        str(root),
        str(root),
    ]
    if codex_home_overlay is not None:
        host_path, private_path = codex_home_overlay
        wrapped.extend(("--bind", str(private_path), str(host_path)))
        wrapped.extend(("--ro-bind", str(host_path / "packages"), str(host_path / "packages")))
    wrapped.extend(("--chdir", str(work_dir), "--", *command))
    return wrapped


_PROVIDER_EVENT_TYPES = frozenset(
    {
        "system",
        "rate_limit_event",
        "result",
        "assistant",
        "user",
        "thread.started",
        "turn.started",
        "turn.completed",
        "turn.failed",
        "item.started",
        "item.updated",
        "item.completed",
        "error",
        "step_start",
        "step_finish",
        "text",
        "tool",
        "tool_use",
        "tool_result",
    }
)
_MODEL_USAGE_FIELDS = frozenset(
    {
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    }
)
_MAX_SAFE_COUNTER = 10**12
_FAILURE_DIAGNOSTIC_NAMES = (
    "codex_linux_optional_dependency_missing",
    "mcp_spawn_failed",
    "mcp_handshake_failed",
    "mcp_startup_timeout",
    "mcp_process_exited",
    "filesystem_write_denied",
    "provider_configuration_rejected",
    "provider_auth_rejected",
    "provider_rate_limited",
)
_PROVIDER_ITEM_TYPES = frozenset(
    {
        "agent_message",
        "command_execution",
        "error",
        "file_change",
        "mcp_tool_call",
        "reasoning",
        "server_tool_use",
        "todo_list",
        "tool_search",
        "tool_search_call",
        "tool_search_output",
        "tool_result",
        "tool_use",
        "web_search",
        "step-start",
        "step-finish",
        "text",
        "tool",
    }
)
_SAFE_INTERACTION_ERROR_CODES = frozenset(
    {
        "capability_unavailable",
        "unauthorized",
        "forbidden",
        "not_found",
        "conflict",
        "stale",
        "invalid",
        "capacity_exceeded",
        "timeout",
        "unavailable",
    }
)
_SAFE_PROTOCOL_ERROR_CODES = {
    -32700: "protocol_parse_error",
    -32600: "protocol_invalid_request",
    -32601: "protocol_method_not_found",
    -32602: "protocol_invalid_params",
    -32603: "protocol_internal_error",
}
_SAFE_PROVIDER_ERROR_PATTERNS = (
    (
        "protocol_invalid_params",
        re.compile(
            r"invalid (?:params?|parameters?|arguments?)|missing required|"
            r"validation (?:error|failed)|extra inputs? (?:are )?not permitted",
            re.IGNORECASE,
        ),
    ),
    (
        "protocol_method_not_found",
        re.compile(r"method not found|unknown tool|tool (?:name )?.{0,80} not found", re.I),
    ),
    (
        "mcp_connection_closed",
        re.compile(r"connection (?:closed|reset)|broken pipe|\beof\b|transport closed", re.I),
    ),
    ("mcp_timeout", re.compile(r"timed? out|timeout", re.I)),
    ("tool_call_cancelled", re.compile(r"cancelled|canceled", re.I)),
    (
        "mcp_server_error",
        re.compile(r"internal (?:server )?error|server error|tool execution failed", re.I),
    ),
)


def _safe_tool_error_code(value: Any) -> str | None:
    """Extract only known error identifiers; never return provider text."""
    if isinstance(value, dict):
        code = value.get("code")
        if isinstance(code, str) and code in _SAFE_INTERACTION_ERROR_CODES:
            return code
        if isinstance(code, int) and not isinstance(code, bool):
            protocol_code = _SAFE_PROTOCOL_ERROR_CODES.get(code)
            if protocol_code:
                return protocol_code
        for key in ("message", "error", "structured_content", "result"):
            candidate = _safe_tool_error_code(value.get(key))
            if candidate:
                return candidate
    elif isinstance(value, str):
        lowered = value.lower()
        for protocol_code, category in _SAFE_PROTOCOL_ERROR_CODES.items():
            if str(protocol_code) in lowered:
                return category
        for category, pattern in _SAFE_PROVIDER_ERROR_PATTERNS:
            if pattern.search(value):
                return category
        for code in _SAFE_INTERACTION_ERROR_CODES:
            if re.search(rf"\b{re.escape(code)}\b", lowered):
                return code
    return None


_SAFE_MCP_RESULT_SHAPES = frozenset(
    {"call_tool_result", "content_list", "mapping_other", "text", "other"}
)
_SAFE_MCP_IS_ERROR_STATES = frozenset({"true", "false", "absent", "invalid"})
_SAFE_MCP_SUCCESS_STATES = frozenset({"true", "false", "unknown"})
_SAFE_MCP_CONTENT_BLOCK_TYPES = frozenset(
    {"text", "image", "audio", "resource", "resource_link", "other"}
)
_SAFE_MCP_RESULT_ERROR_CODES = frozenset(
    set(_SAFE_INTERACTION_ERROR_CODES)
    | set(_SAFE_PROTOCOL_ERROR_CODES.values())
    | {category for category, _ in _SAFE_PROVIDER_ERROR_PATTERNS}
)


def _safe_mcp_result_error_code(value: Any) -> str | None:
    """Extract one fixed error category from MCP result fields only."""
    pending = [value]
    text_candidates: list[str] = []
    visited = 0
    while pending and visited < 64:
        current = pending.pop()
        visited += 1
        if isinstance(current, dict):
            code = current.get("code")
            if code is not None:
                safe_code = _safe_tool_error_code({"code": code})
                if safe_code is not None:
                    return safe_code
            for key in (
                "content",
                "error",
                "structuredContent",
                "structured_content",
                "message",
                "result",
            ):
                nested = current.get(key)
                if key == "content" and isinstance(nested, list):
                    for block in nested[:16]:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "text"
                            and isinstance(block.get("text"), str)
                        ):
                            text_candidates.append(block["text"][:4096])
                elif isinstance(nested, dict):
                    pending.append(nested)
                elif isinstance(nested, list):
                    pending.extend(reversed(nested[:16]))
                elif isinstance(nested, str):
                    text_candidates.append(nested[:4096])
        elif isinstance(current, (list, tuple)):
            pending.extend(reversed(current[:16]))
        elif isinstance(current, str):
            text_candidates.append(current[:4096])
    for text in text_candidates[:32]:
        safe_code = _safe_tool_error_code(text)
        if safe_code is not None:
            return safe_code
    return None


_UNAVAILABLE_DETAIL_CAUSES = {
    "The interaction service could not complete the request.": "http_error",
    "The CAO interaction API is unreachable.": "connection_error",
    "The interaction request could not be completed.": "unexpected_error",
}


def _safe_mcp_unavailable_cause(value: Any) -> str | None:
    """Classify only exact production error constants, never arbitrary text."""
    pending = [value]
    causes: set[str] = set()
    visited = 0
    while pending and visited < 64:
        current = pending.pop()
        visited += 1
        if isinstance(current, dict):
            if current.get("code") == "unavailable":
                detail = current.get("detail")
                cause = _UNAVAILABLE_DETAIL_CAUSES.get(detail) if isinstance(detail, str) else None
                if cause is None:
                    return None
                causes.add(cause)
            for key in ("error", "structuredContent", "structured_content", "result"):
                nested = current.get(key)
                if isinstance(nested, dict):
                    pending.append(nested)
            content = current.get("content")
            if isinstance(content, list):
                if len(content) > 16:
                    return None
                pending.extend(content)
            if current.get("type") == "text":
                text = current.get("text")
                if isinstance(text, str) and len(text) <= 4096:
                    pending.append(text)
        elif isinstance(current, list):
            if len(current) > 16:
                return None
            pending.extend(current)
        elif isinstance(current, str) and len(current) <= 4096:
            try:
                decoded = json.loads(current)
            except (ValueError, RecursionError):
                continue
            if isinstance(decoded, (dict, list)):
                pending.append(decoded)
    return next(iter(causes)) if not pending and len(causes) == 1 else None


def _safe_mcp_result_diagnostics(value: Any) -> dict[str, Any]:
    """Return MCP result shape/status enums without arguments or result text."""
    content: Any = None
    structured_present = False
    is_error_state = "absent"
    if isinstance(value, dict):
        shape = (
            "call_tool_result"
            if any(
                key in value
                for key in (
                    "content",
                    "structuredContent",
                    "structured_content",
                    "isError",
                    "is_error",
                )
            )
            else "mapping_other"
        )
        content = value.get("content")
        structured_present = any(
            key in value and value[key] is not None
            for key in ("structuredContent", "structured_content")
        )
        flags = [value[key] for key in ("isError", "is_error") if key in value]
        if flags:
            if all(isinstance(flag, bool) for flag in flags) and len(set(flags)) == 1:
                is_error_state = "true" if flags[0] else "false"
            else:
                is_error_state = "invalid"
    elif isinstance(value, list):
        shape = "content_list"
        content = value
    elif isinstance(value, str):
        shape = "text"
    else:
        shape = "other"

    block_types: set[str] = set()
    if isinstance(content, list):
        for block in content[:64]:
            block_type = block.get("type") if isinstance(block, dict) else None
            block_types.add(
                block_type
                if isinstance(block_type, str) and block_type in _SAFE_MCP_CONTENT_BLOCK_TYPES
                else "other"
            )
    success = (
        "false" if is_error_state == "true" else "true" if is_error_state == "false" else "unknown"
    )
    return {
        "mcp_result_shape": shape,
        "mcp_is_error": is_error_state,
        "mcp_result_success": success,
        "mcp_content_block_types": sorted(block_types),
        "mcp_structured_content_present": structured_present,
        "mcp_result_error_code": _safe_mcp_result_error_code(value),
        "mcp_unavailable_cause": _safe_mcp_unavailable_cause(value),
    }


def _claude_tool_identity(name: Any) -> tuple[str, str]:
    """Map a Claude tool name to fixed MCP server/tool labels without retaining it."""
    if not isinstance(name, str) or not name.startswith("mcp__"):
        return "other", "other"
    server_name, separator, tool_name = name[len("mcp__") :].partition("__")
    if not separator:
        return "other", "other"
    server = "cao-mcp-server" if server_name == "cao-mcp-server" else "other"
    tool = tool_name if tool_name in INTERACTION_TOOLS else "other"
    return server, tool


def _provider_event_summary(path: Path) -> dict[str, Any]:
    """Summarize provider JSONL events without retaining arguments or content."""
    event_counts: dict[str, int] = {}
    item_counts: dict[str, int] = {}
    calls: dict[str, dict[str, Any]] = {}
    fallback_id = 0

    def call_for(item_id: Any) -> dict[str, Any]:
        nonlocal fallback_id
        if not isinstance(item_id, str) or not item_id:
            fallback_id += 1
            item_id = f"missing-id-{fallback_id}"
        return calls.setdefault(
            item_id,
            {
                "server": "other",
                "tool": "other",
                "status": "unknown",
                "error_present": False,
                "result_present": False,
                "error_code": None,
            },
        )

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
                if isinstance(event_type, str) and event_type in _PROVIDER_EVENT_TYPES:
                    event_counts[event_type] = event_counts.get(event_type, 0) + 1
                item = event.get("item")
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if isinstance(item_type, str) and item_type in _PROVIDER_ITEM_TYPES:
                        item_counts[item_type] = item_counts.get(item_type, 0) + 1
                    if item_type == "mcp_tool_call":
                        call = call_for(item.get("id"))
                        if item.get("server") == "cao-mcp-server":
                            call["server"] = "cao-mcp-server"
                        tool_name = item.get("tool")
                        if isinstance(tool_name, str) and tool_name in INTERACTION_TOOLS:
                            call["tool"] = tool_name
                        status = item.get("status")
                        if status in {"in_progress", "completed", "failed"}:
                            call["status"] = status
                        error = item.get("error")
                        if error is not None:
                            call["error_present"] = True
                            call["error_code"] = _safe_tool_error_code(error)
                        result = item.get("result")
                        if result is not None:
                            call["result_present"] = True
                            result_code = _safe_tool_error_code(result)
                            if result_code:
                                call["error_code"] = result_code
                            result_diagnostics = _safe_mcp_result_diagnostics(result)
                            call.update(result_diagnostics)
                            if result_diagnostics["mcp_is_error"] in {
                                "true",
                                "invalid",
                            }:
                                call["error_present"] = True
                        continue

                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                if event_type == "assistant":
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type")
                        if not isinstance(block_type, str) or block_type not in {
                            "tool_use",
                            "server_tool_use",
                        }:
                            continue
                        item_counts[block_type] = item_counts.get(block_type, 0) + 1
                        call = call_for(block.get("id"))
                        name = block.get("name")
                        if block_type == "tool_use":
                            server, tool_name = _claude_tool_identity(name)
                            call["server"] = server
                            call["tool"] = tool_name
                        else:
                            call["server"] = "other"
                            call["tool"] = "other"
                        if call["status"] == "unknown":
                            call["status"] = "in_progress"
                elif event_type == "user":
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            continue
                        item_counts["tool_result"] = item_counts.get("tool_result", 0) + 1
                        item_id = block.get("tool_use_id")
                        call = calls.get(item_id) if isinstance(item_id, str) else None
                        if call is None:
                            continue
                        call["result_present"] = True
                        is_error = block.get("is_error")
                        if is_error is True:
                            call["status"] = "failed"
                            call["error_present"] = True
                        elif is_error is False or "is_error" not in block:
                            call["status"] = "completed"
                            call["error_present"] = False
                        else:
                            call["status"] = "unknown"

    model_usage_counts: dict[str, int] = {}
    model_usage_records = 0
    try:
        usage_stream = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        usage_stream = None
    if usage_stream is not None:
        with usage_stream:
            for line in usage_stream:
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(event, dict):
                    continue
                event_type = event.get("type")
                usage = event.get("usage")
                if event_type not in _PROVIDER_EVENT_TYPES or not isinstance(usage, dict):
                    continue
                accepted = False
                for field in _MODEL_USAGE_FIELDS:
                    value = usage.get(field)
                    if (
                        isinstance(value, int)
                        and not isinstance(value, bool)
                        and 0 <= value <= _MAX_SAFE_COUNTER
                    ):
                        model_usage_counts[field] = model_usage_counts.get(field, 0) + value
                        accepted = True
                if accepted:
                    model_usage_records += 1

    ordered_calls = sorted(
        calls.values(),
        key=lambda call: (call["server"], call["tool"], call["status"]),
    )
    return {
        "provider_event_counts": dict(sorted(event_counts.items())),
        "provider_item_type_counts": dict(sorted(item_counts.items())),
        "model_usage_counts": dict(sorted(model_usage_counts.items())),
        "model_usage_records": model_usage_records,
        "mcp_tool_calls": ordered_calls,
        "mcp_tool_names": sorted(
            {call["tool"] for call in ordered_calls if call["tool"] in INTERACTION_TOOLS}
        ),
    }


_AGENT_MESSAGE_SCAN_LIMIT = 2 * 1024 * 1024
_AGENT_MESSAGE_LINE_LIMIT = 256 * 1024
_AGENT_MESSAGE_DIAGNOSTIC_PATTERNS = {
    "reports_tools_unavailable": (
        "tools are unavailable",
        "tools unavailable",
        "no tools are available",
        "i do not have access to tools",
        "i don't have access to tools",
        "i cannot use tools",
        "i can't use tools",
    ),
    "reports_capability_missing": (
        "missing capability",
        "capability is missing",
        "capability file is missing",
        "capability is unavailable",
        "capability unavailable",
    ),
    "reports_refusal": (
        "i cannot help",
        "i can't help",
        "i cannot assist",
        "i can't assist",
        "i cannot comply",
        "i can't comply",
    ),
    "reports_prompt_mismatch": (
        "prompt mismatch",
        "the prompt does not match",
        "your prompt does not match",
        "i do not understand the request",
        "i don't understand the request",
        "request is unclear",
    ),
}


def _agent_message_failure_diagnostics(path: Path) -> dict[str, bool]:
    """Classify fixed signals in agent-message items without retaining their text."""
    diagnostics = {
        "scan_complete": False,
        "agent_message_seen": False,
        **{name: False for name in _AGENT_MESSAGE_DIAGNOSTIC_PATTERNS},
    }
    try:
        with path.open("rb") as stream:
            data = stream.read(_AGENT_MESSAGE_SCAN_LIMIT + 1)
    except OSError:
        return diagnostics

    scan_complete = len(data) <= _AGENT_MESSAGE_SCAN_LIMIT
    data = data[:_AGENT_MESSAGE_SCAN_LIMIT]
    found: set[str] = set()
    for line in data.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            scan_complete = False
            continue
        if len(line) > _AGENT_MESSAGE_LINE_LIMIT:
            scan_complete = False
            continue
        try:
            event = json.loads(line)
        except RecursionError:
            scan_complete = False
            continue
        except (UnicodeError, ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            continue
        diagnostics["agent_message_seen"] = True
        text = item.get("text")
        if not isinstance(text, str):
            continue
        lowered = text.casefold()
        for name, patterns in _AGENT_MESSAGE_DIAGNOSTIC_PATTERNS.items():
            if any(pattern in lowered for pattern in patterns):
                found.add(name)

    diagnostics["scan_complete"] = scan_complete
    for name in found:
        diagnostics[name] = True
    return diagnostics


def _ack_identity_match_diagnostics(
    *,
    created_group_id: Any,
    waited_group_id: Any,
    waited_answers: Any,
    ack_group_id: Any,
    acknowledgements: Any,
) -> dict[str, Any]:
    """Compare ack identity to the exact in-process wait result without exposing values."""

    def field(value: Any, name: str) -> Any:
        if isinstance(value, dict):
            return value.get(name)
        return getattr(value, name, None)

    created_group = str(created_group_id) if created_group_id is not None else None
    waited_group = str(waited_group_id) if waited_group_id is not None else None
    ack_group = str(ack_group_id) if ack_group_id is not None else None
    expected = list(waited_answers) if isinstance(waited_answers, (list, tuple)) else None
    actual = list(acknowledgements) if isinstance(acknowledgements, (list, tuple)) else None

    def identities(items: list[Any] | None) -> list[tuple[str, int, str]] | None:
        if items is None:
            return None
        result: list[tuple[str, int, str]] = []
        for item in items:
            question_id = field(item, "question_id")
            version = field(item, "version")
            receipt = field(item, "receipt")
            if (
                not isinstance(question_id, str)
                or type(version) is not int
                or not isinstance(receipt, str)
            ):
                return None
            result.append((question_id, version, receipt))
        return result

    expected_identities = identities(expected)
    actual_identities = identities(actual)
    comparisons_available = expected_identities is not None and actual_identities is not None
    expected_question_ids = (
        sorted(identity[0] for identity in expected_identities)
        if expected_identities is not None
        else None
    )
    actual_question_ids = (
        sorted(identity[0] for identity in actual_identities)
        if actual_identities is not None
        else None
    )
    expected_versions = (
        sorted(identity[1] for identity in expected_identities)
        if expected_identities is not None
        else None
    )
    actual_versions = (
        sorted(identity[1] for identity in actual_identities)
        if actual_identities is not None
        else None
    )

    return {
        "comparison_available": comparisons_available,
        "ack_group_matches_created_group": (
            ack_group == created_group
            if ack_group is not None and created_group is not None
            else None
        ),
        "ack_group_matches_wait_group": (
            ack_group == waited_group
            if ack_group is not None and waited_group is not None
            else None
        ),
        "ack_count_matches_returned_answers": (
            len(actual_identities) == len(expected_identities) if comparisons_available else None
        ),
        "ack_question_ids_match_returned_answers": (
            actual_question_ids == expected_question_ids if comparisons_available else None
        ),
        "ack_versions_match_returned_answers": (
            actual_versions == expected_versions if comparisons_available else None
        ),
        "ack_receipts_match_returned_answers": (
            sorted(actual_identities) == sorted(expected_identities)
            if comparisons_available
            else None
        ),
    }


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def _read_agui_snapshot(
    base_url: str, *, forbidden_content: tuple[str, ...], timeout_seconds: float = 8.0
) -> dict[str, Any]:
    """Read one snapshot from the production SSE endpoint without retaining its body."""
    from test.live_provider.interaction_agui_restart import SSEParser, snapshot_from_frame

    import httpx

    deadline = time.monotonic() + timeout_seconds
    with httpx.Client(
        timeout=httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds)),
        trust_env=False,
    ) as client:
        with client.stream(
            "GET",
            f"{base_url}/agui/v1/stream",
            params={"interaction_limit": 100},
        ) as response:
            if response.status_code != 200:
                raise RuntimeError("agui_stream_unavailable")
            parser = SSEParser()
            for line in response.iter_lines():
                if time.monotonic() >= deadline:
                    raise TimeoutError("agui_snapshot_timeout")
                frame = parser.feed(line)
                if frame is not None and frame.event == "STATE_SNAPSHOT":
                    return snapshot_from_frame(
                        frame,
                        forbidden_content=forbidden_content,
                    )
    raise TimeoutError("agui_snapshot_timeout")


def _snapshot_matches_questions(
    snapshot: dict[str, Any],
    expected: dict[str, dict[str, Any]],
    *,
    lifecycle: str | None = None,
    delivery_state: object = ...,
) -> bool:
    from test.live_provider.interaction_agui_restart import snapshot_identity_map

    try:
        actual = snapshot_identity_map(snapshot)
    except ValueError:
        return False
    if set(actual) != set(expected) or len(actual) != len(expected):
        return False
    for question_id, identity in expected.items():
        current = actual[question_id]
        for field in ("group_id", "version", "terminal_id", "incarnation_id"):
            if current[field] != identity[field]:
                return False
        if lifecycle is not None and current["lifecycle"] != lifecycle:
            return False
        if delivery_state is not ... and current["delivery_state"] != delivery_state:
            return False
    return True


def _provider_tool_completed(path: Path, tool_name: str) -> bool:
    events = _provider_event_summary(path)
    return any(
        call["server"] == "cao-mcp-server"
        and call["tool"] == tool_name
        and call["status"] == "completed"
        and not call["error_present"]
        for call in events["mcp_tool_calls"]
    )


def _agui_observation_diagnostics(
    observation_path: Path,
    expected_group_id: str,
    expected_questions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    from test.live_provider.interaction_agui_restart import read_observations

    observations = read_observations(observation_path)
    created = [item for item in observations if item["event"] == "group_created"]
    waits = [item for item in observations if item["event"] == "wait_returned"]
    ready_waits = [item for item in waits if item.get("state") == "ready"]
    acknowledgements = [item for item in observations if item["event"] == "ack_identity"]
    expected_versions = sorted(
        (question_id, int(identity["version"]))
        for question_id, identity in expected_questions.items()
    )
    created_group_matches = bool(created) and all(
        item.get("group_id") == expected_group_id for item in created
    )
    created_questions_match = bool(created) and all(
        sorted(
            (entry["question_id"], entry["version"])
            for entry in item.get("questions", [])
            if isinstance(entry, dict)
        )
        == expected_versions
        for item in created
    )
    ready_wait_matches = any(
        item.get("group_id") == expected_group_id
        and sorted(
            (entry["question_id"], entry["version"])
            for entry in item.get("question_versions", [])
            if isinstance(entry, dict)
        )
        == expected_versions
        for item in ready_waits
    )
    ack_checks = []
    for item in acknowledgements:
        check = {
            key: item[key]
            for key in (
                "group_matches_created",
                "group_matches_waited",
                "question_versions_match_created",
                "question_versions_match_returned",
                "receipt_hashes_match_returned",
                "ack_succeeded",
                "ack_count",
            )
        }
        ack_checks.append(check)
    return {
        "create_group_seen": bool(created),
        "created_group_matches_database": created_group_matches,
        "created_questions_match_database": created_questions_match,
        "wait_call_count": len(waits),
        "ready_wait_count": len(ready_waits),
        "ready_wait_matches_database": ready_wait_matches,
        "ack_call_count": len(acknowledgements),
        "ack_checks": ack_checks,
    }


def _instrument_ack_identity_service(service: Any) -> Callable[[], dict[str, Any]]:
    """Observe in-process MCP service calls and retain only opaque identity comparisons."""
    state: dict[str, Any] = {
        "created_group_id": None,
        "waited_group_id": None,
        "waited_answers": None,
        "wait_call_count": 0,
        "ready_wait_count": 0,
        "ack_call_count": 0,
        "ack_checks": [],
    }
    lock = threading.Lock()

    create_group = service.create_group

    def capture_create_group(*args: Any, **kwargs: Any) -> Any:
        result = create_group(*args, **kwargs)
        with lock:
            state["created_group_id"] = getattr(result, "group_id", None)
        return result

    wait_for_answers = service.wait_for_answers

    async def capture_wait_for_answers(*args: Any, **kwargs: Any) -> Any:
        result = await wait_for_answers(*args, **kwargs)
        with lock:
            state["wait_call_count"] += 1
            if getattr(result, "state", None) == "ready":
                state["ready_wait_count"] += 1
                state["waited_group_id"] = getattr(result, "group_id", None)
                state["waited_answers"] = tuple(getattr(result, "answers", ()))
        return result

    acknowledge_answers = service.acknowledge_answers

    def capture_acknowledge_answers(
        capability: object, group_id: str, acknowledgements: Any
    ) -> Any:
        with lock:
            state["ack_call_count"] += 1
            check = _ack_identity_match_diagnostics(
                created_group_id=state["created_group_id"],
                waited_group_id=state["waited_group_id"],
                waited_answers=state["waited_answers"],
                ack_group_id=group_id,
                acknowledgements=acknowledgements,
            )
            checks = state["ack_checks"]
            if len(checks) < 8:
                checks.append(check)
        return acknowledge_answers(capability, group_id, acknowledgements)

    setattr(service, "create_group", capture_create_group)
    setattr(service, "wait_for_answers", capture_wait_for_answers)
    setattr(service, "acknowledge_answers", capture_acknowledge_answers)

    def safe_diagnostics() -> dict[str, Any]:
        with lock:
            return {
                "create_group_seen": state["created_group_id"] is not None,
                "wait_call_count": state["wait_call_count"],
                "ready_wait_count": state["ready_wait_count"],
                "ack_call_count": state["ack_call_count"],
                "ack_checks": list(state["ack_checks"]),
            }

    return safe_diagnostics


def _preserve_unclassified_mcp_errors(
    path: Path, *, diagnostic_dir: Path | None = None
) -> Path | None:
    """Keep only unknown CAO MCP error fields in a private review file.

    This opt-in diagnostic is used when the fixed safe classifier cannot map a
    failed MCP tool error. It deliberately excludes arguments, results, and
    every other provider event so a local reviewer can inspect the error while
    the normal provider log cleanup still runs.
    """
    records: dict[str, dict[str, Any]] = {}
    fallback_id = 0
    try:
        stream = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None

    with stream:
        for line in stream:
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            item = event.get("item")
            if (
                not isinstance(item, dict)
                or item.get("type") != "mcp_tool_call"
                or item.get("server") != "cao-mcp-server"
            ):
                continue
            tool = item.get("tool")
            error = item.get("error")
            if (
                not isinstance(tool, str)
                or tool not in INTERACTION_TOOLS
                or error is None
                or _safe_tool_error_code(error) is not None
            ):
                continue

            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id:
                fallback_id += 1
                item_id = f"missing-id-{fallback_id}"
            event_type = event.get("type")
            status = item.get("status")
            records[item_id] = {
                "event_type": (
                    event_type
                    if isinstance(event_type, str) and event_type in _PROVIDER_EVENT_TYPES
                    else "unknown"
                ),
                "tool": tool,
                "status": (
                    status
                    if isinstance(status, str) and status in {"in_progress", "completed", "failed"}
                    else "unknown"
                ),
                "error": error,
            }

    if not records:
        return None

    if diagnostic_dir is None:
        cache_dir = ORIGINAL_HOME / ".cache" / "cao-live-provider"
        if cache_dir.is_symlink():
            raise RuntimeError("unsafe_private_error_diagnostic_directory")
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(cache_dir, 0o700)
        diagnostic_dir = cache_dir / "private-diagnostics"
    if diagnostic_dir.is_symlink():
        raise RuntimeError("unsafe_private_error_diagnostic_directory")
    diagnostic_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = diagnostic_dir.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
    ):
        raise RuntimeError("unsafe_private_error_diagnostic_directory")

    diagnostic_path = diagnostic_dir / f"unclassified-mcp-error-{os.urandom(8).hex()}.json"
    payload = {
        "format": "cao-private-mcp-error-diagnostic-v1",
        "calls": list(records.values()),
    }
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(diagnostic_path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    return diagnostic_path


def _failure_category(output: str, returncode: int | None, timed_out: bool) -> str:
    if timed_out:
        return "timed_out"
    lowered = output.lower()
    if "missing optional dependency @openai/codex-linux-x64" in lowered:
        return "provider_install_incomplete"
    if any(
        item in lowered for item in ("authentication_error", "not logged in", "unauthorized", "401")
    ):
        return "authentication_failed"
    if any(
        item in lowered
        for item in (
            "rate limit",
            "rate-limit",
            "rate_limit_event",
            "overloaded",
            "model_not_found",
            "insufficient_quota",
        )
    ):
        return "provider_or_model_rejected"
    if any(item in lowered for item in ("mcp server", "mcp connection", "failed to initialize")):
        return "mcp_connection_failed"
    if returncode is None:
        return "process_still_running"
    return "cli_exited_without_ack"


def _sanitized_failure_signals(output: str) -> list[str]:
    """Return fixed diagnostic labels without exposing provider log text."""
    lowered = output.lower()
    checks = {
        "codex_linux_optional_dependency_missing": (
            "missing optional dependency @openai/codex-linux-x64",
        ),
        "mcp_spawn_failed": ("failed to spawn", "spawn failed", "enoent"),
        "mcp_handshake_failed": (
            "initialize request",
            "initialization failed",
            "handshake failed",
            "connection closed",
        ),
        "mcp_startup_timeout": ("startup timeout", "timed out waiting for mcp"),
        "mcp_process_exited": (
            "mcp server exited",
            "mcp server failed",
            "failed to start mcp server",
        ),
        "filesystem_write_denied": (
            "read-only file system",
            "permission denied",
            "operation not permitted",
        ),
        "provider_configuration_rejected": (
            "invalid config",
            "failed to parse config",
            "unknown config key",
        ),
        "provider_auth_rejected": (
            "not logged in",
            "unauthorized",
            "authentication_error",
            "401",
            "this api key has expired",
            "this api key is invalid",
            "api key is invalid",
            "create a new api key",
        ),
        "provider_rate_limited": (
            "rate_limit_event",
            "rate limit",
            "rate-limit",
            "usage limit exceeded",
            "insufficient_quota",
        ),
    }
    return sorted(
        signal for signal, markers in checks.items() if any(marker in lowered for marker in markers)
    )


def _failure_diagnostic_flags(output: str) -> dict[str, bool]:
    """Return only fixed boolean classes; never return provider error text."""
    signals = set(_sanitized_failure_signals(output))
    return {name: name in signals for name in _FAILURE_DIAGNOSTIC_NAMES}


def _safe_provider_receipt(result: dict[str, Any]) -> dict[str, Any]:
    """Project a journey result to a strict, content-free JSON receipt."""
    safe: dict[str, Any] = {}
    if result.get("provider") in {"codex", "claude_code", "opencode_cli"}:
        safe["provider"] = result["provider"]
    requested_model = result.get("requested_model")
    if requested_model == CODEX_INTERACTION_MODEL:
        safe["requested_model"] = CODEX_INTERACTION_MODEL
    elif result.get("provider") == "opencode_cli" and requested_model == OPENCODE_INTERACTION_MODEL:
        safe["requested_model"] = OPENCODE_INTERACTION_MODEL
    elif (
        result.get("provider") == "claude_code"
        and result.get("agui_restart_enabled") is True
        and requested_model == CLAUDE_AGUI_RESTART_MODEL
    ):
        safe["requested_model"] = CLAUDE_AGUI_RESTART_MODEL
    version = result.get("version")
    if isinstance(version, str):
        provider = result.get("provider")
        version_pattern = _PROVIDER_VERSION_PATTERNS.get(provider)
        safe["version"] = (
            version
            if version == "unknown"
            or (version_pattern is not None and version_pattern.fullmatch(version))
            else "unknown"
        )
    result_code = result.get("result")
    known_results = {
        "completed_and_acknowledged",
        "cli_unavailable",
        "api_start_failed",
        "capability_install_failed",
        "capability_path_missing",
        "mcp_stdio_handshake",
        "mcp_stdio_handshake_timeout",
        "mcp_stdio_handshake_error",
        "unsupported_provider",
        "unsupported_diagnostic_provider",
        "unsupported_cli_version",
        "mcp_config_missing",
        "mcp_config_invalid",
        "mcp_server_configuration_invalid",
        "unclassified_mcp_error_pending_inspection",
        "private_error_diagnostic_capture_failed",
        "mcp_tool_call_failed",
        "mcp_tool_returned_without_question",
        "timed_out_after_question",
        "question_created_but_not_acknowledged",
        "incomplete_journey",
        "harness_error",
        "cli_exited_without_ack",
        "provider_install_incomplete",
        "authentication_failed",
        "provider_or_model_rejected",
        "mcp_connection_failed",
        "process_still_running",
        "mcp_catalog_connected",
        "cli_exited_without_mcp",
        "mcp_catalog_startup_timeout",
        "opencode_cli_unavailable",
        "setup_error",
        "model_run_not_approved",
        "timed_out_before_question",
        "provider_auth_unavailable",
        "provider_model_command_mismatch",
        "agent_tool_scope_mismatch",
        "explicit_model_required",
        "unexpected_question_shape",
        "unexpected_question_contract",
        "unexpected_question_count",
        "unexpected_question_keys",
        "unexpected_question_kind",
        "question_not_required",
        "invalid_choice_question_kind",
        "fixed_choice_not_offered",
        "unexpected_question_group",
        "agui_snapshot_identity_mismatch",
        "agui_restart_identity_changed",
        "native_provider_not_complete",
        "prompt_not_submitted",
        "model_completed_without_tool",
        "provider_error",
    }
    if isinstance(result_code, str):
        safe["result"] = (
            result_code
            if result_code in known_results
            or re.fullmatch(r"human_answer_http_[1-5][0-9]{2}", result_code)
            else "unknown"
        )
    phase = result.get("phase")
    if isinstance(phase, str):
        safe["phase"] = phase if phase in _SAFE_PROVIDER_PHASES else "unknown"
    error_type = result.get("error_type")
    if isinstance(error_type, str):
        safe["error_type"] = error_type if error_type in _SAFE_EXCEPTION_TYPES else "unknown"

    for name in (
        "exit_code",
        "question_count",
        "human_answers_posted",
        "elapsed_seconds",
        "reported_cost_usd",
        "cost_limit_usd",
        "model_usage_records",
        "server_catalog_tool_count",
        "normalization_diagnostics",
        "metadata_item_count",
        "metadata_item_count_after_probe",
        "total_tool_count",
        "api_worker_restarts",
        "agui_snapshot_count",
        "mcp_catalog_unexpected_tool_count",
        "mcp_catalog_observation_count",
        "mcp_tool_unexpected_call_count",
    ):
        value = result.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and -255 <= value <= _MAX_SAFE_COUNTER:
            safe[name] = value
        elif isinstance(value, float) and 0 <= value <= 10**6:
            safe[name] = round(value, 3)

    turn_sequence = result.get("turn_sequence")
    if type(turn_sequence) is int and 1 <= turn_sequence <= 10**9:
        safe["turn_sequence"] = turn_sequence

    native_tui_stage = result.get("native_tui_stage")
    if isinstance(native_tui_stage, str):
        safe["native_tui_stage"] = (
            native_tui_stage if native_tui_stage in _SAFE_NATIVE_TUI_STAGES else "unknown"
        )
    native_turn_stage = result.get("native_turn_stage")
    if isinstance(native_turn_stage, str):
        safe["native_turn_stage"] = (
            native_turn_stage if native_turn_stage in _SAFE_NATIVE_TURN_STAGES else "unknown"
        )
    native_mcp_log_status = result.get("native_mcp_log_status")
    if isinstance(native_mcp_log_status, str):
        safe["native_mcp_log_status"] = (
            native_mcp_log_status
            if native_mcp_log_status in _SAFE_NATIVE_MCP_LOG_STATUSES
            else "unknown"
        )

    for name in (
        "mcp_observer_status",
        "mcp_catalog_observation_status",
        "mcp_tool_observation_status",
    ):
        value = result.get(name)
        if isinstance(value, str):
            safe[name] = value if value in {"observed", "unobserved", "incomplete"} else "unknown"

    for name in (
        "provider_home_isolated",
        "host_filesystem_read_only",
        "raw_provider_output_printed",
        "temporary_files_removed",
        "model_configured",
        "task_submitted",
        "mcp_connected",
        "global_config_modified",
        "agent_tool_scope_exact",
        "work_grant_used",
        "evidence_saved",
        "configured_enabled_tools_are_exact",
        "create_tool_probe_attempted",
        "create_tool_probe_is_error",
        "create_tool_probe_success",
        "metadata_tool_present",
        "metadata_call_error",
        "private_capability_file_available",
        "required_tools_present",
        "operator_auth_unchanged",
        "real_git_repository",
        "agui_restart_enabled",
        "agui_identity_stable",
        "agui_projection_redacted",
        "agui_final_acknowledged",
        "agui_ledger_identity_stable",
        "agui_observation_complete",
        "agui_exact_ack_observed",
        "native_provider_processes_stable",
        "database_target_stable",
        "native_mini_initialized",
        "native_mcp_ready",
        "native_turn_observed",
        "native_provider_status_before_deadline",
        "mcp_observer_shutdown_observed",
        "private_scope_verified",
    ):
        if isinstance(result.get(name), bool):
            safe[name] = result[name]

    scope_attestation = result.get("scope_attestation")
    if (
        isinstance(scope_attestation, dict)
        and set(scope_attestation) == _SAFE_SCOPE_ATTESTATION_KEYS
        and all(type(scope_attestation.get(key)) is bool for key in _SAFE_SCOPE_ATTESTATION_KEYS)
    ):
        safe["scope_attestation"] = {
            key: scope_attestation[key] for key in sorted(_SAFE_SCOPE_ATTESTATION_KEYS)
        }

    final_state = result.get("native_provider_final_state")
    if isinstance(final_state, str):
        safe["native_provider_final_state"] = (
            final_state
            if final_state
            in {
                "IDLE",
                "COMPLETED",
                "PROCESSING",
                "ERROR",
                "UNKNOWN",
                "WAITING_USER_ANSWER",
                "WAITING_QUOTA",
            }
            else "UNKNOWN"
        )
    if result.get("native_provider_state_source") == "tmux_live_pane_via_production_provider":
        safe["native_provider_state_source"] = "tmux_live_pane_via_production_provider"
    if "native_provider_exit_status" in result:
        exit_status = result.get("native_provider_exit_status")
        safe["native_provider_exit_status"] = (
            exit_status
            if isinstance(exit_status, int)
            and not isinstance(exit_status, bool)
            and -255 <= exit_status <= 255
            else None
        )
    if isinstance(result.get("native_provider_exit_status_observed"), bool):
        safe["native_provider_exit_status_observed"] = result[
            "native_provider_exit_status_observed"
        ]

    if isinstance(result.get("provider_event_counts"), dict):
        safe["provider_event_counts"] = _safe_count_map(
            result["provider_event_counts"], _PROVIDER_EVENT_TYPES | {"session.error"}
        )
    if isinstance(result.get("provider_item_type_counts"), dict):
        safe["provider_item_type_counts"] = _safe_count_map(
            result["provider_item_type_counts"], _PROVIDER_ITEM_TYPES
        )
    if isinstance(result.get("model_usage_counts"), dict):
        safe["model_usage_counts"] = _safe_count_map(
            result["model_usage_counts"], _MODEL_USAGE_FIELDS
        )
    opencode_error_names = {
        "ProviderAuthError",
        "GatewayAuthenticationError",
        "NeedsAuthError",
        "ProviderModelNotFoundError",
        "GatewayModelNotFoundError",
        "UnresolvedProviderVariablesError",
        "ConfigError",
        "InvalidConfigurationError",
        "ContentFilterError",
        "AgentNotFoundError",
        "APIError",
        "APIConnectionError",
        "APIConnectionTimeoutError",
    }
    if isinstance(result.get("opencode_error_name_counts"), dict):
        safe["opencode_error_name_counts"] = _safe_count_map(
            result["opencode_error_name_counts"], opencode_error_names
        )
    opencode_failure_categories = {
        "provider_auth_rejected",
        "provider_auth_required",
        "provider_model_unavailable",
        "provider_configuration_missing",
        "provider_configuration_rejected",
        "provider_refusal",
        "agent_unavailable",
        "provider_api_error",
        "provider_connection_error",
        "provider_connection_timeout",
        "provider_rate_limited",
        "tool_unavailable",
        "no_tools_available",
        "tool_calling_unavailable",
        "tool_permission_denied",
        "provider_step_incomplete",
    }
    if isinstance(result.get("opencode_failure_category_counts"), dict):
        safe["opencode_failure_category_counts"] = _safe_count_map(
            result["opencode_failure_category_counts"], opencode_failure_categories
        )
    for name in ("unclassified_error_event_count", "unexpected_tool_name_count"):
        value = result.get(name)
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= _MAX_SAFE_COUNTER
        ):
            safe[name] = value
    if isinstance(result.get("tool_calling_observed"), bool):
        safe["tool_calling_observed"] = result["tool_calling_observed"]
    if result.get("cleanup_status") in {"complete", "incomplete"}:
        safe["cleanup_status"] = result["cleanup_status"]
    if isinstance(result.get("cleanup_failure_categories"), list):
        safe["cleanup_failure_categories"] = sorted(
            {
                value
                for value in result["cleanup_failure_categories"]
                if value in _SAFE_CLEANUP_FAILURES
            }
        )
    if isinstance(result.get("failure_diagnostics"), dict):
        safe["failure_diagnostics"] = {
            key: result["failure_diagnostics"][key]
            for key in _FAILURE_DIAGNOSTIC_NAMES
            if isinstance(result["failure_diagnostics"].get(key), bool)
        }
    message_diagnostics = result.get("agent_message_diagnostics")
    message_diagnostic_names = (
        "scan_complete",
        "agent_message_seen",
        *_AGENT_MESSAGE_DIAGNOSTIC_PATTERNS,
    )
    if isinstance(message_diagnostics, dict) and all(
        isinstance(message_diagnostics.get(key), bool) for key in message_diagnostic_names
    ):
        safe["agent_message_diagnostics"] = {
            key: message_diagnostics[key] for key in message_diagnostic_names
        }
    if isinstance(result.get("sanitized_failure_signals"), list):
        safe["sanitized_failure_signals"] = sorted(
            {
                value
                for value in result["sanitized_failure_signals"]
                if value in _FAILURE_DIAGNOSTIC_NAMES
            }
        )
    if isinstance(result.get("mcp_tool_names"), list):
        safe["mcp_tool_names"] = sorted(
            {
                value
                for value in result["mcp_tool_names"]
                if isinstance(value, str) and value in INTERACTION_TOOLS
            }
        )
    if isinstance(result.get("observed_interaction_tools"), list):
        safe["observed_interaction_tools"] = sorted(
            {
                value
                for value in result["observed_interaction_tools"]
                if isinstance(value, str) and value in INTERACTION_TOOLS
            }
        )
    if isinstance(result.get("mcp_catalog_tool_names"), list):
        safe["mcp_catalog_tool_names"] = sorted(
            {
                value
                for value in result["mcp_catalog_tool_names"]
                if isinstance(value, str) and value in INTERACTION_TOOLS
            }
        )
    if isinstance(result.get("configured_mcp_server_names"), list):
        safe["configured_mcp_server_names"] = [
            "cao-mcp-server"
            for value in result["configured_mcp_server_names"]
            if value == "cao-mcp-server"
        ]
    for name, allowed in (
        ("provider_item_types", _PROVIDER_ITEM_TYPES),
        ("question_kinds", {"text", "single_choice", "multi_choice", "approval"}),
        ("question_lifecycles", {"open", "answered", "expired", "cancelled", "stale"}),
        ("delivery_states", {"agent_acknowledged", "pending", "unknown", "failed", "not_required"}),
        ("interaction_tools_present", INTERACTION_TOOLS),
        ("configured_enabled_tool_names", INTERACTION_TOOLS),
        (
            "agent_tool_scope",
            {
                "cao_mcp_server_cao_ask_user",
                "cao_mcp_server_cao_wait_for_user_answers",
                "cao_mcp_server_cao_acknowledge_user_answers",
            },
        ),
    ):
        values = result.get(name)
        if isinstance(values, list):
            if name == "delivery_states":
                safe[name] = [value for value in values if value in allowed]
            else:
                safe[name] = sorted({value for value in values if value in allowed})

    calls = result.get("mcp_tool_calls")
    if isinstance(calls, list):
        safe_calls = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            server = "cao-mcp-server" if call.get("server") == "cao-mcp-server" else "other"
            tool = call.get("tool") if call.get("tool") in INTERACTION_TOOLS else "other"
            status = (
                call.get("status")
                if call.get("status") in {"in_progress", "completed", "failed"}
                else "unknown"
            )
            error_code = call.get("error_code")
            if (
                error_code not in _SAFE_INTERACTION_ERROR_CODES
                and error_code not in set(_SAFE_PROTOCOL_ERROR_CODES.values())
                and error_code not in {category for category, _ in _SAFE_PROVIDER_ERROR_PATTERNS}
            ):
                error_code = None
            safe_call = {
                "server": server,
                "tool": tool,
                "status": status,
                "error_present": call.get("error_present") is True,
                "result_present": call.get("result_present") is True,
                "error_code": error_code,
            }
            diagnostic_keys = {
                "mcp_result_shape",
                "mcp_is_error",
                "mcp_result_success",
                "mcp_content_block_types",
                "mcp_structured_content_present",
                "mcp_result_error_code",
                "mcp_unavailable_cause",
            }
            if any(key in call for key in diagnostic_keys):
                shape = call.get("mcp_result_shape")
                safe_call["mcp_result_shape"] = (
                    shape
                    if isinstance(shape, str) and shape in _SAFE_MCP_RESULT_SHAPES
                    else "other"
                )
                is_error = call.get("mcp_is_error")
                safe_call["mcp_is_error"] = (
                    is_error
                    if isinstance(is_error, str) and is_error in _SAFE_MCP_IS_ERROR_STATES
                    else "invalid"
                )
                success = call.get("mcp_result_success")
                safe_call["mcp_result_success"] = (
                    success
                    if isinstance(success, str) and success in _SAFE_MCP_SUCCESS_STATES
                    else "unknown"
                )
                content_types = call.get("mcp_content_block_types")
                safe_call["mcp_content_block_types"] = (
                    sorted(
                        {
                            value if value in _SAFE_MCP_CONTENT_BLOCK_TYPES else "other"
                            for value in content_types
                            if isinstance(value, str)
                        }
                    )
                    if isinstance(content_types, list)
                    else []
                )
                safe_call["mcp_structured_content_present"] = (
                    call.get("mcp_structured_content_present") is True
                )
                result_error_code = call.get("mcp_result_error_code")
                safe_call["mcp_result_error_code"] = (
                    result_error_code
                    if isinstance(result_error_code, str)
                    and result_error_code in _SAFE_MCP_RESULT_ERROR_CODES
                    else None
                )
                unavailable_cause = call.get("mcp_unavailable_cause")
                safe_call["mcp_unavailable_cause"] = (
                    unavailable_cause
                    if isinstance(unavailable_cause, str)
                    and unavailable_cause in _UNAVAILABLE_DETAIL_CAUSES.values()
                    else None
                )
            safe_calls.append(safe_call)
        safe["mcp_tool_calls"] = safe_calls

    identity = result.get("ack_identity_diagnostics")
    if isinstance(identity, dict):
        safe_identity: dict[str, Any] = {}
        allowed_identity_keys = {
            "ack_call_count",
            "ack_checks",
            "create_group_seen",
            "created_group_matches_database",
            "created_questions_match_database",
            "ready_wait_count",
            "ready_wait_matches_database",
            "wait_call_count",
        }
        for key in allowed_identity_keys:
            value = identity.get(key)
            if isinstance(value, bool):
                safe_identity[key] = value
            elif isinstance(value, int) and 0 <= value <= _MAX_SAFE_COUNTER:
                safe_identity[key] = value
        checks = identity.get("ack_checks")
        if isinstance(checks, list):
            safe_identity["ack_checks"] = [
                {
                    key: value
                    for key, value in check.items()
                    if key
                    in {
                        "ack_count_matches_returned_answers",
                        "ack_group_matches_created_group",
                        "ack_group_matches_wait_group",
                        "ack_question_ids_match_returned_answers",
                        "ack_receipts_match_returned_answers",
                        "ack_versions_match_returned_answers",
                        "group_matches_created",
                        "group_matches_waited",
                        "question_versions_match_created",
                        "question_versions_match_returned",
                        "receipt_hashes_match_returned",
                        "ack_succeeded",
                        "comparison_available",
                    }
                    and isinstance(value, bool)
                    or key == "ack_count"
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                    and 0 <= value <= 20
                }
                for check in checks
                if isinstance(check, dict)
            ]
        safe["ack_identity_diagnostics"] = safe_identity

    offer_summary = result.get("opencode_http_tool_offer")
    if result.get("provider") == "opencode_cli" and isinstance(offer_summary, dict):
        from test.live_provider.opencode_http_tool_offer_observer import safe_receipt_projection

        safe["opencode_http_tool_offer"] = safe_receipt_projection(offer_summary)

    return safe


def validate_scope_attestation(receipt: dict[str, Any], provider: str) -> bool:
    """Validate the content-free scope proof emitted by a provider journey.

    This is intentionally public to source-bound external runners: they must
    validate the receipt after the journey restores module globals, rather than
    inspecting those globals as if they still described the private run.
    """
    if provider != "opencode_cli" or receipt.get("private_scope_verified") is not True:
        return False
    attestation = receipt.get("scope_attestation")
    return (
        isinstance(attestation, dict)
        and set(attestation) == _SAFE_SCOPE_ATTESTATION_KEYS
        and all(type(attestation.get(key)) is bool and attestation[key] for key in attestation)
    )


def _scope_attestation_is_valid(receipt: dict[str, Any], provider: str) -> bool:
    """Backward-compatible private alias for the external-runner contract."""
    return validate_scope_attestation(receipt, provider)


def _provider_cleanup_succeeded(
    receipt: dict[str, Any], *, require_auth_unchanged: bool = False
) -> bool:
    """Require verified private cleanup before reporting a successful CLI exit."""
    if receipt.get("cleanup_status") != "complete":
        return False
    if receipt.get("temporary_files_removed") is not True:
        return False
    if require_auth_unchanged and receipt.get("operator_auth_unchanged") is not True:
        return False
    return True


def _provider_journey_succeeded(
    receipt: dict[str, Any], *, require_auth_unchanged: bool = False
) -> bool:
    """Apply the closed success contract after the safe receipt projection."""
    if receipt.get("result") != "completed_and_acknowledged" or not _provider_cleanup_succeeded(
        receipt,
        require_auth_unchanged=require_auth_unchanged,
    ):
        return False
    if receipt.get("provider") != "opencode_cli":
        return True
    return (
        receipt.get("requested_model") == OPENCODE_INTERACTION_MODEL
        and receipt.get("question_count") == 2
        and receipt.get("human_answers_posted") == 2
        and receipt.get("delivery_states") == ["agent_acknowledged", "agent_acknowledged"]
        and receipt.get("agui_restart_enabled") is True
        and receipt.get("api_worker_restarts") == 1
        and receipt.get("agui_snapshot_count") == 3
        and receipt.get("agui_identity_stable") is True
        and receipt.get("agui_projection_redacted") is True
        and receipt.get("agui_final_acknowledged") is True
        and receipt.get("agui_ledger_identity_stable") is True
        and receipt.get("agui_observation_complete") is True
        and receipt.get("agui_exact_ack_observed") is True
        and receipt.get("native_provider_processes_stable") is True
        and receipt.get("database_target_stable") is True
        and receipt.get("native_mcp_ready") is True
        and receipt.get("native_provider_final_state") in {"IDLE", "COMPLETED"}
        and receipt.get("native_provider_status_before_deadline") is True
        and receipt.get("native_provider_state_source") == "tmux_live_pane_via_production_provider"
        and receipt.get("native_provider_exit_status") is None
        and receipt.get("native_provider_exit_status_observed") is False
        and receipt.get("operator_auth_unchanged") is True
        and validate_scope_attestation(receipt, "opencode_cli")
    )


def _safe_count_map(values: dict[str, Any], allowed: Any) -> dict[str, int]:
    return {
        key: value
        for key, value in sorted(values.items())
        if key in allowed
        and isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= _MAX_SAFE_COUNTER
    }


async def _mcp_stdio_handshake(
    command: list[str],
    env: dict[str, str],
    interaction_tool_names: set[str],
    stderr_stream: TextIO,
    progress: dict[str, str],
    *,
    probe_create_tool: bool = False,
) -> dict[str, Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    progress["stage"] = "spawn"
    async with stdio_client(
        StdioServerParameters(command=command[0], args=command[1:], env=env),
        errlog=stderr_stream,
    ) as (read_stream, write_stream):
        progress["stage"] = "initialize"
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            progress["stage"] = "list_tools"
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            required = {
                "cao_ask_user",
                "cao_wait_for_user_answers",
                "cao_acknowledge_user_answers",
            }
            progress["stage"] = "list_interactions"
            metadata_call = await session.call_tool("cao_list_interactions", {"limit": 1})
            metadata_error = bool(getattr(metadata_call, "isError", False))

            def item_count(result: Any) -> int | None:
                content_text = "\n".join(
                    block.text
                    for block in getattr(result, "content", [])
                    if getattr(block, "type", None) == "text"
                    and isinstance(getattr(block, "text", None), str)
                )
                try:
                    decoded = json.loads(content_text)
                except (ValueError, TypeError):
                    return None
                if not isinstance(decoded, dict):
                    return None
                items = decoded.get("items")
                return len(items) if isinstance(items, list) else None

            metadata_item_count = item_count(metadata_call)
            probe_summary: dict[str, Any] = {
                "create_tool_probe_attempted": probe_create_tool,
            }
            if probe_create_tool:
                progress["stage"] = "create_interaction_probe"
                probe_call = await session.call_tool(
                    "cao_ask_user",
                    {
                        "operation_key": "mcp-diagnostic-" + os.urandom(12).hex(),
                        "questions": list(QUESTION_SPECS),
                        "wait_for_required": True,
                    },
                )
                probe_structured = getattr(probe_call, "structuredContent", None)
                probe_summary.update(
                    {
                        "create_tool_probe_is_error": bool(getattr(probe_call, "isError", False)),
                        "create_tool_probe_success": (
                            probe_structured.get("success")
                            if isinstance(probe_structured, dict)
                            and isinstance(probe_structured.get("success"), bool)
                            else None
                        ),
                        "create_tool_probe_error_code": _safe_tool_error_code(probe_structured),
                    }
                )
                progress["stage"] = "list_interactions_after_probe"
                after_probe = await session.call_tool("cao_list_interactions", {"limit": 10})
                after_probe_count = item_count(after_probe)
                probe_summary["metadata_item_count_after_probe"] = after_probe_count
                if (
                    probe_summary["create_tool_probe_success"] is None
                    and isinstance(metadata_item_count, int)
                    and isinstance(after_probe_count, int)
                ):
                    probe_summary["create_tool_probe_success"] = (
                        after_probe_count > metadata_item_count
                    )

            progress["stage"] = "complete"
            interaction_names = sorted(names & interaction_tool_names)
            return {
                "total_tool_count": len(names),
                "interaction_tools_present": interaction_names,
                "required_tools_present": required <= names,
                "metadata_tool_present": "cao_list_interactions" in names,
                "metadata_call_error": metadata_error,
                "metadata_item_count": metadata_item_count,
                **probe_summary,
            }


def _source_owner_for_journey(auth_module: Any, *, agui_restart: bool) -> Any:
    """Use the API worker's verified loopback identity only for AG-UI mode."""
    if agui_restart:
        # This factory fails closed when authentication is configured and yields
        # the same trusted local principal as the auth-off loopback API worker.
        return auth_module.local_operator_principal()
    return auth_module._verified_principal(
        "https://live-interaction.invalid",
        "live-acceptance-owner",
        [auth_module.SCOPE_READ, auth_module.SCOPE_WRITE, auth_module.SCOPE_ADMIN],
        "jwt",
    )


def _run_opencode_agui_restart() -> dict[str, Any]:
    """Run the native OpenCode lane with its fixed approved model and restart opt-in."""
    from test.live_provider import opencode_interaction_journey

    result = opencode_interaction_journey.run(
        model=OPENCODE_INTERACTION_MODEL,
        run_model=True,
        agui_restart=True,
    )
    result["requested_model"] = OPENCODE_INTERACTION_MODEL
    return result


def run(
    provider_name: str,
    *,
    diagnose_mcp: bool = False,
    probe_create_tool: bool = False,
    preserve_unclassified_mcp_error: bool = False,
    agui_restart: bool = False,
) -> dict[str, Any]:
    if provider_name == "opencode_cli":
        if not agui_restart or diagnose_mcp or probe_create_tool or preserve_unclassified_mcp_error:
            return {"provider": provider_name, "result": "unsupported_provider"}
        return _run_opencode_agui_restart()
    if provider_name not in {"codex", "claude_code"}:
        return {"provider": provider_name, "result": "unsupported_provider"}

    root = _private_root()
    receipt_holder: list[dict[str, Any]] = []

    def remember_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
        receipt_holder[:] = [receipt]
        return receipt

    phase = "initialize"
    proc: subprocess.Popen[str] | None = None
    server = None
    server_thread: threading.Thread | None = None
    listener: socket.socket | None = None
    api_worker = None
    api_worker_stdout: TextIO | None = None
    api_worker_stderr: TextIO | None = None
    api_worker_spec = None
    database_module = None
    output_path = root / "provider-output.log"
    error_path = root / "provider-error.log"
    started_at: float | None = None
    database_context = None
    database_context_entered = False
    codex_auth_source: Path | None = None
    codex_auth_before: tuple[int, ...] | None = None
    agent_tool_scope_exact: bool | None = None
    agui_restart_count = 0
    agui_snapshot_count = 0
    agui_identity_stable: bool | None = None
    agui_projection_redacted: bool | None = None
    agui_final_acknowledged: bool | None = None
    agui_restart_completed = False
    expected_agui_questions: dict[str, dict[str, Any]] = {}
    expected_agui_group_id: str | None = None
    changed_environment = (
        "CAO_HOME_DIR",
        "CAO_DATABASE_CONFIG_FILE",
        "CAO_API_HOST",
        "CAO_API_PORT",
        "CAO_AUTH_MODE",
    )
    previous_environment = {key: os.environ.get(key) for key in changed_environment}
    try:
        # All CAO path constants are initialized after these environment values
        # are installed, so provider files and the database config stay in this tree.
        cao_home = root / "cao"
        cao_home.mkdir(mode=0o700)
        os.environ.update(
            {
                "CAO_HOME_DIR": str(cao_home),
                "CAO_DATABASE_CONFIG_FILE": str(cao_home / "database-config.json"),
                "CAO_API_HOST": "127.0.0.1",
                "CAO_AUTH_MODE": "off",
            }
        )

        # Provision a disposable, owner-migrated database before importing the
        # runtime's module-level settings. The provider child receives only the
        # runtime target credential through this private config path.
        from test.postgres.fixtures import isolated_live_main_target

        database_context = isolated_live_main_target(cao_home)
        target = database_context.__enter__()
        database_context_entered = True

        import httpx

        from cli_agent_orchestrator import constants
        from cli_agent_orchestrator.clients import database
        from cli_agent_orchestrator.clients.interaction_repository import InteractionRepository
        from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider
        from cli_agent_orchestrator.providers.codex import CodexProvider
        from cli_agent_orchestrator.security import auth
        from cli_agent_orchestrator.services import terminal_service
        from cli_agent_orchestrator.services.interactions.capability import (
            canonical_tool_fingerprint,
        )
        from cli_agent_orchestrator.services.interactions.service import InteractionService

        database_module = database

        cli_env = _base_cli_env(root, provider_name)
        codex_home_overlay = None
        if provider_name == "codex":
            codex_auth_source = _codex_auth_path()
            auth_identity: list[tuple[int, ...]] = []
            codex_home_overlay = _prepare_codex_home_overlay(root, auth_identity=auth_identity)
            codex_auth_before = auth_identity[0] if auth_identity else None
        executable = _codex_executable() if provider_name == "codex" else "claude"
        version_work_dir = root / "version-probe"
        version_work_dir.mkdir(mode=0o700)
        provider_version = _safe_version(
            executable, cli_env, root, version_work_dir, codex_home_overlay
        )
        if provider_version == "unknown":
            return remember_receipt(
                {
                    "provider": provider_name,
                    "version": "unknown",
                    "result": "cli_unavailable",
                    "phase": "version_probe",
                }
            )

        terminal_id = "ac" + os.urandom(3).hex()
        incarnation_id = "live-" + os.urandom(12).hex()
        session_name = "cao-live-" + os.urandom(6).hex()
        profile_name = "live-interaction-" + os.urandom(4).hex()
        operation_key = "live-provider-" + os.urandom(12).hex()
        prompt = _prompt(operation_key, recover_api_restart=agui_restart)
        work_dir = root / "work"
        work_dir.mkdir(mode=0o700)
        os.environ["CAO_API_PORT"] = "0"
        _install_profile(
            root, profile_name, provider_name, 1, terminal_id, agui_restart=agui_restart
        )

        database.init_db()
        database.create_terminal(
            terminal_id,
            session_name,
            "window-0",
            provider_name,
            agent_profile=profile_name,
            allowed_tools=["@cao-mcp-server"],
            working_directory=str(work_dir),
            session_incarnation_id=incarnation_id,
            new_session_incarnation=True,
        )
        owner = _source_owner_for_journey(auth, agui_restart=agui_restart)
        service = InteractionService(InteractionRepository(target))
        service.initialize()
        safe_ack_identity_diagnostics = None

        if agui_restart:
            from test.live_provider.interaction_agui_restart import (
                ApiWorkerController,
                build_api_worker_spec,
                open_private_log,
            )

            port = _free_loopback_port()
            os.environ["CAO_API_PORT"] = str(port)
            api_worker_spec = build_api_worker_spec(
                source_root=Path(__file__).resolve().parents[2],
                run_root=root,
                python_executable=sys.executable,
                port=port,
                parent_env=os.environ,
            )
            for private_directory in (
                root / "home",
                root / "tmp",
                root / "tmux",
                root / "xdg-cache",
                root / "xdg-config",
            ):
                _ensure_private_directory(private_directory)
            api_worker_stdout = open_private_log(root / "api-worker.stdout.log")
            api_worker_stderr = open_private_log(root / "api-worker.stderr.log")
            api_worker = ApiWorkerController(
                api_worker_spec,
                stdout=api_worker_stdout,
                stderr=api_worker_stderr,
            )
        else:
            import uvicorn
            from fastapi import FastAPI

            from cli_agent_orchestrator.api.interactions import router
            from cli_agent_orchestrator.security.auth import get_current_principal

            safe_ack_identity_diagnostics = _instrument_ack_identity_service(service)
            app = FastAPI()
            app.state.interaction_service = service
            app.dependency_overrides[get_current_principal] = lambda: owner
            app.include_router(router)

            @app.get("/terminals/{requested_terminal_id}")
            def terminal_context(requested_terminal_id: str):
                if requested_terminal_id != terminal_id:
                    return {"error": "not_found"}
                return {
                    "id": terminal_id,
                    "session_name": session_name,
                    "provider": provider_name,
                    "allowed_tools": ["@cao-mcp-server"],
                }

            @app.get("/terminals/{requested_terminal_id}/working-directory")
            def working_directory(requested_terminal_id: str):
                return {"working_directory": str(work_dir)}

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
                        "provider": provider_name,
                        "version": provider_version,
                        "result": "api_start_failed",
                    }
                )

        _install_profile(
            root, profile_name, provider_name, port, terminal_id, agui_restart=agui_restart
        )
        if provider_name == "codex":
            provider_instance = CodexProvider(
                terminal_id,
                session_name,
                "window-0",
                profile_name,
                allowed_tools=["@cao-mcp-server"],
            )
        else:
            provider_instance = ClaudeCodeProvider(
                terminal_id,
                session_name,
                "window-0",
                profile_name,
                allowed_tools=["@cao-mcp-server"],
            )
        capability_installed = terminal_service._install_interaction_source_capability(
            provider_instance,
            service,
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            session_name=session_name,
            provider=provider_name,
            provider_version=provider_version,
            execution_mode="local",
            profile_revision="live-provider-acceptance",
            allowed_tool_fingerprint=canonical_tool_fingerprint(["@cao-mcp-server"])[0],
            principal=owner,
        )
        if not capability_installed:
            return remember_receipt(
                {
                    "provider": provider_name,
                    "version": provider_version,
                    "result": "capability_install_failed",
                }
            )
        if not provider_instance.interaction_capability_file:
            return remember_receipt(
                {
                    "provider": provider_name,
                    "version": provider_version,
                    "result": "capability_path_missing",
                }
            )

        if agui_restart:
            if api_worker is None:
                raise RuntimeError("api_worker_not_configured")
            phase = "api_worker_start"
            api_worker.start()

        from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile

        profile = load_agent_profile(profile_name)
        if provider_name == "codex":
            provider_command = provider_instance._build_codex_command()
            command = _codex_command(
                provider_command,
                work_dir,
                prompt,
                executable_override=executable,
            )
        else:
            provider_command = provider_instance._build_claude_command(profile)
            command = _claude_command(provider_command, prompt)
            if agui_restart and not _command_has_exact_model(command, CLAUDE_AGUI_RESTART_MODEL):
                return remember_receipt(
                    {
                        "provider": provider_name,
                        "version": provider_version,
                        "result": "explicit_model_required",
                        "phase": "prepare",
                        "agui_restart_enabled": True,
                    }
                )

        if provider_name == "claude_code":
            cli_env.update(
                {
                    "CAO_HOME_DIR": str(root / "cao"),
                    "CAO_API_HOST": "127.0.0.1",
                    "CAO_API_PORT": str(port),
                    "MCP_TIMEOUT": "65000",
                    "MCP_CONNECT_TIMEOUT_MS": "65000",
                }
            )
        else:
            cli_env.update(
                {
                    "CAO_HOME_DIR": str(root / "cao"),
                    "CAO_API_HOST": "127.0.0.1",
                    "CAO_API_PORT": str(port),
                }
            )

        if diagnose_mcp:
            if provider_name != "codex":
                return remember_receipt(
                    {
                        "provider": provider_name,
                        "version": provider_version,
                        "result": "unsupported_diagnostic_provider",
                    }
                )
            from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile
            from cli_agent_orchestrator.utils.mcp_resolution import resolve_mcp_server_config

            profile = load_agent_profile(profile_name)
            servers = provider_instance.interaction_mcp_servers(profile.mcpServers)
            server_config = servers.get("cao-mcp-server")
            if not isinstance(server_config, dict):
                return remember_receipt(
                    {
                        "provider": provider_name,
                        "version": provider_version,
                        "result": "mcp_config_missing",
                    }
                )
            server_config = resolve_mcp_server_config(server_config)
            server_command = server_config.get("command")
            server_args = server_config.get("args", [])
            if not isinstance(server_command, str) or not isinstance(server_args, list):
                return remember_receipt(
                    {
                        "provider": provider_name,
                        "version": provider_version,
                        "result": "mcp_config_invalid",
                    }
                )
            mcp_env = _diagnostic_mcp_environment(root, port, terminal_id, server_config)
            stdio_command = _sandbox_command(
                [server_command, *server_args],
                root,
                work_dir,
                codex_home_overlay,
            )
            configured_tools = server_config.get("enabled_tools")
            if not isinstance(configured_tools, list) or not all(
                isinstance(tool, str) for tool in configured_tools
            ):
                configured_tools = []
            configured_tools = sorted(set(configured_tools))
            configured_env = server_config.get("env")
            capability_path = (
                configured_env.get("CAO_INTERACTION_CAPABILITY_FILE")
                if isinstance(configured_env, dict)
                else None
            )
            capability_file_private = False
            if isinstance(capability_path, str):
                try:
                    capability_file_private = (
                        Path(capability_path).is_file()
                        and Path(capability_path).stat().st_mode & 0o777 == 0o600
                    )
                except OSError:
                    pass
            started_at = time.monotonic()
            progress: dict[str, str] = {"stage": "prepare"}
            try:
                with _open_private_log(root / "mcp-stderr.log") as mcp_stderr:
                    diagnostic = asyncio.run(
                        asyncio.wait_for(
                            _mcp_stdio_handshake(
                                stdio_command,
                                mcp_env,
                                set(INTERACTION_TOOLS),
                                mcp_stderr,
                                progress,
                                probe_create_tool=probe_create_tool,
                            ),
                            timeout=60,
                        ),
                    )
                diagnostic.update(
                    {
                        "configured_mcp_server_names": sorted(servers),
                        "configured_enabled_tool_names": configured_tools,
                        "configured_enabled_tools_are_exact": set(configured_tools)
                        == set(INTERACTION_TOOLS),
                        "private_capability_file_available": capability_file_private,
                    }
                )
                return remember_receipt(
                    {
                        "provider": provider_name,
                        "version": provider_version,
                        "result": "mcp_stdio_handshake",
                        "elapsed_seconds": round(time.monotonic() - started_at, 2),
                        **diagnostic,
                        "raw_provider_output_printed": False,
                        "temporary_files_removed": True,
                    }
                )
            except TimeoutError:
                return remember_receipt(
                    {
                        "provider": provider_name,
                        "version": provider_version,
                        "result": "mcp_stdio_handshake_timeout",
                        "phase": progress.get("stage", "unknown"),
                        "elapsed_seconds": round(time.monotonic() - started_at, 2),
                        "raw_provider_output_printed": False,
                        "temporary_files_removed": True,
                    }
                )
            except Exception as exc:
                return remember_receipt(
                    {
                        "provider": provider_name,
                        "version": provider_version,
                        "result": "mcp_stdio_handshake_error",
                        "error_type": type(exc).__name__,
                        "elapsed_seconds": round(time.monotonic() - started_at, 2),
                        "raw_provider_output_printed": False,
                        "temporary_files_removed": True,
                    }
                )

        phase = "prepare"
        agent_tool_scope_exact = _agent_tool_scope_exact(provider_name, profile, command, root)
        if not agent_tool_scope_exact:
            return remember_receipt(
                {
                    "provider": provider_name,
                    "version": provider_version,
                    "result": "agent_tool_scope_mismatch",
                    "phase": phase,
                    "agent_tool_scope_exact": False,
                }
            )

        phase = "provider_launch"
        sandboxed_command = _sandbox_command(command, root, work_dir, codex_home_overlay)
        with (
            _open_private_log(output_path) as stdout_file,
            _open_private_log(error_path) as stderr_file,
        ):
            started_at = time.monotonic()
            proc = subprocess.Popen(
                sandboxed_command,
                cwd=work_dir,
                env=cli_env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                start_new_session=True,
                shell=False,
            )
            deadline = started_at + PROVIDER_BUDGET_SECONDS
            answers_sent: set[str] = set()
            shape_error: str | None = None
            while time.monotonic() < deadline and proc.poll() is None:
                with service.repository.read_connection() as connection:
                    rows = connection.execute(
                        "SELECT question_id,version,group_id,terminal_id,incarnation_id,client_key,kind,options_json,required "
                        "FROM interaction_questions WHERE terminal_id=%s AND source_kind='explicit' "
                        "ORDER BY created_at,question_id",
                        (terminal_id,),
                    ).fetchall()
                if rows:
                    if len(rows) != len(QUESTION_SPECS):
                        shape_error = "unexpected_question_count"
                        break
                    by_key = {row["client_key"]: row for row in rows}
                    if set(by_key) != set(HUMAN_ANSWERS):
                        shape_error = "unexpected_question_keys"
                        break
                    group_ids = {row["group_id"] for row in rows}
                    if len(group_ids) != 1:
                        shape_error = "unexpected_question_group"
                        break
                    expected_agui_group_id = next(iter(group_ids))
                    expected_agui_questions = {
                        row["question_id"]: {
                            "group_id": row["group_id"],
                            "version": int(row["version"]),
                            "terminal_id": row["terminal_id"],
                            "incarnation_id": row["incarnation_id"],
                        }
                        for row in rows
                    }
                    if agui_restart and not agui_restart_completed:
                        if not _provider_tool_completed(output_path, "cao_ask_user"):
                            time.sleep(0.08)
                            continue
                        if api_worker is None or api_worker_spec is None:
                            raise RuntimeError("api_worker_not_configured")
                        forbidden_content = tuple(
                            [question["prompt"] for question in QUESTION_SPECS]
                            + [
                                option["label"]
                                for question in QUESTION_SPECS
                                for option in question.get("options", [])
                            ]
                            + [
                                value
                                for answer in HUMAN_ANSWERS.values()
                                for value in (answer.get("text"), *answer.get("option_ids", []))
                                if isinstance(value, str)
                            ]
                        )
                        phase = "agui_snapshot_before_restart"
                        before_snapshot = _read_agui_snapshot(
                            api_worker_spec.base_url,
                            forbidden_content=forbidden_content,
                        )
                        agui_snapshot_count += 1
                        if not _snapshot_matches_questions(
                            before_snapshot,
                            expected_agui_questions,
                            lifecycle="open",
                            delivery_state=None,
                        ):
                            shape_error = "agui_snapshot_identity_mismatch"
                            break
                        from test.live_provider.interaction_agui_restart import (
                            snapshot_identity_map,
                        )

                        before_identity = snapshot_identity_map(before_snapshot)
                        phase = "agui_api_restart"
                        api_worker.restart()
                        agui_restart_count += 1
                        phase = "agui_snapshot_after_restart"
                        after_snapshot = _read_agui_snapshot(
                            api_worker_spec.base_url,
                            forbidden_content=forbidden_content,
                        )
                        agui_snapshot_count += 1
                        after_identity = snapshot_identity_map(after_snapshot)
                        agui_identity_stable = before_identity == after_identity
                        agui_projection_redacted = True
                        if not agui_identity_stable or not _snapshot_matches_questions(
                            after_snapshot,
                            expected_agui_questions,
                            lifecycle="open",
                            delivery_state=None,
                        ):
                            shape_error = "agui_restart_identity_changed"
                            break
                        agui_restart_completed = True
                        phase = "provider_launch"
                    for client_key, response_payload in HUMAN_ANSWERS.items():
                        if client_key in answers_sent:
                            continue
                        row = by_key[client_key]
                        if not row["required"]:
                            shape_error = "question_not_required"
                            break
                        if row["kind"] != QUESTION_KINDS.get(client_key):
                            shape_error = "unexpected_question_kind"
                            break
                        if response_payload["kind"] == "options" and row["kind"] not in {
                            "single_choice",
                            "multi_choice",
                        }:
                            shape_error = "invalid_choice_question_kind"
                            break
                        if response_payload["kind"] == "options":
                            option_ids = {
                                option.get("id")
                                for option in json.loads(row["options_json"])
                                if isinstance(option, dict)
                            }
                            if not set(response_payload["option_ids"]) <= option_ids:
                                shape_error = "fixed_choice_not_offered"
                                break
                        answer_response = httpx.post(
                            f"http://127.0.0.1:{port}/interactions/{row['question_id']}/answers",
                            headers={
                                "If-Match": f"\"{row['version']}\"",
                                "Idempotency-Key": f"human-{provider_name}-{client_key}-fixed",
                            },
                            json={"expected_version": row["version"], "response": response_payload},
                            timeout=5,
                            trust_env=False,
                        )
                        if answer_response.status_code not in {200, 201}:
                            shape_error = f"human_answer_http_{answer_response.status_code}"
                            break
                        answers_sent.add(client_key)
                    if shape_error:
                        break
                time.sleep(0.08)

            timed_out = proc.poll() is None and time.monotonic() >= deadline
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
            returncode = proc.wait(timeout=2)

        with service.repository.read_connection() as connection:
            rows = connection.execute(
                "SELECT q.client_key,q.kind,q.lifecycle,d.state "
                "FROM interaction_questions q "
                "LEFT JOIN interaction_answers a ON a.question_id=q.question_id "
                "LEFT JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                "WHERE q.terminal_id=%s AND q.source_kind='explicit' ORDER BY q.created_at,q.question_id",
                (terminal_id,),
            ).fetchall()
        raw_output = ""
        for path in (output_path, error_path):
            try:
                raw_output += path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        provider_events = _provider_event_summary(output_path)
        private_error_diagnostic_path: Path | None = None
        private_error_diagnostic_status = "disabled"
        if preserve_unclassified_mcp_error:
            try:
                private_error_diagnostic_path = _preserve_unclassified_mcp_errors(output_path)
                private_error_diagnostic_status = (
                    "saved_uninspected"
                    if private_error_diagnostic_path is not None
                    else "not_needed"
                )
            except (OSError, RuntimeError):
                private_error_diagnostic_status = "failed"
        tool_names = provider_events["mcp_tool_names"]
        failure_signals = _sanitized_failure_signals(raw_output)
        delivery_states = [row["state"] for row in rows]
        agui_ack_identity_diagnostics = None
        if agui_restart:
            if agui_restart_completed and api_worker_spec is not None:
                phase = "agui_final_snapshot"
                forbidden_content = tuple(
                    [question["prompt"] for question in QUESTION_SPECS]
                    + [
                        option["label"]
                        for question in QUESTION_SPECS
                        for option in question.get("options", [])
                    ]
                    + [
                        value
                        for answer in HUMAN_ANSWERS.values()
                        for value in (answer.get("text"), *answer.get("option_ids", []))
                        if isinstance(value, str)
                    ]
                )
                final_snapshot = _read_agui_snapshot(
                    api_worker_spec.base_url,
                    forbidden_content=forbidden_content,
                )
                agui_snapshot_count += 1
                agui_final_acknowledged = bool(
                    expected_agui_questions
                ) and _snapshot_matches_questions(
                    final_snapshot,
                    expected_agui_questions,
                    lifecycle="answered",
                    delivery_state="agent_acknowledged",
                )
                agui_projection_redacted = agui_projection_redacted is True
            if expected_agui_group_id is not None and expected_agui_questions:
                agui_ack_identity_diagnostics = _agui_observation_diagnostics(
                    root / "interaction-observations.jsonl",
                    expected_agui_group_id,
                    expected_agui_questions,
                )
        if agui_restart:
            identity_checks = (
                agui_ack_identity_diagnostics.get("ack_checks", [])
                if isinstance(agui_ack_identity_diagnostics, dict)
                else []
            )
            exact_ack_observed = any(
                isinstance(check, dict)
                and check.get("group_matches_created") is True
                and check.get("group_matches_waited") is True
                and check.get("question_versions_match_created") is True
                and check.get("question_versions_match_returned") is True
                and check.get("receipt_hashes_match_returned") is True
                and check.get("ack_succeeded") is True
                and check.get("ack_count") == len(HUMAN_ANSWERS)
                for check in identity_checks
            )
            agui_observation_complete = (
                isinstance(agui_ack_identity_diagnostics, dict)
                and agui_ack_identity_diagnostics.get("created_group_matches_database") is True
                and agui_ack_identity_diagnostics.get("created_questions_match_database") is True
                and agui_ack_identity_diagnostics.get("ready_wait_matches_database") is True
            )
        else:
            exact_ack_observed = True
            agui_observation_complete = True
        acknowledged = (
            len(rows) == 2
            and {row["client_key"] for row in rows} == set(HUMAN_ANSWERS)
            and all(state == "agent_acknowledged" for state in delivery_states)
            and answers_sent == set(HUMAN_ANSWERS)
            and returncode == 0
            and exact_ack_observed
            and agui_observation_complete
            and (
                not agui_restart
                or (
                    agui_restart_count == 1
                    and agui_identity_stable is True
                    and agui_projection_redacted is True
                    and agui_final_acknowledged is True
                )
            )
        )
        unclassified_tool_error = any(
            call["error_present"] and call["error_code"] is None
            for call in provider_events["mcp_tool_calls"]
            if call["server"] == "cao-mcp-server" and call["tool"] in INTERACTION_TOOLS
        )
        if preserve_unclassified_mcp_error and unclassified_tool_error:
            result = (
                "unclassified_mcp_error_pending_inspection"
                if private_error_diagnostic_status == "saved_uninspected"
                else "private_error_diagnostic_capture_failed"
            )
        elif acknowledged:
            result = "completed_and_acknowledged"
        elif shape_error:
            result = shape_error
        elif not rows:
            ask_user_calls = [
                call
                for call in provider_events["mcp_tool_calls"]
                if call["server"] == "cao-mcp-server" and call["tool"] == "cao_ask_user"
            ]
            if any(call["status"] == "failed" or call["error_present"] for call in ask_user_calls):
                result = "mcp_tool_call_failed"
            elif any(call["result_present"] for call in ask_user_calls):
                result = "mcp_tool_returned_without_question"
            else:
                result = _failure_category(raw_output, returncode, timed_out)
        elif timed_out:
            result = "timed_out_after_question"
        elif any(state != "agent_acknowledged" for state in delivery_states):
            result = "question_created_but_not_acknowledged"
        else:
            result = "incomplete_journey"

        elapsed = round(time.monotonic() - started_at, 2) if started_at else None
        return remember_receipt(
            {
                "provider": provider_name,
                "version": provider_version,
                "requested_model": (
                    CLAUDE_AGUI_RESTART_MODEL
                    if provider_name == "claude_code" and agui_restart
                    else CODEX_INTERACTION_MODEL if provider_name == "codex" else None
                ),
                "result": result,
                "exit_code": returncode,
                "elapsed_seconds": elapsed,
                "question_count": len(rows),
                "question_kinds": sorted(row["kind"] for row in rows),
                "question_lifecycles": sorted(row["lifecycle"] for row in rows),
                "human_answers_posted": len(answers_sent),
                "delivery_states": delivery_states,
                "observed_interaction_tools": tool_names,
                "sanitized_failure_signals": failure_signals,
                "agent_message_diagnostics": (
                    _agent_message_failure_diagnostics(output_path)
                    if result != "completed_and_acknowledged"
                    else None
                ),
                "provider_event_counts": provider_events["provider_event_counts"],
                "provider_item_type_counts": provider_events["provider_item_type_counts"],
                "model_usage_counts": provider_events["model_usage_counts"],
                "model_usage_records": provider_events["model_usage_records"],
                "failure_diagnostics": _failure_diagnostic_flags(raw_output),
                "mcp_tool_calls": provider_events["mcp_tool_calls"],
                "private_error_diagnostic_status": private_error_diagnostic_status,
                "private_error_diagnostic_path": (
                    str(private_error_diagnostic_path)
                    if private_error_diagnostic_path is not None
                    else None
                ),
                "provider_home_isolated": (
                    codex_home_overlay is not None
                    if provider_name == "codex"
                    else str(cli_env.get("CLAUDE_CONFIG_DIR", "")).startswith(str(root))
                ),
                "host_filesystem_read_only": True,
                "raw_provider_output_printed": False,
                "agent_tool_scope_exact": agent_tool_scope_exact,
                "agui_restart_enabled": agui_restart,
                "api_worker_restarts": agui_restart_count,
                "agui_snapshot_count": agui_snapshot_count,
                "agui_identity_stable": agui_identity_stable,
                "agui_projection_redacted": agui_projection_redacted,
                "agui_final_acknowledged": agui_final_acknowledged,
                "ack_identity_diagnostics": (
                    agui_ack_identity_diagnostics
                    if agui_restart
                    else safe_ack_identity_diagnostics()
                ),
            }
        )
    except Exception as exc:
        result = {
            "provider": provider_name,
            "result": "harness_error",
            "phase": phase,
            "error_type": type(exc).__name__,
        }
        if agent_tool_scope_exact is not None:
            result["agent_tool_scope_exact"] = agent_tool_scope_exact
        if proc is not None:
            result["agent_message_diagnostics"] = _agent_message_failure_diagnostics(output_path)
        return remember_receipt(result)
    finally:
        cleanup_failures: set[str] = set()
        process_stopped = True
        if proc is not None:
            try:
                process_stopped = _terminate_owned_process_group(proc)
            except Exception:
                process_stopped = False
            if not process_stopped:
                cleanup_failures.add("provider_process_not_stopped")
        if server is not None:
            _cleanup_action(
                cleanup_failures,
                "api_server_not_stopped",
                lambda: setattr(server, "should_exit", True),
            )
        if server_thread is not None:

            def join_server() -> None:
                server_thread.join(timeout=3)
                if server_thread.is_alive():
                    raise RuntimeError("api_server_still_running")

            _cleanup_action(cleanup_failures, "api_server_not_stopped", join_server)
        if api_worker is not None:

            def stop_owned_api_worker() -> None:
                api_worker.stop()
                if api_worker.process is not None:
                    raise RuntimeError("api_worker_still_running")

            try:
                _cleanup_action(
                    cleanup_failures,
                    "api_server_not_stopped",
                    stop_owned_api_worker,
                )
            finally:
                if api_worker.process is not None and api_worker.process.poll() is None:
                    process_stopped = False
        for api_log in (api_worker_stdout, api_worker_stderr):
            if api_log is not None and not api_log.closed:
                _cleanup_action(
                    cleanup_failures,
                    "api_listener_close_failed",
                    api_log.close,
                )
        if listener is not None:
            _cleanup_action(cleanup_failures, "api_listener_close_failed", listener.close)
        if database_module is not None:
            _cleanup_action(
                cleanup_failures,
                "database_engine_dispose_failed",
                database_module.engine.dispose,
            )
        if database_context is not None and database_context_entered:
            _cleanup_action(
                cleanup_failures,
                "database_target_close_failed",
                lambda: database_context.__exit__(None, None, None),
            )
        for key, value in previous_environment.items():

            def restore_environment_value(name: str = key, original: str | None = value) -> None:
                if original is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = original

            _cleanup_action(
                cleanup_failures,
                "environment_restore_failed",
                restore_environment_value,
            )
        operator_auth_unchanged: bool | None = None
        if codex_auth_source is not None and codex_auth_before is not None:
            try:
                operator_auth_unchanged = (
                    _source_file_identity(codex_auth_source, require_private=True)
                    == codex_auth_before
                )
                if not operator_auth_unchanged:
                    cleanup_failures.add("operator_auth_changed")
            except Exception:
                operator_auth_unchanged = False
                cleanup_failures.add("operator_auth_stat_failed")
        temporary_files_removed = _remove_provider_run_root(
            root,
            provider_name=provider_name,
            process_stopped=process_stopped,
            failures=cleanup_failures,
        )
        if receipt_holder:
            receipt_holder[0].update(
                {
                    "temporary_files_removed": temporary_files_removed,
                    "cleanup_status": "complete" if not cleanup_failures else "incomplete",
                    "cleanup_failure_categories": sorted(cleanup_failures),
                }
            )
            if operator_auth_unchanged is not None:
                receipt_holder[0]["operator_auth_unchanged"] = operator_auth_unchanged


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=("codex", "claude_code", "opencode_cli"))
    parser.add_argument(
        "--diagnose-mcp",
        action="store_true",
        help="run the protected MCP stdio handshake and scoped metadata call, without a model call",
    )
    parser.add_argument(
        "--diagnose-create-tool",
        action="store_true",
        help="also create one disposable question through cao_ask_user, without a model call",
    )
    parser.add_argument(
        "--preserve-unclassified-mcp-error",
        action="store_true",
        help="save only unknown CAO MCP error fields to a private 0600 diagnostic file",
    )
    parser.add_argument(
        "--agui-restart",
        action="store_true",
        help="opt in to production AG-UI snapshots across one owned API-process restart",
    )
    args = parser.parse_args(argv)
    diagnose_mcp = args.diagnose_mcp or args.diagnose_create_tool
    if diagnose_mcp and args.agui_restart:
        parser.error("--agui-restart cannot be combined with MCP diagnostics")
    return args


def main() -> int:
    args = _parse_args()
    diagnose_mcp = args.diagnose_mcp or args.diagnose_create_tool
    result = _safe_provider_receipt(
        run(
            args.provider,
            diagnose_mcp=diagnose_mcp,
            probe_create_tool=args.diagnose_create_tool,
            preserve_unclassified_mcp_error=args.preserve_unclassified_mcp_error,
            agui_restart=args.agui_restart,
        )
    )
    if args.provider == "codex" and not diagnose_mcp:
        result["requested_model"] = CODEX_INTERACTION_MODEL
    result["cost_limit_usd"] = 1.0 if args.provider == "claude_code" else None
    result["reported_cost_usd"] = None
    evidence_saved = _append_sanitized_evidence(result)
    result["evidence_saved"] = evidence_saved
    result["evidence_path"] = str(
        ORIGINAL_HOME / ".cache" / "cao-live-provider" / "sanitized-evidence.jsonl"
    )
    print(json.dumps(result, sort_keys=True))
    successful_diagnostic = (
        diagnose_mcp
        and result.get("result") == "mcp_stdio_handshake"
        and result.get("required_tools_present") is True
        and result.get("metadata_call_error") is False
        and result.get("configured_enabled_tools_are_exact") is True
        and result.get("private_capability_file_available") is True
        and (
            not args.diagnose_create_tool
            or (
                result.get("create_tool_probe_is_error") is False
                and result.get("create_tool_probe_success") is True
                and result.get("metadata_item_count_after_probe") == len(QUESTION_SPECS)
            )
        )
    )
    successful_journey = _provider_journey_succeeded(
        result,
        require_auth_unchanged=args.provider in {"codex", "opencode_cli"},
    )
    successful_diagnostic = successful_diagnostic and _provider_cleanup_succeeded(
        result, require_auth_unchanged=args.provider == "codex"
    )
    return 0 if successful_journey or successful_diagnostic else 1


if __name__ == "__main__":
    raise SystemExit(main())
