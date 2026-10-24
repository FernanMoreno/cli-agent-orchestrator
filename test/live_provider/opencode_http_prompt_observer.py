"""Install and project the private diagnostic-only OpenCode HTTP prompt observer."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

MAX_PLUGIN_BYTES = 128 * 1024
MAX_EXPECTATION_BYTES = 64 * 1024
MAX_RECORD_BYTES = 4096
MAX_SEAL_BYTES = 2048
MAX_LEDGER_BYTES = 72 * 1024
MAX_REQUESTS = 16
MAX_PHASE_BYTES = 8192
MAX_PREFIX_BYTES = 8192
MAX_SUFFIX_BYTES = 16384
MAX_LAST_INSTRUCTION_BYTES = 4096
NONCE_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
RECEIPT_PATTERN = re.compile(r"CAO-TURN-RECEIPT-[0-9a-f]{32}\Z")
RECORD_NAME_PATTERN = re.compile(r"request-([0-9]{4})\.json\Z")
EXPECTATION_FIELDS = {
    "schema_version",
    "nonce",
    "phase_prompts",
    "receipt_prefix",
    "receipt_suffix",
    "last_instruction",
}
RECORD_FIELDS = {
    "schema_version",
    "nonce",
    "ordinal",
    "status",
    "phase_ordinal",
    "source_prompt_exact",
    "last_instruction_present",
    "receipt_envelope_shape_valid",
}
SEAL_FIELDS = {
    "schema_version",
    "nonce",
    "hook_disposed",
    "trace_complete",
    "request_count",
    "durable_record_count",
    "overflow",
}
STATUSES = {
    "observed",
    "model_mismatch",
    "non_primary",
    "request_kind_unknown",
    "expectations_unavailable",
    "body_invalid",
    "body_oversized",
    "body_unreadable",
    "message_shape_unavailable",
    "receipt_envelope_unavailable",
    "observer_error",
}


def _private_directory(path: Path, *, create: bool = False) -> Path:
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or path.is_symlink()
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or path.resolve(strict=True) != path.absolute()
    ):
        raise RuntimeError("private_observer_directory_required")
    return path


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _read_private_regular_file(path: Path, *, maximum: int) -> bytes:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or path.is_symlink()
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_size > maximum
    ):
        raise RuntimeError("private_observer_file_invalid")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
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
        after_descriptor = os.fstat(descriptor)
        after_path = path.lstat()
        if (
            len(data) > maximum
            or len(data) != opened.st_size
            or _identity(after_descriptor) != _identity(opened)
            or _identity(after_path) != _identity(opened)
        ):
            raise RuntimeError("private_observer_file_changed")
        return data
    finally:
        os.close(descriptor)


def _read_source_file(path: Path, *, maximum: int) -> bytes:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or path.is_symlink()
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or before.st_size > maximum
    ):
        raise RuntimeError("private_observer_source_invalid")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise RuntimeError("private_observer_source_changed")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after_descriptor = os.fstat(descriptor)
        after_path = path.lstat()
        if (
            len(data) > maximum
            or len(data) != opened.st_size
            or _identity(after_descriptor) != _identity(opened)
            or _identity(after_path) != _identity(opened)
        ):
            raise RuntimeError("private_observer_source_changed")
        return data
    finally:
        os.close(descriptor)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_dev != current.st_dev
            or opened.st_ino != current.st_ino
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o700
        ):
            raise RuntimeError("private_observer_directory_changed")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_expectations(document: Any) -> dict[str, Any]:
    if (
        type(document) is not dict
        or set(document) != EXPECTATION_FIELDS
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or not isinstance(document.get("nonce"), str)
        or NONCE_PATTERN.fullmatch(document["nonce"]) is None
        or type(document.get("phase_prompts")) is not list
        or len(document["phase_prompts"]) != 2
        or not isinstance(document.get("receipt_prefix"), str)
        or not isinstance(document.get("receipt_suffix"), str)
        or not isinstance(document.get("last_instruction"), str)
    ):
        raise RuntimeError("private_observer_expectation_shape_invalid")
    first, second = document["phase_prompts"]
    prefix = document["receipt_prefix"]
    suffix = document["receipt_suffix"]
    last = document["last_instruction"]
    if (
        not isinstance(first, str)
        or not isinstance(second, str)
        or not first
        or not second
        or first == second
        or len(first.encode("utf-8")) > MAX_PHASE_BYTES
        or len(second.encode("utf-8")) > MAX_PHASE_BYTES
        or not prefix
        or len(prefix.encode("utf-8")) > MAX_PREFIX_BYTES
        or not suffix
        or len(suffix.encode("utf-8")) > MAX_SUFFIX_BYTES
        or not last
        or len(last.encode("utf-8")) > MAX_LAST_INSTRUCTION_BYTES
        or last not in first
        or last not in second
    ):
        raise RuntimeError("private_observer_expectation_value_invalid")
    return document


def install_project_plugin(working_directory: Path, source_path: Path, nonce: str) -> Path:
    """Install a private copy of the HTTP observer and return its ledger path."""
    if not isinstance(nonce, str) or NONCE_PATTERN.fullmatch(nonce) is None:
        raise RuntimeError("private_observer_nonce_invalid")
    working_directory = Path(working_directory)
    root_info = working_directory.lstat()
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or working_directory.is_symlink()
        or root_info.st_uid != os.getuid()
        or stat.S_IMODE(root_info.st_mode) != 0o700
        or working_directory.resolve(strict=True) != working_directory.absolute()
    ):
        raise RuntimeError("private_observer_workdir_required")
    source = _read_source_file(Path(source_path), maximum=MAX_PLUGIN_BYTES)
    source_digest = hashlib.sha256(source).digest()
    opencode_directory = _private_directory(working_directory / ".opencode", create=True)
    _sync_directory(working_directory)
    plugins_directory = _private_directory(opencode_directory / "plugins", create=True)
    _sync_directory(opencode_directory)
    ledger_directory = opencode_directory / "http-prompt-observation"
    try:
        ledger_directory.lstat()
    except FileNotFoundError:
        pass
    else:
        raise RuntimeError("private_observer_ledger_already_exists")
    destination = plugins_directory / "cao-http-prompt-observer.js"
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError:
        raise RuntimeError("private_observer_plugin_already_exists") from None
    try:
        with os.fdopen(descriptor, "wb") as writer:
            descriptor = -1
            writer.write(source)
            writer.flush()
            os.fchmod(writer.fileno(), 0o600)
            os.fsync(writer.fileno())
        _sync_directory(plugins_directory)
        installed = _read_private_regular_file(destination, maximum=MAX_PLUGIN_BYTES)
        if hashlib.sha256(installed).digest() != source_digest:
            raise RuntimeError("private_observer_copy_mismatch")
    except BaseException:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            destination.unlink(missing_ok=True)
            _sync_directory(plugins_directory)
        except (OSError, RuntimeError):
            pass
        raise
    return ledger_directory


def write_expectations(working_directory: Path, document: dict[str, Any]) -> Path:
    """Create the one private request-time expectation document without replacement."""
    working_directory = Path(working_directory)
    _private_directory(working_directory)
    opencode_directory = _private_directory(working_directory / ".opencode")
    validated = _validate_expectations(document)
    encoded = json.dumps(validated, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_EXPECTATION_BYTES:
        raise RuntimeError("private_observer_expectation_too_large")
    destination = opencode_directory / "http-prompt-expectations.json"
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError:
        raise RuntimeError("private_observer_expectation_already_exists") from None
    try:
        with os.fdopen(descriptor, "wb") as writer:
            descriptor = -1
            writer.write(encoded)
            writer.flush()
            os.fchmod(writer.fileno(), 0o600)
            os.fsync(writer.fileno())
        result = _read_private_regular_file(destination, maximum=MAX_EXPECTATION_BYTES)
        if result != encoded:
            raise RuntimeError("private_observer_expectation_copy_mismatch")
        _sync_directory(opencode_directory)
    except BaseException:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            destination.unlink(missing_ok=True)
            _sync_directory(opencode_directory)
        except (OSError, RuntimeError):
            pass
        raise
    return destination


def _object_pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError("invalid_json_constant")


def _read_json(path: Path, *, maximum: int) -> Any:
    raw = _read_private_regular_file(path, maximum=maximum)
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_object_pairs_without_duplicates,
        parse_constant=_reject_json_constant,
    )


def _unavailable_summary() -> dict[str, Any]:
    return {
        "observer_status": "unavailable",
        "trace_complete": False,
        "overflow": False,
        "request_count": 0,
        "durable_record_count": 0,
        "phase_observation": "unavailable",
        "records": [],
    }


def _valid_record(value: Any, *, nonce: str, expected_ordinal: int) -> dict[str, Any] | None:
    if type(value) is not dict or set(value) != RECORD_FIELDS:
        return None
    ordinal = value.get("ordinal")
    phase = value.get("phase_ordinal")
    status = value.get("status")
    source_exact = value.get("source_prompt_exact")
    last_present = value.get("last_instruction_present")
    envelope_shape = value.get("receipt_envelope_shape_valid")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("nonce") != nonce
        or type(ordinal) is not int
        or ordinal != expected_ordinal
        or not isinstance(status, str)
        or status not in STATUSES
        or (phase is not None and (type(phase) is not int or phase not in {1, 2}))
        or type(source_exact) is not bool
        or type(last_present) is not bool
        or type(envelope_shape) is not bool
    ):
        return None
    if (phase is not None) != source_exact or (phase is not None and not last_present):
        return None
    if status == "observed":
        if not envelope_shape:
            return None
    elif phase is not None or source_exact or last_present or envelope_shape:
        return None
    return {
        "ordinal": ordinal,
        "status": status,
        "phase_ordinal": phase,
        "source_prompt_exact": source_exact,
        "last_instruction_present": last_present,
        "receipt_envelope_shape_valid": envelope_shape,
    }


def _valid_seal(value: Any, *, nonce: str, record_count: int) -> dict[str, Any] | None:
    if type(value) is not dict or set(value) != SEAL_FIELDS:
        return None
    request_count = value.get("request_count")
    durable_count = value.get("durable_record_count")
    hook_disposed = value.get("hook_disposed")
    trace_complete = value.get("trace_complete")
    overflow = value.get("overflow")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("nonce") != nonce
        or type(hook_disposed) is not bool
        or type(trace_complete) is not bool
        or type(overflow) is not bool
        or type(request_count) is not int
        or not 0 <= request_count <= MAX_REQUESTS
        or type(durable_count) is not int
        or not 0 <= durable_count <= MAX_REQUESTS
        or request_count != record_count
        or durable_count != record_count
        or (trace_complete and (not hook_disposed or overflow or request_count != durable_count))
    ):
        return None
    return {
        "trace_complete": trace_complete,
        "overflow": overflow,
        "request_count": request_count,
        "durable_record_count": durable_count,
    }


def _phase_observation(records: list[dict[str, Any]]) -> str:
    phases = [record["phase_ordinal"] for record in records if record["phase_ordinal"] is not None]
    unique_phases = set(phases)
    if unique_phases == {1, 2}:
        return "both"
    if unique_phases == {1}:
        return "prefix"
    if not unique_phases:
        return "none"
    return "other"


def _bounded_ledger_names(path: Path) -> list[str]:
    names: list[str] = []
    with os.scandir(path) as entries:
        for entry in entries:
            if len(names) >= MAX_REQUESTS + 1:
                raise RuntimeError("private_observer_record_count_invalid")
            names.append(entry.name)
    return sorted(names)


def _validate_ledger(path: Path, expected_nonce: str) -> dict[str, Any]:
    if not isinstance(expected_nonce, str) or NONCE_PATTERN.fullmatch(expected_nonce) is None:
        raise RuntimeError("private_observer_nonce_invalid")
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or path.is_symlink()
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or path.resolve(strict=True) != path.absolute()
    ):
        raise RuntimeError("private_observer_ledger_invalid")
    directory_identity = _identity(info)
    names = _bounded_ledger_names(path)
    if "seal.json" not in names:
        raise RuntimeError("private_observer_seal_missing")
    record_names = [name for name in names if RECORD_NAME_PATTERN.fullmatch(name)]
    if len(record_names) > MAX_REQUESTS:
        raise RuntimeError("private_observer_record_count_invalid")
    if set(names) != set(record_names) | {"seal.json"}:
        raise RuntimeError("private_observer_ledger_entries_invalid")
    expected_names = [f"request-{ordinal:04d}.json" for ordinal in range(1, len(record_names) + 1)]
    if record_names != expected_names:
        raise RuntimeError("private_observer_record_order_invalid")
    total_bytes = 0
    for name in names:
        file_path = path / name
        file_info = file_path.lstat()
        maximum = MAX_SEAL_BYTES if name == "seal.json" else MAX_RECORD_BYTES
        if (
            not stat.S_ISREG(file_info.st_mode)
            or file_path.is_symlink()
            or file_info.st_uid != os.getuid()
            or file_info.st_nlink != 1
            or stat.S_IMODE(file_info.st_mode) != 0o600
            or file_info.st_size > maximum
        ):
            raise RuntimeError("private_observer_file_invalid")
        total_bytes += file_info.st_size
    if total_bytes > MAX_LEDGER_BYTES:
        raise RuntimeError("private_observer_ledger_too_large")
    _private_directory(path)
    records: list[dict[str, Any]] = []
    for ordinal, name in enumerate(record_names, start=1):
        raw_record = _read_json(path / name, maximum=MAX_RECORD_BYTES)
        record = _valid_record(raw_record, nonce=expected_nonce, expected_ordinal=ordinal)
        if record is None:
            raise RuntimeError("private_observer_record_invalid")
        records.append(record)
    raw_seal = _read_json(path / "seal.json", maximum=MAX_SEAL_BYTES)
    seal = _valid_seal(raw_seal, nonce=expected_nonce, record_count=len(records))
    if seal is None:
        raise RuntimeError("private_observer_seal_invalid")
    final_info = path.lstat()
    if _identity(final_info) != directory_identity or _bounded_ledger_names(path) != names:
        raise RuntimeError("private_observer_ledger_changed")
    if not seal["trace_complete"]:
        return {
            "observer_status": "incomplete",
            "trace_complete": False,
            "overflow": seal["overflow"],
            "request_count": seal["request_count"],
            "durable_record_count": seal["durable_record_count"],
            "phase_observation": "none",
            "records": [],
        }
    phases = _phase_observation(records)
    return {
        "observer_status": "observed",
        "trace_complete": True,
        "overflow": seal["overflow"],
        "request_count": seal["request_count"],
        "durable_record_count": seal["durable_record_count"],
        "phase_observation": phases,
        "records": records,
    }


def read_observation(path: Path, expected_nonce: str) -> dict[str, Any]:
    """Read a sealed, owner-only ledger and return only fixed prompt diagnostics."""
    try:
        return _validate_ledger(Path(path), expected_nonce)
    except (
        OSError,
        RuntimeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ):
        return _unavailable_summary()
