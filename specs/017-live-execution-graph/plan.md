# Implementation Plan: Live execution graph (017)

**Branch**: `claude/frontend-graph-20261021` | **Date**: 2026-10-21 | **Spec**: [spec.md](spec.md)

## Summary

Deliver the first useful, testable slice of the execution map inside the existing web UI
(`web/`) using only read contracts that already exist. No backend change in this slice.
The map is an observation surface: it never starts, resends, cancels or answers work (FR-010).

## Technical context

- **Frontend**: React 18 + TypeScript + Zustand + Tailwind (`web/`), Vitest + jsdom for tests.
- **Data sources actually available today** (verified in source, 2026-10-21):

| Source | Contract | What it proves | What it does NOT prove |
| --- | --- | --- | --- |
| `GET /sessions` + `GET /sessions/{name}` | `Session`, terminal rows from `list_terminals_by_session` plus live `status` | Agent instances (terminal id, provider, profile, session, incarnation id, process status) | Delegation, task outcome |
| `GET /terminals/{id}/inbox/messages` | `InboxMessage` (`id`, `sender_id`, `receiver_id`, `status`, `created_at`) | Directed communication with delivery state | Orchestration type (handoff/assign), task completion |
| `GET /interaction-wait-graph` (016) | `InteractionWaitGraph` (`task`/`question_group` nodes, `required`, `cycle`) | Owner-visible required waits on operator answers | Link to a specific terminal node (ids are opaque `task:` / `question_group:`) |

- **Not available** (backend blocker, documented — not worked around):
  - `caller_id` is stored on `TerminalModel` but **not** returned by `list_terminals_by_session`
    / `GET /sessions/{name}`. Even when exposed it is creation/callback metadata, not proof of
    supervision, delegation or authority. Pending contract: a typed relation (kind, provenance,
    observed-at) authorised against both endpoints. Until then the map draws no creator or
    delegation edges. Owner: backend (Codex lane).
  - The inbox endpoint returns message bodies; the map discards them, but a metadata-only
    projection with per-resource ACL is still pending. Inbox pages are `created_at ASC LIMIT`
    (oldest first), so the map labels them as a first page, not as recent activity.
  - No aggregated topology snapshot nor stream with per-origin cursors. The first slice
    uses explicit refresh/polling and declares freshness; it never shows "live".
  - Wait-graph task ids are not joined to terminals; waits are shown as their own group.

## Design decisions

1. **Pure projection** `buildExecutionMap()` (`web/src/components/execution/executionMap.ts`)
   composes source results into nodes, edges and per-source coverage. All dedupe, scoping,
   and "unknown endpoint" handling live there and are unit-tested.
2. **Edges carry identity and source**: `inbox:<message id>` and `wait:<from>-><to>`; duplicates
   collapse by id (FR-004, FR-018). Message content is never copied into the map (FR-023).
3. **Endpoints outside the visible scope** are counted as hidden, not invented as nodes
   (FR-022, FR-026).
4. **Scope isolation**: responses are discarded if the active fleet node epoch changed or a
   newer load started (FR-021), mirroring the `useGeneration`/`nodeEpoch` pattern.
5. **Accessible list-first view**: grouped agent list + relation table + detail pane, keyboard
   operable, text status labels (FR-027). A drawn canvas is a later increment.
6. **States**: loading, empty (no agents), error (all sources failed), partial (some source
   failed/unavailable) are distinct (FR-016).

## Constitution / workflow check

- Significant classification (new cross-source read composition in UI). Spec → plan → tasks
  exist. Graphify was not regenerated (out of scope by request); structural facts were taken
  from source reads listed above.
- No backend contract changes; existing panels untouched except a new navigation entry.

## Project structure

```
web/src/components/execution/executionMap.ts      # pure projection
web/src/components/execution/ExecutionMapPanel.tsx # UI
web/src/test/execution-map.test.ts(x)              # unit + component tests
web/src/App.tsx                                    # new "Execution map" tab
```
