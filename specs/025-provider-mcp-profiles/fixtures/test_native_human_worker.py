"""Private worker status and scope failures without a provider or network call."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import native_human_api_worker as worker

from cli_agent_orchestrator.models.terminal import TerminalStatus


class WorkerTests(unittest.TestCase):
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
            )
        history.assert_called_once_with("owned", "pane", tail_lines=400)
        self.assertTrue(value["provider_instance_stable"])
        self.assertTrue(value["deadline_met"])
        self.assertNotIn("private user/model", json.dumps(value))

    def test_replaced_provider_is_never_used_to_read_history_or_status(self):
        provider = SimpleNamespace(get_status=Mock())
        history = Mock()
        with self.assertRaisesRegex(ValueError, "probe_provider_changed"):
            worker.provider_snapshot(provider, lambda: object(), history, None, "explicit", 3)
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
            )
            path.write_text(json.dumps(good))
            path.chmod(0o600)
            self.assertEqual(worker.read_scope(path, root), good)
            for field, value in (
                ("deadline", float("nan")),
                ("deadline", float("inf")),
                ("deadline", -1),
                ("nonce", "invalid"),
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
