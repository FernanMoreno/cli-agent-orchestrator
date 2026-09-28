"""Registered managed agent-step delivery; no public caller context enters here."""

from typing import Annotated

from pydantic import StringConstraints

from cli_agent_orchestrator.models.work_contract import FrozenContractModel, Identity
from cli_agent_orchestrator.services.work_delivery import DeliveryAdapter
from cli_agent_orchestrator.services.work_terminal import (
    managed_session_name,
    managed_window_name,
)


class AgentStepPayload(FrozenContractModel):
    """The closed, immutable task data accepted by the managed branch."""

    terminal_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8}$")]
    agent_profile: Identity
    message: Annotated[str, StringConstraints(min_length=1, max_length=32768)]


def _validate_contract(payload, contract):
    if contract.operation_kind != "agent_step":
        raise ValueError("managed agent-step payload requires its exact operation")


def _target_identity(payload):
    return (
        payload.terminal_id,
        managed_session_name(f"cao-{payload.terminal_id}"),
        managed_window_name(payload.agent_profile, payload.terminal_id),
    )


async def _deliver(binding, payload, snapshot, port):
    from cli_agent_orchestrator.services.agent_step import run_managed_agent_step

    return await run_managed_agent_step(binding, payload, snapshot, port)


def agent_step_adapter():
    """Return the explicit server registry entry for version-one agent steps."""
    return DeliveryAdapter(
        AgentStepPayload,
        _deliver,
        terminal_identity=lambda payload: payload.terminal_id,
        terminal_target=_target_identity,
        validate_contract=_validate_contract,
    )
