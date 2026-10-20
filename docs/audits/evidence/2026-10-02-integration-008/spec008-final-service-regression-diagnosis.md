# Final service regression diagnosis and scoped fixes

Outcome: PASS for affected gates; approval-required defaults and exact teardown identity retained. Original10 failures were traced individually; not all were stale tests. Product edits are limited to two small mechanisms.

## Real product defects, reproduced before edits

1. `services/config_service.py` described default approval asFalse in `WorkflowConfig`, `CAOConfig` and registry option metadata, while actual settings resolution/get_config requiredTrue. Pure source probe showed False/False/False/True. New executable assertion failed before the fix. Changed only WorkflowConfig.require_approval and ENV_REGISTRY's workflow.require_approval default toTrue; registry derives from that row. Existing effective precedence/security opt-out behavior is unchanged.

2. `services/work_workflow_plans.py:WorkWorkflowPlans.start` opened/migrated the approval store before its durable start transaction. Raw sqlite3.OperationalError escaped; `_start_scoped_workflow` correctly transported typed plan refusals but did not convert this raw error to503. Independent actual private prepared plan + verified owner + isolatedSQLite proved both submitFalse/True threw raw database error rather than HTTP503, with no run row. `/tmp/spec008-final-prepared-store-diagnosis.log`2RED and `/tmp/spec008-final-service-fixes-red.log`3RED (includes default metadata assertion).

Minimal source fix at the responsible domain boundary:

```python
try:
    with approval_store._connect() as approval_connection:
        pass
except sqlite3.Error:
    raise WorkflowPlanRefused("plan_approval_unavailable", 503) from None
```

Existing API mapping handles this typed refusal; API source unchanged. New regression asserts503, exact safe error kind, omission of raw database message, and absent run row.

## Honest fixture migrations

- `test_approval_gate_integration.py`2 failures: unprepared legacy script starts correctly return422 prepared_plan_required before reaching the approval store. Tests now explicitly prove both route arms refuse before store access/no run; actual prepared-store503 proof moved to the new native boundary test rather than turning off the production gate.
- `test_config_service.py`: effective requiredTrue expectation reflects current contract; new test also compares direct schema defaults, registry metadata and effective default.
- `test_frozen_context_proof.py`: absent settings requires actual frozen plan approval; explicit operator JSON booleanfalse compatibility has its own real-setting test. No mock gate predicate substituted.
- `test_manifest_freeze.py`2 failures: missing/unavailable frozen identity is PlanApprovalUnavailableError503, not missing approvable-plan403. Real over-budget/unavailable baselines and no readable plan_id assertions preserved.
- `test_plugin_event_emission.py`4 originally listed teardown failures plus two later identical fixtures: bare mocked terminal IDs were absent from SQLite, so atomic legacy incarnation binding correctly refused. The helper now creates actual SQLite terminal rows and a durable matching current session incarnation before native teardown mocks. Selection/backfill/ownership guards are not patched. Event-after-confirmed-kill order, per-terminal events, unusable-payload isolation, failed-kill refusal, sweep recovery and no event for surviving row assertions retained.
- Full plugin file revealed3 additional stale successful-send assertions: causal send_input returns the StatusMonitor turn sequence, and an unset mock was a MagicMock rather than boolTrue. Fixture now returns sequence7 and asserts exact7 plus notify_input_sent(real_send=True), preserving dispatch ordering and payload checks; product terminal source unchanged.
- `test_recovery_decision_intake.py`14 failures in root remaining-services gate: historical NULLmanifest runs reached newly required approval before their intended consent/revocation boundaries. Only module-private journal fixture sets isolated settingsJSON workflow.require_approval:false and clears environment enable. All consent/revocation assertions unchanged; actual production settings reader remains in use.

## Paths changed in this scoped bundle

Product:
- src/cli_agent_orchestrator/services/config_service.py (two defaults)
- src/cli_agent_orchestrator/services/work_workflow_plans.py (typed preliminary SQLite store fault)

Tests:
- test/services/test_config_service.py
- test/services/test_approval_gate_integration.py
- test/services/test_frozen_context_proof.py
- test/services/test_manifest_freeze.py
- test/services/test_plugin_event_emission.py
- test/services/test_recovery_decision_intake.py
- test/services/test_integration_008_prepared_store_failure.py (new actual prepared native boundary regression)

No API/frontend/browser/Graphify/docs/authority/teardown product changes, commits, provider launches or external credentials. Dirty unrelated source preserved; these are selective edits, no whole-file source replacement.

## Fresh acceptance

1. Six full affected files (config, new prepared-store, approval integration, frozen context, manifest freeze, plugin events), `--basetemp=/var/tmp/cao-spec008-service-fixes --no-cov -p no:cacheprovider`: **93 passed**,39.12s `/tmp/spec008-final-service-fixes-final.log`.
2. Nearest actual `test_integration_008_prepared_plans.py`, private separate basetemp/no-cov/no-cache: **29 passed**,37.19s `/tmp/spec008-final-service-fixes-prepared.log`.
3. Full `test_recovery_decision_intake.py`, TMPDIR=/var/tmp +private var/tmp basetemp/no-cov/no-cache: **81 passed**,54.45s `/tmp/spec008-final-recovery-consent-fixture-green.log`.

203 passing cases across3 gates, not a claimed single203-case run. Initial full affected gate90passed/3stale message fixtures failed; diagnosed then fresh93green. No test skip/xfail added. Black checks for owned test files pass and scoped diff whitespace clean. Composition outcome appended below.

Final current composition: **5 contracts kept, 0 broken**, `/tmp/spec008-final-service-fixes-composition-final.log`. Final owned test Blackcheck and scoped whitespace check passed after the recovery fixture change. Source ownership released to root; no further edits planned in this bundle.

Final formatting follow-up requested by root: isort sorted only the new `test_integration_008_prepared_store_failure.py`; Black preserved it; both individual isort/Black checks passed. Assertion/source behavior unchanged; no extra product edits.
