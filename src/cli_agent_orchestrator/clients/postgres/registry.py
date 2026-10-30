"""Single explicit owner registry for all native persistence components."""

from .config import DatabaseTarget
from .connection import connect
from .migrations import ComponentMigration, SchemaMismatch, migrate_component


def component_migrations(scope="main"):
    if scope == "main":
        from cli_agent_orchestrator.clients.admission_bootstrap_schema import (
            RESOURCE_ENVELOPE_MIGRATIONS,
        )
        from cli_agent_orchestrator.clients.database import CORE_MIGRATIONS
        from cli_agent_orchestrator.clients.interaction_repository import INTERACTION_MIGRATIONS
        from cli_agent_orchestrator.clients.postgres.import_receipt_schema import (
            import_receipt_migrations,
        )
        from cli_agent_orchestrator.clients.private_plan_postgres_schema import (
            PRIVATE_PLAN_MIGRATIONS,
        )
        from cli_agent_orchestrator.clients.runtime_channel_schema import runtime_channel_migrations
        from cli_agent_orchestrator.clients.work_continuation_postgres_schema import (
            WORKFLOW_CONTINUATION_MIGRATIONS,
        )
        from cli_agent_orchestrator.clients.work_postgres_schema import WORK_POSTGRES_MIGRATIONS
        from cli_agent_orchestrator.clients.workflows_postgres_schema import (
            BEADS_WORK_MIGRATIONS,
            WORKFLOW_MIGRATIONS,
        )

        return (
            CORE_MIGRATIONS,
            WORK_POSTGRES_MIGRATIONS[:1],
            WORKFLOW_MIGRATIONS,
            PRIVATE_PLAN_MIGRATIONS,
            WORKFLOW_CONTINUATION_MIGRATIONS,
            INTERACTION_MIGRATIONS,
            BEADS_WORK_MIGRATIONS,
            runtime_channel_migrations(),
            WORK_POSTGRES_MIGRATIONS,
            RESOURCE_ENVELOPE_MIGRATIONS,
            import_receipt_migrations("main"),
        )
    if scope == "auth":
        from cli_agent_orchestrator.clients.browser_auth_repository import BROWSER_AUTH_MIGRATIONS
        from cli_agent_orchestrator.clients.postgres.import_receipt_schema import (
            import_receipt_migrations,
        )

        return (BROWSER_AUTH_MIGRATIONS, import_receipt_migrations("auth"))
    if scope == "peer":
        from cli_agent_orchestrator.clients.postgres.import_receipt_schema import (
            import_receipt_migrations,
        )
        from cli_agent_orchestrator.services.local_peer_registry import LOCAL_PEER_MIGRATIONS

        return (LOCAL_PEER_MIGRATIONS, import_receipt_migrations("peer"))
    raise ValueError("Unknown PostgreSQL store scope")


def bootstrap_scope(target: DatabaseTarget, *, seed_work: bool = True):
    """Installer/importer operation; never called by an ordinary runtime read."""
    groups = component_migrations(target.scope)
    complete: dict[tuple[str, str], tuple[ComponentMigration, ...]] = {}
    for migrations in groups:
        key = (migrations[0].scope, migrations[0].component)
        if key not in complete or len(migrations) > len(complete[key]):
            complete[key] = migrations
    for migrations in groups:
        key = (migrations[0].scope, migrations[0].component)
        if len(migrations) < len(complete[key]):
            # A staged prefix creates tables before dependent components. Once
            # later versions exist, verify the full history at its final stage;
            # never treat the prefix as a request to downgrade the component.
            with connect(target, readonly=True) as reader:
                installed_row = reader.execute(
                    "SELECT max(version) FROM _cao_schema_migrations WHERE scope=%s AND component=%s",
                    key,
                ).fetchone()
                if installed_row is None:
                    raise SchemaMismatch(
                        "PostgreSQL did not report installed migration version "
                        f"for component {key[1]} in scope {key[0]}"
                    )
                installed = installed_row[0]
            if installed is not None and installed > len(migrations):
                continue
        migrate_component(target, migrations)
    if target.scope == "main" and seed_work:
        from cli_agent_orchestrator.clients.work_repository import bootstrap_work_store

        bootstrap_work_store(target)
