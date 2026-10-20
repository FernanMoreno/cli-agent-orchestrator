# Inventario estático de superficies declaradas

Generado desde AST el 2026-10-10; no importa ni inicia servidores. Sirve como baseline para planificar cobertura, no como prueba de montaje, autorización o ejecución. Prefijos extraídos del APIRouter; montajes condicionales/dinámicos requieren contraste posterior con OpenAPI y fuente.

## src/cli_agent_orchestrator/api/main.py

[Fuente](../../src/cli_agent_orchestrator/api/main.py)

| Método | Ruta declarada | Función | Línea |
| --- | --- | --- | --- |
| POST | `/work-launches` | `create_work_launch` | 2120 |
| GET | `/.well-known/oauth-protected-resource` | `oauth_protected_resource_metadata` | 2360 |
| GET | `/health` | `health_check` | 2386 |
| POST | `/events/ticket` | `events_ticket` | 2573 |
| POST | `/agui/v1/stream/ticket` | `agui_ticket` | 2610 |
| POST | `/terminals/{terminal_id}/ws/ticket` | `terminal_ticket` | 2618 |
| GET | `/events` | `events_stream` | 2625 |
| GET | `/events/history` | `events_history` | 2681 |
| GET | `/agui/v1/stream` | `agui_stream` | 2721 |
| POST | `/agui/v1/emit_ui` | `agui_emit_ui` | 2970 |
| POST | `/agui/v1/interrupts/{interrupt_id}/resume` | `agui_resume_interrupt` | 3039 |
| POST | `/agui/v1/run` | `agui_run` | 3155 |
| GET | `/agents/profiles` | `list_agent_profiles_endpoint` | 3260 |
| GET | `/agents/profiles/search` | `search_agent_profiles_endpoint` | 3302 |
| GET | `/agents/profiles/templates` | `list_profile_templates_endpoint` | 3326 |
| GET | `/agents/profiles/templates/{category}/{name}/schema` | `get_profile_template_schema_endpoint` | 3346 |
| POST | `/agents/profiles/templates/validate` | `validate_profile_template_config_endpoint` | 3378 |
| POST | `/agents/profiles/templates/preview` | `preview_profile_template_endpoint` | 3400 |
| POST | `/agents/profiles/validate` | `validate_agent_profile_endpoint` | 3423 |
| GET | `/agents/profiles/schema` | `get_agent_profile_schema_endpoint` | 3454 |
| GET | `/agents/profiles/{name}` | `get_agent_profile_endpoint` | 3562 |
| POST | `/agents/profiles/install` | `install_agent_profile_endpoint` | 3584 |
| POST | `/agents/profiles` | `create_agent_profile_endpoint` | 3608 |
| PUT | `/agents/profiles/{name}` | `replace_agent_profile_endpoint` | 3642 |
| DELETE | `/agents/profiles/{name}` | `delete_agent_profile_endpoint` | 3675 |
| GET | `/agents/profiles/{name}/source` | `get_agent_profile_source_endpoint` | 3713 |
| GET | `/agents/providers` | `list_providers_endpoint` | 3752 |
| GET | `/settings/agent-dirs` | `get_agent_dirs_endpoint` | 3777 |
| GET | `/settings/memory` | `get_memory_settings_endpoint` | 3805 |
| POST | `/settings/agent-dirs` | `set_agent_dirs_endpoint` | 3825 |
| GET | `/settings/skill-dirs` | `get_skill_dirs_endpoint` | 3855 |
| POST | `/settings/skill-dirs` | `set_skill_dirs_endpoint` | 3868 |
| GET | `/skills/{name}` | `get_skill_content` | 3889 |
| GET | `/plugins` | `list_agent_plugins` | 4003 |
| POST | `/plugins` | `install_agent_plugin` | 4057 |
| POST | `/plugins/validate` | `validate_agent_plugin` | 4101 |
| DELETE | `/plugins/{name}` | `uninstall_agent_plugin` | 4132 |
| POST | `/sessions` | `create_session` | 4174 |
| GET | `/sessions` | `list_sessions` | 4397 |
| GET | `/sessions/{session_name}` | `get_session` | 4410 |
| DELETE | `/sessions/{session_name}` | `delete_session` | 4438 |
| POST | `/sessions/{session_name}/terminals` | `create_terminal_in_session` | 4497 |
| GET | `/sessions/{session_name}/terminals` | `list_terminals_in_session` | 4670 |
| GET | `/terminals/{terminal_id}` | `get_terminal` | 4701 |
| GET | `/terminals/{terminal_id}/children` | `list_terminal_children` | 4725 |
| GET | `/native-children/{child_id}` | `get_native_child_receipt` | 4742 |
| POST | `/native-children/{child_id}/join` | `join_native_child` | 4754 |
| PATCH | `/terminals/{terminal_id}/group` | `update_terminal_group_endpoint` | 4781 |
| PATCH | `/terminals/{terminal_id}/metadata` | `update_terminal_metadata_endpoint` | 4813 |
| GET | `/terminals/{terminal_id}/siblings` | `list_terminal_siblings` | 4846 |
| GET | `/terminals/{terminal_id}/memory-context` | `get_terminal_memory_context` | 4915 |
| GET | `/terminals/{terminal_id}/working-directory` | `get_terminal_working_directory` | 4942 |
| POST | `/terminals/{terminal_id}/input` | `send_terminal_input` | 4961 |
| POST | `/terminals/{terminal_id}/key` | `send_terminal_key` | 5016 |
| GET | `/terminals/{terminal_id}/turn` | `inspect_terminal_turn` | 5055 |
| POST | `/terminals/{terminal_id}/turn/{action}` | `recover_terminal_turn` | 5070 |
| GET | `/terminals/{terminal_id}/output` | `get_terminal_output` | 5108 |
| GET | `/terminals/{terminal_id}/output/range` | `get_terminal_output_range` | 5144 |
| POST | `/terminals/{terminal_id}/exit` | `exit_terminal` | 5191 |
| POST | `/workflows/validate` | `validate_workflow_endpoint` | 6414 |
| GET | `/workflows` | `list_workflows_endpoint` | 6489 |
| GET | `/workflows/runs` | `list_workflow_runs_endpoint` | 6517 |
| GET | `/workflows/{name}` | `get_workflow_endpoint` | 6564 |
| POST | `/workflows` | `create_workflow_endpoint` | 6595 |
| GET | `/workflows/{name}/source` | `get_workflow_source_endpoint` | 6613 |
| PUT | `/workflows/{name}` | `update_workflow_endpoint` | 6632 |
| DELETE | `/workflows/{name}` | `delete_workflow_endpoint` | 6657 |
| POST | `/workflows/runs/{run_id}/steps/{step_id}/output` | `record_step_output_endpoint` | 6679 |
| POST | `/workflows/plans/approve` | `approve_workflow_plan_endpoint` | 6951 |
| GET | `/workflows/runs/{run_id}/plan` | `get_workflow_run_plan_endpoint` | 7027 |
| POST | `/workflows/runs` | `start_workflow_run_endpoint` | 7070 |
| POST | `/workflows/runs:submit` | `submit_workflow_run_endpoint` | 7216 |
| GET | `/workflows/runs/{run_id}` | `get_workflow_run_endpoint` | 7543 |
| GET | `/workflows/runs/{run_id}/events` | `get_workflow_run_events_endpoint` | 7955 |
| GET | `/workflows/runs/{run_id}/compare` | `compare_workflow_runs_endpoint` | 8164 |
| GET | `/workflows/runs/{run_id}/diagnostics` | `get_workflow_run_diagnostics_endpoint` | 8262 |
| DELETE | `/workflows/runs/{run_id}` | `delete_workflow_run_endpoint` | 8388 |
| GET | `/workflows/runs/{run_id}/result` | `get_workflow_run_result_endpoint` | 8573 |
| POST | `/workflows/runs/{run_id}/cancel` | `cancel_workflow_run_endpoint` | 8661 |
| POST | `/workflows/runs/{run_id}/resume` | `resume_workflow_run_endpoint` | 8752 |
| POST | `/workflows/runs/{run_id}/steps/{step_id}/retry` | `retry_managed_workflow_step_endpoint` | 9018 |
| POST | `/workflows/runs/{run_id}/steps/{step_id}:replay` | `replay_workflow_step_endpoint` | 9311 |
| GET | `/graph/providers` | `list_graph_providers_endpoint` | 9514 |
| GET | `/graph/{provider}` | `get_graph_endpoint` | 9522 |
| POST | `/graph/{provider}/export` | `export_graph_endpoint` | 9578 |
| DELETE | `/terminals/{terminal_id}` | `delete_terminal` | 9698 |
| POST | `/terminals/{receiver_id}/inbox/messages` | `create_inbox_message_endpoint` | 9745 |
| GET | `/terminals/{terminal_id}/inbox/messages` | `get_inbox_messages_endpoint` | 9789 |
| WEBSOCKET | `/terminals/{terminal_id}/ws` | `terminal_ws` | 9855 |
| GET | `/flows` | `list_flows` | 10198 |
| GET | `/flows/{name}` | `get_flow` | 10212 |
| POST | `/flows` | `create_flow` | 10229 |
| DELETE | `/flows/{name}` | `remove_flow` | 10270 |
| POST | `/flows/{name}/enable` | `enable_flow` | 10288 |
| POST | `/flows/{name}/disable` | `disable_flow` | 10306 |
| POST | `/flows/{name}/run` | `run_flow` | 10324 |
| POST | `/internal/memory/store` | `internal_memory_store` | 10431 |
| POST | `/internal/memory/recall` | `internal_memory_recall` | 10461 |
| POST | `/internal/memory/forget` | `internal_memory_forget` | 10483 |
| POST | `/internal/memory/context` | `internal_memory_context` | 10500 |
| GET | `/memory/vault/status` | `vault_status_endpoint` | 10572 |
| GET | `/memory` | `list_memories_endpoint` | 10610 |
| GET | `/memory/export` | `export_memories_endpoint` | 10644 |
| GET | `/memory/relationships` | `list_relationships_endpoint` | 10763 |
| POST | `/memory/relationships` | `create_relationship_endpoint` | 10794 |
| PATCH | `/memory/relationships/{relationship_id}` | `patch_relationship_endpoint` | 10824 |
| POST | `/memory/relationships/{relationship_id}/promote` | `promote_relationship_endpoint` | 10852 |
| POST | `/memory/relationships/{relationship_id}/reject` | `reject_relationship_endpoint` | 10870 |
| DELETE | `/memory/relationships/{relationship_id}` | `delete_relationship_endpoint` | 10886 |
| GET | `/memory/{key}` | `get_memory_endpoint` | 10913 |
| DELETE | `/memory/{key}` | `delete_memory_endpoint` | 10952 |
| DELETE | `/memory` | `clear_memories_endpoint` | 10997 |
| POST | `/outcomes` | `create_outcome_endpoint` | 11098 |
| GET | `/outcomes` | `list_outcomes_endpoint` | 11136 |
| POST | `/workflows/plans:prepare` | `prepare_workflow_plan_endpoint` | 11238 |
| GET | `/workflows/plans/{prepared_id}` | `review_workflow_plan_endpoint` | 11277 |
| GET | `/workflows/{name}/provisions` | `discover_workflow_provisions_endpoint` | 11440 |
| POST | `/workflows/{name}/provisions` | `provision_workflow_seed_endpoint` | 11461 |
| DELETE | `/workflows/plans/{prepared_id}` | `delete_prepared_workflow_plan_endpoint` | 11493 |
| POST | `/terminals/{terminal_id}/assignments` | `submit_ordinary_assignment` | 11515 |
| POST | `/assignments` | `submit_fresh_ordinary_assignment` | 11551 |
| GET | `/assignments/{assignment_id}` | `inspect_ordinary_assignment` | 11580 |
| POST | `/terminals/{terminal_id}/elastic-assignments` | `submit_elastic_assignment` | 11599 |

## src/cli_agent_orchestrator/api/beads_routes.py

[Fuente](../../src/cli_agent_orchestrator/api/beads_routes.py)

| Método | Ruta declarada | Función | Línea |
| --- | --- | --- | --- |
| GET | `/beads/capabilities` | `capabilities` | 83 |
| GET | `/beads/workspaces/{workspace_id}/tasks` | `list_tasks` | 88 |
| GET | `/beads/workspaces/{workspace_id}/tasks/{task_id}` | `get_task` | 117 |
| GET | `/beads/workspaces/{workspace_id}/ready` | `ready` | 139 |
| GET | `/beads/workspaces/{workspace_id}/tasks/{task_id}/comments` | `comments` | 165 |
| POST | `/beads/workspaces/{workspace_id}/mutations` | `mutate` | 178 |
| GET | `/beads/operations/{operation_id}` | `operation` | 193 |
| POST | `/beads/decompose-preview` | `preview` | 205 |
| POST | `/beads/workspaces/{workspace_id}/bulk-create` | `bulk` | 222 |
| GET | `/beads/workspaces/{workspace_id}/epics/{epic_id}` | `epic` | 240 |
| GET | `/beads/workspaces/{workspace_id}/tasks/{task_id}/context` | `context` | 263 |
| POST | `/beads/operations/{operation_id}/reconcile` | `reconcile` | 282 |
| POST | `/beads/workspaces/{workspace_id}/tasks/{task_id}/plans:prepare` | `prepare_work` | 315 |
| GET | `/beads/assignments/{binding_id}` | `work_status` | 340 |
| POST | `/beads/assignments/{binding_id}/start` | `start_work` | 363 |
| POST | `/beads/assignments/{binding_id}/unassign` | `unassign_work` | 394 |
| POST | `/beads/assignments/{binding_id}/close-task` | `close_verified_task` | 415 |

## src/cli_agent_orchestrator/api/browser_auth_routes.py

[Fuente](../../src/cli_agent_orchestrator/api/browser_auth_routes.py)

| Método | Ruta declarada | Función | Línea |
| --- | --- | --- | --- |
| GET | `/auth/config` | `config` | 156 |
| POST | `/auth/setup` | `setup` | 181 |
| POST | `/auth/login` | `login` | 258 |
| GET | `/auth/session` | `session` | 296 |
| POST | `/auth/renew` | `renew` | 306 |
| POST | `/auth/logout` | `logout` | 316 |
| POST | `/auth/logout-all` | `logout_all` | 326 |
| POST | `/auth/password` | `password` | 336 |

## src/cli_agent_orchestrator/api/knowledge_routes.py

[Fuente](../../src/cli_agent_orchestrator/api/knowledge_routes.py)

| Método | Ruta declarada | Función | Línea |
| --- | --- | --- | --- |
| POST | `/v1/knowledge/records/{record_id}/revisions` | `propose` | 250 |
| GET | `/v1/knowledge/records/{record_id}` | `read_current` | 260 |
| GET | `/v1/knowledge/records/{record_id}/revisions/{revision}` | `read_history` | 266 |
| POST | `/v1/knowledge/records/{record_id}/revisions/{revision}/review` | `review` | 278 |
| POST | `/v1/knowledge/records/{record_id}/revisions/{revision}/tombstone` | `tombstone` | 294 |
| GET | `/v1/knowledge/instructions` | `instructions` | 308 |
| GET | `/v1/knowledge/recovery` | `recovery` | 316 |

## src/cli_agent_orchestrator/api/local_coordination_routes.py

[Fuente](../../src/cli_agent_orchestrator/api/local_coordination_routes.py)

| Método | Ruta declarada | Función | Línea |
| --- | --- | --- | --- |
| GET | `/local-coordination/identity` | `local_peer_identity` | 140 |
| GET | `/local-coordination/instances` | `list_local_peer_instances` | 149 |
| POST | `/local-coordination/pairings` | `receive_pairing_invitation` | 155 |
| POST | `/local-coordination/pairings/{challenge_id}/accept` | `accept_pairing_invitation` | 175 |
| GET | `/local-coordination/projects/verify` | `verify_local_project` | 210 |
| POST | `/local-coordination/tasks` | `submit_local_peer_task` | 227 |
| GET | `/local-coordination/tasks/{task_id}` | `inspect_local_peer_task` | 253 |
| POST | `/local-coordination/tasks/{task_id}/cancel` | `cancel_local_peer_task` | 275 |
| DELETE | `/local-coordination/peers/{peer_id}` | `revoke_local_peer` | 298 |
| GET | `/local-coordination/sessions` | `list_local_project_sessions` | 314 |
| GET | `/local-coordination/agent/peers` | `list_agent_local_peers` | 380 |
| GET | `/local-coordination/agent/peers/{peer_id}/sessions` | `inspect_agent_peer_sessions` | 403 |
| POST | `/local-coordination/agent/tasks` | `submit_agent_local_peer_task` | 428 |
| GET | `/local-coordination/agent/tasks/{task_id}` | `inspect_agent_local_peer_task` | 479 |
| POST | `/local-coordination/agent/tasks/{task_id}/cancel` | `cancel_agent_local_peer_task` | 506 |

## src/cli_agent_orchestrator/api/work_coordinator_routes.py

[Fuente](../../src/cli_agent_orchestrator/api/work_coordinator_routes.py)

| Método | Ruta declarada | Función | Línea |
| --- | --- | --- | --- |
| POST | `/ralph/templates` | `template` | 68 |
| POST | `/ralph/plans:prepare` | `prepare` | 94 |
| POST | `/ralph/runs` | `start` | 111 |
| GET | `/ralph/runs/{identity}` | `status` | 121 |
| POST | `/ralph/runs/{identity}/feedback` | `feedback` | 135 |
| POST | `/ralph/runs/{identity}/stop` | `stop` | 151 |
| POST | `/ralph/runs/{identity}/complete` | `complete` | 159 |
| POST | `/ralph/runs/{identity}/resume` | `resume` | 173 |
| POST | `/ralph/runs/{run_id}/context` | `context` | 188 |

## src/cli_agent_orchestrator/api/work_routes.py

[Fuente](../../src/cli_agent_orchestrator/api/work_routes.py)

| Método | Ruta declarada | Función | Línea |
| --- | --- | --- | --- |
| GET | `/work-items/{work_item_id}` | `get_work` | 254 |
| GET | `/work-proxy/effects` | `list_unresolved_proxy_effects` | 260 |
| POST | `/work-proxy/effects/{effect_id}/reconcile` | `reconcile_proxy_effect` | 271 |
| GET | `/work-proxy/issues` | `list_unresolved_proxy_issues` | 284 |
| POST | `/work-proxy/issues/{attempt_id}/{generation}/reconcile` | `reconcile_proxy_issue` | 295 |
| GET | `/jobs/{job_id}/events` | `get_work_events` | 309 |
| POST | `/work-items/{work_item_id}/decisions` | `record_decision` | 320 |
| POST | `/work-decisions/{decision_id}/revoke` | `revoke_decision` | 338 |
| GET | `/work-items/{work_item_id}/operations` | `get_work_operations` | 353 |

## src/cli_agent_orchestrator/runtime_channel/server.py

[Fuente](../../src/cli_agent_orchestrator/runtime_channel/server.py)

| Método | Ruta declarada | Función | Línea |
| --- | --- | --- | --- |
| WEBSOCKET | `/runtime/channel` | `runtime_channel` | 169 |
| GET | `/runtimes` | `list_runtimes` | 268 |
| POST | `/runtimes/{runtime_id}/terminals` | `launch_remote` | 277 |
| GET | `/runtimes/{runtime_id}/operations/{op_id}` | `get_operation` | 347 |
| POST | `/runtimes/{runtime_id}/operations/{op_id}/cancel` | `cancel_operation` | 387 |
| GET | `/runtimes/operations` | `get_runtime_operations` | 410 |

## examples/fleet/panel/app/main.py

[Fuente](../../examples/fleet/panel/app/main.py)

| Método | Ruta declarada | Función | Línea |
| --- | --- | --- | --- |
| GET | `/auth/config` | `auth_config` | 269 |
| GET | `/api/fleet` | `fleet` | 274 |
| POST | `/api/machines/{name}/launch` | `launch` | 312 |
| GET | `/api/machines/{name}/sessions/{session_name}` | `session_detail` | 350 |
| POST | `/api/machines/{name}/sessions/{session_name}/send` | `send` | 362 |
| POST | `/api/machines/{name}/sessions/{session_name}/shutdown` | `shutdown` | 386 |
| GET | `/api/machines/{name}/terminals/{terminal_id}/output` | `terminal_output` | 398 |
| GET | `/api/machines/{name}/terminals/{terminal_id}/screen` | `terminal_screen` | 410 |
| POST | `/api/machines/{name}/terminals/{terminal_id}/key` | `terminal_key` | 428 |
| POST | `/api/machines/{name}/terminals/{terminal_id}/input` | `terminal_input` | 443 |
| GET | `/api/machines/{name}/providers` | `machine_providers` | 458 |
| GET | `/api/machines/{name}/profiles` | `machine_profiles` | 469 |
| GET | `/api/machines/{name}/terminals/{terminal_id}/working-directory` | `terminal_wd` | 480 |
| API_ROUTE | `/nodes/{name}/{path:path}` | `node_proxy` | 504 |
| WEBSOCKET | `/nodes/{name}/terminals/{terminal_id}/ws` | `node_terminal_ws` | 634 |
| GET | `/` | `index` | 753 |
| GET | `/{filename}` | `root_asset` | 758 |

## Módulos CLI

Inventario por archivo; los subcomandos se deben detallar y validar en plan. No se considera que un módulo entero sea una sola operación.

- [agent](../../src/cli_agent_orchestrator/cli/commands/agent.py)
- [agent_plugin](../../src/cli_agent_orchestrator/cli/commands/agent_plugin.py)
- [config](../../src/cli_agent_orchestrator/cli/commands/config.py)
- [env](../../src/cli_agent_orchestrator/cli/commands/env.py)
- [fleet](../../src/cli_agent_orchestrator/cli/commands/fleet.py)
- [info](../../src/cli_agent_orchestrator/cli/commands/info.py)
- [init](../../src/cli_agent_orchestrator/cli/commands/init.py)
- [install](../../src/cli_agent_orchestrator/cli/commands/install.py)
- [launch](../../src/cli_agent_orchestrator/cli/commands/launch.py)
- [mcp_server](../../src/cli_agent_orchestrator/cli/commands/mcp_server.py)
- [memory](../../src/cli_agent_orchestrator/cli/commands/memory.py)
- [peer](../../src/cli_agent_orchestrator/cli/commands/peer.py)
- [profile](../../src/cli_agent_orchestrator/cli/commands/profile.py)
- [ralph](../../src/cli_agent_orchestrator/cli/commands/ralph.py)
- [schedule](../../src/cli_agent_orchestrator/cli/commands/schedule.py)
- [session](../../src/cli_agent_orchestrator/cli/commands/session.py)
- [shutdown](../../src/cli_agent_orchestrator/cli/commands/shutdown.py)
- [skills](../../src/cli_agent_orchestrator/cli/commands/skills.py)
- [tasks](../../src/cli_agent_orchestrator/cli/commands/tasks.py)
- [terminal](../../src/cli_agent_orchestrator/cli/commands/terminal.py)
- [tui](../../src/cli_agent_orchestrator/cli/commands/tui.py)
- [update](../../src/cli_agent_orchestrator/cli/commands/update.py)
- [worker](../../src/cli_agent_orchestrator/cli/commands/worker.py)
- [workflow](../../src/cli_agent_orchestrator/cli/commands/workflow.py)

## Herramientas de agente

[MCP de coordinación](../../src/cli_agent_orchestrator/mcp_server/server.py) y [control planes](../../docs/control-planes.md): requieren desglosar herramientas por contrato y autoridad. No se presupone que las herramientas de agente se puedan llamar como acciones humanas.

Total de declaraciones de rutas recogidas: **211**.
