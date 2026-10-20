# Graphify impact: real-provider AG-UI restart acceptance

## Graph build

- Command: `/home/felni/.local/bin/graphify update . --no-cluster`
- Candidate only: `/home/felni/.cache/caos-spec021-evidence-20261015/agui-provider-restart-r1/source`
- Result: 53,958 nodes and 123,089 edges; raw AST extraction, no clustering or LLM call.
- `graphify-out/graph.json` SHA-256: `19441d9c2c7a77a32342c7cb3aa0d6283d27d6f2106f6a6f8de988cb592e6dcb`.

## Relevant nodes and edges

The graph contains these source-backed nodes:

- `test_live_provider_interaction_cli_journey_run` — `test/live_provider/interaction_cli_journey.py:L1945`; it calls the existing provider/profile/capability setup and runs the CLI/MCP process.
- `src_cli_agent_orchestrator_api_main_agui_stream` — `src/cli_agent_orchestrator/api/main.py:L2734`; it calls `InteractionService.validate_projection_cursor`, gets the app-scoped interaction service, and uses the AG-UI lifecycle tracker and frame helpers.
- `src_cli_agent_orchestrator_services_agui_interaction_projection_project_interaction_snapshot` — `src/cli_agent_orchestrator/services/agui/interaction_projection.py:L146`; it is imported by `api.main` and called by the stream's interaction projection.
- `test_integration_test_interaction_transport_contracts_test_agui_stream_snapshots_and_polls_real_interaction_metadata_when_idle` — `test/integration/test_interaction_transport_contracts.py:L356`; it calls the real stream handler through the existing integration context and checks redaction and idle refresh.
- `test_integration_test_interaction_recovery_test_group_and_answer_receipts_replay_after_reopening_the_same_postgres_store` — `test/integration/test_interaction_recovery.py:L38`; it exercises fresh `InteractionService` instances over the same PostgreSQL store and receipt continuity.

`graphify affected src_cli_agent_orchestrator_services_agui_interaction_projection_project_interaction_snapshot --depth 2` identifies `api.main`, the projection adapter and boundary tests, and the integration/API stream consumers. The directed `graphify path` from the real-provider `run()` node to `agui_stream()` has no path. The opt-in path now starts `interaction_api_worker` in a separate process; that worker imports and serves `api.main.app`, whose router dispatches `/agui/v1/stream` over HTTP. The process and HTTP boundaries are dynamic and are not represented as Python call edges by AST extraction.

## Verified against source

- `api.main.agui_stream` is registered on the production `app` and emits an initial `STATE_SNAPSHOT`, then recomputes interaction metadata on authorized heartbeat and emits `STATE_DELTA` when it changes (`src/cli_agent_orchestrator/api/main.py:L1936-L1950,L2855-L2879,L2968-L3043`).
- `_interaction_projection()` resolves `get_interaction_service(request)` and passes its bounded page through the closed `project_interaction_snapshot` function. The route catches projection failures and emits the unavailable projection rather than exposing backend exception content (`src/cli_agent_orchestrator/api/main.py:L2855-L2869`).
- The default provider journey still starts its minimal interaction-only test app. The opt-in restart path instead starts the source-bound worker that imports `cli_agent_orchestrator.api.main.app`, assigns a fresh `InteractionService` to its app state, and serves the production routes (`test/live_provider/interaction_cli_journey.py:L2128-L2135,L2274-L2312`; `test/live_provider/interaction_api_worker.py:L159-L193`). The provider CLI and its MCP subprocess remain the same invocation; the parent submits human answers through HTTP and checks delivery state (`test/live_provider/interaction_cli_journey.py:L2639-L2709`).
- The existing AG-UI integration case exercises the real production stream handler but uses a synthetic bus and service context; the recovery case creates new service objects in-process rather than replacing an API process. Neither alone proves the requested cross-process composition.
- `get_interaction_service()` prefers a real `InteractionService` stored on the request app state, allowing the owned child worker to instantiate a fresh service over the same configured target without monkeypatch inheritance (`src/cli_agent_orchestrator/api/interactions.py:L198-L210`).

## Impact and boundary decision

The missing proof is a test-harness composition across the real provider/MCP journey, durable interaction store, production AG-UI route, and a replaced API process. Production API route, schema, projection, and lifecycle semantics are already the behavior under test, so the implementation boundary remains `test/live_provider/` plus this feature's acceptance documentation. The test worker will import the production application and route modules in a fresh process; it will not copy or reimplement their handlers. Durable terminal/source registration must be completed before worker startup so the replacement process resolves the same source from PostgreSQL. Human answers remain the existing version-checked HTTP writes; only the provider/MCP process may wait for or acknowledge the answer receipt.

Graphify does not represent the dynamic HTTP call edge or cross-process restart. Those boundaries will be verified by the opt-in acceptance itself and by offline tests for worker command/environment construction, SSE parsing, and owned-process cleanup.

## Post-implementation refresh

- Command: `/home/felni/.local/bin/graphify update . --no-cluster`
- Result: 54,058 nodes and 123,318 edges; raw AST extraction, no clustering or LLM call.
- `graphify-out/graph.json` SHA-256: `7649f37bd7636e355eaa06da29c0e96c4e5c9f3f4cc9da26e67c744a00f00181`.
- New source-backed nodes include `test_live_provider_interaction_api_worker_create_production_app` (`test/live_provider/interaction_api_worker.py:L159`), `test_live_provider_interaction_api_worker_run_worker` (`L178`), `test_live_provider_interaction_agui_restart_apiworkercontroller_restart` (`test/live_provider/interaction_agui_restart.py:L424`), and the extended journey `test_live_provider_interaction_cli_journey_run` (`test/live_provider/interaction_cli_journey.py:L2128`). The graph links the journey to its worker controller and links the worker to the configured PostgreSQL target, repository, and service.
- The path from `test_live_provider_interaction_api_worker_create_production_app` to `src_cli_agent_orchestrator_api_main_agui_stream` remains absent because the worker serves `api.main.app` over HTTP and the request dispatch is not a Python call edge. Source inspection confirms the imported app registers `agui_stream` and `get_interaction_service` resolves the worker's app-scoped service. No product route or projection implementation was changed.
- The worker uses the actual production app and middleware but configures auth-off local mode and binds only to loopback. Production agent routes still validate the per-incarnation interaction capability; human answer routes resolve the local operator principal from the loopback ASGI peer; the AG-UI route uses the production scope authorization helper in local mode. Browser-session login/authentication is explicitly outside this acceptance and is not claimed.
- Review also verified the stream's fleet snapshot uses `session_service.list_sessions()`. The worker sets `TMUX_TMPDIR` to its run-private directory; `BoundedTmuxServer.sessions` issues only `list-sessions`, while `libtmux.Server.__init__` does not start a server. This isolates fleet enumeration from the operator's default tmux socket without creating a tmux server.

## Latest candidate refresh

- Command: `/home/felni/.local/bin/graphify update . --no-cluster`
- Result: 54,061 nodes and 123,324 edges; raw AST extraction, no clustering or LLM call.
- `graphify-out/graph.json` SHA-256: `6d157b47a34c2d86c47349727a3bd91c0f69ecc07520415d71a9f3d27ecd1ba7`.
- This refresh includes the malformed-delta parser regression and its type guard. No production source changed.

## R2 sealed candidate refresh

- Command: `/home/felni/.local/bin/graphify update . --no-cluster`
- Result: 54,203 nodes and 123,764 edges; raw AST extraction, no clustering or LLM call.
- `graphify-out/graph.json` SHA-256: `94e9061613a57d5833a745ad0d11272248ac50ae8e4a9b512c1719b51b873fb8`.
- The candidate now includes the R14-reviewed OpenCode harness files and the two frozen application files recorded in its source manifest. The R14 journey uses the exact main `interaction_cli_journey.py` common harness (`594cbbd7…` in the reviewed source); the candidate's common harness is a three-way union that preserves both the R14 native lifecycle/receipt fields and this feature's AG-UI restart fields. Its SHA-256 is `813f7887ec862da116e37dcdb0732df560357533dcad82807226ad0bd67d8612`.
- Source-backed graph nodes include `test_live_provider_interaction_cli_journey_run` (`test/live_provider/interaction_cli_journey.py:L2199`), `test_live_provider_interaction_api_worker_create_production_app` (`test/live_provider/interaction_api_worker.py:L159`), `test_live_provider_interaction_api_worker_run_worker` (`L178`), `test_live_provider_interaction_agui_restart_apiworkercontroller_restart` (`test/live_provider/interaction_agui_restart.py:L767`), and `src_cli_agent_orchestrator_api_main_agui_stream` (`src/cli_agent_orchestrator/api/main.py:L2734`). The graph records journey-to-worker-controller calls and worker-to-repository/service/configuration references. It does not model the HTTP dispatch from the separately running worker to the registered AG-UI route.
- After this graph build, the three copied R14 OpenCode files were restored byte-for-byte from the reviewed candidate. Their SHA-256 values match the reviewed files exactly (`opencode_interaction_journey.py` `4e2b9df8…`, `test_opencode_native_mini_journey.py` `4f94730f…`, `test_provider_harness_upgrade.py` `4a31f591…`). The only intervening candidate edit was formatting; AST equivalence was checked before restoration (`evidence/opencode-ast-equivalence-r2.log`). No second graph build was needed because the restored ASTs are equivalent to the ASTs Graphify extracted. This records formatting-only parity, not a claim that Graphify ran after those restored bytes.


## Private outbound-tool-offer observer impact (2026-10-16)

### Graphify query and limitation

- Existing graphify-out/graph.json and its manifest are stale relative to the current main harness. The read-only query for the current OpenCode AG-UI journey had no unique node match. This is a graph coverage limitation, not evidence that the source seam is absent.
- No graph refresh was performed. The exact OpenCode upstream source at commit cd9a14a is outside this repository graph.

### Direct primary-source fallback

- packages/plugin/src/promise/session.ts defines SessionHttpRequest with the resolved Model.Ref, request kind, and Web Request, and registers http.request in SessionHooks.
- packages/plugin/src/promise/registration.ts defines ModelHookOptions.providerID and the hook registration callback contract.
- packages/core/src/session/model-request.ts constructs a filtered/shaped tool map from the session tool snapshot, creates the LLM request with that tools list, then triggers the HTTP-request hook immediately before invoking the transport handler. This makes the hook the direct boundary for observing serialized offered tools; the MCP server tools/list and configured agent grants are separate observations.
- packages/web/src/content/docs/plugins.mdx documents project-local JavaScript/TypeScript plugins in .opencode/plugins/ and automatic startup loading. The exact source uses the promise plugin contract in packages/plugin/src/promise/plugin.ts, whose Plugin interface exposes id and setup(context) and permits a cleanup callback. The observer proposal uses this official lifecycle and the session.hook API; it adds no proxy or binary patch.

The R1 private candidate proposed keeping payload work in memory on a bounded clone and persisting only one closed summary; R2 supersedes that aggregate-only persistence with per-request records and a separate global seal. This graph/source review does not establish that OpenCode loads the proposed plugin in the actual operator run, that the current model supports tools, or that a provider request invokes any tool. Offline hook-contract tests and a later separately authorized source-bound diagnostic are required for those claims.

### R2 per-request durability boundary

The R2 clarification changes only the private test-harness evidence lifecycle: each request produces one bounded allowlisted ledger record, and hook disposal is a separate global seal. No production source/dependency edge changes, so the stale graph is not refreshed. Direct source review still supplies the hook and plugin-loading facts above; this graph does not represent the observer's runtime event or prove the plugin will load in an actual CLI run. A valid per-request record is not a CLI-exit, provider-completion, or journey-success claim.


## Private Phase 13 impact: OpenCode model-reference match and outer safe receipt

### Graph query and limits

- Queried the current project Graphify graph for the OpenCode AG-UI observer, safe provider receipt, and operator journey projection. The broad query returned many generic nodes, including the existing OpenCode provider safe-receipt test, but did not provide a unique call path for the observer's `Model.Ref` classification. An exact `safe_receipt_projection()` explain query returned no matching node.
- The graph is not used to infer that the model field or operator edge is absent. No graph refresh/build is required for this private candidate; preserve the current graph and verify the narrow impact directly against source.

### Direct source-verified impact

- The exact upstream v2.0.18 `Model.Ref` schema uses `id`, `providerID`, and optional `variant`; `SessionHttpRequest.model` is a `Model.Ref`; `SessionModelRequest` passes the resolved `model.ref` to the `http.request` hook. R16's JS observer reads `modelID`, so the mismatch check exits before request-body parsing.
- The R16 Node and Python observer tests also construct the event using `modelID`, reproducing the same mistaken shape rather than the upstream contract.
- The inner safe provider projection invokes the canonical Python observer projection for `opencode_http_tool_offer`. The operator's `_safe_journey_summary` enumerates a separate allowlist and currently does not forward that key. The candidate's only operator edge change is to validate/load the same canonical helper from the selected source snapshot and include its safe projection.
- A private candidate must not change application `src/`, prompts, model selection, tool grants, OpenCode input count, restart behavior, MCP observer, or time bounds. An observed request-tool list proves only the pre-transport serialized request and does not establish model acceptance, a tool call, or journey success.


## Phase 14: Shared OpenCode MCP action-normalizer impact (2026-10-16)

### Graph and direct-source findings

- The existing Graphify graph does not isolate the journey/profile/writer/permission-filter path. The private read-only queries returned 779 broad nodes (24 displayed) and 281 broad nodes (23 displayed); `explain` on `_agent_source` resolved an unrelated installer rationale. These are coverage limits, not evidence that the source path is absent. No graph refresh was run.
- Direct source review verified that the preserved R17 `_agent_source()` used underscore-form grants, while `src/cli_agent_orchestrator/providers/opencode_cli.py` uses `write_v2_configuration()`, which invokes `_normalize_mcp_actions()` before writing the private v2 config. The v2.0.18 upstream `McpTool.name()` retains the hyphen in server and tool names, while the permission evaluator matches the serialized action. The harness must emit canonical actions, and the shared writer must preserve those hyphens; both facts are covered by the source-derived regression.
- The change edits existing character classes only. It adds no module, import, dependency edge, or production API. The generated graph is stale for current harness files, and no structural edge change warrants refresh; direct source verification remains authoritative.

### Test and image impact

- The pinned QEMU 91-case T097/T098 lane covers host/Bubblewrap/process ownership and PostgreSQL Work dispatch. The lane's acceptance selector does not exercise the OpenCode v2 configuration writer; it remains bound to its immutable source snapshot. This writer change does not alter the 91-case contract or justify rebuilding/rerunning that QEMU lane.
- Both `docker/personal/Dockerfile` and the EKS node Dockerfile copy `src/` (the latter builds a wheel from it), so a future image build from a ported source tree will contain the updated utility. No Dockerfile, dependency, build argument, default provider, or image has changed or been rebuilt here. Existing image IDs remain immutable and are unaffected.


## Phase 15: restart-gap wait recovery impact

## Graphify impact: restart-gap wait recovery

Status: design reviewed by root and independent ECC; main verification and live acceptance remain pending.

### Existing graph query

Queried the existing `graphify-out/graph.json` only. Its metadata reports build commit `87e2d5f354374ea8acc685275e749992df51cdbd`, 54,428 nodes, and 106,992 links. A focused `source_file` query for:

- `test/live_provider/interaction_cli_journey.py`
- `test/live_provider/test_interaction_agui_restart.py`
- `src/cli_agent_orchestrator/mcp_server/server.py`
- `src/cli_agent_orchestrator/services/interactions/service.py`
- `src/cli_agent_orchestrator/services/interactions/capability.py`

found no graph nodes for the journey prompt, restart prompt test, interaction service, or receipt helper. The graph's MCP `server.py` representation does not include `cao_wait_for_user_answers`, `cao_acknowledge_user_answers`, or `_interaction_tool_error`; no graph call path represents this wait/restart seam. This query result is an evidence limitation, not evidence that the source behavior is absent.

### Source-verified impact

Direct inspection of frozen R17 source confirms the proposed scope touches one opt-in prompt path and its tests:

1. `interaction_cli_journey.py::_prompt(..., recover_api_restart=True)` adds the recovery guidance only on the restart prompt. The normal prompt is unchanged. That flag is used for Codex/Claude restart runs; native OpenCode calls the common prompt without the flag.
2. The MCP server converts `requests.ConnectionError` to a structured `unavailable` tool error and routes waits to the same group's wait endpoint.
3. The interaction service uses stable group identity, deterministic per-answer receipts, and a guarded `accepted → returned_to_mcp` event transition. A repeated ready wait returns the same receipt without duplicating the one-time delivery event.
4. Acknowledgement is a distinct service transition requiring the returned receipt. The harness separately checks exact group/question/version/receipt identity.

Source pins and the actual event chronology are recorded in `evidence.md`. Given the graph omission, the source review—not a projected graph path—defines the impact boundary. No production service, route, database schema, provider, or shared runtime code is proposed for change.


**Main integration (2026-10-16):** The accepted three-file delta is ported after root and independent ECC review. The ordinary prompt was compared byte-for-byte with R17 for ASCII and Unicode operation keys. Main module validation and live recovery are pending; no provider result is inferred from this port.


**Main verification update (2026-10-16):** The Phase 14 and Phase 15 source/test changes are integrated in main. The five OpenCode-related Python modules passed 239 tests, the archive module passed 22, and the remote response-loss, AG-UI restart, and PostgreSQL interaction replay modules passed 45 together; each run had zero skips. The Node 22.22.1 observer suite passed all 17 tests. Black/isort passed on 13 changed Python files, and project composition passed five contracts with zero broken. These checks do not establish live provider acceptance, a fresh Docker image, or a passing full backend suite. Final source/diff review and source binding remain pending.


## Phase 16 impact: opaque receipt ASCII validation

### Graph result and source-backed path

Read-only Graphify queries for `AnswerAck`, `acknowledge_answers`, and the ACK/receipt flow returned unrelated generic nodes or no unique ACK call path in the current generated snapshot. The graph does not establish the boundary, and no graph refresh was run because the repository's generated graph is already dirty and this change adds no structural dependency edge.

Direct source inspection establishes this data path:

1. `services/interactions/capability.py::answer_receipt` emits URL-safe base64 text decoded as ASCII.
2. `services/interactions/models.py::AnswerAck`, `mcp_server/server.py::_InteractionAckInput`, and `server.py::_ReturnedInteractionAnswer` currently accept arbitrary Unicode strings within their length bounds. The HTTP `_AckReceipt` DTO also accepts the string, then the route converts it to `AnswerAck` before calling the service.
3. `services/interactions/service.py::InteractionService.acknowledge_answers` compares the expected receipt with the caller value using `hmac.compare_digest`, then calls `receipt_hash`, which encodes as ASCII.
4. The HTTP ACK route will sanitize domain-model validation failures instead of returning FastAPI's default request-validation body, which can reflect input values. The MCP tool sends the returned receipt back to that route. The API wrapper sanitizes service exceptions; the historical `run-step` traceback lacks enough boundary evidence to prove this was its source.

### Changed composition surface

The change will validate at the domain, HTTP route, and MCP contracts, with a service-side fail-closed guard before transaction entry for bypassed models. It preserves exact receipt equality, the delivery state transition, replay idempotency, and database schema. Unicode prompts, question labels, and free-text answers remain permitted under existing UTF-8 bounds. Offline tests prove each input/output edge and that invalid values never reach persistence or comparison; existing PostgreSQL-backed acknowledgement/replay tests cover the real durable transition.

No new import/dependency, migration, message, event, or process edge is introduced. The live-provider run after a new source freeze remains necessary to attribute the historical Claude exception or claim provider acceptance.

### ACK request-validation privacy follow-up

The review identified a pre-endpoint path that the original source summary did not cover: FastAPI validates `_AckReceipt` and `_AckBody` before calling the endpoint, and its default error detail includes `input`. Direct source review confirmed that a route-specific `APIRoute` handler can close this for every app that includes the interactions router without changing other routes. The final patch imports `APIRoute` and `RequestValidationError` from the existing FastAPI package, wraps only the `acknowledge_interaction_answers` route, and returns the endpoint's fixed invalid problem. The global `env_vars` handler remains unchanged. This clarifies the earlier "no new import" statement: the final route-level privacy guard adds imports from FastAPI but no new package dependency or architecture edge.

The dirty Graphify snapshot still has no unique ACK path, and was not refreshed. Offline tests exercise the actual route in the canonical app and in a separate FastAPI app mounting the router, including short/long receipt, missing field, too many receipts, unrelated-route compatibility, and the absence of receipt echo or service invocation. These direct tests define the transport boundary; they do not replace PostgreSQL replay or live-provider validation.


## Phase 17 impact: outer launcher identity and cleanup

The generated graph is built at commit `87e2d5f354374ea8acc685275e749992df51cdbd`, older than the current controller source, and has no `bounded_process_controller.py` node. The earlier Spec 022 graph analysis covers the real-provider journey, API worker, and dynamic process-cleanup boundary, but does not establish the current portable controller's call graph.

Direct source inspection confirms the affected path: `test/live_provider/bounded_process_controller.py::_execute` starts a new-session child with `Popen`, performs one first identity read before stdout/selector setup, and calls `_stop_owned_tree` from final cleanup. `_stop_owned_tree` owns the verified group-signalling path and the incomplete-identity path. `test/live_provider/test_bounded_process_controller.py` injects fake Popen/identity/kill/wait functions and guards against accidental real process creation.

The correction adds no import, package dependency, product/API edge, or persistence boundary. The source now validates the first parent PID and, when either the launcher or controller identity is unverified, uses only the exact `Popen` handle for bounded direct-child TERM/KILL cleanup; group signaling remains pinned-only and artifacts stay preserved. Fake-only controller tests cover these branches and the reused-PID boundary. No graph rebuild is needed; use the stale-graph limitation and source-backed process path as the impact evidence.
