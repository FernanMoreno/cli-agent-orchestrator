# Tasks: PostgreSQL persistence

Dependencies: foundation native driver contract precedes all subsystem ports. US2 requires native PostgreSQL backup/restore; US3 validates US1; US4 requires configuration and native recovery. Pending recovery-safety tasks T077–T080 must pass before T075 Docker acceptance and T076 final gates. Tests are requested and must use real PostgreSQL. SQLite is removed from active product, package, documentation, fixtures and functional tests. No commit or deployment authorization is implied.

## Preparation

- [x] T068 [US4] Wait for the managed PostgreSQL final TCP server before bootstrapping roles: probe explicit loopback TCP, preserving owner/database, timeout, retries and isolation. Reproduce premature Unix-socket readiness against the actual function and pinned image entrypoint; add deterministic bootstrap/timeout regressions, run the full installer unit module, then all three real Docker gates on a newly sealed image before the complete backend regression. Preserve R2 failed native gate and interrupted R16 evidence; the original psql stderr is unavailable. **2026-10-19 evidence:** the complete `test/scripts/test_docker_install.py` module passed 70/70 in 24.31s against current main; the current-source T019/T020/restart packet passed 16/16; and the newly sealed application image `cao-personal:caos-main-20261018-t128-bd46696c` (`sha256:f1e3b76334c0af8a886be8a90c376fc7a8ab123aa1e6db4369d8c36141037fd9`) passed installation, PostgreSQL restore and browser-cookie gates 1/1 each with zero skips/failures. The actual pinned-image entrypoint reproduction passed: during init, default-socket `pg_isready` returned 0 while loopback TCP returned 2; after init completion, final loopback returned 0. Sealed private receipt `/home/felni/.cache/caos-t068-entrypoint-readiness-20261019/receipt.json` SHA256 `e02c0862895c6095d5e7e5c9698ea14afa2da4936a2d9933c34558964c8d5104`; the Docker installer sources used by the gates match the current files byte-for-byte; and the existing container ID `e2b7b35a921276dc80b2caf93863b8b00fd3b7ab9a5dfd610999fe7638035b6b` remained healthy with unchanged `StartedAt` and restart_count 0.
- [x] Spec021/T069 [US3] Audit every native, leased and test instrumentation wrapper that forwards `prepare`; preserve omitted/None default call shape and forward explicit True/False, row factories, lease checks and timing boundaries unchanged. Add direct and pooled regressions for every wrapper, run the affected modules, and confirm no wrapper rejects a supported native call.
- [x] Spec021/T070 [US3] Update the private PostgreSQL test fixture profile to v3 with `max_connections=256`; keep eight xdist workers and the production private memory-audit physical-session cap of four unchanged. Verify worker/schema isolation, server settings and the runtime cap independently, then run fixture lifecycle and connection-pool regressions.
- [x] T071 [US1, US2] Remove every active SQLite path from CAOS source, dependencies, CLI, package data, build artifacts and functional fixtures/tests. Remove SQLite import commands and paths; reject explicitly retired command/flag forms before filesystem, install-lock or database effects. Add a source/package guard proving zero active SQLite support and preserve all existing files/state on rejection.
- [x] T072 [US1] Replace SQLite-backed in-memory PostgreSQL schema/type validators with real PostgreSQL catalog and migration contracts. Preserve type widths, nullability, defaults, constraints, trigger behavior, installed DDL, ledger rows and checksums; prove existing receipts remain read-only and unchanged.
- [x] T073 [US4] Retire the legacy OKF `sqlite-dump.json` reader/format and its fixtures. Preserve modern OKF directory and tar behavior using only SQLite-free payloads. Prove explicit legacy rejection and malformed-archive failures happen without altering source bundles or destination files.
- [x] T074 [US1, US4] Port functional cases that rely on SQLite import, legacy recovery readers or SQLite emulation to native PostgreSQL contracts; retain and strengthen PostgreSQL receipt/provenance tests as read-only behavior tests without an importer. Update active documentation/examples and add source/package checks; leave historical validation records untouched.
- [x] T077 [US4] Exclude stale `cao/pg-client-*/pgpass` client scratch credentials from native deployment backups while preserving those source scratch directories byte-for-byte; verify the archive contains no passfile and the source remains unchanged in `scripts/personal_deployment.py` and `test/scripts/test_personal_deployment.py`.
- [x] T078 [US4] Preflight absence of every configured main/auth/peer destination schema before the first restore write. Record each created schema's OID and owner identity; on later-group, verification, or auth-invalidation failure compensate only schemas whose OID and owner still match, and never drop a foreign/replaced schema. Prove source archive and unrelated destination schemas remain unchanged in `scripts/personal_deployment.py`, `test/scripts/test_personal_deployment.py`, and native PostgreSQL restore contracts.
- [x] T079 [US4] Before any rollback stop, rename, or start, inspect the root-ownership label on both current and previous Docker containers. Reject missing, reused, or foreign names with zero container mutations; prove both-owned success and foreign-name no-effect behavior in `scripts/docker_install.py` and `test/scripts/test_docker_install.py`.
- [x] T080 [US4] Isolate the restore maintenance process HOME/CAO_HOME_DIR in a disposable Docker tmpfs owned by its UID/GID with mode 0700. Reproduce cold browser-auth imports creating an unpublished destination; preserve explicit root/config, database and authentication checks, destination-absence guards and backup immutability. Add fresh-process and command-contract regressions, run full installer/deployment tests, then all three real Docker gates against a new sealed image. Preserve R4/R5 failures and the private R20 diagnostic.
- [ ] T081 [US3] Reproduce the strict covered memory round-trip failure against the frozen product source and record the exact test-body duration, source/profile/worker/plugin binding, collected/skipped nodes, and current routine-query `EXPLAIN (ANALYZE, BUFFERS)` evidence on the dedicated native PostgreSQL fixture. Trace the first expensive boundary from `MemoryService._legacy_repository()` through Work verification to `catalog_manifest`; do not change source, test timing, SQL, selection or thresholds. Preserve the R23 RED receipt and T082's rejected-candidate evidence; keep T081 open until the reproduction and query diagnostics are bound to one complete source/fixture/selection receipt. A later no-coverage 11-module memory regression selected 234 tests: 233 passed and the unchanged round-trip assertion failed at 1.161985059 s (JUnit SHA256 `2673890c4974210e28e3f7a45c25bceaa05aa72c41092eb68168b1863ddc6794`). This confirms the current failure but is not the strict covered three-run acceptance, nor a substitute for source-bound query-plan evidence. Current-source evidence now includes the loaded full-backend failure at 1.345s and a separately sealed three-process cold covered cohort passing at 0.771994 / 0.776891 / 0.817269s without retries; the loaded SLO remains open.
- [x] T082 [US3] Evaluate a private test-first candidate for the five-family routine-dependency query only if T081's plan and controlled measurement identify it as a material cost. Compare the current OR/EXISTS query with a scoped `target_objects(classid,objid)` join on `pg_depend`; retain the separate trigger UNION and exact deduplication/`objsubid` behavior. Require byte-identical normalized nine-family manifests and catalog digests across main/auth/peer, multi-component, empty and drifted schemas, plus caller-owned snapshot and migration identity/history/error-order contracts. Port a product change only if the candidate measurably improves the strict round-trip without changing any existing authority, lease, DDL, checksum, commit or freshness boundary; otherwise record the no-change result. Completed evaluation: diagnostics showed exact parity on 71 tables, but the second comparison was slower (original 22.103 ms; candidate 22.929 ms), so reject the rewrite and keep product SQL unchanged. This records no NFR pass and does not close T076 or the strict round-trip NFR.
- [ ] T083 [US1-US4] Close the regression omissions after T081/T082: run the complete unfiltered eight-worker backend and Apps suites with their unchanged coverage options and floors (backend >=87%, Apps >=90%), resolve every collected failure, reconcile collected/passed/failed/skipped counts and each skip reason, and run the unchanged strict NFR in three independent cold covered processes with all three measurements <1.0 second. Do not prewarm, deselect tests, add exclusions or suppress errors. Run host-gated checks when prerequisites are available; otherwise identify each exact pending gate and prerequisite. Record source seals and results for T076's final aggregate signoff; T076 remains open until all its gates pass. The historical local 37-case E2E selection passed 24 and failed 13 with 0 skipped (8 approval direct-runner, 1 `422 prepared_plan_required`, 3 tmux initial-500 and 1 YAML tmux); preserve its failed receipt. The revised 37-case scope is accounted as 36 local E2E cases and one prepared-node Docker selector, both green on source/test snapshot `a88eef6b676705b4bff9f579d8b7dd197ab6f79971e19d80d8086de7e73d40e1`: local 36 passed/0 failed/0 skipped in 146.92 seconds (receipt `nfr-fix-r23/service-r2/owned-service-r1/backend-run/local-e2e-r4/artifacts/pytest-local-e2e-r4.log`, SHA256 `274fc43e9b60b9ae007fe83351a23235a4391f0837538f7179c709ff7ac2126c`), and Docker 1 passed in 85.23 seconds (R6 receipt above). This closes the local E2E omission category, not global T083/T076. The R3 runner exit-5/zero-collected stale-node receipt remains preserved; the removed script-parameter selector no longer exists. The focused R3 T019/T020 selection passed 14 tests with product source unchanged. The source-bound Apps run passed exactly 92 tests across 12 files on 2 workers at 478/531 covered lines (90.01%) in `frontend-final-r4`. The source-bound Web run passed 504 tests across 45 files; TypeScript and Vite builds passed, and all five build files matched main (`web-final-r2/final-receipt-r2.json`, SHA256 `1209876fcded57310a57c0964a5598a1f61154ce9cd033ce7516a45e8f2bd0c9`). Source-guard findings were zero before and after (this is not a test count); 13/13 provider CLI binaries were visible on PATH (availability only, not authentication or live-generation evidence). R21 is the latest completed full backend run: 17,390 unique selected nodes, 17,252 passed, 113 skipped and 25 unique failed node IDs. Its phase summary reports 15 FAIL and 12 ERROR events (27 failure-phase reports); two nodes failed in multiple phases. The run exited 1 during postrun verification. Coverage measured 67,793/77,816 statements = 87.1196155% with branch coverage disabled: above the line floor, but not a passing full-suite gate. Older R13 coverage was 68,979/79,530 = 86.733308%. The final R3 three-run strict receipt failed all three unchanged `<1.0 s` checks at 1.0844142050045775 s, 1.0654824020020897 s, and 1.0055572290002601 s; the source and HOME guards passed and no prewarm was used. An earlier diagnostic body reported PASS without a logged duration and is not acceptance evidence. T097 selector evidence is 5 passed; the R3 QEMU run completed partially and is recorded in the final acceptance update. T088's actual Linux PostgreSQL/Cargo gate passed; macOS matrix execution remains pending. Current main-bound R5 evidence supersedes the older counts for the final run: 18,391 selected with identical eight-worker collection; 18,153 passed, 2 failed, 236 skipped, 0 errors, 71 subtests; 68,538/78,566 statements (87.2362%); source before/after unchanged. The two failures are the loaded memory round-trip at 1.345s and capacity visible-registration p95 at 3,226.5ms. The independent current-source cold NFR cohort passed 3/3 at 0.771994 / 0.776891 / 0.817269s without retries. The same capacity selector also passed in isolation with unchanged coverage at p95 270.3ms (20/20 creates, 100 visible; lock-wait p95 691.5ms), while the full-suite failure remains open as an environment-contended result. T083/T076 remain open.
- [x] T075 [US4] Replace SQLite-import Docker acceptance with real PostgreSQL backup and isolated restore gates. Cover all main/auth/peer scopes, full schema/data/receipt verification, compatible upgrade/rollback, source backup preservation and fail-closed unknown state. Run all three Docker gates against a newly sealed image before the full regression.
- [ ] T076 [US1-US4] Run the full new backend, Apps, web, integration and installer regressions; three strict cold native NFR passes; coverage ratchets at backend >=87% and Apps >=90%; architecture and dependency-boundary checks; Graphify refresh; and project composition review/check. Do not lower thresholds, add exclusions, or report retired SQLite-import tasks as completed.
- [ ] T067 [US3] Repair the capacity test's transparent SQL timing adapter to accept and forward the native optional `prepare` keyword, preserving default call shape, timing boundaries, twenty concurrent sources, one hundred questions and the original visibility threshold. Reproduce the TypeError using the actual adapter/catalog ASTs before changing the test; verify forwarding for omitted/None/True/False, run the complete capacity module, and run the complete corrected backend regression. Preserve the interrupted R15 evidence; product code remains unchanged.
- [ ] T066 [US3] Port the measured zero-parameter prepared role verification and compound pool reset/settings read. Preserve fresh permission checks/error order, bounded SQL-only caching, all checkout/return resets, seven settings/identity/IDLE/autocommit guards and independent audit commits. Test live role/ACL drift after preparation, generic-plan reuse across resets, tuple row owners, nextset/fault cleanup and pool lease boundaries; require native regression, independent composition/Graphify review, three actual-source cold covered NFR passes and full R15 regression.
- [ ] T065 [US3] Port the measured exact-null routine identity key and catalog-only acyclic JSON digest helper; preserve general `_digest` serialization/cycle rejection and every fresh verification. Prove parity for non-null/overloaded identities, Unicode/JSON edge values/shared acyclic references, native main/auth/peer drift and pool boundaries; require three fresh actual-source covered NFR passes, independent composition review and final full regression.
- [ ] T064 [US3] Port the measured parameterless catalog plan with safely quoted literals and bounded pure SQL rendering cache; support explicit prepare through native/pooled Record cursor guards while preserving default execution. Verify main/auth/peer family digest parity, fresh drift rejection, first-call generic plan reuse across lease resets, tuple-row borrowed owners, expired leases, unchanged 21 full checks/20 audit commits plus the separate read-only initialization commit, three actual-source cold NFR passes and final full regression.
- [x] T084 [US3] Measure the PostgreSQL-18-only `NOT NULL` definition fast path without changing product source. Parity held on the 71-table fixture, but R1 was 22.310 ms original vs 22.460 ms candidate; R2 EXPLAIN was 39.728 ms vs 35.874 ms while its five interleaved reads did not support a benefit; and paired R4 was 20.741 ms original vs 27.301 ms candidate with 12 interleaved reads showing no clear gain. R3 failed with `SyntaxError` before tests. Reject the candidate: no product change and no NFR pass. This closes only the experiment; T081, T083, and T076 remain open.
- [x] T085 [US3] Move `_sanitize_for_log` and its four regexes into pure-stdlib `src/cli_agent_orchestrator/services/log_sanitization.py`; change `audit_log.py` to import the leaf directly and preserve `wiki_compiler._sanitize_for_log` as the same callable re-export. Add `test/services/test_log_sanitization.py` proving byte-identical sanitizer behavior and that importing `audit_log` does not load `wiki_compiler`. Preserve current audit and compiler callers; do not move or change append/scoring behavior. Focused evidence: 137 passed/3 warnings in 75.77s for the new test and four caller modules, plus 29 Kimi tests passed in 16.44s. This closes the focused sanitizer implementation and caller contracts only; T076's full suite, coverage and strict NFR remain open.
- [x] T086 [US3] Evaluate the compact catalog-row transport as a conditional experiment. The R4 peer timing was about 31 ms and variable, and the sampled Work policy rows were empty, so the result did not justify a port or establish a populated-policy benefit. The private `orjson` decoder comparison saved about 20 ms across 21 fresh checks, but no new dependency is accepted. No product or dependency change follows; retain the current JSON-object wire format. This closes the experiment as a no-go only and does not pass FR-044 or close T076.
- [x] T087 [US1-US4] Close the reproduced local same-turn completion overwrite and remote sequence rejection. Preserve the event/barrier local completion and stale-ready contracts; validate exact/current remote projection, already-completed current turn, stale, missing, and malformed state; exercise the remote `terminal_service.send_input` branch through `run_agent_step`; and ensure remote acknowledgement leaves local monitor state/events unchanged. The deterministic pre-fix RED reproduced 4 failures; the final native four-module run passed 285 tests with 0 failures (46 warnings, 124.87 seconds); the separate real lifecycle/restart selection passed 4/4 in 154.28 seconds; and the final post-format focused selection passed 19 tests with 0 failures (17 warnings, 10.28 seconds). Pinned Black, isort and `git diff --check` passed. Exact selectors and source/test hashes are recorded in the private composition evidence and summarized in `research.md`. Ownership, interaction/authentication, receipt, cancellation, delivery and at-most-once guards remain intact. This closes T087's focused local/remote contracts; it does not close T083 or T076.
- [ ] T088 [US1-US4] Complete and validate the implementation candidate in `scripts/ci_rust_contracts.py`, `test/scripts/test_ci_rust_contracts.py`, and the Rust job setup/commands in `.github/workflows/ci.yml`. In each Linux/macOS matrix job, provision an ephemeral job-owned PostgreSQL service, configure isolated main/auth/peer stores with distinct migration/runtime credentials, initialize and verify native schemas before the actual CAO server starts, then pass only private runtime configuration. Run the three live endpoint contracts against that server with zero mocks/skips; setup, verification, server-health, or endpoint failures fail CI. Bound lifecycle to an owner-only run directory and clean up only the job-created cluster/targets. Preserve existing cargo commands, timeouts and selection semantics. The preimplementation stdlib RED failed because the workflow omitted helper invocation. Final helper/test/workflow hashes are `5d8c2f842ee59be5884e2e0b85e87d6c5628d81bf23330654d2d28f2c507c9ed`, `f52821ba5417c3440309c1a80d8c0308662402f07589ae1cda6094e718186441`, and `127ee88669a3da554b7a1943161d9ae6c00b0baac729fb34ceb9c30093513cfc`. The isolated `-I` entrypoint import contract first reproduced a `ModuleNotFoundError`, then the final helper unit module passed 8/8 (receipt `/home/felni/.cache/caos-spec021-evidence-20261014/nfr-fix-r23/rust-tui-contract-r1/entrypoint-import-r2/unittest-final-receipt.json`); the workflow contract passed 1/1. YAML parsing, Black/isort and diff checks passed. The actual Linux gate used the helper-owned PostgreSQL 18.6 cluster and passed `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, and `cargo test --locked`: 262 passed across seven test binaries, zero failed/ignored/filtered, with 4/4 endpoint contracts. The 1,607-file source manifest was byte-identical before and after (SHA256 `7c69cf398a6a6fa710eede83e7bf83225c7c1b01866b6263ec80c777b5cb3c26`); no PostgreSQL process remained and the private runner temp directory was empty and removed. Final Graphify is 1,099 nodes/2,576 links with no failed sources (SHA256 `acfe18d5ae10a237efdbe6b399ac0560abcebdfe6092bb1bda391d4c6b164589`). Linux is verified; the macOS matrix run is unavailable and still required, so T088 remains open and does not close T083/T076.
- [ ] T061 [US3] Remove the R13 dispatcher grant-lock convoy with candidate-scoped qualification savepoints, durable invalid-candidate retirement outside rollback, a pure scheduler preview, and selected-candidate authority revalidation before the locked claim. Check the expected selection before queue/event mutation; preserve fairness, capacity, path reservations, sent/credential fences, job-before-aggregate ordering and the unchanged ten-process/two-slot regression. Verify native lock scope, revocation, full-capacity retirement and adjacent lock-order suites.
- [ ] T062 [US3] Correct the continuation test snapshot helper to enumerate the configured PostgreSQL schema and qualify every table read. Prove that snapshots detect real Work data changes and preserve every existing no-side-effect assertion; run all six continuation cases without exclusions or elevated catalog privileges.
- [ ] T063 [US3] Extend missing transport, managed-workflow authorization and interaction reconciliation/pagination contracts using the completed R13 coverage report. Preserve the strict 87% global backend floor and measure only a completed regression run; no exclusions or artificial branch execution.
- [x] T060 [US3] Restore the startup replacement-database regression using a distinct native PostgreSQL logical target, preserve the prior target's data, and verify four surviving wiki topics are repaired only into the replacement and second startup is a no-op. Keep the existing no-model/no-network assertions and owned-schema cleanup. (2026-10-14: full startup module 3 passed; prior target sentinel preserved column-for-column.)
- [x] T059 [US3] Restore the native cursor row type contract after the lease protocol introduced `Any` into six `Record | None` service returns. Reproduce the configured mypy failures, verify Graphify/source edges, type the cursor boundary accurately (including custom raw row factories), preserve runtime behavior, and rerun mypy/native connection and lease contracts before the final source freeze. (2026-10-14: mypy 425 files passed; 7 new native missing-row regressions, 50 workflow/registry cases and final 17 connection/lease/journal cases passed.)
- [x] T056 [US3] Reproduce sibling-schema teardown terminating a retained private memory audit session; restrict schema fixture cleanup to its owned schema, retain database-owner termination at worker shutdown, and prove sibling connection survival followed by the actual memory audit path. Rerun the R12 failure modules together, preserving fail-closed runtime behavior and the strict NFR. (2026-10-14: 52 native audit tests and 551 tests across all 24 R12 failing modules plus the lifecycle regression passed; all 31 original failing cases passed.)
- [ ] T057 [US3] Port the measured cumulative verification optimization into `clients/postgres/migrations.py`: pure constraint normalization bounded to 512 entries/4096 characters, shared fresh catalog relation selection, fixed module-owned SQL, and role query batching; preserve component identity-before-history query order. Prove all nine families and main/auth/peer digest parity, normalization bounds and Unicode/integer preservation, error precedence, caller-owned transactions, unchanged 21 full checks/20 commits, three actual-source cold NFR passes, and the complete regression gates.
- [ ] T058 [US3] Load the mandatory private audit dependency with MemoryService module imports; preserve deferred target/config/session/repository initialization and disabled behavior. Prove zero physical sessions/config resolution on Python module import and the disabled path, unchanged fork/pool/21-check/20-commit contracts, and three formatted actual-source cold NFR passes. This task concerns Python module loading, not data import. Record startup cost separately.

- [x] T001 Record source inventory, scope and migration requirements in spec.md and research.md
- [x] T002 Fix native driver and schema ownership contracts in contracts/storage.md and data-model.md
- [x] T003 Verify Graphify impact against actual source in graphify-impact.md

## Foundation

- [x] T004 Write failing real-PostgreSQL configuration, transaction and record contract tests in test/clients/test_postgres.py
- [x] T005 Implement private target configuration and native connections in src/cli_agent_orchestrator/clients/postgres/
- [x] T006 Implement component migration ledger, advisory bootstrap lock and pg_catalog verification in clients/postgres/migrations.py
- [x] T007 Add psycopg driver and lock resolution in pyproject.toml and uv.lock
- [ ] T008 Provision isolated PostgreSQL test service and per-test namespaces in test/conftest.py

## US1: all runtime persistence

- [ ] T009 [US1] Port Work schemas and WorkRepository to native PostgreSQL in clients/work_repository.py and clients/work_*_schema.py
- [ ] T010 [US1] Preserve Work store identity, content roots, recovery fences and writer admission in clients/work_repository.py
- [ ] T011 [US1] Port interaction schemas and repository transactions in clients/interaction_repository.py
- [ ] T012 [US1] Port interactions services and exact event ordering in interactions/
- [x] T013 [US1] Port ORM engine, baseline migrations and explicit terminal FIFO in clients/database.py
- [ ] T014 [US1] Port runtime-channel upserts in runtime_channel/store.py and services/remote_terminal_service.py
- [ ] T015 [US1] Port workflow journal and continuation schemas in workflows/ and clients/work_continuation_schema.py
- [x] T016 [US1] Port private plan and approval stores in services/
- [ ] T017 [US1] Port knowledge and memory access validation in services/ and clients/knowledge*_schema.py
- [ ] T018 [US1] Port browser authentication repository, provisioning and lockout in clients/browser_auth_repository.py and security/
- [ ] T019 [US1] Port user-wide peer registry preserving host/user leases in local_peers/
- [x] T020 [US1] Audit and remove remaining runtime SQLite connection paths across src/ (source audit: [runtime-sqlite-audit.md](runtime-sqlite-audit.md))
- [ ] T021 [US1] Run real PostgreSQL API/MCP/CLI, Work, interaction, workflow, auth and peer contracts

## US2: PostgreSQL-native recovery

The user changed scope on 2026-10-14 to remove all SQLite support. T022-T027 below are retired by that scope change, not completed or accepted. Historical PostgreSQL receipt schemas and rows remain immutable provenance and must be tested without an importer.

- [retired] T022 [US2] SQLite database import fixtures and importer integrity tests; superseded by T072/T074 native PostgreSQL contracts.
- [retired] T023 [US2] Consistent read-only SQLite import into an empty PostgreSQL target; importer is out of scope.
- [retired] T024 [US2] SQLite import count/reference/payload/sequence verification; native backup/restore verification is T075.
- [retired] T025 [US2] SQLite import receipts, interruption recovery and configuration cutover; existing PostgreSQL receipt provenance remains read-only under T072/T074.
- [retired] T026 [US2] SQLite migration commands and documentation; explicit old commands are rejected under T071.
- [retired] T027 [US2] SQLite import duplicate/conflict/corruption/source-immutability suite; native recovery regressions are T073/T075.

## US3: concurrency and authority

- [ ] T028 [US3] Write multi-connection claim, answer, grant and revocation race tests
- [ ] T029 [US3] Implement aggregate locks and CAS without replaying external effects in services/work_* and interactions/
- [ ] T030 [US3] Implement repeatable read-only snapshots and preserve post-read AG-UI authorization
- [ ] T031 [US3] Replace filesystem SQL identity and dispatch locking with verified target identity and PostgreSQL locks
- [ ] T032 [US3] Test restart, abort, deadlock handling, exactly-once settlement and timestamp cursor boundaries

## US4: installation and recovery

- [ ] T033 [US4] Implement managed PostgreSQL topology and private secrets in installation scripts and Dockerfiles
- [ ] T034 [US4] Strip database credentials from provider and worker environments
- [ ] T035 [US4] Implement PostgreSQL native backup, isolated restore and compatibility validation in recovery services
- [ ] T036 [US4] Test external PostgreSQL configuration and managed per-profile/auth/peer isolation
- [ ] T037 [US4] Run install, upgrade, compatible rollback, PostgreSQL-native backup/isolated restore and required host acceptance tests; verify unknown existing state fails closed and remains untouched.

## Final validation

- [ ] T038 Run complete backend, frontend, integration and applicable composition/architecture checks against PostgreSQL
- [x] T039 Run project-composition-check and record actual output
- [ ] T040 Complete system composition review and review final diff
- [ ] T041 Refresh isolated Graphify evidence and decide durable knowledge persistence
- [ ] T042 Record coverage, failures, external constraints and verification in validation.md; reconcile Spec016 acceptance
- [x] T043 [US3] Add real-PostgreSQL AG-UI regressions for permission downgrade after initial projection and during an idle heartbeat; emit only the scopes returned by the final post-read validation
- [x] T044 [US1] Reverify the continuation component at each driver operation boundary, including `enqueue_projected()` on new and replayed workflow projections, before reads/writes/effects; cover schema drift after construction and atomic rollback
- [x] T045 [US3] Serialize script and YAML workflow attempt allocation on the parent run row; prove concurrent same-step callers produce one durable attempt and no duplicate delivery
- [x] T046 [US1] Map native Work schema mismatch during YAML resume to the existing corrupt-history `ResumeCorruptError`/HTTP 422 contract before agent execution; cover real PostgreSQL schema drift at service and API boundaries while preserving unrelated database error classifications
- [x] T047 [US1] Fail closed when PostgreSQL cleanup-owner connection or advisory-lock acquisition is unavailable; translate database failure to `WorkConflict`, close only an allocated connection, avoid unlocking when acquisition failed, and prove the cleanup body is never entered.
- [x] T048 [US3] Recheck the exact Work projection marker after acquiring the run lock so concurrent identical projections return an idempotent result without repeating the step CAS
- [x] T049 [US3] Partition `graph/providers/memory.py` cache entries by the complete native PostgreSQL main target identity and prove same-target reuse versus distinct-target isolation when filesystem roots are shared, using real PostgreSQL (`test/integration/test_memory_knowledge_policy.py`; full module: 25 passed)
- [x] T050 [US1] Enforce Work job→item→attempt row-lock order before child writes, receipt/event foreign keys, and terminal transitions; reread and CAS after lock waits, with real-PostgreSQL deadlock, stale-state, stale-revision, and exact-once replay regressions in `test/clients/test_work_repository_deadlock.py`
- [x] T051 [US1] Prevent `WorkService.retry_work()` replacement-release inversion by locking the Work job before the scheduler aggregate and revalidating the replacement/stop proof after waits; prove the public admission/retry interleaving completes without PostgreSQL deadlocks.
- [x] T052 [US1] Prevent Work scheduler aggregate/event lock inversions on ordinary release, revoked terminal batch withdrawal (without taking the aggregate lock), and public claim/enqueue wrappers; enforce shared job→aggregate→event ordering and prove the public interleavings complete without PostgreSQL deadlocks.
- [x] T053 [US3] Preserve full Work schema verification at the legacy memory-access audit write fence while avoiding redundant pre-fence verification; keep verification private and mandatory, and prove corruption rejection, audit privacy, transaction rollback, and connection cleanup with real PostgreSQL.
- [x] T054 [US1] Batch the nine independent structural `pg_catalog` reads in `clients/postgres/migrations.py` into one fresh statement snapshot without changing category ordering, normalization, completeness checks, error behavior, manifest digests, or migration checksums; prove old/new manifest and digest parity for main/auth/peer targets, empty categories, nested routines, caller-owned snapshots, schema drift, and the unchanged ten-thread dispatch-convoy budget in `test/clients/test_postgres_catalog_batch.py`.
- [ ] T055 [US3] Add a process-wide bounded connection manager used only by the private legacy memory repository from `MemoryService._legacy_repository()`; generic WorkRepository callers retain direct connect/close. Count the repository's initialization/read-snapshot verification sessions and audit sessions in the same four-physical-session cap; keep general SQLAlchemy metadata sessions outside that scoped counter. Preserve fresh repeatable-read/read-only initialization verification, per-phase write transaction, store fence, full post-fence verification, separate commits, and lease-before-filesystem boundary. Re-resolve config on checkout, fail closed on a stale repository identity, fingerprint all effective target/libpq/TLS inputs without logging secrets, and reject inherited libpq settings that are not explicitly represented. Include retired active leases in the cap; test rotation retirement, fork reset, bounded checkout, and unusable saved lease/raw/cursor references. Set and validate `plan_cache_mode=force_custom_plan` after `RESET ALL` only on private pooled sessions, prove direct WorkRepository connections keep their existing default, and confirm manifest digest parity across plan modes. Prove rollback/disconnect invalidation, cleanup after short-lived API service churn, unchanged 21 full verifications/20 audit commits, and the existing memory round-trip under one second for three cold repetitions in real PostgreSQL (`test/services/test_legacy_memory_audit.py`, `test/services/test_memory_service_index_roundtrip.py`, and focused pool lifecycle tests).


## NFR, host acceptance, and live-provider follow-up

- [x] T089 [US3] Compare the shared-column target against the current nine-family catalog read using 20 paired fresh checks, raw-value and digest parity, and the unchanged native fixture. The result was 23.147 ms original versus 23.163 ms candidate; reject the candidate and retain current source. This no-go closes only the experiment, not FR-044/T076.
- [x] T090 [US3] Measure the private Pydantic Core JSON decoder comparison against the current decoder over 21 fresh checks. The approximately 24 ms aggregate difference did not establish an accepted end-to-end cold-round-trip gain; do not add a dependency or alter the wire format. This no-go closes only the experiment.
- [x] T091 [US3] Compare the Rust encoder with the existing standard-library encoder using the same payload. The measured 3.510 ms versus 3.327 ms did not improve the baseline; do not port it. This no-go closes only the experiment.
- [x] T092 [US3] Harden and verify the local live-provider journey harness: resolve `Path.home()` before imports, bound child `HOME`/`PYTHONPATH` to the selected source, isolate Claude with `CLAUDE_CONFIG_DIR`, preserve operator HOME/CODEX_HOME, and retain only sanitized metadata. The offline selection passed 18 tests across **two modules** with no skips, bound to **four source/test input files**; four import-order fixes were isort-only with no non-import AST change. The Claude config-path module passed 15; the OpenCode list module passed 27 and its focused v2 list node passed in two fresh runs. These are harness/configuration checks, not three real-provider journey passes; see T095. The offline selection is recorded in `research.md`.
- [ ] T093 [US3] Evaluate the `MemoryService` Core candidate for the fresh metadata collision read and metadata upsert. Preserve cross-tier collision checks before filesystem publication, metadata upsert after file/index publication, the existing session/commit/error boundaries, unique-key behavior, and all current verification/authority fences. Do not cache results or metadata. The R3-bound Graphify identifies the affected query paths. The two-file candidate is now present in main: `src/cli_agent_orchestrator/services/memory_service.py` SHA256 `58ec723493698a8c95ebb0a99e4781a4c469608115e8af9a1f6fbc994a89814f` and `test/clients/test_vault_schema_migration.py` SHA256 `33f89d93251747e538da44b876f7ca07a578769a0cea097f013d275992bd638f`. The fresh native main caller slice passed 21 tests in 25.798 s (JUnit `current-core-callers-r1/junit.xml`, SHA256 `5aac5e4a69c48871feca0499525c9066888646e4c8190de59adb7f8455290fd8`). Private paired run 02 measured baseline 1.105620873 s and Core candidate 1.004116244 s, a 101.504629 ms gain; both failed the unchanged strict `<1.0 s` assertion. Five native semantic probes passed, with ten metadata commits. The later Core/decoder pair measured Core at 1.032364007 s and combined at 1.120388702 s; both failed, the decoder arm was slower, and it is not ported. Both post-timing catalog audit probes errored on digest comparison and remain under investigation. These diagnostic runs do not close FR-044; require exact accepted cold evidence, caller/native semantics, and all three unchanged strict cold runs before accepting the candidate.
- [ ] T094 [US3] Diagnose and rerun the T097/T098 QEMU acceptance against the sealed source without changing production timeout or selection semantics to conceal delay. The R3 run expected 91 cases: group 1 passed 8 in 882.33 s; group 2 failed 2; 81 were not reached and 0 were skipped. Preserve the failure and cleanup receipts. The 2026-10-14 QEMU2 trace (111 sanitized events) establishes a test-only deadline-origin error: dispatch task creation was at 28,952.8 ms, backend execution at 40,032.2 ms, ACK at 69,440.0 ms, GO at 90,679.3 ms, and the callback at 104,070.4 ms, after the helper's dispatch-anchored 65 s timeout at 94,021.7 ms. The initial helper wait now awaits the callback or real dispatch future without a synthetic aggregate timeout; retain the existing 65 s post-SIGKILL/cleanup wait, production phase timeout values, and all 91 cases. No single production deadline spans preparation through pre-GO callback; the job-level 45-minute timeout is the outer hang guard. Require all planned cases, owned marker cleanup, source identity, and no unexplained skips for acceptance; T094 remains open.
- [ ] T095 [US1-US4] Complete the existing bounded real-provider interaction journey for OpenCode, Claude Code, and Codex using actual installed CLIs, configured authentication, and supported models. Require provider-generated questions, user answers, and explicit provider acknowledgment; preserve operator authentication state and capture only sanitized metadata. Claude's prior journey acknowledged two answered questions. The latest private-harness Codex journey passed in 14.17 s with two questions, two answers, one identity-matched acknowledgment, and unchanged auth/operator state; retain the earlier Codex failure as history. The latest private-harness OpenCode journey exited 0 in 11.11 s but failed with zero questions and zero acknowledgments (one `step_start`, one `text` event); the cause is unknown and the read-only MCP preflight is pending. These live journey receipts predate the T096 harness changes now in the current source tree; they are not fresh live-provider acceptance on those bytes. Keep T095/SC-011 open; do not infer a full provider-matrix pass.

R4 scoped validation: guarded package verification passed for manifest
`c4827350259bb4b4815126d11c15b2f9141cc11a8b71716f335cf8db6b37407e` (runner
exit 0; 389 wheel/sdist modules and five active SQLite guards passed; HEAD,
index, and HOME unchanged). Receipt SHA256:
`f0a4c966274532de62db189ff10441edfbc666cf3951a0137a5d496bb83fc13f`. The
source-bound Web20 selection passed 20/20, zero skips, exit 0; log SHA256
`159ae898419dad4115e61b38686b65a671e7fce76d9087712905eb8a97706554` and summary
`/home/felni/.cache/caos-spec021-evidence-20261014/frontend-private-r3/web20-20261014-214156-714984-ac1ogaku/summary.json`.
These scoped checks do not close T076, T083, T095, or the strict NFR.

### Latest scoped verification — 2026-10-14

A no-coverage native memory regression selected 234 tests across 11 modules: 233 passed, 1 failed the unchanged strict round-trip assertion at 1.161985059 s, 0 errored and 0 skipped (JUnit SHA256 `2673890c4974210e28e3f7a45c25bceaa05aa72c41092eb68168b1863ddc6794`). This does not satisfy the strict covered three-run gate. The scoped static review passed all four checks: Black/isort on two files, ImportLinter, and composition (399 files, 1,775 dependencies, 5 kept, 0 broken), receipt SHA256 `d82f19af42bcc3ea94460a531390ed489459c1bf031e8307667a46aa18b7cf7f`. Manual Core review found no blocking conversion defect and recorded a pre-existing cross-tier preflight race not introduced by the candidate (SHA256 `38188ba53187f7e8818df2eeb840eecde19bd31b7392008bb8fa44e4b04d50a6`).

The Core/decoder paired diagnostic failed both unchanged strict measurements (Core 1.032364007 s; combined 1.120388702 s), with the combined arm slower; both post-timing catalog digest audit probes errored (receipt SHA256 `d9982040d92fd8d6b9c0efb42fe3204bd850abc6445fa30aa962128cdc93ab13`). The latest private-harness Codex journey passed with two answered/acknowledged questions; OpenCode exited 0 without questions or acknowledgment. Neither closes provider acceptance. QEMU2 completed only its two-selector diagnostic: `after_upstream` passed, `before_upstream_effect` failed, 0 skipped, with verified cleanup; it does not close the full QEMU acceptance. T076, T081, T083, T093, T094 and T095 remain open.

- [x] T096 [US1-US4] Harden the current bounded live-provider harness's auth staging and OpenCode journey isolation. Validate Codex subscription-profile eligibility before creating the private overlay; preserve operator/source auth and config bytes, make setup rollback explicit, keep child environments and receipt projection bounded, and retain the production OpenCode config/capability path. Current source hashes: `test/live_provider/interaction_cli_journey.py` `8b136a5746e101f16f94c540d99e2db2d69d55f5d9fab62f3ea8e3d39074e130`; `test/live_provider/test_interaction_cli_journey.py` `2792ec1d699d2f78b63254b37fd07da86ba18cad3044d632575405abf2f5f890`; `test/live_provider/opencode_interaction_journey.py` `848d150f87136fec84e425db76ad305b62c4821cdc59d1f94761bf1a4e9e44fd`; `test/live_provider/test_provider_harness_upgrade.py` `efd58e94f18a8b8b034afb518d3f158199b5274e1a5fc1cabe6330bc95ca64bc`. Main-bound RED was 73 passed/3 failed/0 skipped (JUnit SHA256 `35dcac728e1d92f77a7d2afc130be30fd5702ced6edbb0f95d5b725ecc18cdab`); after the fix, 76 passed/0 failed/errors/skips across the two modules in 3.211 s (JUnit SHA256 `89415323d7a5a44cf7418c31f42d2891d76a2335a7ef7b85bfc8e934c91916e7`); Black/isort/compile checks passed. This task verifies harness behavior only; it does not close T095/SC-011 or claim provider-generated interaction on current bytes.

T095 live-provider status remains open: Codex and Claude positive journeys are from earlier private-harness runs, not a fresh run of the current main harness; the latest OpenCode journey remains zero questions and zero acknowledgment. The current main-bound 76-test suite is offline harness verification only.

T094 QEMU2 diagnostic: two selectors produced one pass (`after_upstream`) and one failure (`before_upstream_effect`), 0 skipped; receipt `nfr-fix-r23/t097-sigkill-diagnostic-r1/runs/run-0739fff4662c30cc-tu1iepls/run-receipt.json`, SHA256 `51cbee10d4c702e04d28ed6ec7484c93306f0a51024900f4a83758353e93972e`. Marker cleanup succeeded, runner ID was absent, PostgreSQL remained healthy, and OOM/restart were false/zero. The callback arrived 13.391 s after GO, but the 65-second wait had expired; the TDD helper correction now waits on the callback or real dispatch future without a synthetic initial timeout, while preserving the post-SIGKILL/cleanup timeout. Ten offline contract tests and the independent Python 3.10 error-preservation review pass. No fresh QEMU run has launched after the change; a prepared tar is not acceptance. T094 remains open.

T093 owner-JSON loader experiment (2026-10-15): semantic, counter/catalog/loader, source, and HOME guards passed, but baseline 1.306698168 s and candidate 1.565738595 s both failed `<1.0 s`; candidate was 259.040427 ms slower. Do not port; pair receipt SHA256 `dcb51b9cb3cc2fdb1326097b60e584221ceb6fe64e8c30f5442e2c55c2b3088d`.

T094 follow-up (2026-10-15): the corrected offline helper-contract selection passed 10/10 in 4.01 s (JUnit SHA256 `cef799393c147333824de70fa6bc40b9260e1bcc938c43a84ee52f8d11eadcc2`; log SHA256 `238189ef2b5b53c54b71fae94f4aab46c2686a22e91c8c6a4517c5537fe783d5`). The independent source-only Python 3.10 review passed bounded `add_note` compatibility and primary-exception preservation (review SHA256 `5d0453379873c62dab86bb83c9fb1f26605b24c3a226d4e40aef203fe59719d8`; inputs are bound in `research.md`). No fresh QEMU run has launched after the helper correction; the prepared tar is not acceptance. Preserve the earlier failed QEMU receipts and keep T094 open.

T093/T094 diagnostic recovery (2026-10-15): the catalog-plan run's measured round-trip was 1.805611058 s, a strict `<1.0 s` failure. The raw wrapper exited 1 before persisting its run receipt because the expected-red parser did not accept pytest's appended exact assertion line. Private postprocessing matched that line to the clock, JUnit and pytest log; all nine proof checks passed, all ten plans were present with no plan errors, and 71 tables were reported. Original failure preserved; no NFR pass. Receipt `nfr-fix-r23/catalog-plan-core2-r1/recovery-r1/recovered-result-receipt.json` SHA256 `0af8126623d8a33c17b9709fb7936efa8bc1e978c93a39b1cec108c699e5b9a1`. The strict parser's offline suite passed 10 tests (validation receipt SHA256 `c4635fcb806a2cb1b9ac97dadeeb87f1ce33294f56df144bf4f8957cb7087caa`). The original parent HOME and main-source guard values were not persisted, so the recovery receipt leaves them unavailable.

T094 corrected-helper QEMU2 diagnostic: both selected SIGKILL cases passed in 235.15 s, zero warnings, guest runner exit 0; cleanup succeeded. The original wrapper exit 1 and `diagnostic_failed` status remain preserved because its summary parser miscounted the raw two-pass guest log. Recovered receipt SHA256 `addf5550137ad6236a2b9be1e033ccf651b0a63895ed078d9290dccd6cab18e5`; raw QEMU log SHA256 `70159e926a62080caed92a751e379823e6dff26a7b1e4917610591bef9a9a844`; original wrapper receipt SHA256 `2ddc69626ea4fb1ab53379915bdb93fbde4d91c1806e110cd23c3ee4a3bba244`. The sealed test input hash matches current `test/integration/t097/test_proxy_sigkill_t097_restored.py` (`6f6da51600c0f43c0fc1c7a0a3fd0e87493547c5179482474f220c075c1f033b`). This two-case result is not the full 91-case acceptance; T094 remains open.

### Dated verification addendum — 2026-10-15

- **Service metadata:** Docker Desktop recovered. PostgreSQL 18.6 remained the same owned container; the published port changed 61997 → 56421. Sanitized metadata SHA256: `200b53e7bd46f495ed1246cab792eb0e6909bbec3cc1ad962a05b99f2a14f1d1`. The receipt verifies version/admin and historical-result continuity (SHA256 `b792881742290c78bcc596ca0d65a00a602b6aefb0a0041a779de4d53b9c57e5`).
- **T098 private R3:** 18 offline tests plus 9 subtests passed; native baseline and candidate each passed 8 tests. Strict scored pair: baseline 1.024650518 s failed, candidate 0.967219559 s passed. Three-run cohort: 1.017240271 s failed, 1.034487912 s failed, 0.973277629 s passed. One of three is insufficient; T098 remains open. Diagnostic-only phase timing was 1.032294341 s with 21 catalog reads in 592.153 ms (receipt SHA256 `54add99c7eaa859e9baaf3bd25eee6f7f4ab04c3cf20ddede0dc7eb9b7d38045`).
- **FULL91:** preflight exit 2 at 6062 MiB available versus the unchanged 6144 MiB floor; no VM started. The user cannot free additional memory, so the gate remains pending; do not lower the floor (log SHA256 `de5c72bed00571290a21fe8342b635caa7541c1bd8abfb298a945b660779f760`).
- **Provider/projection:** OpenCode R8 timed out at 83.13 s with zero questions and acknowledgments; cleanup status was incomplete; `private_files_preserved` was a cleanup-failure category. No final matrix or AGUI restart occurred, and no T098 R3/R8 port or global rerun was made. A separate projection proposal passed 101 offline tests with 6 warnings; this does not claim a runtime fix. All aggregate acceptance remains open.

### R9 and source-guard follow-up — 2026-10-15

- **OpenCode R9:** real attempt timed out at 83.54 s with 0 questions/0 acknowledgments. Observed `turn_sequence=1`, `initialized=true`, `MCP_ready=false`, TUI idle, and expected log absent before cleanup. The offline probe found that the bundled MCP command was recognized before it was wrapped with `env -i` and was unrecognized after wrapping; this is not evidence of a live MCP connection. Current diagnosis is that the harness lookup omitted readiness. No product runtime fix has been made. Exact owned tmux kill exited 0 and server was gone, but the socket was stale; root-owned files were preserved. Receipt: `R9launcher/runs/514ad4818318477b819d5c13e58dc6ca/root-owned-tmux-cleanup-r1.json`. No R10 result or provider acceptance is claimed.
- **SQLite guard:** source-only selection passed 5 pure test functions, 1 warning, 125.53 s, with `--noconftest --assertplain`. This is not a full-suite or coverage result. Aggregate gates remain open.

## T099–T101 — private memory context-tail batch port

- [x] T099 [US3] Add offline fake-driver tests for ordered inbox/control/recovery validation, pipeline and unique savepoint recovery, typed rows, lease taint on recovery failure, expiry/fork guards and sequential generic paths in `test/services/test_legacy_memory_context_batch.py`; keep the test database marker offline-only.
- [x] T100 [US3] Port only the reviewed R3 changes to `src/cli_agent_orchestrator/clients/work_repository.py` and `src/cli_agent_orchestrator/services/legacy_memory_audit_repository.py`; preserve all preceding full verification checks and existing transaction, fence, lease, and commit boundaries.
- [x] T101 [US3] Add portable native PostgreSQL contract cases for the actual batch path, exact `Record` parity, read-only snapshot and audit transaction state, table locks, context failures, earlier-inbox precedence over later missing/denied/locked relations, and borrowed tuple fallback in `test/services/test_legacy_memory_context_batch_postgres.py`; root ran the native group separately.

The T098 private R3 prototype passed 18 offline tests plus 9 subtests, and both native baseline/candidate selections passed 8 cases. Only one of three prior candidate strict runs passed; this port is incremental and does not close FR-044. Keep all three original cold covered runs and the unchanged `<1.0 s` threshold as a separate root-owned acceptance gate.

### T099–T101 port verification — 2026-10-15

The reviewed R3 bytes are now in main: `work_repository.py` SHA256
`c9211ea08e7796c27ba21d71cec1b742ad0f4f4a8705319fa4cf6eccea693852` and
`legacy_memory_audit_repository.py` SHA256
`8c6dd825ecd58025469b2539f70c4c34157007dc10d1624f29bad552377718ba`.
The offline unit module passed 18 tests plus 9 subtests. The first native
attempt passed 7 cases and failed the borrowed-tuple test in its test-only
statement observer: membership in a set raised `TypeError` for a
`psycopg.sql.SQL` object. The observer now checks for `str` before set
membership; no product behavior was changed for this failure. The fresh native
run passed all 8 cases with zero failures, errors, or skips, and its source,
origin, HOME, and count guards passed. Final native-test SHA256:
`6b402bef3e8d6aa6385e6239bc15c96111bef8a63a7405b9f269c2aef98a1623`;
receipt: `nfr-fix-r23/current-context-batch-native-r2/receipt.json`.
The strict `<1.0 s` gate remains open: the previous candidate cohort passed
only one of three cold runs, and this port has not been rescored.


## Static verification follow-up — 2026-10-16

- [x] T102 [US1, US3] Resolve the four configured mypy failures in WorkRepository context buckets and MemoryService metadata typing using precise annotations and an identity-preserving Table cast, without changing SQL or runtime contracts. Verify normalized AST/table identity/compiled-SQL parity, then pass full main mypy. Format only the full Black/isort failing-file union, preserve syntax/import inventory and existing work, and pass the complete configured checks. Record main integration, composition and native/final regression results separately; static checks alone do not close T076/T083.

### Current verification checkpoint — 2026-10-16

The type corrections and the reviewed formatting union are integrated in main. Configured mypy passed all 396 source files; full Black left all 1,317 checked files unchanged. Twelve focused real PostgreSQL contracts passed, including batched contexts and both metadata conflict paths; the separate offline message/observer selection passed 55 cases. Composition preserved five contracts with none broken. A partial formatting clone initially classified some project imports as third-party; full-main isort reproduced seven failures. The reviewed seven-file correction uses the complete main source classification, preserves per-block import inventory and executable AST, and is integrated; root subsequently ran the complete configured isort check on main, which exited zero. The seven corrected files also pass Black. T102 is closed for the static correction; final regression remains separate.

Earlier R9/R10-production-parity cold acceptance passed all three unchanged strict assertions; this newly formatted/type-corrected source still needs its own bound cohort and full regression. Full91 QEMU ended at the unchanged 2,700-second outer deadline: nine completed groups reported 87 passes, and the final four-case group started without a final summary. Those four are unfinished, not skips. The original failed cleanup receipt is retained; separately bound manual cleanup verified removal of its exact owned marker after confirming its runner container absent. Extending a future outer budget remains a pending operator decision.

Docker native acceptance previously passed installation, PostgreSQL restore and browser-cookie renewal gates. Codex question/answer/ACK and generic-message journeys each passed their own API-replacement and AG-UI checks with verified cleanup. Claude's later question diagnostic reports authentication_failed, so renewed authentication is pending. OpenCode's Unicode matrix produced one passing pytest case with four validated scenarios and two internal not-applicable quota categories; the outer controller still failed its cleanup gate. OpenCode question and fresh generic-message acceptance remain open. These results do not close T076/T083 or assert completion of every CI platform.


## Provider acceptance lifecycle follow-up — 2026-10-16

- [ ] T103 [US1] Reproduce the native session response namespace mismatch offline using the real Terminal response shape; centralize only the necessary canonical response validation in the existing matrix harness and let the private MCP adapter consume it. Preserve bare-name API cleanup, selected provider/model/Unicode instruction/tools/timeouts and strict receipt criteria.
- [ ] T104 [US1] Give the real-provider matrix fixture explicit ownership of a fresh short private tmux root and confirmed closure of only its empty, identity-pinned server after API shutdown. Reuse/extract existing fixture cleanup machinery; reject unknown/reused ownership, nonempty sessions, changed sockets and live API; preserve primary errors and retain unconfirmed resources. Run red/green offline tests, one bounded real private tmux lifecycle check, affected suites, formatting, composition and independent review before integration and a newly frozen real-provider acceptance.


## Bound strict timing evidence — 2026-10-16 04:37 UTC

R11 source manifest `918bc61313a29fbae3231c8a28b2901b4a87e7808276c7811a6ef04504e3c503` passed all three fresh-process executions of the unchanged `test_roundtrip_runs_under_one_second` assertion (`elapsed < 1.0`). Each selected exactly one test with zero failures/skips, active line coverage, no prewarm or retry, unchanged source before/after and unchanged private HOME/profile. Root and an independent ECC reviewer checked the final artifacts. This closes the newly bound timing cohort, not the pending complete regression or provider gates. The inner elapsed values were not persisted, so no numeric margin is claimed; kernel page cache was not flushed. Coverage data was validated as readable line data including MemoryService; the runner's `coverage_matches_r7_critical` flag is constant and does not prove equality to a separate three-node coverage artifact. The final summary and process exit zero are authoritative; preliminary launch/preparation snapshots retain their earlier states.


## Provider fixture fixes integrated — 2026-10-16

T103/T104 implementation is integrated in the main workspace. The matrix validates the API's canonical session name and compensates any post-creation parsing, ID, namespace or readiness failure through the original requested name, preserving the primary exception and recording unconfirmed cleanup, including the Python 3.10 fallback. Its fixture owns a short private tmux root, confirms API shutdown, closes only the verified empty server, and removes HOME only after confirmed closure. Spec023 reuses the extracted helper while retaining its own API-exit and deleted-terminal preconditions and checks the helper's source origin and identity.

The corrected baseline reproduction failed nine selected cases; the final candidate and root's main-workspace rerun each passed all 136 affected offline tests, with zero failures/skips. Root reviewed the entire final diff and the independent ECC findings. Composition passed five architecture contracts; Black passed all 1,318 Python files. Isort is checked separately before freezing. The controlled normal-socket tmux check has a contemporaneous cleanup receipt. The agent exceeded the authorized single check: an earlier failed probe and a successful explicit-socket probe are documented retrospectively, with no API, PostgreSQL or model calls; the explicit-socket success lacks a contemporaneous receipt and the first failure lacks a process-wide check. These implementation results do not repair the original failed provider receipts or close fresh real-provider/global acceptance tasks.

## T094 follow-up: atomic test-controller PID publication (2026-10-16)

- [x] Reproduce the R14 empty-PID race offline at the controller-write/consumer-exists boundary; use Graphify and source inspection to verify impact.
- [x] Publish the fixture PID atomically, retain the existing readiness deadline and all ownership/recovery assertions, and pass a deterministic red/green regression.
- [ ] Review and integrate the fixture/regression in main, bind the next source, and rerun all 91 QEMU cases together. Preserve R14's failed receipt (79 passed, one failed, 11 unexecuted); T094 stays open until complete acceptance and cleanup pass.

**Atomic-publication local evidence (2026-10-16):** The reviewed four-line fixture correction and portable offline regression are integrated in main. The new test failed on main before the fixture correction; after integration, it and the ten existing SIGKILL wait contracts passed (11/11, 8.51 seconds). The new test extracts the actual embedded controller code and uses fake process/modules plus deterministic barriers; the offline marker prevents the PostgreSQL autouse fixture. Black/isort and diff whitespace checks pass. The consumer polling, five-second deadline, exact identity assertions, recovery semantics and production source remain unchanged. Main evidence is under `r14-qemu-pid-publish-diagnosis-r1/main-verification/`. The original R14 run container is absent and its marker database was removed. Full 91-case acceptance remains pending a new bound source/run.


## Spec 021 diagnostic tasks R2 — required-wait signal

- [x] Record R1's workflow omission without changing or backdating its receipt.
- [x] Query the existing Graphify JSON read-only with exact vocabulary terms; record nodes, edge representation, missing coverage, and source discrepancies.
- [x] Trace the current test and production source to the first observable positive-wait signal; keep the historical cause unresolved.
- [x] Build a reduced private candidate with fixed-name bounded diagnostics and unchanged timeout semantics.
- [x] Add and run only a fake early-exception / early-return / pending regression; preserve exact command and output privately.
- [ ] Root reviews the private diff and chooses whether to use it in a future serial diagnostic.
- [ ] If separately authorized after QEMU, run the selected integration test once in a quiet environment. Do not infer a fix or loosen any timeout from the fake result.

### R3 Python 3.10 compatibility tasks (disjoint from T105–T111)

- [x] **T112 — Reuse the source-backed R2 graph and R15 pin review.** Keep the historical cause unresolved and preserve R2 unchanged.
- [x] **T113 — Replace `add_note` in the private candidate.** Attach the fixed diagnostic through `TimeoutError.args`, preserving the caught object and exception chaining.
- [x] **T114 — Verify the no-`add_note` path and format.** Run the private synthetic exception/return/pending cases and focused Black/isort checks with single workers/jobs.
- [ ] **T115 — Root review and any future integration diagnostic.** Review the R3 diff and evidence. A future integration run requires a separate serial quiet-window authorization.


## Tasks: Local Claude Unicode Credential Retention

**Input**: Private Spec 021 addendum at `spec.md`; implementation outline in `plan.md`.

### Phase 1: Source and workflow proof

- [x] T105 Record the current matrix fixture, auth-copy, session-delete, managed API stop, owned tmux cleanup, and archive-helper call chain in private source notes.
- [x] T106 Query existing Graphify without refreshing it; record its stale location/truncation and verify all relevant order against source.

### Phase 2: Private candidate contracts

- [x] T107 Create a private-only candidate for the exact Claude self-cell, using the existing fixed `message` archive layout and preserving the current four substantive scenarios and quota outcomes.
- [x] T108 Add offline fixture contracts for archive-root preflight, exact path/modes, source/stage non-mutation, positive teardown ordering, workspace retention on failed/uncertain teardown, and archive-after-teardown when the functional assertion fails.
- [x] T109 Run only the focused offline contract and canonical archive-helper tests; record exact candidate pins and receipts, with no live service or provider run.
- [x] T110 Obtain independent root review before any main-tree port or live Claude cell.
- [ ] T111 After separate root GO and quiet host, run one explicitly selected Claude self-cell with unchanged model/prompt/timeout/scenario criteria; report each scenario and cleanup proof without claiming manager reconciliation.

### Dependencies

T105–T106 are complete and precede candidate work. T107 precedes T108–T109. T110 blocks both main-tree integration and T111. Offline contract results never substitute for T111.


**Current main integration and acceptance update (2026-10-16):** The complete R15 QEMU run passed 91/91 cases with zero failures/skips, four warnings, verified owned-container removal, healthy PostgreSQL, and canonical source-guard success. This supersedes the earlier pending-full-QEMU statements above; it does not relabel the historical R14 failure. The reviewed R3 wait-signal diagnostic and Claude Unicode credential-retention fixture/contracts are integrated in main. Main native/archive contracts passed 114/114; the diagnostic's three synthetic cases passed privately. T115's review is complete, while its real integration diagnostic and the quiet full backend remain pending; no historical timing root cause or performance fix is claimed. T111's actual Claude Unicode cell remains pending. The current helper binding must be verified in the same pytest process before that live cell launches. Original/staged Claude credentials are outside the write boundary.

## R17 failure-cohort serial recheck — 2026-10-18

The 33 PostgreSQL-associated node IDs from the closed R17 failure receipts were rerun serially (`pytest -n 0`) against the already running, owned PostgreSQL test container, with PostgreSQL autostart disabled. The real local Docker browser-auth test was explicitly enabled and passed; no replacement PostgreSQL container was created. Result: **29 passed, 4 skipped, 0 failed**. The four skips are the three pinned bubblewrap no-exec cases and the same-host-UID user-namespace case; the guards report that the pinned `cao-work-bwrap` image is absent and intentionally refuse the host `/usr/bin/bwrap`. The other seven non-PostgreSQL failure IDs were then rerun with the corrected current test name and passed **7/7**. The first selection attempt used the stale name `test_delete_session_refuses_unprefixed_name_before_touching_anything`; pytest rejected collection before executing tests, and the corrected rerun used `test_delete_session_refuses_unregistered_unprefixed_name_before_teardown`.

Receipts and private logs: `/home/felni/.cache/caos-r17-pg-probe-r1/pg-cohort-receipt.json` (log SHA-256 `3fccb3e67ace41dbc252e40c279fdfd4feedab133696c933cf17d35cba75ac82`) and `nonpg-cohort-r2-receipt.json` (log SHA-256 `381f22e74e52311bc1352afb5a56edc5eea6179b9ad53e79f342e1c3c50b9cec`). After both runs the existing PostgreSQL container remained `running/healthy` with restart count zero. This serial cohort did not reproduce a current failure, but it does not identify the historical R17 timeout cause, close the full backend run, or satisfy the remaining QEMU/provider acceptance tasks. Four sandbox cases remain unverified until the pinned image is available.


## Current measured performance follow-up — 2026-10-18

- [x] T116 Measure eligible NOT NULL cardinality and statement-local native-render grouping on the unchanged current native fixture; compare every manifest family/digest and six interleaved reads plus EXPLAIN. Preserve the covered test result independently and reject an ineffective or semantically different candidate.
- [x] T117 Only if T116 demonstrates substantial repeatable benefit, implement the minimal guarded current-main query selection, verify PG16/17/unknown/future fallback and fresh drift/snapshot/error behavior, then run affected native tests and the three unchanged cold covered NFR runs before the full backend gate. Otherwise record a no-go without modifying product source.

T116/T117 result: NO-GO, no product edit. On 72 tables, 697 eligible NOT NULL
rows formed 210 signatures. All 12 complete manifests/digests matched. Six
interleaved read medians were 21.810 ms baseline versus 25.644 ms grouped
(17.583% slower); EXPLAIN execution was 20.189 versus 25.134 ms. The unchanged
covered test independently failed at 1.088907277 s. Evidence:
`/tmp/caos-t116-native-grouping-20261018.{json,log,junit.xml}`.

- [x] T118 Evaluate one compact-row JSON transport variant after the unchanged covered timer. Retain fresh nine-family SQL and exact reconstructed manifest/digest; include reconstruction in paired timing, measure payload reduction, reject any ineffective variant, and do not port from query-only evidence.

T118 result: NO-GO. All twelve complete manifests/digests matched, and the
JSON representation shrank from 537,032 to 376,093 bytes. Including dictionary
reconstruction, median reads improved only 2.362% (22.762 to 22.224 ms),
insufficient to justify a transport/decoder contract change or meet the SLO.
No product edit. Evidence: `/tmp/caos-t118-compact-manifest-20261018.json`.

- [x] T119 Measure a statement-local complete Work-proof read bundle against the existing private pooled connection, compare every identity/history/catalog/role/context value and paired timings, and reject it if the measured gain cannot justify implementation. Keep the unchanged covered test outcome separate.

T119: 12 raw-proof parity checks passed; medians 24.932 ms for five read
sections versus 22.617 ms bundled (9.286%, approximately 49 ms over 21 checks,
before implementation overhead). NO-GO for implementation: unchanged READ
COMMITTED statement snapshots and the timing/retention of context locks have
not been proved. Existing validators, transactions and product source remain
unchanged. The covered test independently failed at 1.004849982 s. Receipt:
`/tmp/caos-t119-work-proof-bundle-20261018.json`.

- [x] T120 Isolate native fixture garbage debt using one explicit local pre-body GC collection, preserve benchmark body/timers/GC settings and all FR-044 invariants, review setup ordering, and run three independent covered cold processes without retries. This is a benchmark preparation correction, not product performance acceptance on its own.

T120: three independent fresh covered processes passed without retries at
0.913459783, 0.947938199 and 0.951082612 seconds. Body SHA-256 stayed
`432446cee143278bc16b1266bd3cbe4ca6bcab84cdbb729af3f4d4d0c4126f82`.
This closes the preparation task and three-run timing selection only; full
backend, semantic evidence and aggregate signoff remain open. Receipt:
`/tmp/caos-t120-three-cold-20261018.json`.

- [x] T121 Complete capacity trace coverage for expiry-before-read and snapshot entry, retain all existing workload/timer/assertion criteria, verify all twenty phase measurements, and rerun the capacity module before the full backend. Historical full-load failure remains open until fresh full validation.

- [x] T122 Reproduce non-ASCII credential failures at the three API service-bearer exclusions and runtime WebSocket boundary using synthetic credentials, add fail-closed ASCII token comparison shared by these handlers, and verify original ASCII authority behavior plus composition. Do not attribute or close historical jobs from this regression.

T121: source-reviewed by Luna (APPROVE), complete capacity module passed
3/3 with real PostgreSQL. The measured phases are subdivisions of total
metadata latency, never added to it. Historical full-suite latency is open.
Log/JUnit: `/tmp/caos-t121-capacity-20261018.*`.

- [x] T123 Repair missing MCP Apps build in wheel/sdist preparation, preserve all publication policy and platform gates, correct generated-output documentation, and verify workflow contracts plus local Apps/Web asset production. No publication.

T122: root RED10failed/2passed reproduced TypeError at all four real handler
comparisons. GREEN12passed; expanded final synthetic boundary selection16passed
(includes equal invalid Unicode and surrogate inputs). Existing token-file/API/
real PostgreSQL runtime authentication+epoch regression selection61passed
(includes the original12), no failures/skips. Luna security review APPROVE.
Existing optional_creator exact ASCII→None regression is in
`test/api/test_interactions_transport.py`, exercised in that61. Black6files,
isort and composition5kept/0broken passed. Historical run-step attribution
and live-provider validation remain open.

- [x] T124 Reconcile previous selection omissions and restore available local test prerequisites without global installs. Current explicit unfiltered collection has18,363 unique nodes:340 existing cases absent from the old XML plus16 new Unicode regressions.618 prerequisite/cohort tests passed after adding real fish/zsh/Cargo and checksum-pinned Gitleaks to PATH. Full T083 still pending.

T123 scoped implementation/configuration accepted after root review:80wheel
contracts, current newcontract1pass,92Apps tests/90.01%line coverage,504Webtests,
all typechecks/builds/JIT/size passed. E2E/fullbackend remain separate acceptance.

- [ ] **T125 — Reconcile the four scope-auditor failures.** After the active full
  backend run ends, map all four failure records to exact node IDs and verify
  the stale inline-comparison expectation in `test/api/test_scope_coverage.py`.
  Port the private candidate as a test-only change: check the canonical
  `ascii_token_matches` call, its live binding, and both ASCII prechecks before
  the native constant-time comparison. Retain existing positive authority and
  enforcement tests; add the two negative cases for a weakened helper body and
  a rebound helper. Run the focused module and applicable static checks, then
  the complete unfiltered covered backend against final source. No selection,
  threshold, product-auth, or skip changes. T125 remains open until the focused and complete-backend checks pass.
  Main integration complete; focused82 tests passed. All four corrected nodes and the two new negative auditor cases passed in complete R5 diagnostics; final uninstrumented full acceptance remains pending.


- [ ] **T126 — Decouple required-wait terminal telemetry mock from PostgreSQL.**
  Preserve all real ledger operations, startup/active-time limits, acceptance
  versus MCP-pull assertions and no-redelivery assertions. Run the whole
  composition module after the fixture correction and retain full-suite closure.
  Main integration complete; whole module7 passed. The corrected required-wait node passed setup/call/teardown in complete R5 diagnostics; final uninstrumented full acceptance remains pending.


- [ ] **T127 — Make remote response-loss recovery deterministic.** Preserve
  real durable/fence logic, model transport loss after bridge enqueue separately
  from the normal command deadline, prove one delegated paste and unchanged
  successful result across reconnect/replay. Retain controlled red/green and
  ordinary complete-module results; full-suite closure remains separate.
  Main integration complete; whole module6 passed, valid controlled green1
  passed. The corrected response-loss node passed setup/call/teardown in complete R5 diagnostics. Early invalid diagnostic runs are preserved and explicitly excluded; final uninstrumented full acceptance remains pending.


- [ ] **T128 — Resolve configured mypy errors in Work authority/proxy/messages.**
  Preserve runtime validation and dispatch/rejection semantics; use explicit
  types, without new ignore/cast escapes or configuration changes. Review
  Graphify against source, run meaningful existing regressions, global mypy
  and style, architecture/composition and final backend on the resulting main.
  Main integration complete; Work regressions146, mypy400, Black/isort1342,
  architecture12 and composition5/0 passed. Complete R5 diagnostics retained these typing changes with only the strict NFR failing; final uninstrumented full acceptance remains pending.


- [x] **T129 — Evaluate the distinct routine-definition JSON hypothesis.**
  R5 diagnostics leave only strict NFR failed; R6 same-operation control passes
  isolated. Graphify guidance must be checked against current migrations/Work/
  memory/role source. Keep the previously rejected T082 rewrite retired. Prepare
  and root-review the private post-call paired SQL probe, retaining JSONB
  identity_arguments and every existing authority/durability check. Record
  decoded/numeric-type/normalized/digest parity, bounded read-only execution,
  all six predeclared samples and plan aggregates. Broaden meaningful semantic
  parity only if the experiment justifies it. No product port or NFR acceptance
  from a private experiment; preserve strict final full/cold gates.

  Evaluation completed and candidate rejected: all three AB/BA/AB pairs had raw,
  numeric-type, normalized and digest parity, but no stable performance benefit.
  Prepared client reads A/B were 24.870/23.182, 19.322/19.092 and
  18.737/19.897 ms. No product SQL change was accepted. The first diagnostic
  did not execute SQL because its table guard used only the final migration;
  the corrected diagnostic uses the same sorted table union as verify_component.
  Both receipts are preserved; T083/T076 and the loaded strict NFR remain open.


- [x] **T130 — Diagnose shared target catalog subsets without changing proof.**
  Root-review a new private post-call AB/BA/AB experiment; preserve baseline SQL
  shape, all family rows, native projected types, routine classes/keys/OR/UNION,
  fresh snapshot, role/context checks and audit transactions. Add allowlisted
  per-node relation/loop/timing metrics to distinguish actual repeated work from
  mere repeated SQL references. No execution alongside active QEMU or another
  heavy gate, no source port without stable gain and meaningful broader parity.
  Retain every failure and rejected candidate. Strict NFR/T083/T076 stay open.
  Evaluation completed: exact parity in all three pairs; client reads improved
  modestly, but server execution worsened in the two later pairs. No product
  port is justified by this evidence; strict loaded NFR remains open.


- [ ] **T131 — Correct the pidfd-readable/child-status-unavailable race.**
  Preserve current QEMU failure (9 passed,1 failed,81 not run) and root cause
  evidence. Reproduce the immediate timeout0 boundary, review ownership/error/
  lock/deadline contracts independently, integrate only a bounded safe main fix
  with meaningful regressions, and rerun supervisor/gateway/composition plus
  complete QEMU91 on the final sealed source. No increased timeout, weakened
  TERMINATED assertion, fabricated process proof or replay of uncertain work.
  Reviewed R2 integrated into main; canonical new regression cases reproduce
  baseline8 failures/7 passes and pass15/15 after correction. Full supervisor/
  gateway checks pass224 cases/22 explicit capability skips; final exact15
  cases, mypy400+1, style2 and composition5/0 pass. The requested scoped
  current-main final-group run then passed exactly the four remaining T098
  selectors (4/4, zero skips/failures, owned marker removed); it is recorded
  as a four-case result and does not claim the full QEMU91 gate. Complete
  QEMU91 and final full-backend closure remain pending if that broader gate is
  later requested.

**Current main verification update (2026-10-19):** The runtime-role query now
checks effective privileges on the existing protected registry tables in
addition to role flags, namespace ACLs, ownership and schema CREATE. It rejects
missing `SELECT` or any effective write/DDL grant on
`_cao_schema_migrations`, `_cao_store_identity` and `_cao_import_receipts`,
while preserving the prior error precedence. The regression was reproduced
before the change and then passed in 3/3 PostgreSQL table-grant cases and 3/3
column-grant cases, 27 role and prepared-query cases, and the complete
PostgreSQL-only/source-guard selection passed 41/41 against the reused healthy
service with automatic provisioning disabled. The source guard now also
inspects `uv.lock`; `uv lock --check` passed, and the five changed Python files
pass Black, isort and bytecode compilation.
This closes the ACL/lockfile omission; the loaded full-backend latency failures
and provider live gates remain separate open requirements.

**Publication diagnostic (2026-10-20):** T081 remains open. The current sealed
serial pre-push run reproduced the unchanged memory round-trip at
1.0100495500082616 s and was interrupted for diagnosis after 12,724 passes,
one failure and 65 skips. Two focused diagnostic processes passed; no cause
or product correction is demonstrated. Preserve the failed receipt and see
[the publication NFR record](publication-nfr-20261020.md). The complete
eight-worker T083 comparison, cold cohort and aggregate gates remain separate.

The subsequent normal eight-worker hook completed: 18,263 passed, one failed,
83 skipped and 71 successful subtests in 3,020.04 s. The unchanged T081 timer
failed at 2.200422219990287 s. Its displayed 87% coverage rounds 86.913605%;
neither that display nor the marker-filtered selection establishes T083.
The push was rejected; T076/T081/T083 remain open, with no performance fix.


- [ ] T132 [US3] Diagnose the fresh current-source global-R5 cold covered NFR failure (1.0231529650045559s, main5628bdf6) with source/fixture-bound, content-free boundary timing and independent Codex read review. Preserve the actual failed receipt and every original FR-044, benchmark, coverage, authority, transaction and cleanup criterion. Reuse T129/T130 history without assuming current causality or rerunning unchanged acceptance for green; identify and prove any first incorrect/expensive boundary before a separate reviewed correction. T076/T081/T083 remain open.

T132 diagnostic update (2026-10-22, Codex only): boundary timing and post-call unchanged baseline plans completed, preserving all three actual failures and verified owned cleanup. The baseline plans measured 22.199–31.556ms server execution and 2.770–5.679ms planning over 73 current Work tables. No candidate or product correction is established; T132 and the original cold/full gates stay open. See [current-source receipts and limits](current-source-nfr-20261022.md).


- [x] T133 [US3] Evaluate a distinct direct-composite json_agg catalog candidate against the exact current nine-family baseline using one post-call AB/BA/AB read-only snapshot, strict raw/type/normalized/digest parity and content-free client/server metrics. Preserve original benchmark/coverage/authority/freshness/transactions and failed receipts. Require pure RED/controls and independent Codex review; accept no product change or NFR/global closure from a diagnostic alone.


- [ ] T134 [US1-US4] Run one current-source uninstrumented three-cold/full8-backend/Apps/ratchet acceptance with unchanged criteria and source/runtime/existingPG binding; preserve stop-first-failure and prior diagnostic/historical failures. Review new orchestration/closed admission before execution; account actual reports, coverage and omissions. No QEMU91 or historical job action. Complete remaining applicable Web/build/type/composition checks only after their prerequisites pass; retain any external gate explicitly.

T132/T133 checkpoint2026-10-30: actual current-source covered diagnostics fail1.049357595s/1.059715725s, preserved; catalogexecute dominates471ms/21calls. Direct-record candidate has3/3semantic/digest parity but fails predeclared gain criterion and isREJECTED, no product port.56base+4split and5candidate controls pass; samePG0/0/0, source unchanged, Codex-onlyreview. T134 closed preparation awaits final admission/review for uninstrumented current acceptance. [Evidence](current-source-nfr-20261030.md).


- [ ] T135 [US3] Implement a bounded pure checksum cache only for validated immutable migration source descriptors, preserving exact checksum bytes and uncached fallbacks/current-field replacement detection. Require RED and parity/mutation/subclass/bounds plus native schema/role/audit regressions, Codex review and applicable style/type/composition checks; integrate accepted source in main and rebind a fresh uninstrumented global acceptance. Do not cache observed proof or lower any NFR/coverage/worker criterion.

T134/T135 checkpoint2026-10-30: actual uninstrumented coldR1failed1.042606872s, downstreamNOTRUN; originalreceipt retained. T135 source-only cache validated25controls +85focusedtests, typesPASS, architecture5/0, Codexreview; same freshproof/authority/transaction contracts. T135 staysopen until main integration and fresh source-bound global attempt. [Evidence](current-source-nfr-20261030.md).
