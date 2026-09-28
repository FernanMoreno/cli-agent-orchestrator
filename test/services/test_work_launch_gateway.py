"""The injectable durable-launch gateway admits only through trusted runtimes."""

import hashlib
import time

import pytest

from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContract,
    ObservedValue,
)
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_launch import launch_adapter
from cli_agent_orchestrator.services.work_launch_runtime import LaunchRuntime
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler


class ProtectedFakeBackend(TmuxBackend):
    """A backend port fake: admission may preflight but never create a terminal."""

    def __init__(self):
        self.preflights = []
        self.effects = []

    def preflight_work(self, contract):
        self.preflights.append(contract)

    def create_session(self, *args, **kwargs):
        self.effects.append((args, kwargs))
        raise AssertionError("gateway admission must not create a terminal")


class FailingPreflightBackend(ProtectedFakeBackend):
    def preflight_work(self, contract):
        super().preflight_work(contract)
        raise RuntimeError("server-private-preflight-detail")


class RuntimeProvider:
    """Server-port fake returning already-composed runtimes by trusted identity."""

    def __init__(self, runtimes):
        self.runtimes = runtimes
        self.calls = []

    def runtime_for(self, principal, selection):
        self.calls.append((principal, selection))
        return self.runtimes.get((principal.id, selection))


def create_context(repository, principal, root, *, label):
    job = repository.create_job(
        project_id=f"project-{label}",
        principal_id=principal.id,
        allowed_providers=["mock_cli"],
        grant_id=f"root-{label}",
        budget={"scheduler_units": 100},
    )
    authority = WorkAuthority(repository)
    grant = authority.issue_root(
        principal,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read", "tool.read"}, paths={str(root)}),
        expires_at=time.time() + 600,
    )
    contract_id = f"trusted-launch-contract-{label}"
    snapshot = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision)
    ).freeze(
        principal=principal,
        job_id=job["id"],
        contract_id=contract_id,
        binding_key=f"trusted-launch-context-{label}",
        request_hash=hashlib.sha256(f"trusted launch context {label}".encode()).hexdigest(),
        scope="project",
        scope_id=f"project-{label}",
        resolver=lambda connection, actor: ResolvedSnapshot(f"frozen launch context {label}"),
    )
    contract = EffectiveWorkContract(
        id=contract_id,
        operation_kind="launch",
        provider="mock_cli",
        backend="test",
        permissions=ContractPermissions(tools=("tool.read",), paths=(str(root),)),
        resources=ContractResources(
            checkout_root=str(root),
            write_paths=(str(root / f"launch-work-{label}"),),
            units=1,
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
        model=ObservedValue(status="known", value="mock-model", provenance="launch_config"),
    )
    return WorkProvisioning(repository).provision_launch(
        principal,
        subject=principal,
        selector=label,
        expected_revision=0,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        contract=contract,
        adapter_version=1,
        lease_seconds=300,
    )


def make_runtime(repository, *, backend=None):
    backend = backend or ProtectedFakeBackend()
    runtime = LaunchRuntime(
        repository,
        backends={"test": backend},
        delivery_adapters={("launch", 1): launch_adapter()},
    )
    return runtime, backend


def durable_counts(repository):
    with repository.connection() as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "work_items",
                "work_attempts",
                "work_dispatch_bindings",
                "work_delivery_orders",
                "work_scheduler_requests",
            )
        )


@pytest.fixture
def gateway_setup(tmp_path):
    repository = WorkRepository(tmp_path / "launch-gateway.sqlite3")
    repository.initialize()
    WorkScheduler(repository).configure(
        capacity=2, max_queue=100, aging_seconds=10, expected_policy_revision=0
    )
    principal = auth._verified_principal(
        "https://issuer.test", "launch-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    own_context = create_context(repository, principal, tmp_path, label="own")
    queued_context = create_context(repository, principal, tmp_path, label="queued")
    own_runtime, own_backend = make_runtime(repository)
    queued_runtime, queued_backend = make_runtime(repository)
    provider = RuntimeProvider(
        {
            (principal.id, "own"): own_runtime,
            (principal.id, "queued"): queued_runtime,
        }
    )
    return repository, principal, provider, own_context, own_backend, queued_backend


def request(gateway_module, message="review this change", *, selection="own"):
    return gateway_module.DurableLaunchRequest(
        selection=selection,
        agent_profile="developer",
        session_name="cao-durable-launch",
        message=message,
        allowed_tools=("tool.read",),
    )


def test_gateway_returns_its_own_durable_receipt_and_replays_after_runtime_restart(gateway_setup):
    """Breaks if the gateway returns another queued order or claims task receipt."""
    from cli_agent_orchestrator.services import work_launch_gateway as gateway_module

    repository, principal, provider, own_context, own_backend, queued_backend = gateway_setup
    gateway = gateway_module.DurableLaunchGateway(provider)

    first = gateway.admit(principal, request(gateway_module))
    other = gateway.admit(principal, request(gateway_module, selection="queued"))
    assert other.work_item_id != first.work_item_id

    restarted_runtime, restarted_backend = make_runtime(repository)
    restarted_provider = RuntimeProvider({(principal.id, "own"): restarted_runtime})
    replay = gateway_module.DurableLaunchGateway(restarted_provider).admit(
        principal, request(gateway_module)
    )

    assert replay == first
    assert first.state == "queued"
    assert durable_counts(repository) == (2, 2, 2, 2, 2)
    assert set(first.__dataclass_fields__) == {"work_item_id", "attempt_id", "generation", "state"}
    assert own_backend.effects == []
    assert queued_backend.effects == []
    assert restarted_backend.effects == []

    with repository.connection() as connection:
        own_work = connection.execute(
            "SELECT id, state FROM work_items WHERE id=?", (first.work_item_id,)
        ).fetchone()
        own_order = connection.execute(
            "SELECT attempt_id, generation FROM work_delivery_orders WHERE attempt_id=? AND generation=?",
            (first.attempt_id, first.generation),
        ).fetchone()
    assert tuple(own_work) == (first.work_item_id, "queued")
    assert tuple(own_order) == (first.attempt_id, first.generation)


def test_gateway_fails_closed_for_missing_provider_or_foreign_selector_without_admission(
    gateway_setup,
):
    """Breaks if absent or foreign trusted context reaches durable admission."""
    from cli_agent_orchestrator.services import work_launch_gateway as gateway_module

    repository, principal, provider, _, own_backend, queued_backend = gateway_setup
    foreign = auth._verified_principal(
        "https://issuer.test", "other-owner", [auth.SCOPE_ADMIN], "jwt"
    )

    for gateway, actor, launch_request in (
        (gateway_module.DurableLaunchGateway(None), principal, request(gateway_module)),
        (gateway_module.DurableLaunchGateway(provider), foreign, request(gateway_module)),
        (
            gateway_module.DurableLaunchGateway(provider),
            principal,
            request(gateway_module, selection="missing"),
        ),
    ):
        with pytest.raises(gateway_module.DurableLaunchGatewayError) as raised:
            gateway.admit(actor, launch_request)
        assert raised.value.as_dict() == {
            "code": "launch_context_unavailable",
            "message": "Trusted launch context is unavailable.",
            "retryable": False,
            "required_action": "provision_launch_context",
        }

    with pytest.raises(TypeError):
        gateway_module.DurableLaunchRequest(
            selection="own",
            agent_profile="developer",
            session_name="cao-durable-launch",
            message="forged authority",
            allowed_tools=("tool.read",),
            idempotency_key="forged-1",
        )
    with pytest.raises(TypeError):
        gateway_module.DurableLaunchGateway(provider).admit(
            principal, request(gateway_module), caller_id=foreign.id
        )

    assert durable_counts(repository) == (0, 0, 0, 0, 0)
    assert own_backend.effects == []
    assert queued_backend.effects == []


def test_gateway_sanitizes_an_unexpected_preflight_failure_without_admission(gateway_setup):
    """Breaks if a backend exception body escapes the application boundary."""
    from cli_agent_orchestrator.services import work_launch_gateway as gateway_module

    repository, principal, _, own_context, _, _ = gateway_setup
    backend = FailingPreflightBackend()
    runtime, _ = make_runtime(repository, backend=backend)
    provider = RuntimeProvider({(principal.id, "own"): runtime})

    with pytest.raises(gateway_module.DurableLaunchGatewayError) as raised:
        gateway_module.DurableLaunchGateway(provider).admit(principal, request(gateway_module))

    assert raised.value.as_dict() == {
        "code": "launch_runtime_unavailable",
        "message": "Trusted launch runtime is unavailable.",
        "retryable": False,
        "required_action": "inspect_server_configuration",
    }
    assert "server-private-preflight-detail" not in str(raised.value)
    assert durable_counts(repository) == (0, 0, 0, 0, 0)
    assert backend.effects == []


def test_gateway_derives_a_distinct_durable_identity_for_changed_intent(gateway_setup):
    """Breaks if a changed public intent can claim the prior durable admission."""
    from cli_agent_orchestrator.services import work_launch_gateway as gateway_module

    repository, principal, provider, _, own_backend, queued_backend = gateway_setup
    gateway = gateway_module.DurableLaunchGateway(provider)
    first = gateway.admit(principal, request(gateway_module, "one"))
    changed = gateway.admit(principal, request(gateway_module, "two"))

    assert changed.work_item_id != first.work_item_id
    assert durable_counts(repository) == (2, 2, 2, 2, 2)
    assert own_backend.effects == []
    assert queued_backend.effects == []
