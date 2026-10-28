"""Private, explicitly gated production A→B generic-message acceptance."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from test.fixtures import owned_tmux
from test.live_provider import claude_auth_archive as _claude_auth_archive
from typing import Any, Mapping

import pytest
import requests
from message_api_restart import (
    INBOX_STATUSES,
    NATIVE_FAILURE_DETAILS,
    NATIVE_FAILURE_KINDS,
    NATIVE_FAILURE_POINTS,
    NATIVE_TERMINAL_STATUSES,
    OBSERVER_AUDIT_PATH_ENV,
    POST_RESTART_MESSAGE,
    PRE_RESTART_MESSAGE,
    PROVIDER_GATE_ENV,
    SOURCE_ROOT_ENV,
    audit_completion_seal_valid,
    build_message_mcp_server_config,
    classify_native_create_error_body,
    classify_native_create_error_response,
    compare_inbox_row,
    expected_persisted_message,
    message_mcp_config_ready,
    owned_session_name,
    parse_claude_eof_gate,
    parse_provider_gates,
    phase_trigger,
    project_safe_receipt,
    read_dispatch_audit,
    require_selected_provider_ready,
    validate_agui_notification,
    validate_dispatch_history,
    validate_restart_handoff,
)

PINNED_PYTHON = os.environ.get(
    "CAO_SPEC023_PYTHON", "/home/felni/.cache/caos-spec016-validation-venv/bin/python"
)
LIVE_ENABLE_ENV = "CAO_RUN_MESSAGE_API_RESTART_LIVE"
EVIDENCE_ROOT_ENV = "CAO_SPEC023_EVIDENCE_ROOT"
POSTGRES_METADATA_ENV = "CAO_TEST_POSTGRES_METADATA_FILE"
POSTGRES_AUTOSTART_ENV = "CAO_TEST_POSTGRES_AUTOSTART"
_SOURCE_BINDING_FILES = (
    "src/cli_agent_orchestrator/api/main.py",
    "src/cli_agent_orchestrator/backends/registry.py",
    "src/cli_agent_orchestrator/backends/tmux_backend.py",
    "src/cli_agent_orchestrator/clients/database.py",
    "src/cli_agent_orchestrator/clients/postgres/config.py",
    "src/cli_agent_orchestrator/clients/postgres/migrations.py",
    "src/cli_agent_orchestrator/clients/tmux.py",
    "src/cli_agent_orchestrator/constants.py",
    "src/cli_agent_orchestrator/models/inbox.py",
    "src/cli_agent_orchestrator/models/terminal.py",
    "src/cli_agent_orchestrator/mcp_server/server.py",
    "src/cli_agent_orchestrator/mcp_server/physical_eof.py",
    "src/cli_agent_orchestrator/utils/mcp_owner_stop.py",
    "src/cli_agent_orchestrator/services/session_service.py",
    "src/cli_agent_orchestrator/services/cleanup_service.py",
    "src/cli_agent_orchestrator/mcp_server/interaction_pull_flow_only.py",
    "src/cli_agent_orchestrator/mcp_server/tool_profiles.py",
    "src/cli_agent_orchestrator/plugins/__init__.py",
    "src/cli_agent_orchestrator/plugins/base.py",
    "src/cli_agent_orchestrator/plugins/builtin/event_log_publisher.py",
    "src/cli_agent_orchestrator/plugins/events.py",
    "src/cli_agent_orchestrator/plugins/registry.py",
    "src/cli_agent_orchestrator/providers/base.py",
    "src/cli_agent_orchestrator/providers/mcp_interactions.py",
    "src/cli_agent_orchestrator/providers/manager.py",
    "src/cli_agent_orchestrator/providers/codex.py",
    "src/cli_agent_orchestrator/providers/claude_code.py",
    "src/cli_agent_orchestrator/providers/opencode_cli.py",
    "src/cli_agent_orchestrator/services/install_service.py",
    "src/cli_agent_orchestrator/services/agui_stream.py",
    "src/cli_agent_orchestrator/services/event_log_service.py",
    "src/cli_agent_orchestrator/services/inbox_service.py",
    "src/cli_agent_orchestrator/services/status_monitor.py",
    "src/cli_agent_orchestrator/services/terminal_service.py",
    "src/cli_agent_orchestrator/services/turn_recovery_service.py",
    "src/cli_agent_orchestrator/utils/orchestration.py",
    "src/cli_agent_orchestrator/utils/mcp_resolution.py",
    "src/cli_agent_orchestrator/utils/tool_mapping.py",
    "src/cli_agent_orchestrator/utils/mcp_tool_policy.py",
    "src/cli_agent_orchestrator/utils/mcp_readiness.py",
    "src/cli_agent_orchestrator/utils/opencode_config.py",
    "src/cli_agent_orchestrator/utils/opencode_permissions.py",
    "src/cli_agent_orchestrator/utils/opencode_v2.py",
    "test/fixtures/cao_server.py",
    "test/postgres/fixtures.py",
    "test/e2e/test_real_provider_matrix.py",
    "test/fixtures/owned_tmux.py",
)
_TERMINAL_ID = re.compile(r"[0-9a-fA-F]{8}\Z")
_RUN_STAGES = frozenset(
    {
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
)
_PRESERVED_TARGET_CONTEXTS: list[Any] = []


_LIVE_PROVIDER_MATRIX_ENABLE_ENV = "CAO_RUN_LIVE_PROVIDER_TESTS"
_LIVE_PROVIDER_MATRIX_MODULE = "test.e2e.test_real_provider_matrix"


class _ClaudeExitRequestOutcome(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    REQUEST_ERROR = "request_error"
    HTTP_200 = "http_200"
    HTTP_OTHER_STATUS = "http_other_status"


class _ClaudeExitPollResult(str, Enum):
    NOT_CHECKED = "not_checked"
    NO_SEAL_AT_POLL_RETURN = "no_seal_at_poll_return"
    SEAL_FOUND_POLL_RETURN_BEFORE_DEADLINE = "seal_found_poll_return_before_deadline"
    SEAL_FOUND_POLL_RETURN_AFTER_DEADLINE = "seal_found_poll_return_after_deadline"


class _ClaudeExitBudgetOutcome(str, Enum):
    WITHIN_NOMINAL_BUDGET = "within_nominal_budget"
    EXHAUSTED_BEFORE_REQUEST = "exhausted_before_request"
    EXHAUSTED_AFTER_REQUEST = "exhausted_after_request"
    EXHAUSTED_AFTER_POLL = "exhausted_after_poll"


@dataclass(frozen=True)
class _ClaudeExitDiagnostic:
    request: _ClaudeExitRequestOutcome
    seal_poll_result: _ClaudeExitPollResult
    budget: _ClaudeExitBudgetOutcome

    def as_record(self) -> dict[str, str]:
        return {
            "schema": "spec023.claude-exit-diagnostic.v2",
            "exit_request": self.request.value,
            "seal_poll_result": self.seal_poll_result.value,
            "budget": self.budget.value,
        }


class AcceptanceFailure(RuntimeError):
    """Failure containing only a fixed stage code."""

    def __init__(self, stage: str):
        super().__init__(stage if stage in _RUN_STAGES else "preflight")
        self.stage = stage if stage in _RUN_STAGES else "preflight"


@dataclass
class RunState:
    provider: str
    run_root: Path
    workspace: Path
    home: Path
    source_root: Path
    audit_path: Path
    tmux_tmpdir: Path | None = None
    targets: Any = None
    target_context: Any = None
    port: int = 0
    servers: list[Any] | None = None
    stopped_servers: set[int] | None = None
    active_server: Any = None
    sender_id: str | None = None
    receiver_id: str | None = None
    session_name: str | None = None
    deleted_ids: set[str] | None = None
    captures: list[Any] | None = None
    phase_rows: list[dict[str, str]] | None = None
    event_count: int = 0
    first_row_identity_matched: bool = False
    terminals_preserved: bool = False
    audit_failed: bool = False
    shutdown_mode: str = "not_observed"
    claude_quiescent_eof: bool = False
    claude_eof_outcome: str = "not_requested"
    expected_turn_sequence: int | None = None
    previous_turn_generation: str | None = None
    observed_turn_generation: str | None = None
    turn_observations: dict[str, dict[str, Any]] | None = None
    claude_auth_staging_attempted: bool = False
    api_replacements: int = 0
    stage: str = "preflight"
    failure_stage: str = "none"
    cleanup: str = "not_attempted"
    result: str = "failed"
    claude_exit_diagnostic: _ClaudeExitDiagnostic | None = None
    failure_point: str = "none"
    failure_kind: str = "none"
    failure_http_status: int = 0
    last_terminal_status: str = "unknown"
    failure_elapsed_ms: int = 0
    failure_detail: str = "none"

    def __post_init__(self) -> None:
        self.servers = []
        self.stopped_servers = set()
        self.deleted_ids = set()
        self.captures = []
        self.phase_rows = []
        self.turn_observations = {}


class AguiCapture:
    """Consume one real AG-UI SSE connection without retaining raw frames."""

    def __init__(self, base_url: str, sender_id: str, receiver_id: str):
        self._url = f"{base_url}/agui/v1/stream"
        self._sender_id = sender_id
        self._receiver_id = receiver_id
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._matches: queue.Queue[bool] = queue.Queue()
        self._response: requests.Response | None = None
        self._thread = threading.Thread(target=self._read, name="spec023-agui", daemon=True)
        self._failed = False

    def start(self, timeout: float = 20.0, *, failure_stage: str = "phase_a") -> None:
        self._thread.start()
        if not self._ready.wait(timeout):
            raise AcceptanceFailure(failure_stage)
        if self._failed:
            raise AcceptanceFailure(failure_stage)

    def _read(self) -> None:
        try:
            response = requests.get(self._url, stream=True, timeout=(5.0, 60.0))
            self._response = response
            if response.status_code != 200:
                self._failed = True
                self._ready.set()
                return
            event_name = "message"
            data_lines: list[str] = []
            lines = iter(response.iter_lines(decode_unicode=True))
            while True:
                try:
                    raw_line = next(lines)
                except StopIteration:
                    break
                except AttributeError:
                    # Some urllib3 versions raise this from raw.read() when
                    # stop() closes the response during an active stream read.
                    # Suppress it only at the iterator boundary and only after
                    # the caller has explicitly stopped this capture.
                    if self._stop.is_set():
                        break
                    raise
                if self._stop.is_set():
                    break
                line = raw_line or ""
                if not self._ready.is_set():
                    self._ready.set()
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif not line:
                    self._publish_frame(event_name, data_lines)
                    event_name = "message"
                    data_lines = []
            if data_lines:
                self._publish_frame(event_name, data_lines)
        except AttributeError:
            # AttributeError outside the iterator boundary is always a stream
            # failure, even if shutdown happened concurrently.
            self._failed = True
            self._ready.set()
        except (requests.RequestException, OSError, ValueError):
            if not self._stop.is_set():
                self._failed = True
            self._ready.set()
        finally:
            if self._response is not None:
                self._response.close()

    def _publish_frame(self, event_name: str, lines: list[str]) -> None:
        if not lines:
            return
        try:
            payload = json.loads("\n".join(lines))
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        metadata = payload.get("metadata")
        if not isinstance(metadata, Mapping):
            return
        if (
            metadata.get("sender") != self._sender_id
            or metadata.get("receiver") != self._receiver_id
        ):
            return
        safe_event = dict(payload)
        if "type" not in safe_event:
            safe_event["type"] = event_name
        elif safe_event.get("type") != event_name:
            self._matches.put(False)
            return
        self._matches.put(
            validate_agui_notification(
                safe_event,
                sender_id=self._sender_id,
                receiver_id=self._receiver_id,
            )
        )

    def take_until(self, deadline: float) -> list[bool]:
        matches: list[bool] = []
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                matches.append(self._matches.get(timeout=min(0.25, remaining)))
            except queue.Empty:
                continue
        while True:
            try:
                matches.append(self._matches.get_nowait())
            except queue.Empty:
                break
        return matches

    def stop(self) -> None:
        self._stop.set()
        if self._response is not None:
            self._response.close()
        self._thread.join(timeout=3.0)
        if self._thread.is_alive() and not self._failed:
            self._failed = True


def _private_directory(path: Path, *, must_exist: bool) -> None:
    try:
        owned_tmux.ensure_private_directory(path, must_exist=must_exist)
    except OSError:
        raise AcceptanceFailure("preflight") from None


def _import_source_module(name: str) -> Any:
    """Import a source-bound module without collecting live matrix cells.

    The message acceptance process enables the live-provider flag for its own
    child journeys.  The matrix module materializes pytest parameters at
    import time, however, and correctly requires a reviewed cell filter when
    that flag is enabled.  This source-binding import only needs the module
    identity; temporarily suppress collection and restore the caller's exact
    environment before returning to the live journey.
    """
    if name != _LIVE_PROVIDER_MATRIX_MODULE:
        return importlib.import_module(name)
    previous = os.environ.get(_LIVE_PROVIDER_MATRIX_ENABLE_ENV)
    os.environ[_LIVE_PROVIDER_MATRIX_ENABLE_ENV] = "0"
    try:
        return importlib.import_module(name)
    finally:
        if previous is None:
            os.environ.pop(_LIVE_PROVIDER_MATRIX_ENABLE_ENV, None)
        else:
            os.environ[_LIVE_PROVIDER_MATRIX_ENABLE_ENV] = previous


def _source_modules(source_root: Path) -> tuple[Any, Any, Any, Any, Any, Any]:
    for entry in (str(source_root), str(source_root / "src")):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    names = (
        "cli_agent_orchestrator.constants",
        "cli_agent_orchestrator.clients.tmux",
        "cli_agent_orchestrator.clients.postgres.config",
        "test.fixtures.cao_server",
        "test.postgres.fixtures",
        "test.e2e.test_real_provider_matrix",
        "test.fixtures.owned_tmux",
    )
    relative_paths = (
        "src/cli_agent_orchestrator/constants.py",
        "src/cli_agent_orchestrator/clients/tmux.py",
        "src/cli_agent_orchestrator/clients/postgres/config.py",
        "test/fixtures/cao_server.py",
        "test/postgres/fixtures.py",
        "test/e2e/test_real_provider_matrix.py",
        "test/fixtures/owned_tmux.py",
    )
    modules = tuple(_import_source_module(name) for name in names)
    for module, relative_path in zip(modules, relative_paths, strict=True):
        try:
            module_path = Path(module.__file__)
            expected_path = source_root / relative_path
            if not module_path.samefile(expected_path):
                raise ValueError("source module path mismatch")
        except (OSError, TypeError, ValueError):
            raise AcceptanceFailure("preflight") from None
    (
        constants_module,
        tmux_module,
        postgres_config_module,
        server_module,
        postgres_module,
        matrix_module,
        owned_tmux_module,
    ) = modules
    if (
        getattr(constants_module, "SESSION_PREFIX", None) != "cao-"
        or owned_tmux_module is not owned_tmux
    ):
        raise AcceptanceFailure("preflight")
    return (
        constants_module,
        tmux_module,
        postgres_config_module,
        server_module,
        postgres_module,
        matrix_module,
    )


def _provider_auth_helpers(source_module: Any) -> tuple[Any, Any]:
    """Resolve provider authorization only from the source-bound CaoServer fixture."""
    if getattr(source_module, "__name__", None) != "test.fixtures.cao_server":
        raise AcceptanceFailure("preflight")
    authorize = getattr(source_module, "_authorize_live_provider", None)
    stage_auth = getattr(source_module, "_stage_live_provider_auth", None)
    if (
        not callable(authorize)
        or not callable(stage_auth)
        or getattr(authorize, "__module__", None) != "test.fixtures.cao_server"
        or getattr(stage_auth, "__module__", None) != "test.fixtures.cao_server"
    ):
        raise AcceptanceFailure("preflight")
    return authorize, stage_auth


def _create_private_tmux_tmpdir() -> Path:
    try:
        return owned_tmux.create_private_tmux_tmpdir()
    except OSError:
        raise AcceptanceFailure("preflight") from None


def _hash_source_bindings(source_root: Path) -> dict[str, str]:
    bindings: dict[str, str] = {}
    try:
        for relative in _SOURCE_BINDING_FILES:
            path = source_root / relative
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise AcceptanceFailure("preflight")
            bindings[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, RuntimeError):
        raise AcceptanceFailure("preflight") from None
    return bindings


def _hash_candidate_bindings(candidate_dir: Path) -> dict[str, str]:
    relatives = (
        "message_api_restart.py",
        "message_api_restart_live.py",
        "test_message_api_restart.py",
        "message_mcp_observer.py",
        "message_mcp_shutdown.py",
        "test_message_mcp_shutdown.py",
        "test_message_phase_binding.py",
        "codex_runtime_snapshot.py",
        "test_codex_runtime_snapshot.py",
        "sitecustomize.py",
        "claude_auth_archive.py",
    )
    bindings: dict[str, str] = {}
    try:
        for relative in relatives:
            path = candidate_dir / relative
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise AcceptanceFailure("preflight")
            bindings[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, RuntimeError):
        raise AcceptanceFailure("preflight") from None
    return bindings


def _claude_archive_helper_origin_matches_candidate() -> bool:
    try:
        helper_path = Path(_claude_auth_archive.__file__)
        expected_path = Path(__file__).parent / "claude_auth_archive.py"
        helper_info = helper_path.lstat()
        expected_info = expected_path.lstat()
        return (
            stat.S_ISREG(helper_info.st_mode)
            and not stat.S_ISLNK(helper_info.st_mode)
            and stat.S_ISREG(expected_info.st_mode)
            and not stat.S_ISLNK(expected_info.st_mode)
            and helper_path.samefile(expected_path)
        )
    except (OSError, TypeError, ValueError):
        return False


def _staged_auth_is_private(home: Path, authorization: Any) -> bool:
    auth_files = getattr(authorization, "auth_files", None)
    if not isinstance(auth_files, tuple) or not auth_files:
        return False
    for relative in auth_files:
        try:
            metadata = (home / relative).lstat()
        except (OSError, TypeError):
            return False
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            return False
    return True


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise AcceptanceFailure("cleanup") from None


def _write_claude_exit_diagnostic(state: RunState, diagnostic: _ClaudeExitDiagnostic) -> None:
    """Write a best-effort, closed-enum sidecar without affecting acceptance."""
    try:
        _write_private_json(state.run_root / "claude-exit-diagnostic.json", diagnostic.as_record())
    except AcceptanceFailure:
        # Diagnostic I/O is intentionally outside the cleanup decision.
        return


def _audit_failure_marker(state: RunState) -> Path:
    return state.audit_path.with_name(state.audit_path.name + ".failed")


def _audit_failure_present(state: RunState) -> bool:
    try:
        _audit_failure_marker(state).lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _audit_completion_present(state: RunState) -> bool:
    return audit_completion_seal_valid(state.audit_path)


def _wait_for_audit_completion(state: RunState, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _audit_completion_present(state):
            return True
        time.sleep(0.1)
    return _audit_completion_present(state)


def _api_request(
    method: str,
    url: str,
    *,
    timeout: float | requests.adapters.TimeoutSauce = 8.0,
    failure_stage: str = "phase_a",
    **kwargs: Any,
) -> tuple[int, Any]:
    response: requests.Response | None = None
    try:
        response = requests.request(method, url, timeout=timeout, **kwargs)
        status_code = response.status_code
        try:
            body = response.json()
        except (requests.JSONDecodeError, ValueError):
            body = None
        return status_code, body
    except requests.RequestException:
        raise AcceptanceFailure(failure_stage) from None
    finally:
        if response is not None:
            response.close()


def _profile_text(
    *,
    name: str,
    provider: str,
    model: str,
    allowed_tools: list[str],
    mcp_config: Mapping[str, Any] | None,
    prompt: str,
) -> str:
    lines = [
        "---",
        f"name: {name}",
        f"description: Private Spec023 {provider} acceptance profile",
        f"provider: {provider}",
        f"model: {json.dumps(model, ensure_ascii=False)}",
        f"allowedTools: {json.dumps(allowed_tools, ensure_ascii=False)}",
    ]
    if mcp_config is not None:
        lines.extend(
            (
                "mcpServers:",
                "  cao-mcp-server: " + json.dumps(mcp_config, ensure_ascii=False),
            )
        )
    lines.extend(("---", "", prompt.strip(), ""))
    return "\n".join(lines)


def _write_profile(home: Path, profile_text: str, name: str) -> Path:
    store = home / ".aws/cli-agent-orchestrator/agent-store"
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(store, 0o700)
    path = store / f"{name}.md"
    encoded = profile_text.encode("utf-8")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise AcceptanceFailure("native_terminals") from None
    return path


def _pick_free_port() -> int:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            candidate.bind(("127.0.0.1", 0))
            return int(candidate.getsockname()[1])
    except OSError:
        raise AcceptanceFailure("api_a_start") from None


def _api_process(server: Any, *, failure_stage: str = "api_a_start") -> subprocess.Popen[Any]:
    stop = server.stop
    for cell in getattr(stop, "__closure__", ()) or ():
        try:
            value = cell.cell_contents
        except ValueError:
            continue
        if isinstance(value, subprocess.Popen):
            return value
    raise AcceptanceFailure(failure_stage)


def _target_fingerprint(targets: Any, *, database_target_type: type[Any]) -> str:
    identities = []
    for name in ("main", "auth", "peer"):
        target = getattr(targets, name, None)
        if type(target) is not database_target_type:
            raise AcceptanceFailure("postgres_targets")
        identity = getattr(target, "identity", None)
        if (
            type(identity) is not tuple
            or len(identity) != 5
            or not isinstance(identity[0], str)
            or not identity[0]
            or isinstance(identity[1], bool)
            or not isinstance(identity[1], int)
            or not 1 <= identity[1] <= 65535
            or any(not isinstance(value, str) or not value for value in identity[2:])
        ):
            raise AcceptanceFailure("postgres_targets")
        identity_json = json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode(
            "ascii"
        )
        identities.append((name, hashlib.sha256(identity_json).hexdigest()))
    return hashlib.sha256(json.dumps(identities, separators=(",", ":")).encode("ascii")).hexdigest()


def _terminal_status(base_url: str, terminal_id: str, *, failure_stage: str) -> str:
    status, data = _api_request(
        "GET",
        f"{base_url}/terminals/{terminal_id}",
        failure_stage=failure_stage,
    )
    if status != 200 or not isinstance(data, dict):
        return "missing"
    value = data.get("status")
    return value if isinstance(value, str) else "unknown"


@dataclass(frozen=True)
class _PhaseTurnObservation:
    status: str
    sequence: int | None
    completed: int | None
    generation: str | None
    verified: bool


_UNKNOWN_PHASE_TURN = _PhaseTurnObservation("unknown", None, None, None, False)


def _terminal_turn_observation(
    base_url: str, terminal_id: str, *, provider: str, failure_stage: str, deadline: float
) -> _PhaseTurnObservation:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return _UNKNOWN_PHASE_TURN
    status, data = _api_request(
        "GET",
        f"{base_url}/terminals/{terminal_id}",
        timeout=min(2.0, remaining),
        failure_stage=failure_stage,
    )
    if status == 404:
        return _PhaseTurnObservation("missing", None, None, None, False)
    if (
        status != 200
        or not isinstance(data, dict)
        or data.get("id") != terminal_id
        or data.get("provider") != provider
    ):
        return _UNKNOWN_PHASE_TURN
    sequence, completed = data.get("turn_sequence"), data.get("turn_completed")
    if (
        type(sequence) is not int
        or sequence < 0
        or type(completed) is not int
        or not 0 <= completed <= sequence
    ):
        return _UNKNOWN_PHASE_TURN
    value = data.get("status")
    terminal_status = (
        value if isinstance(value, str) and value in NATIVE_TERMINAL_STATUSES else "unknown"
    )
    turn = data.get("turn")
    generation = turn.get("generation") if isinstance(turn, dict) else None
    bound = (
        isinstance(turn, dict)
        and turn.get("terminal_id") == terminal_id
        and turn.get("provider") == provider
        and type(generation) is str
        and re.fullmatch(r"[0-9a-f]{32}", generation) is not None
    )
    return _PhaseTurnObservation(
        terminal_status,
        sequence,
        completed,
        generation if bound else None,
        bool(bound and turn.get("state") == "verified"),
    )


def _wait_ready(
    base_url: str,
    terminal_id: str,
    timeout: float = 120.0,
    *,
    failure_stage: str = "native_terminals",
) -> str:
    deadline = time.monotonic() + timeout
    ready = {"idle", "completed"}
    consecutive = 0
    last = "unknown"
    while time.monotonic() < deadline:
        last = _terminal_status(base_url, terminal_id, failure_stage=failure_stage)
        if last in ready:
            consecutive += 1
            if consecutive == 3:
                return last
        else:
            consecutive = 0
        time.sleep(0.4)
    return last


def _get_rows(base_url: str, receiver_id: str, *, failure_stage: str) -> list[dict[str, Any]]:
    status, data = _api_request(
        "GET",
        f"{base_url}/terminals/{receiver_id}/inbox/messages",
        params={"limit": 10},
        failure_stage=failure_stage,
    )
    if (
        status != 200
        or not isinstance(data, list)
        or any(not isinstance(row, dict) for row in data)
    ):
        raise AcceptanceFailure(failure_stage)
    return data


def _valid_row(row: Mapping[str, Any], sender_id: str, receiver_id: str, body: str) -> bool:
    expected = {"id", "sender_id", "receiver_id", "message", "created_at"}
    return (
        set(row) == expected | {"status"}
        and type(row.get("id")) is int
        and row["id"] > 0
        and all(isinstance(row.get(key), str) and row[key] for key in expected - {"id"})
        and row.get("sender_id") == sender_id
        and row.get("receiver_id") == receiver_id
        and row.get("message") == body
        and row.get("status") in INBOX_STATUSES
    )


def _get_children(base_url: str, receiver_id: str, *, failure_stage: str) -> list[dict[str, Any]]:
    status, data = _api_request(
        "GET",
        f"{base_url}/terminals/{receiver_id}/children",
        failure_stage=failure_stage,
    )
    if (
        status != 200
        or not isinstance(data, list)
        or any(not isinstance(row, dict) for row in data)
    ):
        raise AcceptanceFailure(failure_stage)
    return data


def _start_stream(state: RunState, base_url: str, *, failure_stage: str) -> AguiCapture:
    if state.sender_id is None or state.receiver_id is None:
        raise AcceptanceFailure(state.stage)
    capture = AguiCapture(base_url, state.sender_id, state.receiver_id)
    state.captures.append(capture)
    capture.start(failure_stage=failure_stage)
    return capture


def _submit_trigger(
    base_url: str, sender_id: str, phase: str, *, state: RunState | None = None
) -> int:
    stage = "phase_a" if phase == "before_restart" else "phase_b"
    started = time.monotonic()
    if state is not None:
        state.observed_turn_generation = None
        try:
            prior_status, prior = _api_request(
                "GET",
                f"{base_url}/terminals/{sender_id}/turn",
                failure_stage=stage,
                timeout=2.0,
            )
        except AcceptanceFailure:
            _record_native_failure(
                state, point="phase_input", kind="request_error", started_at=started
            )
            raise
        previous_generation = prior.get("generation") if isinstance(prior, dict) else None
        valid_prior = (
            prior_status == 200
            and isinstance(prior, dict)
            and prior.get("terminal_id") == sender_id
            and prior.get("provider") == state.provider
            and (
                (previous_generation is None and prior.get("state") == "none")
                or (
                    type(previous_generation) is str
                    and re.fullmatch(r"[0-9a-f]{32}", previous_generation) is not None
                )
            )
        )
        if not valid_prior:
            _record_native_failure(
                state, point="phase_input", kind="invalid_shape", started_at=started
            )
            raise AcceptanceFailure(stage)
        state.previous_turn_generation = previous_generation
    try:
        status, body = _api_request(
            "POST",
            f"{base_url}/terminals/{sender_id}/input",
            params={"message": phase_trigger(phase)},
            timeout=12.0,
            failure_stage=stage,
        )
    except AcceptanceFailure:
        if state is not None:
            _record_native_failure(
                state, point="phase_input", kind="request_error", started_at=started
            )
        raise
    valid = (
        isinstance(body, dict)
        and body.get("success") is True
        and type(body.get("turn_sequence")) is int
        and body["turn_sequence"] > 0
    )
    if status not in {200, 201, 202} or not valid:
        if state is not None:
            _record_native_failure(
                state,
                point="phase_input",
                kind="http_status" if status not in {200, 201, 202} else "invalid_shape",
                started_at=started,
                http_status=status,
            )
        raise AcceptanceFailure(stage)
    return body["turn_sequence"]


def _accumulate_event_evidence(evidence: list[bool], batch: list[bool]) -> bool:
    evidence.extend(batch)
    return len(evidence) <= 1 and all(value is True for value in evidence)


def _record_failure_stage(state: RunState, stage: str) -> None:
    state.failure_stage = stage if stage in _RUN_STAGES - {"none"} else "preflight"


def _record_native_failure(
    state: RunState,
    *,
    point: str,
    kind: str,
    started_at: float,
    http_status: int = 0,
    terminal_status: str = "unknown",
    detail: str = "none",
) -> None:
    """Keep only allowlisted stage diagnostics; never retain response or pane text."""
    state.failure_point = point if point in NATIVE_FAILURE_POINTS else "none"
    state.failure_kind = kind if kind in NATIVE_FAILURE_KINDS else "none"
    state.failure_http_status = (
        http_status if type(http_status) is int and 100 <= http_status <= 599 else 0
    )
    state.last_terminal_status = (
        terminal_status if terminal_status in NATIVE_TERMINAL_STATUSES else "unknown"
    )
    elapsed_ms = int(max(0.0, time.monotonic() - started_at) * 1000)
    state.failure_elapsed_ms = min(elapsed_ms, 600_000)
    state.failure_detail = detail if detail in NATIVE_FAILURE_DETAILS else "unclassified"


_PHASE_FAILURE_KINDS = {
    "error": "provider_error",
    "waiting_quota": "quota_wait",
    "waiting_user_answer": "interaction_wait",
    "reconcile": "reconcile_required",
    "missing": "terminal_missing",
}
_PHASE_SETTLED_STATUSES = frozenset({"idle", "completed"})
_PHASE_STATUS_POLL_SECONDS = 1.0
_PHASE_NO_TOOL_GRACE_SECONDS = 8.0


def _phase_failure_kind(status: str) -> str | None:
    """Map one API terminal status to a fixed safe phase diagnostic."""
    return _PHASE_FAILURE_KINDS.get(status)


def _await_phase(
    state: RunState,
    *,
    capture: AguiCapture,
    expected_bodies: tuple[str, ...],
    phase_body: str,
    expected_call_count: int,
    phase: str,
) -> list[dict[str, Any]]:
    phase_started = time.monotonic()
    deadline = phase_started + 180.0
    stage = "phase_a" if phase == "before_restart" else "phase_b"
    if state.sender_id is None or state.receiver_id is None or state.active_server is None:
        raise AcceptanceFailure(stage)
    if type(state.expected_turn_sequence) is not int or state.expected_turn_sequence <= 0:
        raise AcceptanceFailure(stage)
    if phase not in {"before_restart", "after_restart"}:
        raise AcceptanceFailure(stage)
    try:
        persisted_bodies = tuple(
            expected_persisted_message(body, state.sender_id) for body in expected_bodies
        )
        persisted_phase_body = expected_persisted_message(phase_body, state.sender_id)
    except ValueError:
        raise AcceptanceFailure(stage) from None
    base_url = state.active_server.url
    matching_rows: list[dict[str, Any]] = []
    phase_events: list[bool] = []
    last_terminal_status = "unknown"
    next_status_poll = phase_started
    settled_polls = 0
    settled_since: float | None = None
    expected_generation: str | None = None
    turn_verified = False
    while time.monotonic() < deadline:
        if _audit_failure_present(state):
            raise AcceptanceFailure("observer_audit")
        try:
            records = read_dispatch_audit(state.audit_path)
        except RuntimeError:
            records = []
        if len(records) > 2 or any(record["tool"] != "send_message" for record in records):
            raise AcceptanceFailure(stage)
        if len(records) == expected_call_count and not validate_dispatch_history(
            records, expected_calls=expected_call_count
        ):
            raise AcceptanceFailure(stage)

        rows = _get_rows(base_url, state.receiver_id, failure_stage=stage)
        if len(rows) > len(persisted_bodies):
            raise AcceptanceFailure(stage)
        if len(rows) == len(persisted_bodies):
            by_body: dict[str, dict[str, Any]] = {}
            for row in rows:
                message = row.get("message")
                if not isinstance(message, str) or message in by_body:
                    raise AcceptanceFailure(stage)
                by_body[message] = row
            if set(by_body) == set(persisted_bodies) and all(
                _valid_row(by_body[body], state.sender_id, state.receiver_id, body)
                for body in persisted_bodies
            ):
                matching_rows = [by_body[body] for body in persisted_bodies]
        if not _accumulate_event_evidence(phase_events, capture.take_until(time.monotonic())):
            raise AcceptanceFailure(stage)

        # A native provider can finish a turn without ever reaching the MCP
        # server.  The old loop waited the full 180 seconds and then emitted an
        # all-``none`` receipt, which made a provider error indistinguishable
        # from a turn that simply never called a tool.  Poll the already
        # terminal's fixed metadata at a bounded cadence. Keep polling after
        # effects: a cached ready status or unseen-turn counter backstop is not
        # completion of this dispatch. Require its verified durable receipt.
        now = time.monotonic()
        evidence_seen = bool(records or matching_rows or phase_events)
        if now >= next_status_poll:
            try:
                observation = _terminal_turn_observation(
                    base_url,
                    state.sender_id,
                    provider=state.provider,
                    failure_stage=stage,
                    deadline=deadline,
                )
            except AcceptanceFailure:
                # The existing row/audit requests remain authoritative.  A
                # transient status read must not turn a healthy dispatch into
                # a false provider failure; the final deadline records the
                # last status that was actually observed.
                observation = _UNKNOWN_PHASE_TURN
            last_terminal_status = observation.status
            matches = observation.sequence == state.expected_turn_sequence
            generation_changed = False
            generation_reused = bool(
                matches
                and observation.generation is not None
                and observation.generation == state.previous_turn_generation
            )
            if matches and observation.generation is not None and not generation_reused:
                if expected_generation is None:
                    expected_generation = observation.generation
                    state.observed_turn_generation = expected_generation
                else:
                    generation_changed = observation.generation != expected_generation
            turn_verified = bool(
                matches
                and observation.verified
                and expected_generation is not None
                and observation.generation == expected_generation
                and not generation_reused
            )
            state.turn_observations[phase] = {
                "expected_sequence": state.expected_turn_sequence,
                "observed_sequence": observation.sequence,
                "observed_completed": observation.completed,
                "terminal_status": last_terminal_status,
                "receipt_verified": observation.verified,
                "causal_turn_verified": turn_verified,
                "generation_changed": generation_changed,
                "generation_reused": generation_reused,
            }
            state.last_terminal_status = last_terminal_status
            next_status_poll = now + _PHASE_STATUS_POLL_SECONDS
            if generation_changed or generation_reused:
                _record_native_failure(
                    state,
                    point="phase_wait",
                    kind="mismatch",
                    started_at=phase_started,
                    terminal_status=last_terminal_status,
                )
                raise AcceptanceFailure(stage)

            failure_kind = _phase_failure_kind(last_terminal_status)
            if failure_kind is not None:
                _record_native_failure(
                    state,
                    point="phase_wait",
                    kind=failure_kind,
                    started_at=phase_started,
                    terminal_status=last_terminal_status,
                )
                raise AcceptanceFailure(stage)

            if (
                not evidence_seen
                and turn_verified
                and last_terminal_status in _PHASE_SETTLED_STATUSES
            ):
                settled_polls += 1
                if settled_since is None:
                    settled_since = now
                # Only the verified current turn can satisfy this grace
                # window; cached readiness from an earlier turn cannot.
                if (
                    settled_polls >= 3
                    and settled_since is not None
                    and now - settled_since >= _PHASE_NO_TOOL_GRACE_SECONDS
                ):
                    _record_native_failure(
                        state,
                        point="phase_wait",
                        kind="no_tool_call",
                        started_at=phase_started,
                        terminal_status=last_terminal_status,
                    )
                    raise AcceptanceFailure(stage)
            else:
                settled_polls = 0
                settled_since = None
        if (
            len(records) == expected_call_count
            and matching_rows
            and matching_rows[-1].get("message") == persisted_phase_body
            and matching_rows[-1].get("status") == "delivered"
            and phase_events == [True]
            and turn_verified
            and last_terminal_status in _PHASE_SETTLED_STATUSES
            and _wait_ready(base_url, state.receiver_id, timeout=1.6, failure_stage=stage)
            in {"idle", "completed"}
        ):
            if not _accumulate_event_evidence(
                phase_events, capture.take_until(time.monotonic() + 0.6)
            ):
                raise AcceptanceFailure(stage)
            if phase_events == [True]:
                state.event_count += 1
                return matching_rows
            raise AcceptanceFailure(stage)
        time.sleep(0.5)
    _record_native_failure(
        state,
        point="phase_wait",
        kind="phase_timeout",
        started_at=phase_started,
        terminal_status=last_terminal_status,
    )
    raise AcceptanceFailure(stage)


def _stream_close_all(state: RunState) -> bool:
    okay = True
    for capture in state.captures or []:
        try:
            capture.stop()
        except BaseException:
            okay = False
        if capture._thread.is_alive() or capture._failed:
            okay = False
    return okay


def _delete_one(base_url: str, terminal_id: str) -> bool:
    try:
        status, _ = _api_request(
            "DELETE",
            f"{base_url}/terminals/{terminal_id}",
            timeout=12.0,
            failure_stage="cleanup",
        )
    except BaseException:
        return False
    return status == 200


def _graceful_claude_sender_exit_and_wait_for_seal(state: RunState, base_url: str) -> bool:
    """Use the existing audit allowance for Claude exit request plus seal polling."""
    if state.sender_id is None:
        return False
    deadline = time.monotonic() + 6.0
    request_ok = False
    request_outcome = _ClaudeExitRequestOutcome.NOT_ATTEMPTED
    seal_poll_result = _ClaudeExitPollResult.NOT_CHECKED
    budget_outcome = _ClaudeExitBudgetOutcome.WITHIN_NOMINAL_BUDGET
    remaining = max(0.0, deadline - time.monotonic())
    try:
        if getattr(state, "claude_quiescent_eof", False) is True:
            from message_mcp_shutdown import request_shutdown

            state.claude_eof_outcome = "requested"
            try:
                if not validate_dispatch_history(
                    read_dispatch_audit(state.audit_path), expected_calls=2
                ):
                    state.claude_eof_outcome = "history_invalid"
                    return False
            except (OSError, ValueError, RuntimeError):
                state.claude_eof_outcome = "history_invalid"
                return False
            try:
                if state.shutdown_mode != "harness_requested_eof":
                    if not request_shutdown(state.audit_path, deadline=deadline):
                        state.claude_eof_outcome = "eof_request_failed"
                        if time.monotonic() >= deadline:
                            budget_outcome = _ClaudeExitBudgetOutcome.EXHAUSTED_BEFORE_REQUEST
                            state.claude_eof_outcome = "deadline_exhausted"
                        return False
                if time.monotonic() >= deadline:
                    budget_outcome = _ClaudeExitBudgetOutcome.EXHAUSTED_BEFORE_REQUEST
                    state.claude_eof_outcome = "deadline_exhausted"
                    return False
                if not _audit_completion_present(state):
                    state.claude_eof_outcome = "normal_seal_missing"
                    return False
                state.shutdown_mode = "harness_requested_eof"
                state.claude_eof_outcome = "normal_eof_verified"
            except (OSError, ValueError, RuntimeError):
                state.claude_eof_outcome = "eof_control_error"
                if time.monotonic() >= deadline:
                    budget_outcome = _ClaudeExitBudgetOutcome.EXHAUSTED_BEFORE_REQUEST
                    state.claude_eof_outcome = "deadline_exhausted"
                return False
            remaining = max(0.0, deadline - time.monotonic())
        if remaining > 0:
            try:
                timeout = requests.adapters.TimeoutSauce(
                    total=remaining, connect=remaining, read=remaining
                )
                status, _ = _api_request(
                    "POST",
                    f"{base_url}/terminals/{state.sender_id}/exit",
                    timeout=timeout,
                    failure_stage="cleanup",
                )
                request_ok = status == 200
                request_outcome = (
                    _ClaudeExitRequestOutcome.HTTP_200
                    if request_ok
                    else _ClaudeExitRequestOutcome.HTTP_OTHER_STATUS
                )
            except BaseException:
                request_outcome = _ClaudeExitRequestOutcome.REQUEST_ERROR
                request_ok = False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # The existing poller's zero-time final read can see a seal after its
            # budget expired. Do not let that late observation turn cleanup green.
            budget_outcome = (
                _ClaudeExitBudgetOutcome.EXHAUSTED_AFTER_REQUEST
                if request_outcome is not _ClaudeExitRequestOutcome.NOT_ATTEMPTED
                else _ClaudeExitBudgetOutcome.EXHAUSTED_BEFORE_REQUEST
            )
            return False
        seal_ok = _wait_for_audit_completion(state, timeout=remaining)
        within_budget = time.monotonic() < deadline
        if seal_ok:
            seal_poll_result = (
                _ClaudeExitPollResult.SEAL_FOUND_POLL_RETURN_BEFORE_DEADLINE
                if within_budget
                else _ClaudeExitPollResult.SEAL_FOUND_POLL_RETURN_AFTER_DEADLINE
            )
        else:
            seal_poll_result = _ClaudeExitPollResult.NO_SEAL_AT_POLL_RETURN
        budget_outcome = (
            _ClaudeExitBudgetOutcome.WITHIN_NOMINAL_BUDGET
            if within_budget
            else _ClaudeExitBudgetOutcome.EXHAUSTED_AFTER_POLL
        )
        return request_ok and seal_ok and within_budget
    finally:
        state.claude_exit_diagnostic = _ClaudeExitDiagnostic(
            request=request_outcome,
            seal_poll_result=seal_poll_result,
            budget=budget_outcome,
        )


def _graceful_codex_mcp_shutdown(state: RunState) -> bool:
    from message_mcp_shutdown import request_shutdown

    deadline = time.monotonic() + 6.0
    try:
        if not validate_dispatch_history(read_dispatch_audit(state.audit_path), expected_calls=2):
            return False
        if getattr(state, "shutdown_mode", None) == "harness_requested_eof":
            return _audit_completion_present(state)
        completed = (
            request_shutdown(state.audit_path, deadline=deadline)
            and time.monotonic() < deadline
            and _audit_completion_present(state)
        )
        if completed:
            state.shutdown_mode = "harness_requested_eof"
        return completed
    except (OSError, ValueError, RuntimeError):
        return False


def _cleanup(state: RunState, *, server_module: Any, tmux_module: Any | None = None) -> bool:
    okay = _stream_close_all(state)
    owned_ids = [
        terminal_id
        for terminal_id in (state.sender_id, state.receiver_id)
        if terminal_id is not None and terminal_id not in (state.deleted_ids or set())
    ]
    if owned_ids and state.active_server is None and state.targets is not None:
        try:
            recovery = server_module._start_cao_server(
                state.home,
                state.port,
                extra_env={
                    "CAO_MCP_APPS_ENABLED": "true",
                    "CAO_ENABLE_SENDER_ID_INJECTION": "true",
                    "TMUX": "",
                    "TMUX_TMPDIR": str(state.tmux_tmpdir),
                    "TMPDIR": str(state.tmux_tmpdir),
                },
                deadline=30.0,
                database_targets=state.targets,
            )
            state.servers.append(recovery)
            state.active_server = recovery
        except BaseException:
            okay = False
    if state.provider == "codex" and state.sender_id is not None:
        if not _graceful_codex_mcp_shutdown(state):
            state.audit_failed = True
    if state.provider == "claude_code" and state.sender_id is not None:
        if state.sender_id in owned_ids and state.active_server is not None:
            if not _graceful_claude_sender_exit_and_wait_for_seal(state, state.active_server.url):
                state.audit_failed = True
        elif not _audit_completion_present(state):
            state.audit_failed = True
    if owned_ids and state.active_server is not None:
        for terminal_id in owned_ids:
            if _delete_one(state.active_server.url, terminal_id):
                state.deleted_ids.add(terminal_id)
            else:
                okay = False
    if (
        state.session_name is not None
        and (state.receiver_id is None or state.sender_id is None)
        and state.active_server is not None
    ):
        # If a create response was lost, delete only the uniquely generated
        # session name registered before its POST; never enumerate sessions.
        try:
            status, _ = _api_request(
                "DELETE",
                f"{state.active_server.url}/sessions/{state.session_name}",
                timeout=12.0,
                failure_stage="cleanup",
            )
        except BaseException:
            status = 599
        if status not in {200, 404}:
            okay = False
    for server in reversed(state.servers or []):
        if not _stop_server(state, server):
            okay = False
    state.audit_failed = state.audit_failed or _audit_failure_present(state)
    if state.sender_id is not None:
        if state.provider in {"claude_code", "codex"}:
            if not _audit_completion_present(state):
                state.audit_failed = True
        elif not _wait_for_audit_completion(state):
            state.audit_failed = True
    api_processes_exited = True
    for server in state.servers or []:
        try:
            if _api_process(server).poll() is None:
                api_processes_exited = False
        except BaseException:
            api_processes_exited = False
    if not api_processes_exited:
        okay = False
    state.active_server = None
    tmux_root_removed = True
    if tmux_module is not None:
        tmux_root_removed = _cleanup_private_tmux_root(
            state,
            tmux_module,
            api_processes_exited=api_processes_exited,
        )
        if not tmux_root_removed:
            okay = False
    if state.target_context is not None and api_processes_exited and tmux_root_removed:
        try:
            state.target_context.__exit__(None, None, None)
        except BaseException:
            okay = False
            state.cleanup = "failed"
        finally:
            state.target_context = None
    elif state.target_context is not None:
        _PRESERVED_TARGET_CONTEXTS.append(state.target_context)
        state.target_context = None
    state.cleanup = "pass" if okay and len(state.deleted_ids) == len(owned_ids) else "failed"
    if (
        state.cleanup == "pass"
        and state.provider == "claude_code"
        and state.claude_auth_staging_attempted
    ):
        try:
            if not _claude_archive_helper_origin_matches_candidate():
                raise _claude_auth_archive.CredentialArchiveError("archive helper origin invalid")
            archive_root = _claude_auth_archive.prepare_archive_root(
                state.run_root / "claude-auth-archive"
            )
            _claude_auth_archive.archive_private_credential_copy(
                state.workspace,
                archive_root,
                process_stopped=api_processes_exited,
                layout="message",
                archive_key=state.run_root.name,
            )
        except Exception:
            state.cleanup = "failed"
            okay = False
    if state.cleanup == "pass":
        try:
            shutil.rmtree(state.workspace)
        except FileNotFoundError:
            pass
        except OSError:
            okay = False
            state.cleanup = "failed"
    if state.claude_exit_diagnostic is not None:
        _write_claude_exit_diagnostic(state, state.claude_exit_diagnostic)
    return okay


def _verify_selected_setup(
    provider: str, source_root: Path, candidate_dir: Path
) -> tuple[Any, Any, Any, Any, Any, Any, Any, Any]:
    if os.environ.get(LIVE_ENABLE_ENV) != "1":
        raise AcceptanceFailure("preflight")
    if os.environ.get(POSTGRES_AUTOSTART_ENV) != "0":
        raise AcceptanceFailure("preflight")
    metadata_path = os.environ.get(POSTGRES_METADATA_ENV)
    if not metadata_path or not Path(metadata_path).is_absolute():
        raise AcceptanceFailure("preflight")
    try:
        metadata = json.loads(os.environ.get("CAO_REAL_PROVIDER_E2E_PROVIDERS", ""))
    except json.JSONDecodeError:
        raise AcceptanceFailure("preflight") from None
    selected = metadata.get(provider) if isinstance(metadata, dict) else None
    if not isinstance(selected, dict) or selected.get("exclude_reason") is not None:
        raise AcceptanceFailure("preflight")
    if shutil.which(selected.get("binary", "")) is None:
        raise AcceptanceFailure("preflight")
    source_files = _hash_source_bindings(source_root)
    candidate_files = _hash_candidate_bindings(candidate_dir)
    (
        constants_module,
        tmux_module,
        postgres_config_module,
        source_module,
        postgres_module,
        matrix_module,
    ) = _source_modules(source_root)
    for key in (SOURCE_ROOT_ENV,):
        if os.environ.get(key) != str(source_root):
            raise AcceptanceFailure("preflight")
    if not (candidate_dir / "sitecustomize.py").is_file():
        raise AcceptanceFailure("preflight")
    if str(Path(PINNED_PYTHON)) != sys.executable or not Path(PINNED_PYTHON).is_file():
        raise AcceptanceFailure("preflight")
    return (
        source_files,
        candidate_files,
        constants_module,
        tmux_module,
        postgres_config_module,
        source_module,
        postgres_module,
        matrix_module,
    )


def _start_server(state: RunState, server_module: Any, port: int) -> Any:
    if state.tmux_tmpdir is None:
        raise AcceptanceFailure("preflight")
    server = server_module._start_cao_server(
        state.home,
        port,
        extra_env={
            "CAO_MCP_APPS_ENABLED": "true",
            "CAO_ENABLE_SENDER_ID_INJECTION": "true",
            "TMUX": "",
            "TMUX_TMPDIR": str(state.tmux_tmpdir),
            "TMPDIR": str(state.tmux_tmpdir),
        },
        deadline=45.0,
        database_targets=state.targets,
    )
    state.servers.append(server)
    state.active_server = server
    return server


def _stop_server(state: RunState, server: Any) -> bool:
    if id(server) in (state.stopped_servers or set()):
        return True
    try:
        try:
            server.stop()
        except BaseException:
            pass
        process = _api_process(server)
        if process.poll() is None:
            if os.getpgid(process.pid) != process.pid:
                return False
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3.0)
        if process.poll() is None:
            return False
    except BaseException:
        return False
    state.stopped_servers.add(id(server))
    return True


def _read_process_identity(pid: int) -> tuple[int, str] | None:
    """Read an owned process UID and Linux start time without exposing argv."""
    return owned_tmux._read_process_identity(pid)


def _cleanup_private_tmux_root(
    state: RunState,
    tmux_module: Any,
    *,
    command_runner: Any = None,
    api_processes_exited: bool = True,
) -> bool:
    root = state.tmux_tmpdir
    if root is None:
        return True
    owned_terminal_count = sum(
        terminal_id is not None for terminal_id in (state.sender_id, state.receiver_id)
    )
    if not api_processes_exited or len(state.deleted_ids or set()) != owned_terminal_count:
        return False
    return owned_tmux.cleanup_private_tmux_root(
        root,
        state.home,
        tmux_module,
        owner_stopped=True,
        command_runner=command_runner,
    )


def _admit_codex_runtime(state: RunState, binary_name: str) -> None:
    """Bind the full package before staging account files or acquiring services."""
    started = time.monotonic()
    try:
        module = importlib.import_module("test.live_provider.codex_runtime_snapshot")
        expected_source = state.source_root / "test/live_provider/codex_runtime_snapshot.py"
        if not Path(module.__file__).samefile(expected_source):
            raise RuntimeError("codex_runtime_snapshot_unavailable")
        selected = shutil.which(binary_name)
        manifest = os.environ.get("CAO_SPEC023_CODEX_RUNTIME_MANIFEST", "")
        expected = os.environ.get("CAO_SPEC023_CODEX_RUNTIME_SHA256", "")
        if not selected or not manifest or not expected:
            raise RuntimeError("codex_runtime_snapshot_unavailable")
        result = module.verify_codex_runtime_snapshot(
            Path(selected), Path(manifest), expected_manifest_sha256=expected
        )
        _write_private_json(
            state.run_root / "codex-runtime-admission.json",
            {
                "schema": "caos.codex.runtime-admission.v1",
                "version": result["version"],
                "asset_count": result["asset_count"],
                "manifest_sha256": expected,
            },
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        _record_native_failure(
            state, point="runtime_admission", kind="invalid_shape", started_at=started
        )
        raise AcceptanceFailure("preflight") from None


def _build_sender_prompt(provider: str, receiver_id: str) -> str:
    input_rule = "the entire user message is exactly one of these phase triggers"
    reply_rule = "reply only with `sent`"
    if provider in {"codex", "claude_code", "opencode_cli"}:
        input_rule = (
            "the entire user message is exactly one of these phase triggers or exactly "
            "one of these triggers followed only by the CAO-generated turn-receipt envelope; "
            "any other text or second trigger is invalid"
        )
        reply_rule = (
            "reply with `sent` on its own line followed by the exact CAO receipt required by "
            "that envelope on the final visible line"
        )
    return (
        "You may use only the configured CAO MCP server. "
        f"Do not call any tool unless {input_rule}. "
        f"For SPEC023_SEND_BEFORE_RESTART, call send_message exactly once with receiver_id "
        f"{receiver_id} and message {json.dumps(PRE_RESTART_MESSAGE, ensure_ascii=False)}. "
        f"For SPEC023_SEND_AFTER_RESTART, call send_message exactly once with the same "
        f"receiver_id and message {json.dumps(POST_RESTART_MESSAGE, ensure_ascii=False)}. "
        f"After the tool returns, {reply_rule}. Never repeat a call."
    )


def run_selected_message_acceptance(request: Any) -> dict[str, Any]:
    """Run one explicitly selected real provider lane; all gates default off."""
    selected = parse_provider_gates(os.environ)
    if not selected:
        pytest.skip("Spec023 real-provider lane disabled; all provider gates default off")
    provider = selected[0]
    try:
        claude_quiescent_eof = (
            parse_claude_eof_gate(os.environ) if provider == "claude_code" else False
        )
    except ValueError:
        pytest.fail("spec023_preflight_failed", pytrace=False)
    source_env = os.environ.get(SOURCE_ROOT_ENV)
    evidence_env = os.environ.get(EVIDENCE_ROOT_ENV)
    if not source_env or not evidence_env:
        pytest.fail("spec023_preflight_failed", pytrace=False)
    try:
        source_root = Path(source_env).resolve(strict=True)
        evidence_root = Path(evidence_env)
        _private_directory(evidence_root, must_exist=True)
        (
            source_files,
            candidate_files,
            constants_module,
            tmux_module,
            postgres_config_module,
            server_module,
            postgres_module,
            matrix_module,
        ) = _verify_selected_setup(
            provider,
            source_root,
            Path(__file__).parent,
        )
    except BaseException:
        pytest.fail("spec023_preflight_failed", pytrace=False)

    run_root = evidence_root / f"spec023-{secrets.token_hex(8)}"
    try:
        _private_directory(run_root, must_exist=False)
    except BaseException:
        pytest.fail("spec023_preflight_failed", pytrace=False)
    binding_receipt = {
        "schema_version": 1,
        "source_files": source_files,
        "candidate_files": candidate_files,
    }
    _write_private_json(run_root / "source-bindings.json", binding_receipt)
    state = RunState(
        provider=provider,
        run_root=run_root,
        workspace=run_root / "workspace",
        home=run_root / "workspace" / "home",
        source_root=source_root,
        audit_path=run_root / "sender-mcp-audit.jsonl",
        claude_quiescent_eof=claude_quiescent_eof,
    )
    state.workspace.mkdir(mode=0o700)
    state.home.mkdir(mode=0o700)

    try:
        if (
            _hash_source_bindings(source_root) != source_files
            or _hash_candidate_bindings(Path(__file__).parent) != candidate_files
        ):
            raise AcceptanceFailure("preflight")
        state.tmux_tmpdir = _create_private_tmux_tmpdir()
        port = _pick_free_port()
        state.port = port
        state.stage = "preflight"
        authorize_live_provider, stage_live_provider_auth = _provider_auth_helpers(server_module)
        authorization = authorize_live_provider(provider, opt_in_env=LIVE_ENABLE_ENV)
        if provider == "codex":
            _admit_codex_runtime(state, authorization.binary)
        if provider == "claude_code":
            state.claude_auth_staging_attempted = True
        stage_live_provider_auth(state.home, authorization)
        config = build_message_mcp_server_config(
            provider=provider,
            python_executable=Path(PINNED_PYTHON),
            source_root=source_root,
            candidate_test_dir=Path(__file__).parent,
            audit_path=state.audit_path,
            claude_quiescent_eof=state.claude_quiescent_eof,
        )
        tools_ready = message_mcp_config_ready(
            provider,
            config,
            python_executable=Path(PINNED_PYTHON),
            source_root=source_root,
            candidate_test_dir=Path(__file__).parent,
            audit_path=state.audit_path,
            claude_quiescent_eof=state.claude_quiescent_eof,
        )
        require_selected_provider_ready(
            provider,
            {
                "binary": shutil.which(authorization.binary) is not None,
                "auth": _staged_auth_is_private(state.home, authorization),
                "source": (
                    _hash_source_bindings(source_root) == source_files
                    and _hash_candidate_bindings(Path(__file__).parent) == candidate_files
                ),
                "tools": tools_ready,
            },
        )

        state.stage = "postgres_targets"
        postgres_service = request.getfixturevalue("postgres_service")
        target_context = postgres_module.isolated_live_targets(
            state.workspace / "postgres", service=postgres_service
        )
        state.target_context = target_context
        state.targets = target_context.__enter__()
        database_identity = _target_fingerprint(
            state.targets,
            database_target_type=postgres_config_module.DatabaseTarget,
        )

        state.stage = "api_a_start"
        server_a = _start_server(state, server_module, port)
        process_a = _api_process(server_a)

        state.stage = "native_terminals"
        receiver_name = f"spec023-receiver-{secrets.token_hex(4)}"
        sender_name = f"spec023-sender-{secrets.token_hex(4)}"
        state.session_name = owned_session_name(
            constants_module.SESSION_PREFIX, secrets.token_hex(5)
        )
        receiver_prompt = (
            "You are a native message receiver. Read incoming agent messages and acknowledge "
            "them briefly. Do not call tools, delegate, edit files, or start other work."
        )
        profile_started = time.monotonic()
        try:
            receiver_profile = _write_profile(
                state.home,
                _profile_text(
                    name=receiver_name,
                    provider=provider,
                    model=authorization.model,
                    allowed_tools=[],
                    mcp_config=None,
                    prompt=receiver_prompt,
                ),
                receiver_name,
            )
        except AcceptanceFailure:
            _record_native_failure(
                state,
                point="profile_write",
                kind="io_error",
                started_at=profile_started,
            )
            raise
        if provider == "opencode_cli":
            install_started = time.monotonic()
            try:
                status, _ = _api_request(
                    "POST",
                    f"{server_a.url}/agents/profiles/install",
                    json={"source": receiver_name, "provider": provider},
                    timeout=15.0,
                    failure_stage="native_terminals",
                )
            except AcceptanceFailure:
                _record_native_failure(
                    state,
                    point="profile_install",
                    kind="request_error",
                    started_at=install_started,
                )
                raise
            if status != 200:
                _record_native_failure(
                    state,
                    point="profile_install",
                    kind="http_status",
                    started_at=install_started,
                    http_status=status,
                )
                raise AcceptanceFailure("native_terminals")
        receiver_started = time.monotonic()
        try:
            receiver_response = requests.post(
                f"{server_a.url}/sessions",
                params={
                    "provider": provider,
                    "agent_profile": receiver_name,
                    "session_name": state.session_name,
                },
                timeout=240.0,
            )
        except requests.RequestException:
            _record_native_failure(
                state,
                point="receiver_create",
                kind="request_error",
                started_at=receiver_started,
            )
            raise AcceptanceFailure("native_terminals") from None
        state.failure_point = "receiver_create"
        try:
            if receiver_response.status_code not in {200, 201}:
                _record_native_failure(
                    state,
                    point="receiver_create",
                    kind="http_status",
                    started_at=receiver_started,
                    http_status=receiver_response.status_code,
                    detail=classify_native_create_error_response(receiver_response),
                )
                raise AcceptanceFailure("native_terminals")
            receiver_data = receiver_response.json()
        except ValueError:
            _record_native_failure(
                state,
                point="receiver_create",
                kind="invalid_shape",
                started_at=receiver_started,
                http_status=receiver_response.status_code,
            )
            raise AcceptanceFailure("native_terminals") from None
        finally:
            receiver_response.close()
        if not isinstance(receiver_data, dict):
            _record_native_failure(
                state,
                point="receiver_create",
                kind="invalid_shape",
                started_at=receiver_started,
                http_status=receiver_response.status_code,
            )
            raise AcceptanceFailure("native_terminals")
        receiver_id = receiver_data.get("id")
        session_name = receiver_data.get("session_name")
        if not isinstance(receiver_id, str) or not _TERMINAL_ID.fullmatch(receiver_id):
            _record_native_failure(
                state,
                point="receiver_create",
                kind="invalid_shape",
                started_at=receiver_started,
                http_status=receiver_response.status_code,
            )
            raise AcceptanceFailure("native_terminals")
        if not isinstance(session_name, str) or not session_name:
            _record_native_failure(
                state,
                point="receiver_create",
                kind="invalid_shape",
                started_at=receiver_started,
                http_status=receiver_response.status_code,
            )
            raise AcceptanceFailure("native_terminals")
        state.receiver_id = receiver_id
        if session_name != state.session_name:
            _record_native_failure(
                state,
                point="receiver_create",
                kind="mismatch",
                started_at=receiver_started,
                http_status=receiver_response.status_code,
            )
            raise AcceptanceFailure("native_terminals")
        ready_started = time.monotonic()
        try:
            receiver_status = _wait_ready(
                server_a.url,
                receiver_id,
                failure_stage="native_terminals",
            )
        except AcceptanceFailure:
            _record_native_failure(
                state,
                point="receiver_readiness",
                kind="request_error",
                started_at=ready_started,
            )
            raise
        if receiver_status not in {"idle", "completed"}:
            elapsed = time.monotonic() - ready_started
            _record_native_failure(
                state,
                point="receiver_readiness",
                kind="readiness_timeout" if elapsed >= 120.0 else "mismatch",
                started_at=ready_started,
                terminal_status=receiver_status,
            )
            raise AcceptanceFailure("native_terminals")
        state.failure_point = "none"
        state.failure_kind = "none"
        state.failure_http_status = 0
        state.last_terminal_status = "unknown"
        state.failure_elapsed_ms = 0
        state.failure_detail = "none"

        prompt = _build_sender_prompt(provider, receiver_id)
        profile_started = time.monotonic()
        try:
            sender_profile = _write_profile(
                state.home,
                _profile_text(
                    name=sender_name,
                    provider=provider,
                    model=authorization.model,
                    allowed_tools=["@cao-mcp-server"],
                    mcp_config=config,
                    prompt=prompt,
                ),
                sender_name,
            )
        except AcceptanceFailure:
            _record_native_failure(
                state,
                point="profile_write",
                kind="io_error",
                started_at=profile_started,
            )
            raise
        if provider == "opencode_cli":
            install_started = time.monotonic()
            try:
                status, _ = _api_request(
                    "POST",
                    f"{server_a.url}/agents/profiles/install",
                    json={"source": sender_name, "provider": provider},
                    timeout=15.0,
                    failure_stage="native_terminals",
                )
            except AcceptanceFailure:
                _record_native_failure(
                    state,
                    point="profile_install",
                    kind="request_error",
                    started_at=install_started,
                )
                raise
            if status != 200:
                _record_native_failure(
                    state,
                    point="profile_install",
                    kind="http_status",
                    started_at=install_started,
                    http_status=status,
                )
                raise AcceptanceFailure("native_terminals")
        if not receiver_profile.is_file() or not sender_profile.is_file():
            _record_native_failure(
                state,
                point="profile_write",
                kind="mismatch",
                started_at=time.monotonic(),
            )
            raise AcceptanceFailure("native_terminals")

        sender_started = time.monotonic()
        try:
            sender_response = requests.post(
                f"{server_a.url}/sessions/{session_name}/terminals",
                params={
                    "provider": provider,
                    "agent_profile": sender_name,
                    "caller_id": receiver_id,
                },
                timeout=240.0,
            )
        except requests.RequestException:
            _record_native_failure(
                state,
                point="sender_create",
                kind="request_error",
                started_at=sender_started,
            )
            raise AcceptanceFailure("native_terminals") from None
        state.failure_point = "sender_create"
        try:
            if sender_response.status_code != 201:
                _record_native_failure(
                    state,
                    point="sender_create",
                    kind="http_status",
                    started_at=sender_started,
                    http_status=sender_response.status_code,
                    detail=classify_native_create_error_response(sender_response),
                )
                raise AcceptanceFailure("native_terminals")
            sender_data = sender_response.json()
        except ValueError:
            _record_native_failure(
                state,
                point="sender_create",
                kind="invalid_shape",
                started_at=sender_started,
                http_status=sender_response.status_code,
            )
            raise AcceptanceFailure("native_terminals") from None
        finally:
            sender_response.close()
        sender_id = sender_data.get("id") if isinstance(sender_data, dict) else None
        if not isinstance(sender_id, str) or not _TERMINAL_ID.fullmatch(sender_id):
            _record_native_failure(
                state,
                point="sender_create",
                kind="invalid_shape",
                started_at=sender_started,
                http_status=sender_response.status_code,
            )
            raise AcceptanceFailure("native_terminals")
        state.sender_id = sender_id
        sender_ready_started = time.monotonic()
        try:
            sender_status = _wait_ready(
                server_a.url,
                sender_id,
                failure_stage="native_terminals",
            )
        except AcceptanceFailure:
            _record_native_failure(
                state,
                point="sender_readiness",
                kind="request_error",
                started_at=sender_ready_started,
            )
            raise
        if sender_status not in {"idle", "completed"}:
            elapsed = time.monotonic() - sender_ready_started
            _record_native_failure(
                state,
                point="sender_readiness",
                kind="readiness_timeout" if elapsed >= 120.0 else "mismatch",
                started_at=sender_ready_started,
                terminal_status=sender_status,
            )
            raise AcceptanceFailure("native_terminals")
        state.failure_point = "none"
        state.failure_kind = "none"
        state.failure_http_status = 0
        state.last_terminal_status = "unknown"
        state.failure_elapsed_ms = 0
        state.failure_detail = "none"
        topology_started = time.monotonic()
        try:
            children = _get_children(
                server_a.url,
                receiver_id,
                failure_stage="native_terminals",
            )
        except AcceptanceFailure:
            _record_native_failure(
                state,
                point="topology_check",
                kind="request_error",
                started_at=topology_started,
            )
            raise
        if [row.get("terminal_id") for row in children] != [sender_id]:
            _record_native_failure(
                state,
                point="topology_check",
                kind="mismatch",
                started_at=topology_started,
            )
            raise AcceptanceFailure("native_terminals")
        inbox_started = time.monotonic()
        try:
            inbox_rows = _get_rows(
                server_a.url,
                receiver_id,
                failure_stage="native_terminals",
            )
        except AcceptanceFailure:
            _record_native_failure(
                state,
                point="inbox_check",
                kind="request_error",
                started_at=inbox_started,
            )
            raise
        if inbox_rows:
            _record_native_failure(
                state,
                point="inbox_check",
                kind="mismatch",
                started_at=inbox_started,
            )
            raise AcceptanceFailure("native_terminals")

        state.stage = "phase_a"
        capture_a = _start_stream(state, server_a.url, failure_stage="phase_a")
        state.expected_turn_sequence = _submit_trigger(
            server_a.url, sender_id, "before_restart", state=state
        )
        rows_a = _await_phase(
            state,
            capture=capture_a,
            expected_bodies=(PRE_RESTART_MESSAGE,),
            phase_body=PRE_RESTART_MESSAGE,
            expected_call_count=1,
            phase="before_restart",
        )
        statuses_a = {
            receiver_id: _wait_ready(server_a.url, receiver_id, failure_stage="phase_a"),
            sender_id: _wait_ready(server_a.url, sender_id, failure_stage="phase_a"),
        }
        state.terminals_preserved = set(statuses_a) == {sender_id, receiver_id} and all(
            value in {"idle", "completed"} for value in statuses_a.values()
        )
        if not state.terminals_preserved:
            raise AcceptanceFailure("api_restart")
        capture_a.stop()
        first_row = dict(rows_a[0])
        state.phase_rows.append(first_row)
        state.stage = "api_restart"
        audit_before = read_dispatch_audit(state.audit_path)
        if not validate_dispatch_history(audit_before, expected_calls=1):
            raise AcceptanceFailure("api_restart")
        audit_stat_before = state.audit_path.stat()
        stop_ok = _stop_server(state, server_a)
        state.active_server = None
        if not stop_ok or process_a.poll() is None:
            raise AcceptanceFailure("api_restart")

        state.stage = "api_b_verify"
        server_b = _start_server(state, server_module, port)
        process_b = _api_process(server_b, failure_stage="api_b_verify")
        if process_b.pid == process_a.pid:
            raise AcceptanceFailure("api_b_verify")
        statuses_b = {
            receiver_id: _wait_ready(server_b.url, receiver_id, failure_stage="api_b_verify"),
            sender_id: _wait_ready(server_b.url, sender_id, failure_stage="api_b_verify"),
        }
        if _get_children(server_b.url, receiver_id, failure_stage="api_b_verify") != children:
            child_ids = [
                row.get("terminal_id")
                for row in _get_children(server_b.url, receiver_id, failure_stage="api_b_verify")
            ]
            if child_ids != [sender_id]:
                raise AcceptanceFailure("api_b_verify")
        state.terminals_preserved = set(statuses_b) == {sender_id, receiver_id} and all(
            value in {"idle", "completed"} for value in statuses_b.values()
        )
        api_a = {
            "source_root": str(source_root),
            "home": str(state.home),
            "port": port,
            "database_identity": database_identity,
            "process_id": process_a.pid,
            "healthy": True,
        }
        api_b = {
            "source_root": str(source_root),
            "home": str(state.home),
            "port": port,
            "database_identity": database_identity,
            "process_id": process_b.pid,
            "healthy": True,
        }
        if not validate_restart_handoff(api_a, api_b, statuses_a, statuses_b):
            raise AcceptanceFailure("api_b_verify")
        rows_b = _get_rows(server_b.url, receiver_id, failure_stage="api_b_verify")
        if len(rows_b) != 1 or not _valid_row(
            rows_b[0],
            sender_id,
            receiver_id,
            expected_persisted_message(PRE_RESTART_MESSAGE, sender_id),
        ):
            raise AcceptanceFailure("api_b_verify")
        immutable = {
            key: first_row[key]
            for key in ("id", "sender_id", "receiver_id", "message", "created_at")
        }
        state.first_row_identity_matched = compare_inbox_row(immutable, rows_b[0])
        if not state.first_row_identity_matched:
            raise AcceptanceFailure("api_b_verify")
        audit_stat_after = state.audit_path.stat()
        if (audit_stat_before.st_dev, audit_stat_before.st_ino) != (
            audit_stat_after.st_dev,
            audit_stat_after.st_ino,
        ):
            raise AcceptanceFailure("api_b_verify")
        if (
            _hash_source_bindings(source_root) != source_files
            or _hash_candidate_bindings(Path(__file__).parent) != candidate_files
        ):
            raise AcceptanceFailure("api_b_verify")
        state.api_replacements = 1

        state.stage = "phase_b"
        capture_b = _start_stream(state, server_b.url, failure_stage="phase_b")
        state.expected_turn_sequence = _submit_trigger(
            server_b.url, sender_id, "after_restart", state=state
        )
        rows_final = _await_phase(
            state,
            capture=capture_b,
            expected_bodies=(PRE_RESTART_MESSAGE, POST_RESTART_MESSAGE),
            phase_body=POST_RESTART_MESSAGE,
            expected_call_count=2,
            phase="after_restart",
        )
        state.phase_rows = [dict(row) for row in rows_final]
        if len(rows_final) != 2 or len({row["id"] for row in rows_final}) != 2:
            raise AcceptanceFailure("phase_b")
        final_audit = read_dispatch_audit(state.audit_path)
        if not validate_dispatch_history(final_audit, expected_calls=2):
            raise AcceptanceFailure("phase_b")
        if (
            _hash_source_bindings(source_root) != source_files
            or _hash_candidate_bindings(Path(__file__).parent) != candidate_files
        ):
            raise AcceptanceFailure("phase_b")
        state.result = "pass"
        state.stage = "none"
        # A successful receipt does not need to expose the last polling sample;
        # keep native diagnostics neutral on the pass path.
        state.last_terminal_status = "unknown"
    except AcceptanceFailure as error:
        _record_failure_stage(state, error.stage)
        state.stage = error.stage
    except BaseException:
        _record_failure_stage(state, state.stage)
        state.stage = state.failure_stage
    finally:
        state.stage = "cleanup"
        cleanup_ok = _cleanup(state, server_module=server_module, tmux_module=tmux_module)
        if not cleanup_ok:
            state.result = "failed"
            if state.failure_stage == "none":
                state.failure_stage = "cleanup"
        if state.audit_failed:
            state.result = "failed"
            if state.failure_stage == "none":
                _record_failure_stage(state, "observer_audit")

    try:
        records = read_dispatch_audit(state.audit_path)
    except RuntimeError:
        records = []
    if state.result == "pass" and (
        state.audit_failed
        or not validate_dispatch_history(records, expected_calls=2)
        or not _audit_completion_present(state)
    ):
        state.result = "failed"
        _record_failure_stage(state, "observer_audit")
    _write_private_json(
        run_root / "phase-turn-observations.json",
        {"schema": "caos.message-phase-turn-observations.v1", "phases": state.turn_observations},
    )
    safe = project_safe_receipt(
        {
            "provider": state.provider,
            "shutdown_mode": state.shutdown_mode,
            "claude_quiescent_eof_requested": state.claude_quiescent_eof,
            "claude_eof_outcome": state.claude_eof_outcome,
            "result": state.result,
            "mcp_calls": records,
            "first_row_identity_matched": state.first_row_identity_matched,
            "events_redacted": state.event_count == 2,
            "terminals_preserved": state.terminals_preserved,
            "row_count": len(state.phase_rows or []),
            "event_count": state.event_count,
            "api_replacements": state.api_replacements,
            "cleanup": state.cleanup,
            "failure_stage": state.failure_stage if state.result != "pass" else "none",
            "failure_point": state.failure_point,
            "failure_kind": state.failure_kind,
            "failure_http_status": state.failure_http_status,
            "last_terminal_status": state.last_terminal_status,
            "failure_elapsed_ms": state.failure_elapsed_ms,
            "failure_detail": state.failure_detail,
        }
    )
    _write_private_json(run_root / "receipt.json", safe)
    return safe


def test_live_message_api_restart(request: Any) -> None:
    receipt = run_selected_message_acceptance(request)
    if receipt.get("result") != "pass" or receipt.get("cleanup") != "pass":
        pytest.fail("spec023_acceptance_failed", pytrace=False)
