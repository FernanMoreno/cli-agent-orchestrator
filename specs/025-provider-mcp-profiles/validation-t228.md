# T228 — real native MCP execution and discovery-marker authorization

**PASS for two bounded native operation cases, 2026-10-29.** Source `74df3baa4184acb21a77090878a09660805ec964`. [Machine receipt](validation-t228.json).

Claude Code2.1.303 calls the production stdio `list_siblings` handler, which reaches the actual owned HTTP API and held PostgreSQL. Both profiles expose exactly one MCP tool and0 native built-ins. Both have the same temporary explicit native client permission `--allowedTools mcp__cao-mcp-server__list_siblings`, with permissionMode:default; no operator setting/login or server filter is changed. Only the persisted CAO discovery marker differs.

| Case | Native result | Actual own API GETs | Final turn | Time |
| --- | --- | --- | --- | --- |
| @cao-mcp-server + discovery | success:true, siblings:[] | metadata1 + siblings1, both200 | verified |130.63s|
| @cao-mcp-server alone | success:false, missing-discovery denial | metadata1; siblings0 | verified |124.85s|

A native tool_use and its single tool_result share the issued call ID in each case. The structured result is validated before the final response is released. The caller's nonempty unique group is persisted through the real API; the expected set is empty. These cases prove that catalogue/client permission does not replace the production server's discovery check. Caller lookup unavailability also fails closed in an executable production-handler control.

## Isolation and lifecycle

A generic test-only Unix bridge binds one caller to the owned API and admits only its metadata/siblings GET paths. Both ends reject writes, queries, bodies, foreign IDs and alternate URLs; the host forwarder disables environment proxy routing and the namespace asserts proxy variables absent. The native process retains its isolated network/operator/DB masks and sees only the owned mode0600 socket, not PostgreSQL credentials. One server thread,8 attempts maximum and bounded request/response sizes keep this diagnostic finite.

Each1891-pin fresh run preserves its exact executed runner/source archive, re-closes its operational wrapper, retires the read bridge/observed processes/wrapper/tmux and removes owned HOME. The same existing PostgreSQL ends with0 borrower connections/0 private databases. Lowest recorded memory is7466MiB authorized and7367MiB denied, above unchanged6144MiB. Original180s/5s predicates are preserved.140 expanded controls plus64 overlapping final format/transport/config controls pass; Black/isort/diff and composition/Import Linter5kept/0broken pass. Claude read review findings were corrected and rechecked.

## Preserved failure and correction

Authorized-r1 on c1779249 remains **FAILED117.10s**: native tool_result.content contained the operation JSON followed by Claude's total_tokens system-reminder in the same string. The observer's JSON-only decoder raised ExtraData and no own final receipt was accepted. Owned cleanup and heldPG0-0 passed; exact failed source/requests remain private and unchanged. The initially prepared denied-r1 descriptor was never claimed/executed.

The exact captured shape first reproduced RED offline. The corrected observer decodes JSON only from the beginning (with leading whitespace), retains duplicate-key/call-ID/type/business checks, and accepts only whitespace or one exact anchored numeric total_tokens reminder afterward. String and text-block forms, authorized/denied bodies, repeated/unknown reminders, additional JSON and arbitrary prose are tested. No response substring search, criterion/deadline reduction, product permission change or retroactive PASS was used. The two R2 cases execute newly archived source74df3baa.

## Acceptance boundary

This is **MCP discovery-marker authorization**, with an explicit diagnostic native client grant and an auth-disabled owned API fixture. It does not accept default native-client approval, API bearer/scope/tenant policies, nonempty or cross-session/depth discovery, all tools/providers/profiles/backends, external inference or global capacity. T226/T227 and historical job receipts retain their own source/scenario bindings; no historical reconciliation job was closed or rerun.
