# T071 · receiver evidence and restart failure

## Actual R11 outcome · 2026-10-22

R11 ran once on main `2dbfadc3fc682fa6b608d843a0448cc62dcdae2c`, with507 exact source pins, the same pinned v2 binary and explicit `opencode-go/glm-5.3-flash`. No model fallback, recovery action, permission expansion or acceptance/deadline change was introduced. [Sanitized receipt](validation-t071-native-v2-r11.json).

The first message phase passed: sender12.380s, receiver7.679s, both below75s; same-turn Unicode LAST, receiver completion once, one exact PostgreSQL/API message and one redacted AG-UI notification were verified. At failure capture, the receiver's read-only turn projection was `verified`, reason `none`, attempts0; its durable receipt was `result_verified` with the same generation. **The R10 receiver reconciliation did not recur.** This run does not explain or supersede that failure.

API restart changed the API process while preserving the PostgreSQL target. Message identity and terminal metadata checks after restart passed before the runner raised `terminal_lifecycle_not_durable`. The second message phase was not reached. The failing lifecycle helper does not retain the offending role, status or counters before raising; the captured receiver turn alone cannot identify that boundary's actual values.

## Instrumentation and offline review

Failure-only diagnostics captured bounded owned receiver log/history/viewport and a read-only current-turn/receipt projection before cleanup, in57ms. Native text, identifiers, receipt nonce/hash and paths remain private. Log opens reject links and use nonblocking regular-file checks; failures in diagnostics leave original failure handling and cleanup reachable. The public receipt contains only finite projections and evidence digests.

Preparation passed202 inherited pure controls,35 R9e controls,10 R10 controls and13 R11 controls. Root separately passed45 controls for diagnostic privacy, bounds, ownership, symlink/FIFO rejection, timeouts and cleanup reachability. Claude's source read review approved the bounded diagnostic; root corrected its nonblocking-open and partial-evidence findings and reran controls. Both execution gates were restored closed after the actual run; closed runner SHA256 `2656669f606c02e67d2a092bb0703deccd6d90c150b08bb15493c82b803c42b0`.

Root replayed both actual receiver captures through the current provider with the captured generation/hash and native status access disabled. Both retained a user boundary, extracted a response and verified its receipt. Independent Luna read review confirmed that the delivery contract is delimited and absent from the extracted response: the valid receipt was not copied from the user contract. The1812-character result contains an ACK line and extra text; exact ACK is a diagnostic, not this journey's acceptance condition. No parser correction is justified by R11.

Source review found a separate harness assumption requiring investigation: `StatusMonitor` owns `_turn` and `_turn_done` in memory, and `turn_state()` defaults to0/0 for an unknown local terminal. The private runner compares these counters across API processes as durable state. Observation reattachment seeds detection asynchronously, while the lifecycle helper immediately requires idle/completed. These are falsifiable source-level concerns, **not a demonstrated explanation of the actual R11 error**, because its offending snapshot was not saved. Preserve readiness and completed-turn requirements; capture finite lifecycle evidence and distinguish process-local counters from durable receipt identity before changing the gate. Do not simply remove a failing assertion.

## Cleanup and remaining scope

All admissions exceeded6144MiB (minimum7119MiB). DELETE200, immediate native roots0/capability files0, owned API/tmux/PostgreSQL cleanup and empty cleanup failures were recorded. Private runtime state was removed; operator authentication, source, binary, runner and interpreter were unchanged during execution. Private evidence files remain intentionally retained.

At the R11 checkpoint T071 remained open for the original R10 cause or an explicit final evidence limit, and T068 awaited a complete native-v2 restart/message journey. No product behavior changed. Further final Claude read review was attempted but the bridge returned `Transport closed`; this is not a successful review or a provider-authentication diagnosis.

Composition assessment: **PASS WITH RISKS for bounded diagnostics; native journey FAIL at restart lifecycle gate**. Reviewed neighbors are tmux/log capture, turn projection, receipt persistence, status observation, API restart and owned cleanup. The actual PostgreSQL dependency was reused with its pinned identity. Diagnostic controls cover partial failures and privacy; public API/schema behavior was unchanged, so no new consumer contract was introduced. Restart semantics remain unresolved and no global acceptance is claimed.

Final registered MAIN composition check passed:412 files/1834 dependencies,5 contracts kept/0 broken. Root reran all runner self-check groups and45 pure diagnostic controls after the actual execution, with no model/service call; all passed. Production source/test files remained unchanged.

Final bounded diagnostic closure after [R12 evidence](validation-t072.md) and [R13 acceptance](validation-t073.md): T071 is complete with an explicit evidence limit. R10 did not capture the receiver data needed to establish its cause; subsequent runs did not reproduce it. R12 exposed a separate private restart-gate defect, and R13 passed after correcting that validation boundary. This does not explain R10 or close any historical reconciliation job. A future recurrence requires fresh receiver-side evidence. T068's separate native-v2 acceptance is now recorded by R13.
