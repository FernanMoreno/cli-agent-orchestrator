# T214 native R1 · failed render verification

2026-10-29 · **FAILED; no native acceptance.** Source
`8885aae160f5134581eb8fa8b91dc9329d47f89b`, 599 source pins.
Admission: 10,100 MiB; original 180 s task-query limit and 6144 MiB floor.

One actual OpenCode Go `get_local_cao_task` call started, produced its protected
task-result observation and completed. The controller then exhausted its task
deadline waiting for durable result verification. Total run: 296.982 s,
including native startup and normal owned compensation.

The retained automatic verifier trace identifies the first observed rejection:
four successful result extractions, active/native identity checks, native parts
and receipt witnesses, followed by **four native-render comparison rejections**.
The canonicalizer failed and durable CAS never started. The automatic verifier
did execute; manually reading output is not required to schedule it. The first
output-read hypothesis and its artificial controlled test were rejected after
tracing the actual status-monitor scheduler; no such workaround was integrated.

The exact native/rendered pair was not retained before the ordinary private
runtime removal. Its difference is unknown: do not label it Markdown or change
render equivalence from stage counts alone. T215 adds an explicit bounded
private capture and offline replay before another parser/equivalence change.

Cleanup passed: API/tmux identities retired, private runtime removed, owned PG
targets closed, 0 client borrowers/0 private databases in the verified reused
parent, source/interpreter/binary/operator auth unchanged. Operational execution
descriptors are reclosed; original executed runner/manifest/review archives are
preserved. No historical case was reexecuted and no new PG service was created.
Configuration/offline success and the completed MCP call do not close the full
native call/HTTP-offer/turn/seal acceptance predicate. OpenCode v2's previously
accepted questions/ACK/restart scope remains unchanged.

[Structured failed receipt](validation-t214-native-r1.json).
