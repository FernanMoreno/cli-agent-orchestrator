# T218 · task-query PostgreSQL witness emission

2026-10-29 · **Component PASS; native final acceptance pending.**

R4 verified the real native turn and cleaned normally, but the original final
receipt remains false: the task controller omitted the PostgreSQL target
stability witness that the strict final predicate requires. A one-field
counterfactual only identifies the missing boundary; it is not execution proof.

The controller now compares the API's actual main/auth/peer target fingerprint
with the initial owned target snapshot before fixture writes/input and again
after verified completion and receiver checks. The field starts false and
becomes true only after both observations match. Expected identity must be a
64-character lowercase hex digest. Invalid targets become a generic gate error;
only the real fingerprint helper's specific exception is caught. The requirement
and existing message/restart branch remain unchanged.

RED: 1 failed/1 passed/65 deselected, 38.37 s. Initial regression 225 PASS;
final regression **231 PASS**, 98.34 s, 3 inherited warnings. Tests exercise
healthy, stale turn, changed-before/after, invalid-before/after and missing
expected identity with actual immutable DatabaseTarget snapshots and the real
fingerprint helper. Failed prechecks have no INSERT/input; failures never emit
true. The final-consumer fixture no longer seeds this witness: it must fail
without it and pass with the actual healthy controller's emitted observation.
False/None/deleted witnesses are also rejected. This prevents the prior test mask.

Final formatted controller check: 7 PASS/68 deselected in 31.44 s.
Black/isort and diff checks pass. Architecture: 5 kept/0 broken. Claude final review found no blockers
(`1f937de8899042a8adc26db24b1f5469`). Original 180 s/6144 MiB limits and all
MCP/HTTP/result/receipt/seal/cleanup gates remain. No product permission or
acceptance threshold change. A fresh pinned native execution is still required.
[Structured evidence](validation-t218-component.json).
