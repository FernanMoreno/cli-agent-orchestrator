# Native diagnostic fixtures

These readers and coordinators are not imported by CAOS product code. Reviewed private launchers must bind their exact SHA before use on the current run-owned major1 database and durable receipt. They export fixed status codes and sanitized booleans/counts; they never supply acceptance text. FR071–FR078 and validations T089–T099 distinguish historical native runs from synthetic checks.

Run all synthetic controls from the repository root, without native services or operator data:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. python -m unittest discover -s specs/025-provider-mcp-profiles/fixtures -p 'test_native*.py'
```

The tests create temporary synthetic SQLite tables and owned roots. An actual diagnostic additionally requires reviewed source/binary/ownership/generation bindings, verified PostgreSQL identity and the unchanged memory/deadline/acceptance gates. No default operator-database location is opened.

`native_phase_observer.NativePhaseObserver` addresses the T096 blind spot: call `tick()` independently of sender acceptance from the existing phase loop. The first state poll prioritizes receiver; subsequent polls rotate so a slow pending callback cannot starve the other role. Each tick polls only one pending role and may perform one output/viewport/probe attempt, and each role is attempted once after its exact expected turn completes. Pending-state polls occur no faster than4Hz globally (normally2Hz per role while both wait); no sleep, thread, model, message dispatch or state writer is created.

Construct `RoleScope(terminal_id, expected_turn, expected_agent)` for each fixed `sender`/`receiver` key. Keep terminal IDs internal. The sender sequence comes from the accepted input response; the receiver sequence is its pre-input baseline plus one. Pass the original pre-input monotonic `phase_started`, even if input delivery already consumed time. The deadline is always `phase_started +75`, shared across roles and ticks. That role gets a fresh maximum3s total tick budget across its state/read/capture/probe work; a pending peer cannot consume it. The original phase deadline always caps the budget.

Provide three read-only callbacks, taking `(terminal_id, absolute_stop)`, plus the required `max_bytes` keyword for both frame readers:

- `read_state` returns `http_status`, `status`, `turn_sequence`, `turn_completed`. Completed observations require HTTP200, status `completed` and both counters exactly equal to the scope. Stale states wait; malformed or later counters are unavailable.
- `read_output` returns public API LAST as text, without normalization.
- `capture_viewport` returns the owned viewport as text. Frame readers receive `max_bytes=65537` (CAP+1) and must enforce that limit while reading, before materializing an unbounded response. Both returned text frames have the existing64KiB UTF-8 cap; oversized frames are rejected, never truncated.

`clock` must use the same monotonic time domain as `time.monotonic` and `phase_started`; injection is for tests with a correspondingly controlled probe. Backward/nonfinite clock values fail before more I/O. The synthetic SQLite composition uses the real shared monotonic clock.

Use one non-reentrant caller per observer. Readers must honor the passed absolute deadline in their own network/process calls and verify the existing owned API/tmux process/socket identities. Synchronous Python cannot preempt a callback that ignores its deadline. The coordinator rejects late returns and starts no further I/O after its tick/phase budget. The native probe itself still verifies the owned major1 root, current PostgreSQL receipt digest, parent/session, native agent and size/time bounds.

`snapshot()` returns copies of the two standard diagnostic keys and fixed role states. `finish()` performs no I/O: it closes completed observations, or marks pending roles `not_triggered_by_deadline` once the original phase expires. It refuses early finalization while roles remain pending. No missing observation is classified as a wrong native agent. Closing releases private scopes, targets and callback references; only sanitized snapshots remain. Probe output is projected through fixed vocabulary and bounded typed shapes; unknown fields are dropped and malformed results fail closed.

A launcher must defer its sender-failure exit while it continues the existing read-only diagnostic loop; this component cannot change an external caller's control flow. Merge the returned diagnostics into that phase only. Preserve every existing ACK, Unicode, receipt, sequence, restart, catalog and cleanup check, and use `native_profiles_exact(phases)` solely as an additional conjunction. Profile identity evidence alone does not establish response conformity.

There is no checked-in live caller for this new coordinator yet. The T094/T096 private runners/receipts were unavailable on resumption and have not been reconstructed or rerun. Portable synthetic composition is not current native acceptance, and it does not prove that a receiver response existed during historical T096.
