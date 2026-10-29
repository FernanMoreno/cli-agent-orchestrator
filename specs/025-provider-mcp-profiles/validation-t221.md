# T221 — independent progress in inbox fallback sweeps

**Component PASS; global T013/OC-06 remains open.** [Structured receipt](validation-t221.json).

The actual OpenCode poll and provider-agnostic orphan sweep queried/delivered receivers serially. Holding A's pending-row query prevented the same sweep from reaching ready B. Two event-barrier controls reproduced this before correction (2 failures,57.29s). The existing T138–T140 status consumer did not cover these separate API fallback daemons. No unsupported shared-terminal/multi-API assumption was used.

Each sweep now uses at most four dedicated workers and four submitted futures, replenished as work completes. It copies context independently for each submission, avoiding nested work on the parent/default API executor. The existing outer admitted-operation lease lasts through actual executor join, so cancellation, shutdown or drain cannot mistake a live worker for completed work. Same-key counted locks still cover read→mark→send; busy keys are skipped without row mutation and remain eligible for the next event or periodic retry. Independent receivers may complete out of discovery order; same-receiver FIFO remains intact. No SQL schema, row-authority, provider permission or model behavior changed.

The two fallback daemons have separate four-slot bounds; status delivery retains its separate four slots. This establishes no process-wide or distributed thread limit. Receiver discovery still materializes its candidate list and remains a separate scalability limit; this change bounds dispatch, not that inventory.

Verification:

- Final configured regression: **147 PASS**, five inherited warnings,129.18s across14 files, including14 new cases. Coverage settings unchanged.
- New controls cover independent B progress, finite iterator/submission window, context propagation and joined threads, busy keys, queued work after stop/caller cancellation, drain/reopen, a single occupied default-executor thread, and propagation of the original worker interruption after joining other workers.
- Actual PostgreSQL/API daemon composition: both real listing queries and pending-row reads/status writes; B is marked delivered/dispatched while A's SQL read is held, A's two messages keep FIFO, no resend, and worker `current_database()` matches its owned target. Terminal `send_input` is controlled; this is not native inference or a model acceptance. Four focused integration checks passed53.81s.
- Mypy passed for the changed source; Black/isort passed; composition5 kept/0 broken. Claude read review and Luna Max/ECC found no material blockers after real PostgreSQL composition was added.

The first broader run (144 PASS/1 FAIL,132.99s) is retained. Its `InterruptedLock` test fake only implemented context-manager methods; explicit nonblocking locking requires `acquire/release`. The fixture now raises the same original `KeyboardInterrupt` from `acquire`, fails on any unearned release, and preserves identical exception/active-operation/registry assertions. The corrected fixture plus interruption controls passed17 tests32.36s before the final full rerun. No criterion or production budget was relaxed.

Graphify's existing87e2d5 index supplied21 incident edges for the polling/reconciliation/lock/API paths; all decisions were checked against current source. Foreign Graphify files were preserved, not regenerated. No native launcher, operator login or historical reconciliation job was changed. T220 remains accepted on its original d552ce47 native execution; T221 adds shared inbox component evidence, not blanket acceptance of that new source across providers.
