# Workflow-index connection ownership — 2026-10-07

The fresh Python matrix exposed an owned SQLite connection left open by
`clients.database._migrate_workflow_index`. A SQLite connection context manager
commits or rolls back its transaction but does not close the connection. Python
3.13 records a ResourceWarning on finalization; retained diagnostic records also
retain the associated connection object. A bounded probe of 100 actual helper
calls reproduced 100 such warnings and RSS increasing from 63.4 to 145.5 MiB.
The initial whole-matrix attempts are failed/interrupted history, not acceptance.

## Regression and narrow fix

Before changing production, both real-SQLite regression cases failed exactly
because the owned connection remained usable after the helper returned:
`test/clients/test_workflow_index_connection_lifecycle.py` exercises successful
commit and handled-error rollback using a pending write, and then checks closure
and the ordering of transaction exit before closure. The patch adds an outer
`contextlib.closing` while retaining the original inner connection transaction
context. SQL, schemas, migration policy and existing exception handling remain
unchanged; rollback of this code change requires no data migration.

After the patch: the two regressions plus workflow-run migrations passed
**13 tests in 1.24 s**; workflow-index migration, database and permission suites
passed **118 tests in 9.57 s**. Black and isort accepted both changed files.
Logs are local under `/home/felni/cao015-final/sqlite-closure-{red,green}.log`
and `sqlite-related-green.log`; raw diagnostics are not published.

## Impact and final acceptance

Graphify was consulted before the patch, but its cached, truncated search did
not resolve this helper precisely. Source inspection established its startup
and isolated-database callers. The change adds a standard-library resource
ownership wrapper without a new internal dependency, SQL contract or schema.
The independent composition reviewer found no blockers: owned connections
close after commit/rollback, including failures; existing legacy rebuilding,
idempotence and failure logging retain their behavior. The final Python matrix
and post-fix resource probe are recorded separately; neither is inferred from
the targeted passing cases.

The post-fix Python 3.13 probe invoked the actual helper 100 times while
retaining every captured diagnostic. It produced **0 ResourceWarnings**, and
RSS remained **63,572 KiB** before the loop, after 50 calls and after 100.
The workflow-index catalog retained the same six columns. No warning filters
or diagnostic-source mutations were added. Probe results are kept locally in
`/home/felni/cao015-final-python/workflow-index-{before,after}.json`.
