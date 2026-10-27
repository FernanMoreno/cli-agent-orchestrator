"""Draft offline reader contracts for native OpenCode streaming activity.

Intended to sit beside ``native_stream_activity.py`` in the Spec 025 fixture
directory. These real-SQLite controls do not establish native acceptance.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import native_stream_activity as activity
import pytest

pytestmark = pytest.mark.offline_database

DIAGNOSTIC_NONCE = "d" * 32
RECEIPT_TOKEN = "CAO-TURN-RECEIPT-" + "e" * 32
RECEIPT_SHA256 = hashlib.sha256(RECEIPT_TOKEN.encode("utf-8")).hexdigest()
OTHER_RECEIPT_SHA256 = hashlib.sha256(b"different active receipt").hexdigest()
GENERATION = "f" * 32
OTHER_GENERATION = "a" * 32
EXPECTED_AGENT = "storage_probe"
FIRST_TEXT = "partial α"
SECOND_TEXT = "a completely revised but longer partial Ω"
MISSING = object()


def _assistant_data(
    text: str = FIRST_TEXT,
    *,
    agent=EXPECTED_AGENT,
    time_fields=MISSING,
    finish=MISSING,
    raw_finish=MISSING,
    parts=None,
):
    content = [{"type": "text", "text": text}] if parts is None else parts
    data = {"content": content}
    if agent is not MISSING:
        data["agent"] = agent
    if time_fields is not MISSING:
        data["time"] = {"created": 3, **time_fields}
    if finish is not MISSING:
        data["finish"] = finish
    if raw_finish is not MISSING:
        data["rawFinish"] = raw_finish
    return data


def _user_text(*, diagnostic_nonce=DIAGNOSTIC_NONCE, token=RECEIPT_TOKEN) -> str:
    return f"Current private task {diagnostic_nonce}\n{token}"


def _insert(db, message_id, kind, seq, data, *, session="session-1"):
    db.execute(
        "INSERT INTO session_message VALUES(?,?,?,?,?,?,?)",
        (message_id, session, kind, seq, seq, seq, json.dumps(data, ensure_ascii=False)),
    )


def _make_store(
    path: Path,
    *,
    user_text=MISSING,
    assistant_data=MISSING,
) -> None:
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
        if user_text is MISSING:
            user_text = _user_text()
        if user_text is not None:
            _insert(
                db,
                "user-1",
                "user",
                5,
                {"text": user_text, "files": [], "time": {"created": 2}},
            )
        if assistant_data is MISSING:
            assistant_data = _assistant_data()
        if assistant_data is not None:
            _insert(db, "assistant-1", "assistant", 8, assistant_data)
        db.commit()
    path.chmod(0o600)


def _read(
    path: Path,
    *,
    diagnostic_nonce=DIAGNOSTIC_NONCE,
    receipt_sha256=RECEIPT_SHA256,
    generation=GENERATION,
    expected_agent=EXPECTED_AGENT,
    stop=None,
):
    return activity.read_snapshot(
        path,
        diagnostic_nonce=diagnostic_nonce,
        receipt_sha256=receipt_sha256,
        generation=generation,
        expected_agent=expected_agent,
        stop=time.monotonic() + 2 if stop is None else stop,
    )


def _replace_assistant(path: Path, data) -> None:
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "UPDATE session_message SET data=? WHERE id='assistant-1'",
            (json.dumps(data, ensure_ascii=False),),
        )
        db.commit()


def _add_user(path: Path, *, text: str, session="session-2") -> None:
    with closing(sqlite3.connect(path)) as db:
        _insert(db, "duplicate-user", "user", 5, {"text": text}, session=session)
        db.commit()


def _add_later_message(path: Path, *, kind: str, seq: int) -> None:
    with closing(sqlite3.connect(path)) as db:
        _insert(db, f"later-{kind}", kind, seq, {"reason": "later"})
        db.commit()


def test_intervening_user_invalidates_the_candidate_assistant(tmp_path):
    database = tmp_path / "opencode.db"
    _make_store(database)
    with closing(sqlite3.connect(database)) as db:
        _insert(db, "intervening-user", "user", 7, {"text": "A different task"})
        db.commit()

    assert _read(database) is None


def _assert_private_snapshot(snapshot) -> None:
    """The snapshot carries identifiers and counts, never prompt/response text."""
    assert dataclasses.is_dataclass(snapshot)
    assert {item.name for item in dataclasses.fields(snapshot)} == {
        "generation",
        "receipt_sha256",
        "database_identity",
        "session_id",
        "user_id",
        "user_seq",
        "assistant_id",
        "assistant_seq",
        "text_bytes",
        "text_parts",
    }
    assert FIRST_TEXT not in repr(snapshot)
    assert SECOND_TEXT not in repr(snapshot)
    assert type(snapshot.database_identity) is tuple
    assert len(snapshot.database_identity) == 2
    assert all(type(value) is int for value in snapshot.database_identity)
    assert type(snapshot.text_bytes) is int and snapshot.text_bytes > 0
    assert type(snapshot.text_parts) is int and snapshot.text_parts > 0


@pytest.mark.parametrize(
    "assistant_data",
    [
        _assistant_data(),  # No time.streamed field is required.
        _assistant_data(time_fields={"streamed": 4}),
        _assistant_data(time_fields={"completed": None}, finish=None),
    ],
)
def test_accepts_incomplete_text_with_absent_or_null_completion_metadata(tmp_path, assistant_data):
    database = tmp_path / "opencode.db"
    _make_store(database, assistant_data=assistant_data)
    before_sha256 = hashlib.sha256(database.read_bytes()).hexdigest()

    snapshot = _read(database)
    after_sha256 = hashlib.sha256(database.read_bytes()).hexdigest()

    assert snapshot is not None
    assert after_sha256 == before_sha256
    _assert_private_snapshot(snapshot)
    assert snapshot.generation == GENERATION
    assert snapshot.receipt_sha256 == RECEIPT_SHA256
    assert snapshot.session_id == "session-1"
    assert snapshot.user_id == "user-1"
    assert snapshot.user_seq == 5
    assert snapshot.assistant_id == "assistant-1"
    assert snapshot.assistant_seq == 8
    assert snapshot.text_bytes == len(FIRST_TEXT.encode("utf-8"))
    assert snapshot.text_parts == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.text_bytes = 0


def test_fresh_read_transactions_observe_committed_growth_without_exporting_text(tmp_path):
    database = tmp_path / "opencode.db"
    _make_store(database, assistant_data=_assistant_data(FIRST_TEXT))

    first = _read(database)
    assert first is not None

    # The writer commits between independent read_snapshot calls. The row ID
    # and sequence stay fixed; only its unfinished TEXT content changes.
    _replace_assistant(database, _assistant_data(SECOND_TEXT))
    second = _read(database)

    assert second is not None
    _assert_private_snapshot(first)
    _assert_private_snapshot(second)
    assert (first.assistant_id, first.assistant_seq) == (
        second.assistant_id,
        second.assistant_seq,
    )
    assert (first.user_id, first.user_seq, first.session_id) == (
        second.user_id,
        second.user_seq,
        second.session_id,
    )
    assert first.text_bytes < second.text_bytes
    assert activity.growing(first, second) is True


@pytest.mark.parametrize(
    "assistant_data",
    [
        _assistant_data(time_fields={"completed": 9}),
        _assistant_data(finish="stop", raw_finish="stop"),
    ],
)
def test_completed_or_finished_assistant_is_not_live_activity(tmp_path, assistant_data):
    database = tmp_path / "opencode.db"
    _make_store(database, assistant_data=assistant_data)

    assert _read(database) is None


@pytest.mark.parametrize(
    "later_kind",
    ["user", "idle"],
)
def test_any_later_message_invalidates_the_candidate_assistant(tmp_path, later_kind):
    database = tmp_path / "opencode.db"
    _make_store(database)
    _add_later_message(database, kind=later_kind, seq=9)

    assert _read(database) is None


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_active_hash",
        "duplicate_user_nonce",
        "duplicate_receipt_token",
        "present_wrong_agent",
    ],
)
def test_ambiguous_or_mismatched_receipt_binding_is_an_integrity_error(tmp_path, fault):
    database = tmp_path / "opencode.db"
    if fault == "wrong_active_hash":
        _make_store(database)
        with pytest.raises(activity.StreamError):
            _read(database, receipt_sha256=OTHER_RECEIPT_SHA256)
        return
    if fault == "duplicate_user_nonce":
        _make_store(database)
        _add_user(database, text=_user_text())
        with pytest.raises(activity.StreamError):
            _read(database)
        return
    if fault == "duplicate_receipt_token":
        _make_store(database, user_text=_user_text(token=f"{RECEIPT_TOKEN}\n{RECEIPT_TOKEN}"))
        with pytest.raises(activity.StreamError):
            _read(database)
        return

    _make_store(database, assistant_data=_assistant_data(agent="foreign_agent"))
    with pytest.raises(activity.StreamError):
        _read(database)


def test_missing_user_assistant_or_agent_and_non_text_parts_are_not_activity(tmp_path):
    database = tmp_path / "opencode.db"
    _make_store(database, user_text=None)
    assert _read(database) is None

    _make_store(database, assistant_data=None)
    assert _read(database) is None

    _make_store(database, assistant_data=_assistant_data(agent=MISSING))
    assert _read(database) is None

    _make_store(
        database,
        assistant_data=_assistant_data(
            parts=[
                {"type": "reasoning", "text": FIRST_TEXT},
                {"type": "tool", "text": SECOND_TEXT},
            ]
        ),
    )
    assert _read(database) is None


def test_growth_requires_positive_strict_byte_increase(tmp_path):
    database = tmp_path / "opencode.db"
    _make_store(database)
    previous = _read(database)
    assert previous is not None

    assert activity.growing(previous, dataclasses.replace(previous)) is False
    assert (
        activity.growing(
            previous, dataclasses.replace(previous, text_bytes=previous.text_bytes - 1)
        )
        is False
    )
    assert (
        activity.growing(
            previous,
            dataclasses.replace(
                previous,
                text_bytes=previous.text_bytes + 1,
                text_parts=previous.text_parts + 1,
            ),
        )
        is True
    )
    assert (
        activity.growing(
            dataclasses.replace(previous, text_bytes=0),
            dataclasses.replace(previous, text_bytes=previous.text_bytes + 1),
        )
        is False
    )


def test_growth_rejects_a_changed_generation(tmp_path):
    database = tmp_path / "opencode.db"
    _make_store(database)
    previous = _read(database)
    assert previous is not None

    current = dataclasses.replace(
        previous,
        generation=OTHER_GENERATION,
        text_bytes=previous.text_bytes + 1,
    )

    assert activity.growing(previous, current) is False


def test_growth_rejects_each_changed_receipt_database_or_message_identity(tmp_path):
    database = tmp_path / "opencode.db"
    _make_store(database)
    previous = _read(database)
    assert previous is not None
    device, inode = previous.database_identity
    mutations = {
        "receipt_sha256": OTHER_RECEIPT_SHA256,
        "database_identity": (device, inode + 1),
        "session_id": "session-2",
        "user_id": "another-user",
        "user_seq": previous.user_seq + 1,
        "assistant_id": "another-assistant",
        "assistant_seq": previous.assistant_seq + 1,
    }

    for field, changed in mutations.items():
        current = dataclasses.replace(
            previous,
            **{field: changed},
            text_bytes=previous.text_bytes + 1,
        )
        assert activity.growing(previous, current) is False, field


def test_database_inode_replacement_breaks_growth_identity(tmp_path):
    database = tmp_path / "opencode.db"
    parked = tmp_path / "parked.db"
    replacement = tmp_path / "replacement.db"
    _make_store(database)
    previous = _read(database)
    assert previous is not None
    _make_store(replacement)

    # Keep the old inode alive while replacing the pathname, preventing inode
    # reuse from making this test pass accidentally.
    os.replace(database, parked)
    os.replace(replacement, database)
    current = _read(database)

    assert current is not None
    assert current.database_identity != previous.database_identity
    assert (
        activity.growing(previous, dataclasses.replace(current, text_bytes=previous.text_bytes + 1))
        is False
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"diagnostic_nonce": "D" * 32},
        {"receipt_sha256": "0" * 63},
        {"generation": "not-a-generation"},
        {"expected_agent": "invalid agent id"},
    ],
)
def test_invalid_identity_inputs_raise_stream_error(tmp_path, arguments):
    database = tmp_path / "opencode.db"
    _make_store(database)

    with pytest.raises(activity.StreamError):
        _read(database, **arguments)


def test_expired_deadline_is_refused(tmp_path):
    database = tmp_path / "opencode.db"
    _make_store(database)

    with pytest.raises(activity.StreamError):
        _read(database, stop=time.monotonic() - 1)


def test_schema_without_the_observed_unique_sequence_index_is_refused(tmp_path):
    database = tmp_path / "opencode.db"
    _make_store(database)
    with closing(sqlite3.connect(database)) as db:
        db.execute("DROP INDEX session_message_session_seq_idx")
        db.commit()

    with pytest.raises(activity.StreamError):
        _read(database)


def test_text_projection_limit_is_refused(tmp_path):
    database = tmp_path / "opencode.db"
    _make_store(database, assistant_data=_assistant_data("x" * (65536 + 1)))

    with pytest.raises(activity.StreamError):
        _read(database)


def test_database_hardlink_is_refused(tmp_path):
    database = tmp_path / "opencode.db"
    alias = tmp_path / "alias.db"
    _make_store(database)
    os.link(database, alias)

    with pytest.raises(activity.StreamError):
        _read(database)


@pytest.mark.parametrize("mismatch", [False, True])
def test_capture_requires_snapshot_to_match_owned_database_identity(
    tmp_path, monkeypatch, mismatch
):
    import native_v2_text_probe

    database = tmp_path / "opencode.db"
    _make_store(database)
    snapshot = _read(database)
    identity = snapshot.database_identity
    if mismatch:
        snapshot = dataclasses.replace(snapshot, database_identity=(identity[0], identity[1] + 1))
    monkeypatch.setattr(activity, "active_receipt", lambda *args: (GENERATION, RECEIPT_SHA256))
    monkeypatch.setattr(native_v2_text_probe, "_owned_database", lambda *args: (database, identity))
    monkeypatch.setattr(activity, "read_snapshot", lambda *args, **kwargs: snapshot)
    server = SimpleNamespace(database_targets=SimpleNamespace(main=object()))

    result = activity.capture(
        server,
        tmp_path / "cao-home",
        "terminal-1",
        GENERATION,
        DIAGNOSTIC_NONCE,
        EXPECTED_AGENT,
        time.monotonic() + 2,
    )

    assert result == (None if mismatch else snapshot)
