# Remote cancellation and Python compatibility — 2026-10-07

## T082 — caller cancellation at a completed boundary

The unchanged full Python 3.10 and 3.11 diagnostic suites exposed
`test_cancelled_launch_retains_durable_compensation_request` waiting the full
launch budget and returning HTTP 504 instead of propagating cancellation.
The send callback sets an event and completes in the same event-loop turn
in which its caller cancels the launch. The Python 3.10/3.11 `wait_for`
implementation can return that completed child's result after receiving the
caller's cancellation. The following result wait then consumes the deadline.

A bounded real-channel probe reproduced this 20/20 times as
`RemoteOutcomeUnknownError`; after correction it propagated `CancelledError`
20/20 times. Regression cases cover both simultaneous send completion and
result completion. Against unchanged Python 3.11 source, both failed as
expected; an expired-guard no-send control passed (two failures, one pass,
1.84 seconds).

The channel owns each bounded await through `asyncio.wait`, cancels and drains
its child on timeout or caller cancellation, and propagates that cancellation.
An already-completed child at a zero deadline retains its existing result;
an unstarted send at a zero deadline is cancelled before executing. Existing
send/result error mapping, lock release, pending cleanup, durable operation
reconciliation and compensation-request persistence are retained.

All 71 runtime-channel tests passed on Python 3.10 and 3.11 after the behavior
change (15.30 and 14.34 seconds). Independent review found no material blocker
and ran 47 neighboring cases successfully on Python 3.11, including the
original durable compensation failure. Mypy passed all 398 source files.
The final rerun after the helper's generic return annotation also passed all
71 cases on Python 3.10 (15.26 seconds) and 3.11 (14.47 seconds). The five
original diagnostic runs remain failed history; fresh full matrix acceptance
is pending.

## T083 — Docker TOML imports on Python 3.10

Two executable Docker scripts imported Python 3.11-only `tomllib` without
using the Python 3.10 fallback already declared by the project. The existing
registered-project mount-policy and Windows MCP initialize-preflight tests
reproduced four failures and 40 passes against unchanged Python 3.10 source.

Both functions now try `tomllib`, then import the runtime dependency `tomli`
on Python 3.10. TOML parsing, mount policy, initialize-response validation and
the dependency lock remain unchanged. All 44 neighboring cases passed on
Python 3.10 (3.93 seconds) and Python 3.12 (3.50 seconds). Independent review
found no blocker. No test is skipped to accommodate the advertised version.

The fresh [T083 source manifest](final-source-manifest-t083.json) includes all 21 tracked
executable scripts alongside maintained source/test files: 1,358 records,
SHA-256 `a2a9b937a17ac6c8db7dc72c67f5793fa930cf07b989c72ce6cb06685dcf325d`.
The earlier T080 manifest remains [immutable historical evidence](final-source-manifest-t080.json).

After the final helper typing correction, composition checks again kept all
five contracts (400 files, 1,680 dependencies, zero broken). Black accepted
all 1,194 Python source/test/workflow files, isort passed, and mypy reported
no issues in 398 source files. The real two-process local-peer acceptance
also passed (one case, 32.60 seconds) on the same production bytes.
