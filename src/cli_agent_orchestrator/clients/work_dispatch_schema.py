"""Immutable per-attempt order bindings; repository migration owns installation."""

DISPATCH_SCHEMA = (
    """CREATE TABLE work_dispatch_bindings (
        attempt_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
        job_id TEXT NOT NULL, work_item_id TEXT NOT NULL,
        principal_id TEXT NOT NULL REFERENCES work_principals(id),
        grant_id TEXT NOT NULL, grant_revision INTEGER NOT NULL CHECK(grant_revision>0),
        contract_id TEXT NOT NULL,
        contract_json TEXT NOT NULL CHECK(json_valid(contract_json)
            AND json_type(contract_json)='object'
            AND coalesce(json_extract(contract_json,'$.schema_version'),0)=1
            AND json_type(contract_json,'$.schema_version')='integer'
            AND coalesce(json_type(contract_json,'$.id'),'')='text'
            AND coalesce(json_extract(contract_json,'$.id'),'')=contract_id
            AND length(contract_json)<=32768),
        contract_hash TEXT NOT NULL CHECK(length(contract_hash)=64),
        snapshot_id TEXT NOT NULL REFERENCES work_delegation_snapshots(id),
        created_at REAL NOT NULL,
        PRIMARY KEY(attempt_id,generation),
        FOREIGN KEY(job_id,work_item_id) REFERENCES work_items(job_id,id),
        FOREIGN KEY(work_item_id,attempt_id,generation) REFERENCES work_attempts(work_item_id,id,generation),
        FOREIGN KEY(job_id,grant_id,grant_revision) REFERENCES work_grants(job_id,id,revision)
    )""",
    "CREATE INDEX work_dispatch_bindings_contract ON work_dispatch_bindings(contract_id)",
    """CREATE TRIGGER work_dispatch_bindings_immutable_update BEFORE UPDATE ON work_dispatch_bindings
        BEGIN SELECT RAISE(ABORT,'work order bindings are immutable'); END""",
    """CREATE TRIGGER work_dispatch_bindings_immutable_delete BEFORE DELETE ON work_dispatch_bindings
        BEGIN SELECT RAISE(ABORT,'work order history cannot be deleted'); END""",
)


DISPATCH_V2_EVIDENCE_SCHEMA = (
    """CREATE TABLE work_dispatch_v2_evidence (
        attempt_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
        contract_json TEXT NOT NULL CHECK(json_valid(contract_json)
            AND json_type(contract_json)='object'
            AND coalesce(json_type(contract_json,'$.schema_version'),'')='integer'
            AND coalesce(json_extract(contract_json,'$.schema_version'),0)=2
            AND length(contract_json)<=32768),
        contract_hash TEXT NOT NULL CHECK(length(contract_hash)=64
            AND contract_hash NOT GLOB '*[^0-9a-f]*'),
        PRIMARY KEY(attempt_id,generation),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_dispatch_bindings(attempt_id,generation)
    )""",
    """CREATE TRIGGER work_dispatch_v2_evidence_immutable_update
        BEFORE UPDATE ON work_dispatch_v2_evidence
        BEGIN SELECT RAISE(ABORT,'v2 work order evidence is immutable'); END""",
    """CREATE TRIGGER work_dispatch_v2_evidence_immutable_delete
        BEFORE DELETE ON work_dispatch_v2_evidence
        BEGIN SELECT RAISE(ABORT,'v2 work order evidence cannot be deleted'); END""",
)
