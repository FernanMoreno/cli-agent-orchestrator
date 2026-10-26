"""Test-only helpers for a real API-process restart during interaction acceptance."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol, TextIO


@dataclass(frozen=True)
class SSEFrame:
    event: str | None
    data: str
    event_id: str | None


class SSEParser:
    """Parse one line at a time and dispatch only complete SSE frames."""

    def __init__(self) -> None:
        self._event: str | None = None
        self._event_id: str | None = None
        self._data: list[str] = []

    def feed(self, line: str) -> SSEFrame | None:
        if not isinstance(line, str):
            raise TypeError("sse_line_must_be_text")
        line = line.rstrip("\r\n")
        if not line:
            if not self._data:
                self._reset()
                return None
            frame = SSEFrame(self._event, "\n".join(self._data), self._event_id)
            self._reset()
            return frame
        if line.startswith(":"):
            return None
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            self._event = value
        elif field == "id" and "\x00" not in value:
            self._event_id = value
        elif field == "data":
            self._data.append(value)
        return None

    def _reset(self) -> None:
        self._event = None
        self._event_id = None
        self._data.clear()


def parse_sse_frames(lines: str | Iterable[str]) -> tuple[SSEFrame, ...]:
    """Parse complete frames from finite text/lines, ignoring an incomplete tail."""
    source = lines.splitlines() if isinstance(lines, str) else lines
    parser = SSEParser()
    frames: list[SSEFrame] = []
    for line in source:
        frame = parser.feed(line)
        if frame is not None:
            frames.append(frame)
    return tuple(frames)


def iter_sse_frames(lines: Iterable[str]) -> Iterable[SSEFrame]:
    parser = SSEParser()
    for line in lines:
        frame = parser.feed(line)
        if frame is not None:
            yield frame


_INTERACTION_SNAPSHOT_FIELDS = frozenset(
    {"available", "items", "next_cursor", "truncated", "event_sequence"}
)
_INTERACTION_ITEM_FIELDS = frozenset(
    {
        "question_id",
        "version",
        "group_id",
        "lifecycle",
        "delivery_state",
        "source",
        "task",
        "kind",
        "required",
        "recipient",
        "group_progress",
        "origin",
        "can_answer",
        "actions",
        "manual_reason",
        "created_at",
        "expires_at",
        "answer_receipt",
    }
)
_SOURCE_FIELDS = frozenset(
    {"terminal_id", "incarnation_id", "provider", "provider_version", "execution_mode"}
)
_TASK_FIELDS = frozenset({"work_item_id", "attempt_id", "session_name"})
_RECIPIENT_FIELDS = frozenset({"kind", "label", "reason"})
_GROUP_PROGRESS_FIELDS = frozenset({"required_total", "required_answered"})
_ANSWER_RECEIPT_FIELDS = frozenset(
    {"question_id", "version", "answer_id", "lifecycle", "delivery_state", "accepted_at"}
)
_LIFECYCLES = frozenset(
    {"open", "answered", "expired", "cancelled", "stale", "manual_only", "reconciled_external"}
)
_DELIVERY_STATES = frozenset(
    {
        "accepted",
        "prepared",
        "claimed",
        "returned_to_mcp",
        "agent_acknowledged",
        "confirmed",
        "uncertain",
        "no_effect",
        "reconciled",
    }
)
_UNSAFE_INTERACTION_KEYS = frozenset(
    {
        "prompt",
        "prompt_content",
        "options",
        "options_json",
        "response",
        "response_json",
        "receipt",
        "receipt_token",
        "capability",
        "source_token",
        "authorization",
    }
)

# Importing the production application and opening its isolated PostgreSQL
# connection can exceed ten seconds on a cold or memory pressured runner.  A
# short readiness budget made the AG-UI acceptance fail before OpenCode was
# even created; keep this bounded while allowing the worker to reach its
# health endpoint under normal cold-start conditions.
API_WORKER_READINESS_TIMEOUT_SECONDS = 45.0


def _assert_safe_interaction_value(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and key.lower() in _UNSAFE_INTERACTION_KEYS:
                raise ValueError("unsafe_interaction_projection")
            _assert_safe_interaction_value(child)
    elif isinstance(value, list):
        for child in value:
            _assert_safe_interaction_value(child)


def _exact_object(value: object, fields: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("unsafe_interaction_projection")
    return value


def _nullable_text(value: object) -> bool:
    return value is None or isinstance(value, str)


def _nonnegative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _enum_value(value: object, allowed: frozenset[str]) -> bool:
    return isinstance(value, str) and value in allowed


def _validate_answer_receipt(value: object) -> None:
    if value is None:
        return
    receipt = _exact_object(value, _ANSWER_RECEIPT_FIELDS)
    if (
        not isinstance(receipt.get("question_id"), str)
        or not receipt.get("question_id")
        or not isinstance(receipt.get("version"), int)
        or isinstance(receipt.get("version"), bool)
        or receipt["version"] < 1
        or not isinstance(receipt.get("answer_id"), str)
        or not receipt.get("answer_id")
        or not _enum_value(receipt.get("lifecycle"), _LIFECYCLES)
        or not _enum_value(receipt.get("delivery_state"), _DELIVERY_STATES)
        or not isinstance(receipt.get("accepted_at"), str)
    ):
        raise ValueError("unsafe_interaction_projection")


def _validate_source(value: object) -> None:
    source = _exact_object(value, _SOURCE_FIELDS)
    if (
        not isinstance(source.get("terminal_id"), str)
        or not source.get("terminal_id")
        or not isinstance(source.get("incarnation_id"), str)
        or not source.get("incarnation_id")
        or any(
            not _nullable_text(source.get(field))
            for field in ("provider", "provider_version", "execution_mode")
        )
    ):
        raise ValueError("unsafe_interaction_projection")


def _validate_task(value: object) -> None:
    if value is None:
        return
    task = _exact_object(value, _TASK_FIELDS)
    if any(not _nullable_text(task.get(field)) for field in _TASK_FIELDS):
        raise ValueError("unsafe_interaction_projection")


def _validate_recipient(value: object) -> None:
    recipient = _exact_object(value, _RECIPIENT_FIELDS)
    if (
        not _enum_value(
            recipient.get("kind"), frozenset({"human", "supervisor", "unassigned", "manual"})
        )
        or not _nullable_text(recipient.get("label"))
        or not isinstance(recipient.get("reason"), str)
    ):
        raise ValueError("unsafe_interaction_projection")


def _validate_group_progress(value: object) -> None:
    if value is None:
        return
    progress = _exact_object(value, _GROUP_PROGRESS_FIELDS)
    if not all(_nonnegative_integer(progress.get(field)) for field in _GROUP_PROGRESS_FIELDS):
        raise ValueError("unsafe_interaction_projection")


def _validate_interaction_item(value: object) -> Mapping[str, object]:
    item = _exact_object(value, _INTERACTION_ITEM_FIELDS)
    if (
        not isinstance(item.get("question_id"), str)
        or not item.get("question_id")
        or not isinstance(item.get("version"), int)
        or isinstance(item.get("version"), bool)
        or item["version"] < 1
        or not _nullable_text(item.get("group_id"))
        or not _enum_value(item.get("lifecycle"), _LIFECYCLES)
        or (
            item.get("delivery_state") is not None
            and not _enum_value(item.get("delivery_state"), _DELIVERY_STATES)
        )
        or not _enum_value(
            item.get("kind"),
            frozenset(
                {
                    "text",
                    "single_choice",
                    "multi_choice",
                    "choice_or_text",
                    "approval",
                    "credential",
                    "native_unknown",
                }
            ),
        )
        or not isinstance(item.get("required"), bool)
        or not _enum_value(item.get("origin"), frozenset({"explicit", "native"}))
        or item.get("can_answer") is not False
        or item.get("actions") != []
        or not _nullable_text(item.get("manual_reason"))
        or not isinstance(item.get("created_at"), str)
        or not _nullable_text(item.get("expires_at"))
    ):
        raise ValueError("unsafe_interaction_projection")

    _validate_source(item.get("source"))
    _validate_task(item.get("task"))
    _validate_recipient(item.get("recipient"))
    _validate_group_progress(item.get("group_progress"))
    _validate_answer_receipt(item.get("answer_receipt"))
    return item


def _validate_interaction_snapshot(value: object) -> Mapping[str, object]:
    interactions = _exact_object(value, _INTERACTION_SNAPSHOT_FIELDS)
    items = interactions.get("items")
    cursor = interactions.get("next_cursor")
    if interactions.get("available") is not True or not isinstance(items, list) or len(items) > 100:
        raise ValueError("unsafe_interaction_projection")
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 512):
        raise ValueError("unsafe_interaction_projection")
    if not isinstance(interactions.get("truncated"), bool):
        raise ValueError("unsafe_interaction_projection")
    sequence = interactions.get("event_sequence")
    if sequence is not None and not _nonnegative_integer(sequence):
        raise ValueError("unsafe_interaction_projection")
    for item in items:
        _validate_interaction_item(item)
    return interactions


def _validate_item_field_value(field: str, value: object) -> None:
    if field == "source":
        _validate_source(value)
        return
    elif field == "task":
        _validate_task(value)
        return
    elif field == "recipient":
        _validate_recipient(value)
        return
    elif field == "group_progress":
        _validate_group_progress(value)
        return
    elif field == "answer_receipt":
        _validate_answer_receipt(value)
        return
    elif field == "question_id":
        valid = isinstance(value, str) and bool(value)
    elif field == "version":
        valid = isinstance(value, int) and not isinstance(value, bool) and value >= 1
    elif field == "group_id":
        valid = _nullable_text(value)
    elif field == "lifecycle":
        valid = _enum_value(value, _LIFECYCLES)
    elif field == "delivery_state":
        if value is None or _enum_value(value, _DELIVERY_STATES):
            return
        raise ValueError("invalid_agui_delta_delivery_state")
    elif field == "kind":
        valid = _enum_value(
            value,
            frozenset(
                {
                    "text",
                    "single_choice",
                    "multi_choice",
                    "choice_or_text",
                    "approval",
                    "credential",
                    "native_unknown",
                }
            ),
        )
    elif field == "required":
        valid = isinstance(value, bool)
    elif field == "origin":
        valid = _enum_value(value, frozenset({"explicit", "native"}))
    elif field == "can_answer":
        valid = value is False
    elif field == "actions":
        valid = value == []
    elif field == "manual_reason":
        valid = _nullable_text(value)
    elif field == "created_at":
        valid = isinstance(value, str)
    elif field == "expires_at":
        valid = _nullable_text(value)
    else:
        raise ValueError("unsafe_interaction_projection")
    if valid is False:
        raise ValueError("unsafe_interaction_projection")


def _validate_interaction_delta_path(parts: list[str], value: object) -> tuple[int, str] | None:
    """Validate a patch value whenever its RFC-6902 path enters interactions."""
    if not parts or parts[0] != "interactions":
        return None
    if len(parts) == 1:
        _validate_interaction_snapshot(value)
        return None
    root_field = parts[1]
    if root_field not in _INTERACTION_SNAPSHOT_FIELDS:
        raise ValueError("unsafe_interaction_projection")
    if len(parts) == 2:
        if root_field == "items":
            if not isinstance(value, list) or len(value) > 100:
                raise ValueError("unsafe_interaction_projection")
            for item in value:
                _validate_interaction_item(item)
        elif root_field == "available":
            if value is not True:
                raise ValueError("unsafe_interaction_projection")
        elif root_field == "next_cursor":
            if value is not None and (not isinstance(value, str) or len(value) > 512):
                raise ValueError("unsafe_interaction_projection")
        elif root_field == "truncated":
            if not isinstance(value, bool):
                raise ValueError("unsafe_interaction_projection")
        elif value is not None and not _nonnegative_integer(value):
            raise ValueError("unsafe_interaction_projection")
        return None
    if root_field != "items" or not parts[2].isdigit() or str(int(parts[2])) != parts[2]:
        raise ValueError("unsafe_interaction_projection")
    item_index = int(parts[2])
    if len(parts) == 3:
        _validate_interaction_item(value)
        return item_index, ""
    item_field = parts[3]
    if item_field not in _INTERACTION_ITEM_FIELDS:
        raise ValueError("unsafe_interaction_projection")
    if len(parts) == 4:
        _validate_item_field_value(item_field, value)
        return item_index, item_field
    nested_fields = {
        "source": _SOURCE_FIELDS,
        "task": _TASK_FIELDS,
        "recipient": _RECIPIENT_FIELDS,
        "group_progress": _GROUP_PROGRESS_FIELDS,
        "answer_receipt": _ANSWER_RECEIPT_FIELDS,
    }.get(item_field)
    if nested_fields is None or len(parts) != 5 or parts[4] not in nested_fields:
        raise ValueError("unsafe_interaction_projection")
    if item_field == "source":
        if parts[4] in {"terminal_id", "incarnation_id"}:
            valid = isinstance(value, str) and bool(value)
        else:
            valid = _nullable_text(value)
    elif item_field == "task":
        valid = _nullable_text(value)
    elif item_field == "recipient":
        if parts[4] == "kind":
            valid = _enum_value(value, frozenset({"human", "supervisor", "unassigned", "manual"}))
        elif parts[4] == "label":
            valid = _nullable_text(value)
        else:
            valid = isinstance(value, str)
    elif item_field == "group_progress":
        valid = _nonnegative_integer(value)
    else:
        valid = _answer_receipt_field_is_valid(parts[4], value)
    if not valid:
        raise ValueError("unsafe_interaction_projection")
    return item_index, item_field


def _answer_receipt_field_is_valid(field: str, value: object) -> bool:
    if field in {"question_id", "answer_id", "accepted_at"}:
        return isinstance(value, str) and bool(value)
    if field == "version":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 1
    if field == "lifecycle":
        return _enum_value(value, _LIFECYCLES)
    if field == "delivery_state":
        return _enum_value(value, _DELIVERY_STATES)
    return False


def snapshot_from_frame(
    frame: SSEFrame,
    *,
    forbidden_content: Iterable[str] = (),
) -> dict[str, object]:
    """Decode one real AG-UI snapshot and reject unsafe or incomplete metadata."""
    if frame.event != "STATE_SNAPSHOT":
        raise ValueError("expected_agui_state_snapshot")
    try:
        payload = json.loads(frame.data)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid_agui_snapshot_json") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"snapshot"}
        or not isinstance(payload.get("snapshot"), dict)
    ):
        raise ValueError("invalid_agui_snapshot_shape")
    snapshot = payload["snapshot"]
    interactions = snapshot.get("interactions")
    if not isinstance(interactions, dict) or interactions.get("available") is not True:
        raise ValueError("interaction_projection_unavailable")
    interactions = _validate_interaction_snapshot(interactions)
    items = interactions["items"]
    _assert_safe_interaction_value(interactions)
    encoded_interactions = json.dumps(interactions, ensure_ascii=True, sort_keys=True)
    if any(secret and secret in encoded_interactions for secret in forbidden_content):
        raise ValueError("unsafe_interaction_projection")
    return snapshot


def snapshot_identity_map(snapshot: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """Project the already-validated snapshot to safe question identities only."""
    interactions = snapshot.get("interactions")
    if not isinstance(interactions, Mapping):
        raise ValueError("invalid_interaction_items")
    validated = _validate_interaction_snapshot(interactions)
    items = validated["items"]
    identities: dict[str, dict[str, object]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("invalid_interaction_item")
        question_id = item.get("question_id")
        version = item.get("version")
        group_id = item.get("group_id")
        source = item.get("source")
        if (
            not isinstance(question_id, str)
            or not question_id
            or question_id in identities
            or not isinstance(version, int)
            or isinstance(version, bool)
            or version < 1
            or not isinstance(group_id, str)
            or not group_id
            or not isinstance(source, Mapping)
            or not isinstance(source.get("terminal_id"), str)
            or not isinstance(source.get("incarnation_id"), str)
            or not isinstance(item.get("lifecycle"), str)
            or (
                item.get("delivery_state") is not None
                and not isinstance(item.get("delivery_state"), str)
            )
        ):
            raise ValueError("invalid_interaction_identity")
        identities[question_id] = {
            "group_id": group_id,
            "version": version,
            "terminal_id": source["terminal_id"],
            "incarnation_id": source["incarnation_id"],
            "lifecycle": item["lifecycle"],
            "delivery_state": item["delivery_state"],
        }
    return identities


def delivery_state_updates(
    frame: SSEFrame,
    interaction_items: list[dict[str, object]],
    *,
    forbidden_content: Iterable[str] = (),
) -> dict[str, str | None]:
    """Return question delivery-state changes from one safe production delta."""
    if frame.event != "STATE_DELTA":
        raise ValueError("expected_agui_state_delta")
    try:
        payload = json.loads(frame.data)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid_agui_delta_json") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"delta"}
        or not isinstance(payload.get("delta"), list)
    ):
        raise ValueError("invalid_agui_delta_shape")
    _assert_safe_interaction_value(payload)
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    if any(secret and secret in encoded for secret in forbidden_content):
        raise ValueError("unsafe_interaction_projection")
    updates: dict[str, str | None] = {}
    allowed_states = {
        None,
        "accepted",
        "returned_to_mcp",
        "agent_acknowledged",
        "confirmed",
        "uncertain",
        "no_effect",
        "reconciled",
    }
    for operation in payload["delta"]:
        if not isinstance(operation, dict):
            raise ValueError("invalid_agui_delta_operation")
        operation_name = operation.get("op")
        expected_operation_fields = (
            {"op", "path"} if operation_name == "remove" else {"op", "path", "value"}
        )
        if (
            operation_name not in {"add", "replace", "remove"}
            or set(operation) != expected_operation_fields
        ):
            raise ValueError("invalid_agui_delta_operation")
        path = operation.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("invalid_agui_delta_path")
        parts = [part.replace("~1", "/").replace("~0", "~") for part in path.split("/")[1:]]
        if any(part.lower() in _UNSAFE_INTERACTION_KEYS for part in parts):
            raise ValueError("unsafe_interaction_projection")
        validated_target = _validate_interaction_delta_path(parts, operation.get("value"))
        if validated_target is None or validated_target[1] != "delivery_state":
            continue
        try:
            if not parts[2].isdigit():
                raise ValueError
            item_index = validated_target[0]
            if len(parts) != 4 or item_index is None:
                raise ValueError
            item = interaction_items[item_index]
        except (ValueError, IndexError) as exc:
            raise ValueError("invalid_agui_delta_identity") from exc
        question_id = item.get("question_id")
        value = operation.get("value")
        if (
            not isinstance(question_id, str)
            or not question_id
            or operation_name not in {"add", "replace"}
            or (value is not None and not isinstance(value, str))
            or value not in allowed_states
        ):
            raise ValueError("invalid_agui_delta_delivery_state")
        updates[question_id] = value
    return updates


@dataclass(frozen=True)
class WorkerSpec:
    command: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    port: int

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def build_api_worker_spec(
    *,
    source_root: Path,
    run_root: Path,
    python_executable: str,
    port: int,
    parent_env: Mapping[str, str],
) -> WorkerSpec:
    """Build a source-bound worker command with only run-private CAO state."""
    root = Path(run_root).absolute()
    source = Path(source_root).absolute()
    if root.is_symlink() or not root.is_absolute():
        raise ValueError("unsafe_api_worker_root")
    if (
        not source.is_absolute()
        or not python_executable
        or not Path(python_executable).is_absolute()
    ):
        raise ValueError("invalid_api_worker_source")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("invalid_api_worker_port")

    env = {
        "PATH": parent_env.get("PATH", os.defpath),
        "LANG": parent_env.get("LANG", "C.UTF-8"),
        "HOME": str(root / "home"),
        "TMPDIR": str(root / "tmp"),
        "TMUX_TMPDIR": str(root / "tmux"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "PYTHONPATH": os.pathsep.join((str(source / "src"), str(source))),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "CAO_HOME_DIR": str(root / "cao"),
        "CAO_DATABASE_CONFIG_FILE": str(root / "cao" / "database-config.json"),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": str(port),
        "CAO_AUTH_MODE": "off",
        "CAO_AGUI_ENABLED": "true",
        "CAO_INTERACTION_AUDIT_FILE": str(root / "interaction-observations.jsonl"),
        "CAO_RUN_ROOT": str(root),
    }
    if parent_env.get("LC_ALL"):
        env["LC_ALL"] = parent_env["LC_ALL"]
    command = (
        python_executable,
        "-m",
        "test.live_provider.interaction_api_worker",
        "--port",
        str(port),
    )
    return WorkerSpec(command=command, cwd=root / "work", env=env, port=port)


class _OwnedProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


PopenFactory = Callable[..., _OwnedProcess]
ReadinessProbe = Callable[[_OwnedProcess, float], bool]


class ApiWorkerController:
    """Own only API children started by this controller; never signal global PIDs."""

    def __init__(
        self,
        spec: WorkerSpec,
        *,
        popen_factory: PopenFactory | None = None,
        readiness_probe: ReadinessProbe | None = None,
        readiness_timeout: float = API_WORKER_READINESS_TIMEOUT_SECONDS,
        stop_timeout: float = 5.0,
        stdout: TextIO | int = subprocess.DEVNULL,
        stderr: TextIO | int = subprocess.DEVNULL,
    ) -> None:
        self.spec = spec
        self._popen_factory = popen_factory if popen_factory is not None else subprocess.Popen
        self._readiness_probe = readiness_probe or self._http_readiness_probe
        self._readiness_timeout = readiness_timeout
        self._stop_timeout = stop_timeout
        self._stdout = stdout
        self._stderr = stderr
        self.process: _OwnedProcess | None = None

    def _http_readiness_probe(self, process: _OwnedProcess, timeout: float) -> bool:
        import httpx

        deadline = time.monotonic() + timeout
        with httpx.Client(timeout=httpx.Timeout(0.5, connect=0.5), trust_env=False) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    return False
                try:
                    response = client.get(f"{self.spec.base_url}/interactions?limit=1")
                except httpx.HTTPError:
                    time.sleep(0.05)
                    continue
                # The request itself can consume the remaining budget.  A
                # successful response received after the deadline is not
                # readiness evidence, and a worker that exited while the
                # request was in flight must not be accepted either.
                if (
                    response.status_code == 200
                    and process.poll() is None
                    and time.monotonic() <= deadline
                ):
                    return True
                time.sleep(0.05)
        return False

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("api_worker_already_running")
        process = self._popen_factory(
            list(self.spec.command),
            cwd=str(self.spec.cwd),
            env=dict(self.spec.env),
            stdin=subprocess.DEVNULL,
            stdout=self._stdout,
            stderr=self._stderr,
            text=True,
            start_new_session=True,
            shell=False,
        )
        self.process = process
        try:
            ready = self._readiness_probe(process, self._readiness_timeout)
        except BaseException:
            self.stop()
            raise
        if not ready:
            self.stop()
            raise TimeoutError("api_worker_not_ready")

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=self._stop_timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self._stop_timeout)
        elif process.poll() is not None:
            process.wait(timeout=self._stop_timeout)
        if process.poll() is None:
            raise RuntimeError("api_worker_cleanup_failed")
        self.process = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def crash(self) -> None:
        """Inject a crash only into this controller's live Popen child and reap it."""
        process = self.process
        if process is None or process.poll() is not None:
            raise RuntimeError("api_worker_not_live")
        process.kill()
        process.wait(timeout=self._stop_timeout)
        if process.poll() is None:
            raise RuntimeError("api_worker_cleanup_failed")
        self.process = None


def open_private_log(path: Path) -> TextIO:
    """Open a new private log or append to one created for this run only."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise RuntimeError("unsafe_api_worker_log_path")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_mode & 0o777 != 0o600:
            raise RuntimeError("unsafe_api_worker_log_mode")
        return os.fdopen(descriptor, "a", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _answer_identity(value: object) -> tuple[str, int, str] | None:
    question_id = _field(value, "question_id")
    version = _field(value, "version")
    receipt = _field(value, "receipt")
    if (
        not isinstance(question_id, str)
        or not question_id
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
        or not isinstance(receipt, str)
        or not receipt
    ):
        return None
    return question_id, version, hashlib.sha256(receipt.encode("utf-8")).hexdigest()


def ack_identity_diagnostics(
    *,
    created_group_id: object,
    created_questions: object,
    waited_group_id: object,
    waited_answers: object,
    ack_group_id: object,
    acknowledgements: object,
) -> dict[str, bool | int]:
    """Compare actual wait/ack identities while returning no receipt material."""

    def group(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    def versions(items: object) -> list[tuple[str, int]] | None:
        if not isinstance(items, (list, tuple)):
            return None
        result: list[tuple[str, int]] = []
        for item in items:
            question_id = _field(item, "question_id")
            version = _field(item, "version")
            if (
                not isinstance(question_id, str)
                or not question_id
                or not isinstance(version, int)
                or isinstance(version, bool)
                or version < 1
            ):
                return None
            result.append((question_id, version))
        return result

    created = group(created_group_id)
    waited = group(waited_group_id)
    acknowledged = group(ack_group_id)
    created_versions = versions(created_questions)
    returned_versions = versions(waited_answers)
    ack_versions = versions(acknowledgements)
    returned_identities = (
        [_answer_identity(item) for item in waited_answers]
        if isinstance(waited_answers, (list, tuple))
        else None
    )
    ack_identities = (
        [_answer_identity(item) for item in acknowledgements]
        if isinstance(acknowledgements, (list, tuple))
        else None
    )
    return {
        "group_matches_created": created is not None and acknowledged == created,
        "group_matches_waited": waited is not None and acknowledged == waited,
        "question_versions_match_created": (
            created_versions is not None
            and returned_versions is not None
            and sorted(created_versions) == sorted(returned_versions)
        ),
        "question_versions_match_returned": (
            returned_versions is not None
            and ack_versions is not None
            and sorted(returned_versions) == sorted(ack_versions)
        ),
        "receipt_hashes_match_returned": (
            returned_identities is not None
            and ack_identities is not None
            and all(item is not None for item in returned_identities)
            and all(item is not None for item in ack_identities)
            and sorted(returned_identities) == sorted(ack_identities)
        ),
        "ack_count": len(acknowledgements) if isinstance(acknowledgements, (list, tuple)) else -1,
    }


_OBSERVATION_FIELDS = {
    "wait_started": frozenset({"event", "group_id", "worker_pid", "wait_seconds"}),
    "group_created": frozenset({"event", "group_id", "questions"}),
    "wait_returned": frozenset({"event", "group_id", "state", "question_versions"}),
    "ack_identity": frozenset(
        {
            "event",
            "group_matches_created",
            "group_matches_waited",
            "question_versions_match_created",
            "question_versions_match_returned",
            "receipt_hashes_match_returned",
            "ack_count",
            "ack_succeeded",
        }
    ),
}


def _validate_observation(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("invalid_interaction_observation")
    event = value.get("event")
    if not isinstance(event, str) or event not in _OBSERVATION_FIELDS:
        raise ValueError("invalid_interaction_observation")
    if set(value) != _OBSERVATION_FIELDS[event]:
        raise ValueError("invalid_interaction_observation")
    _assert_safe_interaction_value(value)
    if event == "group_created":
        group_id = value.get("group_id")
        questions = value.get("questions")
        if (
            not isinstance(group_id, str)
            or not group_id
            or not isinstance(questions, list)
            or not questions
            or any(
                not isinstance(item, dict)
                or set(item) != {"question_id", "version"}
                or not isinstance(item.get("question_id"), str)
                or not item.get("question_id")
                or not isinstance(item.get("version"), int)
                or isinstance(item.get("version"), bool)
                or item.get("version", 0) < 1
                for item in questions
            )
        ):
            raise ValueError("invalid_interaction_observation")
    elif event == "wait_started":
        if (
            not isinstance(value.get("group_id"), str)
            or not value.get("group_id")
            or type(value.get("worker_pid")) is not int
            or value["worker_pid"] <= 0
            or type(value.get("wait_seconds")) is not int
            or not 0 <= value["wait_seconds"] <= 30
        ):
            raise ValueError("invalid_interaction_observation")
    elif event == "wait_returned":
        state = value.get("state")
        versions = value.get("question_versions")
        if (
            not isinstance(value.get("group_id"), str)
            or not value.get("group_id")
            or state not in {"pending", "ready", "expired", "cancelled", "stale", "cycle_blocked"}
            or not isinstance(versions, list)
            or (state != "ready" and versions)
            or any(
                not isinstance(item, dict)
                or set(item) != {"question_id", "version"}
                or not isinstance(item.get("question_id"), str)
                or not item.get("question_id")
                or not isinstance(item.get("version"), int)
                or isinstance(item.get("version"), bool)
                or item.get("version", 0) < 1
                for item in versions
            )
        ):
            raise ValueError("invalid_interaction_observation")
    else:
        boolean_fields = (
            "group_matches_created",
            "group_matches_waited",
            "question_versions_match_created",
            "question_versions_match_returned",
            "receipt_hashes_match_returned",
            "ack_succeeded",
        )
        ack_count = value.get("ack_count")
        if (
            any(type(value.get(field)) is not bool for field in boolean_fields)
            or not isinstance(ack_count, int)
            or isinstance(ack_count, bool)
            or not 0 <= ack_count <= 20
        ):
            raise ValueError("invalid_interaction_observation")
    return value


def append_observation(path: Path, observation: Mapping[str, object]) -> None:
    """Append a closed, content-free observation to a private run-owned file."""
    path = Path(path)
    if path.parent.is_symlink() or path.is_symlink():
        raise RuntimeError("unsafe_interaction_observation_path")
    payload = _validate_observation(dict(observation))
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if os.fstat(descriptor).st_mode & 0o777 != 0o600:
            raise RuntimeError("unsafe_interaction_observation_mode")
        os.write(descriptor, encoded)
    finally:
        os.close(descriptor)


def read_observations(path: Path) -> tuple[dict[str, object], ...]:
    """Read only validated, content-free worker observations from this run."""
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError("unsafe_interaction_observation_path")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return ()
    try:
        if os.fstat(descriptor).st_mode & 0o777 != 0o600:
            raise RuntimeError("unsafe_interaction_observation_mode")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            observations = []
            for line in stream:
                try:
                    observations.append(_validate_observation(json.loads(line)))
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ValueError("invalid_interaction_observation") from exc
            return tuple(observations)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
