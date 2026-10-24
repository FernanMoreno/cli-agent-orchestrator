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

T100 adds a checked-in, default-closed major-1 caller (`run_native_v1_acceptance.py`) and owned transport adapters; fresh execution results are recorded separately in validation-t100.md. The T094/T096 private runners/receipts were unavailable on resumption and have not been reconstructed or rerun. Portable synthetic composition is not current native acceptance, and it does not prove that a receiver response existed during historical T096.

The T100 launcher requires a private reviewed manifest, exact committed HEAD, the complete runtime hash inventory (507 in the original T100 snapshot), and all7 native artifacts, a freshly completed generation receipt bound to binary/runner/plugin hashes, and a verified PostgreSQL metadata file whose ID/port/hash match. `--go` alone cannot open it: the committed runner remains disabled, and a reviewed private copy may differ only in its single execution flag. Optimized Python is rejected; final predicates independently require sender Unicode/counters, exact receiver ACK/counters, complete delivered PG/API row parity, API process replacement, profile/configuration and inherited cleanup.

`OwnedPhaseReaders` uses a nonblocking Content-Length JSON HTTP/1.x client with one absolute deadline, bounded headers/body, identity encoding only, no proxy/netrc credentials and explicit ownership checks. Each output/viewport frame rechecks the same completed terminal/session/pane/turn before and after capture. Only exact loopback API scopes are accepted. The tmux helper is incrementally bounded and reaped even after setup failure. There is no persistent raw model output snapshot: cleanup records bounded counts only. The active protocol receipt is removed by the product’s verified LAST boundary; the caller requires literal public `ACK` while the native probe independently binds the raw standalone receipt.

T102 adds the separate default-closed `run_native_v1_human.py` entry point. It requires a private reviewed manifest, the complete pinned-HEAD runtime hash inventory (507 in the original T102 snapshot), and8 artifacts, a same-source successful fresh generation, exact major1 binary and owned PostgreSQL identity. The restricted profile exposes only ask/wait/ACK: two actual human answers, pre-wait API restart, three exact redacted AG-UI snapshots and a verified durable Unicode final result. Its private worker uses the full production lifespan plus a nonce/deadline-gated status probe; product API routes remain unchanged. `native_human_contract.py` independently gates identity/payload/wait/catalog/final conjunctions. Current real outcomes live in validation-t102.md, with immutable failed attempts retained. Expanded mixed-profile operations and global acceptance remain separate.

The common bounded HTTP reader still requires200. `HTTPStatusError` adds only a numeric status to its fixed `http_status` message. Only T102's final-output loop retries202 pending proof within its original shared deadlines;409 and other errors fail immediately. A pending response is never final-result evidence.


T126 makes both v1 entry points derive their full runtime inventory from `git ls-tree` at the pinned HEAD, with NUL-safe path handling. The manifest must name exactly that nonempty set; every SHA and committed blob is still checked and the artifact sets are unchanged. Receipts report the actual inventory size. Source additions require new pins and fresh reviewed bindings, not a source-code count edit or reduced coverage.
