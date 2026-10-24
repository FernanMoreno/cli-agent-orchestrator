"""Offline real-SQLite contracts for the bounded OpenCode-v2 store reader."""

import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

import native_v2_store as store

NONCE = "CAO-TURN-RECEIPT-" + "d" * 32
AGENT = "storage_probe"
TEXT = "Validado ñ, 中文."


class NativeV2StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "opencode.db"
        self._create_store()

    def tearDown(self):
        self.temp.cleanup()

    def _create_store(self, *, assistant=True, legacy=False):
        if self.database.exists():
            self.database.unlink()
        with closing(sqlite3.connect(self.database)) as db:
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
                   CREATE INDEX session_message_session_type_seq_idx
                       ON session_message(session_id,type,seq);
                   INSERT INTO session_v2 VALUES
                       ('session-1','project-1','slug','/workspace','2.0.18',0,1,1),
                       ('session-2','project-1','slug-2','/workspace','2.0.18',0,1,1);""")
            self._insert(db, "switch", "agent-switched", 1, {"agent": AGENT})
            self._insert(
                db,
                "user-1",
                "user",
                5,
                {
                    "text": "Control nonce: " + NONCE + ". Do not send it again.",
                    "files": [],
                    "time": {"created": 2},
                },
            )
            if assistant:
                self._insert(
                    db,
                    "assistant-1",
                    "assistant",
                    8,
                    {
                        "agent": AGENT,
                        "content": [
                            {"type": "reasoning", "text": "ignored"},
                            {"type": "text", "text": TEXT + "\n" + NONCE},
                            {"type": "tool", "text": "ignored too"},
                            {"type": "text", "text": " after"},
                        ],
                        "time": {"created": 3, "streamed": 4, "completed": 5},
                        "finish": "stop",
                        "rawFinish": "stop",
                    },
                )
            self._insert(db, "idle", "idle", 13, {"reason": "completed"})
            if legacy:
                db.executescript("""CREATE TABLE message(id TEXT,session_id TEXT,data TEXT);
                       CREATE TABLE part(id TEXT,message_id TEXT,session_id TEXT,data TEXT);""")
                db.execute(
                    "INSERT INTO message VALUES(?,?,?)",
                    ("old-user", "session-1", json.dumps({"role": "user"})),
                )
                db.execute(
                    "INSERT INTO part VALUES(?,?,?,?)",
                    (
                        "old-user-text",
                        "old-user",
                        "session-1",
                        json.dumps({"type": "text", "text": "old " + NONCE}),
                    ),
                )
                db.execute(
                    "INSERT INTO message VALUES(?,?,?)",
                    (
                        "old-assistant",
                        "session-1",
                        json.dumps(
                            {
                                "role": "assistant",
                                "parentID": "old-user",
                                "agent": AGENT,
                                "time": {"completed": 1},
                            }
                        ),
                    ),
                )
                db.execute(
                    "INSERT INTO part VALUES(?,?,?,?)",
                    (
                        "old-assistant-text",
                        "old-assistant",
                        "session-1",
                        json.dumps({"type": "text", "text": TEXT + "\n" + NONCE}),
                    ),
                )
            db.commit()

    @staticmethod
    def _insert(db, message_id, kind, seq, data, *, session="session-1"):
        db.execute(
            "INSERT INTO session_message VALUES(?,?,?,?,?,?,?)",
            (
                message_id,
                session,
                kind,
                seq,
                seq,
                seq,
                json.dumps(data, ensure_ascii=False),
            ),
        )

    def _read(self, *, stop=None, expected_agent=AGENT):
        return store._native_parts(
            self.database,
            NONCE,
            time.monotonic() + 2 if stop is None else stop,
            expected_agent=expected_agent,
        )

    def _replace_message_data(self, message_id, data):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "UPDATE session_message SET data=? WHERE id=?",
                (
                    data if isinstance(data, str) else json.dumps(data, ensure_ascii=False),
                    message_id,
                ),
            )
            db.commit()

    def test_reads_inline_nonce_user_and_completed_assistant_with_sequence_gaps(self):
        self.assertEqual(self._read(), (TEXT + "\n" + NONCE, " after"))

    def test_old_message_part_decoy_cannot_replace_missing_v2_assistant(self):
        self._create_store(assistant=False, legacy=True)
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "assistant_receipt_not_unique")

    def test_user_parent_must_be_unique_and_join_a_v2_session(self):
        with closing(sqlite3.connect(self.database)) as db:
            self._insert(
                db,
                "duplicate-user",
                "user",
                20,
                {"text": "duplicate " + NONCE},
                session="session-2",
            )
            db.commit()
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "user_not_unique")
        self.assertEqual(raised.exception.user_parent_status, "multiple")

        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DELETE FROM session_message WHERE type='user'")
            db.commit()
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "user_not_unique")
        self.assertEqual(raised.exception.user_parent_status, "missing")

        self._create_store()
        self._replace_message_data(
            "user-1",
            {"text": "nonce " + NONCE + " repeated " + NONCE},
        )
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "user_not_unique")
        self.assertEqual(raised.exception.user_parent_status, "multiple")

    def test_foreign_session_assistant_is_not_considered(self):
        with closing(sqlite3.connect(self.database)) as db:
            self._insert(
                db,
                "foreign-assistant",
                "assistant",
                8,
                {
                    "agent": AGENT,
                    "content": [{"type": "text", "text": TEXT + "\n" + NONCE}],
                    "time": {"completed": 1},
                },
                session="session-2",
            )
            db.execute("DELETE FROM session_message WHERE id='assistant-1'")
            db.commit()
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "assistant_receipt_not_unique")

    def test_assistant_before_user_or_after_next_user_is_outside_nonce_window(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE session_message SET seq=2 WHERE id='assistant-1'")
            db.commit()
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "assistant_receipt_not_unique")

        self._create_store(assistant=True)
        with closing(sqlite3.connect(self.database)) as db:
            self._insert(db, "next-user", "user", 100, {"text": "later user"})
            db.commit()
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "user_not_current")

    def test_wrong_agent_and_incomplete_assistant_fail_closed(self):
        self._replace_message_data(
            "assistant-1",
            {
                "agent": "foreign-agent",
                "content": [{"type": "text", "text": TEXT + "\n" + NONCE}],
                "time": {"created": 1, "completed": 2},
            },
        )
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "native_agent_mismatch")

        self._replace_message_data(
            "assistant-1",
            {
                "agent": AGENT,
                "content": [{"type": "text", "text": TEXT + "\n" + NONCE}],
                "time": {"created": 1},
            },
        )
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "assistant_receipt_not_unique")

        self._replace_message_data(
            "assistant-1",
            {
                "agent": "x" * 70000,
                "content": [{"type": "text", "text": TEXT + "\n" + NONCE}],
                "time": {"created": 1, "completed": 2},
            },
        )
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "native_agent_metadata_invalid")

    def test_duplicate_or_split_standalone_nonce_is_not_a_receipt(self):
        self._replace_message_data(
            "assistant-1",
            {
                "agent": AGENT,
                "content": [{"type": "text", "text": TEXT + "\n" + NONCE + "\n" + NONCE}],
                "time": {"completed": 1},
            },
        )
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "assistant_receipt_not_unique")

        self._create_store(assistant=True)
        with closing(sqlite3.connect(self.database)) as db:
            self._insert(
                db,
                "second-assistant",
                "assistant",
                10,
                {
                    "agent": AGENT,
                    "content": [{"type": "text", "text": TEXT + "\n" + NONCE}],
                    "time": {"completed": 6},
                },
            )
            db.commit()
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "assistant_receipt_not_unique")

        self._create_store(assistant=True)
        self._replace_message_data(
            "assistant-1",
            {
                "agent": AGENT,
                "content": [
                    {"type": "text", "text": TEXT + "\n" + NONCE[:20]},
                    {"type": "text", "text": NONCE[20:]},
                ],
                "time": {"completed": 1},
            },
        )
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "assistant_receipt_not_unique")

    def test_malformed_json_and_missing_schema_fail_closed(self):
        self._replace_message_data("assistant-1", "{bad-json " + NONCE)
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "native_schema_mismatch")

        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DROP TABLE session_v2")
            db.commit()
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "native_schema_mismatch")

    def test_text_and_part_caps_are_checked_before_text_projection(self):
        self._replace_message_data(
            "assistant-1",
            {
                "agent": AGENT,
                "content": [{"type": "text", "text": "x" * (store.TEXT_CAP + 1) + "\n" + NONCE}],
                "time": {"completed": 1},
            },
        )
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "text_size_or_type")

        self._replace_message_data(
            "assistant-1",
            {
                "agent": AGENT,
                "content": [
                    {"type": "text", "text": "x" * 35000 + "\n" + NONCE},
                    {"type": "text", "text": "y" * 35000},
                ],
                "time": {"completed": 1},
            },
        )
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "total_size")

        self._replace_message_data(
            "assistant-1",
            {
                "agent": AGENT,
                "content": [{"type": "text", "text": ""}] * (store.PART_CAP + 1),
                "time": {"completed": 1},
            },
        )
        with closing(sqlite3.connect(self.database)) as db:
            data = json.loads(
                db.execute("SELECT data FROM session_message WHERE id='assistant-1'").fetchone()[0]
            )
            data["content"][-1]["text"] = NONCE
            db.execute(
                "UPDATE session_message SET data=? WHERE id='assistant-1'",
                (json.dumps(data),),
            )
            db.commit()
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "parts_limit")

    def test_text_budgets_cover_all_completed_nonce_candidates_before_fetch(self):
        self._create_store(assistant=False)
        with closing(sqlite3.connect(self.database)) as db:
            self._insert(
                db,
                "reasoning-decoy",
                "assistant",
                8,
                {
                    "agent": AGENT,
                    "content": [
                        {"type": "reasoning", "text": "quoted " + NONCE},
                        {"type": "text", "text": "x" * 35000},
                    ],
                    "time": {"completed": 2},
                },
            )
            self._insert(
                db,
                "actual-assistant",
                "assistant",
                12,
                {
                    "agent": AGENT,
                    "content": [{"type": "text", "text": "y" * 35000 + "\n" + NONCE}],
                    "time": {"completed": 3},
                },
            )
            db.commit()
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "total_size")

        self._create_store(assistant=False)
        with closing(sqlite3.connect(self.database)) as db:
            self._insert(
                db,
                "reasoning-decoy",
                "assistant",
                8,
                {
                    "agent": AGENT,
                    "content": [
                        {"type": "reasoning", "text": "quoted " + NONCE},
                        *({"type": "text", "text": "x"} for _ in range(8)),
                    ],
                    "time": {"completed": 2},
                },
            )
            self._insert(
                db,
                "actual-assistant",
                "assistant",
                12,
                {
                    "agent": AGENT,
                    "content": [
                        *({"type": "text", "text": "y"} for _ in range(8)),
                        {"type": "text", "text": TEXT + "\n" + NONCE},
                    ],
                    "time": {"completed": 3},
                },
            )
            db.commit()
        with self.assertRaises(store.ProbeError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), "parts_limit")

    def test_partial_unique_index_cannot_prove_global_sequence_uniqueness(self):
        for duplicate in (False, True):
            with self.subTest(duplicate_same_seq_user=duplicate):
                self._create_store()
                with closing(sqlite3.connect(self.database)) as db:
                    db.execute("DROP INDEX session_message_session_seq_idx")
                    db.execute(
                        "CREATE UNIQUE INDEX session_message_session_seq_idx "
                        "ON session_message(session_id,seq) WHERE seq>1000"
                    )
                    if duplicate:
                        self._insert(db, "same-seq-user", "user", 5, {"text": "another input"})
                    db.commit()
                with self.assertRaises(store.ProbeError) as raised:
                    self._read()
                self.assertEqual(str(raised.exception), "native_schema_mismatch")

    def test_expired_deadline_and_authorizer_deny_unrelated_reads_and_writes(self):
        with self.assertRaises(store.ProbeError) as raised:
            self._read(stop=time.monotonic() - 1)
        self.assertEqual(str(raised.exception), "deadline")

        with closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE TABLE credential(secret TEXT)")
            db.execute("INSERT INTO credential VALUES('private')")
            db.commit()
            db.set_authorizer(store._authorizer)
            for query in (
                "SELECT secret FROM credential",
                "DELETE FROM session_message",
                "ATTACH DATABASE ':memory:' AS extra",
            ):
                with self.subTest(query=query), self.assertRaises(sqlite3.DatabaseError):
                    db.execute(query)


if __name__ == "__main__":
    unittest.main()
