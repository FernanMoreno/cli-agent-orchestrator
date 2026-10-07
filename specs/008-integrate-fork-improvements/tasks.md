# Tasks: Integración completa de mejoras del fork

Plan: [plan.md](plan.md). Execute in main; no commits/push/publication. Original dirty source snapshot: /tmp/cao-integration-008-baseline-root.

Tests precede corresponding implementation. Shared files have one owner; independent research/reviews may run in parallel. Each capability is complete only after acceptance evidence, not file presence.

## Setup

- [x] T001 [setup] Capture baseline snapshot and original dirty status outside repository; preserve source and .specify context.
- [x] T002 [setup] Create complete frozen source/capability ledger in coverage.json and compare32 refs against remote-head evidence.
- [x] T003 [setup] Consolidate design/contracts/research and execute Graphify impact + inspect source boundaries.

## C01 — Core execution reliability

- [x] T004 [US1] Add real post-send timeout to workflow retry regression in test/services/test_integration_008_core.py; assert one delivery/live handle.
- [x] T005 [US1] Set delivery uncertainty for post-send failures in services/agent_step.py and preserve no-redelivery workflow_service.py guard.
- [x] T006 [US1] Add partial provider reconstruction/LAST regression with durable pending state in test/services/test_integration_008_core.py.
- [x] T007 [US1] Publish restored providers atomically and fail closed on receipt/recovery mismatch in providers/manager.py and terminal_service.py.
- [x] T008 [US1] Add DB outage capture/deadline budget regressions in test/services/test_integration_008_core.py.
- [x] T009 [US1] Separate local budget/backoff/deadline from reporting persistence in services/terminal_service.py; retain generation fence.
- [x] T010 [US1] Add Codex restarted post-answer picker digest regression in test/providers/test_codex_provider_unit.py.
- [x] T011 [US1] Verify strict Codex picker candidates with restored digest in providers/codex.py without answering dialog.
- [x] T012 [US1] Add blocking workflow extraction exception owner/state regression in test/services/test_integration_008_core.py.
- [x] T013 [US1] Converge non-engine substrate failures in workflow_service.py/agent_step.py without retrying delivered work.

## C02 — Policy and provider compatibility

- [x] T014 [US2] Add unknown/custom role, explicit empty tools, MCP glob and broker-denial regressions in test/utils and test/mcp_server.
- [x] T015 [US2] Unify resolve_allowed_tools, selectors and assign_elastic guard in utils/tool_mapping.py, mcp_server/server.py and provider launch policy.
- [x] T016 [US2] Adapt final enforcement table, OpenCode @builtin, Kiro tools-at-install and private Grok rules with install/launch tests.
- [x] T017 [US2] Resolve code_supervisor prompt/tool contradiction using inline task descriptions and delegated persistence; test actual catalog.
- [x] T018 [US2] Port ShimHTTPError structured refusal and OpenCode v1 spaced marker/Kiro2.25 extraction fixtures while preserving007/v2.

## C03 — Graph and consumer contracts

- [x] T019 [US2] Add graph detached-build cancellation/admission/deadline/isolation/degradation regressions in test/graph.
- [x] T020 [US2] Adapt fix/r3-aprime cache/providers and catalog/timeout endpoints preserving owner/policy keys and strict scope normalization.
- [x] T021 [US2] Add Apps-only fail-closed and HTML availability tests; adapt r4 middleware, plugin startup and ext_apps resources.
- [x] T022 [US2] Preserve structured turn diagnostics and add fenced recovery to ops MCP and MCP Apps; honest capability controls.
- [x] T023 [US2] Add quota/reconcile/turn diagnostics to Rust TUI and verify API/CLI/main MCP/web equivalence.

## C04 — Lifecycle and providers

- [x] T024 [US1] Implement incarnation/backend identity/tombstone/deferred failure migrations with restart/exact cleanup/recovery inventory tests.
- [x] T025 [US1] Adapt canonical tmux namespace/env/FIFO acceptance, herdr uncertainty and compensation semantics; preserve Work/007 locks.
- [x] T026 [US4] Implement Kimi Code dialect/isolated runtime-home/transcript rejection and persisted provider variant, retaining legacy.
- [x] T027 [US4] Implement bounded opt-in native Kimi swarm with inbox/status/epoch tests and parent-result receipt guard.
- [x] T028 [US4] Implement pane mode/layout/captions/stable identity resolver and bounded dying-server retry with isolated tmux checks.
- [x] T029 [US1] Adapt735 causal protocol as turn_sequence fields and pin verdicts to dispatch; port randomized model and007 composition tests.

## C05 — Workflow and tasks

- [x] T030 [US3] Implement guarded no-replace create and SHA CAS canonical publication/index-rebuild semantics; concurrency/symlink/mode/fsync tests.
- [x] T031 [US3] Complete source validation/errors and CLI/API/MCP authoring verbs with current auth/allowlist/DTO compatibility.
- [x] T032 [US3] Implement bounded SCOPE parser/targets/bindings and plan-v2 canonical material identity preserving historic plan-v1.
- [x] T033 [US3] Implement private snapshot/effective policy/launch closure/profile-reference freezing using current Work authorization.
- [x] T034 [US3] Integrate scope/material checks into Work/legacy admission and resume before effects, preserving executable and grant pins.
- [x] T035 [US3] Complete usable structured plan review/admin approval/run and atomic settings default-required migration together.
- [x] T036 [US4] Implement optional bd adapter CRUD/comments/labels/notes/status/priority with bounded subprocess and workspace tests.
- [x] T037 [US4] Implement durable external bead bindings/idempotent assign/unassign and task-session mapping through Work.
- [x] T038 [US4] Implement epics/dependencies/readiness/progress/concurrency/ancestry context with authorized frozen material.
- [x] T039 [US4] Expose task/epic planning CLI/API/MCP/web and coordinator launch/profile/auto-mode with current permissions.

## C06 — Remote and KAS

- [x] T040 [US4] Add opt-in KAS profiles/Cedar compiler/lint/launch guard and profile interfaces with no permissive fallbacks.
- [x] T041 [US4] Implement fleet UI node routing/registry/proxy and stale-response/auth-outage semantics from best fleet v2 family.
- [x] T042 [US4] Implement runtime_channel protocol/token/registry/bridge and scoped central routing, retaining target_host mode.
- [x] T043 [US4] Integrate runtime incarnation/op_id/deadline/reconnect status and cancellation/compensating delete with durable placement.
- [x] T044 [US4] Verify Kubernetes/elastic/fleet CLI equivalent behavior; integrate missing deployment fixes without activating resources.

## C07 — Autonomous progress and continuation

- [x] T045 [US1] Add durable generation-fenced assignment intent for response-loss R01 and integration test proving one accepted delivery.
- [x] T046 [US1] Implement late-receipt durable observation sweep/event recheck with independent budgets; no repaste after reconcile.
- [x] T047 [US3] Implement accepted-result continuation outbox/projector and one driver per run with revoke/restart/duplicate-event tests.
- [x] T048 [US3] Implement Work-backed checkpoints/iteration/feedback/progress/fresh-artifact validators and bounded correction via inbox.
- [x] T049 [US4] Expose Ralph-compatible start/status/feedback/stop/complete and escalation evidence with real completion/stop semantics.

## C08 — Complete coverage and verification

- [x] T050 [US4] Add latest PR-health example/guard/templates/tests and scoped review docs; fake gh only, no schedules registered.
- [x] T051 [US4] Integrate functional CI/build/dependency/release workflow changes using latest compatible policies; regenerate lockfiles.
- [x] T052 [US4] Prove equivalent profile CRUD/TUI/memory/vault/archive/regex families and reconcile every branch ledger item with tests.
- [x] T053 [verify] Run component contract/integration/full applicable Python/frontend/build/Rust gates and resolve failures.
- [x] T054 [verify] Run project-composition-check and system-composition-review including persistence/order/retries/authority/real dependency paths.
- [x] T055 [verify] Run isolated real providers/backend/fleet/bridge acceptance where available, documenting actual external prerequisites honestly.
- [x] T057 [US1] Prove G04 operational owner-bound diagnostics for held capacity/offline nodes and existing authorized recovery/resume; close only demonstrated gaps without lease-expiry release or blocked-restore activation.
- [x] T056 [verify] Review final diff, update Graphify after structural changes, assess durable knowledge and verify ledger100% coverage.

## Dependencies and execution

C01 before automatic recovery/continuation. C02 before provider/KAS/control modes. C03 graph/Apps may proceed alongside C01 only with disjoint ownership. C04 identity before735/bridge. C05 frozen material before scoped autonomous tasks. C06 preserves local/backends and requires lifecycle ownership. C07 follows C01/C02/C04/C05. C08 closes all components; no pending capacity may be omitted.

Reproduction commands and outcomes: quickstart.md, docs/audits/evidence/2026-10-02-integration-008/evidence-index.json. No commit steps because user has not requested commits. Spec Kit hooks absent.

## Phase 9: Convergence — autonomous native acceptance repair

- [x] T058 [US1] Reproduce OpenCode v2's stale-duration extraction from authentic final receipt captures; extract and verify the current answer while rejecting historical/echoed receipts and active processing, per FR-005/FR-015 (acceptance evidence in the autonomous repair audit).
- [x] T059 [US1] Restore observation of existing native terminals after server restart with backend identity/incarnation/Work fences, retained receipt generation and no input replay; preserve provider runtime dialect and pending inbox delivery for all registered providers, per FR-007/FR-008/SC-005 (acceptance evidence in the autonomous repair audit).
- [x] T060 [US2] Recognize the native OpenCode quota refusal and audit quota/error handling and receipt restoration across the registered provider catalog; expose blocked state without false success or duplicate task execution, per FR-009/FR-015/SC-004 (acceptance evidence in the autonomous repair audit).
- [x] T061 [verify] Repair the same acceptance demo's missing GET stats and undeclared cleanup controls; prove actual HTTP statistics, edit/clear UI behavior and unchanged original notes in isolated data fixtures, per FR-020/SC-006 and explicit user repair request (acceptance evidence in the autonomous repair audit).
- [x] T062 [verify] Run provider-wide regression/contract gates, real native acceptance where authenticated capacity is available, restart/inbox composition checks and final diff review; report prerequisites separately from verified corrections, per FR-020/SC-006 (acceptance evidence in the autonomous repair audit).

- [x] T063 [US1] Bind each receipt contract to the worker's own terminal identity and explicitly require the final visible reply after any result callback; preserve receipt generation and reject reasoning/tool-argument/callback substitutions. Verify against the native model's observed identity/receipt-placement confusion without synthesizing completion evidence.

- [x] T064 [US1] Accept an eight-digit numeric terminal reference at the shared MCP send_message boundary without guessing leading zeroes, accepting booleans/floats, or changing string/remote identities; verify schema and actual FastMCP argument parsing, then repeat native peer coordination.

- [x] T065 [US1] Prevent stale ready frames during receipt preparation/paste/CAS from starting verification; distinguish Codex commentary redraws from current final evidence while preserving bounded missing-receipt reconciliation. Prove failed transport leaves authentic late evidence observable and does not authorize replay.

- [x] T066 [US1] Correct shared soft-enforcement instructions that mistake CAO permission categories for literal provider-native tools; preserve exact grants, deny-all and MCP scopes across all consuming providers. Reproduce native Codex false missing-tool refusal and rerun native acceptance without operator corrective messages.

- [x] T067 [US1] Preserve Claude's genuine current final receipt when optional post-turn rating chrome uses the assistant bullet, and prevent tool/commentary or stale frames from consuming verification budget. Use authentic captured fixture, preserve genuine survey-shaped answers and repeat native late-receipt/restart acceptance including supervisor message draining.

## Phase 10: Convergence — remaining native providers

- [x] T068 [US1] Recognize Copilot 1.0.91's observed sidebar footer at the owning parser without treating loading or processing as ready; reproduce startup failure, add red/green regressions and prove two distinct native CAO turns on the existing demo, per FR-015/FR-020/SC-006 (partial).
- [x] T069 [verify] Verify official Linux installations for Copilot and the nine previously absent providers; preserve Cursor's agent identity when Grok installs its alias, correct Kiro installation documentation, and record native authentication prerequisites separately from successful model execution, per FR-020/SC-006 (partial).
- [x] T070 [verify] Run applicable provider/contract/composition checks, review the final diff, refresh structural evidence as required, and remove owned test sessions and temporary credential copies, per FR-020/SC-006 (partial).
