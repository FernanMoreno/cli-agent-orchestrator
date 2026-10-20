"""Version-two compatibility is based on captured mini frames, not guessed v1 chrome."""

from unittest.mock import AsyncMock, patch

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
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents/reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    monkeypatch.setattr(
        opencode_cli,
        "resolve_opencode_runtime",
        lambda: ("/opt/bin/opencode", {"PATH": "/opt/bin:/usr/bin:/bin"}),
    )
    monkeypatch.setattr(opencode_config, "read_config", lambda: {})
    monkeypatch.setattr(opencode_cli, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda runtime: 2)
    backend = MagicMock()
    monkeypatch.setattr(opencode_cli, "get_backend", lambda: backend)
    instance = OpenCodeCliProvider(
        "test1234", "test", "window", "reviewer", model="opencode-go/longcat-2.5-preview-free"
    )
    monkeypatch.setattr(instance, "_wait_for_initial_ready", AsyncMock(return_value=True))
    wait_for_mcp = AsyncMock(return_value=False)
    monkeypatch.setattr(instance, "_wait_for_v2_cao_mcp_ready", wait_for_mcp)
    assert await instance.initialize() is True
    config = json.loads((instance._v2_config_dir / "opencode.json").read_text())
    assert "longcat-2.5-preview-free" in config["providers"]["opencode-go"]["models"]
    assert "mini --standalone" in backend.send_keys.call_args.args[2]
    assert instance._interaction_preflight_ok is False
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"
    wait_for_mcp.assert_not_awaited()


@pytest.mark.parametrize("disabled_flag", [{"disabled": True}, {"enabled": False}])
def test_interaction_source_ignores_disabled_bundled_server(disabled_flag, monkeypatch):
    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.utils import opencode_config

    monkeypatch.setattr(
        opencode_cli,
        "resolve_opencode_runtime",
        lambda: ("/opt/bin/opencode", {"PATH": "/opt/bin:/usr/bin:/bin"}),
    )
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda runtime: 2)
    monkeypatch.setattr(
        opencode_config,
        "read_config",
        lambda: {
            "mcp": {
                "servers": {
                    "cao-mcp-server": {
                        "type": "local",
                        "command": ["cao-mcp-server"],
                        **disabled_flag,
                    }
                }
            }
        },
    )
    instance = OpenCodeCliProvider(
        "test1234", "test", "window", "reviewer", allowed_tools=["@cao-mcp-server"]
    )

    assert instance.interaction_source_configured({}) is False


@pytest.mark.asyncio
async def test_v2_requested_interaction_requires_runtime_capability(tmp_path, monkeypatch):
    """An active native server must not start a source-less child process."""
    from unittest.mock import AsyncMock, MagicMock

    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.utils import opencode_config

    monkeypatch.setattr(opencode_cli, "CAO_HOME_DIR", tmp_path / "cao")
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    monkeypatch.setattr(opencode_cli, "OPENCODE_AGENTS_DIR", agents)
    monkeypatch.setattr(
        opencode_cli,
        "resolve_opencode_runtime",
        lambda: ("/opt/bin/opencode", {"PATH": "/opt/bin:/usr/bin:/bin"}),
    )
    monkeypatch.setattr(
        opencode_config,
        "read_config",
        lambda: {
            "mcp": {
                "servers": {
                    "cao-mcp-server": {
                        "type": "local",
                        "command": ["cao-mcp-server"],
                    }
                }
            }
        },
    )
    monkeypatch.setattr(opencode_cli, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda runtime: 2)
    backend = MagicMock()
    monkeypatch.setattr(opencode_cli, "get_backend", lambda: backend)

    instance = OpenCodeCliProvider(
        "test1234", "test", "window", "reviewer", allowed_tools=["@cao-mcp-server"]
    )
    with pytest.raises(ValueError, match="CAO MCP capability is unavailable"):
        await instance.initialize()

    backend.send_keys.assert_not_called()


@pytest.mark.asyncio
async def test_v2_requested_interaction_requires_native_agent_profile(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from cli_agent_orchestrator.providers import opencode_cli

    monkeypatch.setattr(opencode_cli, "wait_for_shell", AsyncMock(return_value=True))
    backend = MagicMock()
    monkeypatch.setattr(opencode_cli, "get_backend", lambda: backend)

    instance = OpenCodeCliProvider("test1234", "test", "window", allowed_tools=["@cao-mcp-server"])
    with pytest.raises(ValueError, match="requires a native agent profile"):
        await instance.initialize()

    backend.send_keys.assert_not_called()


@pytest.mark.asyncio
async def test_interaction_source_reuses_one_runtime_probe(tmp_path, monkeypatch):
    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.utils import opencode_config

    calls = {"resolve": 0, "detect": 0}

    def resolve():
        calls["resolve"] += 1
        return ("/opt/bin/opencode", {"PATH": "/opt/bin:/usr/bin:/bin"})

    def detect(runtime):
        calls["detect"] += 1
        return 2

    monkeypatch.setattr(opencode_cli, "resolve_opencode_runtime", resolve)
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", detect)
    monkeypatch.setattr(
        opencode_config,
        "read_config",
        lambda: {
            "mcp": {
                "servers": {
                    "cao-mcp-server": {
                        "type": "local",
                        "command": ["cao-mcp-server"],
                    }
                }
            }
        },
    )
    instance = OpenCodeCliProvider("test1234", "test", "window", "")
    assert instance.interaction_source_configured({}) is True
    assert calls == {"resolve": 1, "detect": 1}
    monkeypatch.setattr(instance, "_initialize_validated", AsyncMock(return_value=True))
    assert await instance.initialize() is True
    assert calls == {"resolve": 1, "detect": 1}


def test_v2_configuration_preserves_native_timeout_units_and_shape(tmp_path):
    """OpenCode v2 timeouts are nested positive millisecond fields.

    The v2.0.18 schema defines startup/catalog/execution in
    `packages/schema/src/mcp.ts`; do not flatten a v1 integer timeout into
    this shape. See https://github.com/anomalyco/opencode/blob/v2.0.18/packages/schema/src/mcp.ts
    """
    import json

    from cli_agent_orchestrator.utils.opencode_v2 import write_v2_configuration

    agents = tmp_path / "agents"
    agents.mkdir()
    destination = tmp_path / "private-config"
    native_timeout = {"startup": 60_000, "catalog": 60_000, "execution": 65_000}
    source_config = {
        "mcp": {
            "timeout": native_timeout,
            "servers": {
                "cao-mcp-server": {
                    "type": "local",
                    "command": ["cao-mcp-server"],
                    "timeout": native_timeout,
                }
            },
        }
    }

    write_v2_configuration(
        destination,
        source_agents=agents,
        source_config=source_config,
        session_id="timeout-shape-test",
    )

    config = json.loads((destination / "opencode.json").read_text(encoding="utf-8"))
    assert config["mcp"]["timeout"] == native_timeout
    assert config["mcp"]["servers"]["cao-mcp-server"]["timeout"] == native_timeout
    assert isinstance(config["mcp"]["servers"]["cao-mcp-server"]["timeout"], dict)


def test_v2_configuration_translates_resource_scoped_v1_permissions(tmp_path):
    import json

    from cli_agent_orchestrator.utils.opencode_v2 import write_v2_configuration

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text(
        "---\ndescription: Reviewer\n---\nReview.\n", encoding="utf-8"
    )
    # A profile that is not selected for this terminal must still be copied
    # without making the whole v2 configuration invalid. OpenCode v1 permits
    # resource-scoped permission maps; v2 carries the resource as a rule field.
    (agents / "neighbor.md").write_text(
        "---\n"
        "description: Neighbor\n"
        "permission:\n"
        "  bash:\n"
        '    "git *": allow\n'
        "  read:\n"
        '    "*": false\n'
        "---\n"
        "Neighbor.\n",
        encoding="utf-8",
    )
    destination = tmp_path / "v2"

    write_v2_configuration(
        destination,
        source_agents=agents,
        source_config={},
        session_id="nested-permission",
    )

    profiles = json.loads((destination / "opencode.json").read_text(encoding="utf-8"))["agents"]
    assert {
        "action": "shell",
        "resource": "git *",
        "effect": "allow",
    } in profiles[
        "neighbor"
    ]["permissions"]
    assert {
        "action": "read",
        "resource": "*",
        "effect": "deny",
    } in profiles[
        "neighbor"
    ]["permissions"]


@pytest.mark.asyncio
async def test_v2_initialization_isolates_data_and_preserves_auth_for_mcp_readiness(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock, MagicMock

    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.utils import opencode_config

    monkeypatch.setattr(opencode_cli, "CAO_HOME_DIR", tmp_path / "cao")
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    monkeypatch.setattr(opencode_cli, "OPENCODE_AGENTS_DIR", agents)
    monkeypatch.setattr(
        opencode_cli,
        "resolve_opencode_runtime",
        lambda: ("/opt/bin/opencode", {"PATH": "/opt/bin:/usr/bin:/bin"}),
    )
    auth = tmp_path / "source-data/opencode/auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text('{"opencode-go":{"type":"oauth","refresh":"local"}}')
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "source-data"))
    monkeypatch.setattr(
        opencode_config,
        "read_config",
        lambda: {
            "mcp": {
                "servers": {
                    "cao-mcp-server": {
                        "type": "local",
                        "command": ["cao-mcp-server"],
                    }
                }
            },
            "agent": {"reviewer": {"tools": {"cao-mcp-server*": True}}},
        },
    )
    monkeypatch.setattr(opencode_cli, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda runtime: 2)
    backend = MagicMock()
    monkeypatch.setattr(opencode_cli, "get_backend", lambda: backend)
    instance = OpenCodeCliProvider(
        "test1234", "test", "window", "reviewer", allowed_tools=["@cao-mcp-server"]
    )
    instance.set_interaction_capability_file(tmp_path / "capability.json")
    monkeypatch.setattr(instance, "_wait_for_initial_ready", AsyncMock(return_value=True))
    wait_for_mcp = AsyncMock(return_value=True)
    monkeypatch.setattr(instance, "_wait_for_v2_cao_mcp_ready", wait_for_mcp)

    assert await instance.initialize() is True
    assert instance.interaction_capabilities["explicit_channel"] == "pull_explicit"

    data_home = instance._v2_data_home
    auth_link = data_home / "opencode/auth.json"
    assert auth_link.is_file()
    assert not auth_link.is_symlink()
    assert auth_link.read_bytes() == auth.read_bytes()
    assert auth_link.stat().st_mode & 0o777 == 0o600
    assert auth.read_text(encoding="utf-8") == '{"opencode-go":{"type":"oauth","refresh":"local"}}'
    assert data_home.stat().st_mode & 0o777 == 0o700
    assert "XDG_DATA_HOME=" + str(data_home) in backend.send_keys.call_args.args[2]
    assert "--log-level info --print-logs" in backend.send_keys.call_args.args[2]
    assert "2> " + str(data_home / "mcp-startup.log") in backend.send_keys.call_args.args[2]
    assert (data_home / "mcp-startup.log").stat().st_mode & 0o777 == 0o600
    wait_for_mcp.assert_awaited_once()
    assert wait_for_mcp.await_args.kwargs["timeout"] > 0
    owned_dir = instance._owned_v2_config_dir
    instance.cleanup()
    assert owned_dir is not None and not owned_dir.exists()


@pytest.mark.asyncio
async def test_v2_initialization_uses_private_db_without_legacy_auth(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.utils import opencode_config

    monkeypatch.setattr(opencode_cli, "CAO_HOME_DIR", tmp_path / "cao")
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    monkeypatch.setattr(opencode_cli, "OPENCODE_AGENTS_DIR", agents)
    monkeypatch.setattr(
        opencode_cli,
        "resolve_opencode_runtime",
        lambda: ("/opt/bin/opencode", {"PATH": "/opt/bin:/usr/bin:/bin"}),
    )
    data_home = tmp_path / "source-data"
    credential_db = tmp_path / "operator-state" / "opencode.db"
    credential_db.parent.mkdir(parents=True)
    credential_db.write_bytes(b"v2-credential-state")
    credential_db.chmod(0o644)
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("OPENCODE_DB", str(credential_db))
    monkeypatch.setattr(
        opencode_config,
        "read_config",
        lambda: {
            "mcp": {
                "servers": {
                    "cao-mcp-server": {
                        "type": "local",
                        "command": ["cao-mcp-server"],
                    }
                }
            },
            "agent": {"reviewer": {"tools": {"cao-mcp-server*": True}}},
        },
    )
    monkeypatch.setattr(opencode_cli, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda runtime: 2)
    backend = MagicMock()
    monkeypatch.setattr(opencode_cli, "get_backend", lambda: backend)
    instance = OpenCodeCliProvider(
        "test1234", "test", "window", "reviewer", allowed_tools=["@cao-mcp-server"]
    )
    instance.set_interaction_capability_file(tmp_path / "capability.json")
    monkeypatch.setattr(instance, "_wait_for_initial_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(instance, "_wait_for_v2_cao_mcp_ready", AsyncMock(return_value=True))

    assert await instance.initialize() is True

    data_path = instance._v2_data_home / "opencode"
    assert (data_path / "opencode.db").read_bytes() == credential_db.read_bytes()
    assert (data_path / "opencode.db").stat().st_mode & 0o777 == 0o600
    assert not (data_path / "auth.json").exists()
    assert (instance._v2_config_dir / "provider.env").read_text(encoding="utf-8") == ""
    assert instance._v2_data_home.stat().st_mode & 0o777 == 0o700
    launch_command = backend.send_keys.call_args.args[2]
    assert "OPENCODE_DB=" + str(data_path / "opencode.db") in launch_command
    assert str(credential_db) not in launch_command
    instance.cleanup()


@pytest.mark.asyncio
async def test_v2_initialization_cleans_private_config_after_mcp_preflight_failure(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock, MagicMock

    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.utils import opencode_config

    cao_home = tmp_path / "cao"
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    monkeypatch.setattr(opencode_cli, "CAO_HOME_DIR", cao_home)
    monkeypatch.setattr(opencode_cli, "OPENCODE_AGENTS_DIR", agents)
    monkeypatch.setattr(
        opencode_cli,
        "resolve_opencode_runtime",
        lambda: ("/opt/bin/opencode", {"PATH": "/opt/bin:/usr/bin:/bin"}),
    )
    monkeypatch.setattr(
        opencode_config,
        "read_config",
        lambda: {
            "mcp": {
                "servers": {
                    "cao-mcp-server": {
                        "type": "local",
                        "command": ["cao-mcp-server"],
                    }
                }
            }
        },
    )
    monkeypatch.setattr(opencode_cli, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda runtime: 2)
    backend = MagicMock()
    monkeypatch.setattr(opencode_cli, "get_backend", lambda: backend)

    instance = OpenCodeCliProvider(
        "test1234", "test", "window", "reviewer", allowed_tools=["@cao-mcp-server"]
    )
    instance.set_interaction_capability_file(tmp_path / "capability.json")
    with pytest.raises(ValueError, match="has no CAO MCP grant"):
        await instance.initialize()

    assert instance._owned_v2_config_dir is None
    assert not list((cao_home / "opencode-v2").glob("terminal-*/provider.env"))
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"
    backend.send_keys.assert_not_called()


@pytest.mark.asyncio
async def test_v2_mcp_readiness_waits_for_its_private_connection_log(tmp_path):
    import asyncio

    instance = provider()
    instance._v2_data_home = tmp_path / "data"
    instance._v2_cao_mcp_server_name = "cao-mcp-server"
    log = instance._v2_data_home / "mcp-startup.log"
    log.parent.mkdir(parents=True)
    log.write_text("INFO starting mini\n")

    pending = asyncio.create_task(instance._wait_for_v2_cao_mcp_ready(timeout=1.0))
    await asyncio.sleep(0.02)
    log.write_text(
        'timestamp=2026-10-05T12:00:00Z level=INFO message="mcp connected" '
        "server=cao-mcp-server tools=72 role=server\n"
    )

    assert await pending is True


@pytest.mark.asyncio
async def test_v2_mcp_readiness_times_out_without_a_connection(tmp_path):
    instance = provider()
    instance._v2_data_home = tmp_path / "data"
    instance._v2_cao_mcp_server_name = "cao-mcp-server"

    assert await instance._wait_for_v2_cao_mcp_ready(timeout=0.01) is False


@pytest.mark.asyncio
async def test_v2_mcp_readiness_does_not_accept_another_server(tmp_path):
    instance = provider()
    instance._v2_data_home = tmp_path / "data"
    instance._v2_cao_mcp_server_name = "cao-mcp-server"
    log = instance._v2_data_home / "mcp-startup.log"
    log.parent.mkdir(parents=True)
    log.write_text('level=INFO message="mcp connected" server=cao-mcp-server-other tools=72\n')

    assert await instance._wait_for_v2_cao_mcp_ready(timeout=0.01) is False


@pytest.mark.parametrize(
    "config",
    [
        {"mcp": {"cao-mcp-server": {"command": ["cao-mcp-server"], "enabled": False}}},
        {"mcp": {"servers": {"cao-mcp-server": {"command": ["cao-mcp-server"], "disabled": True}}}},
        {"mcp": {"custom-cao": {"command": ["cao-mcp-server"], "enabled": False}}},
    ],
)
def test_disabled_cao_mcp_is_not_awaited(config):
    from cli_agent_orchestrator.providers.opencode_cli import _find_cao_mcp_server_name

    assert _find_cao_mcp_server_name(config) is None


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


def test_version_probe_supports_both_release_output_formats(monkeypatch):
    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.providers.opencode_cli import detect_opencode_major

    monkeypatch.setattr(
        opencode_cli,
        "resolve_opencode_runtime",
        lambda: ("/opt/bin/opencode", {"PATH": "/opt/bin:/usr/bin:/bin"}),
    )

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


def test_v2_config_normalizes_v1_mcp_grants_to_v2_tool_names(tmp_path):
    import json

    from cli_agent_orchestrator.utils.opencode_v2 import write_v2_configuration

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    destination = tmp_path / "v2"
    write_v2_configuration(
        destination,
        source_agents=agents,
        source_config={
            "mcp": {
                "cao-mcp-server": {
                    "type": "local",
                    "command": ["cao-mcp-server"],
                    "enabled": True,
                }
            },
            "agent": {"reviewer": {"tools": {"cao-mcp-server*": True}}},
        },
        session_id="test1234",
    )

    config = json.loads((destination / "opencode.json").read_text())
    assert {
        "action": "cao-mcp-server_*",
        "resource": "*",
        "effect": "allow",
    } in config["agents"][
        "reviewer"
    ]["permissions"]


@pytest.mark.parametrize("native_codemode", [None, True])
def test_v2_flat_mcp_forces_codemode_off_for_bundled_cao_server(tmp_path, native_codemode):
    import json

    from cli_agent_orchestrator.utils.opencode_v2 import (
        validate_v2_mcp_configuration,
        write_v2_configuration,
    )

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    cao_server = {"type": "local", "command": ["cao-mcp-server"], "enabled": True}
    if native_codemode is not None:
        cao_server["codemode"] = native_codemode
    destination = tmp_path / "v2"
    write_v2_configuration(
        destination,
        source_agents=agents,
        source_config={
            "mcp": {"cao-mcp-server": cao_server},
            "agent": {"reviewer": {"tools": {"cao-mcp-server*": True}}},
        },
        session_id="flat-codemode",
    )

    config = json.loads((destination / "opencode.json").read_text(encoding="utf-8"))
    assert config["mcp"]["servers"]["cao-mcp-server"]["codemode"] is False
    validate_v2_mcp_configuration(destination, agent_name="reviewer", server_name="cao-mcp-server")


def test_v2_mcp_preflight_rejects_server_without_selected_agent_grant(tmp_path):
    import pytest

    from cli_agent_orchestrator.utils.opencode_v2 import (
        validate_v2_mcp_configuration,
        write_v2_configuration,
    )

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    destination = tmp_path / "v2"
    write_v2_configuration(
        destination,
        source_agents=agents,
        source_config={
            "mcp": {
                "cao-mcp-server": {
                    "type": "local",
                    "command": ["cao-mcp-server"],
                    "enabled": True,
                }
            }
        },
        session_id="test1234",
    )

    with pytest.raises(ValueError, match="has no CAO MCP grant"):
        validate_v2_mcp_configuration(
            destination,
            agent_name="reviewer",
            server_name="cao-mcp-server",
        )


def test_v2_mcp_preflight_rejects_empty_agent_name(tmp_path):
    from cli_agent_orchestrator.utils.opencode_v2 import validate_v2_mcp_configuration

    destination = _write_v2_grant(
        tmp_path,
        [_rule("cao-mcp-server_*", "allow")],
    )

    with pytest.raises(ValueError, match="requires a native agent profile"):
        validate_v2_mcp_configuration(destination, agent_name="", server_name="cao-mcp-server")


def test_v2_mcp_preflight_accepts_selected_agent_grant(tmp_path):
    import json

    from cli_agent_orchestrator.utils.opencode_v2 import (
        validate_v2_mcp_configuration,
        write_v2_configuration,
    )

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    destination = tmp_path / "v2"
    write_v2_configuration(
        destination,
        source_agents=agents,
        source_config={
            "mcp": {
                "cao-mcp-server": {
                    "type": "local",
                    "command": ["cao-mcp-server"],
                    "enabled": True,
                }
            },
            "agent": {"reviewer": {"tools": {"cao-mcp-server*": True}}},
        },
        session_id="test1234",
    )

    validate_v2_mcp_configuration(
        destination,
        agent_name="reviewer",
        server_name="cao-mcp-server",
    )
    assert json.loads((destination / "opencode.json").read_text())["mcp"]["servers"]


@pytest.mark.parametrize(
    ("action", "effect"),
    [
        ("cao-mcp-server_*", "deny"),
        ("cao-mcp-server_*", "ask"),
        ("*", "deny"),
    ],
)
def test_v2_mcp_preflight_rejects_a_later_non_allow_rule(tmp_path, action, effect):
    import json

    import pytest

    from cli_agent_orchestrator.utils.opencode_v2 import (
        validate_v2_mcp_configuration,
        write_v2_configuration,
    )

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    destination = tmp_path / "v2"
    write_v2_configuration(
        destination,
        source_agents=agents,
        source_config={
            "mcp": {
                "cao-mcp-server": {
                    "type": "local",
                    "command": ["cao-mcp-server"],
                    "enabled": True,
                }
            },
            "agent": {"reviewer": {"tools": {"cao-mcp-server*": True}}},
        },
        session_id="test1234",
    )
    config_path = destination / "opencode.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["agents"]["reviewer"]["permissions"].append(
        {"action": action, "resource": "*", "effect": effect}
    )
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="has no CAO MCP grant"):
        validate_v2_mcp_configuration(
            destination,
            agent_name="reviewer",
            server_name="cao-mcp-server",
        )


def test_v2_mcp_preflight_does_not_attribute_a_prefixed_server_grant(tmp_path):
    from cli_agent_orchestrator.utils.opencode_v2 import (
        validate_v2_mcp_configuration,
        write_v2_configuration,
    )

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    destination = tmp_path / "v2"
    write_v2_configuration(
        destination,
        source_agents=agents,
        source_config={
            "mcp": {
                "cao": {"type": "local", "command": ["cao-mcp-server"], "enabled": True},
                "cao-other": {
                    "type": "local",
                    "command": ["other-server"],
                    "enabled": True,
                },
            },
            "agent": {"reviewer": {"tools": {"cao-other*": True}}},
        },
        session_id="test1234",
    )

    with pytest.raises(ValueError, match="has no CAO MCP grant"):
        validate_v2_mcp_configuration(
            destination,
            agent_name="reviewer",
            server_name="cao",
        )


@pytest.mark.parametrize("native_codemode", [None, True])
def test_v2_nested_mcp_forces_codemode_off_for_bundled_cao_server(tmp_path, native_codemode):
    import json

    from cli_agent_orchestrator.utils.opencode_v2 import (
        validate_v2_mcp_configuration,
        write_v2_configuration,
    )

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    cao_server = {"type": "local", "command": ["cao-mcp-server"], "extra": {"keep": 1}}
    if native_codemode is not None:
        cao_server["codemode"] = native_codemode
    other_server = {"type": "local", "command": ["other-server"], "codemode": True}
    source_config = {
        "mcp": {"servers": {"cao-mcp-server": cao_server, "other": other_server}},
        "agent": {"reviewer": {"tools": {"cao-mcp-server*": True}}},
    }
    destination = tmp_path / "v2"
    write_v2_configuration(
        destination, source_agents=agents, source_config=source_config, session_id="t1"
    )

    servers = json.loads((destination / "opencode.json").read_text())["mcp"]["servers"]
    assert servers["cao-mcp-server"]["codemode"] is False
    assert servers["cao-mcp-server"]["extra"] == {"keep": 1}
    assert servers["other"] == other_server
    assert source_config["mcp"]["servers"]["cao-mcp-server"].get("codemode") == native_codemode
    validate_v2_mcp_configuration(destination, agent_name="reviewer", server_name="cao-mcp-server")


def _write_v2_grant(tmp_path, permissions, *, codemode=False):
    import json

    destination = tmp_path / "v2"
    destination.mkdir()
    server = {"type": "local", "command": ["cao-mcp-server"]}
    if codemode is not None:
        server["codemode"] = codemode
    config = {
        "mcp": {"servers": {"cao-mcp-server": server}},
        "agents": {"reviewer": {"permissions": permissions}},
    }
    (destination / "opencode.json").write_text(json.dumps(config), encoding="utf-8")
    return destination


def _rule(action, effect, resource="*"):
    return {"action": action, "resource": resource, "effect": effect}


_CAO_TOOLS = (
    "cao_ask_user",
    "cao_wait_for_user_answers",
    "cao_acknowledge_user_answers",
)


@pytest.mark.parametrize("codemode", [None, True, "false", 0])
def test_v2_mcp_preflight_requires_codemode_exactly_false(tmp_path, codemode):
    from cli_agent_orchestrator.utils.opencode_v2 import validate_v2_mcp_configuration

    destination = _write_v2_grant(tmp_path, [_rule("cao-mcp-server_*", "allow")], codemode=codemode)
    with pytest.raises(ValueError, match="must disable codemode"):
        validate_v2_mcp_configuration(
            destination, agent_name="reviewer", server_name="cao-mcp-server"
        )


@pytest.mark.parametrize(
    "permissions",
    [
        [_rule("*", "deny"), _rule("execute", "deny")]
        + [_rule("cao-mcp-server_" + tool, "allow") for tool in _CAO_TOOLS],
        [_rule("*", "allow")],
        [_rule("*", "deny"), _rule("cao-mcp-server_cao_*", "allow")],
        [_rule("*", "deny"), _rule("cao-mcp-server_*", "allow"), _rule("other_*", "deny")],
        [_rule("cao-mcp-server_*", "allow"), _rule("cao-mcp-server_cao_ask_user", "allow", "x")],
        [_rule("cao-mcp-server_*", "allow"), _rule("cao-mcp-server_cao_ask_user", "deny", "x")],
    ],
)
def test_v2_mcp_preflight_accepts_all_required_tools(tmp_path, permissions):
    from cli_agent_orchestrator.utils.opencode_v2 import validate_v2_mcp_configuration

    destination = _write_v2_grant(tmp_path, permissions)
    validate_v2_mcp_configuration(destination, agent_name="reviewer", server_name="cao-mcp-server")


@pytest.mark.parametrize(
    "permissions",
    [
        [_rule("*", "deny"), _rule("cao-mcp-server_cao_ask_user", "allow")],
        [_rule("*", "deny")]
        + [_rule("cao-mcp-server_" + tool, "allow") for tool in _CAO_TOOLS[:2]],
        [_rule("cao-mcp-server_*", "allow"), _rule("cao-*", "deny")],
        [_rule("cao-mcp-server_*", "allow"), _rule("c*", "ask")],
        [_rule("cao-mcp-server_*", "allow"), _rule("*_cao_ask_user", "deny")],
        [
            _rule("cao-mcp-server_*", "allow"),
            _rule("cao-mcp-server_cao_wait_for_?ser_answers", "deny"),
        ],
        [_rule("*", "deny"), _rule("cao-mcp-server_*", "allow", "some-resource")],
        [_rule("*", "deny"), _rule("cao_mcp_server_*", "allow")],
        [{"action": "cao-mcp-server_*", "effect": "allow"}],
    ],
)
def test_v2_mcp_preflight_rejects_partial_or_overridden_grants(tmp_path, permissions):
    from cli_agent_orchestrator.utils.opencode_v2 import validate_v2_mcp_configuration

    destination = _write_v2_grant(tmp_path, permissions)
    with pytest.raises(ValueError, match="has no CAO MCP grant"):
        validate_v2_mcp_configuration(
            destination, agent_name="reviewer", server_name="cao-mcp-server"
        )


def test_v2_mcp_preflight_rejects_normalized_server_name_collision(tmp_path):
    import pytest

    from cli_agent_orchestrator.utils.opencode_v2 import (
        validate_v2_mcp_configuration,
        write_v2_configuration,
    )

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    destination = tmp_path / "v2"
    write_v2_configuration(
        destination,
        source_agents=agents,
        source_config={
            "mcp": {
                "cao.mcp": {
                    "type": "local",
                    "command": ["cao-mcp-server"],
                    "enabled": True,
                },
                "cao_mcp": {
                    "type": "local",
                    "command": ["other-server"],
                    "enabled": True,
                },
            }
        },
        session_id="test1234",
    )

    with pytest.raises(ValueError, match="server names are ambiguous"):
        validate_v2_mcp_configuration(
            destination,
            agent_name="reviewer",
            server_name="cao.mcp",
        )


def test_v2_mcp_action_normalizer_preserves_upstream_hyphen_and_selector_contract():
    from cli_agent_orchestrator.utils.opencode_v2 import _normalize_mcp_actions

    rules = [
        {"action": "cao-mcp-server_cao-tool-name", "resource": "*", "effect": "allow"},
        {"action": "cao-mcp-server_cao-*?", "resource": "*", "effect": "allow"},
        {"action": "cao-mcp-server*", "resource": "*", "effect": "allow"},
        {"action": "shell", "resource": "*", "effect": "deny"},
        {"action": "mcp_*_tool-name", "resource": "*", "effect": "allow"},
    ]

    normalized = _normalize_mcp_actions(rules, {"cao-mcp-server", "mcp", "mcp_*"})

    assert [rule["action"] for rule in normalized] == [
        "cao-mcp-server_cao-tool-name",
        "cao-mcp-server_cao-*?",
        "cao-mcp-server_*",
        "shell",
        "mcp___tool-name",
    ]


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


def test_v2_configuration_avoids_legacy_auth_when_private_state_exists(tmp_path):
    import json

    from cli_agent_orchestrator.utils.opencode_v2 import write_v2_configuration

    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps({"opencode-go": {"type": "api", "key": "stale-key"}}),
        encoding="utf-8",
    )
    credentials = tmp_path / "opencode.db"
    credentials.write_bytes(b"private-credential-state")

    destination = tmp_path / "config"
    write_v2_configuration(
        destination,
        source_agents=tmp_path / "agents",
        source_config={},
        source_auth=auth,
        source_credential_db=credentials,
        session_id="db-credential-test",
        selected_model="opencode-go/longcat-2.5-preview-free",
    )

    provider_env = (destination / "provider.env").read_text(encoding="utf-8")
    assert provider_env == ""
    assert "stale-key" not in provider_env


def test_private_credential_state_bundle_copies_sidecars_owner_only(tmp_path):
    from cli_agent_orchestrator.providers.opencode_cli import _copy_private_state_bundle

    source = tmp_path / "operator" / "opencode.db"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"main-state")
    (tmp_path / "operator" / "opencode.db-wal").write_bytes(b"wal-state")
    (tmp_path / "operator" / "opencode.db-shm").write_bytes(b"shm-state")
    destination = tmp_path / "private" / "opencode"
    source_snapshot = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in source.parent.glob("opencode.db*")
    }

    _copy_private_state_bundle(source, destination)

    for name, (payload, _) in source_snapshot.items():
        if name.endswith("-shm"):
            continue
        copied = destination / name
        assert copied.read_bytes() == payload
        assert copied.stat().st_mode & 0o777 == 0o600
    assert not (destination / "opencode.db-shm").exists()
    assert source_snapshot == {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in source.parent.glob("opencode.db*")
    }


def test_private_credential_state_bundle_normalizes_custom_source_basename(tmp_path):
    from cli_agent_orchestrator.providers.opencode_cli import _copy_private_state_bundle

    source = tmp_path / "operator" / "operator.sqlite"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"main-state")
    (tmp_path / "operator" / "operator.sqlite-wal").write_bytes(b"wal-state")
    destination = tmp_path / "private" / "opencode"

    _copy_private_state_bundle(source, destination)

    assert (destination / "opencode.db").read_bytes() == b"main-state"
    assert (destination / "opencode.db-wal").read_bytes() == b"wal-state"
    assert not (destination / "operator.sqlite").exists()
    assert not (destination / "operator.sqlite-wal").exists()


def test_private_credential_state_bundle_rejects_symlink_source(tmp_path):
    from cli_agent_orchestrator.providers.opencode_cli import _copy_private_state_bundle

    real_source = tmp_path / "real.db"
    real_source.write_bytes(b"state")
    source = tmp_path / "opencode.db"
    source.symlink_to(real_source)

    with pytest.raises(RuntimeError, match="cannot be inspected safely"):
        _copy_private_state_bundle(source, tmp_path / "private")


def test_private_credential_state_bundle_propagates_cancellation(tmp_path, monkeypatch):
    import asyncio

    from cli_agent_orchestrator.providers import opencode_cli

    source = tmp_path / "operator" / "opencode.db"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"state")
    calls = {"copy": 0}

    def cancelled_copy(*_args, **_kwargs):
        calls["copy"] += 1
        raise asyncio.CancelledError()

    monkeypatch.setattr(opencode_cli, "_copy_private_state_file", cancelled_copy)

    with pytest.raises(asyncio.CancelledError):
        opencode_cli._copy_private_state_bundle(source, tmp_path / "private")

    assert calls["copy"] == 1


@pytest.mark.parametrize("screen", [False, True])
def test_real_go_upstream_model_unavailable_is_error(screen):
    from pathlib import Path

    frame = (Path(__file__).parent / "fixtures/opencode-v2-unavailable-model-real.txt").read_text()
    instance = provider()
    status = (
        instance.get_status_from_screen(frame.splitlines())
        if screen
        else instance.get_status(frame)
    )
    assert status == TerminalStatus.ERROR


@pytest.mark.parametrize("screen", [False, True])
def test_quoted_go_model_unavailable_does_not_override_completed_answer(screen):
    frame = (
        "Upstream request failed: Model is unavailable.\n"
        "This is a documented example; the actual request succeeded.\n"
        "reviewer · Kimi K3 · 2s\n┃\nreviewer · Kimi K3 · OpenCode Go · ctrl+p menu"
    )
    instance = provider()
    status = (
        instance.get_status_from_screen(frame.splitlines())
        if screen
        else instance.get_status(frame)
    )
    assert status == TerminalStatus.COMPLETED
