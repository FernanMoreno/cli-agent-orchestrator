# Feature Specification: Real-Provider AG-UI Restart Acceptance

**Feature Branch**: `[022-provider-agui-restart]`

**Created**: 2026-10-15

**Status**: Draft

**Input**: User description: "Add an opt-in real-provider acceptance journey that observes actual interaction questions through the production AG-UI stream, restarts the API process while those questions are pending, and then proves the same provider/MCP invocation receives answers and acknowledges the exact receipts."

## User Scenarios & Testing

### User Story 1 - Verify questions survive a real API restart (Priority: P1)

As a maintainer, I need one bounded acceptance journey for each real Codex, Claude, and OpenCode provider to create two questions, show only their safe metadata through the production AG-UI stream, survive replacement of the API worker while those questions are pending, and let the same provider/MCP invocation receive and acknowledge the exact answer receipts after restart.

**Why this priority**: The current real-provider journey proves question creation, answer return, and exact acknowledgement. Separate AG-UI and service-reopen tests prove projection and storage behavior. None proves that the actual stream and provider journey remain composed across an API process restart while questions are pending.

**Independent Test**: Run the opt-in lane separately for Codex, Claude, and native OpenCode against a disposable PostgreSQL target. Each provider must create the existing two-question group. Stop and replace only the owned API worker before human answers. The replacement must show the same pending questions through the production AG-UI stream, and the same provider/MCP invocation must receive and acknowledge both exact answer receipts through its own MCP path. The OpenCode restart point is after the real create-group response is received and before that same response is returned to the MCP caller; this is pre-wait restart evidence and does not prove recovery from an interrupted wait. The OpenCode lane also must satisfy its native completion rule in SC-010.

**Acceptance Scenarios**:

1. **Given** a real provider/MCP invocation and two durable unanswered questions, **When** the real `/agui/v1/stream` handler emits its initial state, **Then** it reports the same question and group IDs, versions, open lifecycle, and unanswered delivery state while excluding prompts, options, answer content, and private receipt tokens.
2. **Given** those questions are still pending, **When** the owned API worker exits and a fresh API process starts against the same PostgreSQL target, **Then** a new AG-UI connection shows the same two question IDs, group ID, versions, and pending state, and the ledger contains one group rather than a duplicate.
3. **Given** the replacement server is healthy, **When** a human submits the existing fixed answers through the real HTTP answer routes, **Then** the still-running provider/MCP invocation retrieves those answers and acknowledges the exact returned question IDs, versions, and receipts.
4. **Given** the provider has acknowledged both answers, **When** a fresh real AG-UI connection reads its initial snapshot, **Then** the same question IDs reach `agent_acknowledged` without exposing answer content or receipt tokens.
5. **Given** the opt-in flag is absent, **When** the existing provider journey is run in its ordinary mode, **Then** its established behavior and evidence contract remain unchanged.

## Edge Cases

- The replacement API process fails readiness: fail the acceptance and clean up only owned processes and database resources.
- The provider exits during the restart: fail the acceptance; do not synthesize a question, wait result, answer return, or acknowledgement.
- The AG-UI snapshot is unavailable or omits either expected question: fail rather than treating an empty projection as success.
- The replacement process changes question/group identity, version, source incarnation, or creates a duplicate group: fail.
- The API process is restarted while the questions are pending: the harness verifies the replacement process through a fresh AG-UI snapshot, while the provider remains responsible for obtaining and acknowledging answers through its ordinary MCP path. The harness never pulls answers or acknowledges on the provider’s behalf. The wait transport phase at restart is unobserved and may overlap the replacement; retry or recovery of a wait interrupted by restart is not proven, and observed provider/API errors fail acceptance.
- A fresh AG-UI snapshot cannot be read from the replacement process: fail the acceptance.
- A provider is not configured or its required staged authentication is unavailable: report the provider setup failure distinctly; do not switch to an API-key billing path or count it as a pass.
- The standalone AG-UI runner cannot verify its launch baseline, process identities, or final kernel child inventory within its existing 180-second journey and 25-second cleanup windows: fail cleanup, preserve the private run evidence, and do not signal or wait on an unverified process.

## Requirements

### Functional Requirements

- **FR-001**: The existing real-provider interaction journey MUST offer a narrowly scoped opt-in mode for the combined AG-UI/restart acceptance while preserving the default journey behavior.
- **FR-002**: The acceptance MUST cover the existing two-question flow for Codex, Claude, and OpenCode with their already-approved staged authentication and provider-native setup. Codex MUST use `gpt-6-luna`; Claude's AG-UI restart lane MUST use the reviewed `sonnet` alias while ordinary Claude behavior remains unpinned; OpenCode MUST use the already-approved native model `opencode-go/longcat-2.5-preview-free`, selected by its native request command and recorded in the safe receipt. The OpenCode lane MUST use its private native profile/agent setup and MUST NOT use an API-key fallback, synthetic profile-model override, or external action.
- **FR-003**: The API worker MUST serve the production interaction routes and production `/agui/v1/stream` handler in an owned child process. A restart MUST create a fresh process and service instance over the same disposable PostgreSQL target and stable source registration.
- **FR-004**: Terminal and interaction-source registration required by the child process MUST be durable and visible cross-process. The acceptance MUST NOT depend on parent-only monkeypatches or process-local objects for source identity.
- **FR-005**: Before restart and after restart, the real AG-UI stream MUST expose the exact pending question/group identity and safe metadata. The harness MUST validate the production interaction projection against the exact closed typed allowlists for the interaction snapshot and its nested item, source, task, recipient, group-progress, and answer-receipt shapes; it MUST reject unknown fields recursively and omit prompt/options/answer content and private receipt tokens.
- **FR-006**: The restart MUST occur after the provider has created both questions and before the harness submits human answers. The harness MUST keep the provider/MCP invocation and PostgreSQL target alive and restart only the owned API worker.
- **FR-007**: Human answers MUST use the actual version-checked HTTP answer route and stable idempotency keys. The provider/MCP invocation MUST own the wait and acknowledgement calls; the harness MUST NOT perform either action.
- **FR-008**: For Codex and Claude, acceptance succeeds only when the provider exits successfully, both answer deliveries are `agent_acknowledged`, and the recorded acknowledgement identities exactly match the returned group, question IDs, versions, and receipts. OpenCode MUST satisfy the native completion rule in SC-010; its natural provider process status is a separate nullable observation and MUST NOT be inferred from harness exit, acknowledgements, or cleanup.
- **FR-009**: The replacement AG-UI stream MUST project the final acknowledged states for those same questions without exposing answer content or private receipt tokens.
- **FR-010**: The opt-in harness MUST use run-private paths derived from its temporary root, a minimal child environment, private logs/evidence, and ownership-scoped cleanup. It MUST NOT contain operator-specific absolute auth/cache paths or retain raw provider transcripts in the sanitized receipt.
- **FR-011**: Pure unit tests MUST cover SSE parsing, API-worker startup configuration, process ownership/restart orchestration, safe environment construction, and negative cases without starting a provider, HTTP server, or database.
- **FR-012**: The opt-in AG-UI acceptance MUST remain in its standalone runner process with the existing 180-second journey and 25-second cleanup bounds. It may use the portable controller's ownership helpers, but MUST NOT call the generic standalone `main()` path or inherit its separate 900/880-second window. The generic standalone command MUST remain prepared-only unless `--execute` is explicit; that execution path MUST require a private validated `ControllerConfig` and retain its reviewed 900-second hard and 880-second run-stop bounds. Its controller-module digest MUST match both the configured frozen source file and the actual loaded module origin. Both owning controller paths MUST verify an empty baseline child inventory, pin their own and launcher identities, keep bounded PPID census limited to discovery after verified task-list churn, preserve verified-process-group signal checks and exact-PID reap rules, and declare cleanup complete only after the pinned controller's post-run kernel child inventory is empty within the applicable hard deadline. Unreadable, ambiguous, reused, or unresolved identities MUST fail closed. The ordinary in-process journey and `ApiWorkerController` MUST NOT enable subreaper behavior.

## Key Entities

- **Acceptance Run**: One bounded provider invocation plus its isolated API worker lifecycle and sanitized result.
- **Interaction Group**: The two-question request created by the actual MCP tool and durably identified in PostgreSQL.
- **Question Metadata Projection**: The closed AG-UI state for each question; includes safe IDs, version, lifecycle and delivery metadata but excludes protected content and receipt tokens.
- **API Worker Instance**: One owned API process initialized over the configured PostgreSQL target. A replacement instance must recover the same durable interaction state.
- **Answer Receipt**: The opaque receipt returned to the provider/MCP process and used in its exact acknowledgement; the token is not part of the AG-UI projection.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Each opt-in Codex and Claude journey produces exactly two provider-created questions, two human answers, and two identity-matched `agent_acknowledged` deliveries with natural provider process exit code zero. OpenCode is evaluated by SC-010; its natural process exit status remains null/unobserved unless the provider-owned exit status is directly captured.
- The sanitized receipt MUST retain the order and multiplicity of allowed per-question delivery states, including two identical acknowledgements. Filtering private or unsupported values MUST NOT turn this sequence into a set. Other categorical receipt fields retain their existing deduplication policy.
- **SC-002**: Exactly one API worker restart occurs between durable question creation and answer submission; question IDs, group ID, versions, and source incarnation remain unchanged across restart.
- **SC-003**: Both pre-restart and post-restart real AG-UI snapshots contain both pending questions, and a post-acknowledgement projection shows both as acknowledged.
- **SC-004**: The SSE evidence contains no question prompt, offered option text, answer text, opaque receipt token, or provider transcript content.
- **SC-005**: The offline harness test module completes without provider calls, server starts, or PostgreSQL access, and verifies cleanup ownership and default-off behavior.
- **SC-006**: The successful Claude AG-UI restart receipt records exactly `sonnet`, after the provider command is verified to contain one matching `--model` argument; default Claude mode remains unpinned and receipts reject arbitrary model names.
- **SC-007**: The portable outer controller imports without operator-specific absolute paths or authentication material, requires all runtime/source pins through validated configuration, binds its configured controller-module path to its actual loaded origin, and reports incomplete cleanup whenever its pinned post-run kernel child inventory is not empty.

## Assumptions

- Existing private provider-auth staging and approved provider/model selections remain authoritative, including the approved native OpenCode auth and profile setup.
- A loopback-only API worker with authentication disabled is sufficient for this test-only stream acceptance; this does not replace browser-session authentication coverage.
- `api.main.app` can be imported with its lifespan disabled while the worker explicitly initializes an `InteractionService` over the configured target. The worker must still use the production route handlers.
- Existing question and answer wire semantics remain unchanged; this work adds test orchestration only and does not add a production API, schema, or product behavior.
- The existing per-provider runtime budget remains the upper bound; the acceptance does not extend it to mask restart delays.

## Coverage boundary

### CX-07 in-flight HTTP interruption

A separate opt-in Codex case must observe a positive-duration wait entering the
original owned API worker, with the native wait call still in progress and no
successful wait return. Crash/reap that exact child and prove the pending
request did not return before starting its replacement on the same port.
The same native MCP wait invocation must reach the replacement for the same
group and return the exact receipts later acknowledged once. Private worker
observations contain only group ID, worker PID and wait duration. The existing
75-second provider budget, HTTP wait budget and worker readiness limit remain.
Model-free real-socket/PostgreSQL fault injection and native evidence remain
separately scoped; neither closes other providers or global acceptance.

### Diagnostic distinction after a failed wait (2026-10-26)

The sanitized MCP result must distinguish the existing fixed `unavailable`
details as `http_error`, `connection_error`, or `unexpected_error`. Emit only
these enums, never the detail, transcript, arguments, or response body. Unknown,
conflicting, or nonmatching details remain unclassified. This is diagnostic
evidence, not a change to MCP errors, retries, provider budgets, or acceptance.
The distinction must survive provider event parsing and final receipt sanitizing.

The harness opens separate, one-shot SSE requests for the pre-restart, post-restart, and post-acknowledgement snapshots, then closes each request after its initial snapshot. It proves that the same two pending questions survive API process replacement and that the same provider/MCP invocation later receives and acknowledges the returned answers. The wait transport phase at replacement is unobserved and may overlap it; interruption, retry, or recovery of a wait across replacement is not proven. Actual provider/API errors fail the acceptance.

The portable outer-controller extraction and its offline lifecycle suite have been validated separately from this live acceptance. The private operator adapter has not yet been bound to this repository revision. No Codex, Claude, or OpenCode provider acceptance is claimed by offline validation.


## Follow-up: exact identity recheck after an empty task listing

**Status:** Proposed portable controller follow-up. The preserved R3 failure remains unresolved; this amendment does not claim that the candidate explains or repairs that run.

### Evidence boundary

The preserved R3 controller run reported `process_task_inventory_empty` while reading owned-process children. Its inventory recorded four process groups before the later scan and zero afterward, with unreadable state still true and cleanup incomplete. The run has no JUnit result, launcher receipt, or handoff evidence. No post-empty observation of the pinned process identity was recorded, so the live R3 cause is unknown: the pinned process may have exited, remained the same process, or been replaced by a reused PID. The private synthetic RED reproduction demonstrates a classification gap in the copied baseline only; it does not establish which condition occurred in R3. Preserve the original R3 evidence unchanged.

### Functional requirements

- **FR-013:** Whenever enumerating `/proc/<pid>/task` for an already pinned process returns an empty list, the controller MUST make exactly one immediate fresh observation of that same PID identity before classifying the empty result. The observation MUST compare the existing identity pin fields, including PID, start time, UID, process group, and session.
- **FR-014:** If that exact identity is absent, the controller MUST use the existing `identity_missing_after_snapshot` process-exit branch. The owned-tree scan MAY use only its existing bounded restart behavior for that branch.
- **FR-015:** If the identity still matches the pin, the controller MUST retain `process_task_inventory_empty` and report inventory/cleanup as incomplete. An empty task list MUST NOT be treated as an empty child set or a clean ownership proof.
- **FR-016:** If the identity has changed, the controller MUST use the existing `identity_changed_after_snapshot` failure branch. It MUST NOT adopt, signal, wait on, or reap the replacement identity; that branch is not an owned-process-exit retry.
- **FR-017:** An unreadable or failed identity recheck, or an expired inventory deadline, MUST fail closed. Such outcomes MUST NOT be reclassified as a missing process. A later empty observation receives the same single recheck and remains subject to the existing attempt and deadline limits.
- **FR-018:** Non-empty task enumeration and existing bounded TID-churn behavior MUST remain unchanged. The change MUST NOT add retries, extend any journey/run-stop/cleanup deadline, widen signal or reap authority, add a PPID-census fallback, add telemetry/receipt fields, or alter cleanup-completion criteria.

### Success criteria

- **SC-008:** The new empty-task regression cases in the existing portable offline suite use a synthetic proc tree and distinguish missing, unchanged, changed, unreadable/error, and deadline outcomes after an empty task listing, including a subsequent empty observation. These new cases do not inspect host `/proc`, spawn or signal a process, or start a provider/service. Existing suite cases may inspect the test process's own procfs identity and children.
- **SC-009:** Only confirmed absence of the exact pinned identity reaches the already bounded owned-tree restart branch. Same-pin emptiness, identity changes, unreadable identity, and deadline expiry remain fail-closed with cleanup incomplete where ownership cannot be proven.

The provider acceptance and the generic full-backend run remain separate gates. Passing the synthetic cases cannot be reported as a live R3 pass or as proof of the cause of the preserved R3 failure.

## Native OpenCode restart boundary

The combined OpenCode lane pauses only after the real provider/MCP create-group POST has returned and the response is committed, and before the same response object is returned to the native MCP caller. After the API worker replacement and barrier release, the native provider continues with its ordinary wait and exact acknowledgement calls. This verifies a restart before the provider begins its wait phase; it does not establish interrupted-wait recovery. The opt-in marker is a private bounded sidecar signal, not MCP stdout, and contains no prompt, response body, question ID, receipt, URL, or provider text. The fixed OpenCode command, model, profile, auth, tool permissions, two-question prompt, and absolute journey deadline remain unchanged.

## Follow-up: native OpenCode AG-UI restart acceptance

### Additional functional requirements

- **FR-019:** The OpenCode acceptance MUST use real native OpenCode initialization and interaction tools while the owned API worker serves the production interaction routes and `/agui/v1/stream`. The same OpenCode provider/MCP process and PostgreSQL target MUST remain alive while only the API worker is replaced. Restart MUST occur after the real create-group POST response is received and committed but before that exact response is returned to the same MCP invocation. This is a pre-wait restart; it MUST NOT be described as interrupted-wait recovery. The separate native OpenCode diagnostic does not satisfy this requirement.
- **FR-020:** The OpenCode acceptance MUST reconcile the exact two question IDs, group ID, versions, and source incarnation across three production AG-UI snapshots and the durable interaction ledger: pending before restart, still pending after restart, and `agent_acknowledged` after the provider acknowledges both answers. It MUST also reconcile provider/MCP evidence for group creation, ready wait, and exact receipt acknowledgement. Missing or mismatched evidence MUST fail closed. The harness MUST NOT synthesize a provider wait, answer return, or acknowledgement.

### Additional success criterion

- **SC-010:** OpenCode acceptance requires the approved native model, exactly two identity-matched provider acknowledgements, and unchanged identities in the pending-before-restart, pending-after-restart, and final acknowledged production snapshots and durable ledger. Before the existing absolute journey deadline and immediately before owned terminal cleanup, the harness MUST obtain a fresh backend history buffer and call `get_status()` on the same production `OpenCodeCliProvider` instance; only `IDLE` or `COMPLETED` is acceptable. In the tmux lane the status source is `tmux_live_pane_via_production_provider`: the existing production provider classifies its fresh live pane buffer. Harness-side TUI parsing, `_native_tui_stage`, and synthetic monitor state MUST NOT substitute. Owned cleanup MUST be verified and the acceptance harness MUST exit zero. Harness exit zero alone is insufficient. Natural OpenCode provider exit status MUST remain null/unobserved unless directly captured and MUST NOT be inferred from cleanup, acknowledgements, or harness exit.

## Private follow-up: portable stdio ownership cleanup helper

This private addendum extends the existing FR-010 ownership boundary for the isolated MCP/stdio operator. It does not change provider behavior, production API behavior, cleanup authority, or the FR-010 privacy requirements.

### Additional functional requirements

- **FR-021:** The MCP/stdio acceptance adapter MUST obtain native receipt parsing and ownership-scoped cleanup from one portable helper under `test/live_provider/mcp_stdio_cleanup.py`. The helper MUST contain no host-specific source paths, runtime or manifest pins, credentials, provider profiles, or auth/cache defaults. The host-bound adapter MUST import and use the exact helper symbols; it MUST NOT retain duplicate receipt or cleanup implementations.
- **FR-022:** Receipt parsing MUST remain strict about success, parent identity, unique UUID and terminal IDs, allowed states, and presence of the cleanup field. Cleanup MUST delete only incomplete child receipts validated for the requested parent, then retain the existing parent/session deletion and absence-poll sequence, loopback URL and session-name validation, request timeouts, fixed diagnostic vocabulary, first-failure behavior, and existing 25-second cleanup deadline. Cleanup MUST skip a child only when its parsed receipt has `cleanup_completed=True`, meaning `cleanup_completed_at` is non-null; a null stamp remains incomplete and eligible for deletion after the existing parent-ownership checks. This preserves the production API's completed-child cleanup ownership and MUST NOT convert a completed child into a deletion attempt that can fail with HTTP 409.
- **FR-023:** The portable helper and its offline tests MUST not start a provider, invoke an HTTP service, access PostgreSQL, or make network calls. The private host adapter MUST retain source binding, SDK/runtime verification, auth staging, logging, and result handling outside the portable helper. Until a new source snapshot includes the helper and tests, the R13 source manifest MUST NOT be represented as pinning those new files.

### Additional success criteria

- **SC-011:** Offline-marked tests exercise the real receipt parser and cleanup helper, including completed-receipt skip, active owned-child deletion, unverified/malformed receipt refusal, parent scoping, input validation, bounded request behavior, fixed diagnostics, and fail-closed handling of HTTP errors. Deadline behavior is tested with a deterministic fake clock while retaining the 25-second cleanup setting. The tests run without provider/API/PG/database/network activity.
- **SC-012:** The private adapter imports the exact `NativeReceipt`, `OperatorFailure`, `receipt_rows`, `cleanup_owned`, cleanup bound, and diagnostic projection/vocabulary objects from the portable module. Existing host-specific source/runtime/auth verification remains outside that module, and the candidate's R13 limitation is recorded honestly.

These criteria extend the test-only ownership cleanup contract already described by FR-010. They do not authorize an actual provider run, source freeze, or main integration.


## Addendum: OpenCode AG-UI native 75-second budget boundary

### Additional functional requirement

- **FR-024:** In the opt-in OpenCode AG-UI restart lane, the existing 75-second native interaction deadline MUST be computed and published exactly once from the existing `on_phase("native_send_input")` callback, after `terminal_service.create_terminal(..., defer_init=False)` has completed provider initialization and immediately before the single `terminal_service.send_input()` call. Initialization remains inside the outer 180-second journey bound but outside this 75-second sub-budget. The 75 seconds cover the input call, response-barrier wait, question/answer/acknowledgement wait, and final provider-status observation. A missing barrier directory, pre-existing or invalid deadline, or publication failure MUST fail closed before input is sent. The deadline MUST NOT be reset, renewed, or extended. The ordinary native journey's existing deadline placement remains unchanged.

### Additional success criterion

- **SC-013:** Offline tests MUST prove the order provider initialization → one deadline publication → exactly one input send; a missing barrier, pre-existing deadline, or publication failure MUST prevent input. A simulated initialization that outlasts a hypothetical pre-create deadline MUST still publish a fresh 75-second budget only after initialization. Tests MUST preserve the existing 180-second journey and 25-second cleanup bounds. This boundary clarifies the AG-UI 75-second start point; it does not claim that ordinary native mode uses the same start point.


## Addendum: Preserve Claude credentials across isolated-run cleanup

This Claude-only addendum extends FR-010's private-path and cleanup boundary. It preserves the accepted provider/model/auth setup and journey criteria. A retained archive is a preservation copy; it does not restore or promote credentials automatically. No conclusion about the cause of any earlier Claude logout or credential rotation is implied.

### Additional functional requirements

- **FR-025:** For each Claude journey that uses an isolated per-run home, after the owned Claude process group has been positively confirmed reaped and before its temporary run root is removed, the harness MUST preserve a byte-identical copy of the final Claude credential file from that isolated per-run home in an operator-owned archive outside the temporary root. The archive entry MUST be unique and created without overwriting an existing entry. Archive directories MUST be owned by the harness user and have mode 0700; the credential file MUST be owned by that user and have mode 0600. The copy MUST come only from the isolated per-run home, never from the original login source or staged input, and its file and archive-directory entry MUST be durable before run-root removal is allowed.
- **FR-026:** Archiving MUST be one-way preservation. The harness MUST NOT write, replace, move, delete, or automatically promote the operator's original Claude login or staged credential source. If process reaping is unverified, the credential is missing or unsafe, the source identity changes during copying, the archive root or destination is unsafe or already occupied, or writing/syncing the archive fails, cleanup MUST fail closed and retain the complete per-run root and its private evidence. Diagnostics MUST use fixed safe categories and MUST NOT expose credential bytes, auth values, credential paths, or source/stage contents. This behavior MUST remain limited to Claude and MUST NOT change Codex or OpenCode auth, models, prompts, permissions, or acceptance criteria.

### Additional success criterion

- **SC-014:** Offline-marked tests MUST exercise the same cleanup entrypoint used by the R14 operator, with synthetic credential bytes under an isolated temporary run root. They MUST prove the archive copy is complete and durable before the real run-root removal path runs; validate exact copied bytes, owner and 0600/0700 permissions; and prove source/stage sentinels and the operator login boundary remain untouched. Missing or unsafe credential input, unverified stop, destination collision, source replacement, or copy/sync failure MUST retain the run root and fail cleanup. Failure diagnostics MUST exclude the synthetic bytes and credential paths. Tests MUST not read actual Claude credentials or start a provider, API/HTTP service, database, PostgreSQL, or network connection.

The archive preserves the last credential bytes present in the isolated run home. It cannot guarantee that those credentials remain valid, establish why a prior logout happened, or prove any provider-side rotation. Live Claude acceptance remains separately gated.

## OpenCode MCP protocol observation (2026-10-16)

R14 produced no question and retained no structured tool invocation trace. Its configured grants and MCP connection prove neither catalog contents nor a successful call. The cause of the missing question remains unknown.

- **FR-027:** An opt-in, source-bound test startup hook MUST observe the real registered MCP tools/list and tools/call handlers without changing production source, prompts, models, grants or deadlines. It MUST delegate each handler exactly once and preserve the identical returned object or raised error, including normal MCP error results. Only fixed tool names/statuses/counts and a private run nonce may enter its bounded owner-only trace; arguments, results, descriptions and error text MUST be excluded.
- **FR-028:** Receipt projection MUST distinguish configured authorization from observed catalog/calls. Missing, malformed, foreign-run, partial or uncertain-close evidence MUST NOT become observed success. Internal catalog refresh during a call MUST NOT be counted as a client tools/list request. Audit write/fsync/close failure MUST fail observation integrity. Natural CLI exit and sampled production completion remain separate observations.
- **SC-015:** Offline tests exercise real FastMCP registered handlers and the source-bound startup hook with fake run inputs, proving exact-once delegation, unchanged result/error identity, no payload leakage, internal-refresh suppression/context isolation, absent/partial evidence and write/fsync/close failure. No provider, transport service or PostgreSQL starts. This is diagnostic coverage; it does not fix or explain R14's zero-question outcome.

## Private diagnostic follow-up: OpenCode outbound tool offer

The R15 OpenCode AG-UI failure had no observed MCP calls. Existing MCP `tools/list` evidence describes the server catalog, and configured agent grants describe authorization; neither proves which tools were serialized into an OpenCode model request. The current official model catalog also reports no `capabilities.tools` value for the target model. This follow-up adds evidence at the outgoing-request boundary without changing the acceptance behavior or claiming the cause of the earlier failure.

### Additional functional requirements

- **FR-029:** In the opt-in OpenCode diagnostic only, a private project-local OpenCode plugin MUST use the official `session.http.request` hook to observe requests for the exact approved provider/model and primary request kind. It MUST inspect only a bounded clone of the `Request` body in memory, leave the original event and `Request` unchanged, and keep the existing model, prompt, profile, grants, API behavior, and deadlines unchanged. Each callback MUST publish at most one bounded, owner-only, create-only per-request record containing only the private run nonce, ordinal, exact-model-match boolean, request kind, fixed status, bounded counts, and names from the three already-granted OpenCode tools. Each valid record MUST be written to an exclusive temporary file, file-synced, closed, atomically published, and followed by a successful sync of its containing directory before the callback returns. The observer MUST NOT persist or print prompt/body text, tool schemas, tool arguments, request URL/headers, authentication data, responses, or unrecognized tool names. A persistence failure MUST invalidate that record and make the aggregate trace incomplete, but MUST NOT suppress, retry, or rewrite the original model request.
- **FR-030:** Receipt projection MUST keep three independent facts separate: configured authorization, server-side MCP catalog/call observations, and tools observed in each serialized outgoing model request. Only a well-formed exact-model primary request with a validated `tools` array and a successfully published per-request record may establish a per-request offer observation. The request is observed at OpenCode's pre-transport hook; this does not prove that HTTP was sent or received, or that the provider/model accepted the offered tools. An explicitly empty valid array means zero tools were offered for that request. A separate bounded global seal MUST be published only after hook registration disposal and completion of all queued record writes; it MUST report hook-disposal and trace-integrity facts, not CLI exit or journey completion. Missing/invalid seal, missing nonce, partial/malformed record, overflow, write/file-fsync/directory-fsync/close failure, or uncertain disposal MUST leave the overall offer status incomplete or unobserved even when valid individual records survive. Such records may be projected as per-request facts, but they MUST NOT establish overall trace/offer completeness or substitute for the pre-existing journey acceptance criteria. Observer incompleteness alone MUST NOT add a new journey failure condition. Current catalog metadata and MCP readiness MUST NOT substitute for an outbound-request observation.
- **FR-031:** OpenCode tool-offer diagnostics MUST identify the request model using the pinned client version's `Model.Ref` contract (`providerID`, `id`, optional `variant`), not an inferred or provider-specific field name. The persisted record MUST contain only a boolean exact-match result; it MUST NOT persist the model ID, variant, or any other model metadata. A missing or malformed model reference MUST remain a mismatch/unobserved request and MUST NOT be treated as a matching target. The outer operator receipt MUST carry the inner observer summary only through the same strict safe projection used by the provider receipt, preserving `unobserved` or `incomplete` when the field is absent or invalid.

### Additional success criterion

- **SC-016:** Offline tests MUST exercise the actual observer hook with real local `Request` objects. They MUST prove event/request identity and the original request body remain unchanged; exact allowlisted tool-name extraction and count behavior; valid empty-array handling; fail-closed behavior for absent, malformed, unsupported, and oversized bodies; preservation of a pre-existing pending path on exclusive-create collision; no prompt/header/auth/schema/argument leakage; and separate per-request durability versus global hook-disposal evidence. A forced-termination fixture MUST kill an isolated local Node child only after its hook callback returns with a successfully file-synced, closed, atomically published, and directory-synced per-request fact. The surviving fact may be projected as that request observation, but the absent global seal MUST leave the overall trace and offer status incomplete/unobserved. Partial, malformed, missing, nonce-mismatched, overflowed, or uncertain-close evidence MUST never create aggregate success. Tests MUST run without OpenCode, providers, HTTP services, MCP transport, databases, or PostgreSQL. This criterion adds diagnostic evidence only; it neither changes the pre-existing OpenCode journey acceptance requirements nor claims that the diagnostic explains the R15 zero-question result.
- **SC-017:** Offline tests MUST use the exact pinned `Model.Ref` object shape `{providerID, id, variant?}` at the observer boundary. They MUST prove that the requested provider/model with an optional variant is recognized using only the fixed boolean, while wrong IDs, wrong providers, and a `modelID`-only lookalike fail closed. An end-to-end outer-receipt projection test MUST run with only the operator runner directory on `PYTHONPATH`, load the source-bound canonical observer helper, and forward its safe projection without serializing raw model identifiers or observer input. Missing and malformed observer summaries MUST remain unobserved/incomplete. These tests MUST make no provider, OpenCode CLI, API, MCP transport, network, database, or PostgreSQL calls and MUST NOT change journey acceptance gates.


## Phase 14: Preserve canonical OpenCode MCP permission actions through normalization

The preserved R17 profile encoded the intended CAO MCP actions with an underscore-form server prefix (`cao_mcp_server_...`), which does not match the canonical v2.0.18 action names. Correcting the profile to canonical hyphenated actions alone would still be insufficient: the shared CAO v2 config writer sanitizes `-` to `_` before writing `opencode.json`. The pinned OpenCode v2.0.18 MCP action name retains the server hyphen. This phase corrects the profile and shared normalization boundary and records the offline contract; it does not claim that the change has yet passed main verification or live OpenCode acceptance.

- **FR-032:** The shared CAO v2 config writer MUST preserve hyphens in MCP server names and tool selectors while replacing other unsupported characters as before. It MUST retain existing `*` and `?` selectors, longest-server matching, non-MCP actions, and rule ordering/precedence. The opt-in OpenCode AG-UI profile MUST preserve default deny and allow exactly the three canonical v2.0.18 direct MCP actions: `cao-mcp-server_cao_ask_user`, `cao-mcp-server_cao_wait_for_user_answers`, and `cao-mcp-server_cao_acknowledge_user_answers`. Its configured MCP server MUST set `codemode: false`; `execute`, server-wide wildcard grants, and all other MCP actions MUST remain denied. Server command/environment, model, prompt, authentication, input count, restart behavior, and time bounds MUST remain unchanged.
- **FR-033:** The existing generated-scope assertion MUST validate actual `write_v2_configuration()` output after normalization and prove exactly those three direct actions, default deny, `codemode: false`, and no `execute` or unlisted tool permission. The existing catalog and outbound-request observer allowlists/fixtures MUST use the canonical names. Their schemas, privacy rules, and acceptance gates MUST remain unchanged; observing an offered name MUST NOT count as a tool invocation.

### Additional success criterion

- **SC-018:** Offline tests MUST run the actual CAO config-writer path and apply the pinned v2.0.18 direct-tool permission rules to the generated config. The preserved underscore-form R17 profile MUST yield zero active direct MCP definitions. The corrected profile MUST yield exactly the three canonical actions, with `execute` and all other MCP actions denied. Tests MUST cover server/tool hyphen preservation, `*`/`?`, unchanged non-MCP actions, longest-server matching, generated keys, and `codemode: false`. The observer MUST recognize only the three canonical names while counting unknown names without persisting their values. These tests MUST NOT start OpenCode, a provider, MCP transport, an API, a database, PostgreSQL, or a network service. Existing question, acknowledgement, restart, status, cleanup, and budget criteria remain unchanged.


## Spec 022 Phase 15: recover the same wait after restart

Status: design reviewed by root and independent ECC; main verification and live acceptance remain pending.

### User story

As a provider completing a required interaction while its API worker restarts, I need to resume waiting on the already-created question group after a transient unavailable response, so I can receive the human's answers without creating duplicate questions or acknowledging answers I did not observe.

### Functional requirements

- **FR-034:** In the opt-in API-restart interaction prompt, if `cao_wait_for_user_answers` returns a structured error whose code is exactly `unavailable`, the provider MUST repeat that wait using the same `group_id` and unchanged `wait_seconds=20`, just as it already repeats when the wait state is `pending`. Repetition continues only within the existing 75-second provider-task budget and stops on a ready or terminal state. It MUST NOT call `cao_ask_user` again. This is prompt guidance only; no automatic client or server retry is introduced.
- **FR-035:** The restart prompt MUST fail closed on an error code other than `unavailable`, on a terminal wait state, or when the existing journey bound expires. It MUST NOT call `cao_acknowledge_user_answers` after any wait error, pending result without answers, or terminal state. Acknowledgement remains permitted only after a `ready` result contains both expected answers and their exact `question_id`, `version`, and `receipt` values.
- **FR-036:** Existing pending-state handling, the ordinary non-restart prompt, the exact question payload/count, provider/model/auth/tool configuration, the 20-second wait argument, the 75-second provider-task budget, the outer journey deadline, and cleanup bounds MUST remain unchanged.

### Success criteria

- **SC-019:** A focused prompt test proves the ordinary prompt is unchanged and the opt-in restart prompt repeats only the same-group wait on `pending` or structured `unavailable`, preserves the no-repeat-ask rule, and keeps exact-receipt acknowledgement conditional on a ready response.
- **SC-020:** The existing PostgreSQL-backed replay test proves that a repeated ready wait for the same group returns the same answer receipts, preserves exact-receipt acknowledgement, and does not append a second answer-return event.
- **SC-021:** A separately authorized Codex restart run either completes the existing acceptance contract with one group, the same two questions, a ready wait, exact acknowledgements, unchanged restart count and budgets, and verified cleanup, or fails closed. A prompt edit or offline test alone MUST NOT be reported as live recovery success.

### Scope boundaries

The additional retry instruction applies only to `cao_wait_for_user_answers` results with `error.code == "unavailable"`, only in the opt-in restart flow, and within the existing 75-second provider-task budget. It does not retry `cao_ask_user`, answer submission, or acknowledgement. It adds no MCP/API operation and changes no server/client retry policy. The overall acceptance remains governed by the existing provider budget and all existing answer, acknowledgement, identity, status, and cleanup gates.


**Main integration (2026-10-16):** The accepted three-file delta is ported after root and independent ECC review. The ordinary prompt was compared byte-for-byte with R17 for ASCII and Unicode operation keys. Main module validation and live recovery are pending; no provider result is inferred from this port.


**Main verification update (2026-10-16):** The Phase 14 and Phase 15 source/test changes are integrated in main. The five OpenCode-related Python modules passed 239 tests, the archive module passed 22, and the remote response-loss, AG-UI restart, and PostgreSQL interaction replay modules passed 45 together; each run had zero skips. The Node 22.22.1 observer suite passed all 17 tests. Black/isort passed on 13 changed Python files, and project composition passed five contracts with zero broken. These checks do not establish live provider acceptance, a fresh Docker image, or a passing full backend suite. Final source/diff review and source binding remain pending.


## Spec 022 Phase 16: reject non-ASCII answer receipts safely

### User story

As an agent acknowledging a returned interaction answer, I need malformed receipt text to fail as typed invalid input so Unicode in prompts or answers cannot turn receipt verification into an internal error.

### Functional requirements

- **FR-037:** Opaque answer receipts are ASCII URL-safe tokens. The domain `AnswerAck` and MCP acknowledgement DTO MUST reject a non-ASCII receipt before service work or receipt comparison. The HTTP acknowledgement route MUST reject it while converting the request DTO to `AnswerAck`, before calling the service, and MUST return a fixed sanitized 422 without echoing receipt contents. The interaction service MUST independently reject a non-ASCII receipt with `InteractionInvalid` before opening a database transaction, comparing it with `hmac.compare_digest`, or hashing it as ASCII. A malformed MCP/API wait response containing a non-ASCII receipt MUST be rejected as an invalid response. Validation errors MUST NOT expose receipt contents in application logs or custom error details.
- **FR-038:** Unicode remains supported in user prompts, question labels, and free-text answers under their existing UTF-8 length limits. Receipt validation MUST NOT impose an ASCII restriction on ordinary user-authored content or change receipt generation, acknowledgement identity, delivery transitions, or replay semantics.

### Success criteria

- **SC-022:** Offline tests reject a non-ASCII receipt at the domain, HTTP, and MCP input/output boundaries, and a defensive service test proves even a forged model cannot reach the transaction or `compare_digest`. The regression reproduces the exact prior `TypeError` without the fix and receives a stable typed invalid result with it. A canonical ASCII receipt remains accepted by each schema.
- **SC-023:** Existing PostgreSQL-backed interaction acknowledgement/replay tests pass unchanged, and explicit model tests preserve Unicode prompts and free-text answers. These checks MUST NOT call a real provider or modify provider credentials.
- **SC-024:** The historical Claude `run-step` failure is not attributed to this ACK boundary unless a fresh, separately source-bound run or preserved trace shows that call path. Passing offline regressions alone MUST NOT be reported as live Claude acceptance.

### Scope boundaries

This phase hardens only the opaque answer-receipt boundary across the existing domain, HTTP, MCP, and service layers. It adds no retry, schema migration, receipt format change, telemetry, or provider-specific behavior. Unicode question and answer text remain valid; only opaque receipt tokens require ASCII.

**HTTP validation privacy clarification:** The no-echo requirement also covers request-validation failures raised before the ACK endpoint runs, including receipt length, missing fields, and receipt-list bounds. The interactions router MUST return the same fixed invalid-request problem for every ACK request-validation error, even when the router is mounted in a separate FastAPI app. Other routes retain their existing validation-error behavior.


## Phase 17: Preserve bounded cleanup when the launcher identity pin fails

### Functional requirements

- **FR-039:** The first identity observation of the outer launcher's `Popen` child MUST match the known controller PID as its parent, as well as the existing PID, UID, process-group, session, and session-separation checks. A missing or mismatched identity MUST fail before stdout or selector access. The controller MUST NOT retry the first pin or adopt a later identity at the same numeric PID.
- **FR-040:** When the launcher identity or controller identity cannot be verified during cleanup, the supervisor MUST use only the exact `Popen` handle to stop and reap its direct launcher child, within the existing cleanup deadline. It MUST NOT signal a process group, signal or reap a replacement PID, claim descendant cleanup, or remove private artifacts on this path. If the direct child cannot be confirmed stopped, or detached descendants remain unverified, cleanup MUST remain incomplete and preserve the run artifacts. Existing journey, run-stop, and cleanup deadlines MUST NOT increase.

### Success criterion

- **SC-025:** Offline fake-process tests prove that a launcher with a mismatched parent is rejected before stdout access and receives no process-group signal; a still-live direct `Popen` child is stopped through its handle after a missing first identity or unverifiable controller identity, including bounded `terminate()` to `kill()` escalation; and an already-exited child with a reused numeric PID does not cause the replacement to be signaled, adopted, reaped, or treated as clean tree cleanup. Every unverified-identity case remains incomplete and retains artifacts. Tests make no provider, API, HTTP, database, PostgreSQL, or external-process calls.

### Scope boundary

This phase hardens only the test harness's outer process ownership boundary. It does not change provider behavior, API or PostgreSQL semantics, process-group signaling after a valid pin, acceptance gates, or any existing time budget. Stopping the direct launcher via its `Popen` object does not prove that its descendants exited.
