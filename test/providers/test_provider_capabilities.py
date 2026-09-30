"""Operation discovery describes source-backed transport, never runtime authority."""

import json
import shlex

import pytest

import cli_agent_orchestrator.providers.manager as manager_module
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.providers.catalog import registered_provider_descriptors
from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider
from cli_agent_orchestrator.providers.codex import CodexProvider
from cli_agent_orchestrator.providers.copilot_cli import CopilotCliProvider
from cli_agent_orchestrator.providers.manager import ProviderManager


def descriptor(name):
    return next(item for item in registered_provider_descriptors() if item.name == name)


def operation(item, name="launch", **kwargs):
    assert hasattr(item, "operation_capabilities"), "operation preflight descriptors missing"
    return item.operation_capabilities(name, **kwargs)


def test_base_requires_explicit_operation_and_never_promises_runtime_support():
    assert hasattr(BaseProvider, "operation_capabilities"), "base preflight contract missing"
    result = BaseProvider.operation_capabilities("launch")
    assert result["schema_version"] == 1
    assert result["availability"] == "not_probed"
    assert result["model_selection"] == "unverified"
    assert result["effort_selection"] == "unverified"
    assert result["enforcement"] == "unverified"
    assert result["enforcement_owner"] == "backend"
    assert result["continuity"] == "unverified"
    with pytest.raises(ValueError, match="operation"):
        BaseProvider.operation_capabilities("not-an-operation")


def test_legacy_boolean_discovery_and_order_remain_unchanged():
    registry = registered_provider_descriptors()
    assert [item.name for item in registry] == [
        "kiro_cli",
        "claude_code",
        "codex",
        "hermes",
        "kimi_cli",
        "copilot_cli",
        "opencode_cli",
        "cursor_cli",
        "antigravity_cli",
        "gemini_cli",
        "omp",
        "grok_cli",
        "mcode",
    ]
    for item in registry:
        before = item.capabilities()
        operation(item)
        assert item.capabilities() == before
        assert set(before) == {
            "native_children",
            "sibling_messages",
            "terminal_status",
            "screen_status",
            "direct_status_probe",
            "midburst_processing_probe",
            "durable_turn_receipts",
        }
        assert all(type(value) is bool for value in before.values())


def test_codex_descriptor_matches_command_model_and_effort_transport(monkeypatch):
    profile = AgentProfile(
        name="test",
        description="",
        model="profile-model",
        codexConfig={"model_reasoning_effort": "test-effort"},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.providers.codex.load_agent_profile", lambda _: profile
    )
    provider = CodexProvider(
        "test", "session", "window", agent_profile="test", model="explicit-model"
    )
    command = shlex.split(provider._build_codex_command())
    result = operation(descriptor("codex"), profile=profile)
    assert result["model_selection"] == "passthrough"
    assert result["model_precedence"] == ["model", "profile.model"]
    assert command[command.index("--model") + 1] == "explicit-model"
    assert result["effort_source"] == "profile.codexConfig.model_reasoning_effort"
    assert 'model_reasoning_effort="test-effort"' in command
    assert result["effort_values"] is None  # No claim the CLI/model accepts any enumeration.


def test_claude_native_profile_explicitly_ignores_overrides_without_exposing_values():
    profile = AgentProfile(
        name="secret-name",
        description="",
        native_agent="secret-native",
        model="secret-model",
        claudeConfig={"effort": "secret-effort"},
    )
    provider = ClaudeCodeProvider(
        "test", "session", "window", agent_profile="test", model="override"
    )
    command = shlex.split(provider._build_claude_command(profile))
    result = operation(descriptor("claude_code"), profile=profile)
    assert "--model" not in command and "--effort" not in command
    assert result["model_selection"] == "profile_owned"
    assert result["model_override_ignored"] is True
    assert result["effort_selection"] == "profile_owned"
    assert result["effort_source"] == "native_agent"
    assert "secret-" not in json.dumps(result)


def test_claude_cao_profile_effort_transport_is_not_an_accepted_value_claim():
    profile = AgentProfile(
        name="test", description="", model="profile-model", claudeConfig={"effort": "test-effort"}
    )
    provider = ClaudeCodeProvider(
        "test", "session", "window", agent_profile="test", model="override"
    )
    command = shlex.split(provider._build_claude_command(profile))
    result = operation(descriptor("claude_code"), profile=profile)
    assert result["model_selection"] == "passthrough"
    assert command[command.index("--model") + 1] == "override"
    assert result["effort_source"] == "profile.claudeConfig.effort"
    assert command[command.index("--effort") + 1] == "test-effort"
    assert result["effort_values"] is None


@pytest.mark.parametrize("name", ["cursor_cli", "antigravity_cli"])
def test_profile_winning_model_precedence_is_explicit(name):
    profile = AgentProfile(name="test", description="", model="profile-model")
    result = operation(descriptor(name), profile=profile)
    assert result["model_precedence"] == ["profile.model", "model"]
    assert result["model_override_ignored"] is True
    assert result["availability"] == "not_probed"


@pytest.mark.parametrize("name", ["send_input", "observe", "continuation"])
def test_nonlaunch_operations_do_not_inherit_launch_overrides(name):
    result = operation(descriptor("codex"), name)
    assert result["operation"] == name
    assert result["model_selection"] == "unverified"
    assert result["effort_selection"] == "unverified"
    assert result["continuity"] == "unverified"


def test_discovery_does_not_probe_binaries_or_serialize_profile_secrets(monkeypatch):
    def no_probe(*args, **kwargs):
        raise AssertionError("descriptor must not probe runtime availability")

    monkeypatch.setattr("shutil.which", no_probe)
    profile = AgentProfile(
        name="test",
        description="",
        system_prompt="secret-prompt",
        mcpServers={"test": {"env": {"TOKEN": "secret-token"}}},
    )
    for item in registered_provider_descriptors():
        result = operation(item, profile=profile)
        assert result["availability"] == "not_probed"
        assert result["enforcement"] == "unverified"
        assert result["model_names"] is None
        assert "secret-" not in json.dumps(result)
        with pytest.raises(ValueError, match="operation"):
            item.operation_capabilities("launch-typo", profile=profile)


def test_descriptor_results_cannot_mutate_later_preflight_reads():
    first = operation(descriptor("codex"))
    first["model_precedence"].append("untrusted")
    second = operation(descriptor("codex"))
    assert second["model_precedence"] == ["model", "profile.model"]


def test_manager_rejects_declared_inapplicable_model_before_constructing_or_registering(
    monkeypatch,
):
    """A descriptor refusal must stop the factory before it has any side effect."""

    def declares_model_inapplicable(cls, requested_operation):
        assert requested_operation == "launch"
        return {
            "operation": requested_operation,
            "model_selection": "not_applicable",
            "effort_selection": "unverified",
        }

    class ConstructionForbidden:
        def __init__(self, *args, **kwargs):
            raise AssertionError("provider constructed before preflight rejection")

    monkeypatch.setattr(
        CopilotCliProvider,
        "operation_capabilities",
        classmethod(declares_model_inapplicable),
    )
    monkeypatch.setattr(manager_module, "CopilotCliProvider", ConstructionForbidden)
    manager = ProviderManager()

    with pytest.raises(ValueError, match="model.*not applicable"):
        manager.create_provider(
            "copilot_cli",
            terminal_id="rejected-terminal",
            tmux_session="session",
            tmux_window="window",
            model="explicit-model",
        )

    assert manager.list_providers() == {}
