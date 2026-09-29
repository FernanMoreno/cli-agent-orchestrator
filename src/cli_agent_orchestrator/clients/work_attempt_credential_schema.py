"""Additive schema for one-time, attempt-bound CAO credentials (T019)."""

WORK_ATTEMPT_CREDENTIAL_SCHEMA = (
    "CREATE UNIQUE INDEX work_attempt_credential_binding "
    "ON work_dispatch_bindings(attempt_id,generation,contract_hash)",
    """CREATE TABLE work_attempt_credentials (
        attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
        job_id TEXT NOT NULL,
        work_item_id TEXT NOT NULL,
        principal_id TEXT NOT NULL REFERENCES work_principals(id),
        grant_id TEXT NOT NULL,
        grant_revision INTEGER NOT NULL CHECK(typeof(grant_revision)='integer' AND grant_revision>0),
        installation_uuid TEXT NOT NULL CHECK(length(installation_uuid)=32
            AND installation_uuid NOT GLOB '*[^0-9a-f]*'),
        contract_hash TEXT NOT NULL CHECK(length(contract_hash)=64
            AND contract_hash NOT GLOB '*[^0-9a-f]*'),
        attempt_revision INTEGER NOT NULL CHECK(typeof(attempt_revision)='integer' AND attempt_revision>0),
        lease_expires_at REAL NOT NULL CHECK(typeof(lease_expires_at)='real' AND lease_expires_at>0),
        expires_at REAL NOT NULL CHECK(typeof(expires_at)='real' AND expires_at>0),
        credential_sha256 TEXT NOT NULL UNIQUE CHECK(length(credential_sha256)=64
            AND credential_sha256 NOT GLOB '*[^0-9a-f]*'),
        issued_at REAL NOT NULL CHECK(typeof(issued_at)='real' AND issued_at>0),
        schema_version INTEGER NOT NULL DEFAULT 1 CHECK(typeof(schema_version)='integer' AND schema_version=1),
        PRIMARY KEY(attempt_id,generation),
        CHECK(expires_at<=lease_expires_at),
        FOREIGN KEY(job_id,work_item_id) REFERENCES work_items(job_id,id),
        FOREIGN KEY(work_item_id,attempt_id,generation)
            REFERENCES work_attempts(work_item_id,id,generation),
        FOREIGN KEY(job_id,grant_id,grant_revision)
            REFERENCES work_grants(job_id,id,revision),
        FOREIGN KEY(attempt_id,generation,contract_hash)
            REFERENCES work_dispatch_bindings(attempt_id,generation,contract_hash)
    )""",
    """CREATE TRIGGER work_attempt_credentials_immutable_update
        BEFORE UPDATE ON work_attempt_credentials
        BEGIN SELECT RAISE(ABORT,'attempt credentials are immutable'); END""",
    """CREATE TRIGGER work_attempt_credentials_immutable_delete
        BEFORE DELETE ON work_attempt_credentials
        BEGIN SELECT RAISE(ABORT,'attempt credential history cannot be deleted'); END""",
)
