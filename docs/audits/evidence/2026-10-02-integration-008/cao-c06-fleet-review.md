# C06 fleet composition review

Verdict: **PASS WITH RISKS**. All seven concrete reviewed findings have owner-applied fixes and passing regression evidence; remaining limits are deployment acceptance. The minor NodeSwitcher hook-order note is resolved by owner. Reviewer changed only dedicated regression tests and off-workspace patch/report artifacts, never application source.

## Scope and provenance

Reviewed `/tmp/cao-c06-fleet-review.diff`, current assembled source and `/var/tmp/caos-integration-008-cy0jdbdw/files` resolved through `/tmp/cao-integration-008-baseline-root`. Baseline already contains Work, durable007 turn recovery and browser authentication; those were not mistaken for fleet additions. `web/src/auth.ts` has no fleet diff. The fleet delta adds selected-node routing, abort authority/epoch guards, dashboard remount, NodeSwitcher, registered-node proxy/WS bridge, node credentials, live ConfigMap registry and frontend Docker stage. Root retains source ownership; C05 persistence/workflow work is separate.

Graphify local query: `/tmp/cao-c06-fleet-graphify.txt` (generated graph is stale for new fleet hunks); verified actual callers against web/api/store/App/TerminalView/useEventFollow and panel main/config source.

## Findings reproduced and resolved by owner

1. Selected-node Plugins were broken: `/plugins` was absent from panel `_NODE_API_PREFIXES` although all four existing plugin operations use that namespace. Added a real FastAPI->httpx MockTransport regression. Source now includes plugins; all other actual dashboard namespaces already match.
2. Missing credentials on one registered node escaped fleet probe's try while constructing AsyncClient; gather raised and blanked healthy nodes. Regression uses one bad and one healthy node. Construction/resolution now inside per-node try; bad entry remains offline with generic error type.
3. Missing/unreadable credential file produced unhandled500. Proxy now returns generic structured503 `node_credential_unavailable` without private path/token bytes. Credential validation rejects ASCII control/non-ASCII values before transport. WebSocket path already catches credential errors and closes generically without forwarding browser query tokens.
4. Late non-OK JSON body escaped epoch ownership after node switch. Deferred body regression produced old409 instead of node-selection rejection. Added epoch check after error-body await; old component global errors cannot attribute that body to current node.
5. Initial auth bootstrap outage followed by successful retry opened Dashboard with activeNode=null because fleet discovery lived only in mount effect. Regression reproduced this assembled App/auth/UI path. Discovery now runs upon principal availability/identity changes and explicit fleetReady is set only on fleet success or definitive404. Bootstrap/login/setup/error guards stay ahead of the dashboard gate. Local renewal preserves principal availability and therefore locked drafts/navigation.
6. Cancellation inside HTTP c.send occurred before stream generator owned client and escaped HTTPError handler. Direct ASGI cancellation regression proved owned client remained open. Owner added cancellation/error release before re-raising.
7. Browser disappeared after node WS attach but before browser acceptance, outside relay finally. Direct handshake regression proved attached node socket remained open. Owner now releases upstream socket when acceptance raises/cancels.

Fix proposals preserved at `/tmp/cao-c06-fleet-review-fixes.patch`, `/tmp/cao-c06-fleet-bootstrap-fix.patch`, `/tmp/cao-c06-fleet-resource-fix.patch`; root applied them selectively and formatted source. RED evidence: `/tmp/cao-c06-fleet-review-red.txt` (3 failed), `/tmp/cao-c06-fleet-race-red.txt` (1 failed), `/tmp/cao-c06-fleet-bootstrap-red.txt` (1 failed), `/tmp/cao-c06-fleet-resource-red.txt` (2 failed). Malformed credential regression is included in final panel suite; no personal credentials used.

## Contracts checked

- Panel Authorization/Basic and HttpOnly strict WS cookie authenticate only the panel. Proxied request headers exclude Authorization/Cookie/Host; node headers resolve registered token_env/token_file independently on each request, including legacy probes/actions. WS upstream receives only node header credential, no browser token query or browser Origin; panel verifies browser Origin itself before attach. CAO API confirms header bearer is supported at actual terminal WS boundary.
- HTTP routing, SSE and WS all use node prefix and base path. Node switch aborts prior request authority, store mutations check epoch, Dashboard remount clears local same-ID state; SSE and WS late-frame callbacks are guarded and no input replay exists. Non-OK body race now checked as above.
- Fleet errors403/outage keep dashboard closed; only actual404 allows standalone origin. Added assembled App tests for403/503 and a deferred404 response, plus outage recovery.
- Proxy resolves registered nodes only, excludes internal/private namespaces, repeatedly decodes/rejects traversal separators, caps request body at2MiB before upstream send, preserves response statuses/body202/409 and SSE raw stream headers. Stream generator/relay tasks own cleanup after transfer; resource tests cover pre-transfer holes.
- Registry awaits first ConfigMap read before serving, uses verifiedHTTPS/ServiceAccountCA, rereads rotated bearer file each time, keeps last-good snapshot, reports readiness/source failure, and cancels+awaits owned refresh task at shutdown.
- Docker node24 frontend stage builds main web app with absolute WEB_BASE default/, copies web_ui, prefers it only when index exists, retains optional legacy static fallback. Dockerignore admits web source and excludes host node_modules.
- Existing Profiles, Plugins, Work projection/turn recovery and navigation locks remain; actual profile/setup/auth/transport regressions were read, not blindly repeated.

## Verification

Owner evidence inspected:
- Original panel expanded9 + legacy44 passed.
- Before review patch full web424 passed/33 files (`/tmp/spec008-fleet-web-all-final.log`).
- Reviewed panel13 passed (`/tmp/spec008-fleet-panel-reviewed.log`) includes first4 new panel regressions.
- Reviewed HTTP/API/base/epoch routing60 passed (`/tmp/spec008-fleet-web-reviewed.log`).
- Auth/setup/bootstrap42 passed (`/tmp/spec008-fleet-bootstrap-final.log`).
- Prior UI access/profile45, auth/transport33 and frontend production build succeeded; logs supplied by owner.

Reviewer fresh assembled bootstrap gate: `npm --prefix web test -- --run src/test/fleet-review-bootstrap.test.tsx`: **4 passed**, `/tmp/cao-c06-fleet-bootstrap-review-final.txt` (outage recovery,403,503,deferred404). A transient fixture failure was corrected to share/clone the deferred404 response across obsolete discovery requests; source correctly discarded those obsolete replies.

Reviewer architecture command `project-composition-check "$(cat .ai/project-name)"`: **PASS,5 kept,0 broken**, `/tmp/cao-c06-fleet-composition.log`.

Dedicated reviewer tests: `test/examples/test_integration_008_fleet_review.py`, `web/src/test/fleet-review-races.test.ts`, `web/src/test/fleet-review-bootstrap.test.tsx`.

## Limits

No live cluster, remote nodes or personal credentials were used. FastAPI/HTTPX integration tests exercise real routing with hermetic transport; direct ASGI/fake-owned resources expose exact cancellation/acceptance boundaries. Real TLS rotation, reverse-proxy WS attach and Docker image execution remain owner deployment acceptance rather than this source review. Existing optional ConfigMap/UI credentials and upstream CAO authorization remain authoritative. Final production build after App fixes passed (`/tmp/spec008-fleet-web-final-build.log`,18.06s).


Final owner resource gate: `.venv/bin/python -m pytest --no-cov -p no:cacheprovider -q test/examples/test_integration_008_fleet_panel.py test/examples/test_integration_008_fleet_review.py`: **15 passed**, `/tmp/spec008-fleet-resources-final.log` (9.21s), including the two initially RED ownership failures. Final scoped diff whitespace check passed.

Minor robustness note sent to owner: NodeSwitcher initially places its navLockCount useStore hook after the empty-fleet return. Current bootstrap/dashboard remount means the fleet list normally stays nonempty throughout the mounted switcher's lifetime, but moving the hook before that return prevents React hook-order failure if registry UI later clears/refills without a remount. This does not invalidate the tested assembled fleet paths and needs no architecture change.

## Follow-up: elastic membership polling (T041 tail)

Reviewed only added fleetMode/fleetError/refreshFleet, Dashboard10s interval, default-node preference and NodeSwitcher missing-node option. Original fresh owner gate `/tmp/spec008-fleet-refresh-green.log`:10passed (membership refresh,403 retention, assembled bootstrap). Polling never changes selected-node authority: deleted registration keeps its address and synthetic dropdown option, last-good nodes survive registry errors and error banner stays visible; navigation lock still guards selection. Initial selection prefers online supervisor/central, then online, then first registered node. Hook-order note above is now fixed by owner.

Found one tail composition gap:10s interval and20s registry timeout permit overlapping polls. refreshFleet's boolean fleetMode check allowed an earlier delayed response to replace newer membership/error. Clearing the interval on Dashboard remount did not abort/invalidate that owner's direct registry request. Dedicated `web/src/test/fleet-review-refresh.test.ts` produced2RED (`/tmp/cao-c06-fleet-refresh-red.txt`): deferred two-poll stale publication, and stopped owner retaining in-flight request.

Off-workspace fix proposal `/tmp/cao-c06-fleet-refresh-owner-fix.patch` adds a request generation and owned AbortController; newer polls abort older requests and guard both success/error publication, even when mocked transport ignores abort. Dashboard cleanup stops interval and invalidates/aborts its pending request. api.getFleet(signal?) merges caller+timeout signals while direct registry requests remain independent of selected-node authority; existing browserFetch/auth boundary stays intact. Application source not edited by reviewer. Owner's final focused gate and production build will be appended when available; no broad tests repeated.


Follow-up final verdict: **PASS** for the reviewed membership-polling tail. Root applied the owner-generation patch. `/tmp/spec008-fleet-ownership-final.log`: **13passed/4files** covering refresh ownership/order, membership/403 retention, assembled auth/bootstrap403/503/deferred404, and late error-body epoch. Scoped diff check passed. No unresolved review finding remains in this tail; previous live-deployment limits still apply. Reviewer made no application-source edits and did not repeat broad gates.
