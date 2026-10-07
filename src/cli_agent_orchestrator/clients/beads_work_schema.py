"""External task correlations around approved Work; no second execution ledger."""

from contextlib import closing

DDL = """
CREATE TABLE IF NOT EXISTS beads_work_bindings (
 binding_id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES work_principals(id),
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, workspace_identity TEXT NOT NULL,
 material_hash TEXT NOT NULL, material_json TEXT NOT NULL,
 prepared_id TEXT NOT NULL REFERENCES workflow_prepared_plan(prepared_id),
 plan_id TEXT NOT NULL REFERENCES workflow_plan_snapshot(plan_id),
 run_id TEXT REFERENCES workflow_run(run_id), coordinator_id TEXT REFERENCES workflow_coordinator(id),
 operation_key TEXT NOT NULL, request_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('prepared','assigned','stopping','stopped','completed','reconcile')),
 revision INTEGER NOT NULL CHECK(revision>0), created_at REAL NOT NULL,
 UNIQUE(owner,workspace_id,task_id,operation_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_beads_one_active_assignment ON beads_work_bindings(workspace_id,task_id)
 WHERE state IN ('assigned','stopping','reconcile');
CREATE TRIGGER IF NOT EXISTS beads_binding_identity_immutable BEFORE UPDATE ON beads_work_bindings
 WHEN OLD.binding_id!=NEW.binding_id OR OLD.owner!=NEW.owner OR OLD.workspace_id!=NEW.workspace_id
 OR OLD.task_id!=NEW.task_id OR OLD.workspace_identity!=NEW.workspace_identity OR OLD.material_hash!=NEW.material_hash
 OR OLD.material_json!=NEW.material_json OR OLD.prepared_id!=NEW.prepared_id OR OLD.plan_id!=NEW.plan_id
 OR OLD.operation_key!=NEW.operation_key OR OLD.request_hash!=NEW.request_hash OR OLD.created_at!=NEW.created_at
 BEGIN SELECT RAISE(ABORT,'beads binding identity immutable'); END;
"""


def initialize(connection):
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

    with (
        closing(sqlite3.connect(":memory:")) as expected_connection,
        expected_connection as expected,
    ):
        expected.executescript(DDL)
        for kind, name, sql in expected.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        ):
            actual = connection.execute(
                "SELECT type,sql FROM sqlite_master WHERE name=?", (name,)
            ).fetchone()
            if (
                actual is None
                or actual[0] != kind
                or " ".join(actual[1].split()) != " ".join(sql.split())
            ):
                raise ValueError("beads_binding_schema_integrity:" + name)
