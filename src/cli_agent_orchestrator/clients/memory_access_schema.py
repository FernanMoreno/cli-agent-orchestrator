"""Append-only local compatibility audit in the existing durable work store."""

MEMORY_ACCESS_SCHEMA = (
    """CREATE TABLE work_memory_access_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version=1),
        operation_id TEXT NOT NULL CHECK(length(operation_id)=32 AND operation_id NOT GLOB '*[^0-9a-f]*'),
        actor_id TEXT NOT NULL CHECK(length(trim(actor_id)) BETWEEN 1 AND 512),
        action TEXT NOT NULL CHECK(action IN ('store','recall','forget','compact','context','curated_context','export','import','graph','relationships','repair')),
        target_hash TEXT NOT NULL CHECK(length(target_hash)=64 AND target_hash NOT GLOB '*[^0-9a-f]*'),
        phase TEXT NOT NULL CHECK(phase IN ('authorized','completed','failed','denied')),
        occurred_at REAL NOT NULL CHECK(typeof(occurred_at) IN ('real','integer') AND occurred_at>0 AND occurred_at<1e20),
        UNIQUE(operation_id,phase)
    )""",
    """CREATE UNIQUE INDEX work_memory_access_audit_terminal
        ON work_memory_access_audit(operation_id) WHERE phase IN ('completed','failed','denied')""",
    """CREATE TRIGGER work_memory_access_audit_requires_intent
        BEFORE INSERT ON work_memory_access_audit WHEN NEW.phase IN ('completed','failed')
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM work_memory_access_audit
                WHERE operation_id=NEW.operation_id AND phase='authorized'
                AND actor_id=NEW.actor_id AND action=NEW.action AND target_hash=NEW.target_hash
                AND occurred_at<=NEW.occurred_at
            ) THEN RAISE(ABORT,'memory access audit requires matching intent') END;
        END""",
    *tuple(
        f"CREATE TRIGGER work_memory_access_audit_immutable_{operation.lower()} "
        f"BEFORE {operation} ON work_memory_access_audit "
        "BEGIN SELECT RAISE(ABORT,'memory access audit is immutable'); END"
        for operation in ("UPDATE", "DELETE")
    ),
)
