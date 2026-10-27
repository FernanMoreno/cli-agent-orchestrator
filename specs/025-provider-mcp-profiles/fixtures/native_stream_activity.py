"""Read-only unfinished native TEXT snapshots; never a final-result reader.

The native binary must prove partial persistence in a synthetic control before
this fixture can be used as an activity gate. It does not prove HTTP liveness.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import os
import re
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path

from native_paired_protocol import HarnessError

from cli_agent_orchestrator.utils.opencode_v2_store import (
    AGENT_RE,
    PART_CAP,
    ROW_CAP,
    TEXT_CAP,
    TOKEN_RE,
    _authorizer,
    _schema_matches,
)


class StreamError(HarnessError):
    """Fixed reason code; raw native rows never enter an exception."""


@dataclasses.dataclass(frozen=True)
class Snapshot:
    generation: str
    receipt_sha256: str
    database_identity: tuple[int, int]
    session_id: str
    user_id: str
    user_seq: int
    assistant_id: str
    assistant_seq: int
    text_bytes: int
    text_parts: int


def _deadline(stop):
    if time.monotonic() >= stop:
        raise StreamError("native_stream_deadline")


def _database_identity(path):
    info = path.lstat()
    if (
        not path.is_absolute()
        or path.resolve(strict=True) != path
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_size > 64 * 1024 * 1024
    ):
        raise StreamError("native_stream_database_invalid")
    return info.st_dev, info.st_ino


def _identifier(value):
    return type(value) is str and 0 < len(value.encode("utf8")) <= 256


def read_snapshot(path, *, diagnostic_nonce, receipt_sha256, generation, expected_agent, stop):
    """Each call opens a fresh bounded transaction and exports no native text."""
    if (
        type(diagnostic_nonce) is not str
        or re.fullmatch(r"[a-f0-9]{32}", diagnostic_nonce) is None
        or type(receipt_sha256) is not str
        or re.fullmatch(r"[a-f0-9]{64}", receipt_sha256) is None
        or type(generation) is not str
        or re.fullmatch(r"[a-f0-9]{32}", generation) is None
        or type(expected_agent) is not str
        or AGENT_RE.fullmatch(expected_agent) is None
        or type(stop) not in (int, float)
        or not math.isfinite(stop)
    ):
        raise StreamError("native_stream_identity_invalid")
    stop = min(stop, time.monotonic() + 1.0)
    _deadline(stop)
    path = Path(path)
    try:
        identity = _database_identity(path)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            db.set_progress_handler(lambda: int(time.monotonic() >= stop), 100)
            if db.execute("PRAGMA encoding").fetchone() != ("UTF-8",) or not _schema_matches(
                db, stop
            ):
                raise StreamError("native_stream_schema_mismatch")
            if db.execute("SELECT key FROM json_each('[]') LIMIT 1").fetchone() is not None:
                raise StreamError("native_stream_schema_mismatch")
            db.set_authorizer(_authorizer)
            users = db.execute(
                """SELECT m.id,m.session_id,m.seq,json_extract(m.data,'$.text')
              FROM session_message AS m JOIN session_v2 AS s ON s.id=m.session_id
              WHERE m.type='user' AND typeof(m.data)='text'
                AND length(CAST(m.data AS BLOB)) BETWEEN 2 AND ? AND json_valid(m.data)
                AND json_type(m.data,'$.text')='text'
                AND instr(json_extract(m.data,'$.text'),?)>0
              ORDER BY m.seq DESC,m.id LIMIT 2""",
                (ROW_CAP, diagnostic_nonce),
            ).fetchall()
            if not users:
                return None
            if len(users) != 1:
                raise StreamError("native_stream_user_not_unique")
            user_id, session_id, user_seq, prompt = users[0]
            if (
                not _identifier(user_id)
                or not _identifier(session_id)
                or type(user_seq) is not int
                or type(prompt) is not str
                or len(prompt.encode("utf8")) > ROW_CAP
            ):
                raise StreamError("native_stream_user_invalid")
            tokens = TOKEN_RE.findall(prompt)
            if (
                prompt.count(diagnostic_nonce) != 1
                or len(tokens) != 1
                or hashlib.sha256(tokens[0].encode("utf8")).hexdigest() != receipt_sha256
            ):
                raise StreamError("native_stream_receipt_mismatch")
            # No prompt text crosses this reader boundary.
            del prompt, tokens
            _deadline(stop)
            if (
                db.execute(
                    "SELECT id FROM session_message WHERE session_id=? AND type='user' AND seq>? LIMIT 1",
                    (session_id, user_seq),
                ).fetchone()
                is not None
            ):
                return None
            rows = db.execute(
                """SELECT id,seq,json_type(data,'$.agent'),json_extract(data,'$.agent'),
                json_type(data,'$.time'),json_type(data,'$.time.completed'),
                json_type(data,'$.finish'),json_type(data,'$.rawFinish'),json_type(data,'$.content')
              FROM session_message WHERE session_id=? AND type='assistant' AND seq>?
                AND typeof(data)='text' AND length(CAST(data AS BLOB)) BETWEEN 2 AND ?
                AND json_valid(data) ORDER BY seq,id LIMIT 2""",
                (session_id, user_seq, ROW_CAP),
            ).fetchall()
            if not rows:
                return None
            if len(rows) != 1:
                raise StreamError("native_stream_assistant_not_unique")
            aid, seq, agent_type, agent, time_type, completed, finish, raw_finish, content = rows[0]
            if not _identifier(aid) or type(seq) is not int or seq <= user_seq:
                raise StreamError("native_stream_assistant_invalid")
            if (
                completed not in (None, "null")
                or finish not in (None, "null")
                or raw_finish not in (None, "null")
            ):
                return None
            if time_type not in (None, "null", "object"):
                raise StreamError("native_stream_time_invalid")
            if agent_type in (None, "null"):
                return None
            if agent_type != "text" or agent != expected_agent:
                raise StreamError("native_stream_agent_mismatch")
            latest = db.execute(
                "SELECT id,seq,type FROM session_message WHERE session_id=? ORDER BY seq DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if latest != (aid, seq, "assistant"):
                return None
            if content is None:
                return None
            if content != "array":
                raise StreamError("native_stream_parts_invalid")
            parts = db.execute(
                """SELECT je.key,json_type(je.value,'$.text'),
                    length(CAST(json_extract(je.value,'$.text') AS BLOB))
              FROM session_message AS m,json_each(m.data,'$.content') AS je
              WHERE m.id=? AND m.session_id=? AND m.seq=?
                AND json_extract(je.value,'$.type')='text'
              ORDER BY CAST(je.key AS INTEGER) LIMIT ?""",
                (aid, session_id, seq, PART_CAP + 1),
            ).fetchall()
            if len(parts) > PART_CAP:
                raise StreamError("native_stream_parts_limit")
            total = 0
            for key, kind, size in parts:
                if (
                    type(key) is not int
                    or kind != "text"
                    or type(size) is not int
                    or not 0 <= size <= TEXT_CAP
                ):
                    raise StreamError("native_stream_text_limit")
                total += size
            if total > TEXT_CAP:
                raise StreamError("native_stream_text_limit")
            if total == 0:
                return None
            _deadline(stop)
            if _database_identity(path) != identity:
                raise StreamError("native_stream_database_changed")
            return Snapshot(
                generation,
                receipt_sha256,
                identity,
                session_id,
                user_id,
                user_seq,
                aid,
                seq,
                total,
                len(parts),
            )
    except StreamError:
        raise
    except Exception:
        raise StreamError("native_stream_read_unavailable") from None


def growing(previous, current):
    if not isinstance(previous, Snapshot) or not isinstance(current, Snapshot):
        return False
    keys = (
        "generation",
        "receipt_sha256",
        "database_identity",
        "session_id",
        "user_id",
        "user_seq",
        "assistant_id",
        "assistant_seq",
    )
    return (
        all(getattr(previous, k) == getattr(current, k) for k in keys)
        and 0 < previous.text_bytes < current.text_bytes
        and previous.text_parts > 0
        and current.text_parts >= previous.text_parts
    )


def active_receipt(target, terminal_id, generation, stop):
    """The authoritative PG receipt is read only; API generations are not hashes."""
    _deadline(stop)
    if (
        type(target.schema) is not str
        or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", target.schema) is None
    ):
        raise StreamError("native_stream_pg_binding_invalid")
    import psycopg
    from psycopg import sql

    params = target.connection_parameters()
    params.update(
        connect_timeout=1,
        options="-c statement_timeout=500 -c lock_timeout=500 -c default_transaction_read_only=on",
        application_name="t187-native-stream-readonly",
    )
    try:
        with psycopg.connect(**params) as db:
            rows = db.execute(
                sql.SQL(
                    "SELECT generation,receipt_sha256,phase,provider FROM {}.terminal_turn_receipts WHERE terminal_id=%s LIMIT 2"
                ).format(sql.Identifier(target.schema)),
                (terminal_id,),
            ).fetchall()
        _deadline(stop)
    except StreamError:
        raise
    except Exception:
        raise StreamError("native_stream_pg_read_unavailable") from None
    if not rows:
        return None
    if len(rows) != 1:
        raise StreamError("native_stream_pg_receipt_invalid")
    gen, digest, phase, provider = rows[0]
    if gen != generation or phase != "sent":
        return None
    if (
        provider != "opencode_cli"
        or type(digest) is not str
        or re.fullmatch(r"[a-f0-9]{64}", digest) is None
    ):
        raise StreamError("native_stream_pg_receipt_invalid")
    return gen, digest


def capture(server, cao_home, terminal_id, generation, nonce, expected_agent, stop):
    from native_v2_text_probe import _owned_database

    before = active_receipt(server.database_targets.main, terminal_id, generation, stop)
    if before is None:
        return None
    run_root = Path(cao_home).parent
    path, identity = _owned_database(run_root, Path(cao_home), terminal_id)
    result = read_snapshot(
        path,
        diagnostic_nonce=nonce,
        receipt_sha256=before[1],
        generation=generation,
        expected_agent=expected_agent,
        stop=stop,
    )
    after = active_receipt(server.database_targets.main, terminal_id, generation, stop)
    repeated_path, repeated_identity = _owned_database(run_root, Path(cao_home), terminal_id)
    if (
        after != before
        or repeated_path != path
        or repeated_identity != identity
        or (result is not None and result.database_identity != identity)
    ):
        return None
    return result
