# Tasks: Generic Message Acceptance Across API Restart

**Status**: Integrated into main after ECC Python/security review. Historical source-bound generic-message runs are Codex R17 PASS, OpenCode R17 PASS, and Claude R17 FAIL at `observer_audit`; none binds the current main tree. A current-main Codex diagnostic now passes after the expanded source-binding guard, while current-main OpenCode and Claude probes end at `phase_a` without calls and were not repeated. T023, T024 and T044 remain pending; the historical external CAO records are not administratively reconciled here.

## Phase 1 — Offline Contract Helpers (TDD)

- [x] T001 Add failing offline tests for inbox row comparison using only the existing `id`, `sender_id`, `receiver_id`, `message`, `status`, and `created_at` contract; assert body privately and reject a claimed version field.
- [x] T002 Add failing offline tests for one redacted `TEXT_MESSAGE_CONTENT` projection: empty `delta`, exact safe metadata, no body, and no inbox/event ID identity claim.
- [x] T003 Add failing offline tests for Codex/Claude/OpenCode opt-in gates: defaults off, invalid values fail closed, unselected lanes N/A, selected provider setup/initialization failures fail.
- [x] T004 Add failing offline tests for A/B ownership descriptors: identical source/HOME/port/database target identities, distinct owned API process identities, retained sender/receiver IDs, and failure on missing terminal without recreation.
- [x] T005 Implement only pure helpers needed by T001–T004, then run the new explicit offline test file and its focused lint/type check if available.

## Phase 2 — Production MCP Dispatch Observer (TDD)

- [x] T006 Add failing offline tests using an in-memory delegate: non-`send_message` calls are refused without delegation; a `send_message` call delegates once with unchanged arguments; returned result is preserved; raised error is re-raised unchanged.
- [x] T007 Add failing privacy tests with body, result, auth, and exception sentinels; serialized observer records may contain only fixed `send_message`/`other` tool enums and fixed status enums.
- [x] T008 Implement the private, explicit-opt-in observer around the source-bound FastMCP public dispatch, capturing and calling the original bound method; keep the canonical bundled MCP command and production tool implementation intact.
- [x] T009 Add static/offline tests for observer opt-in isolation, expected source module/server identity, private audit path validation, no auth/cache path inheritance, and no process/network/database calls in the observer unit tests.
- [x] T010 Run the new offline observer/helper tests with live-provider flags set to `0`; record the exact command and result in the private receipt.

## Phase 3 — Private Acceptance Driver and Pre-Live Review

- [x] T011 Add a standalone explicit provider-lane descriptor/receipt projection. It must default all three provider gates off, permit one selected provider per serial run, expose no raw body/arguments/exceptions/credentials/paths, and fail rather than skip when a selected provider is unavailable.
- [x] T012 Add lifecycle functions that compose existing production `CaoServer`, native terminal, inbox GET, and AG-UI stream surfaces without direct inbox POSTs, mocks, fake panes, global tmux cleanup, or R7/Spec022 behavior changes.
- [x] T013 Add offline source-contract tests for both original native pane IDs remaining present and ready on B and for cleanup targeting only the exact IDs created on A.
- [x] T014 Produce private candidate hash/receipt covering new files, focused test command, and observed limitations. Do not include fixed bodies or secret paths.
- [x] T016 Add source-bound checks for the actual `DatabaseTarget` tuple identity, CAO session prefix, normal sender-ID suffix, and explicit MCP child injection flag; retain the sender body and suffix comparison in memory only.
- [x] T017 Require a private positive `.complete` audit seal only after normal MCP return, observer close, and a clean audit-write flag; test rejection of two surviving success rows when a prior rejection audit write failed and the server later raises.
- [x] T018 Isolate A/B/recovery tmux with one short owner-only root, clear inherited `TMUX`, and test exact-socket teardown only after all API processes exit, owned IDs are deleted, sessions are empty, and server socket/PID identity is pinned.
- [x] T019 Retain caller-owned PostgreSQL targets and the workspace if any API process remains alive or cannot be identified; report the current failed stage for phase-B and API-B transport/read errors.
- [x] T020 Run both explicit offline test files with the pinned interpreter and eight plugins, all live/provider/database gates off; record the final result and hashes.
- [x] T021 Root and independent ECC Python/security reviews completed for the final offline pin: source binding, profile grant and dispatch boundary, audit-completion seal, API A→B lifecycle, receipt privacy, and cleanup. User authorization already covers the real tests; run them serially after final executable preflight.
- [x] T022 Correct and regress the live authorization-helper module boundary discovered during launcher preparation; verify normal-suite portability without private environment setup.
- [x] T025 Reproduce OpenCode receiver creation before native profile installation through the actual acceptance function and native-profile validator, then install each OpenCode profile before its creation request. Preserve prompts, tools, timeouts, messages, API replacement and cleanup. Add normal-suite regressions for receiver/sender ordering, installation rejection before creation, and an unaffected Codex lane; run affected main modules and composition checks, review the final diff, and require fresh OpenCode live acceptance separately under T023.

**T025 verification (2026-10-16):** Root reviewed the frozen-source failure and corrected installation ordering, then integrated both implementation and regressions in main. The full message/observer selection passed 55 cases before formatting and again passed 55 cases after formatting; the three new regressions cover both installations, rejection before creation, and Codex isolation. Composition preserved all five contracts. Root reviewed each subsequent mechanical import correction and independently verified normalized AST/import-block parity. T023 and T024 remain open; this does not count as fresh OpenCode live acceptance.

**R12 status update (2026-10-16):** Codex and OpenCode each later passed the generic-message acceptance under their provider-specific source closures. This resolves the OpenCode fresh-acceptance condition recorded in the T025 verification, while preserving the R20 failure as historical evidence. Claude remains pending. These results do not claim an R15-source run.
- [ ] T023 Execute real Codex, Claude, and OpenCode lanes serially with the owned PostgreSQL service and explicit source-bound launcher; require inbox persistence and a redacted AG-UI notification on both sides of the API restart, plus verified cleanup. Before each lane, set the isolated child environment's `CAO_REAL_PROVIDER_E2E_TEST_FILTER` to the exact self-pair `<selected-provider>-><selected-provider>` (for example, `codex->codex`), with only that Spec023 provider gate enabled. The source-bound preflight imports `test.e2e.test_real_provider_matrix`; when `CAO_RUN_LIVE_PROVIDER_TESTS=1`, its module-level `_matrix_cells()` calls `_pair_selection()` during import and requires this filter. The filter is import-time configuration, not evidence that the generic provider matrix test ran.
- [ ] T024 Record each actual result and limitation, review the final diff, and rerun applicable composition checks before signoff.

## Phase 4 — Claude Staged-Auth Retention (Offline TDD Before Live Acceptance)

- [x] T026 [US2] Add failing offline tests for the portable archive helper's closed `interaction` and `message` layouts; prove exact fake credential bytes are archived without changing original/staged file identity, mode, or bytes; reject unsupported layouts and path traversal; require unique, non-overwriting run archives with directory mode `0700` and file mode `0600`.
- [x] T027 [US2] Add a failing fake-only composition test through `message_api_restart_live._cleanup`: archive the Claude `workspace/home/.claude/.credentials.json` only after all owned API/provider, terminal/tmux, and target resources are confirmed closed and before workspace removal; an archive or shutdown uncertainty must fail cleanup and preserve the workspace; Codex/OpenCode must bypass the archive branch; secret/path sentinels must not enter the safe receipt.
- [x] T028 [US2] Extend `test/live_provider/claude_auth_archive.py` with only a closed layout enum: retain `interaction` as the default mapping to `claude-home/.credentials.json`, add `message` mapping to `home/.claude/.credentials.json`, and use an explicit per-run archive key. Keep descriptor-relative no-follow checks, exclusive creation, identity revalidation, and fail-closed behavior; accept no arbitrary path.
- [x] T029 [US2] Integrate the helper into `test/live_provider/message_api_restart_live.py` only for `claude_code`, immediately before disposable workspace removal after existing cleanup predicates pass; place the archive at `run_root/claude-auth-archive/<run-key>/credentials.json`, outside `workspace`, and retain `workspace` on archive failure. Leave Codex/OpenCode, original auth source/stage, prompts, models, budgets, and provider behavior unchanged.
- [x] T030 [US2] Run the focused helper and message-cleanup composition tests offline with fake credentials and fake process/resource owners; verify source origins, modes, and privacy assertions without provider launches, API worker starts, network requests, PostgreSQL access, or reading actual auth.
- [x] T031 [US2] Obtain root and independent source/security review of the complete helper-to-cleanup composition, then update T023/T024 only with actual source-bound results. Do not launch the Claude provider lane until root reviews the final source pins and explicitly releases the serialized live run.

**Claude archive main integration (2026-10-16):** Root ported the offline regressions first and captured expected RED results (initial five cases: 3 failed/2 passed; final eight cases before implementation: 7 failed/1 passed). Root then integrated the reviewed closed-layout helper and Claude-only cleanup branch. The main selection passed 164/164 tests in 21.46 seconds across archive, message, observer, provider harness and QEMU wait/publication contracts. Black/isort passed for the three affected files. Independent ECC review approved the exact helper/driver/test hashes recorded in `spec023-claude-message-auth-archive-private-r1/main-verification/main-verification-receipt.json`, without reading real credentials or making provider/API/network/PG calls. Original/staged auth remains outside the write boundary; archived bytes are not promoted. T023/T024 and real Claude acceptance remain open; this offline integration does not claim R15 runtime binding or completion of the final composition batch.

## Phase 5 — Claude MCP Graceful Close (Private Offline TDD)

- [x] T032 Add an offline regression in the focused private `test/live_provider/test_claude_graceful_mcp_cleanup.py` showing the existing delete-first cleanup has two observer success rows but no source-bound `.complete` seal after simulated abrupt child termination; assert the audit failure remains even when owned terminal cleanup succeeds.
- [x] T033 In the private `test/live_provider/message_api_restart_live.py` candidate, request graceful `/exit` only for the owned Claude sender, then use only the remaining portion of the shared nominal six-second request-timeout/polling budget to observe the real source-bound completion seal before DELETE. Keep DELETE and all later cleanup fail-safe on exit errors, timeouts, no-ops, or missing/invalid seal; do not change non-Claude lanes.
- [x] T034 Add private offline cases for normal source-bound finalization before DELETE, HTTP 200 without a seal, non-success exit status despite a valid seal, preservation of a prior audit failure despite a valid seal, a valid seal returned after nominal budget expiry, a Requests timeout exception with an exhausted budget and continued DELETE, no `/exit` to the receiver or Codex/OpenCode, and the shared nominal six-second budget without claiming a hard wall-clock deadline. Run only these fake-only tests and the affected existing cleanup tests with all provider/API/database gates disabled; preserve the earlier R15 failure evidence and the unknown live signal.
- [x] T035 Submit the complete private source/test diff, source manifest pins, offline RED/GREEN receipt, and Graphify/source boundary note for root and independent review. Keep T023/T024 and the real Claude provider acceptance pending; do not launch or claim a live rerun.

**R2 expired-budget regression (2026-10-16):** With the sealed R4 candidate driver loaded and source-origin checked, the new fake-clock test reproduced the boundary: `/exit` returned HTTP 200 after seven nominal seconds, the real source-bound finalizer had written a valid seal, and the old candidate still reported `audit_failed=False` after a zero-time wait. The R2 candidate rejects that exhausted budget without polling, keeps deletion of the exact sender and receiver IDs, and remains failed at observer audit. A separate fake raises the real `requests.Timeout` class after exhausting the same budget and proves cleanup still deletes both owned IDs without a zero-time seal poll. Codex and OpenCode retain delete-first ordering. Against frozen R15 the full focused file is expected RED (8 failed, 2 non-Claude passes); candidate GREEN is 10/10. Black and isort pass. This is offline fake-only evidence and does not establish live Claude closure or identify the previous child signal.


**Current main integration update (2026-10-16):** The reviewed Claude-only graceful-close driver and its new offline regression module are integrated in main. Root's main archive/message/observer selection passed 100/100, zero failures/skips. The real R15 run preserved two provider-authored sends, two PostgreSQL inbox rows, two redacted AG-UI events and one API replacement, but failed observer audit because the genuine completion seal was absent; that failed result is retained. No successful live retry of the new close logic is claimed yet. Original/staged credentials remain untouched and the per-run credential archive remains private. T023/T024 stay open. The validated local message operation is `cao-mcp-server.send_message`; no mapping to the external name `cao.work.message` has been established.

## Phase 6 — Receipt-Aware Sender Prompt (Private Offline TDD)

- [x] T036 Add a focused offline regression to `test/live_provider/test_message_api_restart.py` that calls the actual `prepare_input` and active-receipt result guard of Codex, Claude Code, and OpenCode CLI for both exact phase triggers. Assert that the prompt permits only the real trigger-plus-envelope form, rejects arbitrary extra caller text and a second trigger, requires the final visible receipt, and still specifies the same two one-call message actions. Capture RED against frozen R16 before changing the prompt.
- [x] T037 In the private `test/live_provider/message_api_restart_live.py`, make `_build_sender_prompt` provider-neutral for exactly the three supported receipt-bearing IDs. Keep all message bodies, receiver identity, call counts, no-repeat behavior, model/tool permissions, budgets, provider/API logic, and nonselected lanes unchanged. Capture GREEN for the focused regression.
- [x] T038 Run the focused offline test with database and live-provider gates disabled, then Black/isort checks for the two changed Python files. Record the R16 baseline pins, private diff, command, result, and limitations. Submit for root review; keep all live acceptance tasks pending and make no claim that this prompt mismatch caused the R16 `phase_a` failure.


**Private receipt-prompt candidate R1 (2026-10-16):** The focused regression was RED against the immutable R16 sender-prompt source (1 failed, 40 deselected) after invoking real `prepare_input` and receipt guards for all three adapters and both triggers. The private provider-neutral prompt made the same test GREEN (1 passed, 40 deselected). The full offline message contract module passed 41/41 with one `PytestConfigWarning` because plugin autoload was disabled while the frozen `pyproject.toml` declares `asyncio_mode`; Black and isort checks passed. The test binds all provider module origins to the frozen R16 source manifest. No provider, API, network, database, or credential access occurred. The R16 live failure remains preserved and unattributed; root review is pending and no live re-run is claimed.


**Main receipt-prompt integration (2026-10-16):** Root and ECC review accepted the receipt-aware prompt and offline contract test for all three supported providers; both are integrated in main. Main verification passed 41 tests, with the expected configuration warning under disabled plugin autoload; Black/isort passed. The R16 Claude message attempt had zero observed calls and failed phase A; its initial cause remains unknown. Native Claude Unicode acceptance was separately skipped for provider quota, with quota-wait reconciliation validated and four functional scenarios unobserved. The user reports quota reset at 17:40 Europe/Madrid. No successful live execution of this new prompt is claimed, and no original/staged login was changed. Earlier private/review-pending wording is historical.


## Phase 7 — Claude Exit/Seal Diagnostic (Private Offline TDD)

- [x] T039 Add fake-only diagnostic contract tests in `test/live_provider/test_claude_exit_diagnostic.py` before implementation. Exercise the exact graceful-exit helper through its existing fake cleanup seam; assert the closed sidecar schema, no free-form data, one POST, exact sender/receiver DELETE order, and unchanged pass/failure outcomes.
- [x] T040 In the private `test/live_provider/message_api_restart_live.py` overlay, record only closed exit-request, seal-observation, and budget enums around the existing helper. Write a separate private sidecar with the existing exclusive JSON writer; diagnostic-write failure must be inert. Do not change the six-second budget, request count, source-bound seal rule, cleanup order, receipt projection, or non-Claude branches.
- [x] T041 Run the diagnostic test RED against the immutable R17 driver and GREEN against the private overlay, then run the existing source-bound graceful-cleanup module and Black/isort checks for the two private Python files. Keep database, API, provider, and network activity absent.
- [x] T042 Seal the exact private diff, source pins, Graphify limitation, command, test logs/JUnit, and safe schema receipt for root and independent ECC review. Preserve the closed R17 evidence unchanged and keep actual Claude acceptance pending.
- [x] T043 Integrate the ECC-approved R2 diagnostic into main, verify the exact changed source and spec text, run the focused 21-test cleanup/diagnostic selection plus Black/isort/diff checks and project composition review.
- [ ] T044 Refresh the exact source freeze after all accepted main changes and bind each real-provider lane to that manifest before running any live acceptance.

**Current source-binding guard update (2026-10-19):** The generic-message
launcher now hashes the PostgreSQL migration module and the offline acceptance
driver before creating any private temporary directory, selecting a port,
staging provider authentication, or spawning an API/provider process. The
candidate binding set includes `test_message_api_restart.py`; missing,
mutated, and symlinked source/candidate files fail closed in the preflight
regression. The complete offline module passed 54/54 tests, and Black/isort
passed. No provider, API, network, PostgreSQL, or credential activity was
performed by this verification. T023/T024/T044 remain open because a fresh
source-bound live run has not been authorized by evidence and the historical
external records remain unreconciled.

**Private diagnostic candidate r1 (2026-10-16):** The final formatted test module is RED against the immutable R17 driver (8 failed, 2 passed) and GREEN against the private overlay with the existing cleanup module (21 passed). Black and isort checks pass. The candidate preserves the single `/exit` POST, shared nominal budget, genuine seal predicate, exact sender/receiver DELETE order, and sticky audit failure; it writes only closed enums to a sidecar after cleanup. The R17 live failure remains limited to the observed missing seal; its `/exit` result and cause are unknown. No provider, API, network, or database activity occurred. R1 was superseded by the reviewed R2 candidate; T023/T024 remain open.

**Private diagnostic candidate r2 (2026-10-16):** The sidecar names the boolean poller's return boundary (`seal_poll_result`) rather than claiming the exact filesystem-read instant. Its schema identifier is v2; cleanup behavior and all timing/budget decisions are unchanged from reviewed r1. The exact diff SHA-256 is `7950dc779d2293f49be350506655e0a5fe28370a1834d7e61dbef15be65fa913`; the source-bound receipt SHA-256 is `70ba616ccc0dfc297bdcd8894c7b0632597eaa7d80999f67e6d922801b0ecc35`. ECC independently approved R2. Against immutable R17, the diagnostic suite was RED (8 failed, 2 passed); against R2 plus the existing cleanup module, it was GREEN (21 passed, zero skips). Black and isort passed. No provider, API, network, or PostgreSQL calls occurred; actual Claude acceptance remains pending.

**Main integration (2026-10-16):** Root applied the reviewed R2 patch to the main workspace without changing provider behavior or credentials. The diagnostic driver, new fake-only contract tests, and Spec023 spec/plan/tasks are integrated. The precise poll-return boundary and `not_checked` behavior are documented. Main verification passed 21/21 focused tests with zero skips; Black and isort passed; `project-composition-check caos` passed with five contracts kept and none broken. The complete source freeze and all live acceptance lanes remain pending, including Claude Unicode and API-restart acceptance. The two legacy CAO records remain unresolved and were not retried.


## Phase 8 — AG-UI Stream Shutdown Race

- [x] T045 Reproduce the observed `AttributeError` from `Response.iter_lines()` when `AguiCapture.stop()` closes the response concurrently. Treat that exception as expected only at the stream-iterator boundary and after the capture's stop event is set; preserve failed-stream state for unexpected iterator and non-iterator `AttributeError`s. Run focused fake-response tests offline, retain all existing timeouts and live behavior, and do not replay a provider call. The change invalidates prior source pins and requires a fresh source freeze before any later live acceptance.
- [x] T046 Make the private MCP audit distinguish FastMCP dispatcher completion from a logical `send_message` result: record only the closed `success`/`failure`/`unknown` outcome enum, require `success` in acceptance validation, and keep exception/rejected outcomes as `unknown`. Test real FastMCP `ToolResult` success and failure returns offline, reject malformed audit schemas, and prove returned errors/content never enter the JSONL or safe receipt. No provider replay; any later live run requires a fresh source freeze and reviewed source-bound launcher.

**T045/T046 verification (2026-10-16):** The canonical-path offline run of `test_message_api_restart.py` and `test_message_mcp_observer.py` passed 64 tests with zero skips. The tests prove response `close()` releases a reader already blocked in iterator advancement, and prove that only an `AttributeError` from `next()` after explicit stop is treated as shutdown. FastMCP `ToolResult.structured_content` values `success: true/false` map to fixed `success`/`failure` outcomes; unknown and exception results remain non-success, and the receipt validator requires two completed successful calls with an exact closed schema. Error/body sentinels are absent from audit and receipt output. Black, isort, scoped whitespace scan, and `project-composition-check caos` passed (399 files, 1,775 dependencies, five contracts kept, zero broken). Independent ECC review approved both changes and noted the deliberate boundary limit: any `AttributeError` from `next()` after stop is suppressed. An initial offline run via the lowercase WSL path had seven path-origin assertion failures; rerunning from the canonical resolved checkout path passed. Separate Unicode-receipt/OpenCode-profile contracts passed 161 tests with five dependency deprecation warnings in 66.86 seconds. No provider, API worker, network, database, or Docker was started. Live acceptance and source re-freeze remain pending.

## Phase 9 — Adjacent Work Ledger Message Stream

- [x] T047 Exercise the infinite Work ledger `work.message.sent` SSE with a loopback Uvicorn/HTTPX streaming client; verify the initial and recurring calls to the query factory and `WorkQueries.events` run off the API event-loop thread. Source inspection must confirm production `queries()` performs `DatabaseTarget.from_settings()` inside that offloaded call. Map `ConfigurationError` to a safe 503 before streaming and close an already-open stream on a later store error. Preserve disconnect-driven closure, owner revalidation, replay, cursor, retention gaps, and API replacement coverage. Keep this separate from the bundled MCP `send_message` operation; do not claim a mapping between these interfaces.

**T047 verification (2026-10-17):** The loopback regression reproduced both initial/poll SQL reads and the query factory on the request event-loop thread before the fix. The route now runs those calls in `asyncio.to_thread` and maps `ConfigurationError` to a safe `work_store_unavailable` 503; tests cover both initial failure and an open stream closing cleanly on poll failure. The fixture substitutes an instrumented query factory, so the tests assert its thread and the query thread; source inspection confirms production `queries()` constructs `DatabaseTarget.from_settings()` inside the offloaded callable. The combined client/service/API selection passed 31/31 in 163.86 seconds against the reused, already-running owned PostgreSQL container with autostart disabled (five dependency deprecation warnings). Pytest's unraisable-exception teardown plugin was disabled because its `gc.collect()` stalls on this OneDrive/P9 mount; assertions and test cases were unchanged. Black and isort passed. `project-composition-check caos` analyzed 403 files and 1,787 dependencies; five contracts were kept and none were broken. Graphify has no node/path for this route in its existing snapshot; source was checked directly. The Work-specific `cao.work.message` operation and ledger projection are exercised, but this does not establish equivalence with Spec023's generic MCP `send_message` interface. T023/T024 and the live-provider source freeze remain open.


## Phase 10 — Bind Generic Message Persistence, Delivery, and Event Owners

- [x] T048 [US1] Add an offline regression in `test/live_provider/test_message_api_restart.py` that requires the FR-020 production source set and verifies missing, symlinked, and post-snapshot mutation failures happen before auth, port selection, API startup, or provider startup.
- [x] T049 [US1] Extend `_SOURCE_BINDING_FILES` in `test/live_provider/message_api_restart_live.py` to hash inbox persistence/model, receiver readiness, provider/backend delivery, plugin hook dispatch, and event-log owner modules already traced in the source; retain all existing hash checkpoints.
- [x] T050 [US1] Run the focused source-binding tests and the complete offline Spec023 message contract module with provider, API, HTTP/network, PostgreSQL, and credential gates disabled; run targeted Black/isort, `project-composition-check "$(cat .ai/project-name)"`, and final diff review. Keep T023/T024/T044 unchecked because the new bytes require a fresh full-tree source freeze and separately reviewed live launcher.

**Graphify impact (2026-10-19):** The read-only query for the generic `send_message`→inbox→delivery→AG-UI path returned broad ambiguous nodes and no directed symbol path. Direct source tracing is recorded in `graphify-impact.md`; no graph refresh is needed because only the runner's hash inventory and offline tests change.

**Current-main provider probes (2026-10-19):** After the source-binding inventory was expanded to include the PostgreSQL migration, persistence/delivery/event owners, fixtures, and the offline acceptance driver, the fresh Codex generic-message lane passed with two completed MCP sends, two persisted rows, two redacted events, one API replacement, identity match, terminal preservation, and clean cleanup. The sanitized receipt is under `/tmp/caos-spec023-codex-current-20261019-r2/` and its source-binding record is mode 0600. The OpenCode lane was not repeated: its current diagnostic reached the native process stage but ended at `phase_a` with zero calls/events, zero API replacements, and clean cleanup; the receipt is `/tmp/caos-spec023-opencode-current-20261019/spec023-f13b1cf58c0415da/receipt.json`. The corrected Claude invocation used the allowlisted `claude` binary and reached provider processes, then ended at `phase_a` with zero calls/events, zero API replacements, and clean cleanup; the receipt is `/tmp/caos-spec023-claude-current-20261019-r2/spec023-a77452118fde0043/receipt.json`. The earlier absolute-binary Claude preflight failure was a harness rejection before provider start, while the corrected run shows that the remaining Claude result is not explained by that preflight. No provider login was changed and neither failed lane was retried. These records are diagnostics, not all-provider acceptance: T023, T024 and T044 remain open.

The earlier OpenCode stop caused by stale launcher hashes is therefore a real
runner/source-binding defect and must be reported as such; it is not evidence
that OpenCode called or rejected the provider. The expanded fail-closed binding
guard fixes that class of preflight omission offline, while the later native
`phase_a` failure above remains unresolved and was not replayed.

**Current-source OpenCode follow-up (2026-10-19):** A fresh R10-bound lane
started both native OpenCode processes and completed cleanup, but phase A ended
with zero MCP calls, rows, events or API replacements. The stale-hash preflight
was not involved and no provider-initialization timeout was observed. The lane
was not replayed; receipt `/tmp/caos-opencode-current-20261019-r10-r2/receipt.json`.
T023, T024 and T044 remain open.

**Current-source OpenCode R14 follow-up (2026-10-19):** One fresh lane bound
to `current-main-snapshot-20261019-r14` completed preflight and started both
native processes. It ended in phase A after 202.31 seconds with zero MCP
calls, rows, events, or API replacements; cleanup passed, stale-launcher-hash
preflight was false, and no initialization-timeout enum was observed. The
safe receipt is
`/tmp/caos-opencode-current-20261019-r14/evidence/spec023-e3d0d4deb6ab698f/receipt.json`.
The result has no classified provider error and was not retried; the old hash
defect remains fixed and the current provider/model turn failure remains
unattributed.

**Current-main phase diagnostic correction (2026-10-19):** The private
acceptance driver now binds the OpenCode adapter, base receipt path, v2 writer,
MCP overlay/resolution, profile installer, permission translator, and config
owner (`providers/opencode_cli.py`, `providers/base.py`,
`utils/opencode_v2.py`, `providers/mcp_interactions.py`,
`utils/mcp_resolution.py`, `utils/opencode_config.py`,
`utils/opencode_permissions.py`, and `services/install_service.py`) before auth
or process startup. During a phase with no dispatch, inbox, or AG-UI evidence it
polls the sender's persisted status at a bounded one-second cadence. Closed
receipt codes now distinguish provider error, quota wait, provider interaction,
reconciliation, missing terminal, no tool call, and phase timeout; no pane,
prompt, response, or credential text is retained. The normal evidence path
does not add those polls once a dispatch/row/event is observed. Offline
message/provider validation passed **245 tests**, including the bounded
settled-without-tool diagnostic, Black/isort passed, and the
composition check kept all five contracts. This changes diagnosis only; it
does not claim a new OpenCode run or convert R14 into acceptance. The R14
record remains the last live attempt and is still unattributed.


**2026-10-20, Spec 025 implementation increment:** The shared MCP overlay now supports opt-in `interaction`/`collaboration` surface policies with exact list/call enforcement and fail-closed configuration. Completion timeouts expose closed factual observations through run-step HTTP errors, and the real-provider matrix preserves a sanitized failure projection. See [Spec 025 validation](../025-provider-mcp-profiles/validation.md) and [numbered backlog mapping](../025-provider-mcp-profiles/backlog-map.md). This increment has offline contract evidence only; no provider lane, full-tree source-bound acceptance, message/restart gate or historical reconciliation is closed by it. Existing checked and open items above retain their original scope.


**2026-10-20 T007 continuation:** Profile-aware v2 preflight, Codex enabled_tools intersection and native-install rejection of unsupported/shared policy are integrated. The CLI now exits nonzero on service rejection. A real isolated catalog check exposed a receipt-publication startup race in its fixture; the bounded wait correction preserves the 15/45-second limits and retains both failed and corrected evidence. See [T007 validation](../025-provider-mcp-profiles/validation-t007.md). No provider matrix, Work/generic message, source-freeze or historical reconciliation gate is closed by this increment.
