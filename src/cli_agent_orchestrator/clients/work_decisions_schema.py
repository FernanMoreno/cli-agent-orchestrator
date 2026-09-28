"""Append-only durable human-decision evidence for a bound work attempt."""

DECISIONS_SCHEMA = (
    """CREATE TABLE work_human_decisions (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL, work_item_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL REFERENCES work_attempts(id),
        generation INTEGER NOT NULL CHECK(generation>0),
        contract_hash TEXT NOT NULL CHECK(length(contract_hash)=64),
        idempotency_key TEXT NOT NULL,
        actor_id TEXT NOT NULL REFERENCES work_principals(id),
        created_at REAL NOT NULL,
        action TEXT NOT NULL, reason TEXT NOT NULL,
        evidence_refs TEXT NOT NULL CHECK(json_valid(evidence_refs)
            AND json_type(evidence_refs)='array' AND length(evidence_refs)<=32768),
        authorized_effects TEXT NOT NULL CHECK(json_valid(authorized_effects)
            AND json_type(authorized_effects)='array' AND length(authorized_effects)<=32768),
        UNIQUE(attempt_id,generation,idempotency_key),
        FOREIGN KEY(job_id,work_item_id) REFERENCES work_items(job_id,id),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_dispatch_bindings(attempt_id,generation)
    )""",
    """CREATE TABLE work_human_decision_claims (
        decision_id TEXT NOT NULL REFERENCES work_human_decisions(id),
        effect TEXT NOT NULL,
        actor_id TEXT NOT NULL REFERENCES work_principals(id),
        consumed_at REAL NOT NULL,
        PRIMARY KEY(decision_id,effect)
    )""",
    """CREATE TABLE work_human_decision_revocations (
        decision_id TEXT PRIMARY KEY REFERENCES work_human_decisions(id),
        actor_id TEXT NOT NULL REFERENCES work_principals(id),
        revoked_at REAL NOT NULL, reason TEXT NOT NULL
    )""",
    """CREATE TRIGGER work_human_decisions_immutable_update
        BEFORE UPDATE ON work_human_decisions
        BEGIN SELECT RAISE(ABORT,'human decision history is immutable'); END""",
    """CREATE TRIGGER work_human_decisions_immutable_delete
        BEFORE DELETE ON work_human_decisions
        BEGIN SELECT RAISE(ABORT,'human decision history cannot be deleted'); END""",
    """CREATE TRIGGER work_human_decision_claims_immutable_update
        BEFORE UPDATE ON work_human_decision_claims
        BEGIN SELECT RAISE(ABORT,'human decision claims are immutable'); END""",
    """CREATE TRIGGER work_human_decision_claims_immutable_delete
        BEFORE DELETE ON work_human_decision_claims
        BEGIN SELECT RAISE(ABORT,'human decision claims cannot be deleted'); END""",
    """CREATE TRIGGER work_human_decision_revocations_immutable_update
        BEFORE UPDATE ON work_human_decision_revocations
        BEGIN SELECT RAISE(ABORT,'human decision revocations are immutable'); END""",
    """CREATE TRIGGER work_human_decision_revocations_immutable_delete
        BEFORE DELETE ON work_human_decision_revocations
        BEGIN SELECT RAISE(ABORT,'human decision revocations cannot be deleted'); END""",
)
