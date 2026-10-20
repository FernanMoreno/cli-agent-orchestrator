"""Provider-side contracts for the shared interaction runtime."""

import sys
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.providers.mcp_interactions import (
    CAO_INTERACTION_CAPABILITY_FILE,
    is_bundled_cao_mcp_entry,
    overlay_interaction_capability_file,
)
from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV


class _Provider(BaseProvider):
    supports_interaction_mcp_overlay = True

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


def test_native_wait_is_a_bounded_manual_signal_with_no_answer_form():
    provider = _Provider("terminal-1", "session-1", "window-1")
    secret = "private-token-should-never-be-returned"
    screen = ["Sign in", secret, "Press Enter to continue"]

    observation = provider.observe_native_interaction(
        TerminalStatus.WAITING_USER_ANSWER,
        screen,
    )

    assert observation is not None
    assert observation.observation == "manual_signal"
    assert observation.dialog_kind == "unknown"
    assert observation.answer_capability == "none"
    assert observation.prompt is None
    assert observation.options == ()
    assert observation.manual_reason
    assert len(observation.dialog_fingerprint) == 64
    assert secret not in repr(asdict(observation))


def test_native_manual_signal_fingerprint_tracks_only_current_visible_screen():
    provider = _Provider("terminal-1", "session-1", "window-1")

    first = provider.observe_native_interaction(
        TerminalStatus.WAITING_USER_ANSWER,
        ["Permission required", "Allow once", "Deny"],
    )
    same = provider.observe_native_interaction(
        TerminalStatus.WAITING_USER_ANSWER,
        ["Permission required", "Allow once", "Deny"],
    )
    changed = provider.observe_native_interaction(
        TerminalStatus.WAITING_USER_ANSWER,
        ["Permission required", "Allow for this session", "Deny"],
    )

    assert first.dialog_fingerprint == same.dialog_fingerprint
    assert first.dialog_fingerprint != changed.dialog_fingerprint
    assert provider.observe_native_interaction(TerminalStatus.PROCESSING, ["same"]) is None


def test_provider_capability_file_is_private_to_bundled_cao_entry():
    bundled = {
        "command": "cao-mcp-server",
        "env": {CAO_INTERACTION_CAPABILITY_FILE: "/profile/forged", "OTHER": "kept"},
    }
    third_party = {
        "command": "private-mcp",
        "env": {CAO_INTERACTION_CAPABILITY_FILE: "/profile/forged", "OTHER": "kept"},
    }

    result = overlay_interaction_capability_file(
        {"cao": bundled, "third-party": third_party},
        "/srv/cao/private/source.cap",
    )

    assert result["cao"]["command"] == sys.executable
    assert result["cao"]["args"] == ["-m", "cli_agent_orchestrator.mcp_server.server"]
    assert result["cao"]["env"][CAO_INTERACTION_CAPABILITY_FILE] == "/srv/cao/private/source.cap"
    assert _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV not in result["cao"]["env"]
    assert CAO_INTERACTION_CAPABILITY_FILE not in result["third-party"]["env"]
    assert bundled["env"][CAO_INTERACTION_CAPABILITY_FILE] == "/profile/forged"
    assert result["cao"]["env"]["OTHER"] == "kept"


def test_explicit_pull_flow_overlay_sets_private_marker_only_on_bundled_server():
    bundled = {
        "command": ["cao-mcp-server"],
        "environment": {
            _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV: "profile-forged",
            "KEEP": "yes",
        },
    }
    unrelated = {
        "command": ["other-server"],
        "env": {_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV: "profile-forged"},
        "environment": {_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV: "also-forged"},
    }

    result = overlay_interaction_capability_file(
        {"cao-mcp-server": bundled, "unrelated": unrelated},
        "/srv/cao/private/source.cap",
        interaction_pull_flow_only=True,
    )

    assert result["cao-mcp-server"]["environment"] == {
        CAO_INTERACTION_CAPABILITY_FILE: "/srv/cao/private/source.cap",
        _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV: "1",
        "KEEP": "yes",
    }
    assert _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV not in result["unrelated"]["env"]
    assert _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV not in result["unrelated"]["environment"]
    assert bundled["environment"][_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV] == "profile-forged"
    assert unrelated["env"][_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV] == "profile-forged"


def test_overlay_always_strips_forged_pull_flow_marker_from_every_entry():
    result = overlay_interaction_capability_file(
        {
            "cao-mcp-server": {
                "command": ["cao-mcp-server"],
                "environment": {_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV: "1"},
            },
            "other": {
                "command": "other-server",
                "env": {_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV: "1"},
            },
        },
        None,
    )

    assert all(
        _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
        not in config.get("env", {})
        and _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
        not in config.get("environment", {})
        for config in result.values()
    )


def test_no_capability_file_strips_profile_supplied_internal_path():
    result = overlay_interaction_capability_file(
        {
            "cao": {
                "command": "cao-mcp-server",
                "env": {CAO_INTERACTION_CAPABILITY_FILE: "/profile/forged"},
            }
        },
        None,
    )

    assert CAO_INTERACTION_CAPABILITY_FILE not in result["cao"]["env"]


def test_server_name_and_module_substring_do_not_authorize_capability_injection():
    named_but_untrusted = {
        "cao-mcp-server": {"command": "evil-mcp", "env": {"KEEP": "yes"}},
        "other": {"command": "echo", "args": ["cli_agent_orchestrator.mcp_server.server"]},
    }

    result = overlay_interaction_capability_file(
        named_but_untrusted,
        "/srv/cao/private/source.cap",
    )

    assert all(
        CAO_INTERACTION_CAPABILITY_FILE not in config.get("env", {}) for config in result.values()
    )
    assert result["cao-mcp-server"]["env"] == {"KEEP": "yes"}


def test_profile_selected_same_named_executable_does_not_receive_capability(tmp_path):
    fake_script = tmp_path / "cao-mcp-server"
    fake_script.write_text("#!/bin/sh\necho untrusted\n", encoding="utf-8")

    result = overlay_interaction_capability_file(
        {"server": {"command": str(fake_script), "args": []}},
        "/srv/cao/private/source.cap",
    )

    assert CAO_INTERACTION_CAPABILITY_FILE not in result["server"].get("env", {})


def test_bare_bundle_with_profile_arguments_does_not_receive_capability():
    result = overlay_interaction_capability_file(
        {"cao": {"command": "cao-mcp-server", "args": ["--unreviewed-option"]}},
        "/srv/cao/private/source.cap",
    )

    assert result["cao"]["command"] == "cao-mcp-server"
    assert CAO_INTERACTION_CAPABILITY_FILE not in result["cao"].get("env", {})


def test_opencode_array_bundle_uses_environment_and_is_pinned_to_cao_module():
    result = overlay_interaction_capability_file(
        {
            "cao-mcp-server": {
                "type": "local",
                "command": ["cao-mcp-server"],
                "environment": {CAO_INTERACTION_CAPABILITY_FILE: "/profile/forged", "KEEP": "yes"},
            }
        },
        "/srv/cao/private/source.cap",
    )

    entry = result["cao-mcp-server"]
    assert entry["command"] == [sys.executable, "-m", "cli_agent_orchestrator.mcp_server.server"]
    assert "args" not in entry
    assert entry["environment"] == {
        CAO_INTERACTION_CAPABILITY_FILE: "/srv/cao/private/source.cap",
        "KEEP": "yes",
    }
    assert "env" not in entry


def test_opencode_array_substring_or_renamed_binary_does_not_receive_capability():
    assert not is_bundled_cao_mcp_entry("cao-mcp-server", {"command": ["echo", "cao-mcp-server"]})
    result = overlay_interaction_capability_file(
        {
            "cao-mcp-server": {
                "command": ["evil-mcp"],
                "environment": {CAO_INTERACTION_CAPABILITY_FILE: "/profile/forged", "KEEP": "yes"},
            },
            "other": {
                "command": ["cao-mcp-server", "--unreviewed-option"],
                "environment": {CAO_INTERACTION_CAPABILITY_FILE: "/profile/forged"},
            },
        },
        "/srv/cao/private/source.cap",
    )
    assert all(
        CAO_INTERACTION_CAPABILITY_FILE not in config.get("environment", {})
        for config in result.values()
    )


def test_opencode_exact_module_argv_is_a_bundled_array_form():
    command = [sys.executable, "-m", "cli_agent_orchestrator.mcp_server.server"]
    assert is_bundled_cao_mcp_entry("cao-mcp-server", {"command": command})

    result = overlay_interaction_capability_file(
        {"cao-mcp-server": {"command": command, "environment": {}}},
        "/srv/cao/private/source.cap",
    )
    assert result["cao-mcp-server"]["command"] == command
    assert (
        result["cao-mcp-server"]["environment"][CAO_INTERACTION_CAPABILITY_FILE]
        == "/srv/cao/private/source.cap"
    )


def test_canonical_python_module_command_is_valid_without_console_script(monkeypatch, tmp_path):
    from cli_agent_orchestrator.providers import mcp_interactions

    python_executable = tmp_path / "python"
    python_executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        mcp_interactions,
        "sys",
        SimpleNamespace(executable=str(python_executable), platform=sys.platform),
    )
    assert not python_executable.with_name("cao-mcp-server").exists()

    command = {
        "command": str(python_executable),
        "args": ["-m", "cli_agent_orchestrator.mcp_server.server"],
    }
    assert is_bundled_cao_mcp_entry("cao-mcp-server", command)
    result = overlay_interaction_capability_file(
        {"cao-mcp-server": command}, "/srv/cao/private/source.cap"
    )
    assert result["cao-mcp-server"]["env"][CAO_INTERACTION_CAPABILITY_FILE] == (
        "/srv/cao/private/source.cap"
    )


def test_profile_capability_requires_runtime_file_and_native_answer_stays_manual():
    provider = _Provider("terminal-1", "session-1", "window-1")

    before = provider.interaction_capabilities
    provider.set_interaction_capability_file("/private/capability")
    after = provider.interaction_capabilities

    assert before["explicit_channel"] == "unsupported"
    assert after["explicit_channel"] == "pull_explicit"
    assert after["native_detection"] == "manual_only"
    assert after["native_response"] == "manual_only"
    assert after["provider_version"] == "unknown"


@pytest.mark.parametrize(
    "enabled_tools",
    [
        [],
        ["cao_ask_user"],
        ["cao_ask_user", "cao_wait_for_user_answers"],
    ],
)
def test_incomplete_cao_mcp_allowlist_does_not_enable_interaction_source(enabled_tools):
    provider = _Provider("terminal-1", "session-1", "window-1")

    assert not provider.interaction_source_configured(
        {
            "cao-mcp-server": {
                "command": "cao-mcp-server",
                "enabled_tools": enabled_tools,
            }
        }
    )


def test_complete_cao_mcp_allowlist_enables_interaction_source():
    provider = _Provider("terminal-1", "session-1", "window-1")

    assert provider.interaction_source_configured(
        {
            "cao-mcp-server": {
                "command": "cao-mcp-server",
                "enabled_tools": [
                    "cao_ask_user",
                    "cao_wait_for_user_answers",
                    "cao_acknowledge_user_answers",
                ],
            }
        }
    )


def test_unfiltered_cao_mcp_preserves_default_interaction_tool_catalog():
    provider = _Provider("terminal-1", "session-1", "window-1")

    assert provider.interaction_source_configured({"cao-mcp-server": {"command": "cao-mcp-server"}})
