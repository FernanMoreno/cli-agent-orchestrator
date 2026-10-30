# Implementation Plan: Persistencia PostgreSQL

**Branch**: main conservada | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

## Summary

Port completo de tres stores a PostgreSQL nativo, instalación gestionada/externa y backup/restauración PostgreSQL. El alcance actualizado retira toda lectura, importación, empaquetado, dependencia, CLI, fixture y prueba funcional SQLite; no se convierte estado SQLite. Mantener atomicidad e identidad de Work/interactions, autorización, recibos históricos y AG-UI. [research.md](research.md) resuelve decisiones.

## Active scope amendment — 2026-10-14

The user's explicit scope change removes all SQLite support from CAOS. Do not
ship or invoke a SQLite reader, importer, dependency, command/flag, packaged
fixture or functional test. Retired import commands fail before path access,
directory creation, installation locks or database connections. Unknown
existing state fails closed and remains unchanged; new installs keep their
ordinary flow without a generic warning or permission prompt.

Use PostgreSQL-native backups for relational recovery. Existing PostgreSQL
receipt/registry schema and rows stay immutable historical provenance; keep
installed PostgreSQL DDL, ledger entries and checksums unchanged. Preserve
modern OKF directory/tar workflows with SQLite-free payloads, and retire the
legacy `sqlite-dump.json` reader/format. T022-T027 are retired by the scope
change, not accepted as complete. Backend/App coverage floors remain 87%/90%.

## Technical Context

**Language/Version**: Python >=3.10, TypeScript frontend existente.
**Primary Dependencies**: SQLAlchemy 2 existente, psycopg 3 binary nuevo; FastAPI/Pydantic existentes.
**Storage**: PostgreSQL 18 estable por imagen digest; namespaces main/auth/peer. No existe soporte SQLite.
**Testing**: pytest y PostgreSQL Docker real; fixtures por namespace privado, carreras multi-conexión, web/apps/Playwright, Docker/host, cobertura, arquitectura y composición. No se usa SQLite como backend auxiliar de pruebas.
**Target Platform**: Linux/WSL Docker; CAOS server/CLI.
**Performance Goals**: writers independientes pueden progresar concurrentemente; proyección snapshot coherente y página <=100. Medir carreras y reader durante writer; no afirmar carga sin benchmark.
**Constraints**: fuente y operator resources preservados; cero fallback SQLite; secretos sanitizados; efectos fuera de transaction con claims durables; no dual-write.
**Scale/Scope**: aprox.141 tablas y tres stores PostgreSQL; el inventario histórico de módulos SQLite define el alcance de eliminación, no un formato admitido.

## Constitution Check

- Evidencia/source: inventario real y Graphify contrastados; tests reales requeridos.
- Preservación: git status inspeccionado, rama main no cambiada; dirty016/Graphify/spec017–020 preservados.
- Diseño: spec/research/plan/tasks/Graphify antes de implementación; TDD foundation y cada frontera.
- Composición: transacciones, permisos, CAS, locks, secuencias, recovery/install revisados.
- Cierre: tests→composition-check→review→diff→Graphify decision→knowledge→verification. Sin signoff previo.

## Project Structure

- clients/postgres/: configuración/targets, conexión nativa, Record/errores, owner de migración/pg_catalog y baseline por componente. Los recibos PostgreSQL existentes se mantienen sólo como procedencia histórica, inmutable y de lectura.
- clients/database.py: ORM PostgreSQL y orden explícito de terminales; contratos no SQLite existentes preservados.
- clients/work_repository.py + services/work_*: target/store ID, PostgreSQL DDL/locks/CAS y sidecar roots fuera del DSN.
- clients/interaction_repository.py + services/interactions/: mismo main transaction, native sequence/RETURNING, constraints/triggers y snapshot estable.
- clients/browser_auth_repository.py + services/browser_auth.py/browser_setup.py: auth scope/role, epoch/locks/bootstrap y revocación.
- services/local_peer_registry.py: PG peer namespace, host/uid scope, liveness/leases sin SQLite.
- services/workflow_journal.py, approval_store.py, private_plan_snapshot.py, workflow services/runtime channel: port SQL/upserts/permission checks.
- scripts/personal_deployment.py/docker_install.py + docker/personal/: PG provisioning, secretos, backup/restore/cutover/compatibility.
- test/conftest.py y test/postgres/fixtures.py: PG real aislado por test/worker; no se crean ni empaquetan fixtures SQLite.
- specs/021-postgresql-persistence/: diseño/contracts/tasks/evidence.

## Implementation Gates

1. Foundation contract fijado antes de puertos en paralelo: DatabaseTarget/Connection/Record, main identity, %s native placeholders, migration registration y locks.
2. Canonical PG baseline por componente + verificación estructural; nada de sqlite_master/PRAGMA/rowid en runtime PG.
3. Main/Work/interactions comparten conexión y transaction; snapshots REPEATABLE READ READ ONLY.
   La caché de proyección de memoria particiona el owner con `DatabaseTarget.identity` no secreta además de la raíz de wiki y los parámetros normalizados. Targets PostgreSQL distintos no comparten vistas aunque las raíces de archivos coincidan; facades con la misma identidad durable sí conservan el hit de caché.
   La asignación de intentos de workflow serializa por la fila existente `workflow_run`: primero se valida y bloquea esa fila, luego se inspecciona/bloquea `workflow_run_step`, se calcula el siguiente intento y se persiste el contrato y estado en la misma transacción. Las rutas script y YAML usan el mismo orden run→step; no se toma un lock global ni se repiten efectos externos.
   La proyección de un resultado Work usa el mismo lock de `workflow_run` como punto de serialización. Tras obtenerlo vuelve a consultar el marcador `(binding_id, accepted_result_id, content_hash)` antes del CAS: el marcador exacto permite un reintento idempotente y un resultado distinto se rechaza.
   `cleanup_owner_lock` cubre la apertura de conexión y la adquisición del advisory lock con la frontera de fallo: sólo cierra una conexión creada, no intenta desbloquear una adquisición fallida y no ejecuta la sección crítica si PostgreSQL no concede el lock.
4. Auth/peer puertos y seguridad; filesystem permisos siguen para secretos/artefactos, PG grants verifican la autoridad SQL.
5. Instalación/backup/restauración más tests contra PG real; limpiar únicamente recursos propios.
   Restaurar en un destino separado, verificar estado completo y conservar los recibos PostgreSQL históricos antes de activar.
   La configuración personal del planificador pertenece a la instalación normal
   explícita, verifica el esquema, no crea DDL y conserva una política existente.
   Sus regresiones verifican el destino antes de instalar y disponibilidad del
   planificador después de instalar; no alteran los checksums del ledger PostgreSQL.
6. Suites completas y cobertura original conservada, composición, diff y actualización Graphify aislada.

## Native memory audit performance

The full PostgreSQL regression exposed repeated Work catalog verification in each
legacy memory audit phase. A dedicated audited write transaction will connect to
the configured target, begin, acquire the shared store fence, and verify the full
Work schema before yielding. By default it commits or rolls back and closes the
direct connection it opened. T055 defines one opt-in pooled exception for the
private legacy-memory audit repository; that lease is returned before effects.
Only the legacy audit append path uses this context; existing Work transaction
callers retain their behavior. Keep all intent and terminal audit events, the
commit before effects, failure classification, and the verification after waiting
for the store fence. Do not cache verification results across phases or requests,
and do not increase the existing graph performance budget. Native tests must
prove ordering, corruption rejection, rollback, and unchanged privacy and
append-only constraints.

## Fresh catalog verification under concurrent dispatch

The ten-contender dispatch regression exposed a grant lock convoy amplified by
repeated catalog round trips. Batch the nine independent structural catalog reads
into one fresh SQL statement with explicit ordering for every result array. Keep
the existing manifest normalization, completeness checks, errors, digests and
migration checksums. Do not cache authorization or catalog results, change grant
or job lock modes, or increase timeouts. Before acceptance, compare the old and
batched manifest and digest against real main/auth/peer components; test schema
drift, empty categories, nested routine definitions and caller-owned snapshots,
then measure verification and rerun the unchanged concurrent dispatch regression.

## T055 — bounded reuse for legacy memory audit connections

The unchanged one-second memory round-trip budget remains the acceptance gate.
The pool is an opt-in path used only by the private WorkRepository created in
`MemoryService._legacy_repository()` for legacy memory audit rows. Ordinary
`WorkRepository` instances and all other audit writers keep the direct
connect/close behavior from T053. The memory audit pool is process-wide so
short-lived API `MemoryService` instances share it, but it is not exposed to
general WorkRepository transactions or SQLAlchemy caller-owned transactions.
Use a private connection-provider seam and a memory-audit-specific repository
factory; do not add a public constructor flag or general WorkRepository pool
mode. The manager's checkout returns a lease-scoped `Connection` wrapper, not a
raw SQLAlchemy or psycopg connection.

Each audit phase still checks out one exclusive lease, begins a new transaction,
acquires `work_store_control FOR SHARE`, performs the full fresh Work schema,
role and identity verification after that fence, appends exactly one audit
phase, and commits before returning. The lease ends before the operation yields
to memory filesystem effects. The authorized and terminal audit phases remain
separate durable commits; there is no verification cache, audit batching or
retry after an uncertain commit.

The process manager has a hard limit of four physical sessions used by the
private legacy-memory repository, across its initialization/read-snapshot
verification and audit connections, including active, idle and retired
generations. T149 allows independent same-profile leases to use this existing
global budget; sequential operations still reuse an idle connection. General SQLAlchemy
metadata sessions and generic WorkRepository readers are outside this scoped
counter. Checkout has a bounded wait; if all four sessions are active or
retiring, the private memory operation fails closed. Only idle entries can be
evicted. Initialization uses a fresh `REPEATABLE READ READ ONLY` transaction
and full verification on the same bounded lease; it does not create or migrate
schema. Before each checkout, the manager
resolves the target again from its current private database configuration; it
does not trust the possibly cached `DatabaseTarget` held by a short-lived
`MemoryService`. The resolved scope, logical/physical target identity, runtime
role, endpoint, schema, TLS mode and other explicit connection parameters define
the profile. If the repository's cached logical/physical identity, scope or role
no longer matches the newly resolved target, the repository is stale and fails
closed; it is never silently routed to another store. A secret-file rotation
with the same target and role retires the prior profile and reconnects with the
new credential. The profile also covers the private secret-file identity and a
process-keyed HMAC of password bytes; neither passwords nor profile digests are
logged.

The private pool sets `plan_cache_mode=force_custom_plan` after `RESET ALL` and
includes that value in both checkout and return session validation. The setting
is confined to the opt-in memory-audit pool; direct connections and generic
WorkRepository users retain PostgreSQL's configured default. A bounded
PostgreSQL comparison over the exact nine-family catalog statement produced
one manifest digest for `auto`, `force_custom_plan`, and `force_generic_plan`;
the custom-plan arm had the lowest median. This is a session planning choice,
not a verification cache: every initialization snapshot and audit phase still
runs the complete fresh verifier.

The profile captures effective libpq environment and TLS inputs without
logging their values. Explicit connection parameters override their matching
libpq defaults. The opt-in path rejects any nonempty inherited `PG*` setting
that is not explicitly represented by those parameters, including service-file
selection, `PGOPTIONS`, alternate host-address routing and custom SSL
certificate, key, root-certificate or CRL paths. For every TLS-enabled mode, it
includes the effective HOME-derived (or Windows `%APPDATA%`-derived) default
identities and keyed content
fingerprints of `root.crt`, `root.crl`, `postgresql.crt` and `postgresql.key`
when present, since libpq may use these for server verification, revocation, or
client identity. With `sslmode=disable`, the manager does not inspect TLS files.
It does not inspect unrelated operator files. A change to an accepted profile
input retires the old profile. An already checked-out transaction may finish,
but its connection is discarded on return and the retired physical session
continues to count toward the cap until actually closed. The creator and
checkout both compare the resolved profile before application SQL begins.

The implementation uses one native connection manager rather than one
SQLAlchemy `QueuePool` per profile. A per-profile pool cannot enforce the
single process-wide cap across profiles, connection opens in progress, and
retired generations that still have a checked-out lease. The manager owns that
cross-profile reservation and retirement state; the lease facade keeps the
physical psycopg handle private. It does not expose a general pool or alter
ordinary WorkRepository connection ownership.

Every pooled lease is exclusive and logically invalid after return, including
saved connection, raw and cursor references. Checkout starts from an idle
connection and reapplies the target's role/session settings; a successful
return is allowed only after commit, an idle-state check and exact target
session validation. Any phase error, cleanup failure or uncertain commit
invalidates the physical connection; no failed lease is reused. In a forked
child, the manager replaces each inherited socket descriptor in the child's
descriptor table before dropping its libpq references, resets its local
capacity state, and rejects inherited lease wrappers by PID. The parent's
descriptors remain untouched. Explicit retirement never closes an in-flight
transaction; idle entries close before their capacity is released, and a close
failure continues to consume capacity.

Acceptance retains the existing full verification and audit counts, proves
target/profile isolation, exclusive checkout, bounded saturation, password
rotation, fork separation, lease poisoning, rollback/disconnect invalidation,
and disposal after API factory churn. The unchanged memory round-trip test must
pass in three cold repetitions below its existing one-second threshold, with
no new timeout or threshold adjustment. The per-process pool is deliberately
limited to the memory audit path; it does not promise to pool unrelated Work
connections.

## Delivery Strategy

### R12 fixture ownership recovery — 2026-10-14

Reproduce the boundary between function-scoped `postgres_targets` cleanup and
session-scoped `postgres_worker_targets` in their shared worker database. A
schema owner removes only its schema; only `_postgres_worker`, which owns the
whole disposable database, may terminate all remaining backends before dropping
that database. Preserve runtime disconnect invalidation and fail-closed audit
semantics. Prove sibling session survival and rerun the affected memory modules
together so the original fixture ordering is exercised. Update obsolete test
injection seams to the actual private connection class and keep owner-resolution
overrides alive throughout each operation; do not disable target validation.

### T057 measured cumulative verification optimization — 2026-10-14

The isolated cumulative prototype passed three cold repetitions of the strict
memory round-trip assertion, with visible call durations 0.97 and 0.98 seconds.
Port the measured changes into `clients/postgres/migrations.py`: bounded reuse
of the pure constraint text normalization, a shared materialized namespace and
relation selection for the nine catalog families, and batched role reads.
Keep component identity and history reads separate so an identity rejection
precedes errors from an absent or inaccessible ledger. The prototype's tiny
component-read saving does not justify changing that error precedence.
Build module-owned fixed SQL once; bind target values
for every execution. Keep the standard JSON decoder and migration statements.
Before acceptance, test exact uncached/cached normalization parity and bounds,
full old/new manifests and digests for all three scopes, corruption and error
precedence, borrowed transactions, and the audit verification/commit counts.
Repeat the three cold NFRs against actual source without prototype patches,
then rerun the failure-module group and global gates.

### T058 first-operation import boundary — 2026-10-14

The private prototype imported WorkRepository before timing, while the actual
operation loaded its mandatory audit dependency lazily. A private source-only
candidate moves that import to the MemoryService module imports and passed
three cold runs. Fresh-process import timing shows about 20.8 ms added to
startup; importing and calling the disabled path opened zero sessions and
resolved zero target configurations. Port only the import boundary, retaining
repository construction/initialization and all checks inside the authorized
operation. Test zero import/disabled storage effects, import cycles, existing
pool/fork behavior and the actual-source cold NFR after formatting. Do not add
the rejected checksum cache or change verification counts.

Implementar por límites sin publicar versiones parciales: foundation→main/Work/interactions/auth/peer→workflow/runtime→import/deployment→composición/aceptación. No cerrar hasta cubrir inventario y pruebas. Revisión de contratos antes y después de port.

### T059 native cursor type boundary — 2026-10-14

The configured full mypy check reproduced six `no-any-return` errors across
WorkRepository, work_mcp_proxy and work_provisioning. The first incorrect
boundary is `ConnectionCursor.fetchone() -> Any`: the native facade explicitly
sets `record_factory`, but the new lease-compatible protocol loses that row
type. Correct the shared cursor contract rather than suppressing errors or
casting service returns. Preserve raw cursor customization, all runtime SQL
and lease lifetime checks. Verify source/Graphify edges before implementation,
then run full mypy and the native/leased cursor contracts. Amend the not-yet
launched R13 snapshot explicitly, preserving its initial manifest and checking
all unchanged entries and Git metadata before the final freeze.

Honest `Record | None` typing also exposes existing slice annotations and six
unchecked fetch results. Represent the existing tuple slicing behavior in
Record's annotation, accept its actual row type in event projection, and guard
absent aggregate/history rows before effects. Preserve YAML corrupt-history
classification and use SchemaMismatch for a missing bootstrap version row.
Queries and successful results remain unchanged; verify the native journal,
registry and borrowed/leased cursor paths alongside full mypy.

### T060 replacement-target startup regression — 2026-10-14

Source review found that the migrated startup replacement test creates a new
engine for the same session-owned target, unlike its former separate SQLite
database. Use the existing owned `postgres_targets` fixture for a distinct
replacement, resolve its configuration throughout both lifespan calls, and
prove the previous target remains intact. This restores the regression's
original isolation requirement without changing production behavior. Preserve
the four repaired identities, second-start idempotence and forbidden model/
network assertions; run the complete startup test module before final freeze.

### T061 dispatcher qualification — 2026-10-14

## T061 — R13 Work admission lock scope

### Evidence and first incorrect boundary

The R13 PostgreSQL log records `LockNotAvailable: canceling statement due to lock timeout` on `SELECT * FROM work_grants WHERE id=$1 ORDER BY revision DESC LIMIT 1 FOR UPDATE`. The call originates in `WorkAuthority._load()` during `WorkContracts._revalidate_order()`, reached once per queued candidate in `WorkAdmission._eligible()`. `_eligible()` currently obtains exclusive grant-chain and job locks before its `work_path_probe` savepoint, then keeps all successful candidates' locks until the preview or commit transaction ends. Ten dispatchers therefore serialize on grants/jobs across the entire queue scan before the scheduler aggregate capacity check.

### Lock and state protocol

1. In `_eligible()`, open a candidate savepoint before authority revalidation. Include contract/authority revalidation, backend and registered-delivery checks, and the existing path-reservation probe. On every outcome, rollback and release the candidate savepoint. Keep successful bindings only in the local eligible map. If authority is invalid, rollback/release first, then run `_retire_unexecutable()` outside the candidate savepoint so the terminal transition and queue withdrawal remain durable. Do not short-circuit candidate cleanup when held capacity is full.
2. Factor the existing `_claim_next()` selection calculation into a shared read-only selector. `_preview()` uses it as a hint and MUST NOT call the mutating claim path, acquire the scheduler aggregate lock, advance dispatch sequence, update a queued row/item, append an event, or run event foreign-key checks. The selector retains the current capacity, eligibility, priority-aging, round-robin frontier, FIFO, budget, expiry and dependency rules. Its unlocked preview may become stale; commit is authoritative.
3. In `_commit_dispatch()`, revalidate the preview binding under the existing exclusive grant/job authority fence before taking the scheduler aggregate. The locked claim path computes the current selection using the shared selector and compares the selected attempt with the expected preview before any policy/request/event write. If the selected candidate differs, raise the existing retry signal with no claim/event mutation. Preserve the default behavior of public scheduler claim callers that do not supply an expected candidate.
4. For a matching candidate, retain the current locked capacity and queued-row CAS, then reserve paths, run registered-delivery identity checks, transition to `sent`, issue the credential and perform all existing final fence checks in the same transaction. A path conflict or stale fence rolls back; it never authorizes dispatch from the earlier probe.

Do not switch grant/job row locks to shared locks. The sent transition later takes the job row `FOR UPDATE`, so shared locks held by concurrent eligibility scans could deadlock with a dispatcher holding the scheduler aggregate while upgrading its selected job lock. Preserve the existing job-before-scheduler lock order used by enqueue and replacement release. Do not solve the observed convoy by raising `lock_timeout` or weakening the ten-process/two-slot assertion.

### Native RED and GREEN coverage

Use a new focused native PostgreSQL integration module, with two eligible candidates on distinct jobs and grants and transaction/barrier instrumentation:

- **Candidate scope**: run `_eligible()` inside an open transaction, pause after it returns, and from a second connection acquire `FOR UPDATE NOWAIT` on each candidate's grant and job. Both should succeed after each candidate savepoint is released. The current implementation fails because both candidates' locks remain held.
- **Selected fence**: run `_commit_dispatch()` and pause immediately before its locked claim. A second connection must fail `NOWAIT` on the preview candidate's grant/job while succeeding on the nonselected candidate's rows. The current broad scan fails because it retains the nonselected candidate locks too.
- **Pure preview**: spy on `_claim_next()`/`_event()` and snapshot scheduler policy, queued request revisions/states, work-item revisions and event rows. `_preview()` must use the read-only selector and produce no claim/event calls or state changes. Add a deterministic native lock-order case for enqueue holding a candidate job before it requests the scheduler aggregate; preview must not take the aggregate and then wait on the event's job foreign key.
- **Full-capacity retirement**: occupy capacity, revoke a still-queued candidate's authority, run preview/cleanup, and verify the attempt is terminal and its queued request withdrawn. This guards against early full-capacity returns that strand invalid queue entries.
- Preserve and rerun the existing ten-process/two-durable-slot integration assertion. All ten contenders and the two-slot bound remain unchanged; no retry-timeout or concurrency-test relaxation.


### T061 review follow-up: concurrent retirement

The selector alone is read-only. Candidate path probes may execute provisional
reservation events inside their rolled-back savepoint, and invalid-candidate
cleanup remains durable. An empty locked selection preserves the existing
no-capacity/no-candidate no-op; a different existing selected attempt retries.

The independent review identified a real retirement race: the attempt and
request revisions are currently observed before acquiring the attempt parent
locks. Reproduce two callers retiring the same revoked request. Observe the
attempt through the existing job→item→attempt locking helper, then reread the
queue request under that job lock. An already-withdrawn matching request whose
attempt is terminal is an idempotent no-op; a matching held request is skipped
without changing its attempt or releasing its capacity. Inconsistent
state/identity and active writers remain fenced. Prove once-only transition/withdrawal events and
add a peer advisory NOWAIT check for candidate path-lock release.

### T066 fresh role planning and pool configuration — 2026-10-14

Final R14/T065 actual-source cold runs passed two of three: the first measured
1.007944114 seconds; the other calls were reported as 0.97 seconds. Preserve
that failing gate and immutable R14 source. A separate paired Work71 probe
found 14.920 ms saved across 21 actual role-query reads by preparing the
zero-parameter literal query. `EXPLAIN EXECUTE` measured planning at 0.009 ms
versus 0.471 ms for the bound force-custom plan. Earlier `EXPLAIN SELECT`
measurements did not establish reuse of the prepared SELECT itself.

The same probe found 10.571 ms saved across 42 pool configuration calls by
combining the unchanged utility sequence and final settings SELECT. All seven
settings, target/session identity, IDLE/autocommit and seven nextset advances
matched. A fresh private combined candidate passed the unchanged covered NFR;
its postrun observation found 21 generic role executions, one pure-SQL cache
miss and 20 hits, with the cache cleared before the timed operation. This is
prototype evidence, not actual-source acceptance. Private evidence:
`nfr-analysis/t065-margin-candidates-r1/run-02` and `run-05`.

Port only role-query construction/dispatch in the migration module and the
owned physical-session configuration method in the audit connection manager.
Quote all four schema positions; bound the pure rendered-SQL cache to 128 and
bypass unsupported/oversized keys. Keep the same live role/ACL/ownership checks
and ordered errors. Validate actual permission changes after preparation and
reuse across resets. Append the exact existing settings SELECT to the current
RESET/SET sequence, consume result sets using pinned Psycopg nextset semantics,
and perform the same tuple/info checks. Preserve command failures and pool
discard paths. Run native role/pool/transaction/drift/fault regressions, full
static/composition checks and three cold actual-source NFRs. Prepare R15 from
the immutable R14 source with exact manifest-entry copying and fresh main Git
attachment; retain all R14 failures. Do not cache authority/catalog data,
change timers/thresholds, skip resets or reduce audit verification counts.

### T065 exact JSON normalization and catalog digest — 2026-10-14

The actual-source T064 cold gate passed two of three runs; the failing body
measured 1.016984846 seconds. Separate covered Work71 profiling found the
exact `null` identity-key fast path saves about 18.1 ms across 21 checks;
catalog-only `check_circular=False` saves about 8.6 ms. Every paired normalized
value and digest matched. Role-query preparation and SQL sort changes did not
show enough gain and remain rejected; no checksum cache is added.

The combined private candidate passed the unchanged covered NFR (reported
call duration 0.98 seconds), with 21 fresh catalog normalizations/digests,
20 write commits and a separate read-only initialization commit. This is
prototype evidence, not actual-source acceptance. Evidence root:
`nfr-analysis/combined-nullkey-digest-r1` under the dated private evidence tree.

Port only the two migration-module operations. Preserve general `_digest`
unchanged, introduce an internal helper for freshly decoded/normalized catalog
JSON, and use it only immediately after `catalog_manifest` in verification and
ledger insertion. Catalog JSON is acyclic; normalization inserts existing
routine-definition references without creating cycles. No JSON loader
customization exists in the native source. Preserve serializer sorting,
separators, ASCII escaping, numbers and hashing. Prove exact behavior on
non-null and overloaded routine identities, Unicode/controls, shared acyclic
references and default circular-value rejection. Rerun applicable native
catalog/role/drift/lease boundaries, then reserve a quiet window for three
actual-source cold covered NFRs before final R14 freeze and full regression.

### T064 parameterless catalog plan — 2026-10-14

# Private experiment plan: immediate prepare for the catalog statement

## Source baseline

The source is the sealed R13 snapshot whose full manifest SHA-256 is `b4e9402e56d5ab2b58dfcf39f50cd8b7c0054334bfb6e5ed7032599015295c49`. Relevant source hashes and byte-identical private Graphify copies are recorded in `../artifacts/source-manifest.sha256`.

The existing `ConnectionCursor.execute()` protocol and `_GuardedCursor.execute()` already accept `prepare: bool | None` and forward it. The gap is one layer above: `Connection.execute()` and `_PooledAuditConnection.execute()` do not expose that keyword. The catalog query is currently a parameterized shared statement executed once per verification. The r3 candidate renders safe schema/table/family literals into a bounded, zero-bind SQL statement but still lets Psycopg's default prepare threshold defer server preparation until a connection has seen the statement repeatedly.

The full NFR uses the pooled connection path. `_PooledAuditConnection.raw` is a `_GuardedRaw`; its `cursor(row_factory=record_factory)` returns `_GuardedCursor`, whose `execute()` runs through `_LeaseState.call()` and forwards `prepare`. `_LeaseState` uses an `RLock`, so the private wrapper can hold one outer lease operation while the guarded raw view creates and executes the cursor.
The r3 zero-bind run kept 21 fresh catalog calls and exact digest parity but failed the unchanged NFR at 1.019805721 seconds. Its pool probe found no prepared catalog statement on call 5 and a generic plan from call 6 onward. Paired Work71 EXPLAIN planning fell from 3.135 ms for the bound baseline to 0.036 ms after the candidate was prepared. The r4 hypothesis is that `prepare=True` avoids the first five unprepared executions on the pooled connection, recovering roughly 15.7 ms of early planning work; only the single live NFR can establish whether that is enough.

## Candidate dispatch

The private runner wraps the two execute methods without editing the sealed source:

- `prepare is None`: delegate to the untouched native or pooled method, preserving the current call and Psycopg's automatic threshold behavior.
- Explicit preparation on a native connection: call `self.raw.cursor(row_factory=record_factory).execute(statement, params or None, prepare=prepare)`.
- Explicit preparation on a pooled connection: perform `self._raw_view.cursor(row_factory=record_factory).execute(statement, params or None, prepare=prepare)` inside `_lease_state.call(...)`. The raw view and cursor retain their lease checks and return a guarded cursor. The reentrant lock keeps cursor creation and execution in one serialized lease operation.
- The catalog manifest call alone uses `connection.execute(literal_statement, prepare=True)`. The SQL remains the r3 static literal composition; identity, history, and role calls remain sequential, live, and unchanged.

No SQLAlchemy connection, physical pooled handle, or cursor escapes the existing `Connection` / `_GuardedCursor` boundary. No `prepare_threshold`, `plan_cache_mode`, `RESET ALL`, or utility-statement behavior changes.

## Offline guard proof before PostgreSQL

A private test module will use a recording native raw cursor and a recording pooled raw cursor to verify:

- default `prepare=None` delegates to each original method and passes no new cursor keyword;
- explicit `prepare=True` retains `record_factory`, passes the original statement and normalized params, and forwards `prepare=True`;
- the pooled wrapper returns `_GuardedCursor`, executes inside a lease-guarded callback, and rejects use after lease expiration;
- the borrowed connection uses a per-cursor `record_factory` without assigning to or changing the owning raw connection's tuple row factory.

These tests are local mock tests only; they do not connect to PostgreSQL. The live NFR then provides the real borrowed/pool and prepared-statement evidence.

## Live measurement

After all offline parity and source-seal checks pass, run one pinned Python 3.12.13 pytest invocation against the sealed source and canonical PG fixture with the existing full coverage options and the unchanged NFR node. The runner:

1. Checks the R13 manifest, relevant source hashes, source origins, and absence of bytecode.
2. Performs paired baseline/candidate main/auth/peer digest comparisons before timing.
3. Confirms the main Work71 SQL bytes/hash and zero parameter types.
4. Clears only the candidate's bounded pure SQL-render cache before the timed call. It does not clear driver, database, or other caches and does not change timers.
5. Keeps only catalog-call and commit counts in the timed path. Prepared statement inspection and hash calculation occur outside the timed test.
6. Records whether the server prepared the query on call 1, prepared-plan counters through calls 1, 2, and 21, all 21 fresh catalog calls, 20 durable commits, nine-family parity, and the actual strict duration.

A failed strict NFR or any failed guard/parity gate rejects the candidate. This is one hypothesis and one measurement; do not retry without a materially different hypothesis.

## Risks and limits

- Explicit preparation may reduce repeated planning, but the catalog executor, `Record` row conversion, JSON normalization, and SHA-256 work still run on every check. Passing the timing gate is not assumed.
- Pool return performs `RESET ALL` plus fixed session setup. The prepared statement is expected to survive those settings because this experiment does not change reset logic; the run must confirm this on the real leased connection.
- This private run can decide whether to propose a source change. It is not production acceptance; any source port requires its own spec approval, native tests, and actual-source strict NFR evidence.

Product acceptance follows the source port; private prototype pytest exit 1
was caused by counting its separate read-only initialization commit as an
audit write commit. The original unmodified NFR assertion passed in that
prototype, but its post-call plan receipt was incomplete. No final acceptance
is inferred. Preserve the raw failure and prove all native source contracts.

## T067 — transparent capacity instrumentation

The first exclusive R15 capacity profile failed before capacity locking: all twenty operations rejected `prepare=True` at the test wrapper signature. An AST reproduction using the actual TimedConnection and catalog_manifest bodies raises the same TypeError before the underlying execute is called. The service and capacity test were unchanged from R13; T064 introduced explicit preparation. Correct only the test adapter signature/forwarding, prove omitted/None/True/False semantics, and retain all timing/workload assertions. R15 was interrupted after this definitive cause to avoid completing a known invalid long run; its interrupted receipt and partial logs remain. No product change is required. Validate the whole capacity module and then the complete R16 backend suite. The three actual-source R15 cold NFR passes remain byte-identical product evidence, subject to final source parity checks.

## T068 — managed PostgreSQL final-server readiness

The first final-R2 native lifecycle gate failed when role-bootstrap psql exited 2. Its exact stderr was suppressed and cannot be reconstructed. The pinned image entrypoint starts a temporary server with listen_addresses empty, creates the requested database, stops it, and then starts the final server. The installer polls pg_isready without host and therefore accepts that temporary Unix socket. A deterministic execution of the actual frozen function reproduces premature return; an in-memory candidate adding only explicit loopback TCP waits for final readiness. Port that probe change with bootstrap/timeout regressions; do not alter bootstrap SQL, owner, database, retries or timeout. Preserve the failed R2 gate and interrupted R16 run (no backend failure at 36%). Build a fresh sealed image and finish the updated native install, backup and isolated-restore Docker gates before launching the final complete backend suite; there is no SQLite import gate.

## T077–T079 — pending recovery-safety review

These findings refine the existing FR-009 native backup/restore and FR-018
rollback contracts. They remain pending and are prerequisites for T075 Docker
acceptance and T076 final gates; no current source behavior is accepted by this
plan entry.

### T077 — exclude transient PostgreSQL client credentials from backup

The native client helper creates private `cao/pg-client-*` directories under
the source deployment for one-shot `pg_dump`/`pg_restore` passfiles. A crash can
leave one behind. Backups must omit these transient credential files while
leaving the source tree untouched. Test both a stale scratch directory and a
normal client invocation: the archive must contain no passfile or scratch
directory, and the original source files and bytes must remain unchanged.

### T078 — preflight and compensate isolated PostgreSQL restore

Before the first `pg_restore`, inspect all configured main/auth/peer schemas and
all physical database groupings. If any target schema is present, reject before
writing to any target. Record the OID and owner identity of each schema created
by this restore. If a later group, bootstrap/verification, or browser-auth
invalidation fails, remove only schemas whose recorded OID and owner still
match. If a schema was replaced, ownership changed, or cleanup cannot prove the
identity, preserve it and report cleanup failure; never issue a broad schema
drop. Exercise failures after an earlier group has restored and prove the
source archive, unrelated schemas, and pre-existing destination data remain
unchanged.

### T079 — validate Docker ownership before rollback effects

The rollback path must inspect the current and previous container names and
validate both installation-root ownership labels before any stop, rename, or
start. A missing or reused name owned by another root must fail without a
container mutation. Tests cover both correctly owned containers, a foreign
previous name, a foreign current name, and absence of either name; verify the
source deployment manifest and all unrelated containers remain unchanged on
rejection.


## T080: cold restore maintenance imports

A fresh Docker restore process must not use the unpublished destination as its
ambient application home. Give only the restore administration container a
private, disposable tmpfs home owned by its configured UID/GID (0700). Keep the
explicit destination `--root`, selected PostgreSQL configuration, parent bind
mount, network and verified maintenance socket unchanged. Backup and other
administrative commands keep their existing profile environment.

Prove the import boundary with a fresh Python process and a browser-enabled
source configuration. Do not move or remove the destination-absence check,
weaken account verification, precreate the destination, or alter native DDL.
Run installer/deployment regressions and all three Docker gates before the
full backend/coverage gates. Retain the R4 port failure, R5 restore failure and
private cold-import diagnostic as failed attempts.


## T081–T083 — strict cold memory round-trip and complete regression closure

The unchanged `test_roundtrip_runs_under_one_second` test currently fails at
**1.1544596599997021 s** against `<1.0 s` in the root's fresh covered-main
reproduction. The receipt is `nfr-fix-r23/baseline-r1/logs/junit.xml`; the
matching private PostgreSQL v3 fixture is run `4f0d61880628`, container
`f84d3eb5d81a4cae77ffb8a3873fb7316f7494ff4f596b1713d4954fd482201c`. Its timed
body is unchanged: ten memory stores, scope resolution and index read/parse;
fixture setup is outside that interval. This is a confirmed RED. No new
optimization is accepted from the failure alone.

### T081 — reproduce and locate cost before code changes

Bind a repeat of the existing strict node to a frozen source manifest, pinned
Python, fixture/profile metadata, coverage options, worker/plugin set, exact
pytest collection, failure/skip counts and test-body duration. Do not warm the
MemoryService, repository, pooled session, prepared statement or target query
before starting the timer. Inspect the current routine-family query with
`EXPLAIN (ANALYZE, BUFFERS)` in a separate unscored session, recording planning
and execution cost and the relevant relation/row counts so explain overhead
does not contaminate the NFR timing. Preserve a failure as a failure.

The source path to inspect is `MemoryService._legacy_repository()` → private
`_LegacyMemoryAuditWorkRepository` → `WorkRepository._verify()` →
`verify_component()` → fresh `catalog_manifest()`. This path also includes
target identity and migration-history checks, runtime-role checks, bounded
audit leases, write fences and durable audit transactions. The focused
preimpact Graphify and source proof are in
`nfr-fix-r23/preimpact-graph-r1/artifacts/`; the global graph predates SQLite
retirement. Graph edges are candidate impact information and were checked
against the current Python and SQL source.

### T082 — conditional private candidate

Only if T081 shows that routine dependency selection is a material part of the
measured latency, compare the current `pg_depend` filter (five OR-connected
class-specific `EXISTS` branches) with a private scoped `target_objects(classid,
objid)` relation joined by both identity columns. The candidate maps only
constraints, `pg_attrdef`, indexes, policies and rewrite rules for the current
component relations. Keep the trigger `UNION`, `UNION` set semantics, routine
namespace restriction and current treatment of `objsubid`. Use a separate
unscored query-plan comparison; port only if plan and controlled elapsed time
show a material improvement.

Use test-first parity checks. Compare the exact old and candidate normalized
manifests and digest for all nine families on main/auth/peer and multiple
components, with empty categories, routine dependencies through each family,
triggers, overloaded/nested routines, constraints/defaults/indexes/RLS/rules,
schema drift and caller-owned snapshots. Keep identity/history/error order,
DDL, ledger/checksum bytes, 21 full checks, 20 durable audit write commits and
the separate read-only initialization commit unchanged. Do not cache catalog,
role, authority or drift results. If the measured plan does not justify the
candidate, retain source SQL and document the no-change finding.

### T083 — full-suite omissions and acceptance

After T081/T082, run the complete backend and Apps test selections with eight
workers and the existing coverage setup. Reconcile the collection manifest
against the intended selections and report passed, failed, skipped and
uncollected cases with reasons. Fix every actual failure; do not hide it with
deselection, exclusions, altered worker/plugin settings or a source/test
timing change. Run the memory NFR three times in fresh cold covered processes;
all three must measure `<1.0 s`. Backend coverage must remain at least 87% and
Apps coverage at least 90%. Execute host-gated checks when their real external
prerequisites are present; otherwise state the exact gate and missing
prerequisite as pending. Bind all results to before/after source seals. T083
provides evidence for T076; it does not close T076, whose architecture,
composition and aggregate completion gates remain required.


## T082 measurement outcome and T084 conditional PostgreSQL 18 constraint path

### T082 outcome — routine dependency rewrite rejected

The private planner comparisons on the native 71-table fixture returned exact
manifest parity, but did not establish a robust benefit for the proposed
`target_objects(classid,objid)` rewrite. The first diagnostic reported 21.298
ms original versus 20.306 ms candidate; the second reported 22.103 ms original
versus 22.929 ms candidate. These are unscored query diagnostics, not strict
round-trip results. Retire the rewrite: do not port it, change the existing
shared query, or describe it as an NFR pass. Keep the separate trigger UNION,
deduplication, `objsubid` behavior, and all current catalog SQL unchanged.

### T084 — measure and validate the PostgreSQL 18 `NOT NULL` hypothesis

A separate constraints-family diagnostic reported 8.198 ms, 1,407 rows, and
4,317 buffer hits. This scoped a hypothesis; the family cost alone did not
prove that synthesizing a `NOT NULL` definition would reduce the timed
round-trip. The final paired comparison is a no-go: parity held, but R1 was
22.310 ms original versus 22.460 ms candidate; R2 EXPLAIN was 39.728 ms versus
35.874 ms while its five interleaved reads did not support a benefit; and R4
EXPLAIN was 20.741 ms versus 27.301 ms with 12 interleaved reads showing no
clear gain. R3 failed with `SyntaxError` before tests and remains in the failure
history. No product source changed and no NFR pass is claimed; T084 closes as a
rejected experiment only.

The proposed candidate applies only to PostgreSQL major version 18 after the
live connection's server-version handshake. It may synthesize a definition
only for a fresh `pg_constraint` row whose `conkey` is exactly one-dimensional,
contains exactly one attribute number, and has lower bound 1, joined to the
matching live, non-dropped `pg_attribute`. Any missing, null, malformed, or
unsupported shape uses the existing `pg_get_constraintdef` path. PostgreSQL
16/17, unknown versions and future major versions use the unchanged query.
If SQL text is cached, the cache must be bounded and version-keyed; it stores
only rendered SQL text, never catalog or verification results.

The synthesized text must match PostgreSQL's native `NOT NULL` renderer byte
for byte, including `quote_ident` behavior and the native order of `NO INHERIT`,
`DEFERRABLE`, `INITIALLY DEFERRED`, `NOT ENFORCED`, or `NOT VALID` suffixes when
applicable. Verify native behavior against PostgreSQL 18 source:
[ruleutils.c (REL_18_STABLE)](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/utils/adt/ruleutils.c#L2395-L2470)
and [quote.c (REL_18_STABLE)](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/utils/adt/quote.c#L23-L31).

Use test-first parity checks for quoted and Unicode names, each supported flag,
renamed and dropped columns, inheritance and partitioned tables, and malformed
or unsupported key shapes. Verify byte-identical normalized nine-family
manifests and digests across main/auth/peer, multiple components, empty and
drifted schemas, plus caller-owned snapshots and the existing identity,
history, DDL, checksum, ledger, authorization and commit boundaries. Do not
modify `_catalog_manifest_queries` or cache catalog rows/results. Accept a
product change only when a controlled comparison shows a substantial
improvement in the unchanged FR-044 cold round-trip and three independent
covered runs remain below 1.0 second. Otherwise record a no-go and leave the
source unchanged. T076 and the 87% backend / 90% Apps full-suite floors remain
open until their existing acceptance gates pass.


## T083 current full-regression evidence

The historical local E2E aggregate collected 37 cases: 24 passed, 13 failed,
and none were skipped. Its failures were 8 approval direct-runner cases, one
script lifecycle `422 prepared_plan_required`, three tmux lifecycle initial-500
cases, and one YAML-internal tmux case. The initial tmux/YAML run had an
overlong private socket path (`ENAMETOOLONG`); this receipt remains in the
failure history. The revised 37-case scope is accounted as 36 local E2E cases plus one
prepared-node Docker selector, both green on source/test snapshot
`a88eef6b676705b4bff9f579d8b7dd197ab6f79971e19d80d8086de7e73d40e1`. The local
selection passed 36/36 with no failures or skips in 146.92 seconds; its receipt
is `nfr-fix-r23/service-r2/owned-service-r1/backend-run/local-e2e-r4/artifacts/pytest-local-e2e-r4.log`
(SHA256 `274fc43e9b60b9ae007fe83351a23235a4391f0837538f7179c709ff7ac2126c`). The
R6 prepared-node Docker selector passed 1 test in 85.23 seconds, verifying
approval/review, unprepared `422`, run-step `409`, exact replay, stable attempt,
artifact cleanup, frozen source, owner API, durable binding, snapshot, receipt
and restart hashes. Its receipt is
`interaction-e2e-r6/t020-prepared-docker/prepared-selector.log` (SHA256
`796d108f275c16a31cf5009f5308589d1db51f884865795c41f8db818dcd82d2`). This
closes the local E2E omission category, but not the complete T083/T076 gates.
The removed script-parameter selector no longer exists; R3's replacement runner
exited 5 with zero collected tests at a stale node and remains a failed receipt.
The focused R3 T019/T020 selectors passed 14 tests with product source
unchanged.

The source-bound Apps run passed exactly 92 tests across 12 files on 2 workers at
478/531 covered lines (90.01%) in `frontend-final-r4`. The source-bound Web run
passed 504 tests across 45 files; TypeScript and Vite builds passed, and all
five build files matched main (`web-final-r2/final-receipt-r2.json`, SHA256
`1209876fcded57310a57c0964a5598a1f61154ce9cd033ce7516a45e8f2bd0c9`). Source-guard findings were zero before and after (not a test count); 13/13 provider CLI binaries were visible on PATH (availability only, not authentication or live-generation evidence). A separate T097 selector
slice has 5 passes and still has a partial R3 QEMU result recorded in the final acceptance update. R21 is the latest completed full backend run: 17,390 unique selected nodes, 17,252 passed, 113 skipped, and 25 unique failed node IDs. Its phase summary reports 15 FAIL and 12 ERROR events (27 failure-phase reports); two nodes failed in multiple phases. The run exited 1 during postrun verification. Coverage measured 67,793/77,816 statements = 87.1196155% with branch coverage disabled: above the line floor, but not a passing full-suite gate. Older R13 coverage was 68,979/79,530 = 86.733308%. The final R3 three-run strict receipt failed all three unchanged `<1.0 s` checks at 1.0844142050045775 s, 1.0654824020020897 s, and 1.0055572290002601 s; the source and HOME guards passed and no prewarm was used. An earlier diagnostic body reported PASS without a logged duration and is not acceptance evidence. T097 selector evidence is 5 passed; the R3 QEMU run completed partially and is recorded in the final acceptance update. T088's actual Linux PostgreSQL/Cargo gate passed; macOS matrix execution remains pending. The revised 37-case scope has not been run as one aggregate. T083 and T076 remain open. The R6 prepared-node run closed the previous reader-owner fixture omission.


## T085 — sanitizer dependency split: focused implementation complete

The source change is limited to moving the existing four-regex, standard-
library sanitizer into `src/cli_agent_orchestrator/services/log_sanitization.py`.
`audit_log.py` imports that leaf directly, and `wiki_compiler._sanitize_for_log`
re-exports the same callable for compatibility. Sanitizer behavior and
truncation remain byte-identical; audit append ordering and memory scoring were
not moved.

Focused verification passed: 137 tests passed with 3 warnings in 75.77 seconds
for the sanitizer test and four caller modules; the complete Kimi module passed
29 tests in 16.44 seconds. The focused result closes the T085 implementation
and caller contracts. It is not evidence for the three cold FR-044 runs, the
full backend suite, or T076; those aggregate gates remain open.


## T086 — conditional compact catalog-row transport hypothesis

Keep the compact JSON-array row format and exact reconstruction as a private
measurement hypothesis only. Compare against current JSON-object transport in
paired runs bound to identical source, fixture, and cold test state. Before
porting, prove byte-identical row reconstruction, normalization, all nine
manifest digests, snapshots, drift checks, and main/auth/peer parity. The
current paired candidate has not demonstrated an accepted improvement; no
dependency change is authorized. A private profile attributed about 0.122 s
to JSON decoding, 0.073 s to digest construction, and 0.112 s to lazy imports;
these are profile costs, not demonstrated savings from a transport rewrite. A
private `orjson` decoder comparison gained about 0.95 ms per check (about 20 ms
across 21 checks), which is not accepted as a new dependency or a proven
end-to-end benefit. The latest R4 peer timing was about 31 ms and variable; its
sample Work policy rows were empty, so it does not establish a benefit for
populated policies. No compact transport is ported. Port only after a repeat
paired comparison establishes a substantial unchanged FR-044 improvement;
otherwise retain the existing wire format and dependencies. The current
experiment is closed as a no-go; it does not pass FR-044 or close T076.


## T087 — same-turn completion and remote projection: focused implementation complete

Both regressions were reproduced before the fix. The local event/barrier RED
showed that the post-send phase could reopen the dispatched turn and overwrite
a completion observed during the blocking send. The remote RED exercised the
real `terminal_service.send_input` branch through `run_agent_step`; a valid peer
sequence was rejected because the local monitor counters remained zero.

The focused implementation now acknowledges the already-open local turn
without advancing its sequence or demoting an accepted completion. For remote
terminals it checks the exact current peer projection and a valid completed
sequence, accepts an already-completed current turn, and fails closed for stale,
missing, or malformed state. Remote acknowledgement does not synthesize local
monitor counters, captures, buffers, or status events. Ownership,
interaction/authentication, durable receipt, cancellation, delivery and
at-most-once uncertainty boundaries remain in place.

The pre-fix RED selection reproduced four failures. The final native four-module
run passed 285 tests with no failures (46 warnings, 124.87 seconds); a separate
real lifecycle/restart selection passed 4/4 in 154.28 seconds; and the final
post-format focused selection passed 19 tests with no failures (17 warnings,
10.28 seconds). Pinned Black, isort and `git diff --check` passed for the scoped
changes. These results close T087's focused contracts only; the broader T083
aggregate and T076 remain open. Receipt paths and source hashes are listed in
`research.md` and `graphify-impact.md`.

The final focused selection covers `test/services/test_agent_step.py`,
`test/services/test_status_monitor.py`,
`test/integration/test_interaction_wait_composition.py`, and
`test/api/test_run_step.py`. It retains turn correlation, stale capture,
current-turn completion, failed-send/abort, remote projection shape and
freshness, and receipt-backed completion contracts. Aggregate regression and
strict NFR acceptance remain tracked by T083/T076.


## T088 — provision native PostgreSQL for Rust CI endpoint contracts

The Rust CI matrix must execute the three live endpoint contracts against the
actual CAO server backed by an ephemeral PostgreSQL cluster owned by that job.
Add `scripts/ci_rust_contracts.py` and
`test/scripts/test_ci_rust_contracts.py`; update only the Rust job setup and
commands in `.github/workflows/ci.yml`. Provision isolated main/auth/peer
stores with separate migration and runtime credentials, run native schema
creation and verification before server startup, and provide only the private
runtime configuration to the server. PostgreSQL setup, verification, health,
or endpoint failure must fail the job; none of the three live tests may be
mocked or skipped. Bound cluster lifecycle and cleanup to a private
owner-only run directory and the job-created cluster/targets.

Keep `cargo fmt --check`,
`cargo clippy --locked --all-targets -- -D warnings`, `cargo test --locked`,
existing timeouts, and all test-selection semantics unchanged. The pre-change
RED is an isolated standard-library contract test that fails because the Rust
workflow does not invoke the required helper. Before accepting the change,
prove Linux and macOS matrix wiring, helper unit contracts, actual server
health, native schema verification, all three endpoint tests with zero skips,
and owned-resource cleanup. The root-owned preimpact Graphify and source review are recorded in `graphify-impact.md`. The final helper/test/workflow hashes are listed in `tasks.md`; the isolated `-I` entrypoint contract exposed a missing project-root import before the final helper module passed 8/8, and the workflow contract passed 1/1. On Linux, the actual helper-owned PostgreSQL 18.6 run passed `cargo fmt --check`, locked Clippy, and `cargo test --locked` (262 passed across seven binaries, zero failed/ignored/filtered; endpoint contract 4/4). The 1,607-file source manifest is unchanged before/after. Final Graphify has 1,099 nodes/2,576 links with no failed sources; path and digest are in `graphify-impact.md`. The macOS matrix run remains unavailable, so T088 stays open. T088 supplies the missing Rust live-test database prerequisite for T083 but does not close T083 or T076.


## T089–T091 — measured no-go experiments

These private comparisons changed no product source or dependency and do not
count as FR-044 acceptance. T089 compared shared-column selection in 20 paired
fresh checks with raw and digest parity (23.147 ms original, 23.163 ms
candidate); reject it. T090's Pydantic Core decoder comparison was about 24 ms
over 21 checks and did not establish an accepted end-to-end gain; do not add
the dependency. T091 measured the Rust encoder at 3.510 ms versus 3.327 ms for
the standard-library baseline; do not port it. Preserve all original read,
freshness, verification, and timing boundaries.

## T092 — provider harness privacy and environment contracts

Resolve `Path.home()` before imports that consult it, bound child `HOME` and
`PYTHONPATH` to the selected source, isolate Claude child configuration with
`CLAUDE_CONFIG_DIR`, and preserve caller HOME/CODEX_HOME and auth-file state.
Retain sanitized metadata only; never persist prompts, provider output, logs, or
credentials. The corrected offline selection passed 18 tests across two
modules with zero skips, bound to four source/test input files; the Claude
config-path module passed 15; OpenCode's list module passed 27 and its focused
v2 node passed in two fresh runs. The R3 source binding and exact files are in
`research.md`. These checks validate the harness only; live provider journeys
are tracked independently in T095.

R4 guarded package verification passed against source manifest
`c4827350259bb4b4815126d11c15b2f9141cc11a8b71716f335cf8db6b37407e`: runner
exit 0, 389 wheel/sdist modules and five active SQLite guards passed, and
HEAD/index/HOME remained unchanged. Receipt:
`coverage-contracts/package-verification-r4/artifacts/guarded-package-result-r1.json`
(SHA256 `f0a4c966274532de62db189ff10441edfbc666cf3951a0137a5d496bb83fc13f`).
The source-bound R4 Web20 run passed 20/20 with zero skips and exit 0; its log
SHA256 is `159ae898419dad4115e61b38686b65a671e7fce76d9087712905eb8a97706554`.
These scoped results do not close the full-suite, provider, or strict-NFR gates.

## T093 — conditional Core metadata-path candidate

A focused preimpact Graphify over the exact R3 `memory_service.py` and
`database.py` bytes identified two query paths: the fresh cross-tier metadata
collision lookup before filesystem/index publication, and metadata upsert
after publication. A private candidate may replace only ORM mapper/entity and
unit-of-work overhead with Core queries; the profile attributes 28 ORM mapper
constructions/unit-of-work setups to these paths, which is a diagnostic count,
not a measured saving. Keep each current session/transaction
boundary, `NULL`-aware predicates, unique-conflict classification, migration
receipt exception, error mapping, and publication order. Do not cache database
rows, permissions, schema verification, or metadata. The Core candidate is now
in main at `memory_service.py` SHA256
`58ec723493698a8c95ebb0a99e4781a4c469608115e8af9a1f6fbc994a89814f`; its
focused native test is `test/clients/test_vault_schema_migration.py` SHA256
`33f89d93251747e538da44b876f7ca07a578769a0cea097f013d275992bd638f`. The
fresh native main caller slice passed 21 tests in 25.798 s (JUnit SHA256
`5aac5e4a69c48871feca0499525c9066888646e4c8190de59adb7f8455290fd8`). This
does not accept the performance candidate or close FR-044.

Private Core paired run 02 measured 1.105620873 s baseline and 1.004116244 s
candidate (101.504629 ms faster); both failed the unchanged `<1.0 s`
assertion. A later Core/decoder pair measured 1.032364007 s for Core and
1.120388702 s for combined; both failed and the decoder arm was slower, so no
decoder change is ported. Both post-timing catalog-digest audit probes errored
and remain under investigation. Keep T093 open until these audit probes are
resolved and all three unchanged cold strict runs pass.

## T094 — partial T097/T098 QEMU acceptance

Keep the R3 QEMU result as a failure, not a partial pass: 91 expected, 8 passed,
2 timed out/failed, 81 not reached, 0 skipped. Both failures stopped before
the upstream callback started under the harness's initial dispatch-anchored
65-second wait; cleanup later surfaced `DeliveryUncertain`. That wait was a
test-side bound, not a production total pre-GO deadline. Do not attribute this
to TCG without evidence. Preserve the marker database OID/owner check, marker
removal receipt, healthy owned PostgreSQL service, and source-manifest parity.
A future run must explain/fix the callback boundary and execute the complete
selection without changing production phase timeouts or mutating the operator's
source.

### T097 callback-wait boundary diagnosis — 2026-10-14

The two-case QEMU2 diagnostic selected `before_upstream_effect` and
`after_upstream`: one passed, one failed, zero skipped. The failed sanitized
`T097_DIAGNOSTIC_STAGE_TRACE` contained 111 events. Relative to that test's
start, dispatch task creation was 28,952.8 ms; dispatch preparation began at
28,996.1 ms and ended at 37,339.2 ms; adapter launch began at 40,028.3 ms;
backend execution began at 40,032.2 ms; Bubblewrap spawned at 56,606.0 ms; ACK
wait began at 56,685.2 ms and the ACK arrived at 69,440.0 ms (12.754 s); the
durable GO release was at 90,679.3 ms; and the upstream callback was entered at
104,070.4 ms. The helper's initial 65-second wait, anchored at dispatch task
creation, timed out at 94,021.7 ms (65.069 s after dispatch creation). Thus the
callback was 64.038 s after backend execution began and 13.391 s after GO,
while the harness wait had already expired. The worker was still alive at the
timeout; dispatch later failed during cleanup at 105,353.6 ms.
The paired `after_upstream` case passed. This is evidence of a harness deadline
starting before dispatch preparation, not a TCG failure.

Source review of `WorkAdmission.dispatch_registered_next` through the launch
runtime/gateway and Bubblewrap composition confirms that setup-info and setup
ACK reads each use the phase `setup_timeout_seconds` (20 s in this host
acceptance), while the worker execution timeout (45 s in this QEMU selection)
starts only after GO. Durable authorization and GO follow ACK and include
separate repository transactions. There is no single production deadline
covering preparation through pre-GO callback; the workflow's 45-minute job
timeout remains the outer hang guard. The initial test helper's 65-second
aggregate was therefore not a valid total bound from dispatch creation.

The TDD fix is limited to the test helper: wait for either the upstream event
or the real dispatch future without a synthetic initial timeout; propagate the
same dispatch exception, and fail if dispatch returns normally before the
callback. Keep the existing 65-second `wait_for` used after SIGKILL and during
cleanup, along with production's 20-second phase values, 45-second worker
value, and the 91-case selection. Offline contract tests cover callback-first,
dispatch-error identity (including `TimeoutError`), dispatch completion before
callback, cancellation cleanup of the upstream waiter, and the absence of an
initial `asyncio.wait` timeout. This correction does not claim a single bounded
pre-GO interval; T094 stays open until the fresh full 91-case QEMU run and
cleanup/source-identity gates pass.

## T095 — real-provider interaction acceptance

Run the existing bounded journey with the real OpenCode, Claude Code, and Codex
CLIs, configured authentication, and a supported model. Each journey must show
a generated question, delivered human answer, and provider acknowledgment.
Zero exit status alone is insufficient. Preserve user auth state, isolate child
configuration where the provider requires it, and retain sanitized lifecycle
metadata only. Claude's earlier journey acknowledged two answered questions.
The latest private-harness Codex journey passed in 14.17 seconds with two
questions, two answers, and a matched acknowledgment; auth/operator state was
unchanged. The latest private-harness OpenCode journey exited 0 in 11.11
seconds but produced no question or acknowledgment (one `step_start` and one
`text` event); the cause is unknown and read-only MCP preflight is pending.
The harness implementation has since landed in the current source tree under T096; the cited provider journeys remain private-run receipts, not fresh live-provider acceptance on current source. Preserve the earlier
Codex failure as history; do not classify it as the latest result. T095 and
SC-011 remain open until OpenCode and the complete requested provider journey
are accepted. Do not classify harness tests or binary visibility as provider
acceptance.

## Current evidence update — 2026-10-14

The latest no-coverage memory regression selected 234 tests across 11 modules, with 233 passing and the strict round-trip NFR failing at 1.161985059 s (JUnit SHA256 `2673890c4974210e28e3f7a45c25bceaa05aa72c41092eb68168b1863ddc6794`). This is not a strict covered run. Four scoped static checks passed (Black/isort, ImportLinter, and composition over 399 files/1,775 dependencies; receipt SHA256 `d82f19af42bcc3ea94460a531390ed489459c1bf031e8307667a46aa18b7cf7f`). Manual composition review found no blocking Core conversion defect; it noted the pre-existing cross-tier preflight race is not introduced by this port (review SHA256 `38188ba53187f7e8818df2eeb840eecde19bd31b7392008bb8fa44e4b04d50a6`).

The Core run-02 pair and the later Core/decoder pair both remain below acceptance: both arms in each pair failed `<1.0 s`; the combined Core/decoder result was slower, and the post-timing catalog digest audit probes errored. Preserve those receipts; no decoder change is accepted and T093/FR-044 stay open. The QEMU2 two-selector diagnostic completed 1 pass and 1 failure (0 skipped): `after_upstream` passed and `before_upstream_effect` failed. Cleanup succeeded; this selector diagnostic is not acceptance of the full 91-case QEMU suite, so T094 remains open. T076/T081/T083/T093/T094/T095 remain open.

## T096 provider-harness and QEMU2 follow-up — 2026-10-14

The bounded harness implementation is now in the current source tree at four hash-bound paths: `test/live_provider/interaction_cli_journey.py` (`8b136a5746e101f16f94c540d99e2db2d69d55f5d9fab62f3ea8e3d39074e130`), `test/live_provider/test_interaction_cli_journey.py` (`2792ec1d699d2f78b63254b37fd07da86ba18cad3044d632575405abf2f5f890`), `test/live_provider/opencode_interaction_journey.py` (`848d150f87136fec84e425db76ad305b62c4821cdc59d1f94761bf1a4e9e44fd`), and `test/live_provider/test_provider_harness_upgrade.py` (`efd58e94f18a8b8b034afb518d3f158199b5274e1a5fc1cabe6330bc95ca64bc`). The initial main-bound selection RED was 73 passed/3 failed/0 skipped on unsupported or metered Codex auth staging (JUnit SHA256 `35dcac728e1d92f77a7d2afc130be30fd5702ced6edbb0f95d5b725ecc18cdab`). Auth-profile validation now precedes overlay creation; failed staging rolls back and preserves source auth bytes. The fresh main-bound rerun passed 76/76 across two modules with no errors/skips in 3.211 s (JUnit SHA256 `89415323d7a5a44cf7418c31f42d2891d76a2335a7ef7b85bfc8e934c91916e7`); Black/isort/compile checks passed. This closes only the scoped harness contract, not T095's real-provider journeys.

The QEMU2 diagnostic selected two cases only: `after_upstream` passed; `before_upstream_effect` failed; none skipped. Cleanup succeeded (marker removed, runner ID absent, PostgreSQL healthy, no OOM/restart). The callback arrived 13.391 s after GO and 64.038 s after backend start, while the outer unchanged 65-second wait had timed out 10.049 s earlier. Source review confirms the issue is the test helper’s dispatch-anchored aggregate wait, not a production total pre-GO deadline. The helper-only correction is covered by 10 passing offline tests. The earlier two-selector failure predates the correction; no fresh QEMU run has launched, and a prepared tar is not acceptance evidence. Keep T094 open until the full 91-case run and cleanup/source-identity gates pass.

## T093/T094 latest measurements — 2026-10-15

The private owner-JSON loader candidate preserved the nine semantic/counter/source/HOME proof gates, but regressed timing: baseline 1.306698168 s, candidate 1.565738595 s; both miss `<1.0 s`, with the candidate 259.040427 ms slower. Do not port it. Receipt SHA256 `dcb51b9cb3cc2fdb1326097b60e584221ceb6fe64e8c30f5442e2c55c2b3088d`; T093/FR-044 remain open.

The test-only T094 deadline-origin correction passed 10/10 offline tests in 4.01 s (JUnit SHA256 `cef799393c147333824de70fa6bc40b9260e1bcc938c43a84ee52f8d11eadcc2`; log SHA256 `238189ef2b5b53c54b71fae94f4aab46c2686a22e91c8c6a4517c5537fe783d5`). Independent source review passed Python 3.10 cleanup-diagnostic compatibility and exception identity preservation for inputs `test/integration/t097/test_proxy_sigkill_t097_restored.py` (`6f6da51600c0f43c0fc1c7a0a3fd0e87493547c5179482474f220c075c1f033b`) and `test/scripts/test_t097_sigkill_wait_contract.py` (`0e5a95e3bb2cc8991c65eaa9838821159cd08dc7701f0236b37b18223bc379c9`); review receipt SHA256 `5d0453379873c62dab86bb83c9fb1f26605b24c3a226d4e40aef203fe59719d8`. This review is source-only. No fresh QEMU has launched against the corrected helper; a prepared tar does not count as acceptance. Keep T094 open pending the fresh full 91-case run.

### Diagnostic recovery — 2026-10-15

The catalog-plan baseline's inner round-trip clock was 1.805611058 s, so the strict `<1.0 s` condition failed. The wrapper process returned 1 before receipt creation: `_expected_slow_red()` rejected pytest's assertion-rewritten suffix `assert 1.8056110579927918 < 1.0`. An offline strict parser then bound the precise message, exact numeric assertion, elapsed clock samples, JUnit failure, and pytest summary. It verified all nine semantic/counter/catalog/loader proof checks, the complete ten-plan set, no plan errors, and 71 tables. The recovered evidence preserves the original failure and does not satisfy FR-044; the original wrapper's parent HOME and main-source before/after values were not persisted. Receipt `nfr-fix-r23/catalog-plan-core2-r1/recovery-r1/recovered-result-receipt.json` SHA256 `0af8126623d8a33c17b9709fb7936efa8bc1e978c93a39b1cec108c699e5b9a1`; parser tests passed 10/10 offline (validation receipt SHA256 `c4635fcb806a2cb1b9ac97dadeeb87f1ce33294f56df144bf4f8957cb7087caa`).

The corrected T094 helper has now run in QEMU2: the two selected SIGKILL cases both passed in 235.15 s with zero warnings, and the guest runner exited 0. Preserve the original wrapper exit 1 and its `diagnostic_failed` receipt because its summary parser reported 0 passing selectors; the raw log and offline recovery receipt establish the observed 2/2 guest result. Recovered receipt SHA256 `addf5550137ad6236a2b9be1e033ccf651b0a63895ed078d9290dccd6cab18e5`; original runner receipt SHA256 `2ddc69626ea4fb1ab53379915bdb93fbde4d91c1806e110cd23c3ee4a3bba244`; raw QEMU log SHA256 `70159e926a62080caed92a751e379823e6dff26a7b1e4917610591bef9a9a844`. This is only the two-selector diagnostic; full 91-case QEMU and T094/T076 acceptance remain open.

## Service recovery and current acceptance limits — 2026-10-15

Docker Desktop is available again. The owned PostgreSQL 18.6 container identity is unchanged; only the published port moved from 61997 to 56421. The sanitized metadata snapshot is bound by SHA256 `200b53e7bd46f495ed1246cab792eb0e6909bbec3cc1ad962a05b99f2a14f1d1`; the recovery receipt confirms admin/version verification and unchanged historical results (SHA256 `b792881742290c78bcc596ca0d65a00a602b6aefb0a0041a779de4d53b9c57e5`).

T098 private R3 evidence: 18 offline tests plus 9 subtests passed; native baseline and candidate each passed 8 tests. The scored pair was baseline 1.024650518 s (fail) and candidate 0.967219559 s (pass), while the three-run candidate cohort was 1.017240271 s (fail), 1.034487912 s (fail), and 0.973277629 s (pass). The required three-run acceptance is therefore still open. The separately guarded phase diagnostic measured 1.032294341 s and 21 catalog reads in 592.153 ms; it is diagnostic only (receipt SHA256 `54add99c7eaa859e9baaf3bd25eee6f7f4ab04c3cf20ddede0dc7eb9b7d38045`). A separate projection proposal's offline suite passed 101 tests with 6 warnings; no runtime fix is inferred.

FULL91 stopped at preflight with exit 2: available RAM was 6062 MiB, below the unchanged 6144 MiB floor, and no VM was started. The user cannot free more memory, so leave this gate pending without reducing the floor (log SHA256 `de5c72bed00571290a21fe8342b635caa7541c1bd8abfb298a945b660779f760`). OpenCode R8 timed out after 83.13 s with zero questions/acknowledgments and cleanup status was incomplete, with `private_files_preserved` recorded as a cleanup-failure category. No final provider matrix, AGUI restart, global rerun, or T098 R3/R8 main-source port has taken place. These gates remain open.

## OpenCode R9 diagnostic — 2026-10-15

The real R9 run timed out after 83.54 s with 0 questions and 0 acknowledgments. The observed state was `turn_sequence=1`, `initialized=true`, `MCP_ready=false`, TUI idle, and the expected log absent before cleanup. The offline probe found that the bundled MCP command was recognized before it was wrapped with `env -i` and was unrecognized after wrapping; it did not establish a live MCP connection. The current diagnosis is a harness lookup that omitted readiness. No product runtime fix has been made. The owned tmux cleanup exited 0 and the server was gone, but a stale socket remained; root-owned files were preserved. Receipt: `R9launcher/runs/514ad4818318477b819d5c13e58dc6ca/root-owned-tmux-cleanup-r1.json`. This is not a live-provider pass and does not establish an R10 outcome.

The source-only SQLite guard selection passed 5 pure test functions with 1 warning in 125.53 s under `--noconftest --assertplain`; it is not full-suite or coverage evidence. Keep provider, global, and full-suite acceptance open.

## T099–T101 — private memory context-tail batch port

Port the reviewed R3 candidate for the final three Work context reads. The
existing full component/catalog, migration-history, runtime-role and configured
identity checks remain fresh and ordered before the tail. On an active private
memory-audit lease, the tail may issue the three typed context reads in one
pipeline under a unique savepoint and ordered `ACCESS SHARE NOWAIT` locks. If
speculative SQL fails, recover only that savepoint and run the original
inbox/control/recovery reads once in their original order. A recovery failure
escapes the lease guard and taints the lease. Generic Work, borrowed connections
and autocommit verification retain sequential reads. No outer transaction,
commit, fence, schema, checksum, authority check, full verification count or
audit commit changes.

The R3 implementation inputs are byte-bound to the current Core2 baseline:
`work_repository.py` candidate SHA256
`c9211ea08e7796c27ba21d71cec1b742ad0f4f4a8705319fa4cf6eccea693852`, and
`legacy_memory_audit_repository.py` candidate SHA256
`8c6dd825ecd58025469b2539f70c4c34157007dc10d1624f29bad552377718ba`. Their
baseline inputs match current main at the pre-port boundary. Portable tests
cover pipeline/savepoint ownership, typed rows, exact validation order,
fallback, taint-on-recovery-failure, lease expiry/fork, and sequential generic
paths. Native tests cover repeatable-read and write-audit transactions,
`Record` parity, table locks, each context error, inbox precedence against a
later missing/denied/locked relation, and borrowed tuple-row fallback.

The R3 implementation is in main at the exact reviewed source hashes:
`work_repository.py` `c9211ea08e7796c27ba21d71cec1b742ad0f4f4a8705319fa4cf6eccea693852`
and `legacy_memory_audit_repository.py`
`8c6dd825ecd58025469b2539f70c4c34157007dc10d1624f29bad552377718ba`. The
offline module passed 18 tests plus 9 subtests; the root-owned native run passed
8 cases with all source/origin/HOME/count guards intact. Its first attempt had
7 passes and one test-only `TypeError` because the borrowed-connection
observer used an unhashable `psycopg.sql.SQL` statement in set membership. A
`str` guard fixed the observer; the product source was unchanged for that
correction. The final native test hash is
`6b402bef3e8d6aa6385e6239bc15c96111bef8a63a7405b9f269c2aef98a1623`.

This remains an incremental implementation, not FR-044 acceptance. The earlier
private three-run cohort passed only one of three strict cold measurements.
Keep the original covered test, timer, 21 full verification checks, 20 audit
commits, and all three `<1.0 s` runs unchanged; a new scored cohort is still
required.


## Static verification follow-up — 2026-10-16

The configured full mypy run reproduced four errors in Work context buckets and memory metadata updates/table typing (396 source files). Use the existing Record type for bucket rows, an explicit scalar/datetime/None update-value union, and an identity-preserving typing.cast to the actual SQLAlchemy Table; import Table only under TYPE_CHECKING. Verify normalized AST parity and identical PostgreSQL-dialect SQL/parameters for nullable and scoped conflict paths without a connection checkout. Then run the configured main mypy check and existing native repository/memory contracts.

Full Black/isort checks reported 20/12 files. Restrict formatting to their union, check Black AST equivalence and the unchanged import inventory for sorted test imports, and preserve unrelated main work. WorkRepository formatting follows the type correction to avoid overlapping writes. Existing Graphify/source impact records cover MemoryService, WorkRepository and their PostgreSQL callers; these corrections add no runtime project dependency or storage behavior. Rerun applicable composition, focused tests, and the complete frozen-source regression after integration.


## Provider acceptance lifecycle follow-up — 2026-10-16

R7 MCP acceptance stopped at parent_create before any handoff. Its valid parent terminal ID and the production response path establish the first incorrect boundary: terminal_service prefixes a fresh session with cao-, while the private test compared the returned canonical name to the bare request. API deletion intentionally canonicalizes the bare name, so keep that scoped cleanup contract unchanged. Add a minimal reusable response validator to the existing matrix harness only if both existing matrix creation and the private MCP adapter can use it without a second framework. Exercise the production Terminal response shape in offline regressions.

The same run observed one pinned tmux daemon during outer cleanup. Source confirms create_session sets exit-empty off; the managed API fixture stops its API process but has no owned tmux lifecycle. Reuse the established short-private-root and empty-server/socket/PID/starttime/pidfd checks from the generic-message harness, extracting a small pure shared fixture helper if required. Avoid importing a model journey or constructing unrelated RunState fields just for cleanup. Keep cleanup ownership per fixture, preserve the primary error, and remove HOME only after the API and tmux closure is confirmed. Preserve the managed PostgreSQL target lifetime already tied to confirmed API shutdown; this test-only change does not introduce a new database owner. Never weaken the outer classifier or kill ambient/nonempty servers.

Before implementation, query the existing graph vocabulary and traverse relevant fixture/server/tmux relationships, then verify all findings against current source because the graph predates these harnesses. Use red/green tests for canonical response handling and the observed empty-daemon lifecycle, including fail-closed and repeated-stop paths; add one bounded real private tmux case without a model or PostgreSQL where practical. Run affected normal suites, formatting, architecture/composition and independent review before integrating. Freeze the resulting source before fresh OpenCode acceptance. Preserve the original failed R7 receipt; it is not retroactively repaired.

## QEMU monitor-PID publication race correction (2026-10-16)

Source review traces the R14 failure to the test controller's `Path.write_text(str(process.pid))`: file creation precedes content completion, while its consumer waits only for pathname existence. The consumer may therefore parse an empty string before monitor ownership verification. Publish via a sibling temporary file and atomic replacement after the full PID write. Preserve the five-second consumer deadline and every process identity/recovery check. Add a deterministic offline regression with synthetic PID and a controlled publication boundary, proving the final pathname is absent before complete publication and readable afterwards. Do not start Bubblewrap, QEMU, providers or PostgreSQL for this regression. Integrate only after Graphify/source impact review and red/green reproduction; then bind the final test and new regression in the next immutable source. Full 91-case QEMU acceptance remains required.


## Spec 021 diagnostic plan R2 — wait-start boundary

### Source and graph analysis

The current R15 source has the test signal at `test/integration/test_interaction_wait_composition.py:117-144`, with the positive-time wrapper setting the event at lines 127-129 and the unchanged one-second wait at line 144. The production path begins at `src/cli_agent_orchestrator/services/agent_step.py:598`; before the positive wait it can call `_interaction_wait_snapshot`, `status_monitor.get_status`, `_pause_timeout_for_native_wait`, and `_wait_for_required_interaction`. The latter delegates to `InteractionService.wait_for_required` in `src/cli_agent_orchestrator/services/interactions/service.py:1542`.

The Graphify query and source cross-check are in `graphify-analysis.md`. The existing graph is stale and omits the integration test and interactions-service file, so source remains authoritative.

### Candidate design

Add one small test-local signal-wait helper. It owns the existing `asyncio.wait_for(event.wait(), timeout=1.0)` call and, only on its timeout, inspects the completion task once without awaiting or cancelling it. It appends one bounded note to the same `TimeoutError`, and chains the exact task exception when the task had already failed. A task that returned early, was cancelled, or remains pending does not satisfy the event.

Use two test-local wrappers (async and sync) for the pre-signal boundaries. Each records a fixed enter/return/raise label and `time.monotonic_ns()` delta; each returns the wrapped object unchanged or re-raises the original exception. The trace has a fixed 32-row limit and no dynamic runtime values beyond exception type names and task state.

### Verification

The private fake regression intercepts only `asyncio.wait_for`, asserts the passed deadline equals `1.0`, and deterministically raises `TimeoutError` without sleeping or touching the DB. It exercises a task that raised, one that returned, and one that remains pending. The first case must retain the original task exception object as `__cause__`; the other cases must remain a `TimeoutError` with no cause. The pending fake task is cancelled and reaped by the test's own `finally` block.

Only this small private fake module may be run now. Do not run the DB-backed integration test, full suite, providers, Docker, or load tests while QEMU is active. Preserve the R1 artifacts and do not modify main or the frozen source.

### R3 compatibility addendum (tasks T112–T115)

Reuse R2's read-only Graphify/source trace and frozen R15 source binding. Make the smallest Python 3.10-compatible edit in the private candidate by assigning the timeout diagnostic through `wait_timeout.args`; keep the `raise wait_timeout from task_error` path unchanged. The fake regression swaps only the candidate's `asyncio` reference for a narrow object whose timeout class has no `add_note`, then drives the existing exception, return, and pending cases without sleeping or touching PostgreSQL. Run Black with one worker and isort with one job using the repository's pyproject settings, then rerun only that private fake module. Do not run the integration test or change main/source.


## Implementation Plan: Local Claude Unicode Credential Retention

**Branch**: `private-spec021-claude-unicode-retention` | **Date**: 2026-10-16 | **Spec**: `spec.md`

**Input**: Private Spec 021 addendum; no repository spec or production source is changed by this candidate.

### Summary

Add a single opt-in retention path for the existing Claude self-cell in the real-provider matrix. A validated durable evidence root holds the isolated workspace. After session deletion, managed API stop/deregistration, and empty owned tmux shutdown are all proven, the existing archive helper copies only `home/.claude/.credentials.json` using its fixed `message` layout. Functional assertion success is irrelevant to this teardown gate. The workspace is removed only after that copy succeeds; any uncertain teardown/archive boundary keeps the workspace.

### Technical Context

**Language/Version**: Python 3.12-compatible project code

**Primary Dependencies**: Existing pytest harness, `test.fixtures.cao_server`, `test.fixtures.owned_tmux`, and `test.live_provider.claude_auth_archive`

**Storage**: Files under an explicitly supplied private evidence root; no database change

**Testing**: Offline pytest contracts with synthetic credentials and mocked process/session/tmux boundaries

**Target Platform**: Existing local Linux/WSL live-provider test runner; no provider run in candidate validation

**Project Type**: Python test harness

**Performance Goals**: No new provider call or timeout; directory/archive operations remain bounded filesystem operations

**Constraints**: Main/source tree frozen for this private candidate; no live provider, API, PostgreSQL, Docker, QEMU, or external job-manager calls

**Scale/Scope**: One exact `claude_code -> claude_code` cell and two private candidate source/test files; all non-Claude and ordinary offline paths stay as-is

### Constitution Check

- Evidence remains source-led; Graphify is treated as structural context and is checked against current source.
- Spec, plan, and tasks precede implementation. Tests are fake-only and cannot assert live-provider acceptance.
- Existing auth source/stage is never modified; no secret contents or raw output enter receipts.
- The new retention path is isolated to an explicitly selected cell. Default/offline and non-Claude behavior remain unchanged.
- No persistent knowledge or main-tree edits are part of this private candidate.

### Source and Graphify Findings

The existing fixture in `test/e2e/test_real_provider_matrix.py` validates auth-home isolation before startup, creates a private tmux root, starts CAO with a redirected HOME, then stops the server, checks its registered owner is gone, verifies/removes its empty tmux server, and deletes HOME. The matrix cell calls `_delete_session` in its `finally` before the fixture resumes. `_link_auth_material` copies only manifest-listed auth files and chmods copied files to `0600`; in the docs the Claude manifest lists `.claude/.credentials.json` and `.claude.json`.

The archive helper’s fixed `message` layout is exactly `root/home/.claude/.credentials.json`. It validates path components, owner/mode/link count/size and source identity, writes an exclusive archive file, fsyncs file/directories, and rejects unknown layouts. The existing message-archive contract demonstrates the intended `workspace` root and keeps the archived file outside it.

Graphify query evidence is in `graphify/query-output.txt`. The graph returned 54,428 nodes but truncated a broad query and reports `test_real_provider_native_child_matrix()` at stale line 1181; current source defines it at line 1326. Therefore source inspection, not graph locations, is authoritative. The query did not expose the precise fixture→session-delete→tmux→archive ordering; that path was confirmed directly in source.

### Design

1. Gate the durable mode on the exact selected `claude_code -> claude_code` cell and one explicit archive/evidence-root environment value. For that cell, require an existing absolute real directory with current-uid ownership and mode `0700`, outside pytest’s temporary base; reject symlink components and do this before server startup/auth copy.
2. Create only fixed private children beneath the validated root: a unique `runs/<run-key>/workspace/home` and a sibling `archives` directory. Keep the archive path outside `workspace`. Require file-based `.credentials.json` for this archive-enabled lane; do not read or log credential contents.
3. Track session-deletion confirmation in process memory for the exact `CaoServer`/home. Set it only after the matrix’s final session DELETE succeeds, regardless of the matrix’s functional result. Missing state or unsuccessful session DELETE disables archival. Do not use the scenario outcome, marker result, or pytest pass/fail status as an archive gate.
4. Preserve the existing fixture teardown order. After `server.stop()` succeeds, the exact server is absent from `_LIVE_SERVERS`, and `cleanup_private_tmux_root(..., owner_stopped=True)` returns true, archive the private copy with `layout="message"` and a unique run key. Only after archive return succeeds may a strict helper remove `workspace`; do not remove the evidence root or archive.
5. On any uncertainty before session deletion or during process/tmux teardown/archive, leave the whole workspace under the durable root. If setup fails after auth has been copied, retain the workspace. Preserve original test errors and add only a fixed sanitized cleanup note. Do not write a new receipt containing filesystem paths or content.
6. Existing non-Claude/default branch continues to use `tmp_path/live_provider_cao_home` and current cleanup behavior. The prompt, model, timeout, filters, four substantive scenarios, quota statuses, and evidence schema are unchanged.

### Project Structure

Private candidate payload (not main):

```text
test/e2e/test_real_provider_matrix.py
test/test_real_provider_matrix_contract.py
```

The existing canonical archive helper and its contract module are dependencies only and are not edited by this candidate.

**Structure Decision**: Keep the change in the existing matrix fixture/test contract; do not add a new helper framework or new product runtime code.

### Test-First Decision

Use private, offline-only red/green contracts around the candidate matrix module. Cover invalid-root preflight before server/auth copy; exact workspace layout/modes; success ordering (session DELETE confirmation → API stop/deregister → tmux proof → archive → workspace removal); original/stage identity preservation; and retention/no archive for failed DELETE, failed stop, registered owner, failed tmux, failed archive, setup uncertainty, and missing DELETE confirmation. Separately prove that a functional assertion failure does not suppress archive after successful teardown. Run existing canonical archive helper contracts as reference; do not call a provider or DB.

### Risks and Limits

- The fixture finalizer does not determine functional test success. Its only eligibility signal is successful session deletion tied to the exact server/home; process and tmux teardown are then independently checked by the fixture. A failed functional assertion must not be used as a reason to preserve credentials after confirmed teardown.
- A non-file Claude auth path cannot satisfy this archive contract; the lane must reject that configuration before launching or copying auth.
- Only the fixed `.credentials.json` path is in scope; `.claude.json` and other Claude-containing cells remain unchanged.
- Offline tests prove ordering and filesystem invariants only. They do not prove Claude Unicode generation, provider billing behavior, database cleanup, or external manager reconciliation.


## Bounded catalog-rendering probe — 2026-10-18

Reuse the current native fixture and the unchanged covered memory test. Only
after its timer ends, count eligible NOT NULL rows and distinct native-render
signatures, compare complete baseline/candidate manifests and digests, and
measure six interleaved reads of each plus EXPLAIN/BUFFERS. Keep product source
unchanged for the probe. Budget: one query variant, at most 30 minutes, no
provider calls, no new/restarted PostgreSQL service. A material repeatable gain
and equivalence evidence are prerequisites to any main implementation. Then
require version fallback, drift/snapshot/error parity, focused native callers,
three cold covered NFR runs and the complete regression. A rejected experiment
is retained as evidence and does not count as a fix. The graph query is broad
and stale for the newer native modules; current source establishes the actual
MemoryService -> private audit repository -> Work verification -> catalog path.

T116 native grouping was slower and is rejected. The next bounded diagnostic
(T118, one variant/30 minutes) evaluates compact positional JSON rows plus
exact dictionary reconstruction. It changes transport representation only;
all source catalog queries, row order, native renderers and fresh verifications
remain. Compare full values/digests and include reconstruction in timings.
No runtime or dependency change is authorized by a diagnostic-only result.

The codec-only diagnostic found only approximately 11–15 ms total potential
savings over 21 parses, insufficient by itself; do not revive the rejected
owner-loader candidate. Next measure one Work-proof read bundle on the existing
private pooled connection versus the current five read sections. Preserve all
raw values and snapshot/lease boundaries, and include conversion in timings.
This is an architectural read-boundary diagnostic, not permission to bypass
validators, cache proofs between verifications, or reorder errors. Any future
implementation requires ordered validation and savepoint-backed sequential
fallback for batch errors before focused native drift and cold NFR checks.

### Native benchmark GC preparation isolation

Observation found a 50.215 ms generation-2 collection in a 1.043542 s failing
body; the single post-fixture collection diagnostic passed at 0.961760 s and
collected 1,636 unreachable objects before timing. This does not establish
object provenance or a product improvement. Add one explicit local fixture
depending on isolated_memory_db and tmp_path, call gc.collect once, and leave
GC settings unchanged. Preserve the test function body byte-for-byte. TDD uses
the recorded failing unchanged test, then three independent covered cold runs
with no retries; retain all results. Review fixture order and final source diff.
Graphify's existing memory call-path query is stale for native modules; source
confirms this change only precedes the MemoryService benchmark and does not
change Work, PostgreSQL, audit or production lifecycle boundaries.

### Capacity diagnostic omission

The corrected bounded eight-worker cohort passed 38 tests, capacity p95
480.1 ms. Expiry p95 was 265.08 ms and read context 292.22 ms. The original
trace omits expiry and snapshot entry, as source confirms; add two small local
wrappers/timers and include each phase in slowest-request and aggregate output.
Keep all production paths and existing SLO clocks/assertions unchanged. Use the
capacity module itself as regression and verify every source has phase values.
This corrects diagnostic attribution only, not product speed. The existing
Graphify/source trace covers Work/Interaction and the fresh native catalog; no
new runtime dependency or generated-graph rebuild is required.

### Unicode credential comparison regression

Source review found three Interaction API service-token exclusions and runtime
WebSocket authentication passing untrusted strings to hmac.compare_digest
without ASCII validation. Python reproduces the reported TypeError, but no
historical traceback links the old run-step error to these headers. First add
offline boundary regressions, execute RED on actual handlers, then add a shared
ASCII token comparator in security/local_token.py and replace the four calls.
Reject non-ASCII before comparison; keep all valid ASCII behavior, scopes,
principal resolution, WebSocket close1008 and no DB/terminal effects. Run new
regressions and existing API/runtime security contracts, then composition.
Graphify memory query is insufficient for this surface: query token/auth paths
and confirm source before production edits. No provider/login writes are needed.

### UI packaging omission

Source inspection confirms both publication build jobs generate Web but omit
MCP Apps while Hatch lists the ignored apps_static output. Add the missing
locked npm install/build:all in both jobs, account for both lockfiles in caching,
and update release instructions to the actual Vite output directories. Use
existing workflow contract tests for a regression requiring asset production
before each packaging boundary, and validate Apps/Web builds locally without
publication. Preserve all release policy/pins/permissions/matrix gates.

## T125 — Keep the scope source auditor aligned with the canonical token guard

After the current unfiltered backend run terminates, reconcile its four recorded
failures to their exact JUnit node IDs and confirm they originate in
`test/api/test_scope_coverage.py`'s stale inline-`hmac.compare_digest`
expectations. Port only the private test-auditor candidate: require the existing
route call to `ascii_token_matches`, inspect the canonical helper's live binding
and implementation, and add the two negative regressions (unsafe helper body;
rebound helper). Preserve all existing positive route/enforcement assertions.
Do not alter production auth, selection, thresholds, skip behavior, or the
active backend run. Run the focused module and static checks after the run, then
repeat the complete unfiltered covered backend on the resulting final source and


## T126 — Remove the redundant ledger read from the terminal-status mock

R3 measured snapshot391 ms, status348 ms, native369 ms before positive wait.
The status phase is a test stub that calls `has_required_wait`, not the real
monitor. Replace only its predicate with the test-local answer acceptance flag;
retain all real snapshot/native/required wait operations and ledger assertions.
The completion loop remains blocked until MCP pulls the answer, so status after
release remains equivalent. This is fixture isolation, not product optimization
or a claim of observing COMPLETED before the pull. Run the complete composition
module with its unchanged limits before acceptance; final full suite stays open.


## T127 — Separate transport loss from authority expiry in the recovery test

R3 replayed an `input_blocked` result under a test-specific 1 s dispatch budget.
The source assigns the same short budget to waiting and durable dispatch
authority. An initial diagnostic inserted real delay but eagerly acquired the
pair fixture, altering backend fixture order; its red/green attempts are invalid
for attributing the original R3 failure. Preserve those artifacts, correct that
private diagnostic, and run the ordinary module without instrumentation. Simulate acknowledgement loss by enqueuing
the bridge then raising OSError, using COMMAND_TIMEOUT for dispatch authority.
Retain native/store/receipt guards and add one-paste and unchanged-replay state
assertions. Compare the same controlled delay after the correction, then run the
whole remote-services module without the diagnostic. No retry or clock mock.


## T128 — Correct the three configured mypy errors

Baseline R1 mypy1.18.2 reports two no-any-return errors (grant job identity and
body-free MCP projection) and one attr-defined error from the inferred common
Pydantic metaclass. The effective configuration is mypy.ini (Python3.11), not
the separate strict pyproject section. The pinned cache and main venv tool
versions agree; do not change dependencies or configuration. Retain the existing
runtime job-ID validator, annotate the local value, describe recursive JSON
shape with overloads and retain the three-entry model dispatch mapping with
explicit class/return union types. Root Graphify impact found WorkAuthority and
WorkMcpProxy dependencies; WorkMessageService is absent from the stale graph,
so inspect the current WorkOrigins dispatch and source/tests directly. No
architecture edge, schema, authority, receipt or message behavior should change.
Review the private R2 diff, guarded-port only those three main files, run the
existing authority/proxy/message regressions and configured global mypy/style,
then applicable architecture/composition checks and final complete backend.


## T129 — Diagnose routine-definition JSON construction after the loaded failure

R5 full eight-worker diagnostic completed18,375 cases:18,138 passed, one strict
NFR failed,236 skipped, zero errors. All six other R3 failing nodes passed every
phase; the two added negative auditor cases are present, with no removed nodes.
Coverage68,501/78,536=87.22242029133136%; source/HEAD/index guards passed.
The strict body measured2.069717154 s; passive call timing2.070461006 s.
The same-profile one-worker control passed at about0.990 s call wall, retaining
21 Work checks,21 catalog reads,145 executions and21 commits. Both are
diagnostics; the selected control does not carry a whole-suite coverage claim.

The loaded profile excludes GC (~5ms) and commits (~14ms) as principal costs.
Catalog calls total1.098s wall versus0.507s isolated; their CPU also differs.
This does not separate concurrent load, per-process history, database catalog
state, driver or server time. The T082 dependency rewrite is the same old
rejected source-object join and remains rejected.

A distinct hypothesis is avoiding JSONB construction only inside the routines
definition that is subsequently serialized as JSON. Preserve JSONB
identity_arguments for SQL ordering. Prepare a private post-call/pre-fixture-
teardown probe: no prewarm or fixture reordering, no product mutation. Run a
read-only bounded transaction, fixed A/B, B/A, A/B comparisons, recording
EXPLAIN ANALYZE BUFFERS aggregate timings and data-free parity/count/type
summaries. Retain all samples, including failures. No globals/settings/cache
changes; no SQL, parameters, catalog values or credentials in emitted JSON.
If a difference merits further work, require representative NULL/numeric/type/
Unicode/overload/variadic/unsupported-routine/trigger parity before any main
port. No migration, model or API contract changes are proposed. Final acceptance
remains the uninstrumented full eight-worker suite and three cold strict passes.
Existing Spec021 artifacts continue in place; do not bootstrap a replacement
plan over the accepted ongoing work.


### T130 bounded diagnostic plan

Reuse the prior Graphify lookup limitation (catalog_manifest absent in the
current graph), checked against the unchanged migrations/Work/memory source.
Source review shows repeated catalog references but does not prove duplicate
execution cost: existing plan summaries lack relation/loop attribution. Prepare
one private post-call comparison, source-bound to R2, baseline literal builder
identical to production, with explicit candidate CTE/query mappings and no
module mutation. Retain the canonical sorted union of all Work migration
tables, call-completed guard, 5s query timeout/savepoints, owned read-only
repeatable-read connection and fixed AB/BA/AB observations. Record all nine
families' raw/numeric-type/normalized/digest parity and allowlisted per-node
catalog/CTE loop/timing metrics, without SQL, conditions, values or credentials.
CTE materialization may add tuplestore cost or impede index access: reject any
unsupported or unstable result. QEMU runs first; do not overlap heavy runs.
Only a demonstrated safe gain may justify broader semantic regression work
and a separately reviewed main change. Final uninstrumented full8/cold gates
and exact coverage floors remain required.


### T131 supervisor race diagnosis and bounded correction

Preserve the failed QEMU receipt (9 passed,1 failed,81 not executed), with its
source R2 binding and successful owned-resource cleanup. Trace wait() in
work_process_supervisor and SIGKILL gateway cleanup. Use Graphify against
source before product integration. Reproduce transient child-status
unavailability after readable pidfds, including the existing timeout0/positive/
None semantics. A correction must keep the original deadline, preserve
ownership/pidfd/reaping uncertainty and external reattachment behavior, and
release the global supervisor lock while waiting; the per-attempt wait lock
may serialize that attempt. Add meaningful regressions for transient success,
persistent unavailability/deadline, errors, missing ownership and cancellation
responsiveness. Root-review Luna's isolated candidate before main integration.
Run applicable supervisor/gateway/architecture/composition checks, then seal
the resulting source and repeat the complete91 QEMU cases. Do not attribute
the failure to host pressure or increase the existing8s test bound.


### T132 bounded current-source diagnosis after global R5

Preserve the fresh failed R5 receipt and stop-first-failure behavior. Recheck source/HEAD/interpreter/fixture/worker/coverage binding and exact owned PostgreSQL identity; retain 6144MiB admission and serial heavy execution. Consult Graphify privately and verify MemoryService→legacy audit→Work verification→catalog manifest against source, accounting for current Work V4 changes. Use independent Codex read review to choose one bounded measurement that separates Python/driver/SQL/catalog/commit costs without changing production or warming the benchmark. Existing T129/T130 plans and rejected query candidates are context, not demonstrated current gains.

A private profiling hook may observe the exact benchmark call and aggregate allowlisted function timings/call counts only; preserve coverage, the test body/timer/assertion, GC settings and fixture cleanup. Any profiled failure remains failure, and any profiled pass remains diagnostic rather than acceptance. Do not emit SQL, parameters, catalog values, credential material or arbitrary exceptions. Establish the first expensive boundary before proposing a product correction; then require a meaningful RED/GREEN contract and complete authority/schema/freshness regressions, style/type/composition and independent review before integration. Leave T076/T081/T083 open until their original acceptance gates pass.

T132 first diagnostic result: 21 Work checks/20 audit write contexts were preserved; catalog manifests consumed 523.697ms of 1.047529s whole-call wall time under instrumentation. This narrows the observed boundary but does not prove server causality or a safe optimization. The next bounded diagnostic may add a post-call-only plan capture of the unchanged canonical statement before fixture teardown: a fresh owned readonly repeatable-read connection, 5s local statement timeout, fixed three baseline EXPLAIN ANALYZE BUFFERS JSON samples, no candidate rewrites or retry after query failure. Export only finite numeric metrics and fixed catalog-node labels, never SQL, predicates, parameters or catalog payload values. Preserve the original pytest outcome and make missing/malformed/incomplete plan evidence fail the diagnostic controller. Query planning/EXPLAIN overhead and fresh-versus-reused connection effects remain explicit limits.

T132 post-call capture completed with the original failure 1.038285341s retained. The three baseline plans and cleanup checks are recorded in [the current-source diagnosis](current-source-nfr-20261022.md). Keep SQL candidates retired unless new independent evidence supports a distinct hypothesis; no product change or acceptance follows from these plans.


T132 continuation2026-10-30: verify current source versus5628bdf6 and exact interpreter/fixture/PG. Extend the previously controlled reversible boundary plugin only with logical catalog-scoped execute, fetchone, normalization and literal-render spans; do not add SQL, prewarm, data dumps or alter the benchmark. Verify privacy/result/error/context/descriptor/alias/restoration controls before one serialized covered observation. Root and independent Codex review choose the next discriminating experiment only from actual measured boundaries. User now requires Codex only; no Claude calls. Retain all rejected SQL candidates and original global/cold/coverage criteria.


### T133 bounded experiment plan

Use one fresh owned post-call readonly/repeatable-read connection before fixture teardown, after the unchanged covered benchmark. Baseline must be byte-equal to current production literal statement; candidate replaces exactly nine static json_agg(row_to_json(manifest_row) ORDER BY prefixes with json_agg(manifest_row ORDER BY, changing nothing else. Fixed AB/BA/AB pairs,5s statementtimeout, current sorted union of Work tables, all nine raw arrays plus strict recursive numeric/type parity and canonical normalized digest parity. Keep all six client timings and bounded content-free EXPLAIN ANALYZE BUFFERS summaries; query text/parameters/catalogvalues never exported. Preserve actual pytest failure/coverage/GC/timer and original21checks20auditcommits; post-call measurements are diagnostic only. No blind retry after failure, SQLsettings/globalmutation, prior-candidate revival or weakercriteria. Validate pure controls/closedimports/source/runtime/PG bindings first; root onlywriter and Codex-onlyreview.

T133 predeclared candidate screen: retain every sample; a promising result needs lower candidate prepared-client and EXPLAIN server execution time in each of the three pairs, plus at least5% median reduction in both measurements. Otherwise reject this experiment as insufficient for a product port. This screen does not replace the unchanged strict/global acceptance gates, and any proposed port still needs broader semantic parity.


### T134 execution plan

Rebind the existing reviewed R5 orchestration to canonical current checkout, current locked interpreters, exact existing PG and held live reuse coordinator. Preserve source-tree guards and original cold/full/coverage arguments and stop-first-failure. Use an existing Linux cached environment only after common wheel bytes and locked-extra versions/RECORDs are verified against the current environment/lock; separately pin actual entrypoints and source imports. No installation, data/connection prewarm or benchmark mutation. Stream private logs for progress, preserve emitted artifacts and generated predecessor reports, and reclose operational descriptors after actual exit while retaining execution bytes. Missing coverage reports fail closed instead of being silently ratchet-skipped. Account each failure/skip as observed; no aggregate final closure until all required gates are met.

T134 timing witness: use only the supported pytest_assertion_pass callback for the exact already-stopped elapsed<1.0 assertion (current source line379), after both timer reads and all measured operations. No callable wrapping, profiler/tracer, changed timer or model output. A fresh per-process Python cache prefix ensures the pass-hook rewrite is used; real framework toy and bounded numeric/path/duplicate controls must pass first. Save the exact asserted elapsed value; do not substitute JUnit fixture-inclusive duration. This nonprofiling receipt applies only to cold3; the full backend keeps its original arguments.


### T135 execution plan

A source-only AB/BA/AB prototype preserves all84checksums (21×4versions) and reduces repeated expected-source checksum work from9.69–9.87ms to0.50–0.61ms, including a cold cache miss. This is not enough by itself to claim1s acceptance. Lazily capture validated immutable source-field keys per exact descriptor without changing dataclass constructor, equality/repr or fields; check current field object identities before any cache hit. A bounded32-entry pure checksum LRU admits at most1024statements/256tables/262144totalcharacters; unsupported/mutable/oversized/subclass/replaced-field values take the original uncached serializer. Write meaningful RED controls against baseline, exact checksum/parity and invalidation/constructor/subclass/mutation/bounds contracts plus native schema/role/drift/audit regressions. Use Graphifycheckedsource and Codex review; style/types/composition, actualsourcefreeze, then uninstrumented3cold/full8 acceptance with unchanged limits. Preserve T134 coldR1failure1.042606872s and its not-run stages; do not reuse its claimedroot.

### T136 execution

T135 global cold failure1.159185422s is retained. Existing T132 import counts measured plugin installation, not original imports. Use a private reversible plugin scoped to the exact benchmark and awaited store calls; fixed module names only, no inputs/content capture. Pure controls cover hit/miss, exceptions, nested imports, phase/thread isolation and restoration; Codex review before one source/runtime/PG-bound covered diagnostic. Reuse exact existing PostgreSQL through a fresh reuse-only coordinator after memory admission. Do not compare different-run wall times as causal evidence or run unchanged acceptance for green.

### T137 import correction

Graphify/source identify memory_service to pure memory_scoring and memory_append; both contain definitions/constants only and no dependency back to memory_service/settings/database. Add top-level module references and use their attributes in store instead of2local imports, preserving lookup of patched/replaced functions/classes. First extend existing fresh-process import/no-DB control to require both helper modules loaded before any store; reproduce RED. Then minimal product correction, GREEN import/disabled/append/scoring/audit-transaction tests, style/types/composition and Codex review. Integrate/main/push and rebind a new3cold/full8 acceptance only after source is frozen; preserve all failed receipts.

### T138–T144 execution plan

Use structuredJUnit inventory from globalT137, not repeatedunchangedfullruns. First reproduce/repair short temporary-workspace admission generically, preserving sentinelCAO_API_PORT=1 and everypytestworker/coverage/timer flag. Verify55Docker contracts andsocket subsets under the corrected harness. Update mocked MCP URL expectations to actual configured API base, retaining separatedefault9889coverage. Correct KAS snapshots to compare files and CLI mocks to supply real resolvedPath; keep all failure/PID/ownership assertions. Fix native harness fixtures (privatebytecache env, case closure, matchedrollbackownership, nativeOpenCode profile and frozenselection witnesses); use currentprojectvenv for -I callbacks, neverdisableisolation/namespacechecks. Fix explicit Dockercontext precedence perofficialCLI docs. Move the remaining CAO SQLitefixture to nativePG; narrow guard exceptions onlyto auditedupstreamread-onlyadapter/testfixtures. Batch reviewedrootcausepatches, targetedchecks andarchitecture/type/style, integrate main, then one freshsource-bound3cold/full8 acceptance; full8NFR1.331s remains a failing criterion despite3coldPASS.


### T145 correction sequence

Retain the completed T138 source/runtime/PG-bound failure and compare original failure identities. Graphify identifies vault_factory callers across Vault, API and CLI; current source verifies the tempfile confinement boundary. Add regression controls for basetemp containment and a fresh child invoking the actual Vault factory with configured TMPDIR and worker-like paths; retain RED. Change only OwnedPytestTemp.basetemp to a child of tmpdir, keep owner-only directories and existing socket checks. Run the helper/Vault negatives plus the183failed Vault identities under the corrected launch environment and8worker native IPC controls; preserve all failed receipts. Handle subprocess startup, Work input fence/restart observation and exact tmux cleanup as separately diagnosed defects; do not relax timers or cleanup authority.


### T147 first-guard evidence

Reproduce the missing diagnostic interface with owned-root/unknown-liveness/known-absent controls. Keep existing bool behavior and short-circuit order while adding an optional typed diagnostic list at each rejection. Exercise both omitted and supplied sinks and foreign sentinels. Wire only the scoped mockAPI test to save a0600 outcome outside the socket root before asserting the original bool. Run targeted guards and actual existing scoped case, then inspect first failed guard; a passing attempt cannot establish the cause of older false cleanup results. Preserve old retained socket/root evidence without action.


T147 caller evidence must set owner_stopped only after the managed server.stop returns: the fixture's _terminate explicitly confirms process exit or raises. A raised stop must keep the flag false and forbid probing/removing the namespace. The final false outcome after rmtree also needs a fixed root_not_removed reason, preserving the same exists predicate and bool result.

### T148 capacity execution sequence

Complete the bounded current-source runtime admission before the capacity run. Reuse the real mock_cli API/session/inbox lifecycle and strict owned tmux cleanup rather than create a provider-specific substitute. Verify Graphify relationships against current source before implementation. Implement a serial 2/4/8/12/20-worker ramp plus one sender per rung, with concurrent inbox submissions and a bounded mock processing workload. Observe actual processing overlap and retain source/runtime/process identities; an observed maximum below the requested target is a result to diagnose, not a simultaneous-capacity pass. Reuse the verified PostgreSQL coordinator; admit each rung only after memory and exact service identity checks. Retain per-rung timing, delivery, memory and ownership/cleanup records. Validate accounting and overlap controls before the first live ramp. Review results at 20 before admitting the optional 32-worker rung. Keep global, model-inference, graph and multi-instance gates separate.

After the retained 32-worker read timeout, run only that failed rung with private wrappers measuring the actual create_inbox_message and deliver_pending calls, plus a bounded same-server health heartbeat. Reuse the immutable successful 20-worker receipt only with its hash and unchanged product/test source binding. Prove wrapper return/exception behavior without services first, archive/pin executed support, and preserve original request deadlines. Do not infer event-loop blocking solely from endpoint source or total latency; correlate actual phase durations and health observations before any product correction.

T149 next discriminant: use private nested span IDs propagated through asyncio.to_thread to separate HTTP-await scheduling time from the actual delivery method and watchdog/status-triggered work. Measure outer delivery versus its locked body, pending-row/status writes, provider/status resolution, input guards/memory preparation, backend send and post-send callbacks. Preserve all original calls/arguments/exceptions and actual production workload; prove synchronous wrapper behavior and async context propagation before a single fresh 32-only diagnostic. Parent/child intervals are diagnostic bounds, not additive CPU accounting or permission to remove revalidation.

T149 pool admission reproduction: demand four overlapping native leases of one profile under max_sessions=4, retain distinct backend identities, and prove a fifth lease still times out while those four remain held. Preserve sequential reuse of one connection. If the current profile-keyed map blocks spare capacity, index physical entries independently while matching their immutable profiles for reuse/rotation. Keep every active/reserved/retired slot counted, including unconfirmed closes, and rerun pool/fork/rotation/authority/audit composition controls before real capacity acceptance. The concurrent-factory test must check the unchanged global cap rather than mandate accidental one-session serialization; the new held-lease test strengthens overlap and fifth-lease rejection evidence.

T149 bounded receipt dispatch: reproduce the endpoint waiting for delivery and the missing explicit-wake interface first. Extend the existing pending ordinal queue so API wakes and real ready events retain one per-receiver sequence, with separate bounded per-ordinal ambient-context/registry payloads. Keep the old pop interface for existing consumers/tests; the owner loop pops payloads, creates worker tasks within the captured context (Python 3.10 compatible), and then uses the unchanged tracked delivery path. Reject wake admission after the service fence, on a different loop or at full capacity; release queued context references on consumption/close. Preserve the POST body/status/errors, synchronous commit order and all delivery guards. Run sink/dispatch/context/overload/shutdown, API receipt and native PostgreSQL ordering/restart regressions before a newly frozen full ramp with complete memory audit pairs.

T149 output-read diagnosis: the fresh 12-worker trace reproduces GET3 timeout with output spans 3.134–3.147s, including 2.789–2.847s entering the terminal dispatch lock; ordinary nonreceipt verification is a no-op after acquisition. Source/runtime/support and strict cleanup are preserved. First add a controlled contended-lock RED, with protected receipt/ordinary-observation cases and ownership-inside-fence controls. Apply a narrowly scoped conditional wrapper only to receipt verification, preserving exact is True capability semantics and __wrapped__; keep all mutating/authority paths on the existing lock. Run receipt/canonicalization/recovery/output/API regressions and a fresh uninstrumented full capacity ramp.
