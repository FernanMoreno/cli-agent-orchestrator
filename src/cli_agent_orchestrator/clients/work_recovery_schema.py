"""T065 v24 durable recovery context; restore stays blocked until a later workflow reconciles it."""

from dataclasses import dataclass

RECOVERY_CONTEXT_VERSION = 1
RECOVERY_STATE_NORMAL = "normal"
RECOVERY_STATE_BLOCKED_RESTORE = "blocked_restore"


@dataclass(frozen=True)
class RecoveryStoreContext:
    """Verified, non-secret context for one v24 work-store installation."""

    context_version: int
    execution_state: str
    installation_uuid: str
    source_installation_uuid: str | None
    bundle_digest: str | None
    restore_receipt: str | None


RECOVERY_CONTEXT_SCHEMA = (
    """CREATE TABLE work_recovery_context (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        context_version INTEGER NOT NULL CHECK(context_version=1),
        execution_state TEXT NOT NULL
            CHECK(execution_state IN ('normal','blocked_restore')),
        installation_uuid TEXT NOT NULL UNIQUE
            CHECK(length(installation_uuid)=32
                AND installation_uuid NOT GLOB '*[^0-9a-f]*'),
        source_installation_uuid TEXT
            CHECK(source_installation_uuid IS NULL OR
                (length(source_installation_uuid)=32
                    AND source_installation_uuid NOT GLOB '*[^0-9a-f]*')),
        bundle_digest TEXT
            CHECK(bundle_digest IS NULL OR
                (length(bundle_digest)=64 AND bundle_digest NOT GLOB '*[^0-9a-f]*')),
        restore_receipt TEXT
            CHECK(restore_receipt IS NULL OR
                (length(restore_receipt)=64
                    AND restore_receipt NOT GLOB '*[^0-9a-f]*')),
        created_at REAL NOT NULL,
        CHECK(
            (execution_state='normal'
                AND source_installation_uuid IS NULL
                AND bundle_digest IS NULL
                AND restore_receipt IS NULL)
            OR
            (execution_state='blocked_restore'
                AND source_installation_uuid IS NOT NULL
                AND bundle_digest IS NOT NULL
                AND restore_receipt IS NOT NULL
                AND source_installation_uuid<>installation_uuid)
        )
    )""",
    """CREATE TRIGGER work_recovery_context_transition
        BEFORE UPDATE ON work_recovery_context
        WHEN NOT (
            OLD.singleton=1
            AND OLD.context_version=1
            AND OLD.execution_state='normal'
            AND OLD.source_installation_uuid IS NULL
            AND OLD.bundle_digest IS NULL
            AND OLD.restore_receipt IS NULL
            AND NEW.singleton=1
            AND NEW.context_version=1
            AND NEW.execution_state='blocked_restore'
            AND NEW.installation_uuid<>OLD.installation_uuid
            AND NEW.source_installation_uuid=OLD.installation_uuid
            AND NEW.bundle_digest IS NOT NULL
            AND NEW.restore_receipt IS NOT NULL
            AND NEW.created_at=OLD.created_at
        )
        BEGIN SELECT RAISE(ABORT,'recovery context only transitions to blocked restore'); END""",
    """CREATE TRIGGER work_recovery_context_immutable_delete
        BEFORE DELETE ON work_recovery_context
        BEGIN SELECT RAISE(ABORT,'recovery context cannot be deleted'); END""",
)

# T069 is deliberately separate from the restore context above.  A live cut is
# a short-lived server-owned writer fence, never a restore authorization.
OFFLINE_CUT_SCHEMA = (
    """CREATE TABLE work_offline_cuts (
        id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
        store_identity TEXT NOT NULL,
        operator_principal_id TEXT NOT NULL REFERENCES work_principals(id),
        owner TEXT NOT NULL CHECK(owner='work'),
        scope TEXT NOT NULL CHECK(scope='registered-work-writers'),
        epoch INTEGER NOT NULL CHECK(epoch>0),
        fence INTEGER NOT NULL CHECK(fence>0),
        expires_at REAL NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>0),
        state TEXT NOT NULL CHECK(state IN ('live','revoked','expired','published')),
        created_at REAL NOT NULL,
        revoked_at REAL,
        published_path TEXT,
        published_capture_id TEXT,
        published_manifest_digest TEXT,
        published_manifest_size INTEGER,
        published_at REAL,
        CHECK(
            (state='published'
                AND published_path IS NOT NULL
                AND published_capture_id IS NOT NULL
                AND length(published_capture_id)=32
                AND published_capture_id NOT GLOB '*[^0-9a-f]*'
                AND published_manifest_digest IS NOT NULL
                AND length(published_manifest_digest)=64
                AND published_manifest_digest NOT GLOB '*[^0-9a-f]*'
                AND published_manifest_size IS NOT NULL
                AND published_manifest_size>=0
                AND published_at IS NOT NULL)
            OR
            (state<>'published'
                AND published_path IS NULL
                AND published_capture_id IS NULL
                AND published_manifest_digest IS NULL
                AND published_manifest_size IS NULL
                AND published_at IS NULL)
        )
    )""",
    """CREATE UNIQUE INDEX work_offline_cuts_one_live
        ON work_offline_cuts(state) WHERE state='live'""",
)

OFFLINE_CUT_OBSERVATION_SCHEMA = (
    """CREATE TABLE work_offline_cut_observations (
        lease_id TEXT NOT NULL REFERENCES work_offline_cuts(id),
        capture_id TEXT NOT NULL CHECK(length(capture_id)=32 AND capture_id NOT GLOB '*[^0-9a-f]*'),
        phase TEXT NOT NULL CHECK(phase IN ('before','after','promote')),
        observation TEXT NOT NULL,
        PRIMARY KEY(lease_id,capture_id,phase)
    )""",
)

WORK_WRITER_REGISTRY_SCHEMA = (
    """CREATE TABLE work_registered_writers (
        writer_id TEXT PRIMARY KEY REFERENCES work_attempts(id),
        owner TEXT NOT NULL CHECK(owner='work'),
        scope TEXT NOT NULL CHECK(scope='registered-work-writers'),
        state TEXT NOT NULL CHECK(state IN ('active','released')),
        revision INTEGER NOT NULL CHECK(revision>0),
        registered_at REAL NOT NULL,
        released_at REAL
    )""",
    """CREATE INDEX work_registered_writers_active
        ON work_registered_writers(state) WHERE state='active'""",
)

OFFLINE_CUT_REJECTION_SCHEMA = (
    """CREATE TABLE work_offline_cut_rejections (
        id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
        lease_id TEXT NOT NULL,
        phase TEXT NOT NULL CHECK(phase IN ('before','after','promote','stage')),
        reason TEXT NOT NULL CHECK(reason='capture_rejected'),
        created_at REAL NOT NULL
    )""",
)
