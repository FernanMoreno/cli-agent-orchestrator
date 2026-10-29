# T219 · OpenCode cancelled-turn startup and public receipt fence

2026-10-29 · **Component PASS; native concurrency acceptance pending.**
Base main `0c5dfcc410c290191cde5bfafb22d33d1575a5d6`.

Real adapter initialization under the cancellation-startup context reproduced
two defects: it published IDLE before releasing the old receipt and accepted a
foreign generation. A second control reproduced raw/screen public detectors
returning IDLE under a pending receipt, including the gap between initialization
return and durable cancellation. That could release ready-state consumers early.

Initialization now validates context identity/generation before profile/runtime
effects. On the FIFO/TUI path an independent probe checks the owned replacement
viewport, changed foreground command and exact generation. It rejects processing,
permission, stale completion/receipt/user frames and preserves provider errors.
Readiness is recorded without publishing or clearing the old task. The transient
shell baseline is cleared on success/failure. The public UI detectors retain
PROCESSING while a receipt is pending; the explicit private probe alone may
observe transport readiness. They never consult a copied cancellation ContextVar.
Durable finish still precedes ordinary refresh, and no task is resent.

All OpenCode UI turns with a pending receipt now report PROCESSING rather than
IDLE, including outside cancellation. Stable-IDLE fallback and IDLE-triggered
inbox delivery remain withheld while that receipt is pending; receipt
verification and the existing step deadline remain authoritative.

Normal startup still publishes IDLE/COMPLETED. V1's component control uses its
required installed profile; both version initialization paths are exercised.
MCP startup helpers only read readiness files/logs and do not publish. Native
event-status resolution remains unchanged. Herdr inherits the unsupported
confirmed-reset method and refuses before initialization: this is tested through
the actual inherited method, not claimed as event-backend cancellation support.
The cancellation event-inbox bootstrap is unreachable under that backend
contract and has not been validated as unchanged.

Liveness controls drive the existing completion loop with the actual OpenCode
status detector: no receipt reaches the step deadline without success/resend;
two stable results reach the existing matching CAS. Deferred initial delivery
still follows successful initialization (`terminal_service`), so initial task
receipts are not prepared ahead of normal startup. Existing v2 parser tests pass
unchanged. Controlled transport/FIFO seams do not establish native acceptance.

Isolated RED: 2 FAIL/1 PASS in 31.91 s; public-detector RED: 2 FAIL. The first
fixture failed ownership isolation before launch and was corrected. Two existing
Codex controls independently failed for a missing FIFO restart witness; their
positive fixture now supplies and asserts the same prepare/restart/activate
identity. No Codex product change. The extended v1 fixture initially omitted its
installed profile; a liveness fixture initially returned inconsistent CAS text.
All failures remain private and no deadline or success criterion was relaxed.

Final: **381 PASS**, 5 inherited warnings, 192.63 s across 13 files; includes
real PostgreSQL, private tmux reset and cancellation ownership composition.
The final focused controls pass 29 cases in 34.17 s; these overlap the regression.
Architecture 5 kept/0 broken; Black/isort and scoped diff checks pass. Claude
source review reported no hard blockers and requested the liveness/event-reset
controls subsequently verified above. Its final follow-up
(`6ccdafe4dba9412eabf55644dc474e16`) confirms both concerns resolved and no blocker. Existing PG parent identity verified,
0 borrowers/0 private databases after the suite; no new service or native CLI/model.

This closes T219's component correction only. T013/OC-06's native two-terminal
cancellation, broader profiles/Work and global acceptance remain open. Earlier
native acceptances retain their original sources; this code is not attributed
to their receipts. [Structured evidence](validation-t219.json).

Durable Obsidian operation `save-20261029-caos-t219-cancelled-readiness` applied;
strict lint: 0 issues.
