# T216 · structured strong-emphasis render equivalence

2026-10-29 · **Component PASS; native task-query acceptance pending.**

T215 captured the actual mismatch before changing behavior: the TUI omits
strong-emphasis delimiters while retaining all visible content. The guard keeps
its existing literal/whitespace comparison first. Only a mismatch can enter a
bounded CommonMark tokenizer using the already declared markdown-it dependency.
It accepts paragraph/text/softbreak and single-depth strong tokens only, requires
exact lexical reconstruction, projects only native formatting, and compares that
visible content to the rendered signature. It returns original native TEXT.

The new path requires the bound receipt as the final nonblank line and rechecks
database identity. Existing source/agent/nonce/receipt/durable CAS fences remain.
The native text cap is still 64 KiB and budget still 3 s. Code, links, headings,
HTML, escapes, entities, hidden reference definitions, nested/triple emphasis,
hard breaks, NUL and changed visible characters gain no new acceptance. Parser
failure becomes the original generic extraction error. The rendered side is not
reparsed and the observer still receives exactly two comparison calls.

Initial RED: 3 failed/26 passed. Provider regression: 223 PASS in 135.58 s,
2 inherited Pydantic deprecation warnings. Observer/case regression: 146 PASS
in 157.53 s, giving 369 distinct Python tests. Four sealed actual pairs replayed
through the real bound vendor-store reader return the exact native text.
The fixture uses the vendor's own SQLite format; CAOS persistence remains
PostgreSQL. Architecture: 5 contracts kept/0 broken. Claude read review found no
blockers (`f5d51cc922d04f838463eedaa741050c`). No dependency installation, migration,
permission expansion or plaintext-only prompt workaround.

T214 R1/R2 remain failed historical runs. A fresh pinned native task query must
still pass the original HTTP offer, successful operation, durable turn, seal and
cleanup predicate before acceptance. Baseline v2 questions/ACK/restart remains
accepted independently. [Structured evidence](validation-t216-component.json).

Durable diagnostic conclusions saved to Obsidian in operation
`save-20261029-caos-t215-proven-strong-render`; strict lint: 0 issues.

Later native outcome: [R5 passes the scoped task-query contract](validation-t214-native-r5.md)
on source ae12c82f. This component record retains its original pre-native status.
