# Implementation Plan: Deterministic pidfd test setup

**Feature**: T097 QEMU test-precondition correction | **Date**: 2026-10-15
**Spec**: [spec.md](spec.md)

## Summary

Correct only the flaky setup in the existing pidfd readiness test. Use `/bin/true`, open its pidfd, wait for native `POLLIN` using a bounded setup poll, reap the child, then preserve the exact `0.0` product assertion. Cleanup remains owned by the test and closes the descriptor on all paths.

## Technical Context

- **Language/Version**: Python 3.12 in the sealed QEMU profile; tests also target the repository's supported Python versions.
- **Primary Dependencies**: Existing Python `subprocess`, `os`, `select`, and pytest; no new dependency.
- **Storage**: None.
- **Testing**: pytest, pinned Python, Linux pidfd.
- **Target Platform**: Existing Linux T097 guest.
- **Project Type**: Test-only change.
- **Performance Goals**: None. The zero-timeout helper argument remains unchanged; setup uses an explicit bounded wait.
- **Constraints**: Do not change production helpers, QEMU selection, skip semantics, or the 91-case count.

## Constitution Check

- Evidence before conclusion: the QEMU receipt and deterministic replay identify `process.wait(timeout=1.0)` as the first failing boundary.
- Scope preservation: only the selected test's child lifecycle changes; no production API or timeout changes.
- TDD: preserve the QEMU RED and add an offline regression for readiness ordering and cleanup before editing.
- Composition: verify child reaping, pidfd closure, and unchanged selector count together.
- Durable knowledge: no vault update is required for this bounded test fix.

## Source Impact

- Existing selected node: `test/security/test_work_bubblewrap_composition.py::test_wait_pidfd_readable_checks_ready_pidfd_with_zero_timeout`.
- Existing selector manifest: `test/integration/t097/scratch-selectors.txt`; the target is one of 63 manifest entries, 62 in the broker group. The script still checks 91 aggregate cases.
- Product call path: the test's `_composition_api()` imports `cli_agent_orchestrator.services.work_bubblewrap_composition`; the only production function invoked by this node is `_wait_pidfd_readable`.
- Planned changed paths: the selected test module and, if required to exercise cleanup without QEMU, one new offline-only contract module not listed in the scratch selector manifest. No production file or QEMU runner change.
- Graphify refresh: not applicable to production structure because no production node or edge changes. The direct test-to-helper edge is verified from source and recorded in `source-impact.md`.

## Design

1. Preserve the existing skip if `os.pidfd_open` is unavailable.
2. Spawn exactly one owned `/bin/true` child and open its pidfd inside a `try/finally` that also covers pidfd-open failure.
3. Use `select.poll()` independently from the product helper and a bounded setup wait (planned 5 seconds) to confirm this pidfd reports `POLLIN`; do not use `/bin/sleep` or a subsecond child-duration assumption.
4. Reap the child with a bounded `Popen.wait`.
5. Call the exact existing assertion with timeout `0.0`.
6. In `finally`, kill only this `Popen` child if it remains live, reap it, and close the pidfd in a nested `finally`.
7. Add offline tests outside the selected QEMU manifest for setup-error cleanup and the native-readiness-before-product-poll ordering. Do not add nodes to the selected composition module.

## Verification

The fresh main-origin run passed 4 tests: the offline contract module (3) and the exact selected node (1). The origin guard confirmed that both the selected test and product helper came from the main tree. The run loaded no conftest or PostgreSQL fixture, and HOME/CODEX_HOME remained unchanged. The source-guarded receipt SHA-256 is `674b8a42f6059d4fe4bc72cac1c730221d7cdae9f8a44cb51f70c8e560725132`; JUnit SHA-256 is `0176945b3bd029ff9eb14777f7da11378f2a7e155c44e28a4f6460ffee37e0b0`. The selector manifest, QEMU runner, and production helper are unchanged. The full T097 QEMU acceptance has not yet been rerun against this correction. Run it only in the authorized VM window; require all 91 cases, no unexplained skips, exact source identity, and cleanup receipts. No production performance claim follows from this test correction.
