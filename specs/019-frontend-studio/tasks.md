# Tasks: CAOS Studio (019)

Input: [spec.md](spec.md), [plan.md](plan.md), [capability-map.md](capability-map.md).

## Increment 1 — Execution map in Studio (US4, C12)

- [x] T001 Navigation destination "Execution map" appended after Questions (Alt+Shift+2); existing shortcuts unchanged — `web/src/App.tsx`, test updated in `web/src/test/profiles-panel.test.tsx`
- [x] T002 Surface implemented per [017 tasks](../017-live-execution-graph/tasks.md) Phase 1

## Increment 2 — Goal-based navigation and context (US1)

- [~] T010 Group destinations (Work / Operation / Library / Administration) without renumbering shortcuts — `web/src/components/StudioNav.tsx`, `web/src/App.tsx`. Done: shortcuts stay bound to destination order; Library (profiles, memory, plugins = definitions) is separated from Work (running agents, questions, tasks, execution map); manual-activation tabs with a single Tab stop on the selected tab (roving `tabIndex`), arrow/Home/End focus, Enter/Space/click selection through the save-locked `requestTabChange`, stable `studio-tab-<key>` ids, `aria-controls` on the selected tab only and one `role="tabpanel"` labelled by it; `aria-describedby` names the group, `aria-keyshortcuts` exposes the shortcut. Tests: `web/src/test/studio-nav.test.tsx` (tab stop, ids, panel relation, focus vs selection) and `profiles-panel.test.tsx` (tab stop + panel label follow Alt shortcut and click). Not yet claimed: full WAI-ARIA APG conformance — no screen-reader acceptance; the bounded real-browser checks are recorded below; the group label spans sit inside the tablist; Tab stop is not remembered on the last focused tab.
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

## Root review correction after Claude R3

Independent source review found two further auth boundaries: disabled-mode/no-token 401 incorrectly revoked the Dashboard, and a 200 response body that finished after bearer revocation could still return data. Root regression runs preserved the initial two failures, the later full-suite compatibility failures, and the delayed-body failure in disabled mode. The final correction requires a non-null current token before rejecting bearer credentials, aborts the old authority in every actual-token mode, and marks the UI revoked only in bearer mode. `fetchJSON` checks authority again after body consumption. Existing manual/bootstrap bearer-clearing assertions remain unchanged; normal local renewal retains the same session authority.

Private preimplementation Graphify pinned nine source/test inputs (171 nodes/330 edges), with auth/body boundaries verified against source. Independent narrow source review approved the auth correction. The complete web suite then passed **531 tests in 48 modules**, TypeScript and production build, with source hashes unchanged during all three phases. This is web regression evidence, not native-provider or full-spec acceptance.

A real Chromium smoke with explicit local API mocks confirmed grouped navigation, context, manual arrow/Enter/Space activation, existing Alt shortcuts and the execution snapshot. Its mobile check failed at 375 px (document/body 546 px): the Work group did not wrap its buttons, and header controls also exceeded the viewport. Root corrected those layout boundaries by wrapping controls without hiding overflow or weakening the width assertion. Final focused tests, build and browser results are recorded in [validation-studio-increment.md](validation-studio-increment.md).

Screen-reader acceptance, full 017/019/020, bearer-reader identity, live snapshots/cursors, server permission projection and scale remain open. Replacing a bearer token without revocation is outside this narrow auth correction.

## Static original mockup: approved design, progressive scale

- [x] T080 Integrate bounded synthetic 100/10k/1M scenarios, hierarchy, search, lists, decisions and activity while preserving the approved demonstration; independently run model/DOM checks and review the delta.
- [x] T081 Correct link/worker label overlap; validate demo/scaled interactions in a real browser at desktop/mobile sizes, capture fresh evidence and verify the preview serves the accepted main HTML. Production scale and full accessibility acceptance remain separate.

T080/T081 (2026-10-22): static mockup only, 33 model +19 DOM +26 real-browser checks, fresh desktop/mobile screenshots and matching served-main HTML. [Root integration, failures and limits](mockup-studio-original-research.md#revisión-e-integración-del-lead-2026-10-22). Production/reader acceptance remains open.
