"""Bounded, immutable delegation snapshot DDL; repository migration owns installation."""

SNAPSHOT_SCHEMA = (
    """CREATE TABLE work_delegation_snapshots (
        id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL CHECK(schema_version=1),
        job_id TEXT NOT NULL REFERENCES work_jobs(id),
        contract_id TEXT NOT NULL, binding_key TEXT NOT NULL,
        request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
        scope TEXT NOT NULL CHECK(scope IN ('project','job')), scope_id TEXT NOT NULL,
        producer_principal_id TEXT NOT NULL REFERENCES work_principals(id),
        source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
        delivered_hash TEXT NOT NULL CHECK(length(delivered_hash)=64),
        content BLOB NOT NULL CHECK(typeof(content)='blob' AND length(content)<=65536),
        redacted INTEGER NOT NULL CHECK(redacted IN (0,1)),
        truncated INTEGER NOT NULL CHECK(truncated IN (0,1)), created_at REAL NOT NULL,
        UNIQUE(job_id,contract_id,binding_key)
    )""",
    """CREATE TABLE work_snapshot_sources (
        snapshot_id TEXT NOT NULL REFERENCES work_delegation_snapshots(id),
        record_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
        PRIMARY KEY(snapshot_id,record_id,revision),
        FOREIGN KEY(record_id,revision) REFERENCES work_knowledge_revisions(record_id,revision)
    )""",
    *tuple(
        f"CREATE TRIGGER {table}_immutable_{operation.lower()} BEFORE {operation} ON {table} "
        "BEGIN SELECT RAISE(ABORT,'delegation snapshot history is immutable'); END"
        for table in ("work_delegation_snapshots", "work_snapshot_sources")
        for operation in ("UPDATE", "DELETE")
    ),
)
