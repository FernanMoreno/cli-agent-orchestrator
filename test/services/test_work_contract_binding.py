"""Attempt bindings preserve exact server-resolved orders, not cached authorization."""

import importlib
import sqlite3
import time

import pytest
from pydantic import ValidationError

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from test.clients.test_work_repository import (
    legacy_admit,
    legacy_v21_store,
    migrate_legacy_v21_store,
)


@pytest.mark.parametrize("payload", ['{"schema_version":1}', '{"schema_version":1,"id":null}'])
def test_dispatch_ddl_rejects_missing_contract_identity(payload):
    from cli_agent_orchestrator.clients.work_dispatch_schema import DISPATCH_SCHEMA

    with sqlite3.connect(":memory:") as connection:
        for statement in DISPATCH_SCHEMA:
            connection.execute(statement)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO work_dispatch_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "attempt",
                    1,
                    "job",
                    "work",
                    "actor",
                    "grant",
                    1,
                    "contract",
                    payload,
                    "a" * 64,
                    "snapshot",
                    time.time(),
                ),
            )


def test_v28_companion_ddl_rejects_missing_contract_version():
    from cli_agent_orchestrator.clients.work_dispatch_schema import DISPATCH_V2_EVIDENCE_SCHEMA

    with sqlite3.connect(":memory:") as connection:
        connection.execute(DISPATCH_V2_EVIDENCE_SCHEMA[0])
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO work_dispatch_v2_evidence VALUES (?,?,?,?)",
                ("attempt", 1, '{"id":"contract"}', "a" * 64),
            )


def context(tmp_path, *, legacy=False):
    models = importlib.import_module("cli_agent_orchestrator.models.work_contract")
    module = importlib.import_module("cli_agent_orchestrator.services.work_contract")
    path = tmp_path / "binding.db"
    repo = legacy_v21_store(path) if legacy else WorkRepository(path)
    if not legacy:
        repo.initialize()
    actor = auth._verified_principal("https://issuer.test", "owner", [auth.SCOPE_ADMIN], "jwt")
    job = repo.create_job(
        project_id="project", principal_id=actor.id, allowed_providers=["mock_cli"], grant_id="root"
    )
    authority = WorkAuthority(repo)
    grant = authority.issue_root(
        actor,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read", "Read"}, paths={str(tmp_path)},
            commands={"/bin/alpha", "/bin/beta"},
        ),
        expires_at=time.time() + 600,
    )
    snapshots = DelegationSnapshots(repo, policy=KnowledgePolicy(repo, job["id"], grant.id, 1))
    snapshot = snapshots.freeze(
        principal=actor,
        job_id=job["id"],
        contract_id="contract",
        binding_key="context",
        request_hash="a" * 64,
        scope="project",
        scope_id="project",
        resolver=lambda conn, principal: ResolvedSnapshot(""),
    )
    admission = dict(
        operation_kind="launch",
        idempotency_key="one",
        request_hash="a" * 64,
        contract_id="contract",
        snapshot_id=snapshot.id,
        provider="mock_cli",
        actor_id=actor.id,
    )
    item = (
        legacy_admit(repo, job, **admission)
        if legacy
        else repo.admit_work(job_id=job["id"], **admission)
    )
    contract = models.EffectiveWorkContract(
        id="contract",
        operation_kind="launch",
        provider="mock_cli",
        backend="test_backend",
        permissions=models.ContractPermissions(tools=("Read",), paths=(str(tmp_path),)),
        resources=models.ContractResources(
            checkout_root=str(tmp_path), write_paths=(str(tmp_path / "target"),), units=1
        ),
        snapshot=models.ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    return models, module, repo, actor, job, authority, grant, item, contract


def test_existing_compares_frozen_order_without_granting_live_authority(tmp_path):
    data = context(tmp_path)
    _, module, repo, actor, _, _, grant, item, contract = data
    binding = bind(data)
    manager = module.WorkContracts(repo)
    arguments = dict(
        attempt_id=item["attempts"][0]["id"],
        generation=1,
        principal_id=actor.id,
        grant_id=grant.id,
        grant_revision=1,
        contract=contract,
    )
    with repo.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET state='finished' WHERE id=?", (arguments["attempt_id"],)
        )
        assert manager._existing(connection, **arguments) == binding
        assert manager._existing(connection, **dict(arguments, attempt_id="missing")) is None
        for changed in (
            {"principal_id": "other"},
            {"grant_revision": 2},
            {"contract": contract.model_copy(update={"backend": "other"})},
        ):
            with pytest.raises(module.ContractConflict):
                manager._existing(connection, **dict(arguments, **changed))
    with pytest.raises(module.ContractConflict):
        manager.revalidate_order(arguments["attempt_id"], generation=1)


def bind(context, *, contract=None, **changes):
    _, module, repo, actor, _, _, grant, item, original = context
    arguments = dict(
        principal=actor,
        attempt_id=item["attempts"][0]["id"],
        generation=1,
        expected_attempt_revision=1,
        grant_id=grant.id,
        expected_grant_revision=1,
        contract=contract or original,
    )
    arguments.update(changes)
    return module.WorkContracts(repo).bind(**arguments)


def test_v2_binding_roundtrips_exact_executable_evidence_and_rejects_digest_change(tmp_path):
    data = context(tmp_path)
    models, module, repo, actor, _, _, grant, item, original = data
    identities = tuple(
        models.ExecutableIdentity(
            command_token=token,
            content_reference="sha256:" + digest,
            sha256_digest=digest,
            elf_machine="x86_64",
            elf_class="ELF64",
            endianness="little",
        )
        for token, digest in (("/bin/alpha", "a" * 64), ("/bin/beta", "b" * 64))
    )
    v2 = models.EffectiveWorkContractV2(
        **{
            **original.model_dump(),
            "schema_version": 2,
            "permissions": original.permissions.model_copy(
                update={"commands": ("/bin/alpha", "/bin/beta")}
            ),
            "executable_identities": identities,
        }
    )
    first = bind(data, contract=v2)
    assert first.contract == v2
    assert first.contract_hash == v2.canonical_hash()
    assert bind(data, contract=v2) == first
    restored = module.WorkContracts(WorkRepository(repo.path)).revalidate_order(
        first.attempt_id, generation=1
    )
    assert restored.contract.executable_identities == identities
    changed = v2.model_copy(
        update={
            "executable_identities": (
                identities[0].model_copy(
                    update={"sha256_digest": "c" * 64, "content_reference": "sha256:" + "c" * 64}
                ),
                identities[1],
            )
        }
    )
    with pytest.raises(module.ContractConflict):
        bind(data, contract=changed)
    with repo.read_snapshot() as connection:
        row = connection.execute(
            "SELECT contract_json,contract_hash FROM work_dispatch_v2_evidence WHERE attempt_id=?",
            (item["attempts"][0]["id"],),
        ).fetchone()
        assert tuple(row) == (v2.canonical_json(), v2.canonical_hash())


def test_v1_contract_input_without_explicit_version_keeps_historical_default(tmp_path):
    data = context(tmp_path)
    _, module, _, _, _, _, _, _, contract = data
    payload = contract.model_dump()
    payload.pop("schema_version")
    assert module.WorkContracts._contract(payload) == contract


def test_binding_roundtrip_hash_and_immutable_retry(tmp_path):
    data = context(tmp_path)
    models, module, repo, actor, job, _, _, item, contract = data
    first = bind(data)
    assert first.contract_hash == contract.canonical_hash()
    assert bind(data) == first
    restored = module.WorkContracts(WorkRepository(repo.path)).revalidate_order(
        first.attempt_id, generation=1
    )
    assert restored == first
    assert restored.contract.model.status == "unknown"
    assert restored.contract.snapshot.delivered_hash == contract.snapshot.delivered_hash
    changed = contract.model_copy(update={"backend": "another_backend"})
    with pytest.raises(module.ContractConflict):
        bind(data, contract=changed)
    with repo.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM work_dispatch_bindings")


def test_contract_is_strict_frozen_closed_and_hash_is_canonical(tmp_path):
    models, _, _, _, _, _, _, _, contract = context(tmp_path)
    with pytest.raises(ValidationError):
        contract.provider = "other"
    for mutation in ({"schema_version": True}, {"schema_version": 2}, {"prompt": "secret"}):
        with pytest.raises(ValidationError):
            models.EffectiveWorkContract.model_validate(dict(contract.model_dump(), **mutation))
    a = models.ContractPermissions(tools=("Read", "Edit"))
    b = models.ContractPermissions(tools=("Edit", "Read"))
    assert (
        contract.model_copy(update={"permissions": a}).canonical_hash()
        == contract.model_copy(update={"permissions": b}).canonical_hash()
    )


def test_contract_model_rejects_oversized_combined_json(tmp_path):
    models, _, _, _, _, _, _, _, contract = context(tmp_path)
    payload = contract.model_dump()
    payload["permissions"]["commands"] = tuple(str(i) + "x" * 3000 for i in range(12))
    with pytest.raises(ValidationError):
        models.EffectiveWorkContract.model_validate(payload)


@pytest.mark.parametrize(
    "mutation",
    ["provider", "contract_id", "snapshot_id", "snapshot_hash", "paths", "unknown_value", "secret"],
)
def test_invalid_contract_relationships_cannot_create_binding(tmp_path, mutation):
    data = context(tmp_path)
    models, module, repo, _, _, _, _, _, contract = data
    payload = contract.model_dump()
    if mutation == "provider":
        payload["provider"] = "codex"
    elif mutation == "contract_id":
        payload["id"] = "other-contract"
    elif mutation == "snapshot_id":
        payload["snapshot"]["id"] = "missing"
    elif mutation == "snapshot_hash":
        payload["snapshot"]["delivered_hash"] = "f" * 64
    elif mutation == "paths":
        payload["resources"]["write_paths"] = (str(tmp_path.parent / "escape"),)
    elif mutation == "unknown_value":
        payload["model"]["value"] = "invented"
    else:
        payload["model"] = {
            "status": "known",
            "value": "AKIAIOSFODNN7EXAMPLE",
            "provenance": "launch_config",
        }
    with pytest.raises((ValueError, PermissionError)):
        bind(data, contract=payload)
    with repo.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_dispatch_bindings").fetchone()[0] == 0


def test_authentication_and_live_grant_are_not_replaced_by_binding(tmp_path):
    data = context(tmp_path)
    _, module, repo, actor, _, authority, grant, _, _ = data
    with pytest.raises(PermissionError):
        bind(data, principal={"id": actor.id})
    first = bind(data)
    authority.revoke(actor, grant_id=grant.id, expected_grant_revision=1, reason="revoked")
    with pytest.raises(PermissionError):
        module.WorkContracts(repo).revalidate_order(first.attempt_id, generation=1)


def test_bind_helper_rolls_back_binding_and_event_with_caller(tmp_path):
    data = context(tmp_path)
    _, module, repo, actor, job, _, grant, item, contract = data
    service = module.WorkContracts(repo)
    args = dict(
        principal=actor,
        attempt_id=item["attempts"][0]["id"],
        generation=1,
        expected_attempt_revision=1,
        grant_id=grant.id,
        expected_grant_revision=1,
        contract=contract,
    )
    before = repo.read_events(job["id"])["high_water"]
    with repo.connection() as connection:
        with pytest.raises(ValueError):
            service._bind(connection, **args)
    with pytest.raises(RuntimeError):
        with repo.transaction() as connection:
            repo._verify(connection)
            service._bind(connection, **args)
            raise RuntimeError("later admission failed")
    assert repo.read_events(job["id"])["high_water"] == before
    with repo.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_dispatch_bindings").fetchone()[0] == 0


def test_absent_snapshot_never_authorizes_new_execution(tmp_path):
    data = context(tmp_path)
    models, _, _, _, _, _, _, _, contract = data
    absent = models.ContractSnapshot(state="absent", absence_reason="legacy_parent_has_no_snapshot")
    with pytest.raises(ValueError):
        bind(data, contract=contract.model_copy(update={"snapshot": absent}))


def test_revalidation_rejects_replaced_attempt(tmp_path):
    data = context(tmp_path)
    _, module, repo, _, _, _, _, item, _ = data
    first = bind(data)
    with repo.transaction() as connection:
        connection.execute(
            "INSERT INTO work_attempts(id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) VALUES ('replacement',?,2,2,'mock_cli',?,?)",
            (item["id"], time.time() + 300, time.time()),
        )
    with pytest.raises(ValueError):
        module.WorkContracts(repo).revalidate_order(first.attempt_id, generation=1)


def test_descendants_keep_parent_snapshot_with_their_own_contracts(tmp_path):
    data = context(tmp_path, legacy=True)
    _, module, repo, actor, job, _, grant, parent, contract = data
    descendants = []
    ancestor = parent
    for number in (1, 2):
        child_contract = contract.model_copy(update={"id": "child-contract-" + str(number)})
        child = legacy_admit(
            repo,
            job,
            parent_work_item_id=ancestor["id"],
            operation_kind="launch",
            idempotency_key="child-" + str(number),
            request_hash="a" * 64,
            contract_id=child_contract.id,
            snapshot_id=contract.snapshot.id,
            provider="mock_cli",
            actor_id=actor.id,
        )
        descendants.append((child, child_contract))
        ancestor = child
    migrate_legacy_v21_store(repo)
    bind(data)
    manager = module.WorkContracts(repo)
    for child, child_contract in descendants:
        binding = manager.bind(
            principal=actor,
            attempt_id=child["attempts"][0]["id"],
            generation=1,
            expected_attempt_revision=1,
            grant_id=grant.id,
            expected_grant_revision=1,
            contract=child_contract,
        )
        assert manager.revalidate_order(binding.attempt_id, generation=1) == binding
        assert binding.contract.snapshot == contract.snapshot
    with repo.transaction() as connection:
        connection.execute("UPDATE work_items SET snapshot_id=NULL WHERE id=?", (data[7]["id"],))
    with pytest.raises(module.ContractConflict):
        manager.revalidate_order(binding.attempt_id, generation=1)


@pytest.mark.parametrize("ancestry", ["unrelated", "broken_snapshot", "cycle"])
def test_snapshot_from_nonancestor_or_broken_ancestry_is_rejected(tmp_path, ancestry):
    data = context(tmp_path, legacy=True)
    _, module, repo, actor, job, _, grant, parent, contract = data
    unrelated = legacy_admit(
        repo,
        job,
        operation_kind="launch",
        idempotency_key="other",
        request_hash="a" * 64,
        contract_id="other-contract",
        snapshot_id=contract.snapshot.id,
        provider="mock_cli",
        actor_id=actor.id,
    )
    parent_id = unrelated["id"] if ancestry in {"unrelated", "cycle"} else parent["id"]
    child_contract = contract.model_copy(update={"id": "child-contract"})
    child = legacy_admit(
        repo,
        job,
        parent_work_item_id=parent_id,
        operation_kind="launch",
        idempotency_key="child",
        request_hash="a" * 64,
        contract_id=child_contract.id,
        snapshot_id=contract.snapshot.id,
        provider="mock_cli",
        actor_id=actor.id,
    )
    migrate_legacy_v21_store(repo)
    with repo.transaction() as connection:
        if ancestry == "broken_snapshot":
            connection.execute("UPDATE work_items SET snapshot_id=NULL WHERE id=?", (parent_id,))
        elif ancestry == "cycle":
            connection.execute(
                "UPDATE work_items SET parent_work_item_id=? WHERE id=?", (child["id"], parent_id)
            )
    with pytest.raises(module.ContractConflict):
        module.WorkContracts(repo).bind(
            principal=actor,
            attempt_id=child["attempts"][0]["id"],
            generation=1,
            expected_attempt_revision=1,
            grant_id=grant.id,
            expected_grant_revision=1,
            contract=child_contract,
        )


def _v2_model_type():
    models = importlib.import_module("cli_agent_orchestrator.models.work_contract")
    assert hasattr(models, "EffectiveWorkContractV2"), "v2 work contract model is missing"
    return models.EffectiveWorkContractV2


def _v2_identity(**changes):
    identity = {
        "command_token": "/usr/bin/tool",
        "content_reference": "sha256:" + "a" * 64,
        "sha256_digest": "a" * 64,
        "elf_machine": "x86_64",
        "elf_class": "ELF64",
        "endianness": "little",
        "static": True,
    }
    identity.update(changes)
    return identity


def _v2_payload(**changes):
    return {
        "schema_version": 2,
        "id": "contract",
        "operation_kind": "launch",
        "provider": "mock_cli",
        "backend": "test_backend",
        "permissions": {"commands": ("/usr/bin/tool",)},
        "resources": {"checkout_root": "/workspace", "units": 1},
        "snapshot": {
            "state": "absent",
            "absence_reason": "legacy_parent_has_no_snapshot",
        },
        "executable_identities": (_v2_identity(),),
        **changes,
    }


def test_v1_canonical_hash_remains_byte_compatible():
    models = importlib.import_module("cli_agent_orchestrator.models.work_contract")
    contract = models.EffectiveWorkContract(
        id="contract",
        operation_kind="launch",
        provider="mock_cli",
        backend="test_backend",
        permissions=models.ContractPermissions(),
        resources=models.ContractResources(checkout_root="/workspace", units=1),
        snapshot=models.ContractSnapshot(
            state="absent", absence_reason="legacy_parent_has_no_snapshot"
        ),
    )

    assert contract.canonical_hash() == (
        "80082b3ac353dc9a10eeac78171d84b8dcaa2eaadb7984960e8ba142257a5412"
    )


def test_effective_work_contract_v2_roundtrips_and_sorts_identities():
    contract_type = _v2_model_type()
    second = _v2_identity(
        command_token="/usr/local/bin/another",
        content_reference="sha256:" + "b" * 64,
        sha256_digest="b" * 64,
    )
    contract = contract_type.model_validate(
        _v2_payload(
            permissions={"commands": ("/usr/local/bin/another", "/usr/bin/tool")},
            executable_identities=(second, _v2_identity()),
        )
    )

    restored = contract_type.model_validate_json(contract.canonical_json())
    assert contract.executable_identities[0].command_token == "/usr/bin/tool"
    assert restored == contract
    assert restored.canonical_json() == contract.canonical_json()
    assert restored.canonical_hash() == contract.canonical_hash()


@pytest.mark.parametrize("version", [1, True, 2.0, "2"])
def test_effective_work_contract_v2_rejects_non_integer_or_wrong_version(version):
    contract_type = _v2_model_type()
    with pytest.raises(ValidationError):
        contract_type.model_validate(_v2_payload(schema_version=version))


def test_effective_work_contract_v2_rejects_extra_fields():
    contract_type = _v2_model_type()
    with pytest.raises(ValidationError):
        contract_type.model_validate(_v2_payload(unrecognized="secret"))


def test_executable_identity_rejects_dynamic_state():
    models = importlib.import_module("cli_agent_orchestrator.models.work_contract")
    with pytest.raises(ValidationError):
        models.ExecutableIdentity.model_validate(_v2_identity(static=False))


def test_executable_identity_forbids_interpreter_field():
    models = importlib.import_module("cli_agent_orchestrator.models.work_contract")
    with pytest.raises(ValidationError):
        models.ExecutableIdentity.model_validate(_v2_identity(interpreter=None))


def test_executable_identity_rejects_nul_in_command_token():
    models = importlib.import_module("cli_agent_orchestrator.models.work_contract")
    with pytest.raises(ValidationError):
        models.ExecutableIdentity.model_validate(
            _v2_identity(command_token="/usr/bin/tool\x00suffix")
        )


@pytest.mark.parametrize("command_token", ["/", "/usr/bin/tool name", "/usr/bin/tool;id"])
def test_executable_identity_enforces_command_token_grammar(command_token):
    models = importlib.import_module("cli_agent_orchestrator.models.work_contract")
    with pytest.raises(ValidationError):
        models.ExecutableIdentity.model_validate(_v2_identity(command_token=command_token))


@pytest.mark.parametrize(
    "identity_change",
    [
        {"elf_machine": "i386", "elf_class": "ELF32"},
        {"elf_machine": "arm", "elf_class": "ELF32"},
        {"elf_machine": "aarch64", "elf_class": "ELF64"},
        {"elf_class": "ELF32"},
    ],
)
def test_executable_identity_rejects_unsupported_machine_or_abi(identity_change):
    models = importlib.import_module("cli_agent_orchestrator.models.work_contract")
    with pytest.raises(ValidationError):
        models.ExecutableIdentity.model_validate(_v2_identity(**identity_change))


def test_executable_identity_rejects_non_little_endian():
    models = importlib.import_module("cli_agent_orchestrator.models.work_contract")
    with pytest.raises(ValidationError):
        models.ExecutableIdentity.model_validate(_v2_identity(endianness="big"))


@pytest.mark.parametrize(
    "identity_change",
    [
        {"command_token": "usr/bin/tool"},
        {"command_token": "/usr/bin/../bin/tool"},
        {"command_token": "/usr/bin/tool/"},
        {"content_reference": "sha256:ABCDEF"},
        {"content_reference": "sha256:" + "b" * 64},
        {"sha256_digest": "A" * 64},
        {"elf_machine": "riscv64"},
        {"elf_class": "ELF128"},
        {"endianness": "middle"},
    ],
)
def test_effective_work_contract_v2_rejects_invalid_executable_identity(identity_change):
    contract_type = _v2_model_type()
    payload = _v2_payload(executable_identities=(_v2_identity(**identity_change),))
    with pytest.raises(ValidationError):
        contract_type.model_validate(payload)


@pytest.mark.parametrize(
    "permissions,identities",
    [
        ({"commands": ()}, (_v2_identity(),)),
        ({"commands": ("/usr/bin/tool",)}, ()),
        (
            {"commands": ("/usr/bin/tool",)},
            (_v2_identity(), _v2_identity()),
        ),
        (
            {"commands": ("/usr/bin/another",)},
            (_v2_identity(),),
        ),
    ],
)
def test_effective_work_contract_v2_requires_one_to_one_command_mapping(permissions, identities):
    contract_type = _v2_model_type()
    with pytest.raises(ValidationError):
        contract_type.model_validate(
            _v2_payload(permissions=permissions, executable_identities=identities)
        )
