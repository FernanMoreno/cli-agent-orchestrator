# Research: Real-Provider AG-UI Restart Acceptance

## Decision 1: Reuse the bounded real-provider journey

**Decision**: Extend `test/live_provider/interaction_cli_journey.py` behind an explicit opt-in flag and reuse its existing Codex/Claude auth staging, two-question payload, answer values, provider process monitoring, exact acknowledgement diagnostics, sanitized receipts, and cleanup. Keep Codex pinned to `gpt-6-luna`; pin Claude to `sonnet` only for this opt-in. The default Claude profile remains unpinned.

**Rationale**: This is the existing real evidence path for provider-generated questions. A second provider prompt or separate model harness would introduce behavior and auth differences unrelated to restart coverage. The explicit opt-in model pin makes the Claude run reproducible and the sanitized receipt names the selection that the built command is checked to use.

**Alternatives considered**: A new standalone model journey was rejected because it duplicates the current prompt/auth/proof logic. A mock-provider integration test was rejected because it cannot prove provider-owned wait/ack behavior.

## Decision 2: Use a fresh API process over the existing disposable PostgreSQL target

**Decision**: Add a test-owned API worker entry point using `cli_agent_orchestrator.api.main.app`, the configured main target, an explicitly initialized `InteractionService`, and the normal production route handlers. Run its Uvicorn server with unrelated lifespan daemons disabled. Terminate and replace that owned process while the provider/MCP process and database remain alive.

**Rationale**: Re-instantiating `InteractionService` in one process is already covered, but does not reset process-local route state, event buses, or module singletons. A child process provides the smallest genuine API-process restart while keeping the existing isolated database and provider invocation.

**Alternatives considered**: Restarting only `InteractionService` was rejected because existing `test/integration/test_interaction_recovery.py` already exercises that seam. Starting the full production lifespan was rejected because unrelated daemons/provider integrations are outside this acceptance and would make the run non-hermetic.

## Decision 3: Observe the production AG-UI stream, not a copied projection

**Decision**: Expose and connect to the production `/agui/v1/stream` route implemented by `api.main.agui_stream`. Parse its real `STATE_SNAPSHOT` and `STATE_DELTA` frames over loopback HTTP before restart, after restart, and after acknowledgement.

**Rationale**: The stream is the shared metadata surface whose composition with a real provider and restart is unproven. Its projection is closed and excludes prompt/options/response/private receipt token content.

**Alternatives considered**: Calling `project_interaction_snapshot` directly was rejected because it bypasses route authorization, SSE framing, fresh-connection snapshots, and database refresh boundaries. Reusing the current fake-bus AG-UI test alone is insufficient for the real provider composition.

## Decision 4: Keep the human response and acknowledgement authorities separate

**Decision**: The orchestrator sends only the existing fixed human answer payloads through the real version-checked HTTP answer routes. The actual provider/MCP process must call `cao_wait_for_user_answers` and acknowledge the exact receipts.

**Rationale**: The acceptance must preserve the distinction between human answer acceptance, answer return to MCP, and observed agent acknowledgement.

**Alternatives considered**: Reading/pulling answer rows directly in the harness or synthesizing acknowledgement was rejected because either would make the acceptance pass without provider observation.

## Decision 5: Keep this as test-harness-only work

**Decision**: Do not change product API behavior, PostgreSQL schema, AG-UI projection rules, or production startup. Any server configuration and restart orchestration live under `test/live_provider/` and use paths derived from the private run root.

**Rationale**: The gap is missing end-to-end evidence, not a known product-contract gap. The existing production route and persistence contracts are the target under test.

## TDD decision

**Decision**: Test the new harness logic before wiring it into the provider journey. The first tests will be offline unit tests for strict SSE parsing, child worker configuration, private environment/source binding, and owned process cleanup/restart state. They will assert default-off behavior and fail closed for malformed snapshots, missing question identity, worker launch failure, and unowned-process cleanup attempts. They must not invoke a provider, start an HTTP listener, connect to PostgreSQL, or perform any network request.

**Implementation order**: (1) add those tests and run them red; (2) implement the test-only worker/config/stream helpers and run the same tests green; (3) wire a narrow opt-in mode into the existing real-provider journey; (4) leave live provider/API/PostgreSQL acceptance for a separately authorized gate after the offline tests pass.

**Evidence boundary**: Offline helper tests prove orchestration and parser behavior only. They do not prove provider/MCP wait or acknowledgement, real AG-UI route behavior, process restart durability, or end-to-end success; those remain explicit live acceptance outcomes.
