# US3 implementation evidence — spec 015

Scope: T028–T037. Existing dirty work was inspected before editing and preserved. No commits, pushes, publication, resets, or subagents were used. Parent owns plan/tasks updates.

## Source investigation and first incorrect boundaries

- T028/T033: `WorkflowContinuationDriver.tick()` selected the same unrestricted first128 non-stopping rows every round. A real SQLite fixture containing261 rows showed only128 unique visits over four rounds. Ordered keyset paging now advances both normal and stopping inventories, and wraps on the next empty page. Eligible rows behind waiting rows get a turn; existing lease, epoch, admission, deadline and result fences remain the owners of execution eligibility.
- T029/T034: `restore_once()` swallowed target exceptions; `run()` treated those pages as success and used zero-delay continuation when a cursor remained. Inventory exceptions also retained a stale cursor. Target failure reporting now reaches the loop, which delays1/2/4/8/16/30 seconds, then waits on the existing60-second maintenance cadence. Successful restoration resets backoff. Inventory-query errors reset the cursor. Target-page errors preserve cursor progress across cooldown so later observations cannot be starved by an unavailable prefix. Cancellation still drains an in-flight worker. Only observation is restored; input is never resent.
- T030/T035: completed task handles remained in `driver.tasks` until shutdown. Cleanup now retrieves exceptions and drops only the same task object, retaining task results and durable journal/result ownership. Driver-created tasks have completion callbacks; tasks assigned by existing coordinator paths are also reaped at the beginning of each tick. A late old-generation cleanup cannot remove a replacement.
- T032/T036: `body.update(opts)` replaced the entire managed `env_vars` object. User options now enter before managed body fields, and custom environment entries are merged before run/generation/step identity. Invalid environment shape gets a local `ShimError`.
- T031/T037: Pydantic's default extra-field policy silently ignored typos at the root, step and input-declaration levels. These authoring models now forbid extras, and `validate_only()` retains nested error locations such as`steps.0.promtp` and`inputs.task.requried`. Open JSON-Schema payloads and runtime result DTOs remain unchanged.

## Workflow and impact checks

Read `AGENTS.md`, relevant AI_WORKFLOW sections5–7, approved spec/plan/tasks and C-004 contract. Applied systematic debugging, tests-first, Spec Kit implement artifacts, Graphify and composition/verification skills. The spec-quality checklist had16 checked items and0 unchecked items; no extension hooks existed. Prerequisite command:

```bash
.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
```

Exit0; FEATURE_DIR resolved to specs/015-local-audit-closure with research/data-model/contracts/quickstart/tasks available.

```bash
graphify query 'workflow_continuation_driver terminal_observation_recovery managed agent options impact' --budget 1200
```

Exit0; graph had54428 nodes and found935 impacted/context nodes (output truncated to32). Graph output was orientation, verified against current driver, coordinator task assignment sites, SQLite continuation schema, terminal restoration, API lifecycle and shim/model source. No new package dependency or external protocol was introduced; parent controls the final Graphify refresh decision.

## Red/green commands and results

Initial setup tried`python`, which is absent; switched immediately to`python3` for edit scripts and`.venv/bin/python` for project checks. The first regression command with default coverage printed seven failures but coverage teardown crashed (`no such table: file` / mismatched uppercase/lowercase WSL paths). It was not accepted as clean evidence; every authoritative run below uses`--no-cov`.

Red command (before production fixes):

```bash
.venv/bin/python -m pytest --no-cov -q test/services/test_continuation_responsiveness.py::test_successive_rounds_reach_work_behind_128_waiting_rows test/services/test_integration_008_continuation.py::test_completed_handle_cleanup_keeps_result_and_new_generation test/services/test_integration_008_continuation.py::test_shim_options_preserve_managed_identity_and_custom_environment test/models/test_workflow.py::test_unknown_configuration_field_reports_its_path
```

Exit1: seven failures in46.66s, all at the expected behavioral assertions:128/261 visited rows, retained completed handle, forged identity accepted (both shim surfaces), and extras accepted at three model levels.

```bash
.venv/bin/python -m pytest --no-cov -q test/services/test_terminal_observation_recovery.py -k 'back_off or resets'
```

Exit1: two expected failures in33.63s. Actual delays were all0, rather than1/2/4/8/16/30/60 with success reset.

Initial green:

```bash
.venv/bin/python -m pytest --no-cov -q test/services/test_terminal_observation_recovery.py
.venv/bin/python -m pytest --no-cov -q test/services/test_continuation_responsiveness.py test/services/test_integration_008_continuation.py test/models/test_workflow.py test/cao_workflow
```

Exit0:4 passed in23.03s;136 passed in49.62s respectively. Existing real HTTP responsiveness, real SQLite projection/lease fences, YAML grammar and complete HTTP-only shim surface all passed.

Refinement red:

```bash
.venv/bin/python -m pytest --no-cov -q test/services/test_terminal_observation_recovery.py::test_cooldown_preserves_progress_beyond_six_failing_pages
```

Exit1: expected failure in31.36s; the initial reset-at-cap implementation restarted at the unavailable prefix (eighth delay1 instead of0). Parent approved preserving the target-page cursor across cooldown, while resetting only for inventory-query outage/completed inventory. The regression uses112 real SQLite terminal records,96 failed targets followed by16 successful targets.

Final broad run initially hit environment failure: `/tmp` tmpfs reached100% (4KiB free). The separate fairness check showed `OSError: [Errno28] No space left on device` and SQLite `database or disk is full` during fixture setup. Interrupted only this task's affected processes, preserved unrelated files, and reran with task-specific `TMPDIR=/home/felni/tmp-cao015-us3` on the902GiB-free home filesystem. These disk-full attempts are not acceptance evidence.

Final command (task-local temporary filesystem):

```bash
TMPDIR=/home/felni/tmp-cao015-us3 .venv/bin/python -m pytest --no-cov -q test/services/test_terminal_observation_recovery.py test/services/test_continuation_responsiveness.py test/services/test_integration_008_continuation.py test/services/test_integration_008_continuation_review.py test/services/test_integration_008_coordinator.py test/services/test_integration_008_coordinator_deadline.py test/services/test_integration_008_scoped_results.py test/models/test_workflow.py test/cao_workflow
```

Exit0:191 passed,7 existing dependency deprecation warnings in307.03s. Normal and stopping inventories each contain261 real SQLite rows. Callback lifecycle now also covers completion, exception and cancellation.

```bash
project-composition-check "$(cat .ai/project-name)"
```

Exit0 on three runs, including after both recovery refinements; final run analyzed400 files/1673 dependencies (earlier runs1669),5 architecture contracts kept,0 broken. It runs Import Linter; no separate contradictory architecture checker was invented.

```bash
git diff --check -- src/cao_workflow/__init__.py src/cli_agent_orchestrator/models/workflow.py src/cli_agent_orchestrator/services/workflow_continuation_driver.py src/cli_agent_orchestrator/services/terminal_observation_recovery.py test/services/test_continuation_responsiveness.py test/services/test_integration_008_continuation.py test/services/test_terminal_observation_recovery.py test/models/test_workflow.py specs/015-local-audit-closure/us3-evidence.md
.venv/bin/python -m compileall -q src/cao_workflow/__init__.py src/cli_agent_orchestrator/models/workflow.py src/cli_agent_orchestrator/services/workflow_continuation_driver.py src/cli_agent_orchestrator/services/terminal_observation_recovery.py
```

Exit0. Reviewed the implementation delta against pre-edit copies because driver/recovery files and several tests were already untracked, and model/shim had preexisting edits. Optional Black check flagged formatting in the driver baseline too; avoided unrelated sweeping formatting. Formatted recovery and newly added test blocks.

## Composition review report

- Changed subsystems: continuation scheduling/task handles, terminal observation maintenance, subprocess shim options, authoring grammar validation.
- Neighbors reviewed: coordinator's two task-registration paths; workflow journal/result registry and projection/outbox; continuation leases/process fences; terminal incarnation/lifecycle/dispatch locks; lifespan task startup and cancellation; shim HTTP-only boundary and model validation consumer.
- Invariants: real SQLite snapshot reads remain on worker threads; keyset cursors neither revoke leases nor execute ineligible work; terminal observations do not submit input; cooldown preserves progress; cancellation drains worker ownership; callbacks compare task identity and leave durable journal/results untouched; user configuration cannot replace managed correlation; errors include nested field location while JSON Schema remains open.
- Architecture: Import Linter through project composition gate,5/5 kept.
- Scenarios: above suites exercise SQLite result receipt/projection/restart, lease ownership races and stop fences, coordinator/deadline continuation, real HTTP health under SQLite verification, observation pagination/outages/reset/cancellation, task replacement/lifecycle, shim replay/identity/recovery and nested YAML grammar.
- Contracts: existing shim boundary/surface tests plus C-004 regressions. No new separately deployed protocol was added, so Pact was not applicable.
- Dependencies: real local SQLite and real local HTTP for the boundaries where behavior matters. Backend outage, observation delays and shim transport are controlled seams to avoid provider credentials/time dependence.
- Failures/root causes: all five specified defects reproduced; cursor reset-at-cap starvation reproduced and corrected with parent approval; coverage teardown and full tmpfs are environmental and recorded separately.
- Residual scope: real providers and multi-process peer acceptance belong to the parent feature acceptance tasks; no provider success is claimed here. The task-local temporary directory avoids the exhausted shared tmpfs.
- Verdict: PASS for US3 scoped local checks. Broad suite191 passed; final recovery6 passed after the last isolated retry-budget change; final architecture gate5/5 kept.

## Plan refinements

- Parent-approved recovery ruling: exponential1/2/4/8/16/30s, after six consecutive failure pages stay on existing60s maintenance until successful observation. Preserve target-page progress through that cooldown; reset on query outage/completed inventory. Success resets backoff; never resend input.
- Existing coordinator directly assigns tasks into the shared driver map. Avoided editing that unrelated owner: driver reaps externally assigned completed handles every tick in addition to callbacks for its own tasks.
- Strict extra validation also covers `InputDecl`, because nested misspellings are equally capable of silently changing workflow behavior.


## Final self-review: persistent outage after the retry budget

Found that resetting the failure counter at cooldown would allow a fresh fast retry window without any successful observation. The planning ruling resets after successful observation, so after six failures the counter now remains saturated and retries stay on60s maintenance until success.

```bash
TMPDIR=/home/felni/tmp-cao015-us3 .venv/bin/python -m pytest --no-cov -q test/services/test_terminal_observation_recovery.py::test_persistent_outage_stays_on_maintenance_until_success
```

Exit1: expected failure in47.29s, eighth delay1 rather than60. Fixed only the recovery budget transition. Focused green/composition results:6 passed,2 existing deprecation warnings in31.58s; composition gate exit0,5 contracts kept,0 broken (400 files,1673 dependencies). Final diff check exit0.

```bash
TMPDIR=/home/felni/tmp-cao015-us3 .venv/bin/python -m pytest --no-cov -q test/services/test_terminal_observation_recovery.py
project-composition-check "$(cat .ai/project-name)"
```
