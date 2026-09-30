# Tasks: Personal deployment and remaining acceptance

Dependency order: US1 -> US2 -> US3 -> joint acceptance.

## Phase 1: US1 — Upstream

- [x] T001 Snapshot current checkout and review candidate integration delta.
- [x] T002 Incorporate accepted T085 code while preserving later local changes.
- [x] T003 Run affected upstream architecture, contract and regression gates.

## Phase 2: US2 — Providers

- [x] T004 Reproduce OpenCode v2 incompatibilities and add adapter regressions.
- [x] T005 Implement v2 launch/status/result compatibility while retaining v1.
- [x] T006 Repeat real Claude workflows after login renewal.
- [x] T007 Inventory O03 and accept available authorized provider/backend cells.

## Phase 3: US3 — Personal deployment

- [x] T008 Add failing persistent deployment/auth/backup lifecycle tests.
- [x] T009 Implement private persistent configuration and authenticated startup.
- [x] T010 Add user service restart and consistent backup/restore tooling.
- [x] T011 Provision explicit Docker Work and accept real work/restart/restore.

## Phase 4: Joint acceptance

- [x] T012 Run composition, architecture, regressions and final diff review.
- [x] T013 Reconcile current status, limitations and operator quickstart.

Acceptance: [completion-evidence.md](completion-evidence.md). Unavailable provider
cells remain explicit limitations in the inventory, not passed tests.

## Browser correction reported by operator

- [x] T014 Reproduce Offline/401 and add browser transport regressions.
- [x] T015 Implement browser bearer connection and private local access command.
- [x] T016 Build/install web UI; accept real authenticated browser requests.
- [x] T017 Review composition, regressions and update acceptance limitations.
