# Host-safe script parsing — 2026-10-07

The unchanged Python 3.10 full suite lost an xdist worker with SIGSEGV inside
`ast.parse` on the original `a` plus 200,000 `.b` attribute chain (400 KB).
An isolated, core-disabled child reproduced exit -11. This is a native parser
failure, so catching Python RecursionError does not keep the host alive.

The linter now performs a pure stdlib tokenize preflight before its existing
AST parse. A logical statement admits at most 1,024 substantive tokens;
logical NEWLINE and semicolons outside brackets reset the count, while implicit
and explicit continuations retain it. Comments and ordinary literal content
do not consume additional complexity. On Python before 3.12, interpolation is
opaque inside a STRING token, so f-string tokens longer than 4,096 characters
are conservatively refused. Newer Python exposes interpolation tokens.
These are intentional limits, reported through the existing syntax/error
finding. Ordinary long scripts, comments and plain/multiline strings remain
supported. No target import/execution, network, filesystem access or process
launch is added to production lint; its existing single AST walk is retained.

The unchanged 400 KB chain remains in the regression, executed in a child so
an unfixed parser cannot kill pytest. Seven hostile cases failed before the
change; six large benign controls passed. All 98 script-lint cases passed on
Python 3.10/3.11/3.12/3.13/3.14 (12.82/16.88/14.31/15.71/16.47 seconds),
including smaller excessive expressions and continuation/f-string cases.
The 106 caller cases passed in 10.12 seconds. Mypy for the changed module,
Black, isort and scoped whitespace checks passed.

Independent review reran all 98 cases on Python 3.10 (11.88 seconds) and five
near-limit probes without a native crash. It confirmed token/error handling,
f-string accounting, compatibility limits and unchanged rule/schema behavior.
No material blocker was found. The full final matrix remains pending.
