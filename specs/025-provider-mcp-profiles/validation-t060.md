# T060 — managed native-v1 collaboration: retained diagnostics

Full gate remains **open**. Two actual native terminals and production-installed profiles are required; a native call returning a protocol result is not by itself payload success, persisted delivery or final receipt acceptance. External cao.work.message remains separate.

## r1 — 936f7de6, 2026-10-21

Admissions8090/8071/8063/8001/7817/7115/6576MiB, unchanged6144 floor. Both production installations and native terminal201/model readiness passed (13.65/13.57s), separate private auth files and exact native catalogs sender2/receiver0. Actual list_siblings and send_message calls returned protocol results; no other calls. The sender did not produce a verified final result within the original75s budget (observed77.08s including requests); no restart/second phase acceptance. Total132.43s. Deleting the session before outer teardown left zero provider roots; API/PostgreSQL/tmux/private state cleanup and source/operator/binary preservation passed. Forced failure teardown did not produce graceful observer seals.

First diagnostic-boundary errors: it encoded allowed_tools query as JSON, but the API splits comma-separated names. Thus the discovery marker was not effective; protocol completion cannot establish successful discovery. Production source-defined sender injection also adds an identity suffix to the inbox body, so a bare-body comparison would have been wrong. Retain the original failed receipt/log without retroactive acceptance. The actual cause of the unfinished model turn is **unproved**: no current viewport was retained and the bounded raw output ends in native spinner updates. No timeout increase, receipt bypass or parser change follows from that observation.

## r2 — ec882c37, 2026-10-21

Fresh diagnostic uses installed allowedTools directly, asserts exact current terminal permissions, compares the existing expected_persisted_message suffix, and retains current screen/PostgreSQL observations during model work. Both production installs passed. Empty-filter receiver201/idle/selected model/allowed_tools=[] passed in13.67s. Sender creation returned **404** in3.20s with fixed detail **OpenCode v1 interaction channel requires all three interaction tools**; no model request or API restart ran. Admissions8260/8204/8176/8161/7985/7139MiB. Total41.61s, zero private roots after deletion and complete cleanup/preservation.

This is a reproduced production defect: a correctly authorized @cao-mcp-server/discovery sender with a validated collaboration filter containing only send_message/list_siblings is forced into the human three-tool protocol by the coarse selector. T061 corrects that boundary; it does not lower explicit human-protocol criteria or explain r1's model timeout. Both failed receipts remain immutable private evidence.
