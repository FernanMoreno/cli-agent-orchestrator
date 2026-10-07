"""Additive continuation inventory; central database migration registers this schema."""

DDL = """
CREATE TABLE IF NOT EXISTS workflow_driver (
 run_id TEXT PRIMARY KEY REFERENCES workflow_run(run_id),
 owner_principal_id TEXT NOT NULL REFERENCES work_principals(id),
 owner_instance TEXT, epoch INTEGER NOT NULL CHECK(epoch>0), revision INTEGER NOT NULL CHECK(revision>0),
 run_generation TEXT NOT NULL, lease_expires_at REAL NOT NULL, heartbeat_at REAL NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('ready','driving','waiting','paused','stopping','stopped')),
 source_hash TEXT NOT NULL, plan_id TEXT NOT NULL REFERENCES workflow_plan_snapshot(plan_id),
 process_identity_json TEXT, stop_evidence_ref TEXT, pause_reason TEXT
);
CREATE TABLE IF NOT EXISTS workflow_continuation_outbox (
 id TEXT PRIMARY KEY, binding_id TEXT NOT NULL REFERENCES work_workflow_step_projections(binding_id),
 run_id TEXT NOT NULL REFERENCES workflow_run(run_id), run_generation TEXT NOT NULL,
 step_id TEXT NOT NULL, step_attempt INTEGER NOT NULL CHECK(step_attempt>0),
 accepted_result_id TEXT NOT NULL REFERENCES work_results(id), content_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('ready','claimed','consumed','blocked','cancelled')),
 revision INTEGER NOT NULL CHECK(revision>0), claim_epoch INTEGER, created_at REAL NOT NULL,
 UNIQUE(binding_id,accepted_result_id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_continuation_pending ON workflow_continuation_outbox(state,created_at,id);
CREATE TABLE IF NOT EXISTS workflow_coordinator (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES workflow_run(run_id),
 owner_principal_id TEXT NOT NULL REFERENCES work_principals(id), mode TEXT NOT NULL CHECK(mode IN ('ralph','beads')),
 prepared_id TEXT NOT NULL REFERENCES workflow_prepared_plan(prepared_id),
 plan_id TEXT NOT NULL REFERENCES workflow_plan_snapshot(plan_id), frozen_policy_hash TEXT NOT NULL,
 policy_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('ready','running','waiting','paused','stopping','stopped','completed','escalated','closed_unverified')),
 revision INTEGER NOT NULL CHECK(revision>0), current_iteration INTEGER NOT NULL CHECK(current_iteration>=0),
 min_iterations INTEGER NOT NULL CHECK(min_iterations>=1), max_iterations INTEGER NOT NULL CHECK(max_iterations>=min_iterations),
 deadline REAL NOT NULL, correction_budget INTEGER NOT NULL CHECK(correction_budget>=0),
 last_progress_hash TEXT, no_progress_count INTEGER NOT NULL CHECK(no_progress_count>=0),
 escalation_reason TEXT, external_binding_ref TEXT
);
CREATE TABLE IF NOT EXISTS workflow_coordinator_events (
 coordinator_id TEXT NOT NULL REFERENCES workflow_coordinator(id), sequence INTEGER NOT NULL CHECK(sequence>0),
 request_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('feedback','checkpoint','progress','correction','stop','complete','escalate')),
 iteration INTEGER NOT NULL CHECK(iteration>=0),
 binding_id TEXT REFERENCES work_workflow_step_bindings(binding_id), accepted_result_id TEXT REFERENCES work_results(id),
 content_hash TEXT NOT NULL, public_evidence_json TEXT NOT NULL, private_artifact_ref TEXT,
 PRIMARY KEY(coordinator_id,sequence), UNIQUE(coordinator_id,request_id)
);
CREATE TRIGGER IF NOT EXISTS workflow_outbox_identity_immutable BEFORE UPDATE ON workflow_continuation_outbox
 WHEN OLD.id!=NEW.id OR OLD.binding_id!=NEW.binding_id OR OLD.run_id!=NEW.run_id
 OR OLD.run_generation!=NEW.run_generation OR OLD.step_id!=NEW.step_id OR OLD.step_attempt!=NEW.step_attempt
 OR OLD.accepted_result_id!=NEW.accepted_result_id OR OLD.content_hash!=NEW.content_hash OR OLD.created_at!=NEW.created_at
 BEGIN SELECT RAISE(ABORT,'continuation identity immutable'); END;
CREATE TRIGGER IF NOT EXISTS coordinator_events_no_update BEFORE UPDATE ON workflow_coordinator_events
 BEGIN SELECT RAISE(ABORT,'coordinator events immutable'); END;
CREATE TRIGGER IF NOT EXISTS coordinator_events_no_delete BEFORE DELETE ON workflow_coordinator_events
 BEGIN SELECT RAISE(ABORT,'coordinator events immutable'); END;
"""


def initialize(connection):
    # executescript would implicitly commit a borrowed SQLite transaction.
    import sqlite3

    statement = ""
    for line in DDL.splitlines(True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""

    verify(connection)


def verify(connection):
    import sqlite3

    # Compare canonical sqlite-generated definitions, including FK/check/unique
    # clauses and immutable triggers. Existing malformed objects are not repaired.
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript(DDL)
        inventory = expected.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for kind, name, sql in inventory:
            actual = connection.execute(
                "SELECT type,sql FROM sqlite_master WHERE name=?", (name,)
            ).fetchone()
            normalize = lambda value: " ".join(value.split())
            if actual is None or actual[0] != kind or normalize(actual[1]) != normalize(sql):
                raise ValueError("continuation_schema_integrity:" + name)
    finally:
        expected.close()
