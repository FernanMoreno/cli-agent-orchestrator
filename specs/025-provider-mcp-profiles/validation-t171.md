# T171 · bounded explicit interaction wait reconnect

Current Codex on7079a7a8 failed45.5s with three waits returning the exact
connection-error category, without ACK. The controlled API worker did recover
and two human answers persisted. The original failed receipt and verified
cleanup remain separate from the correction. See the
[native diagnostic record](../022-provider-agui-restart/validation-codex-current.md).

Only positive-duration `cao_wait_for_user_answers` now reconnects after
`requests.ConnectionError`, with a single monotonic wait_seconds+5 budget,
250ms bounded backoff and decreasing HTTP/server waits. It pins the source
capability once and preserves group/cursor. Each attempt closes its HTTP session.
Zero probes, Timeout/ConnectTimeout, HTTP errors and other tools retain their
existing behavior. No prompt, provider budget, tool permission or acceptance
predicate changed. The optional capability argument preserves the utility's
default per-request file validation for existing callers.

Validation before native execution:

- RED: two reconnect/budget cases failed, six existing error controls passed.
- GREEN: 137 focused tests passed, five warnings,71.38s; owner total79.78s.
  Includes real PostgreSQL, production HTTP/MCP composition, reconstruction,
  both live-journey contract modules, fixed-clock retries, capability pinning,
  zero probe and no-mutator-retry controls.
- Real-PG lost-response replay returns the same complete receipt after reopening
  the service, with exactly one answer_returned event, group satisfied and
  delivery returned_to_mcp; no ACK.
- PostgreSQL borrowers/databases zero before owner exit, exact container absent.
- All5 architecture contracts kept; Black/isort and scoped diff checks pass.
- Root composition/source review and independent Claude review: GO.

Native acceptance on the corrected source remains pending. This shared MCP
correction applies to every provider using the explicit wait tool; it does not
establish current Codex/Claude/OpenCode native acceptance or global gates.
