"""Pure contract helpers for the private Spec023 generic-message lane.

This module deliberately owns no provider, API, database, or terminal process.
The live driver can compose these helpers with the existing production fixtures
only after the private candidate receives an independent source review.
"""

from __future__ import annotations

import errno
import json
import os
import re
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

PRE_RESTART_MESSAGE = "Antes del reinicio: café, español y 日本語."
POST_RESTART_MESSAGE = "Después del reinicio: café, español y 日本語."

INBOX_STATUSES = frozenset({"pending", "delivered", "reconcile", "failed"})
READY_TERMINAL_STATES = frozenset({"idle", "completed"})
PROVIDERS = ("codex", "claude_code", "opencode_cli")
PROVIDER_GATE_ENV = {
    "codex": "CAO_RUN_MESSAGE_API_RESTART_CODEX",
    "claude_code": "CAO_RUN_MESSAGE_API_RESTART_CLAUDE_CODE",
    "opencode_cli": "CAO_RUN_MESSAGE_API_RESTART_OPENCODE",
}
OBSERVER_ENABLE_ENV = "CAO_SPEC023_MCP_OBSERVER"
OBSERVER_AUDIT_PATH_ENV = "CAO_SPEC023_MCP_AUDIT_PATH"
SOURCE_ROOT_ENV = "CAO_SPEC023_SOURCE_ROOT"
CLAUDE_EOF_ENV = "CAO_SPEC023_CLAUDE_REQUEST_QUIESCENT_EOF"
CLAUDE_EOF_OUTCOMES = frozenset(
    {
        "not_requested",
        "requested",
        "history_invalid",
        "eof_request_failed",
        "deadline_exhausted",
        "eof_control_error",
        "normal_seal_missing",
        "normal_eof_verified",
    }
)

_ROW_FIELDS = frozenset({"id", "sender_id", "receiver_id", "message", "created_at"})
_AGUI_METADATA_FIELDS = frozenset({"sender", "receiver", "orchestration_type"})
_ACCEPTANCE_MESSAGE_BODIES = frozenset({PRE_RESTART_MESSAGE, POST_RESTART_MESSAGE})
_AUDIT_COMPLETE_CONTENT = b"SPEC023_AUDIT_COMPLETE\n"
_SAFE_RECEIPT_RESULTS = frozenset({"pass", "failed", "not_selected"})
_SAFE_OBSERVER_TOOLS = frozenset({"send_message", "other"})
_SAFE_OBSERVER_STATUSES = frozenset({"completed", "failed", "rejected"})
_SAFE_OBSERVER_OUTCOMES = frozenset({"success", "failure", "unknown"})
NATIVE_FAILURE_POINTS = frozenset(
    {
        "none",
        "profile_write",
        "profile_install",
        "receiver_create",
        "receiver_readiness",
        "sender_create",
        "sender_readiness",
        "topology_check",
        "inbox_check",
        "phase_wait",
        "phase_input",
        "runtime_admission",
    }
)
NATIVE_FAILURE_KINDS = frozenset(
    {
        "none",
        "request_error",
        "http_status",
        "invalid_shape",
        "readiness_timeout",
        "mismatch",
        "io_error",
        # Fixed, privacy-preserving turn diagnostics.  These describe the
        # terminal/API boundary only; provider output and prompt text are never
        # copied into a receipt.
        "provider_error",
        "quota_wait",
        "interaction_wait",
        "reconcile_required",
        "terminal_missing",
        "no_tool_call",
        "phase_timeout",
    }
)
NATIVE_FAILURE_DETAILS = frozenset(
    {
        "none",
        "shell_timeout",
        "provider_init_timeout",
        "unclassified",
    }
)
NATIVE_TERMINAL_STATUSES = frozenset(
    {
        "unknown",
        "idle",
        "processing",
        "completed",
        "waiting_user_answer",
        "waiting_quota",
        "reconcile",
        "error",
        "missing",
    }
)


def classify_native_create_error_body(body: Any) -> str:
    """Reduce a native-create error response to one fixed, non-content code."""
    if not isinstance(body, Mapping):
        return "unclassified"
    detail = body.get("detail")
    if not isinstance(detail, str):
        return "unclassified"
    if re.fullmatch(
        r"Failed to create (?:session|terminal): Shell initialization timed out "
        r"after [0-9]+(?:\.[0-9]+)?s",
        detail,
    ):
        return "shell_timeout"
    if re.fullmatch(
        r"Failed to create (?:session|terminal): "
        r"(?:Codex|Claude Code|OpenCode CLI) initialization timed out "
        r"after [0-9]+(?:\.[0-9]+)?(?:s| seconds)",
        detail,
    ):
        return "provider_init_timeout"
    return "unclassified"


def classify_native_create_error_response(response: Any) -> str:
    """Parse and reduce an HTTP error response without retaining its body."""
    if response is None:
        return "unclassified"
    try:
        body = response.json()
    except Exception:
        return "unclassified"
    return classify_native_create_error_body(body)


def phase_trigger(phase: str) -> str:
    """Return a body-free, fixed prompt trigger for one permitted action."""
    triggers = {
        "before_restart": "SPEC023_SEND_BEFORE_RESTART",
        "after_restart": "SPEC023_SEND_AFTER_RESTART",
    }
    try:
        return triggers[phase]
    except (KeyError, TypeError):
        raise ValueError("invalid_phase") from None


def expected_persisted_message(message: str, sender_id: str) -> str:
    """Return the source-defined sender-ID suffix for one fixed acceptance body."""
    if (
        not isinstance(message, str)
        or message not in _ACCEPTANCE_MESSAGE_BODIES
        or not isinstance(sender_id, str)
        or re.fullmatch(r"[a-f0-9]{8}", sender_id) is None
    ):
        raise ValueError("invalid_persisted_message")
    return (
        f"{message}\n\n[Message from terminal {sender_id}. "
        "Use send_message MCP tool for any follow-up work.]"
    )


def owned_session_name(session_prefix: str, run_token: str) -> str:
    """Build one CAO-owned session name from its source-pinned prefix."""
    if (
        session_prefix != "cao-"
        or not isinstance(run_token, str)
        or re.fullmatch(r"[a-f0-9]{10}", run_token) is None
    ):
        raise ValueError("invalid_session_name")
    return f"{session_prefix}spec023-{run_token}"


def audit_completion_seal_valid(audit_path: Path) -> bool:
    """Require the source-bound MCP child’s fixed, private positive completion seal."""
    try:
        marker_path = audit_path.with_name(audit_path.name + ".complete")
        parent_metadata = marker_path.parent.lstat()
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or stat.S_ISLNK(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        ):
            return False
        descriptor = os.open(
            marker_path,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != len(_AUDIT_COMPLETE_CONTENT)
        ):
            return False
        content = os.read(descriptor, len(_AUDIT_COMPLETE_CONTENT) + 1)
        return content == _AUDIT_COMPLETE_CONTENT
    except OSError:
        return False
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def is_source_bound_mcp_target(
    *,
    module_spec_name: Any,
    module_server: Any,
    server: Any,
    module_file: str | Path,
    source_root: Path,
) -> bool:
    """Accept only the source-root singleton the bundled MCP module runs."""
    if (
        module_spec_name != "cli_agent_orchestrator.mcp_server.server"
        or module_server is not server
    ):
        return False
    try:
        root = source_root.resolve(strict=True)
        module_path = Path(module_file).resolve(strict=True)
        module_path.relative_to(root / "src")
        return module_path.name == "server.py"
    except (OSError, RuntimeError, ValueError, TypeError):
        return False


def build_message_mcp_server_config(
    *,
    provider: str,
    python_executable: Path,
    source_root: Path,
    candidate_test_dir: Path,
    audit_path: Path,
    claude_quiescent_eof: bool = False,
) -> dict[str, Any]:
    """Build a private, source-pinned MCP entry for the actual bundled server."""
    if provider not in PROVIDERS:
        raise RuntimeError("selected_provider_unavailable")
    if type(claude_quiescent_eof) is not bool or (
        claude_quiescent_eof and provider != "claude_code"
    ):
        raise RuntimeError("invalid_claude_shutdown_mode")
    try:
        if not python_executable.is_absolute() or ".." in python_executable.parts:
            raise RuntimeError("mcp_snapshot_unavailable")
        python_path = Path(os.path.abspath(python_executable))
        python_target = python_path.resolve(strict=True)
        source_path = source_root.resolve(strict=True)
        candidate_path = candidate_test_dir.resolve(strict=True)
        if not audit_path.is_absolute() or ".." in audit_path.parts:
            raise RuntimeError("mcp_snapshot_unavailable")
        audit_file = Path(os.path.abspath(audit_path))
        audit_parent = audit_file.parent
        audit_parent_metadata = audit_parent.lstat()
        parent_cursor = audit_parent
        while True:
            if stat.S_ISLNK(parent_cursor.lstat().st_mode):
                raise RuntimeError("mcp_snapshot_unavailable")
            if parent_cursor.parent == parent_cursor:
                break
            parent_cursor = parent_cursor.parent
    except (OSError, RuntimeError):
        raise RuntimeError("mcp_snapshot_unavailable") from None
    if (
        not python_target.is_file()
        or not os.access(python_target, os.X_OK)
        or not (source_path / "src/cli_agent_orchestrator/mcp_server/server.py").is_file()
        or not (candidate_path / "sitecustomize.py").is_file()
        or not audit_file.is_absolute()
        or audit_file.exists()
        or audit_file.is_symlink()
        or not stat.S_ISDIR(audit_parent_metadata.st_mode)
        or audit_parent_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(audit_parent_metadata.st_mode) != 0o700
    ):
        raise RuntimeError("mcp_snapshot_unavailable")

    env = {
        OBSERVER_ENABLE_ENV: "1",
        OBSERVER_AUDIT_PATH_ENV: str(audit_file),
        SOURCE_ROOT_ENV: str(source_path),
        "CAO_ENABLE_SENDER_ID_INJECTION": "true",
        "FASTMCP_LOG_LEVEL": "WARNING",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join(
            (
                str(candidate_path),
                str(source_path / "src"),
                str(source_path),
            )
        ),
    }
    config: dict[str, Any] = {
        "command": str(python_path),
        "args": ["-B", "-m", "cli_agent_orchestrator.mcp_server.server"],
        "env": env,
    }
    if provider == "codex":
        config["enabled_tools"] = ["send_message"]
        env["CAO_SPEC023_MCP_GRACEFUL_EOF"] = "1"
    elif provider == "claude_code":
        # Profiled servers require the canonical module command. The pinned
        # environment preserves no-bytecode behavior without the extra flag.
        config["args"] = ["-m", "cli_agent_orchestrator.mcp_server.server"]
        config["cao_tool_profile"] = "collaboration"
        config["enabled_tools"] = ["send_message"]
        if claude_quiescent_eof:
            env["CAO_SPEC023_MCP_GRACEFUL_EOF"] = "1"
    return config


def compare_inbox_row(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    """Compare the existing persisted row contract without returning its body."""
    if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
        return False
    if set(expected) != _ROW_FIELDS or set(actual) != _ROW_FIELDS | {"status"}:
        return False
    if type(expected.get("id")) is not int or expected["id"] <= 0:
        return False
    if type(actual.get("id")) is not int or actual["id"] <= 0:
        return False
    text_fields = _ROW_FIELDS - {"id"}
    if not all(isinstance(expected.get(field), str) and expected[field] for field in text_fields):
        return False
    if not all(isinstance(actual.get(field), str) and actual[field] for field in text_fields):
        return False
    if actual.get("status") not in INBOX_STATUSES:
        return False
    return all(actual.get(field) == expected.get(field) for field in _ROW_FIELDS)


def read_dispatch_audit(path: Path) -> list[dict[str, str]]:
    """Read a bounded, owner-only observer file into fixed audit enums."""
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_size > 4096
        ):
            raise RuntimeError("audit_file_not_private")
        chunks = bytearray()
        while len(chunks) <= 4096:
            block = os.read(descriptor, min(1024, 4097 - len(chunks)))
            if not block:
                break
            chunks.extend(block)
        if len(chunks) > 4096:
            raise RuntimeError("audit_file_not_private")
        content = chunks.decode("utf-8")
        records: list[dict[str, str]] = []
        for line in content.splitlines():
            record = json.loads(line)
            if (
                not isinstance(record, dict)
                or set(record) != {"tool", "status", "outcome"}
                or not all(
                    isinstance(record.get(key), str) for key in ("tool", "status", "outcome")
                )
                or record["tool"] not in {"send_message", "other"}
                or record["status"] not in {"completed", "failed", "rejected"}
                or record["outcome"] not in _SAFE_OBSERVER_OUTCOMES
            ):
                raise RuntimeError("audit_record_invalid")
            records.append(
                {
                    "tool": record["tool"],
                    "status": record["status"],
                    "outcome": record["outcome"],
                }
            )
        return records
    except RuntimeError:
        raise
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.EMLINK}:
            raise RuntimeError("audit_file_not_private") from None
        raise RuntimeError("audit_file_invalid") from None
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise RuntimeError("audit_file_invalid") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def validate_dispatch_history(records: Iterable[Mapping[str, Any]], *, expected_calls: int) -> bool:
    """Accept only the exact expected number of completed send_message dispatches."""
    if (
        isinstance(expected_calls, bool)
        or not isinstance(expected_calls, int)
        or expected_calls < 1
    ):
        return False
    try:
        materialized = list(records)
    except (TypeError, ValueError):
        return False
    return len(materialized) == expected_calls and all(
        isinstance(record, Mapping)
        and set(record) == {"tool", "status", "outcome"}
        and record.get("tool") == "send_message"
        and record.get("status") == "completed"
        and record.get("outcome") == "success"
        for record in materialized
    )


def delete_exact_terminals(
    base_url: str,
    terminal_ids: tuple[str, ...],
    *,
    request_fn: Any,
) -> None:
    """Delete only validated IDs created by this journey; never delete a session."""
    match = (
        re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})", base_url)
        if isinstance(base_url, str)
        else None
    )
    if (
        match is None
        or not 1 <= int(match.group(1)) <= 65535
        or not isinstance(terminal_ids, tuple)
        or len(terminal_ids) > 2
        or len(set(terminal_ids)) != len(terminal_ids)
        or any(
            not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{8}", value) is None
            for value in terminal_ids
        )
    ):
        raise RuntimeError("cleanup_failed")

    failed = False
    for terminal_id in terminal_ids:
        try:
            response = request_fn(f"{base_url}/terminals/{terminal_id}")
            if getattr(response, "status_code", None) != 200:
                failed = True
        except Exception:
            failed = True
    if failed:
        raise RuntimeError("cleanup_failed") from None


def validate_agui_notification(
    event: Mapping[str, Any], *, sender_id: str, receiver_id: str
) -> bool:
    """Check one live redacted send_message projection from its public fields."""
    if not isinstance(event, Mapping) or not sender_id or not receiver_id:
        return False
    if _contains_message_body_key(event):
        return False
    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping) or set(metadata) != _AGUI_METADATA_FIELDS:
        return False
    return (
        event.get("type") == "TEXT_MESSAGE_CONTENT"
        and event.get("message_id") == receiver_id
        and event.get("role") == "assistant"
        and event.get("delta") == ""
        and "message" not in event
        and "body" not in event
        and metadata.get("sender") == sender_id
        and metadata.get("receiver") == receiver_id
        and metadata.get("orchestration_type") == "send_message"
    )


def _contains_message_body_key(value: Any) -> bool:
    if isinstance(value, str):
        return any(body in value for body in _ACCEPTANCE_MESSAGE_BODIES)
    if isinstance(value, Mapping):
        return any(
            (isinstance(key, str) and key.casefold() in {"message", "body"})
            or _contains_message_body_key(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_message_body_key(child) for child in value)
    return False


def parse_claude_eof_gate(environment: Mapping[str, Any]) -> bool:
    """Explicit diagnostic shutdown selection; never infer from truthiness."""
    value = environment.get(CLAUDE_EOF_ENV, "0")
    if type(value) is not str or value not in {"0", "1"}:
        raise ValueError("invalid_claude_shutdown_mode")
    return value == "1"


def parse_provider_gates(environment: Mapping[str, Any]) -> tuple[str, ...]:
    """Return explicitly selected lanes; missing gates default to off."""
    selected: list[str] = []
    for provider in PROVIDERS:
        value = environment.get(PROVIDER_GATE_ENV[provider], "0")
        if not isinstance(value, str) or value not in {"0", "1"}:
            raise ValueError("invalid_provider_gate")
        if value == "1":
            selected.append(provider)
    if len(selected) > 1:
        raise ValueError("one_provider_per_run")
    return tuple(selected)


def require_selected_provider_ready(provider: str, readiness: Mapping[str, Any]) -> None:
    """Fail an explicitly selected provider if any required live gate is absent."""
    required = {"binary", "auth", "source", "tools"}
    if (
        provider not in PROVIDERS
        or not isinstance(readiness, Mapping)
        or set(readiness) != required
        or any(type(readiness.get(name)) is not bool or not readiness[name] for name in required)
    ):
        raise RuntimeError("selected_provider_unavailable")


def message_mcp_config_ready(
    provider: str,
    config: Mapping[str, Any],
    *,
    python_executable: Path,
    source_root: Path,
    candidate_test_dir: Path,
    audit_path: Path,
    claude_quiescent_eof: bool = False,
) -> bool:
    """Check the selected server-level grant and the dispatch observer pins."""
    if provider not in PROVIDERS or not isinstance(config, Mapping):
        return False
    if type(claude_quiescent_eof) is not bool or (
        claude_quiescent_eof and provider != "claude_code"
    ):
        return False
    try:
        python_path = Path(os.path.abspath(python_executable))
        source_path = source_root.resolve(strict=True)
        candidate_path = candidate_test_dir.resolve(strict=True)
        audit_file = Path(os.path.abspath(audit_path))
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    expected_keys = {"command", "args", "env"}
    if provider in {"codex", "claude_code"}:
        expected_keys.add("enabled_tools")
    if provider == "claude_code":
        expected_keys.add("cao_tool_profile")
    if set(config) != expected_keys:
        return False
    expected_command_args = ["-m", "cli_agent_orchestrator.mcp_server.server"]
    if provider != "claude_code":
        expected_command_args.insert(0, "-B")
    environment = config.get("env")
    if not isinstance(environment, Mapping):
        return False
    return (
        set(environment)
        == {
            OBSERVER_ENABLE_ENV,
            OBSERVER_AUDIT_PATH_ENV,
            SOURCE_ROOT_ENV,
            "CAO_ENABLE_SENDER_ID_INJECTION",
            "FASTMCP_LOG_LEVEL",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONPATH",
        }
        | (
            {"CAO_SPEC023_MCP_GRACEFUL_EOF"}
            if provider == "codex" or claude_quiescent_eof
            else set()
        )
        and config.get("command") == str(python_path)
        and config.get("args") == expected_command_args
        and (
            provider not in {"codex", "claude_code"}
            or config.get("enabled_tools") == ["send_message"]
        )
        and (provider != "claude_code" or config.get("cao_tool_profile") == "collaboration")
        and environment.get(OBSERVER_ENABLE_ENV) == "1"
        and (
            not (provider == "codex" or claude_quiescent_eof)
            or environment.get("CAO_SPEC023_MCP_GRACEFUL_EOF") == "1"
        )
        and environment.get(OBSERVER_AUDIT_PATH_ENV) == str(audit_file)
        and environment.get(SOURCE_ROOT_ENV) == str(source_path)
        and environment.get("CAO_ENABLE_SENDER_ID_INJECTION") == "true"
        and environment.get("FASTMCP_LOG_LEVEL") == "WARNING"
        and environment.get("PYTHONDONTWRITEBYTECODE") == "1"
        and environment.get("PYTHONPATH")
        == os.pathsep.join(
            (
                str(candidate_path),
                str(source_path / "src"),
                str(source_path),
            )
        )
    )


def validate_restart_handoff(
    api_a: Mapping[str, Any],
    api_b: Mapping[str, Any],
    terminals_a: Mapping[str, str],
    terminals_b: Mapping[str, str],
) -> bool:
    """Require distinct API processes over unchanged state and exact ready pane IDs."""
    shared_fields = ("source_root", "home", "port", "database_identity")
    if not isinstance(api_a, Mapping) or not isinstance(api_b, Mapping):
        return False
    if any(api_a.get(field) != api_b.get(field) for field in shared_fields):
        return False
    process_a = api_a.get("process_id")
    process_b = api_b.get("process_id")
    if (
        isinstance(process_a, bool)
        or isinstance(process_b, bool)
        or not isinstance(process_a, int)
        or not isinstance(process_b, int)
        or process_a < 1
        or process_b < 1
        or process_a == process_b
        or api_a.get("healthy") is not True
        or api_b.get("healthy") is not True
    ):
        return False
    if not isinstance(terminals_a, Mapping) or not isinstance(terminals_b, Mapping):
        return False
    original_ids = set(terminals_a)
    if len(original_ids) != 2 or original_ids != set(terminals_b):
        return False
    return all(
        terminals_a.get(terminal_id) in READY_TERMINAL_STATES
        and terminals_b.get(terminal_id) in READY_TERMINAL_STATES
        for terminal_id in original_ids
    )


def project_safe_receipt(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Project only fixed enums and bounded booleans/counts into a safe receipt."""
    provider = raw.get("provider")
    if provider not in PROVIDERS:
        provider = "other"
    result = raw.get("result")
    if result not in _SAFE_RECEIPT_RESULTS:
        result = "failed"

    calls: list[dict[str, str]] = []
    raw_calls = raw.get("mcp_calls")
    if isinstance(raw_calls, (list, tuple)):
        for call in raw_calls[:4]:
            if not isinstance(call, Mapping):
                continue
            tool = call.get("tool")
            status = call.get("status")
            outcome = call.get("outcome")
            calls.append(
                {
                    "tool": (
                        tool if isinstance(tool, str) and tool in _SAFE_OBSERVER_TOOLS else "other"
                    ),
                    "status": (
                        status
                        if isinstance(status, str) and status in _SAFE_OBSERVER_STATUSES
                        else "rejected"
                    ),
                    "outcome": (
                        outcome
                        if isinstance(outcome, str) and outcome in _SAFE_OBSERVER_OUTCOMES
                        else "unknown"
                    ),
                }
            )
    receipt: dict[str, Any] = {
        "provider": provider,
        "result": result,
        "mcp_calls": calls,
    }
    if provider in {"codex", "claude_code"}:
        mode = raw.get("shutdown_mode")
        receipt["shutdown_mode"] = mode if mode == "harness_requested_eof" else "not_observed"
        receipt["native_automatic_shutdown_accepted"] = False
    if provider == "claude_code":
        receipt["claude_quiescent_eof_requested"] = (
            raw.get("claude_quiescent_eof_requested") is True
        )
        outcome = raw.get("claude_eof_outcome", "not_requested")
        receipt["claude_eof_outcome"] = (
            outcome if type(outcome) is str and outcome in CLAUDE_EOF_OUTCOMES else "not_observed"
        )
    for name in (
        "first_row_identity_matched",
        "events_redacted",
        "terminals_preserved",
    ):
        value = raw.get(name)
        receipt[name] = value if type(value) is bool else False
    for name in ("row_count", "event_count", "api_replacements"):
        value = raw.get(name)
        receipt[name] = value if type(value) is int and 0 <= value <= 2 else 0
    cleanup = raw.get("cleanup", "not_attempted")
    receipt["cleanup"] = (
        cleanup
        if isinstance(cleanup, str) and cleanup in {"pass", "failed", "not_attempted"}
        else "failed"
    )
    stage = raw.get("failure_stage", "none")
    receipt["failure_stage"] = (
        stage
        if isinstance(stage, str)
        and stage
        in {
            "preflight",
            "postgres_targets",
            "api_a_start",
            "native_terminals",
            "phase_a",
            "api_restart",
            "api_b_verify",
            "phase_b",
            "observer_audit",
            "cleanup",
            "none",
        }
        else "preflight"
    )
    failure_point = raw.get("failure_point", "none")
    receipt["failure_point"] = (
        failure_point
        if isinstance(failure_point, str) and failure_point in NATIVE_FAILURE_POINTS
        else "none"
    )
    failure_kind = raw.get("failure_kind", "none")
    receipt["failure_kind"] = (
        failure_kind
        if isinstance(failure_kind, str) and failure_kind in NATIVE_FAILURE_KINDS
        else "none"
    )
    http_status = raw.get("failure_http_status", 0)
    receipt["failure_http_status"] = (
        http_status if type(http_status) is int and 100 <= http_status <= 599 else 0
    )
    terminal_status = raw.get("last_terminal_status", "unknown")
    receipt["last_terminal_status"] = (
        terminal_status
        if isinstance(terminal_status, str) and terminal_status in NATIVE_TERMINAL_STATUSES
        else "unknown"
    )
    elapsed_ms = raw.get("failure_elapsed_ms", 0)
    receipt["failure_elapsed_ms"] = (
        elapsed_ms if type(elapsed_ms) is int and 0 <= elapsed_ms <= 600_000 else 0
    )
    failure_detail = raw.get("failure_detail", "none")
    receipt["failure_detail"] = (
        failure_detail
        if isinstance(failure_detail, str) and failure_detail in NATIVE_FAILURE_DETAILS
        else "unclassified"
    )
    return receipt
