# Startup-owned SQLite resources — 2026-10-07

After the first workflow-index fix, actual Python 3.13 startup still retained
unclosed SQLite handles. An isolated allocation probe ran `init_db` twice with
all warning/source records intact: **22 then 21 ResourceWarnings**, 43 total;
RSS increased from 98,828 to 162,440 and 182,968 KiB. The traces identified
20 other database migration helpers, the private Beads schema verifier and
the expected Work schema compiler. The already corrected workflow-index
helper did not appear. No warning filters or diagnostic-source changes were
used to obtain acceptance.

## Regression, fix and compatibility

The new `test/clients/test_startup_connection_ownership.py` verifies all
observed owners: real commit and rollback for each of the 20 migrators;
closure with unchanged materialized Work catalogs; and Beads comparison on
success/error while a borrowed connection retains its pending transaction.
Four migrators intentionally propagate errors, and the test explicitly
preserves that existing policy. An initial harness omission for those four
was corrected without changing production. The corrected test rerun against
immutable pre-T079 source failed **all 43 cases exactly at the closed-handle
assertion** (24.24 s).

The production patch adds `contextlib.closing` outside the original connection
transaction context for only the 22 allocation-identified owners. SQL,
checksums, migrations, error handling, SQLAlchemy engine ownership and borrowed
connections remain unchanged. AST comparison after normalizing only these
wrappers/imports is identical in all three edited source files, proving no
other statements changed. Graphify impact was consulted; source inspection
verified schema materialization and callers.

After the patch: **45 passed in 5.97 s**, including the two previous
workflow-index regressions; **214 passed in 28.81 s** across database,
permissions, workflow migrations and recovery bundles. Black/isort accepted
the changed files. Independent review found no production blocker.

The same Python 3.13 startup probe now yields **0 ResourceWarnings** across
imports and both real `init_db` calls, including actual Work/Beads validation.
RSS is 99,068 / 154,580 / 154,724 KiB; second-call growth is 144 KiB rather than
20,528 KiB. The first-call difference includes normal module/schema setup.
This bounded probe establishes closure for the named owners, not a universal
claim that no unrelated warning can exist.

## Runner containment correction

The earlier external parallel-runner configuration placed pytest basetemp
outside each worker TMPDIR. Vault fixtures correctly rejected this at their
containment boundary. The v2 runner instead uses a common per-version TMPDIR
ancestor with independent pytest-worker directories, HOME/CAO_HOME and tmux
roots. It changes no repository guard, test selection or warning policy.
Two-worker preflights on Python 3.10 and 3.13 each completed **653 passed,
2 skipped** (300.58 / 286.83 s), covering all Vault tests, the exact failing
CLI case, full workflow lifecycle/launch composition and Graph API routes.

The [final source manifest](final-source-manifest.json) now binds 1,332 files;
each final Python snapshot additionally checks the project manifest, lock and
CI workflow (1,335 equal files). The v2 full selection contains 16,648 cases;
its successful completion remains required in [matrix evidence](final-python-matrix-evidence.md).
Private allocation, AST, RED/GREEN and runner logs remain under
`/home/felni/cao015-final/` and `/home/felni/cao015-final-python/`.
