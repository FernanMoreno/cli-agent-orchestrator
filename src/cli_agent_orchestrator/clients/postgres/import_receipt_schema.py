"""Versioned PostgreSQL schema for one-way legacy-import receipts."""

from .migrations import ComponentMigration

IMPORT_RECEIPT_TABLE = "_cao_import_receipts"

_IMPORT_RECEIPT_STATEMENTS = (
    """CREATE TABLE _cao_import_receipts (
        receipt_id UUID PRIMARY KEY,
        scope TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        source_path TEXT NOT NULL,
        source_digest TEXT NOT NULL,
        source_schema_fingerprint TEXT NOT NULL,
        source_identity TEXT NOT NULL,
        target_store_id UUID NOT NULL,
        target_schema TEXT NOT NULL,
        target_identity_hash TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('staging','verified')),
        table_manifest TEXT NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        verified_at TIMESTAMPTZ,
        UNIQUE(scope,target_store_id)
    )""",
)


def import_receipt_migrations(scope: str) -> tuple[ComponentMigration, ...]:
    """Return the single canonical receipt migration for a native store scope."""
    if scope not in {"main", "auth", "peer"}:
        raise ValueError("Unknown PostgreSQL store scope")
    return (
        ComponentMigration(
            component="import_receipts",
            scope=scope,
            version=1,
            statements=_IMPORT_RECEIPT_STATEMENTS,
            tables=(IMPORT_RECEIPT_TABLE,),
        ),
    )
