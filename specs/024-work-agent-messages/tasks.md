# Tasks: PostgreSQL-Durable Work Agent Messages

## Phase 1 — Failing contracts and persistence regressions (TDD)

- [x] T001 [P] [US1] Add closed request/result contract tests for `send`, `receive`, and `ack` in `test/models/test_work_message.py`, covering forbidden identity fields, ASCII idempotency keys, UTF-8 byte bounds, U+0000 rejection before SQL, and receive page limits (FR-003, FR-006).
- [x] T002 [P] [US1] Add V2-to-V3 migration and failed-forward-DDL rollback tests in `test/clients/test_work_postgres_schema.py`; assert V1/V2 checksums remain valid and the partial V3 table/ledger row roll back (FR-007).
- [x] T003 [P] [US1, US3] Add PostgreSQL message-ledger tests in `test/clients/test_work_message_postgres.py` for sender/receiver authorization, same-job restriction, fresh-send receiver liveness, stable sender-item replay before receiver liveness, Unicode/NUL/byte bounds and the V3 octet-length constraint, row/event atomicity, conflict, receive ordering, stale generation, identical and conflicting same-key sends racing across repository instances, simultaneous opposite-direction sends across sibling grants, message-vs-plain receiver `_revalidate_order` serialization across sibling grants, message-vs-dispatch-retirement lane ordering, and acknowledgement idempotency (FR-004–FR-009).
- [x] T004 [P] [US1, US3] Add `WorkOrigins.handle_mcp_request()` tests in `test/services/test_work_origin_message.py` for exact dispatch, live credential-derived identity, sender and receiver tool grants, malformed actions, and denial before persistence (FR-001–FR-005, FR-012).
- [x] T005 [P] [US1, US3] Add body-redacted proxy effect-hash tests in `test/services/test_work_mcp_proxy.py` for send requests and receive responses, including nested `content[].text` redaction, request-ID replay fingerprints, serialized audit metadata, and two maximum-size response bodies at the 65,536-byte boundary (FR-006, FR-009).
- [x] T006 [P] [US2] Add pure Work-event-to-AG-UI projection tests in `test/services/agui/test_work_message_stream.py` for exactly one `TEXT_MESSAGE_CONTENT` per `work.message.sent`, empty delta, routing metadata, body omission, ack suppression, cursor advancement, and filtering unrelated Work events (FR-009, FR-010).
- [x] T007 [P] [US2, US3] Add owner-scoped durable SSE route tests in `test/api/test_work_message_agui.py` for cursor replay, unauthorized job denial, event gaps and a gap SSE ID at the returned durable floor, owner revalidation during an open stream poll, live polling, and API replacement against the same PostgreSQL target (FR-010, FR-011).

## Phase 2 — PostgreSQL schema and Work message operations

- [x] T008 [US1] Add strict Work message request/result models in `src/cli_agent_orchestrator/models/work_message.py` and append V3 to `src/cli_agent_orchestrator/clients/work_postgres_schema.py` without changing V1/V2 statements or checksums (FR-003, FR-006–FR-008).
- [x] T009 [US1, US3] Implement `WorkMessageService` in `src/cli_agent_orchestrator/services/work_message_service.py` and transactional PostgreSQL persistence in `src/cli_agent_orchestrator/clients/work_message_repository.py`; use the verified store fence, message lane then common WorkAuthority job lane, sender-only read-only preflight, sender reauthorization before stable-key replay lookup, replay-before-receiver-liveness, sorted Work item/attempt locks, full current sender/receiver contracts, stable sender-item idempotency, exact body/target comparison, and at-least-once receive/ack (FR-004–FR-009). Ensure every writing `_chain`, `revoke`, origin authorization, and multi-candidate dispatch retirement takes the authority lane before grant or Work row locks.
- [x] T010 [US1, US3] Dispatch `cao.work.message` through `WorkOrigins.handle_mcp_request()` in `src/cli_agent_orchestrator/services/work_origin.py`, authenticating the proxy credential and deriving attempt/job/item/principal/generation only from that credential (FR-001–FR-005, FR-012).
- [x] T011 [US1, US3] Add `cao.work.message` to the explicit opt-in Docker allowlist and make it an optional addition to the exact ProcessAgentStep baseline tool set under its existing acceptance and host gates in `src/cli_agent_orchestrator/services/work_agent_step.py`; preserve baseline authority, require effective-contract authorization, and leave the unregistered Bubblewrap gate closed (FR-001, FR-002, FR-012).
- [x] T012 [US1] Redact all `body` fields before computing `cao.work.message` request/response effect hashes in `src/cli_agent_orchestrator/services/work_mcp_proxy.py`, including bodies serialized inside `content[].text` (FR-009).

## Phase 3 — Durable AG-UI and API replacement

- [x] T013 [US2] Add a pure projector for retained `work.message.sent` and `work.message.acknowledged` rows in `src/cli_agent_orchestrator/services/agui/work_messages.py`; emit one empty-delta text event only for sends and suppress ack/unrelated metadata while advancing the durable cursor (FR-009, FR-010).
- [x] T014 [US2, US3] Add `GET /jobs/{job_id}/messages/stream` in `src/cli_agent_orchestrator/api/work_routes.py` with `Last-Event-ID`/`after_sequence`, owner revalidation each poll, PostgreSQL cursor replay, metadata-only Work message frames, and a gap-only `RAW` frame (FR-010).
- [x] T015 [US2] Add integration coverage in `test/api/test_work_message_agui.py` proving API A send, API replacement on shared PostgreSQL, safe stop/reconciliation of the old receiver before receive/ack by its new authorized generation, safe stop/reconciliation of the old sender and uncertain proxy effect before exact retry with a fresh JSON-RPC ID by its new generation, and redacted replay without duplicate rows/events (FR-008, FR-010, FR-011).
- [x] T016 [US2, US3] Run existing provider generic `send_message`, human question/answer, AG-UI, and API-restart regression lanes; preserve their separate contracts (FR-012, SC-005).

## Phase 4 — Composition and completion gates

- [ ] T017 Run focused native PostgreSQL, Work proxy, process agent-step, Docker, API, and AG-UI tests; then run the complete applicable suite and fix failures at their first incorrect boundary (SC-001–SC-005).
- [ ] T018 Run `project-composition-check "$(cat .ai/project-name)"`, review transaction/concurrency/privacy/API/schema composition, refresh the Graphify impact only if the existing graph cannot represent the changed edges, and review the final diff (FR-001–FR-012).
- [x] T019 Record provider-backed Work identity, Bubblewrap's unregistered status, principal/grant continuity, message/job retention, event-retention gaps, and API-only proxy reattachment limits in `specs/024-work-agent-messages/spec.md`; distinguish those limits from the verified Docker/ProcessAgentStep behavior (FR-002, FR-005, FR-010–FR-011).

**Current main verification update (2026-10-19):** The focused Work message
models/schema/ledger/origin/proxy/AG-UI checks remain green offline. The native
Work message PostgreSQL module passed 17 tests against the reused healthy
service with automatic provisioning disabled, and the Work message API/AG-UI
route passed 10 tests. Additional pure Work message/schema/stream checks
passed 93 tests. T017 and T018 remain open because they still require the
complete applicable suite and final composition/diff review; no provider live
acceptance is inferred from these checks.

**Additional current-main verification (2026-10-19):** The focused Work
message model/service/proxy/AG-UI selection passed 115 tests with two expected
warnings, and `test/api/test_work_message_agui.py` passed 10 tests with five
expected warnings. These are offline/in-process checks; they do not establish
a real API-process replacement or provider-backed `cao.work.message` run.

**Interface boundary record (2026-10-19):** The current Codex AG-UI/restart
diagnostic belongs to the Spec022 interaction-question surface. It does not
stand in for a provider-backed `cao.work.message` execution. The Work message
selection remains 115 focused checks plus 10 API-route checks, and the native
PostgreSQL ledger module remains 17/17; T017 and T018 stay open until the
complete applicable suite, composition/diff review, and a real Work API
process-replacement/provider lane are evidenced.

## Dependencies and implementation order

- All T001–T007 regression tests precede production changes. T008 precedes T009; T009 precedes T010. T011 and T012 depend on the closed tool contract in T008. T013 precedes T014; T015 follows T010–T014. T016–T019 are completion gates after implementation.
- The isolated test files T001–T007 can be authored in parallel. Schema/repository work (T008–T009), proxy privacy work (T012), and pure AG-UI projection work (T013) can proceed independently after the red tests exist; route and end-to-end work depends on their contracts.
- MVP scope is US1 send/receive/ack with PostgreSQL authorization and body privacy. The feature is complete only after US2 durable owner-scoped AG-UI/API replacement and US3 negative authorization boundaries pass.
