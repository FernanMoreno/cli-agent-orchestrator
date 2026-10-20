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
from typing import Any

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
_COMPLETE_SUFFIX = ".complete"
_COMPLETE_PENDING_SUFFIX = ".complete.pending"
_COMPLETE_CONTENT = b"CAO_OPEN_CODE_MCP_OBSERVER_COMPLETE\n"
OBSERVER_ENABLE_ENV = "CAO_OPENCODE_MCP_OBSERVER"
OBSERVER_PATH_ENV = "CAO_OPENCODE_MCP_OBSERVER_PATH"
OBSERVER_NONCE_ENV = "CAO_OPENCODE_MCP_OBSERVER_NONCE"
_IN_TOOL_CALL: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "cao_opencode_mcp_internal_tool_call", default=False
)


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

    def __init__(self, path: Path, nonce: str) -> None:
        if not path.is_absolute() or not _NONCE_RE.fullmatch(nonce):
            raise ValueError("mcp_observer_binding_invalid")
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

    def start_call(self, name: Any) -> tuple[int, str]:
        with self._lock:
            self._call_id += 1
            call_id = self._call_id
        tool = name if isinstance(name, str) and name in INTERACTION_TOOLS else "other"
        self._append("tool_call_started", call_id=call_id, tool=tool)
        return call_id, tool

    def finish_call(
        self,
        call_id: int,
        *,
        status: str,
        error_present: bool,
        result_present: bool,
    ) -> None:
        self._append(
            "tool_call_finished",
            call_id=call_id,
            status=status,
            error_present=error_present,
            result_present=result_present,
        )

    def close(self, *, complete: bool = True) -> None:
        with self._lock:
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


def install_protocol_observer(server: Any, path: Path, nonce: str) -> ProtocolObserver:
    """Wrap the real low-level request dispatchers without changing their results."""
    list_handler = server.request_handlers.get(types.ListToolsRequest)
    call_handler = server.request_handlers.get(types.CallToolRequest)
    if list_handler is None or call_handler is None:
        raise RuntimeError("mcp_observer_handlers_unavailable")
    observer = ProtocolObserver(path, nonce)

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
        try:
            call_id, _tool = observer.start_call(getattr(params, "name", None))
        except Exception:
            call_id = None
        token = _IN_TOOL_CALL.set(True)
        try:
            result = await call_handler(request)
        except BaseException:
            if call_id is not None:
                try:
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
    except Exception:
        raise RuntimeError("mcp_observer_environment_invalid") from None
    if type(enabled) is not str or enabled != "1":
        raise RuntimeError("mcp_observer_not_enabled")
    if type(raw_path) is not str or not raw_path or type(nonce) is not str:
        raise RuntimeError("mcp_observer_binding_incomplete")
    low_level_server = getattr(server, "_mcp_server", None)
    if low_level_server is None:
        raise RuntimeError("mcp_observer_server_unavailable")
    return install_protocol_observer(low_level_server, Path(raw_path), nonce)


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


def read_protocol_observations(path: Path, *, expected_nonce: str) -> dict[str, Any]:
    """Read only a bounded, private sidecar and return closed protocol facts."""
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
            row = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            incomplete = True
            break
        if not isinstance(row, dict) or row.get("nonce") != expected_nonce:
            return _empty_summary()
        if (
            row.get("version") != 1
            or row.get("sequence") != len(rows) + 1
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
                or tool not in INTERACTION_TOOLS | {"other"}
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
        elif event == "tool_call_finished":
            call_id = row.get("call_id")
            call = calls.get(call_id) if type(call_id) is int else None
            status = row.get("status")
            error_present = row.get("error_present")
            result_present = row.get("result_present")
            if (
                call is None
                or call["status"] != "in_progress"
                or not isinstance(status, str)
                or status not in {"completed", "failed", "unknown"}
                or not isinstance(error_present, bool)
                or not isinstance(result_present, bool)
            ):
                incomplete = True
                break
            call.update(
                {
                    "status": status,
                    "error_present": error_present,
                    "result_present": result_present,
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
    return summary
