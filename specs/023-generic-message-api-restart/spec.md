# Feature Specification: Generic Provider Message Across API Restart

**Feature Branch**: `[023-generic-message-api-restart]`

**Created**: 2026-10-15

**Status**: The latest recorded source-bound generic-message runs are Codex R17 PASS, OpenCode R17 PASS, and Claude R17 FAIL at `observer_audit`; none binds the current main tree. R18 runner material is prepared but current-source live acceptance remains open for T023/T024. This specification includes offline MCP-outcome and AG-UI shutdown diagnostics; they do not retroactively explain closed live failures or change the production API/schema.

**Input**: User goal includes real-provider generic messages observed through AG-UI and retained across replacement of the production API process. This supplement is separate from Spec022's two-question/answer journey.

## User Scenarios & Testing

### User Story 1 — Verify provider messages across API replacement (Priority: P1)

As a maintainer, I need a bounded real-provider acceptance that sends one generic message before an API worker replacement and one after it, so I can verify the production API persistence path and the redacted live AG-UI notification on both sides of the restart.

**Why this priority**: Spec022 proves the question/answer ledger journey across API replacement. It does not prove generic `send_message` inbox persistence or the message event projection. The production message path crosses provider MCP, the inbox API/database, delivery hooks, and AG-UI.

**Independent Test**: For one explicitly selected native provider, create one real sender pane and one real receiver pane through production CAO on API A. Send one bounded task into the existing sender pane, observe exactly one successful, provider-authored `send_message` dispatch through the instrumented bundled MCP server and one delivered inbox row plus its live redacted AG-UI event. Stop only owned API process A; retain the same private `HOME`, PostgreSQL targets, sender pane, and receiver pane. Start production API B on the same port and target. Verify both original terminal IDs are still ready without recreating either pane, GET the same first inbox row and privately compare its body and immutable fields, then send one distinct bounded task into that same sender pane and verify the second MCP dispatch, inbox row, and live event. Require successful provider outcomes and scoped cleanup. Offline helper tests run without launching a provider, HTTP worker, network request, or database.

**Acceptance Scenarios**:

1. **Given** API A runs the production lifespan with the real plugin registry, the native sender profile grants the CAO MCP server, and the source-bound observer accepts only `send_message` dispatches, **When** the harness sends the fixed pre-restart Unicode task into the original sender pane, **Then** the provider makes exactly one real call through the bundled MCP server's production dispatch, the production API commits one inbox row to the isolated PostgreSQL target, the ready receiver is actually delivered the message, and the subscribed production AG-UI stream emits one matching `TEXT_MESSAGE_CONTENT` notification with an empty `delta`, expected sender/receiver/orchestration metadata, and no body.
2. **Given** the first call completed and its row/event were observed, **When** API process A is stopped and API B is started on the unchanged port, `HOME`, and PostgreSQL target, **Then** the exact original sender and receiver terminal IDs remain present and ready. The replacement API GET returns the same row ID, sender, receiver, creation timestamp, and exact body when compared only in private memory. Status may advance through its real lifecycle; no version is asserted because the current inbox contract has no version field. The harness must fail if either pane is missing or not ready; it must never recreate a pane to recover a passing result.
3. **Given** API B is healthy, both original panes are still ready, and the first row was found through B's GET route, **When** the harness sends the distinct post-restart Unicode task into the same sender pane, **Then** that provider makes exactly one further real `send_message` call through the same instrumented production dispatch, B persists one new row, and the B live AG-UI stream emits one matching redacted notification.
4. **Given** both provider actions and the API replacement finish, **When** the acceptance receipt is written, **Then** it records only bounded provider/run status, row-count and identity-match booleans, event-type/redaction booleans, process/restart counts, and cleanup status. It contains neither message bodies nor provider transcripts nor auth material.
5. **Given** the generic-message acceptance is disabled, **When** existing provider acceptance lanes run, **Then** Spec022 behavior and receipts remain unchanged.

### User Story 2 — Preserve Claude's staged credential after cleanup (Priority: P1)

As an operator, I need the Claude credential copy used by the isolated generic-message run to remain recoverable after its temporary workspace is removed, so cleanup does not discard the only run-private copy and does not alter the operator's source profile or auth stage.

**Independent Test**: Offline fake-only tests exercise the closed `interaction` and `message` archive layouts and the real message-run cleanup seam. For Claude, the test proves the credential copy is archived only after owned process/resource shutdown and before workspace removal; an archive or shutdown uncertainty fails cleanup and retains the workspace. The source profile and staged auth sentinels remain byte-for-byte unchanged. The Codex and OpenCode paths do not archive or otherwise change behavior. Real Claude message acceptance remains a separate gated run.

1. **Given** Claude authentication was staged into the message run's private `home/.claude` layout and all owned API, provider, terminal, tmux, and target resources are verified closed, **When** successful cleanup is about to remove `workspace`, **Then** cleanup preserves the exact staged `.credentials.json` in the unique run archive outside `workspace`, then removes `workspace` only after the archive is verified.
2. **Given** process/resource shutdown or credential archival is incomplete or uncertain, **When** cleanup runs, **Then** it fails closed and retains `workspace`; it does not report cleanup success or emit credential contents or auth/archive paths.
3. **Given** Codex or OpenCode is selected, **When** cleanup runs, **Then** this Claude-only archive path is not used and existing provider cleanup behavior is unchanged.

## Edge Cases

- The inbox row is committed but the receiver is not deliverable, so `post_send_message` does not fire: fail this acceptance; a queued row alone is not evidence of an AG-UI notification.
- The API route logs an immediate-delivery failure but returns its already committed row: require the separate live event and do not infer success from HTTP 200.
- Either original terminal is missing or not ready through API B: fail; do not recreate it, fabricate an event, or invoke the inbox endpoint directly from the harness.
- The replacement process reads a different database target or source root: fail before the second call.
- The AG-UI event has no inbox-row correlation identifier in the current contract. Match only in the isolated run by the expected sender, receiver, `send_message` orchestration type, event type, and one observed notification for the action. Do not claim a row-ID-to-event-ID mapping.
- Delivery status changes between reads: accept a valid existing status and compare immutable row fields; do not require status equality.
- Unicode is altered or lost in provider MCP/API/database/GET round-trip: fail the private body comparison. Never include the body or a body hash in receipts.
- A provider lane that was not selected is reported N/A. A selected provider whose binary, staged authentication, source-bound MCP server, CAO-server profile grant, dispatch observer, or initialization fails is a failure, not N/A; never fall back to a fake call or another provider.
- Cleanup cannot stop or positively identify every owned API process: fail, retain the PostgreSQL target context and workspace, and preserve private state. Stop the private tmux server only through its exact short socket after owned terminal deletion and a verified empty session list; pin its socket inode and server PID/start-time, then prove that PID exited before unlinking the socket or removing its root.
- The operator has `CAO_ENABLE_SENDER_ID_INJECTION=false`: the MCP child still receives an explicit `true`, so the source-defined suffix remains stable for private body comparison without changing parent environment or production behavior.
- Claude auth preservation copies only the staged `.credentials.json` from the fixed message layout `workspace/home/.claude/.credentials.json` into the per-run archive at `run_root/claude-auth-archive/<run-key>/credentials.json`. The archive stays outside the disposable `workspace`, uses owner-only directories and files, and is never promoted into the operator's provider home. A prior or future provider logout/rotation cause is not inferred from this cleanup behavior.

## Requirements

### Functional Requirements

- **FR-001**: The generic-message acceptance MUST be a separate, explicit opt-in lane and MUST NOT alter Spec022's two-question journey or its default behavior.
- **FR-002**: Each accepted provider lane MUST use its native installed provider path and source-bound CAO/MCP code, with private staged authentication. For OpenCode, each profile MUST be installed through the production profile-install route before requesting creation of its corresponding native terminal; an installation failure MUST prevent that creation request. The same native sender pane and receiver pane MUST span API A and B. Provider lanes MUST run serially and have separate Codex, Claude, and OpenCode gates; one provider's result MUST NOT stand in for another.
- **FR-003**: Each phase MUST contain exactly one actual provider-authored MCP `send_message` call. The harness MUST NOT POST a substitute inbox message, forge an event, retry/resend a message to obtain a pass, or count a previous provider-matrix result as this acceptance.
- **FR-004**: The native sender profile MUST grant the CAO MCP server. The private MCP observer MUST wrap the bundled FastMCP server's public dispatch, reject every non-`send_message` dispatch before delegation, delegate each accepted call once to the original registered production dispatcher, and preserve its exact result or raised error. It MUST record only the fixed tool name and a fixed status category; it MUST never record arguments, message text, results, exceptions, IDs, or credentials. Any rejected dispatch or audit-write failure invalidates the acceptance. For provider adapters without a native per-tool grant, the harness MUST NOT claim that `ListTools` is filtered; the dispatch observer is the enforced boundary. Question-flow tools and unrelated provider tools remain outside this journey.
- **FR-005**: The actual MCP call MUST reach the production `POST /terminals/{receiver_id}/inbox/messages` path and create a row in the isolated PostgreSQL inbox table. After replacement, the production GET route MUST return the first row again.
- **FR-006**: API A and B MUST serve production routes over one unchanged target and use the same private `HOME`, port, source root, and short owner-only `TMUX_TMPDIR`/`TMPDIR`; inherited `TMUX` MUST be cleared. Each MUST load the real production plugin registry through the same registry loader used by API lifespan. The harness MUST confirm every owned API process has exited before releasing the caller-owned PostgreSQL target context. It MUST retain the same private tmux server and both native panes across A→B, without a global tmux command. After B, it MUST delete only the exact sender/receiver IDs owned by this run; it may stop the private tmux server only after those deletions, a successful zero-session query, and a same-owner socket/PID/start-time identity check. It MUST verify the pinned PID exited and the liveness source proves absence before removing the private socket root. It MUST NOT inject an event or use a mocked publisher.
- **FR-007**: The harness MUST subscribe to each production AG-UI stream before its provider action and require exactly one actual `TEXT_MESSAGE_CONTENT` notification for that isolated action, with `delta` empty and exact expected sender/receiver/`send_message` metadata. Bodies and provider call arguments MUST remain private. Because the existing event projection exposes no inbox-row ID and uses the receiver ID as `message_id`, correlation is limited to sender, receiver, orchestration type, event type, and the single live notification; no ID identity or historical replay claim is allowed.
- **FR-008**: The first inbox row MUST be matched after restart by its existing ID, sender, receiver, creation timestamp, and exact body in memory. The current row contract has no version field; no version guarantee MUST be stated. Status may change and MUST be validated against the existing status vocabulary rather than compared for equality.
- **FR-009**: Since the AG-UI projection does not expose the inbox row ID, the acceptance MUST state that correlation is limited to the isolated sender/receiver/action and its single live notification; it MUST NOT assert event-ID/inbox-ID identity or historical replay after process replacement.
- **FR-010**: Receipts and diagnostics MUST exclude message bodies, body hashes, provider transcript text, access tokens, API keys, and auth/cache paths. The fixed Unicode body check MUST occur only in private process memory. The parent MUST require a fixed owner-only positive `.complete` seal created with exclusive/no-follow semantics only after normal MCP server return, observer close, and a clean audit-write state; absent, partial, unexpected, or unsafe seals fail acceptance.
- **FR-011**: Offline tests MUST cover strict message-row checks, redacted event checks, provider opt-in and CAO-server profile gates, production MCP observer delegation/rejection/result preservation, audit-write failure and positive-seal handling, source-defined sender suffixes, actual `DatabaseTarget` identity shape, same-target API A→B configuration, unchanged terminal identities, API-exit/target-context ownership, isolated A/B tmux environment, exact private tmux teardown, stage-specific failures, and receipt privacy without starting a provider, HTTP worker, network request, or database.
- **FR-012**: This acceptance MUST NOT claim that an externally installed or separately running `cao` 500-callsite has been fixed; only the source-bound path actually exercised may be reported.
- **FR-013**: For the `claude_code` lane only, once per-run authentication staging begins, cleanup MUST preserve the staged `.credentials.json` from the fixed message layout `workspace/home/.claude/.credentials.json` before deleting `workspace`, and only after all owned API/provider processes, terminal/tmux resources, and the caller-owned target context are positively confirmed closed. The archive MUST be created under `run_root/claude-auth-archive/<run-key>/credentials.json`, outside `workspace`, with a unique run key, owner-only directories (`0700`), an exclusive no-follow regular file (`0600`), and source identity revalidation. The archive helper MUST accept only the closed layouts `interaction` (`root/claude-home/.credentials.json`, unchanged default) and `message` (`root/home/.claude/.credentials.json`); it MUST NOT accept arbitrary paths. Shutdown, source, or archive uncertainty MUST fail cleanup and retain `workspace`. The runner MUST NOT modify the original auth source or auth stage, automatically promote archived bytes into a provider home, or expose credential bytes or archive/auth paths in receipts or logs. Codex and OpenCode cleanup MUST remain unchanged.

## Key Entities

- **Inbox row**: Existing persisted message with `id`, `sender_id`, `receiver_id`, `message`, `status`, and `created_at`; it has no version field.
- **AG-UI notification**: Existing `TEXT_MESSAGE_CONTENT` projection carrying metadata and an empty `delta`; its `message_id` currently contains the receiver ID, not the inbox row ID.
- **API A / API B**: Two sequential, test-controlled production API processes using the same private `HOME`, loopback port, source root, and isolated PostgreSQL target context.
- **Native panes**: The original provider sender and receiver terminal IDs created through production CAO on API A. Both must remain intact and ready through API B; failure does not trigger recreation.
- **Provider action**: One bounded prompt delivered through production terminal input to the same sender pane, resulting in exactly one provider-authored MCP `send_message` call.
- **MCP observer**: Test-only dispatch wrapper installed only in the source-bound bundled MCP subprocess. It calls the original production FastMCP dispatcher and emits a private allowlisted audit record without observing or serializing arguments, output, or exceptions.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Exactly two real provider `send_message` MCP calls are observed in a passing run: one before and one after exactly one owned API-worker replacement.
- **SC-002**: Both calls produce exactly one new inbox row each; the first row's ID, sender, receiver, creation timestamp, and privately compared body are stable across the API replacement.
- **SC-003**: One real `TEXT_MESSAGE_CONTENT` event is observed for each action from the corresponding live worker stream, with empty `delta` and matching safe metadata.
- **SC-004**: Both provider actions succeed through the same retained native sender and receiver panes; both original IDs remain ready through API B; only the two owned API processes and exact owned terminal IDs are cleaned up.
- **SC-005**: Offline harness tests finish without provider launches, API worker starts, network requests, or PostgreSQL access.
- **SC-006**: The sanitized receipt contains no body, body hash, transcript fragment, credential, or auth/cache path.
- **SC-007**: For the Claude lane, offline tests prove archive creation uses only the fixed `message` layout and a unique run key after verified shutdown; archive files are owner-owned mode `0600`, their directories are mode `0700`, the workspace is removed only after verified archival, and any archive/shutdown uncertainty retains the workspace. Fake source/stage sentinels are unchanged, receipts contain no secret or path sentinel, and Codex/OpenCode do not enter this branch.

## Assumptions

- Each provider action targets a real, existing, deliverable receiver in the isolated run; the actual delivery path is required because the current observer runs from the post-send delivery hook.
- The private fixed bodies contain Spanish and Unicode characters (`café`, `日本語`) and may include a non-secret run marker. The normal provider-side sender-ID injection behavior is left unchanged; the MCP child sets the existing flag to `true`, and private body validation checks the exact source-defined Unicode content plus sender-ID suffix without rewriting the production call.
- Current production API/model/event contracts remain unchanged. This supplement specifies a test-only acceptance, not durable AG-UI history or provider wait/recovery behavior.
- Codex/Claude/OpenCode are separately gated. Unselected provider lanes are reported N/A and never counted as passing evidence. A selected provider's auth, binary, initialization, CAO-server profile grant, observer dispatch, or API setup failure fails that lane.

## Coverage Boundary

This proves two real provider-to-inbox actions around one owned API process replacement, using the same original native panes and the live redacted event for each action. The Claude credential archive requirement protects only the run-private staged copy; it neither changes original credentials nor proves credential rotation or a cause for any earlier logout. It does not prove historical AG-UI replay after replacement, correlation between inbox row IDs and AG-UI event IDs, interrupted-stream recovery, question/answer wait semantics, external installed-CLI callsite behavior, or provider lanes that were not explicitly selected and completed.

## Addendum — Claude MCP Normal Close Before Terminal Deletion (2026-10-16)

This addendum addresses the failed Claude R15 generic-message run whose safe receipt showed two completed provider calls, two persisted rows, two redacted AG-UI events, one API replacement, and an otherwise successful cleanup, but no normal MCP completion seal. The receipt does not expose the MCP child's signal or exit status. The exact event that stopped the child is unknown; this requirement is based on the verified cleanup ordering and the source-bound seal contract, not a claim that a particular live signal caused the failure.

### Functional Requirements

- **FR-014**: For the explicitly selected `claude_code` generic-message lane only, cleanup MUST request graceful exit of the owned sender terminal through the existing production `POST /terminals/{sender_id}/exit` route before deleting that sender terminal. The existing six-second MCP audit-completion allowance MUST be one shared nominal socket-timeout and polling budget for the exit request and the wait for the existing positive `.complete` seal. Cleanup MUST compute a monotonic deadline before the request, pass the remaining budget through Requests/urllib3 total/connect/read timeouts, and give only the remaining budget to the existing seal poller. It MUST NOT reset, extend, or follow the wait with a second audit wait. Because the Requests/urllib3 timeout contract does not guarantee a strict wall-clock deadline under DNS or continuous response streaming, this shared budget MUST NOT be described as a hard wall-clock cap. The only acceptable evidence of normal MCP closure is the source-bound producer's valid `.complete` seal, created after normal `FastMCP.run()` return, observer close, and a clean audit-write state. A successful HTTP response from `/exit` alone MUST NOT count as closure because the production route may return HTTP 200 when the provider refuses to send exit input in an interactive wait state. Cleanup MUST preserve any audit failure already recorded before cleanup; a later valid seal MUST NOT clear that prior failure. A seal MUST be observed while the shared budget is still positive: a poller's final read at zero timeout, or an observation returned after the deadline, MUST NOT qualify. If the request errors, times out, returns a non-success status, or a valid seal has not been observed before the shared deadline, cleanup MUST retain audit failure and still attempt deletion of the exact owned terminal IDs and the remaining existing resource cleanup. It MUST NOT create or accept a seal on behalf of the producer. It MUST NOT send `/exit` to the receiver or change Codex/OpenCode cleanup ordering.

### Acceptance Scenario

- **Given** the Claude sender has completed its permitted message actions and its two allowlisted observer records are present, **When** cleanup requests the provider's existing graceful exit and waits within the current shared nominal six-second request-timeout-plus-polling budget, which is not a strict wall-clock deadline, **Then** a passing cleanup requires the real observer producer's valid `.complete` seal before sender deletion. If exit is refused, fails, times out, or the producer does not close normally within that same bound, cleanup continues deleting only owned IDs but the run remains failed at `observer_audit`; HTTP 200 and two successful rows without the seal are insufficient.

### Success Criterion

- **SC-008**: A Claude generic-message run can pass observer audit only when graceful-exit request succeeded and the existing valid `.complete` seal is observed within one shared nominal six-second request-timeout-plus-polling budget before sender deletion; this budget is not a strict wall-clock deadline. The bound is not extended, no second wait is added, and every exit/request/seal failure remains a failed run even when terminal cleanup succeeds.

### Limits

This does not establish the exact shutdown signal or exit event from the previous live Claude run, and it does not diagnose the prior auth logout/rotation. It adds no provider prompt, tool call, API route, database operation, model change, message retry, or new time allowance. The authenticated provider process, API/database lifecycle, two-message criteria, AG-UI assertions, archive guard, and all existing cleanup ownership checks remain unchanged.

## Addendum — Receipt-Aware Sender Prompt for All Supported Providers (2026-10-16)

The frozen R16 source confirms that the `codex`, `claude_code`, and `opencode_cli` adapters all set `requires_turn_receipt = True`. Production `terminal_service.send_input` passes each message through the provider's `prepare_input`; `BaseProvider.prepare_input` appends one CAO-generated turn-receipt envelope, and the result validator rejects a bare `sent` reply without the active receipt. The Spec023 sender prompt currently describes this input and final-reply contract only for Codex. Claude and OpenCode are instead told to accept only a bare phase trigger and to return only `sent`, which contradicts their real production input/result contracts.

The R16 Claude run failed in `phase_a` before an observed MCP dispatch, and its run retained no provider transcript or pane output. The R15 Claude run completed two dispatches with the same prompt text. Therefore this requirement corrects a verified contract mismatch; it does not establish that the mismatch caused either live outcome.

### Functional Requirements

- **FR-015**: In the generic-message lane, every selected sender whose production adapter requires a turn receipt (`codex`, `claude_code`, or `opencode_cli`) MUST be instructed that the entire input is exactly one of the two fixed phase triggers, optionally followed only by the single CAO-generated turn-receipt envelope appended by production `prepare_input`. No other caller-supplied text, second trigger, or additional content is permitted. The exact receipt-bearing envelope is orchestration metadata for the same trigger, not a new user task or permission to send another MCP call.
- **FR-016**: For those receipt-bearing senders, the prompt MUST require the exact receipt from that envelope on the final visible line after the fixed `sent` acknowledgement. It MUST NOT instruct a bare `sent` result. Existing provider result validation remains authoritative; the acceptance MUST continue to reject a bare result and MUST NOT change message bodies, recipient, model, tools, call count, timing budgets, provider initialization, or API behavior.

### Acceptance Scenario

**Given** one of the three supported receipt-bearing provider adapters and one exact phase trigger, **when** production `prepare_input` appends its actual per-turn envelope, **then** the selected provider's sender prompt allows that trigger plus only that envelope, disallows arbitrary extra text or a second trigger, and requires the same exact receipt as the final visible line. The production receipt guard rejects bare `sent` and accepts the active exact receipt. Both fixed phase triggers remain present and each still permits exactly one `send_message` call.

### Success Criterion

- **SC-009**: An offline contract test exercises the real `prepare_input` and result guard for Codex, Claude Code, and OpenCode CLI for both fixed phase triggers. Every generated envelope is permitted by the corresponding prompt; arbitrary additional caller text and a second trigger are explicitly disallowed; the prompt requires the receipt as the final visible line; bare `sent` is rejected; the exact active receipt is accepted. The test makes no provider launch, API/network request, or PostgreSQL access.

This addendum preserves the prior R16 failure and R15 successful-call evidence without attributing a live cause. Generic-message live acceptance remains separately gated and pending for Claude.


## Addendum — Closed Claude Exit/Seal Diagnostic (2026-10-16)

The closed Claude R17 generic-message run completed its two observed `send_message` calls, two inbox rows, two redacted AG-UI events, and one API replacement, then failed at `observer_audit` with cleanup reported as pass. Its genuine MCP `.complete` marker was absent. The receipt and retained safe evidence do not identify the `/exit` HTTP outcome, child exit status, or the precise event that prevented normal `FastMCP.run()` return. This addendum adds private diagnostic observability only; it does not assign a cause to that run or alter its preserved evidence.

### Functional Requirement

- **FR-017**: In a future explicitly selected `claude_code` generic-message run, the existing graceful-exit helper MAY write a separate owner-only `claude-exit-diagnostic.json` sidecar in that run's private root. Its v2 closed schema MUST contain only a fixed schema identifier and closed enum fields for exit-request outcome (`not_attempted`, `request_error`, `http_200`, `http_other_status`), seal-poll result (`not_checked`, `no_seal_at_poll_return`, `seal_found_poll_return_before_deadline`, `seal_found_poll_return_after_deadline`), and shared nominal-budget outcome (`within_nominal_budget`, `exhausted_before_request`, `exhausted_after_request`, `exhausted_after_poll`). The poll-result timing describes when the boolean poll call returned, not the exact instant the seal file was read. It MUST NOT contain status bodies, exception class or text, paths, IDs, URLs, headers, environment, prompt/provider output, or credentials. The sidecar MUST remain separate from the safe receipt and observer audit and MUST NOT be used as acceptance evidence. Failure to write it MUST NOT change the existing result, cleanup decision, call/DELETE order, exception identity, or nominal six-second budget. The implementation MUST preserve the single existing `/exit` POST, genuine source-bound `.complete` requirement before sender deletion, sticky audit failure, and all owned-resource cleanup behavior. A diagnostic value of `http_200` MUST NOT be interpreted as normal MCP closure; `not_checked` MUST NOT be treated as evidence that a seal was absent.

### Acceptance Scenario

- **Given** the selected Claude lane is cleaning up after its two provider-authored calls, **when** the existing one-shot `/exit` attempt and remaining seal poll finish, **then** any emitted sidecar records only the three closed outcomes above, while the real `.complete` seal remains the sole normal-close evidence and every failed request, missing/late seal, or exhausted budget remains failed at `observer_audit`. Exactly one sender exit request and the same owned DELETEs remain in order. Non-Claude lanes emit no Claude diagnostic.

### Success Criterion

- **SC-010**: Offline fake-only tests exercise request success without seal, request error, non-200 with a genuine seal, request-budget exhaustion, normal seal within budget, and a successful poll result returned after the deadline. They assert one POST, unchanged DELETE order and functional results, exact v2 schema/enums with no sentinel leakage, best-effort sidecar failure that cannot change acceptance, and no sidecar on Codex/OpenCode cleanup. Tests make no provider launch, API/network request, or PostgreSQL access.

### Limits

This diagnostic can classify only the existing request/marker/budget boundary. It cannot identify a child signal, prove why the provider refused or accepted exit input, establish the exact event that ended MCP, or retroactively explain the closed R17 run. An absent or malformed diagnostic sidecar is unavailable evidence, never a passing result.


## Addendum — MCP Result Outcome and AG-UI Stream Shutdown (2026-10-16)

The closed Spec023 phase-A failure's `completed` observer status meant only that FastMCP returned without propagating an exception. The `row_count` and `event_count` receipt fields are acceptance counters, not raw observations of HTTP/SQL/SSE results, so the primary live failure remains unknown. A separate offline reproduction showed that the production send helper can return a logical `success: false` result without raising. The AG-UI reader also needs a bounded shutdown rule for the observed `AttributeError` at the response iterator boundary.

### Functional Requirements

- **FR-018**: The private MCP observer MAY classify a returned `send_message` result using only a strict boolean `success` field and MUST persist only a closed `success`, `failure`, or `unknown` outcome enum alongside the fixed tool/status fields. Dispatcher exceptions and rejected calls MUST use `unknown`; no returned content, error text, arguments, body, IDs, or credentials may be persisted. Acceptance MUST require the expected completed calls to have `outcome: success` and reject missing, unknown, failed, or malformed audit records.
- **FR-019**: The AG-UI capture MAY treat `AttributeError` from advancing the response iterator as expected shutdown only when its explicit stop event is already set. An iterator error before stop and any `AttributeError` outside that iterator-advance boundary MUST remain a failed stream. Existing request and join limits MUST remain unchanged.

### Success Criteria

- **SC-011**: Offline tests exercise actual FastMCP `ToolResult` structured results for strict boolean success and failure, unknown results, dispatcher exceptions, exact audit schema, acceptance rejection of non-success outcomes, and receipt/JSONL privacy. No provider, API worker, network, or database is started.
- **SC-012**: A fake response proves that `AguiCapture.stop()` closes the response while the reader is blocked, releases the iterator, and leaves the capture stopped without marking it failed. Separate tests prove unexpected iterator and non-iterator `AttributeError`s remain failures, including an error outside the iterator boundary after stop. The test remains offline.

### Limits

These diagnostics improve future attribution only. They do not identify which phase-A acceptance predicate failed in the preserved run, prove an HTTP response or PostgreSQL insert for that run, or resolve either historical external CAO job. The iterator rule intentionally treats any `AttributeError` raised by `next()` after explicit stop as shutdown; it does not infer the exception's underlying cause. Any later live run requires a fresh source freeze and reviewed source-bound launcher.


## Addendum — Bind the Generic Message Proof Path (2026-10-19)

The current Spec023 inner runner hashes the MCP dispatcher, the API route module, the AG-UI projector, and the plugin event publisher. It does not yet bind the production modules that persist the inbox row, deliver the message to the receiver, or retain the event observed by AG-UI. A source-bound receipt must cover those direct owners as well as the existing route and projection files before it can support a current-source live claim.

### Functional Requirement

- **FR-020**: The source-bound generic-message runner MUST hash the production source modules that own inbox persistence and reads, message delivery and terminal input, readiness/provider/backend selection, plugin hook dispatch, event-log retention, and AG-UI projection. The required source set MUST include `clients/database.py`, `models/inbox.py`, `models/terminal.py`, `services/inbox_service.py`, `services/terminal_service.py`, `services/status_monitor.py`, `services/event_log_service.py`, `plugins/__init__.py`, `plugins/base.py`, `plugins/events.py`, `plugins/registry.py`, `backends/registry.py`, `backends/tmux_backend.py`, and `providers/manager.py`, alongside the existing source-bound route, MCP, database-target, event-publisher, AG-UI, and orchestration modules. Each required leaf MUST be a regular non-symlink file and MUST be re-hashed at the existing source-integrity checkpoints. Missing, unreadable, symlinked, or changed source MUST fail preflight or invalidate the run before a passing receipt is returned.

### Success Criterion

- **SC-013**: Offline fake-only tests prove every required generic-message production source leaf is present in the binding inventory and that missing, symlinked, or post-snapshot mutation of each newly bound source leaf is rejected before provider authorization, port selection, API startup, or provider startup. The tests MUST make no provider, HTTP/network, PostgreSQL, or credential-file access.

### Limits

This closes the inner runner's direct message-proof source inventory. It does not create or validate the separate immutable full-tree source freeze required by T044, does not authorize a provider run, and does not make historical receipts apply to the current tree.
