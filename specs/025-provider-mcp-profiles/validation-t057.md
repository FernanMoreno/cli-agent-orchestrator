# T057 — owned-generation cancellation and private configuration retirement

Bug-significant: native provider initialization/cleanup composed with fenced turn recovery. Spec025 FR044/SC028, plan and numbered task precede implementation. Existing requirements checklist6/6; no extension hook. Knowledge belongs in repository evidence; existing vault identity mismatch remains untouched. Codex only; no historical job, operator login or global installation changes.

## Actual reproduction

Private pinned native1.18.35 diagnostic on **6a7a0f02** used the production API/tmux/provider, verified existing PostgreSQL/private disposable targets and an intentionally invalid private Go key; no operator credentials staged. Admissions **8,053 / 7,995 / 7,783 / 7,065 / 6,870 MiB**, including cancellation, above unchanged6,144. Requested-model UI/session201/idle **9.20 s**, one input200, actual credential rejection/ERROR and LAST202 refusal before recovery. Observation **73.35 s** within the original75-second budget.

A pending owned generation was observed. Wrong-generation cancel409 preserved native processes. Correct-generation cancel200 in **8.21 s** retained that generation with state cancelled and no allowed actions; old native execution stopped, new identities appeared and CLI returned idle. Repeated cancel409 preserved the new execution. But private root count was **two after reset and one after deleting the session**, before removing the outer disposable test directory. This is a demonstrated ownership leak, not successful provider-state cleanup. Original failed receipt `private_configuration_leaked_after_reset` retained; outer cleanup confirmed all processes/API/PostgreSQL/private disposable state and unchanged operator/source/binary. Total **106.45 s**. A preparation syntax error occurred before any earlier diagnostic launched; it is not runtime evidence.

## First incorrect boundary and fix candidate

Graphify impact found three initialization/cleanup nodes and32 edges; source verified `turn_recovery_service._cancel_locked` fencing → backend pane reset → provider.initialize → shell readiness → new private allocation → final cleanup. Both v1/v2 overwrite `_owned_*_config_dir` without retiring the preceding root. Cleanup later can reach only the latest allocation; outer fixture deletion had masked that risk in simpler journeys.

RED: **seven failures/five controls** across v1/v2 repeated initialization, replacement readiness/configuration failure, cancellation and shell failure. The minimal change calls existing provider cleanup **after confirmed replacement-shell readiness and before allocating a new tree**. Existing receipt/generation state is preserved; no prompt redelivery, schema, authority, operator/sibling mutation or altered timeout. GREEN: **12 passed**,24.20 s, including three successive replacements per version and unchanged pending receipt state. Cleanup retains its existing best-effort filesystem semantics; no broader I/O-failure guarantee is inferred.

Expanded regression/composition and committed-source actual reset cleanup remain required. This is part of T054; actual timeout and full OC1 remain separate.

## Expanded/composition verification

**616 passed**, two existing warnings, **229.06 s**, admission **8,167 MiB** against6,144. Preserved the previous591-case provider/native-policy/installer/preallocation/MCP-readiness/PostgreSQL-only-guard/error-consumer selection and added the12 ownership cases, existing turn-recovery contracts and actual Linux/tmux detached-child delete/cancel/foreign-owner tests. Configured markers/coverage unchanged; focused22% is not global coverage acceptance. Typing **405 source files**, Black/isort both changed Python files and architecture/composition **five kept/zero broken** passed.

Codex composition/diff review: confirmed pane reset and dispatch/generation fencing precede initialize; replacement-shell readiness precedes private retirement/allocation, old receipt/digest survives provider cleanup, then normal recovery confirms cancellation. No changed task delivery, retry/timeouts, event/wire schemas, transaction order, native grants, capability issuance or operator source selection. Pending inbox delivery remains fenced until existing recovery finish. No new external wire shape requires Pact; real existing PostgreSQL plus actual native reset remain required below. Existing best-effort filesystem cleanup semantics are unchanged, not a new universal I/O-failure guarantee. Repository specs hold this durable ownership conclusion; no vault substitution.

A fresh actual diagnostic will cancel a visibly in-flight native turn (processing or permanent-refusal footer with interrupt marker), keeping all generation/process/readiness/private-root predicates. The previous error-status wait is not a prerequisite of cancellation: T056 owns that separate error gate. Wrong/repeated-generation refusal, selected model after reset and roots after session deletion must still pass. Original reproduction and all limits remain preserved.

Final bounded AST refresh:92 nodes/209 edges, zero model tokens, private source/test hash receipt; main generated graph preserved. Candidate offline gate passes with risks pending actual committed-source cancellation/cleanup.
