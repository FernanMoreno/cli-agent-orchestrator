# Current-source cold NFR diagnosis — 2026-10-22

Status: **failed acceptance; diagnosis in progress (T132)**. T076, T081 and T083 remain open. No product correction or new global coverage result is established.

## Source and execution

Global R5 used published main `5628bdf6c580e229d54979493881b425e5e1d887`, tracked source digest `826f909b3723b3aaedc82219b66a2976633901dbcf28d83133514d89d6429c4f`, the pinned Python 3.13.15 interpreter and the existing owned PostgreSQL container. Independent source, interpreter, PostgreSQL identity, resource and filesystem checks preceded execution. The 6144 MiB admission floor, coverage, exact benchmark, normal GC and strict `<1.0s` requirement were preserved. Heavy execution was serial; unrelated processes were untouched.

The first independent covered cold call failed at **1.0231529650045559s**. Pytest exited 1, with one test/one failure/no errors. Stop-first-failure prevented the other two cold calls, full backend, Apps and ratchet from starting. The single-node coverage display is not aggregate coverage. Preserve this failure; later diagnostics cannot replace it.

Private receipt: `global-gate-r5-prep/runs/global-20261022T003815Z-2229764/receipt.json`, under `/home/felni/.cache/caos-git-publish-20261020`; SHA-256 `2de7fce11aac2e4341f65ca98186db1235923d53ee55db9213811e339efb4805`. Independent read-only cleanup checks found zero remaining owned databases, roles or sessions after checking the three fixture databases and six roles. The existing service identity stayed unchanged.

## Content-free boundary measurement

A separate private call of the same covered benchmark used lazy wrappers, preserving imports, results, exceptions, context-manager semantics, GC, timer, test body and assertion. It failed at **1.046850522980094s** under instrumentation. The controller verified a complete report, restored bindings, empty timing stack, no missing bindings and unchanged source, PostgreSQL identity and plugin. The profiler passed 56 pure controls; these controls exercise the diagnostic, not application acceptance.

Whole-call wall time was 1.047528893s; caller thread CPU was 0.361861391s. Nested measurements overlap and cannot be added:

| Boundary | Calls | Wall time |
| --- | ---: | ---: |
| Work verification | 21 | 705.000 ms |
| Component verification | 21 | 635.386 ms |
| Catalog manifest | 21 | 523.697 ms |
| Catalog digest | 21 | 72.870 ms |
| Audit commit, including separate initialization | 21 | 8.861 ms |

The 20 audit write contexts remain distinct from the separate read-only initialization. The constructor opens no repository; first-store lazy initialization occurs inside the strict timer. Do not exclude its verification from the measured budget.

Catalog-exclusive wall time was about 73.3ms; nested execution accounts for about 450.4ms. That includes driver, pool, server, transfer and scheduling effects and does not establish server time. SQL text caching and the existing single fresh statement were already present; no caching of catalog results or skipped authority checks is justified.

Private evidence: `nfr-r5-diagnosis/boundary-run-r1/{receipt.json,boundary-timing-summary.json,profile.junit.xml}`. Independent read-only checks again found zero remaining owned databases, roles or sessions, with the existing service identity unchanged. Reports contain timing/count metadata, never SQL, predicates, catalog payloads or credential values. Codex independently reviewed the diagnostic. Following the latest operator instruction, subsequent work uses Codex only.

## Post-call baseline plans

A subsequent single diagnostic completed the predeclared three unchanged-statement samples after the benchmark, before fixture teardown. The benchmark still failed at **1.0382853409973904s**, with one test/one failure/no errors. Admission was 9121 MiB and pre-probe memory 8967 MiB. All nine families used the current sorted union of **73 tables**; older 71-table fixture measurements are historical context.

| Sample | Planning | Server execution | Client round trip |
| --- | ---: | ---: | ---: |
| 1 | 5.679 ms | 31.556 ms | 45.329 ms |
| 2 | 2.954 ms | 22.773 ms | 30.002 ms |
| 3 | 2.770 ms | 22.199 ms | 29.292 ms |

The owned fresh connection remained read-only/repeatable-read, with a 5s local statement timeout and a stable snapshot, and was closed. Source, service and plugin identities stayed unchanged. The strict controller accepted the complete diagnostic report while returning the original pytest exit 1. Independent read-only cleanup checks found no remaining owned databases, roles or sessions. The diagnostic passed 86 pure controls and Codex read review; its execution gates were closed again after the single run, preserving the executed bytes.

The third sample places the largest depth-1 family aggregate in **constraints** at 8.218ms; its nested subquery produced 1515 rows. Other family aggregates took 3.635ms for routines, 3.403ms for indexes and 3.213ms for columns; the columns subquery produced 780 rows. These parent-node timings include child work and must not be summed with descendants. No SQL, predicates or catalog payloads were exported.

These measurements observe a post-workload cache, include EXPLAIN overhead and do not reproduce the runtime prepared-statement/pool round trip. They establish material catalog server work without proving that any proposed SQL rewrite would safely reduce it. T084's NOT NULL fast path and T129/T130 candidates remain rejected; this is no new evidence for accepting them. No product optimization has been integrated.

Private evidence: `nfr-r5-diagnosis/catalog-plan-run-r1/{receipt.json,catalog-plan-summary.json,profile.junit.xml,root-cleanup-verification.json}`. Receipt SHA-256: `81315adcb2f92c1fc489ac4987924b859b0f75675a820d9bc019e89faad9c0b9`; plan summary SHA-256: `55d187f37b97a44ea772a29255dd150d7961e5da86a7dbbfc435a95c69f0af9c`.

T132 remains open for a separately justified correction and the original cold/full acceptance gates. Architecture checks pass five contracts with zero broken; that result does not close performance or global coverage.
