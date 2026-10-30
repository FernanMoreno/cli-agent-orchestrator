"""Operator attestation history; this schema does not grant resource admission."""

from cli_agent_orchestrator.clients.postgres.migrations import ComponentMigration

RESOURCE_ENVELOPE_TABLE = "_cao_resource_envelopes"

RESOURCE_ENVELOPE_MIGRATIONS = (
    ComponentMigration(
        component="resource_envelopes",
        scope="main",
        version=1,
        statements=(
            """CREATE TABLE _cao_resource_envelopes (
                instance_id UUID NOT NULL,
                revision BIGINT NOT NULL CHECK (revision > 0),
                protocol_version INTEGER NOT NULL CHECK (protocol_version = 1),
                store_id UUID NOT NULL,
                target_identity_sha256 TEXT NOT NULL CHECK (
                    length(target_identity_sha256)=64
                    AND target_identity_sha256 !~ '[^0-9a-f]'),
                process_generation UUID NOT NULL,
                process_pid BIGINT NOT NULL CHECK (process_pid > 0),
                process_started_at DOUBLE PRECISION NOT NULL CHECK (
                    process_started_at > 0 AND process_started_at < 'Infinity'::double precision),
                mount_ns_device BIGINT NOT NULL CHECK (mount_ns_device >= 0),
                mount_ns_inode BIGINT NOT NULL CHECK (mount_ns_inode > 0),
                cgroup_ns_device BIGINT NOT NULL CHECK (cgroup_ns_device >= 0),
                cgroup_ns_inode BIGINT NOT NULL CHECK (cgroup_ns_inode > 0),
                resource_view_sha256 TEXT NOT NULL CHECK (
                    length(resource_view_sha256)=64
                    AND resource_view_sha256 !~ '[^0-9a-f]'),
                memory_budget_bytes BIGINT NOT NULL CHECK (memory_budget_bytes > 0),
                cpu_budget_millicores BIGINT NOT NULL CHECK (cpu_budget_millicores > 0),
                storage_budget_slots BIGINT NOT NULL CHECK (storage_budget_slots > 0),
                complete_hierarchy BOOLEAN NOT NULL,
                root_is_host BOOLEAN NOT NULL,
                active BOOLEAN NOT NULL,
                actor_principal_id TEXT NOT NULL CHECK (
                    actor_principal_id ~ '^principal-[0-9a-f]{64}$'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (instance_id,revision),
                CHECK (NOT root_is_host OR complete_hierarchy)
            )""",
        ),
        tables=(RESOURCE_ENVELOPE_TABLE,),
    ),
)
