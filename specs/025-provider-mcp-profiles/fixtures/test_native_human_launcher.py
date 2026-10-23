"""Execute execution/deadline gates without starting native resources."""

import ast
import json
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SOURCE = Path(__file__).with_name("run_native_v1_human.py")


class HumanLauncherTests(unittest.TestCase):
    def test_actual_final_poll_retries_pending_http_but_rejects_errors(self):
        import native_phase_readers as readers

        tree = ast.parse(SOURCE.read_text())
        main = next(node for node in tree.body if isinstance(node, ast.Try))
        loop = next(
            node
            for node in main.body
            if isinstance(node, ast.While)
            and any(
                isinstance(child, ast.Name) and child.id == "http_json_bytes"
                for child in ast.walk(node)
            )
        )
        for statuses in ([202, 200], [409], [500], [202], []):
            with self.subTest(statuses=statuses):
                calls = []

                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass

                    def do_GET(self):
                        status = statuses[min(len(calls), len(statuses) - 1)]
                        calls.append(status)
                        body = json.dumps({"output": "Validado ñ, 中文.", "mode": "last"}).encode()
                        self.send_response(status)
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(
                    target=server.serve_forever, kwargs={"poll_interval": 0.01}
                )
                thread.start()
                scope = dict(
                    time=time,
                    deadline=time.monotonic() + (0.15 if statuses == [202] else 3),
                    start=time.monotonic() - (180 if statuses == [] else 0),
                    http_json_bytes=readers.http_json_bytes,
                    HTTPStatusError=readers.HTTPStatusError,
                    server=SimpleNamespace(url="http://127.0.0.1:" + str(server.server_port)),
                    terminal_id="abcd1234",
                    json=json,
                    requests=SimpleNamespace(
                        get=lambda *a, **kw: SimpleNamespace(json=lambda: {"status": "completed"})
                    ),
                    remaining_timeout=lambda limit: limit,
                    receipt={},
                )
                try:
                    code = compile(ast.Module(body=[loop], type_ignores=[]), str(SOURCE), "exec")
                    if statuses == []:
                        with self.assertRaisesRegex(RuntimeError, "human_final_output_missing"):
                            exec(code, scope)
                        self.assertEqual(calls, [])
                    elif statuses == [202]:
                        with self.assertRaisesRegex(RuntimeError, "human_final_output_missing"):
                            exec(code, scope)
                        self.assertTrue(calls and set(calls) == {202})
                        self.assertLessEqual(len(calls), 3)
                    elif statuses[0] == 202:
                        exec(code, scope)
                        self.assertEqual(calls, [202, 200])
                        self.assertEqual(scope["value"]["mode"], "last")
                        self.assertEqual(scope["receipt"]["last_output_http_status"], 200)
                        self.assertEqual(scope["receipt"]["last_output_pending_observations"], 1)
                    else:
                        with self.assertRaises(ValueError) as caught:
                            exec(code, scope)
                        self.assertEqual(
                            getattr(caught.exception, "status_code", None), statuses[0]
                        )
                        self.assertEqual(calls, statuses)
                        self.assertEqual(str(caught.exception), "http_status")
                        self.assertEqual(scope["receipt"]["last_output_http_status"], statuses[0])
                        self.assertNotIn("last_output_pending_observations", scope["receipt"])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=1)

    def test_actual_create_binding_uses_returned_normalized_backend_session(self):
        tree = ast.parse(SOURCE.read_text())
        main = next(node for node in tree.body if isinstance(node, ast.Try))
        first = next(
            index
            for index, node in enumerate(main.body)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and isinstance(node.value.func.value, ast.Name)
            and node.value.func.value.id == "created"
            and node.value.func.attr == "json"
        )
        last = next(
            index
            for index in range(first, len(main.body))
            if isinstance(main.body[index], ast.Assign)
            and any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "terminals"
                for target in main.body[index].targets
            )
        )
        viewport = next(
            node
            for node in main.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "viewport" for target in node.targets
            )
        )
        helpers = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_created_terminal_target"
        ]
        returned = dict(id="abc12345", session_name="cao-t102-owned", name="native-human")

        def history(session, pane, **kwargs):
            if (session, pane) != (returned["session_name"], returned["name"]):
                raise ValueError("Session target not found")
            return "synthetic viewport"

        scope = dict(
            created=SimpleNamespace(json=lambda: returned),
            terminals={},
            session_name="t102-owned",
            tmux=SimpleNamespace(get_history=history),
        )
        exec(
            compile(
                ast.Module(
                    body=[*helpers, *main.body[first : last + 1], viewport], type_ignores=[]
                ),
                str(SOURCE),
                "exec",
            ),
            scope,
        )
        self.assertEqual(scope["session_name"], returned["session_name"])
        self.assertEqual(scope["viewport"], "synthetic viewport")

    def test_public_gate_and_optimized_python_fail_before_manifest_or_resources(self):
        for options, expected in (
            ([], "execution_gate_closed"),
            (["-O"], "optimized_python_not_supported"),
        ):
            with self.subTest(options=options):
                result = subprocess.run(
                    [
                        sys.executable,
                        *options,
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
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("FileNotFoundError", result.stderr)

    def test_actual_input_callback_publishes_once_before_single_post(self):
        functions = [
            node
            for node in ast.walk(ast.parse(SOURCE.read_text()))
            if isinstance(node, ast.FunctionDef)
            and node.name in {"mark_native_phase", "send_native_input"}
        ]
        events = []

        def publish(**kwargs):
            self.assertIsNone(kwargs["existing_deadline"])
            events.append("deadline")
            return 85

        post = Mock(side_effect=lambda *args, **kw: events.append("input"))
        scope = dict(
            deadline=None,
            model_start=None,
            barrier=Path("/unused/private"),
            receipt={},
            journey=SimpleNamespace(_publish_agui_native_deadline_before_send=publish),
            requests=SimpleNamespace(post=post),
            server=SimpleNamespace(url="http://owned"),
            terminal_id="abc12345",
            prompt="private",
            time=SimpleNamespace(monotonic=lambda: 10),
            start=0,
        )
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"), scope)
        scope["send_native_input"]()
        self.assertEqual(events, ["deadline", "input"])
        self.assertEqual(scope["deadline"], 85)
        self.assertEqual(scope["model_start"], 10)
        with self.assertRaises(AssertionError):
            scope["send_native_input"]()
        self.assertEqual(post.call_count, 1)

    def test_missing_deadline_publication_never_sends_input(self):
        functions = [
            node
            for node in ast.walk(ast.parse(SOURCE.read_text()))
            if isinstance(node, ast.FunctionDef)
            and node.name in {"mark_native_phase", "send_native_input"}
        ]
        publish = Mock(side_effect=RuntimeError("barrier missing"))
        post = Mock()
        scope = dict(
            deadline=None,
            model_start=None,
            barrier=None,
            receipt={},
            journey=SimpleNamespace(_publish_agui_native_deadline_before_send=publish),
            requests=SimpleNamespace(post=post),
        )
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"), scope)
        with self.assertRaisesRegex(RuntimeError, "barrier missing"):
            scope["send_native_input"]()
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
