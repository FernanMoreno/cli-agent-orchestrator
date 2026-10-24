"""Offline contracts for the bounded OpenCode-v2 native-text probe."""

import hashlib
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPO_ROOT / "specs/025-provider-mcp-profiles/fixtures"
sys.path[:0] = [str(FIXTURE_ROOT), str(REPO_ROOT / "src")]

import native_v2_store as native_v2_store
import native_v2_text_probe as probe

NONCE = "CAO-TURN-RECEIPT-" + "b" * 32
OTHER_NONCE = "CAO-TURN-RECEIPT-" + "c" * 32
SECRET = "t111-private-canary-never-export"
TERMINAL = "sender-fixture"
AGENT = "v2-sender"
TARGET = SimpleNamespace(schema="cao_test", connection_parameters=lambda: {})


def _valid_child_result(expected_marker=probe.MARKER):
    result = probe._base_result(expected_marker, expected_marker)
    shape = probe._shape(expected_marker, expected_marker)
    result.update(
        status="observed",
        reason=None,
        active_receipt_bound=True,
        owned_major2_root=True,
        user_parent_bound=True,
        assistant_agent_matches=True,
        assistant_turn_completed=True,
        native_equals_rendered=True,
        active_nonce_candidate_count=1,
        native_text_part_count=1,
        native_public=shape,
        rendered_public=shape,
        text_part_shapes=[shape],
        classification="native_equals_rendered",
        elapsed_ms=1,
        within_three_seconds=True,
        within_original_deadline=True,
    )
    return result


class NativeV2TextProbeTests(unittest.TestCase):
    def test_compact_whitespace_without_linefeed_does_not_claim_linebreak(self):
        for text in ("Validado  ñ, 中文.", "Validado\u00a0ñ, 中文."):
            with self.subTest(text_shape=probe._shape(text)):
                classification = probe._classify(
                    text, probe._shape(text), probe._shape(probe.MARKER), 1
                )
                self.assertNotEqual(classification, "native_text_contains_linebreak")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run_root = Path(self.temp.name) / "run"
        self.run_root.mkdir(mode=0o700)
        os.chmod(self.run_root, 0o700)
        self.home = self.run_root / "home"
        self.home.mkdir(mode=0o700)
        os.chmod(self.home, 0o700)
        aws = self.home / ".aws"
        aws.mkdir(mode=0o700)
        os.chmod(aws, 0o700)
        self.cao_home = aws / "cli-agent-orchestrator"
        self.cao_home.mkdir(mode=0o700)
        os.chmod(self.cao_home, 0o700)
        self.config = self.run_root / "database-config.json"
        self.config.write_text("{}", encoding="utf-8")
        os.chmod(self.config, 0o600)
        self.source_root = REPO_ROOT
        self.native_root = None
        self.db_path = None
        self._create_owned_db(2)

    def tearDown(self):
        self.temp.cleanup()

    def _create_owned_db(self, major=2, terminal_id=TERMINAL):
        from cli_agent_orchestrator.utils.opencode_ownership import PrivateConfigOwnership

        owner = PrivateConfigOwnership(self.cao_home, terminal_id)
        self.native_root = owner.allocate(major)
        data = self.native_root / "data"
        data.mkdir(mode=0o700)
        os.chmod(data, 0o700)
        opencode = data / "opencode"
        opencode.mkdir(mode=0o700)
        os.chmod(opencode, 0o700)
        self.db_path = opencode / "opencode.db"
        self._replace_with_observed_v2_store()

    def _replace_with_observed_v2_store(self, *, assistant=True, legacy_decoy=False):
        """Use the observed v2 session_message/session_v2 layout in SQLite tests."""
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executescript("""DROP TABLE IF EXISTS part;
                   DROP TABLE IF EXISTS message;
                   DROP TABLE IF EXISTS credential;
                   DROP TABLE IF EXISTS session_message;
                   DROP TABLE IF EXISTS session_v2;
                   CREATE TABLE session_v2 (
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
                     ON session_message(session_id, seq);
                   CREATE INDEX session_message_session_type_seq_idx
                     ON session_message(session_id, type, seq);""")
            db.execute(
                "INSERT INTO session_v2 VALUES(?,?,?,?,?,?,?,?)",
                ("session-1", "project-1", "slug", "/workspace", "2.0.18", 0, 1, 1),
            )
            self._insert_v2_message(db, "switch-1", "agent-switched", 1, {"agent": AGENT})
            self._insert_v2_message(
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
                self._insert_v2_message(
                    db,
                    "assistant-1",
                    "assistant",
                    8,
                    {
                        "agent": AGENT,
                        "content": [{"type": "text", "text": probe.MARKER + "\n" + NONCE}],
                        "time": {"created": 3, "streamed": 4, "completed": 5},
                        "finish": "stop",
                        "rawFinish": "stop",
                    },
                )
            self._insert_v2_message(db, "idle-1", "idle", 13, {"reason": "completed"})
            if legacy_decoy:
                db.executescript("""CREATE TABLE message(id TEXT,session_id TEXT,data TEXT);
                       CREATE TABLE part(id TEXT,message_id TEXT,session_id TEXT,data TEXT);""")
                self._insert_message(db, "legacy-user", "user")
                self._insert_part(db, "legacy-user-part", "legacy-user", "legacy\n" + NONCE)
                self._insert_message(
                    db,
                    "legacy-assistant",
                    "assistant",
                    parentID="legacy-user",
                    agent=AGENT,
                    time={"completed": 1},
                )
                self._insert_part(
                    db,
                    "legacy-assistant-part",
                    "legacy-assistant",
                    probe.MARKER + "\n" + NONCE,
                )
            db.commit()

    @staticmethod
    def _insert_v2_message(db, message_id, kind, seq, data):
        db.execute(
            "INSERT INTO session_message VALUES(?,?,?,?,?,?,?)",
            (
                message_id,
                "session-1",
                kind,
                seq,
                seq,
                seq,
                json.dumps(data, ensure_ascii=False),
            ),
        )

    def _replace_v2_assistant_data(self, data):
        with closing(self._db()) as db:
            db.execute(
                "UPDATE session_message SET data=? WHERE id='assistant-1'",
                (json.dumps(data, ensure_ascii=False),),
            )
            db.commit()

    def _replace_v2_assistant_text(self, text):
        self._replace_v2_assistant_data(
            {
                "agent": AGENT,
                "content": [{"type": "text", "text": text}],
                "time": {"created": 3, "streamed": 4, "completed": 5},
                "finish": "stop",
                "rawFinish": "stop",
            }
        )

    @staticmethod
    def _insert_message(db, message_id, role, **extra):
        db.execute(
            "INSERT INTO message VALUES(?,?,?)",
            (message_id, "session-1", json.dumps({"role": role, **extra})),
        )

    @staticmethod
    def _insert_part(db, part_id, message_id, text, *, kind="text", session="session-1"):
        db.execute(
            "INSERT INTO part VALUES(?,?,?,?)",
            (part_id, message_id, session, json.dumps({"type": kind, "text": text})),
        )

    def _db(self):
        return sqlite3.connect(self.db_path)

    def _request(self, *, viewport=None, rendered=None, expected_marker=probe.MARKER):
        return {
            "source_root": str(self.source_root),
            "run_root": str(self.run_root),
            "cao_home": str(self.cao_home),
            "database_config_file": str(self.config),
            "terminal_id": TERMINAL,
            "viewport": "current turn\n" + NONCE if viewport is None else viewport,
            "rendered": probe.MARKER if rendered is None else rendered,
            "expected_agent": AGENT,
            "expected_marker": expected_marker,
            "stop": time.monotonic() + 2.0,
        }

    def _observe_payload(self, payload=None, **request_options):
        request = self._request(**request_options) if payload is None else payload
        digest = hashlib.sha256(NONCE.encode("utf-8")).hexdigest()
        with patch.object(probe, "_receipt_digest", return_value=digest):
            return probe._observe_payload(request, TARGET)

    def test_observed_v2_session_store_is_read_without_legacy_tables(self):
        self._replace_with_observed_v2_store()
        result = self._observe_payload()
        self.assertEqual(result["status"], "observed")
        self.assertIsNone(result["reason"])
        self.assertTrue(result["user_parent_bound"])
        self.assertTrue(result["assistant_turn_completed"])
        self.assertTrue(result["assistant_agent_matches"])
        self.assertEqual(result["native_text_part_count"], 1)
        self.assertFalse(result["raw_text_exported"])
        self.assertNotIn(NONCE, json.dumps(result))
        self.assertNotIn(probe.MARKER, json.dumps(result, ensure_ascii=False))

    def test_legacy_decoys_cannot_replace_missing_v2_assistant(self):
        self._replace_with_observed_v2_store(assistant=False, legacy_decoy=True)
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "assistant_receipt_not_unique")
        self.assertTrue(result["user_parent_bound"])
        self.assertEqual(result["native_text_part_count"], 0)
        self.assertFalse(result["raw_text_exported"])
        self.assertNotIn(NONCE, json.dumps(result))
        self.assertNotIn(probe.MARKER, json.dumps(result, ensure_ascii=False))

    def test_red_green_native_v2_parent_agent_and_receipt_binding(self):
        result = self._observe_payload()
        self.assertEqual(result["schema"], probe.SCHEMA)
        self.assertEqual(result["target_kind"], "unicode")
        self.assertEqual(result["status"], "observed")
        self.assertIsNone(result["reason"])
        self.assertTrue(result["active_receipt_bound"])
        self.assertTrue(result["owned_major2_root"])
        self.assertTrue(result["user_parent_bound"])
        self.assertTrue(result["assistant_agent_matches"])
        self.assertTrue(result["assistant_turn_completed"])
        self.assertTrue(result["native_equals_rendered"])
        self.assertTrue(result["raw_text_exported"] is False)
        self.assertTrue(result["raw_model_response_observed"] is False)
        self.assertTrue(result["acceptance_input_changed"] is False)

    def test_native_sqlite_authorizer_denies_other_tables_and_mutations(self):
        db = self._db()
        try:
            db.execute("CREATE TABLE credential(secret TEXT)")
            db.execute("INSERT INTO credential VALUES(?)", (SECRET,))
            db.commit()
            db.set_authorizer(native_v2_store._authorizer)
            for query in (
                "SELECT secret FROM credential",
                "DELETE FROM session_message",
                "ATTACH DATABASE ':memory:' AS extra",
            ):
                with self.subTest(query=query), self.assertRaises(sqlite3.DatabaseError):
                    db.execute(query)
        finally:
            db.close()

    def test_native_text_primitive_must_resolve_to_current_source_fixture(self):
        foreign = SimpleNamespace(__file__="/tmp/unpinned-native-v1-probe.py")
        with patch.dict(sys.modules, {"native_v2_store": foreign}):
            with self.assertRaises(probe.ProbeFailure) as raised:
                probe._native_text_parts(
                    self.source_root,
                    self.db_path,
                    NONCE,
                    AGENT,
                    time.monotonic() + 2,
                )
        self.assertEqual(raised.exception.reason, "source_root_invalid")

    def test_nonprivate_cao_home_ancestor_is_rejected(self):
        os.chmod(self.home / ".aws", 0o755)
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "cao_home_binding")
        self.assertFalse(result["active_receipt_bound"])

    def test_major_one_claim_is_not_accepted_as_major_two(self):
        from cli_agent_orchestrator.utils.opencode_ownership import PrivateConfigOwnership

        existing = PrivateConfigOwnership(self.cao_home, TERMINAL)
        existing.bind_restored()
        self.assertTrue(existing.cleanup())
        self._create_owned_db(1)
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "major_mismatch")
        self.assertFalse(result["owned_major2_root"])

    def test_foreign_terminal_ownership_is_not_scanned_or_accepted(self):
        from cli_agent_orchestrator.utils.opencode_ownership import PrivateConfigOwnership

        existing = PrivateConfigOwnership(self.cao_home, TERMINAL)
        existing.bind_restored()
        self.assertTrue(existing.cleanup())
        self._create_owned_db(2, terminal_id="different-terminal")
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertIn(result["reason"], {"ownership_missing", "owned_root_invalid"})

    def test_assistant_from_wrong_agent_is_unavailable(self):
        self._replace_v2_assistant_data(
            {
                "agent": "foreign-agent",
                "content": [{"type": "text", "text": probe.MARKER + "\n" + NONCE}],
                "time": {"created": 1, "completed": 2},
            }
        )
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "native_agent_mismatch")
        self.assertIs(result["assistant_agent_matches"], False)

    def test_newer_user_invalidates_the_prior_completed_assistant(self):
        with closing(self._db()) as db:
            self._insert_v2_message(db, "user-later", "user", 100, {"text": "new turn"})
            db.commit()
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "user_parent_not_current")
        self.assertTrue(result["active_receipt_bound"])
        self.assertTrue(result["owned_major2_root"])
        self.assertFalse(result["user_parent_bound"])
        self.assertEqual(result["native_text_part_count"], 0)
        self.assertFalse(result["raw_text_exported"])

    def test_active_nonce_requires_one_current_user_parent(self):
        with closing(self._db()) as db:
            self._insert_v2_message(
                db,
                "user-2",
                "user",
                15,
                {"text": "quoted\n" + NONCE, "files": [], "time": {"created": 6}},
            )
            db.commit()
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "user_parent_not_unique")
        self.assertTrue(result["active_receipt_bound"])
        self.assertTrue(result["owned_major2_root"])
        self.assertFalse(result["user_parent_bound"])
        self.assertEqual(result["native_text_part_count"], 0)
        self.assertFalse(result["raw_text_exported"])
        self.assertFalse(result["acceptance_input_changed"])
        self.assertNotIn("user_parent_status", result)
        self.assertNotIn(NONCE, json.dumps(result))
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn("user-1", json.dumps(result))

    def test_missing_active_user_parent_has_distinct_fixed_reason(self):
        with closing(self._db()) as db:
            db.execute("DELETE FROM session_message WHERE id='user-1'")
            db.commit()
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "user_parent_missing")
        self.assertTrue(result["active_receipt_bound"])
        self.assertTrue(result["owned_major2_root"])
        self.assertFalse(result["user_parent_bound"])
        self.assertEqual(result["native_text_part_count"], 0)
        self.assertFalse(result["raw_text_exported"])
        self.assertFalse(result["acceptance_input_changed"])
        self.assertNotIn("user_parent_status", result)
        self.assertNotIn(NONCE, json.dumps(result))
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn("user-1", json.dumps(result))

    def test_missing_active_nonce_digest_is_unavailable(self):
        payload = self._request(viewport="old receipt\n" + OTHER_NONCE)
        digest = hashlib.sha256(NONCE.encode("utf-8")).hexdigest()
        with patch.object(probe, "_receipt_digest", return_value=digest):
            result = probe._observe_payload(payload, TARGET)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "active_nonce_not_unique")
        self.assertFalse(result["active_receipt_bound"])

    def test_postgres_receipt_requires_verified_phase_and_digest_shape(self):
        for row in (
            ("0" * 64, "prepared"),
            ("bad", "result_verified"),
            [("0" * 64, "result_verified"), ("1" * 64, "result_verified")],
            None,
        ):
            with self.subTest(row_present=row is not None, phase=(row[1] if row else None)):
                with patch.object(probe, "_receipt_row", return_value=row):
                    with self.assertRaises(probe.ProbeFailure) as raised:
                        probe._receipt_digest(TARGET, TERMINAL, time.monotonic() + 2)
                self.assertEqual(raised.exception.reason, "pg_receipt_not_verified")
        verified = [("a" * 64, "result_verified")]
        with patch.object(probe, "_receipt_row", return_value=verified):
            self.assertEqual(
                probe._receipt_digest(TARGET, TERMINAL, time.monotonic() + 2), "a" * 64
            )

    def test_openecode_v2_schema_mismatch_is_unavailable(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("ALTER TABLE session_message RENAME TO old_session_message")
            db.commit()
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "native_schema_mismatch")

    def test_native_parts_must_be_closed_assistant_messages(self):
        self._replace_v2_assistant_data(
            {
                "agent": AGENT,
                "content": [{"type": "text", "text": probe.MARKER + "\n" + NONCE}],
            }
        )
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "assistant_receipt_not_unique")
        self.assertTrue(result["user_parent_bound"])

    def test_oversized_native_text_is_rejected_before_projection(self):
        self._replace_v2_assistant_text("x" * (probe.TEXT_CAP + 1) + "\n" + NONCE)
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "native_text_size_limit")

    def test_native_assistant_parts_are_compared_without_exporting_text(self):
        self._replace_v2_assistant_text("Validado ñ,\n中文.\n" + NONCE)
        result = self._observe_payload(rendered="Validado ñ, 中文.")
        self.assertEqual(result["status"], "observed")
        self.assertFalse(result["native_equals_rendered"])
        self.assertEqual(result["classification"], "native_text_contains_linebreak")
        self.assertEqual(result["native_public"]["lines"], 2)
        self.assertTrue(result["native_public"]["compact_marker"])
        for forbidden in (NONCE, SECRET, probe.MARKER, str(self.run_root), str(self.config)):
            self.assertNotIn(forbidden, json.dumps(result, ensure_ascii=False))

    def test_generic_target_missing_reports_actual_native_rendered_equality(self):
        native = "x" * 69
        self._replace_v2_assistant_text(native + "\n" + NONCE)
        for rendered, equal in ((native, True), ("y" * 69, False)):
            with self.subTest(equal=equal):
                result = self._observe_payload(rendered=rendered)
                self.assertEqual(result["status"], "observed")
                self.assertIs(result["native_equals_rendered"], equal)
                self.assertEqual(
                    result["classification"],
                    "native_equals_rendered" if equal else "native_text_differs_from_rendered",
                )
                for name in ("native_public", "rendered_public"):
                    self.assertFalse(result[name]["contains_marker"])
                    self.assertFalse(result[name]["exact_marker"])
                self.assertFalse(result["acceptance_input_changed"])
                self.assertNotIn(native, json.dumps(result))
                self.assertNotIn(rendered, json.dumps(result))

    def test_fixed_ack_target_uses_exact_marker_and_keeps_unicode_default(self):
        cases = (
            ("ACK", "native_equals_rendered", True, True),
            ("ACK extra", "ack_not_exact", False, True),
            ("ACK\n", "ack_not_exact", False, True),
            ("BACK", "ack_not_exact", False, True),
            (" ACK ", "ack_not_exact", False, True),
            ("A\nCK", "native_text_contains_linebreak", False, False),
            ("A\u00a0CK", "ack_not_exact", False, False),
        )
        for body, classification, exact, contains in cases:
            with self.subTest(shape=classification, exact=exact):
                self._replace_v2_assistant_text(body + "\n" + NONCE)
                result = self._observe_payload(expected_marker="ACK", rendered="ACK")
                self.assertEqual(result["status"], "observed")
                self.assertEqual(result["target_kind"], "ack")
                self.assertEqual(result["native_public"]["exact_marker"], exact)
                self.assertEqual(result["native_public"]["contains_marker"], contains)
                self.assertEqual(result["classification"], classification)
                self.assertEqual(result["native_equals_rendered"], body == "ACK")
                for forbidden in (body, NONCE, SECRET, str(self.run_root), str(self.config)):
                    self.assertNotIn(forbidden, json.dumps(result, ensure_ascii=False))

    def test_public_result_kind_is_bound_to_requested_ack_target(self):
        result = probe.observe_bounded(
            python=sys.executable,
            source_root=self.source_root,
            run_root=self.run_root,
            cao_home=self.cao_home,
            terminal_id=TERMINAL,
            database_config_file=self.config,
            viewport=NONCE,
            rendered="ACK",
            expected_agent=AGENT,
            deadline=time.monotonic() + 9,
            expected_marker="ACK",
            capture=lambda *_args: json.dumps(_valid_child_result()).encode(),
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "child_target_mismatch")
        self.assertEqual(result["target_kind"], "ack")

    def test_ack_removes_only_one_active_nonce_line_delimiter(self):
        self._replace_v2_assistant_text("ACK\n" + NONCE + "\n")
        result = self._observe_payload(expected_marker="ACK", rendered="ACK")
        self.assertEqual(result["status"], "observed")
        self.assertTrue(result["native_public"]["exact_marker"])
        self.assertEqual(result["classification"], "native_equals_rendered")

    def test_public_ack_result_is_returned_when_child_binds_requested_target(self):
        result = probe.observe_bounded(
            python=sys.executable,
            source_root=self.source_root,
            run_root=self.run_root,
            cao_home=self.cao_home,
            terminal_id=TERMINAL,
            database_config_file=self.config,
            viewport=NONCE,
            rendered="ACK",
            expected_agent=AGENT,
            deadline=time.monotonic() + 9,
            expected_marker="ACK",
            capture=lambda *_args: json.dumps(_valid_child_result("ACK")).encode(),
        )
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["target_kind"], "ack")

    def test_observe_bounded_refuses_unlisted_marker_before_capture(self):
        for marker in ("ack", "ACK ", "ACK extra", None):
            calls = []
            with self.subTest(marker_type=type(marker).__name__):
                result = probe.observe_bounded(
                    python=sys.executable,
                    source_root=self.source_root,
                    run_root=self.run_root,
                    cao_home=self.cao_home,
                    terminal_id=TERMINAL,
                    database_config_file=self.config,
                    viewport=NONCE,
                    rendered="ACK",
                    expected_agent=AGENT,
                    deadline=time.monotonic() + 9,
                    expected_marker=marker,
                    capture=lambda *args: calls.append(args),
                )
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["reason"], "invalid_input")
                self.assertEqual(calls, [])

    def test_path_symlinks_and_database_sidecars_fail_closed(self):
        root = self.native_root
        original = self.db_path
        moved = original.with_suffix(".saved")
        original.rename(moved)
        original.symlink_to(moved)
        with self.assertRaises(probe.ProbeFailure) as raised:
            probe._owned_database(self.run_root, self.cao_home, TERMINAL)
        self.assertEqual(raised.exception.reason, "owned_database_invalid")

    def test_owned_database_rejects_oversized_sidecar(self):
        sidecar = Path(str(self.db_path) + "-wal")
        with patch.object(probe, "DB_CAP", 1_000_000):
            sidecar.write_bytes(b"x" * 1_000_001)
            os.chmod(sidecar, 0o600)
            result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "owned_database_invalid")

    def test_owned_database_rejects_symlinked_sidecar(self):
        sidecar = Path(str(self.db_path) + "-wal")
        sidecar.symlink_to(self.config)
        with self.assertRaises(probe.ProbeFailure) as raised:
            probe._owned_database(self.run_root, self.cao_home, TERMINAL)
        self.assertEqual(raised.exception.reason, "owned_database_invalid")

    def test_expired_deadline_does_not_call_capture(self):
        called = []
        result = probe.observe_bounded(
            python=sys.executable,
            source_root=self.source_root,
            run_root=self.run_root,
            cao_home=self.cao_home,
            terminal_id=TERMINAL,
            database_config_file=self.config,
            viewport=NONCE,
            rendered=probe.MARKER,
            expected_agent=AGENT,
            deadline=time.monotonic() - 1,
            capture=lambda *args: called.append(args),
        )
        self.assertEqual(result["reason"], "deadline")
        self.assertEqual(called, [])

    def test_nonfinite_or_unrepresentable_deadline_is_rejected(self):
        for deadline in (float("nan"), 10**1000):
            with self.subTest(deadline_type=type(deadline).__name__):
                result = probe.observe_bounded(
                    python=sys.executable,
                    source_root=self.source_root,
                    run_root=self.run_root,
                    cao_home=self.cao_home,
                    terminal_id=TERMINAL,
                    database_config_file=self.config,
                    viewport=NONCE,
                    rendered=probe.MARKER,
                    expected_agent=AGENT,
                    deadline=deadline,
                    capture=lambda *_args: self.fail("capture must not run"),
                )
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["reason"], "invalid_input")

    def test_outer_capture_contract_is_private_bounded_and_cleans_request(self):
        captured = {}

        def capture(argv, stop, max_bytes):
            request_path = Path(argv[-1])
            captured["argv"] = argv
            captured["stop"] = stop
            captured["max_bytes"] = max_bytes
            captured["mode"] = stat.S_IMODE(request_path.lstat().st_mode)
            captured["request"] = json.loads(request_path.read_text(encoding="utf-8"))
            self.assertTrue(request_path.is_relative_to(self.run_root))
            return json.dumps(_valid_child_result(), separators=(",", ":")).encode() + b"\n"

        viewport = "screen-private-" + SECRET + "\n" + NONCE
        rendered = "parsed-private-" + SECRET
        result = probe.observe_bounded(
            python=sys.executable,
            source_root=self.source_root,
            run_root=self.run_root,
            cao_home=self.cao_home,
            terminal_id=TERMINAL,
            database_config_file=self.config,
            viewport=viewport,
            rendered=rendered,
            expected_agent=AGENT,
            deadline=time.monotonic() + 9,
            capture=capture,
        )
        self.assertEqual(result["status"], "observed")
        self.assertEqual(captured["argv"][0], sys.executable)
        self.assertEqual(captured["argv"][1], "-I")
        self.assertEqual(captured["mode"], 0o600)
        self.assertLessEqual(captured["max_bytes"], probe.CHILD_OUTPUT_CAP)
        self.assertLessEqual(captured["stop"], time.monotonic() + probe.CHILD_BUDGET_SECONDS)
        self.assertEqual(captured["request"]["viewport"], viewport)
        self.assertEqual(captured["request"]["rendered"], rendered)
        self.assertFalse(
            any(p.name.startswith(probe.REQUEST_PREFIX) for p in self.run_root.iterdir())
        )
        for forbidden in (SECRET, NONCE, str(self.run_root), str(self.config)):
            self.assertNotIn(forbidden, json.dumps(result, ensure_ascii=False))
        for argument in captured["argv"]:
            self.assertNotIn(SECRET, argument)
            self.assertNotIn(NONCE, argument)

    def test_child_rejects_request_staged_outside_its_verified_run_root(self):
        wrong_root = self.run_root / "wrong-request-root"
        wrong_root.mkdir(mode=0o700)
        os.chmod(wrong_root, 0o700)
        request_path, identity = probe._make_request_file(wrong_root, self._request())
        output = io.StringIO()
        try:
            with patch("sys.stdout", output):
                self.assertEqual(probe._child_entry(str(request_path)), 0)
        finally:
            probe._remove_request(request_path, identity, wrong_root)
        child = json.loads(output.getvalue())
        self.assertEqual(child["status"], "unavailable")
        self.assertEqual(child["reason"], "request_file_unavailable")
        self.assertNotIn(str(self.run_root), output.getvalue())

    def test_capture_child_timeout_is_redacted_and_request_is_removed(self):
        failures = (
            TimeoutError("secret text: " + SECRET),
            ValueError("capture_deadline"),
            subprocess.TimeoutExpired("private-child", 3),
        )
        for failure in failures:
            observed = {}

            def capture(argv, stop, max_bytes):
                request_path = Path(argv[-1])
                observed["exists_during_capture"] = request_path.exists()
                raise failure

            with self.subTest(failure=type(failure).__name__):
                result = probe.observe_bounded(
                    python=sys.executable,
                    source_root=self.source_root,
                    run_root=self.run_root,
                    cao_home=self.cao_home,
                    terminal_id=TERMINAL,
                    database_config_file=self.config,
                    viewport=NONCE,
                    rendered=SECRET,
                    expected_agent=AGENT,
                    deadline=time.monotonic() + 9,
                    capture=capture,
                )
                self.assertTrue(observed["exists_during_capture"])
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["reason"], "child_timeout")
                self.assertNotIn(SECRET, json.dumps(result))
                self.assertNotIn(NONCE, json.dumps(result))
                self.assertFalse(
                    any(p.name.startswith(probe.REQUEST_PREFIX) for p in self.run_root.iterdir())
                )

    def test_invalid_json_extra_keys_and_unbounded_child_values_are_rejected(self):
        for child_output in (
            b"not-json",
            json.dumps({**_valid_child_result(), "private_text": SECRET}).encode(),
            json.dumps({**_valid_child_result(), "native_public": {"characters": True}}).encode(),
        ):
            with self.subTest(raw_child_bytes=len(child_output)):
                result = probe.observe_bounded(
                    python=sys.executable,
                    source_root=self.source_root,
                    run_root=self.run_root,
                    cao_home=self.cao_home,
                    terminal_id=TERMINAL,
                    database_config_file=self.config,
                    viewport=NONCE,
                    rendered=SECRET,
                    expected_agent=AGENT,
                    deadline=time.monotonic() + 9,
                    capture=lambda *_args, child_output=child_output: child_output,
                )
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["reason"], "child_output_invalid")
                self.assertNotIn(SECRET, json.dumps(result))

    def test_observed_child_projection_must_report_bounded_read(self):
        for field, value in (
            ("elapsed_ms", 3001),
            ("within_three_seconds", False),
            ("within_original_deadline", False),
        ):
            child = _valid_child_result()
            child[field] = value
            with self.subTest(field=field):
                self.assertIsNone(probe._strict_result(child))

    def test_capture_output_cap_and_request_input_cap_are_enforced(self):
        result = probe.observe_bounded(
            python=sys.executable,
            source_root=self.source_root,
            run_root=self.run_root,
            cao_home=self.cao_home,
            terminal_id=TERMINAL,
            database_config_file=self.config,
            viewport="x" * (probe.INPUT_TEXT_CAP + 1),
            rendered="",
            expected_agent=AGENT,
            deadline=time.monotonic() + 9,
            capture=lambda *_args: (_ for _ in ()).throw(AssertionError("must not spawn")),
        )
        self.assertEqual(result["reason"], "input_size_limit")

    def test_request_cleanup_failure_never_returns_observed(self):
        replaced = {}

        def capture(argv, stop, max_bytes):
            request_path = Path(argv[-1])
            replaced["path"] = request_path
            request_path.unlink()
            request_path.write_text("replacement", encoding="utf-8")
            return json.dumps(_valid_child_result()).encode()

        result = probe.observe_bounded(
            python=sys.executable,
            source_root=self.source_root,
            run_root=self.run_root,
            cao_home=self.cao_home,
            terminal_id=TERMINAL,
            database_config_file=self.config,
            viewport=NONCE,
            rendered=probe.MARKER,
            expected_agent=AGENT,
            deadline=time.monotonic() + 9,
            capture=capture,
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "request_cleanup_failed")
        self.assertTrue(replaced["path"].exists())
        self.assertEqual(replaced["path"].read_text(encoding="utf-8"), "replacement")


if __name__ == "__main__":
    unittest.main()
