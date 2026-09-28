"""Immutable worktree evidence references, installed by repository migration."""

WORKTREE_EVIDENCE_SCHEMA = (
    """CREATE TABLE work_worktree_evidence (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL, work_item_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>0),
        attempt_revision INTEGER NOT NULL CHECK(attempt_revision>0),
        content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
        immutable_location TEXT NOT NULL CHECK(immutable_location=content_hash),
        byte_length INTEGER NOT NULL CHECK(byte_length>=0),
        base_head TEXT NOT NULL, stop_evidence_ref TEXT NOT NULL,
        grant_id TEXT NOT NULL, grant_revision INTEGER NOT NULL,
        created_at REAL NOT NULL,
        UNIQUE(attempt_id,content_hash),
        FOREIGN KEY(job_id,work_item_id) REFERENCES work_items(job_id,id),
        FOREIGN KEY(work_item_id,attempt_id,generation) REFERENCES work_attempts(work_item_id,id,generation),
        FOREIGN KEY(grant_id,grant_revision) REFERENCES work_grants(id,revision)
    )""",
    """CREATE TRIGGER work_worktree_evidence_immutable_update BEFORE UPDATE ON work_worktree_evidence
        BEGIN SELECT RAISE(ABORT,'worktree evidence is immutable'); END""",
    """CREATE TRIGGER work_worktree_evidence_immutable_delete BEFORE DELETE ON work_worktree_evidence
        BEGIN SELECT RAISE(ABORT,'worktree evidence is immutable'); END""",
)
