"""Contract tests for generic (non-native) Herdr status observation.

The textual pane observation is transport only.  It is not evidence that a
Work item ran or completed; only a registered provider may interpret it as a
terminal status, and StatusMonitor owns latch/publication.
"""

from unittest.mock import MagicMock, patch

from cli_agent_orchestrator.backends.herdr_backend import HerdrBackend
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services.status_monitor import StatusMonitor


class _NonNativeHerdr:
    """Controlled event-inbox backend with no native agent state."""

    def __init__(self, observation):
        self.observation = observation

    def supports_event_inbox(self):
        return True

    def get_native_status(self, session_name, window_name):
        return None

    def get_non_native_status_observation(self, session_name, window_name):
        return self.observation


class TestHerdrNonNativeStatusObservation:
    def test_backend_transports_nonempty_pane_text_without_classifying_it(self):
        backend = object.__new__(HerdrBackend)
        backend.get_history = MagicMock(return_value="plain shell transcript")

        assert backend.get_non_native_status_observation("session", "window") == (
            "plain shell transcript"
        )
        backend.get_history.assert_called_once_with("session", "window")

    def test_backend_fails_closed_when_the_pane_observation_cannot_be_read(self):
        backend = object.__new__(HerdrBackend)
        backend.get_history = MagicMock(side_effect=RuntimeError("unavailable"))

        assert backend.get_non_native_status_observation("session", "window") is None

    @patch("cli_agent_orchestrator.services.status_monitor.provider_manager")
    @patch("cli_agent_orchestrator.backends.registry.get_backend")
    @patch("cli_agent_orchestrator.services.status_monitor.bus.publish")
    def test_verified_non_native_status_uses_latch_and_status_transport(
        self, publish, get_backend, provider_manager
    ):
        backend = _NonNativeHerdr("provider-specific status fixture")
        provider = MagicMock()
        provider.session_name = "session"
        provider.window_name = "window"
        provider.get_status.return_value = TerminalStatus.PROCESSING
        get_backend.return_value = backend
        provider_manager.get_provider.return_value = provider

        monitor = StatusMonitor()

        assert monitor.get_status("terminal-1") == TerminalStatus.PROCESSING
        assert monitor._last_status["terminal-1"] == TerminalStatus.PROCESSING
        provider.get_status.assert_called_once_with("provider-specific status fixture")
        publish.assert_called_once_with(
            "terminal.terminal-1.status", {"status": TerminalStatus.PROCESSING.value}
        )

    @patch("cli_agent_orchestrator.services.status_monitor.provider_manager")
    @patch("cli_agent_orchestrator.backends.registry.get_backend")
    @patch("cli_agent_orchestrator.services.status_monitor.bus.publish")
    def test_absent_or_malformed_observation_remains_unknown_and_is_not_published(
        self, publish, get_backend, provider_manager
    ):
        provider = MagicMock()
        provider.session_name = "session"
        provider.window_name = "window"
        provider_manager.get_provider.return_value = provider

        monitor = StatusMonitor()
        get_backend.return_value = _NonNativeHerdr(None)
        assert monitor.get_status("terminal-1") == TerminalStatus.UNKNOWN

        get_backend.return_value = _NonNativeHerdr({"not": "terminal text"})
        assert monitor.get_status("terminal-1") == TerminalStatus.UNKNOWN

        provider.get_status.assert_not_called()
        publish.assert_not_called()

    @patch("cli_agent_orchestrator.services.status_monitor.provider_manager")
    @patch("cli_agent_orchestrator.backends.registry.get_backend")
    @patch("cli_agent_orchestrator.services.status_monitor.bus.publish")
    def test_latch_rejects_a_late_non_native_processing_observation(
        self, publish, get_backend, provider_manager
    ):
        backend = _NonNativeHerdr("provider-specific status fixture")
        provider = MagicMock()
        provider.session_name = "session"
        provider.window_name = "window"
        provider.get_status.return_value = TerminalStatus.PROCESSING
        get_backend.return_value = backend
        provider_manager.get_provider.return_value = provider

        monitor = StatusMonitor()
        monitor._last_status["terminal-1"] = TerminalStatus.COMPLETED

        assert monitor.get_status("terminal-1") == TerminalStatus.COMPLETED
        assert monitor._last_status["terminal-1"] == TerminalStatus.COMPLETED
        publish.assert_not_called()
