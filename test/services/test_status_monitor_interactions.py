"""Runtime composition for provider-native manual interaction observations."""

from types import SimpleNamespace
from unittest.mock import Mock

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.services.interactions.models import ObservationReceipt
from cli_agent_orchestrator.services.status_monitor import StatusMonitor


class _Provider(BaseProvider):
    async def initialize(self):
        return True

    def get_status(self, buffer):
        return TerminalStatus.UNKNOWN

    def extract_last_message_from_script(self, script_output):
        return script_output

    def exit_cli(self):
        return "/exit"

    def cleanup(self):
        return None


def _receipt(observation_id, turn_generation, *, replayed=False):
    return ObservationReceipt(
        observation_id=observation_id,
        question_id=f"question-{observation_id}",
        lifecycle="manual_only",
        response_capability="manual_only",
        manual_reason="manual provider dialog",
        turn_generation=turn_generation,
        replayed=replayed,
    )


def test_native_observation_tracks_changed_dialog_and_reconciles_exact_prior_turn(
    monkeypatch,
):
    import cli_agent_orchestrator.services.remote_terminal_service as remote
    import cli_agent_orchestrator.services.terminal_service as terminal_service

    metadata = {
        "session_incarnation_id": "inc-1",
        "tmux_session": "session-1",
        "tmux_window": "window-1",
    }
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda _terminal: metadata)
    monkeypatch.setattr(remote, "placement", lambda _terminal: None)

    service = Mock()
    service.source_status.side_effect = [
        SimpleNamespace(state="current", current_generation=4),
        SimpleNamespace(state="current", current_generation=4),
        # An authorized WebSocket input advanced the durable source before its
        # bytes reached the provider. Closure must still target the old notice.
        SimpleNamespace(state="current", current_generation=5),
        SimpleNamespace(state="current", current_generation=5),
    ]
    service.observe_native.side_effect = [
        _receipt("notice-1", 4),
        _receipt("notice-2", 4),
    ]
    monkeypatch.setattr(terminal_service, "_interaction_service", lambda: service)

    provider = _Provider("terminal-1", "session-1", "window-1")
    monitor = StatusMonitor()
    monitor._observe_native_interaction(
        "terminal-1",
        provider,
        TerminalStatus.WAITING_USER_ANSWER,
        ["Approve access for repository A"],
    )
    first_fingerprint = service.observe_native.call_args.args[3].dialog_fingerprint

    monitor._observe_native_interaction(
        "terminal-1",
        provider,
        TerminalStatus.WAITING_USER_ANSWER,
        ["Approve access for repository B"],
    )
    second_observation = service.observe_native.call_args.args[3]

    assert service.observe_native.call_count == 2
    assert first_fingerprint != second_observation.dialog_fingerprint
    assert second_observation.prompt is None
    assert second_observation.options == ()
    assert second_observation.answer_capability == "none"

    monitor._observe_native_interaction(
        "terminal-1",
        provider,
        TerminalStatus.IDLE,
        ["Ready for input"],
    )
    assert service.reconcile_native_observation.call_count == 0
    monitor._observe_native_interaction(
        "terminal-1",
        provider,
        TerminalStatus.IDLE,
        ["Ready for input"],
    )

    service.reconcile_native_observation.assert_called_once_with(
        "terminal-1",
        "inc-1",
        4,
        observation_id="notice-2",
        dialog_fingerprint=second_observation.dialog_fingerprint,
        provider_evidence_version=second_observation.dialog_version,
        allow_resume=True,
    )
    assert monitor._native_observation_pins == {}


def test_transient_idle_with_original_dialog_visible_does_not_reconcile(monkeypatch):
    import cli_agent_orchestrator.services.remote_terminal_service as remote
    import cli_agent_orchestrator.services.terminal_service as terminal_service

    metadata = {
        "session_incarnation_id": "inc-1",
        "tmux_session": "session-1",
        "tmux_window": "window-1",
    }
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda _terminal: metadata)
    monkeypatch.setattr(remote, "placement", lambda _terminal: None)
    service = Mock()
    service.source_status.side_effect = [
        SimpleNamespace(state="current", current_generation=2),
        SimpleNamespace(state="current", current_generation=2),
    ]
    service.observe_native.return_value = _receipt("notice-1", 2)
    service.latest_unresolved_native_observation.return_value = None
    monkeypatch.setattr(terminal_service, "_interaction_service", lambda: service)

    provider = _Provider("terminal-1", "session-1", "window-1")
    monitor = StatusMonitor()
    unchanged_dialog = ["Provider approval dialog remains visible"]
    monitor._observe_native_interaction(
        "terminal-1",
        provider,
        TerminalStatus.WAITING_USER_ANSWER,
        unchanged_dialog,
    )
    monitor._observe_native_interaction(
        "terminal-1",
        provider,
        TerminalStatus.IDLE,
        unchanged_dialog,
    )

    service.reconcile_native_observation.assert_not_called()
    pin = monitor._native_observation_pins["terminal-1"]
    assert pin["closure_candidate_fingerprint"] is None
    assert pin["closure_observations"] == 0


def test_unknown_status_does_not_reconcile_native_observation(monkeypatch):
    import cli_agent_orchestrator.services.remote_terminal_service as remote
    import cli_agent_orchestrator.services.terminal_service as terminal_service

    metadata = {
        "session_incarnation_id": "inc-1",
        "tmux_session": "session-1",
        "tmux_window": "window-1",
    }
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda _terminal: metadata)
    monkeypatch.setattr(remote, "placement", lambda _terminal: None)
    service = Mock()
    service.source_status.return_value = SimpleNamespace(state="current", current_generation=1)
    service.observe_native.return_value = _receipt("notice-1", 1)
    service.latest_unresolved_native_observation.return_value = None
    monkeypatch.setattr(terminal_service, "_interaction_service", lambda: service)

    provider = _Provider("terminal-1", "session-1", "window-1")
    monitor = StatusMonitor()
    monitor._observe_native_interaction(
        "terminal-1",
        provider,
        TerminalStatus.WAITING_USER_ANSWER,
        ["Provider dialog"],
    )

    monitor._observe_native_interaction(
        "terminal-1",
        provider,
        TerminalStatus.UNKNOWN,
        ["Unclassified frame"],
    )

    service.reconcile_native_observation.assert_not_called()
    assert "terminal-1" in monitor._native_observation_pins
