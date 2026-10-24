"""Real-SQLite contracts for the production OpenCode-v2 TEXT reader."""

from __future__ import annotations

import importlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

NONCE = "CAO-TURN-RECEIPT-" + "d" * 32
AGENT = "storage_probe"
TEXT = "Validado ñ, 中文."


def _store():
    return importlib.import_module("cli_agent_orchestrator.utils.opencode_v2_store")


def _make_store(path: Path, *, user=True, assistant=True):
    if path.exists():
        path.unlink()
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""CREATE TABLE session_v2 (
                   id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                   slug TEXT NOT NULL, directory TEXT NOT NULL,
                   version TEXT NOT NULL, cost REAL NOT NULL DEFAULT 0,
                   time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL
               );
               CREATE TABLE session_message (
                   id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                   type TEXT NOT NULL, seq INTEGER NOT NULL,
                   time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL,
                   data TEXT NOT NULL
               );
               CREATE UNIQUE INDEX session_message_session_seq_idx
                   ON session_message(session_id,seq);
               INSERT INTO session_v2 VALUES
                   ('session-1','project-1','slug','/workspace','2.0.18',0,1,1),
                   ('session-2','project-1','slug-2','/workspace','2.0.18',0,1,1);""")
        if user:
            _insert(
                db,
                "user-1",
                "user",
                5,
                {"text": "Current prompt: " + NONCE, "files": [], "time": {"created": 2}},
            )
        if assistant:
            _insert(
                db,
                "assistant-1",
                "assistant",
                8,
                {
                    "agent": AGENT,
                    "content": [
                        {"type": "reasoning", "text": "ignored " + NONCE},
                        {"type": "text", "text": TEXT + "\n"},
                        {"type": "tool", "text": "ignored too"},
                        {"type": "text", "text": "second line\n" + NONCE},
                    ],
                    "time": {"created": 3, "streamed": 4, "completed": 5},
                    "finish": "stop",
                    "rawFinish": "stop",
                },
            )
        _insert(db, "idle", "idle", 13, {"reason": "completed"})
        db.commit()


def _insert(db, message_id, kind, seq, data, *, session="session-1"):
    db.execute(
        "INSERT INTO session_message VALUES(?,?,?,?,?,?,?)",
        (message_id, session, kind, seq, seq, seq, json.dumps(data, ensure_ascii=False)),
    )


def _read(path: Path, *, expected_agent=AGENT, stop=None):
    store = _store()
    return store._native_parts(
        path,
        NONCE,
        time.monotonic() + 2 if stop is None else stop,
        expected_agent=expected_agent,
    )


def _replace(path: Path, message_id: str, data):
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "UPDATE session_message SET data=? WHERE id=?",
            (data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), message_id),
        )
        db.commit()


def test_reads_exact_text_parts_and_preserves_hard_line_feeds(tmp_path):
    database = tmp_path / "opencode.db"
    _make_store(database)

    parts = _read(database)

    assert parts == (TEXT + "\n", "second line\n" + NONCE)
    assert "".join(parts) == TEXT + "\nsecond line\n" + NONCE


def test_missing_duplicate_foreign_and_stale_users_are_rejected(tmp_path):
    store = _store()
    database = tmp_path / "opencode.db"
    _make_store(database, user=False)
    with pytest.raises(store.ProbeError, match="user_not_unique") as missing:
        _read(database)
    assert missing.value.user_parent_status == "missing"

    _make_store(database)
    with closing(sqlite3.connect(database)) as db:
        _insert(db, "duplicate-user", "user", 20, {"text": NONCE}, session="session-2")
        db.commit()
    with pytest.raises(store.ProbeError, match="user_not_unique") as duplicate:
        _read(database)
    assert duplicate.value.user_parent_status == "multiple"

    _make_store(database)
    with closing(sqlite3.connect(database)) as db:
        db.execute("UPDATE session_message SET seq=2 WHERE id='assistant-1'")
        db.commit()
    with pytest.raises(store.ProbeError, match="assistant_receipt_not_unique"):
        _read(database)

    _make_store(database)
    with closing(sqlite3.connect(database)) as db:
        _insert(db, "later-user", "user", 100, {"text": "new turn"})
        db.commit()
    with pytest.raises(store.ProbeError, match="user_not_current"):
        _read(database)


def test_wrong_agent_incomplete_and_ambiguous_receipts_are_rejected(tmp_path):
    store = _store()
    database = tmp_path / "opencode.db"
    _make_store(database)
    _replace(
        database,
        "assistant-1",
        {
            "agent": "foreign_agent",
            "content": [{"type": "text", "text": TEXT + "\n" + NONCE}],
            "time": {"completed": 5},
        },
    )
    with pytest.raises(store.ProbeError, match="native_agent_mismatch"):
        _read(database)

    _replace(
        database,
        "assistant-1",
        {"agent": AGENT, "content": [{"type": "text", "text": TEXT + "\n" + NONCE}]},
    )
    with pytest.raises(store.ProbeError, match="assistant_receipt_not_unique"):
        _read(database)

    _replace(
        database,
        "assistant-1",
        {
            "agent": AGENT,
            "content": [{"type": "text", "text": TEXT + "\n" + NONCE + "\n" + NONCE}],
            "time": {"completed": 5},
        },
    )
    with pytest.raises(store.ProbeError, match="assistant_receipt_not_unique"):
        _read(database)


def test_malformed_schema_and_partial_sequence_index_are_rejected(tmp_path):
    store = _store()
    database = tmp_path / "opencode.db"
    _make_store(database)
    _replace(database, "assistant-1", "{invalid " + NONCE)
    with pytest.raises(store.ProbeError, match="native_schema_mismatch"):
        _read(database)

    _make_store(database)
    with closing(sqlite3.connect(database)) as db:
        db.execute("DROP INDEX session_message_session_seq_idx")
        db.execute(
            "CREATE UNIQUE INDEX session_message_session_seq_idx "
            "ON session_message(session_id,seq) WHERE seq>1000"
        )
        db.commit()
    with pytest.raises(store.ProbeError, match="native_schema_mismatch"):
        _read(database)


def test_reader_rejects_expired_deadline_and_limits_projection(tmp_path):
    store = _store()
    database = tmp_path / "opencode.db"
    _make_store(database)
    with pytest.raises(store.ProbeError, match="deadline"):
        _read(database, stop=time.monotonic() - 1)

    _replace(
        database,
        "assistant-1",
        {
            "agent": AGENT,
            "content": [{"type": "text", "text": "x" * (store.TEXT_CAP + 1) + "\n" + NONCE}],
            "time": {"completed": 5},
        },
    )
    with pytest.raises(store.ProbeError, match="text_size_or_type"):
        _read(database)


def test_reader_rejects_credential_reads_writes_and_attach(tmp_path):
    store = _store()
    database = tmp_path / "opencode.db"
    _make_store(database)
    with closing(sqlite3.connect(database)) as db:
        db.execute("CREATE TABLE credential(secret TEXT)")
        db.execute("INSERT INTO credential VALUES('private')")
        db.commit()
        db.set_authorizer(store._authorizer)
        for query in (
            "SELECT secret FROM credential",
            "DELETE FROM session_message",
            "ATTACH DATABASE ':memory:' AS extra",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                db.execute(query)
