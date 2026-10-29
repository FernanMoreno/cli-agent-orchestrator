"""Replacement startup must not publish readiness under a cancelled receipt."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.providers.base import (
    cancelled_turn_initialization,
    cancelled_turn_startup_context,
)
from cli_agent_orchestrator.services import agent_step
from cli_agent_orchestrator.services import status_monitor as monitor_module
from cli_agent_orchestrator.services import turn_recovery_service as recovery
from cli_agent_orchestrator.utils import opencode_config

pytestmark = [pytest.mark.offline_database, pytest.mark.usefixtures("opencode_test_environment")]
IDLE = "tab agents  ctrl+p commands  • OpenCode"


@pytest.fixture
def startup(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode_cli, "CAO_HOME_DIR", tmp_path / "cao")
    provider = opencode_cli.OpenCodeCliProvider("1234abcd", "session", "window")
    provider._cli_major = 2
    provider._runtime_probe_complete = True
    provider._launch_runtime = ("/opt/bin/opencode", {"PATH": "/opt/bin:/usr/bin:/bin"})
    backend = Mock()
    backend.supports_event_inbox.return_value = False
    backend.get_history.return_value = IDLE
    commands = iter(["bash"])
    backend.get_pane_current_command.side_effect = lambda *_: next(commands, "opencode")
    monitor = Mock()
    monitor.observe_initial_screen_snapshot.return_value = TerminalStatus.IDLE
    monkeypatch.setattr(opencode_cli, "get_backend", lambda: backend)
    monkeypatch.setattr(opencode_cli, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(opencode_config, "read_config", lambda: {})
    monkeypatch.setattr(monitor_module, "status_monitor", monitor)
    yield provider, backend, monitor
    assert provider.cleanup() is True


@pytest.mark.asyncio
@pytest.mark.parametrize("cli_major", [1, 2])
async def test_real_initialization_does_not_publish_before_cancelled_receipt_release(
    startup, cli_major
):
    provider, backend, monitor = startup
    provider._cli_major = cli_major
    if cli_major == 1:
        # V1 requires an installed source even for a zero-tool control.
        agents = opencode_cli.OPENCODE_AGENTS_DIR
        agents.mkdir(parents=True, exist_ok=True)
        (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
        provider._agent_profile = "reviewer"
    original = provider.prepare_input("Do not resend the original task")
    pending = provider.pending_turn_receipt_state()
    with cancelled_turn_initialization(
        provider, provider.terminal_id, "session", "window", pending["generation"]
    ) as authority:
        assert await provider.initialize() is True
        assert provider.pending_turn_receipt_state() == pending
        monitor.observe_initial_screen_snapshot.assert_not_called()
        assert authority.readiness_observed.is_set()
    assert len(backend.send_keys.call_args_list) == 1
    assert original not in str(backend.send_keys.call_args_list)
    assert provider._cancelled_startup_shell_baseline is None


@pytest.mark.asyncio
async def test_cancelled_startup_generation_mismatch_refuses_before_launch(startup):
    provider, backend, _monitor = startup
    provider.prepare_input("Retain current generation")
    provider._runtime_probe_complete = False
    with (
        patch.object(provider, "_prepare_runtime") as prepare,
        patch.object(provider, "cleanup", wraps=provider.cleanup) as cleanup,
        cancelled_turn_initialization(
            provider, provider.terminal_id, "session", "window", "f" * 32
        ),
    ):
        with pytest.raises(ValueError, match="generation changed"):
            await provider.initialize()
        prepare.assert_not_called()
        cleanup.assert_not_called()
    backend.send_keys.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TerminalStatus.IDLE, TerminalStatus.COMPLETED])
async def test_ordinary_initialization_keeps_existing_status_publication(startup, status):
    provider, _backend, monitor = startup
    monitor.observe_initial_screen_snapshot.return_value = status
    assert await provider.initialize() is True
    monitor.observe_initial_screen_snapshot.assert_called_once_with(provider.terminal_id, IDLE)


@pytest.mark.parametrize("screen", [False, True])
def test_pending_receipt_never_appears_idle_to_an_ordinary_detector(startup, screen):
    provider, _backend, _monitor = startup
    provider._resolve_native_status = lambda _: None
    provider._resolve_buffer = lambda value: value
    provider.prepare_input("Keep this turn fenced")
    detected = (
        provider.get_status_from_screen(IDLE.splitlines()) if screen else provider.get_status(IDLE)
    )
    assert detected == TerminalStatus.PROCESSING
    provider.mark_turn_receipt_cancelled()
    assert (
        provider.get_status_from_screen(IDLE.splitlines()) if screen else provider.get_status(IDLE)
    ) == TerminalStatus.IDLE


@pytest.mark.parametrize(
    "frame,command,expected",
    [
        (IDLE, "opencode", TerminalStatus.IDLE),
        (IDLE, "bash", TerminalStatus.UNKNOWN),
        (IDLE, None, TerminalStatus.UNKNOWN),
        ("loading", "opencode", TerminalStatus.UNKNOWN),
        ("esc interrupt", "opencode", TerminalStatus.UNKNOWN),
        ("esc interrupt\n" + IDLE, "opencode", TerminalStatus.UNKNOWN),
        ("△ Permission required", "opencode", TerminalStatus.UNKNOWN),
        ("Error from provider", "opencode", TerminalStatus.ERROR),
        ("› Old prompt\n" + IDLE, "opencode", TerminalStatus.UNKNOWN),
        ("Build · model · 2s\n" + IDLE, "opencode", TerminalStatus.UNKNOWN),
        ("CAO-TURN-RECEIPT-" + "a" * 32 + "\n" + IDLE, "opencode", TerminalStatus.UNKNOWN),
    ],
)
def test_cancelled_probe_uses_bound_transport_and_never_publishes(
    startup, frame, command, expected
):
    provider, backend, monitor = startup
    backend.get_pane_current_command.side_effect = None
    backend.get_pane_current_command.return_value = command
    provider._cancelled_startup_shell_baseline = "bash"
    provider.prepare_input("Keep fenced")
    pending = provider.pending_turn_receipt_state()
    with cancelled_turn_initialization(
        provider, provider.terminal_id, "session", "window", pending["generation"]
    ) as authority:
        assert provider._cancelled_startup_status(frame) == expected
        assert authority.readiness_observed.is_set() == (expected == TerminalStatus.IDLE)
        assert provider.pending_turn_receipt_state() == pending
    monitor.observe_initial_screen_snapshot.assert_not_called()
    backend.send_keys.assert_not_called()


@pytest.mark.parametrize("mutation", ["no_context", "foreign_provider", "generation", "window"])
def test_cancelled_probe_refuses_foreign_authority(startup, mutation):
    provider, backend, _monitor = startup
    provider.prepare_input("Owned task")
    pending = provider.pending_turn_receipt_state()
    provider._cancelled_startup_shell_baseline = "bash"
    backend.get_pane_current_command.side_effect = None
    backend.get_pane_current_command.return_value = "opencode"
    owner = (
        opencode_cli.OpenCodeCliProvider("5678abcd", "session", "window")
        if mutation == "foreign_provider"
        else provider
    )
    if mutation == "no_context":
        assert provider._cancelled_startup_status(IDLE) == TerminalStatus.UNKNOWN
        return
    generation = "f" * 32 if mutation == "generation" else pending["generation"]
    with cancelled_turn_initialization(owner, owner.terminal_id, "session", "window", generation):
        if mutation == "window":
            provider.window_name = "foreign"
            with pytest.raises(ValueError, match="binding changed"):
                provider._cancelled_startup_status(IDLE)
        else:
            assert provider._cancelled_startup_status(IDLE) == TerminalStatus.UNKNOWN
    assert cancelled_turn_startup_context(provider) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refresh_fails,unsupported_event_reset", [(False, False), (True, False), (False, True)]
)
async def test_recovery_finishes_before_refresh_with_actual_opencode_initialization(
    startup, monkeypatch, refresh_fails, unsupported_event_reset
):
    from types import SimpleNamespace

    provider, backend, monitor = startup
    original = provider.prepare_input("Never resend this task")
    pending = provider.pending_turn_receipt_state()
    events = []
    backend.supports_event_inbox.return_value = False
    backend.reset_window.side_effect = lambda *_a, **_k: events.append("reset")
    monkeypatch.setattr(recovery, "get_backend", lambda: backend)
    monkeypatch.setattr(recovery, "status_monitor", monitor)
    monkeypatch.setattr(recovery, "ensure_terminal_is_not_work_owned", lambda *_: None)
    monkeypatch.setattr(
        recovery,
        "get_terminal_metadata",
        lambda *_: {
            "tmux_session": "session",
            "tmux_window": "window",
            "provider": "opencode_cli",
        },
    )
    monkeypatch.setattr(recovery.provider_manager, "get_provider", lambda *_: provider)
    monkeypatch.setattr(
        recovery, "begin_terminal_turn_cancellation", lambda *_: events.append("fenced") or True
    )
    monkeypatch.setattr(recovery, "transition_native_child", lambda *_: None)
    monkeypatch.setattr(recovery, "get_turn", lambda *_: {"state": "cancelled"})
    identity = "e" * 32
    monkeypatch.setattr(
        recovery,
        "fifo_manager",
        SimpleNamespace(
            prepare_reader_restart=Mock(return_value=identity),
            restart_reader=Mock(return_value=identity),
            activate_reader_restart=Mock(return_value=True),
        ),
    )

    def finish(*_):
        assert provider.pending_turn_receipt_state() == pending
        assert provider.get_status_from_screen(IDLE.splitlines()) == TerminalStatus.PROCESSING
        assert provider._cancelled_startup_shell_baseline is None
        events.append("finish")
        return True

    def refresh(*_):
        assert provider.pending_turn_receipt_state() is None
        assert cancelled_turn_startup_context(provider) is None
        assert provider.get_status_from_screen(IDLE.splitlines()) == TerminalStatus.IDLE
        events.append("refresh")
        if refresh_fails:
            raise RuntimeError("advisory refresh failed")

    monkeypatch.setattr(recovery, "finish_terminal_turn_cancellation", finish)
    monitor.observe_initial_screen_snapshot.side_effect = refresh
    if unsupported_event_reset:
        from cli_agent_orchestrator.backends.base import TerminalBackend, TerminalBackendError
        from cli_agent_orchestrator.backends.herdr_backend import HerdrBackend

        assert HerdrBackend.reset_window is TerminalBackend.reset_window
        backend.supports_event_inbox.return_value = True
        backend.reset_window.side_effect = lambda *args, **kwargs: TerminalBackend.reset_window(
            backend, *args, **kwargs
        )
        with patch.object(provider, "initialize", new=AsyncMock()) as initialize:
            with pytest.raises(TerminalBackendError, match="confirmed pane reset"):
                await recovery._cancel_locked(provider.terminal_id, pending["generation"])
            initialize.assert_not_awaited()
        assert provider.pending_turn_receipt_state() == pending
        assert events == ["fenced"]
        monitor.publish_receipt_reconciliation.assert_called_once_with(provider.terminal_id)
        return
    assert await recovery._cancel_locked(provider.terminal_id, pending["generation"]) == {
        "state": "cancelled"
    }
    assert events == ["fenced", "reset", "finish", "refresh"]
    assert provider.pending_turn_receipt_state() is None
    assert len(backend.send_keys.call_args_list) == 1
    assert original not in str(backend.send_keys.call_args_list)
    monitor.publish_receipt_reconciliation.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_contexts_stay_provider_bound_in_concurrent_threads(startup):
    provider, backend, monitor = startup
    other = opencode_cli.OpenCodeCliProvider("5678abcd", "session", "window")
    providers = (provider, other)
    gates = (asyncio.Event(), asyncio.Event())
    backend.get_pane_current_command.side_effect = None
    backend.get_pane_current_command.return_value = "opencode"

    async def observe(index):
        value = providers[index]
        value._cli_major = 2
        value._cancelled_startup_shell_baseline = "bash"
        value.prepare_input("Owned pending task")
        pending = value.pending_turn_receipt_state()
        with cancelled_turn_initialization(
            value, value.terminal_id, "session", "window", pending["generation"]
        ) as authority:
            gates[index].set()
            await gates[1 - index].wait()
            assert (
                await asyncio.to_thread(value._cancelled_startup_status, IDLE)
                == TerminalStatus.IDLE
            )
            assert (
                await asyncio.to_thread(providers[1 - index]._cancelled_startup_status, IDLE)
                == TerminalStatus.UNKNOWN
            )
            assert authority.readiness_observed.is_set()
            assert value.get_status_from_screen(IDLE.splitlines()) == TerminalStatus.PROCESSING
        assert cancelled_turn_startup_context(value) is None
        assert value.pending_turn_receipt_state() == pending

    await asyncio.gather(observe(0), observe(1))
    monitor.observe_initial_screen_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_initialization_error_cleans_runtime_and_keeps_receipt(startup):
    provider, backend, monitor = startup
    provider.prepare_input("Retain fenced task")
    pending = provider.pending_turn_receipt_state()
    backend.get_history.return_value = "Error from provider"
    with cancelled_turn_initialization(
        provider, provider.terminal_id, "session", "window", pending["generation"]
    ) as authority:
        with pytest.raises(RuntimeError, match="reported an error during initial readiness"):
            await provider.initialize()
        assert not authority.readiness_observed.is_set()
    assert provider.pending_turn_receipt_state() == pending
    assert provider._owned_v2_config_dir is None
    assert provider._cancelled_startup_shell_baseline is None
    monitor.observe_initial_screen_snapshot.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_available", [False, True])
async def test_receipt_fenced_idle_still_has_a_bounded_completion_path(
    startup, monkeypatch, receipt_available
):
    provider, backend, _monitor = startup
    provider._resolve_native_status = lambda _: None
    provider._resolve_buffer = lambda value: value
    provider.prepare_input("Retain until verified or timed out")
    pending = provider.pending_turn_receipt_state()
    monkeypatch.setattr(agent_step, "_interaction_wait_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(
        agent_step.status_monitor, "get_status", lambda _: provider.get_status(IDLE)
    )
    probe = Mock(return_value="verified current result" if receipt_available else None)
    settle = Mock(return_value="verified current result")
    monkeypatch.setattr(agent_step.terminal_service, "probe_post_turn_receipt_result", probe)
    monkeypatch.setattr(agent_step.terminal_service, "settle_post_turn_receipt_result", settle)
    monkeypatch.setattr(agent_step, "_COMPLETION_POLL_INTERVAL", 0.001)
    if receipt_available:
        assert (
            await agent_step._wait_for_completion(
                provider.terminal_id, timeout=1, prompt_redelivery=False
            )
            == "verified current result"
        )
        assert probe.call_count == 2
        settle.assert_called_once_with(provider.terminal_id, "verified current result")
    else:
        with pytest.raises(agent_step.StepExecutionError) as error:
            await agent_step._wait_for_completion(
                provider.terminal_id, timeout=0, prompt_redelivery=False
            )
        assert error.value.kind == "timeout"
        settle.assert_not_called()
    assert provider.pending_turn_receipt_state() == pending
    backend.send_keys.assert_not_called()
