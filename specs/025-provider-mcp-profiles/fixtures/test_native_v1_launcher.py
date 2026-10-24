"""Execute the actual launcher phase collector without CLI/model/API/PostgreSQL."""

import ast
import inspect
import json
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

import native_acceptance_contract as acceptance
import native_phase_observer as observers
import native_phase_readers as phase_readers

SOURCE = Path(__file__).with_name("run_native_v1_acceptance.py")


def functions(*names):
    tree = ast.parse(SOURCE.read_text())
    return ast.Module(
        body=[
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
        ],
        type_ignores=[],
    )


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, n):
        self.now += n


class Readers:
    def __init__(self, clock, receiver=True):
        self.clock = clock
        self.receiver = receiver
        self.delivered = False
        self.closed = False
        self.calls = []

    def read_state(self, terminal, stop):
        self.calls.append(terminal)
        sequence = 1 if self.delivered else 0
        complete = self.delivered and (
            terminal == "sender"
            and self.clock.now >= 0.5
            or terminal == "receiver"
            and self.receiver
            and self.clock.now >= 1
        )
        return dict(
            http_status=200,
            status="completed" if complete else "idle" if not self.delivered else "processing",
            turn_sequence=sequence,
            turn_completed=1 if complete else 0,
        )

    def read_output(self, terminal, stop, *, max_bytes):
        return "wrong sender reply" if terminal == "sender" else "ACK"

    def capture_viewport(self, terminal, stop, *, max_bytes):
        return "synthetic viewport"

    def close(self):
        self.closed = True


class LauncherTests(unittest.TestCase):
    def run_phase(self, receiver=True, transient_baseline=False, index=0, turn_override=None):
        clock = Clock()
        readers = Readers(clock, receiver)
        snapshot_calls = []
        if transient_baseline:
            original = readers.read_state

            def delayed(terminal, stop):
                value = original(terminal, stop)
                if not readers.delivered and clock.now < 0.4:
                    value["status"] = "unknown"
                return value

            readers.read_state = delayed

        def post(*args, **kwargs):
            readers.delivered = True
            return SimpleNamespace(status_code=200, json=lambda: {"turn_sequence": 1})

        scope = dict(
            acceptance=acceptance,
            NativePhaseObserver=observers.NativePhaseObserver,
            RoleScope=observers.RoleScope,
            server=SimpleNamespace(url="http://owned"),
            sender_id="sender",
            receiver_id="receiver",
            terminals={"receiver": {"id": "receiver"}, "sender": {"id": "sender"}},
            root="/unused",
            caohome="/unused/home",
            targets=SimpleNamespace(main=None),
            json=json,
            time=clock,
            requests=SimpleNamespace(post=post),
            _INBOX_FAILURE_TERMINAL_STATES=frozenset(
                {
                    "unknown",
                    "idle",
                    "processing",
                    "completed",
                    "waiting_user_answer",
                    "waiting_quota",
                    "reconcile",
                    "error",
                }
            ),
            _capture_failure_inbox_snapshot=lambda phase_index, message, phase, active_readers: (
                snapshot_calls.append((phase_index, message, active_readers.closed))
            ),
        )
        exec(
            compile(
                functions(
                    "_accept_last_receipt",
                    "_bounded_count",
                    "_record_failure_receiver_observation",
                    "_wait_native_ready",
                    "_collect_message_phase",
                ),
                str(SOURCE),
                "exec",
            ),
            scope,
        )
        durable = {
            role: dict(
                generation="a" * 32,
                receipt_sha256="b" * 64,
                result_sha256="c" * 64,
                phase="result_verified",
            )
            for role in ("sender", "receiver")
        }
        scope.update(
            receipt={},
            restart_receipts_before=durable,
            restart_readiness_stop=75,
            _read_durable_turns=lambda stop: durable,
        )
        readers.read_turn = lambda terminal, stop: dict(
            generation="a" * 32, state="verified", attempts=0, allowed_actions=[]
        ) | (turn_override or {})
        diag = dict(
            schema="caos.t089.native-text-shapes.v1",
            diagnostic_only=True,
            raw_text_exported=False,
            acceptance_input_changed=False,
            status="observed",
            native_agent_matches=True,
            active_receipt_bound=True,
            exact_owned_root=True,
            within_three_seconds=True,
            within_original_deadline=True,
        )
        phase = {}
        with (
            patch.object(observers.time, "monotonic", side_effect=clock.monotonic),
            patch.object(observers.probe, "observe", return_value=diag) as probe,
        ):
            scope["_collect_message_phase"](index, "unchanged Unicode message", phase, readers)
        self._last_snapshot_calls = snapshot_calls
        return phase, readers, clock, [call.args[2] for call in probe.call_args_list]

    def snapshot_scope(
        self, *, api_rows, pg_result, clock=None, check_error=None, response_terminal=None
    ):
        import datetime
        import math
        import os
        import subprocess

        sender = "owned-sender-test"
        receiver = "owned-receiver-test"
        message = "private-original-message"
        password = "private-postgres-password"
        store_id = "11111111-2222-4333-8444-555555555555"

        target = SimpleNamespace(
            host="127.0.0.1",
            port=5432,
            database="owned_test",
            user="runtime_test",
            schema="owned_test",
            store_id=store_id,
            scope="main",
            identity=("127.0.0.1", 5432, "owned_test", "owned_test", store_id),
            connection_parameters=Mock(
                return_value={
                    "host": "127.0.0.1",
                    "port": 5432,
                    "dbname": "owned_test",
                    "user": "runtime_test",
                    "password": password,
                    "sslmode": "disable",
                }
            ),
        )
        ownership_calls = []

        class SnapshotReaders:
            def _check(self, terminal, stop):
                ownership_calls.append(terminal)
                if check_error is not None:
                    raise check_error
                if terminal != receiver:
                    raise ValueError("wrong terminal")
                return {"session": "owned-session", "pane": "owned-pane"}

            def _json(self, terminal, stop, max_bytes):
                self._check(terminal, stop)
                if max_bytes != 65537:
                    raise ValueError("wrong response bound")
                return {
                    "id": response_terminal or receiver,
                    "session_name": "owned-session",
                    "name": "owned-pane",
                    "status": "processing",
                    "turn_sequence": 1,
                    "turn_completed": 0,
                    "runtime_id": "private-runtime-id",
                    "runtime_incarnation_id": "private-runtime-incarnation",
                    "native_swarm_pending": False,
                }

        readers = SnapshotReaders()
        expected = lambda value, sender_id: value + "|" + sender_id
        scope = {
            "json": json,
            "datetime": datetime,
            "math": math,
            "os": os,
            "re": re,
            "subprocess": subprocess,
            "sys": sys,
            "time": clock or time,
            "urlsplit": urlsplit,
            "HTTPStatusError": phase_readers.HTTPStatusError,
            "_INBOX_FAILURE_SNAPSHOT_GRACE_SECONDS": 10.0,
            "_INBOX_FAILURE_SNAPSHOT_WORK_SECONDS": 7.5,
            "_INBOX_FAILURE_SNAPSHOT_REAP_SECONDS": 1.0,
            "_INBOX_FAILURE_SNAPSHOT_MAX_BYTES": 4096,
            "_INBOX_FAILURE_SNAPSHOT_ROW_LIMIT": 10,
            "_INBOX_FAILURE_STATUSES": frozenset({"pending", "delivered", "reconcile", "failed"}),
            "_INBOX_FAILURE_TERMINAL_STATES": frozenset(
                {
                    "unknown",
                    "idle",
                    "processing",
                    "completed",
                    "waiting_user_answer",
                    "waiting_quota",
                    "reconcile",
                    "error",
                }
            ),
        }
        exec(
            compile(
                functions(
                    "_bounded_count",
                    "_failure_snapshot_rows_summary",
                    "_record_failure_receiver_observation",
                    "_run_bounded_private_child",
                    "_read_owned_postgres_inbox_snapshot",
                    "_capture_failure_inbox_snapshot",
                ),
                str(SOURCE),
                "exec",
            ),
            scope,
        )
        scope["_actual_read_owned_postgres_inbox_snapshot"] = scope[
            "_read_owned_postgres_inbox_snapshot"
        ]
        scope.update(
            pgentered=True,
            terminals={"sender": {"id": sender}, "receiver": {"id": receiver}},
            sender_id=sender,
            receiver_id=receiver,
            PRE_RESTART_MESSAGE=message,
            POST_RESTART_MESSAGE="private-next-message",
            expected_persisted_message=expected,
            targets=SimpleNamespace(main=target),
            server=SimpleNamespace(url="http://127.0.0.1:54321"),
            readers=readers,
            _failure_snapshot_rows_summary=scope["_failure_snapshot_rows_summary"],
            http_json_bytes=Mock(return_value=json.dumps(api_rows).encode()),
            _read_owned_postgres_inbox_snapshot=Mock(return_value=pg_result),
        )
        return scope, readers, target, ownership_calls, (sender, receiver, message, password)

    def test_actual_collector_observes_receiver_despite_failed_sender_unicode(self):
        phase, readers, clock, calls = self.run_phase()
        self.assertFalse(phase["last_receipt_unicode_verified"])
        self.assertTrue(phase["sender_completed"])
        self.assertTrue(phase["receiver_ack_exact"])
        self.assertEqual(phase["receiver_native_text_diagnostic"]["status"], "observed")
        self.assertEqual(calls, ["sender", "receiver"])
        self.assertEqual(self._last_snapshot_calls, [])
        self.assertTrue(readers.closed)
        self.assertLess(clock.now, 75)

    def test_actual_collector_keeps_original_deadline_for_untriggered_receiver(self):
        phase, readers, clock, calls = self.run_phase(False)
        self.assertEqual(phase["model_seconds"], 75)
        self.assertEqual(calls, ["sender"])
        self.assertEqual(len(self._last_snapshot_calls), 1)
        self.assertEqual(self._last_snapshot_calls[0][0], 0)
        self.assertFalse(self._last_snapshot_calls[0][2])
        self.assertTrue(readers.closed)
        self.assertEqual(
            phase["receiver_native_text_diagnostic"]["reason"], "not_triggered_by_deadline"
        )
        self.assertFalse(acceptance.receiver_phases_exact([phase, phase]))

    def test_actual_collector_waits_for_transient_baseline_before_submitting(self):
        phase, readers, clock, calls = self.run_phase(transient_baseline=True)
        self.assertTrue(phase["receiver_ack_exact"])
        self.assertGreaterEqual(phase["readiness_seconds"], 0.8)
        self.assertEqual(phase["receiver_sequence_before"], 0)
        self.assertTrue(readers.closed)

    def test_actual_postrestart_collector_uses_new_local_baseline_and_durable_identity(self):
        phase, readers, clock, calls = self.run_phase(transient_baseline=True, index=1)
        self.assertEqual(phase["receiver_sequence_before"], 0)
        self.assertEqual(phase["receiver_sequence_after"], 1)
        self.assertTrue(phase["restart_durable_receipts_exact"])
        self.assertTrue(phase["receiver_ack_exact"])
        self.assertTrue(readers.closed)

    def test_ack_failure_snapshot_precedes_reader_cleanup_and_ack_assertion(self):
        source = SOURCE.read_text()
        tree = ast.parse(source)
        collector = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_collect_message_phase"
        )
        snapshot_calls = [
            node
            for node in ast.walk(collector)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_capture_failure_inbox_snapshot"
        ]
        close_calls = [
            node
            for node in ast.walk(collector)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "close"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "readers"
        ]
        self.assertEqual(len(snapshot_calls), 1)
        self.assertEqual(len(close_calls), 1)
        self.assertLess(snapshot_calls[0].lineno, close_calls[0].lineno)

        phase_call = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_collect_message_phase"
        )
        ack_assertion = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assert)
            and node.msg is not None
            and "receiver_ack_not_observed" in ast.unparse(node.msg)
        )
        self.assertLess(phase_call.lineno, ack_assertion.lineno)

    def test_failure_snapshot_captures_owned_rows_and_current_receiver_state_privately(self):
        sender = "owned-sender-test"
        receiver = "owned-receiver-test"
        message = "private-original-message"
        persisted = message + "|" + sender
        api_rows = [
            {
                "id": 73,
                "sender_id": sender,
                "receiver_id": receiver,
                "message": persisted,
                "status": "pending",
                "created_at": "2026-10-24T12:00:00",
            }
        ]
        pg_result = {
            "status": "observed",
            "target_identity_match": True,
            "target_identity_status": "matched",
            "row_count": 1,
            "sender_receiver_match": True,
            "expected_messages_match": True,
            "api_pg_rows_match": True,
            "provider_variant_is_opencode_v1": True,
            "statuses": ["pending"],
        }
        scope, readers, target, owner_checks, private_values = self.snapshot_scope(
            api_rows=api_rows, pg_result=pg_result
        )
        phase = {
            "receiver_ack_exact": False,
            "receiver_ack_within_deadline": False,
            "receiver_turn_completed_once": False,
            "last_receipt_unicode_verified": True,
            "sender_completed": True,
        }
        snapshot = scope["_capture_failure_inbox_snapshot"](0, message, phase, readers)

        self.assertEqual(snapshot["status"], "complete")
        self.assertTrue(snapshot["diagnostic_only"])
        self.assertTrue(snapshot["within_deadline"])
        self.assertEqual(snapshot["api_inbox"]["row_count"], 1)
        self.assertTrue(snapshot["api_inbox"]["sender_receiver_match"])
        self.assertTrue(snapshot["api_inbox"]["expected_messages_match"])
        self.assertEqual(snapshot["api_inbox"]["statuses"], ["pending"])
        self.assertEqual(snapshot["receiver"]["http_status"], 200)
        self.assertEqual(snapshot["receiver"]["terminal_status"], "processing")
        self.assertEqual(snapshot["receiver"]["turn_sequence"], 1)
        self.assertEqual(snapshot["receiver"]["turn_completed"], 0)
        self.assertIsNone(snapshot["receiver"]["phase_elapsed_seconds"])
        self.assertIsNotNone(snapshot["receiver"]["snapshot_elapsed_seconds"])
        self.assertTrue(snapshot["receiver"]["runtime_id_present"])
        self.assertTrue(snapshot["receiver"]["runtime_incarnation_id_present"])
        self.assertIs(snapshot["receiver"]["native_swarm_pending"], False)
        self.assertTrue(snapshot["postgres_inbox"]["provider_variant_is_opencode_v1"])
        self.assertEqual(target.connection_parameters.call_count, 1)
        self.assertTrue(owner_checks)
        self.assertFalse(acceptance.receiver_phases_exact([phase, phase]))
        encoded_phase = json.dumps(phase, ensure_ascii=False)
        for private_value in private_values:
            self.assertNotIn(private_value, encoded_phase)

    def test_failure_snapshot_wrong_rows_and_unavailable_reads_stay_diagnostic(self):
        scope, _, _, _, values = self.snapshot_scope(api_rows=[], pg_result={})
        row_summary = scope["_failure_snapshot_rows_summary"]
        sender, receiver, message, _ = values
        persisted = message + "|" + sender
        wrong_rows = [
            {
                "id": 1,
                "sender_id": "foreign-sender",
                "receiver_id": receiver,
                "message": persisted,
                "status": "pending",
                "created_at": "2026-10-24T12:00:00",
            }
        ]
        wrong = row_summary(wrong_rows, [persisted], sender, receiver)
        self.assertFalse(wrong["sender_receiver_match"])
        self.assertFalse(wrong["expected_messages_match"])
        self.assertNotIn("foreign-sender", json.dumps(wrong))
        wrong_message = [
            {
                **wrong_rows[0],
                "sender_id": sender,
                "message": "different-private-message",
            }
        ]
        wrong = row_summary(wrong_message, [persisted], sender, receiver)
        self.assertTrue(wrong["sender_receiver_match"])
        self.assertFalse(wrong["expected_messages_match"])
        self.assertNotIn("different-private-message", json.dumps(wrong))

        wrong_pg = {
            "status": "observed",
            "target_identity_match": True,
            "target_identity_status": "matched",
            "row_count": 1,
            "sender_receiver_match": False,
            "expected_messages_match": False,
            "api_pg_rows_match": False,
            "provider_variant_is_opencode_v1": True,
            "statuses": ["pending"],
        }
        scope, readers, _, _, _ = self.snapshot_scope(api_rows=wrong_rows, pg_result=wrong_pg)
        phase = {
            "receiver_ack_exact": False,
            "receiver_ack_within_deadline": False,
            "receiver_turn_completed_once": False,
        }
        snapshot = scope["_capture_failure_inbox_snapshot"](0, message, phase, readers)
        self.assertEqual(snapshot["status"], "complete")
        self.assertFalse(snapshot["api_inbox"]["sender_receiver_match"])
        self.assertFalse(snapshot["postgres_inbox"]["expected_messages_match"])
        self.assertFalse(acceptance.receiver_phases_exact([phase, phase]))
        for private_value in values:
            self.assertNotIn(private_value, json.dumps(phase, ensure_ascii=False))

        pg_result = {
            "status": "unavailable",
            "target_identity_match": False,
            "target_identity_status": "unavailable",
            "row_count": 0,
            "sender_receiver_match": False,
            "expected_messages_match": False,
            "api_pg_rows_match": False,
            "provider_variant_is_opencode_v1": False,
            "statuses": [],
        }
        scope, readers, _, _, _ = self.snapshot_scope(api_rows=[], pg_result=pg_result)
        scope["http_json_bytes"].side_effect = ValueError("private response body")
        phase = {
            "receiver_ack_exact": False,
            "receiver_ack_within_deadline": False,
            "receiver_turn_completed_once": False,
            "last_receipt_unicode_verified": True,
            "sender_completed": True,
            "_failure_receiver_state": {
                "bound": True,
                "http_status": 200,
                "status": "processing",
                "turn_sequence": 1,
                "turn_completed": 0,
                "source": "phase_poll",
                "phase_elapsed_seconds": 75.0,
            },
        }
        snapshot = scope["_capture_failure_inbox_snapshot"](0, values[2], phase, readers)
        self.assertEqual(snapshot["status"], "partial")
        self.assertEqual(snapshot["api_inbox"]["status"], "unavailable")
        self.assertEqual(snapshot["receiver"]["source"], "failure_snapshot")
        self.assertFalse(phase["receiver_ack_exact"])
        self.assertFalse(phase["receiver_turn_completed_once"])
        self.assertTrue(phase["last_receipt_unicode_verified"])
        self.assertTrue(phase["sender_completed"])
        self.assertNotIn("private response body", json.dumps(phase))

    def test_failure_snapshot_skips_wrong_owner_and_marks_late_api_read(self):
        result = {
            "status": "observed",
            "target_identity_match": True,
            "target_identity_status": "matched",
            "row_count": 0,
            "sender_receiver_match": False,
            "expected_messages_match": False,
            "api_pg_rows_match": False,
            "provider_variant_is_opencode_v1": False,
            "statuses": [],
        }
        scope, readers, _, _, values = self.snapshot_scope(
            api_rows=[], pg_result=result, check_error=RuntimeError("not owned")
        )
        snapshot = scope["_capture_failure_inbox_snapshot"](0, values[2], {}, readers)
        self.assertEqual(snapshot["status"], "invalid_context")
        scope["http_json_bytes"].assert_not_called()
        scope["_read_owned_postgres_inbox_snapshot"].assert_not_called()

        scope, readers, _, _, values = self.snapshot_scope(
            api_rows=[], pg_result=result, response_terminal="foreign-terminal"
        )
        snapshot = scope["_capture_failure_inbox_snapshot"](0, values[2], {}, readers)
        self.assertEqual(snapshot["receiver"]["fresh_read_status"], "identity_mismatch")
        self.assertEqual(snapshot["receiver"]["source"], "unavailable")
        self.assertNotIn("foreign-terminal", json.dumps(snapshot))

        class LateClock:
            now = 0.0

            def monotonic(self):
                return self.now

        late = LateClock()
        scope, readers, _, _, values = self.snapshot_scope(
            api_rows=[], pg_result=result, clock=late
        )

        def late_api(*args):
            late.now = 2.1
            return json.dumps([]).encode()

        scope["http_json_bytes"].side_effect = late_api
        snapshot = scope["_capture_failure_inbox_snapshot"](0, values[2], {}, readers)
        self.assertEqual(snapshot["api_inbox"]["status"], "deadline")
        self.assertEqual(snapshot["status"], "deadline")
        self.assertTrue(snapshot["within_deadline"])
        self.assertLessEqual(snapshot["elapsed_seconds"], 10.0)

        scope, readers, _, _, values = self.snapshot_scope(api_rows=[], pg_result=result)
        scope["_read_owned_postgres_inbox_snapshot"].side_effect = TimeoutError(
            "private child detail"
        )
        snapshot = scope["_capture_failure_inbox_snapshot"](0, values[2], {}, readers)
        self.assertEqual(snapshot["postgres_inbox"]["status"], "deadline")
        self.assertEqual(snapshot["status"], "deadline")
        self.assertTrue(snapshot["within_deadline"])
        self.assertNotIn("private child detail", json.dumps(snapshot))

    def test_postgres_snapshot_wrapper_uses_private_stdin_and_sanitized_output(self):
        scope, _, _, _, values = self.snapshot_scope(api_rows=[], pg_result={})
        helper = scope["_actual_read_owned_postgres_inbox_snapshot"]
        safe = {
            "status": "observed",
            "target_identity_match": True,
            "target_identity_status": "matched",
            "row_count": 1,
            "sender_receiver_match": True,
            "expected_messages_match": True,
            "api_pg_rows_match": True,
            "provider_variant_is_opencode_v1": True,
            "statuses": ["pending"],
        }
        child = Mock(return_value=json.dumps(safe).encode())
        scope["_run_bounded_private_child"] = child
        context = {
            "connection_parameters": {"password": values[3]},
            "schema": "owned_test",
            "store_id": "11111111-2222-4333-8444-555555555555",
            "scope": "main",
            "sender_id": values[0],
            "receiver_id": values[1],
            "expected_messages": [values[2]],
            "api_rows": [{"message": values[2]}],
            "api_rows_observed": True,
        }
        result = helper(context, time.monotonic() + 1)
        argv = child.call_args.args[0]
        self.assertEqual(argv[1], "-I")
        self.assertEqual(argv[2], "-B")
        compile(argv[4], "<private-inbox-snapshot-child>", "exec")
        self.assertNotIn(values[0], repr(argv))
        self.assertNotIn(values[1], repr(argv))
        self.assertNotIn(values[2], repr(argv))
        self.assertNotIn(values[3], repr(argv))
        payload = child.call_args.args[1]
        self.assertEqual(payload, context)
        self.assertEqual(result, safe)
        for private_value in values:
            self.assertNotIn(private_value, json.dumps(result))

    def test_postgres_snapshot_compares_complete_private_rows_only_after_api_observation(self):
        import datetime
        import io
        from types import ModuleType

        scope, _, _, _, values = self.snapshot_scope(api_rows=[], pg_result={})
        helper = scope["_actual_read_owned_postgres_inbox_snapshot"]
        sender, receiver, message, password = values
        created_at = "2026-10-24T12:00:00+00:00"
        persisted = message + "|" + sender
        api_row = {
            "id": 73,
            "sender_id": sender,
            "receiver_id": receiver,
            "message": persisted,
            "status": "pending",
            "created_at": created_at,
        }
        database_rows = []

        class SQLText(str):
            def format(self, *identifiers):
                return self

        class FakeCursor:
            query = ""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, query, *_args):
                self.query = str(query)
                return None

            def fetchone(self):
                return "main", "11111111-2222-4333-8444-555555555555"

            def fetchall(self):
                selects_full_rows = (
                    "i.id,i.sender_id" in self.query and "i.status,i.created_at" in self.query
                )
                if not database_rows:
                    if selects_full_rows:
                        return [("opencode-v1", None, None, None, None, None, None)]
                    return [("opencode-v1", None, None, None, None)]
                if not selects_full_rows:
                    return [("opencode-v1", *row[1:5]) for row in database_rows]
                return [("opencode-v1", *row) for row in database_rows]

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return FakeCursor()

        fake_psycopg = ModuleType("psycopg")
        fake_psycopg.connect = lambda **_parameters: FakeConnection()
        fake_psycopg.sql = SimpleNamespace(SQL=SQLText, Identifier=lambda value: value)

        def execute_private_child(argv, payload, _stop, *, max_output_bytes):
            captured = io.StringIO()
            old_stdin, old_stdout = sys.stdin, sys.stdout
            try:
                with patch.dict(sys.modules, {"psycopg": fake_psycopg}):
                    sys.stdin = io.StringIO(json.dumps(payload))
                    sys.stdout = captured
                    exec(compile(argv[4], "<private-pg-row-comparison>", "exec"), {})
            finally:
                sys.stdin, sys.stdout = old_stdin, old_stdout
            encoded = captured.getvalue().encode()
            if len(encoded) > max_output_bytes:
                raise AssertionError("child output bound exceeded")
            return encoded

        scope["_run_bounded_private_child"] = execute_private_child

        def compare(api_rows, *, api_rows_observed):
            context = {
                "connection_parameters": {
                    "host": "127.0.0.1",
                    "port": 5432,
                    "dbname": "owned_test",
                    "user": "runtime_test",
                    "password": password,
                    "sslmode": "disable",
                },
                "schema": "owned_test",
                "store_id": "11111111-2222-4333-8444-555555555555",
                "scope": "main",
                "sender_id": sender,
                "receiver_id": receiver,
                "expected_messages": [persisted],
                "api_rows": api_rows,
                "api_rows_observed": api_rows_observed,
            }
            return helper(context, time.monotonic() + 1)

        database_rows[:] = [
            (
                api_row["id"],
                api_row["sender_id"],
                api_row["receiver_id"],
                api_row["message"],
                api_row["status"],
                datetime.datetime.fromisoformat(created_at),
            )
        ]
        matched = compare([api_row], api_rows_observed=True)
        self.assertTrue(matched["api_pg_rows_match"], matched)
        self.assertTrue(matched["sender_receiver_match"])
        self.assertTrue(matched["expected_messages_match"])

        database_rows.clear()
        unavailable_both_empty = compare([], api_rows_observed=False)
        with self.subTest(case="unavailable_api_and_empty_pg"):
            self.assertFalse(unavailable_both_empty["api_pg_rows_match"])

        database_rows[:] = [
            (
                api_row["id"] + 1,
                api_row["sender_id"],
                api_row["receiver_id"],
                api_row["message"],
                api_row["status"],
                datetime.datetime.fromisoformat(created_at),
            )
        ]
        wrong_id = compare([api_row], api_rows_observed=True)
        with self.subTest(case="wrong_pg_id"):
            self.assertFalse(wrong_id["api_pg_rows_match"])

        database_rows[:] = [
            (
                api_row["id"],
                api_row["sender_id"],
                api_row["receiver_id"],
                api_row["message"],
                api_row["status"],
                datetime.datetime.fromisoformat("2026-10-24T12:00:01+00:00"),
            )
        ]
        wrong_created_at = compare([api_row], api_rows_observed=True)
        with self.subTest(case="wrong_pg_created_at"):
            self.assertFalse(wrong_created_at["api_pg_rows_match"])

        invalid_id_summary = scope["_failure_snapshot_rows_summary"]
        safe_summary = invalid_id_summary([api_row], [persisted], sender, receiver)
        self.assertTrue(safe_summary["sender_receiver_match"])
        self.assertTrue(safe_summary["expected_messages_match"])
        self.assertNotIn("73", json.dumps(safe_summary))
        self.assertNotIn(created_at, json.dumps(safe_summary))
        for invalid_id in (True, 0):
            with self.subTest(invalid_api_id=invalid_id):
                summary = invalid_id_summary(
                    [{**api_row, "id": invalid_id}],
                    [persisted],
                    sender,
                    receiver,
                )
                self.assertEqual(summary["valid_id_count"], 0)
                self.assertFalse(summary["sender_receiver_match"])
                self.assertFalse(summary["expected_messages_match"])
                self.assertEqual(summary["status"], "invalid_payload")

        for result in (matched, unavailable_both_empty, wrong_id, wrong_created_at):
            private_output = json.dumps(result)
            self.assertNotIn("73", private_output)
            self.assertNotIn(created_at, private_output)
            self.assertNotIn(password, private_output)

    def test_bounded_private_child_keeps_credentials_off_argv_and_reaps_timeout(self):
        import fcntl
        import math
        import os
        import selectors
        import subprocess

        namespace = {
            "json": json,
            "math": math,
            "os": os,
            "selectors": selectors,
            "subprocess": subprocess,
            "sys": sys,
            "time": time,
            "_INBOX_FAILURE_SNAPSHOT_MAX_BYTES": 4096,
            "_INBOX_FAILURE_SNAPSHOT_REAP_SECONDS": 1.0,
        }
        exec(
            compile(functions("_run_bounded_private_child"), str(SOURCE), "exec"),
            namespace,
        )
        secret = "private-child-password"
        real_popen = subprocess.Popen
        launched = []

        def track_process(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            launched.append(process)
            return process

        with patch.object(subprocess, "Popen", side_effect=track_process) as popen:
            output = namespace["_run_bounded_private_child"](
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "import json,sys; value=json.load(sys.stdin); sys.stdout.write(str(len(value['password'])))",
                ],
                {"password": secret},
                time.monotonic() + 2,
                max_output_bytes=16,
            )
        self.assertEqual(output, str(len(secret)).encode())
        argv = popen.call_args.args[0]
        self.assertNotIn(secret, repr(argv))
        self.assertNotIn(secret, repr(popen.call_args.kwargs["env"]))
        self.assertEqual(set(popen.call_args.kwargs["env"]), {"PATH", "LANG"})
        self.assertEqual(launched[0].returncode, 0)
        self.assertTrue(launched[0].stdin.closed)
        self.assertTrue(launched[0].stdout.closed)

        launched.clear()
        flood_code = "import os; block=b'x'*65536\nwhile True: os.write(1, block)"
        started = time.monotonic()
        with (
            patch.object(subprocess, "Popen", side_effect=track_process),
            self.assertRaisesRegex(ValueError, "failure_snapshot_output_limit"),
        ):
            namespace["_run_bounded_private_child"](
                [sys.executable, "-I", "-B", "-c", flood_code],
                {"password": secret},
                time.monotonic() + 2,
                max_output_bytes=4096,
            )
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(len(launched), 1)
        self.assertNotIn(secret, repr(launched[0].args))
        self.assertIsNotNone(launched[0].poll())
        self.assertTrue(launched[0].stdin.closed)
        self.assertTrue(launched[0].stdout.closed)

        launched.clear()
        with (
            patch.object(subprocess, "Popen", side_effect=track_process),
            self.assertRaisesRegex(RuntimeError, "failure_snapshot_child_unavailable"),
        ):
            namespace["_run_bounded_private_child"](
                [sys.executable, "-I", "-B", "-c", "import sys; sys.exit(7)"],
                {"password": secret},
                time.monotonic() + 2,
                max_output_bytes=4096,
            )
        self.assertEqual(len(launched), 1)
        self.assertNotIn(secret, repr(launched[0].args))
        self.assertIsNotNone(launched[0].poll())
        self.assertTrue(launched[0].stdin.closed)
        self.assertTrue(launched[0].stdout.closed)

        launched.clear()

        def launch_without_stdin_reader(*args, **kwargs):
            process = track_process(*args, **kwargs)
            try:
                fcntl.fcntl(process.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)
            except OSError:
                process.kill()
                process.wait(timeout=1)
                process.stdin.close()
                process.stdout.close()
                self.skipTest("cannot bound the child stdin pipe for this regression")
            return process

        started = time.monotonic()
        with (
            patch.object(subprocess, "Popen", side_effect=launch_without_stdin_reader),
            self.assertRaisesRegex(TimeoutError, "failure_snapshot_deadline"),
        ):
            namespace["_run_bounded_private_child"](
                [sys.executable, "-I", "-B", "-c", "import signal; signal.pause()"],
                {"payload": "x" * 60000, "password": secret},
                time.monotonic() + 0.2,
                max_output_bytes=4096,
            )
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertLessEqual(time.monotonic() - started, 10.0)
        self.assertEqual(len(launched), 1)
        self.assertNotIn(secret, repr(launched[0].args))
        self.assertIsNotNone(launched[0].poll())
        self.assertTrue(launched[0].stdin.closed)
        self.assertTrue(launched[0].stdout.closed)

    def test_postrestart_collector_rejects_recovery_or_changed_api_generation(self):
        for bad in (
            {"generation": "d" * 32},
            {"state": "pending"},
            {"attempts": 1},
            {"allowed_actions": ["verify"]},
            {"attempts": True},
        ):
            with (
                self.subTest(bad=bad),
                self.assertRaisesRegex(RuntimeError, "restart_durable_receipt_mismatch"),
            ):
                self.run_phase(index=1, turn_override=bad)

    def test_durable_pg_snapshot_rejects_missing_unverified_rows_and_late_arrival(self):
        import psycopg

        for mode in ("valid", "missing", "unverified", "wrong_terminal", "late"):
            with self.subTest(mode=mode):
                clock = Clock()
                rows = [
                    (role, "a" * 32, "b" * 64, "c" * 64, "result_verified")
                    for role in ("sender", "receiver")
                ]
                if mode == "missing":
                    rows.pop()
                elif mode == "unverified":
                    rows[0] = (*rows[0][:-1], "sent")
                elif mode == "wrong_terminal":
                    rows[0] = ("unowned", *rows[0][1:])
                cursor = Mock()

                def fetch():
                    if mode == "late":
                        clock.now = 3
                    return rows

                cursor.fetchall.side_effect = fetch
                connection = Mock()
                connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
                connection.cursor.return_value.__exit__ = Mock(return_value=False)
                scope = dict(
                    time=clock,
                    re=re,
                    acceptance=acceptance,
                    targets=SimpleNamespace(
                        main=SimpleNamespace(schema="owned", connection_parameters=lambda: {})
                    ),
                    terminals={role: {"id": role} for role in ("sender", "receiver")},
                )
                exec(compile(functions("_read_durable_turns"), str(SOURCE), "exec"), scope)
                with patch.object(psycopg, "connect") as connect:
                    connect.return_value.__enter__.return_value = connection
                    if mode == "valid":
                        result = scope["_read_durable_turns"](3)
                        self.assertEqual(set(result), {"sender", "receiver"})
                        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 1)
                        self.assertIn(
                            "default_transaction_read_only=on", connect.call_args.kwargs["options"]
                        )
                    else:
                        with self.assertRaisesRegex(
                            RuntimeError, "restart_durable_receipt_unavailable"
                        ):
                            scope["_read_durable_turns"](3)

    def test_both_roles_share_one_readiness_deadline_and_require_settled_streaks(self):
        for mode in ("never_ready", "late_read", "interrupted_streak"):
            with self.subTest(mode=mode):
                clock = Clock()
                n = {"receiver": 0, "sender": 0}

                class StateReader:
                    def read_state(self, terminal, stop):
                        n[terminal] += 1
                        if mode == "late_read":
                            clock.now = stop + 0.01
                        status = (
                            "unknown"
                            if mode == "never_ready"
                            or (mode == "interrupted_streak" and n[terminal] == 2)
                            else "completed"
                        )
                        return dict(
                            http_status=200, status=status, turn_sequence=0, turn_completed=0
                        )

                scope = dict(
                    time=clock, acceptance=acceptance, terminals={role: {"id": role} for role in n}
                )
                exec(compile(functions("_wait_native_ready"), str(SOURCE), "exec"), scope)
                phase = {}
                if mode == "interrupted_streak":
                    ready = scope["_wait_native_ready"](StateReader(), 75, phase)
                    self.assertEqual(set(ready), {"sender", "receiver"})
                    self.assertEqual(n, {"sender": 5, "receiver": 5})
                    self.assertEqual(phase["readiness_seconds"], 1.6)
                else:
                    with self.assertRaisesRegex(RuntimeError, "receiver_baseline_unavailable"):
                        scope["_wait_native_ready"](StateReader(), 75, phase)
                    self.assertGreaterEqual(clock.now, 75 if mode == "never_ready" else 1)
                    self.assertLess(clock.now, 76)

    def test_public_launcher_is_closed_before_manifest_read_or_resource_access(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SOURCE),
                "--manifest",
                "/nonexistent/never-read",
                "--expected-head",
                "a" * 40,
                "--go",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("execution_gate_closed", result.stderr)
        self.assertNotIn("FileNotFoundError", result.stderr)

    def test_optimized_python_cannot_disable_assertion_gates(self):
        result = subprocess.run(
            [
                sys.executable,
                "-O",
                str(SOURCE),
                "--manifest",
                "/nonexistent",
                "--expected-head",
                "a" * 40,
                "--go",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertIn("optimized_python_not_supported", result.stderr)
        self.assertNotIn("FileNotFoundError", result.stderr)

    def test_private_api_request_cannot_redirect_or_leave_loopback(self):
        request = Mock()
        namespace = dict(urlsplit=urlsplit, _session_request=request)
        exec(compile(functions("_private_api_request"), str(SOURCE), "exec"), namespace)
        namespace["_private_api_request"](
            "GET", "http://127.0.0.1:12345/owned", allow_redirects=True
        )
        self.assertIs(request.call_args.kwargs["allow_redirects"], False)
        request.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "private_api_route_invalid"):
            namespace["_private_api_request"]("GET", "https://remote.invalid/")
        request.assert_not_called()

    def test_agui_class_uses_private_requester_without_mutating_shared_module(self):
        with patch.object(sys, "path", [str(SOURCE.parents[3] / "test/live_provider"), *sys.path]):
            import message_api_restart_live as live
        original = live.requests
        response = SimpleNamespace(
            status_code=200, iter_lines=lambda **kw: iter([": ready", ""]), close=Mock()
        )
        private = SimpleNamespace(get=Mock(return_value=response))
        namespace = dict(inspect=inspect, SimpleNamespace=SimpleNamespace)
        exec(compile(functions("_private_agui_type"), str(SOURCE), "exec"), namespace)
        kind = namespace["_private_agui_type"](live, private)
        with patch.object(live.requests, "get") as global_get:
            capture = kind("http://127.0.0.1:12345", "sender", "receiver")
            capture._read()
            global_get.assert_not_called()
        private.get.assert_called_once()
        self.assertIs(live.requests, original)
        self.assertFalse(capture._thread.is_alive())
        response.close.assert_called_once()

    def test_inherited_acceptance_remains_required(self):
        tree = functions("_final_success")
        namespace = {}
        exec(compile(tree, str(SOURCE), "exec"), namespace)
        fn = tree.body[0]
        keys = next(
            node.value.elts
            for node in fn.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "required_true"
                for target in node.targets
            )
        )
        good = {key.value: True for key in keys}
        good.update(
            private_roots_after_session_delete=0,
            cleanup_session_delete_http_status=200,
            postgres_autostarted=False,
            postgres_cleanup_deferred=False,
            cleanup_failure_codes=[],
        )
        self.assertTrue(namespace["_final_success"](good))
        for key in keys:
            changed = {**good, key.value: False}
            self.assertFalse(namespace["_final_success"](changed))


if __name__ == "__main__":
    unittest.main()
