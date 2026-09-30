"""T093 v1 launch-provision history; installed only by the verified v19 migration."""

ORIGIN_SCHEMA = (
    """CREATE TABLE work_launch_provisions (
        principal_id TEXT NOT NULL REFERENCES work_principals(id),
        selector TEXT NOT NULL CHECK(length(selector) BETWEEN 1 AND 128
            AND selector NOT GLOB '*[^A-Za-z0-9._:-]*'),
        revision INTEGER NOT NULL CHECK(revision>0),
        id TEXT NOT NULL CHECK(length(id) BETWEEN 1 AND 128
            AND id NOT GLOB '*[^A-Za-z0-9._:-]*'),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        state TEXT NOT NULL CHECK(state IN ('active','retired')),
        issuer_id TEXT NOT NULL REFERENCES work_principals(id),
        job_id TEXT NOT NULL REFERENCES work_jobs(id),
        grant_id TEXT NOT NULL,
        grant_revision INTEGER NOT NULL CHECK(grant_revision>0),
        contract_id TEXT NOT NULL CHECK(length(contract_id) BETWEEN 1 AND 128),
        contract_json TEXT NOT NULL CHECK(json_valid(contract_json)
            AND json_type(contract_json)='object' AND length(contract_json)<=32768),
        contract_hash TEXT NOT NULL CHECK(length(contract_hash)=64
            AND contract_hash NOT GLOB '*[^0-9a-f]*'),
        snapshot_id TEXT NOT NULL REFERENCES work_delegation_snapshots(id),
        snapshot_hash TEXT NOT NULL CHECK(length(snapshot_hash)=64
            AND snapshot_hash NOT GLOB '*[^0-9a-f]*'),
        adapter_version INTEGER NOT NULL CHECK(adapter_version>0),
        lease_seconds INTEGER NOT NULL CHECK(lease_seconds BETWEEN 1 AND 3600),
        created_at REAL NOT NULL,
        PRIMARY KEY(principal_id,selector,revision),
        FOREIGN KEY(job_id,grant_id,grant_revision)
            REFERENCES work_grants(job_id,id,revision)
    )""",
    """CREATE INDEX work_launch_provisions_current_lookup
        ON work_launch_provisions(principal_id,selector,revision DESC)""",
    """CREATE TRIGGER work_launch_provisions_immutable_update
        BEFORE UPDATE ON work_launch_provisions
        BEGIN SELECT RAISE(ABORT,'launch provision history is immutable'); END""",
    """CREATE TRIGGER work_launch_provisions_immutable_delete
        BEFORE DELETE ON work_launch_provisions
        BEGIN SELECT RAISE(ABORT,'launch provision history cannot be deleted'); END""",
)


ORIGIN_AUTHORITY_SCHEMA = (
    """CREATE TABLE work_origin_subjects (
        subject_id TEXT NOT NULL REFERENCES work_principals(id),
        revision INTEGER NOT NULL CHECK(revision>0),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        origin_kind TEXT NOT NULL CHECK(origin_kind IN ('child','workflow','receiver')),
        state TEXT NOT NULL CHECK(state IN ('active','revoked')),
        issuer_id TEXT NOT NULL REFERENCES work_principals(id),
        created_at REAL NOT NULL,
        PRIMARY KEY(subject_id,revision),
        UNIQUE(subject_id,origin_kind,revision)
    )""",
    """CREATE TABLE work_origin_authorizations (
        subject_id TEXT NOT NULL,
        origin_kind TEXT NOT NULL CHECK(origin_kind IN ('child','workflow','receiver')),
        revision INTEGER NOT NULL CHECK(revision>0),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        state TEXT NOT NULL CHECK(state IN ('active','revoked')),
        issuer_id TEXT NOT NULL REFERENCES work_principals(id),
        subject_revision INTEGER NOT NULL CHECK(subject_revision>0),
        job_id TEXT NOT NULL REFERENCES work_jobs(id),
        grant_id TEXT NOT NULL,
        grant_revision INTEGER NOT NULL CHECK(grant_revision>0),
        actions TEXT NOT NULL CHECK(json_valid(actions) AND json_type(actions)='array'
            AND json_array_length(actions) BETWEEN 1 AND 7),
        expires_at REAL NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY(subject_id,origin_kind,revision),
        FOREIGN KEY(subject_id,subject_revision)
            REFERENCES work_origin_subjects(subject_id,revision),
        FOREIGN KEY(job_id,grant_id,grant_revision)
            REFERENCES work_grants(job_id,id,revision)
    )""",
    """CREATE INDEX work_origin_subjects_current_lookup
        ON work_origin_subjects(subject_id,revision DESC)""",
    """CREATE INDEX work_origin_authorizations_current_lookup
        ON work_origin_authorizations(subject_id,origin_kind,revision DESC)""",
    """CREATE TRIGGER work_origin_subjects_immutable_update
        BEFORE UPDATE ON work_origin_subjects
        BEGIN SELECT RAISE(ABORT,'origin subject history is immutable'); END""",
    """CREATE TRIGGER work_origin_subjects_immutable_delete
        BEFORE DELETE ON work_origin_subjects
        BEGIN SELECT RAISE(ABORT,'origin subject history cannot be deleted'); END""",
    """CREATE TRIGGER work_origin_authorizations_immutable_update
        BEFORE UPDATE ON work_origin_authorizations
        BEGIN SELECT RAISE(ABORT,'origin authorization history is immutable'); END""",
    """CREATE TRIGGER work_origin_authorizations_immutable_delete
        BEFORE DELETE ON work_origin_authorizations
        BEGIN SELECT RAISE(ABORT,'origin authorization history cannot be deleted'); END""",
)


# v21 binds a durable launch admission to its exact v19 provision.  The explicit
# parent marker is deliberately not inferred from a missing row: pre-v21 work
# remains legacy while all v21 launch admissions must have this companion row.
LAUNCH_ORIGIN_SCHEMA = (
    "ALTER TABLE work_items ADD COLUMN origin_protocol TEXT NOT NULL DEFAULT 'legacy' "
    "CHECK(origin_protocol IN ('legacy','launch-v1'))",
    """CREATE TRIGGER work_items_origin_protocol_monotonic
        BEFORE UPDATE OF origin_protocol ON work_items
        WHEN NEW.origin_protocol<>OLD.origin_protocol
          AND NOT (OLD.origin_protocol='legacy' AND NEW.origin_protocol='launch-v1')
        BEGIN SELECT RAISE(ABORT,'work origin protocol is monotonic'); END""",
    """CREATE TABLE work_launch_origin_bindings (
        attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>0),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        origin_kind TEXT NOT NULL CHECK(origin_kind='launch'),
        principal_id TEXT NOT NULL REFERENCES work_principals(id),
        selector TEXT NOT NULL,
        provision_id TEXT NOT NULL,
        provision_revision INTEGER NOT NULL CHECK(provision_revision>0),
        provision_fingerprint TEXT NOT NULL CHECK(length(provision_fingerprint)=64),
        requester_principal_id TEXT NOT NULL REFERENCES work_principals(id),
        executor_principal_id TEXT NOT NULL REFERENCES work_principals(id),
        job_id TEXT NOT NULL REFERENCES work_jobs(id),
        work_item_id TEXT NOT NULL REFERENCES work_items(id),
        request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
        idempotency_key TEXT NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY(attempt_id,generation),
        UNIQUE(principal_id,selector,idempotency_key),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_dispatch_bindings(attempt_id,generation),
        FOREIGN KEY(principal_id,selector,provision_revision)
            REFERENCES work_launch_provisions(principal_id,selector,revision)
    )""",
    "CREATE INDEX work_launch_origin_bindings_provision ON "
    "work_launch_origin_bindings(principal_id,selector,provision_id,provision_revision)",
    """CREATE TRIGGER work_launch_origin_bindings_immutable_update
        BEFORE UPDATE ON work_launch_origin_bindings
        BEGIN SELECT RAISE(ABORT,'work launch origin bindings are immutable'); END""",
    """CREATE TRIGGER work_launch_origin_bindings_immutable_delete
        BEFORE DELETE ON work_launch_origin_bindings
        BEGIN SELECT RAISE(ABORT,'work launch origin bindings are immutable'); END""",
)


# v22 adds a separate managed-lineage marker.  It intentionally does not change
# launch-v1 semantics: pre-v22 work remains legacy lineage, while a managed
# child/handoff must carry an immutable binding for its exact parent attempt.
LINEAGE_ORIGIN_SCHEMA = (
    "ALTER TABLE work_items ADD COLUMN lineage_protocol TEXT NOT NULL DEFAULT 'legacy' "
    "CHECK(lineage_protocol IN ('legacy','managed-v1'))",
    """CREATE TRIGGER work_items_lineage_protocol_monotonic
        BEFORE UPDATE OF lineage_protocol ON work_items
        WHEN NEW.lineage_protocol<>OLD.lineage_protocol
          AND NOT (OLD.lineage_protocol='legacy' AND NEW.lineage_protocol='managed-v1')
        BEGIN SELECT RAISE(ABORT,'work lineage protocol is monotonic'); END""",
    """CREATE TABLE work_child_origin_bindings (
        child_attempt_id TEXT NOT NULL,
        child_generation INTEGER NOT NULL CHECK(child_generation>0),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        kind TEXT NOT NULL CHECK(kind IN ('child','handoff')),
        job_id TEXT NOT NULL REFERENCES work_jobs(id),
        parent_work_item_id TEXT NOT NULL REFERENCES work_items(id),
        parent_attempt_id TEXT NOT NULL,
        parent_generation INTEGER NOT NULL CHECK(parent_generation>0),
        child_work_item_id TEXT NOT NULL REFERENCES work_items(id),
        requester_principal_id TEXT NOT NULL REFERENCES work_principals(id),
        executor_principal_id TEXT NOT NULL REFERENCES work_principals(id),
        child_subject_id TEXT NOT NULL REFERENCES work_principals(id),
        child_subject_revision INTEGER NOT NULL CHECK(child_subject_revision>0),
        child_authorization_kind TEXT NOT NULL CHECK(child_authorization_kind='child'),
        child_authorization_revision INTEGER NOT NULL CHECK(child_authorization_revision>0),
        child_grant_id TEXT NOT NULL,
        child_grant_revision INTEGER NOT NULL CHECK(child_grant_revision>0),
        parent_contract_hash TEXT NOT NULL CHECK(length(parent_contract_hash)=64),
        child_contract_hash TEXT NOT NULL CHECK(length(child_contract_hash)=64),
        snapshot_id TEXT NOT NULL REFERENCES work_delegation_snapshots(id),
        snapshot_hash TEXT NOT NULL CHECK(length(snapshot_hash)=64),
        receiver_subject_id TEXT NOT NULL REFERENCES work_principals(id),
        receiver_subject_revision INTEGER NOT NULL CHECK(receiver_subject_revision>0),
        receiver_authorization_kind TEXT NOT NULL CHECK(receiver_authorization_kind='receiver'),
        receiver_authorization_revision INTEGER NOT NULL CHECK(receiver_authorization_revision>0),
        receiver_grant_id TEXT NOT NULL,
        receiver_grant_revision INTEGER NOT NULL CHECK(receiver_grant_revision>0),
        delivery_id TEXT NOT NULL,
        delivery_hash TEXT NOT NULL CHECK(length(delivery_hash)=64),
        request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
        idempotency_key TEXT NOT NULL,
        native_child_id TEXT,
        terminal_id TEXT,
        handoff_id TEXT,
        created_at REAL NOT NULL,
        PRIMARY KEY(child_attempt_id,child_generation),
        UNIQUE(child_work_item_id),
        UNIQUE(delivery_id),
        UNIQUE(native_child_id),
        UNIQUE(handoff_id),
        UNIQUE(requester_principal_id,parent_attempt_id,parent_generation,kind,idempotency_key),
        FOREIGN KEY(child_attempt_id,child_generation)
            REFERENCES work_dispatch_bindings(attempt_id,generation),
        FOREIGN KEY(parent_attempt_id,parent_generation)
            REFERENCES work_dispatch_bindings(attempt_id,generation),
        FOREIGN KEY(job_id,child_grant_id,child_grant_revision)
            REFERENCES work_grants(job_id,id,revision),
        FOREIGN KEY(job_id,receiver_grant_id,receiver_grant_revision)
            REFERENCES work_grants(job_id,id,revision),
        FOREIGN KEY(child_subject_id,child_subject_revision)
            REFERENCES work_origin_subjects(subject_id,revision),
        FOREIGN KEY(receiver_subject_id,receiver_subject_revision)
            REFERENCES work_origin_subjects(subject_id,revision),
        FOREIGN KEY(child_subject_id,child_authorization_kind,child_authorization_revision)
            REFERENCES work_origin_authorizations(subject_id,origin_kind,revision),
        FOREIGN KEY(receiver_subject_id,receiver_authorization_kind,receiver_authorization_revision)
            REFERENCES work_origin_authorizations(subject_id,origin_kind,revision)
    )""",
    """CREATE INDEX work_child_origin_bindings_parent
        ON work_child_origin_bindings(parent_attempt_id,parent_generation)""",
    """CREATE TRIGGER work_child_origin_bindings_immutable_update
        BEFORE UPDATE ON work_child_origin_bindings
        BEGIN SELECT RAISE(ABORT,'work child origin bindings are immutable'); END""",
    """CREATE TRIGGER work_child_origin_bindings_immutable_delete
        BEFORE DELETE ON work_child_origin_bindings
        BEGIN SELECT RAISE(ABORT,'work child origin bindings cannot be deleted'); END""",
    """CREATE TABLE work_lineage_projection_intents (
        child_attempt_id TEXT NOT NULL,
        child_generation INTEGER NOT NULL CHECK(child_generation>0),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        state TEXT NOT NULL CHECK(state='planned'),
        created_at REAL NOT NULL,
        PRIMARY KEY(child_attempt_id,child_generation),
        FOREIGN KEY(child_attempt_id,child_generation)
            REFERENCES work_child_origin_bindings(child_attempt_id,child_generation)
    )""",
    """CREATE TRIGGER work_lineage_projection_intents_immutable_update
        BEFORE UPDATE ON work_lineage_projection_intents
        BEGIN SELECT RAISE(ABORT,'lineage projection intent is immutable'); END""",
    """CREATE TRIGGER work_lineage_projection_intents_immutable_delete
        BEFORE DELETE ON work_lineage_projection_intents
        BEGIN SELECT RAISE(ABORT,'lineage projection intent cannot be deleted'); END""",
)


# v23 certifies only newly admitted managed lineage.  The v22 binding remains
# immutable history, but cannot be made authoritative retroactively because its
# pre-prepare request representation is not reconstructible from stored rows.
LINEAGE_INTEGRITY_SCHEMA = (
    """CREATE TABLE work_lineage_integrity (
        child_attempt_id TEXT NOT NULL,
        child_generation INTEGER NOT NULL CHECK(child_generation>0),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        canonicalization_version INTEGER NOT NULL CHECK(canonicalization_version=1),
        fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64
            AND fingerprint NOT GLOB '*[^0-9a-f]*'),
        PRIMARY KEY(child_attempt_id,child_generation),
        FOREIGN KEY(child_attempt_id,child_generation)
            REFERENCES work_child_origin_bindings(child_attempt_id,child_generation)
    )""",
    """CREATE TRIGGER work_lineage_integrity_immutable_update
        BEFORE UPDATE ON work_lineage_integrity
        BEGIN SELECT RAISE(ABORT,'work lineage integrity is immutable'); END""",
    """CREATE TRIGGER work_lineage_integrity_immutable_delete
        BEFORE DELETE ON work_lineage_integrity
        BEGIN SELECT RAISE(ABORT,'work lineage integrity cannot be deleted'); END""",
)


WORK_TASK_RECEIVER_ACCEPTANCE_SCHEMA = (
    """CREATE TABLE work_task_receiver_acceptances (
        attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
        receiver_subject_id TEXT NOT NULL,
        receiver_origin_kind TEXT NOT NULL DEFAULT 'receiver'
            CHECK(receiver_origin_kind='receiver'),
        receiver_subject_revision INTEGER NOT NULL
            CHECK(typeof(receiver_subject_revision)='integer' AND receiver_subject_revision>0),
        receiver_authorization_revision INTEGER NOT NULL
            CHECK(typeof(receiver_authorization_revision)='integer' AND receiver_authorization_revision>0),
        delivery_id TEXT NOT NULL CHECK(length(delivery_id) BETWEEN 1 AND 512),
        delivery_hash TEXT NOT NULL CHECK(length(delivery_hash)=64
            AND delivery_hash NOT GLOB '*[^0-9a-f]*'),
        acceptance_sha256 TEXT NOT NULL CHECK(length(acceptance_sha256)=64
            AND acceptance_sha256 NOT GLOB '*[^0-9a-f]*'),
        accepted_at REAL NOT NULL CHECK(typeof(accepted_at)='real' AND accepted_at>0),
        schema_version INTEGER NOT NULL DEFAULT 1
            CHECK(typeof(schema_version)='integer' AND schema_version=1),
        PRIMARY KEY(attempt_id,generation),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_child_origin_bindings(child_attempt_id,child_generation),
        FOREIGN KEY(receiver_subject_id,receiver_origin_kind,receiver_subject_revision)
            REFERENCES work_origin_subjects(subject_id,origin_kind,revision),
        FOREIGN KEY(receiver_subject_id,receiver_origin_kind,receiver_authorization_revision)
            REFERENCES work_origin_authorizations(subject_id,origin_kind,revision)
    )""",
    """CREATE TRIGGER work_task_receiver_acceptances_immutable_update
        BEFORE UPDATE ON work_task_receiver_acceptances
        BEGIN SELECT RAISE(ABORT,'receiver acceptance is immutable'); END""",
    """CREATE TRIGGER work_task_receiver_acceptances_immutable_delete
        BEFORE DELETE ON work_task_receiver_acceptances
        BEGIN SELECT RAISE(ABORT,'receiver acceptance cannot be deleted'); END""",
)


# v25 records the first authenticated receiver acknowledgement for a managed
# delivery.  No historic attempt is backfilled: only an exact v22/v23 binding
# can name this immutable receipt, so legacy replay cannot gain an ACK merely
# by migrating the store.
TASK_RECEIVED_RECEIPT_SCHEMA = (
    """CREATE TABLE work_task_received_receipts (
        attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>0),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        work_item_id TEXT NOT NULL REFERENCES work_items(id),
        job_id TEXT NOT NULL REFERENCES work_jobs(id),
        attempt_revision INTEGER NOT NULL CHECK(attempt_revision>0),
        receiver_subject_id TEXT NOT NULL REFERENCES work_principals(id),
        receiver_subject_revision INTEGER NOT NULL CHECK(receiver_subject_revision>0),
        receiver_authorization_kind TEXT NOT NULL
            CHECK(receiver_authorization_kind='receiver'),
        receiver_authorization_revision INTEGER NOT NULL
            CHECK(receiver_authorization_revision>0),
        receiver_grant_id TEXT NOT NULL,
        receiver_grant_revision INTEGER NOT NULL CHECK(receiver_grant_revision>0),
        delivery_id TEXT NOT NULL CHECK(length(delivery_id) BETWEEN 1 AND 128
            AND delivery_id NOT GLOB '*[^A-Za-z0-9._:-]*'),
        delivery_hash TEXT NOT NULL CHECK(length(delivery_hash)=64
            AND delivery_hash NOT GLOB '*[^0-9a-f]*'),
        nonce TEXT NOT NULL CHECK(length(nonce) BETWEEN 1 AND 128
            AND nonce NOT GLOB '*[^A-Za-z0-9._:-]*'),
        receipt_hash TEXT NOT NULL CHECK(length(receipt_hash)=64
            AND receipt_hash NOT GLOB '*[^0-9a-f]*'),
        received_at REAL NOT NULL,
        PRIMARY KEY(attempt_id,generation),
        UNIQUE(nonce),
        UNIQUE(delivery_id),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_child_origin_bindings(child_attempt_id,child_generation),
        FOREIGN KEY(receiver_subject_id,receiver_subject_revision)
            REFERENCES work_origin_subjects(subject_id,revision),
        FOREIGN KEY(receiver_subject_id,receiver_authorization_kind,
                    receiver_authorization_revision)
            REFERENCES work_origin_authorizations(subject_id,origin_kind,revision),
        FOREIGN KEY(job_id,receiver_grant_id,receiver_grant_revision)
            REFERENCES work_grants(job_id,id,revision)
    )""",
    """CREATE TRIGGER work_task_received_receipts_immutable_update
        BEFORE UPDATE ON work_task_received_receipts
        BEGIN SELECT RAISE(ABORT,'task receipt history is immutable'); END""",
    """CREATE TRIGGER work_task_received_receipts_immutable_delete
        BEFORE DELETE ON work_task_received_receipts
        BEGIN SELECT RAISE(ABORT,'task receipt history cannot be deleted'); END""",
)


# v39 adds server-selected managed workflow step provisioning and exact
# per-attempt bindings.  No legacy workflow rows or child-only receiver proofs
# are backfilled into these tables.
WORKFLOW_STEP_SCHEMA = (
    """CREATE TABLE work_workflow_step_provisions (
        principal_id TEXT NOT NULL REFERENCES work_principals(id),
        workflow_id TEXT NOT NULL CHECK(length(workflow_id) BETWEEN 1 AND 128
            AND workflow_id NOT GLOB '*[^A-Za-z0-9._:-]*'),
        step_id TEXT NOT NULL CHECK(length(step_id) BETWEEN 1 AND 128
            AND step_id NOT GLOB '*[^A-Za-z0-9._:-]*'),
        revision INTEGER NOT NULL CHECK(revision>0),
        id TEXT NOT NULL CHECK(length(id) BETWEEN 1 AND 128
            AND id NOT GLOB '*[^A-Za-z0-9._:-]*'),
        provision_fingerprint TEXT NOT NULL CHECK(length(provision_fingerprint)=64
            AND provision_fingerprint NOT GLOB '*[^0-9a-f]*'),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        state TEXT NOT NULL CHECK(state IN ('active','retired')),
        issuer_id TEXT NOT NULL REFERENCES work_principals(id),
        spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64
            AND spec_hash NOT GLOB '*[^0-9a-f]*'),
        workflow_subject_id TEXT NOT NULL,
        workflow_subject_revision INTEGER NOT NULL CHECK(workflow_subject_revision>0),
        workflow_authorization_kind TEXT NOT NULL CHECK(workflow_authorization_kind='workflow'),
        workflow_authorization_revision INTEGER NOT NULL CHECK(workflow_authorization_revision>0),
        job_id TEXT NOT NULL REFERENCES work_jobs(id),
        grant_id TEXT NOT NULL,
        grant_revision INTEGER NOT NULL CHECK(grant_revision>0),
        contract_id TEXT NOT NULL CHECK(length(contract_id) BETWEEN 1 AND 128),
        contract_json TEXT NOT NULL CHECK(json_valid(contract_json)
            AND json_type(contract_json)='object' AND length(contract_json)<=32768),
        contract_hash TEXT NOT NULL CHECK(length(contract_hash)=64
            AND contract_hash NOT GLOB '*[^0-9a-f]*'),
        snapshot_id TEXT NOT NULL REFERENCES work_delegation_snapshots(id),
        snapshot_hash TEXT NOT NULL CHECK(length(snapshot_hash)=64
            AND snapshot_hash NOT GLOB '*[^0-9a-f]*'),
        delivery_json TEXT NOT NULL CHECK(json_valid(delivery_json)
            AND json_type(delivery_json)='object' AND length(delivery_json)<=65536),
        delivery_template_hash TEXT NOT NULL CHECK(length(delivery_template_hash)=64
            AND delivery_template_hash NOT GLOB '*[^0-9a-f]*'),
        adapter_version INTEGER NOT NULL CHECK(adapter_version>0),
        lease_seconds INTEGER NOT NULL CHECK(lease_seconds BETWEEN 1 AND 3600),
        output_schema_json TEXT CHECK(output_schema_json IS NULL OR
            (json_valid(output_schema_json) AND json_type(output_schema_json)='object'
             AND length(output_schema_json)<=32768)),
        output_schema_hash TEXT CHECK(output_schema_hash IS NULL OR
            (length(output_schema_hash)=64 AND output_schema_hash NOT GLOB '*[^0-9a-f]*')),
        receiver_subject_id TEXT NOT NULL,
        receiver_subject_revision INTEGER NOT NULL CHECK(receiver_subject_revision>0),
        receiver_authorization_kind TEXT NOT NULL CHECK(receiver_authorization_kind='receiver'),
        receiver_authorization_revision INTEGER NOT NULL CHECK(receiver_authorization_revision>0),
        receiver_grant_id TEXT NOT NULL,
        receiver_grant_revision INTEGER NOT NULL CHECK(receiver_grant_revision>0),
        receiver_received_action TEXT NOT NULL CHECK(receiver_received_action='task_received'),
        receiver_result_action TEXT NOT NULL CHECK(receiver_result_action='task_result'),
        created_at REAL NOT NULL CHECK(created_at>0),
        PRIMARY KEY(principal_id,workflow_id,step_id,revision),
        UNIQUE(principal_id,workflow_id,step_id,revision,id),
        CHECK((output_schema_json IS NULL)=(output_schema_hash IS NULL)),
        FOREIGN KEY(workflow_subject_id,workflow_subject_revision)
            REFERENCES work_origin_subjects(subject_id,revision),
        FOREIGN KEY(workflow_subject_id,workflow_authorization_kind,workflow_authorization_revision)
            REFERENCES work_origin_authorizations(subject_id,origin_kind,revision),
        FOREIGN KEY(job_id,grant_id,grant_revision)
            REFERENCES work_grants(job_id,id,revision),
        FOREIGN KEY(receiver_subject_id,receiver_subject_revision)
            REFERENCES work_origin_subjects(subject_id,revision),
        FOREIGN KEY(receiver_subject_id,receiver_authorization_kind,receiver_authorization_revision)
            REFERENCES work_origin_authorizations(subject_id,origin_kind,revision),
        FOREIGN KEY(job_id,receiver_grant_id,receiver_grant_revision)
            REFERENCES work_grants(job_id,id,revision)
    )""",
    "CREATE INDEX work_workflow_step_provisions_current_lookup ON "
    "work_workflow_step_provisions(principal_id,workflow_id,step_id,revision DESC)",
    "CREATE UNIQUE INDEX work_workflow_step_provisions_source_unique ON "
    "work_workflow_step_provisions(principal_id,workflow_id,step_id,spec_hash)",
    """CREATE TRIGGER work_workflow_step_provisions_immutable_update
        BEFORE UPDATE ON work_workflow_step_provisions
        BEGIN SELECT RAISE(ABORT,'workflow step provisions are immutable'); END""",
    """CREATE TRIGGER work_workflow_step_provisions_immutable_delete
        BEFORE DELETE ON work_workflow_step_provisions
        BEGIN SELECT RAISE(ABORT,'workflow step provisions cannot be deleted'); END""",
    """CREATE TABLE work_workflow_run_capabilities (
        run_id TEXT NOT NULL REFERENCES workflow_run(run_id),
        revision INTEGER NOT NULL CHECK(revision>0),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        principal_id TEXT NOT NULL REFERENCES work_principals(id),
        workflow_id TEXT NOT NULL CHECK(length(workflow_id) BETWEEN 1 AND 128
            AND workflow_id NOT GLOB '*[^A-Za-z0-9._:-]*'),
        tier TEXT NOT NULL CHECK(tier IN ('script','yaml')),
        run_generation INTEGER NOT NULL CHECK(run_generation>0),
        spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64
            AND spec_hash NOT GLOB '*[^0-9a-f]*'),
        credential_sha256 TEXT NOT NULL CHECK(length(credential_sha256)=64
            AND credential_sha256 NOT GLOB '*[^0-9a-f]*'),
        state TEXT NOT NULL CHECK(state IN ('active','revoked')),
        issued_at REAL NOT NULL CHECK(issued_at>0),
        expires_at REAL NOT NULL CHECK(expires_at>issued_at),
        PRIMARY KEY(run_id,revision),
        UNIQUE(credential_sha256)
    )""",
    "CREATE INDEX work_workflow_run_capabilities_current_lookup ON "
    "work_workflow_run_capabilities(run_id,revision DESC)",
    """CREATE TRIGGER work_workflow_run_capabilities_immutable_update
        BEFORE UPDATE ON work_workflow_run_capabilities
        BEGIN SELECT RAISE(ABORT,'workflow run capabilities are immutable'); END""",
    """CREATE TRIGGER work_workflow_run_capabilities_immutable_delete
        BEFORE DELETE ON work_workflow_run_capabilities
        BEGIN SELECT RAISE(ABORT,'workflow run capabilities cannot be deleted'); END""",
    """CREATE TABLE work_workflow_step_bindings (
        binding_id TEXT PRIMARY KEY CHECK(length(binding_id) BETWEEN 1 AND 128
            AND binding_id NOT GLOB '*[^A-Za-z0-9._:-]*'),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        tier TEXT NOT NULL CHECK(tier IN ('yaml','script')),
        run_id TEXT NOT NULL REFERENCES workflow_run(run_id),
        run_generation INTEGER NOT NULL CHECK(run_generation>0),
        binding_fingerprint TEXT NOT NULL CHECK(length(binding_fingerprint)=64
            AND binding_fingerprint NOT GLOB '*[^0-9a-f]*'),
        workflow_id TEXT NOT NULL CHECK(length(workflow_id) BETWEEN 1 AND 128
            AND workflow_id NOT GLOB '*[^A-Za-z0-9._:-]*'),
        spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64
            AND spec_hash NOT GLOB '*[^0-9a-f]*'),
        step_id TEXT NOT NULL CHECK(length(step_id) BETWEEN 1 AND 128
            AND step_id NOT GLOB '*[^A-Za-z0-9._:-]*'),
        workflow_step_attempt INTEGER NOT NULL CHECK(workflow_step_attempt>0),
        principal_id TEXT NOT NULL REFERENCES work_principals(id),
        provision_id TEXT NOT NULL,
        provision_revision INTEGER NOT NULL CHECK(provision_revision>0),
        provision_fingerprint TEXT NOT NULL CHECK(length(provision_fingerprint)=64
            AND provision_fingerprint NOT GLOB '*[^0-9a-f]*'),
        workflow_subject_id TEXT NOT NULL,
        workflow_subject_revision INTEGER NOT NULL CHECK(workflow_subject_revision>0),
        workflow_authorization_kind TEXT NOT NULL CHECK(workflow_authorization_kind='workflow'),
        workflow_authorization_revision INTEGER NOT NULL CHECK(workflow_authorization_revision>0),
        job_id TEXT NOT NULL REFERENCES work_jobs(id),
        grant_id TEXT NOT NULL,
        grant_revision INTEGER NOT NULL CHECK(grant_revision>0),
        contract_id TEXT NOT NULL,
        contract_hash TEXT NOT NULL CHECK(length(contract_hash)=64
            AND contract_hash NOT GLOB '*[^0-9a-f]*'),
        snapshot_id TEXT NOT NULL REFERENCES work_delegation_snapshots(id),
        snapshot_hash TEXT NOT NULL CHECK(length(snapshot_hash)=64
            AND snapshot_hash NOT GLOB '*[^0-9a-f]*'),
        delivery_id TEXT NOT NULL CHECK(length(delivery_id) BETWEEN 1 AND 128
            AND delivery_id NOT GLOB '*[^A-Za-z0-9._:-]*'),
        delivery_hash TEXT NOT NULL CHECK(length(delivery_hash)=64
            AND delivery_hash NOT GLOB '*[^0-9a-f]*'),
        work_item_id TEXT NOT NULL REFERENCES work_items(id),
        work_attempt_id TEXT NOT NULL,
        work_generation INTEGER NOT NULL CHECK(work_generation>0),
        request_hash TEXT NOT NULL CHECK(length(request_hash)=64
            AND request_hash NOT GLOB '*[^0-9a-f]*'),
        idempotency_key TEXT NOT NULL,
        output_schema_json TEXT CHECK(output_schema_json IS NULL OR
            (json_valid(output_schema_json) AND json_type(output_schema_json)='object'
             AND length(output_schema_json)<=32768)),
        output_schema_hash TEXT CHECK(output_schema_hash IS NULL OR
            (length(output_schema_hash)=64 AND output_schema_hash NOT GLOB '*[^0-9a-f]*')),
        receiver_subject_id TEXT NOT NULL,
        receiver_subject_revision INTEGER NOT NULL CHECK(receiver_subject_revision>0),
        receiver_authorization_kind TEXT NOT NULL CHECK(receiver_authorization_kind='receiver'),
        receiver_authorization_revision INTEGER NOT NULL CHECK(receiver_authorization_revision>0),
        receiver_grant_id TEXT NOT NULL,
        receiver_grant_revision INTEGER NOT NULL CHECK(receiver_grant_revision>0),
        receiver_received_action TEXT NOT NULL CHECK(receiver_received_action='task_received'),
        receiver_result_action TEXT NOT NULL CHECK(receiver_result_action='task_result'),
        created_at REAL NOT NULL CHECK(created_at>0),
        UNIQUE(tier,run_id,run_generation,step_id,workflow_step_attempt),
        UNIQUE(work_attempt_id,work_generation),
        UNIQUE(binding_id,work_attempt_id,work_generation,work_item_id),
        UNIQUE(binding_id,work_attempt_id,work_generation),
        UNIQUE(principal_id,workflow_id,step_id,idempotency_key),
        CHECK((output_schema_json IS NULL)=(output_schema_hash IS NULL)),
        FOREIGN KEY(principal_id,workflow_id,step_id,provision_revision,provision_id)
            REFERENCES work_workflow_step_provisions(principal_id,workflow_id,step_id,revision,id),
        FOREIGN KEY(work_attempt_id,work_generation)
            REFERENCES work_dispatch_bindings(attempt_id,generation),
        FOREIGN KEY(work_item_id,work_attempt_id,work_generation)
            REFERENCES work_attempts(work_item_id,id,generation),
        FOREIGN KEY(workflow_subject_id,workflow_subject_revision)
            REFERENCES work_origin_subjects(subject_id,revision),
        FOREIGN KEY(workflow_subject_id,workflow_authorization_kind,workflow_authorization_revision)
            REFERENCES work_origin_authorizations(subject_id,origin_kind,revision),
        FOREIGN KEY(job_id,grant_id,grant_revision)
            REFERENCES work_grants(job_id,id,revision),
        FOREIGN KEY(receiver_subject_id,receiver_subject_revision)
            REFERENCES work_origin_subjects(subject_id,revision),
        FOREIGN KEY(receiver_subject_id,receiver_authorization_kind,receiver_authorization_revision)
            REFERENCES work_origin_authorizations(subject_id,origin_kind,revision),
        FOREIGN KEY(job_id,receiver_grant_id,receiver_grant_revision)
            REFERENCES work_grants(job_id,id,revision)
    )""",
    "CREATE INDEX work_workflow_step_bindings_work ON "
    "work_workflow_step_bindings(work_item_id,work_attempt_id,work_generation)",
    """CREATE TRIGGER work_workflow_step_bindings_immutable_update
        BEFORE UPDATE ON work_workflow_step_bindings
        BEGIN SELECT RAISE(ABORT,'workflow step bindings are immutable'); END""",
    """CREATE TRIGGER work_workflow_step_bindings_immutable_delete
        BEFORE DELETE ON work_workflow_step_bindings
        BEGIN SELECT RAISE(ABORT,'workflow step bindings cannot be deleted'); END""",
    """CREATE TABLE work_workflow_step_projections (
        binding_id TEXT PRIMARY KEY REFERENCES work_workflow_step_bindings(binding_id),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        accepted_result_id TEXT NOT NULL REFERENCES work_results(id),
        content_hash TEXT NOT NULL CHECK(length(content_hash)=64
            AND content_hash NOT GLOB '*[^0-9a-f]*'),
        projected_at REAL NOT NULL CHECK(projected_at>0)
    )""",
    """CREATE TRIGGER work_workflow_step_projections_immutable_update
        BEFORE UPDATE ON work_workflow_step_projections
        BEGIN SELECT RAISE(ABORT,'workflow step projections are immutable'); END""",
    """CREATE TRIGGER work_workflow_step_projections_immutable_delete
        BEFORE DELETE ON work_workflow_step_projections
        BEGIN SELECT RAISE(ABORT,'workflow step projection history cannot be deleted'); END""",
    """CREATE TABLE work_workflow_step_receiver_credentials (
        binding_id TEXT NOT NULL REFERENCES work_workflow_step_bindings(binding_id),
        attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>0),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        job_id TEXT NOT NULL,
        work_item_id TEXT NOT NULL,
        receiver_subject_id TEXT NOT NULL REFERENCES work_principals(id),
        receiver_subject_revision INTEGER NOT NULL CHECK(receiver_subject_revision>0),
        receiver_authorization_revision INTEGER NOT NULL CHECK(receiver_authorization_revision>0),
        receiver_grant_id TEXT NOT NULL,
        receiver_grant_revision INTEGER NOT NULL CHECK(receiver_grant_revision>0),
        installation_uuid TEXT NOT NULL CHECK(length(installation_uuid)=32
            AND installation_uuid NOT GLOB '*[^0-9a-f]*'),
        attempt_revision INTEGER NOT NULL CHECK(attempt_revision>0),
        lease_expires_at REAL NOT NULL CHECK(lease_expires_at>0),
        expires_at REAL NOT NULL CHECK(expires_at>0 AND expires_at<=lease_expires_at),
        delivery_id TEXT NOT NULL,
        delivery_hash TEXT NOT NULL CHECK(length(delivery_hash)=64
            AND delivery_hash NOT GLOB '*[^0-9a-f]*'),
        credential_sha256 TEXT NOT NULL UNIQUE CHECK(length(credential_sha256)=64
            AND credential_sha256 NOT GLOB '*[^0-9a-f]*'),
        issued_at REAL NOT NULL CHECK(issued_at>0),
        PRIMARY KEY(attempt_id,generation),
        FOREIGN KEY(job_id,work_item_id) REFERENCES work_items(job_id,id),
        FOREIGN KEY(work_item_id,attempt_id,generation)
            REFERENCES work_attempts(work_item_id,id,generation),
        FOREIGN KEY(job_id,receiver_grant_id,receiver_grant_revision)
            REFERENCES work_grants(job_id,id,revision),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_attempt_credentials(attempt_id,generation),
        FOREIGN KEY(binding_id,attempt_id,generation,work_item_id)
            REFERENCES work_workflow_step_bindings(binding_id,work_attempt_id,work_generation,work_item_id)
    )""",
    """CREATE TRIGGER work_workflow_step_receiver_credentials_immutable_update
        BEFORE UPDATE ON work_workflow_step_receiver_credentials
        BEGIN SELECT RAISE(ABORT,'workflow receiver credentials are immutable'); END""",
    """CREATE TRIGGER work_workflow_step_receiver_credentials_immutable_delete
        BEFORE DELETE ON work_workflow_step_receiver_credentials
        BEGIN SELECT RAISE(ABORT,'workflow receiver credentials cannot be deleted'); END""",
    """CREATE TABLE work_workflow_step_receiver_acceptances (
        binding_id TEXT NOT NULL REFERENCES work_workflow_step_bindings(binding_id),
        attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>0),
        receiver_subject_id TEXT NOT NULL,
        receiver_subject_revision INTEGER NOT NULL CHECK(receiver_subject_revision>0),
        receiver_authorization_kind TEXT NOT NULL CHECK(receiver_authorization_kind='receiver'),
        receiver_authorization_revision INTEGER NOT NULL CHECK(receiver_authorization_revision>0),
        delivery_id TEXT NOT NULL,
        delivery_hash TEXT NOT NULL CHECK(length(delivery_hash)=64
            AND delivery_hash NOT GLOB '*[^0-9a-f]*'),
        acceptance_sha256 TEXT NOT NULL CHECK(length(acceptance_sha256)=64
            AND acceptance_sha256 NOT GLOB '*[^0-9a-f]*'),
        accepted_at REAL NOT NULL CHECK(accepted_at>0),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        PRIMARY KEY(attempt_id,generation),
        FOREIGN KEY(binding_id,attempt_id,generation)
            REFERENCES work_workflow_step_bindings(binding_id,work_attempt_id,work_generation),
        FOREIGN KEY(receiver_subject_id,receiver_subject_revision)
            REFERENCES work_origin_subjects(subject_id,revision),
        FOREIGN KEY(receiver_subject_id,receiver_authorization_kind,receiver_authorization_revision)
            REFERENCES work_origin_authorizations(subject_id,origin_kind,revision)
    )""",
    """CREATE TRIGGER work_workflow_step_receiver_acceptances_immutable_update
        BEFORE UPDATE ON work_workflow_step_receiver_acceptances
        BEGIN SELECT RAISE(ABORT,'workflow receiver acceptance is immutable'); END""",
    """CREATE TRIGGER work_workflow_step_receiver_acceptances_immutable_delete
        BEFORE DELETE ON work_workflow_step_receiver_acceptances
        BEGIN SELECT RAISE(ABORT,'workflow receiver acceptance cannot be deleted'); END""",
    """CREATE TABLE work_workflow_step_task_received_receipts (
        binding_id TEXT NOT NULL REFERENCES work_workflow_step_bindings(binding_id),
        attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>0),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        work_item_id TEXT NOT NULL REFERENCES work_items(id),
        job_id TEXT NOT NULL REFERENCES work_jobs(id),
        attempt_revision INTEGER NOT NULL CHECK(attempt_revision>0),
        receiver_subject_id TEXT NOT NULL REFERENCES work_principals(id),
        receiver_subject_revision INTEGER NOT NULL CHECK(receiver_subject_revision>0),
        receiver_authorization_kind TEXT NOT NULL CHECK(receiver_authorization_kind='receiver'),
        receiver_authorization_revision INTEGER NOT NULL CHECK(receiver_authorization_revision>0),
        receiver_grant_id TEXT NOT NULL,
        receiver_grant_revision INTEGER NOT NULL CHECK(receiver_grant_revision>0),
        delivery_id TEXT NOT NULL CHECK(length(delivery_id) BETWEEN 1 AND 128
            AND delivery_id NOT GLOB '*[^A-Za-z0-9._:-]*'),
        delivery_hash TEXT NOT NULL CHECK(length(delivery_hash)=64
            AND delivery_hash NOT GLOB '*[^0-9a-f]*'),
        nonce TEXT NOT NULL CHECK(length(nonce) BETWEEN 1 AND 128
            AND nonce NOT GLOB '*[^A-Za-z0-9._:-]*'),
        receipt_hash TEXT NOT NULL CHECK(length(receipt_hash)=64
            AND receipt_hash NOT GLOB '*[^0-9a-f]*'),
        received_at REAL NOT NULL CHECK(received_at>0),
        PRIMARY KEY(attempt_id,generation),
        UNIQUE(nonce),
        UNIQUE(delivery_id),
        FOREIGN KEY(binding_id,attempt_id,generation,work_item_id)
            REFERENCES work_workflow_step_bindings(binding_id,work_attempt_id,work_generation,work_item_id),
        FOREIGN KEY(receiver_subject_id,receiver_subject_revision)
            REFERENCES work_origin_subjects(subject_id,revision),
        FOREIGN KEY(receiver_subject_id,receiver_authorization_kind,receiver_authorization_revision)
            REFERENCES work_origin_authorizations(subject_id,origin_kind,revision),
        FOREIGN KEY(job_id,receiver_grant_id,receiver_grant_revision)
            REFERENCES work_grants(job_id,id,revision)
    )""",
    """CREATE TRIGGER work_workflow_step_task_received_receipts_immutable_update
        BEFORE UPDATE ON work_workflow_step_task_received_receipts
        BEGIN SELECT RAISE(ABORT,'workflow task receipt is immutable'); END""",
    """CREATE TRIGGER work_workflow_step_task_received_receipts_immutable_delete
        BEFORE DELETE ON work_workflow_step_task_received_receipts
        BEGIN SELECT RAISE(ABORT,'workflow task receipt history cannot be deleted'); END""",
    """CREATE TABLE work_workflow_step_retry_authorizations (
        authorization_id TEXT PRIMARY KEY CHECK(length(authorization_id) BETWEEN 1 AND 128
            AND authorization_id NOT GLOB '*[^A-Za-z0-9._:-]*'),
        authorization_fingerprint TEXT NOT NULL CHECK(length(authorization_fingerprint)=64
            AND authorization_fingerprint NOT GLOB '*[^0-9a-f]*'),
        binding_id TEXT NOT NULL UNIQUE,
        binding_fingerprint TEXT NOT NULL CHECK(length(binding_fingerprint)=64
            AND binding_fingerprint NOT GLOB '*[^0-9a-f]*'),
        tier TEXT NOT NULL CHECK(tier IN ('yaml','script')),
        run_id TEXT NOT NULL,
        run_generation INTEGER NOT NULL CHECK(run_generation>0),
        workflow_id TEXT NOT NULL,
        spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64
            AND spec_hash NOT GLOB '*[^0-9a-f]*'),
        step_id TEXT NOT NULL,
        workflow_step_attempt INTEGER NOT NULL CHECK(workflow_step_attempt>0),
        principal_id TEXT NOT NULL REFERENCES work_principals(id),
        provision_id TEXT NOT NULL,
        provision_revision INTEGER NOT NULL CHECK(provision_revision>0),
        provision_fingerprint TEXT NOT NULL CHECK(length(provision_fingerprint)=64
            AND provision_fingerprint NOT GLOB '*[^0-9a-f]*'),
        work_item_id TEXT NOT NULL REFERENCES work_items(id),
        work_attempt_id TEXT NOT NULL,
        work_generation INTEGER NOT NULL CHECK(work_generation>0),
        authorized_at REAL NOT NULL CHECK(authorized_at>0),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        FOREIGN KEY(binding_id,work_attempt_id,work_generation,work_item_id)
            REFERENCES work_workflow_step_bindings(binding_id,work_attempt_id,work_generation,work_item_id),
        FOREIGN KEY(principal_id,workflow_id,step_id,provision_revision,provision_id)
            REFERENCES work_workflow_step_provisions(principal_id,workflow_id,step_id,revision,id),
        UNIQUE(work_attempt_id,work_generation)
    )""",
    """CREATE TRIGGER work_workflow_step_retry_authorizations_immutable_update
        BEFORE UPDATE ON work_workflow_step_retry_authorizations
        BEGIN SELECT RAISE(ABORT,'workflow retry authorizations are immutable'); END""",
    """CREATE TRIGGER work_workflow_step_retry_authorizations_immutable_delete
        BEFORE DELETE ON work_workflow_step_retry_authorizations
        BEGIN SELECT RAISE(ABORT,'workflow retry authorizations cannot be deleted'); END""",
)
