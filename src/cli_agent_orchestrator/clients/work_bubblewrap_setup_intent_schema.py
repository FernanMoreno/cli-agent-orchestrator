"""Immutable pre-release Bubblewrap setup evidence."""

BUBBLEWRAP_SETUP_INTENT_SCHEMA = (
    "CREATE UNIQUE INDEX work_bubblewrap_identity_intent_reference "
    "ON work_bubblewrap_process_identities(attempt_id,generation,identity_sha256)",
    """CREATE TABLE work_bubblewrap_setup_intents (
        attempt_id TEXT PRIMARY KEY REFERENCES work_attempts(id),
        generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
        schema_version INTEGER NOT NULL CHECK(typeof(schema_version)='integer' AND schema_version=1),
        contract_hash TEXT NOT NULL CHECK(length(contract_hash)=64 AND contract_hash NOT GLOB '*[^0-9a-f]*'),
        command_token TEXT NOT NULL,
        executable_sha256 TEXT NOT NULL REFERENCES work_executable_contents(content_hash)
            CHECK(length(executable_sha256)=64 AND executable_sha256 NOT GLOB '*[^0-9a-f]*'),
        ack_json TEXT NOT NULL CHECK(json_valid(ack_json) AND length(CAST(ack_json AS BLOB))<=8192),
        ack_sha256 TEXT NOT NULL CHECK(length(ack_sha256)=64 AND ack_sha256 NOT GLOB '*[^0-9a-f]*'),
        process_identity_sha256 TEXT NOT NULL
            CHECK(length(process_identity_sha256)=64 AND process_identity_sha256 NOT GLOB '*[^0-9a-f]*'),
        release_intent TEXT NOT NULL CHECK(release_intent='pending'),
        created_at REAL NOT NULL,
        FOREIGN KEY(attempt_id,generation,process_identity_sha256)
            REFERENCES work_bubblewrap_process_identities(attempt_id,generation,identity_sha256)
    )""",
    """CREATE TRIGGER work_bubblewrap_setup_intents_immutable_update
        BEFORE UPDATE ON work_bubblewrap_setup_intents
        BEGIN SELECT RAISE(ABORT,'Bubblewrap setup intent is immutable'); END""",
    """CREATE TRIGGER work_bubblewrap_setup_intents_immutable_delete
        BEFORE DELETE ON work_bubblewrap_setup_intents
        BEGIN SELECT RAISE(ABORT,'Bubblewrap setup intent cannot be deleted'); END""",
)

BUBBLEWRAP_RELEASE_CLAIM_SCHEMA = (
    "CREATE UNIQUE INDEX work_bubblewrap_setup_release_reference "
    "ON work_bubblewrap_setup_intents("
    "attempt_id,generation,contract_hash,command_token,executable_sha256,"
    "ack_sha256,process_identity_sha256)",
    """CREATE TABLE work_bubblewrap_release_claims (
        attempt_id TEXT PRIMARY KEY,
        generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
        schema_version INTEGER NOT NULL DEFAULT 1 CHECK(typeof(schema_version)='integer' AND schema_version=1),
        contract_hash TEXT NOT NULL CHECK(length(contract_hash)=64 AND contract_hash NOT GLOB '*[^0-9a-f]*'),
        command_token TEXT NOT NULL,
        executable_sha256 TEXT NOT NULL
            CHECK(length(executable_sha256)=64 AND executable_sha256 NOT GLOB '*[^0-9a-f]*'),
        ack_sha256 TEXT NOT NULL CHECK(length(ack_sha256)=64 AND ack_sha256 NOT GLOB '*[^0-9a-f]*'),
        process_identity_sha256 TEXT NOT NULL
            CHECK(length(process_identity_sha256)=64 AND process_identity_sha256 NOT GLOB '*[^0-9a-f]*'),
        claimed_at REAL NOT NULL CHECK(typeof(claimed_at)='real' AND claimed_at>0),
        FOREIGN KEY(attempt_id,generation,contract_hash,command_token,executable_sha256,
                    ack_sha256,process_identity_sha256)
            REFERENCES work_bubblewrap_setup_intents(
                attempt_id,generation,contract_hash,command_token,executable_sha256,
                ack_sha256,process_identity_sha256)
    )""",
    """CREATE TRIGGER work_bubblewrap_release_claims_immutable_update
        BEFORE UPDATE ON work_bubblewrap_release_claims
        BEGIN SELECT RAISE(ABORT,'Bubblewrap release claim is immutable'); END""",
    """CREATE TRIGGER work_bubblewrap_release_claims_immutable_delete
        BEFORE DELETE ON work_bubblewrap_release_claims
        BEGIN SELECT RAISE(ABORT,'Bubblewrap release claim cannot be deleted'); END""",
)
