# Apps native-browser event-stream diagnosis — 2026-10-02

Read-only diagnosis; no source edits, provider/browser launches, installs or external effects. Actual browser gate /tmp/spec008-mcp-apps-browser-native-final.log1failed/6passed fails at initial seed count1 versus0, before send gesture. Chromium worked in the other6 cases; this is not an installation/browser prerequisite failure.

## First incorrect boundary: stale E2E host wire tool names

Production cao_mcp_apps/src/shared/mcpApp.ts:164–191 maps only three app-only tools to native FastMCP names: cao___cao_fetch_history, cao___subscribe_events, cao___submit_command. The production native registration preserves app-only metadata/scopes. e2e/host.js:406–417+421 dispatches only bare cao_fetch_history/subscribe_events/submit_command; aliased requests receive JSON-RPC-32601 unknown tool. EventStreamView.tsx awaits connect then fetchHistory before ingest and subscription. Rejected history therefore leaves initial events=[] and stops that effect before any SSE request. Browser error-context confirms Governance Stream rendered `No fleet events yet`, while agent detail rendered correctly because render_agent_view remains a bare model/app tool. The6 passing tests do not require successful app-only event history/submission and cannot validate that alias boundary.

Executed pure Node VM with actual e2e/host.js and fake DOM/windows, no server: /tmp/spec008-apps-host-alias-proof.log. Bare history returns exactly seed-1; cao___cao_fetch_history returns-32601; aliased subscribe and submit likewise return-32601. This confirms source-level root cause independent of Chromium.

Correct narrow change belongs E2E host adapter: dispatch the three explicit native aliases (prefer exact alias lookup to arbitrary prefix stripping), retain bare names only if required for deliberately old-host fixtures. Do not revert product aliases or weaken native registration/app-only scopes. Count/tracing assertions should record actual wire names separately from normalized harness dispatch.

## Focused regression before fix

Add harness contract assertion via actual host peer: for each exact native alias, issue tools/call and expect structuredContent result rather than-32601; history returns seed-1, subscribe returns valid descriptor, submit increments controlled submit count and emits once. Also assert an unknown similarly prefixed tool remains-32601. The existing Chromium event test then proves1seed→2rows through actual alias-bearing bundle. Keep initial row assertion: replacing it with0 or injecting initialEvents bypasses the broken contract.

## Next boundaries to verify after alias repair

- EventStreamView defaults backplaneBaseUrl=http://127.0.0.1:9889 and concatenates relative descriptor `/events`. Harness server/playwright support arbitrary E2E_PORT. If this run used an alternate port, aliases repair hydration but SSE connects to the wrong server. Do not guess from the failure log; it fails before subscribe and contains no actual configured port. Verify requested SSE URL. An absolute descriptor currently also concatenates incorrectly, so blindly returning absolute URL from harness is insufficient. Robust descriptor resolution uses new URL(desc.sse_url, chosenBase); retain explicit caller base and intended production endpoint authority, or execute this harness on its documented default isolated9889. This is a conditional next seam, not the demonstrated cause of0seed.
- __host.ready() observes initialized notification, not completed history or SSE open. Even rendered seed can precede EventSource connection. A send immediately after seed assertion can hit /emit before subscribers exist. Add deterministic subscription-ready observation before live-event assertion (actual EventSource open/server subscriber registration; no sleeps). Check /emit delivered count so dropped-at-harness event is distinguishable from view rendering failure. This does not justify increasing the10s initial seed timeout.
- EventStream effect currently lacks a history rejection diagnostic. After fixture repair, a separate product negative-path test can assert useful history failure/retry if required; do not treat the current stale harness error as proof production native aliases are wrong.

Suggested owner gate: alias contract focused RED→GREEN, then original event-stream Chromium case, then all7 native-browser cases on isolated known port. Full global completion remains separate.
