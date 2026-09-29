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
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler
from cli_agent_orchestrator.services.work_origin import WorkOrigins


def runtime_module():
    """Keep RED a test failure while the internal runtime does not exist yet."""
    name = "cli_agent_orchestrator.services.work_launch_runtime"
    assert importlib.util.find_spec(name) is not None, "T035 launch runtime is missing"
    return importlib.import_module(name)


def test_launch_runtime_pairs_origin_admission_and_receipt_owners(tmp_path):
    repository = WorkRepository(tmp_path / "origin-runtime.sqlite3")

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
    repository = WorkRepository(tmp_path / "launch-runtime.sqlite3")
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
            tools={"knowledge.read", "tool.read"}, paths={str(tmp_path)},
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
            "WHERE attempt_id=? AND generation=?",
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
def test_omitted_selection_rechecks_uniqueness_before_work_admission(
    trusted_setup, mutation_phase
):
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
    """The final SQLite transaction revalidates the exact resolved provision."""
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
