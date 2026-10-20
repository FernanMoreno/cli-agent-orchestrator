# C01 independent review

Compared product changes against actual snapshot /var/tmp/caos-integration-008-cy0jdbdw/files (marker /tmp/cao-integration-008-baseline-root). Prior dirty007 differences excluded. Read-only review; no product edits. Executed focused test/services/test_integration_008_core.py: 6 passed in37.43s. Root concurrently modified reviewed source; points below identify snapshot/time and fixes reported.

## Concrete findings

1. HIGH: Broad _drive except Exception catches intentional ResumeNotAllowedError(ValueError) from managed Work recovery (missing managed adapter, failed read/admission, malformed recovery response). It changes work_pending/run to FAILED and clears owner pointer, even though authenticated Work attempt still exists. Initial review relayed this; root reported fix. Source checked subsequently showed passthrough accidentally added to _assert_yaml_contract_history around510, while actual _drive around1312 still caught Exception first. Root notified location mismatch. Required fix is passthrough in actual _drive; preserve Work pending run/step/current_step_id and tuple; don't authorize another attempt. Add real managed recovery regression, not generic ValueError-only test.

2. HIGH initial finding, fixed while reviewing: _drive substrate convergence set step FAILED only in memory then cleared current_step_id without _journal_step. Could leave durable step running. Root added captured failed_step_id + journal_step before reset persistence; inspected corrected source and focused tests pass. Verify journal current pointer also cleared in durable run on failure (current implementation removed previous _journal_current_step call; inspect update_run_state signature). Error kind internal_error appropriate; test durable step/current pointer all terminal/converged.

3. MEDIUM: malformed unknown receipt phase bypasses BaseProvider validation because manager only calls restore for prepared/sent and settlement for result_verified. DB receipt.phase is unconstrained String. An unknown phase therefore publishes unfenced adapter; begin_terminal_turn_receipt blocks only known _TURN_RECEIPT_ACTIVE_PHASES and may overwrite unknown row. This preexists C01 but matters to failclosed reconstruction acceptance. Reject unsupported phase and invalid digest/result_verified result digest before cache publication. Begin-turn should independently reject nonrecognized/nonsettled prior phase unless explicit confirmed-cancel proof. Tests active malformed hash fails cache publication; unknown phase never publish/dispatch; malformed verified hash cannot be treated as verified.

4. MEDIUM: initial schedule_receipt_result_verification mark_terminal_turn_verification_started failure is not caught (line550 at review), although local state inserted already. First final-frame observation can raise without capture worker/deadline timer; a silent renderer then produces no retry. On later schedule current state exists so write is skipped and local timer can start, but quiet final answer lacks that later event. Root notified and fixing. Required test call real scheduler with mark-start DB failure, Timer/Thread captured, assert autonomous local bounded observer/deadline exists and no send; DB recovery preserves original deadline and receipt generation. Local helper tests currently cover only failure-recording/deadline helper, not scheduler boundary.

## Reviewed strengths / no blocker found

- manager.publish=False then full receipt restoration before publication closes audited cache hole; RLock serializes concurrent get_provider restoration. Test only sequential failure/retry, needs concurrent-reader proof.
- Retry-budget increment/backoff before diagnostic store IO prevents prior zero-attempt storm; max(local,durable) never decreases local budget. deadline loop charges local bounded attempts during store outage. Final publication receipt fence preserved.
- LAST durable recovery/provider generation match fence blocks historical output when adapter has no active receipt; cancelled/cancelling recovery produces unavailable result, appropriately. FULL remains transcript contract and not proof of success; require tests this route doesn't mutate verified state under malformed provider receipt.
- _wait_for_completion timeout and ERROR now explicitly delivered-uncertain; run_agent_step additionally marks all post-send StepExecutionError (except contract_rejected) uncertain so workflow won't replay. Conservative provider ERROR no retry consistent with side effects may already occur. Add direct ERROR helper test and engine-composition retry test.
- Extraction exceptions now become StepExecutionError(kind=output_extraction_failed, action=reconcile, delivered=True) retaining source as cause. Pending durable receipt prevents terminal teardown. Nonreceipt provider retains historical teardown behavior; no extra success claim.
- Codex restart fix now matches receipt candidates by strict receipt grammar plus digest via _result_has_active_receipt; preserves user/assistant cell and active picker boundary. No plaintext reconstruction/redelivery. Root running provider suite independently, not duplicated here.

## Missing meaningful acceptance tests

A. Two get_provider readers held on restoration DB barrier: second must not see provider before first completes; one adapter restored; failure allows healthy retry. Include cleanup racing restore if lifecycle lock contract claims that guarantee: create_provider/cleanup_provider currently don't take _restore_lock (not a new proven regression, but only get/get serialized).
B. Mark-start first-schedule outage and no further visual event: bounded observer starts/seals under outage, no orphan final answer/no minimal Timer storm. Root own scheduler test in progress.
C. Managed Work recovery refusal keeps running/work_pending tuple and owner pointer; generic substrate exception durably fails active step and clears durable current pointer, not only memory.
D. Malformed active and unknown receipt phase + failure and subsequent repaired retry. Verify no cache publication, no LAST historical output, no new claim/paste.
E. Real run_agent_step extractor raises ValueError: created receipt terminal retained, error wrapped with delivered uncertainty; YAML retries=1 launches only once. Current core test generic mocked worker tests drive convergence only, not this actual extraction boundary.
F. _wait_for_completion actual ERROR returns delivered=True + YAML retry suppression; quota branch stays wait_for_quota.
G. Codex restored-digest picker answer extraction with altered receipt and echoed-only nonce rejected; unchanged authenticated answer recovered, actual WAITING_USER_ANSWER remains, no recovery keystrokes.

No global completion certification: focused six tests passed; remaining suites/gates and concurrent changes owned by root.

## Follow-up on concurrently completed fixes

- Actual _drive now has ResumeNotAllowedError passthrough at1308. Initial misplaced block removed. Root added managed regression; larger suite is owned by root.
- Scheduler start write now caught; root owns RED/green acceptance.
- Receipt schema DOES have CheckConstraint phase (database161); earlier unconstrained-column observation omitted table_args. Unknown phase thus requires old schema/corruption. Isolated in-memory SQLAlchemy DB with check constraints ignored only while seeding legacy unknown phase reproduced on latest source: manager rejects `invalid persisted provider turn receipt phase`, cache remains empty (new root fix verified). Independent real begin_terminal_turn_receipt still returns a fresh claim and replaces unknown phase with prepared: `NEW_CLAIM True PHASE prepared`. Failclosed repository boundary remains actionable for legacy DB. No normal-table check bypass claimed.
- Durable current pointer finding confirmed: workflow_journal.update_run_state SQL1497 updates state/finished_at/error, not current_step_id. Root notified to restore _journal_current_step after captured-step journal. Latest root fix status not rechecked.

## Final C01 acceptance review

Latest source confirms original material findings resolved: actual _drive ResumeNotAllowedError passthrough; captured active step persisted FAILED then durable current pointer cleared; scheduler start-store exception still arms local worker/deadline; malformed phase adapter cache publication rejected; independent begin receipt claim now rejects every nonsettled phase unless durable confirmed cancellation. API extraction refusal maps500. Root verification logs inspected: core68 passed18.82s, workflow140 passed37.50s, policy/API246 passed34.46s. Codex343 earlier is root-reported, not re-run by reviewer.

Added authorized tests-only concurrent reconstruction acceptance to test/services/test_integration_008_core.py: first reader blocked inside receipt DB read, second reader cannot receive adapter/cache remains unpublished, then both receive same fenced restored generation with one DB receipt read. Ran it together with real legacy unknown-phase claim regression: 2 passed12.00s, only existing Pydantic deprecation warnings. No product edits/commits. No material residual C01 finding from this final review. Whole integration's later causal/provider/parser changes still require their own composition validation, beyond C01 acceptance.
