"""Version-two compatibility is based on captured mini frames, not guessed v1 chrome."""

from unittest.mock import patch

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider


def provider():
    value = OpenCodeCliProvider("test1234", "test", "window", "reviewer", model="opencode/free")
    value._cli_major = 2
    return value


@pytest.mark.asyncio
async def test_v2_initialization_builds_config_for_selected_model(tmp_path, monkeypatch):
    import json
    from unittest.mock import AsyncMock, MagicMock

    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.utils import opencode_config

    monkeypatch.setattr(opencode_cli, "CAO_HOME_DIR", tmp_path)
    monkeypatch.setattr(opencode_cli, "OPENCODE_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(opencode_config, "read_config", lambda: {})
    monkeypatch.setattr(opencode_cli, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda: 2)
    backend = MagicMock()
    monkeypatch.setattr(opencode_cli, "get_backend", lambda: backend)
    instance = OpenCodeCliProvider(
        "test1234", "test", "window", "reviewer", model="opencode-go/longcat-2.5-preview-free"
    )
    monkeypatch.setattr(instance, "_wait_for_initial_ready", AsyncMock(return_value=True))
    assert await instance.initialize() is True
    config = json.loads((instance._v2_config_dir / "opencode.json").read_text())
    assert "longcat-2.5-preview-free" in config["providers"]["opencode-go"]["models"]
    assert "mini --standalone" in backend.send_keys.call_args.args[2]


def test_v2_launch_uses_private_mini_server():
    assert (
        "opencode mini --standalone --agent reviewer --model opencode/free"
        in provider()._build_launch_command()
    )


def test_v2_waits_for_paste_rendering_before_its_single_submit():
    instance = provider()
    assert instance.paste_submit_delay == 2.0
    assert instance.paste_enter_count == 1
    instance._cli_major = 1
    assert instance.paste_submit_delay == 1.0


@pytest.mark.parametrize("screen", [False, True])
def test_v2_idle_frame_is_recognized(screen):
    frame = "▫ oc mini v2.0.18\n┃ Ask anything, / for commands, @ for context…\nBuild · free · opencode · ctrl+p menu"
    instance = provider()
    status = (
        instance.get_status_from_screen(frame.splitlines())
        if screen
        else instance.get_status(frame)
    )
    assert status == TerminalStatus.IDLE


@pytest.mark.parametrize("screen", [False, True])
def test_v2_model_error_wins_over_idle_footer(screen):
    frame = "› requested answer\nModel unavailable: opencode/free\nBuild · free · 136ms\n┃\nBuild · free · opencode · ctrl+p menu"
    instance = provider()
    status = (
        instance.get_status_from_screen(frame.splitlines())
        if screen
        else instance.get_status(frame)
    )
    assert status == TerminalStatus.ERROR


def test_version_probe_supports_both_release_output_formats():
    from cli_agent_orchestrator.providers.opencode_cli import detect_opencode_major

    with patch("subprocess.check_output", return_value="opencode v2.0.18\n"):
        assert detect_opencode_major() == 2
    with patch("subprocess.check_output", return_value="1.18.32\n"):
        assert detect_opencode_major() == 1
    with patch("subprocess.check_output", return_value="3.0.0\n"):
        with pytest.raises(ValueError, match="unsupported"):
            detect_opencode_major()


@pytest.mark.parametrize("screen", [False, True])
def test_v2_live_completed_frame(screen):
    from pathlib import Path

    frame = (Path(__file__).parents[1] / "fixtures/opencode/v2-completed.txt").read_text()
    instance = provider()
    status = (
        instance.get_status_from_screen(frame.splitlines())
        if screen
        else instance.get_status(frame)
    )
    assert status == TerminalStatus.COMPLETED
    assert instance.extract_last_message_from_script(frame) == "CAO_V2_OK"


def test_v2_result_keeps_receipt_on_its_own_line():
    from pathlib import Path

    frame = (Path(__file__).parents[1] / "fixtures/opencode/v2-completed.txt").read_text()
    frame = frame.replace("\nCAO_V2_OK\n", "\nDone.\nCAO_RECEIPT:test1234\n")
    assert provider().extract_last_message_from_script(frame) == "Done.\nCAO_RECEIPT:test1234"


def test_v2_new_turn_does_not_extract_previous_completed_answer():
    from pathlib import Path

    frame = (Path(__file__).parents[1] / "fixtures/opencode/v2-completed.txt").read_text()
    frame = frame.replace("\n┃\n", "\n› new task\n\n┃\n").replace(
        "ctrl+p menu", "esc interrupt · ctrl+p menu"
    )
    assert provider().get_status(frame) == TerminalStatus.PROCESSING
    with pytest.raises(ValueError, match="completed"):
        provider().extract_last_message_from_script(frame)


def test_v2_config_translates_agent_tools_and_mcp_without_losing_denials(tmp_path):
    import json

    from cli_agent_orchestrator.utils.opencode_v2 import write_v2_configuration

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text(
        '---\ndescription: Reviewer\nmode: all\npermission:\n  "*": deny\n  read: allow\n  bash: deny\n  task: deny\n---\nKeep the receipt as the last line.\n'
    )
    destination = tmp_path / "v2"
    write_v2_configuration(
        destination,
        source_agents=agents,
        source_config={
            "tools": {"private_*": False},
            "mcp": {"cao": {"type": "local", "command": ["cao-mcp-server"], "enabled": True}},
            "agent": {"reviewer": {"tools": {"cao_*": True}}},
        },
        session_id="test1234",
    )
    data = json.loads((destination / "opencode.json").read_text())
    assert data["agents"]["reviewer"]["system"].strip() == "Keep the receipt as the last line."
    rules = data["agents"]["reviewer"]["permissions"]
    assert {"action": "shell", "resource": "*", "effect": "deny"} in rules
    assert {"action": "subagent", "resource": "*", "effect": "deny"} in rules
    assert {"action": "cao_*", "resource": "*", "effect": "allow"} in rules
    assert {"action": "private_*", "resource": "*", "effect": "deny"} in rules
    assert data["mcp"]["servers"]["cao"]["command"] == ["cao-mcp-server"]
    assert data["mcp"]["servers"]["cao"]["codemode"] is False
    assert destination.stat().st_mode & 0o077 == 0
    assert (destination / "opencode.json").stat().st_mode & 0o077 == 0


def test_v2_explicit_go_model_and_existing_login_stay_private(tmp_path):
    import json
    import shlex

    from cli_agent_orchestrator.utils.opencode_v2 import write_v2_configuration

    auth = tmp_path / "auth.json"
    credential = "test-key-with-$-and-'"
    auth.write_text(json.dumps({"opencode-go": {"type": "api", "key": credential}}))
    destination = tmp_path / "config"
    write_v2_configuration(
        destination,
        source_agents=tmp_path / "agents",
        source_config={},
        source_auth=auth,
        session_id="test1234",
        selected_model="opencode-go/longcat-2.5-preview-free",
    )
    data = json.loads((destination / "opencode.json").read_text())
    assert list(data["providers"]["opencode-go"]["models"]) == ["longcat-2.5-preview-free"]
    assert credential not in (destination / "opencode.json").read_text()
    assert (destination / "provider.env").read_text() == "export OPENCODE_API_KEY=" + shlex.quote(
        credential
    ) + "\n"
    assert (destination / "provider.env").stat().st_mode & 0o077 == 0
