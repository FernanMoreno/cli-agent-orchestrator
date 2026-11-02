"""Bounded intent journal, independent of the frozen resource-admission history."""

from cli_agent_orchestrator.clients.postgres.migrations import ComponentMigration

SQL_OPENING_QUOTAS = "_cao_sql_opening_quotas"
SQL_OPENING_CHANNELS = "_cao_sql_opening_channels"
SQL_OPENING_INTENTS = "_cao_sql_opening_intents"
SQL_OPENING_FENCES = "_cao_sql_opening_fences"
SQL_OPENING_TABLES = (
    SQL_OPENING_QUOTAS,
    SQL_OPENING_CHANNELS,
    SQL_OPENING_INTENTS,
    SQL_OPENING_FENCES,
)

SQL_OPENING_MIGRATIONS: tuple[ComponentMigration, ...] = (
    ComponentMigration(
        component="sql_opening_intents",
        scope="main",
        version=1,
        statements=(
            """CREATE TABLE _cao_sql_opening_quotas (
                journal_id UUID PRIMARY KEY, credit_id UUID NOT NULL UNIQUE,
                instance_id UUID NOT NULL, process_generation UUID NOT NULL,
                binding_sha256 TEXT NOT NULL CHECK(binding_sha256 ~ '^[0-9a-f]{64}$'),
                max_records BIGINT NOT NULL CHECK(max_records>=0),
                actor_principal_id TEXT NOT NULL CHECK(actor_principal_id ~ '^principal-[0-9a-f]{64}$'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(instance_id,process_generation)
            )""",
            """CREATE TABLE _cao_sql_opening_channels (
                journal_id UUID PRIMARY KEY REFERENCES _cao_sql_opening_quotas(journal_id),
                binding_sha256 TEXT NOT NULL CHECK(binding_sha256 ~ '^[0-9a-f]{64}$'),
                backend_pid BIGINT NOT NULL CHECK(backend_pid>0),
                backend_started_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE _cao_sql_opening_intents (
                intent_id UUID PRIMARY KEY,
                journal_id UUID NOT NULL REFERENCES _cao_sql_opening_quotas(journal_id),
                ordinal BIGINT NOT NULL CHECK(ordinal>0),
                binding_sha256 TEXT NOT NULL CHECK(binding_sha256 ~ '^[0-9a-f]{64}$'),
                process_pid BIGINT NOT NULL CHECK(process_pid>0),
                process_started_at DOUBLE PRECISION NOT NULL CHECK(
                    process_started_at>0 AND process_started_at<'Infinity'::double precision),
                sql_application_name TEXT NOT NULL CHECK(
                    sql_application_name ~ '^caos-sql-v1-[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$'),
                path TEXT NOT NULL CHECK(path IN ('native','orm','audit')),
                destination_store_id UUID NOT NULL,
                destination_scope TEXT NOT NULL CHECK(length(destination_scope) BETWEEN 1 AND 128),
                destination_sha256 TEXT NOT NULL CHECK(destination_sha256 ~ '^[0-9a-f]{64}$'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(journal_id,ordinal)
            )""",
            """CREATE TABLE _cao_sql_opening_fences (
                journal_id UUID PRIMARY KEY REFERENCES _cao_sql_opening_quotas(journal_id),
                binding_sha256 TEXT NOT NULL CHECK(binding_sha256 ~ '^[0-9a-f]{64}$'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
        ),
        tables=SQL_OPENING_TABLES,
    ),
)

# Keep the original four-table tuple above frozen: it is part of version1's
# checksum. Version2 adds a distinct planned child opening, never a fake local
# driver call in the original intents table.
SQL_CHILD_OPENINGS = "_cao_sql_child_openings"
SQL_OPENING_ALL_TABLES: tuple[str, ...] = SQL_OPENING_TABLES + (SQL_CHILD_OPENINGS,)
SQL_OPENING_MIGRATIONS += (
    ComponentMigration(
        component="sql_opening_intents",
        scope="main",
        version=2,
        statements=(
            """CREATE TABLE _cao_sql_child_openings (
                intent_id UUID PRIMARY KEY,
                journal_id UUID NOT NULL REFERENCES _cao_sql_opening_quotas(journal_id),
                ordinal BIGINT NOT NULL CHECK(ordinal>0),
                parent_binding_sha256 TEXT NOT NULL CHECK(parent_binding_sha256 ~ '^[0-9a-f]{64}$'),
                parent_process_pid BIGINT NOT NULL CHECK(parent_process_pid>0),
                parent_process_started_at DOUBLE PRECISION NOT NULL CHECK(
                    parent_process_started_at>0 AND parent_process_started_at<'Infinity'::double precision),
                parent_sql_application_name TEXT NOT NULL CHECK(
                    parent_sql_application_name ~ '^caos-sql-v1-[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$'),
                subject_binding JSONB NOT NULL CHECK(jsonb_typeof(subject_binding)='object'),
                subject_binding_sha256 TEXT NOT NULL UNIQUE CHECK(subject_binding_sha256 ~ '^[0-9a-f]{64}$'),
                subject_instance_id UUID NOT NULL,
                subject_process_generation UUID NOT NULL,
                subject_process_pid BIGINT NOT NULL CHECK(subject_process_pid>0 AND subject_process_pid<>parent_process_pid),
                subject_process_started_at DOUBLE PRECISION NOT NULL CHECK(
                    subject_process_started_at>0 AND subject_process_started_at<'Infinity'::double precision),
                subject_sql_application_name TEXT NOT NULL UNIQUE CHECK(
                    subject_sql_application_name ~ '^caos-sql-v1-[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$'
                    AND subject_sql_application_name<>parent_sql_application_name),
                boot_id UUID NOT NULL,
                start_time_ticks BIGINT NOT NULL CHECK(start_time_ticks>0),
                pid_namespace_device BIGINT NOT NULL CHECK(pid_namespace_device>=0),
                pid_namespace_inode BIGINT NOT NULL CHECK(pid_namespace_inode>=0),
                path TEXT NOT NULL CHECK(path IN ('native','orm','audit')),
                destination_store_id UUID NOT NULL,
                destination_scope TEXT NOT NULL CHECK(length(destination_scope) BETWEEN 1 AND 128),
                destination_sha256 TEXT NOT NULL CHECK(destination_sha256 ~ '^[0-9a-f]{64}$'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(journal_id,ordinal),
                UNIQUE(subject_instance_id,subject_process_generation),
                UNIQUE(boot_id,pid_namespace_device,pid_namespace_inode,subject_process_pid,start_time_ticks)
            )""",
        ),
        tables=SQL_OPENING_ALL_TABLES,
    ),
)

# Prior migration objects retain their original table tuples and checksums.
SQL_CHILD_FOLLOWUPS = "_cao_sql_child_followups"
SQL_OPENING_ALL_TABLES += (SQL_CHILD_FOLLOWUPS,)
SQL_OPENING_MIGRATIONS += (
    ComponentMigration(
        component="sql_opening_intents",
        scope="main",
        version=3,
        statements=(
            """ALTER TABLE _cao_sql_child_openings
                ADD CONSTRAINT _cao_sql_child_openings_anchor_key UNIQUE(intent_id,journal_id)""",
            """CREATE TABLE _cao_sql_child_followups (
                intent_id UUID PRIMARY KEY,
                journal_id UUID NOT NULL REFERENCES _cao_sql_opening_quotas(journal_id),
                startup_intent_id UUID NOT NULL,
                ordinal BIGINT NOT NULL CHECK(ordinal>0),
                sequence BIGINT NOT NULL CHECK(sequence>0),
                path TEXT NOT NULL CHECK(path IN ('native','orm','audit')),
                destination_store_id UUID NOT NULL,
                destination_scope TEXT NOT NULL CHECK(length(destination_scope) BETWEEN 1 AND 128),
                destination_sha256 TEXT NOT NULL CHECK(destination_sha256 ~ '^[0-9a-f]{64}$'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(startup_intent_id,journal_id)
                    REFERENCES _cao_sql_child_openings(intent_id,journal_id),
                UNIQUE(journal_id,ordinal),
                UNIQUE(startup_intent_id,sequence)
            )""",
        ),
        tables=SQL_OPENING_ALL_TABLES,
    ),
)


SQL_CHILD_FUNDINGS = "_cao_sql_child_session_fundings"
SQL_OPENING_ALL_TABLES += (SQL_CHILD_FUNDINGS,)
SQL_OPENING_MIGRATIONS += (
    ComponentMigration(
        component="sql_opening_intents",
        scope="main",
        version=4,
        statements=(
            """CREATE TABLE _cao_sql_child_session_fundings (
            intent_id UUID PRIMARY KEY, session_id UUID NOT NULL UNIQUE,
            startup_intent_id UUID REFERENCES _cao_sql_child_openings(intent_id),
            followup_intent_id UUID REFERENCES _cao_sql_child_followups(intent_id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK(pg_catalog.num_nonnulls(startup_intent_id,followup_intent_id)=1),
            CHECK(intent_id=COALESCE(startup_intent_id,followup_intent_id))
        )""",
        ),
        tables=SQL_OPENING_ALL_TABLES,
    ),
)
