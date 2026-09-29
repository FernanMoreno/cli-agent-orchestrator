"""Additive per-attempt bearer for the independently authorized receiver."""

WORK_TASK_RECEIVER_CREDENTIAL_SCHEMA = (
    """CREATE TABLE work_task_receiver_credentials (
        attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
        job_id TEXT NOT NULL,
        work_item_id TEXT NOT NULL,
        receiver_subject_id TEXT NOT NULL REFERENCES work_principals(id),
        receiver_subject_revision INTEGER NOT NULL
            CHECK(typeof(receiver_subject_revision)='integer' AND receiver_subject_revision>0),
        receiver_authorization_revision INTEGER NOT NULL
            CHECK(typeof(receiver_authorization_revision)='integer' AND receiver_authorization_revision>0),
        receiver_grant_id TEXT NOT NULL,
        receiver_grant_revision INTEGER NOT NULL
            CHECK(typeof(receiver_grant_revision)='integer' AND receiver_grant_revision>0),
        installation_uuid TEXT NOT NULL CHECK(length(installation_uuid)=32
            AND installation_uuid NOT GLOB '*[^0-9a-f]*'),
        attempt_revision INTEGER NOT NULL CHECK(typeof(attempt_revision)='integer' AND attempt_revision>0),
        lease_expires_at REAL NOT NULL CHECK(typeof(lease_expires_at)='real' AND lease_expires_at>0),
        expires_at REAL NOT NULL CHECK(typeof(expires_at)='real' AND expires_at>0),
        delivery_id TEXT NOT NULL CHECK(length(delivery_id) BETWEEN 1 AND 512),
        delivery_hash TEXT NOT NULL CHECK(length(delivery_hash)=64
            AND delivery_hash NOT GLOB '*[^0-9a-f]*'),
        credential_sha256 TEXT NOT NULL UNIQUE CHECK(length(credential_sha256)=64
            AND credential_sha256 NOT GLOB '*[^0-9a-f]*'),
        issued_at REAL NOT NULL CHECK(typeof(issued_at)='real' AND issued_at>0),
        schema_version INTEGER NOT NULL DEFAULT 1 CHECK(typeof(schema_version)='integer' AND schema_version=1),
        PRIMARY KEY(attempt_id,generation),
        CHECK(expires_at<=lease_expires_at),
        FOREIGN KEY(job_id,work_item_id) REFERENCES work_items(job_id,id),
        FOREIGN KEY(work_item_id,attempt_id,generation)
            REFERENCES work_attempts(work_item_id,id,generation),
        FOREIGN KEY(job_id,receiver_grant_id,receiver_grant_revision)
            REFERENCES work_grants(job_id,id,revision),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_attempt_credentials(attempt_id,generation),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_child_origin_bindings(child_attempt_id,child_generation)
    )""",
    """CREATE TRIGGER work_task_receiver_credentials_immutable_update
        BEFORE UPDATE ON work_task_receiver_credentials
        BEGIN SELECT RAISE(ABORT,'receiver credentials are immutable'); END""",
    """CREATE TRIGGER work_task_receiver_credentials_immutable_delete
        BEFORE DELETE ON work_task_receiver_credentials
        BEGIN SELECT RAISE(ABORT,'receiver credential history cannot be deleted'); END""",
)
