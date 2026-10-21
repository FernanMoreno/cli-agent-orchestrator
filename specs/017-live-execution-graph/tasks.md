# Tasks: Live execution graph (017)

Input: [spec.md](spec.md), [plan.md](plan.md). `[P]` = parallelizable. Status reflects real evidence only.

## Phase 1 — P1 slice on existing contracts (US1, US2, US3, US4 partial)

- [x] T001 Pure projection `buildExecutionMap` (agents as nodes from session detail; inbox edges; 016 wait group; coverage per source) — `web/src/components/execution/executionMap.ts`
- [x] T002 Unit tests: dedupe by message id, hidden endpoints counted not invented, content excluded, source failure → partial coverage, empty vs error — `web/src/test/execution-map.test.ts`
- [x] T003 `ExecutionMapPanel` with loading/empty/error/partial states, observed-at freshness, explicit "not live" label, refresh — `web/src/components/execution/ExecutionMapPanel.tsx`
- [x] T004 Keyboard/list-first navigation: agents grouped by session, relation table, detail pane with process status separate from delivery state — same file
- [x] T005 Stale response isolation on node switch / newer load (FR-021) — same file, tested
- [x] T006 Navigation entry "Execution map" in `web/src/App.tsx` (Alt+Shift+2)
- [x] T007 Component test for states and selection — `web/src/test/execution-map-panel.test.tsx`

## Phase 2 — Blocked on backend contracts (do not fake)

- [ ] T010 BLOCKED: expose nullable `caller_id` in session terminal rows → draw "created by" edges (FR-003). Contract proposal in plan.md.
- [ ] T011 BLOCKED: join wait-graph tasks to terminal identity (016 service) → attach waits to agent nodes (FR-013).
- [ ] T012 BLOCKED: aggregated topology snapshot + per-origin cursor stream → live updates within 2 s (SC-001), gap declaration (FR-020).
- [ ] T013 Work/attempt projection (`/work-items`) per agent with owner-only authorization (FR-005, FR-006).

## Phase 3 — Later

- [ ] T020 Drawn canvas (sigma/graphology already in deps) with stable positions (FR-025)
- [ ] T021 Filters/search/branch focus with hidden counts (US5)
- [ ] T022 Historical replay (US6) — needs captured relation history
- [ ] T023 Remote fleet nodes per-origin freshness

## Evidence

Verified 2026-10-21 in `web/` (deps symlinked read-only from main checkout, 1 worker):

- `tsc --noEmit -p web` — pass.
- `vitest run src/test/execution-map.test.tsx` — 7/7 pass (projection dedupe, hidden endpoints,
  content exclusion, empty vs failed vs partial, 016 waits, keyboard selection, stale-load discard).
- `vitest run` on the five App-rendering suites (browser-access, browser-login, browser-setup,
  fleet-review-bootstrap, profiles-panel) — all pass after updating the tab-order assertion.
- NOT run: full web suite, build, Playwright/browser, real API/PG. No live-data validation of
  the map against a running server yet. SC-001..SC-007 not measured.

Acceptance status: US1 AS1/AS3/AS4 partially (no delegation edges: T010); US2 AS1–AS3 partially
(node/edge detail, no navigation to terminal yet); US3 AS1 partially (waits shown, not linked to
agents); US4 AS2/AS3/AS5 partially (snapshot + refresh, no live follow). Feature 017 is NOT complete.
