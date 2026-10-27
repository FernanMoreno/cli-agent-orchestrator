"""Read-only PG-bound projection of the diagnostic HTTP TEXT stream ledger.

This module has no SQLite imports and never changes PG or ledger state. The HTTP
observer is diagnostic evidence; this projection does not attest served-model
identity or authorize task cancellation.
"""

from __future__ import annotations

import dataclasses
import math
import re
import time
from collections.abc import Mapping
from pathlib import Path
from test.live_provider import opencode_http_text_stream_observer as http_observer
from typing import Any

GENERATION_RE = re.compile(r"[a-f0-9]{32}\Z")
NONCE_RE = re.compile(r"[a-f0-9]{32}\Z")
DIGEST_RE = re.compile(r"[a-f0-9]{64}\Z")
SCHEMA_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
TERMINAL_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
MAX_REQUESTS = 16
MAX_SAMPLES = 128
MAX_WIRE_BYTES = 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_EVENTS = 4096


@dataclasses.dataclass(frozen=True)
class Snapshot:
    """Safe counters bound to one live API turn, PG receipt, and HTTP ledger."""

    terminal_id: str
    generation: str
    receipt_sha256: str
    directory_identity: tuple[int, int]
    request_ordinal: int
    sequence: int
    text_bytes: int
    text_events: int
    wire_bytes: int
    event_count: int


def _valid_stop(stop: Any) -> bool:
    if type(stop) not in (int, float):
        return False
    try:
        return math.isfinite(stop) and time.monotonic() < stop
    except (OverflowError, TypeError, ValueError):
        return False


def _api_generation(metadata: Any, terminal_id: str) -> str | None:
    """Validate the actual GET /terminals/{id} response with nested turn data."""
    if type(metadata) is not dict:
        return None
    turn = metadata.get("turn")
    if type(turn) is not dict:
        return None
    generation = turn.get("generation")
    if not (
        metadata.get("id") == terminal_id
        and metadata.get("provider") == "opencode_cli"
        and metadata.get("status") == "processing"
        and turn.get("terminal_id") == terminal_id
        and turn.get("provider") == "opencode_cli"
        and turn.get("state") == "pending"
        and type(generation) is str
        and GENERATION_RE.fullmatch(generation) is not None
    ):
        return None
    return generation


def _active_receipt(target: Any, terminal_id: str, generation: str, stop: float):
    """Read one sent PG receipt using bounded, identifier-safe SELECT only."""
    if not _valid_stop(stop):
        return None
    schema = getattr(target, "schema", None)
    connection_parameters = getattr(target, "connection_parameters", None)
    if (
        type(schema) is not str
        or SCHEMA_RE.fullmatch(schema) is None
        or not callable(connection_parameters)
    ):
        return None

    try:
        import psycopg
        from psycopg import sql

        params = connection_parameters()
        if not isinstance(params, Mapping):
            return None
        connect_args = dict(params)
        connect_args.update(
            connect_timeout=1,
            options=(
                "-c statement_timeout=500 -c lock_timeout=500 "
                "-c default_transaction_read_only=on"
            ),
            application_name="t187-http-stream-readonly",
        )
        query = sql.SQL(
            "SELECT generation,receipt_sha256,phase,provider "
            "FROM {}.terminal_turn_receipts WHERE terminal_id=%s LIMIT 2"
        ).format(sql.Identifier(schema))
        with psycopg.connect(**connect_args) as db:
            rows = db.execute(query, (terminal_id,)).fetchall()
    except Exception:
        return None

    if not _valid_stop(stop) or type(rows) not in (list, tuple):
        return None
    if len(rows) != 1:
        return None
    row = rows[0]
    if type(row) not in (list, tuple) or len(row) != 4:
        return None
    row_generation, digest, phase, provider = row
    if row_generation != generation or phase != "sent":
        return None
    if (
        type(row_generation) is not str
        or GENERATION_RE.fullmatch(row_generation) is None
        or provider != "opencode_cli"
        or type(digest) is not str
        or DIGEST_RE.fullmatch(digest) is None
    ):
        return None
    return row_generation, digest


def _snapshot_from_live(
    live: Any,
    *,
    terminal_id: str,
    generation: str,
    receipt_sha256: str,
) -> Snapshot | None:
    if type(live) is not dict:
        return None
    if not (
        live.get("observer_status") == "active"
        and live.get("active") is True
        and live.get("reason") == "none"
        and live.get("request_bound") is True
        and type(live.get("phase_ordinal")) is int
        and live.get("phase_ordinal") == 2
        and live.get("receipt_matches") is True
        and live.get("state") == "active"
        and live.get("original_not_replaced") is True
    ):
        return None

    directory_identity = live.get("directory_identity")
    sample_count = live.get("sample_count")
    request_ordinal = live.get("request_ordinal")
    sequence = live.get("sequence")
    counters = {
        "text_bytes": live.get("text_bytes"),
        "text_events": live.get("text_events"),
        "wire_bytes": live.get("wire_bytes"),
        "event_count": live.get("event_count"),
    }
    if (
        type(directory_identity) is not list
        or len(directory_identity) != 2
        or any(type(value) is not int or value < 0 for value in directory_identity)
        or type(sample_count) is not int
        or not 1 <= sample_count <= MAX_SAMPLES
        or type(request_ordinal) is not int
        or not 1 <= request_ordinal <= MAX_REQUESTS
        or type(sequence) is not int
        or not 1 <= sequence <= MAX_SAMPLES
        or any(type(value) is not int or value < 0 for value in counters.values())
        or not 0 < counters["text_bytes"] <= MAX_TEXT_BYTES
        or not 0 < counters["text_events"] <= MAX_EVENTS
        or not 0 < counters["wire_bytes"] <= MAX_WIRE_BYTES
        or not 0 < counters["event_count"] <= MAX_EVENTS
        or counters["text_events"] > counters["event_count"]
        or counters["text_bytes"] > counters["wire_bytes"]
        or sequence < sample_count
    ):
        return None
    return Snapshot(
        terminal_id=terminal_id,
        generation=generation,
        receipt_sha256=receipt_sha256,
        directory_identity=(directory_identity[0], directory_identity[1]),
        request_ordinal=request_ordinal,
        sequence=sequence,
        text_bytes=counters["text_bytes"],
        text_events=counters["text_events"],
        wire_bytes=counters["wire_bytes"],
        event_count=counters["event_count"],
    )


def capture(
    server: Any,
    private_root: Path,
    terminal_id: str,
    metadata: dict[str, Any],
    nonce: str,
    stop: float,
) -> Snapshot | None:
    """Capture one fresh HTTP ledger projection bracketed by the same PG receipt.

    `metadata` is the GET /terminals/{terminal_id} response: terminal fields are
    at the top level, and the pending turn fields are in `metadata["turn"]`.
    All invalid, stale, finished, or unavailable evidence returns None.
    """
    if (
        type(terminal_id) is not str
        or TERMINAL_RE.fullmatch(terminal_id) is None
        or type(nonce) is not str
        or NONCE_RE.fullmatch(nonce) is None
        or not _valid_stop(stop)
    ):
        return None
    generation = _api_generation(metadata, terminal_id)
    if generation is None:
        return None

    try:
        target = server.database_targets.main
    except Exception:
        return None
    before = _active_receipt(target, terminal_id, generation, stop)
    if before is None or not _valid_stop(stop):
        return None

    candidate = None
    try:
        root = Path(private_root).absolute()
        directory = root / ".opencode" / "http-text-stream-observation" / terminal_id
        live = http_observer.read_live(directory, nonce, before[1])
        candidate = _snapshot_from_live(
            live,
            terminal_id=terminal_id,
            generation=generation,
            receipt_sha256=before[1],
        )
    except Exception:
        candidate = None

    after = _active_receipt(target, terminal_id, generation, stop)
    if not _valid_stop(stop) or after != before:
        return None
    return candidate


def growing(previous: Any, current: Any) -> bool:
    """Require one fresh sequence and strict TEXT growth for the same binding."""
    if type(previous) is not Snapshot or type(current) is not Snapshot:
        return False
    same_binding = (
        previous.terminal_id == current.terminal_id
        and previous.generation == current.generation
        and previous.receipt_sha256 == current.receipt_sha256
        and previous.directory_identity == current.directory_identity
        and previous.request_ordinal == current.request_ordinal
    )
    counters_monotonic = (
        current.wire_bytes >= previous.wire_bytes and current.event_count >= previous.event_count
    )
    return (
        same_binding
        and current.sequence > previous.sequence
        and 0 < previous.text_bytes < current.text_bytes <= MAX_TEXT_BYTES
        and 0 < previous.text_events < current.text_events <= MAX_EVENTS
        and counters_monotonic
    )
