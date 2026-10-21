# T067 validation: Work multi-job lock order

Scope: FR-051 / SC-035. Database-level lock-order proof only; no scale, native or live acceptance.

## Observed failure

Configured 33-module root run: 478 passed, 1 failed (`test/services/test_work_scheduler.py::test_ten_processes_never_exceed_two_slots`, psycopg `DeadlockDetected` on `work_jobs` tuple locks). A serial isolated repeat passed, so the fault is intermittent.

## Root cause (first incorrect boundary)

1. `WorkScheduler.claim_next` locked parent jobs in queued-snapshot first-appearance order. Snapshots taken before and after a concurrent claim can order jobs differently (A,B versus B,A).
2. `WorkAdmission._eligible` retained `_retire_unexecutable` lane/job/item/attempt locks outside the probe savepoint while probing later candidates, and `_commit_dispatch` then locked the selected job. A dispatcher could hold B and wait for A while a claimer held A and waited for B.

## Attempt history

- r1 (cancelled by root, not final): added the claim-path regression and produced a real RED (`DeadlockDetected ... while locking tuple ... in relation "work_jobs"`) against unchanged source. No production change.
- r2 ended with bridge status **failed**: the worker reached its maximum 40 turns after running the checks below. Its source and logs are retained, but no completed peer report or workflow signoff is claimed. Root reviewed the actual changes and took ownership after terminal status.

## Results

- Two new deterministic PostgreSQL tests failed before the fix (11.94 s), with actual lock-cycle detection, then both passed (10.95 s).
- First focused run: **56 passed, one failed** (127.48 s). The existing batch-withdrawal race fixture assumed queue order and waited for a retirement that canonical job ordering legitimately delayed. Its setup now sorts the three jobs by id so the paused first retirement is the lowest job; the original event barriers, eight-second bounds and assertions are preserved.
- Corrected focused run: **57 passed** (129.72 s). Five additional four-case focused repetitions passed; the last took 20.40 s. These repetitions do not establish scale acceptance.
- Neighboring admission, dispatch, delivery and API checks: **77 passed, five deselected** (220.67 s), with the configured selection retained.
- Root source review found a repeated whole-retirement scan per job. Deferred retirements are now grouped once by job, preserving within-job queue order, safe immediate retirements, selected-job authority-lane/parent order and the existing retirement CAS. Suffix construction/grouping are linear in queue length, plus sorting distinct jobs; this is a source complexity argument, not a throughput measurement.
- Independent Codex/Luna source review confirmed the suffix minimum includes all future candidate ids plus the selected id; any retained immediate lock therefore precedes all subsequent probe and deferred locks. Claude's final read review completed and approved the grouped source; it ran no commands. Root verified its open question about cross-job grant lanes: the composite foreign keys bind dispatch grants and grant ancestry to the same job, `_bind` authorizes against the attempt job, and `_chain` rejects any ancestor on a different job. The immutable retry history also requires root, current work and released scheduler identities to share the job. This is source/catalog review, not new runtime evidence.
- Final root checks against frozen grouped source: Black and isort **passed, 13 Python files**. Full-source mypy **passed, 409 files**; the configured **34-module** PostgreSQL regression **passed: 486 tests**, 350.94 s pytest time (355.31 s process time), with source hashes unchanged. Memory admission was 8,049 MiB before style checks, above the unchanged 6,144 MiB floor. Existing PostgreSQL identity and restart count are checked before every phase.

Private evidence: `t067-claude-lock-correction-r1/`, `t067-claude-lock-correction-r2/` and `t066-root-validation/root-gates-3skz_r8p/`. Earlier failures remain failed evidence. Normal fairness/queue selection, capacity two, ten claimant processes and the original 25-second test timeout are unchanged. No extra retry or global mutex is introduced.

## Composition and remaining gates

Changed boundaries are admission candidate probing/retirement, dispatch selected-job revalidation and public scheduler claim locking. Grant authority lanes precede job rows; job locks precede the aggregate. Rollback savepoints still release candidate path/authority locks. The same scheduler owns capacity, fairness, dependencies and budget, and the existing helper owns terminal withdrawal and CAS.

Final source review passed. Integration into MAIN passed with all tested source/config pins unchanged. Registered MAIN composition **passed: five contracts kept, zero broken**. Final diff and independent Claude read review passed. Fresh external two-worker `cao.work.message` acceptance is a separate gate; native OpenCode journeys, production-size migration locking, broad scale, global NFR and coverage are not established here.

Composition verdict: **PASS** for the bounded multi-job locking correction, with the wider live/scale gates above kept separate. Reviewed neighbors include authority grant ancestry, immutable dispatch/retry bindings, attempt transitions and per-job event sequence locks, retirement withdrawal, release, reservations and aggregate selection. The transition/withdrawal helpers only revisit the same already-locked parent job and its event sequence; no new cross-job or aggregate-first path is introduced. Real PostgreSQL and the original multiprocess test cover the relevant lock boundary. No new external protocol was added, so no new Pact contract applies. The registered architecture contracts remain unchanged and pass.
