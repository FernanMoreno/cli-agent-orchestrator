# Feature 016 data model

After Spec021, all persisted interaction state uses the configured native PostgreSQL main target shared with Work. Interaction tables use the interaction_* prefix and their own component migration ledger. Mutation methods accept a caller-owned WorkRepository transaction connection so request validation, Work authority checks, answer insertion and delivery claims share one PostgreSQL transaction. Work and interaction schema integrity are verified under runtime credentials; migrations require explicit bootstrap credentials. Historical SQLite files are read-only import artifacts, not a runtime fallback.

## Entities

### InteractionSource

The durable authority record for one terminal incarnation. It is the source of agent identity and turn freshness.

| Field | Type | Rules |
| --- | --- | --- |
| terminal_id | string | Existing CAO terminal ID; primary key with incarnation_id. |
| incarnation_id | string | Server-recorded session/process incarnation; never supplied as actor proof. |
| provider, provider_version, execution_mode | string | Capability reporting dimensions; unknown version stays unknown. |
| profile_revision, allowed_tool_fingerprint | string | Current profile/tool grant evidence, rechecked on each agent call. |
| owner_principal_id | string or null | Verified Principal subject captured at creation or an explicit operator designation. Null means no human content access. |
| owner_source | enum | authenticated_creation, validated_parent_inheritance, work_principal, operator_designation, unowned. |
| parent_terminal_id | string or null | Bound only from a server-validated spawn/assignment relationship. |
| work_job_id, work_item_id, attempt_id, attempt_generation, contract_hash, grant_id, grant_revision | nullable values | All or none as applicable; exact Work attempt binding. Revalidated by WorkAuthority. |
| turn_generation | positive integer | Interaction-owned counter. Increment on a new task delivery or direct terminal input; never increment for MCP result retrieval/ack. |
| capability_hash | bytes/string | Hash of random capability; raw token is never stored here. |
| capability_available | boolean | Server-managed installed capability state (schema v2); installation failure disables agent authentication and explicit emission while preserving the exact source for manual observation. |
| revoked_at | timestamp or null | Capability and source authorization cease to be usable at revocation/replacement. |

Source creation records a verified human owner when one is available. A child inherits an owner only when the server verifies the parent capability and durable assignment relation. A local loopback Principal retains the existing shared local trust limitation; it is not represented as a uniquely authenticated person. Existing sources without an owner remain metadata-only until an authorized operator explicitly designates one.

### Reviewed clarification delegation

Parent/assignment lineage identifies a candidate supervisor; it does not prove semantic authority over a prompt. Questions default to human-required. Only the verified content owner can review an exact question/version and grant its server-derived current supervisor a bounded clarification delegation (default 60 s, maximum 300 s). Approval and manual-only native questions remain nondelegable. Agent hints about purpose or human_required cannot create a grant. Reads and answers revalidate question/version, assignment, source incarnation/turn, deadline and Work authority. Revocation and deadline escalation return routing to the owner and preserve history. This answer authority grants no execution permission.

### InteractionGroup

One MCP ask/wait conversation that can contain several individual questions.

| Field | Type | Rules |
| --- | --- | --- |
| group_id | opaque UUID | Stable across retries and restart. |
| operation_key | bounded opaque string | Required from `cao_ask_user`; unique per source incarnation and turn. Reused key plus identical canonical request returns the original group; divergent reuse conflicts. |
| request_hash | digest | Canonical group request hash stored with operation_key to detect conflicting retry after a lost response. |
| source_key | terminal_id + incarnation_id | Server-derived from capability. |
| turn_generation | integer | Captured atomically at creation; a later generation makes open members stale. |
| work_binding | nullable exact Work tuple | Matches InteractionSource; no caller-supplied override. |
| required_question_ids | ordered list of opaque IDs | Explicit set; group is ready only when each required member has an answer returned to its MCP source (ack is tracked separately). Acceptance alone keeps the required execution wait blocked. Optional members do not block. |
| created_at, expires_at | UTC timestamps | Finite wall-clock deadline; configured default 1800 s, hard maximum 86400 s. |
| lifecycle | open, satisfied, expired, cancelled, stale, cycle_blocked | Recomputed from member question state and wait edges. |
| version | positive integer | Incremented on group lifecycle/routing transitions. |

Maximum 20 questions per group. A group retains question identity even when the agent polls repeatedly.

### InteractionQuestion and QuestionVersion

Question identity is stable within its lifecycle; each content, option, recipient, or source-binding change creates an immutable version. A response always names both question_id and expected_version.

| Field | Type | Rules |
| --- | --- | --- |
| question_id | opaque UUID | Stable identity, independent of visible text. |
| version | positive integer | Monotonic version; stale versions cannot accept answers or effects. |
| group_id | nullable opaque UUID | Null for an independent/native observation. |
| source_kind | explicit, native | Normal prose never creates an actionable native question. |
| source_key, turn_generation | source binding | Must match a live source at create, answer, return, and effect time. |
| dialog_fingerprint | nullable opaque value | Required for actionable native questions; derived from provider evidence, not raw transcript. |
| provider_evidence_version | nullable string | Captures versioned active-dialog observation. |
| kind | text, single_choice, multi_choice, choice_or_text, approval, credential, native_unknown | Ordinary approval choices are explicit, closed informational responses. They confer no execution authority. A real Work approval uses only the existing WorkDecisions contract. |
| human_required | boolean | Initially true for every explicit question. Approval, credential and unknown/native requests remain human-only. A trusted content owner may review the exact protected text and classify it as a clarification by granting bounded delegation; an agent cannot classify its own authority. No text or choice grants execution permissions. |
| prompt_content | private bounded text | Max 16 KiB UTF-8. Never emitted on general events. No full transcript. |
| options | ordered array of {id,label,available} | Unique stable IDs, max 32 options; each label max 1 KiB. Labels are data, never authority. |
| allow_free_text | boolean | Valid only for text or choice_or_text. |
| required | boolean | Required questions can pause dependent work; optional questions never do. |
| recipient_kind, recipient_id, route_reason | policy result | Derived server-side from owner and current assignment; agent suggestion cannot grant access. |
| response_capability | typed capability enum | pull_explicit, provider_native, manual_only, unsupported. Detection and response capability are separate. |
| lifecycle | open, answered, expired, cancelled, stale, manual_only, reconciled_external | Lifecycle is separate from delivery status. Manual-only cannot accept an API answer. |
| created_at, expires_at | UTC timestamps | Bounded by configured deadline and source lifecycle. |

### InteractionAnswer

Immutable answer to one exact question version. The database enforces one effective answer per question/version.

| Field | Type | Rules |
| --- | --- | --- |
| answer_id | opaque UUID | Stable result receipt identity. |
| question_id, question_version | composite reference | Exact current version at acceptance. |
| idempotency_key | bounded opaque string | Unique within the answer scope. |
| canonical_payload_hash | digest | Detects same-key divergent replay without returning a second effect. |
| response | tagged union | text, option_ids, or free_text; max 4 KiB response text. IDs must belong to this version and remain available. |
| actor_kind, actor_id | principal, agent | Constructed by server from sealed Principal or source capability; never copied from request body. |
| accepted_at | UTC timestamp | Written with the answer and initial accepted delivery record in one transaction. The later MCP pull separately records returned_to_mcp; only an explicit receipt acknowledgement records agent observation. |
| content_visibility | private ACL reference | Response body is returned only to an authorized content principal or the bound source/delegated agent. |

For concurrent attempts, the first valid answer wins. The same key and same payload returns its existing receipt. The same key with a different payload conflicts. A different key with the same canonical answer resolves to the existing receipt; a divergent answer returns a conflict.

An answer to kind approval is a recorded human response only. It does not authorize a Work effect, expand tools/permissions, or replace a Work decision receipt. Any Work approval is created and consumed only through WorkDecisions with its exact attempt, generation, contract hash, grant and current Principal.

### InteractionDelivery

Tracks the way an accepted answer is made visible to the source. An explicit MCP result is a pull receipt; a native provider effect is a claimed external effect.

| Field | Type | Rules |
| --- | --- | --- |
| delivery_id, answer_id | opaque IDs | One answer may have ordered attempts only after a recorded no-effect proof. |
| mode | mcp_pull, provider_native, manual | Explicit channel uses mcp_pull; unknown dialogs remain manual. |
| state | accepted, prepared, claimed, returned_to_mcp, agent_acknowledged, confirmed, uncertain, no_effect, reconciled | Return/confirmation is distinct from agent acknowledgement. |
| claim_nonce, claimed_at | nullable values | Claim is durable before provider I/O. No automatic claim is created for manual-only requests. |
| evidence | bounded safe metadata | No transcript or answer content in public history/events. |
| receipt_hash | digest | Stable replay/ack binding; does not imply model acceptance. |
| updated_at | UTC timestamp | Monotonic state transition. |

If a process restarts after claimed without proof of no effect, recovery changes the state to uncertain. Uncertain and partial effects are never resent automatically. A retry requires explicit reconciliation and evidence that the prior attempt had no effect.

### InteractionWait

Durable dependency edge between work and a group of required questions.

| Field | Type | Rules |
| --- | --- | --- |
| wait_id | opaque UUID | Stable across restart. |
| dependent_kind, dependent_id | terminal task or Work item/attempt identity | Names only the work that depends on the required group. |
| group_id | opaque UUID | Waits only for declared required question IDs. |
| state | waiting, released, expired, cancelled, stale, cycle_blocked | Optional questions do not create a blocking edge. |
| active_elapsed_before_pause | duration | Preserves existing execution budget accounting. |
| response_deadline | UTC timestamp | Separate human wall-clock deadline. |
| paused_at, released_at | timestamps | Wait time is excluded from active runtime budget, not from Work lease/grant validity. |

The wait graph is derived from current edges and server-known assignment links. Cycle detection is advisory and visible; an operator can cancel/resolve a wait edge but the service never synthesizes an answer.

### InteractionPrivateAudit

An append-only protected table stores the bounded operator reason for content-owner designation, cancellation and delegation revocation. Its event sequence, action, sealed Principal identity and exact target are written in the same transaction as the public event. Reasons are not copied into metadata, detail/history DTOs, or provider configuration; recognized secrets are rejected before persistence.

### InteractionEvent

Append-only audit history for creation, routing, escalation, answer acceptance, return/ack, delivery, cancellation, expiration, stale detection and reconciliation.

Events contain opaque IDs, transition, source/actor category, timestamps, and safe reason codes only. They do not contain prompts, answer payloads, credentials, or terminal transcript. Snapshot APIs remain the source of truth after dropped or duplicated events.

## Persistence and transaction rules

- interaction_schema_migrations versions only interaction_* tables. Migration/bootstrap uses the same configured database path and does not modify Work migrations.
- Every mutation runs through WorkRepository.transaction() and passes that connection to InteractionRepository and WorkAuthority. No second connection is opened inside an authorization/claim transaction.
- Request creation, source turn advancement, answer acceptance, delivery claim, cancellation and reconciliation use compare-and-swap on IDs and versions.
- Existing terminal_turn_receipts may add freshness evidence when present; it is not substituted for the interaction source generation.
- IDs, sender_id, and actor_kind from a DTO are ignored or rejected as proof. Agent identity is recovered from capability hash; human identity comes from a verified Principal.
- Request and answer bodies are protected detail. List/snapshot/event data returns metadata only unless an exact resource content grant is present. General cao:read does not grant content.
- Secrets and access credentials are not accepted as answer content. Detection can only cause a private-provider/manual notice; secret-looking text is not placed into a generic answer.
- Saturation rejects the incoming request and preserves current rows. Initial configured limits: 100 active total, 20 active per source, 20 per group, 32 options, 16 KiB prompt, 1 KiB option label, 4 KiB answer, default 30 minute expiry and 24 hour hard maximum.

## State transitions

Request lifecycle:

    open -> answered | expired | cancelled | stale | manual_only
    open -> reconciled_external

Delivery/observation lifecycle:

    accepted -> returned_to_mcp -> agent_acknowledged
    accepted -> prepared -> claimed -> confirmed
    claimed -> uncertain | no_effect
    uncertain -> reconciled
    no_effect -> prepared only after explicit authorized reconciliation

Group/wait lifecycle:

    open/waiting -> satisfied/released | expired | cancelled | stale | cycle_blocked

No transition from expired, cancelled, stale, or manual-only to answer-accepted is permitted. Reopening a changed question creates a new version and requires a new current-source check.

### Deadline race for accepted answers

The group deadline bounds the runtime wait as well as human admission. A pre-expiry same-source pull releases the required edge; expiry committed first remains a terminal reconcile state. A late pull may recover an already accepted answer under fresh source/Work authority, but never reopens the expired task. Post-accept delegation revocation conflicts rather than creating an answered version with no matching answer.
