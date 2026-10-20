# Composition Review — PostgreSQL Persistence

## Current execution checkpoint — 2026-10-16, 00:27 UTC

This reports run evidence, not global sign-off. Offline checks, native Docker
acceptance, the source-bound global suite, and portable provider runtime are
separate gates.

- The resumed R4 PostgreSQL fixture is healthy on the
  `cao-spec021-local-postgres-v3` profile at loopback port 50469; resumed metadata
  SHA-256: `44c57fa4086772496b36aebb3a8aaeaf6391c61ba6d757e04482225fc3274247`.
- Native R8 acceptance passed against the 3,150-entry source manifest
  `b38930121d118f81d0edae3ecc8a6bd2e7b75afdfe6d228025eafc68bd6c2cd6`. Receipt:
  `root-continuation-r1/native-acceptance-r8-actual-r1/artifacts/native-acceptance-receipt.json`,
  SHA-256 `1ba8726ee61ac78839ba4d7c1fb2b7d48294d1d892226cc58632a81bc050ebff`.
  The three lanes passed: install 1 PASS (201.77 s), PostgreSQL restore 1 PASS
  with 3 deselected (83.03 s), and browser-cookie 1 PASS (21.18 s). Each exited
  0 with an empty process group, no timeout, and no cleanup signals; no not-run
  rows were recorded. The outer controller exited 0, with controller and gate
  groups absent at close. This is not a global pass. Temporary forensic
  artifacts totaling 6.65 MB remain, so this checkpoint does not claim complete
  temporary-file cleanup.
- Global R8 remains incomplete: its three serial critical cases passed, then REST
  was interrupted by TERM (exit 143), leaving an empty result and no combined
  coverage/accounting. The operator reported that Docker Desktop was closed or
  Windows/WSL restarted, without a precise time or confirmation of which action
  occurred. No evidence attributes the TERM to that event or establishes its
  cause.
- The earlier ordinary R8 image passed bounded builder qualification, but its
  outer receipt emitter failed with `ValueError` (exit 2) after Docker itself
  exited 0. That invocation did not launch native acceptance; the native PASS
  above comes from the later, separately receipted run.
- AG-UI R16 and generic R15 stopped at supervisor inventory races before inner
  journey receipts. Their journey counts remain unknown. The current R16
  supervisor source candidate has 87 offline PASS, an independent GO, and 87
  fresh-cache PASS. Portable 87 had a first pass but still needs mode/config review;
  it has not been delivered to main, and portable runtime acceptance has not run.
- QEMU diagnostic R1 remains NO-GO pending private quoting/ownership-guard
  corrections; the full 91-case suite has not passed. R9 source freeze has not run.
  Global acceptance and the strict `<1 s` SLO remain open. The two external records
  remain `RECONCILE_REQUIRED` at last read; this checkpoint did not re-audit them.

## Scoped integration review — 2026-10-15, 12:15 UTC

Main now includes the reviewed test harness ports, source-bound generic-message
acceptance and optional QEMU host prerequisites. The latest main regression is
291 passed with no failures/errors/skips; real-provider, final ALL, QEMU and the
three separate personal/browser Docker gates remain open. Production database,
API authorization and event schemas were not changed by these harness fixes.
The message driver preserves native pane identities and caller-owned targets
across API replacement; failed process-exit proof preserves resources and fails
the run. Its positive observer completion seal and redacted live-event evidence
are acceptance requirements, not evidence of an unexecuted live lane.

The Graphify query was broad and based on a pre-existing graph; direct source
inspection supplied the boundary evidence. No full graph rebuild is needed for
these test/docs/prerequisite-only changes; do not treat stale graph edges as
proof. Durable decisions and limitations are recorded in these repository
artifacts and private receipts; no raw credentials or transcripts are copied.
This is a scoped review, not aggregate PostgreSQL migration sign-off.


## Evidence update — 2026-10-15, 10:55 UTC

The source-bound Docker selection now passed all 15 cases with its original
bounds. Three original cold-process strict `<1 s` assertions also passed;
independently reviewed artifact recovery documents a controller filename
defect without rewriting its failed receipt or inventing exact internal times.
These scoped results do not close the aggregate review.

QEMU reached its unchanged 45-minute limit after 80 of 91 passing cases;
cleanup verification remains incomplete and its marker database is preserved.
Codex AG-UI R7 localized a vanished-task inventory race in the private supervisor;
its cleanup is incomplete. OpenCode R6 had complete cleanup but a pre-terminal
harness `ValueError` in the broad version-probe phase. Neither is provider
acceptance. Spec023 generic messages across API replacement is still private
and unaccepted. The main runtime/schema/contracts have not changed in these
diagnostic attempts. The previous five-contract composition result is retained;
it is not a fresh composition result for future candidate ports. See the dated
validation entries for exact scope, receipts and unresolved gates.

## Scoped continuation review — 2026-10-15

**Verdict: PASS WITH RISKS for the fixture and AG-UI harness ports only.**
Overall PostgreSQL acceptance remains in progress.

- Changed subsystems: test-provider authorization/environment fixtures and the
  opt-in provider/API-worker/AG-UI restart harness; no production schema or
  runtime change in these ports.
- Neighbors reviewed: real API subprocess startup, native provider profiles,
  PostgreSQL fixtures, production interaction routes/service, AG-UI projection,
  provider-owned wait/acknowledgement and private process cleanup.
- Invariants checked: explicit provider authorization before calls, private
  HOME/auth routing, loopback binding, retained startup fault injection,
  versioned answer identity, closed metadata projection, source provenance and
  preserved R14 behavior with the reviewed numeric process-parser correction.
  The native startup failure remains a real child
  process test, separate from the simulated health diagnostic.
- Architecture: `project-composition-check caos` passed all five Import Linter
  contracts with zero broken. Main-bound regression selection: 210 offline
  tests and one real PostgreSQL/API startup-negative case passed, zero skips.
  The independent fixture port review verified all 13 paths against R9 and
  main hashes. No independently deployed wire contract changed, so no new Pact
  contract was added.
- Real dependencies: PostgreSQL 18.6 profile v3 and a real API subprocess for
  the startup-negative case. Offline process tests exercise cleanup ownership;
  they do not establish real provider/restart acceptance.
- Known failures/limits: OpenCode traces still produced no questions or ACK;
  R4 additionally failed in supervisor identity parsing. Codex AG-UI R5 proved
  two question identities across one restart, then answers failed with 403
  because the harness source owner differed from the real API principal.
  Offline reproduction confirmed the correct owner-policy rejection. A single
  Docker MCP diagnostic passed without reproducing the nine historical failures.
  Full Docker, QEMU, strict `<1 s` cohort and global acceptance remain open.
  One-shot AG-UI snapshots across
  replacement do not prove retry/recovery of an interrupted wait or SSE stream.

See `validation.md` and the private `root-continuation-r1/` receipts for the
source hashes and scoped evidence. Earlier sections below retain their dated
historical claims; this review does not supersede unresolved aggregate gates.

**Review status:** bounded source/composition review updated 2026-10-11; acceptance
is still in progress. This is not acceptance sign-off.
**Verdict:** IN PROGRESS — the bounded r5 real install/import selectors and
T020 acceptance pass, but the complete backend, T097/QEMU, current-source
application image, final Graphify and SC-007 all-provider acceptance remain
incomplete.

The earlier checks and defect notes below retain their original chronology. Their
“pending” statements describe the snapshot/run at that point; the current status
addendum near the end supersedes only claims for which it records a later result.

## Changed subsystems and neighbors reviewed

- PostgreSQL configuration, connection and component migrations: `clients/postgres/`.
- ORM core and the Work authority, scheduler, dispatch, delivery and repository layers.
- Interaction ledger and transport, runtime-channel persistence, and browser-auth
  and peer registries.
- Workflow journal, workflow service and projector, plus workflow continuation,
  coordinator and Beads tables.
- Knowledge, memory and vault services that use ORM tables or Work-backed audit.
- API, CLI, MCP and AG-UI consumers that construct or share these stores.
- The offline SQLite importer, legacy SQLite recovery inventory and deployment
  backup/restore code. Their SQLite access is source/compatibility handling and
  must remain outside runtime persistence paths.

The preimplementation dependency inventory in
[`graphify-impact.md`](graphify-impact.md) names the main callers and was
cross-checked here against the current constructors and routes. The final
Graphify refresh was pending at this earlier review cut; the final isolated
snapshot refresh is recorded below.

## Boundary and invariant review

### Configuration and store identity

[`DatabaseTarget`](../../src/cli_agent_orchestrator/clients/postgres/config.py)
loads a private regular configuration file without following symlinks, checks
owner and mode, and keeps password-file fields out of `repr`. It distinguishes
`main`, `auth` and `peer` scopes and requires an explicit migration role distinct
from the runtime role. `store_id` is the stable logical UUID, while
`DatabaseTarget.identity` is the configured target tuple
`(host, port, database, schema, store_id)` used to prove that components share
the same configured store. Filesystem `home` and credential paths are not part
of that identity.

The native [`Connection`](../../src/cli_agent_orchestrator/clients/postgres/connection.py)
sets an explicit search path and bounded connection, statement and lock timeouts.
Reads requested as snapshots begin `REPEATABLE READ READ ONLY`; writes begin an
explicit transaction. Psycopg exceptions are surfaced through the project error
types, and SQL placeholders are not rewritten. The `Record` adapter preserves
named and ordinal access used by callers. When Work borrows a SQLAlchemy-owned
connection, `Connection` installs that adapter per cursor; it preserves the
owner's row factory, transaction state and connection lifetime.

### Migration and schema verification

[`migrations.py`](../../src/cli_agent_orchestrator/clients/postgres/migrations.py)
serializes component bootstrap with a PostgreSQL advisory transaction lock,
checks migration version/checksum history, and fingerprints installed columns,
constraints, indexes, triggers, functions, sequences and relation posture.
Runtime verification also checks the singleton logical-store identity, requires
the configured namespace to exist, and rejects runtime roles with unsafe
ownership or DDL privileges. The namespace CREATE check resolves the namespace
OID and checks privileges against that OID in the same catalog statement; a
missing namespace raises `SchemaMismatch` without attempting repair.
[`registry.py`](../../src/cli_agent_orchestrator/clients/postgres/registry.py)
registers the components in dependency order; Work's staged prefix makes
referenced tables available before the final Work version adds cross-component
constraints.

Runtime constructors verify installed state rather than creating or repairing
tables. For example, `WorkRepository.initialize()` performs a read-only
verification, and `InteractionRepository` verifies its component through the
same Work-owned snapshot. The SQLite schema helpers and
`services/recovery_inventory.py` still inspect legacy SQLite input; current
callers route those helpers through `clients/legacy_sqlite/recovery_bundle.py`.
That is an offline compatibility path, not evidence of a runtime SQLite
fallback.

### Import provenance and recovery composition

Import receipts are now a registered `import_receipts` component in main,
auth and peer. Bootstrap owns its DDL; importer validation verifies the
canonical migration and requires exact physical table coverage. The receipt
is excluded from SQLite application-data mappings and included in recovery
inventory. Runtime access is SELECT-only, including after later migrations
and test-worker reset. Native regressions verified import/capture/blocked
restore and retained the historical source target hash; restoration does not
silently rebind that provenance to the new physical destination.

The recovery exporter shares a PostgreSQL snapshot with inventory and dump.
For referenced routines, the catalog fingerprint now reads function source
and behavior fields directly from MVCC-visible `pg_proc` rows and resolves
argument, return, variadic, and transform types through catalog joins; it does
not persist catalog OIDs or call `pg_get_functiondef()`. Trigger entries reuse
the same canonical routine record. SQL-standard parsed bodies, argument
defaults, support routines, and aggregates fail closed because this migration
set does not define them. A two-connection regression reproduced the former
cache/snapshot mismatch, and the corrected repeatable-read manifest plus the
actual archive case now pass: a routine body changed before snapshot export
and reset before inventory is rejected instead of publishing the secret body.
This evidence is specific to routine bodies and the exercised archive race.
Columns, constraints, indexes, trigger definitions, and rules still use
PostgreSQL deparsers, so the review does not claim that every deparser or every
catalog object is MVCC-cache consistent under concurrent DDL. Comments are
excluded from production archives, and standalone types and extended
statistics are rejected because current migrations do not own them. These
checks do not certify every possible PostgreSQL object class.

### Shared Work and interaction transactions

[`WorkRepository.transaction()`](../../src/cli_agent_orchestrator/clients/work_repository.py)
owns and closes its connection, verifies the schema, takes the store write lock,
and commits or rolls back the caller's unit. `read_snapshot()` provides one
verified read-only snapshot. The repository retains filesystem roots for
content-addressed results and delivery/executable payloads; those roots are
sidecar content, while relational state and authority remain in PostgreSQL.

[`InteractionRepository`](../../src/cli_agent_orchestrator/clients/interaction_repository.py)
requires exact `DatabaseTarget.identity` equality with its Work repository and
delegates both write and read contexts to Work. This keeps an interaction
answer/receipt and its related Work state in one transaction when the service
uses the shared connection. Auth and peer state have separate target scopes, so
no cross-scope atomicity is implied; import and cutover must rely on durable
receipts and explicit recovery instead of claiming a distributed transaction.

Cross-request ordering uses explicit PostgreSQL primitives rather than SQLite
rowids or process-local locks. The Work scheduler takes a store-scoped advisory
transaction lock before queue, capacity, policy and dispatch-sequence mutations,
and selects queued work by the persisted `queue_sequence`. Path reservations
take a store-scoped advisory transaction lock across the occupied-path scan and
reserve/release mutation, so nested-path conflicts cannot race between
connections. Interaction events are appended on the caller's active Work
transaction and return the database-assigned event sequence; the state change
and event become visible together at commit. These locks are narrow to the
aggregate they protect; they do not serialize unrelated Work rows or separate
auth/peer databases. The legacy importer maps the scheduler request's SQLite
rowid into `queue_sequence` and restores the sequence high-water mark; its
profile test verifies that the next generated value follows the imported row.

The interaction schema uses database constraints and immutable-version/event
triggers for authority-bearing history. The delivery lifecycle distinguishes
acceptance from return/acknowledgement; required work cannot be treated as
delivered merely because an answer was accepted. Expiry and cancellation are
terminal for the wait, while durable accepted content remains recoverable only
under the exact source/version rules. These boundaries still need the full
race/restart acceptance suite on the current snapshot.

### Workflow state, connection ownership and replay

[`workflow_journal._connect()`](../../src/cli_agent_orchestrator/services/workflow_journal.py)
opens the configured main target and starts an explicit transaction. If
`begin()` fails, it now closes the allocated connection before re-raising. The
operation wrappers close connections they own, while caller-supplied
connections stay open and uncommitted until the caller decides. The regression
in `test_workflow_journal_connection_posture.py` injects a begin failure and
checks the underlying connection is closed; the ownership tests also cause a
real PostgreSQL `SELECT 1 / 0` inside an owned transaction and verify its marker
row rolled back.

[`WorkflowStepProjector`](../../src/cli_agent_orchestrator/services/workflow_step_projector.py)
compares the exact `DatabaseTarget.identity` for the repository, origin reader
and result reader. It does not infer identity from the local artifact path. Its
regression pairs repositories with a shared filesystem home and differing
logical store IDs, then requires construction to fail. Projection re-reads the
journal, immutable binding and accepted result; there is no process-local
result cache that can authorize replay.

Work admission resolves and preflights outside the write transaction, then
revalidates live grant and frozen contract before persistence. Delivery claims
are committed before external sends; a crash after the claim preserves
uncertainty rather than automatically sending again. Workflow replay creates
its own immutable attempt contract and revalidates it before terminal effects.
The YAML delivery guard uses a verified read-only snapshot and plain SELECTs,
matching the original SQLite check; it rechecks generation, run and step state,
attempt number, fingerprint and frozen fields immediately before delivery.
`check_effect()` derives its lock clause from the active native transaction:
read-only recovery snapshots validate without row locks, while writable
admission and coordinator transactions retain `FOR UPDATE` on coordinator
and driver rows. Recovery returns an existing Work item without DML or external
effects; effect-producing admission revalidates inside its write transaction.
The focused PostgreSQL composition tests passed for plans-attached
`recover_step`, valid and stale generation checks within a read-only snapshot,
and a second writer blocked on the driver row until the first transaction
released its lock. The wider T020 acceptance suite remains pending.
The source scan found no literal `FOR UPDATE` query inside a `read_snapshot`
block. `WorkflowContinuationDriver.assert_current()` constructs its lock clause
dynamically; its no-connection/read-snapshot path defaults to `lock=False`, and
its `lock=True` call sites pass connections opened by `repository.transaction()`.

### Memory, auth, peer, API and configuration propagation

The ORM engine is bound to the configured PostgreSQL target and records its
target identity on each SQLAlchemy connection. Legacy memory audit accepts a
borrowed ORM transaction only when it has an enclosing durable audit and the
recorded target identity matches. Reviewed knowledge uses the explicit
`KnowledgePolicy` repository and fails closed instead of falling back to legacy
memory. Browser authentication uses the separate `auth` target; the peer
registry uses `peer`. API, MCP and AG-UI entry points must continue to resolve
their caller scopes before reaching those stores; their full contract and
revocation checks remain part of pending integration validation.

### Failure, ordering, migration and compatibility

The current PostgreSQL fixtures use a pinned PostgreSQL image, private test
schemas and distinct runtime/migration roles in
[`test/postgres/fixtures.py`](../../test/postgres/fixtures.py). The native tests
therefore can detect SQL dialect, transaction, catalog, lock and privilege
behavior that SQLite or mocks would hide. `test/integration/test_work_admission.py`
also corrupts evidence through an owner connection by temporarily disabling
the specific immutable trigger, changing a row, restoring the trigger, and then
checking that runtime replay rejects the evidence while the installed migration
baseline remains valid. The trigger alteration is test setup only; runtime
roles are explicitly denied DDL.

The session-scoped `postgres_worker_targets` fixture bootstraps each private
`main`, `auth` and `peer` schema through the production component registry and
records its schema fingerprint. The autouse `isolated_memory_db` fixture resets
that schema and binds ORM modules to the runtime-role engine/session for each
ordinary test. A few older ORM tests still call `Base.metadata.create_all()`
against this already migrated PostgreSQL schema; those calls are redundant test
setup, not the runtime schema-creation path or a substitute for registry-
migration coverage.

The importer in `clients/postgres/importer.py` reads closed legacy profiles,
records staged/verified receipts and checks source digests and target identity.
Deployment code includes PostgreSQL backup and isolated restore manifests.
Correct recovery across all scopes, interrupted import, upgrade/rollback and
managed installation still requires current real-dependency runs; code presence
alone does not establish those scenarios.

## Composition defects found and current disposition

1. A workflow connection allocated before a failing transaction start could
   remain open. `_connect()` now closes it in the exception path; the test
   described above covers that boundary.
2. The workflow projector previously used a local path as a proxy for database
   identity. It now requires the exact configured target identity across all
   dependencies; the shared-home/different-store test covers this case.
3. A mypy finding in workflow run-capability authentication indexed the optional
   result of an aggregate query. The code now checks `fetchone() is None` and
   denies closed; a real empty-capability-store test asserts the public call
   raises `OriginDenied`. The test uses the real database aggregate result,
   since PostgreSQL's ungrouped aggregate returns one row with a NULL value even
   when there are no matching capabilities.
4. Recovery capture's original implementation inventories and scans rows,
   invokes `pg_dump`, then inventories the source again using separate snapshots.
   The Work offline cut fences registered writers, but raw workflow journal
   writes do not use that registry. A transient credential-shaped value could
   therefore enter the dump without appearing in either inventory or the secret
   scan. The current source contains a shared-snapshot fix, with a regression
   that injects a credential-shaped workflow input before the dump, removes it
   after the dump,
   and inspects the actual archive. A failed patch briefly truncated
   `services/recovery_bundle.py`; Design restored it atomically from the known-
   good ext4 candidate and reapplied only the snapshot hunks. The RED/GREEN
   recovery run used the 60,951-byte source with SHA256
   `d939c25e9dddd87534d45443ef938d62df345ea58ba53ca3afe61d655a16bf19`.
   Root's format-only pass changed the current file hash to
   `4e34cce3518bba3d20ddee2f688ae628f40eb591bac6e95ccbaad0e2605bfe51`; the
   applied hash manifest records AST equality. Static review confirms the
   intended exporter lifetime and snapshot is passed to the archive runner.
   Root reports the focused native regression first failed
   because `pg_restore` exposed the transient credential, then passed after the
   snapshot fix (1 passed). This reviewer did not run that test; broader recovery
   capture/restore coverage remains pending.
6. T020 composition exposed `check_effect()` issuing `SELECT ... FOR UPDATE`
   while `recover_step()` was inside `WorkRepository.read_snapshot()` through
   `WorkWorkflowPlans.revalidate_origin()`. The native RED failed at the first
   coordinator query with `ReadOnlySqlTransaction` (1 failed, 9.43s). The code
   now requests row locks only when `connection.read_only` is false, preserving
   the write-transaction fence while making recovery validation observational.
   The three focused PostgreSQL regressions passed (3 passed, 23.05s): the
   plans-attached recovery returned the exact existing Work; current and stale
   generations were checked in a read-only snapshot; and `pg_blocking_pids()`
   confirmed a second writer waited on a held driver-row lock until release.
   The RED log is
   `/home/felni/.cache/caos-spec021-validation/tmp/workflow-readonly-audit-red.log`;
   the three-case GREEN log is
   `/home/felni/.cache/caos-spec021-validation/tmp/workflow-readonly-audit-green.log`.
   This agent did not run the full T020 or backend suites. Current driver source
   SHA256 is `2378e9ecdaea5cb046cd989d896b144e33c2342b9621e9247ac221a135da1335`.

7. Before the patch, `catalog_manifest()` omitted relation persistence/kind and
   row-level security posture. A migration owner could change a logged table to
   `UNLOGGED`, replace a nullable table with a same-shaped view, or enable a
   restrictive RLS policy without changing the recorded fingerprint. The RLS
   case has a direct cross-role consequence: recovery inventory and secret scanning use
   the runtime role, while the archive adapter uses the migration owner, which
   bypasses non-`FORCE` RLS. Four native regression cases now cover RLS,
   policy-expression-only drift, `UNLOGGED`, and table-to-view drift in
   `test/clients/test_postgres_rls_catalog.py`. Design first ran the three
   structural cases against unchanged code; each failed only because
   verification did not raise `SchemaMismatch`. The relation-kind case
   initially targeted `pg_catalog` through the connection search path; its test
   now schema-qualifies the replacement view. The production fingerprint now
   includes relation kind/persistence, RLS flags, and policy definitions. The
   three original cases plus registry bootstrap idempotence passed after that
   change (4 passed). The added policy-expression-only regression is included
   in Design's comprehensive PostgreSQL batch; its result remains pending. A
   separate recovery run exposed that verification of a missing auth namespace
   raised raw `InvalidSchemaName`. The runtime check now resolves the namespace
   OID and checks CREATE against that OID in the same statement. Design's
   focused recovery rerun passed 4 tests, including the test that drops the auth
   schema, expects `SchemaMismatch`, and confirms runtime verification did not
   recreate it. The current edited `migrations.py` SHA256 is
   `4bd1042ff8562646c64fb4e14455453e4760e5518a3175276840327ce16c939d`.

## Checks and scenarios

- This reviewer ran `black --check`, `isort --check-only`, `py_compile` and
  `git diff --check` on the workflow capability guard/test and the native vault
  audit composition test; these passed.
- The standard `mypy.ini` configuration passed for the changed
  `work_workflow.py` source. Project-wide mypy is still pending.
- A focused `pytest --collect-only` run for the two Work test files collected
  88 tests before the latest regression was added. It did not execute database
  behavior.
- Root reports 9 PostgreSQL tests passed (7 vault determinism tests, memory
  reconciliation and scoring) in `/tmp/caos-spec021-root-final-three.log`, and
  a separate 30-test Work transaction subset passed against an earlier source
  snapshot. The broad pytest run is still pending.
- Root reports a native RED reproduction of the transient credential leak and a
  post-fix GREEN run of the archive-inspection regression (1 passed). This
  reviewer did not run the command; the full recovery capture/restore suite is
  still pending.
- Architecture/import-linter, API/MCP/UI contract tests, full backend and
  frontend suites, deployment/restore tests, `project-composition-check`, and
  the final Graphify refresh are not complete in this review.

## Residual risks and acceptance gaps

- No current full PostgreSQL test result establishes all 17k collected tests
  pass. Collection alone only establishes imports and discovery.
- The workflow empty-capability regression and the full Work transaction suite
  still need current-snapshot verification; earlier focused results do not
  establish the broad suite.
- Import interruption/retry across `main`/`auth`/`peer`, configuration cutover,
  managed/external install, upgrade compatibility, backup and isolated restore
  must be verified in the final serialized acceptance run.
- The exported-snapshot capture path has one reported native archive-inspection
  regression pass. The full recovery capture/restore and final suite remain
  pending; this partial result does not establish overall acceptance.
- Existing SQLite inspection under `legacy_sqlite` and recovery inventory must
  stay behind the offline import/recovery boundary; a future runtime caller
  would violate FR-001/FR-008.
- No claim is made here that SC-001 through SC-006 or the full implementation
  contract has passed.

## Current bounded status — 2026-10-11

This addendum records current results without converting focused checks into a
full acceptance claim.

- The current composition check passed with **427 files, 1,806 dependencies,
  five contracts kept, zero broken**. Evidence:
  `/home/felni/.cache/c16t/spec021-composition-20261011-continuation.log`.
- Repository-configured CI r4 checks passed: Black **1,289 files unchanged**,
  isort exit zero, and mypy **no issues in 424 source files**. Mypy retains its
  standard limitation around unchecked bodies of untyped functions. Evidence:
  `/home/felni/.cache/c16t/spec021-ci-20261011-r4/{black,isort,mypy}.log`.
- Browser r8 passed **20 cases, zero skips**, in 2.5 minutes. The 20-source,
  100-question inbox path measured DOM visibility p95 **1,294 ms**. Evidence:
  `/home/felni/.cache/caos-spec021-live-acceptance/.origin/logs/web-e2e-full-r8.log`.
- Import receipts are now registered in the main, auth and peer catalogs,
  included in native recovery, excluded from legacy application-data mappings,
  and read-only for runtime roles after migration and worker reset. The current
  import/capture/blocked-restore composition run passed **six tests**; the
  catalog/registry/receipt-reset/routine-ownership batch passed **12 tests**.
  Evidence:
  `/home/felni/.cache/caos-spec021-validation/tmp/import-receipts-focused-20261011.log`
  and
  `/home/felni/.cache/caos-spec021-audit-receipts-20261011/tmp/catalog-owner-final-green.log`.
- Recovery shares one exported PostgreSQL snapshot for inventory and `pg_dump`.
  The routine-body catalog uses MVCC-visible `pg_proc` fields, and recovery
  rejects unreviewed schema relations, routines, types, auxiliary objects and
  extended statistics. The tested routine snapshot race, receipt retention and
  normal capture/restore cases passed; other PostgreSQL deparser paths remain
  explicitly unproven for concurrent catalog-cache changes.
- The Docker Desktop gateway run passed the real private PostgreSQL dump plus
  six command/configuration cases (**seven passed**). This verifies the named
  gateway path; it does not replace the stopped-SQLite import acceptance.
  Evidence:
  `/home/felni/.cache/c16-spec021-loopback-r1/gateway-green-r3.log`.
- The actual PostgreSQL capacity regression passed for **20 sources and 100
  pending questions**. It asserts `sorted(visibility_latency)[18] < 2.0` and
  checks explicit saturation and continued visibility. The test does not print
  the measured percentile. Evidence:
  `/home/felni/.cache/caos-spec021-audit-receipts-20261011/tmp/interaction-capacity-p95.log`.
- The combined T019/T020 Docker acceptance completed all **8 T019 and 6 T020
  cases**: **14 passed, zero skipped, five warnings, exit 0** in **233.41s**.
  Evidence:
  `/home/felni/.cache/c16-evidence-archive/t097-local/t020-full-v3-testtimeout-r1/full-t020.log`.
  The final T019 sibling test used a test-only 60-second barrier while leaving
  the production timeout at five seconds; its PID, file-descriptor and canary
  assertions passed in this combined run. The earlier diagnostic log
  `/home/felni/.cache/c16-evidence-archive/t097-local/t019-sibling-diagnosis-v2/focused-pytest-final.log`
  failed at 5.155s because the prior test version's container disappeared
  before inspection. That diagnostic failure is not the final acceptance run.
  The independently checked supervisor artifact is mode `0555`, owned by
  `1000:1000`, SHA256
  `0a18fc3e56e50067f664368de13a9910b3af3950ecc68c4c85c8ddd32c6f4d74`, and
  `readelf` found no `PT_INTERP`; see
  `/home/felni/.cache/c16-evidence-archive/t097-local/t020-full-v3-testtimeout-r1/verification/root-verified-supervisor.json`.
  The worker artifact is SHA256
  `04dc7236707b12c092b69b68b09b69b1bc312156145bb51376bee7e1573765c9` and
  likewise has no `PT_INTERP`, as recorded in
  `/home/felni/.cache/c16-evidence-archive/t097-local/t020-full-v3-testtimeout-r1/verification/t020-verification.json`.
  The build's conditional `rg` check was not recorded as passed; the exact
  binary properties were verified from the artifacts instead.
- T097/QEMU was not run because host memory was below the required 2 GiB. The
  full backend run was still in progress at this review cut, so there is no
  complete backend result yet.

### Latest provider and installer boundary — 2026-10-11

- The real Codex r7 run completed the exercised subscription journey with two
  questions, two human answers and two `agent_acknowledged` deliveries. The
  successful acknowledgement was checked against the created/returned group,
  exact question IDs and versions, receipts and count. An earlier acknowledgement
  item failed; its cause remains unproven. This verifies the r7 path only; it
  does not establish SC-007's all-provider acceptance. The precise result and
  evidence are recorded in `validation.md` under the Codex r7 entry.
- Source review of `scripts/docker_install.py` confirms that both managed main
  and peer PostgreSQL containers run with the invoking UID:GID, while bootstrap,
  verify and upgrade-backup admin calls receive the profile network. The
  isolated-peer cleanup checks the private host identity, canonical store UUID,
  exact expected container name and ownership labels before removal. A separate
  network-owner hash remains scoped to network cleanup.
- In `admin()`, backup and restore commands validate that the Docker socket is
  a non-symlink Unix socket, add its group, and mount it read-only. This is a
  read-only filesystem bind; it does not restrict Docker API operations. The
  separate long-running application container uses a regular socket bind; this
  finding is limited to the backup/restore admin path and makes no claim that
  the Docker API is restricted.
- Password-file reuse compares exact canonical UTF-8 bytes, including the
  newline written on creation. The focused installer group passed **24 tests**
  with **32 deselected** in 1.60s; its regression checks creation, unchanged
  reuse, changed-password rejection and an extra newline. Evidence:
  `/home/felni/.cache/c16t/pa-r4/docker-install-focused-r7.log`.
- The current-image real installation selector passed **one test** in 225.81s.
  Root inspected the run and its assertions for initial install, unchanged
  reinstall, login-cookie continuity across stop/start, upgrade, rollback and
  failed-candidate restoration, including protected image aliases and the
  operator application fingerprint. Evidence:
  `/home/felni/.cache/c16t/pa-r4/installation-r7.log`. This is evidence for
  that image and host run, not a universal UID or platform guarantee.
- At this review cut, the stopped-SQLite three-scope import was still pending;
  the r5 real-image retry below now passes that selector. The complete backend,
  broader host/guest gates, final Graphify refresh and SC-007 all-provider
  acceptance remain pending. No overall PASS is claimed.

### Import and scheduler activation ordering — 2026-10-11

- Source review confirms `import_legacy_sqlite()` verifies each committed
  main/auth/peer receipt against its preflight identity and manifest before it
  writes the import manifest and atomically publishes the pending database
  configuration. Its `bootstrap_work_store()` call seeds only Work identity and
  recovery context; the import function does not configure or mutate scheduler
  policy before that final receipt check.
- `configure_scheduler()` first calls the read-only `verify_database()` across
  all three scopes, then opens the main `WorkRepository`. Scheduler setup
  verifies the Work schema, reads the existing policy, and writes a default
  policy only when the row is absent. This is ordinary transactional DML, not
  schema creation; an existing operator policy is preserved. The normal Docker
  install path invokes this explicit operation after its database verification.
  The `--import-sqlite` path runs the import-only helper and returns before that
  normal activation sequence.
- The native/container acceptance test encodes this lifecycle by checking all
  three durable receipts and `scheduler_configured == false` immediately after
  import, then requiring the normal install to configure the scheduler while
  the receipts have already been checked. Its post-activation helper checks
  scheduler state but does not re-read receipt rows. The non-Docker policy test
  also checks default creation and preservation of a later operator policy.
  The r5 actual-image run below exercises the import then normal-install
  sequence.
- The retry image is pinned as
  `sha256:24faedeb973856ab76d0579d4bdfa007e667d34c0b38c977775a7b3cd9088eb4`;
  its embedded `personal_deployment.py` digest was checked against the current
  source SHA256 `c24fd516e7cddc9ad725a13ea1f28f591fab4465d08715b17f51cbc4b7d12d00`.
  This records the source/image pairing used by r5; it is not by itself a
  deployment result.

### Current real-image install/import and composition result — 2026-10-11

- Against that immutable r5 image, the actual Docker installation selector
  passed **one test** in **281.33s**. The separate stopped-SQLite import plus
  follow-up normal-install selector passed **one test** in **90.47s**. Evidence:
  `/home/felni/.cache/c16t/spec021-app-acceptance-r5/install.log` and
  `/home/felni/.cache/c16t/spec021-app-acceptance-r5/import.log`.
- The import run checks all three source receipts before activation, verifies
  the scheduler is unconfigured immediately after import, then performs normal
  installation and verifies scheduler setup. After activation, the test checks
  scheduler state but does not re-read the receipts; the reviewed scheduler
  operation itself performs no receipt writes. The run's exact owned-resource
  cleanup and protected operator-state assertions also passed. This closes
  these two real-image selectors for the tested image and host; it does not
  establish the complete backend or other host/provider environments.
- The r5 image predates the later `workflow_continuation_driver.py` lock fix.
  Its install/import results remain valid for that image, but a fresh image
  built from the current application source is still required for final
  readiness.
- The current composition check passed with **427 files, 1,806 dependencies,
  five contracts kept and zero broken**. Evidence:
  `/home/felni/.cache/c16t/spec021-composition-r5-current.log`.
- Full backend coverage, T097/QEMU, a current-source application image, final
  Graphify refresh and SC-007 all-provider acceptance remain pending. The
  Codex r7 journey does not substitute for those provider gates.

### Current AG-UI scope projection and continuation schema fences — 2026-10-12

- The AG-UI GET stream now stores the scope list returned by its final
  post-projection authorization check into the emitted full snapshot. On idle
  heartbeats it revalidates after reading the durable interaction projection,
  applies the returned scopes to the candidate state, and computes the delta
  afterward. A revoked read grant exits before the delta is emitted. The
  existing post-read revocation regression remains in the focused run.
- `WorkflowContinuationDriver` revalidates `WORKFLOW_CONTINUATION_MIGRATIONS`
  on the active connection before each newly covered direct read or write:
  enable, owner lookup, process-exit recording, release, stop request, pause,
  `_read_rows`, and `_write` calls without a run fence. Existing claim,
  `assert_current`, shutdown, and `check_effect` guards remain. `check_effect`
  keeps its row locks in writable transactions and stays read-only in recovery
  snapshots. `workflow_journal.project_work_result()` calls `enqueue_projected()`
  on both the first projection and exact-marker replay paths. That helper now
  verifies `WORKFLOW_CONTINUATION_MIGRATIONS` on the active transaction before
  reading the driver row or writing the outbox.
- On the pre-fix implementation, the real-PostgreSQL RED run failed all **10**
  new scope/schema-drift cases. The corrected immutable r7 snapshot passed the
  same cases plus the existing post-read revocation case: **11 passed**, five
  warnings, in **84.54s**. Durable output:
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/green/boundary-green-r7.log`.
  The snapshot import lines point to the r7 ext4 source tree. Black formatting
  of the two test files happened after this run; their AST digests match the
  pre-format versions. Current Black, isort and `git diff --check` checks for
  those files pass.
- Follow-up PostgreSQL drift tests exposed the uncovered projection caller:
  before the guard, both first-projection and exact-replay cases accepted a
  dropped continuation trigger. With the guard, both raise the component
  fingerprint mismatch. The enclosing projection transaction rolls back on
  the first-projection path, leaving the pending step and marker/outbox counts
  unchanged; replay leaves its existing marker and state unchanged. The full
  projection and effect-fence modules passed **52 tests**, with three existing
  deprecation warnings, in **299.38s**. JUnit evidence:
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/t044-guard/effect-projection-final.xml`.
- The projection race was forced after both callers observed no marker and
  before either acquired the run lock. The second exact-marker check after the
  lock makes the losing identical projection return `already_projected`;
  conflicting marker contents still raise `WorkflowProjectionConflict`. The
  combined current-source module run above includes the full projection module.
- Graphify's isolated code-only extraction indexed **1,839** code files into
  **46,643 nodes** and **114,747 edges** without clustering. Its output stays
  outside the repository at
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/graphify-current/graphify-out/graph.json`.
  It places `agui_stream()` with `_stream_authorization()` and interaction
  service neighbors, and connects `WorkflowContinuationDriver` to API startup,
  coordinator routes and workflow services. Direct source inspection confirms
  those paths. Graphify skipped five files it classified as sensitive; none
  was ingested.
- The optional stock `POST /agui/v1/run` route remains a separate
  request-authorized run-plane surface. It checks resume scope before starting
  and captures the initial scopes for its own snapshot callback. Spec016/021
  do not require stream-time scope refresh on that endpoint; it is recorded as
  an adjacent boundary rather than a blocker for the GET interaction stream.
- The existing composition check above predates the latest function-body
  changes. A current-source preflight now passes with **427 files, 1,811
  dependencies, five contracts kept and zero broken**. This structural check
  does not replace the backend suite, application-image acceptance or host and
  provider gates.

### Current-source workflow effect and replay review — 2026-10-12

- The reviewed source freeze is identified by these SHA-256 values: journal
  `cfd49f0d2f7e918f49235c0b46da99d467e7a770b9018ef648b227ba57c2d781`,
  continuation driver
  `bb74df1938ea99f40e28208eebf760d6f1a776e2e4d971ec4e26f94408f029a6`, Work
  repository
  `c1273a0dfc3a81f747337bc73af720f89ed6988a00b6b28257e797487f01f02a`, workflow
  service
  `ed12d953d096f63a60d2a038821f6d1e6290dfd8a6fafb598de5597bf71bef9f`,
  Bubblewrap composition
  `e500661f58fc86f9eadf01732e749b441c271cfec5308b1d994e8afd35b5ed8f`, Core
  database `6827d177e8f32df41fbbf804b58aae70f9066427da6ef0d65b679a57c0b25a39`,
  and API `0dcb51b8a2031ac35c5cc6d49a1be7a808af6262662d0633f93d77a5cd3db4d8`.
- T045's script and YAML attempt writers verify the Work component, lock the
  matching running `workflow_run` row, and allocate or retry the immutable
  `(run, step, generation, attempt)` contract before delivery. The lock
  serializes the `MAX(attempt_number)` allocation and step update. The native
  step-contract module passed **48 tests**. T046 preserves the resume error
  boundary: only catalog/history/fingerprint mismatches from the Work schema
  verifier become `ResumeCorruptError` and HTTP 422; other target, role or
  availability failures retain their classifications. The API/resume module
  passed **92 tests**. T047's cleanup owner is a target-scoped PostgreSQL
  session advisory lock; the cleanup body runs only after successful
  acquisition, and failed acquisition/error paths close the connection
  without an unlock. Its native cleanup module passed **44 tests**.
- T048's `project_work_result()` first checks for an existing marker, then
  verifies the durable accepted Work attempt, locks the run and rechecks the
  exact accepted-result ID and content hash before locking/updating the step.
  Identical concurrent projection returns `already_projected`; a different
  result still raises `WorkflowProjectionConflict`. Both the step CAS and
  marker insert remain in the same Work transaction.
- A source review found that both projection paths then call
  `enqueue_projected()`, which reads `workflow_driver` and may write the
  continuation outbox. It now verifies `WORKFLOW_CONTINUATION_MIGRATIONS` on
  that same active transaction before the first continuation query. The real
  PostgreSQL regression removes a continuation trigger after setup and covers
  both a new projection and an exact-marker replay. Both fail with a component
  fingerprint mismatch; the new projection's step, marker and outbox counts
  remain unchanged, while replay leaves the original marker and state intact.
- The combined current-source projection and effect-fence modules passed **52
  tests, zero failures, zero skips**, with three existing deprecation warnings.
  JUnit evidence is
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/t044-guard/effect-projection-final.xml`.
  The isolated current-source composition preflight passed **427 files, 1,811
  dependencies, five contracts kept, zero broken**. The existing Graphify map
  was checked against the relevant journal, driver, Work, API and coordinator
  source paths; the final-snapshot Graphify refresh remains pending.
- At this review cut, the full r9 backend suite, the current-source web and
  install/import run, Runtime's fresh QEMU/Docker acceptance, and SC-007's
  all-provider journey are pending or in progress. This bounded source and
  test review does not sign off those gates.

### Final capacity, event and scheduler review — 2026-10-12

- The current T079 visibility run recorded all **20 registrations and 100
  questions**. The gated p95 from confirmed commit to metadata visibility was
  **789.4 ms**; the separately retained request-start-to-visible diagnostic was
  **3,312.8 ms**. The latter includes admission and lock time before confirmed
  registration, so it remains visible as a diagnostic and does not replace the
  SC-001 clock. The cycle/Work fast-path integration run passed **8 tests** in
  **40.14 s**; the lifecycle run passed **5 tests** in **20.80 s**. Evidence:
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/`
  `capacity-confirmed-registration-profile.log`, plus
  `/home/felni/.cache/caos-spec021-evidence-20261012/interaction-capacity/logs/`
  `cycle-work-fastpath-current.log` and `cycle-transitions-combined.log`.
- The metadata page fast path stays inside one verified repeatable-read
  snapshot. It batches delivery receipts, group progress, source rows,
  terminals, session incarnations and Work attempts, then validates each
  distinct source against that snapshot before projecting metadata. The
  classification still treats an attempt binding, managed execution mode, or
  historical Work attempt as managed; this is a per-read optimization, not a
  cross-request authority cache. Cycle recomputation considers already blocked
  waits and groups with persisted delegation rows, then rechecks active delegation
  and source authority before deriving cycle edges. Lifecycle regressions cover
  delegation expiry, supervisor replacement, profile drift and mixed required
  and optional occupancy at the 100-question cap.
- The current interaction implementation is identified by service SHA-256
  `02e404a1f73f735f0089f850daa72c9e5d6f9470ad9558952b60a4f44d903ecf` and
  repository SHA-256
  `3891a84f96e1a21cfcc0be463b651a38516760e2d7ba3ab8349da3e6a7ac4b6d`.
  `create_group()` batches question-created events in their input order, then
  appends the group-created event on the same caller-owned transaction.
  `append_events()` validates every metadata value before writing, requires an
  active caller transaction, checks the batch row count, and lets a failed
  insert roll back with the group. Real-PostgreSQL tests cover order and IDs,
  pre-write validation, mid-batch rollback and transaction ownership; a
  separate unit test exercises the short-write row-count guard. The event
  regression selection passed **4 tests**. Evidence:
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/`
  `append-events-shortbatch-regressions.log`. No sequence or privacy behavior
  is inferred from the performance run alone.
- The scheduler row-lock fix was checked across scheduler, WorkRepository and
  admission neighbors. Public enqueue and claim take the parent job lock before
  the aggregate; queued withdrawal uses the job lock and a conditional request
  update without retaining the aggregate; release paths reread their proof
  after the job/aggregate waits and before effects. The current native lock-order
  test file passed **4 tests in 22.80 s**. Its SHA-256 is
  `5ddc67387490202e494f1f09e61d83407afe6f7f08f09d43fbfaf087818c203f`; the
  scheduler source is
  `e59383000eadcf8a889879e7b56467ecceda8042ca48aa812ec424e893cd0dcb` and
  WorkRepository source is
  `d48a9488dc037bf5281ef138646cd3ac85e2ec05d64524050b3a0a557bab1916`.
  Evidence:
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/`
  `scheduler-release-lock-final4-e593830.log`.
  The corresponding lock-order REDs reproduced PostgreSQL deadlocks before
  these changes. The focused current-source runs now pass, but the full backend
  remains a separate gate.
- A fresh isolated code-only Graphify extraction used the frozen source copy
  whose manifest SHA-256 is
  `3981d613f53cc3ec593d273c011614fc07cceaa98c29ba35f6238f677c933147`.
  It indexed **1,848 code files** into **46,767 nodes and 115,142 edges**, with
  no clustering; five files marked potentially sensitive were skipped. Graph
  SHA-256:
  `83cb7b3cc48bb7a21cc7eac531e26ed8ec19b5d0b9b4fcda3b718a1631c3c1e9`;
  manifest SHA-256:
  `9c954e5ddddd390ad689118580fafd5de13cc66b1e58c5641945518ebfc75c5d`.
  The graph and extraction log are at
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/final-static-r1/graphify/graphify-out/graph.json`
  and `.../logs/graphify-extract.log`.
  Diagnostics reported **8,540 dangling-endpoint references**, **zero missing
  endpoint edges**, and **1,823 directed same-endpoint edge collapses**. In
  particular, Graphify did not resolve the scheduler-to-Work row-lock calls
  across files, so the lock order above was checked directly against source and
  native interleaving tests. These are graph-index limitations, not production
  failures. The workspace `graphify-out` hashes were unchanged.
- Static verification on that isolated copy passed: Black reported **1,308
  files unchanged**, isort exited 0, pinned Python 3.12.13 `python -m mypy`
  reported no issues in **424 source files**, and Import Linter analyzed **427
  files / 1,811 dependencies** with **five contracts kept and zero broken**.
  Logs are `black-check.log`, `isort-check.log`, `mypy-src-pinned.log` and
  `lint-imports.log` under
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/final-static-r1/logs/`.
  These static results complement, but do not substitute for, the separately
  recorded project composition check or the complete backend run.

### T053 audited legacy-memory write failure boundary — 2026-10-12

- The additional PostgreSQL commit-failure regression closes the earlier audit
  review gap. It defers a real foreign-key violation until commit of the
  completion receipt, then verifies SQLSTATE `23503` is caught internally,
  rolled back and not exposed in the caller-facing error. The legacy side effect
  has already occurred, so the API reports a sanitized potentially-partial
  outcome; only the durable `authorized` receipt remains, the invalid Work
  result is absent, and both owned database connections are closed.
- The test is
  `test_completion_commit_failure_rolls_back_and_reports_partial_outcome` in
  `test/services/test_legacy_memory_audit.py` (SHA-256
  `290ebe3574ec8b85eb7227e4c9b7d7fccd70d63ee495815bce34d52ed58f4d66`). Its
  real PostgreSQL focused run passed **1 test**; the complete audit module passed
  **23 tests**, and the neighboring memory-policy/provider selection passed
  **46 tests**. Receipts are under
  `/home/felni/.cache/caos-spec021-evidence-20261012/t053-audit-fence/logs/`
  (`commit-failure-focused.log`, `legacy-audit-final-r2.log`, and
  `memory-policy-graph-final-r2.log`). The prelaunch receipt binds Python
  3.12.13, `AUTOSTART=0`, and the actual workspace module origins and source
  hashes for the test, repository and policy. No source change was required by
  this review.
- At this earlier review cut, Runtime's T054 migration/catalog batch and the
  final pinned static and Graphify checks were pending. The later T054/final
  static addendum below supersedes that status. The full backend, guest, Docker
  and SC-007 provider gates remain separate acceptance requirements.

### T054 catalog batching and final static/Graphify review — 2026-10-12

- I independently reviewed the frozen migration implementation
  `src/cli_agent_orchestrator/clients/postgres/migrations.py` (SHA-256
  `cda12c08da6df2a16d42941966b181253ffd4dda22d7ed623b13828ed1dcdb49`) and
  `test/clients/test_postgres_catalog_batch.py` (SHA-256
  `e07a391667188820ffc28b7df1cdc624ccab4aa230dd4cf87df6d5daf373815d`). The
  nine catalog SQL bodies match the previous reader byte-for-byte. The new
  read uses one `json_build_object` statement with ordered `json_agg(row_to_json(...))`
  category values, preserving the prior JSON representation rather than
  changing to JSONB. The test checks exact structure and serialized digest
  parity on main, auth and peer; one-statement execution; empty categories;
  borrowed repeatable-read transaction ownership; caller rollback after a query
  error; and missing-table failure. The focused native PostgreSQL batch passed
  **26 tests**. Its receipt and log are
  `/home/felni/.cache/caos-spec021-evidence-20261012/runtime/catalog-final-r2/`
  `t054-catalog-batch-evidence.json` and
  `.../artifacts/pytest.log`.
- The accompanying contention check passed **1 test** with ten contenders;
  the existing ten-second lock timeout and distinct queued/dispatched outcome
  remained intact. On the measured 71-table/two-version Work schema, catalog
  verification's median/p95 changed from **34.017/37.658 ms** to
  **30.774/33.115 ms**. These are focused implementation measurements, not a
  claim about end-to-end application latency.
- The final isolated source snapshot at
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/final-static-r2/`
  has a parity record with **10,138 entries on each side and zero missing,
  extra or changed entries**; generated `.import_linter_cache` files were
  excluded. Its workspace/snapshot/parity manifest SHA-256 values are
  `87bfa12a8520624f8028a317cf06c7bb33be08bf190e47d2ef2f259ef01561cf`,
  `a79d3cc1dc32a0172e73998a5373d6c2556d2a087ea5f7e341f753e261513b9b`, and
  `94002a7f5a74636bf64074fad96f4e6c6c57e94e48b0aa17eae753ef2d8613b1`.
- Graphify's code-only extraction over that snapshot indexed **1,849 code
  files**, **46,793 nodes** and **115,239 edges**, without clustering. The graph
  SHA-256 is `00618c94fbaf32d5dd69ff0f721c157a75501704b544f27a1686f35104779c56`;
  its manifest SHA-256 is
  `ae061e68090f516fc84e1625b665f05b9a8508316e28b3e944972b3c7c764242`. The
  corrected read-only multigraph diagnosis completed on that graph: it reports
  zero edges with blank endpoint IDs, 8,555 edges whose nonempty endpoint IDs
  are absent from the node set, and 1,823 same-endpoint edges across 1,689
  directed pairs that a simple graph could collapse; it reports no self loops.
  Graphify links `catalog_manifest()` to its
  query/normalizer helpers, `verify_component()` to that manifest, and
  `migrate_component()` to verification and manifest creation. I checked these
  paths directly against the source and native test calls; the dangling and
  same-endpoint diagnostics are limits of the generated impact map, not a
  substitute for source or runtime evidence. Five potentially sensitive files
  were skipped by extraction.
- Static checks on the same ext4 snapshot passed: Black left **1,309 files
  unchanged**, isort exited 0, and pinned Python 3.12.13 mypy reported no
  issues in **424 source files**. `uv run --offline --locked --active
  lint-imports` used the private snapshot venv's interpreter at
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/final-static-r2/`
  `lint-venv/bin/python`; `importlinter` resolved from that venv's
  `site-packages`. It analyzed **427 files / 1,811 dependencies**, with **five
  contracts kept and zero broken**. The separately required root command
  `project-composition-check "$(cat .ai/project-name)"` also exited 0 with the
  same file/dependency/contract counts; its log is
  `root-final-checks/project-composition-check-t054.log` under the evidence
  root. The isolated check logs are in the `final-static-r2/logs/` directory.
- This review found no new T054 composition defect. The complete current-source
  backend run, Guest/T097 acceptance, current-source image/install/import, and
  SC-007 all-provider acceptance remain independent gates. This addendum does
  not turn the overall review into a release sign-off.

## Verdict

**IN PROGRESS.** The bounded source review, current Graphify extraction, static
checks and focused PostgreSQL regressions recorded above passed. The complete
current-source PostgreSQL/backend suite, T097/QEMU, fresh current-source
application-image acceptance, and SC-007 all-provider acceptance still block
sign-off. No global PASS is claimed.


## Current R3 composition and acceptance status — 2026-10-15

**Verdict: IN PROGRESS.** The current evidence does not support aggregate
PostgreSQL migration sign-off or a release-completion claim. This addendum
supersedes older pending/current summaries only where it records a later
result; prior run receipts remain unchanged.

- **Persistence boundary:** The bounded review of the frozen R3 source found
  no active SQLite persistence route. Current configured persistence paths use
  PostgreSQL targets; `sqlite-dump.json` is rejected as a retired archive
  marker. The historical T020/Graphify description of remaining SQLite modules
  is stale relative to R3. This was a source-only review; its guard/tests were
  not executed and external AIPM stores were out of scope. See the private
  review receipt recorded in `validation.md`.
- **Image/runtime boundary:** The source-bound R3 application image built and
  its postchecks passed, with aliases and container configuration unchanged.
  That result does not cover installing or importing the image, restoring a
  PostgreSQL backup, or running an application against it. Those acceptance
  boundaries remain open.
- **QEMU resource and cleanup boundary:** The 91-case R3 run passed ten cases
  across two completed groups before the unchanged 45-minute deadline; its
  third group was incomplete and 81 cases were not completed. The marker DB
  and run artifacts remain preserved. Exact-container inspection later found
  no container, but that is not proof of complete lifetime cleanup. The prior
  80-case R2 QEMU result is historical and does not close the current 91-case
  QEMU gate.
- **Native Docker boundary:** The three native acceptance lanes did not reach
  pytest because preflight found 4,156 MiB RAM against the unchanged 6,144 MiB
  floor. No lane started, so there is no test result to count as pass or skip.
- **Provider/API boundary:** Codex R11's inner journey completed with two
  questions and answers; one ACK call acknowledged both questions
  (`ack_call_count=1`, `ack_count=2`), with one API restart and three
  stable redacted snapshots. Its original outer receipt remains failed because
  of the delivery-state predicate; the corrected predicate was independently
  recovered from that immutable receipt, with no replay. The later MCP
  diagnostic's first wait returned an unavailable category and omitted
  `isError`, leaving tool semantics unknown; the second wait and ACK succeeded.
  It does not establish transparent recovery of an in-flight wait across API
  replacement. Claude R10 produced
  no question or MCP call and failed without an identified cause. OpenCode
  R9/R10 did not establish a functional journey. R10's supervisor reported an
  ENOENT while reading owned-tree children; no inner receipt was produced and
  cleanup remains incomplete. Later marker inspection had unreadable process
  fields and cannot establish complete historical cleanup. At the last recorded
  audit, two external jobs were classified `RECONCILE_REQUIRED`; no newer state
  check is recorded, so their current resolution is unknown. Spec023's offline
  source-preflight fix passed, but its later actual
  attempt stopped in the shared process-inventory helper before a journey
  receipt. These results cannot be combined into provider acceptance.
- **NFR boundary:** Three scoped original cold-cohort tests satisfy their strict
  `<1 s` assertions. Exact internal elapsed times are unavailable, and the
  original outer/controller failure remains preserved. This does not establish
  the entire NFR gate or justify reporting synthesized latency numbers.
- **Composition and state boundary:** The two-file R11 diagnostic port was
  integrated at that checkpoint. Its corrected command-only validation passed **293 tests**
  with zero failures/errors/skips in **19.606 seconds**; the first invocation
  exited before test collection because `main/test/live_provider` was missing
  from `PYTHONPATH`, and no code change was needed for the corrected run. The
  subsequent main composition check passed **399 files, 1,775 dependencies,
  five contracts kept and zero broken**. The subsequent reviewed R13 fix makes
  explicit or invalid MCP error flags fail acceptance; absent/false flags keep
  prior behavior. Its final main validation passed **295 tests**, zero failures,
  errors or skips (pytest summary 18.18 seconds), with main origins verified.
  Composition again kept all five contracts, zero broken. No live journey was
  replayed by these offline checks. Global runner R4 remains inert; final
  source freeze and current-source `ALL` are still pending. Two older external
  records remain unreconciled without a fresh state audit. No complete
  external-state claim is made.

The complete R3 QEMU run, all native Docker acceptance lanes, final current-
source composition/`ALL`, successful provider journeys, install/restore gates,
owned-resource cleanup proof, and external-record reconciliation remain
required before aggregate sign-off. A container-not-found response, source
preflight, offline tests, or an earlier R2 pass cannot close those gates.

### Integrated fixture boundary checkpoint — 2026-10-15 17:15 UTC

The eight-file fixture/package correction is integrated in main, with no product
`src/` delta. Terminal doubles now use a fresh isolated `StatusMonitor` and the
real integer turn contract; they no longer substitute booleans or mutate the
shared singleton. Provider fixture cleanup preserves the primary exception and
reports an unconfirmed HOME cleanup rather than asserting preservation after a
partial deletion. Existing ownership checks and test assertions remain intact.
The generated skill now matches its canonical PostgreSQL example.

The current-main focused selection passed 270 tests with zero failures, errors
or skips, and all eight integrated hashes remained unchanged after execution.
The structural composition check kept five contracts and broke zero, but was
run before this focused selection; final workflow signoff still requires the
ordered completion checks after the remaining work. The source review found no
new dependency-direction, persistence, API, message or migration boundary.

R4 actually completed its test phases with 17,629 passes, 53 failures and 237
skips. Its original postrun failure remains preserved; the separate stat-only
index qualification and missing-constant correction passed as supplemental
evidence. The memory SLO still fails at 1.2292846810014453 seconds. The later
cProfile, EXPLAIN and passing timeout diagnostics are scoped investigations,
not aggregate acceptance, cause attribution or permission to reduce checks.
Current native Docker/QEMU and real-provider limitations are recorded in the
matching validation checkpoint. No whole-resource cleanup or final signoff is
claimed.

### Additional integrated test boundaries — 2026-10-15 18:45 UTC

The OpenCode installation readiness check now waits for the configured agent
inside its original deadline; the full main module passed 27 tests, including
the native check. The supervisor FD test controller now uses the source-root
argument and verifies the imported module origin; all three main tests passed.
The real provider matrix sends Spanish and Japanese Unicode in its run-step
instruction; all 79 contract tests passed. These are test changes with no
product `src/` delta or new dependency direction, schema or persistence API.
The earlier FD timeout and the external Unicode/handoff errors are not
reclassified as fixed by these focused results.

Private launcher supervision remains a blocking operator boundary: both R8
and R9 stopped before an inner Spec023 receipt, and a further candidate needs
review of procfs disappearance, late adoption and cleanup identity checks.
Its private location does not count as an integrated application change.
Scoped PostgreSQL recovery for each failed attempt removed only its proven
three databases and six roles; it does not prove whole-process cleanup.
Four passing tmux diagnostics do not identify the original timeout causes.

The R5 manifest predates these edits. A fresh source freeze, successful live
provider journeys, complete current-source tests, the subsecond SLO and the
outstanding Docker/QEMU gates remain necessary. Ordered final composition,
diff, graph-refresh and durable-knowledge decisions remain pending; this
checkpoint grants no aggregate signoff.

### Inbox identifier boundary review — 2026-10-15 19:43 UTC

The changed surface comprises only the Spec023 phase-A row validator, the
restart comparator and their regression tests. Source-verified neighbors are
PostgreSQL InboxModel's integer primary key, InboxMessage JSON serialization
and the inbox HTTP response. The test contract now accepts the native
positive integer while preserving immutable identity/body fields and the
existing status vocabulary. No application schema, transaction, event, retry,
timeout or resource lifecycle behavior changed. Regression coverage includes
boolean/string/nonpositive rejection and altered identity/body/status.

After the main module's 33 passing tests, the project composition check passed
with five kept and zero broken Import Linter contracts (399 files, 1,775
dependencies). The complete integration diff was reviewed. Graphify refresh
is unnecessary because no product structural edge changed; prior Spec023
impact/source analysis remains applicable. This checkpoint preserves durable
contract and validation conclusions; raw private evidence stays outside main.
Black also fails on all three reconstructed pre-fix files. Four formatting
hunks introduced by this correction were normalized separately, with parsed
AST equality and a fresh 33-test passing module run. Existing unrelated
formatting differences remain; a whole-file formatting pass is not claimed.

Verdict: **PASS WITH RISKS for this isolated contract correction; aggregate
acceptance remains incomplete.** R10's real delivery/restart boundary was not
validated. The later OpenCode question failure and tmux cleanup uncertainty
remain separate unresolved boundaries, as detailed in validation.md. A fresh
source freeze and complete current-source suite are still required.

### Child interpreter environment review — 2026-10-15 20:07 UTC

The generated Spec023 MCP command and its exact configuration validator now
require Python `-B` plus an explicit bytecode-disabled environment. The
regression executes the generated command against a fake local package with
a fresh HOME, rather than relying on the test parent's environment. It proves
the child interpreter flag and absence of compiled files; no provider, real
server or database is involved. The main module's 33 tests and the subsequent
five architecture contracts passed. Root and independent ECC source reviews
found no scope, tool-permission or credential propagation relaxation.

No product import edge, API, schema, transaction or event contract changed;
Graphify refresh is unnecessary for this startup-only harness correction.
This durable checkpoint records the boundary and limited evidence. Verdict:
**PASS WITH RISKS for the correction; aggregate acceptance incomplete.**
The historical writer is unknown, and no live success follows from this unit
result. Full-source/live gates and earlier recorded limitations remain open.

### Prompt and bounded-ready-read review — 2026-10-15 21:01 UTC

Root and independent ECC source reviews approved two test-only corrections:
Codex's sender prompt now accepts the transport's exact turn-receipt envelope
and requires that receipt in its reply; the T097 ready handshake reads a
newline frame within the existing 30-second budget. The former preserves
non-Codex prompt text, exact message bodies, receiver and one call per phase.
The latter preserves the security attack/assertions and existing child
cleanup, uses binary JSON input consistently, and rejects incomplete frames.
The complete main Spec023 module passed 34 tests without deselection or skip;
the main partial-line regression passed once. Each was followed by the
project composition check: five kept contracts, zero broken. Added formatting
was normalized without reformatting unrelated baseline code.

These changes introduce no product API, schema, event, transaction or import
direction change. Product Graphify refresh is unnecessary; the source and
private reproduction receipts establish the local boundaries. The historical
QEMU cause and live Codex phase-A cause remain unproven. Provider failures,
source-qualified ordinary-build evidence and global/SLO/resource limitations
are recorded in validation.md; none is replaced with a passing claim.

A separate private AG-UI diagnostic candidate was rejected before execution:
its file lock could block indefinitely and its hook did not propagate through
the real MCP child environment builder. Its 35 fake passes were insufficient.
It is not a delivered fix and changes no main behavior. Verdict: **PASS WITH
RISKS for the integrated corrections; aggregate acceptance incomplete.**
