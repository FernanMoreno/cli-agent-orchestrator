# T214 · implemented single-query native case

2026-10-29 · **Case implemented; native execution pending.**
Base `b5e42ca04c413a0b53120a613dffc2ba5fcf54d2`.

`--case task_query` is a distinct, default-closed OpenCode Go case. It reuses
the existing two-terminal startup and common exit/seal/strict-read/config/delete
and ownership-aware teardown. The receiver remains unprompted. The original
27 statements implementing message phases and API restart are AST-identical
inside their existing-case branch; old cases and their 75 s model limits remain
unchanged. The new query has its specified 180 s limit and 6144 MiB memory floor.

The case seeds one explicitly synthetic completed PostgreSQL task, bound to
the live API instance, verified project, actual requester terminal and sealed
local operator principal. Its coherent result does not claim worker execution
or stopped-writer authority. A real API GET validates the fixture before private
one-shot issuance and the sole native input. Completion requires the next turn
sequence and a fresh durable `result_verified` generation, followed by exactly
one successful MCP task query with all protected argument/owner/state matches.
The receiver's turn counters and inbox must remain unchanged/empty.

The sender's actual HTTP offer retains the existing exact coordination12
catalog and at least two primary requests for its tool-call/result cycle.
The passive receiver must have a complete sealed zero-request observer trace:
observer observed, tool offer unobserved, strict zero counters and exact empty
tool counts. The source-defined fixture documents that the two-request minimum
applies to the sender; it does not claim receiver inference.

The dedicated final predicate retains source/manifest/review/runtime/PG pins,
authenticated capability ownership, exact memory admissions, normal observer
seal, post-delete int-zero resources and all shared retirement/preservation
fences. The shared early-failure alternative cannot bypass post-delete checks:
it requires API launch false, while this case requires API A launched and one
tracked API record. API B must remain unattempted; no restart is claimed.

## Verification and composition

- Initial contract RED: 48 failures for the not-yet-implemented case.
- First focused GREEN: 48 passed; expanded controller/acceptance controls then
  passed 65 cases. Controlled transport checks prove one submit and rejection
  of an unchanged durable generation; they are not native acceptance.
- Initial broad run: 359 passed/5 failed. Two assertions were misplaced in a
  new test, and adding the case first broke the default-case index and two exact
  registry inventories. Assertions were restored, the new case appended after
  the original three, and inventories explicitly extended without removing old
  cases or weakening the manifest check. The failed log is retained.
- Final broad regression: **401 passed, 20 subtests passed, 3 existing warnings,
  107.41 s**. A final capability-count rejection adds one distinct test:
  15 cleanup/provenance controls pass in 19.13 s; **402 distinct Python tests**.
- Existing HTTP observer JavaScript suite: **17 passed**, 0 failed.
- Actual CLI `--self-check --case task_query`: PASS, no services or credentials.
- Black/isort pass. Composition checks: 5 contracts kept, 0 broken.
- Claude source review initially NO-GO on the passive trace; fixed against the
  actual strict reader. Final review `684bd67be6a74cd6aa434dabd4c370b2`: GO for
  the corrected fences, following full source review
  `584d71c19ed842e1aa0f1e070071a3a2`. Reviews were read-only; checks ran by lead.

Reviewed boundaries: source case/fixture → production profile/child environment
→ owned PG task/API authority → issued metadata → one native turn → MCP/HTTP
readers → shared resource retirement. Offline tests use controlled transport;
the task producer/registered MCP contracts are covered by T213. Actual SQL/API
and native behavior still require the current-source execution. Graphify had no
current runner node; source call paths were inspected, and generated files were
preserved. Existing Obsidian evidence-boundary guidance remains applicable;
routine case details stay in these repository receipts.

Private logs: `/home/felni/.cache/caos-t213-task-witness-20261029/`.
Fresh private launch templates are prepared separately, unarmed, with the
verified held PostgreSQL parent reused. Historical launchers were not executed.
No model, API journey, new database service or credential mutation occurred in
these component checks. T214, OC2-04 and global acceptance remain open pending
actual HTTP offer/call/normal cleanup. [Structured receipt](validation-t214-case.json).
