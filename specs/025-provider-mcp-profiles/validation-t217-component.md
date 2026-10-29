# T217 · reversible inline-code render equivalence

2026-10-29 · **Component PASS; native acceptance pending.**

R3 captured a paragraph containing inline code as well as strong emphasis.
The T216 strong-only projection correctly refused its unsupported code token.
The narrow extension recognizes backtick-delimited inline code only when
rebuilding its exact delimiters and content recovers the native lexical source.
The visible projection keeps all code contents literal. It does not decode
entities or escapes, strip stars inside code, fold newlines or trim padding.
Fenced/indented blocks and unsupported constructs remain refused on this path.
The existing literal fast path is unchanged.

All source/agent/database/nonce/receipt/CAS checks remain. The recognized-markup
path still requires a final receipt, 64 KiB maximum native text and the same 3 s
budget. It returns original native TEXT. No permission, model, request, login,
CAOS persistence, dependency installation or acceptance-threshold change.

Bound-reader RED: 3 failed/44 passed in 61.65 s. All eight sealed real R2/R3
pairs replay through the bound native vendor-store reader and return exact
native text; no model is invoked. Architecture: 5 contracts kept/0 broken.
Claude design and final implementation reviews found no blockers
(`d4b6e89c9d0d4e6faa893253bda8221b`, `5627d4b9c8424a65acc4e3808b275ff6`).
Full provider/observer/case regression: **380 PASS** in 252.13 s, 2 inherited
Pydantic deprecation warnings. Black/isort, local links and diff checks pass.

R1/R2/R3 failed receipts remain intact. T214 and native T216/T217 acceptance
require one fresh source-pinned task query with actual HTTP offer, one completed
owned result, verified durable turn, normal observer seal and owned cleanup.
The accepted v2 questions/ACK/restart baseline is not repeated or reopened.
[Structured component evidence](validation-t217-component.json).

Later native outcome: [R5 passes the scoped task-query contract](validation-t214-native-r5.md)
on source ae12c82f. This component record retains its original pre-native status.
