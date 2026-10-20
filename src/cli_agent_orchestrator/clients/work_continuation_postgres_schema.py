"""Native PostgreSQL schema for durable workflow continuation and coordination."""

from cli_agent_orchestrator.clients.postgres.migrations import ComponentMigration

_WORKFLOW_CONTINUATION_STATEMENTS = (
    """CREATE TABLE workflow_driver (
        run_id TEXT PRIMARY KEY REFERENCES workflow_run(run_id),
        owner_principal_id TEXT NOT NULL REFERENCES work_principals(id),
        owner_instance TEXT,
        epoch BIGINT NOT NULL CHECK (epoch > 0),
        revision BIGINT NOT NULL CHECK (revision > 0),
        run_generation TEXT NOT NULL,
        lease_expires_at DOUBLE PRECISION NOT NULL,
        heartbeat_at DOUBLE PRECISION NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('ready','driving','waiting','paused','stopping','stopped')),
        source_hash TEXT NOT NULL,
        plan_id TEXT NOT NULL REFERENCES workflow_plan_snapshot(plan_id),
        process_identity_json TEXT,
        stop_evidence_ref TEXT,
        pause_reason TEXT
    )""",
    """CREATE TABLE workflow_continuation_outbox (
        id TEXT PRIMARY KEY,
        binding_id TEXT NOT NULL REFERENCES work_workflow_step_projections(binding_id),
        run_id TEXT NOT NULL REFERENCES workflow_run(run_id),
        run_generation TEXT NOT NULL,
        step_id TEXT NOT NULL,
        step_attempt BIGINT NOT NULL CHECK (step_attempt > 0),
        accepted_result_id TEXT NOT NULL REFERENCES work_results(id),
        content_hash TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('ready','claimed','consumed','blocked','cancelled')),
        revision BIGINT NOT NULL CHECK (revision > 0),
        claim_epoch BIGINT,
        created_at DOUBLE PRECISION NOT NULL,
        UNIQUE (binding_id, accepted_result_id)
    )""",
    """CREATE INDEX idx_workflow_continuation_pending
        ON workflow_continuation_outbox(state, created_at, id)""",
    """CREATE TABLE workflow_coordinator (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL UNIQUE REFERENCES workflow_run(run_id),
        owner_principal_id TEXT NOT NULL REFERENCES work_principals(id),
        mode TEXT NOT NULL CHECK (mode IN ('ralph','beads')),
        prepared_id TEXT NOT NULL REFERENCES workflow_prepared_plan(prepared_id),
        plan_id TEXT NOT NULL REFERENCES workflow_plan_snapshot(plan_id),
        frozen_policy_hash TEXT NOT NULL,
        policy_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('ready','running','waiting','paused','stopping','stopped','completed','escalated','closed_unverified')),
        revision BIGINT NOT NULL CHECK (revision > 0),
        current_iteration BIGINT NOT NULL CHECK (current_iteration >= 0),
        min_iterations BIGINT NOT NULL CHECK (min_iterations >= 1),
        max_iterations BIGINT NOT NULL CHECK (max_iterations >= min_iterations),
        deadline DOUBLE PRECISION NOT NULL,
        correction_budget BIGINT NOT NULL CHECK (correction_budget >= 0),
        last_progress_hash TEXT,
        no_progress_count BIGINT NOT NULL CHECK (no_progress_count >= 0),
        escalation_reason TEXT,
        external_binding_ref TEXT
    )""",
    """CREATE TABLE workflow_coordinator_events (
        coordinator_id TEXT NOT NULL REFERENCES workflow_coordinator(id),
        sequence BIGINT NOT NULL CHECK (sequence > 0),
        request_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('feedback','checkpoint','progress','correction','stop','complete','escalate')),
        iteration BIGINT NOT NULL CHECK (iteration >= 0),
        binding_id TEXT REFERENCES work_workflow_step_bindings(binding_id),
        accepted_result_id TEXT REFERENCES work_results(id),
        content_hash TEXT NOT NULL,
        public_evidence_json TEXT NOT NULL,
        private_artifact_ref TEXT,
        PRIMARY KEY (coordinator_id, sequence),
        UNIQUE (coordinator_id, request_id)
    )""",
    """CREATE FUNCTION workflow_outbox_identity_immutable_fn() RETURNS trigger
       LANGUAGE plpgsql AS $$
       BEGIN
           IF OLD.id IS DISTINCT FROM NEW.id
              OR OLD.binding_id IS DISTINCT FROM NEW.binding_id
              OR OLD.run_id IS DISTINCT FROM NEW.run_id
              OR OLD.run_generation IS DISTINCT FROM NEW.run_generation
              OR OLD.step_id IS DISTINCT FROM NEW.step_id
              OR OLD.step_attempt IS DISTINCT FROM NEW.step_attempt
              OR OLD.accepted_result_id IS DISTINCT FROM NEW.accepted_result_id
              OR OLD.content_hash IS DISTINCT FROM NEW.content_hash
              OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
               RAISE EXCEPTION 'continuation identity immutable';
           END IF;
           RETURN NEW;
       END
       $$""",
    """CREATE TRIGGER workflow_outbox_identity_immutable
       BEFORE UPDATE ON workflow_continuation_outbox
       FOR EACH ROW EXECUTE FUNCTION workflow_outbox_identity_immutable_fn()""",
    """CREATE FUNCTION workflow_coordinator_events_immutable_fn() RETURNS trigger
       LANGUAGE plpgsql AS $$
       BEGIN
           RAISE EXCEPTION 'coordinator events immutable';
       END
       $$""",
    """CREATE TRIGGER coordinator_events_no_update
       BEFORE UPDATE ON workflow_coordinator_events
       FOR EACH ROW EXECUTE FUNCTION workflow_coordinator_events_immutable_fn()""",
    """CREATE TRIGGER coordinator_events_no_delete
       BEFORE DELETE ON workflow_coordinator_events
       FOR EACH ROW EXECUTE FUNCTION workflow_coordinator_events_immutable_fn()""",
)

WORKFLOW_CONTINUATION_TABLES = (
    "workflow_driver",
    "workflow_continuation_outbox",
    "workflow_coordinator",
    "workflow_coordinator_events",
)

WORKFLOW_CONTINUATION_MIGRATIONS = (
    ComponentMigration(
        component="workflow_continuation",
        scope="main",
        version=1,
        statements=_WORKFLOW_CONTINUATION_STATEMENTS,
        tables=WORKFLOW_CONTINUATION_TABLES,
    ),
)
