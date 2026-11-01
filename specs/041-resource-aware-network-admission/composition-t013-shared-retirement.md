# Composition Review Report — T013 shared retirement and async ownership

Verdict: **PASS WITH RISKS for this bounded integration checkpoint. T013 remains open.**

## Changed subsystems and neighbors

PostgreSQL authority/migrations/counters, delivery lease ownership, storage scopes and worker handoffs, shared coordinator, runtime lifecycle, Inbox/Work dispatch, interaction polls, terminal initialization and seven providers. Source review followed bootstrap/admin authorization → runtime activation → local lease → metadata transaction → shared preparing/active → physical data work → ready hold → durable release → local retirement → receiver notification. Existing Work guards, transport receipts, provider initialization, cancellation and storage-close registry were checked as neighboring boundaries.

## Contracts and invariants

Migration versions 1/2 retain their original checksums. Additive versions 3/4 protect metadata-credit authority and persist each operation's metadata mode; retries cannot promote raw or incomplete operations. Exact generation/binding membership and complete storage coverage are checked transactionally. Credits are one physical metadata session per enrolled process generation, charged in the same incremental counter as allocations; revocation does not release them. Explicit administrative enrollment remains separate from trusted current-process activation. DTO construction is not authority.

The coordinator has one immutable operation ID and exact retirement hold per local lease. No SQL or external callbacks run under local ledger/registry guards. Receiver and original shared charges survive cancellation, lost acknowledgements and uncertain physical close. Cleanup uses the existing protected serialized session; it cannot free its own hold before that session closes. A ready notification is not the authoritative physical witness. Shutdown joins activation, participants and cleanup before storage retirement. A failed Thread.start after a real start joins the worker before dropping its claim. An already stopped coordinator cannot silently enable local fallback.

Logical async scopes preserve handoff identity but authorize no SQL. Physical scope entry on the event loop is denied in shared mode, including attempted promotion over a logical scope with another owner. SQL-capable provider/startup/poll callbacks use joined owned workers; provider async initialization remains on the original loop. Entry denial preserves PENDING before selection/send; failures in an admitted body are not converted into deferral. Completion explicitly wakes the exact parked receiver after durable release and local retirement, outside guards.

## Executed checks and real dependencies

Four final serialized selections: provider/startup126, composed ledger150, owner/runtime67 and interaction waits20: **363 distinct cases, zero failures/errors/skips**. All 28 changed production files match every executed candidate archive; all 53 changed Python files match at least one final archive. These are four selections, not a claimed full-project suite. Native PostgreSQL was reused with verified container and owner identity, unchanged pool/resource gates; final state: zero other clients, test databases and test roles. See [validation](validation-t013-shared-retirement.json).

Real PostgreSQL scenarios include complete metadata enrollment, concurrent counter/membership fencing, revoked admission, CAS/idempotency, uncertain commits, protected cleanup, Inbox budgets1/6, Work and agent-step ownership, and shared runtime activation/drain with a real repository. Runtime tests use controlled observations and simulated transport; they do not prove physical topology or native model-provider acceptance. Thread/fork/cancellation tests use real threads/process boundaries where applicable. Existing HTTP/MCP question/answer/ACK and native-wait identity compatibility are regression checks, not new native CLI runs.

Format/import/AST:53files; mypy:28production files. Full repository Import Linter:446files/1993dependencies, five contracts kept/zero broken. This change adds a private same-process repository/coordinator port and additive PostgreSQL schema; it changes no independently deployed HTTP/SDK contract, so new Pact contracts do not apply.

## Failures, review and disposition

Reproduced product defects included premature local retirement, uncertain close/accounting fences, unauthorized scope promotion, parked receiver stalling after global cleanup, cleanup thread startup failure, Inbox/Work entry-denial boundaries and SQL remaining on async poll/startup/provider paths. Regression tests cover their corrected boundaries. Historical failed phases are retained; failed fixtures (exception construction, observation installed before start reset, fixed polling sleeps and a test callback running raw SQL on the loop) are recorded separately and not relabeled as product reproductions. Assertions and resource criteria were preserved.

Independent Codex Luna Max/ECC and Claude read reviews were checked against source. The proposed infinite Inbox retry was rejected: consuming a wake does not create another event; the reproduced opposite failure was a missing receiver wake, now fixed. The agent-step revalidation gap is additionally guarded by existing fresh Work checks before input/receipt effects; revocation coverage passes. No pure-async preparation bypass was added because physical scope conveys SQL authority.

## Structural and durable review

Private Graphify AST refresh:28source files,1755nodes/4323rawedges,463dangling endpoints, zero model tokens and no failed sources. This is a partial graph, not full-project graph health; important paths above were verified in source. All twelve foreign tracked Graphify files were preserved. Durable lifecycle/authority lessons were applied through the verified Obsidian transaction markdown-20261101-shared-retirement-async-ownership to three pages; strict lint reports zero issues. Generated graph facts were not copied into the vault.

## Residual gates

Provider quota/rate semantics and complete production funding remain T013 work. T014 still needs an independently funded custodian and exact generation/effect closure evidence; runtime.retired, PID exit, TTL, cancellation or membership revocation cannot retire metadata credits or unknown effects. Deferred initialization and complete startup/combined physical budgets remain unverified. Non-SQL filesystem/tmux work can still block the loop; no performance acceptance is claimed. Shared activation is trusted composition, not yet durable deployment configuration/API/SDK/UI. T018–T023 and physical M1/M2/M3/global gates remain open as tracked. This checkpoint neither reopens historical OpenCode v2 acceptance nor closes native-provider gates.
