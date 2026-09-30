"""Server-owned process adapter for the version-two managed agent-step route."""

import hashlib
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from cli_agent_orchestrator.backends.docker_backend import DockerWorkExecution
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContract,
    EffectiveWorkContractV2,
    ExecutableIdentity,
)
from cli_agent_orchestrator.services.work_agent_step import (
    AgentStepPayload,
    ProcessAgentStepPayloadV2,
    agent_step_adapter,
    agent_step_process_adapter,
)
from cli_agent_orchestrator.services.work_service import DeliveryObservation

_WORKER_BYTES = b"static worker placeholder"
_WORKER_DIGEST = hashlib.sha256(_WORKER_BYTES).hexdigest()
_WORKER_COMMAND = "/cao-agent-step-worker"
_MCP_TOOLS = ("cao.work.submit_result", "cao.work.task_received")


def _process_contract(**overrides):
    values = {
        "id": "managed-step-v2",
        "operation_kind": "agent_step",
        "provider": "mock_cli",
        "backend": "docker-local",
        "permissions": ContractPermissions(
            tools=_MCP_TOOLS,
            commands=(_WORKER_COMMAND,),
        ),
        "resources": ContractResources(checkout_root="/repo", units=1),
        "snapshot": ContractSnapshot(state="present", id="snapshot-v2", delivered_hash="a" * 64),
        "executable_identities": (
            ExecutableIdentity(
                command_token=_WORKER_COMMAND,
                content_reference=f"sha256:{_WORKER_DIGEST}",
                sha256_digest=_WORKER_DIGEST,
                elf_machine="x86_64",
                elf_class="ELF64",
                endianness="little",
                static=True,
            ),
        ),
    }
    values.update(overrides)
    if "executable_identities" in overrides and "permissions" not in overrides:
        values["permissions"] = ContractPermissions(
            tools=_MCP_TOOLS,
            commands=tuple(identity.command_token for identity in values["executable_identities"]),
        )
    return EffectiveWorkContractV2(**values)


class _ProcessPort:
    def __init__(self, execution=None):
        self.calls = []
        self.execution = execution

    def execute_process(self, command_token, worker_input):
        self.calls.append((command_token, worker_input))
        return self.execution


@pytest.mark.asyncio
async def test_process_adapter_runs_only_the_frozen_executable_with_frozen_context():
    adapter = agent_step_process_adapter()
    contract = _process_contract()
    payload = ProcessAgentStepPayloadV2(agent_profile="developer", message="do the managed task")
    port = _ProcessPort(DockerWorkExecution(0, b"", b"", True, True, True))

    result = await adapter.send(
        type("Binding", (), {"contract": contract})(),
        payload,
        SimpleNamespace(content=b"frozen context"),
        port,
    )

    assert result == DeliveryObservation()
    assert port.calls == [
        (_WORKER_COMMAND, "frozen context\n\ndo the managed task".encode("utf-8"))
    ]
    assert adapter.terminal_identity is None
    assert adapter.terminal_target is None


@pytest.mark.asyncio
async def test_process_adapter_reports_only_a_verified_nonzero_docker_exit():
    adapter = agent_step_process_adapter()
    contract = _process_contract()
    payload = ProcessAgentStepPayloadV2(agent_profile="developer", message="fail this attempt")
    binding = SimpleNamespace(
        contract=contract,
        attempt_id="docker-attempt-17",
        generation=4,
    )
    port = _ProcessPort(DockerWorkExecution(23, b"", b"", True, True, True))

    result = await adapter.send(binding, payload, SimpleNamespace(content=b""), port)

    assert result.process_failure.model_dump() == {
        "attempt_id": "docker-attempt-17",
        "generation": 4,
        "exit_code": 23,
        "process_stopped": True,
        "container_removed": True,
        "image_removed": True,
    }


@pytest.mark.asyncio
async def test_process_adapter_keeps_zero_exit_without_result_pending():
    adapter = agent_step_process_adapter()
    binding = SimpleNamespace(contract=_process_contract(), attempt_id="attempt-zero", generation=1)
    payload = ProcessAgentStepPayloadV2(agent_profile="developer", message="no result")
    port = _ProcessPort(DockerWorkExecution(0, b"", b"", True, True, True))

    result = await adapter.send(binding, payload, SimpleNamespace(content=b""), port)

    assert result == DeliveryObservation()


@pytest.mark.asyncio
async def test_process_adapter_fails_closed_when_docker_cleanup_is_unverified():
    adapter = agent_step_process_adapter()
    binding = SimpleNamespace(
        contract=_process_contract(), attempt_id="attempt-dirty", generation=1
    )
    payload = ProcessAgentStepPayloadV2(agent_profile="developer", message="fail this attempt")
    port = _ProcessPort(DockerWorkExecution(23, b"", b"", True, True, True))
    port.execution = SimpleNamespace(
        returncode=23,
        stdout=b"",
        stderr=b"",
        process_stopped=True,
        container_removed=False,
        image_removed=True,
    )

    with pytest.raises(ValueError, match="verified Docker process exit"):
        await adapter.send(binding, payload, SimpleNamespace(content=b""), port)


def test_process_adapter_payload_is_closed_and_does_not_accept_terminal_or_command_authority():
    payload = ProcessAgentStepPayloadV2(agent_profile="developer", message="do the managed task")

    assert payload.model_dump() == {
        "agent_profile": "developer",
        "message": "do the managed task",
    }
    with pytest.raises(ValidationError):
        ProcessAgentStepPayloadV2(
            agent_profile="developer",
            message="do the managed task",
            command_token=_WORKER_COMMAND,
        )
    with pytest.raises(ValidationError):
        ProcessAgentStepPayloadV2(
            agent_profile="developer",
            message="do the managed task",
            terminal_id="a1b2c3d4",
        )


def test_process_adapter_keeps_v1_terminal_delivery_separate():
    adapter = agent_step_adapter()
    payload = AgentStepPayload(
        terminal_id="a1b2c3d4", agent_profile="developer", message="legacy managed step"
    )

    assert adapter.payload_model is AgentStepPayload
    assert adapter.terminal_identity(payload) == "a1b2c3d4"
    assert adapter.terminal_target is not None


@pytest.mark.parametrize(
    "contract, message",
    [
        (
            EffectiveWorkContract(
                id="managed-step-v1",
                operation_kind="agent_step",
                provider="mock_cli",
                backend="docker-local",
                permissions=ContractPermissions(),
                resources=ContractResources(checkout_root="/repo", units=1),
                snapshot=ContractSnapshot(
                    state="present", id="snapshot-v1", delivered_hash="b" * 64
                ),
            ),
            "effective V2 contract",
        ),
        (_process_contract(executable_identities=()), "one exact executable"),
        (
            _process_contract(
                permissions=ContractPermissions(
                    tools=("cao.work.task_received",), commands=(_WORKER_COMMAND,)
                )
            ),
            "receipt and result tools",
        ),
        (
            _process_contract(
                permissions=ContractPermissions(
                    tools=_MCP_TOOLS,
                    commands=(_WORKER_COMMAND,),
                    network=("internet",),
                )
            ),
            "network",
        ),
        (
            _process_contract(
                permissions=ContractPermissions(
                    tools=_MCP_TOOLS,
                    commands=(_WORKER_COMMAND,),
                    paths=("/repo",),
                )
            ),
            "workspace",
        ),
        (
            _process_contract(
                permissions=ContractPermissions(
                    tools=_MCP_TOOLS,
                    commands=(_WORKER_COMMAND,),
                    artifacts=("write",),
                )
            ),
            "artifact",
        ),
        (
            _process_contract(
                resources=ContractResources(
                    checkout_root="/repo", write_paths=("/repo/out",), units=1
                )
            ),
            "workspace",
        ),
    ],
)
def test_process_adapter_rejects_non_process_or_overbroad_contract(contract, message):
    adapter = agent_step_process_adapter()
    payload = ProcessAgentStepPayloadV2(agent_profile="developer", message="do the managed task")

    with pytest.raises(ValueError, match=message):
        adapter.validate_contract(payload, contract)


@pytest.mark.asyncio
async def test_process_adapter_rejects_input_over_the_docker_bound_before_execution():
    adapter = agent_step_process_adapter()
    payload = ProcessAgentStepPayloadV2(agent_profile="developer", message="m" * 32768)
    port = _ProcessPort()

    with pytest.raises(ValueError, match="input bound"):
        await adapter.send(
            type("Binding", (), {"contract": _process_contract()})(),
            payload,
            SimpleNamespace(content=b"snapshot"),
            port,
        )

    assert port.calls == []


@pytest.mark.asyncio
async def test_process_adapter_rejects_non_byte_snapshot_before_execution():
    adapter = agent_step_process_adapter()
    payload = ProcessAgentStepPayloadV2(agent_profile="developer", message="do the managed task")
    port = _ProcessPort()

    with pytest.raises(ValueError, match="snapshot content"):
        await adapter.send(
            type("Binding", (), {"contract": _process_contract()})(),
            payload,
            SimpleNamespace(content="mutable text"),
            port,
        )

    assert port.calls == []
