"""Private diagnostic: exact owned native text vs renderer; never acceptance input."""

import hashlib
import os
import re
import sqlite3
import stat
import time
import unicodedata
from pathlib import Path

MARKER = "Validado ñ, 中文."
TOKEN = r"(?:CAO-TURN-RECEIPT-|CAO_TURN_RECEIPT_)[0-9a-f]{32}"
CAP = 65536
PARTS = 16


class ProbeError(Exception):
    pass


def shape(text, expected=MARKER):
    return dict(
        characters=len(text),
        lines=len(text.splitlines()),
        contains_marker=expected in text,
        nfc_marker=unicodedata.normalize("NFC", text) == expected,
        nfkc_marker=unicodedata.normalize("NFKC", text) == expected,
        exact_marker=text == expected,
        compact_marker="".join(text.split()) == "".join(expected.split()),
    )


def _authorizer(action, table, column, database, trigger):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and table in ("message", "part"):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _native_parts(path, nonce, stop):
    """Read v1 message/part TEXT only, pinned to the current user parent."""
    if time.monotonic() >= stop:
        raise ProbeError("deadline")
    c = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)
    try:
        c.execute("PRAGMA query_only=ON")
        c.execute("BEGIN")
        required = {
            "message": {"id": "TEXT", "session_id": "TEXT", "data": "TEXT"},
            "part": {"id": "TEXT", "message_id": "TEXT", "session_id": "TEXT", "data": "TEXT"},
        }
        for table, fields in required.items():
            if c.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchall() != [
                ("table",)
            ]:
                raise ProbeError("native_schema_mismatch")
            columns = {r[1]: r[2].upper() for r in c.execute("PRAGMA table_info(" + table + ")")}
            if any(columns.get(k) != v for k, v in fields.items()):
                raise ProbeError("native_schema_mismatch")
        c.set_authorizer(_authorizer)
        c.set_progress_handler(lambda: int(time.monotonic() >= stop), 100)
        users = c.execute(
            """SELECT DISTINCT m.id,m.session_id FROM message m
            JOIN part p ON p.message_id=m.id AND p.session_id=m.session_id
            WHERE length(m.data)<=1048576 AND json_extract(m.data,'$.role')='user'
              AND length(p.data)<=1048576 AND json_extract(p.data,'$.type')='text'
              AND instr(json_extract(p.data,'$.text'),?)>0 LIMIT 2""",
            (nonce,),
        ).fetchall()
        if len(users) != 1:
            raise ProbeError("user_not_unique")
        user, session = users[0]
        source = """ FROM message m JOIN part p
            ON p.message_id=m.id AND p.session_id=m.session_id
            WHERE m.session_id=? AND length(m.data)<=1048576
              AND json_extract(m.data,'$.role')='assistant'
              AND json_extract(m.data,'$.parentID')=?
              AND json_type(m.data,'$.time.completed')='integer'
              AND json_extract(m.data,'$.time.completed')>0
              AND length(p.data)<=1048576 AND json_extract(p.data,'$.type')='text'
            ORDER BY m.id,p.id LIMIT 17"""
        params = (session, user)
        sizes = c.execute(
            "SELECT m.id,p.id,length(CAST(json_extract(p.data,'$.text') AS BLOB))" + source, params
        ).fetchall()
        if len(sizes) > PARTS:
            raise ProbeError("parts_limit")
        if any(type(n) is not int or n > CAP for _, _, n in sizes):
            raise ProbeError("text_size_or_type")
        if sum(n for _, _, n in sizes) > CAP:
            raise ProbeError("total_size")
        rows = c.execute(
            "SELECT m.id,p.id,json_extract(p.data,'$.text')" + source, params
        ).fetchall()
        if [(m, p) for m, p, _ in rows] != [(m, p) for m, p, _ in sizes]:
            raise ProbeError("text_snapshot_changed")
        groups = {}
        for mid, _, text in rows:
            if type(text) is not str:
                raise ProbeError("text_size_or_type")
            groups.setdefault(mid, []).append(text)
        matched = [
            parts
            for parts in groups.values()
            if any(
                re.search(r"(?m)^[ \t]*" + re.escape(nonce) + r"[ \t]*$", text) for text in parts
            )
        ]
        if len(matched) != 1:
            raise ProbeError("assistant_receipt_not_unique")
        if time.monotonic() >= stop:
            raise ProbeError("deadline")
        return matched[0]
    finally:
        c.close()


def _owned_db(run_root, cao_home, terminal_id):
    from cli_agent_orchestrator.utils.opencode_ownership import PrivateConfigOwnership

    if cao_home != run_root / "home/.aws/cli-agent-orchestrator":
        raise ProbeError("home_binding")
    # Read-only use of the exact-key ownership receipt and marker validation. No scan or lock/write.
    ownership = PrivateConfigOwnership(cao_home, terminal_id)
    document = ownership._read()
    if not document or document.get("phase") != "active" or not document.get("claim"):
        raise ProbeError("ownership_missing")
    if document["claim"]["major"] != 1:
        raise ProbeError("major_mismatch")
    owned = ownership._root(document["claim"])
    if owned is None:
        raise ProbeError("root_missing")
    path = owned / "data/opencode/opencode.db"
    # Validate every component from the known owned root; never follow symlinks.
    cur = run_root
    for component in path.relative_to(run_root).parts:
        cur = cur / component
        st = cur.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid():
            raise ProbeError("path_owner_or_link")
        if cur != path and (not stat.S_ISDIR(st.st_mode) or st.st_mode & 0o022):
            raise ProbeError("directory_permissions")
    st = run_root.lstat()
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o700
    ):
        raise ProbeError("run_root_permissions")
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > 64 * 1024 * 1024:
        raise ProbeError("db_file_shape")
    for suffix in ("-wal", "-shm"):
        side = Path(str(path) + suffix)
        if side.exists() or side.is_symlink():
            side_st = side.lstat()
            if (
                not stat.S_ISREG(side_st.st_mode)
                or side_st.st_uid != os.getuid()
                or side_st.st_nlink != 1
                or side_st.st_size > 64 * 1024 * 1024
            ):
                raise ProbeError("db_sidecar_shape")
    return path, (st.st_dev, st.st_ino)


def _digest(target, terminal_id, stop):
    import psycopg
    from psycopg import sql

    if stop - time.monotonic() < 1:
        raise ProbeError("pg_deadline_margin")
    schema = getattr(target, "schema", None)
    if not isinstance(schema, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise ProbeError("pg_schema")
    params = target.connection_parameters()
    params.update(
        connect_timeout=1,
        options="-c statement_timeout=500 -c lock_timeout=500 -c default_transaction_read_only=on",
        application_name="t089-native-text-readonly",
    )
    with psycopg.connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "SELECT receipt_sha256,phase FROM {}.terminal_turn_receipts WHERE terminal_id=%s"
                ).format(sql.Identifier(schema)),
                (terminal_id,),
            )
            row = cur.fetchone()
    if not row or not re.fullmatch(r"[0-9a-f]{64}", row[0]) or row[1] != "result_verified":
        raise ProbeError("pg_receipt_missing_or_incomplete")
    if time.monotonic() >= stop:
        raise ProbeError("deadline")
    return row[0]


def observe(
    run_root, cao_home, terminal_id, target, viewport, rendered, deadline, *, expected=MARKER
):
    start = time.monotonic()
    stop = min(deadline, start + 3.0)
    out = dict(
        schema="caos.t089.native-text-shapes.v1",
        diagnostic_only=True,
        raw_text_exported=False,
        acceptance_input_changed=False,
        status="unavailable",
    )
    try:
        if type(expected) is not str or expected not in (MARKER, "ACK"):
            raise ProbeError("expected_marker_invalid")
        if (
            type(viewport) is not str
            or len(viewport.encode("utf-8")) > CAP
            or type(rendered) is not str
            or len(rendered.encode("utf-8")) > CAP
        ):
            raise ProbeError("input_size_or_type")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", terminal_id):
            raise ProbeError("terminal_identity")
        digest = _digest(target, terminal_id, stop)
        candidates = set(re.findall(TOKEN, viewport))
        if len(candidates) > 8:
            raise ProbeError("nonce_limit")
        active = [n for n in candidates if hashlib.sha256(n.encode()).hexdigest() == digest]
        if len(active) != 1:
            raise ProbeError("active_nonce_not_unique")
        path, identity = _owned_db(Path(run_root), Path(cao_home), terminal_id)
        parts = _native_parts(path, active[0], stop)
        after = path.lstat()
        if (after.st_dev, after.st_ino) != identity or not stat.S_ISREG(after.st_mode):
            raise ProbeError("db_identity_changed")
        # Concatenation is explicitly diagnostic; does not feed LAST or settle any turn.
        public = re.sub(
            r"(?m)^[ \t]*" + re.escape(active[0]) + r"[ \t]*$", "", "".join(parts)
        ).strip()
        native_shape = shape(public, expected)
        render_shape = shape(rendered, expected)
        classification = (
            "multiple_native_text_parts"
            if len(parts) != 1
            else (
                "native_marker_api_missing"
                if native_shape["contains_marker"] and not render_shape["contains_marker"]
                else (
                    "native_text_contains_linebreak"
                    if native_shape["compact_marker"]
                    and not native_shape["exact_marker"]
                    and ("\n" in public or "\r" in public)
                    else "other_native_text_shape"
                )
            )
        )
        out.update(
            status="observed",
            active_receipt_bound=True,
            exact_owned_root=True,
            text_parts=len(parts),
            native_equals_api=public == rendered,
            native_public=native_shape,
            rendered_public=render_shape,
            any_single_text_contains_exact_marker=any(expected in p for p in parts),
            text_part_shapes=[dict(characters=len(p), lines=len(p.splitlines())) for p in parts],
            classification=classification,
            raw_model_response_observed=False,
        )
    except ProbeError as e:
        out["reason"] = str(e)
    except (OSError, ValueError, TypeError, sqlite3.Error):
        out["reason"] = "readonly_probe_error"
    except Exception:
        # No exception message: it could contain connection strings or native text.
        out["reason"] = "probe_dependency_error"
    out["elapsed_ms"] = round((time.monotonic() - start) * 1000)
    out["within_three_seconds"] = time.monotonic() <= stop
    return out
