# Implementation Plan: Generic Message Acceptance Across API Restart

**Branch**: `[023-generic-message-api-restart]` | **Date**: 2026-10-15 | **Spec**: `spec.md`

**Status**: Integrated test harness. Latest recorded source-bound generic-message runs are Codex R17 PASS, OpenCode R17 PASS, and Claude R17 FAIL at `observer_audit`; none binds the current main tree. R18 runner material is prepared, but fresh source-bound provider acceptance remains open. Offline stream and MCP-result diagnostics do not reclassify historical provider results.

## Summary

Add a separate, explicit live-provider acceptance lane for two real `send_message` calls around replacement of a production CAO API process. The lane preserves the original native sender and receiver panes, private `HOME`, port, source tree, PostgreSQL target context, and production plugin path across A→B. It validates the first inbox row through API B, then observes a second call, row, and live redacted AG-UI notification. Offline helper tests exercise only pure validators, gates, receipt projection, observer delegation, and the new Claude cleanup/archive composition using synthetic credentials.

## Verified Source Boundaries

- `test/fixtures/cao_server.py::_start_cao_server` starts the real `cli_agent_orchestrator.api.main` process and allows caller-owned `database_targets`. Separate A/B handles can therefore own API processes while the caller retains one database-target context. `CaoServer.stop` scopes cleanup to that server handle/process group; the future lane must stop A before creating B and preserve the shared HOME/target context.
- `test/e2e/test_real_provider_matrix.py` provides native terminal creation/readiness and private auth/profile helpers. Its current “do not call tools” matrix prompt is not message-call evidence and must not be reused as the action.
- `src/cli_agent_orchestrator/mcp_server/server.py::send_message` is registered on the bundled `FastMCP` object; `main()` calls `mcp.run()`. A test-only wrapper can instrument the public dispatch method and delegate to the original bound method. It must not replace the registered tool implementation or synthesize MCP calls.
- `src/cli_agent_orchestrator/api/main.py` commits through `POST /terminals/{receiver_id}/inbox/messages` and returns GET rows with `id`, `sender_id`, `receiver_id`, `message`, `status`, and `created_at`; there is no version field. The harness uses provider calls for writes and the production GET route for reads.
- `DatabaseTarget.identity` is the actual source-defined five-item tuple and is hashed in memory only. `src/cli_agent_orchestrator/utils/orchestration.py::_send_message_impl` appends its normal sender-ID suffix when `CAO_ENABLE_SENDER_ID_INJECTION` is true (the source default); the source-bound MCP child explicitly sets that existing environment flag to `true`, and private body comparison includes the exact source-defined suffix.
- `src/cli_agent_orchestrator/clients/tmux.py::_tmux_server_liveness` distinguishes proven absence from live/unknown. API A, B, and recovery use one owner-only short `/tmp` root with `TMUX_TMPDIR` and `TMPDIR`; inherited `TMUX` is cleared. Cleanup confirms all API handles exited before closing targets. It deletes only owned terminal IDs, requires an empty private session list, pins the private socket inode and tmux server PID/start-time through a pidfd, uses `kill-server` only with that socket path, and removes the root only after the PID exits and the liveness helper reports absence.
- `src/cli_agent_orchestrator/plugins/builtin/event_log_publisher.py::on_post_send_message` emits routing/orchestration metadata without a body. `src/cli_agent_orchestrator/services/agui_stream.py` projects generic `send_message` events to `TEXT_MESSAGE_CONTENT` with empty `delta`; the current projected `message_id` contains the receiver ID, not the inbox row ID. Correlation therefore stays limited to one isolated action and its sender/receiver/type metadata.
- `test/live_provider/message_api_restart_live.py` stages selected auth under `RunState.home = run_root/workspace/home`, starts the API server with that same home, and removes the disposable `workspace` only after owned cleanup succeeds. `test/fixtures/cao_server.py::_stage_live_provider_auth` copies selected files into that home; the child environment points Claude at `home/.claude`. The existing `test/live_provider/claude_auth_archive.py` uses descriptor-relative, no-follow identity checks and exclusive private archive creation, but its current `interaction` layout is `root/claude-home/.credentials.json` and the helper is not wired into the message cleanup path. The message lane therefore needs only the second fixed layout, `root/home/.claude/.credentials.json`, while preserving the existing layout as the default.
- Graphify links `InboxService` to delivery/status paths and `EventLogPublisher` to the `post_send_message` hook and AG-UI tests. These generated edges were checked against the source methods above. Graphify did not provide a unique direct path between the ambiguous `send_message` symbol and the publisher, so source inspection remains authoritative.

### AG-UI stream execution boundary

The Work ledger message SSE endpoint intentionally remains open until the client disconnects. Its initial replay read and every subsequent PostgreSQL poll construct `WorkQueries` and read `WorkRepository.read_snapshot` inside `asyncio.to_thread`; both configuration-file access and synchronous SQL must stay off the API event loop. `ConfigurationError` is mapped to the same safe `work_store_unavailable` 503 as other verified-store failures. Stream integration tests use a loopback Uvicorn server and HTTPX streaming so they can consume frames before the response completes. Starlette's synchronous in-process `TestClient` waits for the ASGI response to complete before returning from the request context, so it cannot return a response for this infinite stream. Tests record event-loop, query-factory, and query thread identities and retain owner revalidation, replay, retention-gap, cursor, and API replacement coverage. The composition test exercises the Work-specific `cao.work.message` operation and its `work.message.sent` ledger event through that stream. This does not establish an equivalence or mapping between the Work operation and Spec023's generic MCP `send_message` interface.

## Architecture and Data Flow

1. A private standalone driver validates one explicitly selected provider, private staged auth, fixed source root, the native profile's CAO-server grant, and the source-bound dispatch observer. Unselected lanes are N/A; once a provider is selected, any missing binary/auth/config or initialization failure is a failed run.
2. One caller-owned isolated PostgreSQL target context and one private HOME outlive both API handles. Production API A starts on a selected loopback port and loads its real registry through normal lifespan.
3. Write the receiver profile, install it through the production route when using OpenCode, then ask API A to create the receiver's initial native terminal. After readiness, write and, for OpenCode, install the sender profile before creating its child terminal with the receiver as caller. Reject a non-200 install response before the corresponding creation request. Both original IDs are retained across A→B.
4. Each MCP child keeps the canonical bundled launch command. A private, opt-in `sitecustomize` hook in that child environment wraps only the existing `FastMCP` instance's public dispatch when `FastMCP.run()` is invoked for the source-bound `cao-mcp-server`. The observer rejects all non-`send_message` dispatches before delegation, delegates each permitted call exactly once to the captured original bound dispatcher, and preserves the original return value or raised error. It writes only fixed `{tool,status}` records. Provider profile grants and observer dispatch checks are separate; this harness does not claim to filter `ListTools`. Native CLI structured traces are supporting evidence rather than the sole call-count source. Only normal MCP-server return followed by observer close and a clean audit-write state creates the fixed owner-only `.complete` seal; the parent requires that positive seal and exactly two successful records.
5. API A's AG-UI stream is subscribed before the first task is sent through production terminal input to the existing sender pane. The lane requires a successful observer record, successful provider outcome, one new inbox row, actual delivery, and exactly one redacted live event.
6. API A is stopped without deleting terminal IDs, restarting tmux, closing targets, or deleting HOME. API B starts on the same port/HOME/target/source. Both original terminal IDs must still be present and ready. API B's inbox GET must return the same first row ID and immutable fields; its body is compared privately.
7. API B's AG-UI stream is subscribed before one different fixed Unicode task is sent into the same original sender. The lane requires the second real dispatch, row, actual delivery, and live redacted event.
8. Cleanup deletes only the known owned terminal IDs through API B, stops B, confirms every API process exited, then stops only the private tmux server after proving its session list is empty and pinning its socket/PID identity. The PID must exit and the liveness helper must prove absence before removing the socket root. Only then may the caller-owned database target context and workspace be released. A live or unidentifiable API process preserves the target context and workspace; an unproven tmux shutdown preserves its socket root. Any such failure fails the run.
9. If Claude auth staging was attempted and cleanup has otherwise verified all owned API/provider processes, terminal/tmux resources, and the caller-owned target context as closed, preserve the exact staged credential from `workspace/home/.claude/.credentials.json` under `run_root/claude-auth-archive/<run-key>/credentials.json` before removing `workspace`. Reuse the portable helper with a closed layout enum (`interaction` keeps its current `claude-home` default; `message` selects only `home/.claude`) and the existing run root as the unique archive key. The archive root stays outside `workspace`; directories are mode `0700`, the exclusive archive leaf is mode `0600`, and no path or credential bytes enter receipts/logs. Any uncertain shutdown, missing/unstable credential, or archive error fails cleanup and retains `workspace`. This branch is Claude-only; it never modifies the source profile or auth stage and never promotes archived bytes into a provider home.

## Observer Contract

The observer belongs only to the private test harness. It captures `original_dispatch = mcp.call_tool` from the imported source-bound MCP server and wraps that method immediately before the original `mcp.run()` call. It must not wrap/replace `send_message`, alter arguments or results, parse or write message text, log exception values, or make any network calls. Unknown names are refused before reaching the captured dispatcher with a fixed safe error. For allowed calls, it records `send_message/completed` only after a normal production return or `send_message/failed` while re-raising the identical exception. The phase harness requires exactly two successful records total and fails on any extra dispatch; the observer does not filter the provider's tool-list response.

The private audit path must be owner-only, under the run root, and outside provider auth/cache directories. The audit record is a strict allowlist: fixed tool name and fixed status only. The lane treats missing, malformed, extra, or incomplete audit records as failure. Unit tests use an in-memory delegate and do not start FastMCP transport, providers, API processes, or PostgreSQL.

## TDD and Test Design

Write offline tests first, prove they fail against the absent candidate helpers, then implement only those helpers and the private observer module. Tests cover:

- strict inbox row identity/body comparison in memory, existing status vocabulary, no invented version;
- exact `TEXT_MESSAGE_CONTENT`, empty `delta`, and safe metadata with body-free receipt projection;
- per-provider opt-in defaults, invalid gate values, one-selected-lane rule, and failure (not N/A) after explicit provider selection;
- provider profile and observer call boundaries: the sender grants only the CAO server, unexpected dispatch names are rejected pre-delegation, `send_message` is delegated once with arguments unchanged, delegate result/error is preserved, and the audit never includes argument/result/error sentinels;
- A/B configuration uses identical source, HOME, port, and caller-owned targets; original sender/receiver IDs remain unchanged; a missing pane fails rather than creating another;
- receipt projection excludes message/body hashes, transcript, credentials, paths, and raw exceptions;
- test module's no-side-effect boundary (no provider/API/PostgreSQL starts, no HTTP, no `Popen` in offline tests).
- OpenCode profile installation precedes each terminal-creation request. Exercise the actual acceptance function and the provider's native-profile validation with fake transport and a runtime sentinel; reject failed installation before creation and preserve the other provider lanes. The frozen R10 path reproduced the missing receiver profile before runtime resolution; the private correction passed both profile gates without launching the CLI.
- Claude auth cleanup: cover only the closed `interaction` and `message` layouts with fake credential sentinels; prove exact source-file identity/bytes remain unchanged, archive file/directory ownership and modes, unique archive creation without overwrite, and no credential/path sentinel in diagnostics. Through the real message cleanup function with fake server/process/resource owners, prove archive happens after confirmed shutdown and before workspace deletion; failed/uncertain shutdown or archive preserves the workspace; Codex/OpenCode bypass the archive branch. These tests make no provider launch, HTTP call, API start, network request, or PostgreSQL access.

Run only the new offline file by explicit path, with provider flags cleared. Do not run broad provider collection, full API integration, model tests, PostgreSQL suites, global composition, or any actual provider request from this candidate. Before any real-provider run, have the root review the candidate diff and source bindings and explicitly authorize that separate live lane.

## Risks and Limits

- FastMCP transport must call the public `mcp.call_tool` method captured by the observer. The future test should source-bind this behavior to the installed project version and fail closed if that dispatch seam changes.
- The observer is a test-only Python startup hook. It is installed only in the MCP child environment, requires an explicit opt-in flag and private audit path, and is a no-op otherwise. No production source or provider configuration is changed by this candidate.
- The sender profile must grant only the CAO MCP server. For adapters without a native per-tool grant, the observer rejects every dispatch except `send_message`; no claim is made that the provider's `ListTools` output is filtered.
- Event projection has no inbox-row correlation key and no historical replay guarantee. The acceptance makes no claim beyond each observed live event's existing metadata.
- The fixed Unicode body plus its source-defined sender-ID suffix is checked only in process memory and never written to a receipt, observer audit, or public log. The MCP child's injection flag is pinned to `true` without changing the parent process environment.
- The archive is an additional sensitive credential copy retained for operator recovery. Its fixed path must be private and outside the disposable workspace; only the selected Claude run may create it. It is never printed, automatically restored, or merged into the original auth source/stage. This cleanup requirement does not establish that an earlier logout was caused by a deleted run copy.

## Rollout

Private candidate → offline tests → independent root review → root-controlled port after the active QEMU/freeze lane ends → source/hash review and offline checks → separately authorized serial provider runs. No commits or deployment are part of this feature.

## Addendum — Claude MCP Normal-Close Ordering

### Source-verified boundary

The current source already exposes `POST /terminals/{terminal_id}/exit`, which delegates to `terminal_service.exit_terminal_cli`, and Claude's provider returns `/exit`. That service deliberately refuses to send provider input while a terminal is in `WAITING_USER_ANSWER` or `WAITING_QUOTA`; the HTTP route nevertheless returns `{"success": true}` when that no-op returns normally. The existing observer producer writes `.complete` only after its wrapped `FastMCP.run()` returns normally and the observer closes without an audit write failure. The existing message cleanup currently deletes both exact owned terminals before it waits up to six seconds for that producer seal.

### Minimal candidate design

For `claude_code` only, the private candidate will call the existing `/exit` route for the owned sender before the sender's DELETE. It will start one monotonic six-second budget before that request, pass only the remaining time through Requests/urllib3 total/connect/read timeouts, and pass only the remainder to the existing seal wait. Requests/urllib3 documents that this is not a hard wall-clock guarantee under DNS and continuous response streaming; the candidate will make no stronger timing claim. It will not increase the phase deadlines, add a second wait, or infer closure from an HTTP status, audit rows, provider pane text, or process cleanup. The producer seal must be observed while the budget remains positive; a zero-time poller's final read or a result returned after the deadline cannot qualify. An exhausted budget sets the existing audit-failure flag even when the request returned HTTP 200 and the valid seal exists. It will continue the existing exact-ID DELETE and remaining cleanup on every path. The receiver receives no exit request; Codex/OpenCode keep the original delete-then-audit-check path.

### Offline proof design

The regression will call the actual candidate `_cleanup` function with fake API/process owners and an in-memory fake MCP server. Two synthetic `send_message/completed` rows will be written by the real observer. In the graceful path, the fake `/exit` response will invoke the same source-bound normal-return finalizer used by `sitecustomize`, which closes the observer and creates the real `.complete` marker through its no-follow/exclusive writer; cleanup must observe it before DELETE. In the delete-first baseline, fake DELETE terminates the child without normal finalization, leaving the same two rows but no `.complete`; cleanup must record audit failure. Additional cases will prove that HTTP 200 without a producer seal is insufficient, a non-success status is insufficient even when a valid producer seal exists, a previous audit failure remains sticky despite a valid seal, a valid seal returned after deadline expiry is rejected, a real Requests timeout exception with an exhausted fake budget still reaches DELETE and remains failed, only the Claude sender receives `/exit`, and the shared request-plus-wait allowance never exceeds six nominal seconds. Codex and OpenCode both retain delete-first cleanup. No provider, HTTP server, API worker, network, database, or actual credential source is used.

## Addendum — Receipt-Aware Sender Prompt (2026-10-16)

### Verified source boundary

- `src/cli_agent_orchestrator/services/terminal_service.py::send_input` calls `provider.prepare_input(message)` before sending the prepared text to the terminal.
- The Codex, Claude Code, and OpenCode CLI provider classes all require turn receipts. `BaseProvider.prepare_input` creates a per-turn receipt, appends the provider-neutral completion-receipt envelope, and remembers the active receipt. The result guard accepts the exact active receipt on its own line and rejects a bare `sent` response.
- `test/live_provider/message_api_restart_live.py::_build_sender_prompt` currently provides the receipt-envelope allowance and final-receipt instruction only when `provider == "codex"`; Claude and OpenCode receive instructions that contradict the production adapter contract.
- The frozen R16 source manifest is the source baseline for the private candidate. The live R16 phase-A failure does not prove this mismatch caused zero MCP calls; R15 completed both sends with the same prompt, and no R16 pane transcript is available.

### Minimal implementation

Update only `_build_sender_prompt` in the private Spec023 driver. Apply one provider-neutral receipt clause to the three supported IDs `codex`, `claude_code`, and `opencode_cli`. Permit only the exact trigger, optionally followed by the production CAO receipt envelope. Require the final visible reply line to contain the exact receipt after `sent`. Preserve the fixed receiver, the two exact message bodies, exactly one call per trigger, the no-repeat instruction, all gates, and all budgets. No production provider, API, MCP dispatch, model, authorization, or prompt-tool permissions change is in scope.

### Regression design

Extend the existing offline sender-prompt contract in `test/live_provider/test_message_api_restart.py` to instantiate the real `CodexProvider`, `ClaudeCodeProvider`, and `OpenCodeCliProvider`. For each provider and both source-defined triggers, call the production `prepare_input`, obtain its active receipt only in memory, and assert that the generated text is the exact production envelope appended to that trigger. Verify the prompt permits only this form, forbids arbitrary additional text and a second trigger, contains both fixed message/receiver instructions once, and requires the active receipt as the final visible line. Exercise the production receipt guard: bare `sent` is rejected and `sent` followed by the exact active receipt is accepted. Never print or persist the generated receipt.

The regression is marked `offline_database` and uses no provider process, endpoint, network, database, filesystem auth profile, or credentials. The expected RED against frozen R16 is the receipt-contract assertion for Claude/OpenCode; Codex remains covered by its existing behavior. The private GREEN changes only the Spec023 sender-prompt builder and the contract test.


## Addendum — Closed Claude Exit/Seal Diagnostic

### Source and Graphify boundary

The frozen R17 `test/live_provider/message_api_restart_live.py` binds `_graceful_claude_sender_exit_and_wait_for_seal` to one `/exit` request and one remaining-budget seal poll; `_cleanup` records a false result as sticky `audit_failed` before deleting only the owned terminal IDs. `test/live_provider/sitecustomize.py` emits `.complete` only after normal `FastMCP.run()` return and observer close. The R17 driver and sitecustomize leaf hashes and their manifest rows are recorded in `evidence/source-pins-r1.json`. The Graphify query for the exact graceful-exit function returned unrelated generic nodes and no useful path to these private test harness symbols; Graphify is stale for this change. The verified source above is authoritative.

### Diagnostic design

Keep the existing graceful-exit decision as the only acceptance decision. Capture request outcome as a closed enum, the boolean seal-poll result, and whether that poll call returned before or after the existing monotonic deadline. The timing describes the poller's return boundary, not the exact instant the seal file was read. Also record whether the nominal shared budget remained available or was exhausted at a named boundary. A deadline-expired request that skips polling is `not_checked`, not `no_seal_at_poll_return`. Do not add a post-deadline poll, second POST, second wait, or producer-side marker. Do not store exception details or response bodies. Write the small record with the existing exclusive private JSON writer to a sibling sidecar; catch only its fixed `AcceptanceFailure` so diagnostic I/O cannot affect cleanup. The driver return value, exception propagation, `audit_failed`, sender-before-receiver DELETE sequence, and timeout construction remain unchanged.

### Offline verification

Use the frozen R17 source as the baseline and the private driver overlay as the candidate. The focused fake-only module must first fail because the baseline emits no diagnostic sidecar, then pass against the overlay across each closed exit/seal/budget outcome. The existing source-bound cleanup module is retained as a regression for one POST, exact owned DELETE order, cleanup continuation, and the genuine finalizer. Run under an empty environment with private HOME/CODEX_HOME/TMPDIR, provider/database gates unset or disabled, plugin autoload disabled, and no database fixtures. Do not run the actual provider lane from this candidate.


## Addendum — MCP Outcome and AG-UI Stream Shutdown Diagnostics

### Verified boundary

The earlier Spec023 live receipt does not expose raw inbox-query or SSE observations: its row/event counters increment only when acceptance predicates pass. A `send_message` dispatcher can also return normally with a structured logical failure. The observer therefore needs to distinguish dispatcher completion from returned-result success without retaining result content. The stream reader's `AttributeError` is handled only at `next(iterator)` after explicit stop; errors from iterator construction, parsing, or publication remain failures.

Graphify does not model the private harness's response-thread and FastMCP observer-wrapper seams. The call and exception boundaries were verified directly in `message_api_restart_live.py` and `message_mcp_observer.py`; the generated graph was not rebuilt for these test-harness-only changes.

### Minimal implementation

1. Inspect only a strict boolean `success` from a mapping result or FastMCP `ToolResult.structured_content`; emit constant JSONL records with the fixed outcome enum `success`, `failure`, or `unknown`.
2. Require exact audit keys and enum values when reading the log. Require exactly the expected completed `send_message` calls with `outcome: success` before the acceptance can pass or a safe receipt can be emitted. Keep exception/rejection outcomes unknown and never serialize returned content.
3. Advance the AG-UI response iterator explicitly. Suppress `AttributeError` only when it is raised by `next()` after the stop event is set; keep the existing HTTP timeouts and three-second join bound, and fail any `AttributeError` outside that boundary.

### Offline verification and limits

Use fake-only tests for the stream close and actual FastMCP `ToolResult` success/failure structures. Verify no provider, API worker, HTTP client, PostgreSQL target, or network is opened. Preserve the original R25/R18 live evidence; do not replay a provider to investigate the ambiguous phase-A failure. These source edits invalidate previous source pins, so any later live lane needs a new source freeze, runner binding, independent review, and serial execution.


## Addendum — Bind the Generic Message Proof Path (2026-10-19)

### Source boundary

The inner runner currently hashes `mcp_server/server.py`, `api/main.py`, `services/agui_stream.py`, and the event publisher, but the production path crosses additional imported modules whose bytes can change without changing those hashes. Direct source tracing confirms:

`mcp_server.server.send_message` → `utils.orchestration._send_message_impl` → `api.main.create_inbox_message_endpoint` → `clients.database.create_inbox_message` → `services.inbox_service.InboxService.deliver_pending` → `services.terminal_service.send_input` → plugin hook dispatch and `EventLogPublisher` → `services.event_log_service` → the `api.main.agui_stream` route and `services.agui_stream.to_agui_event`.

The path also relies on `models/inbox.py`, `models/terminal.py`, `services/status_monitor.py`, `providers/manager.py`, `backends/registry.py`, and `backends/tmux_backend.py` for message state, receiver readiness, and actual terminal delivery. The required FR-020 inventory binds these direct owners while retaining the existing database-target, migrations, tmux client, constants, route, MCP, event publisher, AG-UI, and orchestration pins. Hash checks remain before private setup and at the existing API-replacement/final-acceptance checkpoints.

Graphify was queried read-only with `graphify query 'send_message -> _send_message_impl -> create_inbox_message -> InboxService deliver -> post_send_message -> AG-UI stream' --budget 1500` and `graphify path 'send_message' 'create_inbox_message'`. The query returned broad ambiguous nodes and no directed path; direct source inspection supplies the call sequence. The existing graph already contains the relevant production modules, and this correction changes only the runner's source inventory and offline tests, so a graph rebuild is not needed.

### Offline proof

Extend the existing source-binding regression to assert the complete required FR-020 subset and to damage each newly bound source leaf in turn. Missing files and symlinks must fail the existing initial preflight. A file changed after the first hash must be detected before provider authorization or any port/process setup. Use fake temporary source trees and the existing side-effect guards; do not exercise the provider, auth files, HTTP, PostgreSQL, or external services.

Any change to the binding inventory invalidates the prior source snapshots. T023/T024/T044 remain open until a fresh full-tree freeze, external launcher binding, review, and separately authorized live lanes are complete.
