# Tasks: CAOS Studio (019)

Input: [spec.md](spec.md), [plan.md](plan.md), [capability-map.md](capability-map.md).

## Increment 1 — Execution map in Studio (US4, C12)

- [x] T001 Navigation destination "Execution map" appended after Questions (Alt+Shift+2); existing shortcuts unchanged — `web/src/App.tsx`, test updated in `web/src/test/profiles-panel.test.tsx`
- [x] T002 Surface implemented per [017 tasks](../017-live-execution-graph/tasks.md) Phase 1

## Increment 2 — Goal-based navigation and context (US1)

- [~] T010 Group destinations (Work / Operation / Library / Administration) without renumbering shortcuts — `web/src/components/StudioNav.tsx`, `web/src/App.tsx`. Done: shortcuts stay bound to destination order; Library (profiles, memory, plugins = definitions) is separated from Work (running agents, questions, tasks, execution map); manual-activation tabs with a single Tab stop on the selected tab (roving `tabIndex`), arrow/Home/End focus, Enter/Space/click selection through the save-locked `requestTabChange`, stable `studio-tab-<key>` ids, `aria-controls` on the selected tab only and one `role="tabpanel"` labelled by it; `aria-describedby` names the group, `aria-keyshortcuts` exposes the shortcut. Tests: `web/src/test/studio-nav.test.tsx` (tab stop, ids, panel relation, focus vs selection) and `profiles-panel.test.tsx` (tab stop + panel label follow Alt shortcut and click). Not yet claimed: full WAI-ARIA APG conformance — no screen-reader or real-browser run; the group label spans sit inside the tablist; Tab stop is not remembered on the last focused tab.
- [~] T011 Context bar: active fleet node (with registry offline flag) from real fleet state — done. Reader: local-password sessions show the signed-in username; bearer mode only shows the access method ("Access token" / "Access token not accepted"), NOT the reader's identity — the server returns no principal for a bearer (pending contract). On a bearer 401 the whole Studio is withdrawn (auth state becomes `revoked`, in-flight reads from the old generation are refused, only the reconnect form remains) — `web/src/test/bearer-revocation.test.tsx`. Permission scope: BLOCKED, the server exposes no permission summary; the bar says so instead of inventing one (pending contract: read-only per-area capability/permission projection for the current reader).
- [ ] T012 Linkable location (hash route) restoring tab + selection on reload/back (US1 AS4)
- [ ] T013 Capability catalog: each area shows available / limited by permission / limited by server, with next step (FR-029)

## Increment 3+ (ordered)

- [ ] T020 Questions inbox ↔ map wait linking (blocked on 017 T011)
- [ ] T030 Agent creation with own prompt and launch (US2)
- [ ] T040 Workflow library + source view, then supported visual editing (US3)
- [ ] T050 Spec Kit (020) surfaces — BLOCKED: no backend contract (see plan.md)
- [ ] T060 Adopt 019 design tokens through `design-tokens/` generator (shared generated files)
- [ ] T070 Spanish localization + glossary (FR-037)
