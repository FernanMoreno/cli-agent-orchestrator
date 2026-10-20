"""Explicit source-bound FastMCP dispatch hooks for private acceptance lanes.

This module is placed first on PYTHONPATH only in selected MCP children. Python
ignores ordinary exceptions raised by ``sitecustomize`` during startup, so
unsafe enabled configurations terminate with ``SystemExit`` before a server
can start without its requested observer.
"""

from __future__ import annotations

import functools
import os
import stat
import sys
from pathlib import Path
from typing import Any

_OBSERVER_ENABLE_ENV = "CAO_SPEC023_MCP_OBSERVER"
_SOURCE_ROOT_ENV = "CAO_SPEC023_SOURCE_ROOT"
_OPENCODE_OBSERVER_ENABLE_ENV = "CAO_OPENCODE_MCP_OBSERVER"
_OPENCODE_SOURCE_ROOT_ENV = "CAO_OPENCODE_MCP_SOURCE_ROOT"
_EXPECTED_MODULE = "cli_agent_orchestrator.mcp_server.server"
_AUDIT_FAILURE_EXIT_CODE = 86
_AUDIT_FAILURE_MARKER = b"SPEC023_AUDIT_WRITE_FAILED\n"
_AUDIT_COMPLETE_MARKER = b"SPEC023_AUDIT_COMPLETE\n"


def _terminate_startup() -> None:
    raise SystemExit("spec023_observer_startup_rejected")


def _write_audit_failure_marker() -> None:
    audit_path = os.environ.get("CAO_SPEC023_MCP_AUDIT_PATH")
    if not audit_path or not os.path.isabs(audit_path):
        raise SystemExit(_AUDIT_FAILURE_EXIT_CODE)
    marker_path = audit_path + ".failed"
    descriptor = -1
    try:
        parent = os.path.dirname(marker_path)
        parent_stat = os.stat(parent, follow_symlinks=False)
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != os.geteuid()
            or stat.S_IMODE(parent_stat.st_mode) != 0o700
        ):
            raise OSError
        descriptor = os.open(
            marker_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        os.write(descriptor, _AUDIT_FAILURE_MARKER)
        os.fsync(descriptor)
    except BaseException:
        raise SystemExit(_AUDIT_FAILURE_EXIT_CODE) from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_audit_completion_marker() -> None:
    audit_path = os.environ.get("CAO_SPEC023_MCP_AUDIT_PATH")
    if not audit_path or not os.path.isabs(audit_path):
        raise SystemExit(_AUDIT_FAILURE_EXIT_CODE)
    parent_fd = -1
    descriptor = -1
    try:
        parent = os.path.dirname(audit_path)
        parent_fd = os.open(
            parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        parent_stat = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != os.geteuid()
            or stat.S_IMODE(parent_stat.st_mode) != 0o700
        ):
            raise OSError
        descriptor = os.open(
            os.path.basename(audit_path) + ".complete",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent_fd,
        )
        os.fchmod(descriptor, 0o600)
        marker_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(marker_stat.st_mode)
            or marker_stat.st_uid != os.geteuid()
            or stat.S_IMODE(marker_stat.st_mode) != 0o600
        ):
            raise OSError
        view = memoryview(_AUDIT_COMPLETE_MARKER)
        while view:
            count = os.write(descriptor, view)
            if count <= 0:
                raise OSError
            view = view[count:]
        os.fsync(descriptor)
        os.fsync(parent_fd)
    except BaseException:
        raise SystemExit(_AUDIT_FAILURE_EXIT_CODE) from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if parent_fd >= 0:
            try:
                os.close(parent_fd)
            except OSError:
                pass


def _finish_source_bound_run(result: Any, observer: Any) -> Any:
    observer.close()
    if observer.audit_write_failed:
        _write_audit_failure_marker()
        raise SystemExit(_AUDIT_FAILURE_EXIT_CODE)
    _write_audit_completion_marker()
    return result


def _install_source_bound_run_hook() -> None:
    message_gate = os.environ.get(_OBSERVER_ENABLE_ENV, "0")
    opencode_gate = os.environ.get(_OPENCODE_OBSERVER_ENABLE_ENV, "0")
    if message_gate == "0" and opencode_gate == "0":
        return
    if (
        message_gate not in {"0", "1"}
        or opencode_gate not in {"0", "1"}
        or (message_gate == "1" and opencode_gate == "1")
    ):
        _terminate_startup()

    if message_gate == "1":
        observer_mode = "message"
        raw_root = os.environ.get(_SOURCE_ROOT_ENV)
        try:
            from message_mcp_observer import install_message_observer_from_env

            observer_installer = install_message_observer_from_env
        except BaseException:
            _terminate_startup()
    else:
        observer_mode = "opencode"
        raw_root = os.environ.get(_OPENCODE_SOURCE_ROOT_ENV)
        try:
            from opencode_mcp_observer import install_protocol_observer_from_env

            observer_installer = install_protocol_observer_from_env
        except BaseException:
            _terminate_startup()
    if not raw_root:
        _terminate_startup()
    try:
        source_root = Path(raw_root).resolve(strict=True)
        from fastmcp import FastMCP
    except BaseException:
        _terminate_startup()

    original_run = FastMCP.run
    run_seen = False

    @functools.wraps(original_run)
    def source_bound_run(server: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal run_seen
        if run_seen:
            _terminate_startup()
        run_seen = True
        module = sys.modules.get("__main__")
        spec = getattr(module, "__spec__", None)
        try:
            module_file = Path(getattr(module, "__file__")).resolve(strict=True)
            expected_server_file = source_root / "src/cli_agent_orchestrator/mcp_server/server.py"
            source_file_matches = module_file.samefile(expected_server_file)
        except (OSError, TypeError, ValueError):
            _terminate_startup()
        if (
            getattr(spec, "name", None) != _EXPECTED_MODULE
            or getattr(module, "mcp", None) is not server
            or not source_file_matches
        ):
            _terminate_startup()

        try:
            observer = observer_installer(server)
        except BaseException:
            _terminate_startup()
        try:
            result = original_run(server, *args, **kwargs)
        except BaseException:
            try:
                if observer_mode == "opencode":
                    observer.close(complete=False)
                else:
                    observer.close()
            except BaseException:
                pass
            raise
        else:
            if observer_mode == "message":
                return _finish_source_bound_run(result, observer)
            try:
                observer.close()
            except BaseException:
                _terminate_startup()
            return result

    FastMCP.run = source_bound_run


_install_source_bound_run_hook()


# The restart lane needs one private rendezvous after the real create response.
# Keep the R13 source-bound observer hook above intact for existing MCP evidence.
_RESTART_BARRIER_ENV = "CAO_MCP_RESTART_BARRIER_DIR"


def _install_mcp_response_barrier() -> None:
    raw_directory = os.environ.get(_RESTART_BARRIER_ENV)
    if raw_directory is None:
        return
    if not raw_directory or not os.path.isabs(raw_directory):
        raise SystemExit("mcp_restart_barrier_startup_rejected")
    try:
        from interaction_opencode_mcp_gate import install_create_response_barrier

        from cli_agent_orchestrator.mcp_server import utils as mcp_utils

        install_create_response_barrier(mcp_utils, Path(raw_directory))
    except BaseException:
        raise SystemExit("mcp_restart_barrier_startup_rejected") from None


_install_mcp_response_barrier()
del _install_mcp_response_barrier
