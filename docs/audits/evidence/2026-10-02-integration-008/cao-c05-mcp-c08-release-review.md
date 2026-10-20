# C05 MCP authentication and C08 release review

Read-only review of current source against the dirty baseline recorded in `/tmp/cao-integration-008-baseline-root` (snapshot files live under its `files/` directory). No product edits, commits, provider launches or release/network operations.

## Conclusion

No material new blocker in the reviewed slice. Expanded MCP credentials are restricted to the existing local API calls; broker lease, release and completion requests retain their separate broker/release headers. The pre-existing target-host handoff path is unchanged by this patch and is not newly certified as a credential-isolation boundary.

- Main MCP lifecycle start/read/result/plan/submit/resume/cancel/wait and SSE now attach `_auth_headers()` to `API_BASE_URL` requests. SSE merges Authorization with Accept and keeps cursor parameters. The four authoring tools use the same local helper and run `_tool_denied_reason` before HTTP effects. The selector-based generic guard accepts tool names without requiring a second annotation catalog; API scopes remain authoritative.
- Source diff confirms auth additions target static local URLs; no auth header was added to elastic broker URLs. Reviewer regression includes a foreign completion control whose available local bearer must never appear alongside the release token.
- Scheduled release requires the upstream repository, exact event SHA CI success, reachable prior stable tag, existing GitHub release and readable PyPI version JSON. Empty commit range skips without unnecessary registry dependency. Conventional breaking/feature subjects choose major/minor; manual input remains the explicit override. Workflow concurrency queues without cancellation; full action SHA pins are retained. Manual release intentionally bypasses scheduled prerequisites, as requested.
- Release preflight and release checkout share the event SHA; no upstream/default-branch advancement is substituted for the tested head. Existing post-tag publishing remains a separate workflow; a failed previous publication prevents later scheduled version advancement.

## Evidence

- Fresh reviewer command `.venv/bin/python -m pytest -q test/mcp_server/test_integration_008_workflow_auth.py`: **4 passed in 46.45s**, `/tmp/spec008-reviewer-mcp-auth-final.log` (local start/status/SSE plus foreign completion).
- Root recorded workflow/auth gate: **68 passed**, reviewed source remains consistent with that scope.
- Read `/tmp/spec008-release-green.log`: **14 passed in 6.62s**, isolated shell policy doubles and CI policy; no actual GitHub/PyPI calls or release effects were executed by review.

Limits: PyPI gate establishes version endpoint publication rather than package artifact integrity, which is the selected upstream policy. Personal-provider acceptance and actual publication are outside this review. No redundant broad test rerun was needed after focused controls passed.
