# US2 backend evidence — 2026-10-07

Scope: T010, T011 backend, T015, T017, T020 backend, T021, T023 backend, T025, T027. Frontend consumer evidence is recorded separately. No tasks.md edits, commit, push, deployment, or publication.

## Investigation and regression evidence

Inspected dirty git status before changing code. Read applicable AI_WORKFLOW sections, approved spec015 plan/tasks/contracts and systematic-debugging, TDD, Graphify, composition-review and completion-verification instructions. Graphify query `graphify query 'event SSE auth ticket app_tools orchestration' --budget 700` identified EventLog/SseBus/log-redaction relationships; verified against actual sources. Existing workflow run cursor/replay remains unchanged.

First incorrect boundaries: MCP history read its own process ring while `/events` subscribed lazily without a history cursor; AG-UI and terminal WebSocket accepted reusable bearer query values; access redaction omitted `token`; remote delete attached local authentication without destination scoping.

Initial command `.venv/bin/pytest -q test/services/test_event_stream_ticket.py` reproduced three failures: missing ticket store, unredacted terminal token and destination-unaware `_auth_headers`. A standalone import assertion also failed for the missing ticket module. Later baseline assertions expecting token-in-query attachment were updated to require rejection. Intermediate suites exposed native header subject compatibility and legacy AG-UI test doubles; native header scope authorization remains supported, while ticket mint requires a verified principal subject. One intermediate run encountered SQLite disk-full on `/tmp`; subsequent runs use a task-specific TMPDIR under `/home/felni` and do not delete unrelated work.

## Delivered contracts

- POST `/events/ticket`, `/agui/v1/stream/ticket`, `/terminals/{id}/ws/ticket` returns `{ticket, expires_in:30}` with `Cache-Control: no-store`. Origin-bearing requests must be same-origin or match an exact configured trusted CORS origin; wildcard entries do not authorize minting. Browser-cookie authentication retains its strict canonical-origin checks; authenticated native callers can omit Origin.
- Opaque tickets are principal-bound, minimum-scope/resource-bound, expire at 30 seconds and consume atomically under a lock. Consumption burns the capability even for wrong resources. No bearer/cookie is returned in URLs.
- Attach and continued delivery validate current authentication, not captured scope arrays. Browser session revocation and JWT expiration/verification failures close live or idle transports. Idle checks occur each second. Terminal input/output authorization failure closes 4401; completion of either channel cancels its sibling and releases resources.
- Headers and browser cookies remain supported. `token` and `access_token` reusable query credentials are rejected. Scoped ticket transports bypass unrelated cookie lifetime checks and enforce the ticket owner's current authentication at the endpoint.
- MCP history uses authenticated HTTP `/events/history`, sharing the API producer's event IDs. It returns `{events,cursor}`. `subscribe_events(last_event_id)` mints a fresh ticket and returns `{url,sse_url,cursor,...}`. An evicted/unknown cursor returns `{resync_required:true,error:event_cursor_expired}` before issuing a ticket.
- `/events` accepts `cursor` or `Last-Event-ID`, registers the live queue before replay, emits `id:` frames, deduplicates the history/live overlap and closes on queue overflow for backfill. Unknown cursors return HTTP 409 with `event_cursor_expired` rather than hiding a gap.
- Terminal `token`, AG-UI `access_token`, and `ticket` are all redacted from access logging. Local orchestration bearer is attached only to the configured API origin; authenticated HTTP calls block redirects, remote targets and callbacks do not inherit the local bearer.

## Verification

All pytest commands below use `TMPDIR=/home/felni/tmp-cao015-us2-backend` and `--no-cov` for focused verification.

Broad command:

```bash
.venv/bin/pytest --no-cov -q test/services/test_event_stream_ticket.py test/api/test_events_endpoints.py test/api/test_ws_auth.py test/utils/test_logging.py test/utils/test_log_redaction.py test/utils/test_orchestration.py test/mcp_server/test_app_tools.py test/api/test_agui_stream_endpoint.py test/api/test_agui_stream_coverage.py test/api/test_agui_auth_hardening.py test/api/test_agui_stream_reconnect.py test/api/test_workflow_events_sse.py test/api/test_browser_channel_auth.py
```

Result: **153 passed, 11 deselected, 6 existing deprecation warnings**, exit 0, 121.09 seconds. This includes race history/live backfill, idle revocation, actual JWKS bearer expiry revalidation, stale-cookie/ticket interaction, AG-UI fresh-ticket reconnect and preserved workflow SSE tests. Subsequent terminal sibling-task lifecycle cleanup and added attach/egress cases receive fresh focused verification below.

Fresh focused command `.venv/bin/pytest --no-cov -q test/api/test_ws_auth.py test/services/test_event_stream_ticket.py test/utils/test_orchestration.py`: **39 passed**, exit 0, 34.54 seconds. Includes new single-use/resource-bound WS attach, exact-origin authenticated local/remote destinations and denial of write-ticket issuance from a read-only subject.

Explicit integration command `.venv/bin/pytest --no-cov -q -m integration test/api/test_events_endpoints.py`: **11 passed, 3 deselected**, exit 0, 10.63 seconds.

Remote/orchestration command `.venv/bin/pytest --no-cov -q test/mcp_server/test_remote_target.py test/mcp_server/test_send_message.py test/mcp_server/test_assign.py test/mcp_server/test_handoff.py test/utils/test_orchestration.py`: **160 passed, 4 failed**. All four failures were strict expected-call dictionaries in provider-resolution tests lacking the intentionally added `allow_redirects=False`. Updated those four expected calls; fresh `.venv/bin/pytest --no-cov -q test/mcp_server/test_assign.py::TestCreateTerminalProviderResolution`: **8 passed**, exit 0, 56.08 seconds. The original failing broad run is retained as evidence rather than relabeled green.

`project-composition-check "$(cat .ai/project-name)"` passed twice (5 contracts kept, 0 broken), including the run after WS/security focused tests. Final compatibility changes receive an additional gate below.

Subsequent consumer review found explicitly trusted static viewers using a different local port and fresh EventSource reconnections unable to set Last-Event-ID manually. Tests reproduced trusted-origin ticket issuance returning 403 and explicit-cursor replay failure before fixes. Mint now supports the exact existing CORS trust list, excluding `*`. AG-UI accepts `?cursor=` on both ticket POST and stream GET; expired/unknown cursors return visible 409 before ticket mint. `since` keeps precedence. Derived frame IDs are accepted only when their base event remains retained, preserving safe mid-record replay. These changes do not weaken browser-cookie canonical-origin enforcement. Final relevant compatibility suite results follow below.

## Composition and limits

EventLog/SseBus producer, API stream, MCP HTTP history and iframe descriptor use the same event IDs; subscription precedes replay and queue overflow causes reconnect. Ticket record owns its credential validation closure; expired unused tickets are pruned on issuance, active streams retain only their own validation closure. Tickets are ephemeral and process-local; restart invalidates them. Default-off app routes remain absent. No database schema or dependency migration is introduced.

The inherited fleet history is a bounded in-memory ring (500 records, 24-hour retention), not a restart-durable journal. Gap-free replay is established within the retained window; stale cursors require resynchronization and lost/expired history is not claimed recoverable. JWT authorization follows the existing verifier/JWKS/expiry policy; no new IdP blacklist protocol is invented. Real terminal PTY/network acceptance beyond the mocked endpoint boundary is not claimed by these focused tests.

Graphify refresh is appropriate for the new ticket-service/API dependency; root owns the final feature-wide refresh and composition review. No vault write is needed: these implementation-specific conclusions belong to the checked-in evidence, not durable cross-project knowledge.

## Final consumer compatibility and retention-race review

Fresh compatibility command:

```bash
.venv/bin/pytest --no-cov -q test/services/test_event_stream_ticket.py test/api/test_agui_stream_reconnect.py test/api/test_agui_auth_hardening.py test/api/test_agui_stream_endpoint.py test/api/test_agui_stream_coverage.py test/api/test_ws_auth.py test/api/test_browser_channel_auth.py
```

Result: **41 passed**, exit 0, 35.52 seconds, 6 existing deprecation warnings.

A subsequent review identified eviction between preflight cursor validation and the generator's first iteration. Both `/events` and AG-UI regressions reproduced this: `.venv/bin/pytest --no-cov -q test/services/test_event_stream_ticket.py -k evicted` returned **2 failed, 7 deselected** before the fix. The first boundary was the permissive `after_id` fallback returning every retained row after the confirmed cursor itself had disappeared.

The generator now registers its subscription, takes one ring snapshot, validates the cursor and selects replay from that same snapshot. Canonical cursors replay the suffix; intermediate AG-UI frame cursors replay their retained parent record. If the cursor expired before first iteration, both surfaces emit `event: cursor_expired` with `{code:event_cursor_expired,resync_required:true}` and close, without delivering a suffix or snapshot that hides loss. HTTP preflight still returns 409 when it can. Client onerror renewal finds the stale cursor via the descriptor/ticket endpoint and visibly resynchronizes retained history.

Fresh final replay/security command:

```bash
.venv/bin/pytest --no-cov -q test/services/test_event_stream_ticket.py test/api/test_events_endpoints.py test/api/test_agui_stream_reconnect.py test/api/test_agui_replay_contract.py test/api/test_agui_stream_endpoint.py test/api/test_agui_stream_coverage.py test/api/test_agui_auth_hardening.py test/api/test_ws_auth.py test/api/test_browser_channel_auth.py
```

Result: **57 passed, 11 integration-marker deselected**, exit 0, 33.51 seconds, 6 existing deprecation warnings. The 11 event integration tests had separately passed with `-m integration`; no workflow cursor implementation was changed.

Final post-race `project-composition-check "$(cat .ai/project-name)"`: **PASS**, 5 contracts kept, 0 broken, exit 0. Reviewed affected producer/ring/subscription, auth/ticket/cookie, descriptor/client reconnect and destination/redirect boundaries against source; no persistence migration or dependency cycle. Final owned diff whitespace check and compileall: **PASS**, exit 0. Feature-wide client suites, two-process acceptance and final Graphify refresh remain with root's aggregate review.

## Root-requested US5 API integration follow-up

The API plugin list originally exposed only installation-time record evidence. Added current `review_installed(record.name, store=store)` under a new `review` field while retaining existing record fields, `affected_sessions`, and the untrusted-content warning. A new test showed the missing review field before the patch; fresh full `test/agent_plugins/test_api.py` passed **42 tests**, exit 0, 33.33 seconds. The regression edits installed SKILL content and verifies current integrity becomes `changed` and current enablement becomes false.

A later review identified partial unreadability propagating as a whole-list failure. An API regression with two plugins reproduces the real shared boundary by making one `review_record` raise `OSError`; `review_installed` wrapped it as `PluginInstallError`, preventing healthy records from being listed. The corrected baseline returned **1 failed**, 34.52 seconds. Root chose one shared service fallback owned by US5, rather than duplicating exception policy in API and CLI. The API source therefore remains only the current-review seam; the test expects current unavailable/unverified/disabled evidence for the affected package and healthy evidence for the other. Final shared-service/API results follow after integration.

The shared US5 service now returns complete unavailable evidence on current package read failure and denies enablement; the API adds no duplicate exception fallback. Fresh source-final command `.venv/bin/pytest --no-cov -q test/agent_plugins/test_api.py test/services/test_event_stream_ticket.py test/api/test_ws_auth.py`: **63 passed**, exit 0, 25.94 seconds, 8 existing deprecation warnings. This includes the healthy/unavailable two-plugin API case and current tamper evidence.

Root's mandatory full mypy run failed with 573 errors across 85 files (398 checked). The reported `app_tools.py` Optional turn-projection indexing error is preexisting work and remains outside this change; the new HTTP history seam is not that branch. Fixed type errors in the added transport helper seams (sealed browser Principal return, shared Request/WebSocket type, and the already-guarded native token). Added full annotations to the new ticket store methods. `.venv/bin/mypy src/cli_agent_orchestrator/services/event_stream_ticket.py`: **PASS**, no issues in 1 source file, exit 0. The global typing gate is not claimed green.

Final source-frozen composition verification after the 63-case run: **PASS**, 5 contracts kept, 0 broken, exit 0 (`composition-source-final.log`). Supplementary `mypy api/main.py` exits 1 with 457 transitive errors across 69 files; no errors remain in the added transport/current-review seams. Root's final mandatory full mypy rerun supersedes the historical 573-error result: **FAIL**, 531 errors across 81 files (398 checked), recorded in `mypy-after-review.log`. Root's final Graphify refresh verified stable hashes for 26 sources, with 1,652 nodes, 3,711 edges and 0 failed sources. Source remains frozen; typing debt is accurately reported rather than broadened into this feature.

## Convergencia local posterior del padre

El diagnóstico histórico de 531 errores quedó corregido por la convergencia posterior: mypy completo pasa en 398 fuentes. Selección completa final: 16470 passed, 110 skipped, cero fallos; ratchet aprobado. Véase [types-root-evidence.md](types-root-evidence.md) para la evidencia final y los límites. Los fallos y bloqueos anteriores se conservan como historial.
