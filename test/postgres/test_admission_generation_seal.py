"""A terminal generation fence is not physical closure or credit retirement."""

from dataclasses import asdict
from test.postgres.test_admission_metadata_credits import grant
from test.postgres.test_admission_reservations import domain, request, shared
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg import errors, sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap

pytestmark = pytest.mark.postgres


def authority(shared):
    target, repository, bound, _ = shared
    issue = getattr(ResourceBootstrap, "issue_terminal_generation_authority", None)
    assert callable(issue), "missing explicit trusted terminal-generation capability"
    bootstrap = ResourceBootstrap(
        repository=SimpleNamespace(resolve_target=lambda: target),
        binding_supplier=lambda selected: bound,
        instance_supplier=lambda: bound.instance_id,
        terminal_generation=True,
    )
    return bootstrap.issue_terminal_generation_authority(bound)


def test_seal_blocks_new_producers_but_preserves_credit_and_closure(shared):
    _, repository, _, module = shared
    storage = domain(shared, 4)
    credit = grant(shared, storage)
    raw_id, mapped_id, active_id = (str(uuid4()) for _ in range(3))
    raw = repository.reserve(raw_id, (request(storage),))
    cost = module.SharedResourceCost(storage_slots=1)
    mapped = repository.reserve_for_cost(mapped_id, cost)
    active = repository.reserve_for_cost(active_id, cost)
    active = repository.transition(active_id, expected_revision=active.revision, state="active")
    used = repository.used(storage.spec.domain_id)
    before = tuple(asdict(c) for c in repository.metadata_credits_current())
    capability = authority(shared)
    seal = repository.seal_current_generation(capability)
    assert repository.seal_current_generation(capability) == seal
    assert seal.instance_id == shared[2].instance_id
    assert seal.process_generation == shared[2].process_generation
    assert repository.used(storage.spec.domain_id) == used
    assert (
        tuple(asdict(c) for c in repository.metadata_credits_current())
        == before
        == (asdict(credit),)
    )
    for fresh in [
        lambda: repository.reserve(str(uuid4()), (request(storage),)),
        lambda: repository.reserve_for_cost(str(uuid4()), cost),
        repository.metadata_coverage_current,
        lambda: repository.transition(raw_id, expected_revision=raw.revision, state="active"),
        lambda: repository.transition(mapped_id, expected_revision=mapped.revision, state="active"),
    ]:
        with pytest.raises(module.SharedAdmissionDenied):
            fresh()
    # Exact acknowledgement is not a new resource grant or a second effect.
    assert repository.reserve(raw_id, (request(storage),)) == raw
    assert repository.reserve_for_cost(mapped_id, cost) == mapped
    assert repository.transition(active_id, expected_revision=1, state="active") == active
    assert repository.read_operation(raw_id) == raw
    repository.transition(raw_id, expected_revision=1, state="released")
    repository.transition(mapped_id, expected_revision=1, state="released")
    repository.transition(active_id, expected_revision=2, state="releasing")
    repository.transition(active_id, expected_revision=3, state="released")
    assert repository.used(storage.spec.domain_id) == 1
    assert repository.metadata_credits_current() == (credit,)
    assert repository.audit_usage() == ()


def test_seal_is_durable_and_runtime_append_only(shared):
    from cli_agent_orchestrator.clients.admission_schema import RESOURCE_ADMISSION_MIGRATIONS

    assert (
        RESOURCE_ADMISSION_MIGRATIONS[-1].version == 7
    ), "missing additive generation fence schema"
    target, repository, _, _ = shared
    storage = domain(shared, 2)
    grant(shared, storage)
    capability = authority(shared)
    seal = repository.seal_current_generation(capability)
    relation = sql.Identifier(target.schema, "_cao_resource_generation_seals")
    with connect(target) as connection:
        row = connection.execute(sql.SQL("SELECT seal_id FROM {}").format(relation)).fetchone()
        assert str(row[0]) == seal.seal_id
        for statement in ["UPDATE {} SET reason=reason", "DELETE FROM {}", "TRUNCATE {}"]:
            with pytest.raises(errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute(sql.SQL(statement).format(relation))
    assert repository.seal_current_generation(capability) == seal
    assert repository.used(storage.spec.domain_id) == 1


def test_same_generation_new_bootstrap_acknowledges_original_seal(shared):
    _, repository, _, _ = shared
    storage = domain(shared, 2)
    credit = grant(shared, storage)
    first = repository.seal_current_generation(authority(shared))
    assert repository.seal_current_generation(authority(shared)) == first
    assert repository.metadata_credits_current() == (credit,)
    assert repository.used(storage.spec.domain_id) == 1


def assert_seal_acl(connection, target):
    relation = target.schema + "." + "_cao_resource_generation_seals"
    expressions = [
        f"has_table_privilege(current_user,%s,'{privilege}')"
        for privilege in [
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
            "MAINTAIN",
        ]
    ]
    row = connection.execute("SELECT " + ",".join(expressions), (relation,) * 8).fetchone()
    assert tuple(row) == (True, True, False, False, False, False, False, False)
    assert (
        connection.execute(
            """SELECT count(*) FROM pg_catalog.pg_class c,
            LATERAL aclexplode(CASE WHEN cardinality(c.relacl)>0 THEN c.relacl ELSE acldefault('r',c.relowner) END) a
            WHERE c.oid=to_regclass(%s) AND a.grantee=0""",
            (relation,),
        ).fetchone()[0]
        == 0
    )
    assert (
        connection.execute(
            """SELECT count(*) FROM pg_catalog.pg_attribute c,
            LATERAL aclexplode(CASE WHEN cardinality(c.attacl)>0 THEN c.attacl END) a
            WHERE c.attrelid=to_regclass(%s) AND a.grantee IN
                (0,(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user))""",
            (relation,),
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    "privilege",
    [
        "UPDATE",
        "DELETE",
        "UPDATE(reason)",
        "REFERENCES(seal_id)",
        "PUBLIC_INSERT",
        "PUBLIC_COLUMN_INSERT",
    ],
)
def test_unsafe_seal_acl_is_rejected_and_restored(shared, privilege):
    from cli_agent_orchestrator.clients.postgres.migrations import (
        SchemaMismatch,
        protect_runtime_authority_tables,
    )

    target, repository, _, _ = shared
    storage = domain(shared, 2)
    credit = grant(shared, storage)
    public = privilege.startswith("PUBLIC_")
    actual = (
        "INSERT (reason)"
        if privilege == "PUBLIC_COLUMN_INSERT"
        else "INSERT" if public else privilege
    )
    relation = sql.Identifier(target.schema, "_cao_resource_generation_seals")
    grantee = sql.SQL("PUBLIC") if public else sql.Identifier(target.user)
    try:
        with connect(target.migration_target()) as owner:
            owner.execute(
                sql.SQL("GRANT {} ON TABLE {} TO {}").format(sql.SQL(actual), relation, grantee)
            )
        with pytest.raises(SchemaMismatch):
            repository.metadata_credits_current()
    finally:
        with connect(target.migration_target()) as owner:
            protect_runtime_authority_tables(owner, target)
    with connect(target, readonly=True) as reader:
        assert_seal_acl(reader, target)
    assert repository.metadata_credits_current() == (credit,)
    assert repository.used(storage.spec.domain_id) == 1


def test_postcommit_binding_ack_loss_retains_original_seal_and_credit(shared, monkeypatch):
    from contextlib import contextmanager

    target, repository, bound, module = shared
    storage = domain(shared, 2)
    credit = grant(shared, storage)
    capability = authority(shared)
    runtime = repository._runtime
    supplier = repository._binding_supplier
    lost = [False]

    @contextmanager
    def lose_ack(**kwargs):
        with runtime(**kwargs) as connection:
            yield connection
        lost[0] = True  # The original context has really committed and closed.

    def read_binding():
        if lost[0]:
            raise module.SharedAdmissionDenied("controlled postcommit binding acknowledgement loss")
        return supplier()

    with monkeypatch.context() as patch:
        patch.setattr(repository, "_runtime", lose_ack)
        patch.setattr(repository, "_binding_supplier", read_binding)
        with pytest.raises(module.SharedCommitUncertain):
            repository.seal_current_generation(capability)
    recovered = repository.seal_current_generation(authority(shared))
    assert recovered.seal_id == capability.seal_id
    with connect(target, readonly=True) as connection:
        row = connection.execute(
            sql.SQL("SELECT count(*),min(binding_sha256) FROM {}").format(
                sql.Identifier(target.schema, "_cao_resource_generation_seals")
            )
        ).fetchone()
        assert tuple(row) == (1, module.binding_digest(capability.binding))
    assert repository.metadata_credits_current() == (credit,)
    assert repository.used(storage.spec.domain_id) == 1
    assert repository.audit_usage() == ()


def test_sealed_generation_preserves_revocation_and_does_not_block_another_generation(shared):
    from dataclasses import replace
    from test.postgres.test_admission_metadata_credits import ADMIN

    target, repository, bound, module = shared
    storage = domain(shared, 4)
    credit = grant(shared, storage)
    repository.seal_current_generation(authority(shared))
    assert grant(shared, storage, credit_id=credit.credit_id) == credit
    repository.provision_member(
        storage.spec.domain_id,
        bound,
        expected_revision=1,
        principal_id=ADMIN,
        active=False,
    )
    with pytest.raises(module.SharedAdmissionDenied):
        repository.provision_member(
            storage.spec.domain_id,
            bound,
            expected_revision=2,
            principal_id=ADMIN,
            active=True,
        )
    assert repository.metadata_credits_current() == (credit,)
    other_binding = replace(bound, process_generation=str(uuid4()))
    repository.provision_member(
        storage.spec.domain_id,
        other_binding,
        expected_revision=0,
        principal_id=ADMIN,
    )
    other_credit = repository.grant_metadata_credit(
        str(uuid4()),
        storage.spec.domain_id,
        other_binding,
        principal_id=ADMIN,
    )
    other = module.ResourceAdmissionRepository(target, binding_supplier=lambda: other_binding)
    assert other.audit_usage() == ()
    assert other.metadata_coverage_current().credits == (other_credit,)
    operation = other.reserve_for_cost(str(uuid4()), module.SharedResourceCost(storage_slots=1))
    operation = other.transition(operation.operation_id, expected_revision=1, state="active")
    other.transition(operation.operation_id, expected_revision=2, state="releasing")
    other.transition(operation.operation_id, expected_revision=3, state="released")
    assert repository.used(storage.spec.domain_id) == 2
    assert repository.metadata_credits_current() == (credit,)
    assert other.metadata_credits_current() == (other_credit,)
    assert repository.audit_usage() == ()
