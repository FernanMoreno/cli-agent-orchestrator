"""Versioned legacy and process-backed launch delivery adapters."""

import asyncio
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from cli_agent_orchestrator.models.work_contract import FrozenContractModel, Identity, Text
from cli_agent_orchestrator.services.work_delivery import DeliveryAdapter
from cli_agent_orchestrator.services.work_service import DeliveryObservation
from cli_agent_orchestrator.services.work_terminal import (
    current_managed_terminal_id,
    managed_session_name,
    managed_terminal_identity,
    managed_window_name,
)


def current_launch_terminal_id():
    """Compatibility alias for callers that predate managed agent-step delivery."""
    return current_managed_terminal_id()


class LaunchPayload(FrozenContractModel):
    terminal_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8}$")]
    agent_profile: Identity
    session_name: Identity
    message: Annotated[str, StringConstraints(max_length=32768)]
    allowed_tools: tuple[Text, ...] = Field(max_length=128)


class ProcessLaunchPayloadV2(FrozenContractModel):
    """Closed launch data for one exact V2 command mapping."""

    terminal_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8}$")]
    agent_profile: Identity
    session_name: Identity
    message: Annotated[str, StringConstraints(max_length=32768)]
    allowed_tools: tuple[Text, ...] = Field(max_length=128)
    command_token: Annotated[
        str,
        StringConstraints(
            min_length=2,
            max_length=4096,
            pattern=r"^/[A-Za-z0-9._/+@-]+$",
        ),
    ]

    @field_validator("command_token")
    @classmethod
    def canonical_command_token(cls, value):
        import posixpath

        if value.startswith("//") or posixpath.normpath(value) != value:
            raise ValueError("command token must be a canonical absolute path")
        return value


def _validate_contract(payload, contract):
    if not set(payload.allowed_tools).issubset(contract.permissions.tools):
        raise ValueError("launch tools exceed its effective contract")


async def _launch(binding, payload, snapshot, port):
    _validate_contract(payload, binding.contract)
    from cli_agent_orchestrator.services import terminal_service

    planned_target = port.expected_target
    if planned_target is None:
        raise ValueError("managed launch has no reserved terminal target")
    with managed_terminal_identity(payload.terminal_id):
        with port.backend_scope():
            terminal = await terminal_service.create_terminal(
                provider=binding.contract.provider,
                agent_profile=payload.agent_profile,
                session_name=planned_target[0],
                new_session=True,
                reserved_window_name=planned_target[1],
                working_directory=binding.contract.resources.checkout_root,
                allowed_tools=list(payload.allowed_tools),
                model=binding.contract.model.value,
                prompt_redelivery=False,
            )
            if payload.message:
                terminal_service.send_input(
                    terminal.id,
                    payload.message,
                    frozen_memory=snapshot.content.decode("utf-8"),
                    task_delivery=True,
                )
            return DeliveryObservation()


def launch_adapter():
    """Return historical V1 terminal delivery for explicitly legacy runtimes only."""
    return DeliveryAdapter(
        LaunchPayload,
        _launch,
        terminal_identity=lambda payload: payload.terminal_id,
        terminal_target=lambda payload: (
            payload.terminal_id,
            managed_session_name(payload.session_name),
            managed_window_name(payload.agent_profile, payload.terminal_id),
        ),
        validate_contract=_validate_contract,
    )


def _validate_process_contract(payload, contract):
    from cli_agent_orchestrator.models.work_contract import EffectiveWorkContractV2

    _validate_contract(payload, contract)
    if not isinstance(contract, EffectiveWorkContractV2):
        raise ValueError("process launch requires an effective V2 contract")
    matches = tuple(
        item
        for item in contract.executable_identities
        if item.command_token == payload.command_token
    )
    if len(contract.executable_identities) != 1 or len(matches) != 1:
        raise ValueError("process launch requires one exact immutable executable mapping")
    if contract.permissions.network:
        raise ValueError("process launch v2 does not support direct network effects")


async def _launch_process(binding, payload, snapshot, port):
    _validate_process_contract(payload, binding.contract)
    frozen_context = snapshot.content.decode("utf-8")
    message = f"{frozen_context}\n\n{payload.message}" if frozen_context else payload.message
    worker_input = message.encode("utf-8")
    if len(worker_input) > 32768:
        raise ValueError("frozen launch content exceeds the process input bound")
    # This capability has no terminal methods and is closed when the adapter
    # returns. to_thread cancellation is handled by WorkService as uncertain;
    # it never authorizes redelivery of a potentially released worker.
    await asyncio.to_thread(
        port.execute_process,
        payload.command_token,
        worker_input,
    )
    return DeliveryObservation()


def process_launch_adapter():
    """V2 process route installed by the production DurableLaunchGateway."""
    return DeliveryAdapter(
        ProcessLaunchPayloadV2,
        _launch_process,
        validate_contract=_validate_process_contract,
    )
