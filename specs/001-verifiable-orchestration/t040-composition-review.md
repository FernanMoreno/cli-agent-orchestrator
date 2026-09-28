# T040/T092 composition review

**Final verdict: PASS — the sole blocking owner/cache consistency defect is resolved.**

## Final bounded re-review

Reviewed `/tmp/caos-t040-cache-review.diff`, `/tmp/caos-t040-cache-fix.md` and the current cache/provider/regression source. The memory provider's key now contains both `base_dir.resolve()` and the actual `_legacy_repository().path.resolve()`, plus provider, scope, scope ID and lint mode. These are stable persistent owner identities rather than instance IDs. Authorization and committed audit intent still precede the lookup; cache hits still complete their own audit before returning. The complete key partitions both cached entries and single-flight locks without changing TTL behavior.

The real-SQLite regression covers different wiki/database, shared wiki with different databases, and shared database with different wikis, without clearing cache between owners. It checks distinct nodes/relationships and verifies that a fresh facade for the same owner reuses its cached projection. This directly covers the original failure, including either identity dimension independently. No new blocking breakage identified in the bounded correction.

Post-correction evidence reviewed (not rerun here): `/tmp/caos-t040-cache-red.log` records **3 failures** before the fix; `/tmp/caos-t040-cache-green.log` records **51 passed** after it; `/tmp/caos-t040-cache-composed.log` records **177 passed, 4 warnings**, process exit 0 supplied by root. `/tmp/caos-t040-final-composition.log` records refreshed **PASS**, **254 files / 912 dependencies**, **2 kept / 0 broken**. The earlier 1106-pass aggregate and 5-pass CLI run below predate this final cache change; they are not represented as freshly rerun post-fix. The 177-test post-fix composition run provides the affected HTTP/MCP/graph boundary verification.

## Original blocking finding — resolved

**P1: Graph cache can return one memory owner's content while auditing another owner.**

`src/cli_agent_orchestrator/graph/providers/memory.py:72` keys the process-global `_CACHE` only by provider, scope, scope ID and lint flag. The same class accepts a different `MemoryService` per instance (`:49-55`) and now audits every request through that instance (`:57`). `graph/cache.py:108` returns a fresh entry without invoking the new owner's builder.

Concrete composition: construct providers A and B using separate persistent SQLite engines and wiki base directories, with the same `global` scope and lint flag. Project A to populate the cache. Project B within the TTL. B records authorized/completed audit rows in B's database, but returns A's nodes/edges and never reads B's files or relationships. The same mismatch arises with a shared DB and different configured wiki roots. This violates the reviewed invariant that graph/relationships/audit follow the actual owner and makes B's successful audit misleading.

The cache issue existed structurally before this patch, but the current T092 integration explicitly binds graph effects and audit to the owner; leaving the cache outside that binding prevents completion of that composition contract. No sole-owner process invariant is enforced by the supported constructor.

Resolution applied: the cache is partitioned by stable owner identity covering both actual persistent SQLite authority and canonical wiki root. Scope/lint dimensions, fresh authorization and per-hit audit are preserved. The new three-case owner regression complements the existing single-owner audit test. Historical line references above describe the reviewed pre-fix defect.

## Reviewed boundaries and nonblocking conclusions

- Consulted the existing FR-014/T038/T040/T092 spec/tasks, supplied diff package and actual source. Graphify query mapped KnowledgePolicy/MemoryService/WorkRepository/HTTP/gateway neighbors; truncated graph results were used only for orientation and checked against source.
- Reviewed-memory facade delegates to KnowledgePolicy/KnowledgeRevisions. Policy checks principal scopes, exact DB transaction path, current job/grant/revision and capability chain. Reads and writes insert audit in the same transaction before returning; audit insertion failure prevents returned content. Legacy fallback is refused by configured reviewed services. Approved instructions exclude tombstones, stale and unapproved revisions.
- Legacy MemoryService derives WorkRepository from its actual idle SQLAlchemy session bind, requires persistent SQLite and records committed intent before body execution. Decorators record completed/failed before return and propagate typed audit failures, including cancellation via BaseException. Partial filesystem outcomes are explicitly distinguishable; no filesystem/SQLite atomicity claim.
- Verified JWT callers are rejected on legacy HTTP memory routes; denied audit precedes 403, and audit unavailability maps to 503. Graph middleware precedes cached projection. MCP/remote routing uses the authenticated authority and does not silently fall back to a local reviewed store.
- Relationships inherit the owner session and policy. Lint uses that owner's read-only metadata engine while audit/derived relationships use separate transactions; policy/audit failures propagate through optional enrichments. Repair wraps inspection/apply using the same owner and existing migration logic.
- Shared redaction occurs before context limits, compiler/lint model input and persisted shared revision limits. Archive body skip-versus-redact semantics remain explicit; historical tags are independently sanitized. Graph output is redacted even on cache hits, but that does not resolve the owner mismatch above.
- Schema v12/v13 adds separate append-only audits. Legacy audit SQL enforces matching authorized intent before terminal completed/failed, unique terminal phase and immutable rows. Existing prior migration history remains represented; v13 is the predeployment finalized schema described in the brief.

## Evidence reviewed, not rerun

- `/tmp/caos-t040-final-green.log`: **1106 passed, 7 warnings**, supplied process exit 0.
- `/tmp/caos-repair-cli-final.log`: **5 passed**, supplied process exit 0.
- `/tmp/caos-t040-composition.log`: **PASS**; Import Linter analyzed **254 files / 912 dependencies**, **2 contracts kept / 0 broken**.
- Temporary real SQLite and real isolated CLI are included in supplied verification. API/MCP contract tests and integration scenarios cover authorization, redaction, failed audit, partial writes, relationships, archives, repair and single-owner cache. The composition wrapper itself did not execute tests from empty `tests/`; integration evidence comes from the separate suite.
- No tests or providers were invoked by this independent review. Production was not edited. Existing fixture edits authored by this reviewer were already verified separately; review focused on production composition.

## Residual limits

- Authenticated legacy APIs intentionally return 403; clients needing shared access must use v1 knowledge authority.
- Secret detection is regex-based and is not universal credential detection.
- Full repository tests are **not** accredited; previously observed broader API failures remain disclosed.
- T091 internal delivery tests do not establish T017–T020 public integration. T043 and the rest of the roadmap were not implemented or expanded.
- This reviewer did not execute tests during either review. The final verdict incorporates the implementer's recorded RED/GREEN regression and root's refreshed composition evidence. Scope remains T040/T092; no roadmap expansion.

## Workflow closeout

Final diff-check passed. Graphify refreshed locally: 30,458 nodes, 59,757 edges,
1,404 communities; nine affected modules verified against source. Generated
graph artifacts remain in the repository, not the vault. Durable operational
invariants are recorded in docs/work-recovery.md. T038/T040/T092 are closed;
T043 is not started. Execution stops here at the user's request.
