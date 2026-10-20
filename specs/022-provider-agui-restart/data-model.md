# Data Model: Real-Provider AG-UI Restart Acceptance

This acceptance introduces no persisted product entity or schema. It verifies existing interaction records across two API process lifetimes.

## Acceptance Run

One opt-in invocation with attributes:

- provider identity: `codex` or `claude_code`;
- private run root and private database configuration;
- provider process identity and owned API worker process identities;
- API listener port shared by MCP configuration across restart;
- result categories, bounded phase timings, cleanup outcome, and sanitized evidence.

Run-private filesystem data is temporary and removed by existing ownership-scoped cleanup after all processes stop. Raw provider output stays in private logs and is not copied into the public/sanitized receipt.

## Source Registration

The durable interaction source consists of existing PostgreSQL terminal/source records:

- terminal ID and session name;
- exact session incarnation ID;
- provider and execution mode;
- hashed/capability-backed source authorization maintained by the existing interaction service.

The orchestrator creates this state before API worker launch. Both worker processes load the same isolated target config. Tests compare identities across the process boundary but do not place the raw capability in evidence.

## Interaction Group and Questions

The existing group comprises exactly two required typed questions for the current live journey. The stored and projected identity fields include group ID, question IDs, question versions, source terminal/incarnation, lifecycle, and nullable delivery state. The restart must preserve those values and group count.

No new question kinds, lifecycle values, recipient authority, expiration, or answer semantics are introduced.

## AG-UI Question Projection

The production projection is validated against closed typed shapes. The interaction snapshot allows only `available`, `items`, `next_cursor`, `truncated`, and `event_sequence`. Each item allows the production metadata fields plus the derived `can_answer` and `actions`; nested source, task, recipient, group-progress, and answer-receipt objects each use their exact production field sets. Unknown fields fail recursively, including unknown fields added in future nested objects.

For the acceptance, relevant observable values are:

- `available` and `truncated`;
- question/group IDs and question version;
- lifecycle and nullable delivery state;
- source terminal/incarnation and provider metadata;
- origin, required flag, and recipient metadata;
- answer receipt metadata after response, excluding the opaque receipt token.

The projection does not include prompt, options, answer response, private source capability, delegation authority, or the receipt token. Unit tests use the production field names and typed shape; the harness does not define an alternate projection schema.

## Answer and Receipt

The human actor submits the already configured text and choice answers through the existing version-checked route. The provider/MCP process receives the typed answers and receipt, then acknowledges those exact question IDs, versions, and receipt values. The delivery state transitions are read from the existing ledger and projected by the production stream; the harness does not advance them directly.
