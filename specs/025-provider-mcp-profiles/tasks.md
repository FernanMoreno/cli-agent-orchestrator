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
- [x] T022 Design and implement strict per-agent native policy propagation with artifact binding and partial-install failure protection; preserve legacy installs and refuse unsupported v1 policy launches. T007 remains open until this succeeds.

- [x] T023 Correct the reproduced install CLI success-exit-on-service-failure boundary in cli/commands/install.py; strengthen test/cli/commands/test_install.py and rerun full applicable install checks.

- [x] T024 Reject reserved policy env markers during native installation and add CLI coverage; document URL/--env staging and the pending preflight-call activation under T022.

- [x] T025 Reproduce/fix the native catalog readiness→private service-auth publication race in test/cli/commands/test_install_opencode.py, with RED/GREEN bounded fake-clock tests in test/scripts/test_opencode_catalog_auth_wait.py. Preserve 15/45-second bounds and the failed receipt; perform one corrected pinned native check.

## Publication verification across supported Python versions

- [x] T026 Correct the reproduced Python 3.13 inherited Path.home descriptor assumption in test/scripts/test_interaction_runtime_environment.py; prove restoration of both the descriptor and its original owning-class behavior on Python 3.12 and 3.13. Keep HOME/CODEX_HOME privacy assertions intact. Recheck with all declared optional test features installed; distinguish the publication hook from global scalability acceptance. See [publication verification](validation-publication.md): 3/3 tests on each Python, 65/65 corrected-environment regressions, independent Luna/ECC review. The complete publication hook and global scalability gates remain separate.

## T007 continuation: explicit-policy launch boundary

- [x] T027 Reproduce missing profile-grant validation without interaction opt-in in the real v2 initialization path; cover both native layouts, complete/absent/partial grants, disabled children and multiple children. Implement validation of each effective active profiled child before backend launch, retaining cleanup and legacy unprofiled/no-opt-in behavior. Run scoped contracts, architecture/composition and independent review. T007/T022 remain open; this does not implement native policy publication or establish real-provider acceptance.

T027 evidence: [final verification](validation-t027.md) records RED 6/8, the separately reproduced text-command gap, final 279 passes including 20 new cases, five architecture contracts, 403-file CI mypy, style and independent Luna approval. This closes only T027; T007/T022 and global/live acceptance remain open.

## T022 implementation sequence

- [x] T028 Add RED native installation/launch regressions, then implement the bounded envelope parser, renderer and content/configuration binding. Cover malformed/unknown/pending values and preserved native payload semantics.
- [x] T029 Integrate frozen selected-agent input, managed-agent catalog isolation, private profile injection and managed-v1 refusal; prove grant/cleanup/nonmutation and TOCTOU boundaries.
- [x] T030 Integrate atomic pending/ready native installation and managed-to-legacy recovery; retain direct translation/reserved-marker guards and normal shared command persistence. Run installation/provider/transport/refresh regressions, typing/style, architecture/composition and independent review. Close T022 only on integrated code and evidence; leave T007 and live/global gates open as applicable.

T022/T028–T030 evidence: [native policy verification](validation-t022.md), with 527 passing contracts including 72 new cases, preserved RED diagnostics, 404-file typing, five architecture contracts, style and completed independent Claude design/product/correction reviews. T007 and live/global acceptance remain open.

## T006 observation continuation

- [x] T031 Reproduce missing receipt-probe/recovery observations; add factual counters and verified-delivery flag through completion timeout, HTTP and closed matrix evidence, preserving legacy diagnostic shapes and all deadlines/settling semantics. Run regression/composition checks and independent review, then record a serial source-bound Claude diagnostic if resource/auth preconditions hold. This advances T006 without presuming a historical cause or full matrix acceptance.

T031 evidence: [timeout observations and real diagnostic](validation-t031.md): RED 11 failures/24 passes, corrected 214 passing regressions, 404-file typing, composition 5/0 and completed read-only Claude review. Corrected real Unicode probe: HTTP 200 in 21.909 seconds, verified marker/native receipt and confirmed cleanup. The first private verifier failure is preserved. T006 remains open because the historical 504 was not reproduced.

## T007 Claude explicit filters

- [ ] T032 Reproduce ineffective propagation of Claude profiled `enabled_tools`; implement a protected server-side immutable filter with strict startup parsing and empty/partial semantics, leaving legacy unprofiled behavior unchanged. Prove actual builder-to-MCP catalog/call boundaries, malformed/forged input refusal and instance isolation; run provider/native/install regressions, architecture/composition, typing/style and independent review. Keep full T007 and live acceptance open.

## T007 native routing guard

- [x] T033 Reproduce `native_agent` silently bypassing explicit CAO MCP policy; reject that unsupported combination before command/artifact/backend emission, including mapped and Pydantic entries and invalid/null selections. Preserve legacy native/full-CAO routing and grants. Run the new scoped guard and existing provider unit checks, typing/style/composition and Codex-only diff review. Keep T032's deferred expanded validation and full T007/live gates open.

T033 evidence: [native routing policy refusal](validation-t033.md): RED 30 failures/6 compatibility passes, 243 passing tests including 36 new cases and two changed unit cases rechecked after the mock-isolation review, 404-file typing, composition 5/0, style and Codex-only review. T032 remains explicitly deferred and full T007/live/global gates remain open.

## T007 native installation explicit filter guard

- [x] T034 Reproduce OpenCode native installation silently dropping profiled `enabled_tools`; refuse unsupported non-null filters before publication. Cover pure preparation, CLI failure status, fresh and already managed artifact preservation, input nonmutation and null/absent/legacy compatibility. Verify scoped serial configuration contracts, typing/style/composition and Codex-only review. Actual native filter support, T007, T032's deferred validation and live/global gates remain open.

T034 evidence: [native installation filter refusal](validation-t034.md): RED 24 failures/28 controls, final 52 passes including 29 new cases, 404-file typing, composition 5/0, style and Codex-only composition review. Native filter propagation, full T007 and the deferred T032/live/global gates remain open.

## Complete OpenCode backlog continuation

- [x] T035 Implement bound version-two native explicit filters, retaining version-one compatibility and malformed-input refusal; propagate private policy and exact preflight/server enforcement. Verify installation, repair, legacy-marker/interaction channel and instance isolation contracts.
- [x] T036 Complete OC-01–04/OC-AUTH/OC-LOCAL/OC-CATALOG: audit every registered operation and its authority, define separate coordination/supervision/knowledge/elastic profiles and test both surface and operation gates.
- [ ] T037 Complete OC1-01–05 with a privately pinned v1 binary, effective agent/model, isolation and actual Unicode/error/cancel/interaction/message/restart scenarios; retain unsupported-feature refusals until implemented.
- [ ] T038 Complete OC2-01–06 and OC-05–07 with refreshed source-bound runners, expanded real catalogs/calls, separate generic/Work message authority, authentication modes and concurrency/control metrics. Preserve historical RECONCILE_REQUIRED and distinguish unproven historical causes.

Execute T035 → T036 → T037/T038 serially in main. These refine T007–T014 rather than claiming completion. User now requests the entire OpenCode list; no blanket cancellation of the deferred Claude T032 validation or other restrictions is inferred.

T035 evidence: [native filter propagation](validation-t035.md): RED 28 failures/126 controls, final 193 passes across seven scoped modules, exact version-one byte/binding compatibility in 12 cases, 404-file typing, composition 5/0 and Codex-only review. T034 valid-list refusal is superseded by bound propagation; malformed refusal remains. Full OpenCode list/T007/live/global acceptance remains in progress.

T036 evidence: [expanded catalog and authority](validation-t036.md): seven immutable profiles classify all 79 registered operations; 181 passing contracts, eight supplemental real-handler local refusal probes, 404-file typing, composition 5/0 and Codex-only review. Only offline/configuration/catalog/authority items are closed; OpenCode real v1/v2/message/concurrency/auth acceptance remains T037/T038.

## T037 substeps: real v1 preparation and isolation

- [x] T039 Pin official OpenCode v1.18.35 release/archive digest, isolated binary SHA/version and upstream source commit without global installation or credential access. This closes OC1-01 only; see [preparation evidence](validation-t037.md).
- [ ] T040 Implement/test private v1 configuration, data/auth/cache/state/temp and overlay isolation before any model run. Preserve native v1 permission syntax, frozen/bound managed profiles and explicit interaction preflight; verify actual pinned config/agent/model/catalog without auth before live gates.

T037/OC1-02–05 remain open after binary preparation. T040 precedes real v1 use because the previous legacy launch pointed to shared operator config/data. The private legacy foundation is implemented with offline contracts, including two simultaneous mocked agents and independent cleanup; managed-v1 propagation and pinned real config/catalog acceptance still keep T040 open. See [partial implementation evidence](validation-t040.md). At the T040/T041 checkpoint all real OpenCode validation was explicitly deferred, regardless of memory. The later explicit resumption below supersedes that OpenCode hold; T032 remains separately deferred.

- [x] T041 Reproduce and correct OpenCode native profile validation after allocation: read/validate and freeze the native agent before new terminal/child/backend/state allocation; transfer the snapshot through the manager and preserve current binding/version/permission gates, idempotent reuse, direct initialization and other providers. Verify eager/deferred root/child failures, frozen payload/identity, policy binding and applicable offline regressions, type/style/composition and source review. OC1-02 model/runtime real acceptance and T037/T040 remain open.

T041 evidence: [pre-allocation native profile gate](validation-t041.md): 23 RED failures/3 controls; 296 OpenCode and 70 shared lifecycle/manager passing tests, 405-file typing and six-file style checks. This closes the bounded offline gate only; OC1-02 real effective agent/model and T037/T040 remain open. Ordinary regressions reuse verified private PostgreSQL schemas; no real OpenCode journey or deferred T032 suite was run.

## Real OpenCode validation resumed by explicit user instruction

The user explicitly authorized resuming real OpenCode tests after a fresh memory measurement exceeded 6,144 MiB. This supersedes the earlier OpenCode-only deferral prospectively; historical receipts remain unchanged. Serialize real runs, measure memory before each, verify current source/runner/bin bindings, reuse the verified existing PostgreSQL service, preserve operator credentials and historical jobs. T032 remains deferred; Codex only.

- [ ] T042 Validate the pinned v1.18.35 native configuration/selected agent/model and MCP grammar in owned private state before authenticating or running a model. Correct reproduced defects with RED/GREEN evidence, preserving managed-v1 refusal until the actual supported route is established. Then execute only source-bound, isolated v1 scenarios supported by the corrected route.
- [ ] T043 Revalidate current-source OpenCode v2 through the production terminal path with questions/answers/ACK, AG-UI identity and API restart, complete cleanup and operator-state nonmutation. Inspect and bind the actual runner/source first; do not equate this restricted journey with all expanded profiles, external cao.work.message or concurrency/auth acceptance.

- [ ] T044 Correct the reproduced real-journey isolated writer adapter TypeError for selected_agent_source at both forwarding boundaries. Verify RED/GREEN snapshot and serializer contracts, preserve isolation/deadlines/criteria, then repin and retry the real v2 journey. This is a harness correction under T043, not acceptance of expanded profiles or external message contracts.
