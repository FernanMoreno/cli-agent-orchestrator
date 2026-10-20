# T020 runtime SQLite path audit — refreshed 2026-10-16

## Current result

The current source has no live SQLite connection path. The SQLite importer,
legacy SQLite recovery verifier and old SQLite schema modules described in the
2026-10-12 audit have since been removed. They are not an available offline
import feature in the current application. The old `DATABASE_FILE` constant
remains a compatibility path definition; no current Python caller uses it to
open a database. `WorkRepository` may retain a `.db` artifact namespace without
using SQLite for persistence.

This refresh checks source wiring; it does not replace the PostgreSQL, Docker,
QEMU, provider, performance or full regression acceptance gates in `tasks.md`.

## Current operational boundaries

| Entry point | Verified current behavior |
| --- | --- |
| [`install.sh`](../../install.sh) → [`scripts/docker_install.py`](../../scripts/docker_install.py) | Installer database preparation reuses an explicit PostgreSQL configuration or provisions managed PostgreSQL. It refuses unrecognized nonempty existing state. Retired `--import-sqlite` options are rejected. |
| [`clients/postgres/config.py`](../../src/cli_agent_orchestrator/clients/postgres/config.py) | `DatabaseTarget.from_settings()` requires a version-1 target configuration, selects the requested PostgreSQL scope and fails on absent or invalid targets. It has no SQLite fallback. |
| [`scripts/personal_deployment.py`](../../scripts/personal_deployment.py) | Backup verifies native PostgreSQL targets and uses `pg_dump`; restore validates main/auth/peer targets and uses `pg_restore`. Old `.db`/`.sqlite*` files are excluded from copied deployment state. The retired `import-sqlite` command is rejected. |
| [`scripts/docker_personal_runtime.py`](../../scripts/docker_personal_runtime.py) | The personal runtime starts the PostgreSQL-configured deployment service. |
| [`scripts/local_work_docker_demo.py`](../../scripts/local_work_docker_demo.py) | The additional local Work wrapper requires an explicit database config, copies it privately and binds `CAO_DATABASE_CONFIG_FILE`; it rejects conflicting inherited configuration. |
| [`services/recovery_bundle.py`](../../src/cli_agent_orchestrator/services/recovery_bundle.py) | Current recovery operates on PostgreSQL targets and native backup tooling; the former legacy SQLite recovery module is absent. |

The current installer, runtime, backup and restore chains were reviewed against
their actual callers and configuration propagation. Searches for SQLite
connection APIs and retired modules were checked against source, rather than
assuming that an old graph or similarly named artifact path proves a live
backend.

## Historical evidence — 2026-10-12

The original audit examined a different source state that still contained
`clients/postgres/importer.py`, `clients/legacy_sqlite/recovery_bundle.py`,
legacy schema validators and `services/recovery_inventory.py`. Its importer
and historical-artifact call paths are retired; they must not be presented as
current capabilities.

That historical isolated Graphify extraction covered 439 source files,
13,647 nodes and 35,562 edges. Its source-copy manifest was
`f163961b687661872cc1c23fee7164bd30b8df1e18faf258f775bfb0b78faf3b`;
graph SHA-256 was
`56520b9ef42f80b2ac9f9be4f9eb8a0f61618ce93eae9425a90f533168f14fb8`.
The historical graph manifest was
`efb79280a44ffbd79bbbe6662cea114c23ca300f390a8c81a0821513d837c971`.
These hashes preserve provenance; the historical graph is not current-source
proof. The original audit text remains preserved in the private evidence.
No shared Graphify cache was rebuilt for this documentation correction.

## T009–T019 source reconciliation

The source wiring below is present. Existing task checkboxes are preserved
except for T020; this table does not substitute for each task's required tests
or global acceptance.

| Task | Current persistence boundary in source |
| --- | --- |
| T009–T010 | [`clients/work_repository.py`](../../src/cli_agent_orchestrator/clients/work_repository.py) binds `DatabaseTarget.from_settings(scope="main")`; a `Path` argument only selects file-backed artifact roots. `connection` and transaction methods use PostgreSQL. Native Work migrations are in `work_postgres_schema.py` and the `work_*_schema.py` modules. |
| T011–T012 | [`clients/interaction_repository.py`](../../src/cli_agent_orchestrator/clients/interaction_repository.py) is the native PostgreSQL interaction ledger; interaction services use that repository. |
| T013 | [`clients/database.py`](../../src/cli_agent_orchestrator/clients/database.py) binds the SQLAlchemy engine to a psycopg connection and `init_db()` verifies the provisioned PostgreSQL core schema. T013 is already checked in `tasks.md`. |
| T014 | [`runtime_channel/store.py`](../../src/cli_agent_orchestrator/runtime_channel/store.py) connects to a PostgreSQL target and verifies the runtime-channel migrations. |
| T015 | [`services/workflow_journal.py`](../../src/cli_agent_orchestrator/services/workflow_journal.py) and native `workflows_postgres_schema.py` / `work_continuation_postgres_schema.py` provide the PostgreSQL workflow and continuation stores. |
| T016 | [`services/private_plan_snapshot.py`](../../src/cli_agent_orchestrator/services/private_plan_snapshot.py) and `services/approval_store.py` use PostgreSQL connections and verify native migrations. T016 is already checked in `tasks.md`. |
| T017 | Knowledge reads/writes use `WorkRepository` and the native Work schema. The former legacy SQLite schema catalog is absent from current source. |
| T018 | [`clients/browser_auth_repository.py`](../../src/cli_agent_orchestrator/clients/browser_auth_repository.py) uses an auth-scope PostgreSQL target and native migration verification. |
| T019 | [`services/local_peer_registry.py`](../../src/cli_agent_orchestrator/services/local_peer_registry.py) resolves the shared peer PostgreSQL target and verifies/connects to it. Host identity/configuration files do not provide a SQLite persistence fallback. |

## T020 disposition

No runtime SQLite connection path remains in the audited source tree. No
production change was needed for T020. The task is checked as a source audit;
runtime acceptance for T009–T019 and the separate integration/global gates
remain open as shown in `tasks.md` and `validation.md`.
