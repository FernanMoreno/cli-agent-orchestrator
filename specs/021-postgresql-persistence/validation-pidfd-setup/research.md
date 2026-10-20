# Research: pidfd test precondition

## First incorrect boundary

The sealed R6 test creates `/bin/sleep 0.05`, opens a pidfd, then calls `process.wait(timeout=1.0)` before the product assertion. The QEMU run raised `subprocess.TimeoutExpired` at that wait; the next line, `_wait_pidfd_readable(pidfd, 0.0) is True`, was not executed. The selected scratch group had 61 passed and 1 failed; the QEMU run ended with 72 passed, 1 failed, 18 not reached, and no skips. There is no evidence that the product helper returned incorrectly.

## Reproduction

- Sealed QEMU run: `run-976c06e7df8496e9-qbp0ift4`.
- Sanitized log: `TimeoutExpired` from `process.wait(timeout=1.0)` for `/bin/sleep 0.05`.
- Deterministic replay: a private harness invoked the actual old test function with a child whose `wait` raises `TimeoutExpired`; the helper call count was zero and the pidfd was closed. This proves the setup boundary precedes the assertion.
- Independent local kernel check: a private pinned-Python run spawned `/bin/true`, observed native `POLLIN`, reaped the child, and the actual R6 `_wait_pidfd_readable(pidfd, 0.0)` returned `True`. This is diagnostic evidence only, not QEMU acceptance.

## Decision

Remove the duration-based sleeper precondition. Establish readiness by native poll under a bounded setup guard, keep the product poll timeout at exactly zero, and make test-owned child/fd cleanup unconditional. Do not infer a scheduler or CPU-pressure root cause; the receipt does not prove one.

## Post-change evidence

The fresh main-origin run passed the lifecycle/order tests 3/3 and the exact selected node 1/1. The origin guard confirmed the selected test and product helper came from the main tree; no conftest or PostgreSQL fixture was loaded. The selected test SHA-256 is `1b9fcf89b287f7df7bccb0488eeee16e6f9a73126dd219ea1884dffb23c8b3f2`; the offline contract test SHA-256 is `cee0476dc1d40eb215e133647ee2aeee994efd17780737f432b225a694c3c6bb`. The scratch manifest hash remains `3fa2a10acf09b993a33082120342a880b5b314b4ae1c3a156d2e326e4a5d740c`. The full 91-case QEMU result remains pending.
