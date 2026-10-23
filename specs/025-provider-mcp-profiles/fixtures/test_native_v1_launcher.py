"""Execute the actual launcher phase collector without CLI/model/API/PostgreSQL."""

import ast
import inspect
import json
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
    def run_phase(self, receiver=True):
        clock = Clock()
        readers = Readers(clock, receiver)

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
        )
        exec(
            compile(
                functions("_accept_last_receipt", "_collect_message_phase"), str(SOURCE), "exec"
            ),
            scope,
        )
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
            scope["_collect_message_phase"](0, "unchanged Unicode message", phase, readers)
        return phase, readers, clock, [call.args[2] for call in probe.call_args_list]

    def test_actual_collector_observes_receiver_despite_failed_sender_unicode(self):
        phase, readers, clock, calls = self.run_phase()
        self.assertFalse(phase["last_receipt_unicode_verified"])
        self.assertTrue(phase["sender_completed"])
        self.assertTrue(phase["receiver_ack_exact"])
        self.assertEqual(phase["receiver_native_text_diagnostic"]["status"], "observed")
        self.assertEqual(calls, ["sender", "receiver"])
        self.assertTrue(readers.closed)
        self.assertLess(clock.now, 75)

    def test_actual_collector_keeps_original_deadline_for_untriggered_receiver(self):
        phase, readers, clock, calls = self.run_phase(False)
        self.assertEqual(clock.now, 75)
        self.assertEqual(calls, ["sender"])
        self.assertTrue(readers.closed)
        self.assertEqual(
            phase["receiver_native_text_diagnostic"]["reason"], "not_triggered_by_deadline"
        )
        self.assertFalse(acceptance.receiver_phases_exact([phase, phase]))

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
