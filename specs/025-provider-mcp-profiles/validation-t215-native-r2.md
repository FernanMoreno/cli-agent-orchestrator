# T215 native R2 · captured comparison and proven cause

2026-10-29 · **Capture diagnostic PASS; T214 native task-query acceptance FAILED.**
Source `e6acc459f45de51ce11618fa12ce2051787b0dcd`, 600 source pins;
admission 9833 MiB, original 180 s task deadline and 6144 MiB floor.
Total run 292.307 s including startup and normal cleanup.

Exactly one native MCP task query completed, but the turn did not reach durable
verification before its deadline. The private capture was sealed normally:
8 records, 4 complete matched pairs, failed=false, overflow=false. Each native body
has 133 characters versus 129 rendered: the only difference is two pairs of strong
Markdown delimiters. Structured CommonMark parsing reconstructs each source
exactly; projecting only the recognized strong markers gives the exact rendered
whitespace signature. Raw texts remain private. R1 had no captured pair and is
not retroactively assigned this cause.

T216 replays all four sealed pairs through the actual source-bound private
vendor-store reader: 4 PASS, returning original native text exactly. This is an
offline reproduction, not native acceptance of the pending product fix. The
vendor SQLite fixture models OpenCode's own storage; CAOS persistence remains
PostgreSQL.

Owned cleanup passed; API/tmux retired, private runtime removed and PostgreSQL
scopes closed. The verified reused parent has 0 client borrowers/0 private databases.
Source/interpreter/binary/operator credentials remained intact. Operational
execution descriptors are reclosed and immutable executed archives preserved.
No historical job was restarted. T215 is complete in its diagnostic scope;
T214 remains open until a fresh source-pinned native task-query case passes all
original offer, turn, seal and cleanup checks. Previously accepted v2
questions/ACK/restart acceptance remains unchanged.

[Structured evidence](validation-t215-native-r2.json).
