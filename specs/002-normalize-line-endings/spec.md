# Feature Specification: Normalize Line Endings

**Feature Branch**: `002-normalize-line-endings`
**Created**: 2026-09-27
**Status**: Completed — normalization and LF policy; historical validation recorded in tasks.md
**Input**: User description: "Normalize line endings (CRLF to LF) of 97 dirty working-tree files to match HEAD so git diff --check passes; no semantic change."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Clean whitespace gate (Priority: P1)

A maintainer finishing a session needs the repository whitespace check to pass
so the completion gate stops blocking, without losing or altering any pending
work.

**Why this priority**: The completion gate blocks every session while the
check fails.

**Independent Test**: Run the repository whitespace check against the last
commit; it reports no issues.

**Acceptance Scenarios**:

1. **Given** files whose only difference from the last commit is Windows line
   terminators, **When** normalization runs, **Then** those files no longer
   appear as changed.
2. **Given** files with real pending edits plus Windows terminators, **When**
   normalization runs, **Then** the real edits remain byte-identical except for
   line terminators.

### Edge Cases

- Files already using Unix terminators are untouched.
- Untracked files are out of scope.
- Binary files are never modified.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Only files reported by the whitespace check are modified.
- **FR-002**: The only change is removal of a carriage return at line end.
- **FR-003**: After the change, the whitespace check reports zero issues.
- **FR-004**: Content ignoring line terminators is identical before and after.
- **FR-005**: No commit or history change is made.
- **FR-006**: A repository attributes policy keeps text files LF in index and
  working tree to prevent recurrence.

## Success Criteria *(mandatory)*

- **SC-001**: Whitespace check exit status is success.
- **SC-002**: 0 content lines differ when line terminators are ignored.
- **SC-003**: Existing automated tests keep their pass/fail status.

## Assumptions

- The last commit uses Unix terminators for these files (verified on samples).
- Windows terminators were introduced by the local editing/sync environment.
