#!/usr/bin/env python3
"""Run one explicitly configured acceptance command under bounded ownership.

This portable outer controller has no host-specific paths or credential
roots. A host-bound caller supplies the runtime/source/auth pins in a required
ControllerConfig. The controller never removes a run directory and reports
incomplete cleanup if its deadline or ownership proof is unresolved.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

TOTAL_WALL_SECONDS = 900.0
RUN_STOP_OFFSET_SECONDS = 880.0
INT_GRACE_SECONDS = 5.0
TERM_GRACE_SECONDS = 5.0
KILL_GRACE_SECONDS = 5.0
MAX_STDOUT_BYTES = 8192
MAX_TASK_INVENTORY_ATTEMPTS = 3
MAX_OWNED_TREE_SCAN_ATTEMPTS = 3
MAX_STOP_RECONCILIATION_SCANS = 3
SAFE_LAUNCHER_RESULTS = frozenset(
    {
        "completed_and_acknowledged",
        "model_run_not_approved",
        "explicit_model_required",
        "provider_auth_unavailable",
        "api_start_failed",
        "capability_install_failed",
        "capability_path_missing",
        "mcp_stdio_handshake",
        "mcp_stdio_handshake_timeout",
        "mcp_stdio_handshake_error",
        "unsupported_cli_version",
        "mcp_config_missing",
        "mcp_config_invalid",
        "mcp_server_configuration_invalid",
        "unclassified_mcp_error_pending_inspection",
        "private_error_diagnostic_capture_failed",
        "mcp_tool_call_failed",
        "mcp_tool_returned_without_question",
        "timed_out_after_question",
        "timed_out_before_question",
        "question_created_but_not_acknowledged",
        "cli_exited_without_ack",
        "unexpected_question_shape",
        "unexpected_question_contract",
        "unexpected_question_count",
        "unexpected_question_keys",
        "unexpected_question_kind",
        "question_not_required",
        "invalid_choice_question_kind",
        "fixed_choice_not_offered",
        "cli_unavailable",
        "harness_error",
        "source_precheck_failed",
        "launcher_preflight_failed",
    }
)


class ControllerError(RuntimeError):
    """A fixed controller failure code with optional fixed diagnostic metadata."""

    def __init__(self, message: str, *, inventory_branch: str | None = None) -> None:
        super().__init__(message)
        self.inventory_branch = inventory_branch


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    ppid: int
    pgrp: int
    session: int
    starttime: int
    uid: int
    state: str


@dataclass(frozen=True)
class GroupIdentity:
    pgid: int
    anchor_pid: int
    anchor_starttime: int
    uid: int
    session: int


@dataclass(frozen=True)
class ControllerConfig:
    """Required, host-supplied runtime and static-source bindings.

    Wall/run-stop budgets are included to make the caller's requested contract
    explicit and are rejected unless they match the reviewed fixed values.
    """

    launcher_root: Path
    runner: Path
    runner_sha256: str
    manifest: Path
    manifest_sha256: str
    python_executable: Path
    python_version: tuple[int, int, int]
    site_shim: Path
    site_shim_sha256: str
    private_profile_home: Path
    receipt_root: Path
    controller_module: Path
    controller_module_sha256: str
    total_wall_seconds: float
    run_stop_offset_seconds: float

    def __post_init__(self) -> None:
        path_values = (
            self.launcher_root,
            self.runner,
            self.manifest,
            self.python_executable,
            self.site_shim,
            self.private_profile_home,
            self.receipt_root,
            self.controller_module,
        )
        if any(not isinstance(value, Path) or not value.is_absolute() for value in path_values):
            raise ControllerError("controller_config_invalid")
        digest_values = (
            self.runner_sha256,
            self.manifest_sha256,
            self.site_shim_sha256,
            self.controller_module_sha256,
        )
        if any(
            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in digest_values
        ):
            raise ControllerError("controller_config_invalid")
        if (
            not isinstance(self.python_version, tuple)
            or len(self.python_version) != 3
            or any(type(value) is not int or value < 1 for value in self.python_version)
            or type(self.total_wall_seconds) not in {float, int}
            or type(self.run_stop_offset_seconds) not in {float, int}
            or self.total_wall_seconds != TOTAL_WALL_SECONDS
            or self.run_stop_offset_seconds != RUN_STOP_OFFSET_SECONDS
        ):
            raise ControllerError("controller_config_invalid")

    @classmethod
    def from_mapping(cls, value: object) -> "ControllerConfig":
        fields = {
            "launcher_root",
            "runner",
            "runner_sha256",
            "manifest",
            "manifest_sha256",
            "python_executable",
            "python_version",
            "site_shim",
            "site_shim_sha256",
            "private_profile_home",
            "receipt_root",
            "controller_module",
            "controller_module_sha256",
            "total_wall_seconds",
            "run_stop_offset_seconds",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ControllerError("controller_config_invalid")
        payload = dict(value)
        for name in (
            "launcher_root",
            "runner",
            "manifest",
            "python_executable",
            "site_shim",
            "private_profile_home",
            "receipt_root",
            "controller_module",
        ):
            if not isinstance(payload[name], str):
                raise ControllerError("controller_config_invalid")
            payload[name] = Path(payload[name])
        version = payload["python_version"]
        if not isinstance(version, list) or len(version) != 3:
            raise ControllerError("controller_config_invalid")
        payload["python_version"] = tuple(version)
        try:
            return cls(**payload)
        except TypeError as error:
            raise ControllerError("controller_config_invalid") from error


_INVENTORY_FAILURE_CODES = frozenset(
    {
        "process_stat_invalid",
        "process_uid_invalid",
        "process_identity_unreadable",
        "process_identity_invalid",
        "process_children_unreadable",
        "process_task_inventory_invalid",
        "process_task_inventory_raced",
        "process_children_invalid",
        "process_task_inventory_empty",
    }
)
_INVENTORY_REASON_CODES = frozenset(
    {
        "controller_children_unavailable",
        "unverified_subreaper_child",
        "postcleanup_subreaper_child",
        "postcleanup_controller_identity_unverified",
        "known_pid_starttime_changed",
        "known_pid_identity_changed",
        "owned_pid_reconciliation_budget_exhausted",
        "process_uid_mismatch",
        "process_started_before_launcher",
        "launcher_identity_mismatch",
    }
)

_INVENTORY_BRANCH_CODES = frozenset(
    {
        "identity_missing_at_inventory_start",
        "identity_changed_at_inventory_start",
        "identity_missing_before_snapshot",
        "identity_changed_before_snapshot",
        "identity_missing_after_snapshot",
        "identity_changed_after_snapshot",
        "identity_missing_after_tid_enoent",
        "identity_changed_after_tid_enoent",
        "identity_missing_after_task_root_enoent",
        "identity_changed_after_task_root_enoent",
        "identity_missing_after_tid_esrch",
        "identity_changed_after_tid_esrch",
        "identity_missing_after_task_root_esrch",
        "identity_changed_after_task_root_esrch",
        "identity_missing_during_owned_scan",
        "controller_identity_missing_before_scan",
        "controller_identity_missing_before_retry",
        "controller_identity_changed_before_retry",
        "owned_tree_retry_budget_exhausted",
        "tid_children_enoent_retry_exhausted",
        "tid_children_esrch_retry_exhausted",
        "tid_set_changed_retry_exhausted",
        "proc_census_retry_exhausted",
        "inventory_deadline_exceeded",
        "inventory_retry_budget_exhausted",
    }
)

_OWNED_PROCESS_EXIT_BRANCHES = frozenset(
    {
        "identity_missing_at_inventory_start",
        "identity_missing_before_snapshot",
        "identity_missing_after_snapshot",
        "identity_missing_after_tid_enoent",
        "identity_missing_after_task_root_enoent",
        "identity_missing_after_tid_esrch",
        "identity_missing_after_task_root_esrch",
        "identity_missing_during_owned_scan",
    }
)
_TID_INVENTORY_CHURN_BRANCHES = frozenset(
    {
        "tid_set_changed_retry_exhausted",
        "tid_children_enoent_retry_exhausted",
        "tid_children_esrch_retry_exhausted",
    }
)


def _task_inventory_race(branch: str) -> ControllerError:
    """Build a fixed race error without exposing procfs values or text."""
    safe_branch = (
        branch if branch in _INVENTORY_BRANCH_CODES else "inventory_retry_budget_exhausted"
    )
    return ControllerError("process_task_inventory_raced", inventory_branch=safe_branch)


_INVENTORY_FRAME_LABELS = {
    "_scan_owned_tree": "owned_tree_scan",
    "_read_identity": "identity_read",
    "_read_children": "children_read",
    "_read_proc_parent_census": "process_census",
    "_read_children_from_process_census": "process_census",
    "_postcleanup_subreaper_children": "owned_tree_cleanup",
    "_group_has_verified_member": "group_identity_check",
    "_stop_owned_tree": "owned_tree_cleanup",
}


_INVENTORY_ERRNO_CODES = {
    errno.ESRCH: "ESRCH",
    errno.ENOENT: "ENOENT",
    errno.EACCES: "EACCES",
    errno.EPERM: "EPERM",
    errno.EIO: "EIO",
}


def _inventory_exception_diagnostic(error: BaseException) -> dict[str, Any]:
    """Reduce a process-inventory exception to fixed, non-sensitive facts."""
    failure_code = (
        str(error)
        if isinstance(error, ControllerError) and str(error) in _INVENTORY_FAILURE_CODES
        else "process_inventory_unclassified"
    )
    cause = error.__cause__
    if cause is None and not isinstance(error, ControllerError):
        cause = error
    if isinstance(cause, UnicodeDecodeError):
        cause_class = "unicode_decode_error"
    elif isinstance(cause, FileNotFoundError):
        cause_class = "file_not_found"
    elif isinstance(cause, PermissionError):
        cause_class = "permission_error"
    elif isinstance(cause, ValueError):
        cause_class = "invalid_proc_field"
    elif isinstance(cause, (IndexError, StopIteration)):
        cause_class = "missing_proc_field"
    elif isinstance(cause, OSError):
        cause_class = "os_error"
    elif cause is None:
        cause_class = "none"
    else:
        cause_class = "other_error"

    frames: list[str] = []
    traceback = error.__traceback__
    while traceback is not None:
        label = _INVENTORY_FRAME_LABELS.get(traceback.tb_frame.f_code.co_name)
        if label is not None and label not in frames:
            frames.append(label)
        traceback = traceback.tb_next
    diagnostic = {
        "failure_scope": "exception",
        "failure_code": failure_code,
        "cause_class": cause_class,
        "frames": frames,
    }
    if isinstance(cause, OSError):
        diagnostic["errno_code"] = _INVENTORY_ERRNO_CODES.get(cause.errno, "other")
    branch = getattr(error, "inventory_branch", None)
    if failure_code == "process_task_inventory_raced" and branch in _INVENTORY_BRANCH_CODES:
        diagnostic["inventory_branch"] = branch
    return diagnostic


def _record_inventory_unreadable(diagnostic_sink: dict[str, Any] | None, reason: str) -> None:
    """Store the first fixed guard reason without changing scan behavior."""
    if diagnostic_sink is None or diagnostic_sink:
        return
    safe_reason = reason if reason in _INVENTORY_REASON_CODES else "unknown_owned_tree_guard"
    diagnostic_sink.update(
        {
            "failure_scope": "unreadable_flag",
            "failure_code": "owned_tree_unreadable",
            "cause_class": "none",
            "reason": safe_reason,
            "frames": ["owned_tree_scan"],
        }
    )


def _record_inventory_exception(
    diagnostic_sink: dict[str, Any] | None, error: BaseException
) -> None:
    """Keep the first classified inventory exception, excluding its text."""
    if diagnostic_sink is None or diagnostic_sink:
        return
    diagnostic = _inventory_exception_diagnostic(error)
    if diagnostic["failure_code"] == "process_inventory_unclassified" and not diagnostic["frames"]:
        return
    diagnostic_sink.update(diagnostic)


def _lstat_owned(path: Path, *, mode: int | None = None, directory: bool = False) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as error:
        raise ControllerError("owned_path_unavailable") from error
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (
        not expected
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
    ):
        raise ControllerError("owned_path_invalid")
    return info


def _sha256_file(path: Path, *, mode: int = 0o600) -> str:
    before = _lstat_owned(path, mode=mode)
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ControllerError("pinned_file_changed")
        while True:
            block = os.read(descriptor, 64 * 1024)
            if not block:
                break
            digest.update(block)
    finally:
        os.close(descriptor)
    after = _lstat_owned(path, mode=mode)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ControllerError("pinned_file_changed")
    return digest.hexdigest()


def _verify_static_pins(config: ControllerConfig) -> dict[str, str]:
    if (
        sys.version_info[:3] != config.python_version
        or Path(sys.executable).resolve() != config.python_executable.resolve()
    ):
        raise ControllerError("pinned_python_required")
    if _sha256_file(config.runner) != config.runner_sha256:
        raise ControllerError("runner_pin_mismatch")
    if _sha256_file(config.manifest) != config.manifest_sha256:
        raise ControllerError("source_manifest_pin_mismatch")
    if _sha256_file(config.site_shim) != config.site_shim_sha256:
        raise ControllerError("site_shim_pin_mismatch")
    if _sha256_file(config.controller_module) != config.controller_module_sha256:
        raise ControllerError("controller_module_pin_mismatch")
    try:
        loaded_module = Path(__file__).resolve(strict=True)
        configured_module = config.controller_module.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ControllerError("controller_module_origin_unavailable") from error
    if loaded_module != configured_module:
        raise ControllerError("controller_module_origin_mismatch")
    profile_info = _lstat_owned(config.private_profile_home, mode=0o700, directory=True)
    return {
        "runner_sha256": config.runner_sha256,
        "source_manifest_sha256": config.manifest_sha256,
        "site_shim_sha256": config.site_shim_sha256,
        "private_profile_device": str(profile_info.st_dev),
        "private_profile_inode": str(profile_info.st_ino),
    }


def _launcher_environment(parent: dict[str, str], config: ControllerConfig) -> dict[str, str]:
    """Pass only runtime basics; provider/API credentials are never inherited."""
    home = parent.get("HOME")
    path = parent.get("PATH")
    if not home or not path:
        raise ControllerError("parent_environment_incomplete")
    environment = {
        "HOME": home,
        "PATH": path,
        "LANG": parent.get("LANG", "C.UTF-8"),
        "TERM": parent.get("TERM", "xterm-256color"),
        "PYTHONPATH": str(config.site_shim.parent),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "CAO_PRIVATE_PROFILE_HOME": str(config.private_profile_home),
    }
    if "CODEX_HOME" in parent:
        environment["CODEX_HOME"] = parent["CODEX_HOME"]
    sensitive = re.compile(
        r"(?:API_KEY|ACCESS_KEY|SECRET|TOKEN|PASSWORD|AUTH|JWKS|ISSUER)", re.IGNORECASE
    )
    if any(sensitive.search(name) for name in environment):
        raise ControllerError("unsafe_launch_environment")
    return environment


def _enable_child_subreaper() -> None:
    if not sys.platform.startswith("linux"):
        raise ControllerError("linux_subreaper_required")
    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_CHILD_SUBREAPER = 36: detached descendants are reparented here.
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise ControllerError("subreaper_unavailable")


def _proc_decimal(value: bytes) -> int:
    """Parse a required non-negative /proc integer using ASCII digits only."""
    if not value or any(byte < ord("0") or byte > ord("9") for byte in value):
        raise ValueError("invalid numeric proc field")
    return int(value)


def _read_identity(pid: int) -> ProcessIdentity | None:
    proc = Path("/proc") / str(pid)
    try:
        raw = (proc / "stat").read_bytes()
        close = raw.rfind(b")")
        if close < 0:
            raise ControllerError("process_stat_invalid")
        fields = raw[close + 2 :].split()
        uid_line = next(
            line for line in (proc / "status").read_bytes().splitlines() if line.startswith(b"Uid:")
        )
        uid_values = uid_line.split()
        if len(uid_values) != 5:
            raise ControllerError("process_uid_invalid")
        return ProcessIdentity(
            pid=pid,
            state=fields[0].decode("ascii"),
            ppid=_proc_decimal(fields[1]),
            pgrp=_proc_decimal(fields[2]),
            session=_proc_decimal(fields[3]),
            starttime=_proc_decimal(fields[19]),
            uid=_proc_decimal(uid_values[1]),
        )
    except FileNotFoundError:
        return None
    except ProcessLookupError as error:
        if error.errno == errno.ESRCH:
            return None
        raise ControllerError("process_identity_invalid") from error
    except PermissionError as error:
        raise ControllerError("process_identity_unreadable") from error
    except (OSError, StopIteration, IndexError, ValueError) as error:
        raise ControllerError("process_identity_invalid") from error


def _check_process_census_deadline(*, clock: Callable[[], float], deadline: float | None) -> None:
    if deadline is not None and clock() >= deadline:
        raise _task_inventory_race("inventory_deadline_exceeded")


def _read_proc_parent_pid(pid: int, *, proc_root: Path) -> int | None:
    """Read a process leader's PPID without opening unrelated status files."""
    try:
        raw = (proc_root / str(pid) / "stat").read_bytes()
        close = raw.rfind(b")")
        if close < 0:
            raise ControllerError("process_stat_invalid")
        fields = raw[close + 2 :].split()
        if len(fields) <= 1:
            raise ControllerError("process_stat_invalid")
        return _proc_decimal(fields[1])
    except FileNotFoundError:
        return None
    except ProcessLookupError as error:
        if error.errno == errno.ESRCH:
            return None
        raise ControllerError("process_identity_invalid") from error
    except PermissionError as error:
        raise ControllerError("process_identity_unreadable") from error
    except ControllerError:
        raise
    except (OSError, ValueError) as error:
        raise ControllerError("process_identity_invalid") from error


def _read_proc_parent_census(
    *,
    proc_root: Path = Path("/proc"),
    clock: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
) -> dict[int, int]:
    """Return numeric process-leader PPID edges from one bounded procfs pass."""
    _check_process_census_deadline(clock=clock, deadline=deadline)
    parents: dict[int, int] = {}
    try:
        process_dirs = proc_root.iterdir()
        for entry in process_dirs:
            _check_process_census_deadline(clock=clock, deadline=deadline)
            if not entry.name.isascii() or not entry.name.isdigit():
                continue
            pid = _proc_decimal(entry.name.encode("ascii"))
            parent_pid = _read_proc_parent_pid(pid, proc_root=proc_root)
            if parent_pid is not None:
                parents[pid] = parent_pid
    except PermissionError as error:
        raise ControllerError("process_identity_unreadable") from error
    except OSError as error:
        raise ControllerError("process_identity_invalid") from error
    return parents


def _read_children_from_process_census(
    pid: int,
    expected_identity: ProcessIdentity,
    *,
    read_identity: Callable[[int], ProcessIdentity | None],
    read_process_census: Callable[[], dict[int, int]] | None = None,
    clock: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
) -> set[int]:
    """Reconcile direct PPID edges, pinning the owner and each child read."""

    def same_pin(expected: ProcessIdentity, actual: ProcessIdentity | None) -> bool:
        return actual is not None and (
            actual.pid,
            actual.starttime,
            actual.uid,
            actual.pgrp,
            actual.session,
        ) == (
            expected.pid,
            expected.starttime,
            expected.uid,
            expected.pgrp,
            expected.session,
        )

    for _attempt in range(MAX_TASK_INVENTORY_ATTEMPTS):
        _check_process_census_deadline(clock=clock, deadline=deadline)
        before = read_identity(pid)
        if not same_pin(expected_identity, before):
            branch = (
                "identity_missing_before_snapshot"
                if before is None
                else "identity_changed_before_snapshot"
            )
            raise _task_inventory_race(branch)

        parents = (
            read_process_census()
            if read_process_census is not None
            else _read_proc_parent_census(clock=clock, deadline=deadline)
        )
        _check_process_census_deadline(clock=clock, deadline=deadline)
        if not isinstance(parents, dict) or any(
            not isinstance(child_pid, int)
            or not isinstance(parent_pid, int)
            or child_pid < 1
            or parent_pid < 0
            for child_pid, parent_pid in parents.items()
        ):
            raise ControllerError("process_task_inventory_invalid")

        # A live, pinned parent must itself have appeared in each /proc pass.
        # Otherwise this pass cannot establish that the child edge map was read
        # from a complete process-leader inventory.
        raced = pid not in parents
        observed: dict[int, ProcessIdentity] = {}
        for child_pid, parent_pid in parents.items():
            _check_process_census_deadline(clock=clock, deadline=deadline)
            if child_pid == pid or parent_pid != pid:
                continue
            child = read_identity(child_pid)
            if child is None or child.pid != child_pid or child.ppid != pid:
                raced = True
                continue
            observed[child_pid] = child

        after = read_identity(pid)
        if not same_pin(expected_identity, after):
            branch = (
                "identity_missing_after_snapshot"
                if after is None
                else "identity_changed_after_snapshot"
            )
            raise _task_inventory_race(branch)

        if not raced:
            for child_pid, child in observed.items():
                _check_process_census_deadline(clock=clock, deadline=deadline)
                current_child = read_identity(child_pid)
                if not same_pin(child, current_child) or current_child.ppid != pid:
                    raced = True
                    break

        if not raced:
            _check_process_census_deadline(clock=clock, deadline=deadline)
            return set(observed)

    raise _task_inventory_race("proc_census_retry_exhausted")


def _read_children(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
    read_identity: Callable[[int], ProcessIdentity | None] | None = None,
    expected_identity: ProcessIdentity | None = None,
    clock: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
) -> set[int] | None:
    """Read one stable task/children snapshot for an already-owned process.

    A TID can exit after ``task`` is enumerated but before its ``children``
    file is read. Retry only that procfs race, or a changed TID set, and only
    while the same process identity remains pinned. Partial child sets are
    discarded on every retry. An optional hard deadline is checked throughout
    directory enumeration, per-TID reads, and all bounded attempts.
    """
    read_identity = read_identity or _read_identity
    task_directory = proc_root / str(pid) / "task"

    def check_deadline() -> None:
        _check_process_census_deadline(clock=clock, deadline=deadline)

    def identity_matches(expected: ProcessIdentity, actual: ProcessIdentity | None) -> bool:
        # PPID and state can change while a process remains the same process.
        # PID/start-time is the kernel identity; UID/PGRP/SID pin ownership and
        # the process-group/session boundary used by cleanup.
        return actual is not None and (
            actual.pid,
            actual.starttime,
            actual.uid,
            actual.pgrp,
            actual.session,
        ) == (
            expected.pid,
            expected.starttime,
            expected.uid,
            expected.pgrp,
            expected.session,
        )

    pinned_identity = expected_identity

    def task_root_missing(error: OSError) -> None:
        # ENOENT and exact ESRCH both mean the proc task root could not be
        # resolved. Only a fresh check of the original pin can authorize the
        # caller to restart its complete owned-tree walk.
        esrch = error.errno == errno.ESRCH
        check_deadline()
        actual = read_identity(pid)
        if actual is None and pinned_identity is not None:
            branch = (
                "identity_missing_after_task_root_esrch"
                if esrch
                else "identity_missing_after_task_root_enoent"
            )
            raise _task_inventory_race(branch) from error
        if (
            actual is not None
            and pinned_identity is not None
            and not identity_matches(pinned_identity, actual)
        ):
            branch = (
                "identity_changed_after_task_root_esrch"
                if esrch
                else "identity_changed_after_task_root_enoent"
            )
            raise _task_inventory_race(branch) from error
        raise ControllerError("process_children_unreadable") from error

    def read_task_entries() -> list[Path]:
        try:
            entries: list[Path] = []
            for task in task_directory.iterdir():
                check_deadline()
                entries.append(task)
            tasks = sorted(entries, key=lambda item: item.name)
            check_deadline()
        except FileNotFoundError as error:
            # Confirm absence before allowing a fresh owned-tree walk.
            task_root_missing(error)
        except ProcessLookupError as error:
            if error.errno == errno.ESRCH:
                task_root_missing(error)
            raise ControllerError("process_children_unreadable") from error
        except PermissionError as error:
            raise ControllerError("process_children_unreadable") from error
        except OSError as error:
            raise ControllerError("process_children_unreadable") from error
        for task in tasks:
            if not task.name.isdigit():
                raise ControllerError("process_task_inventory_invalid")
        if not tasks:
            # An empty proc task listing is not enough to infer that a pinned
            # process exited. Recheck that exact identity before classifying
            # the race; identity-read failures and deadline expiry stay
            # fail-closed, while same-pin emptiness remains incomplete.
            check_deadline()
            after_identity = read_identity(pid)
            check_deadline()
            if not identity_matches(pinned_identity, after_identity):
                branch = (
                    "identity_missing_after_snapshot"
                    if after_identity is None
                    else "identity_changed_after_snapshot"
                )
                raise _task_inventory_race(branch)
            raise ControllerError("process_task_inventory_empty")
        return tasks

    check_deadline()
    first_identity = read_identity(pid)
    if first_identity is None:
        if pinned_identity is None:
            return None
        raise _task_inventory_race("identity_missing_at_inventory_start")
    if pinned_identity is None:
        pinned_identity = first_identity
    elif not identity_matches(pinned_identity, first_identity):
        raise _task_inventory_race("identity_changed_at_inventory_start")

    for attempt in range(MAX_TASK_INVENTORY_ATTEMPTS):
        # Check the same process identity immediately before every complete
        # task-set snapshot, including retries.
        check_deadline()
        before_identity = read_identity(pid)
        check_deadline()
        if not identity_matches(pinned_identity, before_identity):
            branch = (
                "identity_missing_before_snapshot"
                if before_identity is None
                else "identity_changed_before_snapshot"
            )
            raise _task_inventory_race(branch)
        tasks_before = read_task_entries()
        task_ids_before = {task.name for task in tasks_before}
        children: set[int] = set()
        raced_error: OSError | None = None
        for task in tasks_before:
            check_deadline()
            try:
                value = (task / "children").read_text(encoding="ascii")
                check_deadline()
            except FileNotFoundError as error:
                raced_error = error
                break
            except ProcessLookupError as error:
                if error.errno == errno.ESRCH:
                    raced_error = error
                    break
                raise ControllerError("process_children_unreadable") from error
            except PermissionError as error:
                raise ControllerError("process_children_unreadable") from error
            except OSError as error:
                raise ControllerError("process_children_unreadable") from error
            try:
                children.update(int(part) for part in value.split())
            except ValueError as error:
                raise ControllerError("process_children_invalid") from error

        # Validate the process pin and re-enumerate TIDs even after an ENOENT.
        # A vanished process or task root is an incomplete inventory, never an
        # inferred empty child set.
        tasks_after = read_task_entries()
        task_ids_after = {task.name for task in tasks_after}
        check_deadline()
        after_identity = read_identity(pid)
        check_deadline()
        if not identity_matches(pinned_identity, after_identity):
            if raced_error is not None:
                if raced_error.errno == errno.ESRCH:
                    branch = (
                        "identity_missing_after_tid_esrch"
                        if after_identity is None
                        else "identity_changed_after_tid_esrch"
                    )
                else:
                    branch = (
                        "identity_missing_after_tid_enoent"
                        if after_identity is None
                        else "identity_changed_after_tid_enoent"
                    )
                raise _task_inventory_race(branch) from raced_error
            branch = (
                "identity_missing_after_snapshot"
                if after_identity is None
                else "identity_changed_after_snapshot"
            )
            raise _task_inventory_race(branch)
        if raced_error is None and task_ids_before == task_ids_after:
            return children
        if attempt + 1 == MAX_TASK_INVENTORY_ATTEMPTS:
            if raced_error is not None:
                branch = (
                    "tid_children_esrch_retry_exhausted"
                    if raced_error.errno == errno.ESRCH
                    else "tid_children_enoent_retry_exhausted"
                )
                raise _task_inventory_race(branch) from raced_error
            raise _task_inventory_race("tid_set_changed_retry_exhausted")
        # Discard every child observed in the unstable attempt. The next loop
        # starts from a fresh process identity and complete task enumeration.
    raise _task_inventory_race("inventory_retry_budget_exhausted")


def _scan_owned_tree(
    *,
    controller_pid: int,
    launcher: ProcessIdentity,
    baseline_controller_children: set[int],
    known: dict[int, ProcessIdentity],
    read_identity: Callable[[int], ProcessIdentity | None] | None = None,
    read_children: Callable[[int], set[int] | None] | None = None,
    read_process_census: Callable[[], dict[int, int]] | None = None,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    diagnostic_sink: dict[str, Any] | None = None,
) -> tuple[dict[int, ProcessIdentity], dict[int, GroupIdentity], bool]:
    """Follow verified child edges, with a PPID census fallback for TID churn.

    TID-set churn uses a bounded PPID census while the process pin remains
    stable. A full walk restarts only when a fixed identity-missing branch
    confirms process exit. Each restart rebuilds the walk and controller's
    direct-child snapshot so adopted children remain discoverable; other
    incomplete inventories fail closed.
    """
    read_identity = read_identity or _read_identity
    read_children = read_children or _read_children
    owner_uid = os.geteuid()
    controller_pin = _read_identity(controller_pid)
    if (
        controller_pin is None
        or controller_pin.pid != controller_pid
        or controller_pin.uid != owner_uid
    ):
        raise _task_inventory_race("controller_identity_missing_before_scan")

    def same_pin(expected: ProcessIdentity, actual: ProcessIdentity | None) -> bool:
        return actual is not None and (
            actual.pid,
            actual.starttime,
            actual.uid,
            actual.pgrp,
            actual.session,
        ) == (
            expected.pid,
            expected.starttime,
            expected.uid,
            expected.pgrp,
            expected.session,
        )

    def require_controller_pin() -> None:
        current = _read_identity(controller_pid)
        if current is None:
            raise _task_inventory_race("controller_identity_missing_before_retry")
        if not same_pin(controller_pin, current):
            raise _task_inventory_race("controller_identity_changed_before_retry")

    # Absence confirmations are local to this scan call. A known launcher that
    # stays dead after the first fresh controller-child snapshot must not force
    # repeated full walks; an unpinned child that reappears is never adopted.
    confirmed_absences: dict[int, ProcessIdentity | None] = {}

    for scan_attempt in range(MAX_OWNED_TREE_SCAN_ATTEMPTS):
        attempt_unreadable = False
        retry_target_pid: int | None = None

        def mark_unreadable(reason: str) -> None:
            nonlocal attempt_unreadable
            attempt_unreadable = True
            _record_inventory_unreadable(diagnostic_sink, reason)

        def read_pinned_children(pid: int, identity: ProcessIdentity) -> set[int] | None:
            nonlocal retry_target_pid
            try:
                if read_children is _read_children:
                    return read_children(
                        pid,
                        read_identity=read_identity,
                        expected_identity=identity,
                        clock=clock,
                        deadline=deadline,
                    )
                # Preserve injectable one-argument callbacks while the
                # production reader receives the exact identity pinned above.
                return read_children(pid)
            except ControllerError as error:
                if getattr(error, "inventory_branch", None) in _TID_INVENTORY_CHURN_BRANCHES:
                    try:
                        return _read_children_from_process_census(
                            pid,
                            identity,
                            read_identity=read_identity,
                            read_process_census=read_process_census,
                            clock=clock,
                            deadline=deadline,
                        )
                    except ControllerError as census_error:
                        if (
                            getattr(census_error, "inventory_branch", None)
                            in _OWNED_PROCESS_EXIT_BRANCHES
                        ):
                            retry_target_pid = pid
                        raise
                if getattr(error, "inventory_branch", None) in _OWNED_PROCESS_EXIT_BRANCHES:
                    retry_target_pid = pid
                raise

        def scan_once() -> tuple[dict[int, ProcessIdentity], dict[int, GroupIdentity], bool]:
            nonlocal retry_target_pid
            queue = [launcher.pid]
            controller_children = read_pinned_children(controller_pid, controller_pin)
            unreadable = controller_children is None
            controller_child_roots: set[int] = set()
            if unreadable:
                mark_unreadable("controller_children_unavailable")
            if controller_children is not None:
                for child_pid in controller_children:
                    if (
                        child_pid == launcher.pid
                        or child_pid in known
                        or child_pid not in baseline_controller_children
                    ):
                        queue.append(child_pid)
                        controller_child_roots.add(child_pid)
            observed: dict[int, ProcessIdentity] = {}
            groups: dict[int, GroupIdentity] = {}
            visited: set[int] = set()
            while queue:
                pid = queue.pop()
                if pid in visited:
                    continue
                visited.add(pid)
                identity = read_identity(pid)
                if identity is None:
                    if pid not in confirmed_absences:
                        confirmed_absences[pid] = known.get(pid)
                        retry_target_pid = pid
                        raise _task_inventory_race("identity_missing_during_owned_scan")
                    continue
                if pid in confirmed_absences and confirmed_absences[pid] is None:
                    unreadable = True
                    mark_unreadable("unverified_pid_reappeared_after_absence")
                    continue
                previous = known.get(pid)
                if previous is not None and previous.starttime != identity.starttime:
                    unreadable = True
                    mark_unreadable("known_pid_starttime_changed")
                    continue
                if identity.uid != owner_uid:
                    unreadable = True
                    mark_unreadable("process_uid_mismatch")
                    continue
                if identity.starttime < launcher.starttime:
                    unreadable = True
                    mark_unreadable("process_started_before_launcher")
                    continue
                if pid in controller_child_roots and pid != launcher.pid:
                    # `_execute` establishes an empty controller-child
                    # baseline before its one launcher spawn. A later direct
                    # child is in that launch scope only while the pinned
                    # controller PPID edge still verifies.
                    if identity.ppid != controller_pid:
                        unreadable = True
                        mark_unreadable("unverified_subreaper_child")
                        continue
                if pid == launcher.pid and (
                    identity.starttime != launcher.starttime
                    or identity.pgrp != launcher.pgrp
                    or identity.session != launcher.session
                ):
                    unreadable = True
                    mark_unreadable("launcher_identity_mismatch")
                    continue
                observed[pid] = identity
                # Keep verified identities across a restarted walk so a PID
                # reused during the race is rejected instead of adopted.
                known[pid] = identity
                # A descendant can move into a process group whose original
                # leader has exited. Anchor each group to any positively
                # observed descendant in it, then revalidate that member's
                # PID/start time/UID/PGID/SID before signaling.
                if identity.pgrp > 1:
                    groups.setdefault(
                        identity.pgrp,
                        GroupIdentity(
                            pgid=identity.pgrp,
                            anchor_pid=identity.pid,
                            anchor_starttime=identity.starttime,
                            uid=identity.uid,
                            session=identity.session,
                        ),
                    )
                children = read_pinned_children(pid, identity)
                if children is None:
                    continue
                for child_pid in children:
                    child = read_identity(child_pid)
                    if child is None:
                        if child_pid not in confirmed_absences:
                            confirmed_absences[child_pid] = known.get(child_pid)
                            retry_target_pid = child_pid
                            raise _task_inventory_race("identity_missing_during_owned_scan")
                        continue
                    if child_pid in confirmed_absences and confirmed_absences[child_pid] is None:
                        unreadable = True
                        mark_unreadable("unverified_pid_reappeared_after_absence")
                        continue
                    if child.ppid != pid:
                        # It may have been reparented; the subreaper child list
                        # will pick it up on this or the next full snapshot.
                        continue
                    queue.append(child_pid)
            return observed, groups, unreadable

        require_controller_pin()
        try:
            result = scan_once()
        except ControllerError as error:
            retryable_exit = (
                getattr(error, "inventory_branch", None) in _OWNED_PROCESS_EXIT_BRANCHES
                and retry_target_pid is not None
                and retry_target_pid != controller_pid
                and not attempt_unreadable
            )
            if not retryable_exit:
                raise
            require_controller_pin()
            if scan_attempt + 1 == MAX_OWNED_TREE_SCAN_ATTEMPTS:
                raise _task_inventory_race("owned_tree_retry_budget_exhausted") from error
            # scan_once creates fresh queue/observed/groups state; controller
            # direct children are read again at the start of the next walk.
            continue
        require_controller_pin()
        return result

    raise _task_inventory_race("owned_tree_retry_budget_exhausted")


def _group_has_verified_member(
    group: GroupIdentity,
    known: dict[int, ProcessIdentity],
    *,
    read_identity: Callable[[int], ProcessIdentity | None] = _read_identity,
    on_absent: Callable[[int, ProcessIdentity], None] | None = None,
    on_present: Callable[[int, ProcessIdentity, ProcessIdentity], None] | None = None,
    on_changed: Callable[[int, ProcessIdentity, ProcessIdentity], None] | None = None,
) -> bool:
    for pid, previous in list(known.items()):
        if previous.pgrp != group.pgid or previous.session != group.session:
            continue
        current = read_identity(pid)
        if current is None:
            if on_absent is not None:
                on_absent(pid, previous)
            continue
        if not _same_process_identity_pin(previous, current):
            if on_changed is not None:
                on_changed(pid, previous, current)
            continue
        if on_present is not None:
            on_present(pid, previous, current)
        if current.uid == group.uid and current.state not in {"Z", "X", "x"}:
            return True
    return False


def _same_process_identity_pin(expected: ProcessIdentity, actual: ProcessIdentity | None) -> bool:
    return actual is not None and (
        actual.pid,
        actual.starttime,
        actual.uid,
        actual.pgrp,
        actual.session,
    ) == (
        expected.pid,
        expected.starttime,
        expected.uid,
        expected.pgrp,
        expected.session,
    )


def _reap_owned_adopted_children(
    known: dict[int, ProcessIdentity],
    *,
    controller_pid: int,
    launcher_pid: int,
    read_identity: Callable[[int], ProcessIdentity | None] | None = None,
    waitpid: Callable[[int, int], tuple[int, int]] | None = None,
    reaped_pids: set[int] | None = None,
    on_absent: Callable[[int, ProcessIdentity], None] | None = None,
    on_present: Callable[[int, ProcessIdentity, ProcessIdentity], None] | None = None,
    on_changed: Callable[[int, ProcessIdentity, ProcessIdentity], None] | None = None,
) -> bool:
    """Reap only identity-verified zombies adopted by this subreaper."""
    read_identity = read_identity or _read_identity
    waitpid = waitpid or os.waitpid
    incomplete = False
    for pid, previous in list(known.items()):
        if pid == launcher_pid:
            continue  # subprocess.Popen owns the direct launcher's wait status.
        if reaped_pids is not None and pid in reaped_pids:
            continue  # The exact child was already reaped by this controller.
        current = read_identity(pid)
        if current is None:
            if on_absent is not None:
                on_absent(pid, previous)
            else:
                incomplete = True
            continue
        if not _same_process_identity_pin(previous, current):
            incomplete = True
            if on_changed is not None:
                on_changed(pid, previous, current)
            continue
        if on_present is not None:
            on_present(pid, previous, current)
        if current.state not in {"Z", "X", "x"} or current.ppid != controller_pid:
            continue
        try:
            reaped_pid, _ = waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            # The child may have been reaped concurrently. Verify disappearance
            # rather than guessing from the exception.
            current = read_identity(pid)
            if current is None:
                if on_absent is not None:
                    on_absent(pid, previous)
                else:
                    incomplete = True
            elif not _same_process_identity_pin(previous, current):
                incomplete = True
                if on_changed is not None:
                    on_changed(pid, previous, current)
            else:
                incomplete = True
            continue
        except OSError:
            incomplete = True
            continue
        if reaped_pid == pid:
            if on_absent is not None:
                on_absent(pid, previous)
            if reaped_pids is None:
                known.pop(pid, None)
            else:
                # Keep the pin as a tombstone after reaping. A later child at
                # the same numeric PID must not be adopted as this process.
                reaped_pids.add(pid)
        else:
            current = read_identity(pid)
            if current is None:
                if on_absent is not None:
                    on_absent(pid, previous)
                else:
                    incomplete = True
            else:
                if not _same_process_identity_pin(previous, current):
                    if on_changed is not None:
                        on_changed(pid, previous, current)
                incomplete = True
    return incomplete


def _postcleanup_subreaper_children(
    *,
    controller_pid: int,
    controller_identity: ProcessIdentity,
    baseline_controller_children: set[int],
    launcher_identity: ProcessIdentity,
    known: dict[int, ProcessIdentity],
    read_identity: Callable[[int], ProcessIdentity | None],
    read_children: Callable[[int], set[int] | None],
    clock: Callable[[], float],
    deadline: float,
) -> set[int]:
    """Return postbaseline direct children left in the kernel child list.

    This check runs only after owned groups have settled. It uses the pinned
    controller's all-TID ``children`` inventory, with its existing bounded
    three-snapshot stability rule. Residual children are never signaled here.
    """
    _check_process_census_deadline(clock=clock, deadline=deadline)
    before = _read_identity(controller_pid)
    if not _same_process_identity_pin(controller_identity, before):
        raise ControllerError("postcleanup_controller_identity_unverified")
    if read_children is _read_children:
        children = read_children(
            controller_pid,
            read_identity=_read_identity,
            expected_identity=controller_identity,
            clock=clock,
            deadline=deadline,
        )
    else:
        children = read_children(controller_pid)
    _check_process_census_deadline(clock=clock, deadline=deadline)
    if children is None or any(
        not isinstance(child_pid, int) or child_pid < 1 for child_pid in children
    ):
        raise ControllerError("controller_children_unavailable")

    remaining: set[int] = set()
    for child_pid in children:
        _check_process_census_deadline(clock=clock, deadline=deadline)
        if child_pid in baseline_controller_children:
            continue
        child = read_identity(child_pid)
        if (
            child is None
            or child.pid != child_pid
            or child.ppid != controller_pid
            or child.uid != controller_identity.uid
            or child.starttime < launcher_identity.starttime
        ):
            raise ControllerError("postcleanup_subreaper_child")
        expected = known.get(child_pid)
        if expected is not None and not _same_process_identity_pin(expected, child):
            raise ControllerError("postcleanup_subreaper_child")
        current_child = read_identity(child_pid)
        if (
            not _same_process_identity_pin(child, current_child)
            or current_child.ppid != controller_pid
        ):
            raise ControllerError("postcleanup_subreaper_child")
        # Even a known, correctly pinned child is unresolved until it has left
        # the controller's child inventory (or was reaped by exact PID).
        remaining.add(child_pid)

    _check_process_census_deadline(clock=clock, deadline=deadline)
    after = _read_identity(controller_pid)
    if not _same_process_identity_pin(controller_identity, after):
        raise ControllerError("postcleanup_controller_identity_unverified")
    return remaining


def _signal_group_if_verified(
    group: GroupIdentity,
    known: dict[int, ProcessIdentity],
    signum: int,
    *,
    killpg: Callable[[int, int], None] = os.killpg,
    read_identity: Callable[[int], ProcessIdentity | None] = _read_identity,
    on_absent: Callable[[int, ProcessIdentity], None] | None = None,
    on_present: Callable[[int, ProcessIdentity, ProcessIdentity], None] | None = None,
    on_changed: Callable[[int, ProcessIdentity, ProcessIdentity], None] | None = None,
) -> bool:
    if not _group_has_verified_member(
        group,
        known,
        read_identity=read_identity,
        on_absent=on_absent,
        on_present=on_present,
        on_changed=on_changed,
    ):
        return False
    killpg(group.pgid, signum)
    return True


def _wait_for_groups(
    groups: dict[int, GroupIdentity],
    known: dict[int, ProcessIdentity],
    duration: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    read_identity: Callable[[int], ProcessIdentity | None] = _read_identity,
) -> None:
    deadline = clock() + duration
    while clock() < deadline:
        if not any(
            _group_has_verified_member(group, known, read_identity=read_identity)
            for group in groups.values()
        ):
            return
        sleep(min(0.05, max(0.0, deadline - clock())))


def _deadlines(started: float, config: ControllerConfig) -> tuple[float, float]:
    return (
        min(started + config.run_stop_offset_seconds, started + config.total_wall_seconds),
        started + config.total_wall_seconds,
    )


def _terminate_verified_groups(
    groups: dict[int, GroupIdentity],
    known: dict[int, ProcessIdentity],
    *,
    launcher_pgid: int,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    killpg: Callable[[int, int], None] = os.killpg,
    read_identity: Callable[[int], ProcessIdentity | None] = _read_identity,
    deadline: float | None = None,
) -> set[int]:
    """Signal only recorded sessions; return groups still identity-verifiable."""
    active = {
        pgid: group
        for pgid, group in groups.items()
        if _group_has_verified_member(group, known, read_identity=read_identity)
    }
    # Nested journey/helper sessions receive graceful signals before the
    # launcher session so their own finally blocks can run first.
    order = sorted(active.values(), key=lambda group: group.pgid == launcher_pgid)
    for signum, grace in (
        (signal.SIGINT, INT_GRACE_SECONDS),
        (signal.SIGTERM, TERM_GRACE_SECONDS),
        (signal.SIGKILL, KILL_GRACE_SECONDS),
    ):
        if deadline is not None:
            remaining_seconds = deadline - clock()
            if remaining_seconds <= 0:
                break
            grace = min(grace, remaining_seconds)
        for group in order:
            if not _group_has_verified_member(group, known, read_identity=read_identity):
                continue
            try:
                _signal_group_if_verified(
                    group,
                    known,
                    signum,
                    killpg=killpg,
                    read_identity=read_identity,
                )
            except ProcessLookupError:
                pass
        _wait_for_groups(
            active,
            known,
            grace,
            clock=clock,
            sleep=sleep,
            read_identity=read_identity,
        )
        active = {
            pgid: group
            for pgid, group in active.items()
            if _group_has_verified_member(group, known, read_identity=read_identity)
        }
        if not active:
            break
    return set(active)


def _stop_unpinned_popen_child(
    process: Any,
    *,
    deadline: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> bool:
    """Stop only the direct child owned by this Popen handle.

    This does not establish ownership of the child's descendants. This helper
    relies on the CLI controller's single-reaper model: no other thread or
    SIGCHLD handler may wait for this child between Popen.poll() and its
    signal method. Do not use this fallback from an embedding host with a
    concurrent reaper; that host must provide an atomic pidfd-based signal.
    """
    if process is None:
        return False

    def exited() -> bool | None:
        try:
            return process.poll() is not None
        except Exception:
            return None

    state = exited()
    if state is True:
        return True
    # If poll cannot establish whether this exact child is still ours, do not
    # risk signaling its numeric PID after an external reap or reuse.
    if state is None or clock() >= deadline:
        return False

    try:
        process.terminate()
    except ProcessLookupError:
        pass
    except Exception:
        # Continue through the bounded grace and attempt kill only if poll
        # still confirms the same Popen child is live.
        pass

    def wait_for_exit(grace_seconds: float) -> bool:
        stop_at = min(deadline, clock() + grace_seconds)
        while clock() < stop_at:
            state = exited()
            if state is not None:
                return state
            sleep(min(0.05, max(0.0, stop_at - clock())))
        return exited() is True

    if wait_for_exit(TERM_GRACE_SECONDS):
        return True
    if clock() >= deadline or exited() is not False:
        return False
    try:
        process.kill()
    except ProcessLookupError:
        pass
    except Exception:
        pass
    return wait_for_exit(KILL_GRACE_SECONDS)


def _stop_owned_tree(
    *,
    controller_pid: int,
    process: Any,
    launcher_identity: ProcessIdentity | None,
    baseline_controller_children: set[int],
    known: dict[int, ProcessIdentity],
    groups: dict[int, GroupIdentity],
    deadline: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    read_identity: Callable[[int], ProcessIdentity | None],
    read_children: Callable[[int], set[int] | None],
    killpg: Callable[[int, int], None],
    waitpid: Callable[[int, int], tuple[int, int]],
    read_process_census: Callable[[], dict[int, int]] | None = None,
    diagnostic_sink: dict[str, Any] | None = None,
) -> tuple[set[int], bool, bool]:
    """Rescan, signal, and reap only positively owned descendants.

    The return values are (remaining active PGIDs, inventory incomplete,
    unexpected live groups observed). Adopted zombies are reaped by their
    exact positive PID; this never calls waitpid(-1).
    """
    owner_uid = os.geteuid()
    controller_identity = _read_identity(controller_pid)
    launcher_pid = int(process.pid) if process is not None else 0
    if (
        controller_identity is None
        or controller_identity.pid != controller_pid
        or controller_identity.uid != owner_uid
    ):
        _stop_unpinned_popen_child(
            process,
            deadline=deadline,
            clock=clock,
            sleep=sleep,
        )
        _record_inventory_unreadable(diagnostic_sink, "postcleanup_controller_identity_unverified")
        return set(), True, False
    if launcher_identity is None:
        # The first process identity pin failed. Stop only the direct child
        # through its Popen handle; never signal a group or adopt descendants
        # by PID. The unresolved inventory still makes cleanup incomplete.
        _stop_unpinned_popen_child(
            process,
            deadline=deadline,
            clock=clock,
            sleep=sleep,
        )
        _record_inventory_unreadable(diagnostic_sink, "launcher_identity_mismatch")
        return set(), True, False
    anchor = launcher_identity
    inventory_incomplete = False
    saw_live_groups = False
    pending_absences: dict[int, ProcessIdentity] = {}
    confirmed_absences: dict[int, ProcessIdentity] = {}
    reconciliation_scans = 0
    reaped_pids: set[int] = set()

    def note_absent(pid: int, expected: ProcessIdentity) -> None:
        if pid not in confirmed_absences:
            pending_absences.setdefault(pid, expected)

    def note_present(pid: int, expected: ProcessIdentity, actual: ProcessIdentity) -> None:
        if not _same_process_identity_pin(expected, actual):
            note_changed(pid, expected, actual)
            return
        # A positive observation of the same pin invalidates an earlier
        # absence confirmation. A later disappearance then requires a new
        # fresh owned-tree walk before cleanup can be complete.
        pending_absences.pop(pid, None)
        confirmed_absences.pop(pid, None)

    def note_changed(_pid: int, _expected: ProcessIdentity, _actual: ProcessIdentity) -> None:
        nonlocal inventory_incomplete
        inventory_incomplete = True
        _record_inventory_unreadable(diagnostic_sink, "known_pid_identity_changed")

    def mark_reconciliation_incomplete() -> None:
        nonlocal inventory_incomplete
        inventory_incomplete = True
        _record_inventory_unreadable(diagnostic_sink, "owned_pid_reconciliation_budget_exhausted")

    def refresh() -> None:
        nonlocal inventory_incomplete, anchor
        pending_before_scan = dict(pending_absences)
        may_confirm_absence = bool(pending_before_scan) and clock() < deadline
        if process is not None:
            try:
                process.poll()  # Reap the direct Popen child when it has exited.
            except Exception:
                inventory_incomplete = True
        try:
            observed, seen_groups, unreadable = _scan_owned_tree(
                controller_pid=controller_pid,
                launcher=anchor,
                baseline_controller_children=baseline_controller_children,
                known=known,
                read_identity=read_identity,
                read_children=read_children,
                read_process_census=read_process_census,
                deadline=deadline,
                clock=clock,
                diagnostic_sink=diagnostic_sink,
            )
            observed_same_pin: set[int] = set()
            changed_observed: set[int] = set()
            for pid, actual in observed.items():
                expected = known.get(pid)
                if expected is None:
                    continue
                if not _same_process_identity_pin(expected, actual):
                    note_changed(pid, expected, actual)
                    changed_observed.add(pid)
                    continue
                if pid in pending_absences or pid in confirmed_absences:
                    observed_same_pin.add(pid)
                    note_present(pid, expected, actual)
            known.update(
                {pid: actual for pid, actual in observed.items() if pid not in changed_observed}
            )
            groups.update(seen_groups)
            inventory_incomplete |= unreadable
            if pending_before_scan:
                if unreadable or not may_confirm_absence:
                    inventory_incomplete = True
                else:
                    for pid, expected in pending_before_scan.items():
                        try:
                            current = read_identity(pid)
                        except Exception:
                            inventory_incomplete = True
                            continue
                        if pid in observed_same_pin:
                            if current is None:
                                # The pin was present in this full snapshot and
                                # disappeared afterward. This scan predates the
                                # new absence, so it cannot confirm that absence.
                                note_absent(pid, expected)
                            elif _same_process_identity_pin(expected, current):
                                note_present(pid, expected, current)
                            else:
                                pending_absences.pop(pid, None)
                                note_changed(pid, expected, current)
                            if clock() >= deadline:
                                inventory_incomplete = True
                            continue
                        if clock() >= deadline:
                            inventory_incomplete = True
                            continue
                        if current is None:
                            confirmed_absences[pid] = expected
                            if pending_absences.get(pid) == expected:
                                pending_absences.pop(pid, None)
                        elif _same_process_identity_pin(expected, current):
                            note_present(pid, expected, current)
                        else:
                            pending_absences.pop(pid, None)
                            note_changed(pid, expected, current)
        except Exception as error:
            inventory_incomplete = True
            _record_inventory_exception(diagnostic_sink, error)
        try:
            inventory_incomplete |= _reap_owned_adopted_children(
                known,
                controller_pid=controller_pid,
                launcher_pid=launcher_pid,
                read_identity=read_identity,
                waitpid=waitpid,
                reaped_pids=reaped_pids,
                on_absent=note_absent,
                on_present=note_present,
                on_changed=note_changed,
            )
        except Exception:
            inventory_incomplete = True

    def active_groups() -> dict[int, GroupIdentity]:
        return {
            pgid: group
            for pgid, group in groups.items()
            if _group_has_verified_member(
                group,
                known,
                read_identity=read_identity,
                on_absent=note_absent,
                on_present=note_present,
                on_changed=note_changed,
            )
        }

    def reconcile_pending_absences() -> None:
        nonlocal reconciliation_scans
        while pending_absences:
            if clock() >= deadline or reconciliation_scans >= MAX_STOP_RECONCILIATION_SCANS:
                mark_reconciliation_incomplete()
                return
            reconciliation_scans += 1
            refresh()
            if inventory_incomplete:
                return

    def settled_active_groups() -> dict[int, GroupIdentity]:
        active = active_groups()
        if pending_absences:
            reconcile_pending_absences()
            active = active_groups()
        if pending_absences:
            mark_reconciliation_incomplete()
        return active

    for signum, grace in (
        (signal.SIGINT, INT_GRACE_SECONDS),
        (signal.SIGTERM, TERM_GRACE_SECONDS),
        (signal.SIGKILL, KILL_GRACE_SECONDS),
    ):
        if clock() >= deadline:
            break
        refresh()
        active = settled_active_groups()
        if not active:
            break
        saw_live_groups = True
        order = sorted(active.values(), key=lambda group: group.pgid == anchor.pgrp)
        for group in order:
            if clock() >= deadline:
                break
            try:
                _signal_group_if_verified(
                    group,
                    known,
                    signum,
                    killpg=killpg,
                    read_identity=read_identity,
                    on_absent=note_absent,
                    on_present=note_present,
                    on_changed=note_changed,
                )
            except ProcessLookupError:
                pass
            except OSError:
                inventory_incomplete = True
        if pending_absences:
            reconcile_pending_absences()
        wait_deadline = min(clock() + grace, deadline)
        while clock() < wait_deadline:
            refresh()
            if not settled_active_groups():
                break
            sleep(min(0.05, max(0.0, wait_deadline - clock())))

    # A parent may have exited during the last signal interval, causing new
    # descendants to be adopted just before the deadline. Rescan/reap once more
    # and report anything still active instead of claiming full cleanup.
    refresh()
    active = settled_active_groups()
    if pending_absences:
        mark_reconciliation_incomplete()
    if not active and not inventory_incomplete and not pending_absences:
        try:
            residual_children = _postcleanup_subreaper_children(
                controller_pid=controller_pid,
                controller_identity=controller_identity,
                baseline_controller_children=baseline_controller_children,
                launcher_identity=anchor,
                known=known,
                read_identity=read_identity,
                read_children=read_children,
                clock=clock,
                deadline=deadline,
            )
            if residual_children:
                inventory_incomplete = True
                _record_inventory_unreadable(diagnostic_sink, "postcleanup_subreaper_child")
        except Exception as error:
            inventory_incomplete = True
            _record_inventory_exception(diagnostic_sink, error)
    return set(active), inventory_incomplete, saw_live_groups


def _read_launcher_stdout(stream: Any, buffer: bytearray, *, total: int) -> tuple[int, bool, bool]:
    while True:
        try:
            block = os.read(stream.fileno(), 4096)
        except BlockingIOError:
            return total, False, False
        except OSError:
            return total, True, True
        if not block:
            return total, True, False
        total += len(block)
        if len(buffer) < MAX_STDOUT_BYTES:
            buffer.extend(block[: MAX_STDOUT_BYTES - len(buffer)])


def _parse_launcher_stdout(buffer: bytearray, total: int, *, launcher_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "unavailable"}
    if not buffer or total > MAX_STDOUT_BYTES:
        return result
    try:
        payload = json.loads(bytes(buffer).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return result
    if not isinstance(payload, dict) or payload.get("status") != "finished":
        return result
    receipt_path = payload.get("receipt_path")
    exit_code = payload.get("exit_code")
    if (
        not isinstance(receipt_path, str)
        or not re.fullmatch(r"[0-9a-f]{32}", Path(receipt_path).parent.name)
        or Path(receipt_path).parent.parent != launcher_root / "runs"
        or Path(receipt_path).name != "receipt.json"
        or not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
    ):
        return result
    result.update({"status": "finished", "exit_code": exit_code, "receipt_path": receipt_path})
    return result


def _inner_cleanup_ok(launcher_result: dict[str, Any]) -> bool:
    if launcher_result.get("status") != "finished":
        return False
    path = Path(launcher_result["receipt_path"])
    try:
        _lstat_owned(path, mode=0o600)
        raw = path.read_bytes()
        if len(raw) > 64 * 1024:
            return False
        receipt = json.loads(raw)
    except (ControllerError, OSError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(receipt, dict)
        and receipt.get("cleanup_status") == "complete"
        and receipt.get("temporary_files_removed") is True
        and receipt.get("operator_auth_unchanged") is True
        and receipt.get("source_unchanged") is True
    )


def _successful_exit(
    launcher_result: dict[str, Any],
    *,
    launcher_returncode: int | None,
    cleanup_status: str,
    outer_timed_out: bool,
    identity_failure: bool,
) -> bool:
    """Accept only a successful journey whose owned cleanup also completed."""
    return bool(
        cleanup_status == "complete"
        and launcher_result.get("status") == "finished"
        and launcher_result.get("exit_code") == 0
        and launcher_returncode == 0
        and not outer_timed_out
        and not identity_failure
    )


def _new_receipt_file(config: ControllerConfig) -> tuple[str, int]:
    config.receipt_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _lstat_owned(config.receipt_root, mode=0o700, directory=True)
    run_id = uuid.uuid4().hex
    path = config.receipt_root / f"{run_id}.json"
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    return str(path), descriptor


def _write_receipt(path: str, descriptor: int, receipt: dict[str, Any]) -> None:
    try:
        payload = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("receipt_write_failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    info = _lstat_owned(Path(path), mode=0o600)
    if info.st_size > 16 * 1024:
        raise ControllerError("outer_receipt_size_invalid")


def _execute(
    *,
    config: ControllerConfig,
    popen: Callable[..., Any],
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    verify_pins: Callable[[], dict[str, str]] | None = None,
    enable_subreaper: Callable[[], None] | None = None,
    read_identity: Callable[[int], ProcessIdentity | None] | None = None,
    read_children: Callable[[int], set[int] | None] | None = None,
    read_process_census: Callable[[], dict[int, int]] | None = None,
    killpg: Callable[[int, int], None] | None = None,
    waitpid: Callable[[int, int], tuple[int, int]] | None = None,
    write_receipt: Callable[[str, int, dict[str, Any]], None] | None = None,
) -> tuple[int, dict[str, Any]]:
    # Dependencies are resolved at call time so offline tests can inject
    # sentinels. In particular, every caller must pass the spawn callable
    # explicitly; tests cannot accidentally inherit a real subprocess.Popen.
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    verify_pins = verify_pins or (lambda: _verify_static_pins(config))
    enable_subreaper = enable_subreaper or _enable_child_subreaper
    read_identity = read_identity or _read_identity
    read_children = read_children or _read_children
    killpg = killpg or os.killpg
    waitpid = waitpid or os.waitpid
    write_receipt = write_receipt or _write_receipt
    started = clock()
    run_deadline, hard_deadline = _deadlines(started, config)
    receipt_path, receipt_fd = _new_receipt_file(config)
    receipt: dict[str, Any] = {
        "controller_status": "failed",
        "controller_failure": None,
        "runner_sha256": config.runner_sha256,
        "manifest_sha256": config.manifest_sha256,
        "total_wall_seconds": config.total_wall_seconds,
        "run_stop_offset_seconds": config.run_stop_offset_seconds,
        "launcher_started": False,
        "launcher_returncode": None,
        "outer_deadline_reached": False,
        "launcher_stdout": {"status": "unavailable", "bytes": 0, "truncated": False},
        "launcher_receipt_path": None,
        "owned_group_count": 0,
        "remaining_owned_group_count": 0,
        "unreadable_process_inventory": False,
        "process_inventory_diagnostic": None,
        "cleanup_status": "not_started",
        "temporary_files_removed": False,
        "receipt_path": receipt_path,
    }
    process: Any = None
    known: dict[int, ProcessIdentity] = {}
    groups: dict[int, GroupIdentity] = {}
    inventory_diagnostic: dict[str, Any] = {}
    launcher_identity: ProcessIdentity | None = None
    outer_timed_out = False
    identity_failure = False
    cleanup_finalized = False
    controller_pid = os.getpid()
    baseline_children: set[int] = set()
    stdout_buffer = bytearray()
    stdout_total = 0
    stdout_closed = False
    selector: selectors.BaseSelector | None = None
    return_code = 1
    try:
        receipt["pins"] = verify_pins()
        environment = _launcher_environment(dict(os.environ), config)
        observed_baseline = read_children(controller_pid)
        if observed_baseline is None:
            _record_inventory_unreadable(inventory_diagnostic, "controller_children_unavailable")
            raise ControllerError("controller_has_existing_children")
        if observed_baseline:
            raise ControllerError("controller_has_existing_children")
        baseline_children = observed_baseline
        enable_subreaper()
        if clock() >= run_deadline:
            raise ControllerError("prelaunch_deadline_exceeded")
        process = popen(
            [str(config.python_executable), "-B", str(config.runner), "--execute"],
            cwd=str(config.launcher_root),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            bufsize=0,
        )
        receipt["launcher_started"] = True
        # The very first observation is the only safe ownership pin. If it is
        # unavailable, fail closed before touching stdout or selector state;
        # retrying could adopt a different process after PID reuse.
        try:
            launcher_identity = read_identity(process.pid)
        except Exception as error:
            _record_inventory_exception(inventory_diagnostic, error)
            launcher_identity = None
        if launcher_identity is None or not (
            launcher_identity.uid == os.geteuid()
            and launcher_identity.ppid == controller_pid
            and launcher_identity.pid == launcher_identity.pgrp == launcher_identity.session
            and launcher_identity.session != os.getsid(controller_pid)
        ):
            receipt["controller_failure"] = "launcher_identity_unverified"
            identity_failure = True
            receipt["unreadable_process_inventory"] = True
            _record_inventory_unreadable(inventory_diagnostic, "launcher_identity_mismatch")
            receipt["process_inventory_diagnostic"] = dict(inventory_diagnostic)
            launcher_identity = None
            raise ControllerError("launcher_identity_unverified")
        else:
            known[launcher_identity.pid] = launcher_identity
        # Pin the launched process before touching stdout or selector state.
        # If any of that setup fails, cleanup can now target only this verified
        # process group; without the first pin, cleanup remains signal-free.
        stdout = process.stdout
        if stdout is None:
            raise ControllerError("launcher_stdout_unavailable")
        os.set_blocking(stdout.fileno(), False)
        selector = selectors.DefaultSelector()
        selector.register(stdout, selectors.EVENT_READ)
        while (
            not outer_timed_out
            and not identity_failure
            and process.poll() is None
            and clock() < run_deadline
        ):
            assert launcher_identity is not None
            observed, seen_groups, unreadable = _scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher_identity,
                baseline_controller_children=baseline_children,
                known=known,
                read_identity=read_identity,
                read_children=read_children,
                read_process_census=read_process_census,
                deadline=run_deadline,
                clock=clock,
                diagnostic_sink=inventory_diagnostic,
            )
            known.update(observed)
            groups.update(seen_groups)
            receipt["unreadable_process_inventory"] |= unreadable
            if inventory_diagnostic:
                receipt["process_inventory_diagnostic"] = dict(inventory_diagnostic)
            if selector is not None and selector.get_map():
                for key, _ in selector.select(timeout=0.05):
                    added, closed, read_error = _read_launcher_stdout(
                        key.fileobj, stdout_buffer, total=stdout_total
                    )
                    stdout_total = added
                    if closed:
                        selector.unregister(key.fileobj)
                        stdout_closed = True
                    if read_error:
                        receipt["unreadable_process_inventory"] = True
            else:
                sleep(0.05)
        if not identity_failure and process.poll() is None and clock() >= run_deadline:
            outer_timed_out = True
            receipt["outer_deadline_reached"] = True
        receipt["launcher_returncode"] = process.poll()
        if selector is not None and selector.get_map() and process.poll() is not None:
            post_exit_deadline = min(clock() + 1.0, hard_deadline)
            while clock() < post_exit_deadline and selector.get_map():
                for key, _ in selector.select(timeout=0.05):
                    added, closed, read_error = _read_launcher_stdout(
                        key.fileobj, stdout_buffer, total=stdout_total
                    )
                    stdout_total = added
                    if closed:
                        selector.unregister(key.fileobj)
                        stdout_closed = True
                    if read_error:
                        receipt["unreadable_process_inventory"] = True
            if selector.get_map():
                receipt["unreadable_process_inventory"] = True
                for key in list(selector.get_map().values()):
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
        receipt["launcher_stdout"] = {
            "status": "read" if stdout_closed else "unavailable",
            "bytes": stdout_total,
            "truncated": stdout_total > MAX_STDOUT_BYTES,
        }
        launcher_result = _parse_launcher_stdout(
            stdout_buffer, stdout_total, launcher_root=config.launcher_root
        )
        receipt["launcher_receipt_path"] = launcher_result.get("receipt_path")
        receipt["launcher_result"] = launcher_result.get("status", "unavailable")
        remaining, inventory_incomplete, saw_live_groups = _stop_owned_tree(
            controller_pid=controller_pid,
            process=process,
            launcher_identity=launcher_identity,
            baseline_controller_children=baseline_children,
            known=known,
            groups=groups,
            deadline=hard_deadline,
            clock=clock,
            sleep=sleep,
            read_identity=read_identity,
            read_children=read_children,
            read_process_census=read_process_census,
            killpg=killpg,
            waitpid=waitpid,
            diagnostic_sink=inventory_diagnostic,
        )
        receipt["unreadable_process_inventory"] |= inventory_incomplete
        if inventory_diagnostic:
            receipt["process_inventory_diagnostic"] = dict(inventory_diagnostic)
        live_groups = {pgid: groups[pgid] for pgid in remaining if pgid in groups}
        receipt["owned_group_count"] = len(groups)
        cleanup_needed = (
            outer_timed_out
            or identity_failure
            or saw_live_groups
            or bool(live_groups)
            or receipt["unreadable_process_inventory"]
        )
        if cleanup_needed:
            # Any timeout, unreadable inventory, or unexpectedly live child
            # preserves the private root and cannot be reported as clean.
            receipt["cleanup_status"] = "incomplete"
            receipt["temporary_files_removed"] = False
        else:
            receipt["cleanup_status"] = (
                "complete" if _inner_cleanup_ok(launcher_result) else "incomplete"
            )
            receipt["temporary_files_removed"] = receipt["cleanup_status"] == "complete"
        receipt["remaining_owned_group_count"] = len(live_groups)
        receipt["launcher_result"] = launcher_result.get("status", "unavailable")
        receipt["launcher_exit_code"] = launcher_result.get("exit_code")
        receipt["journey_succeeded"] = bool(
            launcher_result.get("status") == "finished"
            and launcher_result.get("exit_code") == 0
            and receipt["launcher_returncode"] == 0
        )
        receipt["controller_status"] = (
            "outer_deadline_exceeded"
            if outer_timed_out
            else "controller_error" if identity_failure else "completed"
        )
        cleanup_finalized = True
        return_code = (
            0
            if _successful_exit(
                launcher_result,
                launcher_returncode=receipt["launcher_returncode"],
                cleanup_status=receipt["cleanup_status"],
                outer_timed_out=outer_timed_out,
                identity_failure=identity_failure,
            )
            else 1
        )
    except ControllerError as error:
        receipt["controller_failure"] = str(error)
        _record_inventory_exception(inventory_diagnostic, error)
        if inventory_diagnostic:
            receipt["process_inventory_diagnostic"] = dict(inventory_diagnostic)
        receipt["controller_status"] = (
            "prelaunch_refused" if process is None else "controller_error"
        )
        receipt["cleanup_status"] = "incomplete" if process is not None else "not_started"
        receipt["temporary_files_removed"] = False
    except Exception as error:
        _record_inventory_exception(inventory_diagnostic, error)
        if inventory_diagnostic:
            receipt["process_inventory_diagnostic"] = dict(inventory_diagnostic)
        receipt["controller_failure"] = "controller_internal_error"
        receipt["controller_status"] = "controller_error"
        receipt["cleanup_status"] = "incomplete" if process is not None else "not_started"
        receipt["temporary_files_removed"] = False
    finally:
        if process is not None and not cleanup_finalized:
            try:
                remaining, inventory_incomplete, _ = _stop_owned_tree(
                    controller_pid=controller_pid,
                    process=process,
                    launcher_identity=launcher_identity,
                    baseline_controller_children=baseline_children,
                    known=known,
                    groups=groups,
                    deadline=hard_deadline,
                    clock=clock,
                    sleep=sleep,
                    read_identity=read_identity,
                    read_children=read_children,
                    read_process_census=read_process_census,
                    killpg=killpg,
                    waitpid=waitpid,
                    diagnostic_sink=inventory_diagnostic,
                )
                receipt["owned_group_count"] = len(groups)
                receipt["remaining_owned_group_count"] = len(remaining)
                receipt["unreadable_process_inventory"] |= inventory_incomplete
                if inventory_diagnostic:
                    receipt["process_inventory_diagnostic"] = dict(inventory_diagnostic)
            except BaseException:
                # Preserve artifacts and report uncertainty if cleanup itself
                # is interrupted or the exact process inventory is unreadable.
                receipt["unreadable_process_inventory"] = True
            if inventory_diagnostic:
                receipt["process_inventory_diagnostic"] = dict(inventory_diagnostic)
            receipt["cleanup_status"] = "incomplete"
            receipt["temporary_files_removed"] = False
            cleanup_finalized = True
        if selector is not None:
            try:
                selector.close()
            except Exception:
                receipt["controller_failure"] = "selector_cleanup_failed"
                receipt["controller_status"] = "controller_error"
                receipt["cleanup_status"] = "incomplete"
                receipt["temporary_files_removed"] = False
                return_code = 1
        try:
            write_receipt(receipt_path, receipt_fd, receipt)
        except Exception:
            # The file was created with O_EXCL before launch; never overwrite
            # or delete it after an uncertain write.
            receipt["controller_failure"] = "outer_receipt_write_failed"
            receipt["controller_status"] = "controller_error"
            receipt["cleanup_status"] = "incomplete"
            receipt["temporary_files_removed"] = False
            return_code = 1
            try:
                os.close(receipt_fd)
            except OSError:
                pass
    return return_code, receipt


def _load_config(path: Path) -> ControllerConfig:
    _lstat_owned(path, mode=0o600)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) != 0o600:
            raise ControllerError("controller_config_invalid")
        raw = os.read(descriptor, 64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ControllerError("controller_config_invalid")
    finally:
        os.close(descriptor)
    try:
        return ControllerConfig.from_mapping(json.loads(raw))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ControllerError("controller_config_invalid") from error


def main(
    config: ControllerConfig | None = None,
    argv: list[str] | None = None,
    *,
    execute: bool = False,
) -> int:
    if config is None:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--config", type=Path)
        parser.add_argument("--execute", action="store_true")
        args = parser.parse_args(argv)
        if not args.execute:
            print(json.dumps({"status": "prepared_only", "execution_started": False}))
            return 0
        if args.config is None:
            parser.error("--config is required with --execute")
        try:
            config = _load_config(args.config)
        except ControllerError as error:
            print(json.dumps({"status": "configuration_refused", "reason": str(error)}))
            return 2
    elif execute is not True:
        print(json.dumps({"status": "prepared_only", "execution_started": False}))
        return 0
    if not isinstance(config, ControllerConfig):
        print(
            json.dumps({"status": "configuration_refused", "reason": "controller_config_invalid"})
        )
        return 2
    result, receipt = _execute(config=config, popen=subprocess.Popen)
    print(
        json.dumps(
            {
                "status": receipt.get("controller_status"),
                "exit_code": result,
                "receipt_path": receipt.get("receipt_path"),
            },
            sort_keys=True,
        )
    )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
