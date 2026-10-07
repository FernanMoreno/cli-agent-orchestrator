"""Typed remote errors preserve delivery and receipt recovery semantics."""


def encode_error(exc):
    from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement
    from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError
    from cli_agent_orchestrator.providers.base import (
        OutputExtractionError,
        TurnResultUnavailableError,
    )
    from cli_agent_orchestrator.runtime_channel.registry import RemoteOutcomeUnknownError
    from cli_agent_orchestrator.services.terminal_service import WorkOwnedTerminalError
    from cli_agent_orchestrator.services.turn_recovery_service import TurnRecoveryConflict

    if isinstance(exc, RemoteOutcomeUnknownError):
        return "outcome_unknown", {}
    if isinstance(exc, TerminalInputBlockedError):
        return "input_blocked", {
            "action": exc.action,
            "delivery_may_have_occurred": exc.delivery_may_have_occurred,
        }
    if isinstance(exc, TurnResultUnavailableError):
        return "output_pending", {}
    if isinstance(exc, TurnRecoveryConflict):
        return "generation_conflict", {}
    if isinstance(exc, OutputExtractionError):
        return "extraction_error", {}
    if isinstance(exc, (WorkOwnedTerminalError, UnsupportedWorkEnforcement)):
        return "work_refused", {}
    if isinstance(exc, ValueError):
        return "invalid_request", {}
    return "internal", {}


def raise_error(result):
    from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError
    from cli_agent_orchestrator.providers.base import (
        OutputExtractionError,
        TurnResultUnavailableError,
    )
    from cli_agent_orchestrator.runtime_channel.registry import (
        RemoteCommandError,
        RemoteOutcomeUnknownError,
    )
    from cli_agent_orchestrator.services.terminal_service import WorkOwnedTerminalError
    from cli_agent_orchestrator.services.turn_recovery_service import TurnRecoveryConflict

    kind, message = result.error_kind, result.error or "Remote operation failed"
    if kind == "input_blocked":
        raise TerminalInputBlockedError(
            message,
            action=result.error_detail.get("action", "reconcile"),
            delivery_may_have_occurred=result.error_detail.get("delivery_may_have_occurred", True),
        )
    if kind == "output_pending":
        raise TurnResultUnavailableError(message)
    if kind == "generation_conflict":
        raise TurnRecoveryConflict(message)
    if kind == "extraction_error":
        raise OutputExtractionError(message)
    if kind == "work_refused":
        raise WorkOwnedTerminalError(message)
    if kind == "outcome_unknown":
        raise RemoteOutcomeUnknownError(message)
    raise RemoteCommandError(message)
