"""Actual launcher wiring with synthetic files and no provider processes."""

import ast
import copy
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import frontmatter

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.providers.mcp_interactions import CAO_INTERACTION_CAPABILITY_FILE
from cli_agent_orchestrator.services import terminal_service
from cli_agent_orchestrator.utils.opencode_v1 import validate_v1_mcp_configuration

FIXTURES = Path(__file__).parent
SOURCE = FIXTURES / "run_native_v1_human.py"
REPO = FIXTURES.resolve().parents[2]
SENDER_ID = "a1b2c3d4"
RECEIVER_ID = "e5f60718"


class CollaborationLauncherTests(unittest.TestCase):
    @staticmethod
    def _function(name):
        return next(
            node
            for node in ast.parse(SOURCE.read_text()).body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )

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
                        scope["receiver_ack_snapshot"](identity, required, "before_restart"),
                        required,
                    )
                else:
                    with self.assertRaisesRegex(RuntimeError, "mixed_receiver_ack_failed"):
                        scope["receiver_ack_snapshot"](identity, required, "before_restart")
                self.assertIn("receiver_before_restart_terminal_status", scope["receipt"])
                self.assertIn(
                    scope["receipt"]["receiver_before_restart_terminal_status"],
                    {
                        "completed",
                        "UNKNOWN",
                    },
                )
                self.assertIs(scope["receipt"]["receiver_before_restart_ack_text_exact"], True)
                if observed == required and type(observed[0]) is int:
                    self.assertIs(
                        scope["receipt"]["receiver_before_restart_receipt_check_valid"],
                        success,
                    )
                else:
                    self.assertNotIn(
                        "receiver_before_restart_receipt_check_valid", scope["receipt"]
                    )

    def test_receiver_ack_failure_keeps_only_bounded_status_and_ack_evidence(self):
        function = next(
            node
            for node in ast.walk(ast.parse(SOURCE.read_text()))
            if isinstance(node, ast.FunctionDef) and node.name == "receiver_ack_snapshot"
        )
        clock = iter(range(1, 100))
        receipt = {}
        state = SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "idle", "turn_sequence": 0, "turn_completed": 0},
        )
        scope = dict(
            time=SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None),
            deadline=3,
            start=0,
            http_json_bytes=lambda *a: json.dumps(
                {"mode": "last", "output": "RAW_PRIVATE_OUTPUT"}
            ).encode(),
            server=SimpleNamespace(url="http://owned"),
            receiver_id="abc12345",
            json=json,
            HTTPStatusError=RuntimeError,
            requests=SimpleNamespace(get=lambda *a, **kw: state),
            remaining_timeout=lambda _: 1,
            receipt=receipt,
            connect=lambda *a, **kw: self.fail("receipt query must not run before ACK"),
            targets=SimpleNamespace(main=object()),
            re=__import__("re"),
            check_deadline=lambda: None,
            RuntimeError=RuntimeError,
        )
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), scope)
        with self.assertRaisesRegex(RuntimeError, "mixed_receiver_ack_failed"):
            scope["receiver_ack_snapshot"](None, (1, 1), "final")
        self.assertEqual(receipt["receiver_final_terminal_status"], "idle")
        self.assertIs(receipt["receiver_final_ack_text_exact"], False)
        self.assertNotIn("receiver_final_receipt_check_valid", receipt)
        self.assertNotIn("RAW_PRIVATE_OUTPUT", repr(receipt))

        unknown_receipt = {}
        unknown_clock = iter(range(1, 100))
        scope["time"] = SimpleNamespace(monotonic=lambda: next(unknown_clock), sleep=lambda _: None)
        scope["receipt"] = unknown_receipt
        scope["requests"] = SimpleNamespace(
            get=lambda *a, **kw: SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "status": "PRIVATE_STATUS_DETAIL",
                    "turn_sequence": 0,
                    "turn_completed": 0,
                },
            )
        )
        with self.assertRaisesRegex(RuntimeError, "mixed_receiver_ack_failed"):
            scope["receiver_ack_snapshot"](None, (1, 1), "final")
        self.assertEqual(unknown_receipt["receiver_final_terminal_status"], "UNKNOWN")
        self.assertNotIn("PRIVATE_STATUS_DETAIL", repr(unknown_receipt))

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
        helper = self._function("_mixed_private_observer_source")
        helper_scope = {}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), str(SOURCE), "exec"), helper_scope)
        with tempfile.TemporaryDirectory() as temp:
            scope = dict(
                root=Path(temp),
                repo=REPO,
                case=case,
                mixed=True,
                importlib=importlib,
                _mixed_private_observer_source=helper_scope[helper.name],
            )
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
            original_functions = {
                node.name: node for node in original.body if isinstance(node, ast.FunctionDef)
            }
            copied_functions = {
                node.name: node for node in copied.body if isinstance(node, ast.FunctionDef)
            }
            self.assertEqual(
                {
                    name: ast.dump(node)
                    for name, node in copied_functions.items()
                    if name != "install_protocol_observer_from_env"
                },
                {
                    name: ast.dump(node)
                    for name, node in original_functions.items()
                    if name != "install_protocol_observer_from_env"
                },
            )
            adapted_installer = copied_functions["install_protocol_observer_from_env"]
            self.assertIn("CAO_TERMINAL_ID", ast.unparse(adapted_installer))
            self.assertIn("mcp_observer_terminal_binding_invalid", ast.unparse(adapted_installer))
            self.assertIn("Path(raw_path).name + '-' + terminal", ast.unparse(adapted_installer))
            self.assertEqual(
                ast.dump(copied.body[-1]),
                ast.dump(
                    ast.parse(
                        "INTERACTION_TOOLS = frozenset(" + repr(tuple(sorted(case["tools"]))) + ")"
                    ).body[0]
                ),
            )
            self.assertTrue(all(n in case["tools"] for n in ("send_message", "cao_ask_user")))

            observation_module = scope["observer_module"]
            with tempfile.TemporaryDirectory() as run_temp:
                run_root = Path(run_temp)
                run_root.chmod(0o700)
                base = run_root / "mcp.events"
                nonce = "a" * 32

                async def handler(_request):
                    return None

                def server():
                    return SimpleNamespace(
                        _mcp_server=SimpleNamespace(
                            request_handlers={
                                observation_module.types.ListToolsRequest: handler,
                                observation_module.types.CallToolRequest: handler,
                            }
                        )
                    )

                sender_env = {
                    "CAO_OPENCODE_MCP_OBSERVER": "1",
                    "CAO_OPENCODE_MCP_OBSERVER_PATH": str(base),
                    "CAO_OPENCODE_MCP_OBSERVER_NONCE": nonce,
                    "CAO_TERMINAL_ID": SENDER_ID,
                }
                receiver_env = {**sender_env, "CAO_TERMINAL_ID": RECEIVER_ID}
                sender_observer = observation_module.install_protocol_observer_from_env(
                    server(), sender_env
                )
                receiver_observer = observation_module.install_protocol_observer_from_env(
                    server(), receiver_env
                )
                receiver_observer.record_catalog(
                    SimpleNamespace(root=observation_module.types.ListToolsResult(tools=[]))
                )
                sender_observer.close()
                receiver_observer.close()
                sender_path = base.with_name(base.name + "-" + SENDER_ID)
                receiver_path = base.with_name(base.name + "-" + RECEIVER_ID)
                self.assertTrue(sender_path.is_file())
                self.assertTrue(receiver_path.is_file())
                self.assertNotEqual(
                    (sender_path.stat().st_dev, sender_path.stat().st_ino),
                    (receiver_path.stat().st_dev, receiver_path.stat().st_ino),
                )
                receiver_observation = observation_module.read_protocol_observations(
                    receiver_path, expected_nonce=nonce
                )
                self.assertEqual(receiver_observation["observer_status"], "observed")
                self.assertEqual(receiver_observation["mcp_catalog_observation_status"], "observed")
                self.assertEqual(receiver_observation["mcp_catalog_tool_names"], [])
                self.assertEqual(receiver_observation["mcp_tool_calls"], [])
                self.assertIs(receiver_observation["mcp_observer_shutdown_observed"], True)

    def test_original_shared_observer_path_red_rejects_second_child(self):
        source = REPO / "test/live_provider/opencode_mcp_observer.py"
        spec = importlib.util.spec_from_file_location("original_mcp_observer_red", source)
        original = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(original)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            directory.chmod(0o700)
            path = directory / "mcp.events"
            env = {
                "CAO_OPENCODE_MCP_OBSERVER": "1",
                "CAO_OPENCODE_MCP_OBSERVER_PATH": str(path),
                "CAO_OPENCODE_MCP_OBSERVER_NONCE": "b" * 32,
            }

            async def handler(_request):
                return None

            server = SimpleNamespace(
                _mcp_server=SimpleNamespace(
                    request_handlers={
                        original.types.ListToolsRequest: handler,
                        original.types.CallToolRequest: handler,
                    }
                )
            )
            first = original.install_protocol_observer_from_env(server, env)
            with self.assertRaisesRegex(ValueError, "mcp_observer_path_invalid"):
                original.install_protocol_observer_from_env(server, env)
            first.close()

    def test_passive_receiver_profile_matches_t101_explicit_empty_filter(self):
        namespace = {"frontmatter": frontmatter, "RuntimeError": RuntimeError}
        function = self._function("_native_v1_passive_receiver_profile")
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
        profiles = json.loads((FIXTURES / "native-message-acceptance-profiles.json").read_text())[
            "profiles"
        ]
        sender_definition = {
            "command": ["python", "-m", "cli_agent_orchestrator.mcp_server.server"],
            "args": ["--private"],
            "env": {"CAO_OPENCODE_MCP_OBSERVER": "1"},
            "cao_tool_profile": "collaboration",
            "timeout": 60000,
        }
        profile = namespace[function.name](profiles["receiver"], sender_definition)
        server = profile["mcpServers"]["cao-mcp-server"]
        self.assertEqual(profile["allowedTools"], [])
        self.assertEqual(set(profile["mcpServers"]), {"cao-mcp-server"})
        self.assertEqual(server["cao_tool_profile"], "collaboration")
        self.assertEqual(server["enabled_tools"], [])
        self.assertEqual(server["env"], sender_definition["env"])
        self.assertNotIn("enabled_tools", sender_definition)

    def test_receiver_capability_path_matches_terminal_service_source(self):
        function = self._function("_receiver_interaction_capability_path")
        namespace = {
            "hashlib": hashlib,
            "Path": Path,
            "re": re,
            "RuntimeError": RuntimeError,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
        with tempfile.TemporaryDirectory() as temp:
            caohome = Path(temp) / "cao-home"
            caohome.mkdir(mode=0o700)
            previous = constants.CAO_HOME_DIR
            constants.CAO_HOME_DIR = caohome
            try:
                production_path = terminal_service._interaction_capability_path(
                    RECEIVER_ID, "f" * 32
                )
            finally:
                constants.CAO_HOME_DIR = previous
            self.assertEqual(
                namespace[function.name](caohome, RECEIVER_ID, "f" * 32), production_path
            )
            for terminal_id, incarnation_id in (
                ("not-an-id", "f" * 32),
                (RECEIVER_ID, "not-an-incarnation"),
            ):
                with self.subTest(terminal_id=terminal_id, incarnation_id=incarnation_id):
                    with self.assertRaisesRegex(RuntimeError, "receiver_identity_failed"):
                        namespace[function.name](caohome, terminal_id, incarnation_id)

    def test_receiver_capability_absence_is_lstat_only_and_bound_to_config(self):
        absent = self._function("_interaction_capability_file_absent")
        exact = self._function("_receiver_capability_absence_exact")
        namespace = {"FileNotFoundError": FileNotFoundError}
        exec(
            compile(ast.Module(body=[absent, exact], type_ignores=[]), str(SOURCE), "exec"),
            namespace,
        )
        binding = (1, 2, "a" * 64, "b" * 64)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "interaction.cap"
            self.assertIs(namespace[absent.name](path), True)
            self.assertIs(namespace[exact.name]({}, True, binding, binding), True)
            self.assertIs(
                namespace[exact.name](
                    {CAO_INTERACTION_CAPABILITY_FILE: "injected"}, True, binding, binding
                ),
                False,
            )
            self.assertIs(namespace[exact.name]({}, False, binding, binding), False)
            self.assertIs(namespace[exact.name]({}, True, binding, (1, 3, *binding[2:])), False)

            path.write_text("opaque capability contents")
            self.assertIs(namespace[absent.name](path), False)
            self.assertIs(namespace[exact.name]({}, False, binding, binding), False)
            path.unlink()
            path.symlink_to(root / "missing-target")
            self.assertTrue(path.is_symlink())
            self.assertIs(namespace[absent.name](path), False)

        class UnknownStat:
            @staticmethod
            def lstat():
                raise RuntimeError("stat failed")

        self.assertIs(namespace[absent.name](UnknownStat()), False)

    def test_observer_path_adapter_preserves_t102_and_rejects_bad_mixed_ids(self):
        function = self._function("_observer_path_for_terminal")
        namespace = {"re": __import__("re"), "RuntimeError": RuntimeError}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
        path = Path("/private/run/mcp.events")
        self.assertIs(namespace[function.name](path, "not-an-id", mixed=False), path)
        self.assertEqual(
            namespace[function.name](path, SENDER_ID, mixed=True),
            path.with_name(path.name + "-" + SENDER_ID),
        )
        for invalid in ("", "A1B2C3D4", "a1b2c3d", "a1b2c3d4-extra"):
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(RuntimeError, "mcp_observer_terminal_binding_invalid"),
            ):
                namespace[function.name](path, invalid, mixed=True)

    def test_mixed_exit_observes_both_seals_in_one_ten_second_grace(self):
        tree = ast.parse(SOURCE.read_text())
        branch = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "mixed"
            and any(
                isinstance(child, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "sender_observation"
                    for target in child.targets
                )
                for child in node.body
            )
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.chmod(0o700)
            receiver_root = root / "receiver"
            receiver_root.mkdir(mode=0o700)
            receiver_config_path = receiver_root / "opencode.json"
            receiver_config_path.write_text("{}\n")
            receiver_ownership_path = receiver_root / ".cao-ownership.json"
            receiver_ownership_path.write_text("owned\n")
            receiver_config_info = receiver_config_path.stat()
            receiver_config_binding = (
                receiver_config_info.st_dev,
                receiver_config_info.st_ino,
                hashlib.sha256(receiver_config_path.read_bytes()).hexdigest(),
                hashlib.sha256(receiver_ownership_path.read_bytes()).hexdigest(),
            )
            base = root / "mcp.events"
            sender_path = base.with_name(base.name + "-" + SENDER_ID)
            receiver_path = base.with_name(base.name + "-" + RECEIVER_ID)
            nonce = "c" * 32
            for path in (sender_path, receiver_path):
                path.write_text(json.dumps({"nonce": nonce}) + "\n")
                path.chmod(0o600)
                path.with_name(path.name + ".complete").write_text("sealed")
            posts = []
            seals = []
            observations = []

            def post(url, *, timeout):
                posts.append((url, timeout))
                return SimpleNamespace(status_code=200)

            observer = SimpleNamespace(
                read_protocol_observations=lambda path, *, expected_nonce: (
                    observations.append((path, expected_nonce))
                    or {"mcp_observer_shutdown_observed": True}
                )
            )
            contract = SimpleNamespace(
                protocol_exact=lambda _value: True,
                catalogs_exact=lambda _rows: True,
                receiver_protocol_exact=lambda _value: True,
                receiver_catalogs_empty_exact=lambda _rows, _summary: True,
                observer_paths_exact=lambda *_args: True,
            )
            capability_absent = self._function("_interaction_capability_file_absent")
            capability_exact = self._function("_receiver_capability_absence_exact")
            capability_namespace = {}
            exec(
                compile(
                    ast.Module(body=[capability_absent, capability_exact], type_ignores=[]),
                    str(SOURCE),
                    "exec",
                ),
                capability_namespace,
            )
            namespace = {
                "mixed": True,
                "observation": base,
                "terminal_id": SENDER_ID,
                "receiver_id": RECEIVER_ID,
                "nonce": nonce,
                "server": SimpleNamespace(url="http://owned"),
                "requests": SimpleNamespace(post=post),
                "start": 0.0,
                "outer_remaining": 179.0,
                "observer_module": observer,
                "mixed_contract": contract,
                "receipt": {"receiver_profile_configuration_exact": True},
                "receiver_root": receiver_root,
                "receiver_config_binding": receiver_config_binding,
                "receiver_mcp_environment": {},
                "receiver_capability_path": root / "receiver.cap",
                "receiver_capability_absent_at_config": True,
                "_observer_path_for_terminal": lambda path, terminal, *, mixed: path.with_name(
                    path.name + "-" + terminal
                ),
                "_private_protocol_rows": lambda path, expected_nonce: json.loads(path.read_text())
                and [{"nonce": expected_nonce}],
                "_observer_sidecar_identity": lambda path: (
                    path.stat().st_dev,
                    path.stat().st_ino,
                ),
                "_interaction_capability_file_absent": capability_namespace[capability_absent.name],
                "_receiver_capability_absence_exact": capability_namespace[capability_exact.name],
                "_sha256": lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
                "_graceful_seals_seen_by_deadline": lambda seen, now, stop: (
                    seals.append((seen, now, stop)) or (seen is True and now <= stop)
                ),
                "time": SimpleNamespace(
                    monotonic=lambda: 1.0, sleep=lambda _: self.fail("unexpected wait")
                ),
                "human": SimpleNamespace(),
                "json": json,
                "os": __import__("os"),
                "stat": __import__("stat"),
            }
            exec(
                compile(ast.Module(body=[branch], type_ignores=[]), str(SOURCE), "exec"),
                namespace,
            )
            receipt = namespace["receipt"]
            self.assertEqual(
                [url for url, _timeout in posts],
                [
                    "http://owned/terminals/" + SENDER_ID + "/exit",
                    "http://owned/terminals/" + RECEIVER_ID + "/exit",
                ],
            )
            self.assertTrue(all(0 < timeout <= 15 for _url, timeout in posts))
            self.assertEqual([path for path, _nonce in observations], [sender_path, receiver_path])
            self.assertTrue(all(seal[2] == 11.0 for seal in seals))
            self.assertIs(receipt["observer_graceful_seal"], True)
            self.assertIs(receipt["receiver_observer_graceful_seal_exact"], True)
            self.assertIs(receipt["receiver_observer_isolation_exact"], True)
            self.assertIs(receipt["receiver_no_runtime_human_capability_exact"], True)

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
                {
                    "status": "unknown",
                    "turn_sequence": 0,
                    "turn_completed": 0,
                    "session_incarnation_id": "f" * 32,
                },
                {
                    "status": "idle",
                    "turn_sequence": 0,
                    "turn_completed": 0,
                    "session_incarnation_id": "f" * 32,
                },
                {
                    "status": "idle",
                    "turn_sequence": 0,
                    "turn_completed": 0,
                    "session_incarnation_id": "f" * 32,
                },
                {
                    "status": "completed",
                    "turn_sequence": 0,
                    "turn_completed": 0,
                    "session_incarnation_id": "f" * 32,
                },
                {
                    "status": "idle",
                    "turn_sequence": 0,
                    "turn_completed": 0,
                    "session_incarnation_id": "f" * 32,
                },
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
                                "mcp": {
                                    "cao-mcp-server": {
                                        "type": "local",
                                        "command": [
                                            sys.executable,
                                            "-m",
                                            "cli_agent_orchestrator.mcp_server.server",
                                        ],
                                        "enabled": True,
                                        "environment": {
                                            "CAO_INTERNAL_MCP_TOOL_PROFILE": "collaboration",
                                            "CAO_INTERNAL_MCP_ENABLED_TOOLS": "[]",
                                        },
                                    }
                                },
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
            capability_path = self._function("_receiver_interaction_capability_path")
            capability_absent = self._function("_interaction_capability_file_absent")
            capability_exact = self._function("_receiver_capability_absence_exact")
            namespace = {
                "RuntimeError": RuntimeError,
                "ValueError": ValueError,
                "re": __import__("re"),
                "hashlib": hashlib,
                "Path": Path,
            }
            exec(
                compile(
                    ast.Module(
                        body=[
                            bounded_count,
                            reader,
                            helper,
                            capability_path,
                            capability_absent,
                            capability_exact,
                        ],
                        type_ignores=[],
                    ),
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
                "validate_v1_mcp_configuration": validate_v1_mcp_configuration,
                "_receiver_interaction_capability_path": namespace[
                    "_receiver_interaction_capability_path"
                ],
                "_interaction_capability_file_absent": namespace[
                    "_interaction_capability_file_absent"
                ],
                "_receiver_capability_absence_exact": namespace[
                    "_receiver_capability_absence_exact"
                ],
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
