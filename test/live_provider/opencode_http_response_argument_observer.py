"""Private installer and strict reader for T128 HTTP response diagnostics.

The public projection reports only fixed status codes, ordinals, counters,
UTF-8 lengths and comparison booleans. It deliberately excludes nonce,
messages, request/response identifiers, headers, URLs, paths and exception
text. A mechanically complete observer trace does not imply functional or
model acceptance.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

MAX_PLUGIN_BYTES = 128 * 1024
MAX_PROTOCOL_BYTES = 128 * 1024
MAX_EXPECTATION_BYTES = 64 * 1024
MAX_RECORD_BYTES = 4096
MAX_SEAL_BYTES = 2048
MAX_LEDGER_BYTES = 72 * 1024
MAX_RESPONSES = 16
MAX_PHASE_BYTES = 8192
MAX_ARGUMENT_BYTES = 64 * 1024
MAX_TARGET_TOOL_CALLS = 32

NONCE_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
RESPONSE_NAME_PATTERN = re.compile(r"response-([0-9]{4})\.json\Z")
PROMPT_EXPECTATION_NAME = "http-prompt-expectations.json"
RESPONSE_EXPECTATION_NAME = "http-response-argument-expectations.json"
LEDGER_NAME = "http-response-argument-observation"
PLUGIN_NAME = "cao-http-response-argument-observer.js"
PROMPT_PLUGIN_NAME = "cao-http-prompt-observer.js"
SUPPORT_DIRECTORY_NAME = "cao-http-response-support"
PROTOCOL_NAME = "opencode_http_response_protocol.js"

PLUGIN_IMPORT_REWRITES = (
    (
        b'from "./opencode_http_prompt_observer.js"',
        b'from "./cao-http-prompt-observer.js"',
    ),
    (
        b'from "./opencode_http_response_protocol.js"',
        b'from "../cao-http-response-support/opencode_http_response_protocol.js"',
    ),
)
PLUGIN_DEFAULT_MARKER = b"export default createHttpResponseArgumentObserver()"

RESPONSE_EXPECTATION_FIELDS = {"schema_version", "nonce", "phase_messages"}
PROMPT_EXPECTATION_FIELDS = {
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
    "phase_ordinal",
    "request_bound",
    "status",
    "reason",
    "target_tool_call_count",
    "argument_exact",
    "known_punctuation_variant_exact",
    "expected_utf8_bytes",
    "observed_utf8_bytes",
    "clone_complete",
    "original_not_replaced",
}
SEAL_FIELDS = {
    "schema_version",
    "nonce",
    "hook_disposed",
    "trace_complete",
    "request_count",
    "response_count",
    "durable_record_count",
    "overflow",
}
STATUSES = {"observed", "no_target_tool", "unavailable"}
REASONS = {
    "none",
    "unsupported_content_type",
    "body_oversized",
    "malformed_response",
    "ambiguous_response",
    "incomplete_response",
    "malformed_arguments",
    "arguments_oversized",
    "invalid_expectation",
    "unbound_request",
    "expectations_unavailable",
    "http_error",
    "inspection_timeout",
    "response_unreadable",
    "observer_error",
}


def _path_identity(info: os.stat_result) -> tuple[int, ...]:
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


def _inode_key(identity: tuple[int, ...]) -> tuple[int, int]:
    return identity[0], identity[1]


def _fixed_error(code: str) -> RuntimeError:
    return RuntimeError(code)


def _private_directory(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        raise _fixed_error("private_response_observer_directory_invalid") from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or path.is_symlink()
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or resolved != path.absolute()
    ):
        raise _fixed_error("private_response_observer_directory_invalid")
    return info


def _sync_directory(path: Path) -> None:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        raise _fixed_error("private_response_observer_directory_sync_failed") from None
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_dev != current.st_dev
            or opened.st_ino != current.st_ino
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o700
            or _path_identity(opened) != _path_identity(current)
        ):
            raise _fixed_error("private_response_observer_directory_changed")
        os.fsync(descriptor)
    except OSError:
        raise _fixed_error("private_response_observer_directory_sync_failed") from None
    finally:
        os.close(descriptor)


def _read_private_file_with_identity(path: Path, *, maximum: int) -> tuple[bytes, tuple[int, ...]]:
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or path.is_symlink()
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size > maximum
        ):
            raise _fixed_error("private_response_observer_file_invalid")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise _fixed_error("private_response_observer_file_invalid") from None
    try:
        opened = os.fstat(descriptor)
        if _path_identity(opened) != _path_identity(before):
            raise _fixed_error("private_response_observer_file_changed")
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
            or _path_identity(after_descriptor) != _path_identity(opened)
            or _path_identity(after_path) != _path_identity(opened)
        ):
            raise _fixed_error("private_response_observer_file_changed")
        return data, _path_identity(opened)
    except OSError:
        raise _fixed_error("private_response_observer_file_changed") from None
    finally:
        os.close(descriptor)


def _read_source_file(path: Path, *, maximum: int) -> bytes:
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or path.is_symlink()
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or before.st_size > maximum
            or path.resolve(strict=True) != path.absolute()
        ):
            raise _fixed_error("response_observer_source_invalid")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise _fixed_error("response_observer_source_invalid") from None
    try:
        opened = os.fstat(descriptor)
        if _path_identity(opened) != _path_identity(before):
            raise _fixed_error("response_observer_source_changed")
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
            or _path_identity(after_descriptor) != _path_identity(opened)
            or _path_identity(after_path) != _path_identity(opened)
        ):
            raise _fixed_error("response_observer_source_changed")
        return data
    except OSError:
        raise _fixed_error("response_observer_source_changed") from None
    finally:
        os.close(descriptor)


def _valid_nonce(value: Any) -> bool:
    return type(value) is str and NONCE_PATTERN.fullmatch(value) is not None


def _utf8_size(value: Any, *, maximum: int, allow_empty: bool = False) -> int:
    if type(value) is not str:
        raise _fixed_error("response_observer_expectation_invalid")
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        raise _fixed_error("response_observer_expectation_invalid") from None
    if (not allow_empty and size == 0) or size > maximum:
        raise _fixed_error("response_observer_expectation_invalid")
    return size


def _validate_prompt_expectation(document: Any) -> str:
    if (
        type(document) is not dict
        or set(document) != PROMPT_EXPECTATION_FIELDS
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or not _valid_nonce(document.get("nonce"))
        or type(document.get("phase_prompts")) is not list
        or len(document["phase_prompts"]) != 2
    ):
        raise _fixed_error("prompt_expectation_shape_invalid")
    first, second = document["phase_prompts"]
    if first == second:
        raise _fixed_error("prompt_expectation_value_invalid")
    _utf8_size(first, maximum=MAX_PHASE_BYTES)
    _utf8_size(second, maximum=MAX_PHASE_BYTES)
    prefix = _utf8_size(document["receipt_prefix"], maximum=MAX_PHASE_BYTES)
    suffix = _utf8_size(document["receipt_suffix"], maximum=16 * 1024)
    last = document["last_instruction"]
    _utf8_size(last, maximum=4096)
    if last not in first or last not in second or prefix <= 0 or suffix <= 0:
        raise _fixed_error("prompt_expectation_value_invalid")
    return document["nonce"]


def _validate_response_expectation(document: Any, *, nonce: str) -> dict[str, Any]:
    if (
        type(document) is not dict
        or set(document) != RESPONSE_EXPECTATION_FIELDS
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("nonce") != nonce
        or type(document.get("phase_messages")) is not list
        or len(document["phase_messages"]) != 2
    ):
        raise _fixed_error("response_expectation_shape_invalid")
    messages = document["phase_messages"]
    for message in messages:
        _utf8_size(message, maximum=MAX_PHASE_BYTES)
    return document


def specialize_plugin_bytes(source: bytes, nonce: str) -> bytes:
    """Bind the T128 runtime plugin to its private nonce and support modules."""
    if type(source) is not bytes or len(source) > MAX_PLUGIN_BYTES:
        raise _fixed_error("response_observer_source_invalid")
    if not _valid_nonce(nonce):
        raise _fixed_error("response_observer_nonce_invalid")
    for old, _new in PLUGIN_IMPORT_REWRITES:
        if source.count(old) != 1:
            raise _fixed_error("response_observer_import_marker_invalid")
    if any(source.count(new) for _old, new in PLUGIN_IMPORT_REWRITES):
        raise _fixed_error("response_observer_import_marker_invalid")
    if source.count(PLUGIN_DEFAULT_MARKER) != 1:
        raise _fixed_error("response_observer_default_marker_invalid")
    if b"export default createHttpResponseArgumentObserver({ nonce:" in source:
        raise _fixed_error("response_observer_default_marker_invalid")

    specialized = source
    for old, new in PLUGIN_IMPORT_REWRITES:
        specialized = specialized.replace(old, new, 1)
    default_replacement = (
        b'export default createHttpResponseArgumentObserver({ nonce: "'
        + nonce.encode("ascii")
        + b'" })'
    )
    specialized = specialized.replace(PLUGIN_DEFAULT_MARKER, default_replacement, 1)
    if len(specialized) > MAX_PLUGIN_BYTES:
        raise _fixed_error("response_observer_source_invalid")
    return specialized


def _remove_created_file(path: Path, identity: tuple[int, ...]) -> None:
    try:
        current = path.lstat()
        if (
            stat.S_ISREG(current.st_mode)
            and current.st_uid == os.getuid()
            and current.st_nlink == 1
            and stat.S_IMODE(current.st_mode) == 0o600
            and _inode_key(_path_identity(current)) == _inode_key(identity)
        ):
            path.unlink()
            _sync_directory(path.parent)
    except (OSError, RuntimeError):
        return


def _remove_created_directory(path: Path, identity: tuple[int, ...]) -> None:
    try:
        current = path.lstat()
        if (
            stat.S_ISDIR(current.st_mode)
            and current.st_uid == os.getuid()
            and stat.S_IMODE(current.st_mode) == 0o700
            and _inode_key(_path_identity(current)) == _inode_key(identity)
        ):
            path.rmdir()
            _sync_directory(path.parent)
    except (OSError, RuntimeError):
        return


def _create_private_directory(path: Path) -> tuple[int, ...]:
    identity: tuple[int, ...] | None = None
    try:
        os.mkdir(path, 0o700)
        info = path.lstat()
        identity = _path_identity(info)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise _fixed_error("private_response_observer_directory_invalid")
        _sync_directory(path.parent)
        if identity is None:
            raise _fixed_error("private_response_observer_directory_create_failed")
        return identity
    except FileExistsError:
        raise _fixed_error("response_observer_destination_exists") from None
    except RuntimeError:
        if identity is not None:
            _remove_created_directory(path, identity)
        raise
    except OSError:
        if identity is not None:
            _remove_created_directory(path, identity)
        raise _fixed_error("private_response_observer_directory_create_failed") from None


def _require_absent(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise _fixed_error("response_observer_destination_invalid") from None
    raise _fixed_error("response_observer_destination_exists")


def _write_new_private_file(path: Path, data: bytes, *, maximum: int) -> tuple[int, ...]:
    if type(data) is not bytes or len(data) > maximum:
        raise _fixed_error("response_observer_file_too_large")
    descriptor = -1
    created_identity: tuple[int, ...] | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        opened = os.fstat(descriptor)
        created_identity = _path_identity(opened)
        if not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid() or opened.st_nlink != 1:
            raise _fixed_error("private_response_observer_file_invalid")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as writer:
            descriptor = -1
            writer.write(data)
            writer.flush()
            os.fsync(writer.fileno())
        readback, verified_identity = _read_private_file_with_identity(path, maximum=maximum)
        if readback != data or _inode_key(verified_identity) != _inode_key(created_identity):
            raise _fixed_error("response_observer_file_copy_mismatch")
        _sync_directory(path.parent)
        return verified_identity
    except FileExistsError:
        raise _fixed_error("response_observer_destination_exists") from None
    except RuntimeError:
        if descriptor >= 0:
            os.close(descriptor)
        if created_identity is not None:
            _remove_created_file(path, created_identity)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        if created_identity is not None:
            _remove_created_file(path, created_identity)
        raise _fixed_error("response_observer_file_write_failed") from None


def _rollback_install(
    files: list[tuple[Path, tuple[int, ...]]],
    directories: list[tuple[Path, tuple[int, ...]]],
) -> None:
    for path, identity in reversed(files):
        _remove_created_file(path, identity)
    for path, identity in reversed(directories):
        _remove_created_directory(path, identity)


def install_project_plugin(
    working_directory: Path,
    source_path: Path,
    protocol_source_path: Path,
    nonce: str,
) -> Path:
    """Install private observer/support copies and return the absent ledger path.

    The JS observer exclusively creates the ledger directory on startup; this
    installer verifies it is unused but does not claim its ownership.
    """
    if not _valid_nonce(nonce):
        raise _fixed_error("response_observer_nonce_invalid")
    work = Path(working_directory)
    work_identity = _path_identity(_private_directory(work))
    opencode = work / ".opencode"
    opencode_identity = _path_identity(_private_directory(opencode))
    plugins = opencode / "plugins"
    plugins_identity = _path_identity(_private_directory(plugins))

    prompt_plugin = plugins / PROMPT_PLUGIN_NAME
    _prompt_plugin_identity = _read_private_file_with_identity(
        prompt_plugin, maximum=MAX_PLUGIN_BYTES
    )[1]

    plugin_source = _read_source_file(Path(source_path), maximum=MAX_PLUGIN_BYTES)
    protocol_source = _read_source_file(Path(protocol_source_path), maximum=MAX_PROTOCOL_BYTES)
    specialized_plugin = specialize_plugin_bytes(plugin_source, nonce)

    support_directory = opencode / SUPPORT_DIRECTORY_NAME
    protocol_destination = support_directory / PROTOCOL_NAME
    plugin_destination = plugins / PLUGIN_NAME
    ledger_directory = opencode / LEDGER_NAME
    for path in (support_directory, protocol_destination, plugin_destination, ledger_directory):
        _require_absent(path)

    created_files: list[tuple[Path, tuple[int, ...]]] = []
    created_directories: list[tuple[Path, tuple[int, ...]]] = []
    try:
        support_identity = _create_private_directory(support_directory)
        created_directories.append((support_directory, support_identity))
        protocol_identity = _write_new_private_file(
            protocol_destination, protocol_source, maximum=MAX_PROTOCOL_BYTES
        )
        created_files.append((protocol_destination, protocol_identity))
        plugin_identity = _write_new_private_file(
            plugin_destination, specialized_plugin, maximum=MAX_PLUGIN_BYTES
        )
        created_files.append((plugin_destination, plugin_identity))
        try:
            current_prompt_identity = _read_private_file_with_identity(
                prompt_plugin, maximum=MAX_PLUGIN_BYTES
            )[1]
        except RuntimeError:
            raise _fixed_error("prompt_observer_plugin_changed") from None
        if (
            current_prompt_identity != _prompt_plugin_identity
            or _inode_key(_path_identity(work.lstat())) != _inode_key(work_identity)
            or _inode_key(_path_identity(opencode.lstat())) != _inode_key(opencode_identity)
            or _inode_key(_path_identity(plugins.lstat())) != _inode_key(plugins_identity)
        ):
            raise _fixed_error("prompt_observer_plugin_changed")
        _require_absent(ledger_directory)
        return ledger_directory
    except BaseException:
        _rollback_install(created_files, created_directories)
        raise


def _object_pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid_json_constant")


def _decode_json(raw: bytes) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_pairs_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except RecursionError:
        raise ValueError("json_too_deep") from None


def _read_private_json_with_identity(path: Path, *, maximum: int) -> tuple[Any, tuple[int, ...]]:
    raw, identity = _read_private_file_with_identity(path, maximum=maximum)
    return _decode_json(raw), identity


def _read_t125_nonce(opencode: Path) -> tuple[str, tuple[int, ...]]:
    document, identity = _read_private_json_with_identity(
        opencode / PROMPT_EXPECTATION_NAME, maximum=MAX_EXPECTATION_BYTES
    )
    return _validate_prompt_expectation(document), identity


def write_expectations(working_directory: Path, document: dict[str, Any]) -> Path:
    """Write the response-phase messages once, bound to the existing T125 nonce."""
    work = Path(working_directory)
    _private_directory(work)
    opencode = work / ".opencode"
    opencode_identity = _path_identity(_private_directory(opencode))
    if type(document) is not dict or not _valid_nonce(document.get("nonce")):
        raise _fixed_error("response_expectation_shape_invalid")
    prompt_path = opencode / PROMPT_EXPECTATION_NAME
    try:
        t125_nonce, prompt_identity = _read_t125_nonce(opencode)
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError, TypeError):
        raise _fixed_error("prompt_expectation_unavailable") from None
    if document["nonce"] != t125_nonce:
        raise _fixed_error("response_expectation_nonce_mismatch")
    validated = _validate_response_expectation(document, nonce=t125_nonce)
    encoded = json.dumps(
        validated,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    if len(encoded) > MAX_EXPECTATION_BYTES:
        raise _fixed_error("response_expectation_too_large")
    destination = opencode / RESPONSE_EXPECTATION_NAME
    _require_absent(destination)
    destination_identity = _write_new_private_file(
        destination, encoded, maximum=MAX_EXPECTATION_BYTES
    )
    try:
        if _path_identity(prompt_path.lstat()) != prompt_identity or _inode_key(
            _path_identity(opencode.lstat())
        ) != _inode_key(opencode_identity):
            _remove_created_file(destination, destination_identity)
            raise _fixed_error("prompt_expectation_changed")
    except OSError:
        _remove_created_file(destination, destination_identity)
        raise _fixed_error("prompt_expectation_changed") from None
    return destination


def _valid_record(
    document: Any,
    *,
    nonce: str,
    expected_ordinal: int,
    expected_sizes: tuple[int, int],
) -> dict[str, Any] | None:
    if type(document) is not dict or set(document) != RECORD_FIELDS:
        return None
    ordinal = document.get("ordinal")
    phase = document.get("phase_ordinal")
    request_bound = document.get("request_bound")
    status = document.get("status")
    reason = document.get("reason")
    target_count = document.get("target_tool_call_count")
    argument_exact = document.get("argument_exact")
    punctuation_exact = document.get("known_punctuation_variant_exact")
    expected_bytes = document.get("expected_utf8_bytes")
    observed_bytes = document.get("observed_utf8_bytes")
    clone_complete = document.get("clone_complete")
    original_not_replaced = document.get("original_not_replaced")
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("nonce") != nonce
        or type(ordinal) is not int
        or ordinal != expected_ordinal
        or type(request_bound) is not bool
        or (phase is not None and (type(phase) is not int or phase not in {1, 2}))
        or (request_bound and phase not in {1, 2})
        or (not request_bound and phase is not None)
        or type(status) is not str
        or status not in STATUSES
        or type(reason) is not str
        or reason not in REASONS
        or type(argument_exact) is not bool
        or type(punctuation_exact) is not bool
        or type(clone_complete) is not bool
        or type(original_not_replaced) is not bool
    ):
        return None

    if expected_bytes is not None and (
        type(expected_bytes) is not int or not 1 <= expected_bytes <= MAX_PHASE_BYTES
    ):
        return None
    if observed_bytes is not None and (
        type(observed_bytes) is not int or not 0 <= observed_bytes <= MAX_ARGUMENT_BYTES
    ):
        return None
    if type(target_count) is not int or not 0 <= target_count <= MAX_TARGET_TOOL_CALLS:
        if target_count is not None:
            return None

    if not request_bound:
        if (
            reason not in {"unbound_request", "expectations_unavailable", "observer_error"}
            or status != "unavailable"
            or target_count is not None
            or argument_exact
            or punctuation_exact
            or expected_bytes is not None
            or observed_bytes is not None
        ):
            return None
    elif reason == "unbound_request":
        return None

    expected_for_phase = expected_sizes[phase - 1] if phase in {1, 2} else None
    if status == "observed":
        if (
            reason != "none"
            or not request_bound
            or target_count != 1
            or type(expected_bytes) is not int
            or expected_bytes != expected_for_phase
            or type(observed_bytes) is not int
            or not clone_complete
            or not original_not_replaced
            or (argument_exact and punctuation_exact)
        ):
            return None
        if argument_exact and observed_bytes != expected_bytes:
            return None
        if punctuation_exact:
            # The exact final-character policy is confirmed again using the
            # expectation text in the phase summary; length is a cheap gate.
            if observed_bytes != expected_bytes + 2:
                return None
    elif status == "no_target_tool":
        if (
            reason != "none"
            or not request_bound
            or target_count != 0
            or argument_exact
            or punctuation_exact
            or type(expected_bytes) is not int
            or expected_bytes != expected_for_phase
            or observed_bytes is not None
            or not clone_complete
            or not original_not_replaced
        ):
            return None
    else:
        if (
            reason == "none"
            or target_count is not None
            or argument_exact
            or punctuation_exact
            or observed_bytes is not None
            or (
                not request_bound
                and reason not in {"unbound_request", "expectations_unavailable", "observer_error"}
            )
            or (not original_not_replaced and reason != "observer_error")
        ):
            return None
        if expected_bytes is not None:
            if phase is None or expected_bytes != expected_for_phase:
                return None
        elif reason not in {"expectations_unavailable", "invalid_expectation", "unbound_request"}:
            return None

    return {
        "ordinal": ordinal,
        "phase_ordinal": phase,
        "request_bound": request_bound,
        "status": status,
        "reason": reason,
        "target_tool_call_count": target_count,
        "argument_exact": argument_exact,
        "known_punctuation_variant_exact": punctuation_exact,
        "expected_utf8_bytes": expected_bytes,
        "observed_utf8_bytes": observed_bytes,
        "clone_complete": clone_complete,
        "original_not_replaced": original_not_replaced,
    }


def _valid_seal(
    document: Any,
    *,
    nonce: str,
    record_count: int,
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if type(document) is not dict or set(document) != SEAL_FIELDS:
        return None
    request_count = document.get("request_count")
    response_count = document.get("response_count")
    durable_count = document.get("durable_record_count")
    hook_disposed = document.get("hook_disposed")
    trace_complete = document.get("trace_complete")
    overflow = document.get("overflow")
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("nonce") != nonce
        or type(hook_disposed) is not bool
        or type(trace_complete) is not bool
        or type(overflow) is not bool
        or type(request_count) is not int
        or not 0 <= request_count <= MAX_RESPONSES
        or type(response_count) is not int
        or not 0 <= response_count <= MAX_RESPONSES
        or type(durable_count) is not int
        or not 0 <= durable_count <= MAX_RESPONSES
        or response_count != record_count
        or durable_count > response_count
        or request_count < response_count
    ):
        return None
    if trace_complete and (
        not hook_disposed
        or overflow
        or request_count != response_count
        or durable_count != response_count
        or any(not row["request_bound"] or row["phase_ordinal"] not in {1, 2} for row in records)
    ):
        return None
    return {
        "hook_disposed": hook_disposed,
        "trace_complete": trace_complete,
        "request_count": request_count,
        "response_count": response_count,
        "durable_record_count": durable_count,
        "overflow": overflow,
    }


def _phase_result(
    phase: int,
    records: list[dict[str, Any]],
    message: str,
    *,
    trace_complete: bool,
) -> dict[str, Any]:
    expected_bytes = len(message.encode("utf-8"))
    base = {
        "phase_ordinal": phase,
        "status": "unavailable",
        "target_tool_call_count": None,
        "argument_exact": None,
        "known_punctuation_variant_exact": None,
        "expected_utf8_bytes": expected_bytes,
        "observed_utf8_bytes": None,
    }
    phase_records = [record for record in records if record["phase_ordinal"] == phase]
    if not trace_complete:
        base["status"] = "incomplete"
        return base
    if not phase_records:
        base["status"] = "not_observed"
        return base
    if any(record["status"] == "unavailable" for record in phase_records):
        base["status"] = "unavailable"
        return base

    target_count = sum(record["target_tool_call_count"] for record in phase_records)
    base["target_tool_call_count"] = target_count
    if target_count == 0:
        base["status"] = "target_call_missing"
        return base
    if target_count != 1:
        base["status"] = "ambiguous_target_calls"
        return base

    target_record = next(record for record in phase_records if record["status"] == "observed")
    exact = target_record["argument_exact"]
    punctuation_variant = target_record["known_punctuation_variant_exact"]
    observed_bytes = target_record["observed_utf8_bytes"]
    if punctuation_variant and not message.endswith("."):
        base["status"] = "unavailable"
        return base
    base["observed_utf8_bytes"] = observed_bytes
    base["argument_exact"] = exact
    base["known_punctuation_variant_exact"] = punctuation_variant
    if exact:
        base["status"] = "exact"
    elif punctuation_variant:
        base["status"] = "known_punctuation_variant"
    else:
        base["status"] = "body_mismatch"
    return base


def _unavailable_summary() -> dict[str, Any]:
    return {
        "observer_status": "unavailable",
        "trace_complete": False,
        "overflow": None,
        "request_count": None,
        "response_count": None,
        "durable_record_count": None,
        "phase_results": [
            {
                "phase_ordinal": phase,
                "status": "unavailable",
                "target_tool_call_count": None,
                "argument_exact": None,
                "known_punctuation_variant_exact": None,
                "expected_utf8_bytes": None,
                "observed_utf8_bytes": None,
            }
            for phase in (1, 2)
        ],
        "records": None,
    }


def _bounded_names(path: Path) -> list[str]:
    names: list[str] = []
    with os.scandir(path) as entries:
        for entry in entries:
            if len(names) >= MAX_RESPONSES + 1:
                raise _fixed_error("response_observer_record_count_invalid")
            names.append(entry.name)
    return sorted(names)


def _read_observation(path: Path, expected_nonce: str) -> dict[str, Any]:
    if not _valid_nonce(expected_nonce):
        raise _fixed_error("response_observer_nonce_invalid")
    if path.name != LEDGER_NAME or path.parent.name != ".opencode":
        raise _fixed_error("response_observer_ledger_invalid")
    ledger_info = _private_directory(path)
    ledger_identity = _path_identity(ledger_info)
    opencode = path.parent
    opencode_identity = _path_identity(_private_directory(opencode))

    prompt_nonce, prompt_identity = _read_t125_nonce(opencode)
    if prompt_nonce != expected_nonce:
        raise _fixed_error("prompt_expectation_nonce_mismatch")
    expectation_document, expectation_identity = _read_private_json_with_identity(
        opencode / RESPONSE_EXPECTATION_NAME,
        maximum=MAX_EXPECTATION_BYTES,
    )
    expectations = _validate_response_expectation(expectation_document, nonce=expected_nonce)
    phase_messages = expectations["phase_messages"]
    expected_sizes = tuple(len(message.encode("utf-8")) for message in phase_messages)

    names = _bounded_names(path)
    if "seal.json" not in names:
        raise _fixed_error("response_observer_seal_missing")
    record_names = [name for name in names if RESPONSE_NAME_PATTERN.fullmatch(name)]
    if len(record_names) > MAX_RESPONSES:
        raise _fixed_error("response_observer_record_count_invalid")
    if set(names) != set(record_names) | {"seal.json"}:
        raise _fixed_error("response_observer_ledger_entries_invalid")
    expected_names = [f"response-{ordinal:04d}.json" for ordinal in range(1, len(record_names) + 1)]
    if record_names != expected_names:
        raise _fixed_error("response_observer_record_order_invalid")

    identities: dict[Path, tuple[int, ...]] = {}
    total_bytes = 0
    for name in names:
        file_path = path / name
        try:
            info = file_path.lstat()
        except OSError:
            raise _fixed_error("response_observer_file_invalid") from None
        maximum = MAX_SEAL_BYTES if name == "seal.json" else MAX_RECORD_BYTES
        if (
            not stat.S_ISREG(info.st_mode)
            or file_path.is_symlink()
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > maximum
        ):
            raise _fixed_error("response_observer_file_invalid")
        total_bytes += info.st_size
    if total_bytes > MAX_LEDGER_BYTES:
        raise _fixed_error("response_observer_ledger_too_large")

    records: list[dict[str, Any]] = []
    for ordinal, name in enumerate(record_names, start=1):
        record_path = path / name
        raw_record, identity = _read_private_json_with_identity(
            record_path,
            maximum=MAX_RECORD_BYTES,
        )
        record = _valid_record(
            raw_record,
            nonce=expected_nonce,
            expected_ordinal=ordinal,
            expected_sizes=expected_sizes,
        )
        if record is None:
            raise _fixed_error("response_observer_record_invalid")
        if record["known_punctuation_variant_exact"] and not phase_messages[
            record["phase_ordinal"] - 1
        ].endswith("."):
            raise _fixed_error("response_observer_record_invalid")
        identities[record_path] = identity
        records.append(record)

    seal_path = path / "seal.json"
    raw_seal, seal_identity = _read_private_json_with_identity(
        seal_path,
        maximum=MAX_SEAL_BYTES,
    )
    seal = _valid_seal(
        raw_seal,
        nonce=expected_nonce,
        record_count=len(records),
        records=records,
    )
    if seal is None:
        raise _fixed_error("response_observer_seal_invalid")
    identities[seal_path] = seal_identity

    for file_path, expected_identity in identities.items():
        if _path_identity(file_path.lstat()) != expected_identity:
            raise _fixed_error("response_observer_file_changed")
    if _path_identity((opencode / PROMPT_EXPECTATION_NAME).lstat()) != prompt_identity:
        raise _fixed_error("response_observer_expectation_changed")
    if _path_identity((opencode / RESPONSE_EXPECTATION_NAME).lstat()) != expectation_identity:
        raise _fixed_error("response_observer_expectation_changed")
    final_ledger = path.lstat()
    if (
        _path_identity(final_ledger) != ledger_identity
        or _path_identity(opencode.lstat()) != opencode_identity
        or _bounded_names(path) != names
    ):
        raise _fixed_error("response_observer_ledger_changed")

    trace_complete = seal["trace_complete"]
    phases = [
        _phase_result(
            phase,
            records,
            phase_messages[phase - 1],
            trace_complete=trace_complete,
        )
        for phase in (1, 2)
    ]
    public_records = [dict(record) for record in records]
    return {
        "observer_status": "observed" if trace_complete else "incomplete",
        "trace_complete": trace_complete,
        "overflow": seal["overflow"],
        "request_count": seal["request_count"],
        "response_count": seal["response_count"],
        "durable_record_count": seal["durable_record_count"],
        "phase_results": phases,
        "records": public_records,
    }


def read_observation(path: Path, expected_nonce: str) -> dict[str, Any]:
    """Project a strictly validated private response ledger without raw content.

    ``observer_status`` describes mechanics only: ``observed`` means the trace
    sealed complete, ``incomplete`` means a valid but incomplete seal, and
    ``unavailable`` means the private evidence failed validation. The two
    ``phase_results`` separately report ``exact``,
    ``known_punctuation_variant``, ``body_mismatch``,
    ``target_call_missing``, ``ambiguous_target_calls``, ``unavailable``,
    ``incomplete`` or ``not_observed``. Exact body results require a complete
    trace and exactly one fully parsed target call for that phase. No result is
    functional or model acceptance evidence.
    """
    try:
        return _read_observation(Path(path), expected_nonce)
    except (
        OSError,
        RuntimeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ):
        return _unavailable_summary()
