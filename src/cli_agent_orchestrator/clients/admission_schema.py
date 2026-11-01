"""Additive shared ledger. Authority is provisioned separately from runtime state."""

from cli_agent_orchestrator.clients.postgres.migrations import ComponentMigration

AUTHORITY_TABLES = (
    "_cao_resource_domains",
    "_cao_resource_domain_history",
    "_cao_resource_members",
    "_cao_resource_member_history",
)
LEDGER_TABLES = ("_cao_resource_usage", "_cao_resource_operations", "_cao_resource_allocations")

RESOURCE_ADMISSION_MIGRATIONS = (
    ComponentMigration(
        component="resource_admission",
        scope="main",
        version=1,
        statements=(
            """CREATE TABLE _cao_resource_domains (
                domain_id UUID PRIMARY KEY, incarnation UUID NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('host','node','storage')),
                unit TEXT NOT NULL CHECK (unit IN ('bytes','millicores','postgres_session')),
                capacity BIGINT NOT NULL CHECK (capacity>=0),
                revision BIGINT NOT NULL CHECK (revision>0), active BOOLEAN NOT NULL,
                physical_binding_sha256 TEXT NOT NULL CHECK (
                    physical_binding_sha256 ~ '^[0-9a-f]{64}$'),
                actor_principal_id TEXT NOT NULL CHECK (
                    actor_principal_id ~ '^principal-[0-9a-f]{64}$'),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (physical_binding_sha256,kind,unit),
                CHECK ((kind='storage')=(unit='postgres_session'))
            )""",
            """CREATE TABLE _cao_resource_domain_history (
                domain_id UUID NOT NULL REFERENCES _cao_resource_domains(domain_id),
                revision BIGINT NOT NULL CHECK (revision>0), payload_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (domain_id,revision)
            )""",
            """CREATE TABLE _cao_resource_members (
                domain_id UUID NOT NULL REFERENCES _cao_resource_domains(domain_id),
                instance_id UUID NOT NULL, process_generation UUID NOT NULL,
                binding_sha256 TEXT NOT NULL CHECK (binding_sha256 ~ '^[0-9a-f]{64}$'),
                revision BIGINT NOT NULL CHECK (revision>0), active BOOLEAN NOT NULL,
                actor_principal_id TEXT NOT NULL CHECK (
                    actor_principal_id ~ '^principal-[0-9a-f]{64}$'),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (domain_id,instance_id,process_generation)
            )""",
            """CREATE TABLE _cao_resource_member_history (
                domain_id UUID NOT NULL, instance_id UUID NOT NULL,
                process_generation UUID NOT NULL, revision BIGINT NOT NULL CHECK (revision>0),
                payload_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (domain_id,instance_id,process_generation,revision),
                FOREIGN KEY (domain_id,instance_id,process_generation) REFERENCES
                    _cao_resource_members(domain_id,instance_id,process_generation)
            )""",
            """CREATE TABLE _cao_resource_usage (
                domain_id UUID PRIMARY KEY REFERENCES _cao_resource_domains(domain_id),
                committed BIGINT NOT NULL CHECK (committed>=0)
            )""",
            """CREATE TABLE _cao_resource_operations (
                operation_id UUID PRIMARY KEY, instance_id UUID NOT NULL,
                process_generation UUID NOT NULL, process_pid BIGINT NOT NULL CHECK (process_pid>0),
                process_started_at DOUBLE PRECISION NOT NULL CHECK (
                    process_started_at>0 AND process_started_at<'Infinity'::double precision),
                binding_sha256 TEXT NOT NULL CHECK (binding_sha256 ~ '^[0-9a-f]{64}$'),
                request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
                state TEXT NOT NULL CHECK (state IN (
                    'preparing','active','releasing','uncertain','released')),
                revision BIGINT NOT NULL CHECK (revision>0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE _cao_resource_allocations (
                operation_id UUID NOT NULL REFERENCES _cao_resource_operations(operation_id),
                domain_id UUID NOT NULL REFERENCES _cao_resource_domains(domain_id),
                incarnation UUID NOT NULL, domain_revision BIGINT NOT NULL CHECK (domain_revision>0),
                member_revision BIGINT NOT NULL CHECK (member_revision>0),
                amount BIGINT NOT NULL CHECK (amount>0),
                PRIMARY KEY (operation_id,domain_id)
            )""",
            "CREATE INDEX resource_allocations_domain ON _cao_resource_allocations(domain_id)",
            "CREATE INDEX resource_operations_live ON _cao_resource_operations(operation_id) WHERE state<>'released'",
        ),
        tables=AUTHORITY_TABLES + LEDGER_TABLES,
    ),
    ComponentMigration(
        component="resource_admission",
        scope="main",
        version=2,
        statements=(
            """ALTER TABLE _cao_resource_operations
                ADD COLUMN membership_sha256 TEXT,
                ADD COLUMN cost_sha256 TEXT,
                ADD CONSTRAINT resource_operation_mapping CHECK (
                    (membership_sha256 IS NULL AND cost_sha256 IS NULL) OR
                    (membership_sha256 IS NOT NULL AND cost_sha256 IS NOT NULL
                     AND membership_sha256 ~ '^[0-9a-f]{64}$'
                     AND cost_sha256 ~ '^[0-9a-f]{64}$'))""",
            """CREATE INDEX resource_members_process ON _cao_resource_members
                (instance_id,process_generation,domain_id)""",
        ),
        tables=AUTHORITY_TABLES + LEDGER_TABLES,
    ),
)
