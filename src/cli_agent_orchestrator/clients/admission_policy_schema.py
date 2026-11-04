"""Versioned operator policy history, separate from resource authority."""

from cli_agent_orchestrator.clients.postgres.migrations import ComponentMigration

ADMISSION_POLICY_TABLE = "_cao_admission_policy_history"

ADMISSION_POLICY_MIGRATIONS = (
    ComponentMigration(
        component="admission_policy",
        scope="main",
        version=1,
        statements=(
            f"""CREATE TABLE {ADMISSION_POLICY_TABLE} (
                profile_id UUID NOT NULL,
                revision BIGINT NOT NULL CHECK (revision > 0),
                active BOOLEAN NOT NULL,
                memory_per_delivery_bytes BIGINT NOT NULL CHECK (memory_per_delivery_bytes > 0),
                cpu_per_delivery_millicores BIGINT NOT NULL CHECK (
                    cpu_per_delivery_millicores > 0),
                memory_reserve_percent BIGINT NOT NULL CHECK (
                    memory_reserve_percent >= 0 AND memory_reserve_percent < 100),
                cpu_reserve_percent BIGINT NOT NULL CHECK (
                    cpu_reserve_percent >= 0 AND cpu_reserve_percent < 100),
                max_sample_age_s DOUBLE PRECISION NOT NULL CHECK (
                    max_sample_age_s > 0 AND max_sample_age_s <> 'NaN'::double precision
                    AND max_sample_age_s < 'Infinity'::double precision),
                postgres_reserve_percent BIGINT NOT NULL CHECK (
                    postgres_reserve_percent >= 0 AND postgres_reserve_percent < 100),
                postgres_statement_timeout_ms BIGINT NOT NULL CHECK (
                    postgres_statement_timeout_ms > 0),
                sample_interval_s DOUBLE PRECISION NOT NULL CHECK (
                    sample_interval_s > 0 AND sample_interval_s <> 'NaN'::double precision
                    AND sample_interval_s < 'Infinity'::double precision),
                healthy_samples_before_growth BIGINT NOT NULL CHECK (
                    healthy_samples_before_growth > 0),
                actor_principal_id TEXT NOT NULL CHECK (
                    actor_principal_id ~ '^principal-[0-9a-f]{{64}}$'),
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (profile_id, revision),
                CHECK (sample_interval_s < max_sample_age_s)
            )""",
        ),
        tables=(ADMISSION_POLICY_TABLE,),
    ),
)
