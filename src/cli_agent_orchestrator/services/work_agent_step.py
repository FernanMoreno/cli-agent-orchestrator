"""Registered terminal and process delivery adapters for managed agent steps."""

import asyncio
from typing import Annotated

from pydantic import StringConstraints

from cli_agent_orchestrator.models.work_contract import (
    EffectiveWorkContractV2,
    FrozenContractModel,
    Identity,
)
from cli_agent_orchestrator.services.work_delivery import DeliveryAdapter
from cli_agent_orchestrator.services.work_service import DeliveryObservation
from cli_agent_orchestrator.services.work_terminal import (
    managed_session_name,
    managed_window_name,
)


class AgentStepPayload(FrozenContractModel):
    """The closed, immutable task data accepted by the managed branch."""

    terminal_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8}$")]
    agent_profile: Identity
    message: Annotated[str, StringConstraints(min_length=1, max_length=32768)]


class ProcessAgentStepPayloadV2(FrozenContractModel):
    """Closed V2 process data; command identity comes only from the frozen contract."""

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


_PROCESS_AGENT_STEP_TOOLS = frozenset({"cao.work.task_received", "cao.work.submit_result"})


def _validate_process_contract(payload, contract):
    if contract.operation_kind != "agent_step":
        raise ValueError("managed agent-step payload requires its exact operation")
    if not isinstance(contract, EffectiveWorkContractV2):
        raise ValueError("process agent-step requires an effective V2 contract")
    identities = contract.executable_identities
    if len(identities) != 1 or contract.permissions.commands != (identities[0].command_token,):
        raise ValueError("process agent-step requires one exact executable mapping")
    if contract.permissions.tools != tuple(sorted(_PROCESS_AGENT_STEP_TOOLS)):
        raise ValueError("process agent-step requires only receipt and result tools")
    if (
        contract.permissions.network
        or contract.permissions.paths
        or contract.permissions.artifacts
        or contract.resources.write_paths
    ):
        raise ValueError("process agent-step cannot request network, workspace, or artifact access")


async def _deliver_process(binding, payload, snapshot, port):
    _validate_process_contract(payload, binding.contract)
    if type(snapshot.content) is not bytes:
        raise ValueError("frozen snapshot content must be bytes")
    frozen_context = snapshot.content.decode("utf-8")
    message = f"{frozen_context}\n\n{payload.message}" if frozen_context else payload.message
    worker_input = message.encode("utf-8")
    if len(worker_input) > 32768:
        raise ValueError("frozen agent-step content exceeds the process input bound")
    command_token = binding.contract.executable_identities[0].command_token
    # The process capability has no terminal methods. Cancellation remains
    # uncertain in WorkService and never permits implicit redelivery.
    from cli_agent_orchestrator.backends.docker_backend import DockerWorkExecution

    execution = await asyncio.to_thread(port.execute_process, command_token, worker_input)
    if type(execution) is not DockerWorkExecution or (
        type(execution.returncode) is not int
        or not 0 <= execution.returncode <= 255
        or execution.process_stopped is not True
        or execution.container_removed is not True
        or execution.image_removed is not True
    ):
        raise ValueError("verified Docker process exit and cleanup proof are required")
    if execution.returncode != 0:
        return DeliveryObservation(
            process_failure={
                "attempt_id": binding.attempt_id,
                "generation": binding.generation,
                "exit_code": execution.returncode,
                "process_stopped": execution.process_stopped,
                "container_removed": execution.container_removed,
                "image_removed": execution.image_removed,
            }
        )
    return DeliveryObservation()


def agent_step_adapter():
    """Return the explicit server registry entry for version-one agent steps."""
    return DeliveryAdapter(
        AgentStepPayload,
        _deliver,
        terminal_identity=lambda payload: payload.terminal_id,
        terminal_target=_target_identity,
        validate_contract=_validate_contract,
    )


def agent_step_process_adapter():
    """Return the V2 process route for an explicitly provisioned agent step."""
    return DeliveryAdapter(
        ProcessAgentStepPayloadV2,
        _deliver_process,
        validate_contract=_validate_process_contract,
    )
