"""Complete infrastructure coverage before shared delivery effects."""

import hashlib
from dataclasses import replace
from test.postgres.test_admission_metadata_credits import ADMIN, credit_rows, grant
from test.postgres.test_admission_reservations import domain, shared
from uuid import uuid4

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect

pytestmark = pytest.mark.postgres


def unbound_storage(shared, capacity=3):
    _, repository, _, module = shared
    spec = module.DomainSpec(
        str(uuid4()),
        str(uuid4()),
        "storage",
        "postgres_session",
        capacity,
        hashlib.sha256(uuid4().bytes).hexdigest(),
    )
    return repository.provision_domain(spec, expected_revision=0, principal_id=ADMIN)


def test_missing_metadata_coverage_is_not_an_allowance(shared):
    _, repository, _, module = shared
    domain(shared, 3)
    with pytest.raises(module.SharedAdmissionDenied):
        repository.metadata_coverage_current()


def test_coverage_includes_exact_private_set_and_all_credits(shared):
    _, repository, current, module = shared
    first, second = domain(shared, 4), domain(shared, 4)
    credits = (grant(shared, first), grant(shared, second))
    coverage = repository.metadata_coverage_current()
    assert coverage.binding == current
    assert len(coverage.membership_sha256) == 64
    assert set(coverage.storage_domain_ids) == {first.spec.domain_id, second.spec.domain_id}
    assert set(coverage.credits) == set(credits)


def test_partial_credit_set_never_passes_coverage(shared):
    _, repository, _, module = shared
    first, second = domain(shared, 4), domain(shared, 4)
    grant(shared, first)
    with pytest.raises(module.SharedAdmissionDenied):
        repository.metadata_coverage_current()
    grant(shared, second)
    assert len(repository.metadata_coverage_current().credits) == 2


def test_credit_required_admission_is_atomic_and_persists_its_mode(shared):
    target, repository, _, module = shared
    record = domain(shared, 3)
    op = str(uuid4())
    with pytest.raises(module.SharedAdmissionDenied):
        repository.reserve_for_cost(
            op, module.SharedResourceCost(storage_slots=1), require_metadata=True
        )
    assert repository.used(record.spec.domain_id) == 0
    assert repository.read_operation(op) is None
    grant(shared, record)
    receipt = repository.reserve_for_cost(
        op, module.SharedResourceCost(storage_slots=1), require_metadata=True
    )
    assert receipt.state == "preparing"
    with connect(target, readonly=True) as observer:
        row = observer.execute(
            sql.SQL("SELECT metadata_required FROM {} WHERE operation_id=%s").format(
                sql.Identifier(target.schema, "_cao_resource_operations")
            ),
            (op,),
        ).fetchone()
    assert row[0] is True
    assert repository.used(record.spec.domain_id) == 2


def test_retry_cannot_promote_or_demote_a_resource_operation(shared):
    _, repository, _, module = shared
    record = domain(shared, 4)
    cost = module.SharedResourceCost(storage_slots=1)
    plain = repository.reserve_for_cost(str(uuid4()), cost)
    grant(shared, record)
    with pytest.raises(module.SharedRevisionConflict):
        repository.reserve_for_cost(plain.operation_id, cost, require_metadata=True)
    covered = repository.reserve_for_cost(str(uuid4()), cost, require_metadata=True)
    with pytest.raises(module.SharedRevisionConflict):
        repository.reserve_for_cost(covered.operation_id, cost)
    assert repository.used(record.spec.domain_id) == 3


def test_operation_reconciliation_reads_only_its_exact_owner(shared):
    target, repository, current, module = shared
    record = domain(shared, 3)
    grant(shared, record)
    assert repository.read_operation(str(uuid4())) is None
    prepared = repository.reserve_for_cost(
        str(uuid4()), module.SharedResourceCost(storage_slots=1), require_metadata=True
    )
    assert repository.read_operation(prepared.operation_id) == prepared
    foreign = module.ResourceAdmissionRepository(
        target, binding_supplier=lambda: replace(current, process_generation=str(uuid4()))
    )
    with pytest.raises(module.SharedAdmissionDenied):
        foreign.read_operation(prepared.operation_id)


def test_credited_node_cannot_add_storage_without_joint_metadata_grant(shared):
    target, repository, current, module = shared
    first = domain(shared, 3)
    grant(shared, first)
    second = unbound_storage(shared)
    with pytest.raises(module.SharedAdmissionDenied):
        repository.provision_member(
            second.spec.domain_id, current, expected_revision=0, principal_id=ADMIN
        )
    assert repository.used(second.spec.domain_id) == 0
    assert repository.metadata_coverage_current().storage_domain_ids == (first.spec.domain_id,)


def test_joint_storage_membership_and_metadata_credit_is_atomic(shared):
    _, repository, current, module = shared
    first = domain(shared, 3)
    grant(shared, first)
    second = unbound_storage(shared)
    credit_id = str(uuid4())
    revision = repository.provision_member(
        second.spec.domain_id,
        current,
        expected_revision=0,
        principal_id=ADMIN,
        metadata_credit_id=credit_id,
    )
    assert revision == 1
    coverage = repository.metadata_coverage_current()
    assert set(coverage.storage_domain_ids) == {first.spec.domain_id, second.spec.domain_id}
    assert credit_id in {c.credit_id for c in coverage.credits}
    assert repository.used(second.spec.domain_id) == 1


def test_joint_membership_capacity_failure_leaves_no_partial_authority(shared):
    target, repository, current, module = shared
    first = domain(shared, 3)
    grant(shared, first)
    second = unbound_storage(shared, 0)
    with pytest.raises(module.SharedCapacityUnavailable):
        repository.provision_member(
            second.spec.domain_id,
            current,
            expected_revision=0,
            principal_id=ADMIN,
            metadata_credit_id=str(uuid4()),
        )
    assert repository.used(second.spec.domain_id) == 0
    assert credit_rows(target, second.spec.domain_id) == 0
    with connect(target, readonly=True) as observer:
        for table in ("_cao_resource_members", "_cao_resource_member_history"):
            assert (
                observer.execute(
                    sql.SQL("SELECT count(*) FROM {} WHERE domain_id=%s").format(
                        sql.Identifier(target.schema, table)
                    ),
                    (second.spec.domain_id,),
                ).fetchone()[0]
                == 0
            )
