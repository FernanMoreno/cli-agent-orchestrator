"""Offline contracts for PG-bound HTTP TEXT evidence; no real PG or SQLite."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import os
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import native_http_stream_activity as activity
import pytest

pytestmark = pytest.mark.offline_database

TERMINAL_ID = "terminal-a"
GENERATION = "f" * 32
OTHER_GENERATION = "a" * 32
NONCE = "d" * 32
TOKEN = "CAO-TURN-RECEIPT-" + "e" * 32
RECEIPT_SHA256 = hashlib.sha256(TOKEN.encode("utf-8")).hexdigest()
OTHER_SHA256 = hashlib.sha256(b"another receipt").hexdigest()
PRIVATE_TEXT = "private TEXT canary α"


def api_metadata(**overrides):
    terminal = {"id": TERMINAL_ID, "provider": "opencode_cli", "status": "processing"}
    turn = {
        "terminal_id": TERMINAL_ID,
        "provider": "opencode_cli",
        "state": "pending",
        "generation": GENERATION,
    }
    for key, value in overrides.items():
        target, field = key.split(".", 1)
        (terminal if target == "terminal" else turn)[field] = value
    return {**terminal, "turn": turn}


def live_projection(**overrides):
    value = {
        "observer_status": "active",
        "active": True,
        "reason": "none",
        "sample_count": 1,
        "request_bound": True,
        "phase_ordinal": 2,
        "receipt_matches": True,
        "state": "active",
        "wire_bytes": 300,
        "text_bytes": 12,
        "text_events": 2,
        "event_count": 3,
        "original_not_replaced": True,
        "sequence": 4,
        "request_ordinal": 1,
        "directory_identity": [12, 34],
    }
    value.update(overrides)
    return value


class FakeTarget:
    schema = "main"

    def connection_parameters(self):
        return {"dbname": "synthetic-database-not-for-export"}


def fake_server():
    return SimpleNamespace(database_targets=SimpleNamespace(main=FakeTarget()))


def _install_fake_pg(monkeypatch, receipts, calls):
    class Identifier:
        def __init__(self, name):
            self.name = name

        def __str__(self):
            return '"' + self.name.replace('"', '""') + '"'

    class Query:
        def __init__(self, value):
            self.value = value

        def format(self, identifier):
            return Query(self.value.replace("{}", str(identifier)))

        def __str__(self):
            return self.value

    class Connection:
        def __init__(self, rows):
            self.rows = rows

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, parameters):
            calls.append((str(query), parameters))
            return self

        def fetchall(self):
            return self.rows

    fake = types.ModuleType("psycopg")
    fake.sql = SimpleNamespace(SQL=Query, Identifier=Identifier)

    def connect(**parameters):
        calls.append(parameters)
        rows = receipts.pop(0) if len(receipts) > 1 else receipts[0]
        return Connection(rows)

    fake.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", fake)


def _mkdir_private(path: Path) -> None:
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)


def _write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, separators=(",", ":"))
    os.chmod(path, 0o600)


def _ledger(private_root: Path, *, state="active", marker=None) -> Path:
    for directory in (
        private_root,
        private_root / ".opencode",
        private_root / ".opencode" / "http-text-stream-observation",
        private_root / ".opencode" / "http-text-stream-observation" / TERMINAL_ID,
    ):
        _mkdir_private(directory)
    path = private_root / ".opencode" / "http-text-stream-observation" / TERMINAL_ID
    reason = "none" if state == "active" else "stream_finished"
    sample = {
        "schema_version": 1,
        "nonce": NONCE,
        "sequence": 1,
        "request_ordinal": 1,
        "request_bound": True,
        "phase_ordinal": 2,
        "receipt_sha256": RECEIPT_SHA256,
        "state": state,
        "reason": reason,
        "wire_bytes": 300,
        "text_bytes": 12,
        "text_events": 2,
        "event_count": 3,
        "original_not_replaced": True,
    }
    _write_json(path / "sample-0001.json", sample)
    if marker == "invalid":
        _write_json(
            path / "invalid.json",
            {
                "schema_version": 1,
                "nonce": NONCE,
                "status": "unavailable",
                "reason": "observer_integrity",
            },
        )
    elif marker == "seal":
        _write_json(
            path / "seal.json",
            {
                "schema_version": 1,
                "nonce": NONCE,
                "trace_complete": False,
                "hook_disposed": True,
                "request_count": 1,
                "eligible_requests": 1,
                "sample_count": 1,
                "sample_attempts": 1,
            },
        )
    return path


def test_capture_uses_readonly_bounded_pg_select_and_real_strict_http_reader(tmp_path, monkeypatch):
    calls = []
    _install_fake_pg(
        monkeypatch,
        [[(GENERATION, RECEIPT_SHA256, "sent", "opencode_cli")]] * 2,
        calls,
    )
    private_root = tmp_path / "private-run"
    path = _ledger(private_root)

    result = activity.capture(
        fake_server(), private_root, TERMINAL_ID, api_metadata(), NONCE, time.monotonic() + 5
    )

    assert result is not None
    assert dataclasses.is_dataclass(result) and result.__dataclass_params__.frozen
    assert {field.name for field in dataclasses.fields(result)} == {
        "terminal_id",
        "generation",
        "receipt_sha256",
        "directory_identity",
        "request_ordinal",
        "sequence",
        "text_bytes",
        "text_events",
        "wire_bytes",
        "event_count",
    }
    assert (result.terminal_id, result.generation, result.receipt_sha256) == (
        TERMINAL_ID,
        GENERATION,
        RECEIPT_SHA256,
    )
    assert (result.text_bytes, result.text_events, result.sequence) == (12, 2, 1)
    assert result.directory_identity == (path.stat().st_dev, path.stat().st_ino)
    assert PRIVATE_TEXT not in repr(result)
    assert TOKEN not in repr(result)
    assert "synthetic-database-not-for-export" not in repr(result)
    assert str(private_root) not in repr(result)
    assert len([entry for entry in calls if isinstance(entry, dict)]) == 2
    for parameters in (entry for entry in calls if isinstance(entry, dict)):
        assert parameters["connect_timeout"] == 1
        assert parameters["application_name"] == "t187-http-stream-readonly"
        assert "statement_timeout=500" in parameters["options"]
        assert "lock_timeout=500" in parameters["options"]
        assert "default_transaction_read_only=on" in parameters["options"]
    queries = [entry for entry in calls if isinstance(entry, tuple)]
    assert len(queries) == 2
    for query, parameters in queries:
        assert query.startswith('SELECT generation,receipt_sha256,phase,provider FROM "main".')
        assert "terminal_turn_receipts" in query and "LIMIT 2" in query
        assert parameters == (TERMINAL_ID,)


def test_wrong_api_metadata_fails_before_pg_or_ledger_read(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(activity, "_active_receipt", lambda *_args: calls.append("pg"))
    monkeypatch.setattr(activity.http_observer, "read_live", lambda *_args: calls.append("ledger"))
    invalid = [
        api_metadata(**{"terminal.id": "other-terminal"}),
        api_metadata(**{"terminal.provider": "other"}),
        api_metadata(**{"terminal.status": "idle"}),
        api_metadata(**{"turn.terminal_id": "other-terminal"}),
        api_metadata(**{"turn.provider": "other"}),
        api_metadata(**{"turn.state": "completed"}),
        api_metadata(**{"turn.generation": "not-a-generation"}),
        {"id": TERMINAL_ID, "provider": "opencode_cli", "status": "processing"},
    ]
    for metadata in invalid:
        assert (
            activity.capture(
                fake_server(), tmp_path, TERMINAL_ID, metadata, NONCE, time.monotonic() + 5
            )
            is None
        )
    assert calls == []


def test_invalid_nonce_terminal_and_expired_deadline_refuse_without_pg(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(activity, "_active_receipt", lambda *_args: calls.append("pg"))
    valid = api_metadata()
    assert (
        activity.capture(
            fake_server(), tmp_path, TERMINAL_ID, valid, "BAD" * 10 + "bb", time.monotonic() + 5
        )
        is None
    )
    assert (
        activity.capture(fake_server(), tmp_path, "bad/id", valid, NONCE, time.monotonic() + 5)
        is None
    )
    assert (
        activity.capture(fake_server(), tmp_path, TERMINAL_ID, valid, NONCE, time.monotonic() - 1)
        is None
    )
    assert activity.capture(fake_server(), tmp_path, TERMINAL_ID, valid, NONCE, 10**1000) is None
    assert calls == []


def test_pg_missing_changed_phase_generation_or_duplicate_receipt_refuses(monkeypatch, tmp_path):
    cases = [
        [],
        [(OTHER_GENERATION, RECEIPT_SHA256, "sent", "opencode_cli")],
        [(GENERATION, RECEIPT_SHA256, "pending", "opencode_cli")],
        [(GENERATION, RECEIPT_SHA256, "sent", "opencode_cli")] * 2,
        [(GENERATION, "malformed", "sent", "opencode_cli")],
        [(GENERATION, RECEIPT_SHA256, "sent", "other_provider")],
    ]
    for rows in cases:
        calls = []
        _install_fake_pg(monkeypatch, [rows], calls)
        monkeypatch.setattr(
            activity.http_observer,
            "read_live",
            lambda *_args: pytest.fail("ledger must not be read without one active PG receipt"),
        )
        assert (
            activity.capture(
                fake_server(), tmp_path, TERMINAL_ID, api_metadata(), NONCE, time.monotonic() + 5
            )
            is None
        )


@pytest.mark.parametrize(
    "after",
    [
        (OTHER_GENERATION, RECEIPT_SHA256),
        (GENERATION, OTHER_SHA256),
    ],
)
def test_pg_receipt_change_after_fresh_ledger_read_refuses_snapshot(monkeypatch, tmp_path, after):
    before = (GENERATION, RECEIPT_SHA256)
    calls = []
    receipt_values = iter((before, after))
    monkeypatch.setattr(activity, "_active_receipt", lambda *_args: next(receipt_values))
    monkeypatch.setattr(activity.http_observer, "read_live", lambda *_args: live_projection())

    assert (
        activity.capture(
            fake_server(), tmp_path, TERMINAL_ID, api_metadata(), NONCE, time.monotonic() + 5
        )
        is None
    )


def test_malformed_live_counter_projection_refuses_snapshot(monkeypatch, tmp_path):
    invalid = [
        {"text_bytes": True},
        {"text_bytes": 64 * 1024 + 1},
        {"text_events": 0},
        {"event_count": 0},
        {"event_count": 1},
        {"wire_bytes": 0},
        {"directory_identity": [12, "34"]},
        {"phase_ordinal": 2.0},
        {"sample_count": 0},
        {"sample_count": 2, "sequence": 1},
    ]
    monkeypatch.setattr(
        activity,
        "_active_receipt",
        lambda *_args: (GENERATION, RECEIPT_SHA256),
    )
    for mutation in invalid:
        monkeypatch.setattr(
            activity.http_observer,
            "read_live",
            lambda *_args, mutation=mutation: live_projection(**mutation),
        )
        assert (
            activity.capture(
                fake_server(), tmp_path, TERMINAL_ID, api_metadata(), NONCE, time.monotonic() + 5
            )
            is None
        )


def test_http_reader_markers_finished_or_mismatched_receipt_refuse(tmp_path, monkeypatch):
    matching_pg = [(GENERATION, RECEIPT_SHA256, "sent", "opencode_cli")]
    for marker, state in [("invalid", "active"), ("seal", "active"), (None, "finished")]:
        private_root = tmp_path / f"run-{marker}-{state}"
        _ledger(private_root, marker=marker, state=state)
        calls = []
        _install_fake_pg(monkeypatch, [matching_pg, matching_pg], calls)
        assert (
            activity.capture(
                fake_server(),
                private_root,
                TERMINAL_ID,
                api_metadata(),
                NONCE,
                time.monotonic() + 5,
            )
            is None
        )

    private_root = tmp_path / "run-receipt-mismatch"
    _ledger(private_root)
    calls = []
    wrong_digest = [(GENERATION, OTHER_SHA256, "sent", "opencode_cli")]
    _install_fake_pg(monkeypatch, [wrong_digest], calls)
    assert (
        activity.capture(
            fake_server(), private_root, TERMINAL_ID, api_metadata(), NONCE, time.monotonic() + 5
        )
        is None
    )


def test_each_capture_is_fresh_but_third_active_read_need_not_grow(monkeypatch, tmp_path):
    samples = [
        live_projection(sequence=4),
        live_projection(sequence=5, text_bytes=13, text_events=3),
        live_projection(sequence=5, text_bytes=13, text_events=3),
    ]
    read_count = 0

    def read_live(*_args):
        nonlocal read_count
        value = samples[read_count]
        read_count += 1
        return value

    pg_count = 0

    def receipt(*_args):
        nonlocal pg_count
        pg_count += 1
        return GENERATION, RECEIPT_SHA256

    monkeypatch.setattr(activity, "_active_receipt", receipt)
    monkeypatch.setattr(activity.http_observer, "read_live", read_live)
    captures = [
        activity.capture(
            fake_server(), tmp_path, TERMINAL_ID, api_metadata(), NONCE, time.monotonic() + 5
        )
        for _ in range(3)
    ]

    assert all(item is not None for item in captures)
    assert read_count == 3 and pg_count == 6
    assert activity.growing(captures[0], captures[1]) is True
    assert captures[1].sequence == captures[2].sequence
    assert activity.growing(captures[1], captures[2]) is False


def test_growing_requires_same_binding_advanced_sequence_and_text_growth():
    previous = activity.Snapshot(
        TERMINAL_ID, GENERATION, RECEIPT_SHA256, (12, 34), 1, 4, 12, 2, 300, 3
    )
    current = dataclasses.replace(
        previous, sequence=5, text_bytes=13, text_events=3, wire_bytes=350, event_count=4
    )
    assert activity.growing(previous, current) is True

    changed = [
        dataclasses.replace(current, terminal_id="terminal-b"),
        dataclasses.replace(current, generation=OTHER_GENERATION),
        dataclasses.replace(current, receipt_sha256=OTHER_SHA256),
        dataclasses.replace(current, directory_identity=(12, 35)),
        dataclasses.replace(current, request_ordinal=2),
        dataclasses.replace(current, sequence=4),
        dataclasses.replace(current, text_bytes=12),
        dataclasses.replace(current, text_events=2),
        dataclasses.replace(current, wire_bytes=299),
        dataclasses.replace(current, event_count=2),
    ]
    assert all(activity.growing(previous, value) is False for value in changed)
    assert activity.growing(None, current) is False


def test_module_has_no_sqlite_or_old_activity_reader_import():
    source = Path(activity.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name == "sqlite3" or name.endswith(".sqlite3") for name in imported)
    assert "native_stream_activity" not in imported
