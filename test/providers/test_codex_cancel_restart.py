"""A replacement's startup is distinct from its cancelled task's completion."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers import codex
from cli_agent_orchestrator.providers.base import (
    cancelled_turn_initialization,
    cancelled_turn_startup_context,
)
from cli_agent_orchestrator.services import status_monitor as monitor_module
from cli_agent_orchestrator.services import turn_recovery_service as recovery

pytestmark = pytest.mark.offline_database
FRAME = Path(__file__).with_name("fixtures") / "codex_0164_cancel_replacement.json"


def frame_text():
    return json.loads(FRAME.read_text())["frame"]


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh_fails", [False, True])
async def test_cancel_initializes_actual_codex_without_releasing_the_old_receipt_early(
    monkeypatch, tmp_path, refresh_fails
):
    provider = codex.CodexProvider("1234abcd", "session", "window")
    provider._resolve_native_status = lambda _: None
    provider._resolve_buffer = lambda value: value
    task = provider.prepare_input("Original task must never be resent")
    pending = provider.pending_turn_receipt_state()
    frame = frame_text()
    events = []
    backend = Mock()
    backend.supports_event_inbox.return_value = False
    monkeypatch.setattr(recovery, "FIFO_DIR", tmp_path)
    capture_identity = "e" * 32
    fifo = SimpleNamespace(
        prepare_reader_restart=Mock(return_value=capture_identity),
        restart_reader=Mock(return_value=capture_identity),
        activate_reader_restart=Mock(return_value=True),
    )
    monkeypatch.setattr(recovery, "fifo_manager", fifo)
    backend.get_history.return_value = frame
    commands = iter(["bash"])
    backend.get_pane_current_command.side_effect = lambda *_: next(commands, "codex")
    backend.reset_window.side_effect = lambda *_a, **_k: events.append("reset")
    monitor = Mock()

    def refresh(*_):
        assert provider.pending_turn_receipt_state() is None
        assert cancelled_turn_startup_context(provider) is None
        events.append("refresh")
        if refresh_fails:
            raise RuntimeError("advisory refresh unavailable")

    monitor.observe_initial_screen_snapshot.side_effect = refresh
    monkeypatch.setattr(monitor_module, "status_monitor", monitor)
    monkeypatch.setattr(recovery, "status_monitor", monitor)
    monkeypatch.setattr(codex, "get_backend", lambda: backend)
    monkeypatch.setattr(recovery, "get_backend", lambda: backend)
    monkeypatch.setattr(recovery, "ensure_terminal_is_not_work_owned", lambda *_: None)
    monkeypatch.setattr(
        recovery,
        "get_terminal_metadata",
        lambda *_: {
            "tmux_session": "session",
            "tmux_window": "window",
            "provider": "codex",
        },
    )
    monkeypatch.setattr(recovery.provider_manager, "get_provider", lambda *_: provider)
    monkeypatch.setattr(recovery, "begin_terminal_turn_cancellation", lambda *_: True)
    monkeypatch.setattr(recovery, "transition_native_child", lambda *_: None)
    monkeypatch.setattr(recovery, "get_turn", lambda *_: {"state": "cancelled"})

    def finish(*_):
        assert provider.pending_turn_receipt_state() == pending
        events.append("finish")
        return True

    monkeypatch.setattr(recovery, "finish_terminal_turn_cancellation", finish)
    monkeypatch.setattr(codex, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(provider, "_handle_trust_prompt", AsyncMock())
    monkeypatch.setattr(provider, "_build_codex_command", lambda: "codex")

    async def wait(_tid, targets, **kwargs):
        assert provider.pending_turn_receipt_state() == pending
        assert cancelled_turn_startup_context(provider).generation == pending["generation"]
        assert provider.get_status(frame) == TerminalStatus.PROCESSING
        events.append("initialize_wait")
        probe = kwargs.get("initialization_probe")
        detected = probe() if probe is not None else provider.get_status(frame)
        return detected in targets

    monkeypatch.setattr(codex, "wait_until_status", wait)
    assert await recovery._cancel_locked("1234abcd", pending["generation"]) == {
        "state": "cancelled"
    }
    assert events == ["reset", "initialize_wait", "finish", "refresh"]
    assert provider.pending_turn_receipt_state() is None
    assert provider.get_status(frame) == TerminalStatus.IDLE
    monitor.publish_receipt_reconciliation.assert_not_called()
    fifo.prepare_reader_restart.assert_called_once_with("1234abcd")
    fifo.restart_reader.assert_called_once_with("1234abcd", capture_identity)
    fifo.activate_reader_restart.assert_called_once_with("1234abcd", capture_identity)
    assert all(task not in str(call) for call in backend.send_keys.call_args_list)


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "history_before_banner",
        "user",
        "assistant",
        "working",
        "login",
        "trust",
        "update",
        "error",
        "quota",
        "draft",
        "no_banner",
        "shell",
        "unknown_process",
        "no_context",
        "wrong_provider",
        "wrong_generation",
        "changed_window",
    ],
)
def test_scoped_startup_requires_a_fresh_empty_transport_not_task_completion(monkeypatch, mutation):
    provider = codex.CodexProvider("1234abcd", "session", "window")
    provider._resolve_native_status = lambda _: None
    provider._resolve_buffer = lambda value: value
    provider.shell_baseline = "bash"
    provider.prepare_input("Old task")
    pending = provider.pending_turn_receipt_state()
    text = frame_text()
    insertion = {
        "user": "› New task\n",
        "assistant": "• Old answer\n",
        "working": "• Working (1s • esc to interrupt)\n",
        "login": "Sign in with ChatGPT\nPress enter to continue\n",
        "trust": "Do you trust the contents of this directory?\nPress enter to continue\n",
        "update": "Update available!\n1. Update now\n2. Skip\n3. Skip until next version\n",
        "error": "■ unexpected status 401\n",
        "quota": "■ You’ve hit your usage limit.\n",
    }.get(mutation, "")
    text = text.replace("› Ask Codex", insertion + "› Ask Codex")
    if mutation == "history_before_banner":
        text = "› Old task\n• Old answer\n" + text
    elif mutation == "draft":
        text = text.replace("› Ask Codex to do anything", "› Unsubmitted draft")
    elif mutation == "no_banner":
        text = text.replace(">_ OpenAI Codex", "Quoted interface")
    backend = Mock()
    backend.get_history.return_value = text
    backend.get_pane_current_command.return_value = (
        "bash" if mutation == "shell" else None if mutation == "unknown_process" else "codex"
    )
    monkeypatch.setattr(codex, "get_backend", lambda: backend)
    other = codex.CodexProvider("5678abcd", "session", "window")
    owner = other if mutation == "wrong_provider" else provider
    generation = pending["generation"] if mutation != "wrong_generation" else "f" * 32
    if mutation == "no_context":
        assert provider._cancelled_startup_status() == TerminalStatus.UNKNOWN
    else:
        with cancelled_turn_initialization(
            owner, owner.terminal_id, "session", "window", generation
        ) as startup:
            if mutation == "changed_window":
                provider.window_name = "foreign"
                with pytest.raises(ValueError, match="binding changed"):
                    provider._cancelled_startup_status()
            else:
                result = provider._cancelled_startup_status()
                assert result == (
                    TerminalStatus.IDLE
                    if mutation in {None, "history_before_banner"}
                    else TerminalStatus.UNKNOWN
                )
                assert startup.readiness_observed.is_set() == (result == TerminalStatus.IDLE)
            assert provider.pending_turn_receipt_state() == pending
    assert cancelled_turn_startup_context(owner) is None
    assert provider.pending_turn_receipt_state() == pending
    assert provider.get_status(frame_text()) == TerminalStatus.PROCESSING


@pytest.mark.asyncio
async def test_context_is_task_local_copied_to_threads_and_never_changes_normal_detection():
    providers = [codex.CodexProvider(tid, "s", "w") for tid in ["1234abcd", "5678abcd"]]
    gates = [asyncio.Event(), asyncio.Event()]

    async def run(index):
        provider = providers[index]
        provider._resolve_native_status = lambda _: None
        provider._resolve_buffer = lambda value: value
        provider.prepare_input("Retain old receipt")
        pending = provider.pending_turn_receipt_state()
        with cancelled_turn_initialization(
            provider, provider.terminal_id, "s", "w", pending["generation"]
        ):
            gates[index].set()
            await gates[1 - index].wait()
            assert cancelled_turn_startup_context(providers[1 - index]) is None
            assert await asyncio.to_thread(cancelled_turn_startup_context, provider) is not None
            assert (
                await asyncio.to_thread(provider.get_status, frame_text())
                == TerminalStatus.PROCESSING
            )
        assert cancelled_turn_startup_context(provider) is None
        assert provider.pending_turn_receipt_state() == pending

    await asyncio.gather(run(0), run(1))


@pytest.mark.parametrize("exception", [TimeoutError, RuntimeError, asyncio.CancelledError])
def test_context_always_releases_authority_without_releasing_receipt(exception):
    provider = codex.CodexProvider("1234abcd", "s", "w")
    provider.prepare_input("Retain old receipt")
    pending = provider.pending_turn_receipt_state()
    with pytest.raises(exception):
        with cancelled_turn_initialization(provider, "1234abcd", "s", "w", pending["generation"]):
            raise exception()
    assert cancelled_turn_startup_context(provider) is None
    assert provider.pending_turn_receipt_state() == pending


@pytest.mark.asyncio
async def test_scoped_wait_requires_probe_even_when_monitor_has_cached_idle(monkeypatch):
    from cli_agent_orchestrator.utils.terminal import wait_until_status

    monitor = Mock()
    monitor.get_status.return_value = TerminalStatus.IDLE
    monkeypatch.setattr(monitor_module, "status_monitor", monitor)
    probe = Mock(side_effect=[TerminalStatus.UNKNOWN, TerminalStatus.IDLE])
    assert await wait_until_status(
        "1234abcd", {TerminalStatus.IDLE}, timeout=1, polling_interval=0, initialization_probe=probe
    )
    assert probe.call_count == 2
    monitor.get_status.assert_not_called()
    monitor.observe_initial_viewport.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_receipt", [False, True])
async def test_wrong_startup_generation_fails_before_launch_and_retains_receipt(
    monkeypatch, missing_receipt
):
    provider = codex.CodexProvider("1234abcd", "s", "w")
    if not missing_receipt:
        provider.prepare_input("Retain old receipt")
    pending = provider.pending_turn_receipt_state()
    backend_factory = Mock()
    monkeypatch.setattr(codex, "get_backend", backend_factory)
    with cancelled_turn_initialization(provider, "1234abcd", "s", "w", "f" * 32):
        with pytest.raises(ValueError, match="generation changed"):
            await provider.initialize()
    backend_factory.assert_not_called()
    assert provider.pending_turn_receipt_state() == pending
    assert cancelled_turn_startup_context(provider) is None


@pytest.mark.asyncio
async def test_native_event_backend_keeps_existing_initialization_authority(monkeypatch):
    provider = codex.CodexProvider("1234abcd", "s", "w")
    provider.prepare_input("Retain old receipt")
    pending = provider.pending_turn_receipt_state()
    backend = Mock()
    backend.supports_event_inbox.return_value = True
    backend.get_pane_current_command.return_value = "native"
    monkeypatch.setattr(codex, "get_backend", lambda: backend)
    monkeypatch.setattr(codex, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(provider, "_handle_trust_prompt", AsyncMock())
    monkeypatch.setattr(provider, "_build_codex_command", lambda: "codex")

    async def wait(_tid, targets, **kwargs):
        assert "initialization_probe" not in kwargs
        assert targets == {
            TerminalStatus.IDLE,
            TerminalStatus.COMPLETED,
            TerminalStatus.WAITING_USER_ANSWER,
        }
        return True

    monkeypatch.setattr(codex, "wait_until_status", wait)
    with cancelled_turn_initialization(
        provider, "1234abcd", "s", "w", pending["generation"]
    ) as startup:
        assert await provider.initialize()
        assert not startup.readiness_observed.is_set()
    assert provider.pending_turn_receipt_state() == pending
