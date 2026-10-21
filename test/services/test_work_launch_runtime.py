"""The internal launch runtime resolves trusted setup before durable admission."""

import hashlib
import importlib
import importlib.util
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContract,
    EffectiveWorkContractV2,
    ExecutableIdentity,
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
from cli_agent_orchestrator.services.work_origin import WorkOrigins
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _provision_work_postgres_schema(postgres_targets):
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    bootstrap_scope(postgres_targets.main)


def runtime_module():
    """Keep RED a test failure while the internal runtime does not exist yet."""
    name = "cli_agent_orchestrator.services.work_launch_runtime"
    assert importlib.util.find_spec(name) is not None, "T035 launch runtime is missing"
    return importlib.import_module(name)


def test_launch_runtime_pairs_origin_admission_and_receipt_owners(tmp_path):
    repository = WorkRepository(tmp_path / "origin-runtime")

    runtime = runtime_module().LaunchRuntime(
        repository,
        backends={},
        delivery_adapters={},
    )

    assert isinstance(runtime.origins, WorkOrigins)
    assert runtime.origins.repository is repository
    assert runtime._admission.origins is runtime.origins


class ProtectedFakeBackend(TmuxBackend):
    """A port fake: admission may preflight it but it must never create a terminal."""

    def __init__(self):
        self.preflights = []
        self.effects = []

    def preflight_work(self, contract):
        self.preflights.append(contract)

    def create_session(self, *args, **kwargs):
        self.effects.append((args, kwargs))
        raise AssertionError("launch runtime must not create a terminal")


@pytest.fixture
def trusted_setup(tmp_path):
    repository = WorkRepository(tmp_path / "launch-runtime")
    repository.initialize()
    WorkScheduler(repository).configure(
        capacity=2, max_queue=100, aging_seconds=10, expected_policy_revision=0
    )
    principal = auth._verified_principal(
        "https://issuer.test", "launch-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    job = repository.create_job(
        project_id="project",
        principal_id=principal.id,
        allowed_providers=["mock_cli"],
        grant_id="root-launch",
        budget={"scheduler_units": 100},
    )
    authority = WorkAuthority(repository)
    grant = authority.issue_root(
        principal,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read", "tool.read"},
            paths={str(tmp_path)},
            commands={"/bin/alpha", "/bin/beta"},
        ),
        expires_at=time.time() + 600,
    )
    snapshot = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], grant.id, 1)
    ).freeze(
        principal=principal,
        job_id=job["id"],
        contract_id="trusted-launch-contract",
        binding_key="trusted-launch-context",
        request_hash=hashlib.sha256(b"trusted launch context").hexdigest(),
        scope="project",
        scope_id="project",
        resolver=lambda connection, principal: ResolvedSnapshot("frozen launch context"),
    )
    contract = EffectiveWorkContract(
        id="trusted-launch-contract",
        operation_kind="launch",
        provider="mock_cli",
        backend="test",
        permissions=ContractPermissions(tools=("tool.read",), paths=(str(tmp_path),)),
        resources=ContractResources(
            checkout_root=str(tmp_path),
            write_paths=(str(tmp_path / "launch-work"),),
            units=1,
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
        model=ObservedValue(status="known", value="mock-model", provenance="launch_config"),
    )
    return repository, principal, authority, job, grant, contract


def make_runtime(setup):
    module = runtime_module()
    repository, _, _, _, _, _ = setup
    backend = ProtectedFakeBackend()
    runtime = module.LaunchRuntime(
        repository,
        backends={"test": backend},
        delivery_adapters={("launch", 1): launch_adapter()},
    )
    return module, runtime, backend


def provision(setup, *, selection="opaque"):
    repository, principal, _, job, grant, contract = setup
    return WorkProvisioning(repository).provision_launch(
        principal,
        subject=principal,
        selector=selection,
        expected_revision=0,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        contract=contract,
        adapter_version=1,
        lease_seconds=300,
    )


def provision_for_principal(setup, principal, *, selection):
    """Provision an independent owner so omitted-selection lookup can prove scoping."""
    repository, _, _, _, _, contract = setup
    root = repository.path.parent
    project_id = f"project-{principal.subject}"
    job = repository.create_job(
        project_id=project_id,
        principal_id=principal.id,
        allowed_providers=["mock_cli"],
        grant_id=f"root-{principal.subject}",
        budget={"scheduler_units": 100},
    )
    grant = WorkAuthority(repository).issue_root(
        principal,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read", "tool.read"}, paths={str(root)}),
        expires_at=time.time() + 600,
    )
    contract_id = f"contract-{principal.subject}"
    snapshot = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision)
    ).freeze(
        principal=principal,
        job_id=job["id"],
        contract_id=contract_id,
        binding_key=f"snapshot-{principal.subject}",
        request_hash=hashlib.sha256(principal.id.encode()).hexdigest(),
        scope="project",
        scope_id=project_id,
        resolver=lambda connection, actor: ResolvedSnapshot("foreign frozen launch context"),
    )
    foreign_contract = contract.model_copy(
        update={
            "id": contract_id,
            "resources": contract.resources.model_copy(
                update={"write_paths": (str(root / f"{principal.subject}-work"),)}
            ),
            "snapshot": ContractSnapshot(
                state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
            ),
        }
    )
    return WorkProvisioning(repository).provision_launch(
        principal,
        subject=principal,
        selector=selection,
        expected_revision=0,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        contract=foreign_contract,
        adapter_version=1,
        lease_seconds=300,
    )


def intent(module, message="review this change"):
    return module.LaunchIntent(
        agent_profile="developer",
        session_name="cao-durable-launch",
        message=message,
        allowed_tools=("tool.read",),
    )


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


def test_runtime_resolves_frozen_context_and_replays_one_durable_admission(trusted_setup):
    module = runtime_module()
    active_provision = provision(trusted_setup)
    module, runtime, backend = make_runtime(trusted_setup)
    _, principal, _, _, _, contract = trusted_setup

    resolved = runtime.resolve_launch(principal, "opaque", intent(module))
    payload = json.loads(resolved.envelope.payload_json)
    assert resolved.contract == contract
    assert resolved.provision_ref == active_provision
    assert resolved.idempotency_key == f"launch-v1-{resolved.request_hash}"
    assert payload["allowed_tools"] == ["tool.read"]
    assert payload["terminal_id"] == resolved.terminal_id
    assert resolved.contract.model.value == "mock-model"
    assert resolved.contract.resources.checkout_root == str(trusted_setup[0].path.parent)

    first = runtime.admit_launch(principal, resolved)
    _, restarted, restarted_backend = make_runtime(trusted_setup)
    replay = restarted.admit_launch(
        principal, restarted.resolve_launch(principal, "opaque", intent(module))
    )

    assert replay == first
    assert first.state == "queued"
    assert durable_counts(trusted_setup[0]) == (1, 1, 1, 1, 1)
    assert len(backend.preflights) == 1
    assert restarted_backend.preflights == []
    assert backend.effects == []


def test_runtime_carries_provisioned_v2_identity_through_binding_and_preflight(trusted_setup):
    repository, principal, authority, job, grant, original = trusted_setup
    identities = tuple(
        ExecutableIdentity(
            command_token=token,
            content_reference="sha256:" + digest,
            sha256_digest=digest,
            elf_machine="x86_64",
            elf_class="ELF64",
            endianness="little",
        )
        for token, digest in (("/bin/alpha", "a" * 64), ("/bin/beta", "b" * 64))
    )
    contract = EffectiveWorkContractV2(
        **{
            **original.model_dump(),
            "schema_version": 2,
            "permissions": original.permissions.model_copy(
                update={"commands": ("/bin/alpha", "/bin/beta")}
            ),
            "executable_identities": identities,
        }
    )
    setup = (repository, principal, authority, job, grant, contract)
    provision(setup)
    module, runtime, backend = make_runtime(setup)
    resolved = runtime.resolve_launch(principal, "opaque", intent(module))
    assert resolved.contract == contract
    assert resolved.contract.executable_identities == identities
    first = runtime.admit_launch(principal, resolved)
    assert backend.preflights[0].executable_identities == identities
    with repository.read_snapshot() as connection:
        row = connection.execute(
            "SELECT contract_json,contract_hash FROM work_dispatch_v2_evidence "
            "WHERE attempt_id=%s AND generation=%s",
            (first.attempt_id, first.generation),
        ).fetchone()
        assert tuple(row) == (contract.canonical_json(), contract.canonical_hash())
    _, restarted, restarted_backend = make_runtime(setup)
    replay = restarted.admit_launch(
        principal, restarted.resolve_launch(principal, "opaque", intent(module))
    )
    assert replay == first
    assert restarted_backend.preflights == []
    assert backend.effects == []


def test_omitted_selection_queues_the_only_active_provision_for_verified_principal(trusted_setup):
    module = runtime_module()
    active_provision = provision(trusted_setup)
    module, runtime, backend = make_runtime(trusted_setup)
    repository, principal, _, _, _, _ = trusted_setup

    resolved = runtime.resolve_launch(
        principal, None, intent(module), idempotency_key="internal-override"
    )
    receipt = runtime.admit_launch(principal, resolved)

    assert resolved.selection == active_provision.selector
    assert resolved.provision_ref == active_provision
    assert resolved.idempotency_key == f"launch-v1-{resolved.request_hash}"
    assert receipt.work_item_id
    assert receipt.state == "queued"
    assert repository.get_work(receipt.work_item_id)["state"] == "queued"
    assert durable_counts(repository) == (1, 1, 1, 1, 1)
    assert len(backend.preflights) == 1
    assert backend.effects == []


@pytest.mark.parametrize(
    "selections",
    [(), ("first", "second")],
    ids=["zero-active-provisions", "multiple-active-provisions"],
)
def test_omitted_selection_fails_closed_without_exactly_one_active_provision(
    trusted_setup, selections, monkeypatch
):
    module = runtime_module()
    for selection in selections:
        provision(trusted_setup, selection=selection)
    module, runtime, backend = make_runtime(trusted_setup)
    repository, principal, _, _, _, _ = trusted_setup
    resolve_unique = getattr(runtime._provisioning, "resolve_unique_active_launch", None)
    lookup_calls = []

    def observe_unique_lookup(authenticated_principal):
        lookup_calls.append(authenticated_principal)
        assert callable(resolve_unique), "unique active-provision resolver is missing"
        return resolve_unique(authenticated_principal)

    monkeypatch.setattr(
        runtime._provisioning,
        "resolve_unique_active_launch",
        observe_unique_lookup,
        raising=False,
    )

    with pytest.raises(module.LaunchRuntimeError) as raised:
        runtime.resolve_launch(principal, None, intent(module))

    assert raised.value.as_dict() == {
        "code": "launch_context_unavailable",
        "message": "Trusted launch context is unavailable.",
        "retryable": False,
        "required_action": "provision_launch_context",
    }
    assert lookup_calls == [principal]
    assert durable_counts(repository) == (0, 0, 0, 0, 0)
    assert backend.preflights == []
    assert backend.effects == []


def test_omitted_selection_ignores_retired_history_and_other_principals(trusted_setup):
    module = runtime_module()
    repository, principal, _, _, _, _ = trusted_setup
    provisioning = WorkProvisioning(repository)
    retired = provision(trusted_setup, selection="retired")
    provisioning.retire_launch(
        principal, subject=principal, selector=retired.selector, expected_revision=1
    )
    foreign = auth._verified_principal(
        "https://issuer.test", "foreign-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    provision_for_principal(trusted_setup, foreign, selection="foreign")
    active = provision(trusted_setup, selection="current")
    module, runtime, backend = make_runtime(trusted_setup)

    resolved = runtime.resolve_launch(principal, None, intent(module))
    receipt = runtime.admit_launch(principal, resolved)

    assert resolved.selection == active.selector
    assert receipt.state == "queued"
    assert durable_counts(repository) == (1, 1, 1, 1, 1)
    assert len(backend.preflights) == 1
    assert backend.effects == []


@pytest.mark.parametrize("mutation_phase", ["before_admission", "during_preflight"])
def test_omitted_selection_rechecks_uniqueness_before_work_admission(trusted_setup, mutation_phase):
    module = runtime_module()
    provision(trusted_setup)
    module, runtime, backend = make_runtime(trusted_setup)
    repository, principal, _, _, _, _ = trusted_setup
    resolved = runtime.resolve_launch(principal, None, intent(module))

    def introduce_second_selection(effective_contract):
        backend.preflights.append(effective_contract)
        provision(trusted_setup, selection="second")

    if mutation_phase == "during_preflight":
        backend.preflight_work = introduce_second_selection
    else:
        provision(trusted_setup, selection="second")

    with pytest.raises(module.LaunchRuntimeError) as raised:
        runtime.admit_launch(principal, resolved)

    assert raised.value.as_dict() == {
        "code": "launch_context_unavailable",
        "message": "Trusted launch context is unavailable.",
        "retryable": False,
        "required_action": "provision_launch_context",
    }
    assert durable_counts(repository) == (0, 0, 0, 0, 0)
    assert len(backend.preflights) == (1 if mutation_phase == "during_preflight" else 0)
    assert backend.effects == []


def test_missing_foreign_revoked_or_stale_context_fails_before_admission(trusted_setup):
    module = runtime_module()
    provision(trusted_setup)
    module, runtime, backend = make_runtime(trusted_setup)
    repository, principal, authority, _, grant, _ = trusted_setup
    foreign = auth._verified_principal("https://issuer.test", "other", [auth.SCOPE_ADMIN], "jwt")

    for actor, selection, expected in (
        (
            principal,
            "missing",
            {
                "code": "launch_context_unavailable",
                "message": "Trusted launch context is unavailable.",
                "retryable": False,
                "required_action": "provision_launch_context",
            },
        ),
        (
            foreign,
            "opaque",
            {
                "code": "launch_context_unavailable",
                "message": "Trusted launch context is unavailable.",
                "retryable": False,
                "required_action": "provision_launch_context",
            },
        ),
    ):
        with pytest.raises(module.LaunchRuntimeError) as raised:
            runtime.resolve_launch(actor, selection, intent(module))
        assert raised.value.as_dict() == expected

    authority.revoke(principal, grant_id=grant.id, expected_grant_revision=1, reason="test")
    with pytest.raises(module.LaunchRuntimeError) as raised:
        runtime.resolve_launch(principal, "opaque", intent(module))
    assert raised.value.as_dict() == {
        "code": "launch_context_invalid",
        "message": "Trusted launch context is no longer valid.",
        "retryable": False,
        "required_action": "refresh_launch_context",
    }
    assert durable_counts(repository) == (0, 0, 0, 0, 0)
    assert backend.preflights == []
    assert backend.effects == []


def test_changed_intent_derives_a_distinct_durable_identity(trusted_setup):
    module = runtime_module()
    provision(trusted_setup)
    module, runtime, backend = make_runtime(trusted_setup)
    repository, principal, _, _, _, _ = trusted_setup

    first = runtime.admit_launch(
        principal, runtime.resolve_launch(principal, "opaque", intent(module, "one"))
    )
    changed = runtime.admit_launch(
        principal, runtime.resolve_launch(principal, "opaque", intent(module, "two"))
    )

    assert changed.work_item_id != first.work_item_id
    assert durable_counts(repository) == (2, 2, 2, 2, 2)
    assert len(backend.preflights) == 2
    assert backend.effects == []


@pytest.mark.parametrize("change", ("revision", "job"))
def test_same_selector_changed_provision_derives_a_new_fenced_identity(trusted_setup, change):
    """A changed provision cannot claim the original durable admission identity."""
    repository, principal, authority, job, grant, contract = trusted_setup
    provision(trusted_setup)
    module, runtime, backend = make_runtime(trusted_setup)
    first_resolved = runtime.resolve_launch(principal, "opaque", intent(module))
    first = runtime.admit_launch(principal, first_resolved)

    if change == "revision":
        next_job, next_grant = job, grant
        next_contract = contract.model_copy(
            update={
                "resources": contract.resources.model_copy(
                    update={"write_paths": (str(repository.path.parent / "changed-launch-work"),)}
                )
            }
        )
    else:
        next_job = repository.create_job(
            project_id="project-two",
            principal_id=principal.id,
            allowed_providers=["mock_cli"],
            grant_id="root-launch-two",
            budget={"scheduler_units": 100},
        )
        next_grant = authority.issue_root(
            principal,
            job_id=next_job["id"],
            providers={"mock_cli"},
            permissions=Permissions(
                tools={"knowledge.read", "tool.read"}, paths={str(repository.path.parent)}
            ),
            expires_at=time.time() + 600,
        )
        snapshot = DelegationSnapshots(
            repository,
            policy=KnowledgePolicy(repository, next_job["id"], next_grant.id, 1),
        ).freeze(
            principal=principal,
            job_id=next_job["id"],
            contract_id="trusted-launch-contract-two",
            binding_key="trusted-launch-context-two",
            request_hash=hashlib.sha256(b"trusted second launch context").hexdigest(),
            scope="project",
            scope_id=next_job["project_id"],
            resolver=lambda connection, principal: ResolvedSnapshot("second frozen launch context"),
        )
        next_contract = contract.model_copy(
            update={
                "id": "trusted-launch-contract-two",
                "resources": contract.resources.model_copy(
                    update={"write_paths": (str(repository.path.parent / "second-launch-work"),)}
                ),
                "snapshot": ContractSnapshot(
                    state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
                ),
            }
        )

    WorkProvisioning(repository).provision_launch(
        principal,
        subject=principal,
        selector="opaque",
        expected_revision=1,
        job_id=next_job["id"],
        grant_id=next_grant.id,
        grant_revision=next_grant.revision,
        contract=next_contract,
        adapter_version=1,
        lease_seconds=300,
    )

    changed_resolved = runtime.resolve_launch(principal, "opaque", intent(module))
    assert changed_resolved.idempotency_key != first_resolved.idempotency_key

    if change == "revision":
        with pytest.raises(module.LaunchRuntimeError) as raised:
            runtime.admit_launch(principal, changed_resolved)
        assert raised.value.code == "launch_context_invalid"
        assert durable_counts(repository) == (1, 1, 1, 1, 1)
    else:
        changed = runtime.admit_launch(principal, changed_resolved)
        assert changed.work_item_id != first.work_item_id
        assert durable_counts(repository) == (2, 2, 2, 2, 2)
        assert repository.get_work(changed.work_item_id)["job_id"] == next_job["id"]

    assert repository.get_work(first.work_item_id)["job_id"] == job["id"]
    assert len(backend.preflights) == 2
    assert backend.effects == []


def test_reused_internal_key_conflicts_after_principal_origin_replacement(trusted_setup):
    """A key stays bound to its first durable origin across a job replacement."""
    repository, principal, authority, job, _, contract = trusted_setup
    provision(trusted_setup)
    module, runtime, backend = make_runtime(trusted_setup)
    idempotency_key = "internal-replay-key"
    first_resolved = runtime.resolve_launch(
        principal, "opaque", intent(module), idempotency_key=idempotency_key
    )
    first = runtime.admit_launch(principal, first_resolved)

    next_job = repository.create_job(
        project_id="project-two",
        principal_id=principal.id,
        allowed_providers=["mock_cli"],
        grant_id="root-launch-two",
        budget={"scheduler_units": 100},
    )
    next_grant = authority.issue_root(
        principal,
        job_id=next_job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read", "tool.read"}, paths={str(repository.path.parent)}
        ),
        expires_at=time.time() + 600,
    )
    snapshot = DelegationSnapshots(
        repository,
        policy=KnowledgePolicy(repository, next_job["id"], next_grant.id, next_grant.revision),
    ).freeze(
        principal=principal,
        job_id=next_job["id"],
        contract_id="trusted-launch-contract-two",
        binding_key="trusted-launch-context-two",
        request_hash=hashlib.sha256(b"trusted second launch context").hexdigest(),
        scope="project",
        scope_id=next_job["project_id"],
        resolver=lambda connection, principal: ResolvedSnapshot("second frozen launch context"),
    )
    next_contract = contract.model_copy(
        update={
            "id": "trusted-launch-contract-two",
            "resources": contract.resources.model_copy(
                update={"write_paths": (str(repository.path.parent / "second-launch-work"),)}
            ),
            "snapshot": ContractSnapshot(
                state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
            ),
        }
    )
    replacement = WorkProvisioning(repository).provision_launch(
        principal,
        subject=principal,
        selector="opaque",
        expected_revision=1,
        job_id=next_job["id"],
        grant_id=next_grant.id,
        grant_revision=next_grant.revision,
        contract=next_contract,
        adapter_version=1,
        lease_seconds=300,
    )
    changed_resolved = runtime.resolve_launch(
        principal, "opaque", intent(module), idempotency_key=idempotency_key
    )

    assert replacement.principal_id == principal.id
    assert replacement != first_resolved.provision_ref
    assert changed_resolved.job_id == next_job["id"]
    assert changed_resolved.idempotency_key == first_resolved.idempotency_key
    with pytest.raises(module.LaunchRuntimeError) as raised:
        runtime.admit_launch(principal, changed_resolved)

    assert raised.value.as_dict() == {
        "code": "launch_idempotency_conflict",
        "message": "Launch operation conflicts with an existing server-owned identity.",
        "retryable": False,
        "required_action": "inspect_existing_launch",
    }
    assert repository.get_work(first.work_item_id)["job_id"] == job["id"]
    assert durable_counts(repository) == (1, 1, 1, 1, 1)
    with repository.connection() as connection:
        origins = connection.execute(
            "SELECT work_item_id,job_id FROM work_launch_origin_bindings"
        ).fetchall()
    assert [tuple(origin) for origin in origins] == [(first.work_item_id, job["id"])]
    assert len(backend.preflights) == 1
    assert backend.effects == []


@pytest.mark.parametrize("mutation", ("retire", "replace"))
def test_provision_change_between_admission_transactions_leaves_no_work(trusted_setup, mutation):
    """The final store transaction revalidates the exact resolved provision."""
    repository, principal, _, job, grant, contract = trusted_setup
    provision(trusted_setup)
    module, runtime, backend = make_runtime(trusted_setup)
    resolved = runtime.resolve_launch(principal, "opaque", intent(module))
    provisioning = WorkProvisioning(repository)

    def mutate_during_preflight(effective_contract):
        backend.preflights.append(effective_contract)
        if mutation == "retire":
            provisioning.retire_launch(
                principal, subject=principal, selector="opaque", expected_revision=1
            )
            return
        replacement = contract.model_copy(
            update={
                "resources": contract.resources.model_copy(
                    update={"write_paths": (str(repository.path.parent / "replacement-work"),)}
                )
            }
        )
        provisioning.provision_launch(
            principal,
            subject=principal,
            selector="opaque",
            expected_revision=1,
            job_id=job["id"],
            grant_id=grant.id,
            grant_revision=grant.revision,
            contract=replacement,
            adapter_version=1,
            lease_seconds=300,
        )

    backend.preflight_work = mutate_during_preflight
    with pytest.raises(module.LaunchRuntimeError) as raised:
        runtime.admit_launch(principal, resolved)

    assert raised.value.code == "launch_context_invalid"
    assert durable_counts(repository) == (0, 0, 0, 0, 0)
    assert len(backend.preflights) == 1
    assert backend.effects == []


def test_concurrent_identical_launch_replay_keeps_one_durable_identity(trusted_setup):
    """Concurrent callers may race preflight but can never multiply the durable order."""
    provision(trusted_setup)
    module, runtime, backend = make_runtime(trusted_setup)
    repository, principal, _, _, _, _ = trusted_setup
    resolved = runtime.resolve_launch(principal, "opaque", intent(module))

    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: runtime.admit_launch(principal, resolved), range(8)))

    assert {(item.work_item_id, item.attempt_id, item.generation) for item in receipts} == {
        (receipts[0].work_item_id, receipts[0].attempt_id, receipts[0].generation)
    }
    assert durable_counts(repository) == (1, 1, 1, 1, 1)
    assert backend.effects == []


def test_runtime_conflict_has_a_server_owned_recovery_action(trusted_setup, monkeypatch):
    """A caller cannot replace a server-derived operation key to resolve this conflict."""
    provision(trusted_setup)
    module, runtime, backend = make_runtime(trusted_setup)
    repository, principal, _, _, _, _ = trusted_setup
    resolved = runtime.resolve_launch(principal, "opaque", intent(module))

    def conflict(*args, **kwargs):
        raise WorkConflict("private durable conflict detail")

    monkeypatch.setattr(runtime._admission, "admit", conflict)

    with pytest.raises(module.LaunchRuntimeError) as raised:
        runtime.admit_launch(principal, resolved)

    assert raised.value.as_dict() == {
        "code": "launch_idempotency_conflict",
        "message": "Launch operation conflicts with an existing server-owned identity.",
        "retryable": False,
        "required_action": "inspect_existing_launch",
    }
    assert "private durable conflict detail" not in str(raised.value)
    assert durable_counts(repository) == (0, 0, 0, 0, 0)
    assert backend.effects == []


def _retry_admission_setup(trusted_setup):
    """Create a real durable generation-two plan without running a backend."""
    from uuid import uuid4

    from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
    from cli_agent_orchestrator.services.work_reservations import StoppedWriter
    from cli_agent_orchestrator.services.work_scheduler import WorkScheduler
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, principal, _, _, _, _ = trusted_setup
    event_suffix = uuid4().hex
    module, runtime, backend = make_runtime(trusted_setup)
    provision(trusted_setup)
    initial_handoff = runtime.resolve_launch(principal, "opaque", intent(module, "initial"))
    initial = runtime.admit_launch(principal, initial_handoff)
    scheduler = runtime._admission.scheduler
    held = scheduler.claim_next(actor_id=principal.id)
    assert held is not None and held.attempt_id == initial.attempt_id

    sent = repository.transition_attempt(
        attempt_id=initial.attempt_id,
        generation=1,
        expected_revision=1,
        expected_state="planned",
        target="sent",
        actor_id=principal.id,
        event_id=f"replacement-test-sent-{event_suffix}",
        evidence=TransitionEvidence(
            generation=1,
            expected_generation=1,
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
    )
    reconciled = repository.transition_attempt(
        attempt_id=initial.attempt_id,
        generation=1,
        expected_revision=sent["attempts"][-1]["revision"],
        expected_state="sent",
        target="reconcile",
        actor_id=principal.id,
        event_id=f"replacement-test-reconcile-{event_suffix}",
        evidence=TransitionEvidence(generation=1, expected_generation=1),
    )
    retry_scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda owner: StoppedWriter(
            owner.attempt_id,
            owner.generation,
            owner.revision,
            f"test-stop-{owner.attempt_id}-{owner.generation}",
        ),
    )
    replanned = WorkService(repository).retry_work(
        reconciled["id"],
        scheduler=retry_scheduler,
        expected_revision=reconciled["revision"],
        provider="mock_cli",
        evidence=TransitionEvidence(
            generation=1,
            expected_generation=1,
            reconciliation_authorized=True,
        ),
        actor_id=principal.id,
        lease_seconds=300,
    )
    return module, runtime, backend, principal, initial_handoff, initial, replanned


def test_replacement_admission_binds_generation_and_replay_is_read_only(trusted_setup):
    """Replanned work stays inert until explicit current-authority admission."""
    module, runtime, backend, principal, initial_handoff, initial, replanned = (
        _retry_admission_setup(trusted_setup)
    )
    repository = trusted_setup[0]
    replacement_handoff = runtime.resolve_replacement(
        principal,
        "opaque",
        intent(module, "replacement"),
        work_item_id=initial.work_item_id,
        generation=2,
        idempotency_key="receiver-generation-two",
    )

    assert replacement_handoff.retry_idempotency_key != initial_handoff.idempotency_key
    assert replacement_handoff.generation == 2
    assert replanned["attempts"][-1]["state"] == "planned"
    with repository.transaction() as connection:
        initial_origin_before = connection.execute(
            "SELECT request_hash,idempotency_key FROM work_launch_origin_bindings "
            "WHERE attempt_id=%s AND generation=1",
            (initial.attempt_id,),
        ).fetchone()
        assert (
            connection.execute(
                "SELECT 1 FROM work_scheduler_requests WHERE work_item_id=%s AND generation=2",
                (initial.work_item_id,),
            ).fetchone()
            is None
        )

    first = runtime.admit_replacement(principal, replacement_handoff)
    same_runtime_replay = runtime.admit_replacement(principal, replacement_handoff)
    _, restarted, restarted_backend = make_runtime(trusted_setup)
    replay_handoff = restarted.resolve_replacement(
        principal,
        "opaque",
        intent(module, "replacement"),
        work_item_id=initial.work_item_id,
        generation=2,
        idempotency_key="receiver-generation-two",
    )
    replay = restarted.admit_replacement(principal, replay_handoff)
    assert same_runtime_replay == first
    assert replay == first
    assert restarted_backend.preflights == []
    assert (first.work_item_id, first.generation, first.state) == (
        initial.work_item_id,
        2,
        "queued",
    )
    work = repository.get_work(initial.work_item_id)
    current_attempt = work["attempts"][-1]
    assert current_attempt["generation"] == 2
    assert current_attempt["state"] == "planned"
    with repository.transaction() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_launch_retry_bindings WHERE work_item_id=%s",
                (initial.work_item_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM work_scheduler_requests WHERE work_item_id=%s",
                (initial.work_item_id,),
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM work_attempt_credentials AS credential "
                "JOIN work_attempts AS attempt ON attempt.id=credential.attempt_id "
                "AND attempt.generation=credential.generation WHERE attempt.work_item_id=%s",
                (initial.work_item_id,),
            ).fetchone()[0]
            == 0
        )
        delivery_hashes = connection.execute(
            "SELECT delivery.delivery_hash FROM work_delivery_orders AS delivery "
            "JOIN work_attempts AS attempt ON attempt.id=delivery.attempt_id "
            "AND attempt.generation=delivery.generation WHERE attempt.work_item_id=%s "
            "ORDER BY attempt.generation",
            (initial.work_item_id,),
        ).fetchall()
        assert len(delivery_hashes) == 2
        assert delivery_hashes[0]["delivery_hash"] != delivery_hashes[1]["delivery_hash"]
        current_origin = connection.execute(
            "SELECT id,state,units,attempt_id,generation FROM work_scheduler_requests "
            "WHERE work_item_id=%s AND generation=2",
            (initial.work_item_id,),
        ).fetchone()
        assert tuple(current_origin)[1:] == ("queued", 1, current_attempt["id"], 2)
        retry_origin = connection.execute(
            "SELECT root_attempt_id,prior_attempt_id,prior_scheduler_request_id,retry_actor_id,"
            "current_scheduler_request_id FROM work_launch_retry_bindings "
            "WHERE work_item_id=%s AND generation=2",
            (initial.work_item_id,),
        ).fetchone()
        prior_scheduler = connection.execute(
            "SELECT id FROM work_scheduler_requests WHERE attempt_id=%s AND generation=1 "
            "AND state='released' AND stop_evidence_ref IS NOT NULL",
            (initial.attempt_id,),
        ).fetchone()
        assert prior_scheduler is not None
        assert tuple(retry_origin) == (
            initial.attempt_id,
            initial.attempt_id,
            prior_scheduler["id"],
            principal.id,
            current_origin["id"],
        )
        initial_origin_after = connection.execute(
            "SELECT request_hash,idempotency_key FROM work_launch_origin_bindings "
            "WHERE attempt_id=%s AND generation=1",
            (initial.attempt_id,),
        ).fetchone()
        assert tuple(initial_origin_after) == tuple(initial_origin_before)
        runtime._admission.contracts._revalidate_order(
            connection, current_attempt["id"], generation=2
        )

    original_replay = runtime.admit_launch(principal, initial_handoff)
    assert original_replay == initial
    assert len(backend.effects) == 0
    assert durable_counts(repository) == (1, 2, 2, 2, 2)


def _admitted_replacement(trusted_setup):
    module, runtime, backend, principal, _, initial, _ = _retry_admission_setup(trusted_setup)
    handoff = runtime.resolve_replacement(
        principal,
        "opaque",
        intent(module, "replacement"),
        work_item_id=initial.work_item_id,
        generation=2,
        idempotency_key="receiver-generation-two",
    )
    admitted = runtime.admit_replacement(principal, handoff)
    return module, runtime, principal, initial, handoff, admitted


def _job_high_water(repository, work_item_id):
    with repository.transaction() as connection:
        return connection.execute(
            "SELECT sequence.job_id,sequence.high_water FROM work_event_sequences AS sequence "
            "JOIN work_items AS item ON item.job_id=sequence.job_id WHERE item.id=%s",
            (work_item_id,),
        ).fetchone()


def test_event_retention_after_replacement_admission_keeps_dispatch_provenance(trusted_setup):
    """Pruning the journal neither fails nor erases the immutable replacement facts."""
    from cli_agent_orchestrator.services.work_contract import ContractConflict

    module, runtime, principal, initial, handoff, admitted = _admitted_replacement(trusted_setup)
    repository = trusted_setup[0]
    head = _job_high_water(repository, initial.work_item_id)

    repository.prune_events(head["job_id"], through_sequence=head["high_water"])

    with repository.transaction() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_events WHERE work_item_id=%s",
                (initial.work_item_id,),
            ).fetchone()[0]
            == 0
        )
        runtime._admission.contracts._revalidate_order(
            connection, admitted.attempt_id, generation=2
        )
    assert runtime.admit_replacement(principal, handoff) == admitted

    # A missing event above the retention watermark was never legitimately pruned.
    with pytest.raises(ContractConflict):
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE work_event_sequences SET retained_after=0 WHERE job_id=%s",
                (head["job_id"],),
            )
            runtime._admission.contracts._revalidate_order(
                connection, admitted.attempt_id, generation=2
            )


def test_unpruned_missing_release_event_fails_dispatch_revalidation(trusted_setup):
    from cli_agent_orchestrator.services.work_contract import ContractConflict

    _, runtime, _, initial, _, admitted = _admitted_replacement(trusted_setup)
    repository = trusted_setup[0]
    with pytest.raises(ContractConflict):
        with repository.transaction() as connection:
            connection.execute(
                "DELETE FROM work_events WHERE attempt_id=%s AND event_type='scheduler.released'",
                (initial.attempt_id,),
            )
            runtime._admission.contracts._revalidate_order(
                connection, admitted.attempt_id, generation=2
            )


def test_pruned_release_evidence_fails_replacement_admission_closed(trusted_setup):
    """Initial admission requires the actual durable release/replan events."""
    module, runtime, _, principal, _, initial, _ = _retry_admission_setup(trusted_setup)
    repository = trusted_setup[0]
    head = _job_high_water(repository, initial.work_item_id)
    repository.prune_events(head["job_id"], through_sequence=head["high_water"])
    handoff = runtime.resolve_replacement(
        principal,
        "opaque",
        intent(module, "replacement"),
        work_item_id=initial.work_item_id,
        generation=2,
        idempotency_key="receiver-generation-two",
    )

    with pytest.raises(module.LaunchRuntimeError):
        runtime.admit_replacement(principal, handoff)
    with repository.transaction() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_scheduler_requests WHERE work_item_id=%s",
                (initial.work_item_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM work_launch_retry_bindings WHERE work_item_id=%s",
                (initial.work_item_id,),
            ).fetchone()[0]
            == 0
        )


def test_predecessor_cleanup_after_admission_keeps_replacement_dispatchable(trusted_setup):
    """Legitimate cleanup revisions of the stopped generation do not fence the new one."""
    import psycopg

    from cli_agent_orchestrator.services.work_contract import ContractConflict
    from cli_agent_orchestrator.services.work_service import WorkService

    _, runtime, principal, initial, handoff, admitted = _admitted_replacement(trusted_setup)
    repository = trusted_setup[0]
    contracts = runtime._admission.contracts
    prior = repository.get_attempt(initial.attempt_id)

    pending = repository.record_cleanup(
        initial.attempt_id,
        generation=1,
        expected_revision=prior["revision"],
        state="pending",
        actor_id=principal.id,
    )
    # Current policy: an unobserved pending cleanup blocks dispatch of the replacement.
    with pytest.raises(ContractConflict):
        with repository.transaction() as connection:
            contracts._revalidate_order(connection, admitted.attempt_id, generation=2)
    repository.record_cleanup(
        initial.attempt_id,
        generation=1,
        expected_revision=pending["attempts"][0]["revision"],
        state="failed",
        actor_id=principal.id,
    )
    cleaned = WorkService(repository).cleanup_attempt(
        initial.attempt_id, lambda: True, actor_id=principal.id
    )
    assert cleaned["attempts"][0]["cleanup_state"] == "complete"
    assert cleaned["attempts"][0]["revision"] > prior["revision"]

    with repository.transaction() as connection:
        contracts._revalidate_order(connection, admitted.attempt_id, generation=2)
    assert runtime.admit_replacement(principal, handoff) == admitted

    # The released stop proof compared exactly above cannot be altered afterwards.
    with pytest.raises(psycopg.errors.CheckViolation):
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE work_scheduler_requests SET stop_evidence_ref=%s "
                "WHERE attempt_id=%s AND generation=1",
                ("altered-stop-proof", initial.attempt_id),
            )
    with repository.transaction() as connection:
        contracts._revalidate_order(connection, admitted.attempt_id, generation=2)


def test_replacement_handoff_from_another_runtime_is_rejected(trusted_setup):
    """A resolved generation cannot be moved to another LaunchRuntime owner."""
    module, runtime, _, principal, _, initial, _ = _retry_admission_setup(trusted_setup)
    _, foreign_runtime, _ = make_runtime(trusted_setup)
    handoff = runtime.resolve_replacement(
        principal,
        "opaque",
        intent(module, "replacement"),
        work_item_id=initial.work_item_id,
        generation=2,
        idempotency_key="receiver-generation-two",
    )

    with pytest.raises(module.LaunchRuntimeError):
        foreign_runtime.admit_replacement(principal, handoff)

    assert durable_counts(trusted_setup[0]) == (1, 2, 1, 1, 1)


def test_replacement_identity_conflict_is_read_only(trusted_setup):
    """A current generation cannot be rebound to another action key or payload."""
    module, runtime, _, principal, _, initial, _ = _retry_admission_setup(trusted_setup)
    repository = trusted_setup[0]
    accepted = runtime.resolve_replacement(
        principal,
        "opaque",
        intent(module, "replacement"),
        work_item_id=initial.work_item_id,
        generation=2,
        idempotency_key="receiver-generation-two",
    )
    runtime.admit_replacement(principal, accepted)
    before = durable_counts(repository)

    with pytest.raises(module.LaunchRuntimeError):
        runtime.resolve_replacement(
            principal,
            "opaque",
            intent(module, "replacement"),
            work_item_id=initial.work_item_id,
            generation=2,
            idempotency_key="different-action",
        )
    with pytest.raises(module.LaunchRuntimeError):
        runtime.resolve_replacement(
            principal,
            "opaque",
            intent(module, "changed replacement"),
            work_item_id=initial.work_item_id,
            generation=2,
            idempotency_key="receiver-generation-two",
        )

    assert durable_counts(repository) == before
    with repository.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_launch_retry_bindings WHERE work_item_id=%s",
                (initial.work_item_id,),
            ).fetchone()[0]
            == 1
        )


def test_replacement_origin_rows_are_immutable_in_postgres(trusted_setup):
    """The database trigger protects replacement provenance after admission."""
    import psycopg

    module, runtime, _, principal, _, initial, _ = _retry_admission_setup(trusted_setup)
    repository = trusted_setup[0]
    handoff = runtime.resolve_replacement(
        principal,
        "opaque",
        intent(module, "replacement"),
        work_item_id=initial.work_item_id,
        generation=2,
        idempotency_key="receiver-generation-two",
    )
    runtime.admit_replacement(principal, handoff)
    attempt_id = repository.get_work(initial.work_item_id)["attempts"][-1]["id"]

    with pytest.raises(psycopg.errors.CheckViolation):
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE work_launch_retry_bindings SET retry_request_hash=%s "
                "WHERE attempt_id=%s AND generation=2",
                ("b" * 64, attempt_id),
            )
    with pytest.raises(psycopg.errors.CheckViolation):
        with repository.transaction() as connection:
            connection.execute(
                "DELETE FROM work_launch_retry_bindings WHERE attempt_id=%s AND generation=2",
                (attempt_id,),
            )

    with repository.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_launch_retry_bindings WHERE attempt_id=%s AND generation=2",
                (attempt_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM work_scheduler_requests WHERE work_item_id=%s AND generation=2",
                (initial.work_item_id,),
            ).fetchone()[0]
            == 1
        )


def test_replacement_requires_explicit_current_provision_selector(trusted_setup):
    """An omitted selector cannot race another active provision into ambiguity."""
    module, runtime, _, principal, _, initial, _ = _retry_admission_setup(trusted_setup)

    with pytest.raises(module.LaunchRuntimeError):
        runtime.resolve_replacement(
            principal,
            None,
            intent(module, "replacement"),
            work_item_id=initial.work_item_id,
            generation=2,
            idempotency_key="receiver-generation-two",
        )

    assert durable_counts(trusted_setup[0]) == (1, 2, 1, 1, 1)


def test_replacement_rejects_a_provision_replaced_after_resolution(trusted_setup):
    """A sealed handoff cannot bind an older active provision revision."""
    module, runtime, backend, principal, _, initial, _ = _retry_admission_setup(trusted_setup)
    repository, _, _, job, grant, contract = trusted_setup
    handoff = runtime.resolve_replacement(
        principal,
        "opaque",
        intent(module, "replacement"),
        work_item_id=initial.work_item_id,
        generation=2,
        idempotency_key="receiver-generation-two",
    )
    before = durable_counts(repository)
    preflights = len(backend.preflights)
    WorkProvisioning(repository).provision_launch(
        principal,
        subject=principal,
        selector="opaque",
        expected_revision=1,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        contract=contract,
        adapter_version=1,
        lease_seconds=300,
    )

    with pytest.raises(module.LaunchRuntimeError):
        runtime.admit_replacement(principal, handoff)

    assert durable_counts(repository) == before
    assert len(backend.preflights) == preflights


def test_replacement_rejects_a_stale_planned_generation_cas(trusted_setup):
    """A sealed handoff cannot bind after the planned attempt revision advances."""
    module, runtime, backend, principal, _, initial, replanned = _retry_admission_setup(
        trusted_setup
    )
    repository = trusted_setup[0]
    attempt = replanned["attempts"][-1]
    handoff = runtime.resolve_replacement(
        principal,
        "opaque",
        intent(module, "replacement"),
        work_item_id=initial.work_item_id,
        generation=2,
        idempotency_key="receiver-generation-two",
    )
    repository.renew_attempt(
        attempt["id"],
        generation=2,
        expected_revision=attempt["revision"],
        lease_seconds=300,
        actor_id=principal.id,
    )
    before = durable_counts(repository)
    preflights = len(backend.preflights)

    with pytest.raises(module.LaunchRuntimeError):
        runtime.admit_replacement(principal, handoff)

    assert durable_counts(repository) == before
    assert len(backend.preflights) == preflights


def test_concurrent_identical_replacement_admission_has_one_order(trusted_setup):
    """Concurrent exact retries converge without multiplying current-generation rows."""
    module, runtime, _, principal, _, initial, _ = _retry_admission_setup(trusted_setup)
    repository = trusted_setup[0]
    handoff = runtime.resolve_replacement(
        principal,
        "opaque",
        intent(module, "replacement"),
        work_item_id=initial.work_item_id,
        generation=2,
        idempotency_key="receiver-generation-two",
    )

    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: runtime.admit_replacement(principal, handoff), range(4)))

    assert {(item.work_item_id, item.attempt_id, item.generation) for item in receipts} == {
        (receipts[0].work_item_id, receipts[0].attempt_id, receipts[0].generation)
    }
    assert receipts[0].generation == 2
    with repository.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_launch_retry_bindings WHERE work_item_id=%s",
                (initial.work_item_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM work_scheduler_requests WHERE work_item_id=%s AND generation=2",
                (initial.work_item_id,),
            ).fetchone()[0]
            == 1
        )


def test_revoked_current_grant_blocks_replacement_before_preflight(trusted_setup):
    """A previously sealed retry context grants no authority after revocation."""
    module, runtime, backend, principal, _, initial, _ = _retry_admission_setup(trusted_setup)
    repository, _, authority, _, grant, _ = trusted_setup
    handoff = runtime.resolve_replacement(
        principal,
        "opaque",
        intent(module, "replacement"),
        work_item_id=initial.work_item_id,
        generation=2,
        idempotency_key="receiver-generation-two",
    )
    before = durable_counts(repository)
    preflights = len(backend.preflights)
    authority.revoke(
        principal,
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
        reason="replacement admission revocation test",
    )

    with pytest.raises(module.LaunchRuntimeError):
        runtime.admit_replacement(principal, handoff)

    assert durable_counts(repository) == before
    assert len(backend.preflights) == preflights
    with repository.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_scheduler_requests WHERE work_item_id=%s AND generation=2",
                (initial.work_item_id,),
            ).fetchone()[0]
            == 0
        )


def test_replacement_enqueue_failure_rolls_back_the_generation_order(trusted_setup, monkeypatch):
    """A late scheduler refusal cannot leave a partially bound replacement."""
    module, runtime, _, principal, _, initial, replanned = _retry_admission_setup(trusted_setup)
    repository = trusted_setup[0]
    handoff = runtime.resolve_replacement(
        principal,
        "opaque",
        intent(module, "replacement"),
        work_item_id=initial.work_item_id,
        generation=2,
        idempotency_key="receiver-generation-two",
    )
    before = durable_counts(repository)

    def refuse_enqueue(*_args, **_kwargs):
        from cli_agent_orchestrator.services.work_scheduler import SchedulerBackpressure

        raise SchedulerBackpressure("test queue refusal")

    monkeypatch.setattr(runtime._admission.scheduler, "_enqueue", refuse_enqueue)
    with pytest.raises(module.LaunchRuntimeError):
        runtime.admit_replacement(principal, handoff)

    assert durable_counts(repository) == before
    with repository.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_launch_retry_bindings WHERE work_item_id=%s",
                (initial.work_item_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM work_delivery_orders WHERE attempt_id=%s",
                (replanned["attempts"][-1]["id"],),
            ).fetchone()[0]
            == 0
        )
