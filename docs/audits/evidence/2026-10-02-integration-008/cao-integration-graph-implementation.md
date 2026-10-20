# T019/T020 graph implementation

Implemented in shared main working tree, preserving preexisting dirty007/Work changes. No commits, provider launches, runtime deployment, or child agents. Read spec/plan/contracts/tasks008 and the domain research, relevant AI_WORKFLOW instructions, TDD/verification/composition skills. Graphify existing cache/plugin/UI nodes were inspected in research and checked against actual source (generated line offsets are stale); repository-wide refresh remains parent T056.

## Changes and preserved contracts

- `src/cli_agent_orchestrator/graph/cache.py`: cache-owned strong-reference shared tasks, shielded compatibility caller waits, two active/two queued admission,600s active coroutine deadline, bounded64-entry LRU and64 failure diagnostics, observable queued/in_progress/rejected_queue_full/failed_deadline, exception retrieval, explicit async shutdown cancel+drain. Arbitrary owner key tuples retained. Existing tuple `(view,cached,as_of)` and lock-reference/expiry guarantees retained; `clear()` retains existing non-cancelling serialization, while shutdown explicitly drains detached work.
- `src/cli_agent_orchestrator/graph/providers/base.py`: optional capability hooks documented without default implementations so generic-provider fallback still cancels on request timeout.
- `src/cli_agent_orchestrator/graph/providers/memory.py`: common validated/canonical scope request helper and detached projection/status hooks. Current base_dir/repository/binding fingerprint/lint/scope owner partition retained; global/federated ignore irrelevant scope_id, project/session/agent retain normalized binding ID. Each request/hook remains audited; detached builder audits the entire read and redacts before cached completion. Captures exact current service/binding rather than reconstructing a historical native-only provider. Vault metadata-only reads, binding eligibility, knowledge policy failure propagation and relationship/lint ordering remain intact. Returned lint_error findings now mark failed enrichment and count; existing exception/relationship/vault diagnostics remain independent.
- `src/cli_agent_orchestrator/api/main.py`: graph imports, graph helper/catalog section and graph-only lifespan drain. GET /graph/providers registered ahead of variable route and read/write/admin-scope gated; exposes names only. GET/export existing legacy knowledge dependencies, private-scope checks and secret/export-path checks untouched. Retryable504 and Retry-After5 (600 after deadline) plus content-free allowed build status; whitelist protects standard envelope from both projection_status and queue exceptions. Generic providers retain request cancellation. Repeated waits on shared task register one API failure callback. Lifespan finally drains tracked graph coroutines.
- `docs/knowledge-graph-viewing.md`: actual catalog, detached ownership, limits, retry/degradation/policy boundaries, owner partition and coroutine/thread limitations; corrected relationship-store edge description. No unsupported new benchmark/performance claims.

## Tests changed

- New `test/graph/test_integration_008_graph.py` exercises cancellation->same original build, admission active/queued/rejection, deadline/recovery/bounded failure diagnostics, LRU/owner isolation, cache shutdown, protected API timeout envelope, generic cancellation, API shutdown, queue status overwrite defense and single failure callback across retries.
- `test/graph/test_api_routes.py`: catalog response/static precedence/auth.
- `test/graph/providers/test_memory_provider.py`: canonical global key owner isolation, invalid scope refusal, returned lint_error degradation.
- `test/graph/test_cache.py`: intentional caller-cancellation contract updated: cancelling caller retains builder and registered waiter gets original result. Remaining old lock/reference/clear/expiry tests stay green.

## TDD and verification evidence

1. New regressions against old source: `/tmp/cao-graph-red.txt`:13 failed,45 passed (expected missing functionality). Two fixture syntax/data errors were corrected before provider implementation; provider red rerun `/tmp/cao-graph-provider-red.txt`:3 failed,18 deselected with intended missing normalization/unknown scope/lint_error metadata.
2. Cache/API green `/tmp/cao-graph-cache-green.txt`:56 passed,6 deprecation warnings; memory green `/tmp/cao-graph-memory-green.txt`:21 passed.
3. Queue overwrite/deduplicated callback regressions red before their fixes `/tmp/cao-graph-envelope-red.txt`:2 failed.
4. Full focused composition command:
   `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest test/graph test/api/test_memory_policy_boundary.py test/services/vault/test_work_audit_composition.py -q --no-cov -p no:cacheprovider`
   `/tmp/cao-graph-final-tests.txt`: **155 passed**,6 existing Pydantic/Starlette/anyio deprecation warnings,28.77s,exit0.
5. After graph-only API formatting, fresh API/regression command:
   `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest test/graph/test_integration_008_graph.py test/graph/test_api_routes.py -q --no-cov -p no:cacheprovider`
   `/tmp/cao-graph-final-api-tests.txt`: **39 passed**,6 same deprecation warnings,11.78s,exit0.
6. `project-composition-check "$(cat .ai/project-name)"`: PASS,5 contracts kept/0 broken. Initial `/tmp/cao-graph-composition.txt`; fresh postformat `/tmp/cao-graph-final-composition.txt`,exit0.
7. `.venv/bin/black --check` owned graph/test Python files:7 files unchanged,exit0. API graph block formatted in isolation to preserve unrelated dirty source.
8. `git diff --check`:exit0, `/tmp/cao-graph-final-diff-check.txt` empty. Reviewed graph/API/local docs diff; API007 existing changes retained.

## Composition Review Report

Changed: GraphView cache/provider/API lifecycle/catalog; graph documentation/tests.
Neighbors checked: MemoryService/legacy_memory_access/knowledge_policy/audit context, WorkRepository actor/repository checks, vault binding/reader eligibility, wiki_lint persistence ordering, graph sinks/secret_gate, HTTP auth/private-scope dependencies and generic provider fallback.
Invariants: same authorized owner/binding key joins one build; no caller timeout cancellation of owned work; no crossowner view; invalid scopes reject; failed authority never degrades into optional success; redaction before cached result; queued admission bounded; explicit lifecycle drains; provider metadata cannot replace stable timeout contract; generic request ownership preserved; old tuple/clear behavior compatible.
Real dependencies: existing temporary filesystem/SQLite provider and vault-audit composition tests; controllable coroutine builders at expensive lint/admission boundaries. No real LLM/provider/network required to exercise these graph ownership contracts. No production cost benchmarking or already-running blocking-thread shutdown test.
Architecture: Import Linter5 contracts pass through project gate. Consumer/provider wire tests and safe knowledge boundary suites pass.
Root causes addressed: caller-owned cancellation duplicated projection; unbounded completed cache/build admission; absent catalog/retry build status; returned lint_error ignored. Additional composition fixes: queue exception could overwrite stable error fields and retry callers duplicated failure log callbacks.
Residual limits: deadline bounds awaited active coroutine (queued task may wait for bounded predecessor work first); `to_thread` already running cannot be forcibly stopped; metadata/view size isn't capped; multiprocess cache is per-process; async service task context follows existing local operator authorization/audit model rather than granting new authority. Whole008 coverage and final Graphify refresh belong to parent, not this component.
Verdict: **PASS WITH DOCUMENTED RUNTIME LIMITS**, T019/T020 ready for parent acceptance. No task checkboxes/ledger edited by this agent.
