# T080 — journal, store and setup connection ownership

## Cause and scope

The incomplete T079 five-version runs had no failed assertions but later modern
Python API phases still accumulated connection ResourceWarnings. A real Python
3.13 private-profile control called `workflow_journal.get_run("missing")` and
`read_events("missing")` 100 times each: **200 ResourceWarnings**; retaining
those recorded warnings increased RSS from **90,234,880 to 297,431,040 bytes**.
SQLite transaction context exit commits/rolls back; it does not close the handle.
This demonstrates resource ownership and warning-capture retention, not a claim
that production always retains warnings or has the same RSS growth.

A source ownership audit then identified **55 normal owned contexts**:
journal 34; browser reference schema 1; approval store 3; workflow spec service
6; generation persistence 1; Work workflow plans 2; recovery bundle 8.
Their results are fully materialized before return. External closing wrappers
preserve the original inner transaction contexts, SQL, explicit commits,
error fallbacks, migration caching and borrowed connections.

Real corrupt-file controls also demonstrated one ResourceWarning each from
terminal migration, browser connection setup and Work connection setup.
These **three setup/error owners** now close before failed handoff. Browser
rethrows the original error for its existing caller conversion; Work protects
setup with its existing finally; terminal migration retains its fail-soft
warning and explicit per-column commits, with no added transaction wrapper.

## Regression evidence

- Journal: eight owned success/error cases failed the real closed-handle
  assertion before the fix; one borrowed run/event transaction control passed.
  The nine new cases plus journal neighbors passed: **77 tests**.
- Adjacent stores: **13 cases** failed the same closed-handle assertion before
  correction, then passed. The initial parametrization included an inapplicable
  empty-plan query-error cell; it was removed before the corrected RED run,
  which had 13 failures and no skips. No production failure policy was changed.
- Setup/reference owners: **7 closure failures / 5 passing controls** before
  correction, then **12 passing cases**. Browser/Work/database/permission
  neighbors passed: **168 tests in 12.92 seconds**.
- Root journal/store/setup/approval/spec/recovery selection passed:
  **248 tests in 29.44 seconds**. Independent task review separately ran all
  **34 new ownership cases: passed**.
- The same 200-call journal control after correction: **zero ResourceWarnings**,
  RSS **89,612,288 bytes before and after**. Approval reads/grant, cold browser
  catalogs, empty workflow listings and recovery compaction also emitted zero
  ResourceWarnings in the repeated real controls. All three corrupt-file
  setup controls emitted zero, retaining their original errors.
- Six normalized source ASTs matched their saved pre-wrapper versions after
  removing only new closing imports/wrappers. Independent source review found
  no escaped cursors, lazy results, changed transactions or borrowed ownership.
- Mypy: **398 files clean**; Black: **1,192 files unchanged**; isort: passed.
  Composition: **400 files, 1,680 dependencies, five contracts kept, zero broken**.
  Real two-process peer integration after these changes: **1 passed in 33.09s**.

The [T080 source manifest](final-source-manifest-t080.json) binds 1,335 source/test files;
its SHA-256 is `fd1d17a9bd9159a2d6f59ce7342a60dd16408f3fb21d322036d75b2a7398a21a`.
The full corrected five-version matrix remains pending in
[matrix evidence](final-python-matrix-evidence.md). Earlier interrupted runs
remain diagnostic history; no incomplete coverage is counted as acceptance.

## Private evidence identifiers

Raw logs and probes remain local under `/home/felni/cao015-final/`. They are
excluded from publication; this maintained report records their conclusions.

- `workflow-journal-ownership-probe.json`: SHA-256 `c5d2a3690d1c512f5f9a1df00b972b8a213ac1d9ba6741f82c12f3a083bd76c3`.
- `workflow-journal-ownership-after.json`: SHA-256 `58d9b336fbc4c3c31d27a17f19916df811e5ece2d96bf67e2f0fa0e9d628fdb1`.
- `sqlite-owner-real-probe.json`: SHA-256 `d210a6e702276e46713b17408dea96f0147ceafd5f096124e1cb4a86ad62b67a`.
- `sqlite-all-owner-after-output.json`: SHA-256 `15c1e9a935c78f2caa46378c61c474e20048575dc26694968ad779e10467e336`.
- `sqlite-owner-error-probe.json`: SHA-256 `657342167c845e58cf42ddabf7a0bbeb76e1ea9eb3d766ae69fd41ac727d6832`.
- `sqlite-owner-error-after-probe.json`: SHA-256 `d96da7e832d5c2d04b75eebba9bb780263a5dc7b1e9285684e82bbfc71215375`.
- `journal-ownership-red.log`: SHA-256 `0b5c0d31a9394c6f531ba606b7eccf29113712d5c1ac189bc30c56d854ebff48`.
- `store-ownership-corrected-red.log`: SHA-256 `50b2f95550b4194bff563155c2023b79cdc7c7dd19772329866c862a14c3f776`.
- `sqlite-owner-setup-red.log`: SHA-256 `928977680bdaa9c56ca7bcf54ceb66fac7355a6c9eb6a5fe3e6e6f6d403f2816`.
- `all-store-ownership-green.log`: SHA-256 `920018d8ca2d5e03b184a2b8ad906755feb43bf1136defd5fe7a29c11401c3f0`.
- `journal-store-ast-proof.json`: SHA-256 `107f689ecae96544f378ab60035c10399ad1a60c1e12a5fa3012b95638345fd9`.
