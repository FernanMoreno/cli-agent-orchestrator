# Implementation Plan: Normalize Line Endings

**Branch**: `002-normalize-line-endings` | **Date**: 2026-09-27 | **Spec**: [spec.md](spec.md)

## Summary

Strip the trailing carriage return from each line of the working-tree files
that `git diff --check HEAD` reports, so they match the LF endings stored in
HEAD. No semantic edit, no commit.

## Technical Context

- **Scope**: 97 tracked, already-dirty files (Python, TypeScript, Rust, Markdown,
  TOML, YAML, JSON, SVG, lockfile) across `src/`, `test/`, `tui/`, `web/`,
  `docs/`, `docusaurus/`, `examples/` and root.
- **Tooling**: `sed -i 's/\r$//'` over the exact list emitted by
  `git diff --check HEAD`.
- **Subsystems**: all touched mechanically; no behavioral boundary changes.

## Constitution / Workflow Check

- Preserve unrelated work: only terminators change; pending edits kept.
- No commit/push.
- Significant route triggered by file-count threshold, not by behavior.

## Research

- **Decision**: CR stripping over `git add --renormalize`.
  **Rationale**: renormalize touches the index; the user's dirty state must stay
  unstaged.
- **Follow-up (approved by user)**: add `.gitattributes` with
  `* text=auto eol=lf`; index already all LF (`git ls-files --eol`), so no
  renormalization churn.

## Composition surface

- Python/Rust/TS parsers accept LF; HEAD already uses LF, so the post-change
  bytes equal the committed convention.
- Lockfile `uv.lock` and `pyproject.toml`: LF matches HEAD; tooling unaffected.

## Validation

1. `git diff --check HEAD` exit 0.
2. `git diff HEAD --ignore-cr-at-eol --stat` unchanged by the operation.
3. `compileall` over `src` and `test`; targeted pytest suites.
4. `project-composition-check caos`.
