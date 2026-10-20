"""Native private executable snapshots and write-once approval baseline."""

from cli_agent_orchestrator.clients.postgres.migrations import ComponentMigration

PRIVATE_PLAN_MIGRATIONS = (
    ComponentMigration(
        "private_plans",
        "main",
        1,
        (
            "CREATE TABLE workflow_plan_approval (plan_id TEXT PRIMARY KEY, approved_at TEXT NOT NULL, approved_by TEXT NOT NULL)",
            "CREATE FUNCTION approval_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'approval records are immutable' USING ERRCODE='23514'; END $$",
            "CREATE TRIGGER approval_immutable BEFORE UPDATE OR DELETE ON workflow_plan_approval FOR EACH ROW EXECUTE FUNCTION approval_immutable()",
            "CREATE TABLE workflow_plan_snapshot (plan_id TEXT PRIMARY KEY, component_set_version TEXT NOT NULL)",
            "CREATE TABLE workflow_plan_snapshot_component (plan_id TEXT NOT NULL REFERENCES workflow_plan_snapshot(plan_id) ON DELETE CASCADE, component_name TEXT NOT NULL, content_digest TEXT NOT NULL, content BYTEA NOT NULL, PRIMARY KEY(plan_id,component_name))",
            "CREATE TABLE workflow_run_plan_snapshot (run_id TEXT PRIMARY KEY REFERENCES workflow_run(run_id) ON DELETE CASCADE, plan_id TEXT NOT NULL REFERENCES workflow_plan_snapshot(plan_id) ON DELETE RESTRICT)",
            "CREATE TABLE workflow_prepared_plan (prepared_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES workflow_plan_snapshot(plan_id), workflow_name TEXT NOT NULL, tier TEXT NOT NULL, source_hash TEXT NOT NULL, principal_id TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL, public_manifest_json TEXT NOT NULL)",
            "CREATE TABLE workflow_scoped_run (run_id TEXT PRIMARY KEY REFERENCES workflow_run(run_id) ON DELETE CASCADE, prepared_id TEXT NOT NULL REFERENCES workflow_prepared_plan(prepared_id), principal_id TEXT NOT NULL, plan_id TEXT NOT NULL)",
            "CREATE TABLE workflow_plan_step_alias (run_id TEXT NOT NULL REFERENCES workflow_run(run_id) ON DELETE CASCADE, step_id TEXT NOT NULL, target_key TEXT NOT NULL, agent_profile TEXT NOT NULL, workflow_alias TEXT NOT NULL, seed_id TEXT NOT NULL, seed_revision BIGINT NOT NULL, provision_id TEXT NOT NULL, PRIMARY KEY(run_id,step_id))",
            "CREATE INDEX idx_workflow_run_plan_snapshot_plan ON workflow_run_plan_snapshot(plan_id)",
        ),
        (
            "workflow_plan_approval",
            "workflow_plan_snapshot",
            "workflow_plan_snapshot_component",
            "workflow_run_plan_snapshot",
            "workflow_prepared_plan",
            "workflow_scoped_run",
            "workflow_plan_step_alias",
        ),
    ),
)
