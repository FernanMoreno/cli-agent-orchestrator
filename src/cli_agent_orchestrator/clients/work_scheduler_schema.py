"""Durable scheduler accounting, separate from authoritative execution state."""

SCHEDULER_SCHEMA = (
    """CREATE TABLE work_scheduler_policy (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        revision INTEGER NOT NULL CHECK(revision>0),
        capacity INTEGER NOT NULL CHECK(capacity>0),
        max_queue INTEGER NOT NULL CHECK(max_queue>0),
        aging_seconds INTEGER NOT NULL CHECK(aging_seconds>0),
        dispatch_sequence INTEGER NOT NULL DEFAULT 0 CHECK(dispatch_sequence>=0)
    )""",
    """CREATE TABLE work_scheduler_requests (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL, work_item_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>0),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        units INTEGER NOT NULL CHECK(units>0), dependencies TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','held','released','withdrawn')),
        queued_at REAL NOT NULL, expires_at REAL NOT NULL,
        dispatch_sequence INTEGER, stop_evidence_ref TEXT,
        UNIQUE(attempt_id,generation),
        FOREIGN KEY(job_id,work_item_id) REFERENCES work_items(job_id,id),
        FOREIGN KEY(work_item_id,attempt_id,generation) REFERENCES work_attempts(work_item_id,id,generation),
        CHECK((state='queued' AND dispatch_sequence IS NULL AND stop_evidence_ref IS NULL)
           OR (state='held' AND dispatch_sequence IS NOT NULL AND stop_evidence_ref IS NULL)
           OR (state='released' AND dispatch_sequence IS NOT NULL AND stop_evidence_ref IS NOT NULL)
           OR (state='withdrawn' AND dispatch_sequence IS NULL AND stop_evidence_ref IS NULL))
    )""",
    "CREATE INDEX work_scheduler_requests_state ON work_scheduler_requests(state,job_id)",
    """CREATE TABLE work_scheduler_dependencies (
        job_id TEXT NOT NULL, work_item_id TEXT NOT NULL, dependency_id TEXT NOT NULL,
        PRIMARY KEY(work_item_id,dependency_id), CHECK(work_item_id!=dependency_id),
        FOREIGN KEY(job_id,work_item_id) REFERENCES work_items(job_id,id),
        FOREIGN KEY(job_id,dependency_id) REFERENCES work_items(job_id,id)
    )""",
    """CREATE TRIGGER work_scheduler_requests_frozen
        BEFORE UPDATE OF id,job_id,work_item_id,attempt_id,generation,units,dependencies,queued_at
        ON work_scheduler_requests
        BEGIN SELECT RAISE(ABORT,'scheduler request is frozen'); END""",
    """CREATE TRIGGER work_scheduler_requests_no_revival BEFORE UPDATE ON work_scheduler_requests
        WHEN OLD.state IN ('released','withdrawn')
        BEGIN SELECT RAISE(ABORT,'settled scheduler history is immutable'); END""",
    """CREATE TRIGGER work_scheduler_requests_no_delete BEFORE DELETE ON work_scheduler_requests
        BEGIN SELECT RAISE(ABORT,'scheduler accounting history cannot be deleted'); END""",
)


# Additive migration; never rewrite the original scheduler schema/checksum.
SCHEDULER_FAIRNESS_SCHEMA = (
    "ALTER TABLE work_scheduler_requests ADD COLUMN enqueue_frontier INTEGER NOT NULL DEFAULT 0 CHECK(enqueue_frontier>=0)",
    """CREATE TRIGGER work_scheduler_requests_frozen_frontier
        BEFORE UPDATE OF enqueue_frontier ON work_scheduler_requests
        BEGIN SELECT RAISE(ABORT,'scheduler enqueue frontier is frozen'); END""",
)
