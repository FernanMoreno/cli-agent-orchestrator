"""Regression coverage for the opt-in at-most-once CAO delivery contract."""

from cli_agent_orchestrator.api.main import CreateTerminalBody, RunStepRequest


def test_deferred_terminal_delivery_defaults_to_historical_recovery():
    assert CreateTerminalBody().prompt_redelivery is True


def test_deferred_terminal_delivery_accepts_an_explicit_no_redelivery_policy():
    assert CreateTerminalBody(prompt_redelivery=False).prompt_redelivery is False


def test_run_step_preserves_the_explicit_no_redelivery_policy():
    request = RunStepRequest(
        provider="codex",
        agent="worker",
        prompt="perform one side-effecting operation",
        prompt_redelivery=False,
    )
    assert request.prompt_redelivery is False
