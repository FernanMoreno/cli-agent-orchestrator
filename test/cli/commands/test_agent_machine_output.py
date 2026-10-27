"""Agent CLI automation/TTY contracts; all service implementations are doubles."""

import importlib
import json
import sys
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from cli_agent_orchestrator.mcp_server.models import HandoffResult

commands = importlib.import_module("cli_agent_orchestrator.cli.commands.agent")
pytestmark = pytest.mark.offline_database


class TtyRunner(CliRunner):
    @contextmanager
    def isolation(self, *args, **kwargs):
        with super().isolation(*args, **kwargs) as result:
            sys.stdout.isatty = lambda: True
            yield result


CASES = [
    ("assign", ["developer", "controlled task"], "_assign_impl", {"terminal_id": "controlled"}),
    ("handoff", ["developer", "controlled task"], "_handoff_impl", {}),
    ("send-message", ["controlled message"], "_send_message_impl", {}),
    ("status", ["controlled"], "_status_impl", {"status": "idle"}),
    ("result", ["controlled"], "_result_impl", {"output": "line one\n中文"}),
    (
        "verify",
        ["controlled"],
        "_verify_impl",
        {"state": "pending", "allowed_actions": ["verify", "cancel"]},
    ),
    ("cancel", ["controlled"], "_cancel_impl", {"deleted": False}),
    (
        "children",
        [],
        "_list_native_children_impl",
        {"children": [{"id": "controlled", "state": "pending"}]},
    ),
    (
        "join",
        ["controlled"],
        "_join_native_child_impl",
        {"child_id": "controlled", "state": "pending"},
    ),
]


@pytest.mark.parametrize("name,args,implementation,fields", CASES, ids=[c[0] for c in CASES])
@pytest.mark.parametrize("success", [False, True], ids=["failure", "success"])
@pytest.mark.parametrize("tty", [False, True], ids=["pipe", "tty"])
@pytest.mark.parametrize("explicit_json", [False, True], ids=["default", "json"])
def test_operation_payload_exit_and_output_mode(
    monkeypatch, name, args, implementation, fields, success, tty, explicit_json
):
    payload = {"success": success, "message": "controlled result", **fields}
    threads = []
    real_thread = commands.threading.Thread

    def tracked_thread(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(commands.threading, "Thread", tracked_thread)
    if name == "handoff":
        result_model = HandoffResult(
            success=success,
            message=payload["message"],
            terminal_id="controlled",
            output="line one\n中文",
        )
        payload = result_model.model_dump()

        async def handoff(*args, **kwargs):
            kwargs["on_terminal_id"]("controlled")
            return result_model

        monkeypatch.setattr(commands, implementation, handoff)
    else:
        monkeypatch.setattr(commands, implementation, lambda *a, **k: payload)
    result = (TtyRunner() if tty else CliRunner()).invoke(
        commands.agent, [name, *args, *(["--json"] if explicit_json else [])]
    )
    assert result.exit_code == (0 if success else 1), result.output
    if explicit_json or not tty:
        document, end = json.JSONDecoder().raw_decode(result.stdout)
        assert document == payload
        assert result.stdout[end:].strip() == ""
        assert "Waiting for" not in result.stdout
        assert "still waiting" not in result.stdout
    else:
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.stdout)
        assert payload["message"] in result.stdout
        if payload.get("output"):
            assert "output:\n" + payload["output"] in result.stdout
    if name == "handoff":
        assert "terminal_id: controlled" in result.stderr
        assert payload["output"] not in result.stderr
        assert bool(threads) is (tty and not explicit_json)
        assert all(not thread.is_alive() for thread in threads)
    else:
        assert not threads


@pytest.mark.parametrize("tty", [False, True], ids=["pipe", "tty"])
@pytest.mark.parametrize("explicit_json", [False, True], ids=["default", "json"])
def test_interrupted_handoff_preserves_exit_stderr_and_thread_cleanup(
    monkeypatch, tty, explicit_json
):
    threads = []
    real_thread = commands.threading.Thread

    def tracked_thread(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        threads.append(thread)
        return thread

    async def interrupted(*args, **kwargs):
        kwargs["on_terminal_id"]("controlled")
        raise KeyboardInterrupt

    monkeypatch.setattr(commands.threading, "Thread", tracked_thread)
    monkeypatch.setattr(commands, "_handoff_impl", interrupted)
    result = (TtyRunner() if tty else CliRunner()).invoke(
        commands.agent,
        ["handoff", "developer", "controlled task", *(["--json"] if explicit_json else [])],
    )
    assert result.exit_code == 130
    assert "terminal_id: controlled" in result.stderr
    assert "Interrupted" in result.stderr
    assert "may still be running" in result.stderr
    assert all(not thread.is_alive() for thread in threads)
    if explicit_json or not tty:
        assert result.stdout == ""
        assert not threads
    else:
        assert "Waiting for" in result.stdout
        assert len(threads) == 1
