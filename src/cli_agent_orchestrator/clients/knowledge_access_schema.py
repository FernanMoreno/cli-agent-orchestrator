"""Content-free access audit, independent of knowledge record CAS versions."""

KNOWLEDGE_ACCESS_SCHEMA = (
    """CREATE TABLE work_knowledge_access_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_id TEXT NOT NULL,
        authority_hash TEXT NOT NULL CHECK(length(authority_hash)=64),
        action TEXT NOT NULL CHECK(action IN ('read','instructions','propose','review','tombstone')),
        target_hash TEXT NOT NULL CHECK(length(target_hash)=64),
        outcome TEXT NOT NULL CHECK(outcome IN ('allowed','denied')),
        occurred_at REAL NOT NULL
    )""",
    *tuple(
        f"CREATE TRIGGER work_knowledge_access_audit_immutable_{operation.lower()} "
        f"BEFORE {operation} ON work_knowledge_access_audit "
        "BEGIN SELECT RAISE(ABORT,'knowledge access audit is immutable'); END"
        for operation in ("UPDATE", "DELETE")
    ),
)
