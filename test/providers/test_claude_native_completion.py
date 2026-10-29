"""Native completion cannot bypass current receipt and visible live-state fences."""

import hashlib
import json
import shlex
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers import claude_code as module
from cli_agent_orchestrator.providers.claude_completion_hook import normalize
from cli_agent_orchestrator.utils.native_completion_signal import (
    NativeCompletionChannel,
    publish_event,
)

pytestmark = pytest.mark.offline_database
NONCE = "CAO-TURN-RECEIPT-" + "b" * 32
GEN = "a" * 32
HASH = hashlib.sha256(NONCE.encode()).hexdigest()
RESULT = "Complete native text ñ\n" + NONCE
VIEW = RESULT + "\n✻ Crunched for 28s\n────────────\n❯\n────────────"


def provider(tmp_path):
    p = module.ClaudeCodeProvider("terminal", "session", "window")
    p._pending_turn_generation = GEN
    p._pending_turn_receipt_sha256 = HASH
    p._pending_turn_receipt = NONCE
    p._completion_channel = NativeCompletionChannel.create(tmp_path, "claude_code", "terminal")
    c = p._completion_channel
    publish_event(c.directory, c.binding, "start", "native-session")
    p.record_dispatched_message("prepared task with " + NONCE)
    publish_event(c.directory, c.binding, "stop", "native-session", RESULT)
    return p


def test_missing_head_native_text_needs_viewport_and_preserves_receipt_detector(tmp_path):
    p = provider(tmp_path)
    with pytest.raises(ValueError):
        p.extract_last_message_from_script(VIEW)
    assert p.get_status_from_screen(VIEW.splitlines()) == TerminalStatus.PROCESSING
    assert p.extract_post_turn_completion_result(VIEW) is None
    assert p.extract_viewport_completion_result(VIEW) == RESULT
    assert p.receipt_result_viewport_terminal_status(VIEW, RESULT) == TerminalStatus.COMPLETED
    assert p.receipt_result_terminal_status(VIEW, RESULT) is None
    p.mark_turn_receipt_result_verified()
    assert p.extract_viewport_completion_result(VIEW) is None
    assert p.get_status_from_screen(VIEW.splitlines()) == TerminalStatus.COMPLETED


@pytest.mark.parametrize(
    "view",
    [
        VIEW.replace(NONCE, "foreign receipt"),
        VIEW.replace("✻ Crunched for 28s", ""),
        VIEW.replace("────────────", ""),
        VIEW + "\n✻ Thinking… (3s)",
        VIEW + "\n✻ Waiting for 1 dynamic workflow to finish",
        VIEW + "\n↑/↓ to navigate · Enter to confirm · Esc to cancel",
        VIEW + "\n⚠ Usage limit reached · continuing automatically at 5:30pm · esc to cancel",
        VIEW + "\nAPI Error: 503 service unavailable",
    ],
)
def test_live_work_or_missing_finished_witness_rejects_native_event(tmp_path, view):
    p = provider(tmp_path)
    assert p.extract_viewport_completion_result(view) is None
    assert p.receipt_result_viewport_terminal_status(view, RESULT) is None


@pytest.mark.parametrize("change", ["generation", "receipt", "session", "predispatch", "missing"])
def test_native_identity_and_dispatch_are_required(tmp_path, change):
    p = provider(tmp_path)
    c = p._completion_channel
    if change == "generation":
        p._pending_turn_generation = "c" * 32
    elif change == "receipt":
        p._pending_turn_receipt_sha256 = "d" * 64
        p._pending_turn_receipt = "CAO-TURN-RECEIPT-" + "d" * 32
    elif change == "session":
        state = json.loads((c.directory / "state.json").read_text())
        state["session"] = "foreign"
        (c.directory / "state.json").write_text(json.dumps(state))
    elif change == "predispatch":
        p.record_dispatched_message("next input")
    else:
        c.close()
    assert p.extract_viewport_completion_result(VIEW) is None


@pytest.mark.parametrize(
    "change",
    [
        {"hook_event_name": "SubagentStop"},
        {"hook_event_name": "StopFailure"},
        {"stop_hook_active": True},
        {"stop_hook_active": 0},
        {"background_tasks": ["running"]},
        {"background_tasks": False},
        {"session_crons": ["pending"]},
    ],
)
def test_adapter_rejects_other_events_and_unfinished_native_work(change):
    packet = dict(
        hook_event_name="Stop",
        session_id="s",
        stop_hook_active=False,
        last_assistant_message=RESULT,
        background_tasks=[],
        session_crons=[],
    )
    packet.update(change)
    assert normalize(packet) is None


def test_cli_settings_are_private_mergeable_and_exact_callback_works_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "CAO_HOME_DIR", tmp_path)
    monkeypatch.setattr(module, "get_backend", lambda: TmuxBackend(client=MagicMock()))
    p = module.ClaudeCodeProvider("terminal 'with space", "session", "window")
    command = p._with_native_completion_settings("claude --model pinned --resume native-id", None)
    args = shlex.split(command)
    settings_path = args[args.index("--settings") + 1]
    settings = json.loads(open(settings_path).read())
    assert set(settings) == {"hooks"}
    assert set(settings["hooks"]) == {"SessionStart", "Stop"}
    assert args[:5] == ["claude", "--model", "pinned", "--resume", "native-id"]
    c = p._completion_channel
    for event in ("SessionStart", "Stop"):
        if event == "Stop":
            c.arm(GEN, HASH)
        packet = dict(
            hook_event_name=event,
            session_id="native-session",
            stop_hook_active=False,
            last_assistant_message=RESULT,
            background_tasks=[],
            session_crons=[],
        )
        callback = settings["hooks"][event][0]["hooks"][0]["command"]
        assert shlex.split(callback)[1:3] == ["-I", "-m"]
        (tmp_path / "json.py").write_text("raise RuntimeError('workspace shadow executed')")
        result = subprocess.run(
            shlex.split(callback),
            cwd=tmp_path,
            input=json.dumps(packet),
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    assert c.result(GEN, HASH) == RESULT
    assert c.directory.stat().st_mode & 0o777 == 0o700
    assert __import__("pathlib").Path(settings_path).stat().st_mode & 0o777 == 0o600
    old = c.directory
    p._with_native_completion_settings("claude --agent native", None)
    assert not old.exists()
    assert p._completion_channel.directory != old
    p.cleanup()
    assert p._completion_channel is None


def test_unsupported_backend_does_not_create_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "CAO_HOME_DIR", tmp_path)
    monkeypatch.setattr(module, "get_backend", MagicMock())
    p = module.ClaudeCodeProvider("terminal", "session", "window")
    assert p._with_native_completion_settings("claude", None) == "claude"
    assert not list(tmp_path.iterdir())


def test_incomplete_stop_envelope_is_not_completion():
    for missing in ("background_tasks", "session_crons"):
        packet = dict(
            hook_event_name="Stop",
            session_id="s",
            stop_hook_active=False,
            last_assistant_message=RESULT,
            background_tasks=[],
            session_crons=[],
        )
        del packet[missing]
        assert normalize(packet) is None


def test_failed_retirement_does_not_rearm_old_channel(tmp_path, monkeypatch):
    p = provider(tmp_path)
    c = p._completion_channel
    (c.directory / "unknown").write_text("preserve")
    monkeypatch.setattr(module, "get_backend", lambda: TmuxBackend(client=MagicMock()))
    assert p._with_native_completion_settings("claude", None) == "claude"
    publish_event(c.directory, c.binding, "start", "native-session")
    c.arm(GEN, HASH)
    publish_event(c.directory, c.binding, "stop", "native-session", RESULT)
    assert c.result(GEN, HASH) is None
    assert p.extract_viewport_completion_result(VIEW) is None
    assert (c.directory / "unknown").read_text() == "preserve"
