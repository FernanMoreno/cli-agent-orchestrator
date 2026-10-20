# Validation: PostgreSQL-Durable Work Agent Messages

## 2026-10-17 focused verification

The existing healthy CAOS PostgreSQL container was reused with autostart disabled; no second PostgreSQL container was created.

- Work messages, schema migration, PostgreSQL ledger, origin dispatch, proxy privacy, AG-UI projection and route, ProcessAgentStep, and Docker backend suite: **185 passed**, 5 dependency deprecation warnings, 313.70 s.
- Human interaction AG-UI/restart harness: **35 passed** offline. PostgreSQL interaction recovery/replay module: **4 passed**, 5 dependency deprecation warnings, 25.77 s. The group/answer receipt replay is durable across a fresh service instance on the same PostgreSQL store; this is not a live provider or real API-process acceptance.
- The 33 R17 nodes recorded as PostgreSQL connection timeouts were rerun serially: **28 passed, 5 skipped**. The skips were the Docker opt-in test and four T097 Bubblewrap host characterizations. The Docker test passed separately with `CAO_BROWSER_DOCKER_ACCEPTANCE=1`; the four host characterizations require the pinned scratch binary at `/tmp/caos-exec/T097/bubblewrap-build/bwrap`, which is absent. The installed system `bwrap` is intentionally not accepted by these tests.
- The seven other R17 failed node IDs passed serially (**7 passed**), including the archive-umask and remote bridge response-loss regressions.
- Source-bound process-launcher refusal on an invalid pin: **1 passed**; it rejects before `Popen`. This verifies the fail-closed guard, not the reported OpenCode packet. No preserved receipt identifies an OpenCode stale hash, and the R18 OpenCode packet remains unbound, so no OpenCode provider call was made.
- `project-composition-check caos`: **403 files**, 1,787 dependencies, 5 contracts kept, 0 broken. Full Black check: **1,341 files unchanged**. Full isort check and `git diff --check` passed.

These checks clear the targeted Work-message and R17 failure-node regressions. The complete backend suite has not been rerun against the current worktree; its R17 full run remains recorded as failed (17,908 passed, 40 failed, 231 skipped). The focused reruns did not reproduce those failures, but four host-only Bubblewrap cases remain unexecuted, and full source-bound provider acceptance remains open.

## API replacement coverage correction

The API replacement regression now creates a new `WorkRepository`, `WorkOrigins`, and recovery `WorkMcpProxy` over the shared PostgreSQL target. API B reconciles API A's uncertain effect and performs the replacement receiver's `receive`/`ack` plus the replacement sender's exact retry through its own origin service. The test makes API A's message handler fail if any post-restart request reaches it, and still compares the retained AG-UI event through API A and API B.

- Corrected `test_api_replacement_replays_proxy_send_after_generation_reconciliation`: **1 passed**.
- Combined message AG-UI, interaction AG-UI/restart, PostgreSQL interaction recovery, generic `send_message`, MCP observer, inbox, and interaction-provider regression modules: **144 passed**, 5 dependency deprecation warnings, 72.27 s.
- The replacement is tested with fresh API message-service objects and separate Uvicorn stream clients over the shared database. The generation stop/reconcile/retry fixture still uses its original lifecycle services; this test does not prove that API B owns that full lifecycle. It remains an offline composition regression, not a real provider call or a full external API process restart acceptance.
