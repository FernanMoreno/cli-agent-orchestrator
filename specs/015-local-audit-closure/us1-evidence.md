# US1 and local peer authorization/session evidence

## Scope and routing

Implemented T001–T009 and T018–T019. T007 required no MCP server change: its existing tools forward the full API receipt, now covered by a canceled-receipt regression. Added necessary operator recovery coverage and a real two-process acceptance test for T053. No commits, pushes, publication, history changes, or remote resources. Existing unrelated work was preserved.

The approved spec, plan, task list and C-001/C-002 contracts were read before changes. Graphify query `graphify query 'local_peer_service cancellation task receipt registry reservation' --budget 1500` identified the service, shared registry, task refresh, terminal service and reservation boundaries; its 534-node traversal was truncated, so each important boundary was verified against source. No Graphify facts were treated as proof or copied into the vault. Systematic debugging, tests-first regressions, compatibility-safe migration, composition review and completion verification were applied. Repository-level gate/signoff is owned by the parent implementation agent.

## Reproduced defects and tests-first commands

All commands use `.venv/bin/python` from the repository root.

1. `-m pytest --no-cov -q test/services/test_local_peer_recovery.py -k 'cancel or completion or stale'`: **4 failed, 1 passed, 8 deselected**. Cancellation returned `state=cancelled` with a running result; completion was overwritten during cancellation; stale refresh replaced a terminal receipt; a released lease was reactivated.
2. `-m pytest --no-cov -q test/services/test_local_peer_recovery.py -k terminal_receipt_retries`: **1 failed**. A failure between the task-database and shared-lease commits left the lease held forever.
3. `-m pytest --no-cov -q test/api/test_local_coordination_routes.py -k 'principal or unavailable'`: cross-principal task reads returned 200 and unavailable peers returned 404; both must fail before any unauthorized peer call. Additional failures in that evolving test run were fixture/selection effects and not treated as product proof.
4. `-m pytest --no-cov -q test/services/test_local_peer_recovery.py -k principal_binding_migration`: **1 failed** because the required additive ownership migration was absent.
5. Signed project session route tests initially returned 404. Injecting failure at the real backend-list boundary then reproduced **empty** instead of **unavailable**, because the generic session helper swallowed backend errors. A simultaneous `/tmp` capacity error affected a separate fixture; clean repetitions use the task-specific temporary directory below.
6. `TMPDIR=/home/felni/tmp-cao015-us1 .venv/bin/python -m pytest --no-cov -q test/services/test_local_peer_recovery.py -k legacy_terminal`: **4 failed** for legacy canceled/succeeded/failed receipts with missing or contradictory results. Their authoritative terminal state must survive repair, retained output must survive, and unproven legacy writers must remain fenced.
7. Same prefix with `test/services/test_local_peer_recovery.py -k operator`: **2 failed**. Terminal early exits bypassed operator stop verification and prevented release of legacy held reservations.
8. Same prefix with `test/cli/commands/test_peer.py`: **1 failed**. The CLI skipped confirmation/recovery for legacy terminal receipts lacking stop evidence.
9. Same prefix with `test/api/test_local_coordination_routes.py -k agent_session_query`: **1 failed**, because the agent-facing scoped peer-session query was absent.
10. Same prefix with `test/services/test_local_peer_recovery.py -k disappearing`: **1 failed**. A peer disappearing during status returned generic reconciliation rather than explicit unavailability and retained state.

The first cancellation run without `--no-cov` reproduced three failures but ended with a coverage SQLite internal error. Subsequent focused runs disable coverage to avoid concurrent shared coverage-file interference. `/tmp` later filled from unrelated activity; no unrelated files were deleted, and reruns use `TMPDIR=/home/felni/tmp-cao015-us1`.

## Changes and composition review

- State and result are written together. One conditional SQLite UPDATE makes the first terminal transition permanent across threads/processes; stale nonterminal and competing terminal updates return the persisted winner.
- Cancellation commits `state=cancelled` and a matching receipt containing `worker_stopped=true` only after terminal runtime deletion confirms success. Failed stop retains the lease and a nonterminal reconciliation state. A deterministic stop/completion interleaving and concurrent SQLite winner test cover races.
- Shared lease updates require the exact task/instance owner and cannot reactivate a released lease. A delayed old release cannot remove a successor's reservation. Heartbeat expiry does not grant permission to another writer.
- Receipt persistence and shared lease release span two databases. Reads/startup retry the exact-owner release when durable stop/task-terminal evidence exists; legacy records without evidence remain fenced.
- Legacy contradictory receipts are repaired using a compare-and-swap over their terminal state and original result. Output survives; stale observations are not promoted into stop proof. Operator recovery checks for an active worker, then annotates confirmed stop while preserving the terminal winner and output. The CLI prompts for this legacy case; valid completed/error/runtime-stop evidence avoids unnecessary re-review.
- Successful/error task completion continues to use native completed/error task-terminal evidence. Cancellation uses runtime stop proof. An idle process awaiting future input is not treated as an active task writer. No unsolicited teardown was added to completed tasks.
- Outgoing assignments bind the verified API principal in a nullable `requester_principal_id`. Cross-user reads/cancels with the same terminal/project fail before a network call. Idempotent task keys cannot transfer to another principal. Requested projects must match the requester terminal's working-directory project.
- The schema change is expand-only, serialized, idempotent and preserves old rows/results. Legacy ownership is not invented: unbound local task access is limited to local-operator/admin compatibility. Old readers ignore the nullable column; rolling back an old writer can create unbound records, which new ordinary JWT readers reject. No destructive contraction/backfill was performed.
- `session:read` is an explicit pairing capability. Existing grants are not auto-expanded; re-pairing is required. Signed receiver and agent-facing outbound routes return available/empty/unavailable snapshots, count disappearing sessions, filter terminals by project identity, and use bounded loopback requests without master credentials.
- Existing revoked-grant behavior remains intentional: an authenticated peer can read/cancel only its own retained task for cleanup, while new submission and session operations are denied. Scope, project, signature, process generation, requester and assignment ownership checks remain in effect.
- Known-inactive task peers return 503 `peer_unavailable`; disappearance during a read preserves the last durable task receipt and reports `peer_state=unavailable`/`error=peer_unavailable`. Cached terminal receipts remain authoritative after peer exit.

## Verification runs already completed

- Service/SQLite/reservation/auth/registry/API/MCP suite: **49 passed**, then **57 passed** after concurrency, legacy repair and scope additions. These intermediate runs do not substitute for the final fresh suite below.
- First successful real acceptance: `TMPDIR=/home/felni/tmp-cao015-us1 .venv/bin/python -m pytest --no-cov -q -m integration test/integration/test_local_peer_two_process.py`: **1 passed in 58.30s**. Two full CAO server processes, distinct private profiles, shared temporary registry and real `mock_cli` workers performed pairing, success, failure, cancellation, replay, revocation and cached result after peer exit. This run preceded final lease/hash/session/outage enhancements.
- Earlier acceptance failures were harness defects (test control routes placed after the frontend mount, synchronous helpers blocking the event loop during mutual identity probes, and a missing `asyncio` import). They were fixed in test code and are not reported as product acceptance failures or passes.

Final fresh focused suite, enhanced acceptance, artifact hashes and self-review results are appended after completion.

## Essential upstream stop-proof correction

Independent composition review found that ordinary `dismantle_terminal_runtime()` ignored a false/exceptional backend kill and continued destroying live resources, eventually returning success. A real cancellation-to-teardown regression (`... test/services/test_local_peer_recovery.py -k unacknowledged`) reproduced **2 failures**, covering both false and exception outcomes. The fix requires an acknowledged backend stop before unregistering inbox/FIFO/status/provider/worktree resources or deleting the row. Unacknowledged stop remains retryable and preserves the peer lease. An exact identity proof of ABSENT is also accepted so an earlier successful kill followed by deferred cleanup can be retried safely; unknown/present proofs remain fenced. Existing teardown success mocks now explicitly acknowledge True; the old exception-success expectation was replaced with metadata/resource retention.

The first expanded teardown run reported **150 passed, 4 failed, 43 deselected**. Those four worktree tests used unconfigured mock kills rather than acknowledging backend success; their doubles now return True. The fresh expanded result follows below.

## Real two-process acceptance

Enhanced acceptance command:

```bash
TMPDIR=/home/felni/tmp-cao015-us1 .venv/bin/python -m pytest --no-cov -q -m integration test/integration/test_local_peer_two_process.py
```

**1 passed in 52.33s**, after the upstream stop-proof correction. Both profiles ran full CAO servers and actual mock_cli workers in an isolated temporary tmux server. Test-only operator helpers performed the real one-use pairing flow; task/session/status/cancel operations crossed public HTTP endpoints and real signatures. No cloud credentials or external model account were used, and HOME was not reassigned. Both server processes and the test-owned tmux runtime were cleaned up. The environment had no inherited TMUX context; the fixture also explicitly clears it for future runs.

Verified outcomes:

- actual success, injected provider error, and cancellation receipts match their persistent task state;
- idempotent replay preserves task and terminal identifiers;
- real sleeping worker input is observed before cancellation;
- a concurrent project assignment is rejected while its owner's lease is nonreleased (observed `reconcile`, which remains protective); cancellation releases it after confirmed teardown;
- peer sessions progress from empty to available, then unavailable after process exit;
- revocation rejects new submission; a second explicit pairing restores authorization before the outage scenario;
- target exit causes fresh task submission and pending task query to return 503 `peer_unavailable`;
- the canceled terminal receipt remains identical after target exit;
- the shared file's SHA-256 is unchanged: `bb7006071d1c49b95099fbf94b365c58feaf1c3b1d5e3628c736e341c94ae47c` before and after.

Sanitized receipts, public process identities, observed session/lease states, HTTP statuses and hashes are saved in [us1-two-process-receipts.json](us1-two-process-receipts.json). Artifact SHA-256: `06c44478b70275e1b816f477241fb39feb158408e75e56023184b47d7e741a78`.

An earlier enhanced run failed because the assertion demanded the literal lease label `held`, although the observed `reconcile` lease correctly blocked all successors. The acceptance now asserts exact task ownership and nonrelease, and records the actual state instead of relabeling it.

Final expanded regression verification after stop-proof fixes and explicit successful backend acknowledgments:

```bash
TMPDIR=/home/felni/tmp-cao015-us1 .venv/bin/python -m pytest --no-cov -q test/services/test_local_peer_recovery.py test/services/test_work_reservations.py test/services/test_local_peer_auth.py test/services/test_local_peer_registry.py test/api/test_local_coordination_routes.py test/mcp_server/test_local_peer_transport.py test/cli/commands/test_peer.py test/services/test_terminal_tombstone_exact_cleanup.py test/services/test_terminal_service_coverage.py test/services/test_terminal_service_full.py -k 'not CreateTerminal'
```

**154 passed, 43 deselected, 5 warnings in 100.76s.** The deselection omits unrelated terminal-creation tests; this suite includes receipt persistence/races, migration, public authorization/session views, MCP canceled receipts, operator reconciliation, both ordinary backend stop failures, exact-cleanup retry and existing worktree teardown tests. Warnings are existing framework deprecations.

A fresh targeted type scan of local_peer_service and local_coordination_routes after annotations reduced the old snapshot's errors to one validated response JSON return (`Any`); this is explicitly cast to its checked dict type. Registry port conversion and pairing payload numbers now normalize object-valued input through string parsing; persisted ORM scalar reads use explicit scalar casts. No type ignores were added. The parent owns the full mandatory source type gate and reports its collective outcome independently.

After final scalar type normalization:

```bash
TMPDIR=/home/felni/tmp-cao015-us1 .venv/bin/python -m pytest --no-cov -q test/services/test_local_peer_auth.py test/services/test_local_peer_registry.py test/api/test_local_coordination_routes.py
```

**19 passed, 5 warnings in 13.51s.** Final tracked whitespace review (`git diff --check`) passed. Service/auth/registry/routes were formatted with Black; final narrow type scans are recorded below when complete. The prior full-source mandatory gate's failures and the parent's fresh full-source result are separate collective evidence, not overridden by these focused checks.

Final focused type checks, after all owned-source edits:

```bash
TMPDIR=/home/felni/tmp-cao015-us1 .venv/bin/python -m mypy --follow-imports=silent src/cli_agent_orchestrator/services/local_peer_service.py src/cli_agent_orchestrator/services/local_peer_registry.py src/cli_agent_orchestrator/api/local_coordination_routes.py
TMPDIR=/home/felni/tmp-cao015-us1 .venv/bin/python -m mypy --follow-imports=silent src/cli_agent_orchestrator/services/local_peer_auth.py
```

Both exited **0**: **no issues found in 3 source files**, and **no issues found in 1 source file**. Final Black check of these four files exited 0. Owned changes were self-reviewed against their source boundaries; unrelated dirty work remains preserved. Completed task IDs: **T001–T009, T018–T019, T053**. Collective mandatory gates remain the root agent's responsibility and are not claimed by this evidence.
