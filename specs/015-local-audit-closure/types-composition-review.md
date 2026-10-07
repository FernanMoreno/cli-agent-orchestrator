# Independent typing composition review

Scope: Spec 015 typing closure across DATA persistence, remote channel publication and teardown, terminal/turn services, script/YAML execution, journal contracts, provider interfaces and tmux identity handling. The inherited dirty implementation was retained. This review used `system-composition-review` and actual source, not the generated graph alone. Root supplied the Graphify impact analysis; the relevant edges were checked against consumers.

## Boundaries and evidence

- **ORM -> persistence consumers:** independently compared the DATA before/after metadata snapshots, normalizing only object repr addresses: all **33 tables / 284 columns** match, including names, SQL types, nullability, keys, indexes, defaults and CHECK definitions. The mapped-column conversion retains constructor arguments, including existing foreign-key declarations. `Mapped` accurately distinguishes instance scalar/nullable fields from class query descriptors. No migration is introduced by typing.
- **Validated process identity -> cleanup:** `clients/work_repository.py` verifies exact field sets, integer types excluding bool, process relationships, namespace pairs and both canonical digests before its TypedDict casts. Tuple namespaces become lists before return. Stored identities are decoded and revalidated; typing does not bypass corruption rejection or add process authority.
- **Remote publication -> identity-bound deletion:** placement publication retains SQLite `BEGIN IMMEDIATE`, settled launch proof, deletion tombstones, terminal collision checks and atomic terminal/placement writes. Remote deletion requires literal `deleted is True`, exact runtime/incarnation/launch/session identity and transactional comparison before deleting rows. The ORM-class delete still targets the same tables and predicates; Connection execution adds no ORM session cascade. Compensation holds the dispatch lock and carries the captured launch/session incarnation.
- **tmux lookup -> teardown:** generic `_read_listing` retains its single parse retry and distinct lookup failure. Exact terminal identity scan, ambiguity refusal, post-kill rescan and process-stop proof remain intact. An unreadable scan produces UNKNOWN; no successful kill is inferred from a nullable return or failed command.
- **Workflow -> recorder -> external delivery:** Protocol views alias the original callback functions. Every declared hook is assigned before publication. YAML immutable-contract identity is committed before delivery guards; script contract hooks retain their strong refusal path while legacy journal writes remain best effort. No cast constructs new approval or execution authority.
- **Scoped records -> prepared/resumed execution:** optional owner/principal/epoch fields default to None and stay private in repr. Resume still checks the exact trusted plan owner and supplied principal; driver ownership is checked before attach/spawn. Missing ports do not acquire authority.
- **Script -> process:** the TypedDict retains omission of `pass_fds` when no credential is present and the exact inherited FD tuple when present. Credential validation, FD barrier, cancellation shielding, drain/reap and driver exit recording remain in place.
- **Work model -> response:** computed `delivery_phase` stays a property derived from authoritative `state`; there is no second state field or persisted authority.

## Findings and correction evidence

1. Separate remote placement/projection reads can observe deletion between snapshots. Initially, `terminal_service.get_terminal` cast an Optional projection to Dict, `turn_recovery_service.get_turn` asserted non-None, and `remote_terminal_service.sessions` cast nullable inventories/projections. A minimal executable reproduction returned `None` from `get_terminal`; its HTTP caller performs `Terminal(**terminal)`, turning normal disappearance into a 500. These were reported immediately to root/DATA. DATA added failing disappearance cases, then changed terminal/turn reads to their existing not-found ValueError contracts and session enumeration to retain only surviving projections. Current source is checked; no Optional casts/asserts remain at these three boundaries. DATA reports **60 focused regressions passed**. The reviewer's later focused run includes all eight disappearance cases.
2. Remote KEY payload `success` previously passed from `Dict[str, Any]` into a bool-annotated local without validation. A minimal executable reproduction returned `None`; AG-UI bound key delivery's `is False` test did not reject it. Root authorized a narrow correction. New tests first reproduced **7 failures / 2 passes**, then `send_special_key` gained an exact bool check and a malformed-acknowledgement RuntimeError. Valid True/False and the existing missing-field False fallback are retained. The final focused suite covering ten new acknowledgement cases, seven durable approval cases and eight disappearance cases reports **25 passed**. Post-claim exceptions still become nonretryable durable delivery uncertainty through the existing approval implementation.
3. Remote output, working-directory, turn-sequence and turn payloads likewise relied on typed local declarations without validating decoded values. Root authorized guards at the actual typed consumers. The new scalar suite first reproduced **35 failures / 24 passes**. Terminal reads now validate strings, cwd validates str-or-None, input validates an exact int excluding bool, and turn read/verify/cancel validate dictionaries. Existing omitted-field KeyErrors, optional cwd fallback, get-turn no-turn fallback, valid integer values and dictionary identity are preserved. No new wire schema, authority, cast, Any annotation or ignore was added. The final combined regression run reports **100 passed**, including all scalar/key/disappearance cases and the existing durable approval, remote-services and turn-recovery suites.

Reviewer commands and logs are under `/home/felni/tmp-cao015-types-api`:

```bash
TMPDIR=/home/felni/tmp-cao015-types-api .venv/bin/pytest --no-cov -q test/services/test_remote_key_acknowledgement.py
TMPDIR=/home/felni/tmp-cao015-types-api .venv/bin/pytest --no-cov -q test/services/test_remote_key_acknowledgement.py test/services/agui/test_durable_handoff_approval.py test/services/test_remote_projection_disappearance.py
.venv/bin/mypy --follow-imports=silent --cache-dir=/home/felni/tmp-cao015-types-api/mypy-cache src/cli_agent_orchestrator/services/terminal_service.py
TMPDIR=/home/felni/tmp-cao015-types-api .venv/bin/pytest --no-cov -q test/services/test_remote_scalar_responses.py test/services/test_remote_key_acknowledgement.py test/services/test_remote_projection_disappearance.py test/services/agui/test_durable_handoff_approval.py test/runtime_channel/test_remote_services.py test/services/test_turn_recovery.py
.venv/bin/mypy --follow-imports=silent --cache-dir=/home/felni/tmp-cao015-types-api/mypy-cache src/cli_agent_orchestrator/services/{terminal_service,turn_recovery_service}.py
```

Logs: `key-ack-red.txt`, `key-ack-green.txt`, `key-ack-mypy.txt`, `scalar-red.txt`, `scalar-green.txt`, `scalar-mypy.txt`. Final focused mypy reports **no issues in two files**. Black and isort checks pass for both sources and both new regression files; `git diff --check` passes. Final source/diff review checked all eight consumer guards and retained unrelated changes. Earlier API/AG-UI/backend review work ran 949 relevant test executions and an eleven-file focused checker; those results are recorded separately in `types-api-evidence.md` and do not establish global completion.

## Verdict and limits

Root owns final architecture/composition, whole-repository mypy and complete pytest checks. They were running during this review; no broad suite was launched or inferred passed by the reviewer. Existing SQLite-backed and disposable-process regressions provide real persistence/lifecycle proof. External provider execution, Docker/Bubblewrap deployment acceptance and account operations were not performed.

**PASS for the reviewed composition boundaries: no confirmed P1/P2 remains open.** Confirmed disappearance and malformed-value gaps were corrected with failing-first regressions. This verdict covers the inspected source contracts and focused executable evidence; whole-project completion still depends on root's final global gates.

## Scope-audit follow-up

Root's whole-suite run exposed two scope-coverage audit failures. Source tracing confirmed introspection blind spots, rather than absent enforcement: ticket issuance calls `_issue_transport_ticket`, which verifies origin, current identity and scopes; `/events` and AG-UI streaming call `_stream_authorization`, consuming resource-bound single-use tickets or checking live header/browser authority before disclosure and on subsequent frames. Local coordination discovery/pairing explicitly requires both client and Host loopback. Pairing additionally validates current registry/process/public-key/project identity or its one-use acceptance challenge. Task/status/cancel/session/revoke requests verify the current peer generation and signed pinned-key grant for the exact project/action, including task/requester identity and nonce replay rejection.

Only `test/api/test_scope_coverage.py` changed for this follow-up. The audit recognizes exact handler identity plus actual top-level guard calls receiving the request. Transport recognition checks exact resource and scope tuple; signed peer recognition checks project and exact action. No production authority markers or protected `_OPEN_READS` additions were made; the protected AG-UI stream was removed from that exemption list. Five audit regressions check missing/wrong guards and ensure manually protected reads never become open exemptions.

Sixteen denial cases exercise the live HTTP boundaries: five non-loopback discovery/pairing requests, five unregistered/unsigned peer operations, five unscoped transport requests and one registered matching-generation peer with a real SQLite pinned `session:read` grant but invalid signature. The last case reaches the real signature verifier; the unregistered cases only prove registry-bound refusal. Existing `test_local_peer_auth.py` separately proves modified-body rejection, nonce replay rejection, retained-task ownership after revocation and exact project/action capability denial.

The first focused run reproduced both audit failures (**3 failed / 51 passed**): its additional failure was a new assertion expecting `not_local` for peer revoke, whose earlier path/header identity check correctly returns `scope_denied`; that assertion was corrected without changing enforcement. Final command:

```bash
TMPDIR=/home/felni/tmp-cao015-types-api .venv/bin/pytest --no-cov -q test/api/test_scope_coverage.py test/services/test_local_peer_auth.py test/api/test_events_endpoints.py test/api/test_agui_stream_endpoint.py test/api/test_agui_stream_reconnect.py test/api/test_ws_auth.py
.venv/bin/isort --check-only test/api/test_scope_coverage.py
.venv/bin/black --check test/api/test_scope_coverage.py
git diff --check
```

Fresh result: **88 passed, 11 deselected by the existing default integration-marker policy**; formatting and diff checks passed. Logs: `/home/felni/tmp-cao015-types-api/scope-audit-red.txt`, `scope-audit-green.txt`, `scope-audit-final.txt`. The earlier 87-pass result preceded the registered invalid-signature case. Neither result establishes whole-suite completion; root owns the fresh global rerun.

## Overflow replay fixture follow-up

The later global run exposed `test_overflow_triggers_reconnect_backfill`. Its `_Log` fixture returned empty retained history while claiming `after_id("evt-2")` could return a dropped record. The current endpoint deliberately validates the cursor and selects its suffix from one locked retained-history snapshot; it cannot safely acknowledge that fixture's absent cursor. Focused RED reproduced **1 failure** (`overflow-red.txt`). This was a fixture contract mismatch; no production change was needed or made.

Only `test/api/test_agui_stream_overflow.py` changed. The test now uses real `EventLog.append` rows with current timestamps and generated IDs. Its retained snapshot contains both acknowledged pre-gap records and the dropped record. It proves that the first finite drain closes/unregisters, then reconnect registers before its one history snapshot, replays only the suffix, de-duplicates a simultaneous live copy and unregisters again. Both requests must opt into `overflow_close=True`. A second case proves an unretained Last-Event-ID explicitly produces `cursor_expired`/resync, emits neither retained nor queued events and unregisters; it cannot silently fall back to replay-all.

```bash
TMPDIR=/home/felni/tmp-cao015-types-api .venv/bin/pytest --no-cov -q test/api/test_agui_stream_overflow.py test/api/test_agui_stream_reconnect.py test/services/test_sse_bus_overflow.py
.venv/bin/isort --check-only test/api/test_agui_stream_overflow.py
.venv/bin/black --check test/api/test_agui_stream_overflow.py
git diff --check
```

Fresh result: **10 passed**, formatting and diff checks passed (`overflow-green.txt`). Source/diff review preserves the strict atomic cursor contract and subscription/replay/live/cleanup ordering. Root retains ownership of final whole-project gates.

## T067 local-peer functional coverage

Root's unchanged whole-project coverage floor exposed previously untested pairing/operator boundaries. The Graphify query supplied by root was verified against the actual peer services, registry, identity, route and CLI sources. Only two new authorized files were added: `test/services/test_local_peer_lifecycle_contracts.py` and `test/cli/commands/test_peer_contracts.py`. No production, policy, ignore, marker, skip or coverage-floor configuration changed.

The **68 passing cases** use real per-test metadata SQLite, a real temporary process registry, a temporary profile identity/private-key file, Ed25519 signing/verification and actual grant/challenge/nonce state. Fakes are limited to external peer HTTP responses and the empty session backend. They prove invitation storage grants no authority; approval pins exact scopes/key; matching retries preserve the grant; consumed approval cannot restore revoked authority; fresh approval can restore a revoked grant; malformed, expired, colliding and changed-identity/project offers cannot create grants; mismatched remote receipts cannot finalize approval; offline/rekeyed profiles are not reported online; revocation stays locally effective despite an unavailable remote acknowledgement; and project paths cannot escape through absolute paths, parent traversal or symlinks.

HTTP cases additionally exercise valid/expired/mismatched pairing and acceptance, a correctly signed project session read, nonce replay refusal, signed revocation, subsequent read denial, and task-status ownership by peer/project/requester with insufficient-action refusal. `CliRunner` exercises all six public peer commands with actual stored state: identity/grant discovery, pairing without premature authority, explicit acceptance and hidden code input, confirmed receipt output, reconciliation retaining a real write lease until operator confirmation, and confirmed/offline/declined/absent revocation.

```bash
PYTHONPATH=/mnt/c/users/ferna/onedrive/escritorio/caos/src TMPDIR=/home/felni/tmp-cao015-types-api COVERAGE_FILE=/home/felni/tmp-cao015-types-api/coverage-new-peers .venv/bin/pytest -o addopts='' -q test/services/test_local_peer_lifecycle_contracts.py test/cli/commands/test_peer_contracts.py --cov=src/cli_agent_orchestrator --cov-report=json:/home/felni/tmp-cao015-types-api/coverage-new-peers.json
.venv/bin/isort --check-only test/services/test_local_peer_lifecycle_contracts.py test/cli/commands/test_peer_contracts.py
.venv/bin/black --check test/services/test_local_peer_lifecycle_contracts.py test/cli/commands/test_peer_contracts.py
```

Fresh result: **68 passed** (`peer-contracts-verified.txt`), formatting checks passed. Intersecting executed source lines with `/home/felni/tmp-cao015-root/coverage-ci-diagnostic.json` missing lines yields **429 newly covered baseline-missing lines**: local peer auth 121, service 174, routes 31, CLI 75, identity 26 and registry 2. Exact line sets are in `/home/felni/tmp-cao015-types-api/peer-coverage-gain.json`; these are observed diagnostic gains, not an inferred global percentage or a claim that the global 87% gate passes. The focused override avoids duplicate default coverage reports; project configuration and the mandatory CI floor are unchanged.

Initial iterations exposed two incorrect new-test assumptions (short collision codes fail length validation before collision checks, and an acquired registry lease is `held`, not `running`) and a transient test-import rename typo. All were corrected in the new tests. The first attempt without explicit lower-case PYTHONPATH collected no useful coverage because the editable package resolved through its case-alias path; the final command above resolves the actual lower-case source and produces executable line evidence. Both new test files are frozen for root's whole-project rerun.
