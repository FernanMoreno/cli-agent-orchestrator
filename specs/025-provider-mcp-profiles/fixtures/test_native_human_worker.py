"""Private worker status and scope failures without a provider or network call."""

import ast
import hashlib
import inspect
import json
import os
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import native_human_api_worker as worker

from cli_agent_orchestrator.models.terminal import TerminalStatus


class WorkerTests(unittest.TestCase):
    def test_actual_private_probe_registration_precedes_root_static_mount(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.staticfiles import StaticFiles

        tree = ast.parse(Path(worker.__file__).read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        registration = next(
            n
            for n in main.body
            if isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Call)
            and (
                isinstance(n.value.func, ast.Attribute)
                and n.value.func.attr == "add_api_route"
                or isinstance(n.value.func, ast.Name)
                and n.value.func.id == "register_private_probe"
            )
        )
        helpers = [
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "register_private_probe"
        ]
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "index.html").write_text("synthetic UI")
            app = FastAPI()
            app.mount("/", StaticFiles(directory=temp, html=True), name="web")
            existing = list(app.router.routes)
            scope = dict(app=app, observe=lambda: {"provider_status": "COMPLETED"})
            exec(
                compile(
                    ast.Module(body=[*helpers, registration], type_ignores=[]),
                    worker.__file__,
                    "exec",
                ),
                scope,
            )
            client = TestClient(app)
            response = client.get("/__v1/human-status")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"provider_status": "COMPLETED"})
            self.assertEqual(client.get("/").text, "synthetic UI")
            self.assertEqual([r for r in app.router.routes if r in existing], existing)

    def test_native_argv_requires_exact_owned_executable_and_single_model_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "native"
            binary.write_bytes(b"synthetic executable")
            process = root / "123"
            process.mkdir()
            (process / "exe").symlink_to(binary)
            identity = SimpleNamespace(pid=123, uid=os.getuid())
            good = b"native\0--model\0explicit\0--agent\0native-human\0"
            for raw, expected in (
                (good, True),
                (good.replace(b"explicit", b"other"), False),
                (good + b"--model\0explicit\0", False),
                (good.replace(b"native-human", b"other"), False),
                (good[:-1], False),
                (b"x" * 65537, False),
            ):
                (process / "cmdline").write_bytes(raw)
                self.assertEqual(
                    worker.native_launch_model_exact(
                        [identity],
                        binary,
                        "explicit",
                        "native-human",
                        lambda _: True,
                        proc_root=root,
                    ),
                    expected,
                )
            (process / "cmdline").write_bytes(good)
            self.assertFalse(
                worker.native_launch_model_exact(
                    [identity], binary, "explicit", "native-human", lambda _: False, proc_root=root
                )
            )
            self.assertFalse(
                worker.native_launch_model_exact(
                    [], binary, "explicit", "native-human", lambda _: True, proc_root=root
                )
            )
            other = root / "other"
            other.write_bytes(b"other")
            (process / "exe").unlink()
            (process / "exe").symlink_to(other)
            self.assertFalse(
                worker.native_launch_model_exact(
                    [identity], binary, "explicit", "native-human", lambda _: True, proc_root=root
                )
            )

    def test_reconstructed_provider_model_uses_original_owned_launch_config(self):
        from cli_agent_orchestrator.providers import manager, opencode_cli
        from cli_agent_orchestrator.utils.opencode_ownership import PrivateConfigOwnership

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            home.chmod(0o700)
            owner = PrivateConfigOwnership(home, "abc12345")
            root = owner.allocate(1)
            config = root / "opencode.json"
            config.write_text(
                json.dumps(
                    {"model": "explicit", "agent": {"native-human": {"model": "agent-fallback"}}}
                )
            )
            config.chmod(0o600)
            info = config.stat()
            binding = dict(
                config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
                config_device=info.st_dev,
                config_inode=info.st_ino,
                ownership_sha256=hashlib.sha256(
                    (root / ".cao-ownership.json").read_bytes()
                ).hexdigest(),
                launch_model="explicit",
                launch_model_observed=True,
            )
            tree = ast.parse(
                textwrap.dedent(inspect.getsource(manager.ProviderManager._get_provider_locked))
            )
            call = next(
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Assign)
                and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Attribute)
                and n.value.func.attr == "create_provider"
            )
            metadata = dict(
                provider="opencode_cli",
                tmux_session="owned",
                tmux_window="pane",
                agent_profile="native-human",
            )
            scope = dict(
                self=manager.ProviderManager(),
                metadata=metadata,
                persisted_engine=None,
                terminal_id="abc12345",
            )
            with (
                patch.object(manager, "registered_provider_descriptor", return_value=None),
                patch.object(opencode_cli, "CAO_HOME_DIR", home),
            ):
                exec(
                    compile(ast.Module(body=[call], type_ignores=[]), manager.__file__, "exec"),
                    scope,
                )
                provider = scope["provider"]
                self.assertIsNone(provider._model)
                provider.restore_private_config_ownership()
                provider.get_status = Mock(return_value=TerminalStatus.COMPLETED)
                kwargs = dict(
                    model_identity=lambda: worker.owned_model_exact(provider, "explicit", binding)
                )
                value = worker.provider_snapshot(
                    provider,
                    lambda: provider,
                    Mock(return_value="private"),
                    SimpleNamespace(session_name="owned", name="pane"),
                    "explicit",
                    time.monotonic() + 3,
                    **kwargs,
                )
                self.assertTrue(value["provider_model_exact"])
                self.assertIsNone(provider._model)
                for key, bad in (
                    ("config_sha256", "0" * 64),
                    ("ownership_sha256", "0" * 64),
                    ("config_inode", info.st_ino + 1),
                    ("config_device", info.st_dev + 1),
                    ("launch_model", "other"),
                    ("launch_model_observed", False),
                ):
                    with self.subTest(key=key):
                        self.assertFalse(
                            worker.owned_model_exact(provider, "explicit", {**binding, key: bad})
                        )
                with patch.object(provider, "terminal_id", "other"):
                    self.assertFalse(worker.owned_model_exact(provider, "explicit", binding))
                with patch.object(provider, "_model", "other"):
                    self.assertFalse(worker.owned_model_exact(provider, "explicit", binding))
                with patch.object(provider, "_agent_profile", "other"):
                    self.assertFalse(worker.owned_model_exact(provider, "explicit", binding))
                original = config.read_bytes()
                wrong = json.loads(original)
                wrong["model"] = "other"
                config.write_text(json.dumps(wrong))
                changed_binding = {
                    **binding,
                    "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                }
                self.assertFalse(worker.owned_model_exact(provider, "explicit", changed_binding))
                config.write_bytes(original)
                config.write_bytes(original + b" ")
                self.assertFalse(worker.owned_model_exact(provider, "explicit", binding))
                config.write_bytes(original)
                backup = root / "original.json"
                config.rename(backup)
                config.write_bytes(original)
                config.chmod(0o600)
                self.assertFalse(worker.owned_model_exact(provider, "explicit", binding))
                os.replace(backup, config)
                self.assertTrue(worker.owned_model_exact(provider, "explicit", binding))
                self.assertTrue(owner.cleanup())
                replacement = owner.allocate(1)
                (replacement / "opencode.json").write_bytes(original)
                (replacement / "opencode.json").chmod(0o600)
                self.assertFalse(worker.owned_model_exact(provider, "explicit", binding))
                self.assertTrue(owner.cleanup())
                owner.allocate(2)
                self.assertFalse(worker.owned_model_exact(provider, "explicit", binding))

    def test_fresh_provider_status_never_exports_history(self):
        provider = SimpleNamespace(
            _cli_major=1, _model="explicit", get_status=Mock(return_value=TerminalStatus.COMPLETED)
        )
        history = Mock(return_value="private user/model text never exported")
        with patch.object(worker.time, "monotonic", return_value=2):
            value = worker.provider_snapshot(
                provider,
                lambda: provider,
                history,
                SimpleNamespace(session_name="owned", name="pane"),
                "explicit",
                3,
                model_identity=lambda: True,
            )
        history.assert_called_once_with("owned", "pane", tail_lines=400)
        self.assertTrue(value["provider_instance_stable"])
        self.assertTrue(value["deadline_met"])
        self.assertNotIn("private user/model", json.dumps(value))

    def test_replaced_provider_is_never_used_to_read_history_or_status(self):
        provider = SimpleNamespace(get_status=Mock())
        history = Mock()
        with self.assertRaisesRegex(ValueError, "probe_provider_changed"):
            worker.provider_snapshot(
                provider,
                lambda: object(),
                history,
                None,
                "explicit",
                3,
                model_identity=lambda: True,
            )
        history.assert_not_called()
        provider.get_status.assert_not_called()

    def test_identity_change_during_read_and_late_status_fail_closed(self):
        provider = SimpleNamespace(
            _cli_major=1, _model="explicit", get_status=Mock(return_value=TerminalStatus.COMPLETED)
        )
        current = Mock(side_effect=[provider, object()])
        with self.assertRaisesRegex(ValueError, "probe_provider_changed"):
            worker.provider_snapshot(
                provider,
                current,
                Mock(return_value="private"),
                SimpleNamespace(session_name="owned", name="pane"),
                "explicit",
                3,
                model_identity=lambda: True,
            )
        provider.get_status.assert_not_called()
        with patch.object(worker.time, "monotonic", return_value=4):
            self.assertFalse(
                worker.provider_snapshot(
                    provider,
                    lambda: provider,
                    Mock(return_value="private"),
                    SimpleNamespace(session_name="owned", name="pane"),
                    "explicit",
                    3,
                    model_identity=lambda: True,
                )["deadline_met"]
            )

    def test_private_scope_rejects_nonfinite_deadlines_and_invalid_nonce(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "scope.json"
            good = dict(
                terminal_id="abc12345",
                model="opencode-go/glm-5.3-flash",
                deadline=3.0,
                nonce="a" * 32,
                config_sha256="b" * 64,
                ownership_sha256="c" * 64,
                config_device=0,
                config_inode=1,
                launch_model="opencode-go/glm-5.3-flash",
                launch_model_observed=True,
            )
            path.write_text(json.dumps(good))
            path.chmod(0o600)
            self.assertEqual(worker.read_scope(path, root), good)
            for field, value in (
                ("deadline", float("nan")),
                ("deadline", float("inf")),
                ("deadline", -1),
                ("nonce", "invalid"),
                ("config_sha256", "invalid"),
                ("ownership_sha256", "invalid"),
                ("config_device", True),
                ("config_inode", 0),
                ("launch_model", "other"),
                ("launch_model_observed", False),
            ):
                with self.subTest(field=field, value=value):
                    path.write_text(json.dumps({**good, field: value}))
                    with self.assertRaisesRegex(ValueError, "probe_scope_invalid"):
                        worker.read_scope(path, root)

    def test_scope_does_not_follow_symlink_or_accept_group_readable_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "scope.json"
            source = root / "source.json"
            source.write_text("{}")
            source.chmod(0o600)
            path.symlink_to(source)
            with self.assertRaises(OSError):
                worker.read_scope(path, root)
            path.unlink()
            path.write_text("{}")
            path.chmod(0o640)
            with self.assertRaisesRegex(ValueError, "probe_scope_invalid"):
                worker.read_scope(path, root)


if __name__ == "__main__":
    unittest.main()
