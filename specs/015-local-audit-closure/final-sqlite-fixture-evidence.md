# Deleted SQLite bytes fixture — 2026-10-07

Python 3.14's linked SQLite 3.46.1 is compiled with SECURE_DELETE and reports
`PRAGMA secure_delete=1` by default. Python 3.13's linked SQLite 3.53.1 reports
zero. The recovery-compaction regression assumed the deleted credential bytes
remained in its source database without explicitly selecting that condition.
Its source precondition failed before production bundle capture was reached.

The exact existing case failed under Python 3.14 before the change (2.04
seconds). Its owned temporary SQLite connection now explicitly selects
`PRAGMA secure_delete=OFF` before inserting and deleting the synthetic secret.
Both precondition checks remain strict: bytes must exist before and after
DELETE. Bundle verification, published-object credential absence, portable
inventory equality and unchanged source state all remain asserted.

The same case then passed on Python 3.14 (1.48 seconds) and Python 3.13
(2.10 seconds). All three neighboring credential/secret cases passed on each
build: 3.14 in 2.10 seconds and 3.13 in 1.66 seconds. The fixture is
function-scoped and closes its connection and disposes its isolated engine.
No production connection or security policy is altered.

The earlier source manifest is [preserved T083 evidence](final-source-manifest-t083.json).
A new whole-matrix source freeze remains pending the five original diagnostic
runs. The speculative fresh Python 3.10 run was stopped after this new fixture
finding: 770 passed, zero failed, exit 2, 407.50 seconds; its coverage JSON
contains no usable data. It is diagnostic history, not final acceptance.

Independent review reran the case on Python 3.14 (one pass, 1.24 seconds)
and found no security blocker. It identified a pre-existing test-owned
read-only observer without explicit closure. A real Python 3.14 pytest/GC
probe emitted one ResourceWarning before correction and zero afterward,
with the original case still passing (1.66 seconds). The observer now has
an outer closing context around its original transaction context; all
portable-inventory and source-state assertions remain unchanged.

The final observer-wrapper diff was independently reviewed without blockers.
The neighboring credential cases passed again on both linked SQLite builds
after that wrapper change; the exact timings are retained in private logs.
