# T212 — typed terminal lifecycle boundaries

Status: scoped component PASS; no global typing or renewed native acceptance.
Source base593c07e4. The existing five-root mypy invocation now succeeds with
configured mypy.ini and follow-imports=silent. Original four-root RED reproduces
17diagnostics; the skip-import experiment produced additional Any diagnostics and
is retained as a different control, not a changed acceptance criterion.

The stripe-lock finalizer closes its exact locally opened int FD, preserving
reentrancy and resetting the shared entry before close. Creation marks capture
the current ID before removal; preallocation cannot remove marks for an unknown
ID. Failed-create cleanup receives stable nonoptional ID/session arguments only
when both exist; row/incarnation/retirement fences and the original exception
remain intact. WorkerOutcome declares the actual backend-create tuple; the
nullable Kiro selection is explicit. The two boolean retirement decorators keep
ParamSpec forwarding and runtime named arguments; no casts/ignores were added.
Concatenate represents its prefix positionally in static signatures; current
Python callers use positional IDs and runtime keyword forwarding is unchanged.

Verification: Black/isort pass, independent Claude read review GO, architecture
5kept/0broken, five selected source roots mypy0, and68existing lifecycle/creation/
PostgreSQL/filesystem controls pass in64.13s with2existing deprecation warnings.
No test source, assertion, deadline, model, coverage criterion or config changed.
The verified PostgreSQL owner has zero borrowers/private databases after testing
and remains held for the next serial gate.

Preserve the failed runs:67PASS/1FAIL77.32s, then67PASS/1FAIL86.56s with Linux
bytecode alone. Both hit the original8s child deadline. Instrumenting the actual
canonical module measured9.8227s importing/10.2036s total, while locks release
in less than1ms. The bounded child had printed nested-ok inside the context,
which alone does not establish exit. This is not evidence of a lock deadlock.

A private Linux snapshot of installed dependencies preserves1197files/assets with identical bytes, including the psycopg wheel's adjacent shared libraries; initial missing
libpq snapshot failure is retained. Product source stays in the canonical checkout.
Actual import/lock/process control completes3.8859s; the same8s test and full
68case selection then pass. No global reinstall or original cold-start-latency
acceptance is claimed. AI_WORKFLOW6.1 now records the actual-helper argument check
and separate import/protocol/finalizer/process measurements.

Provider-list current headings now reflect scoped T197/T210R6 and T211R2; older
failed/date-specific entries are retained. Existing Graphify creation/deletion
nodes were consulted; locations were stale and verified against source. The
unrelated dirty generated graph was preserved.

[Structured result](validation-t212.json). Full provider matrices and native
get_local_cao_task acceptance remain separate.
