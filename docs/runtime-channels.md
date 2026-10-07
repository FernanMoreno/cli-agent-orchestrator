# Remote execution runtimes

`cao-bridge` runs ordinary CAO terminals on another machine while `cao-server`
keeps their public API and durable placement. The original `target_host` HTTP
routing remains available separately. Remote managed Work launches refuse with
`UnsupportedWorkEnforcement`: the bridge does not provide an approved portable
Work enforcement adapter. Local managed backends retain their existing behavior.

Set `CAO_RUNTIME_TOKEN_FILE` (a private file containing a shared channel token),
or `CAO_RUNTIME_TOKEN`, on the server and bridge. Set `CAO_BRIDGE_RUNTIME_ID` to
a stable runtime name and `CAO_BRIDGE_SERVER_URL` to
`wss://your-server/runtime/channel`, then run `cao-bridge`. Use a private CAO home
and SQLite store on each runtime. The token and token-file environment variables
are consumed before launching children; rotating the channel token requires a
restart. The channel token trusts its holder as an execution runtime. It does
not grant access to native Work-owned terminals or ordinary HTTP API scopes.

Use the ordinary server bearer credential with write/admin scope to call
`POST /runtimes/{runtime_id}/terminals`. The JSON body accepts `agent_profile`,
`provider`, `working_directory`, `allowed_tools`, `model`, `engine`, and `op_id`.
Explicit `allowed_tools: []` remains distinct from an omitted restriction.
Choose a stable `op_id` for launch retries: reusing it with different arguments
is refused. `GET /runtimes` requires read, write or admin scope. Agents that call
MCP need the server's normal HTTP address and separately authorized HTTP bearer;
never give them the runtime channel credential.

Returned terminals have `runtime_id` and `runtime_incarnation_id`. Existing
terminal input, key, output/range, work-directory, exit, delete, inspect-turn,
verify and cancel routes then use their durable placement. `turn` is the receipt
object; `turn_sequence` and `turn_completed` remain causal integer fields.
Native swarm work blocks automated delivery until the runtime reports it ready.

The central and bridge journals claim operations before native effects and
persist results before acknowledging them. Deadlines and connection epochs fence
late native writes. Reconnection reads an existing operation's proof and never
replays a claimed uncertain effect. HTTP 503 means no dispatch was possible;
504 means the outcome requires reconciliation. Inbox messages remain pending
when definitely unsent and enter reconcile after an uncertain dispatch.

Inspect a launch with `GET /runtimes/{runtime_id}/operations/{op_id}`; pass
`refresh=true` to ask the bridge for its durable result without replaying it.
`POST /runtimes/{runtime_id}/operations/{op_id}/cancel` records cancellation of a
launch. Unsent launches cancel immediately; a late successful launch is cleaned
up using its exact terminal and session incarnation. Cancellation and exact
teardown remain durable when the HTTP caller leaves. Unknown teardown retains
rows and evidence for inspection.

A bridge restart reuses a runtime incarnation only when the backend proves every
recorded terminal still belongs to it exactly. A changed or unproved incarnation
leaves older placement unavailable; it does not authorize cleanup by reusable
session/window names. Retain the private journals for recovery. Reconnect pushes
recent settled results; older results remain available through explicit operation
inspection. The ready file (`CAO_BRIDGE_READY_FILE`, default `bridge-ready` in the
CAO home) exists only while the authenticated channel is connected.
