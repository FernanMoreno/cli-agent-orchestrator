# T060 memory/vault typing evidence

Scope: 72 initial diagnostics across 12 inventory-owned files; upstream `Memory*` ORM mapping changes coordinated with the data owner. Approved additional seams: `legacy_memory_access.py` and `memory_archive/base.py`. Discovered recovery composition failures extended the approved scope to `recovery_inventory.py`, historical recovery fixtures and `test_recovery_peer_profile.py`. Existing dirty changes were retained. No commits, external provider calls, canonical vault writes, checker suppressions or configuration changes were made.

## Root causes and changes

- Legacy audit decorators erased callable signatures. `ParamSpec` and `TypeVar` preserve arguments and return types; the vault projection wrapper forwards the same argument pack. Audit ordering, async/sync selection, transaction ownership and exceptions remain intact.
- `ContextVar` inferred only its `None` default. Its annotation now also records the enclosing repository/actor/operation tuple. Verified `Principal.scopes` is accessed after the existing principal type check.
- Untrusted JSON validation did not expose validated result shapes. TypeGuards expose strings and dictionaries of `object`; MCP helpers declare their actual dictionary result without adding `Any` values.
- Recovery manifests lost nested shapes behind `dict[str, object]`. TypedDict object/reference fields and checked predicates retain the semantic verifier. The sole manifest cast follows primitive and nested shape checks. Reference version remains `object`: the existing verifier checks equality with 1 rather than an integer-only rule. Source database, executable digest/size, staging and lease invariants are narrowed at their existing validated boundaries. Distinct memory-reference tuple names avoid incompatible local inference. Recovery schema and publication/provenance checks remain intact.
- Archive base typing omitted its service constructor argument. A TYPE_CHECKING-only declaration preserves runtime compatibility with existing fake registry backends. The bounded reader implements RawIOBase with byte-returning reads, a string member set and typed context-manager output. Traversal, link, decompression and member limits remain.
- BM25 loops reused path locals for optional vault candidates. Distinct locals retain the non-null guards. Curator context is narrowed before reuse. Requester anomaly logging narrows its scalar identity without changing sentinel lookup/authorization behavior.
- The data owner's ORM `Mapped` conversion resolves Column-versus-instance errors in vault reader/migration; that owner reports matching before/after column schema signatures.

### Recovery composition repairs

The fresh aggregate passed the vault/archive segment, then historical recovery fixtures failed because upstream local-peer tables were absent from the closed catalog. The exact first failure was `test_historical_v29_store_has_an_explicit_closed_inventory`: five additional tables, beginning with `local_peer_grants`. The peer-profile regression also reproduced the rejected current inventory before implementation (`peer-red.txt`).

Recovery **catalog 39** now explicitly names the five peer tables and their columns; **Work database migration schema 39 is unchanged**. Frozen catalogs 28–38 retain their definitions. Historical fixtures remove only the five empty peer tables; frozen-v38 receipts remain verifiable and are not restored as the current profile. Every peer table is `requires_future_profile`: its schema is classified, but populated project approvals, grants, challenges, replay fences or task receipts block portable capture and restore. No capability or physical project root is revived.

The defensive schema-object regression then showed that an undeclared inert trigger could be captured (`schema-red.txt`: expected `RecoveryBundleError` was not raised). Catalog 39 now admits exactly four native triggers: `beads_binding_identity_immutable`, `workflow_outbox_identity_immutable`, `coordinator_events_no_update`, and `coordinator_events_no_delete`. Existing continuation/beads verifiers validate their SQL. No native views are declared. Work-prefixed objects remain checked against exact Work migration DDL. Legitimate indexes are preserved; historical profile validation is unchanged. Inert trigger/view capture and restoration regressions verify rejection before destination or staging writes.

Graphify impact query before this behavior repair:

```bash
graphify query 'Recovery inventory capture restore local peer database tables authority' --budget 1000
```

The existing graph returned recovery, Work authority/repository and local-peer service boundaries; relevant conclusions were checked against the source models, closed inventory, capture/restore scanner and continuation/beads schema verifiers. Log: `/home/felni/tmp-cao015-types-memory/graphify-peer-impact.txt`.

## Reproduction and verification

Effective checker configuration is the unchanged `mypy.ini` used by repository CI. Initial focused red:

```bash
.venv/bin/mypy --follow-imports=silent src/cli_agent_orchestrator/services/knowledge_policy.py src/cli_agent_orchestrator/services/memory_service.py
```

Result: 15 original errors in these two files, captured in `/home/felni/tmp-cao015-types-memory/red.txt`. The complete initial domain inventory is `/home/felni/tmp-cao015-root/type-inventory/memory.json`.

Final focused command, using every path in that inventory plus the three approved seams:

```python
import json, subprocess
files = list(json.load(open('/home/felni/tmp-cao015-root/type-inventory/memory.json')))
files += ['src/cli_agent_orchestrator/services/legacy_memory_access.py',
          'src/cli_agent_orchestrator/services/memory_archive/base.py',
          'src/cli_agent_orchestrator/services/recovery_inventory.py']
subprocess.run(['.venv/bin/mypy', '--follow-imports=silent',
                '--cache-dir=/home/felni/tmp-cao015-types-memory/mypy-cache', *files])
```

Executed with `TMPDIR=/home/felni/tmp-cao015-types-memory .venv/bin/python`. Fresh result: `Success: no issues found in 15 source files`, exit 0. Log: `/home/felni/tmp-cao015-types-memory/final-mypy.txt`. Root owns the full-source checker and required project composition gate.

An initial test process saw a temporary editing typo (`typing.Anyquests`), corrected before subsequent runs. That stale process was interrupted at 170 passed and one transient import failure; it is not completion evidence. Fresh aggregate verification disables shared coverage output and includes explicitly selected integration files:

```bash
TMPDIR=/home/felni/tmp-cao015-types-memory .venv/bin/pytest --no-cov -o addopts='' -n 2 --dist loadfile -q --tb=short \
  test/services/vault \
  test/services/test_memory_archive_registry.py \
  test/services/test_memory_archive_okf_import.py \
  test/services/test_memory_archive_okf_export.py \
  test/services/test_recovery_bundle.py \
  test/services/test_knowledge_policy.py \
  test/services/test_legacy_memory_audit.py \
  test/services/test_memory_gateway.py \
  test/mcp_server/test_knowledge_tools.py \
  test/services/test_frozen_run_memory.py \
  test/services/test_memory_reconciliation.py \
  test/services/test_memory_service.py \
  test/services/test_integration_008_memory_tar.py \
  test/integration/test_memory_knowledge_policy.py \
  test/services/test_recovery_peer_profile.py
```

Fresh post-repair aggregate result: **1010 passed, 2 skipped, 7 warnings in 432.02s**, exit 0. Warnings concern existing Pydantic class-based configuration and multiprocessing fork deprecations. Log: `/home/felni/tmp-cao015-types-memory/final-tests-parallel.txt`. Workers have separate Python state and disposable fixture databases/files. The previous aggregate exposed the stale recovery catalogs and was stopped for root-cause diagnosis; it is not claimed as a passing full run.

Focused recovery catalog repair checks:

```bash
TMPDIR=/home/felni/tmp-cao015-types-memory .venv/bin/pytest --no-cov -o addopts='' -q --tb=short test/services/test_recovery_bundle.py
TMPDIR=/home/felni/tmp-cao015-types-memory .venv/bin/pytest --no-cov -o addopts='' -x -q --tb=long test/services/test_recovery_peer_profile.py
TMPDIR=/home/felni/tmp-cao015-types-memory .venv/bin/pytest --no-cov -o addopts='' -x -q --tb=short test/services/test_recovery_peer_profile.py -k unreviewed
```

The first two ran before executable-object closure: 85 existing recovery tests passed and 13 new peer/catalog tests passed. The third is the observed schema-object red test. The final aggregate repeats all of them with the four added trigger/view regressions. Separate late-domain verification (`--no-cov -o addopts='' -x -q --tb=short` selecting reconciliation/service/gateway/MCP knowledge/frozen files) passed 177 tests.

Supplementary earlier runs: real tar conversion/security tests, 11 passed; memory/knowledge plus vault audit composition tests, 33 passed. Their commands used default coverage options; the fresh aggregate repeats them without shared coverage.

Formatting: `.venv/bin/black --check` and `.venv/bin/isort --check-only` passed on all 17 inventory/helper/recovery implementation and regression files (Black: `17 files would be left unchanged`; both exits 0). Reproduce using the same 15-file list above plus `test/services/test_recovery_bundle.py` and `test/services/test_recovery_peer_profile.py`. Logs: `/home/felni/tmp-cao015-types-memory/final-black.txt` and `final-isort.txt`. `git diff --check` passed for the owned paths. Tracked diffs and the complete previously untracked tar reader were reviewed. Pre-existing recovery profile-v31 acceptance and unrelated memory/archive/writer edits were retained.

## Composition review

Reviewed boundaries: audit decorator → memory/relationship/archive operations; knowledge payload → gateway/MCP helpers; recovery manifest → receipt verifier → portable SQLite restore; bounded stream → unpacking → current importer; ORM metadata → vault reader/migration.

Source invariants reviewed: committed audit intent before file effects, caller-owned SQLite transaction continuity, no unauthorized legacy fallback, redaction before consumer budgeting, archived path confinement and no links, v2 durable publication before restoration, source identity and blocked-copy provenance, atomic publication/staging cleanup, registry compatibility and sentinel policy handling.

Real dependencies: disposable SQLite stores and physical temporary archive/vault files. Selected existing tests cover grant revocation, audit failures, migration/reconciliation, traversal/links/limits, recovery integrity/publication/provenance and import/export contracts. External providers are unnecessary for these local contracts. Root runs global architecture and composition gates on the integrated changes.

Local composition verdict: **PASS**. The complete fresh aggregate, focused mypy, formatting checks and diff review are green. Global architecture, full-source typing and project-composition gates remain with the root agent and are not claimed here. No durable vault knowledge persisted: this is gate repair evidence in the feature spec directory.

## Follow-up full-CI curator fixture closure

Root's integrated CI selection exposed two additional failures in `test/services/test_context_manager_agent.py`. Fresh full-file reproduction confirmed exactly **2 failed, 15 passed**: the curated-response test returned empty fallback, and the stale-response test never dispatched. Both fabricated an available curator and permitted policy reuse, but left `_get_terminal_context("t1")` to query a missing database terminal. The first incorrect test boundary was absent requester context, before provider/status/output dispatch; production correctly fails closed when it cannot determine that context.

Tests now explicitly supply same-session requester context for these two dispatch fixtures. The busy-curator fixture supplies that context too and asserts the StatusMonitor check is reached while input remains undispatched. A dedicated missing-requester-context test proves a found curator cannot bypass the fallback or reach policy reuse/dispatch. No production changes or policy weakening were made.

Exact red and green command:

```bash
TMPDIR=/home/felni/tmp-cao015-types-memory .venv/bin/pytest --no-cov -o addopts='' -q --tb=short test/services/test_context_manager_agent.py
```

Fresh green: **18 passed, 2 existing Pydantic deprecation warnings in 19.79s**, exit 0. Logs: `/home/felni/tmp-cao015-types-memory/curator-red.txt` and `curator-green.txt`. `.venv/bin/black --check test/services/test_context_manager_agent.py`, `.venv/bin/isort --check-only test/services/test_context_manager_agent.py`, and `git diff --check -- test/services/test_context_manager_agent.py` all exited 0; final test diff reviewed. Root reruns the global CI selection after all independently owned fixture fixes.

## T067 private snapshot coverage contracts and T068 curator discovery

Coverage-gap analysis used root's Graphify traversal (`coverage-graph-query.log`) and verified the actual snapshot store, producer migration, plan-v2 byte contract and terminal database producer against source. New test files are `test/services/test_private_plan_snapshot_contracts.py` and `test/services/test_memory_injection_contracts.py`; existing dirty files were preserved.

Snapshot contracts use physical temporary SQLite files, the actual snapshot migration and real component digests/BLOBs. They prove exact roundtrip/decoding, identity reconstruction distinct from private-byte verification, own and borrowed transaction ownership, rollback on conflicting attachment or SQLite abort, shared-run and prepared-approval reference retention, last-reference collection, noncanonical/malformed/digest-invalid and over-ceiling material rejection without truncation, corruption rejection, owner-only database/sidecar reads without chmod, permission repair before writes, missing store/schema without read migrations, unusable paths, closed connections and sanitized SQLite failures. Lifecycle assertions cover resource release and retained approval evidence (FR-012); input/identity and fail-closed validation support FR-011/FR-018. No core validation was mocked and no new skips were introduced.

Real-DB curator tests exposed T068: `database.list_all_terminals()` produces `tmux_session`, while `_find_context_manager_terminal()` read `session_name`. Eight real-dispatch assertions failed before the repair; previous curator tests mocked discovery and hid this mismatch. The first wrong boundary was consumer field selection. Root authorized the one-key production repair; it now reads `tmux_session`. There is no alternate supported producer requiring an invented legacy alias. The tests preserve real terminal discovery/context and metadata policy stamping/refusal; only external tmux, provider/status/output transports are substituted. They prove session isolation, unavailable/changed durable policy refusal, missing-provider fallback, actual lock contention, malformed/replaced/unclosed buffer rejection, processing response completion, transport exception lock release and settings/cwd failures. Red log: `/home/felni/tmp-cao015-types-memory/snapshots-curator-red.txt` (8 failed, 65 passed).

Exact fresh focused coverage command:

```bash
TMPDIR=/home/felni/tmp-cao015-types-memory \
PYTHONPATH=/mnt/c/users/ferna/onedrive/escritorio/caos/src \
COVERAGE_FILE=/home/felni/tmp-cao015-types-memory/coverage-new-snapshots \
.venv/bin/pytest -o addopts='' -q --tb=short \
  --cov=src/cli_agent_orchestrator \
  --cov-report=json:/home/felni/tmp-cao015-types-memory/coverage-new-snapshots.json \
  test/services/test_private_plan_snapshot_contracts.py \
  test/services/test_memory_injection_contracts.py \
  test/services/test_context_manager_agent.py
```

Result: **91 passed, 2 existing Pydantic warnings in 81.98s**, exit 0 (`snapshots-tests.txt`). Comparison of each report's `executed_lines` against root baseline `coverage-ci-diagnostic.json` `missing_lines` gives **221 newly covered baseline lines**: snapshot store 203, memory service 16, database 2. This is a per-run intersection and does not claim the global 87% floor; root merges/reruns independent contributions. The unique coverage raw file and JSON remain outside the repository.

Fresh focused mypy (`--follow-imports=silent --cache-dir=/home/felni/tmp-cao015-types-memory/mypy-cache src/cli_agent_orchestrator/services/memory_service.py`) exited 0, one source file clean (`t068-mypy.txt`). Black and isort check-only passed on the source and two new tests, all three files unchanged. Final new-file contents and one-key source patch were reviewed; diff check clean. Broader post-T068 memory/archive/vault/recovery regression run passed: **1101 passed, 2 skipped, 7 existing Pydantic/fork warnings in 383.96s**, exit 0 (`t068-broad-tests.txt`). Exact command repeats the preceding complete aggregate plus the existing curator file and these two new contract files. Production/tests are frozen; root owns full-CI coverage aggregation, global gates and task closure.
