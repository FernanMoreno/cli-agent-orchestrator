"""Knowledge history DDL; installed only by the verified work-store migration."""

KNOWLEDGE_SCHEMA = (
    """CREATE TABLE work_knowledge_records (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('project','job')),
        scope_id TEXT NOT NULL, head_revision INTEGER NOT NULL CHECK(head_revision>0),
        version INTEGER NOT NULL CHECK(version>0)
    )""",
    """CREATE TABLE work_knowledge_revisions (
        record_id TEXT NOT NULL REFERENCES work_knowledge_records(id),
        revision INTEGER NOT NULL CHECK(revision>0),
        producer_principal_id TEXT NOT NULL REFERENCES work_principals(id),
        work_item_id TEXT REFERENCES work_items(id),
        attempt_id TEXT REFERENCES work_attempts(id),
        source_artifact_id TEXT REFERENCES work_results(id),
        source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
        delivered_hash TEXT NOT NULL CHECK(length(delivered_hash)=64),
        evidence_refs TEXT NOT NULL CHECK(json_valid(evidence_refs) AND json_type(evidence_refs)='array'),
        confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1),
        fresh_until REAL NOT NULL CHECK(fresh_until>0 AND fresh_until<1.0e15),
        content TEXT NOT NULL CHECK(length(content)<=1048576),
        redacted INTEGER NOT NULL CHECK(redacted IN (0,1)),
        truncated INTEGER NOT NULL CHECK(truncated IN (0,1)),
        legacy INTEGER NOT NULL CHECK(legacy IN (0,1)),
        supersedes INTEGER, created_at REAL NOT NULL,
        PRIMARY KEY(record_id,revision),
        FOREIGN KEY(record_id,supersedes) REFERENCES work_knowledge_revisions(record_id,revision)
    )""",
    """CREATE TABLE work_knowledge_events (
        record_id TEXT NOT NULL REFERENCES work_knowledge_records(id),
        sequence INTEGER NOT NULL CHECK(sequence>0),
        revision INTEGER NOT NULL,
        operation TEXT NOT NULL CHECK(operation IN ('proposed','reviewed','tombstoned')),
        actor_id TEXT NOT NULL REFERENCES work_principals(id),
        occurred_at REAL NOT NULL,
        PRIMARY KEY(record_id,sequence),
        FOREIGN KEY(record_id,revision) REFERENCES work_knowledge_revisions(record_id,revision)
    )""",
    """CREATE TABLE work_knowledge_decisions (
        record_id TEXT NOT NULL, revision INTEGER NOT NULL,
        event_sequence INTEGER NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('proposed','verified','approved','rejected','superseded')),
        actor_id TEXT NOT NULL REFERENCES work_principals(id),
        examined_refs TEXT NOT NULL CHECK(json_valid(examined_refs) AND json_type(examined_refs)='array'),
        PRIMARY KEY(record_id,revision,event_sequence),
        FOREIGN KEY(record_id,revision) REFERENCES work_knowledge_revisions(record_id,revision),
        FOREIGN KEY(record_id,event_sequence) REFERENCES work_knowledge_events(record_id,sequence)
    )""",
    """CREATE TABLE work_knowledge_tombstones (
        record_id TEXT NOT NULL, revision INTEGER NOT NULL,
        event_sequence INTEGER NOT NULL, actor_id TEXT NOT NULL REFERENCES work_principals(id),
        PRIMARY KEY(record_id,revision),
        FOREIGN KEY(record_id,revision) REFERENCES work_knowledge_revisions(record_id,revision),
        FOREIGN KEY(record_id,event_sequence) REFERENCES work_knowledge_events(record_id,sequence)
    )""",
    """CREATE TRIGGER work_knowledge_record_scope_immutable BEFORE UPDATE ON work_knowledge_records
        WHEN NEW.id!=OLD.id OR NEW.scope!=OLD.scope OR NEW.scope_id!=OLD.scope_id
        BEGIN SELECT RAISE(ABORT,'knowledge scope is immutable'); END""",
    *tuple(
        f"CREATE TRIGGER {table}_immutable_{operation.lower()} BEFORE {operation} ON {table} "
        "BEGIN SELECT RAISE(ABORT,'knowledge history is immutable'); END"
        for table in (
            "work_knowledge_revisions",
            "work_knowledge_events",
            "work_knowledge_decisions",
            "work_knowledge_tombstones",
        )
        for operation in ("UPDATE", "DELETE")
    ),
)


# Operational recovery state deliberately lives apart from immutable knowledge
# history.  The durable value is only a hash of an opaque cursor token.
KNOWLEDGE_CURSOR_SCHEMA = (
    """CREATE TABLE work_knowledge_cursors (
        token_hash TEXT PRIMARY KEY CHECK(length(token_hash)=64),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        principal_id TEXT NOT NULL REFERENCES work_principals(id),
        scope TEXT NOT NULL CHECK(scope IN ('project','job')),
        scope_id TEXT NOT NULL,
        job_id TEXT NOT NULL REFERENCES work_jobs(id),
        grant_id TEXT NOT NULL,
        grant_revision INTEGER NOT NULL CHECK(grant_revision>0),
        checkpoint TEXT NOT NULL CHECK(length(checkpoint)=64),
        last_record_id TEXT,
        last_revision INTEGER,
        limit_value INTEGER NOT NULL CHECK(limit_value>0 AND limit_value<=100),
        expires_at REAL NOT NULL CHECK(expires_at>0 AND expires_at<1.0e15),
        created_at REAL NOT NULL,
        CHECK(
            (last_record_id IS NULL AND last_revision IS NULL)
            OR (last_record_id IS NOT NULL AND last_revision>0)
        ),
        FOREIGN KEY(grant_id,grant_revision) REFERENCES work_grants(id,revision)
    )""",
    "CREATE INDEX work_knowledge_cursors_expiry ON work_knowledge_cursors(expires_at)",
)
