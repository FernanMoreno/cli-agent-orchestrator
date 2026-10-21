# T060 — managed native-v1 collaboration: retained diagnostics

Full gate remains **open**. Two actual native terminals and production-installed profiles are required; a native call returning a protocol result is not by itself payload success, persisted delivery or final receipt acceptance. External cao.work.message remains separate.

## r1 — 936f7de6, 2026-10-21

Admissions8090/8071/8063/8001/7817/7115/6576MiB, unchanged6144 floor. Both production installations and native terminal201/model readiness passed (13.65/13.57s), separate private auth files and exact native catalogs sender2/receiver0. Actual list_siblings and send_message calls returned protocol results; no other calls. The sender did not produce a verified final result within the original75s budget (observed77.08s including requests); no restart/second phase acceptance. Total132.43s. Deleting the session before outer teardown left zero provider roots; API/PostgreSQL/tmux/private state cleanup and source/operator/binary preservation passed. Forced failure teardown did not produce graceful observer seals.

First diagnostic-boundary errors: it encoded allowed_tools query as JSON, but the API splits comma-separated names. Thus the discovery marker was not effective; protocol completion cannot establish successful discovery. Production source-defined sender injection also adds an identity suffix to the inbox body, so a bare-body comparison would have been wrong. Retain the original failed receipt/log without retroactive acceptance. The actual cause of the unfinished model turn is **unproved**: no current viewport was retained and the bounded raw output ends in native spinner updates. No timeout increase, receipt bypass or parser change follows from that observation.

## r2 — ec882c37, 2026-10-21

Fresh diagnostic uses installed allowedTools directly, asserts exact current terminal permissions, compares the existing expected_persisted_message suffix, and retains current screen/PostgreSQL observations during model work. Both production installs passed. Empty-filter receiver201/idle/selected model/allowed_tools=[] passed in13.67s. Sender creation returned **404** in3.20s with fixed detail **OpenCode v1 interaction channel requires all three interaction tools**; no model request or API restart ran. Admissions8260/8204/8176/8161/7985/7139MiB. Total41.61s, zero private roots after deletion and complete cleanup/preservation.

This is a reproduced production defect: a correctly authorized @cao-mcp-server/discovery sender with a validated collaboration filter containing only send_message/list_siblings is forced into the human three-tool protocol by the coarse selector. T061 corrects that boundary; it does not lower explicit human-protocol criteria or explain r1's model timeout. Both failed receipts remain immutable private evidence.

## r3 — 42bdbe7d: corrected product boundary, private checker failure

After T061 integration, both production-installed native terminals201/idle/selected model passed in13.51/13.93s, with exact allowed_tools metadata (sender MCP selector/discovery, receiver empty), independent auth leaves/roots and exact two/zero-tool filters. The first actual discovery/send phase passed: one exact message in PostgreSQL and API with the source-defined identity suffix, one valid redacted AG-UI notification, native/API exact Unicode plus verified LAST receipt/completed in **16.78s**. Admissions7849/7810/7867/7845/7693/6953/6331MiB, unchanged6144.

The private checker then raised AttributeError before API restart: its first-row identity projection called items() on the native named/ordinal **Record**, which exposes keys()/indexed access. No application behavior failed at that boundary, but no full restart/second-phase acceptance follows. Total73.69s; deletion left zero provider roots before outer teardown and full API/PostgreSQL/tmux/private-state/source/operator/binary cleanup passed. Failure teardown had no graceful observer seals. Original failed receipt remains separate.

Fresh r4 corrects only Record normalization through keys()/indexed access, preserving actual SQL, selected columns/values, source, limits and full predicate. A pure check against the actual Record type verified all values and datetime identity before launch. The full gate remains open until its own source-bound result.

## r4 — 42bdbe7d: functional restart passed, provider cleanup failed

Both actual production installations/native201/selected models/exact permissions and isolated credentials passed; initializations13.60/13.59s. First phase **26.71s**, API restart **8.80s**, second phase **14.24s**. Each phase passed exact PostgreSQL/API message with identity suffix, one redacted AG-UI notification, exact Unicode/LAST receipt/completed. Original native identities and immutable first PostgreSQL/API row survived restart. Exact sender2/receiver0 catalogs; actual calls list_siblings/send_message/send_message, all completed without MCP error/results present, receiver no calls. Both exit200 and graceful protocol observer seals passed.

But **two provider-private roots remained after deleting the session**, before removing the outer test root. Full predicate therefore **failed**, total112.38s. API/PostgreSQL/tmux/outer disposable-state cleanup passed; source/operator/binary unchanged. Admissions7907/7852/7885/7835/7627/6838/6286/**6183**/6208MiB, all above unchanged6144, including restart and second model stage. Original failed receipt retained without modifying its success=false.

This closes the functional committed-source recheck for bounded T061, not T060/full OC1. New T062 addresses the actual restart ownership failure: re-created OpenCode provider objects have no preceding random private-root pointer and manager fallback has no OpenCode cleanup adapter. T057 only covered same-instance reset; older outer-fixture cleanup is not evidence of provider-level retirement after API restart.
