"""Install and safely project the private OpenCode outbound-tool observation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

MAX_PLUGIN_BYTES = 128 * 1024
MAX_RECORD_BYTES = 4096
MAX_SEAL_BYTES = 2048
MAX_LEDGER_BYTES = 72 * 1024
MAX_REQUESTS = 16
MAX_TOOLS = 256
NONCE_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
RECORD_FILENAME_PATTERN = re.compile(r"request-([0-9]{4})\.json\Z")

TOOL_NAMES = (
    "cao-mcp-server_cao_ask_user",
    "cao-mcp-server_cao_wait_for_user_answers",
    "cao-mcp-server_cao_acknowledge_user_answers",
)
REQUEST_KINDS = ("primary", "compaction", "title", "generate", "unknown")
REQUEST_STATUSES = (
    "observed",
    "valid_empty",
    "model_mismatch",
    "non_primary",
    "request_kind_unknown",
    "tools_absent",
    "tools_invalid",
    "body_invalid",
    "body_oversized",
    "body_unreadable",
    "observer_error",
)
SUMMARY_FIELDS = {
    "observer_status",
    "tool_offer_status",
    "nonce",
    "hook_disposed",
    "trace_complete",
    "overflow",
    "request_count",
    "durable_record_count",
    "target_primary_request_count",
    "valid_target_primary_request_count",
    "request_status_counts",
    "tool_name_counts",
    "other_tool_name_count",
    "requests",
}


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or path.is_symlink()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_mode & 0o700 != 0o700
    ):
        raise RuntimeError("private_observer_directory_required")
    return path


def _fsync_directory(path: Path) -> None:
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
            or opened.st_mode & 0o077
        ):
            raise RuntimeError("private_observer_directory_changed")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _read_pinned_regular_file(path: Path, *, maximum: int) -> bytes:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or path.is_symlink()
        or before.st_uid != os.getuid()
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
        after_fd = os.fstat(descriptor)
        after_path = path.lstat()
        if (
            len(data) > maximum
            or _identity(after_fd) != _identity(opened)
            or _identity(after_path) != _identity(opened)
        ):
            raise RuntimeError("private_observer_source_changed")
        return data
    finally:
        os.close(descriptor)


def install_project_plugin(*, working_directory: Path, source_path: Path, nonce: str) -> Path:
    """Install one source-pinned plugin and return its expected private ledger directory."""
    if not isinstance(nonce, str) or NONCE_PATTERN.fullmatch(nonce) is None:
        raise RuntimeError("private_observer_nonce_invalid")
    root_info = working_directory.lstat()
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or working_directory.is_symlink()
        or root_info.st_uid != os.getuid()
        or root_info.st_mode & 0o077
    ):
        raise RuntimeError("private_observer_workdir_required")
    data = _read_pinned_regular_file(source_path, maximum=MAX_PLUGIN_BYTES)
    source_digest = hashlib.sha256(data).digest()
    opencode_directory = _private_directory(working_directory / ".opencode")
    _fsync_directory(working_directory)
    plugins_directory = _private_directory(opencode_directory / "plugins")
    _fsync_directory(opencode_directory)
    ledger_directory = opencode_directory / "http-tool-offer-observation"
    try:
        ledger_directory.lstat()
    except FileNotFoundError:
        pass
    else:
        raise RuntimeError("private_observer_ledger_already_exists")
    destination = plugins_directory / "cao-http-tool-offer-observer.js"
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as writer:
            descriptor = -1
            writer.write(data)
            writer.flush()
            os.fchmod(writer.fileno(), 0o600)
            os.fsync(writer.fileno())
        os.chmod(destination, 0o600)
        _fsync_directory(plugins_directory)
        installed = _read_pinned_regular_file(destination, maximum=MAX_PLUGIN_BYTES)
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
        except OSError:
            pass
        try:
            _fsync_directory(plugins_directory)
        except (OSError, RuntimeError):
            pass
        raise
    return ledger_directory


def _object_pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _valid_record(value: Any) -> dict[str, Any] | None:
    fields = {
        "schema_version",
        "nonce",
        "ordinal",
        "kind",
        "model_match",
        "status",
        "tool_count",
        "name_counts",
        "other_name_count",
    }
    if not isinstance(value, dict) or set(value) != fields:
        return None
    nonce = value.get("nonce")
    ordinal = value.get("ordinal")
    kind = value.get("kind")
    model_match = value.get("model_match")
    status = value.get("status")
    tool_count = value.get("tool_count")
    name_counts = value.get("name_counts")
    other_name_count = value.get("other_name_count")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or not isinstance(nonce, str)
        or NONCE_PATTERN.fullmatch(nonce) is None
        or type(ordinal) is not int
        or not 1 <= ordinal <= MAX_REQUESTS
        or kind not in REQUEST_KINDS
        or type(model_match) is not bool
        or status not in REQUEST_STATUSES
    ):
        return None
    if status in {"observed", "valid_empty"}:
        if (
            not model_match
            or kind != "primary"
            or type(tool_count) is not int
            or not 0 <= tool_count <= MAX_TOOLS
            or not isinstance(name_counts, dict)
            or set(name_counts) != set(TOOL_NAMES)
            or type(other_name_count) is not int
            or not 0 <= other_name_count <= MAX_TOOLS
        ):
            return None
        if any(
            type(name_counts[name]) is not int or not 0 <= name_counts[name] <= MAX_TOOLS
            for name in TOOL_NAMES
        ):
            return None
        if sum(name_counts.values()) + other_name_count != tool_count:
            return None
        if (status == "valid_empty") != (tool_count == 0):
            return None
    elif tool_count is not None or name_counts is not None or other_name_count is not None:
        return None
    if status == "model_mismatch" and model_match:
        return None
    if status == "non_primary" and (
        not model_match or kind not in {"compaction", "title", "generate"}
    ):
        return None
    if status == "request_kind_unknown" and (not model_match or kind != "unknown"):
        return None
    if status in {
        "tools_absent",
        "tools_invalid",
        "body_invalid",
        "body_oversized",
        "body_unreadable",
    } and (not model_match or kind != "primary"):
        return None
    return {
        "schema_version": 1,
        "nonce": nonce,
        "ordinal": ordinal,
        "kind": kind,
        "model_match": model_match,
        "status": status,
        "tool_count": tool_count,
        "name_counts": dict(name_counts) if isinstance(name_counts, dict) else None,
        "other_name_count": other_name_count,
    }


def _valid_seal(value: Any) -> dict[str, Any] | None:
    fields = {
        "schema_version",
        "nonce",
        "hook_disposed",
        "trace_complete",
        "record_count",
        "durable_record_count",
        "overflow",
    }
    if not isinstance(value, dict) or set(value) != fields:
        return None
    record_count = value.get("record_count")
    durable_count = value.get("durable_record_count")
    hook_disposed = value.get("hook_disposed")
    trace_complete = value.get("trace_complete")
    overflow = value.get("overflow")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or not isinstance(value.get("nonce"), str)
        or NONCE_PATTERN.fullmatch(value["nonce"]) is None
        or type(hook_disposed) is not bool
        or type(trace_complete) is not bool
        or type(record_count) is not int
        or not 0 <= record_count <= MAX_REQUESTS
        or type(durable_count) is not int
        or not 0 <= durable_count <= record_count
        or type(overflow) is not bool
    ):
        return None
    if trace_complete and (not hook_disposed or overflow or durable_count != record_count):
        return None
    return {
        "nonce": value["nonce"],
        "hook_disposed": hook_disposed,
        "trace_complete": trace_complete,
        "record_count": record_count,
        "durable_record_count": durable_count,
        "overflow": overflow,
    }


def _empty_counts() -> dict[str, int]:
    return {name: 0 for name in REQUEST_STATUSES}


def _empty_tools() -> dict[str, int]:
    return {name: 0 for name in TOOL_NAMES}


def _build_summary(
    *,
    nonce: str | None,
    records: list[dict[str, Any]],
    hook_disposed: bool,
    trace_complete: bool,
    overflow: bool | None,
    evidence_present: bool,
    durable_record_count: int,
) -> dict[str, Any]:
    request_status_counts = _empty_counts()
    tool_name_counts = _empty_tools()
    other_tool_name_count = 0
    target_primary_count = 0
    valid_target_primary_count = 0
    target_primary_failed = False
    for record in records:
        request_status_counts[record["status"]] += 1
        if record["model_match"] and record["kind"] == "unknown":
            target_primary_failed = True
        if record["model_match"] and record["kind"] == "primary":
            target_primary_count += 1
            if record["status"] in {"observed", "valid_empty"}:
                valid_target_primary_count += 1
                for name in TOOL_NAMES:
                    tool_name_counts[name] += record["name_counts"][name]
                other_tool_name_count += record["other_name_count"]
            else:
                target_primary_failed = True
    if trace_complete:
        observer_status = "observed"
    elif evidence_present:
        observer_status = "incomplete"
    else:
        observer_status = "unobserved"
    if observer_status != "observed":
        tool_offer_status = "incomplete" if evidence_present else "unobserved"
    elif target_primary_failed:
        tool_offer_status = "incomplete"
    elif target_primary_count == 0:
        tool_offer_status = "unobserved"
    elif valid_target_primary_count == target_primary_count:
        tool_offer_status = "observed"
    else:
        tool_offer_status = "incomplete"
    return {
        "observer_status": observer_status,
        "tool_offer_status": tool_offer_status,
        "nonce": nonce,
        "hook_disposed": hook_disposed,
        "trace_complete": trace_complete,
        "overflow": overflow,
        "request_count": len(records),
        "durable_record_count": durable_record_count,
        "target_primary_request_count": target_primary_count,
        "valid_target_primary_request_count": valid_target_primary_count,
        "request_status_counts": request_status_counts,
        "tool_name_counts": tool_name_counts,
        "other_tool_name_count": other_tool_name_count,
        "requests": records,
    }


def _unobserved(*, status: str = "unobserved") -> dict[str, Any]:
    evidence_present = status != "unobserved"
    return _build_summary(
        nonce=None,
        records=[],
        hook_disposed=False,
        trace_complete=False,
        overflow=None,
        evidence_present=evidence_present,
        durable_record_count=0,
    )


def _read_private_json(path: Path, *, maximum: int) -> Any:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or path.is_symlink()
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size > maximum
    ):
        raise RuntimeError("private_observer_record_invalid")
    raw = _read_pinned_regular_file(path, maximum=maximum)
    return json.loads(raw, object_pairs_hook=_object_pairs_without_duplicates)


def _read_ledger(path: Path, expected_nonce: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _unobserved()
    except OSError:
        return _unobserved(status="incomplete")
    if (
        not stat.S_ISDIR(info.st_mode)
        or path.is_symlink()
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        return _unobserved(status="incomplete")
    names: list[str] = []
    integrity_ok = True
    try:
        with os.scandir(path) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_REQUESTS + 3:
                    integrity_ok = False
                    break
                names.append(entry.name)
    except OSError:
        return _unobserved(status="incomplete")
    total_bytes = 0
    record_paths: list[tuple[int, Path]] = []
    seal_path: Path | None = None
    for name in names:
        if name == "seal.json":
            if seal_path is not None:
                integrity_ok = False
            seal_path = path / name
            continue
        match = RECORD_FILENAME_PATTERN.fullmatch(name)
        if match is not None:
            ordinal = int(match.group(1))
            if not 1 <= ordinal <= MAX_REQUESTS:
                integrity_ok = False
            else:
                record_paths.append((ordinal, path / name))
            continue
        integrity_ok = False
    records: list[dict[str, Any]] = []
    seen_ordinals: set[int] = set()
    for ordinal, record_path in sorted(record_paths):
        if ordinal in seen_ordinals:
            integrity_ok = False
            continue
        seen_ordinals.add(ordinal)
        try:
            file_info = record_path.lstat()
            total_bytes += file_info.st_size
            if total_bytes > MAX_LEDGER_BYTES:
                integrity_ok = False
                continue
            raw_record = _read_private_json(record_path, maximum=MAX_RECORD_BYTES)
            record = _valid_record(raw_record)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            integrity_ok = False
            continue
        if record is None or record["ordinal"] != ordinal:
            integrity_ok = False
            continue
        record_nonce = raw_record.get("nonce") if isinstance(raw_record, dict) else None
        if record_nonce != expected_nonce:
            integrity_ok = False
            continue
        records.append(record)
    seal: dict[str, Any] | None = None
    if seal_path is not None:
        try:
            seal_info = seal_path.lstat()
            total_bytes += seal_info.st_size
            if total_bytes > MAX_LEDGER_BYTES:
                raise RuntimeError("private_observer_ledger_oversized")
            seal = _valid_seal(_read_private_json(seal_path, maximum=MAX_SEAL_BYTES))
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            seal = None
            integrity_ok = False
        if seal is None or seal["nonce"] != expected_nonce:
            integrity_ok = False
    evidence_present = bool(names)
    if seal is None:
        integrity_ok = False if evidence_present else integrity_ok
        hook_disposed = False
        trace_complete = False
        overflow = None
        durable_count = len(records)
    else:
        hook_disposed = seal["hook_disposed"]
        overflow = seal["overflow"]
        durable_count = seal["durable_record_count"]
        if durable_count != seal["record_count"] or len(records) != durable_count:
            integrity_ok = False
            records = []
        ordinals = sorted(record["ordinal"] for record in records)
        expected_ordinals = list(range(1, seal["record_count"] + 1))
        trace_complete = (
            integrity_ok
            and seal["nonce"] == expected_nonce
            and seal["trace_complete"]
            and hook_disposed
            and not seal["overflow"]
            and durable_count == seal["record_count"]
            and len(records) == durable_count
            and ordinals == expected_ordinals
        )
        if not trace_complete:
            integrity_ok = False
    if not integrity_ok:
        trace_complete = False
    return _build_summary(
        nonce=expected_nonce if evidence_present else None,
        records=records,
        hook_disposed=hook_disposed,
        trace_complete=trace_complete,
        overflow=overflow,
        evidence_present=evidence_present or seal is not None,
        durable_record_count=len(records),
    )


def project_safe_summary(value: Any) -> dict[str, Any]:
    """Strictly reduce a parsed observer summary to its closed receipt shape."""
    if not isinstance(value, dict):
        return _unobserved(status="incomplete")
    nonce = value.get("nonce")
    requests = value.get("requests")
    if (
        (
            nonce is not None
            and (not isinstance(nonce, str) or NONCE_PATTERN.fullmatch(nonce) is None)
        )
        or not isinstance(requests, list)
        or len(requests) > MAX_REQUESTS
    ):
        return _unobserved(status="incomplete")
    hook_disposed = value.get("hook_disposed")
    if type(hook_disposed) is not bool:
        hook_disposed = False
    requested_trace_complete = value.get("trace_complete")
    if type(requested_trace_complete) is not bool:
        requested_trace_complete = False
    overflow = value.get("overflow")
    if overflow is not None and type(overflow) is not bool:
        overflow = None
    status_counts = value.get("request_status_counts")
    tool_counts = value.get("tool_name_counts")
    records: list[dict[str, Any]] = []
    fields_valid = set(value) == SUMMARY_FIELDS
    for record_value in requests:
        record = _valid_record(record_value)
        if record is None or record["nonce"] != nonce:
            fields_valid = False
            continue
        records.append(record)
    if len({record["ordinal"] for record in records}) != len(records):
        fields_valid = False
        unique: dict[int, dict[str, Any]] = {}
        for record in records:
            unique.setdefault(record["ordinal"], record)
        records = list(unique.values())
    if [record["ordinal"] for record in records] != list(range(1, len(records) + 1)):
        fields_valid = False
    for count_map, expected_keys, maximum in (
        (status_counts, set(REQUEST_STATUSES), MAX_REQUESTS),
        (tool_counts, set(TOOL_NAMES), MAX_TOOLS * MAX_REQUESTS),
    ):
        if (
            not isinstance(count_map, dict)
            or set(count_map) != expected_keys
            or any(
                type(count) is not int or not 0 <= count <= maximum for count in count_map.values()
            )
        ):
            fields_valid = False
    for name in (
        "request_count",
        "durable_record_count",
        "target_primary_request_count",
        "valid_target_primary_request_count",
        "other_tool_name_count",
    ):
        if type(value.get(name)) is not int or value[name] < 0:
            fields_valid = False
    evidence_present = bool(records) or nonce is not None or hook_disposed
    nonce_valid = isinstance(nonce, str) and NONCE_PATTERN.fullmatch(nonce) is not None
    trace_complete = (
        requested_trace_complete and hook_disposed and overflow is False and nonce_valid
    )
    expected = _build_summary(
        nonce=nonce,
        records=records,
        hook_disposed=hook_disposed,
        trace_complete=trace_complete,
        overflow=overflow,
        evidence_present=evidence_present,
        durable_record_count=len(records),
    )
    if any(value.get(name) != expected.get(name) for name in SUMMARY_FIELDS):
        fields_valid = False
    if not fields_valid and expected["trace_complete"]:
        expected = _build_summary(
            nonce=nonce,
            records=records,
            hook_disposed=hook_disposed,
            trace_complete=False,
            overflow=overflow,
            evidence_present=evidence_present,
            durable_record_count=len(records),
        )
    return expected


def safe_receipt_projection(value: Any) -> dict[str, Any]:
    """Revalidate the summary before the shared provider receipt can serialize it."""
    return project_safe_summary(value)


def read_observation(path: Path, *, expected_nonce: str | None) -> dict[str, Any]:
    """Read a bounded private ledger; request facts and its global seal stay distinct."""
    if not isinstance(expected_nonce, str) or NONCE_PATTERN.fullmatch(expected_nonce) is None:
        return _unobserved()
    return _read_ledger(path, expected_nonce)
