# Tasks: deterministic pidfd test setup

**Input**: [spec.md](spec.md) and [plan.md](plan.md)

## Phase 1: RED and contract

- [x] T001 [P] Add an offline-only contract test for readiness ordering and failure cleanup in `test/security/test_work_bubblewrap_pidfd_setup_contract.py`; keep it out of `test/integration/t097/scratch-selectors.txt`.
- [x] T002 Record the existing QEMU RED and deterministic setup-boundary replay in the feature evidence packet before changing source.

## Phase 2: Selected test correction

- [x] T003 Update the setup and `finally` cleanup for the existing node in `test/security/test_work_bubblewrap_composition.py`; preserve its title and exact `_wait_pidfd_readable(pidfd, 0.0) is True` assertion.
- [x] T004 Verify the offline contract and the exact selected node against main origins under the pinned interpreter; assert that the scratch selector manifest remains unchanged.

## Phase 3: Acceptance

- [ ] T005 Run the existing T097 QEMU command only in the authorized VM window and require all 91 cases, no unexplained skips, exact source identity, and cleanup receipts.

## Dependencies and execution

T001 and T002 establish the regression before T003. T004 depends on T001 and T003. T005 depends on T004 and root's VM-window authorization. The only parallel opportunity is preparing the offline contract test while the source fix remains unmodified; no QEMU or product work is parallelized.

## Independent test criteria

- Offline contract: forced setup failure still reaps the owned child and closes any opened descriptor; a zero-timeout product poll is invoked only after native readiness has been asserted.
- Selected node: same node ID, native readiness precondition succeeds, exact zero-timeout assertion passes.
- QEMU: pending. The original aggregate 91-case selector remains unchanged and must pass before T097 acceptance is claimed.

## MVP

The selected test correction plus its offline lifecycle contract; full QEMU acceptance remains the final integration gate.
