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

SQL_OPENING_MIGRATIONS = (
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
