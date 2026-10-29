# T211 — FIFO ownership through cancellation

Status: component correction PASS; source-bound native HTTP-disconnect control
remains pending. CX-09/T013 and global acceptance remain open. T179/T181 already
cover managed partial quota; that scenario was not repeated.

## Reproduced boundary

A real named pipe and its real reader demonstrate OLD bytes published after
`reset_buffer` and consumed under the new buffer state. The old cancellation
path reused both FIFO inode and reader/UTF-8 decoder; an event queued before
reset carried only a terminal ID. Real PostgreSQL composition also demonstrated
that a failed reader retirement did not prevent cancellation from completing.
Five other FIFO controls passed in that RED selection; no native CLI, model or
operator login was needed to reproduce these defects.

The correction reserves one replacement identity per owned reader attempt,
installs the monitor fence and advances its epoch before draining, and requires
positive reader retirement before replacement/initialization/finish. Original
pane reset and native-child transition precede fresh capture creation. A late
watchdog can therefore reattach only to the post-reset pane; its earlier replay
cannot borrow the new identity. No inverted session-under-dispatch lock was added.

Reader replacement uses a fresh FIFO/decoder and preserves watchdog callbacks
across retryable failures. Every retry rotates its nonce, including a previous
attach that may have written before failing. Pending metadata is bounded by
currently owned/pending readers and is removed by ordinary cleanup. Checks are
constant-time; no global queue-drain barrier or per-turn history was added.
Initial readers retain the legacy data payload. Fenced replacements add a JSON
string identity, validated before provider evidence/buffer mutations and checked
again under the buffer lock. Tagged retired events cannot resurrect a cleared
monitor. Raw logs/history intentionally remain history and are not claimed fenced.

Observation reconstruction creates a tagged reader, installs its actual nonce
before attachment, and tags its direct seed. It preserves receipt generation
and restores only the proven target. Provider notification accompanies buffer
epoch reset for both FIFO and event-inbox cancellation paths.

## Verification and preserved failures

- Actual FIFO reproduction confirms post-reset OLD publication; original receipts
  remain private and unchanged.
- First PostgreSQL selection:1FAIL/5PASS,49.81s; stopFalse released cancellation.
- New reader contract controls initially fail3cases on the old source,44.12s.
- First patch:20PASS/1FAIL,72.64s. The replay test had reserved a restart before
  entering its callback, so it did not exercise the intended race. It now holds
  the actual callback before replacement and checks the late return.
- Broad selection:275PASS/3FAIL,686s. One new unit referenced a nonexistent
  helper; two existing randomized Claude-loop models exposed a real timer-epoch
  regression. An exact HEAD-class replay passes both seeds in83.78s. The fix
  captures the observation epoch when the timer reads the current screen, rather
  than discarding a fresh screen because its scheduling chunk was older.
- Peer review reproduced missing restore identity and wrong reset/capture order;
  both now have targeted controls. Unit harness DB-configuration failures are
  retained separately; the pure control now stubs only its configuration boundary.
- Corrected focused composition:45PASS,94.37s, real PostgreSQL/FIFO/tmux,
  all registered provider reconstruction shapes, no native provider inference.
- Full combined regression:301PASS,2 existing deprecation warnings,746.58s.
  A subsequent minor provider-notification consistency change and import sorting
  are covered by a fresh55PASS selection,2warnings,103.58s. No model assertions,
  seeds, iterations, coverage thresholds or configured opt-in gates were reduced.
- Core three-module mypy passes. The broader four-module check retains exactly
  HEAD's17terminal_service errors; it is not a global typing PASS.
- Black/isort, scoped diff and composition5kept/0broken pass; independent Claude
  final read review gives GO after its two earlier blockers were corrected.

## Composition and next acceptance

Changed boundaries are reader lifetime, local output event metadata, monitor
epoch/evidence, cancellation dispatch ownership and reconstructed capture. The
existing PostgreSQL generation CAS, Work admission, shielded cancellation job,
publication after lock release and explicit cleanup ownership are preserved.
Real named pipes/PostgreSQL/tmux cover transport and transaction composition;
CLI reset/readiness are controlled in those cases. No business migration, tool
grant, native model-output schema or independently deployed HTTP body changed.
Initial unfenced paths and raw history retain their documented limits.

The private native derivative uses two Codex terminals, actual client socket
closure at an owned FIFO barrier, and generation-bound verification/fresh output
for B while A remains cancelling. It must not claim ASGI observed disconnect,
B task completion, universal FIFO draining, all-profile credential isolation or
full CX-09 solely from that observation. Admission includes a full frozen Codex
package, actual namespace/auth masks, original180s/6144MiB limits, normal owner
and process retirement, and fresh source/runner bindings before enabling.

Graphify's existing recovery/FIFO/monitor nodes and257incident edges were
consulted and verified against source. The unrelated dirty generated graph was
preserved; no refresh or copying of generated facts into the vault. Public
component projection: [validation-t211-component.json](validation-t211-component.json).
