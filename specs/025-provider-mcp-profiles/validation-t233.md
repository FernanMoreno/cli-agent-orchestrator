# T233: external Work validation checkpoint

Current external acceptance remains **pending**, with the fresh exact-source retry prepared on `8ac1dc42` and blocked before any services by the original6144MiB minimum. The191-case Work/PostgreSQL regression passed in405.76s; this does not substitute for external execution.

The first current external run failed `fresh_job_message_count_exceeded_expected_phase` after206.691s. Independent own-job read-only SQL proves exactly2message rows,2sent events and2ACKs, one A and one B. The receiver correctly ACKed B before the private observer reached its required2/2/1 snapshot. The original failed receipt/archive remain failed.

The private observer now uses an exact owned B pending/release barrier. It keeps intermediate2/2/1 with B unacknowledged and final2/2/2, original Cworker/image, authority, budgets and real upstream ACK/reply. It holds no DB transaction or global mutex while waiting and releases before teardown.35distinct controls pass;6additional overlapping controller checks after import formatting pass. Scoped Claude review findings corrected; parent launch is checked free of inherited acceptance-hook variables.72original runner functions and strict count predicates retain identical AST.

Only the current failed diagnostic's exact3DBs/6roles/private credential copies were removed after nonce/owner/session/process/artifact proofs. The same PostgreSQL stays running; no historical reconciliation or fake success cleanup. Work is separate from generic send_message and current head cannot inherit an old source's acceptance without proof.

[Structured evidence](validation-t233.json). Broader global/provider/backend and Spec024 full-suite gates remain separate.


Composition review: reviewed external Cworker→proxy→Work authority/service/repository→PostgreSQL ledger→AG-UI and API restart/replacement, including committed-send/lost-reply/replay, ACK observation ordering, per-proxy lifecycle locks and exact failure compensation.191real-PG regression and35private synchronization controls pass; original strict predicates remain. Import Linter5/0. Actual current external acceptance remains pending, so the journey verdict is NOT ACCEPTED at this checkpoint; an earlier source or generic send_message cannot close it.

The operational descriptor remains closed and unclaimed. The memory waiter and reuse-only coordinator retired after zero borrowers/DBs/work roles; the shared container remains running. Resumption requires a fresh live coordinator and pins for the then-current main HEAD. No background acceptance is left armed.
