"""Private, content-free observation of OpenCode's MCP protocol requests."""

from __future__ import annotations

import contextvars
import json
import os
import re
import stat
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

from mcp import types

INTERACTION_TOOLS = frozenset(
    {
        "cao_ask_user",
        "cao_wait_for_user_answers",
        "cao_acknowledge_user_answers",
        "cao_list_interactions",
        "cao_get_interaction",
        "cao_answer_interaction",
        "cao_escalate_interaction",
    }
)
_NONCE_RE = re.compile(r"[0-9a-f]{32}")
_MAX_EVENTS = 256
_MAX_BYTES = 128 * 1024
_MAX_MESSAGE_ARGUMENT_CHARACTERS = 8192
_MAX_MESSAGE_ARGUMENT_UTF8_BYTES = 32 * 1024
_MAX_SAFE_RESULT_BYTES = 4096
_MAX_SAFE_PEER_COUNT = 10_000
_SAFE_RESULT_TOOL = "list_local_cao_peers"
_SAFE_TASK_RESULT_TOOL = "get_local_cao_task"
_SAFE_RESULT_TOOLS = frozenset({_SAFE_RESULT_TOOL, _SAFE_TASK_RESULT_TOOL})
_SAFE_RESULT_TOOL_ENV = "CAO_OPENCODE_MCP_SAFE_RESULT_TOOL"
_SAFE_TASK_BINDING_ENV = "CAO_OPENCODE_MCP_SAFE_TASK_BINDING_PATH"
_TASK_UUID_RE = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}")
_TASK_REQUESTER_RE = re.compile(r"[a-f0-9]{8}")
_TASK_STATES = frozenset({"succeeded", "failed", "cancelled"})
_MAX_TASK_BINDING_BYTES = 1024
_COMPLETE_SUFFIX = ".complete"
_COMPLETE_PENDING_SUFFIX = ".complete.pending"
_COMPLETE_CONTENT = b"CAO_OPEN_CODE_MCP_OBSERVER_COMPLETE\n"
OBSERVER_ENABLE_ENV = "CAO_OPENCODE_MCP_OBSERVER"
OBSERVER_PATH_ENV = "CAO_OPENCODE_MCP_OBSERVER_PATH"
OBSERVER_NONCE_ENV = "CAO_OPENCODE_MCP_OBSERVER_NONCE"
_IN_TOOL_CALL: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "cao_opencode_mcp_internal_tool_call", default=False
)


class _DuplicateJSONKeyError(ValueError):
    """Raised without exposing private trace data when a row repeats a key."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKeyError("observer_duplicate_json_key")
        result[key] = value
    return result


def _safe_local_peer_result(payload: Any) -> tuple[bool | None, int | None]:
    """Read only the fixed success flag and peer-list length from one tool result."""
    try:
        if not isinstance(payload, types.CallToolResult) or type(payload.isError) is not bool:
            return None, None
        if payload.isError:
            return None, None

        candidate: Any = None
        structured_present = False
        for attribute in ("structuredContent", "structured_content"):
            try:
                structured = getattr(payload, attribute, None)
            except Exception:
                return None, None
            if structured is not None:
                candidate = structured
                structured_present = True
                break

        if structured_present:
            if not isinstance(candidate, Mapping):
                return None, None
        else:
            content = getattr(payload, "content", None)
            if type(content) is not list or len(content) != 1:
                return None, None
            block = content[0]
            if not isinstance(block, types.TextContent):
                return None, None
            text = getattr(block, "text", None)
            if type(text) is not str or len(text) > _MAX_SAFE_RESULT_BYTES:
                return None, None
            encoded = text.encode("utf-8", errors="strict")
            if len(encoded) > _MAX_SAFE_RESULT_BYTES:
                return None, None
            candidate = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_json_object)
            if not isinstance(candidate, Mapping):
                return None, None

        if "result" in candidate:
            if len(candidate) != 1:
                return None, None
            nested = candidate.get("result")
            if not isinstance(nested, Mapping):
                return None, None
            candidate = nested

        success = candidate.get("success")
        if type(success) is not bool:
            return None, None
        peers = candidate.get("peers")
        if type(peers) is not list:
            return (success, None) if success is False else (None, None)
        if len(peers) > _MAX_SAFE_PEER_COUNT:
            return success, None
        return success, len(peers)
    except Exception:
        # Result bodies and parser errors must never escape into observer data.
        return None, None


class _TaskBinding(NamedTuple):
    task_id: str
    requester_terminal_id: str
    state: str


class _SafeTaskResult(NamedTuple):
    success: bool | None = None
    id_matches: bool | None = None
    requester_matches: bool | None = None
    state: str | None = None
    state_matches: bool | None = None
    result_state: str | None = None
    result_state_matches: bool | None = None


_SAFE_TASK_RESULT_FIELDS = frozenset(
    {
        "success",
        "id_matches",
        "requester_matches",
        "state",
        "state_matches",
        "result_state",
        "result_state_matches",
    }
)


def _reject_json_constant(_value: str) -> Any:
    raise ValueError("observer_invalid_json_constant")


def _load_task_binding(path: Path, nonce: str, requester_id: str) -> _TaskBinding | None:
    """Read one issued metadata file; no waiting, payloads or exception text."""
    try:
        if not _private_directory(path.parent):
            return None
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size > _MAX_TASK_BINDING_BYTES
            ):
                return None
            raw = os.read(descriptor, _MAX_TASK_BINDING_BYTES + 1)
            after = os.fstat(descriptor)
            if len(raw) != before.st_size or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                return None
        finally:
            os.close(descriptor)
        document = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        if (
            type(document) is not dict
            or set(document) != {"nonce", "task_id", "requester_terminal_id", "state"}
            or document.get("nonce") != nonce
            or type(document.get("task_id")) is not str
            or _TASK_UUID_RE.fullmatch(document["task_id"]) is None
            or type(document.get("requester_terminal_id")) is not str
            or document["requester_terminal_id"] != requester_id
            or document.get("state") != "succeeded"
        ):
            return None
        return _TaskBinding(document["task_id"], requester_id, "succeeded")
    except Exception:
        return None


def _project_task_document(candidate: Any, binding: _TaskBinding) -> _SafeTaskResult | None:
    if not isinstance(candidate, Mapping):
        return None
    # A task DTO itself contains `result` beside its identity/state fields.
    # Only a sole outer result key is an SDK wrapper.
    if len(candidate) == 1 and "result" in candidate:
        candidate = candidate["result"]
        if not isinstance(candidate, Mapping):
            return None
    success = candidate.get("success")
    if type(success) is not bool:
        return None
    if success is False:
        return _SafeTaskResult(success=False)
    task_id = candidate.get("task_id")
    requester_id = candidate.get("requester_terminal_id")
    state = candidate.get("state")
    nested = candidate.get("result")
    nested_state = nested.get("state") if isinstance(nested, Mapping) else None
    return _SafeTaskResult(
        success=True,
        id_matches=(
            task_id == binding.task_id
            if type(task_id) is str and _TASK_UUID_RE.fullmatch(task_id)
            else None
        ),
        requester_matches=(
            requester_id == binding.requester_terminal_id
            if type(requester_id) is str and _TASK_REQUESTER_RE.fullmatch(requester_id)
            else None
        ),
        state=state if type(state) is str and state in _TASK_STATES else None,
        state_matches=state == binding.state if type(state) is str else None,
        result_state=(
            nested_state if type(nested_state) is str and nested_state in _TASK_STATES else None
        ),
        result_state_matches=(nested_state == binding.state if type(nested_state) is str else None),
    )


def _safe_local_task_result(payload: Any, binding: _TaskBinding | None) -> _SafeTaskResult:
    """Project only protected fields, rejecting conflicting structured/text evidence."""
    empty = _SafeTaskResult()
    if binding is None:
        return empty
    try:
        if not isinstance(payload, types.CallToolResult) or payload.isError is not False:
            return empty
        projections = []
        for attribute in ("structuredContent", "structured_content"):
            candidate = getattr(payload, attribute, None)
            if candidate is not None:
                projection = _project_task_document(candidate, binding)
                if projection is None:
                    return empty
                projections.append(projection)
        content = payload.content
        if type(content) is not list:
            return empty
        if content:
            if len(content) != 1 or not isinstance(content[0], types.TextContent):
                return empty
            text = content[0].text
            if type(text) is not str or len(text) > _MAX_SAFE_RESULT_BYTES:
                return empty
            raw = text.encode("utf-8", "strict")
            if len(raw) > _MAX_SAFE_RESULT_BYTES:
                return empty
            document = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
            projection = _project_task_document(document, binding)
            if projection is None:
                return empty
            projections.append(projection)
        if not projections or any(item != projections[0] for item in projections[1:]):
            return empty
        return projections[0]
    except Exception:
        return empty


def _empty_summary(status: str = "unobserved") -> dict[str, Any]:
    return {
        "observer_status": status,
        "mcp_catalog_observation_status": "unobserved",
        "mcp_catalog_tool_names": [],
        "mcp_catalog_unexpected_tool_count": 0,
        "mcp_catalog_observation_count": 0,
        "mcp_tool_observation_status": "unobserved",
        "mcp_tool_unexpected_call_count": 0,
        "observed_interaction_tools": [],
        "mcp_tool_calls": [],
    }


def _private_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        not stat.S_ISLNK(metadata.st_mode)
        and stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) == 0o700
    )


class ProtocolObserver:
    """Append only fixed MCP event fields to a fresh, private run sidecar."""

    def __init__(
        self,
        path: Path,
        nonce: str,
        *,
        expected_message_bodies: tuple[str, ...] = (),
        expected_safe_result_tool: str | None = None,
        expected_task_binding_path: Path | None = None,
        expected_task_requester_id: str | None = None,
    ) -> None:
        if not path.is_absolute() or not _NONCE_RE.fullmatch(nonce):
            raise ValueError("mcp_observer_binding_invalid")
        if expected_safe_result_tool is not None and (
            type(expected_safe_result_tool) is not str
            or expected_safe_result_tool not in _SAFE_RESULT_TOOLS
        ):
            raise ValueError("mcp_observer_safe_result_tool_invalid")
        if expected_safe_result_tool == _SAFE_TASK_RESULT_TOOL:
            if (
                not isinstance(expected_task_binding_path, Path)
                or not expected_task_binding_path.is_absolute()
                or expected_task_binding_path.parent != path.parent
                or type(expected_task_requester_id) is not str
                or _TASK_REQUESTER_RE.fullmatch(expected_task_requester_id) is None
            ):
                raise ValueError("mcp_observer_task_binding_invalid")
        elif expected_task_binding_path is not None or expected_task_requester_id is not None:
            raise ValueError("mcp_observer_task_binding_invalid")
        if (
            type(expected_message_bodies) is not tuple
            or len(expected_message_bodies) not in {0, 2}
            or any(
                type(body) is not str or len(body) > _MAX_MESSAGE_ARGUMENT_CHARACTERS
                for body in expected_message_bodies
            )
        ):
            raise ValueError("mcp_observer_message_probe_invalid")
        completion_path = path.with_name(path.name + _COMPLETE_SUFFIX)
        pending_path = path.with_name(path.name + _COMPLETE_PENDING_SUFFIX)
        if (
            not _private_directory(path.parent)
            or path.exists()
            or path.is_symlink()
            or completion_path.exists()
            or completion_path.is_symlink()
            or pending_path.exists()
            or pending_path.is_symlink()
        ):
            raise ValueError("mcp_observer_path_invalid")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ValueError("mcp_observer_file_invalid")
        except BaseException:
            os.close(descriptor)
            raise
        self._path = path
        self._nonce = nonce
        self._descriptor = descriptor
        self._sequence = 0
        self._call_id = 0
        self._message_ordinal = 0
        self._expected_message_bodies = expected_message_bodies
        self._expected_safe_result_tool = expected_safe_result_tool
        self._expected_task_binding_path = expected_task_binding_path
        self._expected_task_requester_id = expected_task_requester_id
        self._task_binding_loaded = False
        self._task_binding: _TaskBinding | None = None
        self._task_probes: dict[int, tuple[_TaskBinding | None, bool | None]] = {}
        self._written_bytes = 0
        self._closed = False
        self._write_failed = False
        self._limit_written = False
        self._lock = threading.Lock()
        self._append("observer_ready")

    def _append(self, event: str, **fields: Any) -> None:
        with self._lock:
            if self._closed or self._write_failed:
                return
            if self._sequence >= _MAX_EVENTS - 1:
                if not self._limit_written:
                    self._limit_written = True
                    self._write_row("observation_limit_reached", {})
                return
            self._write_row(event, fields)

    def _write_row(self, event: str, fields: dict[str, Any]) -> None:
        self._sequence += 1
        row = {
            "version": 1,
            "nonce": self._nonce,
            "sequence": self._sequence,
            "event": event,
            **fields,
        }
        try:
            encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            if self._written_bytes + len(encoded) > _MAX_BYTES:
                self._write_failed = True
                return
            view = memoryview(encoded)
            while view:
                written = os.write(self._descriptor, view)
                if written <= 0:
                    raise OSError("short_mcp_observer_write")
                view = view[written:]
            self._written_bytes += len(encoded)
        except OSError:
            self._write_failed = True

    def record_catalog(self, result: Any) -> None:
        payload = getattr(result, "root", None)
        tools = getattr(payload, "tools", None)
        if not isinstance(payload, types.ListToolsResult) or not isinstance(tools, list):
            self._append("catalog_unreadable")
            return
        names: set[str] = set()
        unexpected = 0
        for item in tools:
            name = getattr(item, "name", None)
            if isinstance(name, str) and name in INTERACTION_TOOLS:
                names.add(name)
            else:
                unexpected += 1
        self._append(
            "tools_list",
            names=sorted(names),
            unexpected_count=unexpected,
            tool_count=len(tools),
        )

    def start_call(self, name: Any, arguments: Any = None) -> tuple[int, str]:
        with self._lock:
            self._call_id += 1
            call_id = self._call_id
            ordinal = None
            if self._expected_message_bodies and name == "send_message":
                self._message_ordinal += 1
                ordinal = self._message_ordinal
        if (
            self._expected_safe_result_tool is not None
            and type(name) is str
            and name == self._expected_safe_result_tool
        ):
            tool = self._expected_safe_result_tool
        else:
            tool = name if isinstance(name, str) and name in INTERACTION_TOOLS else "other"
        self._append("tool_call_started", call_id=call_id, tool=tool)
        if ordinal is not None:
            self._record_message_argument(call_id, ordinal, arguments)
        if (
            self._expected_safe_result_tool == _SAFE_TASK_RESULT_TOOL
            and tool == _SAFE_TASK_RESULT_TOOL
        ):
            self._record_task_argument(call_id, arguments)
        return call_id, tool

    def _record_task_argument(self, call_id: int, arguments: Any) -> None:
        with self._lock:
            if self._closed:
                return
            load_binding = not self._task_binding_loaded
            self._task_binding_loaded = True
            binding = self._task_binding
        if load_binding:
            path = self._expected_task_binding_path
            requester_id = self._expected_task_requester_id
            binding = (
                _load_task_binding(path, self._nonce, requester_id)
                if path is not None and requester_id is not None
                else None
            )
            with self._lock:
                if self._closed:
                    return
                self._task_binding = binding
        matches = (
            type(arguments) is dict
            and len(arguments) == 1
            and type(arguments.get("task_id")) is str
            and arguments["task_id"] == binding.task_id
            if binding is not None
            else None
        )
        with self._lock:
            # Evidence is capped even if callers continue after the ledger's
            # event limit. Finish/result paths release each retained entry.
            if not self._closed and call_id <= _MAX_EVENTS:
                self._task_probes[call_id] = (binding, matches)

    def _record_message_argument(self, call_id: int, ordinal: int, arguments: Any) -> None:
        if not isinstance(arguments, Mapping) or "message" not in arguments:
            self._append(
                "message_argument_observed",
                call_id=call_id,
                ordinal=ordinal,
                status="missing",
                exact=False,
                utf8_length=None,
            )
            return
        message = arguments["message"]
        if type(message) is not str:
            status = "invalid_type"
            exact = False
            utf8_length = None
        elif len(message) > _MAX_MESSAGE_ARGUMENT_CHARACTERS:
            status = "oversized"
            exact = False
            utf8_length = None
        else:
            try:
                utf8_length = len(message.encode("utf-8", errors="strict"))
            except UnicodeError:
                status = "invalid_utf8"
                exact = False
                utf8_length = None
            else:
                if utf8_length > _MAX_MESSAGE_ARGUMENT_UTF8_BYTES:
                    status = "oversized"
                    exact = False
                    utf8_length = None
                elif ordinal > len(self._expected_message_bodies):
                    status = "extra_call"
                    exact = False
                else:
                    status = "compared"
                    exact = message == self._expected_message_bodies[ordinal - 1]
        self._append(
            "message_argument_observed",
            call_id=call_id,
            ordinal=ordinal,
            status=status,
            exact=exact,
            utf8_length=utf8_length,
        )

    def finish_call(
        self,
        call_id: int,
        *,
        status: str,
        error_present: bool,
        result_present: bool,
    ) -> None:
        with self._lock:
            self._task_probes.pop(call_id, None)
        self._append(
            "tool_call_finished",
            call_id=call_id,
            status=status,
            error_present=error_present,
            result_present=result_present,
        )

    def record_safe_result(self, call_id: int, payload: Any) -> None:
        if self._expected_safe_result_tool is None:
            return
        if self._expected_safe_result_tool == _SAFE_TASK_RESULT_TOOL:
            with self._lock:
                binding, matches = self._task_probes.pop(call_id, (None, None))
            projection = _safe_local_task_result(payload, binding)
            self._append(
                "safe_task_result_observed",
                call_id=call_id,
                binding_observed=binding is not None,
                argument_matches=matches,
                **{key: getattr(projection, key) for key in sorted(_SAFE_TASK_RESULT_FIELDS)},
            )
            return
        success, peer_count = _safe_local_peer_result(payload)
        self._append(
            "safe_result_observed",
            call_id=call_id,
            success=success,
            peer_count=peer_count,
        )

    def close(self, *, complete: bool = True) -> None:
        with self._lock:
            self._task_probes.clear()
            self._task_binding = None
            if self._closed:
                return
            if not self._limit_written and not self._write_failed:
                self._write_row("observer_stopped", {"writer_incomplete": not complete})
            try:
                os.fsync(self._descriptor)
            except OSError:
                self._write_failed = True
            try:
                os.close(self._descriptor)
            except OSError:
                self._write_failed = True
            self._closed = True
            if complete and not self._write_failed and not self._limit_written:
                if not self._write_completion_marker():
                    self._write_failed = True

    def _write_completion_marker(self) -> bool:
        """Publish a positive close marker only after the trace and marker flush."""
        parent_fd = -1
        marker_fd = -1
        pending_created = False
        published = False
        success = False
        marker_name = self._path.name + _COMPLETE_SUFFIX
        pending_name = self._path.name + _COMPLETE_PENDING_SUFFIX
        try:
            parent_fd = os.open(
                self._path.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
            parent_metadata = os.fstat(parent_fd)
            if (
                not stat.S_ISDIR(parent_metadata.st_mode)
                or parent_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(parent_metadata.st_mode) != 0o700
            ):
                raise OSError("mcp_observer_parent_invalid")
            marker_fd = os.open(
                pending_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=parent_fd,
            )
            pending_created = True
            os.fchmod(marker_fd, 0o600)
            marker_metadata = os.fstat(marker_fd)
            if (
                not stat.S_ISREG(marker_metadata.st_mode)
                or marker_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(marker_metadata.st_mode) != 0o600
            ):
                raise OSError("mcp_observer_marker_invalid")
            view = memoryview(_COMPLETE_CONTENT)
            while view:
                written = os.write(marker_fd, view)
                if written <= 0:
                    raise OSError("mcp_observer_marker_short_write")
                view = view[written:]
            os.fsync(marker_fd)
            os.close(marker_fd)
            marker_fd = -1
            os.rename(
                pending_name,
                marker_name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            pending_created = False
            published = True
            os.fsync(parent_fd)
            success = True
        except OSError:
            success = False
        finally:
            if marker_fd >= 0:
                try:
                    os.close(marker_fd)
                except OSError:
                    success = False
            if parent_fd >= 0:
                try:
                    os.close(parent_fd)
                except OSError:
                    success = False
        if not success and (pending_created or published):
            path = self._path.with_name(marker_name if published else pending_name)
            try:
                path.unlink()
            except OSError:
                pass
        return success


def install_protocol_observer(
    server: Any,
    path: Path,
    nonce: str,
    *,
    expected_message_bodies: tuple[str, ...] = (),
    expected_safe_result_tool: str | None = None,
    expected_task_binding_path: Path | None = None,
    expected_task_requester_id: str | None = None,
) -> ProtocolObserver:
    """Wrap the real low-level request dispatchers without changing their results."""
    list_handler = server.request_handlers.get(types.ListToolsRequest)
    call_handler = server.request_handlers.get(types.CallToolRequest)
    if list_handler is None or call_handler is None:
        raise RuntimeError("mcp_observer_handlers_unavailable")
    observer = ProtocolObserver(
        path,
        nonce,
        expected_message_bodies=expected_message_bodies,
        expected_safe_result_tool=expected_safe_result_tool,
        expected_task_binding_path=expected_task_binding_path,
        expected_task_requester_id=expected_task_requester_id,
    )

    async def observed_list(request: Any) -> Any:
        result = await list_handler(request)
        if not _IN_TOOL_CALL.get():
            try:
                observer.record_catalog(result)
            except Exception:
                pass
        return result

    async def observed_call(request: Any) -> Any:
        params = getattr(request, "params", None)
        tool = None
        try:
            name = getattr(params, "name", None)
            if observer._expected_message_bodies or (
                observer._expected_safe_result_tool == _SAFE_TASK_RESULT_TOOL
                and name == _SAFE_TASK_RESULT_TOOL
            ):
                call_id, tool = observer.start_call(
                    name,
                    getattr(params, "arguments", None),
                )
            else:
                call_id, tool = observer.start_call(name)
        except Exception:
            call_id = None
        token = _IN_TOOL_CALL.set(True)
        try:
            result = await call_handler(request)
        except BaseException:
            if call_id is not None:
                try:
                    if (
                        observer._expected_safe_result_tool is not None
                        and tool == observer._expected_safe_result_tool
                    ):
                        observer.record_safe_result(call_id, None)
                    observer.finish_call(
                        call_id,
                        status="failed",
                        error_present=True,
                        result_present=False,
                    )
                except Exception:
                    pass
            raise
        finally:
            _IN_TOOL_CALL.reset(token)
        payload = getattr(result, "root", None)
        if isinstance(payload, types.CallToolResult) and isinstance(payload.isError, bool):
            failed = payload.isError
            status = "failed" if failed else "completed"
            error_present = failed
        else:
            status = "unknown"
            error_present = False
        if call_id is not None:
            try:
                if (
                    observer._expected_safe_result_tool is not None
                    and tool == observer._expected_safe_result_tool
                ):
                    observer.record_safe_result(call_id, payload)
                observer.finish_call(
                    call_id,
                    status=status,
                    error_present=error_present,
                    result_present=payload is not None,
                )
            except Exception:
                pass
        return result

    server.request_handlers[types.ListToolsRequest] = observed_list
    server.request_handlers[types.CallToolRequest] = observed_call
    return observer


def install_protocol_observer_from_env(
    server: Any, environ: Mapping[str, str] | None = None
) -> ProtocolObserver:
    """Install only for a paired, explicit AG-UI MCP-child opt-in."""
    selected_environment = os.environ if environ is None else environ
    if not isinstance(selected_environment, Mapping):
        raise RuntimeError("mcp_observer_environment_invalid")
    try:
        enabled = selected_environment.get(OBSERVER_ENABLE_ENV)
        raw_path = selected_environment.get(OBSERVER_PATH_ENV)
        nonce = selected_environment.get(OBSERVER_NONCE_ENV)
        raw_safe_result_tool = selected_environment.get(_SAFE_RESULT_TOOL_ENV)
        raw_task_binding_path = selected_environment.get(_SAFE_TASK_BINDING_ENV)
        raw_task_requester_id = (
            selected_environment.get("CAO_TERMINAL_ID")
            if raw_safe_result_tool == _SAFE_TASK_RESULT_TOOL
            else None
        )
    except Exception:
        raise RuntimeError("mcp_observer_environment_invalid") from None
    if type(enabled) is not str or enabled != "1":
        raise RuntimeError("mcp_observer_not_enabled")
    if type(raw_path) is not str or not raw_path or type(nonce) is not str:
        raise RuntimeError("mcp_observer_binding_incomplete")
    if raw_safe_result_tool is not None and (
        type(raw_safe_result_tool) is not str or raw_safe_result_tool not in _SAFE_RESULT_TOOLS
    ):
        raise RuntimeError("mcp_observer_safe_result_tool_invalid")
    task_binding_path = None
    if raw_safe_result_tool == _SAFE_TASK_RESULT_TOOL:
        if (
            type(raw_task_binding_path) is not str
            or not raw_task_binding_path
            or type(raw_task_requester_id) is not str
            or _TASK_REQUESTER_RE.fullmatch(raw_task_requester_id) is None
        ):
            raise RuntimeError("mcp_observer_task_binding_invalid")
        task_binding_path = Path(raw_task_binding_path)
    elif raw_task_binding_path is not None:
        raise RuntimeError("mcp_observer_task_binding_invalid")
    low_level_server = getattr(server, "_mcp_server", None)
    if low_level_server is None:
        raise RuntimeError("mcp_observer_server_unavailable")
    return install_protocol_observer(
        low_level_server,
        Path(raw_path),
        nonce,
        expected_safe_result_tool=raw_safe_result_tool,
        expected_task_binding_path=task_binding_path,
        expected_task_requester_id=raw_task_requester_id,
    )


def _completion_marker_valid(path: Path) -> bool:
    marker_path = path.with_name(path.name + _COMPLETE_SUFFIX)
    try:
        before = marker_path.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != len(_COMPLETE_CONTENT)
        ):
            return False
        descriptor = os.open(
            marker_path,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size != len(_COMPLETE_CONTENT)
            ):
                return False
            content = os.read(descriptor, len(_COMPLETE_CONTENT) + 1)
            after = os.fstat(descriptor)
            return content == _COMPLETE_CONTENT and (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
            ) == (after.st_dev, after.st_ino, after.st_size)
        finally:
            os.close(descriptor)
    except OSError:
        return False


def read_protocol_observations(
    path: Path,
    *,
    expected_nonce: str,
    expected_message_bodies: tuple[str, ...] = (),
    expected_safe_result_tool: str | None = None,
) -> dict[str, Any]:
    """Read only a bounded, private sidecar and return closed protocol facts."""
    if expected_safe_result_tool is not None and (
        type(expected_safe_result_tool) is not str
        or expected_safe_result_tool not in _SAFE_RESULT_TOOLS
    ):
        return _empty_summary("incomplete")
    if (
        type(expected_message_bodies) is not tuple
        or len(expected_message_bodies) not in {0, 2}
        or any(
            type(body) is not str or len(body) > _MAX_MESSAGE_ARGUMENT_CHARACTERS
            for body in expected_message_bodies
        )
    ):
        return _empty_summary("incomplete")
    if not _NONCE_RE.fullmatch(expected_nonce) or not _private_directory(path.parent):
        return _empty_summary()
    try:
        before = path.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size > _MAX_BYTES
        ):
            return _empty_summary()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size > _MAX_BYTES
            ):
                return _empty_summary()
            chunks: list[bytes] = []
            remaining = _MAX_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 8192))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
        finally:
            os.close(descriptor)
    except OSError:
        return _empty_summary()
    if not content or len(content) > _MAX_BYTES:
        return _empty_summary()

    rows: list[dict[str, Any]] = []
    incomplete = not content.endswith(b"\n")
    for raw_line in content.splitlines(keepends=True):
        if not raw_line.endswith(b"\n"):
            break
        try:
            row = json.loads(raw_line, object_pairs_hook=_unique_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJSONKeyError):
            incomplete = True
            break
        if not isinstance(row, dict) or row.get("nonce") != expected_nonce:
            return _empty_summary()
        if (
            type(row.get("version")) is not int
            or row["version"] != 1
            or type(row.get("sequence")) is not int
            or row["sequence"] != len(rows) + 1
            or not isinstance(row.get("event"), str)
        ):
            incomplete = True
            break
        event = row["event"]
        event_keys = {
            "observer_ready": {"version", "nonce", "sequence", "event"},
            "tools_list": {
                "version",
                "nonce",
                "sequence",
                "event",
                "names",
                "unexpected_count",
                "tool_count",
            },
            "catalog_unreadable": {"version", "nonce", "sequence", "event"},
            "tool_call_started": {
                "version",
                "nonce",
                "sequence",
                "event",
                "call_id",
                "tool",
            },
            "message_argument_observed": {
                "version",
                "nonce",
                "sequence",
                "event",
                "call_id",
                "ordinal",
                "status",
                "exact",
                "utf8_length",
            },
            "tool_call_finished": {
                "version",
                "nonce",
                "sequence",
                "event",
                "call_id",
                "status",
                "error_present",
                "result_present",
            },
            "safe_result_observed": (
                {
                    "version",
                    "nonce",
                    "sequence",
                    "event",
                    "call_id",
                    "success",
                    "peer_count",
                }
                if expected_safe_result_tool == _SAFE_RESULT_TOOL
                else None
            ),
            "safe_task_result_observed": (
                {
                    "version",
                    "nonce",
                    "sequence",
                    "event",
                    "call_id",
                    "binding_observed",
                    "argument_matches",
                }
                | _SAFE_TASK_RESULT_FIELDS
                if expected_safe_result_tool == _SAFE_TASK_RESULT_TOOL
                else None
            ),
            "observer_stopped": {
                "version",
                "nonce",
                "sequence",
                "event",
                "writer_incomplete",
            },
            "observation_limit_reached": {"version", "nonce", "sequence", "event"},
        }.get(event)
        if event_keys is None or set(row) != event_keys or len(rows) >= _MAX_EVENTS:
            incomplete = True
            break
        rows.append(row)
    if not rows or rows[0].get("event") != "observer_ready":
        return _empty_summary()

    catalogs: list[dict[str, Any]] = []
    calls: dict[int, dict[str, Any]] = {}
    call_order: list[int] = []
    safe_result_observations: dict[int, tuple[bool | None, int | None]] = {}
    safe_task_observations: dict[int, dict[str, Any]] = {}
    allowed_call_tools = INTERACTION_TOOLS | {"other"}
    if expected_safe_result_tool is not None:
        allowed_call_tools |= {expected_safe_result_tool}
    message_probe_invalid = False
    message_argument_observations: list[dict[str, Any]] = []
    unexpected_calls = 0
    stopped = False
    for row in rows[1:]:
        if stopped:
            incomplete = True
            break
        event = row.get("event")
        if event == "tools_list":
            names = row.get("names")
            unexpected = row.get("unexpected_count")
            tool_count = row.get("tool_count")
            if (
                not isinstance(names, list)
                or any(not isinstance(name, str) or name not in INTERACTION_TOOLS for name in names)
                or names != sorted(set(names))
                or type(unexpected) is not int
                or not 0 <= unexpected <= 10_000
                or type(tool_count) is not int
                or not 0 <= tool_count <= 10_000
            ):
                incomplete = True
                break
            catalogs.append(row)
        elif event == "catalog_unreadable":
            incomplete = True
        elif event == "tool_call_started":
            call_id = row.get("call_id")
            tool = row.get("tool")
            if (
                type(call_id) is not int
                or call_id <= 0
                or call_id in calls
                or not isinstance(tool, str)
                or tool not in allowed_call_tools
            ):
                incomplete = True
                break
            calls[call_id] = {
                "server": "cao-mcp-server",
                "tool": tool,
                "status": "in_progress",
                "error_present": False,
                "result_present": False,
            }
            call_order.append(call_id)
            if tool == "other":
                unexpected_calls += 1
        elif event == "message_argument_observed":
            call_id = row.get("call_id")
            call = calls.get(call_id) if type(call_id) is int else None
            ordinal = row.get("ordinal")
            status = row.get("status")
            exact = row.get("exact")
            utf8_length = row.get("utf8_length")
            valid_length = (
                type(utf8_length) is int and 0 <= utf8_length <= _MAX_MESSAGE_ARGUMENT_UTF8_BYTES
            )
            valid_status = (
                (
                    status == "compared"
                    and type(exact) is bool
                    and valid_length
                    and type(ordinal) is int
                    and 1 <= ordinal <= len(expected_message_bodies)
                )
                or (
                    status == "extra_call"
                    and exact is False
                    and valid_length
                    and type(ordinal) is int
                    and ordinal > len(expected_message_bodies)
                )
                or (
                    type(status) is str
                    and status in {"missing", "invalid_type", "oversized", "invalid_utf8"}
                    and exact is False
                    and utf8_length is None
                    and type(ordinal) is int
                    and ordinal > 0
                )
            )
            if (
                not expected_message_bodies
                or call is None
                or call["status"] != "in_progress"
                or call["tool"] != "send_message"
                or ordinal != len(message_argument_observations) + 1
                or any(item["call_id"] == call_id for item in message_argument_observations)
                or not valid_status
            ):
                incomplete = True
                message_probe_invalid = True
                break
            message_argument_observations.append(
                {
                    "call_id": call_id,
                    "ordinal": ordinal,
                    "status": status,
                    "exact": exact,
                    "utf8_length": utf8_length,
                }
            )
        elif event == "safe_result_observed":
            call_id = row.get("call_id")
            call = calls.get(call_id) if type(call_id) is int else None
            success = row.get("success")
            peer_count = row.get("peer_count")
            if (
                type(call_id) is not int
                or expected_safe_result_tool is None
                or call is None
                or call["tool"] != expected_safe_result_tool
                or call["status"] != "in_progress"
                or call_id in safe_result_observations
                or (success is not None and type(success) is not bool)
                or (success is None and peer_count is not None)
                or (
                    peer_count is not None
                    and (type(peer_count) is not int or not 0 <= peer_count <= _MAX_SAFE_PEER_COUNT)
                )
            ):
                incomplete = True
                break
            safe_result_observations[call_id] = (success, peer_count)
        elif event == "safe_task_result_observed":
            call_id = row.get("call_id")
            call = calls.get(call_id) if type(call_id) is int else None
            binding_observed = row.get("binding_observed")
            argument_matches = row.get("argument_matches")
            success = row.get("success")
            boolean_fields = (
                "id_matches",
                "requester_matches",
                "state_matches",
                "result_state_matches",
            )
            if (
                type(call_id) is not int
                or expected_safe_result_tool != _SAFE_TASK_RESULT_TOOL
                or call is None
                or call["tool"] != _SAFE_TASK_RESULT_TOOL
                or call["status"] != "in_progress"
                or call_id in safe_task_observations
                or type(binding_observed) is not bool
                or (argument_matches is not None and type(argument_matches) is not bool)
                or (success is not None and type(success) is not bool)
                or any(
                    row.get(key) is not None and type(row[key]) is not bool
                    for key in boolean_fields
                )
                or any(
                    row.get(key) is not None
                    and (type(row[key]) is not str or row[key] not in _TASK_STATES)
                    for key in ("state", "result_state")
                )
                or (
                    binding_observed is False
                    and (
                        argument_matches is not None
                        or any(row.get(key) is not None for key in _SAFE_TASK_RESULT_FIELDS)
                    )
                )
                or (binding_observed is True and type(argument_matches) is not bool)
                or (
                    success is not True
                    and any(
                        row.get(key) is not None for key in _SAFE_TASK_RESULT_FIELDS - {"success"}
                    )
                )
                or any(
                    row.get(match_key) is True
                    and row.get(state_key) != "succeeded"
                    or row.get(state_key) is not None
                    and row.get(match_key) is not (row[state_key] == "succeeded")
                    for state_key, match_key in (
                        ("state", "state_matches"),
                        ("result_state", "result_state_matches"),
                    )
                )
            ):
                incomplete = True
                break
            safe_task_observations[call_id] = {
                key: row[key]
                for key in sorted(
                    _SAFE_TASK_RESULT_FIELDS | {"binding_observed", "argument_matches"}
                )
            }
        elif event == "tool_call_finished":
            call_id = row.get("call_id")
            call = calls.get(call_id) if type(call_id) is int else None
            status = row.get("status")
            error_present = row.get("error_present")
            result_present = row.get("result_present")
            if (
                type(call_id) is not int
                or call is None
                or call["status"] != "in_progress"
                or not isinstance(status, str)
                or status not in {"completed", "failed", "unknown"}
                or not isinstance(error_present, bool)
                or not isinstance(result_present, bool)
            ):
                incomplete = True
                break
            if (
                expected_safe_result_tool is not None
                and call["tool"] == expected_safe_result_tool
                and call_id
                not in (
                    safe_task_observations
                    if expected_safe_result_tool == _SAFE_TASK_RESULT_TOOL
                    else safe_result_observations
                )
            ):
                incomplete = True
                break
            if (
                expected_message_bodies
                and call["tool"] == "send_message"
                and not any(item["call_id"] == call_id for item in message_argument_observations)
            ):
                incomplete = True
                message_probe_invalid = True
                break
            call.update(
                {
                    "status": status,
                    "error_present": error_present,
                    "result_present": result_present,
                }
            )
            if expected_safe_result_tool == _SAFE_RESULT_TOOL and call["tool"] == _SAFE_RESULT_TOOL:
                success, peer_count = safe_result_observations[call_id]
                call.update(
                    {
                        "safe_result_success": success,
                        "safe_result_peer_count": peer_count,
                    }
                )
            elif (
                expected_safe_result_tool == _SAFE_TASK_RESULT_TOOL
                and call["tool"] == _SAFE_TASK_RESULT_TOOL
            ):
                call.update(
                    {
                        (
                            "safe_result_success" if key == "success" else "safe_result_task_" + key
                        ): value
                        for key, value in safe_task_observations[call_id].items()
                    }
                )
        elif event == "observer_stopped":
            if not isinstance(row.get("writer_incomplete"), bool):
                incomplete = True
                break
            stopped = True
            if row["writer_incomplete"]:
                incomplete = True
        elif event == "observation_limit_reached":
            incomplete = True
        else:
            incomplete = True
            break

    ordered_calls = [calls[call_id] for call_id in call_order]
    if not stopped or any(call["status"] == "in_progress" for call in ordered_calls):
        incomplete = True
    catalog_names = sorted({name for catalog in catalogs for name in catalog["names"]})
    observed_tools = sorted(
        {call["tool"] for call in ordered_calls if call["tool"] in INTERACTION_TOOLS}
    )
    summary = _empty_summary()
    completion_observed = _completion_marker_valid(path)
    if not completion_observed:
        incomplete = True
    summary.update(
        {
            "observer_status": "incomplete" if incomplete else "observed",
            "mcp_catalog_observation_status": ("observed" if catalogs else "unobserved"),
            "mcp_catalog_tool_names": catalog_names,
            "mcp_catalog_unexpected_tool_count": sum(
                catalog["unexpected_count"] for catalog in catalogs
            ),
            "mcp_catalog_observation_count": len(catalogs),
            "mcp_tool_observation_status": (
                "incomplete"
                if incomplete and ordered_calls
                else "observed" if ordered_calls else "unobserved"
            ),
            "mcp_tool_unexpected_call_count": unexpected_calls,
            "observed_interaction_tools": observed_tools,
            "mcp_tool_calls": ordered_calls,
            "mcp_observer_shutdown_observed": stopped and completion_observed,
        }
    )
    if expected_message_bodies:
        if incomplete:
            message_probe_invalid = True
        accepted_observations = [] if message_probe_invalid else message_argument_observations
        exported_observations = [
            {
                "ordinal": item["ordinal"],
                "status": item["status"],
                "exact": item["exact"],
                "utf8_length": item["utf8_length"],
            }
            for item in accepted_observations
        ]
        summary.update(
            {
                "send_message_argument_observation_status": (
                    "invalid"
                    if message_probe_invalid
                    else "observed_prefix" if accepted_observations else "unobserved"
                ),
                "send_message_argument_observations": exported_observations,
                "send_message_argument_two_phase_exact": (
                    len(accepted_observations) == len(expected_message_bodies)
                    and all(
                        item["status"] == "compared" and item["exact"] is True
                        for item in accepted_observations
                    )
                ),
            }
        )
    return summary
