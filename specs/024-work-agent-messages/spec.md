# Feature Specification: PostgreSQL-Durable Work Agent Messages

**Feature Branch**: `[024-work-agent-messages]`

**Created**: 2026-10-17

**Status**: Draft

**Input**: User confirmed that the API-restart/AG-UI acceptance must include generic messages between agents (`cao.work.message`).

## User Scenarios & Testing

### User Story 1 — Send a message to another Work agent (Priority: P1)

As a running Work agent, I need to send a bounded message to another authorized agent in my job and let that agent retrieve it, so agents can coordinate without using the generic terminal `send_message` authority.

**Why this priority**: The current managed Work MCP proxy has no `cao.work.message` operation. Its current `child` and `handoff` calls create new work; they do not provide ordinary agent-to-agent messages.

**Independent Test**: Create two process-backed Work items in one PostgreSQL job with the message permission. Send, retrieve, and acknowledge one Unicode message through their bound Work proxy identities. Verify one Work message row, one durable Work event, and no message body in events or AG-UI.

**Acceptance Scenarios**:

1. **Given** two distinct live Docker or ProcessAgentStep Work items in the same job whose current effective contracts grant `cao.work.message`, **When** the sender submits a valid message with a stable idempotency key, **Then** PostgreSQL commits one durable message and a body-free Work event, and the sender receives the committed message ID.
2. **Given** a pending message addressed to the receiver Work item, **When** any live generation of that same Work item retrieves messages using its own proxy credential, **Then** it receives the body and stable message ID, and the message remains available until acknowledged.
3. **Given** the receiver acknowledged a message, **When** it retrieves again, **Then** the acknowledged message is absent from the pending result and remains represented in durable history.
4. **Given** a send committed but its response was lost during API replacement, **When** the sender's old generation and uncertain proxy effect have been safely stopped and reconciled and an authorized new generation of that same sender Work item retries the same key, target, and body with a fresh JSON-RPC request ID, **Then** it receives the original message ID and does not create another row or event, even if the receiver is no longer live.

### User Story 2 — Preserve messages across API replacement and project them to AG-UI (Priority: P1)

As an operator, I need Work messages and their audit events to survive API replacement on the same PostgreSQL target, while live AG-UI notifications expose routing metadata without the body.

**Independent Test**: Use API A to send one Work message and observe its live AG-UI notification. Replace the API process while retaining PostgreSQL. After the receiver's old generation has been safely stopped and reconciled, a newly authorized generation of the same receiver Work item retrieves and acknowledges the original message. After safely stopping and reconciling the original sender generation and its uncertain proxy effect, a new authorized generation retries the exact send using a fresh JSON-RPC request ID and receives the original message ID. The owner-scoped AG-UI stream on API B resumes from the durable Work event cursor. Send a second message through B and observe its live redacted notification.

**Acceptance Scenarios**:

1. **Given** a committed message on API A, **When** API A is replaced by API B on the same PostgreSQL target and the receiver Work item starts a new generation, **Then** that generation can retrieve the same message ID and exact body without creating another row.
2. **Given** API B is active, **When** the job owner with a read-capable scope resumes the Work job's AG-UI stream from its durable sequence cursor, **Then** API B replays retained body-free message events from PostgreSQL in order; cursors older than the retained floor report the existing event-gap semantics.
3. **Given** API B is active, **When** a new message is sent, **Then** the durable Work event and live AG-UI `TEXT_MESSAGE_CONTENT` notification are visible on B, with an empty `delta` and no body.

### User Story 3 — Reject cross-job, stale, unauthorized, or malformed messaging (Priority: P1)

As a Work job owner, I need the messaging boundary to prevent agents from reaching other jobs, stale attempt generations, or tools they were not granted.

**Independent Test**: Exercise valid proxy credentials against missing permission, cross-job target, wrong generation, non-running target, revoked/expired sender, invalid payload, duplicate/conflicting idempotency key, and unauthorized receive/ack cases; denied calls create no message or event.

**Acceptance Scenarios**:

1. **Given** a sender and receiver in different jobs, **When** the sender addresses the receiver, **Then** the call is denied without revealing whether that receiver exists.
2. **Given** a stale or revoked sender/receiver generation, **When** the caller sends, receives, or acknowledges, **Then** the call is denied before any state change.
3. **Given** a duplicate send with the same key and identical receiver/body, **When** retried, **Then** it returns the original message ID without another row or event; a different receiver or body with the same key conflicts.
4. **Given** malformed, oversized, or extra-field arguments, **When** called, **Then** the proxy rejects them before persistence.

## Requirements

### Functional Requirements

- **FR-001**: System MUST expose the distinct Work proxy tool name `cao.work.message`; it MUST NOT alias or replace the generic terminal MCP tool `send_message`.
- **FR-002**: The first supported callers MUST be process-backed Work attempts mediated by the existing opt-in `DockerWorkBackend` or acceptance-gated `ProcessAgentStepV2` paths, whose bound proxy credential supplies attempt ID, generation, job, principal, contract, and lease. Messaging MUST remain optional within ProcessAgentStep's exact baseline tool set and MUST NOT broaden its activation or host-acceptance gates. Provider-backed Work calls are out of scope until their source provides an equally strong attempt identity. The unregistered, fail-closed Bubblewrap backend remains out of scope.
- **FR-003**: The tool MUST support three closed argument shapes: `send` = `{operation, receiver_work_item_id, idempotency_key, body}`; `receive` = `{operation, limit?}`; `ack` = `{operation, message_id}`. `operation` MUST be one of the three exact lowercase values. `receiver_work_item_id` and `message_id` MUST be nonempty strings no longer than 128 characters. `idempotency_key` MUST match `[A-Za-z0-9._:-]{1,128}`. `body` MUST be a nonempty Unicode string encodable as strict UTF-8, contain no U+0000, and encode to at most 4096 bytes. `receive.limit` MUST be an integer from 1 to 2, defaulting to 1. Caller arguments MUST NOT supply sender identity, job identity, attempt/generation, credentials, a receiver identity for `receive`/`ack`, or arbitrary URLs. The complete JSON-RPC request frame, including its terminating newline, MUST be at most 16384 bytes; the UTF-8 body bound does not override this envelope bound.
- **FR-004**: Every send MUST authenticate the current sender attempt and verify its effective contract grants `cao.work.message`. Within the write transaction, the system MUST look up `(sender_work_item_id, idempotency_key)` after reauthenticating the sender and before checking receiver liveness. An exact replay MUST compare the original receiver and body and return the original message ID without a new row/event, even if the receiver is no longer live; a changed receiver or body MUST conflict. Only a previously unused key is a new send: it MUST target a distinct Work item in the same job whose current live attempt's effective contract also grants `cao.work.message`; no cross-job target is allowed. A read-only preflight MAY authenticate the sender and inspect the stable key, but MUST NOT inspect the receiver, return a result, or make an authoritative send decision. After the Work store fence, every send, receive, and ack MUST acquire the transaction-scoped message lane keyed by the credential row's job ID and then the common transaction-scoped authority lane for that job before any grant-row locks. Every write `_chain` MUST acquire the authority lane before locking its grant ancestry, and grant revocation MUST acquire it before its first grant-row lock. Origin authorization writers that combine grant-chain checks with origin-history advisory locks MUST take the authority lane before those history locks. Dispatch candidate retirement MUST also take the lane before job/item/attempt locks when the same transaction can revalidate later candidates. For a fresh send, the transaction MUST reauthenticate the sender, check the idempotency key, then lock the job, sorted Work item IDs, and sorted attempt IDs/generations before fully revalidating the current receiver contract and grant ancestry under the same authority lane. Grant-chain checks retain their existing leaf-to-root-then-job row order; a lane already held by the transaction is reentrant. This single per-job lane prevents a sender chain, receiver chain, plain contract revalidation, dispatch retirement, origin authorization, or revocation from holding one branch while waiting on another branch's shared ancestor or job. A fresh receiver change or failed contract/grant check MUST fail closed. The per-job event sequence is then acquired through `_append_event`; concurrent identical keys MUST converge on one immutable message and conflicting reuse MUST fail.
- **FR-005**: Messages MUST be addressed to the stable receiver Work item, not a process attempt. Receive and acknowledge MUST derive that Work item and the current generation from the caller's authenticated proxy credential. A replacement generation may retrieve pending messages only after the old attempt is safely stopped/reconciled and the new generation is current, live, and independently authorized. This mailbox belongs to the logical Work item: the system does not promise principal/grant continuity across generations.
- **FR-006**: Success MUST be returned as compact JSON in the MCP text result with closed shapes: send returns `{message_id, status}` where status is `sent` or `replayed`; receive returns `{messages:[{message_id, sender_work_item_id, body, created_at}]}`; ack returns `{message_id, status}` where status is `acknowledged` or `already_acknowledged`. Receive MUST return the oldest pending messages in `(created_at, message_id)` order, at least once until ack, in a bounded page of at most two, default one. The complete JSON-RPC response frame, including its terminating newline, MUST remain at most 65536 bytes; tests MUST cover two maximum-size valid bodies with worst-case JSON escaping and the byte limit.
- **FR-007**: Message, idempotency binding, and body-free `work.message.sent` event MUST commit atomically through the configured PostgreSQL Work target. No SQLite fallback or cross-database partial commit is permitted.
- **FR-008**: Idempotency MUST be unique per stable sender Work item and key while the initiating attempt ID, generation, and principal remain on the immutable message record. An exact retry by a later current, independently authorized generation of that same logical sender Work item returns the original ID without another row or event, even if its principal/grant binding changed; this is WorkItem-owned mailbox semantics. Reuse with changed target/body conflicts. An application-level idempotency retry MUST use a fresh JSON-RPC request ID; the existing proxy may reject an identical body-redacted effect fingerprint within one generation before dispatch. Acknowledgement MUST be idempotent and append one body-free `work.message.acknowledged` event on its first state change only.
- **FR-009**: Message bodies MUST be stored only in the durable message row and returned only to the authorized receiver. Events, AG-UI, diagnostics, and Work audit metadata MUST omit the body and any digest that depends on it. The existing MCP effect journal MUST hash a canonical request/response projection with every `body` field removed for `cao.work.message`, including JSON encoded inside MCP `content[].text` for receive responses. The redacted request projection MUST preserve the JSON-RPC request ID so the existing per-attempt replay guard remains effective.
- **FR-010**: `GET /jobs/{job_id}/messages/stream` MUST provide an owner-authorized job-scoped AG-UI SSE stream backed by PostgreSQL Work events. It MUST resume from `Last-Event-ID` when supplied, otherwise the `after_sequence` query parameter, and poll/replay retained events by the per-job durable sequence. It MUST map each `work.message.sent` to one `TEXT_MESSAGE_CONTENT` event with the Work sequence as the SSE ID and metadata-only sender/receiver/message identifiers plus empty `delta`; an acknowledgement MUST NOT produce a second text notification. A cursor gap MUST be reported as a `RAW` frame containing only the gap range, and that frame's SSE ID MUST equal the gap's `through_sequence` retained floor so reconnect does not repeat the same gap. The stream MUST advance across unrelated Work events without exposing their metadata, revalidate owner access on reconnect and every poll of an open stream, and MUST NOT use the global process-local replay ring.
- **FR-011**: After API replacement on the same configured PostgreSQL target, both the prior sender and receiver attempts MUST be safely stopped and reconciled before their replacements operate; any uncertain sender proxy effect MUST also be reconciled before a new sender generation retries. Once a new generation is current and independently authorized, that generation of the same receiver Work item MUST retrieve the same pending message ID/body and acknowledge it; a new authorized sender generation of the same sender Work item MUST be able to retry an exact send whose response was lost without duplicate persistence. The system MUST NOT promise that an in-memory proxy socket, the same process generation, or principal/grant identity continuity survives API replacement, and MUST NOT redirect messages to another Work item.
- **FR-012**: Existing generic terminal `send_message`, task receipt, result submission, child, and handoff contracts MUST retain their current authority and behavior.

### Key Entities

- **Work message**: A bounded body addressed to a stable Work item, with sender identity derived from the live attempt credential and receiver generations checked on each read/ack.
- **Message idempotency binding**: The stable sender-Work-item/key tuple plus exact receiver/body comparison that prevent duplicate or conflicting sends across authorized retry generations without storing a body digest; the first principal/attempt/generation remain attribution fields, but a current authorized generation acts for the stable Work item.
- **Work message event**: A sequenced PostgreSQL domain event containing routing metadata and message ID but never the message body.

## Success Criteria

- **SC-001**: Authorized same-job send/receive/ack succeeds across two process-backed attempts and two API process instances sharing PostgreSQL.
- **SC-002**: Replaying an identical send creates exactly one message row and one `work.message.sent` event; repeated acknowledgements create no duplicate acknowledgement event.
- **SC-003**: Every negative authorization or validation case creates zero message rows and zero message events.
- **SC-004**: AG-UI emits exactly one `TEXT_MESSAGE_CONTENT` event per first committed send and can replay retained send events after API replacement; neither Work event metadata nor emitted frames contain the body or a body-dependent digest.
- **SC-005**: Existing Work proxy, provider `send_message`, question/answer, and API-restart checks remain green.

## Assumptions and Scope Boundaries

- Messages are scoped to one Work job and one logical receiver Work item; they are not a public chat API. Receiver attempts are generation-bound at read time.
- Message rows and pending/acknowledged state are retained in PostgreSQL for the lifetime of the Work job. Event retention may remove old replay events; this does not remove a pending message. Version 1 adds no independent message-body pruning API.
- PostgreSQL is the only durable database for Work messages and Work events.
- The general fleet AG-UI replay ring remains process-local. Work messages use a separate job-scoped AG-UI stream backed by PostgreSQL `work_events`, with owner authorization and durable cursors.
- The stable Work item is the logical messaging principal. A newly authorized generation for that same item inherits its pending receiver mailbox and exact-send idempotency bindings, even if its attempt principal or grant differs; the original principal remains in immutable attribution.
- An active Work proxy socket is process-local today. Recovery means a new generation of the same logical Work item may retrieve its mailbox; reconnecting the exact same process/socket is not guaranteed.
- Work-owned attempt-to-attempt messaging uses the Work message ledger. Generic provider-terminal messaging continues to use `send_message` and the existing inbox.
