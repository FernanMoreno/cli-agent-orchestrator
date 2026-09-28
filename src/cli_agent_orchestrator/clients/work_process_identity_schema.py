"""Versioned durable process identities for Work attempts."""

PROCESS_IDENTITY_SCHEMA = (
    """CREATE TABLE work_process_identities (
        attempt_id TEXT PRIMARY KEY REFERENCES work_attempts(id),
        generation INTEGER NOT NULL CHECK(generation>0),
        protocol_version INTEGER NOT NULL CHECK(protocol_version=3),
        identity_json TEXT NOT NULL,
        identity_sha256 TEXT NOT NULL CHECK(
            length(identity_sha256)=64 AND identity_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        created_at REAL NOT NULL
    )""",
    """CREATE TRIGGER work_process_identities_immutable_update
        BEFORE UPDATE ON work_process_identities
        BEGIN SELECT RAISE(ABORT,'Work process identity is immutable'); END""",
    """CREATE TRIGGER work_process_identities_immutable_delete
        BEFORE DELETE ON work_process_identities
        BEGIN SELECT RAISE(ABORT,'Work process identity cannot be deleted'); END""",
)
