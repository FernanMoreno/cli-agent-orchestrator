"""Offline tests for the optional, content-free human-runner diagnostics."""

import ast
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).with_name("run_native_v1_human.py")


def _helpers():
    names = {
        "_receipt_verification_observer_enabled",
        "_create_private_observer_directory",
        "_prepare_receipt_stage_api_pythonpath",
        "_api_pythonpath_for_receipt_observer",
        "_receipt_stage_api_ready",
        "_receipt_stage_epoch_summary",
        "_capture_receipt_stage_diagnostic_safely",
    }
    tree = ast.parse(SOURCE.read_text())
    definitions = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    if {node.name for node in definitions} != names:
        raise AssertionError("receipt diagnostic helper set changed")
    namespace = {"Path": Path, "os": os, "re": re, "stat": stat}
    exec(
        compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), "exec"),
        namespace,
    )
    return namespace


class HumanReceiptDiagnosticTests(unittest.TestCase):
    def test_observer_manifest_flag_is_strict_and_default_closed(self):
        enabled = _helpers()["_receipt_verification_observer_enabled"]
        self.assertIs(enabled({}), False)
        self.assertIs(enabled({"receipt_verification_observer": False}), False)
        self.assertIs(enabled({"receipt_verification_observer": True}), True)
        for invalid in (0, 1, "true", None):
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(RuntimeError, "receipt_stage_observer_flag_invalid"),
            ):
                enabled({"receipt_verification_observer": invalid})

    def test_default_off_keeps_existing_api_path_without_preparing_bootstrap(self):
        helpers = _helpers()
        existing = "/fixture:/src:/repo"
        before = dict(os.environ)
        result = helpers["_api_pythonpath_for_receipt_observer"](
            observer=None,
            bootstrap_root=None,
            raw_dir=None,
            repo=None,
            source_hashes=None,
            role="api_a",
            existing_pythonpath=existing,
        )
        self.assertEqual(result, existing)
        self.assertEqual(dict(os.environ), before)

    def test_api_pythonpath_binds_both_roles_and_only_observer_source_hashes(self):
        helpers = _helpers()
        source_modules = {
            "cli_agent_orchestrator.providers.opencode_cli": "src/providers/opencode_cli.py",
            "cli_agent_orchestrator.services.status_monitor": "src/services/status_monitor.py",
            "cli_agent_orchestrator.services.terminal_service": "src/services/terminal_service.py",
            "cli_agent_orchestrator.utils.receipt_verification_diagnostics": "src/utils/receipt_verification_diagnostics.py",
        }
        expected_hashes = {
            relative: f"{index:064x}" for index, relative in enumerate(source_modules.values(), 1)
        }
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            existing = os.pathsep.join(
                (
                    str(repo / "src"),
                    str(repo),
                    str(repo / "specs/025-provider-mcp-profiles/fixtures"),
                )
            )
            bootstrap_root = Path(temp) / "bootstrap"
            raw_dir = Path(temp) / "raw"

            def prepare_api_observer(bootstrap, raw, source_repo, source_hashes, role):
                calls.append((bootstrap, raw, source_repo, source_hashes, role))
                return os.pathsep.join((str(bootstrap), str(source_repo / "src"), str(source_repo)))

            observer = SimpleNamespace(
                SOURCE_MODULES=source_modules,
                prepare_api_observer=prepare_api_observer,
            )

            for role in ("api_a", "api_b"):
                result = helpers["_api_pythonpath_for_receipt_observer"](
                    observer=observer,
                    bootstrap_root=bootstrap_root,
                    raw_dir=raw_dir,
                    repo=repo,
                    source_hashes={**expected_hashes, "unrelated.py": "f" * 64},
                    role=role,
                    existing_pythonpath=existing,
                )
                self.assertEqual(
                    result,
                    os.pathsep.join((str(bootstrap_root / role), existing)),
                )
            self.assertEqual([call[-1] for call in calls], ["api_a", "api_b"])
            self.assertEqual(calls[0][3], expected_hashes)
            self.assertEqual(calls[1][3], expected_hashes)

    def test_bootstrap_readiness_requires_private_role_directory_and_event_file(self):
        ready = _helpers()["_receipt_stage_api_ready"]
        with tempfile.TemporaryDirectory() as temp:
            raw = Path(temp)
            role = raw / "api_a"
            role.mkdir(mode=0o700)
            event = role / "events.jsonl"
            event.write_bytes(b"")
            event.chmod(0o600)
            self.assertTrue(ready(raw, "api_a"))
            self.assertFalse(ready(raw, "api_b"))
            self.assertFalse(ready(raw, "invalid"))
            event.chmod(0o644)
            self.assertFalse(ready(raw, "api_a"))

    def test_capture_skips_live_writers_and_unavailable_epochs_are_not_zero_filled(self):
        helpers = _helpers()
        allowed = {"attempt": frozenset({"started", "verified"})}
        complete = {
            "observer_status": "complete",
            "role": "api_a",
            "event_count": 2,
            "thread_count": 1,
            "failed_event_count": 0,
            "stage_outcome_counts": {"attempt": {"started": 1, "verified": 1}},
        }
        unavailable = {
            "observer_status": "unavailable",
            "role": "api_b",
            "event_count": None,
            "thread_count": None,
            "failed_event_count": None,
            "stage_outcome_counts": None,
        }
        calls = []

        class Observer:
            _ALLOWED_OUTCOMES = allowed

            @staticmethod
            def read_api_observation(raw_dir, *, role, writers_dead):
                calls.append((role, writers_dead))
                return complete if role == "api_a" else unavailable

        with tempfile.TemporaryDirectory() as temp:
            raw = Path(temp) / "raw"
            raw.mkdir(mode=0o700)
            helpers["raw"] = raw
            live = helpers["_capture_receipt_stage_diagnostic_safely"](
                observer=Observer,
                raw_dir=raw / "receipt-verification",
                writers_dead=False,
                enabled=True,
            )
            self.assertEqual(live["status"], "unavailable")
            self.assertEqual(live["reason"], "writers_not_confirmed_dead")
            self.assertEqual(calls, [])
            off = helpers["_capture_receipt_stage_diagnostic_safely"](
                observer=Observer,
                raw_dir=raw / "receipt-verification",
                writers_dead=True,
                enabled=False,
            )
            self.assertEqual(off["status"], "not_enabled")
            self.assertEqual(calls, [])
            (raw / "receipt-verification").mkdir(mode=0o700)
            dead = helpers["_capture_receipt_stage_diagnostic_safely"](
                observer=Observer,
                raw_dir=raw / "receipt-verification",
                writers_dead=True,
                enabled=True,
            )
            self.assertEqual(dead["status"], "partial")
            self.assertEqual(calls, [("api_a", True), ("api_b", True)])
            self.assertEqual(dead["epochs"]["api_a"]["event_count"], 2)
            self.assertIsNone(dead["epochs"]["api_b"]["event_count"])
            self.assertIsNone(dead["epochs"]["api_b"]["stage_outcome_counts"])

    def test_final_output_loop_records_only_safe_state_and_keeps_409_fatal(self):
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

        class StatusError(ValueError):
            def __init__(self):
                super().__init__("http_status")
                self.status_code = 409
                self.turn_state = "reconcile"

        receipt = {"last_output_turn_state": None}
        scope = {
            "time": SimpleNamespace(monotonic=lambda: 5, sleep=lambda _: None),
            "deadline": 10,
            "start": 0,
            "http_json_bytes": lambda *args: (_ for _ in ()).throw(StatusError()),
            "HTTPStatusError": StatusError,
            "server": SimpleNamespace(url="http://owned"),
            "terminal_id": "abc12345",
            "receipt": receipt,
        }
        code = compile(ast.Module(body=[loop], type_ignores=[]), str(SOURCE), "exec")
        with self.assertRaises(StatusError):
            exec(code, scope)
        self.assertEqual(receipt["last_output_http_status"], 409)
        self.assertEqual(receipt["last_output_turn_state"], "reconcile")
        self.assertNotIn("detail", receipt)
        self.assertNotIn("generation", receipt)


if __name__ == "__main__":
    unittest.main()
