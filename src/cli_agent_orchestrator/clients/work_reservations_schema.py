"""Cooperative path reservation DDL; installed only by repository migration."""

RESERVATIONS_SCHEMA = (
    "CREATE UNIQUE INDEX work_attempts_reservation_owner ON work_attempts(work_item_id,id,generation)",
    """CREATE TABLE work_reservation_sets (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL, work_item_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>0),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        checkout_root TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','released')),
        expires_at REAL NOT NULL, created_at REAL NOT NULL,
        stop_evidence_ref TEXT, released_at REAL,
        UNIQUE(attempt_id,generation),
        FOREIGN KEY(job_id,work_item_id) REFERENCES work_items(job_id,id),
        FOREIGN KEY(work_item_id,attempt_id,generation) REFERENCES work_attempts(work_item_id,id,generation),
        CHECK((state='active' AND stop_evidence_ref IS NULL AND released_at IS NULL) OR
              (state='released' AND stop_evidence_ref IS NOT NULL AND released_at IS NOT NULL))
    )""",
    "CREATE INDEX work_reservation_sets_state ON work_reservation_sets(state)",
    """CREATE TABLE work_path_reservations (
        reservation_set_id TEXT NOT NULL REFERENCES work_reservation_sets(id),
        resource_key TEXT NOT NULL, device INTEGER, inode INTEGER,
        PRIMARY KEY(reservation_set_id,resource_key),
        CHECK((device IS NULL AND inode IS NULL) OR (device IS NOT NULL AND inode IS NOT NULL))
    )""",
    """CREATE TRIGGER work_path_reservations_immutable_update BEFORE UPDATE ON work_path_reservations
        BEGIN SELECT RAISE(ABORT,'reservation paths are immutable'); END""",
    """CREATE TRIGGER work_path_reservations_immutable_delete BEFORE DELETE ON work_path_reservations
        BEGIN SELECT RAISE(ABORT,'reservation path history cannot be deleted'); END""",
    """CREATE TRIGGER work_reservation_sets_immutable_owner
        BEFORE UPDATE OF id,job_id,work_item_id,attempt_id,generation,checkout_root,created_at ON work_reservation_sets
        BEGIN SELECT RAISE(ABORT,'reservation ownership is immutable'); END""",
    """CREATE TRIGGER work_reservation_sets_no_revival BEFORE UPDATE ON work_reservation_sets
        WHEN OLD.state='released' BEGIN SELECT RAISE(ABORT,'released reservations are immutable'); END""",
    """CREATE TRIGGER work_reservation_sets_immutable_delete BEFORE DELETE ON work_reservation_sets
        BEGIN SELECT RAISE(ABORT,'reservation history cannot be deleted'); END""",
)
