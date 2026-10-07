"""KAS restart consumes persisted private policy, with no permissive fallback."""

import pytest

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.providers import manager
from cli_agent_orchestrator.services import kiro_profiles
from cli_agent_orchestrator.utils.kiro_launch_guard import kas_policy_digest


@pytest.fixture
def proof(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "ENABLE_KAS_LAUNCH", True)
    monkeypatch.setattr(kiro_profiles, "KIRO_AGENTS_DIR", tmp_path)
    parsed = AgentProfile(name="review", description="Review", engine="kas", allowedTools=[])
    kiro_profiles.install_runtime_kas_profile("1234abcd", parsed)
    metadata = dict(
        id="1234abcd",
        tmux_session="cao-test",
        tmux_window="1",
        provider="kiro_cli",
        agent_profile="review",
        allowed_tools=[],
        engine="kas",
        shell_command="bash",
        kiro_policy_digest=kas_policy_digest(parsed),
    )
    monkeypatch.setattr(manager, "get_terminal_metadata", lambda _id: metadata)
    monkeypatch.setattr(manager, "get_terminal_turn_receipt", lambda _id: None)
    return metadata


def test_existing_kas_restores_without_launch_or_mutable_profile_lookup(proof, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Restoration consulted mutable operator profile")

    monkeypatch.setattr("cli_agent_orchestrator.utils.agent_profiles.load_agent_profile", forbidden)
    provider = manager.ProviderManager().get_provider("1234abcd")
    assert provider._engine.value == "kas"
    assert provider._initialized is True
    assert provider._allowed_tools == []


def test_missing_policy_proof_never_publishes_provider(proof):
    proof["kiro_policy_digest"] = None
    registry = manager.ProviderManager()
    with pytest.raises(ValueError):
        registry.get_provider("1234abcd")
    assert "1234abcd" not in registry._providers
