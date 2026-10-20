# Implementation Plan: PostgreSQL-Durable Work Agent Messages

## Summary

Add `cao.work.message` as a distinct managed Work proxy operation for process-backed attempts mediated by the existing opt-in Docker and acceptance-gated ProcessAgentStep paths. Persist bounded message bodies, idempotency bindings, acknowledgement state, and body-free domain events in one transaction on the configured PostgreSQL Work target. Bind a new send to the authenticated sender and an active, authorized receiver generation in the same job. Resolve an exact stable-sender-WorkItem/key replay before receiver liveness checks so a lost response can be recovered after both old attempts and the sender's uncertain effect are safely reconciled. Bind receive/ack to the authenticated current generation of the stable receiver Work item; a replacement generation inherits that logical mailbox only after safe stop and reconciliation, with its own live grant and contract. Project committed sends to an owner-authorized job-scoped AG-UI stream without message text. Keep the existing generic provider `send_message` path separate.

## Test-Driven Development Decision

Use TDD for this cross-boundary behavior. First add closed-schema and failing PostgreSQL, proxy authorization, privacy, API-replacement, and owner-scoped AG-UI replay regressions. Include a migration-failure test that proves a failed forward migration rolls back its DDL and version receipt; this migration framework has no downgrade API. Then implement the smallest production change that passes them. Existing tests for generic provider `send_message`, task receipt, result submission, child, and handoff remain regression gates.

## Technical Context

- **Language/runtime**: Python 3.12+, PostgreSQL, psycopg, FastAPI, MCP JSON-RPC.
- **Persistence**: Existing Work schema is a versioned PostgreSQL component migration; `WorkRepository.transaction()` verifies the store and provides the transaction connection used by `work_events`.
- **Identity**: `WorkAttemptCredentialContext` already binds attempt, generation, job, work item, principal, grant revision, contract hash, installation UUID, and expiry.
- **Proxy gates**: `DockerWorkBackend` and `ProcessAgentStepV2` have closed managed-tool allowlists; `WorkOrigins.handle_mcp_request()` dispatches current Work calls. The unregistered Bubblewrap backend remains fail-closed and is not included.
- **Events/UI**: `work_events` is durable and sequence-addressable by job. AG-UI maps metadata-only `send_message` events to `TEXT_MESSAGE_CONTENT`; its fleet replay ring is in process memory.
- **Constraints**: Current process proxy request frame limit is 16 KiB including newline; response frame limit is 64 KiB including newline. No SQLite fallback is allowed. Existing Work schema, V1/V2 SQL and checksums, and broad dirty worktree must be preserved.

## Design Decisions

1. Sender, job, Work item, attempt, principal, generation, grant, contract, and lease identity come exclusively from the authenticated Work proxy credential and current PostgreSQL binding.
2. The target is a stable receiver Work item ID. A fresh key requires a distinct current live attempt, same job, and effective message permission. A replay of an existing exact `(sender_work_item_id, idempotency_key)` binding is checked first and does not require the receiver to remain live. Mailbox access is keyed by Work item so a replacement generation can recover pending messages only after safe reconciliation and its own authorization. Principal/grant continuity across generations is not guaranteed. A read-only preflight authenticates the sender and reads only the stable key; it does not inspect the receiver. After the Work store fence, each message write takes a job-scoped message lane and then a shared WorkAuthority lane before sender reauthorization. Every writing grant-chain check takes that authority lane before locking its leaf, ancestors, and job; `revoke` takes it before its first grant-row lock, origin authorization takes it before origin-history advisory locks, and dispatch retirement takes it before job/item/attempt rows when later candidates may revalidate in the same transaction. The lane is transaction-scoped and reentrant. A write transaction reauthenticates the sender, checks the stable key, and returns an exact replay before any receiver lookup. For a fresh key, the transaction locks the job and sorted Work item/attempt rows, then fully revalidates the current receiver contract and complete grant ancestry while retaining the same authority lane. This serializes message sends with plain contract revalidation and authorization/revocation across sibling grants without changing the ancestry lock order. The per-job event sequence is then acquired through `_append_event`.
3. `send`, `receive`, and `ack` share one MCP tool name but have exact, closed action payloads and results. Keys use `[A-Za-z0-9._:-]{1,128}`; bodies are strict UTF-8, nonempty, NUL-free, at most 4096 bytes. Receive returns the oldest two pending rows at most, at least once, until explicit acknowledgement.
4. Message rows, idempotency, ack state, and `work.message.sent` / `work.message.acknowledged` events live in PostgreSQL Work storage. The row body is the only durable copy of message content; event/audit metadata carries IDs and routing only. Idempotency is keyed by stable sender Work item plus key so an authorized later generation, even with a different principal/grant, can recover a send whose response was lost. The original sender principal/attempt/generation remain immutable attribution. Repeated acknowledgements do not append duplicate events.
5. The proxy effect journal hashes a canonical request/response projection with every message `body` removed, including JSON content nested inside MCP text results. The request projection retains JSON-RPC ID to preserve current per-attempt replay protection; application retries use a fresh JSON-RPC ID.
6. An owner-authorized, job-scoped AG-UI SSE stream reads retained Work event pages by durable PostgreSQL cursor, so API replacement can replay only message-send metadata without using the global in-memory ring or exposing unrelated Work event metadata. Live projection happens only after commit and emits one empty-delta text notification per first send. Acknowledgements remain durable Work events but do not create another text notification.
7. Active Docker and ProcessAgentStep Work attempts are the supported callers in this version. The unregistered Bubblewrap and provider-backed Work identity seams are out of scope.

## Project Structure

- `src/cli_agent_orchestrator/clients/work_postgres_schema.py`: additive V3 schema migration for messages, idempotency, and acknowledgement; V1/V2 SQL, checksums, and `WORK_TABLES` remain unchanged.
- `src/cli_agent_orchestrator/clients/work_message_repository.py`: message-row SQL on the caller-owned Work PostgreSQL transaction.
- `src/cli_agent_orchestrator/services/work_message_service.py`: attempt credential and current-contract authorization, deterministic lock order, idempotent send/replay, receive, acknowledgement, and atomic body-free Work event append.
- `src/cli_agent_orchestrator/models/work_message.py`: closed request/result models and strict UTF-8/NUL/body/page validation.
- `src/cli_agent_orchestrator/services/work_origin.py`: authenticate and dispatch the new tool from proxy credential context.
- `src/cli_agent_orchestrator/services/work_agent_step.py` and `src/cli_agent_orchestrator/backends/docker_backend.py`: add the tool only to active process-backed allowlists; effective contract permission remains mandatory.
- `src/cli_agent_orchestrator/services/work_mcp_proxy.py`: compute message effect-journal request/response digests over body-redacted data.
- `src/cli_agent_orchestrator/services/agui/work_messages.py` and `src/cli_agent_orchestrator/api/work_routes.py`: map committed send metadata to `TEXT_MESSAGE_CONTENT` and implement `GET /jobs/{job_id}/messages/stream` with `Last-Event-ID`/`after_sequence`, owner revalidation on each poll, gap frames, and PostgreSQL event replay.
- `test/clients`, `test/services`, `test/backends`, and `test/services/agui`: migration, authorization, idempotency, API replacement, and privacy regression coverage.

## Data / Transaction Flow

1. A Docker or ProcessAgentStep worker calls `cao.work.message` through its authenticated proxy.
2. The proxy revalidates the sender's current contract, generation, lease, revocation state, and tool permission, then writes a body-redacted effect fingerprint.
3. The handler validates the closed action payload. For `send`, a read-only preflight authenticates the sender and inspects the stable sender-item key only. The PostgreSQL write transaction verifies the store, takes the credential-job message lane, then the common job authority lane, fully reauthenticates the sender, and checks the key again.
4. An exact replay returns its existing ID before receiver lookup or liveness checks. For a fresh `send`, the write transaction locks the job and sorted sender/receiver items and attempts, revalidates the sender after row locks, then fully revalidates the current receiver contract and grant ancestry under the same authority lane. Dispatch retirement also takes the authority lane before its row locks, since candidate scanning may revalidate another grant in the same transaction. One transaction inserts the message and appends one body-free sequenced event.
5. Only after commit, the event stream projects `work.message.sent` to one metadata-only `TEXT_MESSAGE_CONTENT` event with empty delta.
6. The receiver's own authenticated `receive` call returns the oldest bounded page for its stable Work item. `ack` updates only a message addressed to that Work item and, on its first state change only, appends one body-free `work.message.acknowledged` event.
7. A replacement API uses the same PostgreSQL Work target to recover rows and sequence history. A new attempt generation of the same sender/receiver Work item can retry/retrieve only with its independently valid current authorization. The old in-memory proxy socket is not reconstructed.

## Risks and Mitigations

- **Cross-job disclosure**: compare sender/receiver `job_id` inside the transaction; do not reveal whether a denied target exists.
- **Stale generation or revoked attempt**: verify both exact generations, state, contract permission, and lease while holding the transaction's existing store/row locks.
- **Duplicate/conflicting send**: unique stable-sender-WorkItem idempotency key plus exact target/body equality; any current authorized generation acts for that logical item, while the original principal/attempt/generation remain immutable attribution.
- **Body leakage**: keep the body and any body-dependent digest out of Work events, AG-UI, logs, and effect metadata; redact `cao.work.message` before proxy journal request/response hashing and test serialized event/frame/journal structures.
- **Migration compatibility**: append a versioned migration and prove forward migration plus transactional rollback of partially failed DDL without changing the active configured PostgreSQL target; the migration framework has no downgrade API.
- **AG-UI process replacement**: use an owner-authorized job-scoped stream that emits only `work.message.sent` as `TEXT_MESSAGE_CONTENT`, with durable Work event cursors; acknowledgements and unrelated events advance the internal cursor without exposing metadata. Never put Work message data into the global fleet ring. Existing retention gaps remain visible through the current cursor gap semantics.
- **Active attempt replacement**: mailbox identity is the stable Work item, while every read/ack still authenticates the current attempt generation and current grant/contract. A new generation can continue the logical Work item only after safe stop and reconciliation; it may have a different principal/grant. No automatic cross-item reroute occurs and an API restart does not reattach a live proxy socket.

## Validation Strategy

- Contract and boundary tests for exact action/result schemas, valid/invalid JSON-RPC, active allowlists, no-permission calls, Unicode, NUL rejection, maximum byte size, response escaping, and proxy frame limits.
- Real PostgreSQL tests for V2-to-V3 upgrade, partial-DDL rollback, atomic message/event rollback, stable-item replay, retry conflict, receive/ack scoping, concurrent sends/acks, and independent repository instances.
- Work event tests proving body absence and owner-scoped sequence recovery.
- Job-scoped AG-UI stream tests proving empty delta, body omission, unauthorized job denial, and cursor replay after API replacement.
- Existing Work provider/proxy, generic `send_message`, question/answer restart tests, full project suite, architecture/composition gates, and final diff review.
