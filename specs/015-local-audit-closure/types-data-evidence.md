# T057 data typing closure evidence

## Scope and diagnosis

Owned source: clients/database.py, runtime_channel_schema.py, work_repository.py,
beads.py, models/work.py, services/beads_service.py, work_service.py.
Inspected initial git status and AGENTS.md; retained the existing large dirty
worktree and untracked implementations. Root performed Spec 015 / Graphify impact
routing. Read relevant AI_WORKFLOW sections, systematic-debugging,
system-composition-review, verification-before-completion.

Reproduction: project mypy.ini configuration, focused mypy --follow-imports=silent
on those seven files returned 76 errors. Dynamic SQLAlchemy bases and bare Column
attributes exposed class descriptors as loaded scalar instance values; these
errors propagated into persistence consumers. Other causes were list method
shadowing builtins.list, Optional subprocess pipe/binary fields, function-scoped
variable reuse across string/numeric loops, opaque validated process identity
mappings, unannotated helper return contracts, and Pydantic's computed-property
decorator shape.

TDD decision: retain the failing executable type diagnostic as red proof for
annotation corrections. Preserve runtime semantics and validate the real SQLite
persistence/integration suites; no mirror tests added for annotations.

## Changes

- DeclarativeBase / Mapped / mapped_column declarations for 256 local and 28
  remote mapped columns. Preserve every SQL name/type/nullability/key/default.
- Export ProcessIdentityV3 / BubblewrapProcessIdentity TypedDict shapes only after
  the existing exact field/type/content digest checks. Repository reads retain
  shape validation and corruption rejection. Limited casts document already
  validated heterogeneous JSON payloads and numeric fields.
- Scope numeric validation variables separately, type SQL count result, annotate
  loaded session use and service delivery/result helper returns.
- Beads preserves callable subprocess bounds/reaping, narrows known PIPE handles
  and resolved executable, disambiguates builtins.list from BeadsClient.list,
  preserves None-task asdict TypeError / uncertain-write behavior, and exposes
  existing operation return types.
- computed_field still constructs the property itself; serialized delivery_phase
  remains derived from authoritative state.

## Verification

Commands use TMPDIR=/home/felni/tmp-cao015-types-data.

1. `.venv/bin/mypy --follow-imports=silent --cache-dir=/home/felni/tmp-cao015-types-data/mypy-cache` plus seven owned files: **exit 0, no issues in 7 files**.
2. `.venv/bin/pytest -q test/clients/test_database.py test/clients/test_database_permissions.py test/clients/test_work_repository.py test/clients/test_integration_008_beads_adapter.py test/services/test_work_service.py test/services/test_integration_008_beads_operations.py test/services/test_integration_008_beads_work.py`: **185 passed**, five warnings.
3. `.venv/bin/pytest -q test/runtime_channel test/clients/test_work_bubblewrap_process_identity.py test/services/test_work_bubblewrap_cleanup_recovery.py`: **106 passed, 1 skipped**, five warnings. An initial invocation named absent test_work_process_identity.py and returned collection exit 4; corrected to the actual existing test files before obtaining this result.
4. Final affected Beads helper regression: `.venv/bin/pytest -q test/services/test_integration_008_beads_operations.py test/services/test_integration_008_beads_work.py`: **13 passed**, five warnings.
5. Captured schema metadata immediately before/after ORM conversion: **all 33 tables equivalent**, including all column names/types/nullability/keys/defaults and CHECK definitions (normalize only repr memory addresses). No SQL migration.
6. `project-composition-check "$(cat .ai/project-name)"`: **exit 0, PASS**.
7. isort/black owned source; git diff --check: **pass**. Final scoped diff reviewed; inherited dirty database changes remain intact.

## Composition review

Changed contracts are ORM instance versus query typing, repository validated
identity reads, Beads operation callbacks, and Work delivery helpers. Inspected
neighbor consumers in runtime publication, terminal lifecycle, memory models,
and Work process cleanup; notified relevant owners of precise optional/scalar
and identity types. Persistence owns the same tables; transactions, ordering,
receipt CAS, hashes, replay refusal, uncertain write receipts, timeout/process
cleanup and nullable legacy columns retain their prior implementation.

Real dependencies: SQLite through existing repository and ORM regression tests,
real bounded subprocess adapter tests, remote operation/publication tests.
Architecture/composition gate passed. No HTTP wire schema changed, so independent
consumer contract generation was unnecessary. Root performs the final global
mypy and full composition review after other domains settle.

Verdict: **PASS for this focused domain**. Global downstream optional/TypedDict
consumers are being reconciled by their owners; focused checker does not establish
whole-repository completion. No commit/push performed; no durable vault save is
needed for routine annotation edits.
