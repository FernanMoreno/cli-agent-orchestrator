# T072 · exact restart failure captured

## Actual R12 · 2026-10-22

One instrumented R12 ran on main `c93b1be7b8ef95926a16b4626b9065d0a7ea9c63`,507 exact source pins, the same native-v2 binary and explicit GLM5.3Flash. [Sanitized receipt](validation-t072-native-v2-r12.json). The instrumentation retained the original gate and GET count;510 pure diagnostic assertions, inherited202/35/10/13 controls and independent Luna read review passed before execution. Claude bridge was unavailable (`Transport closed`); no Claude review is credited.

The first phase passed: sender13.721s, receiver45.668s, same-turn Unicode LAST, receiver completion once, one exact PostgreSQL/API message and one redacted AG-UI notification. R10 reconciliation was not reproduced.

The exact existing lifecycle GETs now identify the rejected fields:

| Observation | Receiver | Sender | Durable turn projection |
| --- | --- | --- | --- |
| Before first message | idle,0/0 | idle,0/0 | none, attempts0 |
| Before API restart | completed,1/1 | completed,1/1 | both verified, attempts0 |
| After API restart | completed,0/0 | **unknown,0/0** | both verified, attempts0 |

`terminal_lifecycle_not_durable` was raised for the sender's immediate `unknown` status. Both counters were valid integers with completed equal to sequence. Even if sender readiness subsequently settled, the separate strict comparison against before-restart1/1 would reject the reset counters. The run did not wait for later sender readiness and does not establish whether or when it became ready. R11's exact rejected fields remain uncaptured; do not retroactively assign it R12's values.

The public Terminal model describes these counters as live causal dispatch/completion and separates them from durable receipt verification. `StatusMonitor` initializes them in memory; local `turn_state()` defaults to0/0 in a new process. Observation reconstruction is asynchronous. The maintained `message_api_restart_live._wait_ready` requires three ready samples; the private runner instead sampled immediately and treated live counters as durable identity. This is a reproduced private validation boundary, not a demonstrated loss of persisted messages or a reason to redesign production persistence.

Nested `turn.http_status=200` refers to the successful terminal GET whose embedded turn was projected; it is not an additional `/turn` request. Invalid-counter projections do not distinguish absent fields from wrong types/ranges. No transcript, terminal ID or receipt value is public.

## Cleanup and next correction

Elapsed111.448s; all model phases stayed below75s and all memory admissions exceeded6144MiB (minimum7272MiB). DELETE200, immediate roots0/capability files0, empty cleanup failures, owned API/tmux/PostgreSQL cleanup and private runtime-state removal passed. Operator auth, source, binary, runner and interpreter were unchanged. Both execution gates returned to closed; the failed receipt is preserved.

T072 is complete as a diagnostic. T073 corrects restart validation: bounded three-sample readiness, exact verified durable receipt identity across processes, and unchanged local counter/per-message completion checks. At this checkpoint T068 stayed open until a complete native-v2 journey passed; [R13 subsequently passed](validation-t073.md). R10/R11/R12 remain separate retained failures.
