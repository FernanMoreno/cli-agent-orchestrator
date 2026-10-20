# Runtime, event and provider contract

## Provider observation

Providers expose a bounded observation value rather than asking the service to parse terminal prose:

| Field | Contract |
| --- | --- |
| provider, provider_version, execution_mode | Exact observed implementation dimensions; unknown stays unknown. |
| observation | none, active_dialog, manual_signal. |
| dialog_fingerprint, dialog_version | Stable opaque identity for the currently active dialog. Required before an observation is actionable. |
| dialog_kind | text, single_choice, multi_choice, choice_or_text, approval, credential, unknown. |
| prompt, options | Only structured provider evidence for the active dialog; bounded. Never copied from a general transcript. |
| answer_capability | none or provider_native with an explicit supported response type. Detection alone is not capability. |
| manual_reason | Required for unsupported/unknown/credential cases; safe to show without copying secret content. |
| exact_target | Bound runtime/session/window identity, never a caller-chosen terminal selector. |
| observed_at | UTC timestamp; service rejects expired evidence and rechecks the same fingerprint/version immediately before an effect. |

No text regex or status transition alone can produce answerable content. Credential/authentication dialogs expose a private-provider/manual notice with no credential form. The initial Codex, Claude Code, OpenCode and Hermes support record is manual-only for native response until the per-version validation in quickstart proves a type-specific response route. OpenCode's existing profile/tool restrictions are unchanged.

If native answer support is later evidenced, it must use this effect protocol:

1. In one transaction validate question version, source incarnation/turn, dialog fingerprint/version, current target, human content authority and any Work grant/contract; persist answer and claim nonce.
2. Perform the exact provider operation only on the source's authorized runtime. Work uses WorkBackendView and its exact attempt terminal/target validation. Remote workers never fall back to local terminal input.
3. Record confirmed only with provider evidence. If the effect may have happened but confirmation is missing, record uncertain and prohibit automatic resend. Record no_effect only when the adapter proves no input/effect occurred.
4. Reconcile or retry only through an explicit authorized action after proof; preserve the prior attempt in history.

## Source turn and runtime ownership

The interaction source registry owns a durable turn_generation per terminal incarnation for all providers, including providers without receipt support. It advances on server-recorded new task delivery and direct terminal input. It does not advance when an MCP wait returns an answer or when the agent acknowledges a receipt. A source replacement/incarnation change revokes capability and stales pending interactions.

For explicit pull, `returned_to_mcp` means the authorized API wait committed the durable answer into its result for the originating MCP call. It is not proof that a network response arrived or that the model read, used or understood it; a lost response can be retried for the same receipt. `agent_acknowledged` separately records observation. Native effects use `confirmed` only with provider evidence.

terminal_service owns propagation of the verified creation Principal and server-derived parent/Work owner context into source registration. It issues/rotates the capability and writes a private capability file. Provider initialization receives only the file path via the BaseProvider setter, before initialization starts. Provider config helpers overlay that path only on the bundled cao-mcp-server entry; they do not place the secret in argv or general MCP env forwarding. Profile-provided values cannot override the protected entry.

MCP server code remains an HTTP client; it does not import the terminal database or perform tmux/provider I/O. Agent capability and CAO_AUTH_LOCAL_TOKEN are distinct credentials with distinct meanings. Human interaction routes require a sealed Principal and reject the agent capability. Under existing auth-disabled loopback mode, only the existing shared local-operator trust can be claimed.

## Inbox, Work and waiting integration

- InboxService and every automated task/message delivery path consult a provider-independent required-question guard before input. A required open question or accepted answer awaiting its originating MCP pull leaves later task delivery pending; provider opt-in is not the only guard. Optional questions do not block. An explicit answer is never routed through that generic input path.
- Work-owned inputs remain denied by ordinary assignment/input APIs. A managed worker that lacks an exact explicit-interaction capability is reported unsupported. Work V2 process receives no new network, terminal or MCP capability. Work approvals continue through WorkDecisions.
- An answer to an interaction question, including a declared approval choice, is not authorization to execute. Work effects require the existing WorkDecisions receipt and fresh attempt/generation/contract/grant/Principal validation.
- A required question creates a durable wait edge for only the dependent task. agent_step pauses its active execution budget while the edge is waiting, tracks human expiry separately, and preserves task identity and verified result receipts. It resumes the same task after the same group becomes ready, without redelivering its initial task. It never extends a Work lease or grant.
- Direct terminal dialog closure may be reconciled as closed_outside_cao only if the provider can prove that the exact observed dialog closed. No answer content or human identity is inferred.
- An unresolved native notice blocks automated task/key delivery independently of transient provider status. Expiry or cancellation ends the response wait but does not prove physical dialog closure. Only authorized manual WebSocket input may advance the source while the physical notice remains open; explicit required questions still block that input.
- An unchanged exact viewport may retain the original native observation across a contiguous chain of manual WebSocket advances. A newer observation invalidates the old wait. A fresh appearance after exact closure gets a new occurrence identity even in the same generation; duplicate observation retries remain idempotent.
- A native wait binds to its first exact observation, not merely the source generation. The executor retains that observation across bounded polling calls. Cancellation, expiry or replacement of that observation cannot be satisfied by closing another question in the same generation.
- The monitor persists a digest of the original terminal/incarnation/session/window binding. Recovery validates that digest against the current source and requires two stable changed viewport samples before closing the notice. Missing historical binding fails closed. Recovered closure clears only the physical input gate: it never releases or replays the old task, creates an answer, or creates an answer-returned receipt.

## Safe event envelope

Events carry sequence, opaque group/question IDs, source identity safe for the current audience, event type, lifecycle/delivery state, actor category, timestamp and safe reason code. They omit prompt text, option labels where those disclose content, answer payloads, credentials and terminal transcript. Consumers treat events as hints and refresh a current metadata snapshot. Detail is fetched through the protected API.

Required event types: interaction.created, interaction.routed, interaction.escalated, interaction.answer_accepted, interaction.answer_returned, interaction.answer_acknowledged, interaction.delivery_claimed, interaction.delivery_confirmed, interaction.delivery_uncertain, interaction.expired, interaction.cancelled, interaction.stale, interaction.reconciled, interaction.wait_changed, interaction.native_manual_notice.

Internal native recovery evidence uses `interaction.native_observed` (opaque target/dialog binding, original observation and generation) and `interaction.native_closed` (physical closure without lifecycle revival or task resume). Neither event contains raw target names, screen text or an answer. Consumers still refresh the authorized snapshot rather than interpreting these events as a returned answer.

## Required delivery deadline and revocation

Acceptance alone keeps a required group blocked. A same-source pull that commits before expiry marks the answer returned and releases its wait atomically; acknowledgement is optional. If expiry commits first, the group and wait remain terminal expired and runtime requires reconciliation. An already accepted current-version answer remains recoverable by that same authorized source after expiry, without reopening or redispatching the task. Unanswered required members prevent an expired group from producing a ready payload.

An owner cannot revoke a reviewed delegation after its exact-version answer is accepted: the operation conflicts, preserving the immutable accepted answer and its delivery. Source/profile/incarnation/Work authority is still revalidated before any pull.

## Proyección opcional AG-UI (continuación T064)

El stream opcional existente conserva su autorización de transporte (header, sesión o ticket de un solo uso), validada antes de leer y también en idle. No se fabrica un Principal humano a partir de scopes. Un lector explícito de metadatos del ledger, sin autoridad de respuesta, produce una página acotada de estado y recibos actuales en la misma transacción de lectura.

- `STATE_SNAPSHOT` inicial incluye `interactions` dentro del snapshot de flota existente. Los cambios usan `STATE_DELTA` estándar; no hay una segunda instantánea que sustituya accidentalmente la flota ni tipos de evento incompatibles con clientes SDK.
- La página informa de su límite, cursor/continuación y disponibilidad. `can_answer` y las capacidades de acción permanecen deshabilitadas en esta vista. El contenido protegido se obtiene exclusivamente por el API de interacciones con su autoridad humana vigente.
- Los recibos identifican exactamente pregunta, versión y respuesta aceptada, y su estado durable. accepted no demuestra returned_to_mcp; returned_to_mcp no demuestra acknowledged; uncertain permanece distinto. Ningún cierre nativo implica respuesta o recibo.
- Se omiten prompt, opciones, respuesta, transcripción, credenciales, actor privado y binding del target. El mapper aplica una lista cerrada de campos incluso a datos inesperados.
- Cambios del ledger se observan también durante los heartbeats cuando no hay eventos de flota. La reconexión hidrata una instantánea actual. La secuencia SQLite nunca se usa como Last-Event-ID del ring de eventos de flota.
- Un fallo de lectura se presenta como indisponibilidad explícita, sin inventar una bandeja vacía ni estados terminales. La flota y la bandeja principal siguen disponibles. AG-UI deshabilitado sigue devolviendo 404 y no crea un lector activo.
