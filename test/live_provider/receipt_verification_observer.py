"""Private, fixture-only bridge for receipt verification diagnostics.

This module is copied into one API worker's private ``PYTHONPATH`` bootstrap.
It deliberately has no production environment switch. Its stage ledger records
only fixed enums/ordinals; a separate explicit private capture can retain bounded
render-comparison inputs for offline diagnosis.
"""

from __future__ import annotations

import atexit
import functools
import hashlib
import importlib
import inspect
import json
import os
import re
import stat
import threading
import time
from pathlib import Path
from typing import Any, Mapping

SOURCE_MODULES = {
    "cli_agent_orchestrator.providers.opencode_cli": (
        "src/cli_agent_orchestrator/providers/opencode_cli.py"
    ),
    "cli_agent_orchestrator.services.status_monitor": (
        "src/cli_agent_orchestrator/services/status_monitor.py"
    ),
    "cli_agent_orchestrator.services.terminal_service": (
        "src/cli_agent_orchestrator/services/terminal_service.py"
    ),
    "cli_agent_orchestrator.utils.receipt_verification_diagnostics": (
        "src/cli_agent_orchestrator/utils/receipt_verification_diagnostics.py"
    ),
}

_ALLOWED_OUTCOMES = {
    "admission": frozenset({"stale_capture", "current_capture", "progress_unconfirmed"}),
    "attempt": frozenset({"started", "verified", "unresolved", "superseded"}),
    "result_extraction": frozenset({"started", "returned", "unavailable", "error"}),
    "receipt_witness": frozenset({"started", "passed", "rejected", "error", "not_applicable"}),
    "canonicalizer": frozenset({"started", "passed", "rejected", "error", "not_applicable"}),
    "active_receipt_identity": frozenset(
        {"started", "passed", "rejected", "error", "skip", "not_applicable"}
    ),
    "native_binding": frozenset({"started", "passed", "rejected", "error", "skip"}),
    "native_parts": frozenset({"started", "passed", "rejected", "error", "skip"}),
    "native_receipt_identity": frozenset({"started", "passed", "rejected", "error", "skip"}),
    "native_render_guard": frozenset({"started", "passed", "rejected", "error", "skip"}),
    "durable_cas": frozenset({"started", "settled", "conflict", "error"}),
}

SCHEMA_VERSION = 1
MAX_CONFIG_BYTES = 16 * 1024
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_HELPER_BYTES = 2 * 1024 * 1024
MAX_EVENT_BYTES = 4096
MAX_EVENTS = 4096
MAX_LEDGER_BYTES = 1024 * 1024
MAX_FAILURE_COUNT = MAX_EVENTS + 1
MAX_THREAD_COUNT = MAX_EVENTS
BOOTSTRAP_FILENAME = "sitecustomize.py"
HELPER_FILENAME = "receipt_verification_observer.py"
CONFIG_FILENAME = "receipt-verification-observer.json"
EVENT_FILENAME = "events.jsonl"
SEAL_FILENAME = "seal.json"
LATE_FILENAME = "late-callback"
_ROLE_PATTERN = re.compile(r"api_[ab]\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def _role_is_valid(role: object) -> bool:
    return type(role) is str and _ROLE_PATTERN.fullmatch(role) is not None


def _secure_directory(path: Path, *, create: bool = False) -> Path:
    path = Path(path)
    if create and not path.exists():
        path.mkdir(mode=0o700)
    try:
        info = path.lstat()
    except OSError as exc:
        raise RuntimeError("private_observer_directory_required") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise RuntimeError("private_observer_directory_required")
    return path


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        stat.S_IMODE(info.st_mode),
    )


def _read_regular_file(path: Path, *, maximum: int, mode: int | None = None) -> bytes:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or (mode is not None and stat.S_IMODE(before.st_mode) != mode)
        or before.st_size > maximum
    ):
        raise RuntimeError("private_observer_file_invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise RuntimeError("private_observer_file_changed")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after_fd = os.fstat(descriptor)
        after_path = path.lstat()
        if (
            len(data) > maximum
            or _identity(after_fd) != _identity(opened)
            or _identity(after_path) != _identity(opened)
        ):
            raise RuntimeError("private_observer_file_changed")
        return data
    finally:
        os.close(descriptor)


def _write_new_file(path: Path, content: bytes, *, maximum: int) -> None:
    if len(content) > maximum:
        raise RuntimeError("private_observer_file_oversized")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("private observer write did not advance")
            view = view[written:]
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise RuntimeError("private_observer_file_invalid")
    finally:
        os.close(descriptor)


def _sha256_regular_file(path: Path, *, maximum: int) -> str:
    return hashlib.sha256(_read_regular_file(path, maximum=maximum)).hexdigest()


def _validate_source_hashes(repo: Path, source_hashes: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(source_hashes, Mapping) or set(source_hashes) != set(SOURCE_MODULES.values()):
        raise RuntimeError("private_observer_source_set_invalid")
    validated: dict[str, str] = {}
    for relative in sorted(SOURCE_MODULES.values()):
        expected = source_hashes[relative]
        if type(expected) is not str or _SHA256_PATTERN.fullmatch(expected) is None:
            raise RuntimeError("private_observer_source_hash_invalid")
        path = repo / relative
        actual = _sha256_regular_file(path, maximum=MAX_SOURCE_BYTES)
        if actual != expected:
            raise RuntimeError("private_observer_source_mismatch")
        validated[relative] = expected
    return validated


def _sitecustomize_source() -> bytes:
    return (
        "try:\n"
        "    from receipt_verification_observer import _bootstrap_from_private_config\n"
        "    from pathlib import Path\n"
        "    _bootstrap_from_private_config(Path(__file__).with_name("
        f"{CONFIG_FILENAME!r}))\n"
        "except BaseException:\n"
        "    raise SystemExit('private_observer_bootstrap_failed') from None\n"
    ).encode("utf-8")


def prepare_api_observer(
    bootstrap_dir: Path,
    raw_dir: Path,
    repo: Path,
    source_hashes: Mapping[str, str],
    role: str,
    *,
    capture_render_inputs: bool = False,
) -> str:
    """Create one API-only bootstrap and return a child-specific PYTHONPATH.

    The function never mutates this process's environment. The caller applies
    the returned value only to the selected API worker's environment.
    """
    if not _role_is_valid(role) or type(capture_render_inputs) is not bool:
        raise RuntimeError("private_observer_role_invalid")
    repo_path = Path(repo).resolve(strict=True)
    if not repo_path.is_dir() or not (repo_path / "src").is_dir():
        raise RuntimeError("private_observer_repository_invalid")
    hashes = _validate_source_hashes(repo_path, source_hashes)

    helper_source = Path(__file__).resolve(strict=True)
    helper_bytes = _read_regular_file(helper_source, maximum=MAX_HELPER_BYTES)
    helper_hash = hashlib.sha256(helper_bytes).hexdigest()

    bootstrap = Path(bootstrap_dir).absolute()
    raw = Path(raw_dir).absolute()
    _secure_directory(bootstrap.parent)
    _secure_directory(raw, create=True)
    if bootstrap.exists() or bootstrap.is_symlink():
        raise RuntimeError("private_observer_bootstrap_must_be_new")
    bootstrap.mkdir(mode=0o700)
    _secure_directory(bootstrap)

    config = {
        "schema_version": SCHEMA_VERSION,
        "role": role,
        "repo": str(repo_path),
        "raw_dir": str(raw),
        "source_sha256": hashes,
        "helper_sha256": helper_hash,
    }
    if capture_render_inputs:
        config["capture_render_inputs"] = True
    config_bytes = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _write_new_file(bootstrap / HELPER_FILENAME, helper_bytes, maximum=MAX_HELPER_BYTES)
    _write_new_file(bootstrap / CONFIG_FILENAME, config_bytes, maximum=MAX_CONFIG_BYTES)
    _write_new_file(bootstrap / BOOTSTRAP_FILENAME, _sitecustomize_source(), maximum=4096)

    return os.pathsep.join((str(bootstrap), str(repo_path / "src"), str(repo_path)))


class _RenderInputCapture:
    """Bounded private data, never included in the content-free stage ledger."""

    MAX_FILES = 8
    MAX_INPUT_BYTES = 65536
    MAX_RECORD_BYTES = 6 * MAX_INPUT_BYTES + 1024

    def __init__(self, directory: Path, module: Any, attempt_getter: Any) -> None:
        original = getattr(module, "_render_guard_signature", None)
        if not callable(original) or not callable(attempt_getter):
            raise RuntimeError("private_render_capture_hook_invalid")
        self._directory = Path(directory).absolute()
        _secure_directory(self._directory.parent)
        if self._directory.exists() or self._directory.is_symlink():
            raise RuntimeError("private_render_capture_must_be_new")
        self._directory.mkdir(mode=0o700)
        _secure_directory(self._directory)
        self._fd = os.open(
            self._directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        self._directory_identity = (os.fstat(self._fd).st_dev, os.fstat(self._fd).st_ino)
        self._lock = threading.Lock()
        self._closed = False
        self._failed = False
        self._overflow = False
        self._count = 0
        self._threads: dict[int, tuple[int, int]] = {}
        self._module = module
        self._original = original
        self._attempt_getter = attempt_getter

        @functools.wraps(original)
        def wrapped(text: str) -> Any:
            result = original(text)
            try:
                self._record_input(text)
            except BaseException:
                self._failed = True
            return result

        self._wrapped = wrapped
        try:
            module._render_guard_signature = wrapped
        except BaseException:
            os.close(self._fd)
            raise

    def _write(self, name: str, document: Mapping[str, Any]) -> None:
        info = os.fstat(self._fd)
        path_info = self._directory.lstat()
        if (
            (path_info.st_dev, path_info.st_ino) != self._directory_identity
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise RuntimeError("private_render_capture_directory_changed")
        data = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8", "strict"
        )
        if len(data) > self.MAX_RECORD_BYTES:
            raise RuntimeError("private_render_capture_record_too_large")
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=self._fd,
        )
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("private render capture write did not advance")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def _record_input(self, text: str) -> None:
        with self._lock:
            if self._closed:
                return
            if (
                self._count >= self.MAX_FILES
                or type(text) is not str
                or len(text) > self.MAX_INPUT_BYTES
            ):
                self._overflow = True
                return
            if len(text.encode("utf-8", "strict")) > self.MAX_INPUT_BYTES:
                self._overflow = True
                return
            attempt = self._attempt_getter()
            if type(attempt) is not int or attempt not in {0, 1, 2, 3}:
                self._failed = True
                return
            ident = threading.get_ident()
            thread, count = self._threads.get(ident, (len(self._threads) + 1, 0))
            self._write(
                f"input-{self._count + 1:02d}.json",
                {
                    "version": 1,
                    "ordinal": self._count + 1,
                    "attempt_ordinal": attempt,
                    "thread_ordinal": thread,
                    "part_ordinal": count % 2 + 1,
                    "text": text,
                },
            )
            self._threads[ident] = (thread, count + 1)
            self._count += 1

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if getattr(self._module, "_render_guard_signature", None) is self._wrapped:
                    self._module._render_guard_signature = self._original
                else:
                    self._failed = True
                self._write(
                    "seal.json",
                    {
                        "version": 1,
                        "count": self._count,
                        "failed": self._failed,
                        "overflow": self._overflow,
                    },
                )
            finally:
                os.close(self._fd)


class _LedgerWriter:
    """Thread-safe fixed-event writer used only in a selected API worker."""

    def __init__(self, raw_dir: Path, role: str) -> None:
        if not _role_is_valid(role):
            raise RuntimeError("private_observer_role_invalid")
        self._raw_dir = _secure_directory(Path(raw_dir).absolute())
        self._directory = self._raw_dir / role
        self._directory.mkdir(mode=0o700)
        _secure_directory(self._directory)
        self._event_path = self._directory / EVENT_FILENAME
        self._seal_path = self._directory / SEAL_FILENAME
        self._late_path = self._directory / LATE_FILENAME
        if list(self._directory.iterdir()):
            raise RuntimeError("private_observer_ledger_must_be_new")
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        self._fd = os.open(self._event_path, flags, 0o600)
        os.fchmod(self._fd, 0o600)
        self._directory_identity = _identity(self._directory.lstat())
        self._lock = threading.Lock()
        self._thread_local = threading.local()
        self._thread_count = 0
        self._event_count = 0
        self._byte_count = 0
        self._failure_count = 0
        self._overflow = False
        self._failed = False
        self._closed = False
        self._started_ns = time.monotonic_ns()

    def _directory_is_unchanged(self) -> bool:
        try:
            info = self._directory.lstat()
            return (
                stat.S_ISDIR(info.st_mode)
                and not stat.S_ISLNK(info.st_mode)
                and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o700
                and _identity(info) == self._directory_identity
            )
        except OSError:
            return False

    def _note_failure(self, *, overflow: bool = False) -> None:
        self._failed = True
        if self._failure_count < MAX_FAILURE_COUNT:
            self._failure_count += 1
        if overflow:
            self._overflow = True

    def _write_late_marker(self) -> None:
        if not self._directory_is_unchanged():
            self._note_failure()
            return
        try:
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            descriptor = os.open(self._late_path, flags, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except FileExistsError:
            return
        except BaseException:
            self._note_failure()

    def _thread_ordinal(self) -> int | None:
        current = getattr(self._thread_local, "ordinal", None)
        if current is not None:
            return current
        if self._thread_count >= MAX_THREAD_COUNT:
            return None
        self._thread_count += 1
        current = self._thread_count
        self._thread_local.ordinal = current
        return current

    def emit(self, attempt_ordinal: int, stage: str, outcome: str) -> None:
        """Append one fixed event; every sink-side failure is contained."""
        try:
            if (
                type(attempt_ordinal) is not int
                or attempt_ordinal not in {0, 1, 2, 3}
                or type(stage) is not str
                or stage not in _ALLOWED_OUTCOMES
                or type(outcome) is not str
                or outcome not in _ALLOWED_OUTCOMES[stage]
            ):
                with self._lock:
                    self._note_failure()
                return
            with self._lock:
                if self._closed:
                    self._write_late_marker()
                    return
                if not self._directory_is_unchanged():
                    self._note_failure()
                    return
                if self._event_count >= MAX_EVENTS or self._byte_count >= MAX_LEDGER_BYTES:
                    self._note_failure(overflow=True)
                    return
                ordinal = self._thread_ordinal()
                if ordinal is None:
                    self._note_failure(overflow=True)
                    return
                event = {
                    "version": SCHEMA_VERSION,
                    "sequence": self._event_count + 1,
                    "attempt_ordinal": attempt_ordinal,
                    "thread_ordinal": ordinal,
                    "monotonic_ms": max(0, (time.monotonic_ns() - self._started_ns) // 1_000_000),
                    "stage": stage,
                    "outcome": outcome,
                }
                encoded = (
                    json.dumps(event, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
                )
                if (
                    len(encoded) > MAX_EVENT_BYTES
                    or self._byte_count + len(encoded) > MAX_LEDGER_BYTES
                ):
                    self._note_failure(overflow=True)
                    return
                view = memoryview(encoded)
                while view:
                    written = os.write(self._fd, view)
                    if written <= 0:
                        raise OSError("private observer write did not advance")
                    view = view[written:]
                self._event_count += 1
                self._byte_count += len(encoded)
        except BaseException:
            try:
                with self._lock:
                    self._note_failure()
            except BaseException:
                return

    def _write_seal(self) -> None:
        seal = {
            "version": SCHEMA_VERSION,
            "event_count": self._event_count,
            "byte_count": self._byte_count,
            "thread_count": self._thread_count,
            "failed_callback_count": self._failure_count,
            "failed": self._failed,
            "overflow": self._overflow,
        }
        data = json.dumps(seal, sort_keys=True, separators=(",", ":")).encode("ascii")
        if len(data) > 2048 or not self._directory_is_unchanged():
            raise RuntimeError("private_observer_seal_invalid")
        temporary = self._directory / f".seal-{os.getpid()}-{threading.get_ident()}"
        _write_new_file(temporary, data, maximum=2048)
        try:
            os.link(
                temporary,
                self._seal_path,
                follow_symlinks=False,
            )
            os.unlink(temporary)
        except BaseException:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise
        directory_fd = os.open(
            self._directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        self._directory_identity = _identity(self._directory.lstat())

    def close(self) -> None:
        """Seal once; a later callback leaves a durable incomplete marker."""
        try:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                try:
                    os.fsync(self._fd)
                finally:
                    os.close(self._fd)
                self._write_seal()
        except BaseException:
            self._failed = True


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_strict_json(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)


def _read_private_config(path: Path) -> dict[str, Any]:
    config_bytes = _read_regular_file(path, maximum=MAX_CONFIG_BYTES, mode=0o600)
    value = _load_strict_json(config_bytes)
    if type(value) is not dict or set(value) - {"capture_render_inputs"} != {
        "schema_version",
        "role",
        "repo",
        "raw_dir",
        "source_sha256",
        "helper_sha256",
    }:
        raise RuntimeError("private_observer_config_invalid")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or not _role_is_valid(value["role"])
        or type(value["repo"]) is not str
        or type(value["raw_dir"]) is not str
        or type(value["helper_sha256"]) is not str
        or _SHA256_PATTERN.fullmatch(value["helper_sha256"]) is None
        or ("capture_render_inputs" in value and type(value["capture_render_inputs"]) is not bool)
    ):
        raise RuntimeError("private_observer_config_invalid")
    return value


def _validate_imported_sources(repo: Path, source_hashes: Mapping[str, str]) -> dict[str, Any]:
    validated_modules: dict[str, Any] = {}
    for module_name, relative in SOURCE_MODULES.items():
        expected_path = repo / relative
        expected_hash = source_hashes[relative]
        module = importlib.import_module(module_name)
        imported_file = getattr(module, "__file__", None)
        if type(imported_file) is not str:
            raise RuntimeError("private_observer_import_path_invalid")
        imported_path = Path(imported_file)
        if imported_path.suffix != ".py" or not os.path.samefile(imported_path, expected_path):
            raise RuntimeError("private_observer_import_path_mismatch")
        if _sha256_regular_file(imported_path, maximum=MAX_SOURCE_BYTES) != expected_hash:
            raise RuntimeError("private_observer_import_hash_mismatch")
        validated_modules[module_name] = module

    diagnostics = validated_modules["cli_agent_orchestrator.utils.receipt_verification_diagnostics"]
    terminal_service = validated_modules["cli_agent_orchestrator.services.terminal_service"]
    if getattr(terminal_service, "set_receipt_verification_diagnostic_sink", None) is not getattr(
        diagnostics, "set_receipt_verification_diagnostic_sink", None
    ):
        raise RuntimeError("private_observer_setter_identity_mismatch")
    return validated_modules


def _restore_uvicorn_shutdown_hook(binding: tuple[Any, Any, Any] | None) -> None:
    if binding is None:
        return
    server_type, original, wrapper = binding
    try:
        if getattr(server_type, "_serve", None) is wrapper:
            server_type._serve = original
    except BaseException:
        return


def _install_uvicorn_shutdown_hook(finish: Any) -> tuple[Any, Any, Any]:
    """Finish before Uvicorn re-raises captured shutdown signals from serve()."""
    import uvicorn

    server_type = uvicorn.Server
    original = getattr(server_type, "_serve", None)
    if not callable(original) or not inspect.iscoroutinefunction(original):
        raise RuntimeError("private_observer_server_lifecycle_unavailable")

    @functools.wraps(original)
    async def wrapped(server: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await original(server, *args, **kwargs)
        finally:
            try:
                finish()
            except BaseException:
                pass

    server_type._serve = wrapped
    if getattr(server_type, "_serve", None) is not wrapped:
        raise RuntimeError("private_observer_server_lifecycle_unavailable")
    return server_type, original, wrapped


def _bootstrap_from_private_config(config_path: Path) -> None:
    """Validate and install one selected fixture observer or fail startup closed."""
    writer: _LedgerWriter | None = None
    previous_sink: Any = None
    setter: Any = None
    sink_installed = False
    hook_binding: tuple[Any, Any, Any] | None = None
    finish_lock = threading.Lock()
    finished = False
    render_capture: _RenderInputCapture | None = None
    try:
        bootstrap = _secure_directory(Path(__file__).resolve(strict=True).parent)
        if Path(config_path).resolve(strict=True).parent != bootstrap:
            raise RuntimeError("private_observer_config_location_invalid")
        config = _read_private_config(Path(config_path))
        helper_path = bootstrap / HELPER_FILENAME
        if _sha256_regular_file(helper_path, maximum=MAX_HELPER_BYTES) != config["helper_sha256"]:
            raise RuntimeError("private_observer_helper_mismatch")
        repo = Path(config["repo"]).resolve(strict=True)
        raw_dir = Path(config["raw_dir"]).absolute()
        if not repo.is_dir() or not raw_dir.is_dir():
            raise RuntimeError("private_observer_root_invalid")
        _secure_directory(raw_dir)
        hashes = config["source_sha256"]
        if type(hashes) is not dict or set(hashes) != set(SOURCE_MODULES.values()):
            raise RuntimeError("private_observer_source_set_invalid")
        normalized_hashes = _validate_source_hashes(repo, hashes)
        modules = _validate_imported_sources(repo, normalized_hashes)
        diagnostics = modules["cli_agent_orchestrator.utils.receipt_verification_diagnostics"]
        setter = modules[
            "cli_agent_orchestrator.services.terminal_service"
        ].set_receipt_verification_diagnostic_sink
        if diagnostics.receipt_verification_diagnostics_enabled():
            raise RuntimeError("private_observer_sink_already_installed")
        writer = _LedgerWriter(raw_dir, config["role"])
        previous_sink = setter(writer.emit)
        sink_installed = True
        if config.get("capture_render_inputs") is True:
            capture_parent = _secure_directory(raw_dir / "render-inputs", create=True)
            render_capture = _RenderInputCapture(
                capture_parent / config["role"],
                modules["cli_agent_orchestrator.providers.opencode_cli"],
                diagnostics.current_receipt_verification_attempt,
            )

        def finish() -> None:
            nonlocal finished
            with finish_lock:
                if finished:
                    return
                finished = True
            _restore_uvicorn_shutdown_hook(hook_binding)
            if render_capture is not None:
                try:
                    render_capture.close()
                except BaseException:
                    pass
            try:
                setter(previous_sink)
            except BaseException:
                pass
            if writer is not None:
                try:
                    writer.close()
                except BaseException:
                    pass

        hook_binding = _install_uvicorn_shutdown_hook(finish)
        atexit.register(finish)
        os.environ["PYTHONPATH"] = os.pathsep.join((str(repo / "src"), str(repo)))
    except BaseException:
        _restore_uvicorn_shutdown_hook(hook_binding)
        if render_capture is not None:
            try:
                render_capture.close()
            except BaseException:
                pass
        if sink_installed and setter is not None:
            try:
                setter(previous_sink)
            except BaseException:
                pass
        if writer is not None:
            try:
                with writer._lock:
                    writer._note_failure()
                writer.close()
            except BaseException:
                pass
        raise RuntimeError("private_observer_bootstrap_failed") from None


def _zero_counts() -> dict[str, dict[str, int]]:
    return {
        stage: {outcome: 0 for outcome in sorted(outcomes)}
        for stage, outcomes in sorted(_ALLOWED_OUTCOMES.items())
    }


def _unavailable(role: str | None) -> dict[str, Any]:
    return {
        "observer_status": "unavailable",
        "role": role if role in {"api_a", "api_b"} else "unknown",
        "event_count": None,
        "thread_count": None,
        "failed_event_count": None,
        "stage_outcome_counts": None,
    }


def _read_ledger_file(path: Path, maximum: int) -> tuple[bytes, os.stat_result]:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_size > maximum
    ):
        raise RuntimeError("private_observer_ledger_file_invalid")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise RuntimeError("private_observer_ledger_file_changed")
        content = bytearray()
        while len(content) <= maximum:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        after_fd = os.fstat(descriptor)
        after_path = path.lstat()
        if (
            len(content) > maximum
            or _identity(after_fd) != _identity(opened)
            or _identity(after_path) != _identity(opened)
        ):
            raise RuntimeError("private_observer_ledger_file_changed")
        return bytes(content), opened
    finally:
        os.close(descriptor)


def _validate_event(row: Any, sequence: int) -> tuple[int, int, str, str]:
    if type(row) is not dict or set(row) != {
        "version",
        "sequence",
        "attempt_ordinal",
        "thread_ordinal",
        "monotonic_ms",
        "stage",
        "outcome",
    }:
        raise RuntimeError("private_observer_event_invalid")
    integer_fields = ("version", "sequence", "attempt_ordinal", "thread_ordinal", "monotonic_ms")
    if any(type(row[field]) is not int for field in integer_fields):
        raise RuntimeError("private_observer_event_invalid")
    if (
        row["version"] != SCHEMA_VERSION
        or row["sequence"] != sequence
        or row["attempt_ordinal"] not in {0, 1, 2, 3}
        or not 1 <= row["thread_ordinal"] <= MAX_THREAD_COUNT
        or row["monotonic_ms"] < 0
        or type(row["stage"]) is not str
        or type(row["outcome"]) is not str
        or row["stage"] not in _ALLOWED_OUTCOMES
        or row["outcome"] not in _ALLOWED_OUTCOMES[row["stage"]]
    ):
        raise RuntimeError("private_observer_event_invalid")
    return row["thread_ordinal"], row["monotonic_ms"], row["stage"], row["outcome"]


def _read_complete_summary(role_dir: Path) -> dict[str, Any]:
    _secure_directory(role_dir)
    directory_before = role_dir.lstat()
    expected_entries = {EVENT_FILENAME, SEAL_FILENAME}
    if {entry.name for entry in role_dir.iterdir()} != expected_entries:
        raise RuntimeError("private_observer_ledger_entries_invalid")
    event_bytes, event_stat = _read_ledger_file(role_dir / EVENT_FILENAME, MAX_LEDGER_BYTES)
    seal_bytes, seal_stat = _read_ledger_file(role_dir / SEAL_FILENAME, 2048)
    seal = _load_strict_json(seal_bytes)
    if type(seal) is not dict or set(seal) != {
        "version",
        "event_count",
        "byte_count",
        "thread_count",
        "failed_callback_count",
        "failed",
        "overflow",
    }:
        raise RuntimeError("private_observer_seal_invalid")
    for field in ("version", "event_count", "byte_count", "thread_count", "failed_callback_count"):
        if type(seal[field]) is not int:
            raise RuntimeError("private_observer_seal_invalid")
    if type(seal["failed"]) is not bool or type(seal["overflow"]) is not bool:
        raise RuntimeError("private_observer_seal_invalid")
    if (
        seal["version"] != SCHEMA_VERSION
        or seal["event_count"] < 0
        or seal["event_count"] > MAX_EVENTS
        or seal["byte_count"] != len(event_bytes)
        or seal["thread_count"] < 0
        or seal["thread_count"] > MAX_THREAD_COUNT
        or seal["failed_callback_count"] < 0
        or seal["failed_callback_count"] > MAX_FAILURE_COUNT
    ):
        raise RuntimeError("private_observer_seal_invalid")

    lines = event_bytes.splitlines()
    if len(lines) != seal["event_count"] or (event_bytes and not event_bytes.endswith(b"\n")):
        raise RuntimeError("private_observer_event_count_invalid")
    counts = _zero_counts()
    thread_ordinals: set[int] = set()
    last_monotonic = -1
    for sequence, line in enumerate(lines, start=1):
        if not line or len(line) + 1 > MAX_EVENT_BYTES:
            raise RuntimeError("private_observer_event_size_invalid")
        thread_ordinal, monotonic_ms, stage, outcome = _validate_event(
            _load_strict_json(line), sequence
        )
        if monotonic_ms < last_monotonic:
            raise RuntimeError("private_observer_event_order_invalid")
        last_monotonic = monotonic_ms
        thread_ordinals.add(thread_ordinal)
        counts[stage][outcome] += 1
    if len(thread_ordinals) != seal["thread_count"] or thread_ordinals != set(
        range(1, seal["thread_count"] + 1)
    ):
        raise RuntimeError("private_observer_thread_count_invalid")
    directory_after = role_dir.lstat()
    if (
        _identity(directory_after) != _identity(directory_before)
        or _identity((role_dir / EVENT_FILENAME).lstat()) != _identity(event_stat)
        or _identity((role_dir / SEAL_FILENAME).lstat()) != _identity(seal_stat)
        or {entry.name for entry in role_dir.iterdir()} != expected_entries
    ):
        raise RuntimeError("private_observer_ledger_changed")
    if seal["failed"] or seal["overflow"] or seal["failed_callback_count"] != 0:
        raise RuntimeError("private_observer_capture_incomplete")
    return {
        "observer_status": "complete",
        "event_count": seal["event_count"],
        "thread_count": seal["thread_count"],
        "failed_event_count": seal["failed_callback_count"],
        "stage_outcome_counts": counts,
    }


def read_api_observation(raw_dir: Path, *, role: str, writers_dead: bool) -> dict[str, Any]:
    """Return a strict aggregate only after the API writer process has exited."""
    if not _role_is_valid(role) or type(writers_dead) is not bool or not writers_dead:
        return _unavailable(role if _role_is_valid(role) else None)
    try:
        raw = _secure_directory(Path(raw_dir).absolute())
        role_dir = raw / role
        summary = _read_complete_summary(role_dir)
        return {"role": role, **summary}
    except BaseException:
        return _unavailable(role)


__all__ = [
    "BOOTSTRAP_FILENAME",
    "CONFIG_FILENAME",
    "EVENT_FILENAME",
    "HELPER_FILENAME",
    "MAX_EVENTS",
    "MAX_LEDGER_BYTES",
    "SEAL_FILENAME",
    "_install_uvicorn_shutdown_hook",
    "prepare_api_observer",
    "read_api_observation",
]
