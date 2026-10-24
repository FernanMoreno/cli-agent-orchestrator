"""Bounded, read-only TEXT reader for the observed OpenCode v2 SQLite store."""

from __future__ import annotations

import re
import sqlite3
import stat
import time
from pathlib import Path

TOKEN_RE = re.compile(r"(?:CAO-TURN-RECEIPT-|CAO_TURN_RECEIPT_)[0-9a-f]{32}")
AGENT_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
ROW_CAP = 1024 * 1024
TEXT_CAP = 65536
PART_CAP = 16
CANDIDATE_CAP = 64


class ProbeError(Exception):
    """Fixed unavailable reason without private SQLite values."""

    def __init__(self, reason: str, *, user_parent_status: str | None = None):
        self.user_parent_status = (
            user_parent_status if user_parent_status in {"missing", "multiple"} else None
        )
        super().__init__(reason)


def _authorizer(action, table, column, database, trigger):
    """Allow reads from the two observed tables and SQLite's built-in JSON iterator."""
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        function = (column or table or "").lower()
        if function in {
            "json_each",
            "json_valid",
            "json_type",
            "json_extract",
            "length",
            "replace",
            "instr",
            "typeof",
        }:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ:
        allowed = {
            "session_message": {
                "id",
                "session_id",
                "type",
                "seq",
                "time_created",
                "time_updated",
                "data",
            },
            "session_v2": {"id"},
            "json_each": {"key", "value", "type", "atom", "id", "parent", "fullkey", "path"},
        }
        if column in allowed.get(table, set()):
            return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _check_deadline(stop: float) -> None:
    if time.monotonic() >= stop:
        raise ProbeError("deadline")


def _table_columns(db: sqlite3.Connection, table: str, stop: float) -> dict[str, str]:
    _check_deadline(stop)
    if db.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchall() != [
        ("table",)
    ]:
        raise ProbeError("native_schema_mismatch")
    columns = {}
    for row in db.execute(f"PRAGMA table_info({table})"):
        _check_deadline(stop)
        columns[row[1]] = row[2].upper()
    return columns


def _schema_matches(db: sqlite3.Connection, stop: float) -> bool:
    message = _table_columns(db, "session_message", stop)
    session = _table_columns(db, "session_v2", stop)
    required_message = {
        "id": "TEXT",
        "session_id": "TEXT",
        "type": "TEXT",
        "seq": "INTEGER",
        "time_created": "INTEGER",
        "time_updated": "INTEGER",
        "data": "TEXT",
    }
    if any(message.get(name) != kind for name, kind in required_message.items()):
        return False
    if session.get("id") != "TEXT":
        return False

    unique_indexes = {"session_message": set(), "session_v2": set()}
    for table in unique_indexes:
        _check_deadline(stop)
        for row in db.execute(f"PRAGMA index_list({table})"):
            _check_deadline(stop)
            if len(row) < 5 or row[2] != 1 or row[4] != 0:
                continue
            index_name = row[1]
            if not isinstance(index_name, str) or not re.fullmatch(r"[A-Za-z0-9_]+", index_name):
                continue
            fields = []
            for item in db.execute(f"PRAGMA index_info({index_name})"):
                _check_deadline(stop)
                fields.append(item[2])
            unique_indexes[table].add(tuple(fields))
    return (
        ("id",) in unique_indexes["session_message"]
        and ("session_id", "seq") in unique_indexes["session_message"]
        and ("id",) in unique_indexes["session_v2"]
    )


def _standalone_occurrences(text: str, nonce: str) -> int:
    pattern = re.compile(r"(?m)^[ \t]*" + re.escape(nonce) + r"[ \t]*$")
    return len(pattern.findall(text))


def _native_parts(path, nonce, stop, *, expected_agent=None):
    """Read bounded completed v2 assistant TEXT parts for the unique nonce user."""
    _check_deadline(stop)
    if type(nonce) is not str or TOKEN_RE.fullmatch(nonce) is None:
        raise ProbeError("native_schema_mismatch")
    if expected_agent is not None and (
        type(expected_agent) is not str or AGENT_RE.fullmatch(expected_agent) is None
    ):
        raise ProbeError("native_agent_metadata_invalid")

    try:
        database = Path(path)
        metadata = database.lstat()
        if (
            not database.is_absolute()
            or database.resolve(strict=True) != database
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > 64 * 1024 * 1024
        ):
            raise ProbeError("native_schema_mismatch")
        db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0.1)
    except ProbeError:
        raise
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        raise ProbeError("native_schema_mismatch") from None

    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        db.set_progress_handler(lambda: int(time.monotonic() >= stop), 100)
        _check_deadline(stop)
        if db.execute("PRAGMA encoding").fetchone() != ("UTF-8",):
            raise ProbeError("native_schema_mismatch")
        if not _schema_matches(db, stop):
            raise ProbeError("native_schema_mismatch")
        # Verify the built-in iterator on an inert literal before the authorizer is active.
        if db.execute("SELECT key FROM json_each('[]') LIMIT 1").fetchone() is not None:
            raise ProbeError("native_schema_mismatch")

        db.set_authorizer(_authorizer)

        _check_deadline(stop)
        users = db.execute(
            """SELECT m.id,m.session_id,m.seq,
                      length(CAST(m.data AS BLOB)),
                      length(CAST(json_extract(m.data,'$.text') AS BLOB)),
                      (length(json_extract(m.data,'$.text'))-
                       length(replace(json_extract(m.data,'$.text'),?,'')))/length(?)
               FROM session_message AS m
               JOIN session_v2 AS s ON s.id=m.session_id
               WHERE m.type='user' AND typeof(m.data)='text'
                 AND length(CAST(m.data AS BLOB)) BETWEEN 2 AND ?
                 AND json_valid(m.data)
                 AND json_type(m.data,'$.text')='text'
                 AND instr(json_extract(m.data,'$.text'),?)>0
               ORDER BY m.seq DESC,m.id LIMIT 2""",
            (nonce, nonce, ROW_CAP, nonce),
        ).fetchall()
        if len(users) != 1:
            status = "missing" if not users else "multiple"
            raise ProbeError("user_not_unique", user_parent_status=status)
        user_id, session_id, user_seq, row_bytes, prompt_bytes, nonce_occurrences = users[0]
        if (
            type(user_id) is not str
            or not user_id
            or type(session_id) is not str
            or not session_id
            or type(user_seq) is not int
            or type(row_bytes) is not int
            or row_bytes > ROW_CAP
            or type(prompt_bytes) is not int
            or prompt_bytes > ROW_CAP
            or type(nonce_occurrences) is not int
        ):
            raise ProbeError("native_schema_mismatch")
        if nonce_occurrences != 1:
            status = "missing" if nonce_occurrences == 0 else "multiple"
            raise ProbeError("user_not_unique", user_parent_status=status)

        _check_deadline(stop)
        next_user = db.execute(
            """SELECT seq FROM session_message
               WHERE session_id=? AND type='user' AND seq>?
               ORDER BY seq,id LIMIT 1""",
            (session_id, user_seq),
        ).fetchone()
        if next_user is not None:
            if type(next_user[0]) is not int:
                raise ProbeError("native_schema_mismatch")
            raise ProbeError("user_not_current")

        bounds = [session_id, user_seq]
        bounds.append(ROW_CAP)
        bounds.append(nonce)
        _check_deadline(stop)
        candidates = db.execute(
            """SELECT m.id,m.seq,length(CAST(m.data AS BLOB)),json_valid(m.data),
                      json_type(m.data,'$.content')
               FROM session_message AS m
               JOIN session_v2 AS s ON s.id=m.session_id
               WHERE m.session_id=? AND m.type='assistant' AND m.seq>?"""
            + """ AND typeof(m.data)='text'
                    AND length(CAST(m.data AS BLOB)) BETWEEN 2 AND ?
                        AND json_type(m.data,'$.time.completed')='integer'
                        AND json_extract(m.data,'$.time.completed')>0
                    AND instr(m.data,?)>0
               ORDER BY m.seq,m.id LIMIT """
            + str(CANDIDATE_CAP + 1),
            tuple(bounds),
        ).fetchall()
        if len(candidates) > CANDIDATE_CAP:
            raise ProbeError("assistant_receipt_not_unique")

        preflight = []
        total_parts = 0
        total_text_bytes = 0
        for message_id, seq, data_bytes, valid_json, content_type in candidates:
            _check_deadline(stop)
            if (
                type(message_id) is not str
                or not message_id
                or type(seq) is not int
                or type(data_bytes) is not int
                or data_bytes > ROW_CAP
                or type(valid_json) is not int
            ):
                raise ProbeError("native_schema_mismatch")
            if not valid_json:
                raise ProbeError("native_schema_mismatch")
            if content_type != "array":
                continue
            sizes = db.execute(
                """SELECT je.key,json_type(je.value,'$.text'),
                          length(CAST(json_extract(je.value,'$.text') AS BLOB))
                   FROM session_message AS m,
                        json_each(m.data,'$.content') AS je
                   WHERE m.id=? AND m.session_id=? AND m.seq=? AND m.type='assistant'
                     AND typeof(m.data)='text'
                     AND length(CAST(m.data AS BLOB))<=?
                     AND json_valid(m.data)
                     AND json_type(m.data,'$.content')='array'
                     AND json_extract(je.value,'$.type')='text'
                   ORDER BY CAST(je.key AS INTEGER) LIMIT ?""",
                (message_id, session_id, seq, ROW_CAP, PART_CAP + 1),
            ).fetchall()
            if len(sizes) > PART_CAP:
                raise ProbeError("parts_limit")
            total_parts += len(sizes)
            if total_parts > PART_CAP:
                raise ProbeError("parts_limit")
            part_keys = []
            for key, text_type, size in sizes:
                if type(key) is not int or text_type != "text" or type(size) is not int or size < 0:
                    raise ProbeError("text_size_or_type")
                if size > TEXT_CAP:
                    raise ProbeError("text_size_or_type")
                total_text_bytes += size
                if total_text_bytes > TEXT_CAP:
                    raise ProbeError("total_size")
                part_keys.append(key)
            preflight.append(
                (message_id, seq, tuple(part_keys), tuple(size for _, _, size in sizes))
            )

        matched = []
        for message_id, seq, part_keys, sizes in preflight:
            if not part_keys:
                continue
            _check_deadline(stop)
            rows = db.execute(
                """SELECT je.key,json_extract(je.value,'$.text')
                   FROM session_message AS m,
                        json_each(m.data,'$.content') AS je
                   WHERE m.id=? AND m.session_id=? AND m.seq=? AND m.type='assistant'
                     AND typeof(m.data)='text'
                     AND length(CAST(m.data AS BLOB))<=?
                     AND json_valid(m.data)
                     AND json_type(m.data,'$.content')='array'
                     AND json_extract(je.value,'$.type')='text'
                     AND json_type(je.value,'$.text')='text'
                   ORDER BY CAST(je.key AS INTEGER) LIMIT ?""",
                (message_id, session_id, seq, ROW_CAP, PART_CAP + 1),
            ).fetchall()
            if len(rows) != len(part_keys) or tuple(row[0] for row in rows) != part_keys:
                raise ProbeError("text_snapshot_changed")
            parts = []
            for (_, text), expected_size in zip(rows, sizes, strict=True):
                if type(text) is not str:
                    raise ProbeError("text_size_or_type")
                try:
                    actual_size = len(text.encode("utf-8", errors="strict"))
                except UnicodeError:
                    raise ProbeError("text_size_or_type") from None
                if actual_size != expected_size:
                    raise ProbeError("text_snapshot_changed")
                parts.append(text)
            occurrence_count = sum(_standalone_occurrences(text, nonce) for text in parts)
            if occurrence_count == 1:
                matched.append((message_id, seq, tuple(parts)))
            elif occurrence_count > 1:
                raise ProbeError("assistant_receipt_not_unique")

        if len(matched) != 1:
            raise ProbeError("assistant_receipt_not_unique")
        message_id, seq, parts = matched[0]
        _check_deadline(stop)
        metadata = db.execute(
            """SELECT json_type(data,'$.time.completed'),
                      json_extract(data,'$.time.completed'),
                      json_type(data,'$.agent'),
                      length(CAST(json_extract(data,'$.agent') AS BLOB))
               FROM session_message
               WHERE id=? AND session_id=? AND seq=? AND type='assistant'
                 AND typeof(data)='text' AND length(CAST(data AS BLOB))<=?
                 AND json_valid(data) LIMIT 2""",
            (message_id, session_id, seq, ROW_CAP),
        ).fetchall()
        if len(metadata) != 1:
            raise ProbeError("text_snapshot_changed")
        completed_type, completed, agent_type, agent_bytes = metadata[0]
        if completed_type != "integer" or type(completed) is not int or completed <= 0:
            raise ProbeError("assistant_receipt_not_unique")
        if expected_agent is not None:
            if agent_type != "text" or type(agent_bytes) is not int or not 1 <= agent_bytes <= 64:
                raise ProbeError("native_agent_metadata_invalid")
            agent_rows = db.execute(
                """SELECT json_extract(data,'$.agent')
                   FROM session_message
                   WHERE id=? AND session_id=? AND seq=? AND type='assistant'
                     AND typeof(data)='text'
                     AND length(CAST(data AS BLOB))<=?
                     AND json_valid(data)
                     AND json_type(data,'$.agent')='text'
                     AND length(CAST(json_extract(data,'$.agent') AS BLOB)) BETWEEN 1 AND 64
                   LIMIT 2""",
                (message_id, session_id, seq, ROW_CAP),
            ).fetchall()
            if len(agent_rows) != 1 or type(agent_rows[0][0]) is not str:
                raise ProbeError("text_snapshot_changed")
            agent = agent_rows[0][0]
            if AGENT_RE.fullmatch(agent) is None:
                raise ProbeError("native_agent_metadata_invalid")
            if agent != expected_agent:
                raise ProbeError("native_agent_mismatch")
        _check_deadline(stop)
        return parts
    except ProbeError:
        raise
    except sqlite3.OperationalError as exc:
        if time.monotonic() >= stop or "interrupted" in str(exc).lower():
            raise ProbeError("deadline") from None
        raise ProbeError("native_schema_mismatch") from None
    except (sqlite3.Error, OSError, RuntimeError, TypeError, ValueError):
        raise ProbeError("native_schema_mismatch") from None
    finally:
        db.close()
