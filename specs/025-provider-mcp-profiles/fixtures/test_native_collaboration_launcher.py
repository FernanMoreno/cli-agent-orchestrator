"""Actual launcher wiring with synthetic files and no provider processes."""

import ast
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import frontmatter

FIXTURES = Path(__file__).parent
SOURCE = FIXTURES / "run_native_v1_human.py"
REPO = FIXTURES.resolve().parents[2]


class CollaborationLauncherTests(unittest.TestCase):
    def test_mixed_case_cannot_silently_change_the_restricted_human_inputs(self):
        tree = ast.parse(SOURCE.read_text())
        node = next(
            node
            for node in tree.body
            if isinstance(node, ast.If)
            and any(
                isinstance(child, ast.Import)
                and any(alias.name == "native_collaboration_contract" for alias in child.names)
                for child in node.body
            )
        )
        case = json.loads((FIXTURES / "native-collaboration-interaction-profile.json").read_text())
        code = compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec")
        exec(code, dict(mixed=True, repo=REPO, case=case, json=json))
        for field in ("model", "questions", "human_answers", "required_checks", "allowed_tools"):
            with self.subTest(field=field):
                changed = copy.deepcopy(case)
                changed[field] = "wrong"
                with self.assertRaisesRegex(RuntimeError, "native_case_contract_mismatch"):
                    exec(code, dict(mixed=True, repo=REPO, case=changed, json=json))

    def test_before_restart_message_snapshot_waits_for_the_verified_receiver_ack(self):
        tree = ast.parse(SOURCE.read_text())
        block = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "mixed"
            and any(
                isinstance(child, ast.Assign)
                and any(
                    isinstance(target, ast.Tuple)
                    and any(
                        isinstance(item, ast.Name) and item.id == "before_message_pg"
                        for item in target.elts
                    )
                    for target in child.targets
                )
                for child in node.body
            )
        )
        names = {"receiver_native_before", "receiver_model_argv_exact"}
        relevant = [
            child
            for child in block.body
            if not (
                isinstance(child, ast.Assign)
                and any(
                    isinstance(target, ast.Name)
                    and target.id in names
                    or isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value in names
                    for target in child.targets
                )
            )
        ]
        end = next(
            index
            for index, child in enumerate(relevant)
            if isinstance(child, ast.Assign)
            and any(
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "message_pair_before_restart_exact"
                for target in child.targets
            )
        )
        observed = []
        receipt = {}
        identity = ("a" * 32, "b" * 64)

        def ack(expected, counts, phase):
            self.assertIsNone(expected)
            observed.append("ack")
            receipt["receiver_before_restart_identity"] = identity
            return counts

        def rows():
            self.assertEqual(observed, ["ack"], "message snapshot preceded receiver ACK")
            observed.append("rows")
            return ["pg"], ["api"]

        scope = dict(
            receiver_ack_snapshot=ack,
            receipt=receipt,
            receiver_initial_counts=(0, 0),
            message_rows=rows,
        )
        exec(compile(ast.Module(body=relevant[:end], type_ignores=[]), str(SOURCE), "exec"), scope)
        self.assertEqual(observed, ["ack", "rows"])
        self.assertEqual(scope["before_receiver_turn"], identity)

    def test_actual_receiver_ack_observer_rejects_stale_extra_or_foreign_turns(self):
        function = next(
            node
            for node in ast.walk(ast.parse(SOURCE.read_text()))
            if isinstance(node, ast.FunctionDef) and node.name == "receiver_ack_snapshot"
        )
        identity = ("a" * 32, "b" * 64)
        for observed, required, generation, success in (
            ((1, 1), (1, 1), identity[0], True),
            ((0, 0), (0, 0), identity[0], True),
            ((0, 0), (1, 1), identity[0], False),
            ((2, 2), (1, 1), identity[0], False),
            ((1, 1), (0, 0), identity[0], False),
            ((1, 1), (1, 1), "c" * 32, False),
            ((True, 1), (1, 1), identity[0], False),
        ):
            with self.subTest(observed=observed, required=required, generation=generation):
                clock = iter(range(1, 100))
                context = MagicMock()
                context.__enter__.return_value.execute.return_value.fetchall.return_value = [
                    dict(
                        generation=generation,
                        receipt_sha256=identity[1],
                        result_sha256="d" * 64,
                        phase="result_verified",
                    )
                ]
                response = SimpleNamespace(
                    status_code=200,
                    json=lambda: dict(
                        status="completed", turn_sequence=observed[0], turn_completed=observed[1]
                    ),
                )
                scope = dict(
                    time=SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None),
                    deadline=10,
                    start=0,
                    http_json_bytes=lambda *a: json.dumps(
                        {"mode": "last", "output": "ACK"}
                    ).encode(),
                    server=SimpleNamespace(url="http://owned"),
                    receiver_id="abc12345",
                    json=json,
                    HTTPStatusError=RuntimeError,
                    requests=SimpleNamespace(get=lambda *a, **kw: response),
                    remaining_timeout=lambda _: 1,
                    receipt={},
                    connect=lambda *a, **kw: context,
                    targets=SimpleNamespace(main=object()),
                    re=__import__("re"),
                    check_deadline=lambda: None,
                )
                exec(
                    compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"),
                    scope,
                )
                if success:
                    self.assertEqual(
                        scope["receiver_ack_snapshot"](identity, required, "test"), required
                    )
                else:
                    with self.assertRaisesRegex(RuntimeError, "mixed_receiver_ack_failed"):
                        scope["receiver_ack_snapshot"](identity, required, "test")

    def test_mixed_public_and_optimized_gates_precede_all_manifest_resources(self):
        for flags, expected in (
            ([], "execution_gate_closed"),
            (["-O"], "optimized_python_not_supported"),
        ):
            result = subprocess.run(
                [
                    sys.executable,
                    *flags,
                    str(SOURCE),
                    "--case",
                    "collaboration",
                    "--manifest",
                    "/missing",
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

    def test_actual_profile_builder_keeps_restricted_filter_and_full_unfiltered_surface(self):
        tree = ast.parse(SOURCE.read_text())
        main = next(n for n in tree.body if isinstance(n, ast.Try))
        first = next(
            i
            for i, n in enumerate(main.body)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "mcp_definition" for t in n.targets)
        )
        write = next(
            i
            for i in range(first, len(main.body))
            if isinstance(main.body[i], ast.Expr)
            and isinstance(main.body[i].value, ast.Call)
            and isinstance(main.body[i].value.func, ast.Attribute)
            and isinstance(main.body[i].value.func.value, ast.Name)
            and main.body[i].value.func.value.id == "source"
            and main.body[i].value.func.attr == "write_text"
        )
        for mixed, name in (
            (False, "native-human-interaction-profile.json"),
            (True, "native-collaboration-interaction-profile.json"),
        ):
            with self.subTest(mixed=mixed), tempfile.TemporaryDirectory() as temp:
                case = json.loads((FIXTURES / name).read_text())
                path = Path(temp) / "agent.md"
                scope = dict(
                    mixed=mixed,
                    case=case,
                    childenv={"PYTHONPATH": "private"},
                    sys=sys,
                    source=path,
                    profile=case["profile_name"],
                    frontmatter=frontmatter,
                )
                exec(
                    compile(
                        ast.Module(body=main.body[first : write + 1], type_ignores=[]),
                        str(SOURCE),
                        "exec",
                    ),
                    scope,
                )
                profile = frontmatter.loads(path.read_text())
                self.assertEqual(profile["allowedTools"], ["@cao-mcp-server"])
                server = profile["mcpServers"]["cao-mcp-server"]
                self.assertEqual(
                    server["cao_tool_profile"], "collaboration" if mixed else "interaction"
                )
                self.assertEqual(server["env"], scope["childenv"])
                if mixed:
                    self.assertNotIn("enabled_tools", server)
                    self.assertEqual(len(case["tools"]), 8)
                else:
                    self.assertEqual(server["enabled_tools"], case["tools"])
                    self.assertEqual(len(case["tools"]), 3)

    def test_actual_observer_copy_extends_only_private_observation_vocabulary(self):
        tree = ast.parse(SOURCE.read_text())
        main = next(n for n in tree.body if isinstance(n, ast.Try))
        first = next(
            i
            for i, n in enumerate(main.body)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "observer_directory" for t in n.targets)
        )
        case = json.loads((FIXTURES / "native-collaboration-interaction-profile.json").read_text())
        with tempfile.TemporaryDirectory() as temp:
            scope = dict(root=Path(temp), repo=REPO, case=case, mixed=True, importlib=importlib)
            exec(
                compile(
                    ast.Module(body=main.body[first : first + 2], type_ignores=[]),
                    str(SOURCE),
                    "exec",
                ),
                scope,
            )
            observed = scope["observer_module"]
            self.assertEqual(observed.INTERACTION_TOOLS, frozenset(case["tools"]))
            original = ast.parse((REPO / "test/live_provider/opencode_mcp_observer.py").read_text())
            copied = ast.parse(
                (scope["observer_directory"] / "opencode_mcp_observer.py").read_text()
            )
            self.assertEqual(
                ast.dump(ast.Module(body=copied.body[:-1], type_ignores=[])), ast.dump(original)
            )
            self.assertTrue(all(n in case["tools"] for n in ("send_message", "cao_ask_user")))

    def test_runner_never_posts_an_inbox_message_to_fake_the_native_tool_call(self):
        posts = [
            n
            for n in ast.walk(ast.parse(SOURCE.read_text()))
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "requests"
            and n.func.attr == "post"
        ]
        self.assertGreaterEqual(len(posts), 4)
        for call in posts:
            self.assertNotIn("inbox/messages", ast.unparse(call))


class DeferredReceiverReadinessTests(unittest.TestCase):
    @staticmethod
    def _function(name):
        tree = ast.parse(SOURCE.read_text())
        return next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
        )

    @staticmethod
    def _clock(initial=0.0):
        class Clock:
            now = initial

            @classmethod
            def monotonic(cls):
                return cls.now

            @classmethod
            def sleep(cls, seconds):
                cls.now += seconds

        return Clock

    @classmethod
    def _waiter(cls):
        node = cls._function("_wait_for_deferred_receiver_ready")
        namespace = {"RuntimeError": RuntimeError}
        bounded_count = cls._function("_bounded_count")
        exec(
            compile(ast.Module(body=[bounded_count, node], type_ignores=[]), str(SOURCE), "exec"),
            namespace,
        )
        return namespace[node.name]

    @classmethod
    def _provider_reader(cls):
        node = cls._function("_read_deferred_receiver_variant")
        namespace = {"ValueError": ValueError, "re": __import__("re")}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
        return namespace[node.name]

    @staticmethod
    def _response(status_code, body):
        return SimpleNamespace(status_code=status_code, json=lambda: body)

    def test_pending_to_ready_requires_three_settled_zero_turn_observations(self):
        clock = self._clock(10.0)
        states = iter(
            (
                {"status": "unknown", "turn_sequence": 0, "turn_completed": 0},
                {"status": "idle", "turn_sequence": 0, "turn_completed": 0},
                {"status": "idle", "turn_sequence": 0, "turn_completed": 0},
                {"status": "completed", "turn_sequence": 0, "turn_completed": 0},
                {"status": "idle", "turn_sequence": 0, "turn_completed": 0},
            )
        )
        variants = iter((False, False, True, True, True))
        timeouts = []
        receipt = {}

        def get_terminal(timeout):
            timeouts.append(timeout)
            return self._response(200, next(states))

        result = self._waiter()(
            get_terminal,
            lambda _stop: next(variants),
            receipt=receipt,
            journey_start=10.0,
            outer_deadline=20.0,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

        self.assertEqual(result["status"], "idle")
        self.assertEqual(len(timeouts), 5)
        self.assertTrue(all(0 < timeout <= 2 for timeout in timeouts))
        self.assertEqual(
            [row["status"] for row in receipt["receiver_startup_observations"]],
            ["unknown", "idle", "idle", "completed", "idle"],
        )
        self.assertTrue(
            all(
                row["turn_sequence"] == row["turn_completed"] == 0
                and row["deferred_failure_present"] is False
                for row in receipt["receiver_startup_observations"]
            )
        )
        self.assertEqual(
            [row["provider_variant_ready"] for row in receipt["receiver_startup_observations"]],
            [False, False, True, True, True],
        )
        self.assertEqual(receipt["receiver_initialization_settled_samples"], 3)
        self.assertIs(receipt["receiver_initialization_complete"], True)
        self.assertIs(receipt["receiver_initialization_complete_exact"], True)

    def test_structured_deferred_failure_is_reported_without_detail(self):
        clock = self._clock()
        receipt = {}
        failure = {"error_type": "PrivateFailure", "detail": "sensitive detail"}

        with self.assertRaisesRegex(RuntimeError, "receiver_deferred_initialization_failed"):
            self._waiter()(
                lambda **_: self._response(
                    200,
                    {
                        "status": "error",
                        "turn_sequence": 0,
                        "turn_completed": 0,
                        "deferred_init_failure": failure,
                    },
                ),
                lambda _stop: self.fail("variant query ran after deferred failure"),
                receipt=receipt,
                journey_start=0.0,
                outer_deadline=2.0,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )

        self.assertIs(receipt["receiver_deferred_failure_present"], True)
        self.assertIs(receipt["receiver_initialization_complete"], False)
        self.assertIs(receipt["receiver_startup_observations"][0]["deferred_failure_present"], True)
        self.assertNotIn("sensitive detail", repr(receipt))
        self.assertNotIn("PrivateFailure", repr(receipt))

    def test_readiness_deadline_fails_closed_with_bounded_poll_evidence(self):
        clock = self._clock(5.0)
        receipt = {}
        requests = []

        def get_terminal(timeout):
            requests.append(timeout)
            return self._response(
                200, {"status": "unknown", "turn_sequence": 0, "turn_completed": 0}
            )

        with self.assertRaisesRegex(RuntimeError, "receiver_initialization_deadline"):
            self._waiter()(
                get_terminal,
                lambda _stop: False,
                receipt=receipt,
                journey_start=5.0,
                outer_deadline=6.0,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )

        self.assertEqual(len(requests), len(receipt["receiver_startup_observations"]))
        self.assertEqual(len(requests), 4)
        self.assertTrue(all(0 < timeout <= 1 for timeout in requests))
        self.assertTrue(
            all(row["status"] == "unknown" for row in receipt["receiver_startup_observations"])
        )
        self.assertLessEqual(
            max(row["elapsed_seconds"] for row in receipt["receiver_startup_observations"]), 1.0
        )
        self.assertIs(receipt["receiver_initialization_complete"], False)
        self.assertIs(receipt["receiver_initialization_deadline_reached"], True)

    def test_readiness_rejects_non_boolean_provider_witness(self):
        clock = self._clock()
        receipt = {}
        with self.assertRaisesRegex(RuntimeError, "receiver_readiness_database_failed"):
            self._waiter()(
                lambda **_: self._response(
                    200, {"status": "idle", "turn_sequence": 0, "turn_completed": 0}
                ),
                lambda _stop: 1,
                receipt=receipt,
                journey_start=0.0,
                outer_deadline=2.0,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
        self.assertIs(receipt["receiver_startup_observations"][0]["provider_variant_ready"], False)

    def test_provider_variant_child_uses_private_config_and_returns_boolean_only(self):
        clock = self._clock(4.0)
        seen = []

        def capture(argv, stop, max_bytes):
            seen.append((argv, stop, max_bytes))
            self.assertEqual(argv[0], "/owned/venv/bin/python")
            self.assertEqual(argv[1:3], ["-I", "-c"])
            self.assertEqual(
                argv[4:], ["/owned/repo/src", "/private/database-config.json", "d4e5f6a7"]
            )
            self.assertNotIn("password-sentinel", repr(argv))
            self.assertEqual(max_bytes, 64)
            child = argv[3]
            self.assertIn('os.environ["CAO_DATABASE_CONFIG_FILE"] = sys.argv[2]', child)
            self.assertIn('DatabaseTarget.from_settings("main")', child)
            self.assertIn("connect(target, readonly=True)", child)
            self.assertIn("SELECT provider_variant FROM terminals WHERE id=%s", child)
            self.assertIn('sys.stdout.write(\'{"ready":\' + ("true" if ready else "false")', child)
            compile(child, "receiver-provider-read.py", "exec")
            return b'{"ready":true}\n'

        result = self._provider_reader()(
            capture,
            python="/owned/venv/bin/python",
            source_root="/owned/repo/src",
            config_file="/private/database-config.json",
            receiver_id="d4e5f6a7",
            stop=8.0,
            monotonic=clock.monotonic,
        )
        self.assertIs(result, True)
        self.assertEqual(seen[0][1:], (8.0, 64))

    def test_provider_variant_child_cannot_return_a_late_result_as_ready(self):
        clock = self._clock(4.0)

        def late_capture(_argv, stop, _max_bytes):
            clock.now = stop + 0.001
            return b'{"ready":true}\n'

        with self.assertRaisesRegex(ValueError, "receiver_readiness_database_deadline"):
            self._provider_reader()(
                late_capture,
                python="/owned/venv/bin/python",
                source_root="/owned/repo/src",
                config_file="/private/database-config.json",
                receiver_id="d4e5f6a7",
                stop=7.0,
                monotonic=clock.monotonic,
            )

    def test_provider_variant_child_rejects_extra_stdout_and_hides_capture_error(self):
        reader = self._provider_reader()
        kwargs = {
            "python": "/owned/venv/bin/python",
            "source_root": "/owned/repo/src",
            "config_file": "/private/database-config.json",
            "receiver_id": "d4e5f6a7",
            "stop": 7.0,
            "monotonic": self._clock().monotonic,
        }
        with self.assertRaisesRegex(ValueError, "receiver_readiness_database_response"):
            reader(lambda *_: b'{"ready":true} extra', **kwargs)

        def failed_capture(*_args):
            raise TimeoutError("private database failure detail")

        with self.assertRaisesRegex(ValueError, "receiver_readiness_database_failed") as raised:
            reader(failed_capture, **kwargs)
        self.assertNotIn("private database failure detail", str(raised.exception))

    def test_readiness_request_timeout_at_outer_deadline_is_classified_without_detail(self):
        clock = self._clock(5.0)
        receipt = {}

        def get_terminal(timeout):
            self.assertGreater(timeout, 0)
            clock.now = 6.0
            raise TimeoutError("private request detail")

        with self.assertRaisesRegex(RuntimeError, "receiver_initialization_deadline"):
            self._waiter()(
                get_terminal,
                lambda _stop: None,
                receipt=receipt,
                journey_start=5.0,
                outer_deadline=6.0,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )

        self.assertIs(receipt["receiver_initialization_deadline_reached"], True)
        self.assertEqual(receipt["receiver_startup_poll_count"], 1)
        self.assertEqual(receipt["receiver_startup_observations"][0]["status"], "request_error")
        self.assertNotIn("private request detail", repr(receipt))

    def test_receiver_create_timeout_records_elapsed_and_no_http_status_without_detail(self):
        tree = ast.parse(SOURCE.read_text())
        block = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "mixed"
            and any(
                isinstance(child, ast.Expr)
                and isinstance(child.value, ast.Call)
                and isinstance(child.value.func, ast.Name)
                and child.value.func.id == "admit"
                and child.value.args
                and isinstance(child.value.args[0], ast.Constant)
                and child.value.args[0].value == "receiver_terminal"
                for child in node.body
            )
        )
        first = next(
            i
            for i, child in enumerate(block.body)
            if isinstance(child, ast.Expr)
            and isinstance(child.value, ast.Call)
            and isinstance(child.value.func, ast.Name)
            and child.value.func.id == "admit"
            and child.value.args
            and isinstance(child.value.args[0], ast.Constant)
            and child.value.args[0].value == "receiver_terminal"
        )
        stop = next(
            i
            for i, child in enumerate(block.body[first:], first)
            if isinstance(child, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "receiver_created_status"
                for target in child.targets
            )
        )

        class RequestTimeout(Exception):
            pass

        receipt = {}
        clock = self._clock(25.0)

        def post(_url, *, params, timeout):
            self.assertEqual(params.get("defer_init"), "true")
            self.assertGreater(timeout, 0)
            raise RequestTimeout("private detail must not enter receipt")

        scope = {
            "mixed": True,
            "receipt": receipt,
            "server": SimpleNamespace(url="http://owned"),
            "requests": SimpleNamespace(post=post, RequestException=RequestTimeout),
            "time": clock,
            "start": 25.0,
            "home": Path("."),
            "model": "model-fixture",
            "session_name": "session-fixture",
            "admit": lambda _phase: None,
        }
        with self.assertRaises(RequestTimeout):
            exec(
                compile(
                    ast.Module(body=block.body[first : stop + 1], type_ignores=[]),
                    str(SOURCE),
                    "exec",
                ),
                scope,
            )

        self.assertIs(receipt["receiver_create_transport_error"], True)
        self.assertIsNone(receipt["receiver_create_http_status"])
        self.assertGreaterEqual(receipt["receiver_create_elapsed_seconds"], 0)
        self.assertNotIn("private detail", repr(receipt))

    def test_expired_outer_deadline_does_not_attempt_receiver_post(self):
        tree = ast.parse(SOURCE.read_text())
        block = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "mixed"
            and any(
                isinstance(child, ast.Expr)
                and isinstance(child.value, ast.Call)
                and isinstance(child.value.func, ast.Name)
                and child.value.func.id == "admit"
                and child.value.args
                and isinstance(child.value.args[0], ast.Constant)
                and child.value.args[0].value == "receiver_terminal"
                for child in node.body
            )
        )
        first = next(
            i
            for i, child in enumerate(block.body)
            if isinstance(child, ast.Expr)
            and isinstance(child.value, ast.Call)
            and isinstance(child.value.func, ast.Name)
            and child.value.func.id == "admit"
            and child.value.args
            and isinstance(child.value.args[0], ast.Constant)
            and child.value.args[0].value == "receiver_terminal"
        )
        stop = next(
            i
            for i, child in enumerate(block.body[first:], first)
            if isinstance(child, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "receiver_created_status"
                for target in child.targets
            )
        )
        posted = []
        receipt = {}
        scope = {
            "mixed": True,
            "receipt": receipt,
            "admit": lambda _phase: None,
            "start": 25.0,
            "time": SimpleNamespace(monotonic=lambda: 205.0),
            "requests": SimpleNamespace(
                post=lambda *_args, **_kwargs: posted.append(True), RequestException=Exception
            ),
            "server": SimpleNamespace(url="http://owned"),
            "session_name": "session-fixture",
            "home": Path("."),
            "model": "model-fixture",
        }

        with self.assertRaisesRegex(RuntimeError, "human_journey_deadline"):
            exec(
                compile(
                    ast.Module(body=block.body[first : stop + 1], type_ignores=[]),
                    str(SOURCE),
                    "exec",
                ),
                scope,
            )

        self.assertEqual(posted, [])
        self.assertIs(receipt["receiver_initialization_deadline_reached"], True)
        self.assertNotIn("receiver_create_http_status", receipt)
        self.assertNotIn("receiver_create_transport_error", receipt)
        self.assertNotIn("receiver_create_elapsed_seconds", receipt)

    def test_mixed_receiver_registers_owned_identity_then_polls_before_reading_config(self):
        tree = ast.parse(SOURCE.read_text())
        block = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "mixed"
            and any(
                isinstance(child, ast.Expr)
                and isinstance(child.value, ast.Call)
                and isinstance(child.value.func, ast.Name)
                and child.value.func.id == "admit"
                and child.value.args
                and isinstance(child.value.args[0], ast.Constant)
                and child.value.args[0].value == "receiver_terminal"
                for child in node.body
            )
        )
        first = next(
            i
            for i, child in enumerate(block.body)
            if isinstance(child, ast.Expr)
            and isinstance(child.value, ast.Call)
            and isinstance(child.value.func, ast.Name)
            and child.value.func.id == "admit"
            and child.value.args
            and isinstance(child.value.args[0], ast.Constant)
            and child.value.args[0].value == "receiver_terminal"
        )
        last = next(
            i
            for i, child in enumerate(block.body[first:], first)
            if isinstance(child, ast.If)
            and isinstance(child.test, ast.Compare)
            and isinstance(child.test.left, ast.Name)
            and child.test.left.id == "receiver_initial_counts"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_root = root / "opencode-v1"
            sender_root = config_root / "terminal-sender"
            receiver_root = config_root / "terminal-receiver"
            sender_root.mkdir(parents=True)
            private = sender_root
            config_path = receiver_root / "opencode.json"
            clock = self._clock(50.0)
            receipt = {
                "profile_configuration_exact": True,
                "native_permissions_exact": True,
                "runtime_capability_exact": True,
            }
            terminals = {}
            owned = set()
            observed = []
            receiver = {
                "id": "d4e5f6a7",
                "session_name": "session-fixture",
                "name": "v1-receiver",
                "status": "idle",
                "turn_sequence": 0,
                "turn_completed": 0,
                "allowed_tools": [],
            }

            class CreateResponse:
                status_code = 201

                @staticmethod
                def json():
                    return receiver

            def post(url, *, params, timeout):
                self.assertTrue(url.endswith("/sessions/session-fixture/terminals"))
                self.assertEqual(params.get("defer_init"), "true")
                self.assertGreater(timeout, 0)
                observed.append("post_201")
                return CreateResponse()

            poll_states = (
                {"status": "unknown", "turn_sequence": 0, "turn_completed": 0},
                {"status": "idle", "turn_sequence": 0, "turn_completed": 0},
                {"status": "idle", "turn_sequence": 0, "turn_completed": 0},
                {"status": "completed", "turn_sequence": 0, "turn_completed": 0},
                {"status": "idle", "turn_sequence": 0, "turn_completed": 0},
            )
            provider_variants = iter((False, False, True, True, True))
            poll_index = 0

            def http_reader(api, path, stop, wire_limit):
                nonlocal poll_index
                self.assertIn("receiver", terminals, "returned receiver was not cleanup-owned")
                self.assertIn(
                    receiver["id"], owned, "tmux ownership was not recorded before polling"
                )
                self.assertEqual(api, "http://owned")
                self.assertEqual(path, "/terminals/" + receiver["id"])
                self.assertGreater(stop, clock.monotonic())
                self.assertLessEqual(stop, 50.0 + 180)
                self.assertLessEqual(stop, clock.monotonic() + 2)
                self.assertLessEqual(wire_limit, 65536)
                poll_index += 1
                observed.append("api_poll")
                if poll_index < len(poll_states):
                    self.assertFalse(
                        config_path.exists(), "config was read before deferred init settled"
                    )
                else:
                    receiver_root.mkdir()
                    config_path.write_text(
                        json.dumps(
                            {
                                "model": "model-fixture",
                                "agent": {"v1-receiver": {"prompt": "receiver-profile-prompt"}},
                                "permission": {"*": "deny"},
                            }
                        )
                    )
                    (receiver_root / ".cao-ownership.json").write_text("owned")
                return json.dumps(poll_states[poll_index - 1]).encode()

            def snapshot(*_args, terminal_id):
                self.assertIn("receiver", terminals)
                observed.append("snapshot_owned")
                return {terminal_id}

            database_config_file = root / "database-config.json"
            repo_root = root / "source"
            repo_root.mkdir()
            child_calls = []

            def capture_child(argv, stop, max_bytes):
                child_calls.append((argv, stop, max_bytes))
                self.assertEqual(argv[0], "/owned/venv/bin/python")
                self.assertEqual(argv[1:3], ["-I", "-c"])
                self.assertEqual(
                    argv[4:],
                    [str(repo_root / "src"), str(database_config_file), receiver["id"]],
                )
                self.assertNotIn("password-sentinel", repr(argv))
                self.assertLessEqual(stop, 50.0 + 180)
                self.assertEqual(stop, min(50.0 + 180, clock.monotonic() + 3))
                self.assertEqual(max_bytes, 64)
                observed.append("variant_probe")
                return b'{"ready":true}\n' if next(provider_variants) else b'{"ready":false}\n'

            helper = self._function("_wait_for_deferred_receiver_ready")
            bounded_count = self._function("_bounded_count")
            reader = self._function("_read_deferred_receiver_variant")
            namespace = {
                "RuntimeError": RuntimeError,
                "ValueError": ValueError,
                "re": __import__("re"),
            }
            exec(
                compile(
                    ast.Module(body=[bounded_count, reader, helper], type_ignores=[]),
                    str(SOURCE),
                    "exec",
                ),
                namespace,
            )
            scope = {
                "mixed": True,
                "json": json,
                "receipt": receipt,
                "profile": "v1-sender",
                "terminal_id": "sender-id-9876",
                "session_name": "session-fixture",
                "server": SimpleNamespace(url="http://owned"),
                "requests": SimpleNamespace(post=post, RequestException=Exception),
                "http_json_bytes": http_reader,
                "HTTPStatusError": type("HTTPStatusError", (Exception,), {}),
                "SimpleNamespace": SimpleNamespace,
                "capture_bytes": capture_child,
                "sys": SimpleNamespace(executable="/owned/venv/bin/python"),
                "time": clock,
                "start": 50.0,
                "home": root,
                "model": "model-fixture",
                "repo": repo_root,
                "receiver_case": {"prompt": "receiver-profile-prompt"},
                "terminals": terminals,
                "owned": owned,
                "journey": SimpleNamespace(_snapshot_and_recheck_private_tmux_server=snapshot),
                "sid": object(),
                "sockid": object(),
                "socket_path": "private-socket",
                "caohome": root,
                "private": private,
                "targets": SimpleNamespace(config_file=database_config_file),
                "admit": lambda phase: observed.append("admit:" + phase),
                "_created_terminal_target": lambda _value: (
                    receiver["id"],
                    receiver["session_name"],
                    "pane-fixture",
                ),
                "_sha256": lambda _path: "a" * 64,
                "_wait_for_deferred_receiver_ready": namespace["_wait_for_deferred_receiver_ready"],
                "_read_deferred_receiver_variant": namespace["_read_deferred_receiver_variant"],
            }
            exec(
                compile(
                    ast.Module(body=block.body[first : last + 1], type_ignores=[]),
                    str(SOURCE),
                    "exec",
                ),
                scope,
            )

            self.assertEqual(poll_index, 5, "status alone was treated as initialization readiness")
            self.assertEqual(len(child_calls), 5)
            self.assertEqual(owned, {receiver["id"]})
            self.assertIs(terminals["receiver"], receiver)
            self.assertEqual(scope["receiver_initial_counts"], (0, 0))
            self.assertIs(receipt["receiver_profile_configuration_exact"], True)
            self.assertIs(receipt["receiver_initialization_complete"], True)
            self.assertIs(receipt["receiver_initialization_complete_exact"], True)
            self.assertEqual(receipt["receiver_startup_poll_count"], 5)
            self.assertEqual(
                [row["provider_variant_ready"] for row in receipt["receiver_startup_observations"]],
                [False, False, True, True, True],
            )
            self.assertEqual(
                observed[:3], ["admit:receiver_terminal", "post_201", "snapshot_owned"]
            )
            self.assertEqual(receipt["receiver_create_http_status"], 201)
            self.assertGreaterEqual(receipt["receiver_create_elapsed_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
