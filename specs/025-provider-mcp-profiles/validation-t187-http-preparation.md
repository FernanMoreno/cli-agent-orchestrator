# T187 — HTTP TEXT observation preparation

Status: **offline contracts, synthetic native HTTP and real PG binding pass;
cancellation remains inactive**.
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

Native Bun/loader/clone capability now passes in the bounded synthetic control
below. Real PostgreSQL binding also passes in the bounded diagnostic below.
Remaining gates: installed paired-controller multi-poll checks,
fresh API/process/pidfd fence and
source-bound real streaming cancellation with unchanged cleanup/replay gates.
The PG candidate validates supplied metadata once; its caller must fetch fresh
API state for each observation and immediately before cancellation. One positive
file is not fresh activity or permanent uniqueness proof. This implementation
does not attest upstream network liveness, served-model identity, all providers,
or completion of T168/T187.

## Pinned native HTTP control, 2026-10-27

Source `44eb8660`, native OpenCode2.0.18: R6 exits0 in18.053s with
10772MiB available at admission. Two distinct positive sequences for one request
and ledger directory grow from26 to81TEXT bytes while the synthetic response
is held before finish. After finish the strict projection returns
`stream_finished`. Native and outer process groups retire; source and private
inputs remain unchanged. The original75s budget and6144MiB floor are retained.
No real model, operator login, PostgreSQL or Docker is used by this control.

R4 exits0 but produces no samples. R5 isolates the first failing boundary:
autoload and hooks work, but exact request classification rejects the receipt
envelope. Both captured synthetic request bodies contain one literal double
quote before and after the production-formatted input. Offline replay reproduces
both refusals, then accepts both as exact phase2 with a receipt digest when only
the private control expectations include those observed quotes. R6 uses that
expectation correction with the uninstrumented observer. Public parsing and
production input are unchanged; the CLI framing assumption must not be applied
to the paired TUI journey without observing that journey.

[Bounded receipts and replay](validation-t187-native-http.json) preserve all
three outcomes; [exact executed R6 bytes](fixtures/t187/control/native_http_stream_control.py.txt)
are archived as evidence, not as a current launcher. This proves native clone
TEXT visibility before parsed finish. It does not prove upstream liveness,
PostgreSQL authority, actual API freshness or real cancellation. T168/T187 remain
open and the candidate remains disconnected from cancellation.

Claude's scoped read-only review found no blocker in the private expectation
delta; root verified both replay outcomes and the native capability predicate.
Composition passes5 contracts/0 broken. Four durable Obsidian pages record the
boundary and prevention rule; strict vault lint reports no findings. Foreign
generated graph files remain untouched.

## Actual PostgreSQL binding, 2026-10-27

Source44eb8660, the existing pinned fixture and real core migrations: a prepared
receipt is refused, a sent receipt plus a real private ledger is accepted, and a
generation changed after the actual filesystem read is refused by the second PG
lookup. A separate SQL connection confirms the replacement generation was
actually saved. All five capture connections report `transaction_read_only=on`;
this proves their session setting, not that the application role cannot write.
API metadata is a synthetic GET-shaped projection; this diagnostic calls neither
the API nor a provider/model. It establishes PG/reader integration, not final
API freshness or native cancellation. No SQLite is used.

No existing owned CAOS service was active. The verified local image is reused
without pulling; a new fixture owner holds its exact container/network until
all database/reader borrowers exit. R3 admissions10972/10661MiB exceed6144MiB;
identity labels, image and exact resource IDs are verified. Both owned resources
are confirmed absent after normal context cleanup; source pins are unchanged.
[Receipt](validation-t187-real-postgres.json) and
[executed control](fixtures/t187/control/http_postgres_control.py.txt) preserve
the bounded proof. The archived control is closed by default and tied to its old
source HEAD; it must be repinned/reviewed before reuse.

R1 reported success but lacked independent proof of the generation mutation:
an UPDATE error swallowed by the candidate could mimic a refused race. Claude
found that gap; root reproduced the old false positive, added three negative
controls and a positive control, then required a successful mutation flag and
an independent SQL read. R2 retains a failed cleanup-verifier receipt because
Docker29's absence error wording differs from the expected strings. Exact
resource inventories subsequently confirm both R2 resources absent. R3 uses
successful exact-ID inventories instead of parsing stderr; four local controls
and the actual two retired IDs pass before the fresh successful PG execution.
Earlier receipts and executed bytes stay preserved; no uncertain result becomes
accepted by changing its original receipt.
