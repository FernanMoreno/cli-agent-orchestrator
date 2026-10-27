# T170 — agent CLI machine output

Result: **implemented and verified**. [Machine receipt](validation-t170.json). `cao agent` now uses its existing machine
policy for every final operation result: JSON when stdout is not a TTY or
`--json` is present; existing human rendering on a TTY. The operation-specific
payloads, JSON formatting and success-derived exit codes are unchanged.

The first incorrect boundary was `_emit`: `_machine_mode` already promised JSON
for pipes, and handoff used it to suppress progress, but final output consulted
only the explicit flag. A controlled status invocation reproduced exit0 with
human lines. Before the fix, the full nine-command matrix produced18 failures
for default pipe output and54 passing TTY/explicit-JSON controls. `_emit` now
reuses `_machine_mode` rather than creating another policy.

Final verification: **107 PASS in12.28s**. This includes72 cases across all nine
commands × success/failure × pipe/TTY × default/explicit JSON, four interruption
controls, and31 existing wiring/option/rendering cases. Machine stdout contains
one exact JSON value; handoff terminal notifications remain on stderr, response
contents are absent there, and machine mode starts no heartbeat thread. Human
handoff threads stop and join. Interrupted handoff retains exit130 and its stderr
warning that server work may still exist; it does not invent a completed result.

The existing human-output block test now explicitly selects its human policy,
retaining every output assertion. The real TTY-reporting Click stdout matrix
independently tests detection/rendering. Both CLI test modules explicitly opt
out of autouse PostgreSQL: all orchestration implementations are mocked, or the
test requests help/invalid arguments. No test was removed, skipped or weakened;
these tests never exercise database/provider behavior.

Composition review: agent callbacks → shared renderer/machine policy → Click
stdout/stderr/exit and handoff heartbeat lifetime. Architecture passes5 contracts/
0 broken. Claude independently reviewed the source and matrix without blocking
findings. No in-repository consumer was found parsing the old human pipe output.
The final diff changes only the renderer's policy decision and its description,
plus the scoped tests/docs. No dependency, migration, authorization, MCP/HTTP/
AG-UI envelope or provider/model prompt changes are introduced. Verdict: PASS
for this common operator CLI boundary; provider acceptance gates stay separate.
