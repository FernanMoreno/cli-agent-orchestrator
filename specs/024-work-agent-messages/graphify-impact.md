# Graphify Impact: Work Agent Messages

Graphify was queried before implementation using the existing `graphify-out/graph.json`:

- `WorkOrigins` resolves to `src/cli_agent_orchestrator/services/work_origin.py` with 87 neighbors, including `WorkRepository`, `WorkAttemptCredentials`, `WorkLaunchRuntime`, `DockerWorkBackend`, and work lifecycle tests.
- The graph indicates the managed origin is shared by the runtime and Docker backend; source verification confirms `WorkOrigins.handle_mcp_request()` dispatches the current four Work tools.
- The generated graph does not contain a reliable node for the full Docker message path and is known to lag current dirty-worktree changes. Its output is navigation only; it was not rebuilt in place.

Source-verified impact surface:

1. Managed contract/proxy gates: `work_mcp_proxy.py`, `work_agent_step.py`, and `docker_backend.py`. `bubblewrap_backend.py` contains a similar allowlist, but that backend is explicitly unregistered and fail-closed, so it is outside this feature's runtime surface.
2. Server-side identity and dispatch: `work_origin.py`, authenticated by `WorkAttemptCredentialContext`; its `handle_mcp_request()` currently handles task receipt, result submission, child, and handoff, so the new dispatch must be additive.
3. PostgreSQL schema, transaction, event sequence, owner filter: `work_postgres_schema.py` and `work_repository.py`; V3 must preserve V1/V2 statements/checksums and the existing V1 `WORK_TABLES` checksum input.
4. Proxy effect journal: `work_mcp_proxy.py` hashes the complete JSON-RPC request and MCP response today; the new operation needs body-free request and nested response projections while preserving the request-ID replay gate.
5. AG-UI event ring and message projection: `event_log_service.py`, `event_log_publisher.py`, and `agui_stream.py` cover the global process-local fleet ring. Durable message replay must instead read owner-authorized `work_events` from a separate job-scoped route.
6. Existing separate generic provider path: `mcp_server/server.py`, inbox persistence/delivery, and Spec023 tests.

Do not treat the generic terminal `send_message` tool as an alias. Recheck all graph claims against source after implementation and run the project composition gate.
