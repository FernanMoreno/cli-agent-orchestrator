# T187 — HTTP TEXT observation preparation

Status: **implemented and checked offline; cancellation remains inactive**.
The native-store result stays NOT_OBSERVED. CAOS state and authoritative turn
receipts remain in PostgreSQL; the HTTP files are bounded temporary diagnostic
evidence, never an alternative runtime database.

The new parser processes each complete received chunk before publishing activity.
It reuses duplicate-key JSON validation, rejects invalid UTF8, stream identity or
choice changes, and bounds wire/event/TEXT/event count at1MiB/64KiB/64KiB/4096.
Role, reasoning and tool-only deltas do not supply TEXT. A finish/error in the
same chunk, or a trailing incomplete frame, prevents an active observation.

The monitor clones the response without replacing it and reads independently of
filesystem publication. Tests verify exact original bytes, original progress
while publication is blocked, and bounded clone-only abort/lock release. Native
Bun semantics still need the model-free installed-binary control; Node behavior
does not establish them.75s/6144MiB remain unchanged.

The hooks correlate the exact Request object, primary kind/provider/model, phase2
task and receipt-token SHA. They allow one eligible request, coalesce metadata,
and use at most128 sample attempts/72KiB.750ms regular sampling covers75s with
terminal/cleanup slots reserved; terminal evidence bypasses a pending timer.
Samples are exclusive0600 files; superseded publications can leave ordinal gaps.
The seal distinguishes durable `sample_count` from `sample_attempts`. A missing,
invalid or closed trace cannot provide live authority. The
[JSON Schema](contracts/http-text-stream-observation.schema.json) defines the
producer records; the consumer additionally validates private ownership,
identity, matching nonce/receipt, monotonic counters and one request.

The pre-initialize loader is prepared before assignment so it can be inherited by
the Git worktree. Only the specialized core is under private `.opencode/plugins`;
three exact support modules sit outside autoload. The checked0644 project loader
imports the0600 core by absolute file URL. Runtime modules and expectations stay
under0700 directories. No tool or MCP permission is added.

The real BaseProvider formatter includes the terminal ID in its receipt suffix,
although that ID is unknown before assignment. The expectation therefore derives
the production suffix with exactly one `{{CAO_TERMINAL_ID}}` placeholder. The
request inspector validates and substitutes only that suffix using the native
process's checked terminal ID; its default schema/behavior remains unchanged.
The installer rejects missing/ambiguous placeholders and phase prompts that
would differ from the formatter's `message.rstrip()` before writing anything.

Root final verification:98 Node tests pass in2.278s;32 Python tests pass in12.89s;
architecture/composition5 contracts kept/0 broken. Python includes the actual
installed loader → Node hooks → private writer → strict Python live reader,
using exact text from the production formatter and validating produced JSON
against the schema. A fresh second snapshot grows; finish/cleanup removes live
eligibility and the original response remains byte-identical. Other tests cover
PG lookup through a mock only: correct API metadata, same sent receipt bracketing
the ledger, stale/invalid refusal and frozen snapshot/growth identity. **No real
PostgreSQL or native cancellation acceptance follows from those mock controls.**

Draft defects reproduced before activation include missing clone cancellation
on an invalid read, heartbeat exhaustion, seal attempt/file mismatch and delayed
terminal publication. Root corrected them and preserved the failed logs. The
installed Node control initially left stdin open while waiting for child exit;
closing its own stdin restored exit within the unchanged limit. This was a
control-lifecycle failure, not a product cancellation failure.

Claude reviewed the design, corrective core delta and pre-initialize installer;
Luna Max agents supplied adversarial tests, a clone-reader draft, independent
ledger review, installer draft, and PG-binding draft/review. Root inspected and
verified accepted changes. Read-only review is distinct from executable proof.

Remaining gates: actual Bun/loader/clone observation, real PostgreSQL binding,
installed paired-controller multi-poll checks, fresh API/process/pidfd fence and
source-bound real streaming cancellation with unchanged cleanup/replay gates.
The PG candidate validates supplied metadata once; its caller must fetch fresh
API state for each observation and immediately before cancellation. One positive
file is not fresh activity or permanent uniqueness proof. This implementation
does not attest upstream network liveness, served-model identity, all providers,
or completion of T168/T187.
