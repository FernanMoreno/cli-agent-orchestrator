# Research: PostgreSQL completo

## Inventario de origen (baseline previo a la retirada)

Tres stores: main por perfil (aprox.134 tablas), auth por instalación (5 tablas), peers compartido por usuario/host (2 tablas). Cuentas aproximadas de source: 28 ORM +5 runtime +71 Work +13 interactions +16 workflows +1 Beads; confirmar contra esquema ejecutable antes de baseline. El inventario previo registró 138 referencias `DATABASE_FILE`, 37 módulos raw SQLite, 197 tests con indicadores SQLite y 217 con seams de main (cuentas solapadas). Estos recuentos son candidatos de auditoría/eliminación; no son formatos o rutas soportados. La aceptación nueva exige cero soporte SQLite activo y pruebas relacionales contra PostgreSQL real.

Fuentes: constants.py:381; database.py:986/init_db; work_repository.py:648/transaction/read_snapshot; interaction_repository.py:402; browser_auth_repository.py; local_peer_registry.py. Relaciones de Graphify verificadas con código y log en graphify-impact.md.

## Decisiones

- PostgreSQL es el único almacenamiento relacional y la única implementación de base de datos de CAOS. No hay importador, fallback, dependencia, fixture ni herramienta SQLite. Sin dual-write, que rompería atomicidad Work/interactions.
- psycopg 3 síncrono para repositorios actuales y SQLAlchemy 2 con dialecto PostgreSQL para ORM. SQL nativo, sin emular PRAGMA/sqlite_master ni traducir DDL de otro motor. Record tipado permite columnas por nombre y acceso ordinal explícito manteniendo contrato del consumidor. Las validaciones de esquema y tipo usan PostgreSQL real.
- Configuración común PostgreSQL y targets main/auth/peer con namespace e identidad estable. Main co-transaccional para ORM/Work/interactions/workflows/runtime/knowledge. Auth namespace/credencial separados. Peers compartido por host/usuario con host identity, no interpretación de PID remoto.
- Un owner de migraciones PostgreSQL con baseline inmutable por componentes, checksums y fingerprint estructural pg_catalog. Historial legacy ajeno a PostgreSQL no se interpreta ni verifica. Los recibos de procedencia ya instalados en PostgreSQL y sus filas siguen siendo sólo lectura e inmutables.
- READ COMMITTED más locks de agregados/CAS/uniqueness para writes; repeatable-read read-only para snapshots metadata/receipt/high-water. Coordinación de bootstrap por advisory transaction lock; fences/cuts durables. No reintentar context managers con efectos externos.
- Secuencias explícitas sustituyen rowid; DOUBLE PRECISION preserva timestamps REAL de contratos actuales; bytes BYTEA; JSON canónico textual cuando participa en hashes/firma. Nuevos constraints/triggers PostgreSQL preservan checks/immutabilidad.
- Sidecars, Markdown/vault, sockets y perfiles mantienen filesystem con ownership/routing existente. Store ID deja de derivarse de una ruta de fichero. Credenciales del servidor no entran en agentes/Work containers.
- Managed personal: app y PG privados por instalación, peer PG compartido por usuario, sin puerto público, credenciales en archivos privados. External: targets explícitos y TLS verificable según entorno. No reutilizar recursos Docker ajenos.
- Backup PostgreSQL nativo y restore a un destino aislado con verificación completa de scopes, manifest, schema, filas, relaciones, secuencias y recibos antes de activar. El backup de origen y los archivos existentes permanecen intactos ante fallo.
- Rollback binario exige compatibilidad de schema PostgreSQL; un estado desconocido falla cerrado y nunca se borra, convierte o sobrescribe automáticamente. Los bundles OKF modernos de directorio/tar permanecen soportados sin payloads SQLite; el formato legacy `sqlite-dump.json` se retira.

## Fuentes primarias verificadas

[Isolation PostgreSQL](https://www.postgresql.org/docs/current/transaction-iso.html): READ COMMITTED permite snapshots distintos por sentencia; proyección compuesta necesita snapshot estable. [Locks](https://www.postgresql.org/docs/current/explicit-locking.html). [psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html). [18.6](https://www.postgresql.org/docs/current/release-18-6.html). Versión de imagen se fijará por digest real antes de acceptance.

## Alternativas evaluadas

SQLite local, importación SQLite y dual-backend rechazados por la petición explícita de retirarlo todo. Reescritura dinámica del dialecto rechazada por checks/triggers/locking no equivalentes. Reutilizar identidad path y flock entre hosts rechazado; store UUID y coordinación DB explícita.

## Pending recovery review findings — Spec021/T077–T079 (2026-10-14)

- **T077 first incorrect boundary**: `backup()` calls `_copy_state()` after
  `pg_dump`; recursive copying of `cao/` must omit leftover `pg-client-*`
  passfile scratch directories without deleting them from the source.
- **T078 first incorrect boundary**: `restore()` checks schema absence inside
  the per-dump restore loop, so a later occupied schema can be found after an
  earlier group has already been restored. Preflight all targets first, then
  record created schema OIDs/owners for narrowly guarded compensation after any
  later restore, verification, or auth-invalidation failure.
- **T079 first incorrect boundary**: `manage(..., command="rollback")` begins
  by stopping/renaming the current name without first validating ownership of
  both current and previous Docker names. Inspect both root labels before any
  container mutation so a reused name cannot affect another deployment.

These tasks remain pending; this research entry does not claim implementation or acceptance.


## T080: destination created by cold authentication imports

The R4 native Docker restore gate first failed because its restart health check
used database configuration instead of the already selected HTTP port. R20
corrected that test boundary and retained a regression. The subsequent R5 gate
reached the actual restore command, which failed. A private diagnostic against
the sealed R20 source and R5 image captured `FileExistsError` before any restore
write: the destination already contained empty application scaffolding.

Source tracing confirms `docker_install.admin()` sets HOME/CAO_HOME_DIR to the
destination. `restore()` validates the browser-enabled backup with `_config()`;
`_local_binding()` imports `security.auth`, which imports `constants`. The latter
creates CAO_HOME_DIR and log/FIFO directories at module import time. Therefore a
fresh maintenance process creates the destination before the absence check.
The native unit adapter did not expose this because its interpreter had already
imported those modules.

Decision: isolate only the restore maintenance container's ambient home in a
private disposable tmpfs. Continue to pass the real destination and PostgreSQL
configuration explicitly. Retain authentication, schema/ownership checks and
atomic publication; do not relax destination absence or mutate the backup.
Private raw diagnostics remain outside the repository with owner-only access.
This is a required recovery fix; it is not a passed Docker or global gate yet.


## T081–T083: strict memory round-trip NFR follow-up — 2026-10-14

### Observed failure

The root's fresh strict covered-main reproduction failed the existing
`test/services/test_memory_service_index_roundtrip.py::test_roundtrip_runs_under_one_second`
assertion at **1.1544596599997021 s** against `<1.0 s`. Its timed body covers ten
`MemoryService.store()` calls followed by scope resolution and index read/parse;
fixture setup is outside the test's `perf_counter()` interval. The failure is
recorded in `nfr-fix-r23/baseline-r1/logs/junit.xml`. The run used native
PostgreSQL fixture profile v3, run `4f0d61880628`, container
`f84d3eb5d81a4cae77ffb8a3873fb7316f7494ff4f596b1713d4954fd482201c` as recorded
in `nfr-analysis/pg256v3-strict-r1/owned-service-r1/metadata.json`. This is a
RED receipt, not an acceptance result; no threshold or timer change is allowed.

### Source and impact evidence

The focused private preimpact Graphify extract contains 18 current source,
test and fixture paths (767 nodes, 2,143 AST edges); its per-path hashes match
before copy, on the copy and after extraction. The extract is under
`nfr-fix-r23/preimpact-graph-r1/`; its source-verified call path is summarized
in `artifacts/preimplementation-impact.md`. The global Graphify output is
stale after SQLite retirement and is not used as evidence.

Source confirms the path crosses `MemoryService._legacy_repository()`, the
private `_LegacyMemoryAuditWorkRepository`, `WorkRepository._verify`,
`verify_component()` and `catalog_manifest()`. `verify_component()` still
checks target scope/store identity and migration history before comparing the
fresh nine-family catalog digest; private memory audit writes still use the
bounded lease manager and existing durable transaction fences. The suspected
query is the routine family of the one-statement shared manifest. These facts
identify an impact surface, but do not prove that its SQL plan dominates the
timed body.

### Conditional query hypothesis and constraints

The current routine query starts from `pg_depend` rows referencing `pg_proc`
and filters eligible objects with five OR-connected class-specific `EXISTS`
branches for constraints, attribute defaults, indexes, row policies and
rewrite rules. The trigger relation remains a separate `UNION` arm. A possible
plan improvement is to construct a small `target_objects(classid,objid)` set
for those same five families and join `pg_depend` by both object identity keys.
This is a hypothesis only: compare actual `EXPLAIN (ANALYZE, BUFFERS)` plans
and controlled durations before porting. Keep the trigger `UNION`, set
deduplication, routine namespace restriction, and existing behavior of
ignoring `objsubid`; preserve the exact routine definitions and normalized
rows. If the candidate does not materially improve the measured path, make no
product change.

Acceptance must compare all nine manifest families and exact digests for
main/auth/peer, multiple components, empty families and current drift. Include
all five dependency families and triggers, constraints/defaults/indexes/RLS/
rules, overloaded/nested routines, caller-owned snapshots, identity/history
and error order, and immutable DDL/checksum/ledger contracts. No catalog,
authorization or drift-result caching is permitted. Final validation keeps
three independent cold covered runs below 1.0 s, the backend 87% and Apps 90%
coverage floors, full eight-worker suite selection, and explicit accounting
for failures, skipped tests and host-gated omissions. T076 remains open until
its complete aggregate acceptance passes.


## T082/T084 query hypotheses — 2026-10-14

### Routine dependency rewrite: rejected

The private planner diagnostics used the native PostgreSQL v3 fixture with 71
tables and reported exact manifest parity. In `planner-probe-r1`, `EXPLAIN`
execution time was 21.298 ms for the original and 20.306 ms for the candidate.
In `planner-probe-r2`, the original was 22.103 ms and the candidate was 22.929
ms. The second run is slower, and the results do not establish a consistent,
substantial improvement. Retire the `target_objects(classid,objid)` rewrite;
leave the product query unchanged. These diagnostics do not satisfy the
strict memory round-trip NFR, and the earlier R23 cold RED remains a failure.

### PostgreSQL 18 `NOT NULL` definition: conditional hypothesis

A constraints-family diagnostic reported 8.198 ms for 1,407 rows with 4,317
buffer hits. This is a query-cost observation, not proof that a fast path for
one constraint kind improves the full round-trip. The staged
`not-null-probe-r1/summary.json` is marked diagnostic-only and reports 22.310
ms original versus 22.460 ms candidate with manifest parity on 71 tables.
Treat that result as preliminary and inconclusive while the root's final
measurement receipt is pending; it is not permission to port the candidate.

PostgreSQL 18's native constraint renderer emits the `NOT NULL ` prefix plus
the quoted attribute name, then appends applicable `NO INHERIT`, `DEFERRABLE`,
`INITIALLY DEFERRED`, `NOT ENFORCED`, or `NOT VALID` text in source-defined
order. The implementation reference is the official PostgreSQL
[REL_18_STABLE `ruleutils.c`](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/utils/adt/ruleutils.c#L2395-L2470);
identifier quoting delegates to
[REL_18_STABLE `quote.c`](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/utils/adt/quote.c#L23-L31).
This behavior justifies only a constrained candidate: read fresh
`pg_constraint` and `pg_attribute`; synthesize only for a one-dimensional
single-element `conkey` with lower bound 1 and a matching non-dropped
attribute; otherwise call the existing renderer. Select that query variant
only for major version 18 reported by the live connection. PostgreSQL 16/17,
unknown and future versions retain the old path. A bounded version-aware cache
may cache SQL text only, never catalog rows or manifest/verification results.

T084 must prove byte-exact native definition and unchanged nine-family
manifest/digest, snapshots, and drift behavior across main/auth/peer and
multiple components. Include flags, quoted and Unicode identifiers, rename,
drop, inheritance, partitions, and all fallback shapes. Do not modify the
original `_catalog_manifest_queries`. Port only for a substantial controlled
improvement in the unchanged FR-044 cold round-trip; otherwise record a no-go.
The three independent cold runs, full suite accounting, and 87%/90% coverage
floors remain required; T076 remains open.


## Final T084/T083 evidence and T085/T086 follow-up — 2026-10-14

### T084 final result: rejected

The later paired probe supersedes the preliminary `not-null-probe-r1` reading.
Nine-family digest parity held on the 71-table fixture, but no material speed
gain was established. R1 EXPLAIN was 22.310 ms original versus 22.460 ms
candidate. R2 EXPLAIN was 39.728 ms versus 35.874 ms, while five interleaved
reads did not support a candidate benefit. Paired R4 EXPLAIN was 20.741 ms
versus 27.301 ms; twelve interleaved reads also showed no clear gain. R3 ended
with `SyntaxError` before reaching tests and is preserved as a failed attempt.
T084 is closed as a rejected experiment; no product source changed and none of
these diagnostics count as a strict FR-044 pass.

### T083 latest focused selectors; aggregate remains open

The historical local E2E aggregate collected 37 cases: 24 passed, 13 failed,
and zero skipped. Its failures were 8 approval direct-runner cases, one script
lifecycle `422 prepared_plan_required`, three tmux lifecycle initial-500
cases, and one YAML-internal tmux case. The initial tmux and YAML cases ran
with an overlong private socket path (`ENAMETOOLONG`); this failure receipt
remains preserved. The revised 37-case scope is accounted as 36 local E2E cases plus one
prepared-node Docker selector, both green on source/test snapshot
`a88eef6b676705b4bff9f579d8b7dd197ab6f79971e19d80d8086de7e73d40e1`. The local
selection passed 36/36 with no failures or skips in 146.92 seconds. Receipt:
`nfr-fix-r23/service-r2/owned-service-r1/backend-run/local-e2e-r4/artifacts/pytest-local-e2e-r4.log`,
SHA256 `274fc43e9b60b9ae007fe83351a23235a4391f0837538f7179c709ff7ac2126c`. The
R6 prepared-node Docker selector passed 1 test in 85.23 seconds and verified
approval/review, unprepared `422`, run-step `409`, exact replay, stable attempt,
artifact cleanup, frozen source, owner API, durable binding, snapshot, receipt,
and restart hashes; this closes the previous reader-owner fixture omission.
Receipt: `interaction-e2e-r6/t020-prepared-docker/prepared-selector.log`,
SHA256 `796d108f275c16a31cf5009f5308589d1db51f884865795c41f8db818dcd82d2`. The
focused R3 T019/T020 selection passed 14 tests with product source unchanged.
This closes the local E2E omission category, not global T083/T076. The prior R3
runner exit-5/zero-collected stale-node failure remains preserved; the removed
script-parameter selector no longer exists.

The source-bound Apps run passed exactly 92 tests across 12 test files on 2
workers at 478/531 covered lines (90.01%) in `frontend-final-r4`. The source-
bound Web run passed 504 tests across 45 files; TypeScript and Vite builds
passed, and all five build files matched main (`web-final-r2/final-receipt-r2.json`,
SHA256 `1209876fcded57310a57c0964a5598a1f61154ce9cd033ce7516a45e8f2bd0c9`).
Source-guard findings were zero before and after (not a test count); 13/13 provider CLI binaries were visible on PATH (availability only, not authentication or live-generation evidence). The separate T097 selector slice passed 5
tests; the R3 QEMU run completed partially and is recorded in the final acceptance update. R21 is the latest completed full backend run: 17,390 unique selected nodes, 17,252 passed, 113 skipped, and 25 unique failed node IDs. Its phase summary reports 15 FAIL and 12 ERROR events (27 failure-phase reports); two nodes failed in multiple phases. The run exited 1 during postrun verification. Coverage measured 67,793/77,816 statements = 87.1196155% with branch coverage disabled: above the line floor, but not a passing full-suite gate. Older R13 coverage was 68,979/79,530 = 86.733308%. The final R3 three-run strict receipt failed all three unchanged `<1.0 s` checks at 1.0844142050045775 s, 1.0654824020020897 s, and 1.0055572290002601 s; the source and HOME guards passed and no prewarm was used. An earlier diagnostic body reported PASS without a logged duration and is not acceptance evidence. T097 selector evidence is 5 passed; the R3 QEMU run completed partially and is recorded in the final acceptance update. T088's actual Linux PostgreSQL/Cargo gate passed; macOS matrix execution remains pending. The revised 37-case scope has not been run as one aggregate. T083 and T076 remain open.

### T085 source and dependency evidence

Before the authorized source edit, the current import edge is
`audit_log.py` → `wiki_compiler._sanitize_for_log`. Hashes at this preimpact
point are `audit_log.py`
`a8c910a9e3716934003d038067f169391e75769183f1dcf1a83daa1b8c4e76e3`,
`wiki_compiler.py`
`a5675e7a2db86fc13a1449f3d8d465397f52112839fa58c414740fed8c51be76`, and
`memory_service.py`
`b372dd79b6d1e7c57f7d14fc34f0b97c5161b139b14252b387b6d91e09dd8938`.
The private preimpact graph and source seam are documented in
`graphify-impact.md`; current source inspection confirms the sanitizer is a
four-regex, standard-library string transform. The authorized change moves
only this leaf, preserves the compiler's compatibility alias, and removes the
compiler import edge from `audit_log`. No append or scoring logic moves.

### T086 compact transport evaluation — no-go

The compact JSON-array row transport and exact reconstruction candidate did
not show an accepted material gain and is closed as a no-go for this task.
Profile attribution recorded approximately 0.122 s in JSON decoding, 0.073 s
in digest construction, and 0.112 s in lazy imports. These are measured costs,
not demonstrated savings from the wire rewrite. A private `orjson` decoder
comparison gained about 0.95 ms per check (roughly 20 ms across 21 checks); no
new dependency is accepted from that result. Require paired measurements on
the same frozen source and fixture plus exact row/manifest/snapshot/drift
parity before reconsidering T086. No compact-wire or decoder change is
authorized by the current evidence.


## T085–T087 current evidence — 2026-10-14

### T085 focused implementation complete; aggregate gates remain open

The final focused group passed 137 tests with 3 warnings in 75.77 seconds for
`test/services/test_log_sanitization.py` and the four sanitizer caller modules;
the complete Kimi module passed 29 tests in 16.44 seconds. This supersedes the
earlier 9-test slice and stale-metadata setup-error run; those receipts remain
historical. T085 is complete for the focused sanitizer and caller contracts.
It does not pass FR-044 or close the global import, backend, coverage, or T076
gates.

Post-change hashes: `services/log_sanitization.py`
`af3f71ad68c37fea90452e161b8430b75ea2b54c4acaf7b18d2a12d78fe964c5`,
`services/audit_log.py`
`d5dfc62f57092bf959140021e66375b4b83f3949e27ce89a9e1364a9b60438c7`,
`services/wiki_compiler.py`
`deb40debd22689295cbd9f8118188e6ad312da0237b6e41cbe9460d5d733ff4b`, and
`test/services/test_log_sanitization.py`
`595237a54e07788795e616c92f84fc2e5f43c17505cfac3489330c4062ba687d`.
The clean postimpact Graphify has 545 nodes and 1,142 edges across ten files,
SHA256 `1aa33b47dc47accba75bb16d5f539c86ae38aa62cbe584324b4316cfce9619f1`.
It confirms `audit_log` and `wiki_compiler` both import the leaf sanitizer and
the former compiler dependency is absent.

### T086 measurement update — no-go, no port

The compact transport's R4 peer timing was approximately 31 ms and variable;
the sampled Work policy collection was empty, so this run does not establish
behavior or performance for populated policy rows. Close this experiment
without a port; any future evaluation needs a separate task and a populated
policy fixture.
The separate private `orjson` comparison measured about 20 ms of decoder
savings over 21 fresh checks, but no dependency change has been accepted and
the result is not a strict round-trip pass.

### T087 local completion race and remote projection failure were reproduced

The original local same-turn completion regression is covered by the existing
event/barrier tests and implementation work. A separate remote composition RED
was reproduced on unchanged T087 source using the actual
`terminal_service.send_input.__wrapped__` remote branch through
`run_agent_step`, with no database, provider, authentication, or model calls.
The peer returned turn sequence 7; the local monitor remained `(turn=0,
turn_done=0)`, `notify_input_in_flight("remote-term", 7)` returned false, and
`run_agent_step` raised `StepExecutionError(kind="reconcile", action="inspect",
delivery_may_have_occurred=True)` before awaiting completion.

The private reproduction is
`nfr-fix-r23/remote-run-step-boundary-r1/repro_remote_notify_boundary_main.py`
(SHA256 `95823e8df98756c36b1a26d40a1ea363c0b805f17f327455c2cb0093b97b5edc`);
its unchanged-source receipt is SHA256
`3657ee36b2f2a18a3b47d2d418b7b470dffbf9c569b46690b82262322b61f9b1`. The
first incorrect boundary was `StatusMonitor.notify_input_in_flight`: it
compared the peer's sequence with local `_turn` state and had no remote
projection path. The implementation now validates the exact sequence and
completion fields against the authoritative projection without modifying local
monitor state. Missing, malformed, and superseded projection cases fail
closed; an exact turn that is already completed remains acceptable.

### T087 focused implementation verification

The local post-send boundary now acknowledges the existing dispatch turn rather
than opening a second turn, so a completion observed while the transport call
is blocked is not demoted. The remote boundary accepts only an exact current
peer projection and valid completion sequence, including an already-completed
current turn; it leaves local monitor counters/events unchanged and reconciles
on stale, missing, or malformed projection state. Ownership,
interaction/authentication, receipt, delivery, cancellation, and at-most-once
guards remain in the path.

The deterministic pre-fix RED selection had 14 cases: 4 failed at the intended
boundary and 10 passed. The final native four-module selection
(`test/services/test_agent_step.py`, `test/services/test_status_monitor.py`,
`test/integration/test_interaction_wait_composition.py`, and
`test/api/test_run_step.py`) passed 285 tests, 0 failed, with 46 warnings in
124.87 seconds on the native fixture. A separate real lifecycle/restart
selection passed 4/4 in 154.28 seconds. After formatting, the focused selection
passed 19 tests, 0 failed, with 17 warnings in 10.28 seconds; pinned Black,
isort, and `git diff --check` passed. Run and review receipts are
`nfr-fix-r23/t087-remote-boundary-r4/artifacts/run.sh` and
`nfr-fix-r23/t087-remote-modules-r3/artifacts/composition-review-t087.md`.
T087 is complete for these focused contracts only; no full-suite, coverage, or
strict-NFR claim follows.

Final scoped hashes are recorded in the private composition receipt. The
changed source is `agent_step.py`
`b5057128a634ff546044d96ee0c0b2b17c5195f7ee2366a4c8f1a526e6954cea` and
`status_monitor.py`
`3d1e08729adbc9d42068199498bc4d3dbfb5462c689171f214a84151d7c4dbce`;
the formatted tests are `test_agent_step.py`
`e8a0ef13ed1803ed3f5e0c1660ef06c4a11c07d1236b87317bfd6e219e009211` and
`test_status_monitor.py`
`53452c5920d87bd1640bf3bdf896e72aa41572f90435f51bf5fe3cb46c6d658c`.


## T088 preimplementation research — native PostgreSQL for Rust CI

The Rust live endpoint tests require an actual CAO server with native
PostgreSQL. Current source inspection traced configuration resolution through
`config.py:75-83`, startup through `main.py:1530+` and
`cao_server.py:546`, schema verification through `database.init_db`, and
isolated native fixture setup through `test/postgres/fixtures.py:589`. The
three endpoint cases are in `tui/tests/endpoint_contract.rs`. The Rust matrix
currently does not configure the required PostgreSQL service/bootstrap before
running those live endpoint contracts.

The authorized scope is limited to new `scripts/ci_rust_contracts.py`,
`test/scripts/test_ci_rust_contracts.py`, and the Rust job setup/commands in
`.github/workflows/ci.yml`. Each Linux/macOS job must own an ephemeral cluster,
create distinct main/auth/peer stores and migration/runtime credentials, verify
the schemas before launching the actual server, pass it private configuration,
and fail on setup, schema, health, or endpoint failures. The three live
contracts must run without mocks or skips. Keep all existing Cargo commands,
timeouts, and test-selection semantics; cleanup only the job's owned cluster
and targets from its private owner-only run directory.

The first deterministic contract is RED: isolated stdlib test exit 1, one
failure because the workflow lacks a `scripts/ci_rust_contracts.py` invocation.
Log: `nfr-fix-r23/rust-tui-contract-r1/ci-contract-red.log`; test SHA256
`94743b1150098a3d28df657cbadff66206ac7d72fcfa7304668bc826c546d606`. The
workflow source inspected before implementation has SHA256
`a629003f888a57317c21a3e9a7bc864a4c5bbc7a6cdb839783299d60f03d3ef1`.
Final implementation hashes: helper `5d8c2f842ee59be5884e2e0b85e87d6c5628d81bf23330654d2d28f2c507c9ed`, unit test `f52821ba5417c3440309c1a80d8c0308662402f07589ae1cda6094e718186441`, workflow `127ee88669a3da554b7a1943161d9ae6c00b0baac729fb34ceb9c30093513cfc`. The entrypoint import regression first failed with `ModuleNotFoundError` under isolated `-I` mode, before the helper test corrected project-root resolution and passed 8/8 (receipt `/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/rust-tui-contract-r1/entrypoint-import-r2/unittest-final-receipt.json`; raw log is beside it). The isolated workflow contract passed 1/1. The final native Linux receipt is `/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/rust-ci-native-r1/records/native-helper-cargo-receipt.json` (receipt SHA256 `62baf3cd0ab96c86c0c006afa1f8ad1469256352801aed2a385c5bb21fa72be9`; summary SHA256 `5cc9f319370d2acd17c7f38c49c0bc9912a281a9d6e7b3978874830975a27650`): helper-owned PostgreSQL 18.6, `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, and `cargo test --locked` all passed; seven test binaries reported 262 passed, zero failed/ignored/filtered, and endpoint contracts 4/4. The 1,607-file source manifest SHA256 `7c69cf398a6a6fa710eede83e7bf83225c7c1b01866b6263ec80c777b5cb3c26` is byte-identical before/after; no PostgreSQL process remained and the private runner temp directory was empty and removed.

Final Graphify is `/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/rust-ci-native-r1/records/graphify-final-receipt.json` (receipt SHA256 `7ee462420a5c8e8937533848722a7c95d263645f64e67d4fd74e1c501dc005dd`), graph `acfe18d5ae10a237efdbe6b399ac0560abcebdfe6092bb1bda391d4c6b164589`, with 1,099 nodes/2,576 links and no failed sources. Its 17-file source digest is `ea77404b45164d406dbd014ece3834dc1e55f2f8d555534405c8e3cbcbeebb43`. Linux is verified; the macOS matrix leg remains unavailable, so T088 is still open and its full requirement is not complete.

The original six-file local T087 hypothesis below remains useful as the local
state-ownership analysis. The remote projection path is captured separately in
the additional preimplementation Graphify entry in `graphify-impact.md`.

### T087 local same-turn hypothesis (historical analysis)

The preimplementation source hashes were `terminal_service.py`
`5d4275c1dfff3a13c04c37db5fd2b05c5e0b5628c09fdc3cf608986bd46bf09e`,
`agent_step.py`
`dbbe657989e932912a99fc8503c44cefce3c2c5625e552886e996895efef37dd`, and
`status_monitor.py`
`a7a4317b73303f216bca08d9c0c6e7f81f4d7c22d290a6b7425301fd7a0f6386`.
At those hashes, source inspection showed that `send_input()` opened the real
turn before its blocking key send and returned that turn sequence; after the
worker-thread send returned, `run_agent_step()` called `notify_input_sent(...,
assume_processing=True)`. The then-current monitor method opened another turn,
incremented its sequence/generation, and applied an unobserved PROCESSING
assumption. This source ordering led to the event/barrier RED contract, which
reproduced the race before the focused implementation described above.

The T087 preimpact Graphify is the private six-file extraction
`nfr-fix-r23/graphify-t087-pre/source/graphify-out/graph.json`, SHA256
`b368de2fc579ff1819f4508d223097c146b2c3fb9f6d63d49ecaf1b4b6e9a419`, with
893 nodes and 1,896 edges. It contains the current `send_input`,
`run_agent_step`, `notify_input_sent`, `notify_input_delivered`, detector,
completion-wait, and focused service-test nodes. The graph resolves
`run_agent_step()` to `_wait_for_completion()` and the monitor transition to
`_apply_detection()`. It does not resolve the module-global
`status_monitor` attribute call sites as direct call edges; those source lines
were verified manually. The six source/test inputs and their hashes are
recorded in `graphify-impact.md`. Existing unit tests cover turn increments,
stale ready capture, current-turn completion, and run-step completion, but do
not yet exercise completion during the blocking send followed by the second
assumed-processing call.


## Final R3 source-bound acceptance update — 2026-10-14

### Source and unchanged strict NFR

The final source snapshot is `/home/felni/.cache/caos-spec021-evidence-20261014/backend-final-runner-r3/source`, bound to the 3,111-entry manifest SHA256
`ac4351478f2173853ab6e38af731722f8cb8b58043146877bcd002b6d0c9bcb6`
(HEAD `02309f3f1e8cd1cf3b66dc92ca4d9e6c21ccf2db`). The sibling environment
contract's internal binding is
`14c94804967e1fee4044a9f7281f8396b48aa999692957a507f4237262447207`
(the JSON file SHA256 is `a47c303c379f9d8b01c3fd3092255399d61effa45a0c2ea6521ddc3848f10f03`).
The pinned Python 3.12.13 and native PostgreSQL v3 metadata are bound in the
strict receipt. The three independent cold covered runs selected only
`test/services/test_memory_service_index_roundtrip.py::test_roundtrip_runs_under_one_second`; each collected one test and failed the unchanged `<1.0 s` assertion:

| Run | Body duration | Result |
|---|---:|---|
| run-01 | 1.0844142050045775 s | failed |
| run-02 | 1.0654824020020897 s | failed |
| run-03 | 1.0055572290002601 s | failed |

The receipt says no prewarm/cache mutation; source-before/source-after and HOME
checks passed. Summary SHA256 is
`a5eed9d88077de955c4698132539f81c9b275a0ef41cb34e257150ab79edca92`. This is a
reproduced failure, not an NFR pass; T081/T083/T076 remain open.

### T097/T098 QEMU host acceptance — failed partial run

The sealed R3 QEMU acceptance expected 91 cases. Group 1 passed 8 in 882.33 s;
group 2 failed both SIGKILL proxy cases in 301.30 s; 81 cases were not reached,
and zero were skipped. Both failed because dispatch started but no upstream
callback/setup-to-callback observation arrived within the unchanged 65-second
budget; cleanup later surfaced `DeliveryUncertain`. The runner exited 1. This
does not identify TCG as the cause. Postrun evidence SHA256 is
`41ed2c4aeb71267f2f4cca8500b81882c92a481ff31a5db39d60792cf71ba5e8`; QEMU log
SHA256 is `c718324f902d8f4be2452f05d4bb9d178707196b775f9a93a3140c41c094f78e`.
The source manifest was identical before/after. Separate cleanup proof verifies
marker database OID 1934096, owner `cao_spec021_admin`, nonce/run/image row,
marker removed, runner container absent by immutable ID, and the owned PostgreSQL
service still healthy. Cleanup proof SHA256 is
`140961f76756a4f4428797cb07d15536cd757d1ca5cd57925bb9e46d7badb625`.
The separate selector-contract summary was 5 passed; it is not the 91-case
acceptance result.

### T092/T095 provider harness and real journeys

The current offline harness receipt records exit 0, source unchanged, and HOME
unchanged. Its four source/test hashes are in
`current-offline-fixes-r1/receipt.json`; the selection passed 18 tests across
two modules with no skips (pytest log 11.99 s), bound to four source/test input
files. It includes `Path.home()` resolution before
imports, child HOME/PYTHONPATH source binding, and the formal offline
environment contracts. A separate Claude config-path test module passed 15
cases; OpenCode list tests passed 27 cases, and a focused v2 list selector
passed in two fresh R40/R41 runs. These do not represent live provider calls.

The planned provider matrix has 9 nodes and 54 conditional scenario slots;
these are not 54 tests and do not imply quota or provider calls. Staged provider
metadata was used without copying credentials or provider logs into the source
tree or documentation.

The earlier bounded Claude Code journey completed with two answered and
acknowledged questions in 29.29 s. An earlier Codex attempt exited 0 but
produced no questions or acknowledgments; retain that failed receipt as
history. The latest Codex journey, using the private
`provider-harness-upgrade-r1` candidate, passed in 14.17 s: two questions,
two delivered answers, and one acknowledgment whose count, group, question
IDs, receipts, and versions matched. Its auth and operator environment guards
passed. Receipt
`provider-harness-upgrade-r1/artifacts/live-r1/codex/receipt.json` has SHA256
`23ed395e80178b2d512c2edb1a945001e4459485688f99ea4233503277f06fd2`.

The latest private-harness OpenCode journey exited 0 in 11.11 s but failed
with `cli_exited_without_ack`: zero questions and acknowledgments, with one
`step_start` and one `text` event. The cause is unknown; a read-only MCP
preflight is pending. Its auth/operator guards passed. Receipt
`provider-harness-upgrade-r1/artifacts/live-r1/opencode/receipt.json` has
SHA256 `76dfae9dbb375c40de6b1a50938b6dc4cc03442407c1fb538a88fc939611e9d6`.
These latest Codex/OpenCode receipts came from the earlier private harness. The bounded harness implementation has since landed in the current source tree under T096, but these receipts are not live journey evidence for the new bytes. SC-011/T095 remains open; the receipts contain only
sanitized metadata and no credential, prompt, or provider-output content.

The offline harness receipt itself is SHA256
`dca2b472773a9d724dfdc4a6cb95425132505dd147e77bc6107c46a03cad163c`; its
pytest log is `7c72c55d2f81bcf518c2ec82efca27881363369905c615382f2358c249e0a2ad`.
The four frozen inputs are `interaction_cli_journey.py`
`874875024402e0f98c1deaaca9f005477f656a000b7c487223d3894def99b6b2`,
`test_interaction_cli_journey.py`
`c931c4519b6c0dc55c1869a175ff89e4382f1312d8b12e01162bdd8d688aed19`,
`interactions_runtime.py`
`f0ae1b44cec4ca7aaaab5d3788133eb5c8d3c8241e4a227e4d6c2f07e20a634c`, and
`test_interaction_runtime_environment.py`
`54e96746de01b06a769a34efc1d5a516b768df44439bd4d590f4f9d169e42dd9`. The
recorded source correction resolves `Path.home()` before imports that consult
it and binds child HOME/PYTHONPATH to the selected source. Four test import-order
changes were isort-only; no non-import AST change is attributed to those edits.
The Claude child config path is passed via `CLAUDE_CONFIG_DIR`; config-path
module log SHA256 is `b2549de047018f5e048a024c5b15808bc6155e6671213afc3f656412d3048532`.

Provider result hashes: Claude Code `result.json`
`85c872b25549c9911ebc1ddcfd9f9b61fba8798bfe09bb37229a3b89b87f34fb`; Codex
`result.json` `f7a8adefbc77c856d7303e39bd544b4ed7ee56e545319328c725b0a814ffc6c1`.
OpenCode R39 list-module log SHA256 is
`dcae911fabfa37626bfd9a3666a6ccc8ac0e5b3cc61d41d3022f10275f58971d`; focused
R40/R41 logs are `2938bb6e99505ef5f25a5c63d71da689631b8c502ef370056e4d66d19988894a`
and `abeb7ce298267694af0a293cecac2795de455fa208a76420411141efa2c6b667`.
These test receipts establish harness/list behavior only. Auth/config metadata
was checked without copying credentials into these docs or receipts.

The R4 guarded package verification passed against source manifest
`c4827350259bb4b4815126d11c15b2f9141cc11a8b71716f335cf8db6b37407e`: 389
wheel/sdist modules and five active SQLite guards passed, runner exit 0, and
HEAD/index/HOME remained unchanged. Its receipt is
`coverage-contracts/package-verification-r4/artifacts/guarded-package-result-r1.json`
(SHA256 `f0a4c966274532de62db189ff10441edfbc666cf3951a0137a5d496bb83fc13f`).
The separate R4 Web20 run passed all 20 tests with zero skips and exit 0; its
log SHA256 is `159ae898419dad4115e61b38686b65a671e7fce76d9087712905eb8a97706554`,
and its summary is
`frontend-private-r3/web20-20261014-214156-714984-ac1ogaku/summary.json`.
These are scoped package/web checks; they do not establish full backend,
provider, or strict-NFR acceptance.

### T089–T091 experiment outcomes

T089's 20 paired raw/digest-parity runs were 23.147 ms original versus 23.163 ms
candidate; reject shared-column selection. T090's private Pydantic Core decoder
was about 24 ms across 21 checks; reject because no accepted end-to-end gain
was established and no dependency is authorized. T091's Rust encoder was
3.510 ms versus 3.327 ms for the standard-library encoder; reject it. These
experiments changed no product source/dependency and none count as FR-044
passes. The one intrusive cProfile observation at 1.755 s is diagnostic only;
the 21 full checks and 20 audit commits remain required and unchanged.

The no-go probe receipts are T089 `paired-probe.json` SHA256
`cb6ee1ee54543eebfdebac0f748afbf3dff12113c2de63109614771ab1e64b91`, T090
`benchmark-result.json` SHA256
`a45897a9d876bcf02259a5f00effe35f1dbb951a1e75658ad42f0043f9c609b5`, and T091
`encoder-bench.json` SHA256
`13d123848352edf6c60d695b3a6775623acac6d58d6988187637cba6e15ab587`. The
current intrusive cProfile summary SHA256 is
`532c727d52995dd917f3b65c5840abca30c015d2e1df16226d6a04dcad2da40c`; its
1.755-second measurement is diagnostic only and is not an uninstrumented FR-044
run. The full 21 verification checks and 20 audit commits remain unchanged.

### T093 Core candidate is in main; performance acceptance remains open

A source-bound, focused Graphify over `memory_service.py` and `database.py`
identified the cross-tier metadata lookup before file/index publication and
the metadata upsert after publication. The graph has 430 nodes/909 edges and
SHA256 `24c7414a4a72fef52a311b4dd3361a38d90603e59372da0d6fb3075ce667d063`;
input source hashes equal the corresponding R3 manifest bytes. The source
proof SHA256 is `a0699d7bef059b993fd9ff2c65370419bbeffce392f42caff29799d6174bf857`.
The Core candidate is now present in main at
`src/cli_agent_orchestrator/services/memory_service.py` SHA256
`58ec723493698a8c95ebb0a99e4781a4c469608115e8af9a1f6fbc994a89814f`, with
focused native test `test/clients/test_vault_schema_migration.py` SHA256
`33f89d93251747e538da44b876f7ca07a578769a0cea097f013d275992bd638f`. The
fresh native main caller selection passed 21 tests in 25.798 s; JUnit
`current-core-callers-r1/junit.xml` SHA256 is
`5aac5e4a69c48871feca0499525c9066888646e4c8190de59adb7f8455290fd8`.

Private paired run 02 measured baseline 1.105620873 s and Core candidate
1.004116244 s, an improvement of 101.504629 ms. Both failed the unchanged
strict `<1.0 s` assertion. The candidate's five native semantic probes passed
and ten metadata commits were observed. Baseline and candidate reports are
`nfr-fix-r23/metadata-core-candidate-r2/baseline/run-02/logs/candidate-report.json`
(SHA256 `2fb2312bf06425233f5ecf61ab72f5b3a4dec5d28c38968be2c9255413050469`) and
`nfr-fix-r23/metadata-core-candidate-r2/candidate/run-02/logs/candidate-report.json`
(SHA256 `fa7032803c5d08965edccd61ab30e8dd3c099380f047ff798e20732b28b2c5b7`).
The separate Core/decoder pair measured Core at 1.032364007 s and combined at
1.120388702 s; both failed and combined was slower, so the decoder change was
not ported. Both post-timing catalog-digest audit probes errored with
`AssertionError: actual post-timer PostgreSQL catalog digest changed`; their
cause remains under investigation and they do not establish a data mismatch or
a pass. The combined pair receipt is
`nfr-fix-r23/combined-core-fastdecode-r1/paired-runs/pair-01/pair-receipt.json`
(SHA256 `d9982040d92fd8d6b9c0efb42fe3204bd850abc6445fa30aa962128cdc93ab13`).
Keep all reads fresh, preserve exact predicates and commit/error order, and do
not cache results. No FR-044 or T093 performance acceptance follows; T093 stays
open pending resolution of the audit probes and three unchanged cold strict
passes.

### Latest Core, regression, and provider evidence — 2026-10-14

The current Core candidate source is `memory_service.py` SHA256 `58ec723493698a8c95ebb0a99e4781a4c469608115e8af9a1f6fbc994a89814f`; its focused test is `test_vault_schema_migration.py` SHA256 `33f89d93251747e538da44b876f7ca07a578769a0cea097f013d275992bd638f`. The 21-test native caller slice passed in 25.798 s (JUnit SHA256 `5aac5e4a69c48871feca0499525c9066888646e4c8190de59adb7f8455290fd8`). The latest 11-module no-coverage memory regression selected 234 tests: 233 passed, and the unchanged strict round-trip assertion failed at 1.161985059 s; 0 errors and 0 skips. JUnit SHA256 is `2673890c4974210e28e3f7a45c25bceaa05aa72c41092eb68168b1863ddc6794`. It is not full-coverage NFR acceptance.

The scoped static review passed all four checks: Black/isort on the two changed files, ImportLinter, and composition (399 files, 1,775 dependencies, 5 kept, 0 broken). Receipt SHA256 is `d82f19af42bcc3ea94460a531390ed489459c1bf031e8307667a46aa18b7cf7f`. Independent manual review found no blocking Core conversion defect. It did identify that cross-tier exclusion remains a preflight race because native topic and vault projection locks do not coordinate; this is pre-existing and was not introduced by the Core conversion. Review SHA256: `38188ba53187f7e8818df2eeb840eecde19bd31b7392008bb8fa44e4b04d50a6`.

Private Core run 02 measured 1.105620873 s baseline and 1.004116244 s candidate, both over the unchanged threshold. The later Core/decoder pair measured 1.032364007 s and 1.120388702 s, also both over threshold; the combined arm was slower. The post-timing digest audit probes errored and remain under investigation (paired receipt SHA256 `d9982040d92fd8d6b9c0efb42fe3204bd850abc6445fa30aa962128cdc93ab13`). These are failures/diagnostics, not acceptance or evidence to lower the threshold.

Latest provider receipts are from a private harness candidate: Codex passed in 14.17 s with two questions, answers, and matched acknowledgment (SHA256 `23ed395e80178b2d512c2edb1a945001e4459485688f99ea4233503277f06fd2`); OpenCode exited 0 in 11.11 s without a question or acknowledgment (SHA256 `76dfae9dbb375c40de6b1a50938b6dc4cc03442407c1fb538a88fc939611e9d6`). Claude's earlier two-question journey was acknowledged. The provider matrix remains open. The QEMU2 two-selector diagnostic completed 1 pass and 1 failure (0 skipped): `after_upstream` passed and `before_upstream_effect` failed. The marker database was removed without cleanup error; root verified the runner container absent and the PostgreSQL service healthy. This does not accept the full 91-case QEMU run; T094 remains open.

### T096 main-bound provider harness and QEMU2 diagnostic — 2026-10-14

The four current harness source/test inputs and their hashes are:

- `test/live_provider/interaction_cli_journey.py` — `8b136a5746e101f16f94c540d99e2db2d69d55f5d9fab62f3ea8e3d39074e130`
- `test/live_provider/test_interaction_cli_journey.py` — `2792ec1d699d2f78b63254b37fd07da86ba18cad3044d632575405abf2f5f890`
- `test/live_provider/opencode_interaction_journey.py` — `848d150f87136fec84e425db76ad305b62c4821cdc59d1f94761bf1a4e9e44fd`
- `test/live_provider/test_provider_harness_upgrade.py` — `efd58e94f18a8b8b034afb518d3f158199b5274e1a5fc1cabe6330bc95ca64bc`

The main-bound RED selection ran 76 cases: 73 passed and three unsupported/metered Codex auth-profile staging cases failed (JUnit SHA256 `35dcac728e1d92f77a7d2afc130be30fd5702ced6edbb0f95d5b725ecc18cdab`). The correction validates the source auth profile before overlay setup, preserves the operator/source bytes, and rolls back failed staging. The fresh source-bound run then passed 76/76 across the two test modules, no errors or skips, in 3.211 s; JUnit SHA256 `89415323d7a5a44cf7418c31f42d2891d76a2335a7ef7b85bfc8e934c91916e7`. Origins confirm the application package and PostgreSQL fixture were imported from the current main tree. Black, isort and compile checks passed. These are offline contracts; they do not prove live provider journeys on these source bytes.

Positive live Codex and Claude receipts remain historical private-harness evidence. OpenCode's latest live attempt remains a failure (zero questions/acknowledgments); the production-config headless journey is a separate mode from its `mini` TUI readiness path and must not be described as a successful interactive provider journey. Keep T095/SC-011 open.

The QEMU2 diagnostic receipt SHA256 is `51cbee10d4c702e04d28ed6ec7484c93306f0a51024900f4a83758353e93972e`. It selected the `after_upstream` and `before_upstream_effect` SIGKILL cases only: the first passed, the second failed, and neither was skipped. Cleanup removed the marker database without an error; the runner container was absent by its exact ID and the PostgreSQL service remained healthy without OOM or restart. The trace shows dispatch at 28.953 s, backend start at 40.032 s, setup ACK at 69.440 s (12.754 s after its wait began), GO at 90.679 s, timeout at 94.022 s, and callback at 104.070 s. Thus callback was 13.391 s after GO and 64.038 s after backend start. Source review confirmed the 65-second aggregate was anchored before dispatch preparation; the correction changes only the test helper to wait for the upstream event or real dispatch future, preserving the post-SIGKILL/cleanup wait. Ten offline contracts pass, but no fresh QEMU run has launched after this change; a prepared tar does not establish acceptance.

### T093 owner-JSON loader no-go and T094 offline correction — 2026-10-15

The private owner-JSON loader pair preserved exact semantic/call-counter/catalog/loader proof checks and recorded unchanged main-source and HOME/CODEX_HOME guards, but both measured bodies failed the unchanged one-second assertion: baseline 1.306698168 s; candidate 1.565738595 s, 259.040427 ms slower. No product change was ported. The pair receipt is `nfr-fix-r23/owner-json-loader-r1/paired-runs/pair-01/pair-receipt.json` (SHA256 `dcb51b9cb3cc2fdb1326097b60e584221ceb6fe64e8c30f5442e2c55c2b3088d`).

The T094 helper correction removes only the synthetic dispatch-anchored aggregate wait; it awaits the upstream event or the real dispatch future and preserves the later 65-second post-SIGKILL/cleanup wait. Its current offline contract selection passed 10/10 in 4.01 s (JUnit SHA256 `cef799393c147333824de70fa6bc40b9260e1bcc938c43a84ee52f8d11eadcc2`; pytest log SHA256 `238189ef2b5b53c54b71fae94f4aab46c2686a22e91c8c6a4517c5537fe783d5`). Independent source-only review SHA256 `5d0453379873c62dab86bb83c9fb1f26605b24c3a226d4e40aef203fe59719d8` reviewed `test/integration/t097/test_proxy_sigkill_t097_restored.py` (`6f6da51600c0f43c0fc1c7a0a3fd0e87493547c5179482474f220c075c1f033b`) and `test/scripts/test_t097_sigkill_wait_contract.py` (`0e5a95e3bb2cc8991c65eaa9838821159cd08dc7701f0236b37b18223bc379c9`): it passed Python 3.10 instances without callable `add_note`, swallowed diagnostic-attachment failures, and preservation of the original body exception identity. It also confirmed an unaccompanied cleanup error remains raised.

No fresh QEMU run has launched after the helper correction. The prepared tar is not QEMU acceptance. Preserve the earlier R3 91-case failure and the two-case QEMU2 diagnostic; T094 remains open until a fresh full 91-case run and cleanup/source-identity checks pass. T093/FR-044 also remains open.

### Postprocessed diagnostics — 2026-10-15

The corrected-helper QEMU2 run used the same test bytes as current main (`test_proxy_sigkill_t097_restored.py` SHA256 `6f6da51600c0f43c0fc1c7a0a3fd0e87493547c5179482474f220c075c1f033b`). Both selected cases passed, zero warnings, in 235.15 s; the guest runner exit was 0 and cleanup completed. The original wrapper nevertheless exited 1 with `diagnostic_failed` because its summary parser reported zero passes. The original receipt was not rewritten; the postprocessed receipt records the original status and recovered 2/2 guest result, not full-suite acceptance (recovery SHA256 `addf5550137ad6236a2b9be1e033ccf651b0a63895ed078d9290dccd6cab18e5`; original receipt SHA256 `2ddc69626ea4fb1ab53379915bdb93fbde4d91c1806e110cd23c3ee4a3bba244`; QEMU log SHA256 `70159e926a62080caed92a751e379823e6dff26a7b1e4917610591bef9a9a844`). This two-case diagnostic does not close T094.

The catalog-plan baseline workload measured 1.805611058 s and therefore failed the strict one-second bound. The original runner raised before writing its receipt because its expected-red parser rejected the pytest assertion rewrite. A separately tested strict parser matched the exact formatted failure, optional numeric assertion, two clock samples, JUnit outcome, and pytest log; the same artifacts verified all nine semantic/counter/catalog/loader checks, ten plans, no plan errors, and 71 tables. This recovery preserves the original wrapper exit 1 and is diagnostic, not acceptance (receipt SHA256 `0af8126623d8a33c17b9709fb7936efa8bc1e978c93a39b1cec108c699e5b9a1`; offline parser validation SHA256 `c4635fcb806a2cb1b9ac97dadeeb87f1ce33294f56df144bf4f8957cb7087caa`). The missing original parent HOME and main-source before/after guard fields are explicitly unresolved in the recovery receipt.

### T098 measurement and environment update — 2026-10-15

Docker Desktop recovery restored access to the same owned PostgreSQL 18.6 container; its published port was remapped from 61997 to 56421. The sanitized metadata snapshot SHA256 is `200b53e7bd46f495ed1246cab792eb0e6909bbec3cc1ad962a05b99f2a14f1d1`; the recovery receipt records unchanged container identity, verified admin/version, and unchanged prior results (SHA256 `b792881742290c78bcc596ca0d65a00a602b6aefb0a0041a779de4d53b9c57e5`).

The private T098 R3 offline suite passed 18 tests plus 9 subtests; its native baseline and candidate selections each passed 8 tests. One scored pair measured 1.024650518 s for baseline (fail) and 0.967219559 s for candidate (pass). The required candidate cohort then produced 1.017240271 s (fail), 1.034487912 s (fail), and 0.973277629 s (pass), so the three-run criterion remains unmet. The phase timing receipt is explicitly diagnostic: the full call measured 1.032294341 s, including 21 catalog reads totaling 592.153 ms; all harness guards passed, but the measurement does not satisfy FR-044 (receipt SHA256 `54add99c7eaa859e9baaf3bd25eee6f7f4ab04c3cf20ddede0dc7eb9b7d38045`). A separate projection proposal passed 101 offline tests with 6 warnings; this only verifies the offline proposal and is not evidence of a runtime fix.

The current FULL91 preflight reported 6062 MiB available against its unchanged 6144 MiB minimum and exited 2 without starting a VM. No further memory can be freed, so keep the gate pending without relaxing the floor (log SHA256 `de5c72bed00571290a21fe8342b635caa7541c1bd8abfb298a945b660779f760`). The OpenCode R8 live attempt timed out after 83.13 s, with no questions or acknowledgments; its cleanup status was incomplete; `private_files_preserved` was a cleanup-failure category. No final provider matrix, AGUI restart, global rerun, or T098 R3/R8 source port occurred. Neither result closes a gate.

### R9 readiness diagnosis and bounded guard result — 2026-10-15

The real OpenCode R9 journey timed out after 83.54 s with zero questions and acknowledgments. Immediately before cleanup, the captured state was `turn_sequence=1`, `initialized=true`, `MCP_ready=false`, and TUI idle, with the expected log still absent. The offline probe found that the bundled MCP command was recognized before it was wrapped with `env -i` and was unrecognized after wrapping; this is not evidence of an established MCP connection. The current diagnosis is that the harness lookup omitted readiness; no product runtime fix has been made or accepted. The root-owned tmux cleanup exited 0 and the server was gone; a stale socket remained and root-owned files were preserved. Receipt: `R9launcher/runs/514ad4818318477b819d5c13e58dc6ca/root-owned-tmux-cleanup-r1.json`. No R10 result or live-provider acceptance is inferred.

A separate source-only SQLite guard run passed five pure test functions with one warning in 125.53 s (`--noconftest --assertplain`). Its scope does not support a full-suite or coverage claim. Provider and aggregate gates remain open.

### T099–T101 Work context-tail batch port — 2026-10-15

The reviewed R3 files are in main byte-for-byte: `work_repository.py`
SHA256 `c9211ea08e7796c27ba21d71cec1b742ad0f4f4a8705319fa4cf6eccea693852`
and `legacy_memory_audit_repository.py` SHA256
`8c6dd825ecd58025469b2539f70c4c34157007dc10d1624f29bad552377718ba`. The
offline contract module passed 18 tests plus 9 subtests. The first native
attempt had 7 passes and one test-only failure: the borrowed-tuple query
observer used set membership on an unhashable `psycopg.sql.SQL` statement. A
string-type guard fixed that observer; product source was unchanged. The final
native selection passed all 8 tests with zero failures, errors or skips and
passed the source, origin, HOME and count guards (receipt:
`nfr-fix-r23/current-context-batch-native-r2/receipt.json`; final native-test
SHA256 `6b402bef3e8d6aa6385e6239bc15c96111bef8a63a7405b9f269c2aef98a1623`).

The native tests prove the batch path, typed `Record` parity, transaction
state, locks, validation errors, and inbox-error precedence when later
relations are unavailable. They do not instrument or assert one batch call
per current-source NFR invocation. Separately, the prior private scored pair
recorded 21 actual batch calls and zero fallback calls, and the ported runtime
files match those measured candidate bytes. The three-run strict cohort for
that candidate passed only 1/3, so FR-044/T098 remain open; no NFR result is
claimed for the port.
