# T073 · native v2 restart/message acceptance

## Correction and acceptance boundary

R12 captured the private harness rejecting an immediate sender `unknown` after API restart, while both live counter pairs reset from1/1 to0/0 and durable turns remained verified. [Retained R12 failure](validation-t072.md). The public Terminal model defines those counters as live state, separate from durable receipt verification; production observation reattachment is asynchronous. The maintained reference journey waits for three ready observations.

R13 corrects the private harness's state-owner assumption. It requires three consecutive settled ready readings per terminal within **one shared75s restart-readiness deadline**, with bounded HTTP arrival and JSON parsing checks. Local counters must be valid and completed equal to dispatched; the later message must still advance each terminal exactly once relative to its new process's baseline. Across processes it now compares the exact generation, receipt hash, result hash and verified phase of both owned terminals, read privately from PostgreSQL. A new required acceptance flag records this durable identity check. Receipt values never enter the public evidence.

The legacy `terminal_lifecycle_stable_on_api_restart` name now means settled local counters plus verified durable receipt stability, not equality of process-local numbers across restart. Both snapshots remain visible. Native process/root/metadata identities, persisted first message, tool catalogs/calls, two message phases, same-turn Unicode LAST,75s model phases,6144MiB floor, graceful observer seals and complete owned cleanup remain required. No product behavior, database schema, permission, recovery action or model selection changed.

## Offline controls and review

The actual R12 finite values reproduce the old immediate rejection and cross-process counter mismatch. Fake-clock/read-only-SQL controls exercise transient unknown/processing, interrupted ready streaks, changed local counters, invalid/unverified/changed receipt identities, in-flight turns, malformed responses, terminal errors/reconciliation, shared-deadline exhaustion and late HTTP/JSON arrival. They passed1301 assertions (including repeated assertions within timeout loops; **not1301 independent tests**). Normalized AST comparison retains the remainder of `_run`, local lifecycle helper, acceptance function, prompt, observer and cleanup logic; the required-true field set adds exactly the durable receipt stability flag.

Inherited self-check groups passed202/35/10/13 controls, with catalog5/range8 included in the202 group. Independent Luna source read review approved one actual R13, explicitly reviewing the corrected ownership contract and preserved local increments. The reviewer ran no commands. Claude bridge remained unavailable; no Claude review is credited.

## Actual R13 · 2026-10-22

Ran once on main `c93b1be7b8ef95926a16b4626b9065d0a7ea9c63`,507 exact pins, runtime fingerprint `13c78892cb7e98c9c701126679f90ef13bb83b0cffbad281ed492677b4b868b6`, pinned v2 binary and explicit `opencode-go/glm-5.3-flash`. [Sanitized successful receipt](validation-t073-native-v2-r13.json).

| Phase | Sender | Receiver | Verified outcomes |
| --- | --- | --- | --- |
| Before API restart |16.900s |15.048s | current-turn Unicode LAST, receiver completion once, one exact PG/API message, one redacted AG-UI notification |
| After API restart |14.542s |6.055s | same requirements, second distinct message, new local turn completed once |

Both terminals reached three settled ready readings after restart with0/0 local counters and verified turns. Exact durable receipt identities were unchanged. The API process changed while native processes, provider roots, terminal metadata, PostgreSQL target and the first message's PG/API identity remained stable. Both phases passed. The sealed sender observed one completed `list_siblings` and two completed `send_message` calls; receiver catalog/calls were empty, with zero unexpected tools/calls.

Elapsed107.281s. All model phases stayed below75s; minimum memory admission7253MiB exceeded6144MiB. Graceful observer seal took0.100s, below its unchanged10s limit. DELETE200, immediate roots0/capability files0, empty cleanup failures and owned API/tmux/PostgreSQL cleanup passed. Private runtime state was removed; operator auth, source, binary, runner and interpreter were unchanged. The existing PostgreSQL container remained running with the exact pinned identity and restart count0. Both private execution gates returned to closed.

This closes T073 and the fresh native-v2 message/restart portion of T068 on the recorded source. It does not identify R10's receiver reconciliation cause, retroactively pass R11/R12, prove every profile/auth/model, or establish global scale/control-latency acceptance. Work messaging remains its separate contract and evidence.

## Composition assessment

**PASS for the bounded private harness correction and actual generic-message restart journey.** Reviewed boundaries: status observation versus durable receipt ownership, bounded readiness reads, read-only PG receipt snapshots, local new-turn fencing, persisted messages, AG-UI projections, native process/root identity and cleanup after partial failure. No new deployed API/event/schema or consumer contract was introduced. Real PostgreSQL/API/native terminals were used serially; fake clocks/SQL adapters cover rejection boundaries without model traffic. Prior failed receipts remain retained. Repository specs own the durable result; no vault was accessed.

Final verification: root reran1301 R13 assertions,510 R12 diagnostic assertions and inherited202/35/10/13 self-check groups after the actual run; all passed. Registered MAIN composition passed412 files/1834 dependencies,5 contracts kept/0 broken. Independent Luna final read review found the public receipt/write-up consistent and approved T073 and T068's native journey closure. Earlier T068 HTTP correction/regression/review evidence remains in its own validation record. Final source/diff review found no production source/test changes in this increment. The shared generated Graphify artifacts were preserved; a repository structural refresh was not needed for private harness functions and documentation.
