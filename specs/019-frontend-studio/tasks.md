# Tasks: CAOS Studio (019)

Input: [spec.md](spec.md), [plan.md](plan.md), [capability-map.md](capability-map.md).

## Increment 1 — Execution map in Studio (US4, C12)

- [x] T001 Navigation destination "Execution map" appended after Questions (Alt+Shift+2); existing shortcuts unchanged — `web/src/App.tsx`, test updated in `web/src/test/profiles-panel.test.tsx`
- [x] T002 Surface implemented per [017 tasks](../017-live-execution-graph/tasks.md) Phase 1

## Increment 2 — Goal-based navigation and context (US1)

- [x] T010 Group destinations (Work / Operation / Library / Administration) without renumbering shortcuts — `web/src/components/StudioNav.tsx`, `web/src/App.tsx`. Shortcuts stay bound to destination order; Library (profiles, memory, plugins = definitions) is separated from Work (running agents, questions, tasks, execution map). Tabs pattern: arrow/Home/End move focus, `aria-describedby` names the group, `aria-keyshortcuts` exposes the shortcut. Tests: `web/src/test/studio-nav.test.tsx`; order assertions in `profiles-panel.test.tsx` changed from adjacency to group + shortcut preservation.
- [~] T011 Context bar: active fleet node (with registry offline flag) and reader identity from real browser-auth state — done. Permission scope: BLOCKED, the server exposes no permission summary; the bar says so instead of inventing one (pending contract: read-only per-area capability/permission projection for the current reader).
- [ ] T012 Linkable location (hash route) restoring tab + selection on reload/back (US1 AS4)
- [ ] T013 Capability catalog: each area shows available / limited by permission / limited by server, with next step (FR-029)

## Increment 3+ (ordered)

- [ ] T020 Questions inbox ↔ map wait linking (blocked on 017 T011)
- [ ] T030 Agent creation with own prompt and launch (US2)
- [ ] T040 Workflow library + source view, then supported visual editing (US3)
- [ ] T050 Spec Kit (020) surfaces — BLOCKED: no backend contract (see plan.md)
- [ ] T060 Adopt 019 design tokens through `design-tokens/` generator (shared generated files)
- [ ] T070 Spanish localization + glossary (FR-037)
