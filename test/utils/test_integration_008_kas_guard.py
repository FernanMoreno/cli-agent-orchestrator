import pytest

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.models.kiro_engine import KiroEngine
from cli_agent_orchestrator.utils.kiro_launch_guard import check_kas_launch, kas_policy_digest


def sample(**fields):
    return AgentProfile(
        name="review", description="Review", engine="kas", allowedTools=["fs_read"], **fields
    )


def test_kas_default_off(monkeypatch):
    monkeypatch.setattr(constants, "ENABLE_KAS_LAUNCH", False)
    assert not check_kas_launch(engine=KiroEngine.KAS, profile=sample()).allowed
    assert check_kas_launch(engine=KiroEngine.V2).allowed


def test_kas_has_no_profileless_optin_bypass(monkeypatch):
    monkeypatch.setattr(constants, "ENABLE_KAS_LAUNCH", True)
    assert not check_kas_launch(engine=KiroEngine.KAS).allowed


def test_persisted_policy_fence_refuses_drift(monkeypatch):
    monkeypatch.setattr(constants, "ENABLE_KAS_LAUNCH", True)
    parsed = sample()
    digest = kas_policy_digest(parsed)
    assert check_kas_launch(engine=KiroEngine.KAS, profile=parsed, expected_digest=digest).allowed
    modified = parsed.model_copy(update={"allowedTools": ["fs_read", "fs_write"]})
    verdict = check_kas_launch(engine=KiroEngine.KAS, profile=modified, expected_digest=digest)
    assert not verdict.allowed
    assert verdict.reason_code == "policy-drift"


def test_policy_digest_never_returns_prompt_or_credentials():
    parsed = sample(
        prompt="private-prompt",
        mcpServers={"service": {"command": "service", "env": {"KEY": "private-token"}}},
    )
    digest = kas_policy_digest(parsed)
    assert len(digest) == 64
    assert "private" not in digest
