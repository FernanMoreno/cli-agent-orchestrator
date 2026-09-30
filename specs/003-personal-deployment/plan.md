# Implementation Plan: Personal deployment

**Checkout**: main | **Spec**: [spec.md](spec.md)

## Summary and Technical Context

Reuse existing Python/FastAPI server, SQLite, tmux/Herdr provider adapters and
explicit Docker Work registry. No new network or model authority is inferred.
Keep server on the host to use existing subscription CLIs; Docker runs accepted
Work process contracts. A private deployment directory under the Linux home
owns settings/state; a user systemd service manages server restart.

## Constitution Check

Retain executable identity, authenticated receipts, default-deny Work ingress,
and frozen contract semantics. No test result substitutes for a Work result.
No model CLI is smuggled into the static no-network Docker execution contract.

## Phases

1. Snapshot current work; reconcile candidate against its main snapshot using
   three-way merges. Do not publish or rewrite history.
2. Diagnose installed v2 against official CLI and real terminal captures; tests
   precede launch/status changes; preserve v1.
3. Run Claude workflows and the authorized provider/backend matrix, retaining
   explicit exclusions for other installed/unavailable provider capabilities.
4. Implement reusable personal deployment tooling, private persistent auth and
   config, service lifecycle, SQLite backup/restore and operator documentation.
5. Exercise restart/restore/real work; architecture/composition/diff review.

## Acceptance and TDD

Pytest for adapters, config/auth/restart/backup; opt-in real providers with
subscription/free models; actual Docker and systemd. Add failing regression
tests before runtime changes. Import Linter and project-composition-check apply.

## Knowledge Decision

Keep design and acceptance evidence in this spec. No vault duplication of code,
Graphify or temporary logs. Refresh graph only for newly added structural paths.

## Browser authentication repair

Keep existing JWT verification. Centralize browser bearer storage and authenticated
fetch; use the server's existing WebSocket token parameter. Add a connection form
and explicit local operator command producing a private fragment link. Consume the
fragment before mounting the app, and clear credentials on 401. Test REST/SSE/WS,
wrong credentials, fragment removal, redirects and cross-origin isolation; build
and install the changed web bundle in the persistent runtime. Review composition
and repeat actual protected browser requests. Knowledge remains in repo artifacts.
