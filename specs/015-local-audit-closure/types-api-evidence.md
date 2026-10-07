# T059 API, AG-UI and authentication typing evidence

Owned files: `security/auth.py`, `api/browser_auth_routes.py`, `api/main.py`, and `services/agui/{run_plane,handoff_approval,approval_bridge}.py`.

The existing dirty tree was inspected before edits and preserved. Root Graphify impact analysis was verified against the actual SDK and service source. Systematic debugging identified the first incorrect boundaries: untyped injected browser identity helpers; SDK constructors whose static signatures omit permitted extra fields; callbacks annotated as noncallable `object`; optional workflow state hidden behind boolean aliases; an input-delivery protocol that incorrectly prohibited a false acknowledgement; and decorated terminal methods whose signatures were erased upstream.

AG-UI events now use the SDK's typed `model_validate` boundary. Its `extra='allow'` policy preserves the existing thread/run/step extras and serialized payloads. No event dialect, ticket lifetime/single-use behavior, authorization scope, cursor expiration, or projection/retry authority was broadened. SDK `EventEncoder.__init__(accept: str = None)` does no work; when accept is absent, calling its default constructor preserves the original behavior while satisfying its static signature.

Browser helpers return a sealed `Principal` and accept the common HTTP/WebSocket connection interface. The principal factory accepts raw issuer/subject objects and retains its existing string, nonempty and length validation. `Request` injection annotations retain explicit casted `None` defaults because FastAPI recognizes `Request` directly, while legacy direct unit callers also omit the request. The server-owned browser service cast describes the existing injected port; it grants no request-supplied authority.

Workflow state is narrowed at existing refusal boundaries; mapping results are materialized as dictionaries and the prepared workflow helper has an explicit response contract. Missing managed retry fingerprints remain a 409 stale-transition refusal. Typed existing scoped record fields and decorator preservation are supplied by the runtime/work owners.

Baseline command:

```bash
.venv/bin/mypy --follow-imports=silent src/cli_agent_orchestrator/security/auth.py src/cli_agent_orchestrator/api/browser_auth_routes.py src/cli_agent_orchestrator/api/main.py src/cli_agent_orchestrator/services/agui/{run_plane,handoff_approval,approval_bridge}.py
```

Fresh baseline: **107 errors in 6 files**. Full output: `/home/felni/tmp-cao015-types-api/before.txt`.

Final API validation (fresh commands):

```bash
.venv/bin/mypy --follow-imports=silent --cache-dir=/home/felni/tmp-cao015-types-api/mypy-cache src/cli_agent_orchestrator/security/auth.py src/cli_agent_orchestrator/api/browser_auth_routes.py src/cli_agent_orchestrator/api/main.py src/cli_agent_orchestrator/services/agui/{run_plane,handoff_approval,approval_bridge}.py
TMPDIR=/home/felni/tmp-cao015-types-api .venv/bin/pytest -q test/security/test_auth.py test/services/agui/ test/api/test_agui_run_endpoint.py test/api/test_agui_resume_endpoint.py test/api/test_browser_auth.py test/api/test_browser_channel_auth.py test/api/test_ws_auth.py test/api/test_workflow_managed_ingress.py
TMPDIR=/home/felni/tmp-cao015-types-api .venv/bin/pytest -q test/api/test_run_step.py test/api/test_run_step_replay_branch.py test/api/test_workflow_runs.py test/api/test_work_launch_ingress.py test/api/test_work_launch_composition.py
TMPDIR=/home/felni/tmp-cao015-types-api .venv/bin/pytest -q test/graph/test_api_routes.py
```

Results: **mypy: no issues in 6 files; tests: 489 + 213 + 32 = 734 passed**. Logs: `verified-mypy-final.txt`, `tests.txt`, `main-tests-final.txt`, `graph-tests.txt` under `/home/felni/tmp-cao015-types-api`. Formatting was applied with Black, and `git diff --check` passed. Source/diff review confirmed constructor changes preserve SDK extra fields and response mappings preserve service results. Existing deprecation warnings and coverage-path warnings did not fail checks. Full project typing/composition validation is owned by the root task.

 The first pytest collection exposed an evaluated TYPE_CHECKING-only annotation; that annotation was quoted and the suite restarted. No provider accounts, live provider runs, remote mutations, commits or publication were used.


## Additional runtime ownership

Root extended ownership to `backends/docker_backend.py`, `backends/bubblewrap_backend.py`, `services/workflow_step_projector.py`, `services/workflow_spec_service.py`, and the remaining `services/flow_service.py` diagnostic. Fresh focused baseline: **16 errors in 5 files**, log `runtime-before.txt`.

Runtime root causes: missing WeakSet element/WorkOrigins optional annotations, a validated three-element version tuple inferred as variable length, mutable cleanup identity variables captured by a Docker isolation callback, post-validation projector statuses still inferred as broad strings, Python source validation returning a tier union, and a short-circuit boolean context propagating an unrelated expected type into `asyncio.to_thread`.

Corrections preserve the existing backend registration, host acceptance, proxy lifecycle, locking, exact approved Bubblewrap version, projection state contracts, and source-only parsing. Docker proof inspection still reads current cleanup state and refuses absent runtime identity. Projector returns the existing exact status constants after the original refusal guards; no result is synthesized for uncertain state. The flow busy check retains its original conductor truthiness and only separates the nested condition for type inference.

Final runtime validation:

```bash
.venv/bin/mypy --follow-imports=silent --cache-dir=/home/felni/tmp-cao015-types-api/mypy-cache src/cli_agent_orchestrator/backends/{docker_backend,bubblewrap_backend}.py src/cli_agent_orchestrator/services/{workflow_step_projector,workflow_spec_service,flow_service}.py
TMPDIR=/home/felni/tmp-cao015-types-api .venv/bin/pytest -q test/backends/test_docker_backend.py test/backends/test_docker_work_supervisor.py test/backends/test_bubblewrap_backend.py test/services/test_workflow_step_projector.py test/services/test_workflow_step_projection_journal.py test/services/test_workflow_spec_service.py test/services/test_flow_service.py
TMPDIR=/home/felni/tmp-cao015-types-api .venv/bin/pytest -q test/services/test_work_docker_isolation_proof.py
```

Results: **no mypy issues in 5 runtime files; 214 runtime tests + 1 Docker isolation-proof test passed**. Logs: `runtime-after.txt`, `runtime-tests.txt`, `docker-proof-tests.txt`.

A final combined focused gate rechecked all **11 owned files** together: **no issues** (`all-owned-mypy.txt`). Black's final check reported **11 files unchanged** (`format.txt`). `git diff --check` passed and final source/diff review retained unrelated existing changes. Total relevant tests passed across all five commands: **949**. This is local unit/API proof; real Docker/Bubblewrap host acceptance, provider execution and full project composition remain outside this agent's scope and are not claimed here.

## Composition review corrections

The independent review identified malformed generic remote payloads bypassing typed return contracts. Root authorized narrow consumer validation in `terminal_service.py` (key acknowledgement, cwd, input sequence, output and output range) and `turn_recovery_service.py` (get/verify/cancel turn). Exact bool/int checks exclude false acknowledgements and bool-as-int; string/dictionary guards reject malformed decoded values. Valid values and existing missing-field/default semantics remain intact. DATA separately corrected concurrently disappearing projections with its own failing-first regressions.

New acknowledgement cases first reproduced **7 failures / 2 passes**; scalar cases first reproduced **35 failures / 24 passes**. Final commands:

```bash
TMPDIR=/home/felni/tmp-cao015-types-api .venv/bin/pytest --no-cov -q test/services/test_remote_scalar_responses.py test/services/test_remote_key_acknowledgement.py test/services/test_remote_projection_disappearance.py test/services/agui/test_durable_handoff_approval.py test/runtime_channel/test_remote_services.py test/services/test_turn_recovery.py
.venv/bin/mypy --follow-imports=silent --cache-dir=/home/felni/tmp-cao015-types-api/mypy-cache src/cli_agent_orchestrator/services/{terminal_service,turn_recovery_service}.py
.venv/bin/isort --check-only src/cli_agent_orchestrator/services/{terminal_service,turn_recovery_service}.py test/services/test_remote_{key_acknowledgement,scalar_responses}.py
.venv/bin/black --check src/cli_agent_orchestrator/services/{terminal_service,turn_recovery_service}.py test/services/test_remote_{key_acknowledgement,scalar_responses}.py
git diff --check
```

Fresh results: **100 passed; mypy no issues in two files; isort/Black checks pass; diff check passes**. Logs: `key-ack-red.txt`, `scalar-red.txt`, `scalar-green.txt`, `scalar-mypy.txt`. Source review confirms no new casts, Any annotations, ignores, schema redesign or authority grants. The independent verdict and schema/SQL/lifecycle checks are recorded in `types-composition-review.md`. The 100-test focused suite overlaps earlier tests and is not added to the 949 figure. Root retains responsibility for global completion gates.
