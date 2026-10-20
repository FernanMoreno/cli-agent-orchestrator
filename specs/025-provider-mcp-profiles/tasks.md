# Tasks: provider MCP profiles and acceptance

## Phase 1 — scope and evidence

- [x] T001 Read current instructions, provider todos, accumulated handoff, source and recent Spec 022 evidence; preserve main changes.
- [x] T002 Write spec.md and plan.md, then this dependency-ordered task list; record Graphify coverage before implementation.

## Phase 2 — US2 server policy foundation

- [x] T003 Add failing profile transport and protected overlay tests in test/mcp_server/test_tool_profiles.py and test/providers/test_mcp_tool_profiles.py.
- [x] T004 Implement immutable interaction/collaboration policy in src/cli_agent_orchestrator/mcp_server/tool_profiles.py; integrate with restrictive middleware, server startup and providers/mcp_interactions.py.
- [x] T005 Run focused/new and existing configuration/transport contracts, catalog audit, composition gate, final diff and independent Claude review; update all three todos with actual evidence.

## Phase 3 — current failure diagnosis (US1)

- [ ] T006 Diagnose Claude's first incorrect timeout boundary with sanitized timing/status evidence; reproduce a demonstrated defect before correction. Maps CL-01–04. Preserve historical records; no auth mutation.

## Phase 4 — profile propagation and extended authorization (US2/US3)

- [ ] T007 Complete client grants, native profile propagation, enabled_tools intersection and per-profile OpenCode v2 preflight, including empty/partial filters, names/collisions and private overrides. Maps OC-01/02/04, OC2-02/03, CX-01–04, CL-05–08 and AUTH in all lists. The foundation alone does not close these full items.
- [ ] T008 Audit the full server catalog and operation authority; implement separately reviewed coordination, supervision, knowledge and elastic-worker profiles and negative authority tests. Maps OC-03/LOCAL/CATALOG, CX-LOCAL/CATALOG, CL-LOCAL/CATALOG and corresponding profile tasks. Retain local assign project/identity gate and elastic-worker-only complete_assignment.

## Phase 5 — source-bound acceptance and scale (US4)

- [ ] T009 Prepare isolated pinned OpenCode v1 binary and validate all OC1-01–05 scenarios; do not infer live v1 acceptance from offline tests.
- [ ] T010 Refresh external source/runner launch bindings and prove real expanded-profile OpenCode v2 offers/calls/auth isolation. Maps OC2-01/04–06; historical timeout reconciliation remains separate.
- [ ] T011 Execute distinct generic and Work message contracts against verified PostgreSQL with AG-UI/restart, current source and cleanup. Maps OC-05, CX-05, CL message tasks; Spec 023/024 acceptance stays separate.
- [ ] T012 Execute source-bound questions/answers/ACK and interrupted-wait recovery, Unicode/error/cancellation/provider matrix scenarios. Maps CX-06–08 and remaining Claude/OpenCode live tasks, Spec 022 gates.
- [ ] T013 Verify multi-terminal bounds, ordering, isolation and own cleanup; measure control latency separately from model time. Maps OC-06, CX-09 and Claude scale tasks.
- [ ] T014 Consolidate evidence, applicable checks and Spec 022/023/024 updates; preserve earlier failures and remaining gates. Maps OC-07, CX-10 and Claude completion tasks.

## Dependencies and execution

T001 → T002 → T003 → T004 → T005. Diagnose T006 independently in read mode; prioritize demonstrated corrections before expanded live runs. T007 → T008 → T009/T010 → T011/T012 → T013 → T014. All edits and intensive real runs are serial. No parallel implementation is scheduled in this shared checkout.

## CL-01 diagnostic increment

- [x] T015 Add failing factual completion-timeout and API error-mapping regressions in test/services/test_step_wait_diagnostics.py.
- [x] T016 Add closed observations to services/agent_step.py and api/main.py without changing deadlines, settling or precedence; test Unicode privacy, quota/error and legacy error compatibility.
- [x] T017 Run scoped regression/composition checks and peer review; retain CL-01/02/03 open until a source-bound real probe establishes its actual boundary.

Exact item-by-item traceability: [numbered backlog map](backlog-map.md).

## T007 execution: client/preflight boundary

- [x] T018 Add failing effective-profile v2 preflight and real Codex command/TOML tests in test/providers/test_mcp_profile_preflight.py.
- [x] T019 Require effective profile tools in utils/opencode_v2.py; intersect Codex enabled_tools in providers/codex.py without changing legacy behavior.
- [x] T020 Run current focused configuration/transport contracts, architecture/composition, exact diff and independent peer review; record remaining installer/per-agent propagation work under T007.

T018 → T019 → T020 is serial and implements only the identified client/preflight boundary of T007.

- [x] T021 Reproduce native install/translation dropping explicit policy; reject unsupported metadata before artifact writes and test existing artifact preservation in test/cli/commands/test_install_opencode.py.
- [ ] T022 Design and implement strict per-agent native policy propagation with artifact binding and partial-install failure protection; preserve legacy installs and refuse unsupported v1 policy launches. T007 remains open until this succeeds.

- [x] T023 Correct the reproduced install CLI success-exit-on-service-failure boundary in cli/commands/install.py; strengthen test/cli/commands/test_install.py and rerun full applicable install checks.

- [x] T024 Reject reserved policy env markers during native installation and add CLI coverage; document URL/--env staging and the pending preflight-call activation under T022.

- [x] T025 Reproduce/fix the native catalog readiness→private service-auth publication race in test/cli/commands/test_install_opencode.py, with RED/GREEN bounded fake-clock tests in test/scripts/test_opencode_catalog_auth_wait.py. Preserve 15/45-second bounds and the failed receipt; perform one corrected pinned native check.

## Publication verification across supported Python versions

- [x] T026 Correct the reproduced Python 3.13 inherited Path.home descriptor assumption in test/scripts/test_interaction_runtime_environment.py; prove restoration of both the descriptor and its original owning-class behavior on Python 3.12 and 3.13. Keep HOME/CODEX_HOME privacy assertions intact. Recheck with all declared optional test features installed; distinguish the publication hook from global scalability acceptance. See [publication verification](validation-publication.md): 3/3 tests on each Python, 65/65 corrected-environment regressions, independent Luna/ECC review. The complete publication hook and global scalability gates remain separate.
