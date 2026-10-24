"""Bounded diagnostic comparison of verified OpenCode-v2 text and LAST output.

This observer is diagnostic only. It does not alter the runner's acceptance
predicate, settle a receipt, launch a native process, or return native text.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

SCHEMA = "caos.t111.native-v2-text-probe.v1"
MARKER = "Validado ñ, 中文."
ACK_MARKER = "ACK"
MARKER_KIND = {MARKER: "unicode", ACK_MARKER: "ack"}
TOKEN_PATTERN = r"(?:CAO-TURN-RECEIPT-|CAO_TURN_RECEIPT_)[0-9a-f]{32}"
TOKEN_RE = re.compile(TOKEN_PATTERN)
TERMINAL_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
AGENT_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
SCHEMA_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}")

CHILD_BUDGET_SECONDS = 3.0
INPUT_TEXT_CAP = 65536
REQUEST_CAP = 256 * 1024
CHILD_OUTPUT_CAP = 16384
DB_CAP = 64 * 1024 * 1024
TEXT_CAP = 65536
PART_CAP = 16
NONCE_CANDIDATE_CAP = 8
REQUEST_PREFIX = ".t111-v2-text-"

REASONS = frozenset(
    {
        "deadline",
        "invalid_input",
        "input_size_limit",
        "source_root_invalid",
        "run_root_invalid",
        "cao_home_binding",
        "database_config_invalid",
        "request_size_limit",
        "request_file_unavailable",
        "request_cleanup_failed",
        "child_timeout",
        "capture_unavailable",
        "child_output_invalid",
        "child_target_mismatch",
        "database_target_unavailable",
        "pg_read_unavailable",
        "pg_receipt_not_verified",
        "active_nonce_not_unique",
        "nonce_candidate_limit",
        "ownership_missing",
        "major_mismatch",
        "owned_root_invalid",
        "owned_database_invalid",
        "native_schema_mismatch",
        "user_parent_missing",
        "user_parent_not_unique",
        "user_parent_not_current",
        "native_text_size_limit",
        "native_parts_limit",
        "assistant_receipt_not_unique",
        "native_agent_metadata_invalid",
        "native_agent_mismatch",
        "native_database_changed",
        "probe_dependency_error",
    }
)

CLASSIFICATIONS = frozenset(
    {
        "unavailable",
        "native_equals_rendered",
        "multiple_native_text_parts",
        "native_marker_rendered_missing",
        "rendered_marker_native_missing",
        "native_text_contains_linebreak",
        "native_text_differs_from_rendered",
        "ack_not_exact",
    }
)

REQUEST_KEYS = frozenset(
    {
        "source_root",
        "run_root",
        "cao_home",
        "database_config_file",
        "terminal_id",
        "viewport",
        "rendered",
        "expected_agent",
        "expected_marker",
        "stop",
    }
)


class ProbeFailure(Exception):
    """A fixed, redacted reason code for an unavailable native observation."""

    def __init__(self, reason: str):
        if reason not in REASONS:
            reason = "probe_dependency_error"
        self.reason = reason
        super().__init__(reason)


def _shape(text: str, expected_marker: str = MARKER) -> dict[str, Any]:
    compact = "".join(text.split())
    expected_compact = "".join(expected_marker.split())
    standalone = bool(re.search(r"(?m)^[ \t]*" + re.escape(expected_marker) + r"[ \t]*$", text))
    return {
        "characters": len(text),
        "lines": len(text.splitlines()),
        "exact_marker": text == expected_marker,
        "standalone_marker_line": standalone,
        "contains_marker": expected_marker in text,
        "compact_marker": compact == expected_compact,
    }


def _base_result(rendered: str = "", expected_marker: str = MARKER) -> dict[str, Any]:
    if type(expected_marker) is not str or expected_marker not in MARKER_KIND:
        expected_marker = MARKER
    if type(rendered) is not str or len(rendered) > INPUT_TEXT_CAP:
        rendered = ""
    else:
        try:
            if len(rendered.encode("utf-8", errors="strict")) > INPUT_TEXT_CAP:
                rendered = ""
        except UnicodeError:
            rendered = ""
    return {
        "schema": SCHEMA,
        "target_kind": MARKER_KIND[expected_marker],
        "diagnostic_only": True,
        "status": "unavailable",
        "reason": "probe_dependency_error",
        "raw_text_exported": False,
        "raw_model_response_observed": False,
        "acceptance_input_changed": False,
        "active_receipt_bound": False,
        "owned_major2_root": False,
        "user_parent_bound": False,
        "assistant_agent_matches": None,
        "assistant_turn_completed": False,
        "native_equals_rendered": False,
        "active_nonce_candidate_count": 0,
        "native_text_part_count": 0,
        "native_public": _shape("", expected_marker),
        "rendered_public": _shape(rendered, expected_marker),
        "text_part_shapes": [],
        "classification": "unavailable",
        "elapsed_ms": 0,
        "within_three_seconds": False,
        "within_original_deadline": False,
    }


def _unavailable(result: dict[str, Any], reason: str) -> dict[str, Any]:
    result["status"] = "unavailable"
    result["reason"] = reason if reason in REASONS else "probe_dependency_error"
    result["classification"] = "unavailable"
    return result


def _finite_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _canonical_path(value: Any, *, directory: bool) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise ProbeFailure("invalid_input")
    path = Path(value)
    if not path.is_absolute():
        raise ProbeFailure("invalid_input")
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except (OSError, RuntimeError, ValueError):
        raise ProbeFailure("invalid_input") from None
    if path != resolved:
        raise ProbeFailure("invalid_input")
    if directory:
        if not stat.S_ISDIR(metadata.st_mode):
            raise ProbeFailure("invalid_input")
    elif not stat.S_ISREG(metadata.st_mode):
        raise ProbeFailure("invalid_input")
    return path


def _private_directory(path: Path, *, exact_mode: int | None = None) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise ProbeFailure("run_root_invalid") from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.getuid()
        or (exact_mode is not None and stat.S_IMODE(info.st_mode) != exact_mode)
        or (exact_mode is None and info.st_mode & 0o022)
    ):
        raise ProbeFailure("run_root_invalid")


def _strict_request_paths(request: dict[str, Any]) -> tuple[Path, Path, Path, Path]:
    try:
        source_root = _canonical_path(request.get("source_root"), directory=True)
    except ProbeFailure:
        raise ProbeFailure("source_root_invalid")
    try:
        run_root = _canonical_path(request.get("run_root"), directory=True)
    except ProbeFailure:
        raise ProbeFailure("run_root_invalid")
    try:
        cao_home = _canonical_path(request.get("cao_home"), directory=True)
    except ProbeFailure:
        raise ProbeFailure("cao_home_binding")
    try:
        config = _canonical_path(request.get("database_config_file"), directory=False)
    except ProbeFailure:
        raise ProbeFailure("database_config_invalid")
    if not (source_root / "src").is_dir():
        raise ProbeFailure("source_root_invalid")
    if run_root == source_root or run_root.is_relative_to(source_root):
        raise ProbeFailure("run_root_invalid")
    _private_directory(run_root, exact_mode=0o700)
    expected_home = run_root / "home"
    expected_aws = expected_home / ".aws"
    expected_cao = expected_aws / "cli-agent-orchestrator"
    if cao_home != expected_cao:
        raise ProbeFailure("cao_home_binding")
    try:
        for directory in (expected_home, expected_aws, cao_home):
            _private_directory(directory, exact_mode=0o700)
    except ProbeFailure:
        raise ProbeFailure("cao_home_binding") from None
    if not config.is_relative_to(run_root):
        raise ProbeFailure("database_config_invalid")
    try:
        config_info = config.lstat()
    except OSError:
        raise ProbeFailure("database_config_invalid") from None
    if (
        not stat.S_ISREG(config_info.st_mode)
        or stat.S_ISLNK(config_info.st_mode)
        or config_info.st_uid != os.getuid()
        or stat.S_IMODE(config_info.st_mode) != 0o600
        or config_info.st_nlink != 1
        or config_info.st_size > 65536
    ):
        raise ProbeFailure("database_config_invalid")
    return source_root, run_root, cao_home, config


def _validate_request(request: Any) -> dict[str, Any]:
    if type(request) is not dict or set(request) != REQUEST_KEYS:
        raise ProbeFailure("invalid_input")
    if any(type(request.get(key)) is not str for key in REQUEST_KEYS - {"stop"}):
        raise ProbeFailure("invalid_input")
    if request["expected_marker"] not in MARKER_KIND:
        raise ProbeFailure("invalid_input")
    if (
        TERMINAL_RE.fullmatch(request["terminal_id"]) is None
        or AGENT_RE.fullmatch(request["expected_agent"]) is None
        or any("\x00" in request[key] for key in REQUEST_KEYS - {"stop"})
    ):
        raise ProbeFailure("invalid_input")
    if not _finite_number(request["stop"]):
        raise ProbeFailure("invalid_input")
    for key in ("viewport", "rendered"):
        try:
            size = len(request[key].encode("utf-8", errors="strict"))
        except UnicodeError:
            raise ProbeFailure("invalid_input") from None
        if size > INPUT_TEXT_CAP:
            raise ProbeFailure("input_size_limit")
    _strict_request_paths(request)
    return request


def _owned_database(
    run_root: Path, cao_home: Path, terminal_id: str
) -> tuple[Path, tuple[int, int]]:
    """Return only the exact active major-2 database claimed for this terminal."""
    try:
        from cli_agent_orchestrator.utils.opencode_ownership import PrivateConfigOwnership

        ownership = PrivateConfigOwnership(cao_home, terminal_id)
        document = ownership._read()
    except Exception:
        raise ProbeFailure("ownership_missing") from None
    if not isinstance(document, dict) or document.get("phase") != "active":
        raise ProbeFailure("ownership_missing")
    claim = document.get("claim")
    if not isinstance(claim, dict):
        raise ProbeFailure("ownership_missing")
    if type(claim.get("major")) is not int or claim.get("major") != 2:
        raise ProbeFailure("major_mismatch")
    try:
        owned_root = ownership._root(claim)
    except Exception:
        raise ProbeFailure("owned_root_invalid") from None
    if owned_root is None:
        raise ProbeFailure("owned_root_invalid")
    try:
        if owned_root.resolve(strict=True) != owned_root or not owned_root.is_relative_to(run_root):
            raise ProbeFailure("owned_root_invalid")
        _private_directory(owned_root, exact_mode=0o700)
        database = owned_root / "data" / "opencode" / "opencode.db"
        current = run_root
        for component in database.relative_to(run_root).parts[:-1]:
            current = current / component
            _private_directory(current)
        info = database.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_size > DB_CAP
        ):
            raise ProbeFailure("owned_database_invalid")
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(database) + suffix)
            if not sidecar.exists() and not sidecar.is_symlink():
                continue
            side_info = sidecar.lstat()
            if (
                not stat.S_ISREG(side_info.st_mode)
                or stat.S_ISLNK(side_info.st_mode)
                or side_info.st_uid != os.getuid()
                or side_info.st_nlink != 1
                or side_info.st_size > DB_CAP
            ):
                raise ProbeFailure("owned_database_invalid")
    except ProbeFailure:
        raise
    except (OSError, RuntimeError, ValueError):
        raise ProbeFailure("owned_database_invalid") from None
    return database, (info.st_dev, info.st_ino)


def _native_text_parts(
    source_root: Path,
    database: Path,
    nonce: str,
    expected_agent: str,
    stop: float,
    *,
    progress: dict[str, bool] | None = None,
) -> tuple[str, ...]:
    """Use the source-pinned, read-only native TEXT primitive for major 2."""
    if time.monotonic() >= stop:
        raise ProbeFailure("deadline")
    fixtures = source_root / "specs/025-provider-mcp-profiles/fixtures"
    helper_path = fixtures / "native_v2_store.py"
    try:
        if helper_path.resolve(strict=True) != helper_path or not stat.S_ISREG(
            helper_path.lstat().st_mode
        ):
            raise ProbeFailure("source_root_invalid")
        if str(fixtures) not in sys.path:
            sys.path.insert(0, str(fixtures))
        import importlib

        native_store = importlib.import_module("native_v2_store")
        imported_path = Path(native_store.__file__)
        if imported_path.resolve(strict=True) != helper_path or not stat.S_ISREG(
            imported_path.lstat().st_mode
        ):
            raise ProbeFailure("source_root_invalid")
    except ProbeFailure:
        raise
    except Exception:
        raise ProbeFailure("source_root_invalid") from None

    try:
        parts = native_store._native_parts(database, nonce, stop, expected_agent=expected_agent)
    except native_store.ProbeError as exc:
        reason = {
            "deadline": "deadline",
            "native_schema_mismatch": "native_schema_mismatch",
            "user_not_unique": "user_parent_not_unique",
            "user_not_current": "user_parent_not_current",
            "parts_limit": "native_parts_limit",
            "text_size_or_type": "native_text_size_limit",
            "total_size": "native_text_size_limit",
            "text_snapshot_changed": "native_database_changed",
            "assistant_receipt_not_unique": "assistant_receipt_not_unique",
            "expected_agent_invalid": "native_agent_metadata_invalid",
            "native_agent_metadata_invalid": "native_agent_metadata_invalid",
            "native_agent_mismatch": "native_agent_mismatch",
        }.get(str(exc), "probe_dependency_error")
        if reason == "user_parent_not_unique" and exc.user_parent_status == "missing":
            reason = "user_parent_missing"
        if progress is not None and reason in {
            "native_parts_limit",
            "native_text_size_limit",
            "native_database_changed",
            "assistant_receipt_not_unique",
            "native_agent_metadata_invalid",
            "native_agent_mismatch",
        }:
            progress["user_parent_bound"] = True
        if progress is not None and reason in {
            "native_agent_metadata_invalid",
            "native_agent_mismatch",
        }:
            progress["assistant_turn_completed"] = True
        if progress is not None and reason == "native_agent_mismatch":
            progress["assistant_agent_matches"] = False
        raise ProbeFailure(reason) from None
    except Exception:
        raise ProbeFailure("native_schema_mismatch") from None

    if type(parts) not in (tuple, list) or not 1 <= len(parts) <= PART_CAP:
        raise ProbeFailure("native_parts_limit")
    total = 0
    for part in parts:
        if type(part) is not str:
            raise ProbeFailure("native_text_size_limit")
        try:
            size = len(part.encode("utf-8", errors="strict"))
        except UnicodeError:
            raise ProbeFailure("native_text_size_limit") from None
        if size > TEXT_CAP:
            raise ProbeFailure("native_text_size_limit")
        total += size
        if total > TEXT_CAP:
            raise ProbeFailure("native_text_size_limit")
    if progress is not None:
        progress.update(
            user_parent_bound=True,
            assistant_turn_completed=True,
            assistant_agent_matches=True,
        )
    return tuple(parts)


def _receipt_row(target: Any, terminal_id: str, stop: float) -> list[tuple[Any, Any]]:
    if stop - time.monotonic() < 0.25:
        raise ProbeFailure("deadline")
    schema = getattr(target, "schema", None)
    if type(schema) is not str or SCHEMA_RE.fullmatch(schema) is None:
        raise ProbeFailure("pg_read_unavailable")
    try:
        import psycopg
        from psycopg import sql

        params = target.connection_parameters()
        params.update(
            connect_timeout=1,
            options=(
                "-c statement_timeout=500 -c lock_timeout=500 "
                "-c default_transaction_read_only=on"
            ),
            application_name="t111-native-v2-text-readonly",
        )
        with psycopg.connect(**params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT receipt_sha256,phase FROM {}.terminal_turn_receipts "
                        "WHERE terminal_id=%s LIMIT 2"
                    ).format(sql.Identifier(schema)),
                    (terminal_id,),
                )
                rows = cursor.fetchall()
        if time.monotonic() >= stop:
            raise ProbeFailure("deadline")
        return rows
    except ProbeFailure:
        raise
    except Exception:
        raise ProbeFailure("pg_read_unavailable") from None


def _receipt_digest(target: Any, terminal_id: str, stop: float) -> str:
    rows = _receipt_row(target, terminal_id, stop)
    if (
        type(rows) is not list
        or len(rows) != 1
        or type(rows[0]) not in (tuple, list)
        or len(rows[0]) != 2
        or type(rows[0][0]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", rows[0][0]) is None
        or rows[0][1] != "result_verified"
    ):
        raise ProbeFailure("pg_receipt_not_verified")
    return rows[0][0]


def _without_nonce_line(text: str, nonce: str) -> str:
    matches = list(re.finditer(r"(?m)^[ \t]*" + re.escape(nonce) + r"[ \t]*$", text))
    if len(matches) != 1:
        return text
    match = matches[0]
    before = text[: match.start()]
    after = text[match.end() :]
    if before.endswith("\r\n"):
        before = before[:-2]
    elif before.endswith(("\r", "\n")):
        before = before[:-1]
    if after.startswith("\r\n"):
        after = after[2:]
    elif after.startswith(("\r", "\n")):
        after = after[1:]
    return before + after


def _observe_payload(request: Any, target: Any, *, validated: bool = False) -> dict[str, Any]:
    rendered = request.get("rendered", "") if type(request) is dict else ""
    expected_marker = request.get("expected_marker", MARKER) if type(request) is dict else MARKER
    if type(expected_marker) is not str or expected_marker not in MARKER_KIND:
        expected_marker = MARKER
    result = _base_result(rendered, expected_marker)
    progress: dict[str, bool] = {}
    try:
        if not validated:
            request = _validate_request(request)
        _source_root = Path(request["source_root"])
        run_root = Path(request["run_root"])
        cao_home = Path(request["cao_home"])
        stop = float(request["stop"])
        if time.monotonic() >= stop:
            raise ProbeFailure("deadline")
        if target is None:
            raise ProbeFailure("database_target_unavailable")
        digest = _receipt_digest(target, request["terminal_id"], stop)
        candidates = set(TOKEN_RE.findall(request["viewport"]))
        result["active_nonce_candidate_count"] = len(candidates)
        if len(candidates) > NONCE_CANDIDATE_CAP:
            raise ProbeFailure("nonce_candidate_limit")
        active = [
            candidate
            for candidate in candidates
            if hashlib.sha256(candidate.encode("utf-8")).hexdigest() == digest
        ]
        if len(active) != 1:
            raise ProbeFailure("active_nonce_not_unique")
        nonce = active[0]
        result["active_receipt_bound"] = True
        database, identity = _owned_database(run_root, cao_home, request["terminal_id"])
        result["owned_major2_root"] = True
        parts = _native_text_parts(
            _source_root,
            database,
            nonce,
            request["expected_agent"],
            stop,
            progress=progress,
        )
        result.update(progress)
        final = database.lstat()
        if (final.st_dev, final.st_ino) != identity or not stat.S_ISREG(final.st_mode):
            raise ProbeFailure("native_database_changed")
        if expected_marker == ACK_MARKER:
            public_parts = [_without_nonce_line(part, nonce) for part in parts]
            native_public = "".join(public_parts)
            part_shapes = [_shape(part, expected_marker) for part in public_parts]
            native_equals_rendered = native_public == request["rendered"]
        else:
            public_parts = [
                re.sub(r"(?m)^[ \t]*" + re.escape(nonce) + r"[ \t]*$", "", part) for part in parts
            ]
            native_public = "".join(public_parts).strip()
            part_shapes = [_shape(part.strip(), expected_marker) for part in public_parts]
            native_equals_rendered = native_public == request["rendered"].strip()
        native_shape = _shape(native_public, expected_marker)
        rendered_shape = _shape(request["rendered"], expected_marker)
        result.update(
            status="observed",
            reason=None,
            native_equals_rendered=native_equals_rendered,
            native_text_part_count=len(parts),
            native_public=native_shape,
            rendered_public=rendered_shape,
            text_part_shapes=part_shapes,
            classification=_classify(
                native_public,
                native_shape,
                rendered_shape,
                len(parts),
                expected_marker,
                native_equals_rendered=native_equals_rendered,
            ),
            raw_model_response_observed=False,
        )
    except ProbeFailure as exc:
        result.update(progress)
        _unavailable(result, exc.reason)
    except Exception:
        _unavailable(result, "probe_dependency_error")
    return result


def _classify(
    native: str,
    native_shape: dict[str, Any],
    rendered_shape: dict[str, Any],
    parts: int,
    expected_marker: str = MARKER,
    *,
    native_equals_rendered: bool = False,
) -> str:
    if parts > 1:
        return "multiple_native_text_parts"
    if (
        native_shape["compact_marker"]
        and not native_shape["standalone_marker_line"]
        and ("\n" in native or "\r" in native)
    ):
        return "native_text_contains_linebreak"
    if expected_marker == ACK_MARKER:
        if native_shape["exact_marker"] and rendered_shape["exact_marker"]:
            return "native_equals_rendered"
        if native_shape["exact_marker"]:
            return "native_marker_rendered_missing"
        if not native and rendered_shape["exact_marker"]:
            return "rendered_marker_native_missing"
        return "ack_not_exact"
    if native_equals_rendered:
        return "native_equals_rendered"
    if native == MARKER and rendered_shape["exact_marker"]:
        return "native_equals_rendered"
    if native == "" or not native_shape["contains_marker"]:
        if rendered_shape["contains_marker"]:
            return "rendered_marker_native_missing"
        return "native_text_differs_from_rendered"
    if native_shape["contains_marker"] and not rendered_shape["contains_marker"]:
        return "native_marker_rendered_missing"
    return "native_text_differs_from_rendered"


def _read_request(path: Path, expected_run_root: Path) -> dict[str, Any]:
    try:
        if (
            not path.is_absolute()
            or path.parent != expected_run_root
            or path.parent.resolve(strict=True) != path.parent
            or path.resolve(strict=True) != path
            or not path.name.startswith(REQUEST_PREFIX)
        ):
            raise ProbeFailure("request_file_unavailable")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or info.st_size > REQUEST_CAP
            ):
                raise ProbeFailure("request_file_unavailable")
            payload = os.read(descriptor, REQUEST_CAP + 1)
            if len(payload) != info.st_size or len(payload) > REQUEST_CAP:
                raise ProbeFailure("request_file_unavailable")
        finally:
            os.close(descriptor)
        request = _json_object(payload)
        return _validate_request(request)
    except ProbeFailure:
        raise
    except Exception:
        raise ProbeFailure("request_file_unavailable") from None


def _json_object(payload: bytes) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result

    try:
        return json.loads(payload.decode("utf-8", errors="strict"), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError):
        raise ProbeFailure("request_file_unavailable") from None


def _child_entry(request_path: str) -> int:
    """Run inside the pinned isolated Python child; stdout is one safe record."""
    started = time.monotonic()
    result = _base_result()
    try:
        path = Path(request_path)
        # The request is private and was created below this verified root.
        rough_run_root = path.parent
        request = _read_request(path, rough_run_root)
        result = _base_result(request["rendered"], request["expected_marker"])
        source_root = Path(request["source_root"])
        run_root = Path(request["run_root"])
        cao_home = Path(request["cao_home"])
        config = Path(request["database_config_file"])
        if path.parent != run_root:
            raise ProbeFailure("request_file_unavailable")
        if Path(__file__).resolve(strict=True) != (
            source_root / "specs/025-provider-mcp-profiles/fixtures/native_v2_text_probe.py"
        ):
            raise ProbeFailure("source_root_invalid")
        os.environ.clear()
        os.environ.update(
            {
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "HOME": str(run_root / "home"),
                "CAO_HOME_DIR": str(cao_home),
                "CAO_DATABASE_CONFIG_FILE": str(config),
            }
        )
        fixtures = source_root / "specs/025-provider-mcp-profiles/fixtures"
        sys.path[:0] = [str(source_root / "src"), str(fixtures)]
        from cli_agent_orchestrator.clients.postgres.config import DatabaseTarget

        target = DatabaseTarget.from_settings("main")
        result = _observe_payload(request, target, validated=True)
    except ProbeFailure as exc:
        _unavailable(result, exc.reason)
    except Exception:
        _unavailable(result, "database_target_unavailable")
    elapsed = max(0.0, time.monotonic() - started)
    stop = request.get("stop") if "request" in locals() and type(request) is dict else started
    deadline = stop if type(stop) in (int, float) and math.isfinite(stop) else started
    result["elapsed_ms"] = min(2**31 - 1, int(round(elapsed * 1000)))
    result["within_three_seconds"] = elapsed <= CHILD_BUDGET_SECONDS
    result["within_original_deadline"] = time.monotonic() <= deadline
    try:
        sys.stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except Exception:
        return 2
    return 0


def _strict_result(value: Any) -> dict[str, Any] | None:
    if type(value) is not dict or set(value) != set(_base_result()):
        return None
    if (
        value["schema"] != SCHEMA
        or value["target_kind"] not in ("unicode", "ack")
        or value["diagnostic_only"] is not True
        or value["status"] not in ("observed", "unavailable")
        or value["raw_text_exported"] is not False
        or value["raw_model_response_observed"] is not False
        or value["acceptance_input_changed"] is not False
        or value["classification"] not in CLASSIFICATIONS
        or type(value["elapsed_ms"]) is not int
        or not 0 <= value["elapsed_ms"] <= 2**31 - 1
    ):
        return None
    if (value["status"] == "observed") != (value["reason"] is None):
        return None
    if value["status"] == "unavailable" and value["reason"] not in REASONS:
        return None
    bools = (
        "active_receipt_bound",
        "owned_major2_root",
        "user_parent_bound",
        "assistant_turn_completed",
        "native_equals_rendered",
        "within_three_seconds",
        "within_original_deadline",
    )
    if any(type(value[key]) is not bool for key in bools):
        return None
    agent = value["assistant_agent_matches"]
    if agent is not None and type(agent) is not bool:
        return None
    limits = {
        "active_nonce_candidate_count": NONCE_CANDIDATE_CAP,
        "native_text_part_count": PART_CAP,
    }
    if any(
        type(value[key]) is not int or not 0 <= value[key] <= limit for key, limit in limits.items()
    ):
        return None
    if any(_strict_shape(value[key]) is None for key in ("native_public", "rendered_public")):
        return None
    parts = value["text_part_shapes"]
    if (
        type(parts) is not list
        or len(parts) > PART_CAP
        or any(_strict_shape(part) is None for part in parts)
        or (value["status"] == "observed" and len(parts) != value["native_text_part_count"])
    ):
        return None
    if value["status"] == "observed" and (
        not all(
            value[key] is True
            for key in (
                "active_receipt_bound",
                "owned_major2_root",
                "user_parent_bound",
                "assistant_agent_matches",
                "assistant_turn_completed",
                "within_three_seconds",
                "within_original_deadline",
            )
        )
        or value["elapsed_ms"] > CHILD_BUDGET_SECONDS * 1000
    ):
        return None
    return value


def _strict_shape(value: Any) -> dict[str, Any] | None:
    if type(value) is not dict or set(value) != set(_shape("")):
        return None
    if any(
        type(value[key]) is not int or not 0 <= value[key] <= TEXT_CAP
        for key in ("characters", "lines")
    ):
        return None
    if any(type(value[key]) is not bool for key in set(value) - {"characters", "lines"}):
        return None
    return value


def _make_request_file(run_root: Path, request: dict[str, Any]) -> tuple[Path, tuple[int, int]]:
    try:
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ProbeFailure("invalid_input") from None
    if len(encoded) > REQUEST_CAP:
        raise ProbeFailure("request_size_limit")
    descriptor = -1
    path: Path | None = None
    created = False
    try:
        descriptor, name = tempfile.mkstemp(prefix=REQUEST_PREFIX, dir=run_root)
        path = Path(name)
        os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise ProbeFailure("request_file_unavailable")
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        final = path.lstat()
        if (final.st_dev, final.st_ino) != (info.st_dev, info.st_ino):
            raise ProbeFailure("request_file_unavailable")
        created = True
        return path, (info.st_dev, info.st_ino)
    except ProbeFailure:
        raise
    except Exception:
        raise ProbeFailure("request_file_unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not created and path is not None and path.exists():
            try:
                path.unlink()
            except OSError:
                pass


def _remove_request(path: Path, identity: tuple[int, int], run_root: Path) -> bool:
    try:
        if path.parent != run_root:
            return False
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != identity
        ):
            return False
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


_CHILD_BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv[1]);"
    "import native_v2_text_probe as probe;"
    "raise SystemExit(probe._child_entry(sys.argv[2]))"
)


def observe_bounded(
    *,
    python: str,
    source_root: str | Path,
    run_root: str | Path,
    cao_home: str | Path,
    terminal_id: str,
    database_config_file: str | Path,
    viewport: str,
    rendered: str,
    expected_agent: str,
    expected_marker: str = MARKER,
    deadline: float,
    capture: Callable[[list[str], float, int], bytes],
) -> dict[str, Any]:
    """Compare native v2 TEXT parts to already-parsed LAST in a reaped child.

    ``capture`` must be the source-pinned ``capture_bytes`` reader: it must
    enforce ``stop``, kill and reap the child on every path, and bound stdout.
    ``expected_marker`` is restricted to the fixed Unicode marker or literal ACK.
    The temporary request remains until that callback returns or raises.
    """
    started = time.monotonic()
    safe_rendered = rendered if type(rendered) is str else ""
    marker_valid = type(expected_marker) is str and expected_marker in MARKER_KIND
    safe_marker = expected_marker if marker_valid else MARKER
    result = _base_result(safe_rendered, safe_marker)
    if not marker_valid:
        return _finish(_unavailable(result, "invalid_input"), started, deadline)
    if not _finite_number(deadline):
        return _finish(_unavailable(result, "invalid_input"), started, deadline)
    if deadline <= started:
        return _finish(_unavailable(result, "deadline"), started, deadline)
    stop = min(float(deadline), started + CHILD_BUDGET_SECONDS)
    request_path: Path | None = None
    request_identity: tuple[int, int] | None = None
    run_path: Path | None = None
    capture_result: Any = None
    capture_error = False
    cleanup_ok = True
    try:
        if (
            type(python) is not str
            or not python
            or not Path(python).is_absolute()
            or not Path(python).is_file()
            or type(terminal_id) is not str
            or TERMINAL_RE.fullmatch(terminal_id) is None
            or type(expected_agent) is not str
            or AGENT_RE.fullmatch(expected_agent) is None
            or type(viewport) is not str
            or type(rendered) is not str
            or not callable(capture)
        ):
            raise ProbeFailure("invalid_input")
        source_path = _canonical_path(source_root, directory=True)
        run_path = _canonical_path(run_root, directory=True)
        cao_path = _canonical_path(cao_home, directory=True)
        config_path = _canonical_path(database_config_file, directory=False)
        request = {
            "source_root": str(source_path),
            "run_root": str(run_path),
            "cao_home": str(cao_path),
            "database_config_file": str(config_path),
            "terminal_id": terminal_id,
            "viewport": viewport,
            "rendered": rendered,
            "expected_agent": expected_agent,
            "expected_marker": expected_marker,
            "stop": stop,
        }
        _validate_request(request)
        request_path, request_identity = _make_request_file(run_path, request)
        fixtures_path = source_path / "specs/025-provider-mcp-profiles/fixtures"
        argv = [python, "-I", "-c", _CHILD_BOOTSTRAP, str(fixtures_path), str(request_path)]
        try:
            capture_result = capture(argv, stop, CHILD_OUTPUT_CAP)
        except (TimeoutError, subprocess.TimeoutExpired):
            capture_error = True
            raise ProbeFailure("child_timeout") from None
        except ValueError as exc:
            capture_error = True
            error = str(exc)
            if error in {"reader_deadline", "capture_deadline"}:
                reason = "child_timeout"
            elif error == "capture_byte_limit":
                reason = "child_output_invalid"
            else:
                reason = "capture_unavailable"
            raise ProbeFailure(reason) from None
        except Exception:
            capture_error = True
            raise ProbeFailure("capture_unavailable") from None
    except ProbeFailure as exc:
        _unavailable(result, exc.reason)
    except Exception:
        _unavailable(result, "probe_dependency_error")
    finally:
        if request_path is not None and request_identity is not None and run_path is not None:
            cleanup_ok = _remove_request(request_path, request_identity, run_path)
    if not cleanup_ok:
        result = _unavailable(result, "request_cleanup_failed")
    elif not capture_error and capture_result is not None:
        try:
            if type(capture_result) is not bytes or len(capture_result) > CHILD_OUTPUT_CAP:
                raise ValueError("invalid child result")
            child_result = _strict_result(_json_object(capture_result))
            if child_result is None:
                raise ValueError("invalid child projection")
            if child_result["target_kind"] != MARKER_KIND[expected_marker]:
                result = _unavailable(result, "child_target_mismatch")
            else:
                result = child_result
        except Exception:
            result = _unavailable(result, "child_output_invalid")
    if time.monotonic() > stop or time.monotonic() > deadline:
        result = _unavailable(result, "deadline")
    return _finish(result, started, deadline, stop=stop)


def _finish(
    result: dict[str, Any], started: float, deadline: Any, *, stop: float | None = None
) -> dict[str, Any]:
    finished = time.monotonic()
    elapsed = max(0.0, finished - started)
    within_deadline = _finite_number(deadline) and finished <= deadline
    within_child = elapsed <= CHILD_BUDGET_SECONDS and (stop is None or finished <= stop)
    result["elapsed_ms"] = min(2**31 - 1, int(round(elapsed * 1000)))
    result["within_three_seconds"] = within_child
    result["within_original_deadline"] = within_deadline
    if result["status"] == "observed" and not (within_child and within_deadline):
        return _unavailable(result, "deadline")
    return result
