# Last two test seam migrations

Only `test/services/test_workflow_step_result.py` and `examples/workflow/tests/test_workflow_example.py` were edited. Both formatted; write ownership released to root/UI. No production, native coordinator/operations/delegation, shared conftest, authority, schema or default-approval changes.

1. Result-state fixture `_workflow_work_state_fixture.read_binding` now accepts the real additive keyword-only `historical=False`. It **asserts `historical is True`** for these pure state reads and requires an existing active SQLite transaction; it still returns a binding only for the exact attempt/generation/work tuple. This is a typed seam update, not arbitrary kwargs absorption or a live-authority fallback. Existing exact-state, old-binding/current-superseding-attempt, accepted-result and caller-owned-transaction assertions remain unchanged. The same full file also retains actual live result acceptance versus historical read-after-revocation coverage.
2. The example module now owns a per-test autouse private settings fixture, ordered after its existing `_temp_db`. Before `run_script_workflow`/the real child process, it writes the supported explicit JSON boolean `workflow.require_approval=false`, points this test process's settings file at it, and removes inherited enable-only approval env through monkeypatch. This is the example's explicit legacy-script compatibility mode, not a fabricated prepared plan or approval and not a global default change. Every fake HTTP recorded-call, bounded/distinct step-ID, fanout, retained survivor, injected failure, real subprocess and output-sentinel assertion is unchanged.

Captured RED evidence: `/tmp/spec008-services-final-parallel.log` first six failures are the old fixture rejecting `historical`; `/tmp/spec008-workflow-examples-final.log` three failures are unapproved v1 plans before example execution. Other three service failures remain root/UI-owned and were not changed here.

Actual full-file GREEN:

`TMPDIR=/var/tmp .venv/bin/python -m pytest test/services/test_workflow_step_result.py examples/workflow/tests/test_workflow_example.py --no-cov --basetemp=/var/tmp/cao-spec008-last-test-migrations -q`

`/tmp/spec008-last-test-migrations-green.log`: **42 passed,2 existing Pydantic deprecation warnings,52.86s**. Black check both owned files PASS; owned scoped diff whitespace check PASS; final scoped diff reviewed.

Limits: the six state-reader cases deliberately substitute only immutable binding lookup while reading native SQLite states. They are not an independent proof of private v2 closure or live grant authority. The example executes real script children and real HTTP transport against its disclosed fake endpoint, without providers or prepared scoped execution. Dedicated strict approval and native Work/coordinator regressions remain separate and were not disabled or edited. Global service closure is root's gate after the independently identified deadline bug; this report claims only these complete owned files.
