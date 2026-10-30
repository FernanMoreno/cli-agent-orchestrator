# Composition Review — T009 bound Inbox checkpoint

Verdict: **PASS for explicitly bound Inbox components; T009 remains open.**

- Changes: Inbox direct calls, status/request sink, discovery sweeps and physical-child ownership share the injected DeliveryAdmission. Unbound compatibility is unchanged; the API singleton has not been activated.
- Neighbors inspected: monitor/lease owner, API acceptance/lifespan/Herdr callback, terminal dispatch locks and memory/receipt writes, legacy audit pool and PostgreSQL receiver cursor. Work/remote ownership remains T010; no authority or pool limits changed here.
- Invariants: reserve before pop/handoff/SQL, denied wakes retain ordinal/Context/registry, busy heads do not hide unrelated ready keys, per-receiver effects remain exclusive. Physical capacity release does not republish a key while its old consumer Future still owns it.
- Storage/order: rows remain PENDING when capacity is denied; native PostgreSQL tests prove later delivery after capacity returns. All historical DELIVERED-before-send and uncertain-result handling remain unchanged. Storage remains reserved through every send and status/audit/receipt write; no early-release acceptance is claimed.
- Lifecycle: cursor closed before waiting; completion queue avoids per-receiver scans of active children; all known children join before first error is propagated. Each bound sweep child owns a drain count before lease.start, including a controlled real-start-then-error handshake. Never-started failure cancels unused capacity. A failed owner restart leaves service admission closed.
- Validation: 163 passed, zero failures/errors/skips, three warnings across 18 modules. Includes two new native PostgreSQL denial/recovery paths, synthetic eight held delivery threads, context/registry retention, cancellation, start failure, original Inbox ordering/sweep/cursor/lifecycle controls, owner and monitor tests. Source/support preserved; every failed development attempt remains in the receipt.
- Architecture/types/style: 431 files, 1899 dependencies, five contracts kept and zero broken. Scoped mypy passes the changed source; Black/isort pass three files. No independently deployed protocol changed, so Pact is not applicable; internal dataclass/state contracts and actual PostgreSQL rows are exercised.
- Review: third Codex source/control review reports no blocker in the bound scope. Root reviewed final changes and retained the original failure contracts and limits. Graphify was read without regeneration because generated files contain unrelated edits; important call paths were verified against source.
- Remaining: API lifetime binding, authenticated operational enrollment, monitor notifications and Herdr enqueue. No real provider/node capacity, global suite, M1/M2/M3 or T009 completion claim. Batch cost is an estimate for the whole serial invocation; physical cost acceptance requires its own predeclared payloads/resources.
- Durable knowledge: separate physical/observer lifetimes and exceptional child-start ownership are saved to the shared Obsidian concept via an inspected transaction.

Evidence: [receipt](validation-t009-inbox.json), [remaining tasks](tasks.md).
