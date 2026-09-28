"""Private, immutable Bubblewrap process identities for cleanup after restart."""

BUBBLEWRAP_IDENTITY_SCHEMA = (
    """CREATE TABLE work_bubblewrap_process_identities (
        attempt_id TEXT PRIMARY KEY REFERENCES work_attempts(id),
        generation INTEGER NOT NULL CHECK(generation>0),
        protocol_version INTEGER NOT NULL CHECK(protocol_version=1),
        identity_json TEXT NOT NULL,
        identity_sha256 TEXT NOT NULL CHECK(
            length(identity_sha256)=64 AND identity_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        created_at REAL NOT NULL
    )""",
    """CREATE TRIGGER work_bubblewrap_process_identities_immutable_update
        BEFORE UPDATE ON work_bubblewrap_process_identities
        BEGIN SELECT RAISE(ABORT,'Bubblewrap process identity is immutable'); END""",
    """CREATE TRIGGER work_bubblewrap_process_identities_immutable_delete
        BEFORE DELETE ON work_bubblewrap_process_identities
        BEGIN SELECT RAISE(ABORT,'Bubblewrap process identity cannot be deleted'); END""",
    """CREATE TRIGGER work_bubblewrap_process_identities_no_unshare
        BEFORE INSERT ON work_bubblewrap_process_identities
        WHEN EXISTS(SELECT 1 FROM work_process_identities WHERE attempt_id=NEW.attempt_id)
        BEGIN SELECT RAISE(ABORT,'attempt already has an unshare process identity'); END""",
    """CREATE TRIGGER work_process_identities_no_bubblewrap
        BEFORE INSERT ON work_process_identities
        WHEN EXISTS(SELECT 1 FROM work_bubblewrap_process_identities WHERE attempt_id=NEW.attempt_id)
        BEGIN SELECT RAISE(ABORT,'attempt already has a Bubblewrap process identity'); END""",
)
