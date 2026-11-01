"""Known counter divergence forbids activation, preserving the durable allocation."""

from test.postgres.test_admission_reservations import domain, request, shared
from uuid import uuid4

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect

pytestmark = pytest.mark.postgres


def test_known_audit_drift_blocks_active_before_sql_without_releasing_debt(shared, monkeypatch):
    target, repository, _, module = shared
    record = domain(shared, 2)
    receipt = repository.reserve(str(uuid4()), (request(record),))
    with connect(target.migration_target()) as owner:
        owner.execute(
            sql.SQL("UPDATE {} SET committed=0 WHERE domain_id=%s").format(
                sql.Identifier(target.schema, "_cao_resource_usage")
            ),
            (record.spec.domain_id,),
        )
    mismatches = repository.audit_usage()
    assert len(mismatches) == 1
    assert mismatches[0].allocated == 1 and mismatches[0].committed == 0
    with monkeypatch.context() as patch:
        patch.setattr(
            module,
            "connect",
            lambda *args, **kwargs: pytest.fail("uncertain activation must deny before SQL"),
        )
        with pytest.raises(module.SharedAdmissionDenied):
            repository.transition(receipt.operation_id, expected_revision=1, state="active")
    with connect(target, readonly=True) as observer:
        row = observer.execute(
            sql.SQL("""SELECT o.state,o.revision,sum(a.amount) AS allocated
                FROM {} o JOIN {} a USING (operation_id)
                WHERE o.operation_id=%s GROUP BY o.state,o.revision""").format(
                sql.Identifier(target.schema, "_cao_resource_operations"),
                sql.Identifier(target.schema, "_cao_resource_allocations"),
            ),
            (receipt.operation_id,),
        ).fetchone()
    assert tuple(row) == ("preparing", 1, 1)
