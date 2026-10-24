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

import native_v1_probe as native_v1_probe
import native_v2_text_probe as probe

NONCE = "CAO-TURN-RECEIPT-" + "b" * 32
OTHER_NONCE = "CAO-TURN-RECEIPT-" + "c" * 32
SECRET = "t111-private-canary-never-export"
TERMINAL = "sender-fixture"
AGENT = "v2-sender"
TARGET = SimpleNamespace(schema="cao_test", connection_parameters=lambda: {})


def _valid_child_result():
    result = probe._base_result(probe.MARKER)
    shape = probe._shape(probe.MARKER)
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
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executescript("""CREATE TABLE message(id TEXT,session_id TEXT,data TEXT);
                   CREATE TABLE part(id TEXT,message_id TEXT,session_id TEXT,data TEXT);
                   CREATE TABLE credential(secret TEXT);""")
            db.execute("INSERT INTO credential VALUES(?)", (SECRET,))
            self._insert_message(db, "user-1", "user")
            self._insert_part(db, "part-user", "user-1", "prompt\n" + NONCE)
            self._insert_message(
                db,
                "assistant-1",
                "assistant",
                parentID="user-1",
                agent=AGENT,
                time={"completed": 1},
            )
            self._insert_part(db, "part-assistant", "assistant-1", probe.MARKER + "\n" + NONCE)
            db.commit()

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

    def _request(self, *, viewport=None, rendered=None):
        return {
            "source_root": str(self.source_root),
            "run_root": str(self.run_root),
            "cao_home": str(self.cao_home),
            "database_config_file": str(self.config),
            "terminal_id": TERMINAL,
            "viewport": "current turn\n" + NONCE if viewport is None else viewport,
            "rendered": probe.MARKER if rendered is None else rendered,
            "expected_agent": AGENT,
            "stop": time.monotonic() + 2.0,
        }

    def _observe_payload(self, payload=None, **request_options):
        request = self._request(**request_options) if payload is None else payload
        digest = hashlib.sha256(NONCE.encode("utf-8")).hexdigest()
        with patch.object(probe, "_receipt_digest", return_value=digest):
            return probe._observe_payload(request, TARGET)

    def test_red_green_native_v2_parent_agent_and_receipt_binding(self):
        result = self._observe_payload()
        self.assertEqual(result["schema"], probe.SCHEMA)
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
            db.set_authorizer(native_v1_probe._authorizer)
            for query in (
                "SELECT secret FROM credential",
                "DELETE FROM part",
                "ATTACH DATABASE ':memory:' AS extra",
            ):
                with self.subTest(query=query), self.assertRaises(sqlite3.DatabaseError):
                    db.execute(query)
        finally:
            db.close()

    def test_native_text_primitive_must_resolve_to_current_source_fixture(self):
        foreign = SimpleNamespace(__file__="/tmp/unpinned-native-v1-probe.py")
        with patch.dict(sys.modules, {"native_v1_probe": foreign}):
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
        with closing(self._db()) as db:
            db.execute(
                "UPDATE message SET data=? WHERE id='assistant-1'",
                (
                    json.dumps(
                        {
                            "role": "assistant",
                            "parentID": "user-1",
                            "agent": "foreign-agent",
                            "time": {"completed": 1},
                        }
                    ),
                ),
            )
            db.commit()
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "native_agent_mismatch")
        self.assertIs(result["assistant_agent_matches"], False)

    def test_active_nonce_requires_one_current_user_parent(self):
        with closing(self._db()) as db:
            self._insert_message(db, "user-2", "user")
            self._insert_part(db, "part-user-2", "user-2", "quoted\n" + NONCE)
            db.commit()
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "user_parent_not_unique")
        self.assertTrue(result["active_receipt_bound"])

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
            db.execute("ALTER TABLE part RENAME TO old_part")
            db.commit()
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "native_schema_mismatch")

    def test_native_parts_must_be_closed_assistant_messages(self):
        with closing(self._db()) as db:
            db.execute(
                "UPDATE message SET data=? WHERE id='assistant-1'",
                (json.dumps({"role": "assistant", "parentID": "user-1", "agent": AGENT}),),
            )
            db.commit()
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "assistant_receipt_not_unique")
        self.assertTrue(result["user_parent_bound"])

    def test_oversized_native_text_is_rejected_before_projection(self):
        with closing(self._db()) as db:
            self._insert_part(
                db,
                "part-large",
                "assistant-1",
                "x" * (probe.TEXT_CAP + 1) + "\n" + NONCE,
            )
            db.commit()
        result = self._observe_payload()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "native_text_size_limit")

    def test_native_assistant_parts_are_compared_without_exporting_text(self):
        with closing(self._db()) as db:
            db.execute(
                "UPDATE part SET data=? WHERE id='part-assistant'",
                (json.dumps({"type": "text", "text": "Validado ñ,\n中文.\n" + NONCE}),),
            )
            db.commit()
        result = self._observe_payload(rendered="Validado ñ, 中文.")
        self.assertEqual(result["status"], "observed")
        self.assertFalse(result["native_equals_rendered"])
        self.assertEqual(result["classification"], "native_text_contains_linebreak")
        self.assertEqual(result["native_public"]["lines"], 2)
        self.assertTrue(result["native_public"]["compact_marker"])
        for forbidden in (NONCE, SECRET, probe.MARKER, str(self.run_root), str(self.config)):
            self.assertNotIn(forbidden, json.dumps(result, ensure_ascii=False))

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
