"""Additive authority DDL installed only by the verified work repository migration."""

AUTHORITY_SCHEMA = (
    """CREATE TABLE work_principals (
        id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('jwt','local_operator')),
        created_at REAL NOT NULL, UNIQUE(issuer,subject)
    )""",
    """CREATE TABLE work_grants (
        id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
        schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version=1),
        job_id TEXT NOT NULL REFERENCES work_jobs(id),
        principal_id TEXT NOT NULL REFERENCES work_principals(id),
        parent_grant_id TEXT, parent_revision INTEGER,
        allowed_providers TEXT NOT NULL, permissions TEXT NOT NULL,
        enforcement_level TEXT NOT NULL DEFAULT 'cooperative' CHECK(enforcement_level='cooperative'),
        expires_at REAL NOT NULL, created_at REAL NOT NULL,
        PRIMARY KEY(id,revision), UNIQUE(job_id,id,revision),
        CHECK((parent_grant_id IS NULL AND parent_revision IS NULL) OR
              (parent_grant_id IS NOT NULL AND parent_revision IS NOT NULL AND parent_revision>0)),
        FOREIGN KEY(job_id,parent_grant_id,parent_revision) REFERENCES work_grants(job_id,id,revision)
    )""",
    """CREATE TABLE work_grant_revocations (
        grant_id TEXT NOT NULL, grant_revision INTEGER NOT NULL CHECK(grant_revision>0),
        actor_principal_id TEXT NOT NULL REFERENCES work_principals(id),
        revoked_at REAL NOT NULL, reason TEXT NOT NULL,
        PRIMARY KEY(grant_id,grant_revision),
        FOREIGN KEY(grant_id,grant_revision) REFERENCES work_grants(id,revision)
    )""",
    """CREATE TRIGGER work_grants_immutable_update BEFORE UPDATE ON work_grants
        BEGIN SELECT RAISE(ABORT,'grant snapshots are immutable'); END""",
    """CREATE TRIGGER work_grants_immutable_delete BEFORE DELETE ON work_grants
        BEGIN SELECT RAISE(ABORT,'grant history cannot be deleted'); END""",
    """CREATE TRIGGER work_grant_revocations_immutable_update BEFORE UPDATE ON work_grant_revocations
        BEGIN SELECT RAISE(ABORT,'revocations are immutable'); END""",
    """CREATE TRIGGER work_grant_revocations_immutable_delete BEFORE DELETE ON work_grant_revocations
        BEGIN SELECT RAISE(ABORT,'revocations cannot be deleted'); END""",
)
