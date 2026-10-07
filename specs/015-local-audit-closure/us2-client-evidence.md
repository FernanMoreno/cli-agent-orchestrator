# US2 client evidence — 2026-10-07

Scope: T011 (Web), T012, T013, T014, T016, T020 (client), T022, T023 (client), T024, T026. Backend ticket/replay ownership remains with the US2 backend agent. No commit, publish, reset, or unrelated-work cleanup performed.

## Source and contract review

Before implementation: inspected dirty status, applicable AGENTS/AI_WORKFLOW, spec015 plan/tasks/contracts/data model, TDD, Graphify, composition and verification skills. Ran `graphify query 'web auth browserFetch connectTerminalWebSocket McpApp EventStreamView ServerClient authorization' --budget 1300` (exit 0). Verified the resulting call paths in actual source: Web auth/REST/socket, McpApp transport/EventStreamView, backend app_tools/API, TUI authenticated transport.

- Terminal transport authenticates `POST /terminals/{id}/ws/ticket` through browserFetch/fetchJSON, then connects with an encoded one-use ticket. Each reconnect requests a fresh ticket; stale node/auth generations cannot establish a socket.
- MCP history snapshot `{events,cursor}` comes from the same backend source as `/events`. Descriptor request carries `last_event_id`; each native EventSource failure closes the consumed source and requests a new descriptor/ticket. Duplicate/malformed events cannot move the cursor backwards. A `resync_required` descriptor reloads retained history, then requests a new descriptor. Unmount cancels reconnect timers and ignores late callbacks.
- McpApp fixes trusted origin before any request, from explicit bootstrap configuration or browser-provided ancestor/referrer context, and validates both origin and source window before any RPC resolution/notification. Missing context fails closed; all views visibly report unavailable/error.
- Every auth request has one 10-second budget across fetch and body reading, racing authority-generation cancellation. Cancellation closes the reader and cleans deadline/listener resources. Timeout retains the session with unavailable status, never revocation. Existing stale-generation/write fencing is preserved.
- TUI bearer is sent only to a configured numeric loopback HTTP(S) authority with valid nonzero port; userinfo, DNS aliases, abbreviated IPv4, paths, queries, fragments and remote hosts fail before transport. Existing redirect limit of zero remains in both ordinary and streamed transport. Unauthenticated remote configuration remains supported.

## Red evidence

- `npm --prefix web test -- src/test/browser-auth.test.ts`: exit 1, 3 expected failures: reusable bearer appears in URL, stalled fetch/body have no cancellation deadline.
- `npm --prefix cao_mcp_apps test -- src/test/integration.test.tsx -t 'US2 trusted'`: exit 1, 2 expected first-response forgery failures after fixing the test's microtask wait (the initial weak assertion passed and was corrected before implementation).
- `/home/felni/.cargo/bin/cargo test --manifest-path tui/Cargo.toml local_bearer_rejects_untrusted_destinations_before_transport`: exit 101, remote destination reached transport rather than returning Validation.
- `npm --prefix cao_mcp_apps test -- src/test/integration.test.tsx -t 'US2 common'`: exit 1, subscribe arguments lacked the history cursor.
- `npm --prefix cao_mcp_apps test -- src/test/integration.test.tsx -t 'reloads retained'`: exit 1, expired cursor did not reload/reissue.
- `npm --prefix cao_mcp_apps test -- src/test/integration.test.tsx -t 'trusted embedding origin is unavailable'`: exit 1, dashboard/agent lacked a visible alert and produced unhandled bootstrap rejection.
- `TMPDIR=/home/felni/tmp-cao015-clients npm --prefix cao_mcp_apps test -- src/graph/GraphView.test.tsx -t 'trusted host origin is unavailable'`: exit 1, graph lacked alert and produced unhandled bootstrap rejection.

## Green checks

- `TMPDIR=/home/felni/tmp-cao015-clients npm --prefix web test -- src/test/browser-auth.test.ts src/test/browser-session.test.ts src/test/browser-setup.test.tsx src/test/api.test.ts src/test/base-path.test.ts src/test/fleet-routing.test.ts`: exit 0, 6 files / 97 tests.
- `TMPDIR=/home/felni/tmp-cao015-clients npm --prefix web test -- src/test/work-state.test.tsx src/test/browser-terminal.test.tsx --maxWorkers=2`: exit 0, 2 files / 51 tests. Existing tests were adapted to asynchronous minting; the terminal test verifies two distinct tickets and no input replay.
- `/home/felni/.cargo/bin/cargo test --manifest-path tui/Cargo.toml server::tests`: exit 0, 29 server tests (remaining test binaries filtered).
- `TMPDIR=/home/felni/tmp-cao015-clients npm --prefix web run build`: exit 0, TypeScript and Vite production build; existing large-chunk/plugin-timing warnings only.
- `TMPDIR=/home/felni/tmp-cao015-clients npm --prefix cao_mcp_apps run typecheck`: exit 0.
- `TMPDIR=/home/felni/tmp-cao015-clients npm --prefix cao_mcp_apps run build:all`: exit 0, dashboard/agent/events/graph bundles. After final GraphView catch, `TMPDIR=/home/felni/tmp-cao015-clients npm --prefix cao_mcp_apps run build:graph`: exit 0. Existing singlefile/inlineDynamicImports/plugin-timing warnings only.
- `TMPDIR=/home/felni/tmp-cao015-clients /home/felni/.cargo/bin/rustfmt --check tui/src/server.rs`: exit 0.
- `TMPDIR=/home/felni/tmp-cao015-clients project-composition-check "$(cat .ai/project-name)"`: exit 0, 5 architecture contracts kept, 0 broken (latest run after final client source changes).
- Final scoped `git diff --check`: exit 0. Reviewed final hunks and preserved existing fleet, turn/recovery, workflow and TUI catalog work.

Broader-suite final outcomes will be appended below.

## Composition review

Changed subsystems: Web authentication/terminal transport; MCP bridge, event history/live transition and bootstrap error presentation; TUI authenticated destination validation.

Reviewed neighbors: browser session generation/node authority, terminal input fencing, backend mint/consume contracts, common event history/replay cursor and TUI streamed commands. Scenarios cover stalled fetch, stalled body, logout overtaking renewal, missing/forged embedding context, history/live gap, duplicate replay, expired-cursor resync, fresh reconnect tickets, input non-replay and remote bearer rejection. Browser transport/host peers are mocked to deterministically expose timing/window boundaries; TUI tests use real loopback HTTP stubs. Backend ticket expiry, single-use, scope and current authorization are verified separately by the backend owner.

Residual compatibility boundary: hosts withholding both ancestor/referrer context must explicitly provide trusted bootstrap origin; absence is visibly unavailable. TUI bearer requires numeric loopback configuration, not localhost/DNS aliases. Retained-ring replay cannot recover events already evicted; expired cursors visibly resync retained history. No new runtime dependency was added. Graphify structural refresh belongs to the root task; ordinary durable-vault persistence is unnecessary for these implementation details.

## Final broad verification and downstream example repair

- `TMPDIR=/home/felni/tmp-cao015-clients npm --prefix web test -- --maxWorkers=4`: exit 0, **42 files / 469 tests**. Existing jsdom Canvas diagnostics and intentional error-boundary test logs are present; no failed tests. An obsolete parallel verification run was stopped after this fresh full pass.
- `TMPDIR=/home/felni/tmp-cao015-clients npm --prefix cao_mcp_apps test -- --maxWorkers=2`: exit 0, **12 files / 91 tests**, including all four views' trusted-context handling.
- An earlier broad MCP run failed with ENOSPC in the shared `/tmp`; dedicated TMPDIR resolves the environment issue. No unrelated temporary files were removed.
- `TMPDIR=/home/felni/tmp-cao015-clients /home/felni/.cargo/bin/cargo test --manifest-path tui/Cargo.toml`: exit 101: **222 unit + 3 binary tests passed**; **3 live endpoint tests timed out** against the unavailable default API. This initial invocation did not pass and did not run the later integration binaries.
- `TMPDIR=/home/felni/tmp-cao015-clients .venv/bin/python /home/felni/tmp-cao015-clients/tui_api_fixture.py`: exit 0. Disposable wrapper boots the **real CAO API** on an ephemeral loopback port with private `CAO_HOME_DIR`, `TMPDIR`, `TMUX_TMPDIR`, auth env cleared, and `PluginRegistry.load` replaced before main import (no real plugins/providers started). It runs `/home/felni/.cargo/bin/cargo test --manifest-path tui/Cargo.toml --test endpoint_contract` (**4 passed**, real profile/provider API contracts), then `... --test hermeticity_tripwire --test no_backend_attach_call --test no_colour_literal_outside_theme --test pty` (**11 + 5 + 7 + 9 passed**). Server terminates and its private temporary directory is removed in finally. **261 distinct Rust tests are therefore verified across the initial unit/binary run and isolated real-API rerun**; do not represent the initial full invocation as passing.

Root additionally authorized the shipped standalone AG-UI EventSource viewer and its F3 recorder assertions because query-bearer rejection otherwise breaks that upstream consumer. Updated `examples/ag-ui/ag-ui-eventsource-viewer/index.html` and its `tools/record-demo.mjs`; added zero-dependency Node VM behavioral tests executing the actual inline browser script. No package/lockfile dependency changes.

Before viewer implementation, changed recorder F3 to require ticket/cursor-only URLs and ran `TMPDIR=/home/felni/tmp-cao015-clients node --test examples/ag-ui/ag-ui-eventsource-viewer/tools/viewer-transport.test.mjs`: exit 1, absent ticket POST and unsafe query-token seeding reproduced (3 failures; timeout assertion was subsequently made explicit). Final same command: exit 0, **5 passed** covering header-only bearer, new tickets/cursor on reconnect, ignored query credentials, bounded fetch/body deadlines, disconnect/late completion, visible expired-cursor resync. `node --check examples/ag-ui/ag-ui-eventsource-viewer/tools/record-demo.mjs`: exit 0. F3 now checks actual browser POST/stream requests as well as the builder; the full video/demo recording was not executed because its tooling dependencies are not installed in this tools directory.

Viewer mint uses authenticated `POST /agui/v1/stream/ticket?cursor=...`; GET stream uses `ticket` and matching `cursor`. The backend owner confirmed exact explicitly configured CORS-origin trust excluding wildcard, OPTIONS support for Authorization/X-CAO-Browser, canonical-origin checks for cookie auth, and 409 expired-cursor response before mint. The client visibly resyncs with a retained snapshot; it makes no durable-history recovery claim. A post-change `graphify query 'AGUI EventSource viewer stream authentication reconnect' --budget 650` verified the newly discovered upstream surface against source.

Final scoped diff review and diff whitespace check passed, including example/recorder changes. Composition check after example changes again kept **5 contracts / 0 broken**. Final client composition verdict: **PASS WITH RISKS** — deterministic browser/network tests and actual Rust API/PTY contracts pass; full real-browser AG-UI recording and backend ticket lifecycle acceptance remain separate evidence owned by the root/backend tasks. Trusted bootstrap context and numeric-loopback TUI configuration remain the compatibility requirements documented above.

## Review follow-up: named eviction frame — 2026-10-07

Root/runtime review identified ring eviction after endpoint cursor precheck but before replay. Backend now sends named `cursor_expired` with `{code:"event_cursor_expired",resync_required:true}`, then EOF, rather than delivering a suffix with an invisible gap.

Tests first: `TMPDIR=/home/felni/tmp-cao015-clients node --test examples/ag-ui/ag-ui-eventsource-viewer/tools/viewer-transport.test.mjs` failed the new named-frame test (missing registered handler); `TMPDIR=/home/felni/tmp-cao015-clients npm --prefix cao_mcp_apps test -- src/test/integration.test.tsx -t 'named cursor_expired'` likewise failed on the missing handler. Added explicit handlers, instead of depending on native error timing. Both validate the control frame, close the consumed source, show a retained-history-expired status and schedule one resync after 1 second. MCP reloads atomic retained history before requesting a fresh descriptor; the standalone viewer clears its cursor before obtaining a fresh ticket/snapshot stream. Tests deliver the named frame **and subsequent onerror**, checking one close/retry with fresh snapshot/ticket and no duplicated presentations. Fixed the new MCP test to use the project's supported textContent assertion (an intermediate run/typecheck failed on an unavailable jest-dom matcher, not source behavior).

- Viewer final same Node command: exit 0, **6 passed**.
- `TMPDIR=/home/felni/tmp-cao015-clients npm --prefix cao_mcp_apps run typecheck`: exit 0.
- Final scoped diff check: exit 0. Composition check after these source changes: exit 0, **5 contracts kept / 0 broken**.

Focused existing browser test attempted: `TMPDIR=/home/felni/tmp-cao015-clients E2E_PORT=47563 npm --prefix cao_mcp_apps run test:e2e -- --config /home/felni/tmp-cao015-clients/playwright.config.ts`. The task-specific config selects only the existing `e2e/event-stream.spec.ts`, an isolated harness port/output directory, and existing Chromium revision 1243 (the package's expected revision 1228 is absent). All four MCP bundles build successfully before the test. Browser launch is **environment-blocked**: installed Chromium exits 127 because system `libasound.so.2` is absent. Playwright reports one failed launch; the app assertion did not execute. No browser download or system dependency installation was attempted. Harness cleanup completed. This does not establish browser-level success.

Final follow-up MCP full suite: `TMPDIR=/home/felni/tmp-cao015-clients npm --prefix cao_mcp_apps test -- --maxWorkers=4`: exit 0, **12 files / 92 tests**. This supersedes the earlier 91-test MCP result; Web/Rust source was unchanged by this follow-up. Client verdict remains PASS WITH RISKS because the actual-browser test is blocked by the explicit missing system library.

## Convergencia local posterior del padre

El bloqueo histórico de libasound quedó resuelto mediante una biblioteca oficial extraída localmente para el proceso de prueba. Suite navegador MCP Apps completa: 7 passed, exit 0. Web469 y MCP Apps92 pasan; cobertura frontend90.64% supera floor90%. Véase [types-root-evidence.md](types-root-evidence.md) para la evidencia final y los límites. Los fallos y bloqueos anteriores se conservan como historial.
