# Tasks: Normalize Line Endings

**Input**: [spec.md](spec.md), [plan.md](plan.md)

## Phase 1: Setup

- [x] T001 Capture the file list from `git diff --check HEAD` into the session scratchpad `crlf.txt`

## Phase 2: User Story 1 - Clean whitespace gate (P1)

**Independent test**: `git diff --check HEAD` exits 0.

- [x] T002 [US1] Strip trailing CR from every file listed in `crlf.txt` with `sed -i 's/\r$//'`
- [x] T003 [US1] Confirm `git diff --check HEAD` exits 0

## Phase 3: Polish

- [x] T004 Run `compileall` over `src` and `test` and targeted pytest suites (`compileall` passed; proxy suite: 5 passed, 2 failed; runtime snapshot suite failed collection because module is missing. See T097 C02/C06.)
- [x] T005 Run `project-composition-check caos` (passed: 4 contracts kept, 0 broken.)
- [x] T006 Review `git diff --stat` versus `git diff --ignore-cr-at-eol --stat` (identical: 99 files changed, 14,835 insertions, 1,051 deletions.)

- [x] T007 Add `.gitattributes` with `* text=auto eol=lf` at repository root

## Dependencies

T001 → T002 → T003 → T004–T006 (T004–T006 parallel).

## Revisión de cierre — 2026-09-30

Las siete tareas conservan su evidencia histórica. La revisión fresca de
`git diff --check HEAD` salió con código 0 y `git check-attr text eol` confirmó
`text=auto eol=lf` para API main.py. Esta revisión no atribuye a la normalización
los cambios semánticos de 001 ni vuelve a afirmar igualdad contra HEAD después
de esos cambios. No se crearon commits de cierre.
