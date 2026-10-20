# MCP tool contract

These tools are registered by the HTTP-only CAO MCP server. The server reads the per-terminal capability from the private file injected into the bundled cao-mcp-server entry. The shared CAO machine bearer authenticates the MCP-to-API hop only; it is not agent or human identity. CAO derives the terminal, incarnation, actor, current tool grant, source turn, owner and Work binding from server records. Tool arguments never name the source or a target terminal.

The tools are callable only when the current profile/tool allowlist grants cao-mcp-server and the capability remains active. No profile default or wildcard is added. A call whose profile permission, source incarnation, turn, Work attempt or delegation is stale fails closed.

## Tools

### cao_ask_user

Creates a group of explicit questions. Returns immediately with stable group/question IDs and lifecycle state. It does not write to a terminal or claim to have delivered an answer.

Input:

- operation_key: 16..128 characters, unique for one logical group-creation operation. The caller reuses the same value after a lost response; a new logical ask uses a new value. The MCP server forwards it unchanged as the HTTP `Idempotency-Key` header. Same key and same canonical request returns the original group; same key with different request conflicts.
- questions: 1..20 items; each has client_key (optional), kind (text, single_choice, multi_choice, choice_or_text, approval), prompt (1..16384 UTF-8 bytes), required, allow_free_text, and declared options (0..32, stable id plus label <=1024 bytes).
- expires_in_seconds: 1..86400; default 1800.
- wait_for_required: boolean; defaults true.

Output:

- group_id, current lifecycle, source-bound question_id/version pairs, required flags and expiry timestamps.
- Saturation, invalid response types, unsupported source mode or absent content owner are reported explicitly. Content may be recorded for a source that has no human owner, but human content remains unavailable until an authorized owner designation; the tool does not invent a recipient.
- Every explicit question starts human-required. Known approvals, credentials and native/unknown requests cannot be delegated. A content owner can review an exact question and classify it as a clarification through the bounded delegation action; an agent-provided intent or text never proves that classification. Answers are informational and never grant execution permissions; WorkDecisions remains the only Work approval authority.

### cao_wait_for_user_answers

Polls a group for up to 30 seconds per call. It returns ready only when every required question has an accepted response. Optional unanswered questions do not block. Ready responses are typed and include opaque receipts. A pending result contains no answer body. Calls may be repeated for the same group and task; the API revalidates source turn and current authority on every poll. This result returns to the same live MCP invocation/task and does not start a new agent task or replay its initial prompt.

Input: group_id, wait_seconds 0..30, optional after_event_sequence.  
Output state: pending, ready, expired, cancelled, stale, or cycle_blocked; answers; next_poll_after_ms.

### cao_acknowledge_user_answers

Acknowledges that the agent observed the exact returned receipts. Input is group_id and 1..20 tuples of question_id, version and receipt. It is idempotent and cannot acknowledge another source's result. Success returns `{success: true, acknowledged: N}` with only the count; errors use the bounded `{success: false, error: {code, detail}}` shape. Lack of an acknowledgement does not erase an accepted answer.

### cao_list_interactions

Lists safe metadata for the caller's source and live server-derived supervisor/delegation scope. Supports lifecycle filters and cursor/limit. A caller cannot expand scope with a terminal_id, work_item_id or actor field.

### cao_get_interaction

Reads one detail visible to the source or currently delegated supervisor. The API rechecks current relationship, content grant, source turn and Work authority. A redacted or out-of-scope item returns a denial without content.

### cao_answer_interaction

Allows a supervisor agent to answer a delegated clarification only when the server policy proves the answer is within existing authority. Input is question_id, expected_version, idempotency_key and the same tagged response union as the HTTP API. Questions still marked human-required, approvals, credentials, native/unknown questions and undeclared choices are rejected and remain/escalate to the authorized human. Only an owner-reviewed exact clarification version can have the human-required flag removed; answering it never authorizes an effect. The actor identity is derived from capability; Work authority is revalidated at accept and effect time.

### cao_escalate_interaction

Records an escalation reason (human_decision, insufficient_authority, supervisor_unavailable, deadline) and a bounded optional detail. The server chooses the authorized recipient. An agent cannot use this tool to designate a user or grant access.

## Legacy boundary

The existing answer_user_prompt tool has no question/version binding and accepts an arbitrary target terminal ID. It remains outside the feature 016 ledger flow and cannot answer, acknowledge, or reconcile a feature 016 request. Generic terminal input is guarded against an open required question and Work-owned targets remain denied. A manually answered native dialog is reconciled only when runtime evidence proves the dialog closed; CAO records no unseen answer text.
