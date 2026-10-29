# T213 · bounded task-query evidence

2026-10-29 · **Component PASS** on base `9a9be8ecc1601018453eb2abdfe5310c5ec32f7b`.
Native T214 remains pending. OpenCode v2 questions/answers/ACK/API-restart
acceptance is preserved; this work adds evidence for a different operation.

The diagnostic observer can explicitly select `get_local_cao_task`. It binds
the actual argument and returned task/requester identities to once-issued,
private nonce/caller metadata and compares both terminal states. A task DTO
has its own nested `result`; only a sole outer `result` key is an SDK wrapper.
The ledger retains fixed match booleans and allowlisted states, never task IDs,
requester IDs, output, paths, operation keys or error text.

The binding is read once at the first selected call, without waiting. Missing,
unsafe or invalid issuance yields unobserved evidence, without changing the
actual tool result. Pending evidence is bounded and released at completion or
close. Defaults, peer-list capture, grants and existing byte/event limits stay
unchanged. A larger catalog alone does not enable capture.

## Reproductions and verification

- Initial component RED: unsupported binding keyword, 1 failed/1 passed.
- Frozen native file loader RED: dataclass annotation resolution required a
  `sys.modules` entry that the existing loader does not establish. Immutable
  NamedTuple records preserve that actual loader protocol.
- The v1 mixed-observer adapter already failed against the observer at base
  HEAD: it expected a one-line return. AST selection now changes only its path
  argument and preserves keywords. The unchanged v1 suite passes 25 tests and
  20 subtests; this is compatibility evidence, not a new native v1 acceptance.
- Final combined regression: **306 passed, 20 subtests passed, 3 existing
  warnings, 88.73 s**. Suites: task witness, existing MCP observer, native-v2
  fixture contracts and native collaboration launcher. Thread/unraisable
  warnings are errors; coverage configuration and deadlines are unchanged.
- Mypy: no issues in the observer module; Black/isort checks pass.
- Composition check: **5 architecture contracts kept, 0 broken**.
- Claude read reviews `f74a658d4c83434d8eccfef508b2c5bc` and
  `8550800ac303457985946d8dae228025`: GO. Reviewers did not execute checks;
  the lead ran and inspected the checks above.

The first combined run had 305 passes and one unchanged 30 s import-subprocess
timeout; an isolated run also timed out. Both failed logs remain private.
Instrumenting the exact worker subsequently completed in 17.669 s with every
source module present and its source map exact. At 10 s its stack was in
SQLAlchemy file loading on DrvFS. The subsequent unchanged full run passed;
this does not establish cold-start latency or prove the historical timeout's
complete cause. Parent checks use the previously verified byte-identical Linux
dependency snapshot and canonical CAOS source; the isolated child retains its
original environment, interpreter and 30 s deadline. No global installation.

Private logs: `/home/felni/.cache/caos-t213-task-witness-20261029/`.
Source identities and machine-readable scope: [receipt](validation-t213.json).

## Composition review

Changed surfaces are the diagnostic MCP observer, its strict reader, and the
v1 copied-observer adapter. Reviewed neighbors are the registered production
FastMCP handler, its HTTP requester binding, the actual ORM-to-task DTO producer
and frozen native loaders. Tests cover handled HTTP failures, wrong argument,
owner/task/state mismatches, structured/text conflicts, duplicate/oversized
JSON, invalid issuance, retry/interleaving, close races and bounded retention.
The real handler and DTO producer run offline with controlled HTTP responses;
there are no persistence changes requiring a database for this component.
No schema migration, grant change, native model call or provider run occurred.
Graphify lacked current observer nodes; source call paths were checked and
foreign generated files preserved, with no rebuild. **Verdict: component PASS**.

## Remaining acceptance

T214 must independently prove the actual current OpenCode Go HTTP offer,
one successful native query of its own completed fixture, and normal owned
cleanup under the original 180 s/6144 MiB gates. The existing T166 adapter only
accepts peer-list evidence and cannot be reused unchanged. A task GET can
reconcile state; it is not a pure read. No native coordination, complete
OC2-04, task-execution or global-provider gate closes from these component tests.
