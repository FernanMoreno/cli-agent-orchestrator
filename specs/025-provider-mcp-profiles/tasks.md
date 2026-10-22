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

- [x] T032 Reproduce ineffective propagation of Claude profiled `enabled_tools`; implement a protected server-side immutable filter with strict startup parsing and empty/partial semantics, leaving legacy unprofiled behavior unchanged. Prove actual builder-to-MCP catalog/call boundaries, malformed/forged input refusal and instance isolation; run provider/native/install regressions, architecture/composition, typing/style and independent review. Keep full T007 and live acceptance open.

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
- [x] T040 Implement/test private v1 configuration, data/auth/cache/state/temp and overlay isolation before any model run. Preserve native v1 permission syntax, frozen/bound managed profiles and explicit interaction preflight; verify actual pinned config/agent/model/catalog without auth before live gates.

T037/OC1-02–05 remain open after binary preparation. T040 precedes real v1 use because the previous legacy launch pointed to shared operator config/data. The private legacy foundation is implemented with offline contracts, including two simultaneous mocked agents and independent cleanup; managed-v1 propagation and pinned real config/catalog acceptance still keep T040 open. See [partial implementation evidence](validation-t040.md). At the T040/T041 checkpoint all real OpenCode validation was explicitly deferred, regardless of memory. The later explicit resumption below supersedes that OpenCode hold; T032 remains separately deferred.

- [x] T041 Reproduce and correct OpenCode native profile validation after allocation: read/validate and freeze the native agent before new terminal/child/backend/state allocation; transfer the snapshot through the manager and preserve current binding/version/permission gates, idempotent reuse, direct initialization and other providers. Verify eager/deferred root/child failures, frozen payload/identity, policy binding and applicable offline regressions, type/style/composition and source review. OC1-02 model/runtime real acceptance and T037/T040 remain open.

T041 evidence: [pre-allocation native profile gate](validation-t041.md): 23 RED failures/3 controls; 296 OpenCode and 70 shared lifecycle/manager passing tests, 405-file typing and six-file style checks. This closes the bounded offline gate only; OC1-02 real effective agent/model and T037/T040 remain open. Ordinary regressions reuse verified private PostgreSQL schemas; no real OpenCode journey or deferred T032 suite was run.

## Real OpenCode validation resumed by explicit user instruction

The user explicitly authorized resuming real OpenCode tests after a fresh memory measurement exceeded 6,144 MiB. This supersedes the earlier OpenCode-only deferral prospectively; historical receipts remain unchanged. Serialize real runs, measure memory before each, verify current source/runner/bin bindings, reuse the verified existing PostgreSQL service, preserve operator credentials and historical jobs. T032 remains deferred; Codex only.

- [x] T042 Validate the pinned v1.18.35 native configuration/selected agent/model and MCP grammar in owned private state before authenticating or running a model. Correct reproduced defects with RED/GREEN evidence, preserving managed-v1 refusal until the actual supported route is established. Then execute only source-bound, isolated v1 scenarios supported by the corrected route.
- [x] T043 Revalidate current-source OpenCode v2 through the production terminal path with questions/answers/ACK, AG-UI identity and API restart, complete cleanup and operator-state nonmutation. Inspect and bind the actual runner/source first; do not equate this restricted journey with all expanded profiles, external cao.work.message or concurrency/auth acceptance.

- [x] T044 Correct the reproduced real-journey isolated writer adapter TypeError for selected_agent_source at both forwarding boundaries. Verify RED/GREEN snapshot and serializer contracts, preserve isolation/deadlines/criteria, then repin and retry the real v2 journey. This is a harness correction under T043, not acceptance of expanded profiles or external message contracts.

- [x] T045 Correct the R2 real agent_tool_scope_mismatch at native snapshot serialization by preserving action/resource order, without weakening the strict predicate. Prove real loader/serializer and wildcard deny behavior, regressions and unchanged gates; commit/rebind before a real retry.
- [x] T046 Audit ordered permission semantics in native install/render and envelope binding canonicalization. Current v1/v2 bindings sort mapping keys; assess semantic-order coverage and version-compatible migration before claiming all ordered-policy integrity. Do not rewrite old binding formats silently or infer this audit complete from T045.

T044 correction was committed as 0870a25e and its repinned real R2 reached native initialization, MCP readiness and an actual ask_user call without the prior TypeError. The later strict scope failure is the distinct production snapshot-order defect tracked in T045; T043 remains open.

T043/T045 closed for the bounded restricted v2 journey: after snapshot-order fix 398bae68, real R3 passed the unchanged closed questions/answers/ACK/AG-UI/API-restart/cleanup/auth predicate with source and binary unchanged. The earlier R3 admission was blocked by memory and started no native/model run; a new qualifying preflight admitted the actual run. T042/v1 managed and model journeys, expanded-profile operation acceptance, external messages, concurrency/auth and confirmed T046 remain open.

T046 completed for ordered render/binding integrity with explicit envelope v3 migration, exact legacy golden compatibility, fail-closed ambiguous legacy gates, 35 new cases and 642 combined passes; typing405/style3/composition5/0. See [evidence and remaining real gates](validation-t046.md). This closes neither managed CLI v1 nor real expanded-profile acceptance.

Post-integration T046 restricted real v2 acceptance passed on dab6d4fd with all 498 runtime/live source files and binary unchanged: 2 questions/answers/ACK, one API restart, stable AG-UI, exact catalog/offers, timely COMPLETED, full cleanup and auth unchanged. This does not close actual managed-v3 or expanded-profile operation acceptance.

## Explicit resumption of postponed validation

The user's instruction to finish postponed work resumes T032's expanded and Python MCP stdio validation. The Codex-only restriction remains: do not invoke Claude Code, its model or login. Original refusals/receipts remain unchanged.

- [x] T047 Complete the resumed T032 regression gate: diagnose the reproduced stale list-command test after T035 switched native filters to protected child transport; assert exact partial/empty child filters, independent capability paths and input nonmutation. Run the explicit current-source 16-module selection including both real Python stdio children, applicable composition/type/style and Codex review. This is server transport validation, not authenticated Claude acceptance or completion of T007.

- [x] T048 Correct the real journey's closed MCP child environment rejecting the two server-private policy variables, proven by source inspection after T046. Add actual writer boundary RED/GREEN contracts for empty/partial/absent filters, input nonmutation and unknown-env refusal. Preserve strict isolation/criteria; commit/rebind and run the real restricted journey using a privately prepared managed-v3 artifact. Do not infer actual production installation or expanded-profile operation acceptance.

T032/T047 verified on the new explicit 16-module selection: 605 passing tests, both actual Python stdio children included; original failed run preserved (604 pass/one stale expectation). Canonical typing405, style for the changed test and composition5/0; Codex source/diff review. Authenticated vendor and full T007 gates remain open.

T048 evidence: [managed isolated child](validation-t048.md). Six RED failures/two refusal controls; 285 combined passes, typing405, style2, architecture5/0 and reviewed bounded AST refresh. First real managed run loaded/profiled/offered exact tools but timed out before a question; one identical-semantic repeat passed the unchanged full ACK/restart/cleanup/auth predicate plus actual managed-v3 provider/child observations on f6e8be15. Original failed run and administrative annotation amendment retained. No production-installer/expanded/v1/global claim.

## User-ordered remaining groups

Execute v1 → v2 expanded/installer → multi-agent messages/scaling → authentication/launchers → timeout diagnosis → global gates → Codex final matrix. Claude Code remains prohibited; historical job/QEMU restrictions persist. Existing holds do not supersede the latest instruction, but memory admission and evidence requirements remain unchanged.

- [x] T049 Add validated-v3 managed native-v1 noninteraction launch, protected child filters and native-grant preflight; retain legacy/refusal and unvalidated v1 interaction guards. Prove seven profiles and negative/isolation contracts, actual pinned native grammar/MCP, typing/style/architecture/composition and current-source review. Advances T040/T042, not full v1 acceptance.
- [ ] T050 Complete pinned v1 real readiness, explicit questions/answers/ACK, model/Unicode/receipts/errors/timeout/cancel/cleanup and restart/message behavior with source-bound isolated runs. Close T037/T040/T042 and OC1 only on their complete criteria; then proceed to the second requested group.

T049 evidence: [native v1 managed preparation](validation-t049.md). 35 initial RED, seven review failures/36 controls, 43 final unit and 441 combined passes; typing405/style3/architecture5/0. The official pinned v1.18.35 resolved the exact agent/model/Unicode and connected its real MCP child with the exact filtered catalog and observer shutdown, no auth/model/PG. T049 bounded preparation closed; v1 full T040/T042/T050 remains first in the requested order.

- [x] T051 Correct reproduced native-v1 stale auth selection before its real terminal journey. Preserve inline/legacy compatibility, selected model precedence, stable private vendor snapshot and exact active-key export; refuse unknown/malformed/ambiguous/OAuth migration and keep operator state intact. Run RED/GREEN source/isolation/vendor-format contracts plus applicable regressions, type/style/composition, then production native-v1 diagnostics. Part of the first requested group, not completion of the later full authentication matrix.

T051 implementation evidence: [active credential selection](validation-t051.md). 28 final contracts and 475 combined passes, typing405/style3/architecture5/0; actual pinned vendor export and native empty-store compatibility passed. Production terminal diagnostic remains required before closing T051.

T051 production diagnostic passed on304dfdc6: actual API/tmux/provider ready, Unicode model response, LAST active-receipt gate and completed state, complete private cleanup/operator preservation. Bounded criterion closed; full T050 remains open.

- [x] T052 Reuse the existing launch-scoped MCP client-discovery signal for native v1. Protect signal paths/tokens from profile overrides, require successful discovery before initialization, preserve native grants/server filters and retain the interaction guard until actual pinned discovery is proven. Cover stale/forged/missing signals, timeout/cancellation, disabled/foreign children and cleanup; then run a source-bound actual managed native-v1 terminal/catalog diagnostic. Part of ordered group1.

T052 actual managed-v3 terminal/catalog discovery passed oncc8edda8 with complete process/resource cleanup. Observer graceful completion was unobserved; no model/interaction or full OC1 acceptance. See [evidence](validation-t052.md).

- [x] T053 Implement explicit native-v1 interaction only for a validated managed-v3 profile: protected capability overlay, all-three native grant/filter preflight, successful client discovery before channel advertisement, failure/cancellation cleanup. Preserve legacy refusal and operation authority. Verify offline RED/GREEN and applicable regressions, then actual source-bound Unicode questions/answers/ACK, graceful observer cleanup and API restart; do not infer message or global acceptance.

T053: [implementation and actual acceptance evidence](validation-t053.md). 13 final contracts/503 expanded passes; discovered and corrected no-profile-metadata grant bypass. Actual pinned native-v1 interaction/restart passed on f23b1d5f: two questions/answers/ACK, Unicode/LAST/completed, stable PostgreSQL/AG-UI/native identities, exact three-tool observer and graceful/full cleanup. Original blocked/failed diagnostics retained; broader OC1 gates remain open.

- [x] T054 Complete native-v1 actual provider-error, timeout and cancellation diagnostics within T050, using owned isolated state and unchanged memory admission. Start with explicit empty auth/error classification without staging operator credentials; reproduce before changing detectors. T053 interaction/restart is accepted separately; preserve all failed/blocked receipts.

- [x] T055 Prevent reproduced native-v1 silent model fallback. T054's explicit empty-auth diagnostic launched the requested Go model but displayed Big Pickle/OpenCode and a free-tier refusal; LAST stayed unavailable. Confirm actual native `models provider` distinguishes isolated availability with/without the selected key, then add an asynchronous bounded prelaunch inventory check sharing native launch configuration/environment. Preserve auth/model precedence, legacy defaults, cancellation/cleanup, operation authority and existing gates. Verify RED/GREEN and actual no-task refusal plus authenticated control; do not claim wire-model or complete authentication acceptance.

T054 observed actual empty-auth model fallback rather than the expected Go error; LAST stayed unavailable. [Diagnostic limits](validation-t054.md). T055 candidate adds exact native availability preflight: 24 final contracts and527 expanded passes; [evidence](validation-t055.md). Postcommit real negative/control remain pending.

T055 real negative and authenticated control passed on4f032e95: empty auth returns400 before a task with no failed-session/provider-state residue; selected-model UI, Unicode, LAST receipt and completed state pass with active auth. Both fully cleaned/operator-preserved. Bounded gate closed; T054/T053/full OC1 remain open.

- [x] T056 Correct the reproduced current native-v1 Go invalid-credential classification within T054. Require observed native idle body/toast or exact permanent-credential retry footer with current version/model chrome; preserve quoted/stale/completed/transient-retry/version controls and v2 behavior. RED/GREEN both raw/screen paths, applicable regression/composition, then source-bound actual invalid-key refusal and authenticated control. Preserve receipt authority, existing budgets/limits, operator state and failed evidence. Do not infer timeout/cancellation or full OC1 acceptance.

T056 candidate: [evidence](validation-t056.md). Actual invalid-key presentation reproduced;35 final classifier contracts and565 expanded passes, typing405/style/composition5/0. Committed-source real invalid-key/control remain pending.

T056 bounded gate closed: [final evidence](validation-t056.md).61 classifier cases/591 expanded passes, typing405/style/composition5/0; actual invalid-key ERROR/LAST refusal and authenticated native/API exact Unicode/receipt/completed control passed on ce41f190. Earlier control punctuation failure retained with cause unproved. T054 timeout/cancellation and full OC1 remain open.

- [x] T057 Complete owned-generation native-v1 cancellation within T054, checking old execution termination, new readiness, wrong/repeated generation fences, cancelled receipt and exact private-state cleanup before outer fixture teardown. Reproduce and correct any confirmed reinitialization ownership leak with v1/v2 RED/GREEN and applicable composition/regression, then actual committed-source cancellation. Preserve operator/sibling state, capability/receipt authority and existing limits. Actual timeout and broader OC1 remain separate.

T057 bounded gate closed on a97bf19f:616 offline passes and actual owned-generation recovery, stale/repeated409, old-process termination, new selected-model readiness, one/zero private roots before outer teardown and complete cleanup. [Evidence and retained failed diagnostics](validation-t057.md). Genuine in-flight cancellation and active timeout remain T054.

- [x] T058 Finish T054 with a source-bound actual native-v1 active-timeout/in-flight-cancellation diagnostic: selected authenticated model executes only the explicitly granted private `sleep 120` shell operation; reuse the terminal through production run-step with unchanged75-second diagnostic budget and no prompt redelivery. Observe the actual owned sleep descendant, timeout504/closed diagnostics/no final receipt, and cancel the same still-running generation with stale/repeated fences, old descendant termination, new native readiness and exact private-state cleanup. Required human waits intentionally pause the active deadline and are not timeout fixtures. Preserve operator state, existing6144MiB floor, failed evidence and historical jobs. This controlled expected timeout does not establish the cause of a historical timeout.

T058 actual active75-second timeout and in-flight cancellation passed on5d6262d0:504/processing/no redelivery/no receipt, observed owned sleep alive at timeout, stale/repeated409, same-generation cancelled200, sleep/old-native termination, replacement model/readiness and one/zero roots with full cleanup. [Evidence](validation-t058.md). T054 bounded error/timeout/cancel gate is closed with T055–T058. Reconciliation of older task text: T040 private v1 foundation and T042 pinned grammar/catalog prerequisites are now satisfied by T049/T051/T052/T053/T057/T058; retain their earlier partial/blocked statements as historical evidence. T037/T050/full OC1 still require expanded profiles/messages/production installation.

- [x] T059 Execute the production cao install CLI in private owned HOME for each of the seven explicit MCP profiles, validate ready-v3 binding/nonmutation and exact native permissions, then source-bound pinned native-v1 debug agent and actual MCP discovery with an exact per-profile content-free catalog observer. Keep each profile's expected set explicit; never broaden the original restricted journey predicate. No model/operation claim follows from discovery. Advance first ordered group's installer/profile prerequisites; retain message/operation and full-provider gates.

T059 bounded production installer/native catalog gate passed on936f7de6 for seven profiles/21 actual commands, exact catalogs3/8/12/11/10/12/9, current ready-v3 bindings/grants/Unicode identities, complete observers/no calls/no owned processes/operator preservation. Earlier source-name and ANSI diagnostic failures retained. [Evidence](validation-t059.md). This does not close model offer/handler operations/messages or full OC1.

- [ ] T060 Complete actual managed-v1 collaboration with production-installed sender/receiver profiles, two owned native terminals and exact partial server filters, source-bound model calls and PostgreSQL inbox evidence, redacted AG-UI notifications, API restart preserving native/process/message identities, postrestart message and full credential/process/root cleanup. Explicit discovery permission is separate from MCP selector. Keep external cao.work.message and broader authority/scaling gates separate.

- [x] T061 Correct T060's reproduced managed nonhuman-profile creation refusal: separate coarse @cao-mcp-server permission from human-channel intent only for validated nonhuman managed surfaces; suppress human source issuance/advertisement there, preserving explicit runtime capability and any human-tool/full-protocol gates. Source/Graphify → RED/GREEN v1/v2/nonhuman/empty/partial-human/explicit-capability controls → expanded regression/type/style/composition → commit/source-bound actual collaboration recheck. Original T060 failures remain retained.

T060 failed real diagnostics remain [recorded](validation-t060.md). T061 [candidate evidence](validation-t061.md): actual nonhuman creation refusal reproduced,46 final contracts/662 expanded passes, typing405/style2/composition5/0, independent read-only Codex Luna/Max ECC review corrected direct reload and confirmed final scope. Actual T060-r4 on42bdbe7d accepted T061 bounded nonhuman-profile behavior; full T060 remains failed by the separate T062 ownership leak. T060/full OC1/global gates stay open.

T061 bounded functional recheck passed on42bdbe7d via actual T060-r4; full diagnostic success=false retained because two roots survived postrestart session deletion. [Evidence](validation-t060.md). T060/full OC1 remain open.

- [ ] T062 Correct actual API-restart OpenCode private-root ownership loss. Preserve random per-launch private paths while recording bounded terminal/dialect/root identity under a private ownership receipt before staging credentials. Exact receipt lookup supports reconstructed/cached and uncached manager cleanup; malformed/foreign/symlink/changed/I/O-failed ownership must refuse and retain retryable registry evidence, never scan/adopt arbitrary legacy roots. Prove v1/v2 restart/reset/failure/cancel/authority/isolation RED/GREEN and applicable composition/regression, then repeat the unchanged actual two-agent PG/AG-UI/API-restart/graceful/root-cleanup predicate on committed source.

- [x] T063 Correct provider-initialized mixed managed profile truncation at the capability overlay. Preserve exact explicit profile/subset across both dialects and prepared/direct launches, runtime capability/native grants/readiness, legacy three-tool and interaction-only controls. Spec/Graphify/source → RED/GREEN → applicable overlay/server/provider/lifecycle regressions → review/composition/integration. Real full-profile model operations remain separately required; retained T060-r5 upstream failure and T062 full two-message postrestart acceptance stay open.

T063 bounded overlay/catalog/restart gate passed on9cc35033:893 expanded tests, typing406/style5/composition5/0 and independent Codex/ECC review; all fourteen actual native profile/dialect cases passed production installation, exact catalogs, API capability, restart preserving process/metadata, graceful observer sealing and immediate zero roots/capabilities before outer teardown. Minimum stage admission6703MiB, unchanged6144MiB floor. No model tasks or handler operations were submitted. [Evidence](validation-t063.md), [content-free receipts](t063-native-catalog-receipts.json). Full T060/T062, model authorization/authentication and concurrent Work delivery remain separate.

- [x] T064 [US1] Correct registered API Work delivery serialization in src/cli_agent_orchestrator/api/main.py and the narrow Work admission/runtime boundary if needed: existing configured capacity must permit two live process-backed peers while one waits. Preserve authoritative transactional scheduling/grants/reservations/generation fences, full-result direct dispatch compatibility, bounded preparation/no-work polling, owned-task error handling and cancellation/shutdown/uncertain cleanup without resend or release. Spec FR048/SC032 → private Graphify/source → actual-source event-barrier RED → isolated implementation with meaningful Work/API lifecycle regressions → review/type/style/composition → integration and fresh owned real Docker Work message/API-replacement acceptance. Do not substitute two Uvicorn workers or dead receivers whose rows still say running. Maps T011/T013 and OC-05/06; no global or historical reconciliation closure.

- [x] T065 [US1] Correct the distinct default-executor starvation boundary for long admitted Work process/agent-step delivery. Spec FR049/SC033 → bounded private Graphify/source → locked-interpreter actual-source event-barrier RED/positive control → isolated ownership/context/cancellation-safe execution implementation without fixed arbitrary pool caps → configured applicable regression/type/style/composition/independent review → main integration and relevant real Work recheck. Keep SQL capacity/fairness/grants/generation/cleanup authority unchanged, at most one unadmitted preparation, no release/resend or port closure from cancellation while execution remains active. Maps T013/OC-06; does not close full scale/model/global NFR gates.

- [x] T066 [US1] Add explicit internal admission of an already planned Work replacement generation after T052's trusted stop/release/replan. Spec FR050/SC034 → private Graphify/source verification → RED migration/provenance/replay/CAS/authority tests → additive V4 immutable retry-origin relation and isolated replacement-admission service using verified Principal/current sealed provision+contract+delivery → transactional binding and scheduler enqueue without modifying `retry_work` or original launch idempotency → configured PostgreSQL integration and applicable admission/delivery/replacement regressions, type/style/architecture/composition and independent source review → fresh real two-live-worker receiver-replacement message/API-restart acceptance. No public endpoint, automatic retry admission, old-generation credential/delivery reuse, schema backfill, direct scheduler bypass, or claim of full OC1/global NFR closure.

- [x] T067 [US1] Correct the Work multi-job lock-order deadlocks found by the configured T066 regression run. Spec FR051/SC035, then Graphify/source verification, then deterministic event-barrier PostgreSQL RED for the public claim snapshot inversion and for admission retirement plus selected-job ordering. Fix: canonical ascending job-id lock order in `WorkScheduler.claim_next`, and suffix-guarded immediate retirement plus grouped ordered deferred-retirement/selected-job locking in `WorkAdmission._eligible`/`_commit_dispatch`. Then GREEN, the unchanged ten-process two-slot test, the affected scheduler/admission/release lock-order modules and style/type checks. Root then runs the configured regression, composition review and independent review. No capacity/timeout/retry/global-mutex change; no scale or live acceptance claim.

T064–T067 bounded completion, 2026-10-22:486 configured tests, type/style, composition5/0 and independent source review passed; the fresh external Work R4a acceptance passed on 2b3cb03c with two real workers, replacement generations, 2 messages/2 ACK, redacted AG-UI/restart/replay and owned cleanup. [Receipt](validation-t066-external-receipt.json). T011/T013/T037/T038 and OC-05/06 remain open for their additional native/generic/scale requirements.

- [ ] T068 [US1] Fix missing validated group/metadata forwarding in the additional-terminal API route (FR052/SC036), reproduced by current native-v2 R9b. Spec/plan/task → private Graphify/source → actual HTTP RED → narrow forwarding fix → HTTP/discovery/idempotency/authority regression, style/type, registered composition and Claude review → main publication → fresh source-bound native-v2 message/restart acceptance. Preserve absent/null defaults, explicit empty values, current scopes/grants/model/session identity, original timing/cleanup criteria and failed evidence. Maps T011/T038/OC-05; no Work or global gate reopening.

T060 current execution increment, 2026-10-22: R13 requalifies the current-source native-v1 collaboration journey after R12's resource-only abort. Preserve all prior failures and unchanged acceptance predicates, freshly pin main 82237cae and the 505 source/test files, obtain independent Codex read review and serialize the single real run against the exact existing PostgreSQL service with6144MiB stage admission. T060/T062 and the larger OC1 scope remain open until the complete predicate and owned cleanup pass; no global NFR rerun is part of this increment.

T060 R13 failed at the accepted-input→first-MCP boundary; no product defect is proved. Next diagnostic increment R14: pure privacy/deadline/state controls for a closed-enum viewport/detail observer, exact normalized preservation of inherited acceptance logic, Codex read review, then one source-bound serial real run if admission qualifies. No retries for green, raw screenshots/transcripts, classifier change or vendor-error claim from202 alone.

T060 R14 diagnostic executed once, 2026-10-22: first current-turn rendered endpoint/upstream marker at 7.643s (baseline absent), 12/14 captures positive, zero observed MCP calls/messages and no verified LAST by 75s. All cleanup/auth/source/helper/launcher checks passed; T060/T062 remain open. No HTTP/provider root cause or product correction is established. Preserve R13 and R14 independently; see [current R14 evidence](validation-t060.md#r14--timed-native-endpoint-error-before-any-observed-mcp-call).

- [ ] T069 [US1] Attribute the R14 native endpoint error at the actual Go transport boundary using the pinned native CLI with a content-free response observer; retain75s/6144MiB/model/auth/cleanup and no MCP/native tool authority. Review the source-bound diagnostic before one actual run. Correct only a demonstrated default/configuration defect with RED/GREEN contracts and applicable checks, preserving explicit settings and all prior failures. Public model-catalog absence and differing documentation routes alone do not establish a defect or permit changing the model.

T069 diagnostic increment, 2026-10-22: R15 legacy route and R16 explicit current route both observed native HTTP402, with complete recorded cleanup/preservation and unchanged limits.100 pure controls +six writer contracts passed; Claude READ approved both concrete runs. No endpoint/product fix is justified and no specific provider rejection cause is established. Keep T069 and full native gates open; avoid unchanged model replays. [Evidence and sanitized receipts](validation-t069.md).

T069 next increment: deep harness reassessment (actual request identity/auth/header booleans and bounded closed response-error categories; source/native migration comparison). Prior402-only runs do not establish why either request was rejected. Required output includes a causal diagnostic, a correction when demonstrated, and preserved prior evidence.

- [x] T070 [US1] Correct the standard live-journey CLI’s missing current-model catalog admission (FR053/SC037). RED deprecated-model CLI barrier → bounded no-auth public-catalog helper → preserved existing journey/cleanup delegation and closed refusal diagnostics → pure/schema/privacy/HTTP limits and existing harness regressions → composition and independent source review. Keep original model failure evidence, no silent replacement or global runtime policy change, no acceptance claim from catalog eligibility.

T070 bounded standard-CLI catalog gate passed: behavioral RED,219 final offline tests, actual bounded child cleanup, real public catalog rejection before runtime, style/type/composition and Claude READ review. [Evidence and residual programmatic-run responsibility](validation-t070.md). T069 upstream rejection cause and original full native acceptance remain open.
