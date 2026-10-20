"""Private, offline-testable wrapper for the bundled FastMCP call_tool seam.

The live acceptance installs this wrapper in its source-bound MCP child before
the original FastMCP run method. This module never starts a server, provider,
process, network client, or database.
"""

from __future__ import annotations

import inspect
import os
import stat
import threading
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

OBSERVER_ENABLE_ENV = "CAO_SPEC023_MCP_OBSERVER"
OBSERVER_AUDIT_PATH_ENV = "CAO_SPEC023_MCP_AUDIT_PATH"

_ALLOWED_TOOL = "send_message"
_MISSING = object()
_ACTIVE_OBSERVER_SERVER: ContextVar[int | None] = ContextVar(
    "spec023_active_observer_server", default=None
)

_AUDIT_SEND_COMPLETED = {
    "success": b'{"tool":"send_message","status":"completed","outcome":"success"}\n',
    "failure": b'{"tool":"send_message","status":"completed","outcome":"failure"}\n',
    "unknown": b'{"tool":"send_message","status":"completed","outcome":"unknown"}\n',
}
_AUDIT_SEND_FAILED = b'{"tool":"send_message","status":"failed","outcome":"unknown"}\n'
_AUDIT_OTHER_REJECTED = b'{"tool":"other","status":"rejected","outcome":"unknown"}\n'


class ObserverSetupError(RuntimeError):
    """A fixed-enum setup refusal that does not echo private paths or values."""


def _safe_result_outcome(result: Any) -> str:
    """Classify only the explicit boolean success field; never serialize result data."""
    candidate: Any = result if isinstance(result, Mapping) else None
    if candidate is None:
        for attribute in ("structured_content", "structuredContent"):
            try:
                structured = getattr(result, attribute, None)
            except Exception:
                return "unknown"
            if isinstance(structured, Mapping):
                candidate = structured
                break
    if candidate is None:
        return "unknown"
    try:
        success = candidate.get("success")
    except Exception:
        return "unknown"
    if type(success) is bool:
        return "success" if success else "failure"
    return "unknown"


def _open_private_audit_file(audit_path: str | os.PathLike[str]) -> int:
    """Create one owner-only audit file through no-follow directory descriptors."""
    try:
        raw_path = os.fspath(audit_path)
    except Exception:
        raise ObserverSetupError("observer_audit_path_unsafe") from None

    if type(raw_path) is not str or not raw_path.startswith("/"):
        raise ObserverSetupError("observer_audit_path_unsafe")
    components = raw_path.split("/")[1:]
    if not components or any(part in {"", ".", ".."} for part in components):
        raise ObserverSetupError("observer_audit_path_unsafe")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ObserverSetupError("observer_audit_path_unsafe")

    directory_fd: int | None = None
    audit_fd: int | None = None
    leaf = components[-1]
    try:
        directory_fd = os.open(
            "/",
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
        )
        for component in components[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd

        parent_stat = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != os.geteuid()
            or stat.S_IMODE(parent_stat.st_mode) != 0o700
        ):
            raise ObserverSetupError("observer_audit_path_unsafe")

        audit_fd = os.open(
            leaf,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_APPEND
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(audit_fd, 0o600)

        audit_stat = os.fstat(audit_fd)
        parent_stat = os.fstat(directory_fd)
        if (
            not stat.S_ISREG(audit_stat.st_mode)
            or audit_stat.st_uid != os.geteuid()
            or stat.S_IMODE(audit_stat.st_mode) != 0o600
            or parent_stat.st_uid != os.geteuid()
            or stat.S_IMODE(parent_stat.st_mode) != 0o700
        ):
            raise ObserverSetupError("observer_audit_path_unsafe")

        returned_fd = audit_fd
        audit_fd = None
        return returned_fd
    except ObserverSetupError:
        raise
    except (OSError, TypeError, ValueError):
        raise ObserverSetupError("observer_audit_path_unsafe") from None
    finally:
        if audit_fd is not None:
            try:
                os.close(audit_fd)
            except OSError:
                pass
        if directory_fd is not None:
            try:
                os.close(directory_fd)
            except OSError:
                pass


class _FixedAuditWriter:
    """Serialize only constant records; never accepts caller-provided values."""

    def __init__(self, file_descriptor: int) -> None:
        self._fd = file_descriptor
        self._lock = threading.Lock()
        self._closed = False
        self.write_failed = False

    def send_completed(self, outcome: str) -> None:
        self._write(_AUDIT_SEND_COMPLETED.get(outcome, _AUDIT_SEND_COMPLETED["unknown"]))

    def send_failed(self) -> None:
        self._write(_AUDIT_SEND_FAILED)

    def other_rejected(self) -> None:
        self._write(_AUDIT_OTHER_REJECTED)

    def _write(self, record: bytes) -> None:
        with self._lock:
            if self._closed:
                self.write_failed = True
                return
            view = memoryview(record)
            try:
                while view:
                    count = os.write(self._fd, view)
                    if count <= 0:
                        self.write_failed = True
                        return
                    view = view[count:]
            except OSError:
                # Do not replace a production result/error with an audit failure.
                # The missing record makes the acceptance fail closed afterward.
                self.write_failed = True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                os.close(self._fd)
            except OSError:
                self.write_failed = True


class ObserverHandle:
    """Own the audit descriptor and restore the captured dispatch on close."""

    def __init__(self, server: Any, original: Any, wrapped: Any, writer: _FixedAuditWriter) -> None:
        self._server = server
        self._original = original
        self._wrapped = wrapped
        self._writer = writer
        self._closed = False

    @property
    def audit_write_failed(self) -> bool:
        """Whether any fixed record could not be persisted."""
        return self._writer.write_failed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if getattr(self._server, "call_tool", None) is self._wrapped:
                setattr(self._server, "call_tool", self._original)
        finally:
            self._writer.close()


def install_message_observer(server: Any, audit_path: str | os.PathLike[str]) -> ObserverHandle:
    """Wrap the server's public async dispatcher and delegate allowed calls once."""
    try:
        original = getattr(server, "call_tool", None)
    except Exception:
        raise ObserverSetupError("observer_server_invalid") from None
    if not callable(original) or not inspect.iscoroutinefunction(original):
        raise ObserverSetupError("observer_server_invalid")
    if getattr(original, "__spec023_message_observer__", False):
        raise ObserverSetupError("observer_already_installed")

    file_descriptor = _open_private_audit_file(audit_path)
    writer = _FixedAuditWriter(file_descriptor)

    async def observed_call_tool(*args: Any, **kwargs: Any) -> Any:
        if args and "name" not in kwargs:
            tool_name = args[0]
        elif not args:
            tool_name = kwargs.get("name", _MISSING)
        else:
            tool_name = _MISSING

        if type(tool_name) is not str or tool_name != _ALLOWED_TOOL:
            writer.other_rejected()
            raise PermissionError("tool_not_allowed")

        server_identity = id(server)
        if (
            _ACTIVE_OBSERVER_SERVER.get() == server_identity
            and kwargs.get("run_middleware", _MISSING) is False
        ):
            # FastMCP's middleware continuation re-enters self.call_tool with
            # run_middleware=False. The pinned MCP request handler constructs
            # this keyword itself; it does not forward client-supplied kwargs.
            # Keep the continuation's semantics while auditing the external
            # dispatch exactly once. Ordinary reentry still receives its own
            # fixed audit record.
            return await original(*args, **kwargs)

        token = _ACTIVE_OBSERVER_SERVER.set(server_identity)
        try:
            try:
                result = await original(*args, **kwargs)
            except BaseException:
                writer.send_failed()
                raise
            writer.send_completed(_safe_result_outcome(result))
            return result
        finally:
            _ACTIVE_OBSERVER_SERVER.reset(token)

    observed_call_tool.__spec023_message_observer__ = True
    try:
        setattr(server, "call_tool", observed_call_tool)
    except Exception:
        writer.close()
        raise ObserverSetupError("observer_server_invalid") from None

    return ObserverHandle(server, original, observed_call_tool, writer)


def install_message_observer_from_env(
    server: Any, environ: Mapping[str, str] | None = None
) -> ObserverHandle:
    """Install only for an explicit private MCP-child opt-in, failing closed."""
    selected_environment = os.environ if environ is None else environ
    if not isinstance(selected_environment, Mapping):
        raise ObserverSetupError("observer_environment_invalid")

    try:
        enabled = selected_environment.get(OBSERVER_ENABLE_ENV)
        audit_path = selected_environment.get(OBSERVER_AUDIT_PATH_ENV)
    except Exception:
        raise ObserverSetupError("observer_environment_invalid") from None

    if enabled is None or (type(enabled) is str and enabled == "0"):
        raise ObserverSetupError("observer_not_enabled")
    if type(enabled) is not str or enabled != "1":
        raise ObserverSetupError("observer_environment_invalid")

    if type(audit_path) is not str or not audit_path:
        raise ObserverSetupError("observer_audit_path_required")
    return install_message_observer(server, audit_path)
