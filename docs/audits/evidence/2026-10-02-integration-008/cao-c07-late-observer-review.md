# C07 T046 late observer review

Read-only product review, 2026-10-02. Reviewed late_turn_observer, database claims/candidates/deletion, terminal LAST verification, API lifespan ownership and recovery v34. Added isolated reviewer tests only; no provider launch, external effects, commits or product edits.

## Material finding

**Fixed: transient candidate-list failure permanently terminated the observer.** `observe_once` calls `list_late_turn_candidates` before its per-candidate exception handler; previously `run` awaited the batch without an ordinary error handler. An SQLite outage therefore exited the lifespan task. Reproduced by `test_candidate_store_outage_does_not_permanently_stop_observer` in `test/services/test_integration_008_late_receipts_review.py`: 1 failed in 15.69s, `/tmp/spec008-late-review-outage-red.log`. Root fixed the narrow run boundary: ordinary errors log and continue with the existing cooldown, while CancelledError propagates; an unfinished worker remains owned and is drained with shielded gather. Reviewed actual fixed source. Root's fresh combined gate includes the reviewer three cases: 14 passed in 9.53s (--no-cov), `/tmp/spec008-late-review-fix.log`.

## Contracts verified

* Separate six-read durable budget, persisted 60-second cooldown and first-observation one-hour deadline; parent locking serializes claims across processes. A lost read consumes its slot. Ordinary three-attempt verification budget is unchanged. Recovery candidates require reconcile and detected completion, and join the current receipt generation.
* Cancellation/new generations cannot claim a stale slot. LAST expected-generation check plus settlement CAS reject old evidence; authentic result hash is persisted before provider receipt release/status publication. Candidate reads never send input or invent retries.
* Ordinary Work guard runs before claim and again at receipt verification; Work's authenticated result/reducer authority is not acquired by this observer. No managed receipt or nonce is forged.
* Round-robin cursor advances to the last bounded candidate (16 default,64 hard cap), wraps after an empty page, and skips exhausted/not-due rows. Work refusals do not consume slots, but also do not permanently block later IDs. No unbounded per-batch fan-out is introduced.
* One owned to_thread batch is shielded; normal shutdown and failed startup cancel/gather the owner, whose finally drains an in-progress batch. Event-loop cancellation does not abandon the blocking reader. A stuck native backend read remains an existing backend timeout limitation; this review does not assert a strict wall-clock capture bound.
* Terminal-row deletion removes late observation rows. v34 copies v33 and adds only the late observation table; v33 remains available as a frozen historical catalog.

## Independent extraction proof

New `test/services/test_integration_008_late_receipts_review.py` uses a real SQLite DB, actual CodexProvider prepare/opaque restart restoration/extractor/receipt witness, actual terminal_service.get_output LAST and actual durable settlement. Only native history and status publishing are fake; no live agent or operator DB is touched. Two cases passed in 10.29s (--no-cov): authentic late output settles, strips the receipt and preserves the real WAITING_USER_ANSWER rate-limit picker; a different nonce stays reconcile with no successful output/status. Both retain ordinary attempts=3, respect cooldown and make no input/native key writes.

Root source gates inspected: authority26 passed `/tmp/spec008-late-authority-green.log`; inventory/late+recovery116 reported `/tmp/spec008-late-inventory-green.log` (root-owned gate, not duplicated). No architecture/composition rerun was needed for reviewer-only tests; implementation completion still requires root's applicable gates.

Verdict: **PASS for the scoped T046 review** after the outage fix and fresh combined14 gate including all reviewer3 cases. No material residual identified. The subsequently added assignment-intent table and pending v35 inventory belong to a separate slice; v34 remains the reviewed historical late-observer composition.
