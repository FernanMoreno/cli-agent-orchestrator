# Implementation Plan: CAOS Studio (019)

**Branch**: `claude/frontend-graph-20261021` | **Date**: 2026-10-21 | **Spec**: [spec.md](spec.md)

## Summary

Studio is delivered incrementally inside the existing production web app (`web/`), not as a
rewrite. Each increment adds or reshapes one surface on top of real contracts and records its
coverage row in [capability-map.md](capability-map.md). The conceptual prototype
(`design-preview.html`) stays a mockup and is never presented as live (FR-040).

## Technical context

- React 18 + TypeScript + Zustand + Tailwind + lucide icons; Vitest/jsdom for tests.
- Existing navigation is a tab strip with Alt+N shortcuts in `web/src/App.tsx`; new destinations
  are appended so current shortcuts never renumber (existing contract, tested).
- Fleet node scoping (`setActiveNode`, `getNodeEpoch`, `nodeSignal`) already isolates requests
  per node; new surfaces must discard late responses using the epoch (FR-031).
- Design tokens: `design-tokens/` generator feeds `web` and `cao_mcp_apps`; the 019
  `design-tokens.json` proposal is not yet adopted (would change shared generated files).

## Increments (ordered)

1. **Execution map (US4, C12)** — first useful slice of spec 017 in Studio; see
   [017 plan](../017-live-execution-graph/plan.md). Delivered in this branch.
2. **Navigation by goal and context bar (US1, FR-004/005)** — group tabs into Work / Library /
   Operation / Administration and show node + reader identity on every surface.
3. **Questions inbox linked from map waits (US5)** — once 016 wait graph exposes terminal
   identity (017 T011).
4. **Agent creation with own prompt (US2)** — reshape `ProfileCreateModal` + launch flow.
5. **Workflow designer (US3)** — library + source view first; visual DAG only for supported modes.
6. Remaining areas per capability map.

## Spec 020 (Spec Kit orchestration) connection

No backend contract for Spec Kit phases exists today (no `/speckit` or equivalent route found in
`api/main.py`). Studio must not show Spec Kit phases as available. Pending contract: a read
endpoint listing feature directories and artifact presence (`spec.md`/`plan.md`/`tasks.md`) per
authorized project, plus phase execution through existing workflow/Work authority. Until then
020 surfaces stay out of the UI.

## Risks

- Tab strip already at 12 entries; increment 2 is needed before adding more destinations.
- English UI copy kept for consistency with the existing app; Spanish localization (FR-037) is a
  separate cross-cutting increment.

## Static mockup scale and label correction (2026-10-22)

Integrate only the isolated HTML and its model/DOM checks from the reviewed frontend worktree into main. Keep the approved demonstration and add bounded synthetic scopes with progressive drill-down and paginated secondary panels. Fix the reproduced SVG paint-order defect by painting lane/continuation labels after links with a narrow background halo, while nodes retain their foreground layer. Verify model counts and bounded DOM, then use an existing real browser for desktop/mobile, interactions, text and screenshots. Compare the served HTML hash with main before sharing the preview. Existing screenshots remain historical until explicitly replaced. No application/server contract changes; the existing graph has no mockup nodes, so inspect inline source directly and preserve foreign Graphify output.
