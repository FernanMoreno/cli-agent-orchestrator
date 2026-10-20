# Marked workflow legacy fixture correction (test-only)

`test/api/test_workflow_lifecycle_realserver.py` now owns a module-scoped `cao_server` fixture, calling the unchanged shared `_start_cao_server` helper. Before startup it writes the **actual supported setting** `workflow.require_approval: false` into a private JSON settings file. It explicitly passes that isolated `CAO_HOME_DIR` to the subprocess and clears only that subprocess's inherited enable-only `CAO_WORKFLOW_REQUIRE_APPROVAL` value. Other modules keep the shared fixture and production default-required behavior.

The spec-directory helper resolves beside that server's private database, so the parent process's imported CAO_HOME_DIR cannot redirect scripts or settings to another fixture/home. Shared health-budget, HTTP process startup, process-group teardown and every existing durable-before-ack, completion, detached read, cancel, result and error assertion are retained. The integration module's already-existing function-local explicit-false settings fixture was preserved; Black left it unchanged. No production or shared fixture files were edited, and no product authority checks were weakened.

RED supplied by parent: `/tmp/spec008-marked-workflow-realserver-current.log` had **4 failed,14 passed,1 skipped**; first failure boundary was `prepared_plan_required` on legacy submission before script execution.

GREEN actual command:

`.venv/bin/python -m pytest test/api/test_workflow_lifecycle_realserver.py test/api/test_workflow_lifecycle_integration.py -m '' --no-cov --basetemp=/var/tmp/cao-spec008-marked-workflows-fixed -q`

`/tmp/spec008-marked-workflows-fixed.log`: **18 passed,1 skipped,5 existing deprecation warnings,111.35s**. Black check on both files PASS; scoped diff whitespace check PASS; final scoped diff reviewed.

Limits: six no-provider real-HTTP cases exercise actual CAO server and actual script child processes in explicit legacy compatibility. The provider-backed YAML case still skips behind its existing live-provider opt-in prerequisite; no provider/subscription was launched. In-process integration YAML still uses its disclosed agent-leaf stub. These tests do not cover strict prepared-plan admission; that default and its dedicated regression suites remain unchanged. This fixture changes only the posture of its private module-owned server, not global settings or shared session servers.
