"""Native PostgreSQL schemas for the raw workflow journal and Beads bindings.

The ORM owns its own tables in ``core_migrations``. This module only describes
tables that are still accessed through workflow services' parameterized SQL.
"""

from cli_agent_orchestrator.clients.postgres.migrations import ComponentMigration

_WORKFLOW_JOURNAL_STATEMENTS = (
    """CREATE TABLE workflow_index (
        name TEXT PRIMARY KEY,
        source_path TEXT NOT NULL,
        mode TEXT NOT NULL,
        step_count BIGINT,
        description TEXT NOT NULL DEFAULT '',
        indexed_at TEXT NOT NULL
    )""",
    """CREATE TABLE workflow_run (
        run_id TEXT PRIMARY KEY,
        workflow_name TEXT NOT NULL,
        spec_snapshot TEXT NOT NULL,
        inputs_json TEXT NOT NULL,
        state TEXT NOT NULL,
        current_step_id TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        tier TEXT NOT NULL DEFAULT 'yaml',
        generation TEXT NOT NULL DEFAULT '1',
        manifest_json TEXT,
        error TEXT
    )""",
    """CREATE TABLE workflow_run_step (
        run_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        state TEXT NOT NULL,
        attempts BIGINT NOT NULL,
        output_json TEXT,
        error TEXT,
        updated_at TEXT NOT NULL,
        call_fingerprint TEXT,
        terminal_id TEXT,
        reprompted BIGINT,
        error_kind TEXT,
        result_json TEXT,
        PRIMARY KEY (run_id, step_id)
    )""",
    """CREATE TABLE workflow_run_event (
        run_id TEXT NOT NULL,
        seq BIGINT NOT NULL,
        event_type TEXT NOT NULL,
        event_schema_version BIGINT NOT NULL,
        ts TEXT NOT NULL,
        step_id TEXT,
        attempt BIGINT,
        state TEXT,
        elapsed_ms BIGINT,
        provider TEXT,
        agent_profile TEXT,
        engine TEXT,
        terminal_id TEXT,
        terminal_offset_start BIGINT,
        terminal_offset_len BIGINT,
        error_kind TEXT,
        reason TEXT,
        validation_result TEXT,
        output_ref TEXT,
        iteration BIGINT,
        which_guard_fired TEXT,
        PRIMARY KEY (run_id, seq)
    )""",
    """CREATE TABLE workflow_run_seq (
        run_id TEXT PRIMARY KEY,
        high_water BIGINT NOT NULL
    )""",
    "CREATE INDEX idx_workflow_run_started_at ON workflow_run (started_at)",
    "CREATE INDEX idx_workflow_run_state ON workflow_run (state)",
)

WORKFLOW_MIGRATIONS = (
    ComponentMigration(
        component="workflow",
        scope="main",
        version=1,
        statements=_WORKFLOW_JOURNAL_STATEMENTS,
        tables=(
            "workflow_index",
            "workflow_run",
            "workflow_run_step",
            "workflow_run_event",
            "workflow_run_seq",
        ),
    ),
)


_BEADS_BINDING_STATEMENTS = (
    """CREATE TABLE beads_work_bindings (
        binding_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL REFERENCES work_principals(id),
        workspace_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        workspace_identity TEXT NOT NULL,
        material_hash TEXT NOT NULL,
        material_json TEXT NOT NULL,
        prepared_id TEXT NOT NULL REFERENCES workflow_prepared_plan(prepared_id),
        plan_id TEXT NOT NULL REFERENCES workflow_plan_snapshot(plan_id),
        run_id TEXT REFERENCES workflow_run(run_id),
        coordinator_id TEXT REFERENCES workflow_coordinator(id),
        operation_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('prepared','assigned','stopping','stopped','completed','reconcile')),
        revision BIGINT NOT NULL CHECK (revision > 0),
        created_at DOUBLE PRECISION NOT NULL,
        UNIQUE (owner, workspace_id, task_id, operation_key)
    )""",
    """CREATE UNIQUE INDEX idx_beads_one_active_assignment
       ON beads_work_bindings(workspace_id,task_id)
       WHERE state IN ('assigned','stopping','reconcile')""",
    """CREATE FUNCTION beads_binding_identity_immutable_fn() RETURNS trigger
       LANGUAGE plpgsql AS $$
       BEGIN
           IF OLD.binding_id IS DISTINCT FROM NEW.binding_id
              OR OLD.owner IS DISTINCT FROM NEW.owner
              OR OLD.workspace_id IS DISTINCT FROM NEW.workspace_id
              OR OLD.task_id IS DISTINCT FROM NEW.task_id
              OR OLD.workspace_identity IS DISTINCT FROM NEW.workspace_identity
              OR OLD.material_hash IS DISTINCT FROM NEW.material_hash
              OR OLD.material_json IS DISTINCT FROM NEW.material_json
              OR OLD.prepared_id IS DISTINCT FROM NEW.prepared_id
              OR OLD.plan_id IS DISTINCT FROM NEW.plan_id
              OR OLD.operation_key IS DISTINCT FROM NEW.operation_key
              OR OLD.request_hash IS DISTINCT FROM NEW.request_hash
              OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
               RAISE EXCEPTION 'Beads binding identity is immutable';
           END IF;
           RETURN NEW;
       END
       $$""",
    """CREATE TRIGGER beads_binding_identity_immutable
       BEFORE UPDATE ON beads_work_bindings
       FOR EACH ROW EXECUTE FUNCTION beads_binding_identity_immutable_fn()""",
)

BEADS_WORK_MIGRATIONS = (
    ComponentMigration(
        component="beads_work",
        scope="main",
        version=1,
        statements=_BEADS_BINDING_STATEMENTS,
        tables=("beads_work_bindings",),
    ),
)
