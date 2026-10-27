# T187 — disconnected HTTP controller

Initial controller validation: implemented and verified offline. Subsequent
[runner wiring and installed composition](validation-t187-runner.md) are now
implemented; actual paired HTTP cancellation remains pending.

The controller retains an initial snapshot while polling at750ms within the
original deadline. After same-binding TEXT growth it pins the selected native
PID, obtains fresh API/PG-bound evidence and checks API state again. Exact
process ownership and a live pidfd are checked immediately before cancellation.
Only the exact boolean True from the receipt-validating callback is accepted.
Physical retirement must be observed before the deadline. Polling stays in the
owning coroutine; descriptor cleanup cannot race a background select thread.

The caller must supply synchronous API/capture callbacks bounded by the same
deadline, full native executable/argv/terminal ownership validation and the
production cancel-receipt validator. This helper proves its selected process;
the runner still owns descendant retirement and complete cleanup. A finished,
unavailable or changed final capture refuses cancellation. It does not use
SQLite or add tool permissions, and it exports only typed snapshots/process IDs.

Root reproduced an original draft race: cancelling the coroutine closed a
descriptor while its executor thread was still waiting on it. The corrected
draft removes those threads and samples across the producer cadence. Root then
reproduced four failures for truthy untyped receipts and API completion after
the final PG capture; both boundaries now refuse. A fifth RED showed that a
readable pidfd observed after an expired deadline could be credited; time is now
checked before observing retirement.

Final focused verification: **45 PASS in13.00s**, including13 controller cases
and32 existing activity/installed-observer contracts. Two new cases use actual
owned Python processes and kernel pidfds, exercising physical retirement and
interruption during the wait. Other controller captures/API projections are
test doubles. The prior native Bun and real PG controls remain separately
source-bound; they do not turn these tests into an installed paired journey.

Composition review: diagnostic controller → API/capture callbacks → existing
strict ledger/PG projection → Linux process lifetime were reviewed. Identity,
generation/receipt binding, stale/finished refusal, exact receipt type,
deadlines, interruption and descriptor ownership are covered. Architecture
passes5 contracts/0 broken. No runtime schema, migration or external API changes
require a separate contract deployment. Verdict: **PASS for disconnected helper;
installed-controller/runner acceptance remains PENDING**.

Next: commit the checked loader before native initialization, verify inheritance
and repeated observations through the actual installed controller, integrate a
separately selected `native_http_stream` runner case, then execute a freshly
source-bound paired native journey. Keep75s/6144MiB, legacy marker/native_tool,
Unicode/replay/CLI pairing/MCP/Git/operator/source and full cleanup gates.

Claude reviewed the concrete controller and HTTP specification addendum without
blocking findings. Root executed the tests and reviewed the runner boundary;
read-only review does not establish its pending integration. Obsidian records
the durable descriptor/cadence/type/deadline rules.
