# Specification: Deterministic zero-timeout pidfd readiness test

**Feature**: T097 QEMU test-precondition correction (test-only)
**Status**: Implemented; bounded host checks pass; full QEMU acceptance remains pending
**Input**: The selected QEMU test `test_wait_pidfd_readable_checks_ready_pidfd_with_zero_timeout` failed before its product assertion because its child wait timed out.

## User Scenarios & Testing

### User Story 1 — Verify zero-timeout polling only after readiness is established (Priority: P1)

A maintainer runs the unchanged T097 scratch selection and gets a reliable check that `_wait_pidfd_readable(pidfd, 0.0)` recognizes an already-readable pidfd. The test setup must establish that kernel readiness independently, so setup scheduling delay is not mistaken for a product failure.

**Why this priority**: The failed selected node is part of the existing 91-case QEMU acceptance and the product assertion did not execute.

**Independent Test**: Run the existing node under the pinned Linux test environment; verify that an independent native readiness poll reports `POLLIN` before the product helper is called with `0.0`.

**Acceptance Scenarios**:

1. **Given** Linux pidfd support, **When** the selected node starts an already-exiting `/bin/true` child, **Then** a bounded setup wait confirms native pidfd readability and the unchanged zero-timeout assertion returns `True`.
2. **Given** child startup, pidfd open, readiness polling, or the test body fails, **When** cleanup runs, **Then** only the test-owned child is killed if still live, it is reaped, and any opened pidfd is closed.
3. **Given** the scratch selector manifest and QEMU script, **When** this correction is made, **Then** the node ID/title and existing 91-case selection remain unchanged; no node is added to the selected module.

## Edge Cases

- Preserve the existing skip when the host lacks `os.pidfd_open`.
- If pidfd opening fails after child creation, cleanup still reaps the owned child.
- If the bounded readiness poll times out or sees no `POLLIN`, cleanup still runs and the setup fails clearly.
- If the child remains live after a setup failure, terminate only that `Popen` child and wait for it.
- Close an opened pidfd even if child cleanup raises.

## Requirements

- **FR-001**: Keep the selected node ID and title unchanged.
- **FR-002**: Remove `/bin/sleep 0.05` and the one-second setup wait.
- **FR-003**: Establish an already-exited child and confirm pidfd `POLLIN` with an independent, bounded native poll before invoking the product helper.
- **FR-004**: Preserve the exact assertion `_wait_pidfd_readable(pidfd, 0.0) is True`.
- **FR-005**: Ensure `finally` kills/reaps only the child created by the test and closes the pidfd on every path.
- **FR-006**: Leave production source, production timeouts, QEMU selector membership, and the 91-case guard unchanged.

## Success Criteria

- **SC-001**: The existing selected node reaches and passes the unchanged zero-timeout assertion on the existing Linux/QEMU profile.
- **SC-002**: An offline contract test proves setup failure cleanup and that the independent readiness precondition occurs before the zero-timeout product poll.
- **SC-003**: The scratch manifest remains 63 unique entries, including the same target node, and the QEMU runner still enforces exactly 91 cases.

## Assumptions

- The target is a test-only lifecycle correction; `_wait_pidfd_readable` behavior is not being changed.
- `/bin/true` is available in the existing Ubuntu guest.
- A 5-second setup bound is a test harness guard only, not a product timeout or performance claim.

## Current verification

- A fresh main-origin run passed all four checks: three offline lifecycle/order tests and the exact selected node. The main-origin receipt SHA-256 is `674b8a42f6059d4fe4bc72cac1c730221d7cdae9f8a44cb51f70c8e560725132` and its JUnit SHA-256 is `0176945b3bd029ff9eb14777f7da11378f2a7e155c44e28a4f6460ffee37e0b0`.
- The runner verified the selected test and product helper both originated from the main tree; it loaded no conftest or PostgreSQL fixture. HOME and CODEX_HOME were unchanged.
- The selected node ID/title and scratch selector manifest are unchanged. The manifest SHA-256 is `3fa2a10acf09b993a33082120342a880b5b314b4ae1c3a156d2e326e4a5d740c`.
- The selected module SHA-256 is `1b9fcf89b287f7df7bccb0488eeee16e6f9a73126dd219ea1884dffb23c8b3f2`; the new offline contract module SHA-256 is `cee0476dc1d40eb215e133647ee2aeee994efd17780737f432b225a694c3c6bb`.
- Full T097 QEMU acceptance has not been rerun after this correction. Keep that gate open; the earlier 91-case run remains a failure receipt and is not superseded by host tests.
