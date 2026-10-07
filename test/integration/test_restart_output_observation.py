"""A server restart must reconnect existing native output without sending input."""

import shlex
import shutil
import time
from unittest.mock import patch

import libtmux
import pytest

from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.clients.tmux import TmuxClient
from cli_agent_orchestrator.providers.catalog import registered_provider_descriptors
from cli_agent_orchestrator.providers.manager import ProviderManager
from cli_agent_orchestrator.services import terminal_service as ts
from cli_agent_orchestrator.services.fifo_reader import FifoManager
from cli_agent_orchestrator.services.status_monitor import StatusMonitor


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux unavailable")
@pytest.mark.parametrize("descriptor", registered_provider_descriptors(), ids=lambda d: d.name)
def test_existing_provider_output_is_observed_without_launch_or_resend(
    tmp_path, isolated_memory_db, monkeypatch, descriptor
):
    client = TmuxClient()
    client.server = libtmux.Server(socket_path=str(tmp_path / "restart.sock"))
    backend = TmuxBackend(client)
    (tmp_path / "fifo").mkdir()
    fifo = FifoManager()
    monitor = StatusMonitor()
    manager = ProviderManager()
    monkeypatch.setattr("cli_agent_orchestrator.services.fifo_reader.FIFO_DIR", tmp_path / "fifo")
    monkeypatch.setattr(ts, "FIFO_DIR", tmp_path / "fifo")
    monkeypatch.setattr(ts, "fifo_manager", fifo)
    monkeypatch.setattr(ts, "status_monitor", monitor)
    monkeypatch.setattr(ts, "provider_manager", manager)
    monkeypatch.setattr("cli_agent_orchestrator.services.status_monitor.provider_manager", manager)
    monkeypatch.setattr("cli_agent_orchestrator.backends.registry.get_backend", lambda: backend)
    monkeypatch.setattr(ts, "get_backend", lambda: backend)
    monkeypatch.setattr(
        "cli_agent_orchestrator.providers.manager.registered_provider_descriptor", lambda name: None
    )
    terminal_id = "abc123ef"
    try:
        backend.create_session("restart", "worker", terminal_id, str(tmp_path))
        db.create_terminal(terminal_id, "restart", "worker", descriptor.name, "reviewer")
        if descriptor.name in {"kimi_cli", "opencode_cli"}:
            db.update_terminal_provider_variant(
                terminal_id, "code" if descriptor.name == "kimi_cli" else "opencode-v2"
            )
        provider = manager.get_provider(terminal_id)
        receipt = None
        if provider.requires_turn_receipt:
            provider.prepare_input("one task")
            receipt = provider.pending_turn_receipt_state()
            db.begin_terminal_turn_receipt(
                terminal_id, descriptor.name, receipt["generation"], receipt["receipt_sha256"]
            )
            db.mark_terminal_turn_receipt_sent(
                terminal_id, receipt["generation"], receipt["receipt_sha256"]
            )
        manager._providers.clear()
        client.server.sessions.get(session_name="restart").active_window.active_pane.send_keys(
            "printf 'before-restart\\n'; sleep 300"
        )
        deadline = time.monotonic() + 3
        while (
            "before-restart" not in backend.get_history("restart", "worker")
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        restore = getattr(ts, "restore_terminal_observation", lambda terminal: False)
        with (
            patch.object(
                backend, "send_keys", side_effect=AssertionError("recovery replayed input")
            ),
            patch.object(
                backend, "send_special_key", side_effect=AssertionError("recovery submitted a key")
            ),
        ):
            assert restore(terminal_id)
            assert "before-restart" in monitor.get_buffer(terminal_id)
            if receipt:
                assert manager.get_provider(terminal_id).pending_turn_receipt_state() == receipt
                assert db.get_terminal_turn_receipt(terminal_id)["phase"] == "sent"
            assert restore(terminal_id)  # repeated discovery is harmless
            assert (
                client.cleanup_terminal_exact(
                    terminal_id, "restart", "worker", close=False
                ).outcome.value
                == "still_present"
            )
    finally:
        fifo.stop_reader(terminal_id)
        fifo.stop_watchdog()
        client.server.kill()


def test_observation_rejects_unproven_backend_target(tmp_path, isolated_memory_db, monkeypatch):
    db.create_terminal("old", "reused", "worker", "codex")
    restore = getattr(ts, "restore_terminal_observation", lambda terminal: False)
    # A backend that cannot prove the exact target never acquires observation.
    from unittest.mock import MagicMock

    from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult

    backend = MagicMock()
    backend.supports_event_inbox.return_value = False
    backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.UNKNOWN
    )
    backend.terminal_observation_target_matches.return_value = False
    monkeypatch.setattr(ts, "get_backend", lambda: backend)
    assert not restore("old")
