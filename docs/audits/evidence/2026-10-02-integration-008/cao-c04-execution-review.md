# Independent C04 execution review

Reviewed T024–T029 current source against `/tmp/cao-c04-before`, the execution owner's dirty snapshot documented in `/tmp/cao-integration-execution-implementation.md`; HEAD was not used as the preservation baseline. No product edits, commits, provider launches or child agents.

## Findings

One source quality acceptance gap was sent to root and resolved: `clients/tmux.py` contained two identical static `TmuxClient._pane_mark` definitions at lines 637 and 2507. Python silently shadows the first. This currently preserves behavior, so it is not a functional failure, but violates the explicit no-duplicates acceptance. Root asserted AST equality and removed only the trailing copy; reviewer confirmed exactly one definition remains. Existing pane mark/numeric mark checks are sufficient validation. No implementation-mirroring test added.

No additional material functional blocker found in the execution-owned slice. C05's concurrently evolving workflow/admission sources and root-owned runtime-channel/fleet/KAS changes are excluded.

## Contract checks

- Dirty baseline AST comparison inspected 21 changed Python files: no existing function/class symbol was removed. Duplicate-definition check found only the identical `_pane_mark` duplication above. The v28/v29/v30/v31 and legacy recovery catalog assignment ASTs remain unchanged; v32 is additive.
- Terminal public `turn` remains the durable recovery dictionary. Optional `turn_sequence` and `turn_completed` describe live causal monitoring separately. Transport exceptions after a send attempt retain uncertainty; `abort_turn` is called only when no transport attempt occurred. The dispatch lock still spans ownership proof, receipt claim, backend send and sent-CAS; telemetry/callbacks happen after release. Legacy unknown receipt phases cannot claim a new delivery. Receipt publication/verification gates remain provider-generic and precede causal completion bookkeeping.
- Detection paths pin current turn, observation-start state, cleared-buffer turn, buffer epoch and pane capture generation. Dispatch PROCESSING assumption supplies no work proof. Busy-send and continuation state remain distinct. End-state backstop does not erase active durable receipt authority. Observer retry/deadline behavior remains the reviewed C01 contract.
- Session incarnation pointer and terminal row are persisted together, with idempotency publication in the creation transaction. Legacy failed rows require exact backend membership before incarnation backfill. Current session views exclude historic tombstones; teardown rechecks incarnation and Work authority under shared locks.
- Tombstone cleanup requires positively DELETED/ABSENT exact identity; UNKNOWN/STILL_PRESENT retains row, provider private state and worktree. Herdr uncertainty retains routing and retries rather than treating failed metadata lookup as absence. Work retention guards precede runtime changes. Namespace enforcement and forwarded environment byte/count/hijack checks remain layered at client/server boundaries.
- FIFO readers use no-follow/type-proven descriptors and incremental UTF-8 decoding. Timed-out reader generations cannot be replaced; stop cleanup rechecks generation before removing references/path. Liveness writes after stop are fenced. The existing event bus still permits a final buffered-output flush during teardown; this review does not claim a new end-to-end bus generation protocol.
- Kimi dialect is persisted and restart reconstruction refuses unknown dialect. Code runtime homes use deterministic terminal-specific roots, private MCP/config/trust snapshots and symlink-safe cleanup; explicit tool policy is retained. Semantic extraction rejects private/ambiguous transcript content and raw fallback is disabled. Swarm activation is bounded, opt-in and executable-digest guarded; generation/revision-fenced private spool and batch/main-result proof prevent cropped/stale swarm output from settling a parent. Inbox guard applies before eager delivery and readiness status, including an already-ready latch.
- Pane mode stays optional; layouts/captions and window fallback retain stable pane identity, scoped marks, duplicate refusal and exact rollback. Numeric marks are read without libtmux bool/integer conversion. Dying-server retry remains bounded and keeps atomic server-start/options setup.

## Evidence reused and verified

Read actual completion logs rather than repeating broad suites:

- `/tmp/cao-c04-provider-regression-final.txt`: **2152 passed, 6 skipped**, 404.81s.
- `/tmp/cao-c04-final-regression-fixes.txt`: **51 passed**, 11.97s (receipt/Work/API compatibility).
- `/tmp/cao-c04-herdr-retention-final.txt`: **83 passed**, 16.17s.
- `/tmp/cao-c04-limit-lifecycle-final.txt`: **38 passed**, 13.91s.
- `/tmp/cao-c04-composition-final-after-fixes.log`: **5 contracts kept, 0 broken**, PASS.
- Broad execution result remains accurately recorded as 1543 passed, 1 skipped, 6 deselected and 7 stale-fixture failures, followed by a fresh repaired failing-node gate. This report does not relabel that earlier batch as all-green.
- Reviewer executed independent AST preservation/frozen catalog/duplicate checks on current source. No extra behavioral suite was needed because no uncovered functional failure was identified.

## Limits

Herdr snapshot-plus-close has the explicitly documented backend TOCTOU limitation when a pane ID is recycled between proof and close; no atomic backend compare-and-close exists. Current exact cleanup verdict remains conservative when confirmation is unavailable. Ordinary legacy buffered-output teardown semantics are preserved. Provider checks are hermetic histories/mocks plus the owner's isolated tmux smoke, not personal credential/provider acceptance. Dependency/environment changes after recorded runs require final root integration gates. The duplicate was resolved; no outstanding review action remains in this slice.
