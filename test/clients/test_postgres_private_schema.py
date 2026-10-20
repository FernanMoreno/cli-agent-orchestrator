"""Private plan and approval native schema contracts."""

from cli_agent_orchestrator.clients.private_plan_postgres_schema import PRIVATE_PLAN_MIGRATIONS


def test_native_private_storage_has_bytes_and_enforced_references():
    baseline = PRIVATE_PLAN_MIGRATIONS[0]
    assert len(baseline.tables) == 7
    ddl = "\n".join(baseline.statements)
    assert "BYTEA" in ddl
    assert "REFERENCES workflow_run(run_id)" in ddl
    assert "DOUBLE PRECISION" in ddl
    assert "approval_immutable" in ddl
