"""Diagnostic-only private installer/projection for HTTP text streaming.

This module writes only beneath the caller-provided private_root. It returns a
project loader as bytes and never writes to the project or CAO state. PostgreSQL
and cancellation authority belong to the caller.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from test.live_provider import opencode_http_prompt_observer as prompt_observer
from test.live_provider import opencode_http_response_argument_observer as response_observer
from typing import Any, Iterable

MAX_PLUGIN_BYTES = 128 * 1024
MAX_EXPECTATION_BYTES = 64 * 1024
MAX_SAMPLE_BYTES = 4096
MAX_INVALID_BYTES = 1024
MAX_SEAL_BYTES = 2048
MAX_LEDGER_BYTES = 72 * 1024
MAX_SAMPLES = 128
MAX_REQUESTS = 16
MAX_WIRE_BYTES = 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_EVENTS = 4096
NONCE_RE = re.compile(r"[0-9a-f]{32}\Z")
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
TERMINAL_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
SAMPLE_RE = re.compile(r"sample-([0-9]{4})\.json\Z")

SOURCE_NAMES = (
    "opencode_http_text_stream_observer.js",
    "opencode_http_text_response_monitor.js",
    "opencode_http_stream_protocol.js",
    "opencode_http_prompt_observer.js",
)
OBSERVER_MARKER = b"export default createHttpTextStreamObserver()"
OBSERVER_REPLACEMENT_PREFIX = b'export default createHttpTextStreamObserver({ nonce: "'
IMPORTS = {
    "opencode_http_text_stream_observer.js": {
        "./opencode_http_prompt_observer.js",
        "./opencode_http_text_response_monitor.js",
    },
    "opencode_http_text_response_monitor.js": {"./opencode_http_stream_protocol.js"},
    "opencode_http_stream_protocol.js": {"./opencode_http_prompt_observer.js"},
    "opencode_http_prompt_observer.js": set(),
}
BUILTIN_IMPORTS = {
    "opencode_http_text_stream_observer.js": {
        "node:fs/promises",
        "node:fs",
        "node:path",
        "node:url",
    },
    "opencode_http_text_response_monitor.js": set(),
    "opencode_http_stream_protocol.js": set(),
    "opencode_http_prompt_observer.js": {
        "node:fs/promises",
        "node:fs",
        "node:crypto",
        "node:path",
        "node:url",
    },
}
IMPORT_RE = re.compile(rb"\bfrom\s+['\"]([^'\"]+)['\"]")
DYNAMIC_IMPORT_RE = re.compile(rb"\bimport\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")
SIDE_EFFECT_IMPORT_RE = re.compile(rb"\bimport\s*['\"]([^'\"]+)['\"]")
SAMPLE_FIELDS = {
    "schema_version",
    "nonce",
    "sequence",
    "request_ordinal",
    "request_bound",
    "phase_ordinal",
    "receipt_sha256",
    "state",
    "reason",
    "wire_bytes",
    "text_bytes",
    "text_events",
    "event_count",
    "original_not_replaced",
}
INVALID_FIELDS = {"schema_version", "nonce", "status", "reason"}
SEAL_FIELDS = {
    "schema_version",
    "nonce",
    "trace_complete",
    "hook_disposed",
    "request_count",
    "eligible_requests",
    "sample_count",
    "sample_attempts",
}
COUNTERS = ("wire_bytes", "text_bytes", "text_events", "event_count")
PUBLIC_REASONS = {
    "observer_integrity",
    "observer_closed",
    "receipt_mismatch",
    "ambiguous_request",
    "stream_finished",
    "stream_unavailable",
    "stream_not_currently_active",
    "stream_positive_text_required",
    "private_evidence_unavailable",
}


@dataclass(frozen=True)
class InstallResult:
    """Files and hashes needed to integrate the fixture without writing its project."""

    project_loader_path: str
    project_loader_bytes: bytes
    project_loader_sha256: str
    private_root: Path
    expectation_path: Path
    expectation_sha256: str
    support_directory: Path
    ledger_root: Path
    source_sha256: dict[str, str]
    installed_sha256: dict[str, str]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_private_directory(path: Path) -> bool:
    try:
        info = path.lstat()
        return (
            stat.S_ISDIR(info.st_mode)
            and not path.is_symlink()
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700
            and path.resolve(strict=True) == path.absolute()
        )
    except OSError:
        return False


def _all_imports(data: bytes) -> set[str]:
    found = {value.decode("ascii") for value in IMPORT_RE.findall(data)}
    found.update(value.decode("ascii") for value in DYNAMIC_IMPORT_RE.findall(data))
    found.update(value.decode("ascii") for value in SIDE_EFFECT_IMPORT_RE.findall(data))
    if any(not (value.startswith("node:") or value.startswith(".")) for value in found):
        raise RuntimeError("stream_observer_external_import_not_allowed")
    return found


def _relative_imports(data: bytes) -> set[str]:
    return {value for value in _all_imports(data) if value.startswith(".")}


def _specialize_observer(source: bytes, nonce: str) -> bytes:
    if source.count(OBSERVER_MARKER) != 1:
        raise RuntimeError("stream_observer_default_marker_invalid")
    if b"createHttpTextStreamObserver({ nonce:" in source:
        raise RuntimeError("stream_observer_default_already_specialized")
    replacement = (
        OBSERVER_REPLACEMENT_PREFIX
        + nonce.encode("ascii")
        + b'", terminalId: process.env.CAO_TERMINAL_ID })'
    )
    result = source.replace(OBSERVER_MARKER, replacement, 1)
    if len(result) > MAX_PLUGIN_BYTES:
        raise RuntimeError("stream_observer_source_too_large")
    return result


def _relocate_core_imports(source: bytes) -> bytes:
    replacements = {
        b'"./opencode_http_prompt_observer.js"': b'"../http-stream-support/opencode_http_prompt_observer.js"',
        b'"./opencode_http_text_response_monitor.js"': b'"../http-stream-support/opencode_http_text_response_monitor.js"',
    }
    for original, relocated in replacements.items():
        if source.count(original) != 1:
            raise RuntimeError("stream_observer_core_import_marker_invalid")
        source = source.replace(original, relocated, 1)
    return source


def install(
    private_root: Path,
    project: Path,
    nonce: str,
    phase_prompts: Iterable[str],
    receipt_prefix: str,
    receipt_suffix: str,
    last_instruction: str,
) -> InstallResult:
    """Prepare the private module/expectations and return a project loader.

    `private_root` must not already exist. The caller may track the returned
    `.opencode/plugins/stream-loader.js` bytes before native initialization.
    This function does not write to `project` and does not invoke Git.
    """
    if type(nonce) is not str or NONCE_RE.fullmatch(nonce) is None:
        raise RuntimeError("stream_observer_nonce_invalid")
    private_root = Path(private_root).absolute()
    project = Path(project).absolute()
    if not _is_private_directory(project):
        raise RuntimeError("stream_observer_project_not_private")
    if private_root.exists() or private_root.is_symlink():
        raise RuntimeError("stream_observer_private_root_exists")
    if not _is_private_directory(private_root.parent):
        raise RuntimeError("stream_observer_private_parent_invalid")

    expectation = {
        "schema_version": 1,
        "nonce": nonce,
        "phase_prompts": list(phase_prompts),
        "receipt_prefix": receipt_prefix,
        "receipt_suffix": receipt_suffix,
        "last_instruction": last_instruction,
    }
    prompt_observer._validate_expectations(expectation)
    if receipt_suffix.count("{{CAO_TERMINAL_ID}}") != 1:
        raise RuntimeError("stream_observer_terminal_template_invalid")
    if any(prompt != prompt.rstrip() for prompt in expectation["phase_prompts"]):
        raise RuntimeError("stream_observer_phase_prompt_not_prepared")

    source_dir = Path(prompt_observer.__file__).resolve(strict=True).parent
    sources: dict[str, bytes] = {}
    for name in SOURCE_NAMES:
        path = source_dir / name
        data = prompt_observer._read_source_file(path, maximum=MAX_PLUGIN_BYTES)
        if _all_imports(data) != IMPORTS[name] | BUILTIN_IMPORTS[name]:
            raise RuntimeError("stream_observer_import_closure_changed")
        sources[name] = data
    installed = dict(sources)
    installed[SOURCE_NAMES[0]] = _relocate_core_imports(
        _specialize_observer(sources[SOURCE_NAMES[0]], nonce)
    )
    relocated_core_imports = {
        "../http-stream-support/opencode_http_prompt_observer.js",
        "../http-stream-support/opencode_http_text_response_monitor.js",
    }
    if (
        _all_imports(installed[SOURCE_NAMES[0]])
        != relocated_core_imports | BUILTIN_IMPORTS[SOURCE_NAMES[0]]
    ):
        raise RuntimeError("stream_observer_relocated_closure_invalid")

    project_loader = project / ".opencode" / "plugins" / "stream-loader.js"
    if project_loader.exists() or project_loader.is_symlink():
        raise RuntimeError("stream_observer_project_loader_exists")
    observer_uri = (private_root / ".opencode" / "plugins" / SOURCE_NAMES[0]).as_uri()
    loader = (
        "import observer from " + json.dumps(observer_uri) + ";\n" "export default observer;\n"
    ).encode("utf-8")
    if len(loader) > 4096:
        raise RuntimeError("stream_observer_loader_too_large")

    created_files: list[tuple[Path, tuple[int, ...]]] = []
    created_dirs: list[tuple[Path, tuple[int, ...]]] = []
    try:
        root_identity = response_observer._create_private_directory(private_root)
        created_dirs.append((private_root, root_identity))
        opencode = private_root / ".opencode"
        opencode_identity = response_observer._create_private_directory(opencode)
        created_dirs.append((opencode, opencode_identity))
        plugins = opencode / "plugins"
        plugins_identity = response_observer._create_private_directory(plugins)
        created_dirs.append((plugins, plugins_identity))
        support = opencode / "http-stream-support"
        support_identity = response_observer._create_private_directory(support)
        created_dirs.append((support, support_identity))

        installed_hashes: dict[str, str] = {}
        for name in SOURCE_NAMES:
            destination_directory = plugins if name == SOURCE_NAMES[0] else support
            path = destination_directory / name
            identity = response_observer._write_new_private_file(
                path, installed[name], maximum=MAX_PLUGIN_BYTES
            )
            created_files.append((path, identity))
            installed_hashes[name] = _digest(installed[name])

        expectation_path = prompt_observer.write_expectations(private_root, expectation)
        expectation_identity = response_observer._path_identity(expectation_path.lstat())
        created_files.append((expectation_path, expectation_identity))
        raw_expectation, read_identity = response_observer._read_private_file_with_identity(
            expectation_path, maximum=MAX_EXPECTATION_BYTES
        )
        if (
            read_identity != expectation_identity
            or response_observer._decode_json(raw_expectation) != expectation
        ):
            raise RuntimeError("stream_observer_expectation_readback_mismatch")
        response_observer._sync_directory(plugins)
        response_observer._sync_directory(support)
        response_observer._sync_directory(opencode)

        return InstallResult(
            project_loader_path=".opencode/plugins/stream-loader.js",
            project_loader_bytes=loader,
            project_loader_sha256=_digest(loader),
            private_root=private_root,
            expectation_path=expectation_path,
            expectation_sha256=_digest(raw_expectation),
            support_directory=support,
            ledger_root=opencode / "http-text-stream-observation",
            source_sha256={name: _digest(sources[name]) for name in SOURCE_NAMES},
            installed_sha256=installed_hashes,
        )
    except BaseException:
        for path, identity in reversed(created_files):
            response_observer._remove_created_file(path, identity)
        for path, identity in reversed(created_dirs):
            response_observer._remove_created_directory(path, identity)
        raise


def _private_json(path: Path, maximum: int) -> tuple[Any, tuple[int, ...]]:
    value, identity = response_observer._read_private_json_with_identity(path, maximum=maximum)
    if type(value) is not dict:
        raise RuntimeError("stream_observer_json_shape_invalid")
    return value, identity


def _bounded_names(path: Path) -> list[str]:
    names: list[str] = []
    with os.scandir(path) as entries:
        for entry in entries:
            if len(names) >= MAX_SAMPLES + 2:
                raise RuntimeError("stream_observer_entry_count_invalid")
            names.append(entry.name)
    return sorted(names)


def _valid_nonnegative(value: Any, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _valid_sample(
    value: Any,
    *,
    nonce: str,
    receipt_digest: str,
    expected_sequence: int,
) -> dict[str, Any] | None:
    if type(value) is not dict or set(value) != SAMPLE_FIELDS:
        return None
    request_ordinal = value.get("request_ordinal")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("nonce") != nonce
        or type(value.get("sequence")) is not int
        or value["sequence"] != expected_sequence
        or not _valid_nonnegative(request_ordinal, MAX_REQUESTS)
        or request_ordinal < 1
        or type(value.get("request_bound")) is not bool
        or value["request_bound"] is not True
        or type(value.get("phase_ordinal")) is not int
        or value["phase_ordinal"] != 2
        or type(value.get("receipt_sha256")) is not str
        or DIGEST_RE.fullmatch(value["receipt_sha256"]) is None
        or value["receipt_sha256"] != receipt_digest
        or value.get("state") not in {"waiting", "active", "finished", "unavailable"}
        or type(value.get("reason")) is not str
        or len(value["reason"]) > 80
        or type(value.get("original_not_replaced")) is not bool
        or value["original_not_replaced"] is not True
        or not _valid_nonnegative(value.get("wire_bytes"), MAX_WIRE_BYTES)
        or not _valid_nonnegative(value.get("text_bytes"), MAX_TEXT_BYTES)
        or not _valid_nonnegative(value.get("text_events"), MAX_EVENTS)
        or not _valid_nonnegative(value.get("event_count"), MAX_EVENTS)
    ):
        return None
    if value["state"] == "active" and value["reason"] != "none":
        return None
    if value["state"] == "finished" and value["reason"] != "stream_finished":
        return None
    if value["state"] == "waiting" and value["reason"] != "none":
        return None
    if value["state"] == "unavailable" and not value["reason"]:
        return None
    return {
        "sequence": expected_sequence,
        "request_ordinal": request_ordinal,
        "state": value["state"],
        "reason": value["reason"],
        "wire_bytes": value["wire_bytes"],
        "text_bytes": value["text_bytes"],
        "text_events": value["text_events"],
        "event_count": value["event_count"],
    }


def _validate_invalid(value: Any, nonce: str) -> None:
    if (
        type(value) is not dict
        or set(value) != INVALID_FIELDS
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("nonce") != nonce
        or value.get("status") != "unavailable"
        or value.get("reason") != "observer_integrity"
    ):
        raise RuntimeError("stream_observer_invalid_marker_malformed")


def _validate_seal(value: Any, nonce: str) -> None:
    if (
        type(value) is not dict
        or set(value) != SEAL_FIELDS
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("nonce") != nonce
        or type(value.get("trace_complete")) is not bool
        or type(value.get("hook_disposed")) is not bool
        or not _valid_nonnegative(value.get("request_count"), MAX_REQUESTS)
        or not _valid_nonnegative(value.get("eligible_requests"), MAX_REQUESTS)
        or not _valid_nonnegative(value.get("sample_count"), MAX_SAMPLES)
        or not _valid_nonnegative(value.get("sample_attempts"), MAX_SAMPLES)
        or value["eligible_requests"] > value["request_count"]
        or value["sample_count"] > value["sample_attempts"]
    ):
        raise RuntimeError("stream_observer_seal_malformed")


def _unavailable(reason: str, *, sample_count: int = 0) -> dict[str, Any]:
    return {
        "observer_status": "unavailable",
        "active": False,
        "reason": reason,
        "sample_count": sample_count,
        "request_bound": False,
        "phase_ordinal": None,
        "receipt_matches": False,
        "state": "unavailable",
        "wire_bytes": None,
        "text_bytes": None,
        "text_events": None,
        "event_count": None,
        "original_not_replaced": False,
        "sequence": None,
        "request_ordinal": None,
        "directory_identity": None,
    }


def _stable_directory_key(path: Path) -> tuple[int, int, int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino, info.st_uid, info.st_mode


def _read_live(path: Path, nonce: str, receipt_digest: str) -> dict[str, Any]:
    if (
        type(nonce) is not str
        or NONCE_RE.fullmatch(nonce) is None
        or type(receipt_digest) is not str
        or DIGEST_RE.fullmatch(receipt_digest) is None
    ):
        raise RuntimeError("stream_observer_input_invalid")
    path = Path(path).absolute()
    root_directory = path.parent.parent.parent
    opencode_directory = path.parent.parent
    ledger_root_directory = path.parent
    if (
        not TERMINAL_RE.fullmatch(path.name)
        or path.parent.name != "http-text-stream-observation"
        or path.parent.parent.name != ".opencode"
        or not _is_private_directory(root_directory)
    ):
        raise RuntimeError("stream_observer_path_invalid")
    root_key = _stable_directory_key(root_directory)
    response_observer._private_directory(opencode_directory)
    response_observer._private_directory(ledger_root_directory)
    opencode_key = _stable_directory_key(opencode_directory)
    ledger_root_key = _stable_directory_key(ledger_root_directory)
    directory_info = response_observer._private_directory(path)
    directory_identity = response_observer._path_identity(directory_info)

    names = _bounded_names(path)
    sample_names = [name for name in names if SAMPLE_RE.fullmatch(name)]
    if len(sample_names) > MAX_SAMPLES:
        raise RuntimeError("stream_observer_sample_count_invalid")
    sample_sequences = [int(SAMPLE_RE.fullmatch(name).group(1)) for name in sample_names]
    allowed = set(sample_names) | (set(names) & {"invalid.json", "seal.json"})
    if (
        set(names) != allowed
        or any(sequence < 1 or sequence > MAX_SAMPLES for sequence in sample_sequences)
        or sample_sequences != sorted(set(sample_sequences))
    ):
        raise RuntimeError("stream_observer_entries_invalid")

    invalid = None
    seal = None
    marker_identities: dict[Path, tuple[int, ...]] = {}
    if "invalid.json" in names:
        invalid, marker_identities[path / "invalid.json"] = _private_json(
            path / "invalid.json", MAX_INVALID_BYTES
        )
        _validate_invalid(invalid, nonce)
    if "seal.json" in names:
        seal, marker_identities[path / "seal.json"] = _private_json(
            path / "seal.json", MAX_SEAL_BYTES
        )
        _validate_seal(seal, nonce)
    if not sample_names:
        raise RuntimeError("stream_observer_samples_missing")

    all_names = list(names)
    total_bytes = 0
    for name in all_names:
        file_path = path / name
        maximum = (
            MAX_SAMPLE_BYTES
            if SAMPLE_RE.fullmatch(name)
            else (MAX_INVALID_BYTES if name == "invalid.json" else MAX_SEAL_BYTES)
        )
        info = file_path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or file_path.is_symlink()
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > maximum
        ):
            raise RuntimeError("stream_observer_file_invalid")
        total_bytes += info.st_size
    if total_bytes > MAX_LEDGER_BYTES:
        raise RuntimeError("stream_observer_ledger_too_large")

    samples: list[dict[str, Any]] = []
    identities = dict(marker_identities)
    for sequence, name in zip(sample_sequences, sample_names):
        sample_path = path / name
        sample, identity = _private_json(sample_path, MAX_SAMPLE_BYTES)
        validated = _valid_sample(
            sample, nonce=nonce, receipt_digest=receipt_digest, expected_sequence=sequence
        )
        if validated is None:
            if isinstance(sample, dict) and sample.get("receipt_sha256") != receipt_digest:
                raise RuntimeError("receipt_mismatch")
            raise RuntimeError("stream_observer_sample_invalid")
        if samples:
            previous = samples[-1]
            if validated["request_ordinal"] != previous["request_ordinal"]:
                raise RuntimeError("ambiguous_request")
            if any(validated[key] < previous[key] for key in COUNTERS):
                raise RuntimeError("stream_observer_counter_regressed")
        if validated["state"] == "finished":
            raise RuntimeError("stream_finished")
        if validated["state"] == "unavailable":
            raise RuntimeError("stream_unavailable")
        identities[sample_path] = identity
        samples.append(validated)

    # Any invalid marker or seal makes this a non-live/closed observer.
    if invalid is not None:
        raise RuntimeError("observer_integrity")
    if seal is not None:
        if seal["sample_count"] != len(sample_names):
            raise RuntimeError("stream_observer_seal_count_mismatch")
        raise RuntimeError("observer_closed")

    latest = samples[-1]
    if latest["state"] != "active":
        raise RuntimeError("stream_not_currently_active")
    if not (
        latest["wire_bytes"] > 0
        and latest["text_bytes"] > 0
        and latest["text_events"] > 0
        and latest["event_count"] > 0
    ):
        raise RuntimeError("stream_positive_text_required")

    for file_path, identity in identities.items():
        if response_observer._path_identity(file_path.lstat()) != identity:
            raise RuntimeError("stream_observer_file_changed")
    if (
        response_observer._path_identity(path.lstat()) != directory_identity
        or _stable_directory_key(ledger_root_directory) != ledger_root_key
        or _stable_directory_key(opencode_directory) != opencode_key
        or _stable_directory_key(root_directory) != root_key
        or _bounded_names(path) != names
    ):
        raise RuntimeError("stream_observer_directory_changed")
    response_observer._private_directory(root_directory)
    response_observer._private_directory(opencode_directory)
    response_observer._private_directory(ledger_root_directory)

    return {
        "observer_status": "active",
        "active": True,
        "reason": "none",
        "sample_count": len(samples),
        "request_bound": True,
        "phase_ordinal": 2,
        "receipt_matches": True,
        "state": "active",
        "wire_bytes": latest["wire_bytes"],
        "text_bytes": latest["text_bytes"],
        "text_events": latest["text_events"],
        "event_count": latest["event_count"],
        "original_not_replaced": True,
        "sequence": latest["sequence"],
        "request_ordinal": latest["request_ordinal"],
        "directory_identity": [directory_info.st_dev, directory_info.st_ino],
    }


def read_live(directory: Path, nonce: str, receipt_digest: str) -> dict[str, Any]:
    """Project a strict live snapshot; never reads CAO/SQLite/PostgreSQL state."""
    try:
        return _read_live(Path(directory), nonce, receipt_digest)
    except (
        OSError,
        RuntimeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ) as exc:
        code = str(exc)
        return _unavailable(code if code in PUBLIC_REASONS else "private_evidence_unavailable")
