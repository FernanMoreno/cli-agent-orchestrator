"""The launch gateway factory composes only verified, non-authoritative infrastructure."""

import sqlite3
from test.services.test_work_launch_runtime import (
    ProtectedFakeBackend,
    durable_counts,
    provision,
    trusted_setup,
)

import pytest

from cli_agent_orchestrator.clients.work_repository import (
    SchemaMismatch,
    WorkRepository,
)
from cli_agent_orchestrator.models.work_contract import (
    EffectiveWorkContractV2,
    ExecutableIdentity,
)
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_authority import WorkAuthority
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning


def _authority_counts(repository):
    with repository.connection() as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "work_jobs",
                "work_grants",
                "work_principals",
                "work_delegation_snapshots",
                "work_launch_provisions",
                "work_dispatch_bindings",
            )
        )


def _request(gateway_module):
    return gateway_module.DurableLaunchRequest(
        selection="opaque",
        agent_profile="developer",
        session_name="cao-factory-launch",
        message="compose this launch",
        allowed_tools=("tool.read",),
    )


class ProcessLaunchFakeBackend(ProtectedFakeBackend):
    def execute_bound_process(self, *_args, **_kwargs):
        raise AssertionError("composition test must not launch the worker")


def _process_launch_contract(contract):
    payload = contract.model_dump(mode="python")
    payload["schema_version"] = 2
    payload["permissions"]["commands"] = ("/bin/alpha",)
    payload["executable_identities"] = (
        ExecutableIdentity(
            command_token="/bin/alpha",
            content_reference="sha256:" + "a" * 64,
            sha256_digest="a" * 64,
            elf_machine="x86_64",
            elf_class="ELF64",
            endianness="little",
        ).model_dump(mode="python"),
    )
    return EffectiveWorkContractV2.model_validate(payload)


def test_factory_composes_verified_sqlite_then_resolves_later_provision_and_replays_restart(
    trusted_setup,
):
    """Breaks if composition mints authority or provider caches per-request launch context."""
    from cli_agent_orchestrator.services import work_launch_gateway as gateway_module

    repository, principal, _, _, _, _ = trusted_setup
    backend = ProcessLaunchFakeBackend()
    before_factory = _authority_counts(repository)

    gateway = gateway_module.build_durable_launch_gateway(repository, backends={"test": backend})

    assert _authority_counts(repository) == before_factory
    assert durable_counts(repository) == (0, 0, 0, 0, 0)

    repository, principal, _, job, grant, contract = trusted_setup
    WorkProvisioning(repository).provision_launch(
        principal,
        subject=principal,
        selector="opaque",
        expected_revision=0,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        contract=_process_launch_contract(contract),
        adapter_version=2,
        lease_seconds=300,
    )
    first = gateway.admit(principal, _request(gateway_module))
    restarted = gateway_module.build_durable_launch_gateway(
        repository, backends={"test": backend}
    ).admit(principal, _request(gateway_module))

    assert restarted == first
    assert first.state == "queued"
    assert durable_counts(repository) == (1, 1, 1, 1, 1)
    with repository.connection() as connection:
        stored = connection.execute(
            "SELECT idempotency_key,request_hash FROM work_items WHERE id=?", (first.work_item_id,)
        ).fetchone()
    assert stored["idempotency_key"] == f"launch-v1-{stored['request_hash']}"
    assert backend.effects == []


def test_factory_registers_private_managed_agent_step_delivery(trusted_setup):
    """Managed lineage may use the server-owned agent-step adapter after handoff."""
    from cli_agent_orchestrator.services import work_launch_gateway as gateway_module

    repository, _principal, *_rest = trusted_setup
    gateway = gateway_module.build_durable_launch_gateway(
        repository, backends={"test": ProtectedFakeBackend()}
    )
    adapters = gateway._launch_runtime_provider._runtime._admission.deliveries.adapters

    assert set(adapters) == {("launch", 2), ("agent_step", 1)}
    assert adapters[("agent_step", 1)].payload_model.__name__ == "AgentStepPayload"


def test_factory_registers_process_agent_step_only_for_opt_in_local_docker(
    trusted_setup, monkeypatch
):
    """V2 process execution requires T020 opt-in and the local Docker backend."""
    from cli_agent_orchestrator.backends.docker_backend import DockerWorkBackend
    from cli_agent_orchestrator.services import work_launch_gateway as gateway_module

    monkeypatch.delenv("CAO_T020_ACCEPTANCE", raising=False)
    repository, *_ = trusted_setup
    docker_backend = DockerWorkBackend(
        image_ref="sha256:" + "d" * 64,
        repository=repository,
    )
    gateway = gateway_module.build_durable_launch_gateway(
        repository, backends={"docker-local": docker_backend}
    )
    adapters = gateway._launch_runtime_provider._runtime._admission.deliveries.adapters

    assert set(adapters) == {("launch", 2), ("agent_step", 1)}

    monkeypatch.setenv("CAO_T020_ACCEPTANCE", "1")
    legacy_docker_gateway = gateway_module.build_durable_launch_gateway(
        repository, backends={"test": docker_backend}
    )
    legacy_docker_adapters = (
        legacy_docker_gateway._launch_runtime_provider._runtime._admission.deliveries.adapters
    )
    assert set(legacy_docker_adapters) == {("launch", 2), ("agent_step", 1)}

    opt_in_gateway = gateway_module.build_durable_launch_gateway(
        repository, backends={"docker-local": docker_backend}
    )
    adapters = opt_in_gateway._launch_runtime_provider._runtime._admission.deliveries.adapters

    assert set(adapters) == {("launch", 2), ("agent_step", 1), ("agent_step", 2)}
    assert adapters[("agent_step", 1)].payload_model.__name__ == "AgentStepPayload"
    assert adapters[("agent_step", 2)].payload_model.__name__ == "ProcessAgentStepPayloadV2"

    ordinary_gateway = gateway_module.build_durable_launch_gateway(
        repository, backends={"test": ProtectedFakeBackend()}
    )
    ordinary_adapters = (
        ordinary_gateway._launch_runtime_provider._runtime._admission.deliveries.adapters
    )
    assert set(ordinary_adapters) == {("launch", 2), ("agent_step", 1)}


@pytest.mark.parametrize("change", ("retire", "revoke"))
def test_factory_provider_fails_closed_for_foreign_and_changed_durable_context(
    trusted_setup, change
):
    """Breaks if a factory-captured principal, selector, grant, or provision remains effective."""
    from cli_agent_orchestrator.services import work_launch_gateway as gateway_module

    repository, principal, authority, _, grant, _ = trusted_setup
    provision(trusted_setup)
    gateway = gateway_module.build_durable_launch_gateway(
        repository, backends={"test": ProtectedFakeBackend()}
    )
    foreign = auth._verified_principal(
        "https://issuer.test", "foreign-owner", [auth.SCOPE_ADMIN], "jwt"
    )

    with pytest.raises(gateway_module.DurableLaunchGatewayError) as foreign_error:
        gateway.admit(foreign, _request(gateway_module))
    assert foreign_error.value.code == "launch_context_unavailable"

    if change == "retire":
        WorkProvisioning(repository).retire_launch(
            principal, subject=principal, selector="opaque", expected_revision=1
        )
        expected_code = "launch_context_unavailable"
    else:
        WorkAuthority(repository).revoke(
            principal,
            grant_id=grant.id,
            expected_grant_revision=grant.revision,
            reason="test",
        )
        expected_code = "launch_context_invalid"

    with pytest.raises(gateway_module.DurableLaunchGatewayError) as changed_error:
        gateway.admit(principal, _request(gateway_module))
    assert changed_error.value.code == expected_code
    assert durable_counts(repository) == (0, 0, 0, 0, 0)


def test_factory_rejects_unverified_store_invalid_backend_and_unsupported_adapter_without_writes(
    trusted_setup, tmp_path
):
    """Breaks if invalid dependencies fall back, compose partially, or register adapters beyond launch v1."""
    from cli_agent_orchestrator.services import work_launch_gateway as gateway_module

    repository, principal, _, job, grant, contract = trusted_setup
    backend = ProcessLaunchFakeBackend()
    before_invalid_backend = _authority_counts(repository)

    with pytest.raises(ValueError, match="explicit server backend registry required"):
        gateway_module.build_durable_launch_gateway(repository, backends={"test": object()})
    assert _authority_counts(repository) == before_invalid_backend
    assert durable_counts(repository) == (0, 0, 0, 0, 0)

    incompatible = WorkRepository(tmp_path / "incompatible.sqlite3")
    with sqlite3.connect(incompatible.path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    with pytest.raises(SchemaMismatch, match="work schema is incomplete"):
        gateway_module.build_durable_launch_gateway(incompatible, backends={"test": backend})

    WorkProvisioning(repository).provision_launch(
        principal,
        subject=principal,
        selector="opaque",
        expected_revision=0,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        contract=contract,
        adapter_version=3,
        lease_seconds=300,
    )
    gateway = gateway_module.build_durable_launch_gateway(repository, backends={"test": backend})

    with pytest.raises(gateway_module.DurableLaunchGatewayError) as unsupported:
        gateway.admit(principal, _request(gateway_module))
    assert unsupported.value.code == "launch_runtime_unavailable"
    assert durable_counts(repository) == (0, 0, 0, 0, 0)
    assert backend.effects == []
