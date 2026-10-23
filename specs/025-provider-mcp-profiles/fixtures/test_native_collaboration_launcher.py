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


if __name__ == "__main__":
    unittest.main()
