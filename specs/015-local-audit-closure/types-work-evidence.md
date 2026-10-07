# T058 Work type closure evidence

Scope: the 21 files in `/home/felni/tmp-cao015-root/type-inventory/work.json`, plus `services/work_operations.py` authorized by the parent. Existing user changes and other agents' files were preserved; no commits or remote mutations were performed.

## Baseline and causes

` .venv/bin/mypy --follow-imports=silent <owned files> ` reproduced **106 errors in 21 files** using the existing `mypy.ini` (no configuration weakening or extra suppressions). Full diagnostic output: `/home/felni/tmp-cao015-types-work/baseline.txt`.

Source investigation found unannotated local loader results, empty container inference, validation of heterogeneous JSON through `any(...)` that did not narrow individual values, optional handles whose initialization was already required by lifecycle paths, model enums/literals represented as strings, name reuse between dictionaries and model objects, and registry base-class return typing hiding the production durable connection subtype. No protocol behavior bug was demonstrated.

Changes preserve the exact PID/start-time/namespace/digest validation, generation/revision fences, lease and reservation semantics, stopped-writer requirement, transaction and event ordering, proxy effect guard under lifecycle lock, cancellation cleanup retention, and channel incarnation checks. Casts are limited to fields already validated against allowed literal sets or process identity content and the terminal-lock wrapper's unchanged argument forwarding. The libc `prctl` result becomes `int` at its declared ctypes `c_int` boundary. No Any aliases or type-ignore comments were added.

The terminal-lock decorator now preserves its callable's parameter and return types with ParamSpec and TypeVar; its ContextVars record their actual optional terminal/lock values. A socket passed as Popen stdin is represented by its same owned descriptor with `fileno()`, while socket close and process cleanup retain their original order.

No new behavior was specified, so existing meaningful process, SQLite, proxy, workflow and channel tests supply regression coverage instead of implementation-mirroring new tests.

## Verification

- Focused mypy: `/home/felni/tmp-cao015-types-work/final-mypy.txt`: **Success: no issues found in 22 source files**, exit 0. This final run includes the terminal decorator and the exact identity validation guards.
- Runtime suite initial run: `/home/felni/tmp-cao015-types-work/tests.txt`: **479 passed, 1 skipped**, but exit **3** after coverage teardown raised `DataError: no such table: file` in a shared `.coverage.*` artifact. This run is not counted as a passing gate. Exact-suite retry used separate `COVERAGE_FILE=/home/felni/tmp-cao015-types-work/work.coverage` plus the isolated TMPDIR: `/home/felni/tmp-cao015-types-work/tests-retry.txt`: **479 passed, 1 skipped, 5 warnings**, exit **0**, in 329.89s. The isolated coverage artifact eliminated the teardown error.
- Terminal dispatch and Work input fences (`test_terminal_service.py`, `test_terminal_service_full.py`, `test_terminal_service_coverage.py`, `test_work_terminal_input_fence.py`): `/home/felni/tmp-cao015-types-work/terminal-tests.txt`: **181 passed, 5 warnings**, exit 0.
- Registered launch dispatch/cancellation (`test/services/test_work_launch.py`): `/home/felni/tmp-cao015-types-work/launch-tests.txt`: **19 passed, 2 warnings**, exit 0.
- Final proxy lifecycle/failure-translation rerun (`.venv/bin/pytest --no-cov -q test/services/test_work_mcp_proxy.py`, isolated TMPDIR): `/home/felni/tmp-cao015-types-work/proxy-final-tests.txt`: **19 passed, 2 warnings**, exit 0.
- Coverage emitted `no-data-collected`; test results are asserted independently and these runs do not establish a coverage percentage.
- Final source diff reviewed; `git diff --check -- src/cli_agent_orchestrator/services/work* src/cli_agent_orchestrator/runtime_channel/server.py src/cli_agent_orchestrator/runtime_channel/bridge.py`: exit 0.

The exact broad retry command was:

```bash
COVERAGE_FILE=/home/felni/tmp-cao015-types-work/work.coverage \
TMPDIR=/home/felni/tmp-cao015-types-work .venv/bin/pytest -q \
  test/services/test_work_process_supervisor.py \
  test/services/test_work_process_supervisor_fds.py \
  test/services/test_work_process_restart_contract.py \
  test/services/test_work_process_cleanup_recovery.py \
  test/services/test_work_process_seccomp.py \
  test/services/test_work_elf_identity.py \
  test/services/test_work_reservations.py \
  test/services/test_work_scheduler.py \
  test/services/test_work_authority.py \
  test/services/test_work_continuation.py \
  test/services/test_work_mcp_proxy.py \
  test/services/test_work_executable_content.py \
  test/services/test_work_workflow.py \
  test/services/test_work_origin_authority.py \
  test/services/test_work_lineage_origin.py \
  test/services/test_work_launch_runtime.py \
  test/services/test_work_launch_gateway.py \
  test/services/test_work_bubblewrap_cleanup_recovery.py \
  test/services/test_work_launch_composition.py \
  test/runtime_channel
```

## Composition review

Changed boundaries: persistence projections/loaders → reservations/scheduler/authority; immutable contracts → launch/runtime admission; persisted process identities → supervisor/cleanup/isolation proof; lifecycle-locked proxy → durable effect journal; registry → durable channel inspection/compensation; terminal dispatch decorator → service and API callers.

Reviewed inputs and outputs, exact store ownership, snapshot and transaction fences, replay/idempotency, cancellation-retained cleanup tasks, protected proxy effect ordering, owned descriptors, and the prohibition against release based solely on expiry. Tests use real disposable SQLite stores and local sockets/processes; no real provider/account calls were invoked. External provider acceptance is outside this annotation closure.

The parent owns the complete Spec015 Graphify/architecture/project-composition check and overall final signoff. This evidence does not claim those global gates passed.

Final local verdict: **PASS WITH RISKS**: all owned type diagnostics resolved and applicable regression suites exited 0. One test skipped (the quiet invocation did not print its reason); default coverage emitted no-data-collected and does not establish a coverage percentage. Overall Spec015 architecture/composition and acceptance signoff remain with the parent.

## Owned source paths

- `src/cli_agent_orchestrator/services/work_process_seccomp.py`
- `src/cli_agent_orchestrator/services/work_elf_identity.py`
- `src/cli_agent_orchestrator/services/work_reservations.py`
- `src/cli_agent_orchestrator/services/work_bubblewrap_isolation_proof.py`
- `src/cli_agent_orchestrator/services/work_scheduler.py`
- `src/cli_agent_orchestrator/services/work_authority.py`
- `src/cli_agent_orchestrator/services/work_projection.py`
- `src/cli_agent_orchestrator/services/work_terminal.py`
- `src/cli_agent_orchestrator/runtime_channel/server.py`
- `src/cli_agent_orchestrator/runtime_channel/bridge.py`
- `src/cli_agent_orchestrator/services/work_process_supervisor.py`
- `src/cli_agent_orchestrator/services/work_mcp_proxy.py`
- `src/cli_agent_orchestrator/services/work_executable_content.py`
- `src/cli_agent_orchestrator/services/work_origin.py`
- `src/cli_agent_orchestrator/services/work_workflow.py`
- `src/cli_agent_orchestrator/services/work_admission.py`
- `src/cli_agent_orchestrator/services/work_launch_runtime.py`
- `src/cli_agent_orchestrator/services/work_continuation.py`
- `src/cli_agent_orchestrator/services/work_bubblewrap_setup_intent.py`
- `src/cli_agent_orchestrator/services/work_launch_gateway.py`
- `src/cli_agent_orchestrator/services/work_bubblewrap_composition.py`
- `src/cli_agent_orchestrator/services/work_operations.py`
