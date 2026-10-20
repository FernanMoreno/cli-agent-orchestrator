# Graphify Impact: Generic Message Source Binding

**Date:** 2026-10-19
**Scope:** Spec023 live runner's source-integrity inventory for the provider `send_message` → inbox → delivery → event → AG-UI acceptance path.

## Query result

Read-only queries against the existing `graphify-out/graph.json`:

```text
graphify query 'send_message -> _send_message_impl -> create_inbox_message -> InboxService deliver -> post_send_message -> AG-UI stream' --budget 1500
graphify path 'send_message' 'create_inbox_message'
```

The broad query returned many ambiguous generic nodes and was truncated. The path query reported an ambiguous source match and no directed path. Graphify does not provide a unique call path for this runner boundary; that result does not establish that the source connection is absent.

## Source-verified path

Direct inspection confirms the live journey crosses:

1. `src/cli_agent_orchestrator/mcp_server/server.py::send_message` delegates to `src/cli_agent_orchestrator/utils/orchestration.py::_send_message_impl`.
2. `_send_message_impl` calls the production inbox route in `src/cli_agent_orchestrator/api/main.py`.
3. The route creates and later reads the inbox through `src/cli_agent_orchestrator/clients/database.py`, with state types from `src/cli_agent_orchestrator/models/inbox.py`.
4. The route calls `src/cli_agent_orchestrator/services/inbox_service.py::InboxService.deliver_pending`; delivery checks readiness/provider state and sends terminal input through `src/cli_agent_orchestrator/services/terminal_service.py` and the backend/provider registries.
5. Terminal delivery dispatches the `post_send_message` plugin event. `src/cli_agent_orchestrator/plugins/registry.py` dispatches it to the bound `src/cli_agent_orchestrator/plugins/builtin/event_log_publisher.py`, which appends metadata to `src/cli_agent_orchestrator/services/event_log_service.py`.
6. The production AG-UI route in `api/main.py` reads and projects through the already bound `src/cli_agent_orchestrator/services/agui_stream.py`.

The source inventory now also binds `models/terminal.py`, `services/status_monitor.py`, `plugins/__init__.py`, `plugins/base.py`, `plugins/events.py`, `backends/registry.py`, `backends/tmux_backend.py`, and `providers/manager.py`, which own the state and dispatch decisions used by the same observed message path.

## Refresh decision

No graph refresh is needed. This correction changes only the live harness's fixed source-hash inventory and its offline tests; it introduces no source import, runtime dependency, or production call edge. The fresh full-tree source freeze and explicit live review remain separate open gates in Spec023 T023/T024/T044.
