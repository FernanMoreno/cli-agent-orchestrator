# Preimplementation Graphify impact

## T058 import lifecycle impact — 2026-10-14

Four current files (MemoryService, private audit repository, WorkRepository and
work reducer) were verified byte-identical in a focused preimplementation
snapshot at `/home/felni/.cache/caos-spec021-evidence-20261014/t058-impact/source`.
The structural refresh exited 0: 490 nodes, 1,335 edges; graph SHA-256
`4fd86f5df294f544ae5ac139f4cd3399cd98cc6e96a40b6385d68b3a11f0dcce`.
Source confirms the private repository inherits WorkRepository, which imports
the work reducer/models without a reverse MemoryService import. Moving the
existing import changes dependency initialization timing. Its module-level
manager allocates empty bookkeeping/key/lock state and registers lifecycle
hooks; checkout resolves target inputs and opens connections. Test the earlier
lifecycle-hook registration and disabled path explicitly, then rerun Import
Linter, pool/fork checks and actual-source cold measurements. Repository
initialization remains at its authorized operation boundary.

## T057 cumulative verification impact — 2026-10-14

Before porting the measured prototype, four current source files were copied
and verified byte-identical under
`/home/felni/.cache/caos-spec021-evidence-20261014/t057-impact/source`.
The focused structural refresh exited 0: 497 nodes, 1,357 edges; graph SHA-256
`0ee3f34150b22fd0603ad246439eb51b04d9259e5fab953920803a674b056394`.
Extracted symbols include the catalog query/normalization functions,
`verify_component`, `verify_runtime_role`, and `WorkRepository._verify`.
Source confirms Work imports the verification functions from the shared
migration module, and private memory audit/read leases invoke Work verification
in their existing transactions. The change therefore affects shared schema
verification consumers, including migration/import and runtime repositories;
all three scopes need parity and corruption checks. Only deterministic text
normalization may be reused between calls; database authority inputs continue
to be read fresh. This graph is preimplementation impact evidence.

## T056 fixture ownership impact — 2026-10-14

The workspace graph does not contain the new PostgreSQL fixture/pool symbols;
it is not current evidence for this boundary. A focused structural refresh of
three exact source files ran before implementation under
`/home/felni/.cache/caos-spec021-evidence-20261014/fixture-impact/source`.
`graphify update <source> --no-cluster` exited 0: 367 nodes, 886 edges; graph
SHA-256 `873dc8d276eb95c67952521be711c5dbb94f6202e4d4c728dc3cd8b4fedac2f1`.
All three input files were verified byte-identical to main.

Extracted symbols include `_postgres_worker`, `postgres_targets`,
`postgres_worker_targets`, `_legacy_repository`, `checkout`, and
`_configure_raw`. Source inspection confirms the schema fixtures share the
worker databases while owning distinct schemas; database-wide termination
belongs to worker database disposal. A retained audit lease reuses a physical
session and configures it before application SQL. The planned change is to
fixture resource ownership; no runtime retry or authority boundary is relaxed.
This focused graph is impact evidence, not the pending final repository refresh.

2026-10-11: rsync de source actual verificado a snapshot aislado, `graphify update /tmp/caos-spec016-graph-snapshot --no-cluster`, exit0. Log `/tmp/caos-postgres-graphify-preimplementation.log`: 13811 nodes/37864 edges. No cambios a Graphify dirty del workspace. 579 storage nodes y 1012 relaciones imports/calls entrantes extraídas en `/tmp/caos-postgres-graphify-impact.json`.

Source contrasts: knowledge_routes.py:113 y work_routes.py:62/69 construyen WorkRepository; InteractionService constructor exige mismo path actual y transactions pasan por WorkRepository; browser_auth_routes configura store separado; local_peer_registry independiente de perfil; database.init_db construye ORM y migraciones; recovery_bundle sólo formato SQLite. Estos owners y consumidores forman el alcance del port. Root/agents inspeccionan fuentes, no infieren compatibilidad por graph label. Ficheros Work/auth/interaction/database y small DALs identificados en research.

Decisión: actualizar grafo aislado después de la migración y contrastar dependency direction/target identity/transacciones. No duplicar hechos generados en vault.

## T055 memory audit lease impact — 2026-10-12

An isolated Graphify refresh was run against the current ext4 source snapshot
`/home/felni/.cache/caos-spec021-evidence-20261012/interaction-design-r11/notnull-fastpath-r0/source`.
`graphify update <snapshot> --no-cluster` exited 0 and produced
`graphify-out/graph.json` with 43,818 nodes and 109,165 links (SHA-256
`b86ccaa23126da62bf3d098f66b65163780e8612dc25b4aa9d970d5dc7eb28c4`). The only
failed extraction is an unrelated invalid-MCP fixture JSON under
`test/agent_plugins/fixtures/corpus/mcp-invalid/mcp.json`.

The snapshot bytes match the current workspace for the four reviewed sources:

- `clients/work_repository.py` — `3f92c82ea6d33817aeb7aaa36617815d01defb268a38556336cb702da733e0c4`
- `services/knowledge_policy.py` — `ab400ecccb542f56449607ac9d6999b90be65ff9250a8cb4374bd37ad4d8e5ca`
- `services/memory_service.py` — `100cde205c4d327687ea38c76559f3ada705b10ad04fe4d3fbf607df985f2b92`
- `api/main.py` — `eba6f8ead63ce079f37206cb795999e8f9f2352e619823417d0f1ac741f79440`

Source-confirmed graph edges: API `_get_memory_service()` constructs a new
`MemoryService` and is called by memory endpoints; `MemoryService._legacy_repository()`
constructs/verifies the target-bound `WorkRepository`; `_legacy_operation()`
enters `legacy_memory_access()`; that context calls
`WorkRepository.audited_write_transaction()`, which calls `connect()`, then
`_lock_store_for_write()` and `_verify()`. Graphify node explanations identify
the same edges in `api/main.py:10529`, `memory_service.py:463`,
`knowledge_policy.py:192` and `work_repository.py:509`; those line locations were
verified against source. This supports an opt-in only at the private memory
audit repository boundary. No normal WorkRepository transaction, file-effect
call path, schema owner or authorization source is in the pool's scope.

### T055 planner setting — 2026-10-12

The measured A/B/C experiment ran the exact nine-family catalog statement for
71 tables; each arm returned the same manifest digest. `force_custom_plan` had
the lowest median. The implementation boundary is the existing private pool
configuration and session-readback path in
`services/legacy_memory_audit_repository.py::_configure_raw`; it adds no caller
or schema edge. Generic WorkRepository connections still use
`clients/postgres/connection.py::connect` and do not inherit this pool-only
setting. The existing Graphify evidence remains valid for the unchanged call
path; refresh the isolated graph after implementation if the source change
adds or removes a structural edge.
# T059 native cursor row type boundary (2026-10-14)

The subsequent pre-edit R2 extraction includes workflow_journal and the
unchanged startup replacement regression: exit 0, 86/86 source parity, 2,615
nodes and 6,237 edges. Generated graph digest before label finalization:
`7722466f6f0103ca357a54f04cf461f69f587daebe2587bf231ff7efda58dd50`.
Final labeled graph digest:
`c6a64e658cfb296b1664dae1a2ab5b4ff63f1461f1b387b3dc458ceaef2c36ec`.
Source confirms nullable fetch indexing at the journal's aggregate gates and
registry's bootstrap-version read. Native aggregate SQL returns one row with
a possibly NULL value; guards must distinguish that value from a missing
cursor row. The startup test selects the same configured target through a new
engine; T060 restores its distinct-target scenario through existing owned
fixtures. No new production dependency is needed. R2 evidence remains private
at `final-graphify-r2` beside the R1 directory below.

Before implementation, the private scoped Graphify extraction completed with
exit 0: 2,483 nodes and 5,961 edges across 85 source/test files. Graph digest:
`156f02a94426ad0aabec4e0de24f33f52f65250012d97dac2bb26ef653c00822`;
input manifest digest:
`cfdead8f0f409af6e30b9398df2cbe77d604fd0d52d2fe83257b4fd1a227c152`.
The corpus intentionally omits unrelated dependencies; its dangling edges are
not a claim of whole-project graph completeness. Evidence:
`/home/felni/.cache/caos-spec021-evidence-20261014/final-graphify-r1`.

Verified against actual source: Connection selects `record_factory` for both
execute paths, while the shared ConnectionCursor protocol erases fetch rows
to Any. This reaches the six typed Record-or-None returns reported by full
mypy in WorkRepository, work_mcp_proxy and work_provisioning. Raw custom row
factories and lease guards must remain supported; the correction is limited
to type declarations at the native connection boundary.


## T061 dispatcher grant-lock convoy — 2026-10-14

The private Spec Kit draft sequence preceded the Graphify extension and source
inspection. The accepted R13 corpus was extended from 87 to 119 files, including
admission, scheduler, authority, contract, reservations and actual lock-order
tests. All 119 copies matched the accepted manifest. The source confirms that
candidate grant/job locks lie outside the path-probe savepoint, and that preview
currently performs rolled-back scheduler/event writes. Commit must compare the
expected selection under the aggregate lock before writes and retain only the
selected candidate authority fence. Read-only selection preserves existing
capacity, priority, aging, round-robin, FIFO, budget and dependency rules.

Evidence: `/home/felni/.cache/caos-spec021-evidence-20261014/t061-dispatcher-impact-r3/artifacts/preimplementation-impact.md`,
SHA-256 `3c6afe980823f9d27ddc40d4fc4de13971dff6f7daa7d06d5a506f9da0cb00c9`.
No implementation or passing-test claim is implied by this impact proof.

### T061 retirement review follow-up

FR-031 and the follow-up plan precede implementation. The existing Graphify
119-file corpus contains the retirement/parent-lock/transition/withdrawal
boundaries; the focused receipt selects 5 relevant nodes and 53 adjacent edges
from combined extraction SHA-256
`c6e677705dfd4dbb04a3068c29ea00e75cb60baf1c8ecfac88ebaa66a15ce486`.
Source confirms that retirement observes revisions before its later transition
parent lock, while `_lock_attempt_for_write` already provides the required
job→item→attempt lock and fresh row. Withdrawal retains strict queued CAS and
event ownership. The native two-caller reproduction must establish the race
before correcting it; active writers must remain untouched.
Receipt: `t061-dispatcher-impact-r3/artifacts/retirement-impact-followup.json`.

### T064 native and pooled prepare forwarding

# Focused preimplementation Graphify impact

Graphify read eight source/test files copied byte-for-byte from the sealed R13 snapshot. Their source/copy hashes are listed in `../artifacts/source-manifest.sha256`; the full sealed source manifest is SHA-256 `b4e9402e56d5ab2b58dfcf39f50cd8b7c0054334bfb6e5ed7032599015295c49`. Graphify produced 610 nodes and 1,654 links. The private `graph.json` SHA-256 is `e6e36f890ddd91b1d74fe0e47e113f95a442191f793a24d3469dea104683554f`.

The focused paths include:

```text
WorkRepository._verify()
  -> verify_component()
  -> catalog_manifest()
  -> Connection.execute(...)

LegacyMemoryAuditConnectionManager.checkout()
  -> _PooledAuditConnection
  -> Connection (inheritance)

_PooledAuditConnection.execute()
  -> _GuardedCursor
  -> _GuardedRaw / _LeaseState
```

Graphify confirms `WorkRepository._verify -> verify_component -> catalog_manifest` and that `_PooledAuditConnection` inherits `Connection`. Direct source inspection against the same sealed files confirms these method boundaries and their details:

- `clients/postgres/connection.py:55-74` defines `ConnectionCursor.execute(..., prepare: bool | None = None, ...)`; `Connection.execute` at `168-172` currently creates a `record_factory` cursor but does not expose or pass `prepare`.
- `clients/postgres/migrations.py:540-549` runs the combined catalog statement through `connection.execute(statement, parameters)` and then orders and normalizes all nine family results.
- `services/legacy_memory_audit_repository.py:392-419` shows `_GuardedCursor.execute` accepting and forwarding `prepare` within `_LeaseState.call`.
- `services/legacy_memory_audit_repository.py:486-488` shows `_GuardedRaw.cursor` returning that guarded cursor.
- `services/legacy_memory_audit_repository.py:556-562` shows `_PooledAuditConnection.execute` creating the `record_factory` cursor and wrapping it in `_GuardedCursor`, but not accepting `prepare`.
- `services/legacy_memory_audit_repository.py:254-287` defines the active/process/taint checks and reentrant lock in `_LeaseState`; the private prepared path must retain them for cursor creation and execution.
- `test/clients/test_postgres_transactions.py:34-53` proves borrowed tuple-row connections return named `Record` fields without changing the owner's row factory.
- `test/services/test_legacy_memory_audit_pool.py` contains the real lease expiration, reset, and pooled-session contracts.

The private candidate therefore changes only dispatch for explicit preparation: default calls delegate to the old methods, while the catalog statement opts into `prepare=True` through a `record_factory` cursor and the pooled guarded raw/cursor surface. The graph covers this focused call and inheritance surface; it is not a full-repository graph claim. Graphify output is under `graph-source/graphify-out/`, outside the repository.

The private Spec/plan/tasks and Graphify proof preceded the prototype. Product
FR-032/T064 and this plan precede source implementation. Default forwarding
remains unchanged; explicit preparation uses the existing native/guarded
Record cursor boundary. The cache holds safely rendered SQL, never verified
data or authority. Full native regression and cold product-source NFR remain
required after the port.
# T065 preimplementation source boundary — 2026-10-14

T066 preimplementation proof reuses the verified role/pool graph nodes and
checks their ASTs against immutable R14. WorkRepository._verify still calls
verify_runtime_role, and checkout/return still configure the same owned
physical connection through the lease manager. T064's optional prepare
forwarding retains its guarded cursor boundary. Only role SQL construction
and physical reset/settings reads change; all callers and authority/state
dependencies remain. Private source/graph proof:
`nfr-analysis/t065-margin-candidates-r1/design/graphify-source-proof.md`.
Refresh the postimplementation graph for these helpers and their native tests.

The existing focused T057/T064 graphs include `_normalize_catalog_manifest`,
`_digest`, `verify_component`, `catalog_manifest` and `WorkRepository._verify`.
Their claims were checked against the actual migration source before port:
catalog_manifest reads all nine families afresh and normalizes the decoded
JSON; verification and migration-ledger insertion immediately digest that
result. Only those normalization/digest boundaries change. SQL, role checks,
identity/history checks, leases and durable audit transactions retain their
current boundaries. Private proof:
`nfr-analysis/combined-nullkey-digest-r1/artifacts/graphify-source-proof.md`.
Source migration SHA before port:
`67d2934aec01202c39599a27c0ad20fb5e8f52b166bafc9bffe9a47602b7f11b`.
Refresh the postimplementation graph for the internal catalog digest helper
and its two callers; review freshness and generic digest cycle behavior.


## T067 preimplementation impact

Existing Graphify catalog/verify/connection nodes are checked against source. The measured capacity adapter wraps InteractionStore transactions and read connections, which call repository verification, verify_component and catalog_manifest; catalog_manifest now passes prepare=True to execute. The actual TimedConnection AST accepts only statement/params and raises TypeError before its body, while Connection.execute accepts optional keyword-only prepare. Only this test instrumentation boundary changes; product SQL, service locks, visibility and thresholds retain identical bytes. Preserve private AST RED/forwarding evidence and independent graph/source proof.

## T068 preimplementation impact

The prior graph scope omits installer nodes. The narrow Graphify selection is scripts/docker_install.py, test/scripts/test_docker_install.py, test/integration/test_docker_personal_installation.py and test/integration/test_docker_personal_sqlite_import.py. Verify ensure_database -> managed target -> _ensure_postgres_container -> _bootstrap_roles against source. Only the readiness command gains explicit host 127.0.0.1; final TCP readiness excludes the pinned image temporary socket-only server. Role SQL, secrets, schemas, application state and Docker ownership/cleanup retain their existing contracts. Private RED/GREEN proof: controller-final-r2-diagnostic1/artifacts/readiness-red-green.json; exact native psql diagnostic remains unavailable.


## T080 preimplementation impact

Private Graphify refreshed 10 R20 files before implementation (610 AST nodes,
1,486 AST edges). Source verification confirms `docker_install.admin()` points
ambient HOME/CAO_HOME_DIR at the unpublished restore root; browser-enabled
`restore -> _config -> _local_binding -> security.auth -> constants` imports
create that root before the destination-absence guard. The actual cold Docker
reproduction failed with `FileExistsError` and only empty CAO scaffolding.

Change only the restore administration container's ambient home to a disposable
UID/GID-owned 0700 tmpfs outside the source/destination bind mount. Keep explicit
root/config, target resolution, absence checks, authentication, native registry,
DDL/checksums and backup behavior. Root verified the private source chain and
preimpact report `restore-isolation-preimpact-r3/artifacts/preimplementation-impact-r20.md`
(SHA256 `4c29d69a2bbc7466576e73fc7937bf0ef9e90cce9a36497eefa7e8dddfadf31f`).
Refresh composition after the candidate and real Docker regression. This is
preimplementation evidence, not a passed restoration gate.


## T081 preimplementation impact — strict memory round-trip NFR

The full Graphify graph predates SQLite retirement, so it was not reused. A
private focused Graphify AST extraction of 18 current source/test/fixture files
produced 767 nodes and 2,143 edges. Source hashes were equal before copy, on
the private copy and after extraction. Graph artifact:
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/preimpact-graph-r1/graphify-out/graph.json`
(SHA256 `db7ea46a097c72e59b84beca5f7b8db90b078debd56472cf1bf6fb8785152630`);
the per-path hash/count summary is
`nfr-fix-r23/preimpact-graph-r1/artifacts/preimpact-graph-summary.json`
(SHA256 `371ca2e1c4bf2bfa5e9aafbdf4872fe274b198db168577096959473269683925`),
and the source-checked composition proof is
`nfr-fix-r23/preimpact-graph-r1/artifacts/preimplementation-impact.md`
(SHA256 `a2af98e386b019a9a50f5a970baefb30f1488cfa701e851fc5c043a0b56e7f19`).

Focused source inputs were `clients/postgres/migrations.py`,
`clients/work_repository.py`, `services/memory_service.py`,
`services/legacy_memory_audit_repository.py`, `services/knowledge_policy.py`,
`services/legacy_memory_access.py`; catalog batch/roundtrip/digest and
migration-error tests; WorkRepository PostgreSQL/deadlock tests; memory index,
legacy audit/pool/composition and knowledge-policy tests; and
`test/postgres/fixtures.py`. Their exact per-file hashes are in the summary.

Graphify extracts `catalog_manifest -> literal statement builder/normalizer`
and `verify_component -> catalog_manifest + catalog digest`. Source checks
confirm `WorkRepository._verify()` calls component and runtime-role
verification; the memory NFR uses `MemoryService._legacy_repository()` to
create the private audit WorkRepository; its audit/read-snapshot paths use the
bounded manager; and `KnowledgePolicy` verifies the caller's transaction and
repository identity before authority-chain checks. These edges constrain the
impact review; the AST graph alone does not establish SQL runtime cost or
dynamic behavior.

The current shared routine query at `migrations.py` keeps trigger dependencies
in a separate `UNION` arm. Its `pg_depend` arm filters `refclassid=pg_proc`
then tests five object families with OR-connected `EXISTS` subqueries:
`pg_constraint`, `pg_attrdef`, index `pg_class`, `pg_policy` and
`pg_rewrite`. A scoped `(classid,objid)` relation may avoid scanning/filtering
unrelated dependency objects, but the graph and source inspection do not prove
that it improves the measured plan. Preserve the candidate only for T081
`EXPLAIN`/measurement; otherwise leave the SQL unchanged. Any accepted change
must retain trigger UNION and deduplication, exact nine-family values/digests,
main/auth/peer and multi-component parity, freshness/drift, identity/history
and caller snapshot boundaries, and migration DDL/checksum/ledger bytes.

The root's strict covered-main RED measured 1.1544596599997021 s against the
unchanged 1.0 s bound. Its R23 JUnit failure and PGv3 profile metadata are
recorded under `nfr-fix-r23/baseline-r1/` and
`nfr-analysis/pg256v3-strict-r1/owned-service-r1/metadata.json`; this agent did
not run PostgreSQL or tests. This is preimpact evidence only. Refresh Graphify
after any structural source change and bind final acceptance to the sealed
source; the three cold NFR passes, full-suite error/skip reconciliation and
87%/90% coverage floors remain open under T076.


## T082 outcome and T084 preimplementation impact — constraint rendering

The existing private graph remains the applicable preimpact graph because the
candidate would touch the same `migrations.py` catalog-manifest query and
existing catalog tests. Its 18 source/test/fixture files, 767 nodes and 2,143
AST edges are documented above; the graph SHA256 is
`db7ea46a097c72e59b84beca5f7b8db90b078debd56472cf1bf6fb8785152630`, the
path/hash summary SHA256 is
`371ca2e1c4bf2bfa5e9aafbdf4872fe274b198db168577096959473269683925`, and the
source-checked impact proof SHA256 is
`a2af98e386b019a9a50f5a970baefb30f1488cfa701e851fc5c043a0b56e7f19`. The
current `src/cli_agent_orchestrator/clients/postgres/migrations.py` SHA256 is
`b8cb31f9b1776c1f1440948b06fce46bbd2dc24052730b54a4886208d750df6b`, matching
the existing graph input. No structural source change has been made for
T084, so a new graph extraction is not warranted at this planning stage.

The source-verified path is `catalog_manifest()` → query selection and
normalization → fresh constraint rows and the nine-family digest →
`verify_component()` → `WorkRepository._verify()` and the memory audit path
described in T081. The existing `_catalog_manifest_queries` constraints
statement calls `pg_get_constraintdef`; the candidate would add only a
PostgreSQL-18-gated alternative in that same module. It must preserve the
shared query's freshness, result ordering, normalization, caller-owned
snapshot, and all nine manifest families. Tests to refresh/extend are the
existing native catalog-batch, catalog-roundtrip and catalog-digest
optimization tests plus migration-error contracts; the private graph already
contains the relevant source and test paths.

The `pg_depend` rewrite evaluated for T082 is rejected: planner-probe-r2
reported 22.103 ms original versus 22.929 ms candidate despite parity on 71
tables. No source change or NFR pass follows from that experiment. The
constraints-family observation (8.198 ms, 1,407 rows, 4,317 buffer hits) is
only a reason to measure the new hypothesis; it does not establish savings.
Final T084 diagnostics retain exact nine-family digest parity, but do not show
an accepted gain: paired R4 measured 20.741 ms original versus 27.301 ms
candidate with 12 interleaved reads and no clear benefit. R1 was 22.310 versus
22.460 ms; R2 EXPLAIN was 39.728 versus 35.874 ms, but its repeated reads did
not support a benefit. R3 failed with `SyntaxError` before tests. Reject the
candidate without changing product SQL or claiming an NFR pass; preserve all
failed-run history.

Before any source edit, verify candidate output against PostgreSQL 18's native
renderer and exact fallback behavior, then use native fixture tests for
main/auth/peer, multiple components, snapshots, drift, constraint flags,
quoted/Unicode names, rename/drop, inheritance and partitions. Leave the
original query intact for PostgreSQL 16/17, unknown and future versions and
for malformed `conkey` or missing/dropped attributes. Cache only bounded,
version-keyed SQL text if measurement justifies it; no catalog, manifest,
authorization or drift result caching. Any structural source change requires
a Graphify refresh and composition review. The three cold FR-044 runs and
T076's aggregate gates remain open.


## T085 preimplementation impact — shared log sanitizer import seam

The focused private preimpact Graphify extract is
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/graphify-sanitizer-pre/graphify-out/graph.json`
(SHA256 `84b88228fa994632718e78558b38a28ac19eb51d34ea7afe070e0be8de2d5f77`).
It contains 305 nodes and 673 links, with zero failed sources, across six
source files: `constants.py`, `services/audit_log.py`,
`services/memory_append.py`, `services/memory_scoring.py`,
`services/memory_service.py`, and `services/wiki_compiler.py`.

Source checks match the preimpact hashes for the affected existing modules:
`audit_log.py` SHA256
`a8c910a9e3716934003d038067f169391e75769183f1dcf1a83daa1b8c4e76e3`,
`wiki_compiler.py` SHA256
`a5675e7a2db86fc13a1449f3d8d465397f52112839fa58c414740fed8c51be76`, and
`memory_service.py` SHA256
`b372dd79b6d1e7c57f7d14fc34f0b97c5161b139b14252b387b6d91e09dd8938`.
Graphify shows `audit_log.py` importing `_sanitize_for_log` from
`wiki_compiler.py` at line 42; `_sanitize_field_value` and `_sanitize_summary`
call it at lines 169 and 179. `wiki_compiler.py` defines it at line 115, and
`memory_service.py` imports and calls audit logging in the store path. Source
inspection confirms the sanitizer itself uses only standard-library regex and
string operations.

T085's proposed structural change was bounded to a new pure-stdlib
`services/log_sanitization.py`, a direct leaf import in `audit_log.py`, and a
compatibility re-export from `wiki_compiler.py`. The preimpact graph does not
include the new leaf or test; the clean postimpact graph and import-edge review
are recorded below. Audit append, event ordering, and memory scoring stayed
unchanged; the focused dependency result is not a strict NFR pass.


## T086 conditional impact — compact catalog-row transport

T086's compact JSON-array transport evaluation is closed as a no-go on the
existing `catalog_manifest()` path. The R4 peer timing was variable at about
31 ms and sampled no Work policy rows; this does not support a product or
dependency change. The private `orjson` result is not accepted as a dependency
or strict NFR pass. Any future evaluation requires a separate task, populated
policy coverage, and exact array-row reconstruction, normalization,
nine-family digest, snapshot, drift, and main/auth/peer parity. T086 does not
close the strict cold NFR or T076.


## T087 preimplementation impact — conditional run-step completion race

The private focused Graphify extraction is
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/graphify-t087-pre/source/graphify-out/graph.json`, SHA256
`b368de2fc579ff1819f4508d223097c146b2c3fb9f6d63d49ecaf1b4b6e9a419`.
It contains 893 nodes and 1,896 edges from the six source/test inputs as they
existed before the local T087 change, listed below. Those hashes are historical
preimplementation inputs and are not the final source hashes.

| Input | SHA256 |
|---|---|
| `src/cli_agent_orchestrator/services/terminal_service.py` | `5d4275c1dfff3a13c04c37db5fd2b05c5e0b5628c09fdc3cf608986bd46bf09e` |
| `src/cli_agent_orchestrator/services/agent_step.py` | `dbbe657989e932912a99fc8503c44cefce3c2c5625e552886e996895efef37dd` |
| `src/cli_agent_orchestrator/services/status_monitor.py` | `a7a4317b73303f216bca08d9c0c6e7f81f4d7c22d290a6b7425301fd7a0f6386` |
| `test/services/test_agent_step.py` | `27e9e6ee97a052e0d3a48e96e3a2ae3d25c5d1819a697966620a4a0ca2ff06ca` |
| `test/services/test_status_monitor.py` | `5a964c5fb27b537609d180ad600df505c78a9712cfd90d7d4665ee10fe21898a` |
| `test/services/test_terminal_service.py` | `a47e638b77eea3ceeb454ffee3d8528aeb2c1f24a14930456828f559fc169cab` |

Graphify extracts `terminal_service.send_input()` and its transport-related
callers; `agent_step.run_agent_step()` and its `_wait_for_completion()` edge;
`StatusMonitor.notify_input_sent()`, `notify_input_delivered()`,
`_apply_detection()`; and relevant service-test nodes. It does not resolve
module-global `status_monitor` attribute calls into direct edges at the two
call sites below, so that seam was checked against source rather than inferred
from the graph. The call flow is:

1. `terminal_service.py:4777-4782` opens one real turn; lines 4784-4799 clear
   that turn's rolling buffer; lines 4831-4839 perform the blocking key send;
   line 4858 records delivery; line 4926 returns the original turn sequence.
2. `agent_step.py:1703-1728` dispatches the synchronous send in a worker
   thread; line 1764 records the child as sent; lines 1766-1780 publish a
   second `notify_input_sent(..., assume_processing=True)` before
   `_wait_for_completion()` at line 1793.
3. `status_monitor.py:1520-1602` specifies that each call opens a turn,
   increments its sequence and capture generation, resets turn state when not
   a continuation, and applies the assumed PROCESSING state with
   `observed=False`.

The source ordering identified a risk that was later reproduced by an
event/barrier RED test and corrected under T087. The preimplementation graph
and analysis above are retained as historical impact evidence; focused
postimplementation composition and source hashes are recorded below.

The sanitizer's 9-test slice and the 127-caller stale-metadata setup are early
run receipts; the setup error is preserved but is not counted as a pass. The
final focused sanitizer result is recorded in the postimpact section below.
The compact-transport R4 peer result was variable at about 31 ms on an empty
Work policy sample; this does not justify a port. The `orjson` comparison is
not an accepted dependency or strict NFR result.


## T087 remote projection preimplementation impact

Before the remote-path implementation change, a focused code-only Graphify
extraction was built at
`nfr-fix-r23/graphify-t087-remote-pre/graphify-output/graphify-out/graph.json`.
It contains 965 nodes and 2,061 edges; the graph SHA256 is
`9b891b221714fd867ab760307c1512d9a6b8151827ebc23bfeca8af528aba1d8`. The
extraction covers the then-current run-step, status monitor, terminal service,
remote terminal service, runtime bridge, and their three focused service test
modules:

| Input | SHA256 |
|---|---|
| `src/cli_agent_orchestrator/services/agent_step.py` | `b5057128a634ff546044d96ee0c0b2b17c5195f7ee2366a4c8f1a526e6954cea` |
| `src/cli_agent_orchestrator/services/status_monitor.py` | `16a67aee18bae5db931b98107b918b3c60adc1dd94a502367de1ddcb91f777f7` |
| `src/cli_agent_orchestrator/services/terminal_service.py` | `5d4275c1dfff3a13c04c37db5fd2b05c5e0b5628c09fdc3cf608986bd46bf09e` |
| `src/cli_agent_orchestrator/services/remote_terminal_service.py` | `c5b1d1f6fa576e236ffbf80460bb29be44b358f7dfae28f0e40df524ce4c5eb0` |
| `src/cli_agent_orchestrator/runtime_channel/bridge.py` | `4c29cfd54c70f82fa7895b18728b2117a135285373e934eabbd647dd7ccfa4fa` |
| `test/services/test_agent_step.py` | `cfb9907473ad28b1b4413284179e4816707b3658ed1a94dc9a5739a67af9ee24` |
| `test/services/test_status_monitor.py` | `45e14b4061aafda15e7d7212741add508bd43042f0f5ec6b596afc9e9ad75414` |
| `test/services/test_terminal_service.py` | `a47e638b77eea3ceeb454ffee3d8528aeb2c1f24a14930456828f559fc169cab` |

Graphify places `run_agent_step()` before its completion-wait edge and extracts
the terminal send, remote `call()`, remote `terminal_projection()`, status
monitor, and runtime bridge nodes. Its call graph does not resolve the
module-global monitor dispatch through the injected remote send; that route
was verified in source: `send_input()` checks placement, sends input through
`remote.call()`, validates and returns the peer sequence; `run_agent_step()`
passes that sequence to `notify_input_in_flight()` before entering
`_wait_for_completion()`. The bridge opens the peer's logical turn and returns
its sequence and projection. The preimplementation RED reproduced the gap at
the monitor boundary: local counters remain zero for a remote terminal, so the
valid peer sequence is rejected before the wait.

This expands T087 acceptance to local state ownership, remote projection
freshness and shape, exact sequence matching, fast completion, stale projection
reconciliation, and the `run_agent_step` composition path. Remote acknowledgement
must not synthesize local counters or local status events.


## T085 postimpact Graphify — shared sanitizer leaf

The clean postimpact extraction is
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/graphify-sanitizer-post-clean/graphify-out/graph.json`, SHA256
`1aa33b47dc47accba75bb16d5f539c86ae38aa62cbe584324b4316cfce9619f1`. It has
545 nodes and 1,142 edges across ten source files, with no clustering. The
source-verified path is
`MemoryService.store -> write_audit_nowait -> write_audit ->
_sanitize_field_value -> _sanitize_for_log`. After the split, `audit_log` and
`wiki_compiler` both import the sanitizer leaf; the former `audit_log ->
wiki_compiler` dependency is absent, while the compatibility alias remains.
Focused test and caller results are recorded in `research.md`. This extraction
does not replace T076's final Graphify refresh.


## T087 postimplementation source review — focused contracts

The preimplementation graphs above remain historical impact records. The
focused postimplementation Graphify extraction is identified below, and the
final source composition was inspected against
the focused review receipt at
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/t087-remote-modules-r3/artifacts/composition-review-t087.md`.
The fixed local call path uses the existing dispatch sequence through
`run_agent_step` and the in-flight monitor check. The remote path validates the
peer's current projection without changing local monitor state. The native
four-module composition selection passed 285 tests with no failures (46
warnings); the final formatted focused selection passed 19 with no failures
(17 warnings). No aggregate backend, Apps, E2E, or strict-NFR completion is
inferred.

Final reviewed input hashes:

| Input | SHA256 |
|---|---|
| `src/cli_agent_orchestrator/services/agent_step.py` | `b5057128a634ff546044d96ee0c0b2b17c5195f7ee2366a4c8f1a526e6954cea` |
| `src/cli_agent_orchestrator/services/status_monitor.py` | `3d1e08729adbc9d42068199498bc4d3dbfb5462c689171f214a84151d7c4dbce` |
| `src/cli_agent_orchestrator/services/terminal_service.py` | `5d4275c1dfff3a13c04c37db5fd2b05c5e0b5628c09fdc3cf608986bd46bf09e` |
| `src/cli_agent_orchestrator/services/remote_terminal_service.py` | `c5b1d1f6fa576e236ffbf80460bb29be44b358f7dfae28f0e40df524ce4c5eb0` |
| `src/cli_agent_orchestrator/runtime_channel/bridge.py` | `4c29cfd54c70f82fa7895b18728b2117a135285373e934eabbd647dd7ccfa4fa` |
| `test/services/test_agent_step.py` | `e8a0ef13ed1803ed3f5e0c1660ef06c4a11c07d1236b87317bfd6e219e009211` |
| `test/services/test_status_monitor.py` | `53452c5920d87bd1640bf3bdf896e72aa41572f90435f51bf5fe3cb46c6d658c` |
| `test/integration/test_interaction_wait_composition.py` | `f683e8929772c04d50a2d2d9ee5cc622d01641b6cccf9fbe3da96505b38ad6f1` |
| `test/api/test_run_step.py` | `790805749d021ae34316001a2d85a1d57db30d32b2aa64087dd623610b596c87` |

The final T087 Graphify is
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/graphify-t087-remote-post-r1/output/graphify-out/graph.json`, SHA256
`c143c473664e539483c40d84d5a1eb5a129b7d99d0f74661daeb5ba61ea754b0`; its
manifest SHA256 is
`87395de1c8d232323aa09383d07a50cef62357f63ed09c55fb5776ce911c6ec6`. The
eight-file graph contains 972 nodes and 2,089 edges with no clustering. It
refreshes the local/remote monitor path and service-test nodes. The subsequent
two-line Black-only edit changed `test_agent_step.py` from the graph input hash
`d2eec2e7fbdbc67b1d67f04f5287caeaf36ed34a83ff0c1793bfe8a408814a93` to the
final formatted hash listed above; the focused 19-case run and formatting gates
used the final bytes. The integration wait-composition and API run-step modules
were exercised by the native four-module selection and have hashes listed
above, although they were not inputs to the eight-file extraction.


## T088 preimplementation impact — Rust CI native PostgreSQL bootstrap

The isolated-source Graphify extraction is
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/rust-tui-contract-r1/impact-source/graphify-out/graph.json`, SHA256
`b13491c440de00cebe0fd6e8670936f3f4119710f36353e1ca7d80d7108a73ad`. It has
1,072 nodes and 2,492 edges, with no failed sources. The source-verified
composition path includes config resolution (`config.py:75-83`), the actual
server lifespan (`main.py:1530+`), `_start_cao_server()` (`cao_server.py:546`),
native schema initialization/verification (`database.init_db`), isolated
native PostgreSQL targets (`test/postgres/fixtures.py:589`), and the three live
endpoint cases in `tui/tests/endpoint_contract.rs`.

The preimplementation RED log is
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/rust-tui-contract-r1/ci-contract-red.log`;
the isolated stdlib contract failed once because the Rust workflow does not
invoke the required helper. The inspected workflow SHA256 is
`a629003f888a57317c21a3e9a7bc864a4c5bbc7a6cdb839783299d60f03d3ef1`. The
planned seam is limited to a job-private PostgreSQL/helper lifecycle and the
existing Rust CI job commands; no production API or selection change is
authorized. At this preimplementation stage, the helper and matrix runs had
not completed. This is historical preimpact evidence; final Graphify and Linux
native results appear below, while the macOS matrix leg remains pending.


## T088 final postimplementation Graphify and native Linux gate

The final 17-file input receipt is
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/rust-ci-native-r1/records/graphify-source-receipt.json`, with source digest `ea77404b45164d406dbd014ece3834dc1e55f2f8d555534405c8e3cbcbeebb43`. The final Graphify receipt is
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/rust-ci-native-r1/records/graphify-final-receipt.json` (receipt SHA256 `7ee462420a5c8e8937533848722a7c95d263645f64e67d4fd74e1c501dc005dd`). It binds the graph at
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/rust-ci-native-r1/graphify-output-final-r2/graphify-out/graph.json`, SHA256
`acfe18d5ae10a237efdbe6b399ac0560abcebdfe6092bb1bda391d4c6b164589`, with 1,099 nodes, 2,576 links and no failed sources. The final extraction includes the entrypoint import fix, helper-owned PostgreSQL lifecycle, real application config/schema setup, and the exact live endpoint selection.

Final changed inputs are `scripts/ci_rust_contracts.py` SHA256
`5d8c2f842ee59be5884e2e0b85e87d6c5628d81bf23330654d2d28f2c507c9ed`, `test/scripts/test_ci_rust_contracts.py` SHA256
`f52821ba5417c3440309c1a80d8c0308662402f07589ae1cda6094e718186441`, and `.github/workflows/ci.yml` SHA256
`127ee88669a3da554b7a1943161d9ae6c00b0baac729fb34ceb9c30093513cfc`. The isolated `-I` entrypoint contract reproduced
`ModuleNotFoundError` before the fix; final helper tests passed 8/8 (receipt
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/rust-tui-contract-r1/entrypoint-import-r2/unittest-final-receipt.json`). The workflow contract passed 1/1.

The actual Linux native gate used helper-owned PostgreSQL 18.6. It passed
`cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, and
`cargo test --locked`: 262 tests passed across seven binaries with zero
failures, ignored tests, or filtered tests; the endpoint contract count was
4/4. Native receipt: `/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/rust-ci-native-r1/records/native-helper-cargo-receipt.json`; summary:
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/rust-ci-native-r1/records/native-helper-cargo-summary.json`. The source manifest contained 1,607 files and was byte
identical before and after (SHA256 `7c69cf398a6a6fa710eede83e7bf83225c7c1b01866b6263ec80c777b5cb3c26`). No PostgreSQL process
remained, and the private runner temporary directory was empty and removed.
The Linux gate is complete. The macOS matrix execution remains unavailable, so
T088 remains open.


## T093 preimpact Graphify — Core metadata candidate

The private focused extraction is
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/metadata-core-candidate-r1/source/graphify-out/graph.json`, SHA256
`24c7414a4a72fef52a311b4dd3361a38d90603e59372da0d6fb3075ce667d063`
(430 nodes, 909 edges). Its exact two input hashes are bound in
`artifacts/source-proof.json` (SHA256
`a0699d7bef059b993fd9ff2c65370419bbeffce392f42caff29799d6174bf857`):
`memory_service.py` `b372dd79b6d1e7c57f7d14fc34f0b97c5161b139b14252b387b6d91e09dd8938`
and `database.py` `f0569eaf0288ce15af39a9ca0eb91d774fb7aa885931458bd2099314dff79f1a`.
Both hashes match the corresponding files in the final R3 source snapshot
bound by manifest SHA256
`ac4351478f2173853ab6e38af731722f8cb8b58043146877bcd002b6d0c9bcb6`.
This is a two-file impact slice, not the final repository Graphify refresh.

The source-confirmed path in the hash-bound `memory_service.py` is
`MemoryService.store()` (line 933) → `_assert_no_cross_tier_collision()`
(line 1078) before filesystem/index publication, then `_upsert_metadata()`
(line 1226) after those durable changes. `_assert_no_cross_tier_collision()`
is defined at line 1416; `store_vault_memory()` also calls it at line 1347, and
`run_background_compile()` calls `_upsert_metadata()` at line 1833. The model
and its unique constraints are in `database.py:468`. Both
paths open fresh sessions. The collision query preserves key/scope/source-kind
and NULL-aware scope filters, native-file checks, and the migration-receipt
exception. The upsert query preserves the full metadata unique constraints and
its existing commit/error mapping. The current intrusive profile attributes 28 ORM mapper constructions/unit-of-work
setups to these paths; this is diagnostic attribution, not a measured saving.
A Core implementation candidate is now present in main at
`memory_service.py` SHA256
`58ec723493698a8c95ebb0a99e4781a4c469608115e8af9a1f6fbc994a89814f`, with
focused native test `test/clients/test_vault_schema_migration.py` SHA256
`33f89d93251747e538da44b876f7ca07a578769a0cea097f013d275992bd638f`. The
current native caller selection passed 21 tests in 25.798 s (JUnit SHA256
`5aac5e4a69c48871feca0499525c9066888646e4c8190de59adb7f8455290fd8`). The
Graphify above remains a preimpact map of the original paths; it is not a
postimplementation graph refresh. The candidate must preserve the fresh query
predicates, current transaction boundaries, publication order, and all
identity/authorization/schema fences; it does not cache rows or metadata.

Private paired run 02 measured baseline 1.105620873 s and Core candidate
1.004116244 s, a 101.504629 ms reduction; both failed the unchanged strict
`<1.0 s` assertion. A separate Core/decoder pair measured Core at 1.032364007 s
and combined at 1.120388702 s; both failed and the decoder arm was slower, so
no decoder change was ported. Both post-timing catalog-digest audit probes
errored and remain under investigation. These are diagnostic outcomes, not
FR-044 acceptance; T093 remains open.

### T092 scope correction and R4 validation receipts

The offline harness selection was 18 tests across two modules, bound to four
source/test input files; it was not four modules. The four import-order edits
were isort-only with no non-import AST change. This is a harness/privacy
contract, not a new persistence dependency edge, so no additional Graphify
source change is claimed. The R4 guarded package verification passed against
manifest `c4827350259bb4b4815126d11c15b2f9141cc11a8b71716f335cf8db6b37407e`:
389 wheel/sdist modules, five active SQLite guards, runner exit 0, and
unchanged HEAD/index/HOME (receipt SHA256
`f0a4c966274532de62db189ff10441edfbc666cf3951a0137a5d496bb83fc13f`). The
source-bound R4 Web20 selection passed 20/20 with zero skips and exit 0 (log
SHA256 `159ae898419dad4115e61b38686b65a671e7fce76d9087712905eb8a97706554`; summary
`frontend-private-r3/web20-20261014-214156-714984-ac1ogaku/summary.json`). These
scoped receipts do not close full-suite, provider, or strict-NFR acceptance.

### T095 prior private-harness provider receipts

The T096 bounded harness implementation is now in the current source tree; the live receipts below are from the earlier private run and do not prove live journeys on the current bytes. Its latest
Codex receipt passed in 14.17 s with two questions, two delivered answers, one
identity-matched acknowledgment, and unchanged auth/operator state (receipt
SHA256 `23ed395e80178b2d512c2edb1a945001e4459485688f99ea4233503277f06fd2`).
The latest OpenCode receipt exited 0 in 11.11 s but failed with no questions or
acknowledgments (one `step_start`, one `text` event); cause is unknown and the
read-only MCP preflight remains pending (receipt SHA256
`76dfae9dbb375c40de6b1a50938b6dc4cc03442407c1fb538a88fc939611e9d6`). Claude's
previous two-question journey was acknowledged. The provider matrix and SC-011
remain open; no whole-provider acceptance is inferred from these receipts.

### Current-source composition and regression follow-up — 2026-10-14

The focused Core source/test paths currently in main are bound by `memory_service.py` SHA256 `58ec723493698a8c95ebb0a99e4781a4c469608115e8af9a1f6fbc994a89814f` and `test_vault_schema_migration.py` SHA256 `33f89d93251747e538da44b876f7ca07a578769a0cea097f013d275992bd638f`. The fresh native caller selection passed 21 tests in 25.798 s (JUnit SHA256 `5aac5e4a69c48871feca0499525c9066888646e4c8190de59adb7f8455290fd8`). The latest no-coverage 11-module memory regression selected 234 tests, with 233 passing and the strict round-trip assertion failing at 1.161985059 s; this is not strict covered acceptance (JUnit SHA256 `2673890c4974210e28e3f7a45c25bceaa05aa72c41092eb68168b1863ddc6794`).

The four scoped static checks passed (receipt SHA256 `d82f19af42bcc3ea94460a531390ed489459c1bf031e8307667a46aa18b7cf7f`). Manual source-composition review found the Core query predicates, unique-key identities, session/transaction boundary, commit, and publication order intact; it found no blocking defect. It records a pre-existing cross-tier preflight race: native topic and vault projection locks do not coordinate, so concurrent creation may pass both checks before publication. The Core port did not introduce that race (review SHA256 `38188ba53187f7e8818df2eeb840eecde19bd31b7392008bb8fa44e4b04d50a6`). The existing Graphify map remains the earlier preimpact map; this paragraph records source review and does not claim a refreshed postimplementation graph.

The Core/decoder paired diagnostics remain failures: Core 1.032364007 s; combined 1.120388702 s; both catalog-digest post-timer probes errored (paired receipt SHA256 `d9982040d92fd8d6b9c0efb42fe3204bd850abc6445fa30aa962128cdc93ab13`). QEMU2 completed a two-selector diagnostic: `after_upstream` passed and `before_upstream_effect` failed, with zero skips and successful cleanup. This is not full QEMU acceptance; T094 remains open. T093/T094 and global T076 remain open.

### T096 provider harness and QEMU2 composition update — 2026-10-14

Current source inputs are the shared harness `test/live_provider/interaction_cli_journey.py` (SHA256 `8b136a5746e101f16f94c540d99e2db2d69d55f5d9fab62f3ea8e3d39074e130`), its offline contract tests `test/live_provider/test_interaction_cli_journey.py` (`2792ec1d699d2f78b63254b37fd07da86ba18cad3044d632575405abf2f5f890`), the headless OpenCode journey `test/live_provider/opencode_interaction_journey.py` (`848d150f87136fec84e425db76ad305b62c4821cdc59d1f94761bf1a4e9e44fd`), and upgrade contract tests `test/live_provider/test_provider_harness_upgrade.py` (`efd58e94f18a8b8b034afb518d3f158199b5274e1a5fc1cabe6330bc95ca64bc`). Source review confirms auth eligibility is validated before overlay creation; the staging tests cover source byte preservation, private ownership/mode, rollback, and child-environment/receipt boundaries. The current-source-bound offline run passed 76 tests across two modules with no errors/skips in 3.211 s (JUnit SHA256 `89415323d7a5a44cf7418c31f42d2891d76a2335a7ef7b85bfc8e934c91916e7`). No Graphify extraction was run for this harness update; these source paths and test edges are recorded as a focused impact note only.

The live-provider status is separate: previous Codex/Claude successes remain private-run history; the latest OpenCode interaction still had no question or acknowledgment. The 76 offline checks do not close T095/SC-011.

The QEMU2 source-bound diagnostic tested only two SIGKILL selectors, with `after_upstream` passing and `before_upstream_effect` failing (0 skipped; receipt SHA256 `51cbee10d4c702e04d28ed6ec7484c93306f0a51024900f4a83758353e93972e`). Marker removal and owned-resource cleanup succeeded. The trace places callback 13.391 s after GO and 64.038 s after backend process start; the 65-second wait expired 10.049 s earlier. Source review confirmed a test-helper deadline-origin defect, and the helper-only correction is covered by 10 offline tests. No fresh QEMU run has launched after the correction; the prepared tar is not acceptance evidence. T094/full QEMU and T076 remain open.

### T093/T094 evidence addendum — 2026-10-15

The private owner-JSON loader candidate did not add a main-source edge: source and HOME guards were unchanged and the semantic/counter/catalog/loader checks passed. Its measured baseline was 1.306698168 s and candidate 1.565738595 s, so both fail `<1.0 s` and the candidate was slower by 259.040427 ms. No code was ported (pair receipt SHA256 `dcb51b9cb3cc2fdb1326097b60e584221ceb6fe64e8c30f5442e2c55c2b3088d`).

The T094 test-helper correction passed 10 offline tests in 4.01 s (JUnit SHA256 `cef799393c147333824de70fa6bc40b9260e1bcc938c43a84ee52f8d11eadcc2`). The independent source-only review (SHA256 `5d0453379873c62dab86bb83c9fb1f26605b24c3a226d4e40aef203fe59719d8`) covered the restored proxy test helper and its contract test, verifying Python 3.10 cleanup-diagnostic compatibility and preservation of primary exception identity. These inputs have SHA256 `6f6da51600c0f43c0fc1c7a0a3fd0e87493547c5179482474f220c075c1f033b` and `0e5a95e3bb2cc8991c65eaa9838821159cd08dc7701f0236b37b18223bc379c9`. No fresh QEMU run has launched after the helper correction; the prepared tar is not acceptance evidence. This remains a focused offline/source review, not a new Graphify extraction or full QEMU result. T093/T094 and T076 remain open.

### Diagnostic recovery and corrected-helper QEMU2 result — 2026-10-15

The source-bound QEMU2 run used the corrected helper bytes matching current main (`test/integration/t097/test_proxy_sigkill_t097_restored.py` SHA256 `6f6da51600c0f43c0fc1c7a0a3fd0e87493547c5179482474f220c075c1f033b`). Its two exact selectors (`before_upstream_effect`, `after_upstream`) passed in 235.15 s, zero warnings; the guest runner exited 0 and marker/resource cleanup succeeded. The outer wrapper's original exit 1 and `diagnostic_failed` status remain preserved because its parser reported zero passed selectors from the raw two-pass output. The offline recovered receipt binds both outcomes (SHA256 `addf5550137ad6236a2b9be1e033ccf651b0a63895ed078d9290dccd6cab18e5`; original wrapper receipt `2ddc69626ea4fb1ab53379915bdb93fbde4d91c1806e110cd23c3ee4a3bba244`; raw guest log `70159e926a62080caed92a751e379823e6dff26a7b1e4917610591bef9a9a844`). This updates the earlier post-correction “no fresh QEMU” note; it remains a two-case diagnostic, not the full 91-case acceptance. T094/T076 remain open.

The catalog-plan baseline probe itself failed the unchanged NFR at 1.805611058 s. Its wrapper exited 1 before saving a run receipt because the expected-red parser rejected pytest's exact appended assertion rewrite. Postprocessing used a separately tested strict parser and immutable input hashes; it verifies the exact failure against the clock, JUnit and pytest log, all nine source-report proof checks, all ten plans, `errors=[]`, and 71 tables. The original wrapper status and NFR failure remain unchanged. Recovery receipt SHA256 `0af8126623d8a33c17b9709fb7936efa8bc1e978c93a39b1cec108c699e5b9a1`; offline parser-validation receipt SHA256 `c4635fcb806a2cb1b9ac97dadeeb87f1ce33294f56df144bf4f8957cb7087caa`. The failed wrapper did not persist its parent HOME or main-source before/after guard values; the postprocessor does not claim them. No product source changed and no new Graphify extraction was needed for this artifact-only recovery.

### Runtime and performance evidence; no new source edge — 2026-10-15

The private T098 R3 candidate has not been ported to main, so these runs add no product dependency or Graphify edge. Its 18 offline tests plus 9 subtests and native baseline/candidate selections (8 passed apiece) were green. Timing acceptance is still open: the scored pair was baseline 1.024650518 s (fail) and candidate 0.967219559 s (pass), while only one of the three cohort runs passed (`1.017240271`, `1.034487912`, `0.973277629` s). The separate phase profile is diagnostic-only (1.032294341 s total; 21 catalog reads, 592.153 ms), not acceptance. Receipts: pair `nfr-fix-r23/t098-tail-context-r1/nfr-pair-r1/run-01/summary.json` (SHA256 `adef0a094e37ee34c29bd0b66652101b879a30da377620f80c118b1d5e17d309`), cohort SHA256 `2f03120ee7ae7081e426d897d92fd88f7e2b3129c5d51e200b5ea9089b1cacc8`, phase diagnostic SHA256 `54add99c7eaa859e9baaf3bd25eee6f7f4ab04c3cf20ddede0dc7eb9b7d38045`.

The owned PostgreSQL 18.6 container recovered with the same container identity and a published-port change from 61997 to 56421; metadata SHA256 `200b53e7bd46f495ed1246cab792eb0e6909bbec3cc1ad962a05b99f2a14f1d1` (recovery receipt SHA256 `b792881742290c78bcc596ca0d65a00a602b6aefb0a0041a779de4d53b9c57e5`). The FULL91 gate did not enter QEMU: preflight exited 2 at 6062 MiB versus the 6144 MiB floor (log SHA256 `de5c72bed00571290a21fe8342b635caa7541c1bd8abfb298a945b660779f760`). OpenCode R8 timed out at 83.13 s with zero questions/acknowledgments and cleanup status was incomplete, with `private_files_preserved` recorded as a cleanup-failure category; no final provider matrix or AGUI restart was run. A separate projection proposal's 101-pass/6-warning offline result does not establish runtime behavior. None of these operational receipts changes the graph or closes the global gates.

### R9 runtime diagnosis; graph unchanged — 2026-10-15

R9 is a harness/readiness diagnostic, not a new product edge or runtime fix. The real OpenCode attempt timed out after 83.54 s with zero questions/acknowledgments; observation showed `turn_sequence=1`, `initialized=true`, `MCP_ready=false`, an idle TUI, and no expected log before cleanup. The offline probe found that the bundled MCP command was recognized before it was wrapped with `env -i` and was unrecognized after wrapping; it did not establish a live MCP connection. The current diagnosis is an omitted readiness lookup in the harness. The owned tmux kill exited 0 and its server was gone, though a stale socket remained; root-owned files were preserved. Cleanup receipt: `R9launcher/runs/514ad4818318477b819d5c13e58dc6ca/root-owned-tmux-cleanup-r1.json`. No R10 or provider acceptance is claimed.

The source-only SQLite guard's 5 pure test functions passed with 1 warning in 125.53 s using `--noconftest --assertplain`. This narrow run adds no graph claim and is not a full-suite or coverage result.

### T099 preimplementation Graphify/source proof — 2026-10-15

The R3 focused source proof uses the existing Graphify graph
`/home/felni/.cache/caos-spec021-evidence-20261014/final-graphify-r3/graphify-out/graph.json`
(SHA256 `551ab7ed5bcb1819b225613016059cdf87036754ede25323d7f0fb6383a80af1`,
2,637 nodes, 6,294 links). I verified its relevant edges against the current
Core2 source bytes before this implementation: `MemoryService._legacy_repository`
constructs `_LegacyMemoryAuditWorkRepository`; its `read_snapshot()` and
`_audit_connection()` reach `WorkRepository._verify`; `_verify` performs
component/catalog and role checks, then configured identity parsing, then the
inbox → control → recovery tail. The private repository owns the pooled
`_PooledAuditConnection`, whose `_LeaseState.call` guards any new batch call.
These input hashes match the current pre-port workspace:
`work_repository.py` `d3e34ab5f9480ccae4c1249a2d9d3dfaa3c3e06ce40c412f062cd0c1318b3b7b`
and `legacy_memory_audit_repository.py`
`215d360bc4a5d457a96a27b775885c4d816d778be93d69aaa42b857be1de4b1a`.
The candidate overlays are limited to those two runtime files; generic Work,
borrowed connections, and autocommit verification remain sequential. Planned
new edges are the base ordered context-validation seam, the private legacy
override, and the guarded pooled pipeline method. No schema, migration,
catalog, role, transaction-owner, or commit edge is intentionally changed.

This is a preimplementation impact/source proof tied to the existing graph,
not a refreshed generated graph or a claim that post-port acceptance has run.

### T099–T101 implementation verification — 2026-10-15

The two runtime files match the reviewed R3 candidate byte-for-byte after the
port: `work_repository.py` SHA256
`c9211ea08e7796c27ba21d71cec1b742ad0f4f4a8705319fa4cf6eccea693852` and
`legacy_memory_audit_repository.py` SHA256
`8c6dd825ecd58025469b2539f70c4c34157007dc10d1624f29bad552377718ba`. The
post-port changes stay on the preverified edges: only an active private audit
lease batches the ordered inbox/control/recovery tail; the existing full
component/catalog, history, role and identity checks remain before it. The
post-port source review confirms the actual dispatch chain:
`WorkRepository._verify` dynamically reaches
`_LegacyMemoryAuditWorkRepository._verify_store_context`, which calls the
leased connection's `_batch_verify_work_contexts`; successful rows are passed
to `WorkRepository._verify_batched_store_context`, while the fallback
sentinel dispatches to the base sequential `_verify_store_context` path. The
existing 399-node/1,775-edge module view adds no import edge for this change.
The Graphify graph cited above remains explicitly pre-port; no regenerated
post-port graph is claimed. The portable native module passed 8 cases after
correcting a test observer that had attempted set membership on a
non-hashable `psycopg.sql.SQL` statement.
That correction only changes test instrumentation. No postimplementation
Graphify extraction was run, and the graph does not claim a strict NFR pass;
the three-run `<1.0 s` acceptance remains open.
