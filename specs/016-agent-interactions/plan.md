# Implementation Plan: Preguntas e interacciones de agentes

**Branch**: main (the user's implementation authorization does not authorize a branch change)  
**Date**: 2026-10-10  
**Spec**: specs/016-agent-interactions/spec.md

## Summary

Build one durable interaction ledger shared by the CAO web inbox, agent MCP tools, provider observations, and workflow wait handling. Human actions use the existing sealed Principal; agent actions use a server-issued capability bound to one terminal incarnation and checked against current profile/tool grants. Explicit questions return through the originating MCP call so the same running agent task receives the answer. Native dialogs are actionable only when a provider proves active identity, current options, and a safe answer path; otherwise CAO shows a manual-only notice. An interaction answer never grants Work authority.

## Technical Context

**Language/Version**: Python version from pyproject.toml; TypeScript/React version from web package manifest.  
**Primary Dependencies**: FastAPI, Pydantic, psycopg/PostgreSQL (Spec021), existing CAO provider/MCP abstractions, React and existing web API/auth helpers.  
**Storage**: The configured native PostgreSQL main target. The interaction_* tables have an independently versioned component migration ledger and share the Work target and caller-owned transaction; they are not a second database authority. Historical SQLite artifacts are import-only under Spec021.  
**Testing**: pytest for backend, MCP, persistence and runtime; existing web test runner for React/API. Add provider-evaluation evidence for Codex, Claude Code, OpenCode and Hermes; unsupported native cases are recorded as manual-only.  
**Target Platform**: CAO server and local/remote runtimes currently supported by the repository; browser UI.  
**Project Type**: Python API/CLI and React web application.  
**Performance Goals**: At the spec's controlled scale (20 agents, 100 active requests), p95 publish-to-inbox <=2s; no accepted request silently lost. MCP wait calls are bounded to <=30s each.  
**Constraints**: No raw transcript storage; no secret collection; no generic terminal input for Work-owned targets; no local fallback for remote workers; no automatic resend after uncertain effect; no new agent tool permission beyond the bundled CAO MCP capability; wait time does not extend Work leases or grants.  
**Scale/Scope**: 8 user stories, 42 functional requirements, 100 active questions, up to 20 questions per group, 32 options per question, 16 KiB prompt, 1 KiB option label, 4 KiB answer. Limits are server enforced and saturation is visible.

## Evidence and architecture

The design is based on the inspected source seams in research.md:

- WorkRepository.transaction() owns one native PostgreSQL transaction. InteractionRepository shares the exact logical target identity and accepts that caller-owned connection for mutations that also check WorkAuthority. Work and interaction component schemas are verified before operations; runtime credentials do not perform migrations.
- security.auth.Principal is sealed. Browser/JWT/local-loopback identity is obtained from existing auth dependencies. actor_kind, sender_id, terminal_id, or other request-body fields never prove identity. Local loopback mode remains the existing shared local-operator trust boundary and cannot claim a named human.
- Agent identity is a random capability issued by CAO, stored hashed, bound to terminal ID and session incarnation, revoked on terminal replacement/permission change, and passed to only the bundled cao-mcp-server entry as a private capability-file path. The raw capability is never put in argv, profile configuration, general MCP environment, or events. MCP server continues to use HTTP and does not access database/tmux directly.
- A terminal's content owner is bound from a verified Principal at authenticated creation. A child may inherit that owner only through a server-validated parent/assignment relation. Work questions use the owning Work principal/grant. An unowned source is metadata-only until an authorized operator explicitly designates a content owner; cao:read alone is not a content grant. Shared local-loopback identity has the documented human-versus-process trust limitation.
- Supervisor access across sources is resolved from a server-recorded delegation/assignment or Work relationship at every read/answer. A model cannot nominate a target terminal or recipient to create that relationship.
- Every actionable question binds to source terminal, session incarnation and an interaction-owned durable turn generation. New task delivery and direct terminal input advance that generation; MCP answer return/ack does not. Existing terminal_turn_receipts are supplemental evidence, not a universal dialog identity.
- Explicit answer delivery is pull-based: the source agent retrieves the accepted typed answer as the result of its still-running MCP task. The receipt is durable before return; re-reading returns the same receipt; a separate acknowledgement records agent observation. There is no PTY write. Each wait call is bounded to 30 seconds and the question has a finite wall-clock expiry.
- Native observations are provider-owned structured evidence. No prose regex creates an actionable request. A response is enabled only with a stable dialog fingerprint/version, observed live options and demonstrated provider-specific effect. Unknown, unsafe, credential, or unsupported dialogs are manual-only. OpenCode profile tool restrictions remain intact.
- Work approvals remain in WorkDecisions and its exact attempt/generation/contract/grant checks. Managed Work ordinary input remains denied unless a specific exact capability is declared. Work V2 isolated process workers remain unsupported for this feature because their contract allows only task_received and submit_result. No Work permission, isolation, or lease is broadened.

## Constitution Check

### Before design

- I. Evidence before conclusions: PASS. The spec, research and composition review were read; repository seams were inspected. Provider claims are limited to evidenced capabilities.
- II. Preserve work and scope: PASS. Dirty Graphify output and untracked audit/spec artifacts were inspected and are outside this plan's edit set. No branch/commit/push is authorized.
- III. Design before implementation: PASS. This plan, data model, contracts, quickstart and tasks precede source changes. Graphify impact review is a parent-owned gate before implementation.
- IV. Composition verification: PASS WITH REQUIRED FUTURE EVIDENCE. Tasks include persistence, auth, MCP, runtime, workflow, frontend and integration tests plus the composition gate after implementation.
- V. Honest closure and durable knowledge: PASS. This plan records limits and supports a final diff/evidence review; only durable design decisions are appended to research.md.

### After design

PASS. No new external service or framework is introduced. The interaction ledger is a new cohesive subsystem with one authority; it reuses verified human auth and Work authority and preserves runtime isolation. Tests and composition review are assigned to the implementation plan. Native provider response remains manual-only where no safe version-specific interaction contract is proven; the UI must say that explicitly.

## Project Structure

### Feature documents

specs/016-agent-interactions/plan.md  
specs/016-agent-interactions/research.md  
specs/016-agent-interactions/data-model.md  
specs/016-agent-interactions/contracts/http-api.yaml  
specs/016-agent-interactions/contracts/mcp-tools.md  
specs/016-agent-interactions/contracts/runtime-events.md  
specs/016-agent-interactions/quickstart.md  
specs/016-agent-interactions/tasks.md

### Production modules

- Ledger owner: src/cli_agent_orchestrator/clients/interaction_repository.py and src/cli_agent_orchestrator/services/interactions/{models.py,service.py,capability.py,policy.py}.
- API/MCP/auth owner: src/cli_agent_orchestrator/api/interactions.py, api/main.py route registration and authenticated terminal-creation context, mcp_server/server.py, mcp_server/utils.py, and the narrow interaction capability-file reader there. Existing security.auth.py remains the authority; use its sealed Principal/current auth exports and add a narrow route guard in api/interactions.py for service-bearer rejection.
- Runtime owner: src/cli_agent_orchestrator/providers/base.py, providers/mcp_interactions.py, all provider-specific bundled CAO MCP config writers, services/terminal_service.py, services/status_monitor.py, services/agent_step.py, services/assignment_service.py, services/work_agent_step.py only for explicit capability rejection/binding, and services/inbox_service.py. This owner handles provider credential-file path propagation, source generation, native observation/manual notices, assignment/direct-input freshness, inbox guard, and wait budget.
- Frontend owner: web/src/api.ts, web/src/App.tsx, web/src/components/QuestionsPanel.tsx, web/src/components/AgentPanel.tsx only for exact-terminal manual navigation, and feature-owned styles/tests.
- Test ownership follows production ownership. Ledger tests live under test/clients/ and test/services/interactions/; transport tests under test/api/ and test/mcp_server/; runtime tests under test/providers/ and test/services/; web tests remain under web/src or web/tests according to the current runner; composed security/journey tests live under test/integration/ with the integration lead.

No production file is shared between owners except integration points explicitly sequenced in tasks: api/main.py is transport-owned; terminal_service.py/provider writers are runtime-owned; the public contracts in this directory are the shared source of truth.

## Decisions and interfaces

1. After Spec021, ledger mutations use InteractionRepository methods with a caller-owned native PostgreSQL WorkRepository connection. The same transaction validates question CAS/idempotency, source incarnation/generation, content grant, and any Work grant/contract before it records an accepted response or delivery claim. Admission serializes the global capacity invariant with a transaction advisory lock and retains per-source row locks; protected reads use one repeatable-read snapshot. The original SQLite `BEGIN IMMEDIATE` design is superseded by Spec021.
2. The service API is InteractionService.create_group(source_actor, request, operation_key), list_metadata(principal_or_agent, filters), get_detail(actor, question_id, expected_version), accept_answer(human_principal, question_id, version, idempotency_key, response), get_agent_result(agent_capability, question_id), acknowledge_result(agent_capability, receipt), request_escalation(actor, question_id, reason), cancel(principal, question_id, version), reconcile(principal, question_id, version, evidence), observe_native(source_actor, observation), list_history(actor, question_id), get_wait_graph(actor, scope), get_capabilities(actor, provider/version/modality). Actor objects are constructed server-side from Principal or capability; not from DTO fields. The group operation key is scoped to source incarnation and turn and is sent as the `Idempotency-Key` header; a replay with the same canonical request returns the same group.
3. A response is an immutable answer per question version. Same key + same canonical payload returns the stored result; the same key with different content conflicts; another key with identical response returns the existing effective answer; a divergent response conflicts. Answers and safe events are separate, and general events never contain prompt/answer content.
4. Explicit source retrieval uses answer states accepted -> returned_to_mcp -> agent_acknowledged. Native delivery uses prepared -> claimed -> confirmed/accepted or uncertain/no_effect/reconciled. If effect may have occurred, recovery never resends automatically. A known no-effect proof is required before an operator can authorize another attempt.
5. Lifecycle state (open, answered, expired, cancelled, stale, manual_only, reconciled_external) is separate from delivery/observation state. A native manual-only item can be visible and linked to the exact terminal without an answer form.
6. Required groups wait for their declared required question IDs; optional answers do not pause work. The agent step pauses only its active execution-time counter while a required question is pending or its accepted answer has not been returned to the originating MCP result, preserves the same task and verified result proof, and continues independent work. Human expiry is a separate wall-clock budget. Work lease/grant expiry still blocks continuation.
7. The UI lists safe metadata, then fetches protected detail on selection. It sends expected_version and one idempotency key per user intent. Conflicts/uncertainty cause refresh and explicit reconciliation; the UI never automatically repeats an uncertain effect.

## Initial support matrix

| Runtime | Explicit channel | Native detection/response in first release |
| --- | --- | --- |
| Codex CLI, Claude Code, OpenCode with CAO MCP profile permission and current installed source capability | Supported through grouped ask and bounded same-task wait/result tools | Manual-only unless a tested provider/version observation has stable active identity/options and an evidenced safe response adapter. |
| Hermes local | Unsupported explicit channel in the current implementation: no proven safe per-launch overlay; manual terminal inspection | No native response adapter; the installed launcher cannot run. |
| Work V1 terminal | Only when exact active attempt and effective contract grant the CAO MCP capability | Provider/version rule above; every response/result revalidates attempt, generation, contract, grant and exact terminal. |
| Work V2 process, worker without CAO MCP, disallowed profile or unverified remote placement | Unsupported; visible limitation/reconciliation state | No terminal fallback, tool-permission expansion or direct process networking. |

FR-038 and SC-007 remain acceptance gates; executing the provider evaluation matrix in quickstart; each named provider must pass one real native-dialog journey or have a recorded manual-only limitation before release. Nothing in status text alone enables an answer.

## FR coverage

| Requirement | Design element and implementation tasks |
| --- | --- |
| FR-001 | Versioned source/question model and durable records: T005–T008, T019. |
| FR-002 | Typed answer/options validation and receipt: T005, T015, T019. |
| FR-003 | Explicit MCP questions independent of native support: T021–T022. |
| FR-004 | Stable group IDs and required-question set: T019, T021–T022. |
| FR-005 | Evidence-based active native observation: T032–T033. |
| FR-006 | Prose is never an actionable native request: T032–T033. |
| FR-007 | Manual-only notice and exact terminal action: T033–T034. |
| FR-008 | Separate lifecycle, auth/quota/error/uncertainty states: T019, T033, T045. |
| FR-009 | Server-derived recipient/authority policy: T027. |
| FR-010 | Supervisor resolves only delegated clarifications: T027–T030. |
| FR-011 | Escalation reason and retained route history: T027–T029. |
| FR-012 | Unassigned questions stay pending; no default answer: T019, T027. |
| FR-013 | Revalidate Principal/capability/Work authority at read, accept and effect: T007, T028, T057. |
| FR-014 | Interaction approval never authorizes a Work effect: T027, T057. |
| FR-015 | Questions tab, filters, protected detail and typed forms: T020, T023–T024. |
| FR-016 | Separate accepted/returned/acknowledged/confirmed/uncertain status: T023–T024, T045, T047. |
| FR-017 | Supervisor state view and replayable snapshots: T028–T029, T051–T052. |
| FR-018 | Metadata-only events and resource content ACL: T007, T020, T056–T059. |
| FR-019 | Bounded safe presentation and private credential/manual route: T032–T034. |
| FR-020 | Wait only on dependent required group: T037–T038. |
| FR-021 | Separate active, human-expiry and Work lease budgets: T037–T038. |
| FR-022 | Return answer to same live task, no initial-task replay: T022, T038. |
| FR-023 | Prove not-delivered before one initial task delivery, otherwise reconcile: T038, T050–T051. |
| FR-024 | Expiry/cancel/replacement makes late replies stale: T037, T040, T050. |
| FR-025 | Preserve verified result separately from later question: T038, T051. |
| FR-026 | Required-question guard on eager inbox and generic input: T039, T062. |
| FR-027 | Version, incarnation, source-turn and Work-binding check before result/effect: T044, T050, T056. |
| FR-028 | One effective answer per question/version and idempotent retry: T044. |
| FR-029 | Use only exact supported delivery, no generic keys: T032–T033, T046. |
| FR-030 | Durable claim, uncertain/no-effect reconciliation, no automatic resend: T045–T048. |
| FR-031 | Restart recovery with stale-source validation: T006, T051–T053. |
| FR-032 | Fingerprint/version prevents redraw duplicates and cross-turn conflation: T032–T033, T050. |
| FR-033 | Direct terminal closure is reconciled without invented content: T050–T051. |
| FR-034 | Append-only attributable history with scoped access: T006, T051–T052. |
| FR-035 | Visible capacity/content limits without eviction: T005–T006, T019. |
| FR-036 | Wait graph, cycle hint and explicit operator cancel: T037, T040–T042. |
| FR-037 | Provider/version/modality capability endpoint: T061. |
| FR-038 | Real provider check or recorded manual limitation: T033, T035, T066. |
| FR-039 | Existing profile/tool restrictions stay unchanged: T011, T033, T060–T062. |
| FR-040 | Legacy routes cannot bypass ledger; optional views project common state: T062, T064–T065. |
| FR-041 | Exact Work/remote identity and no process/local fallback: T010–T011, T056–T058. |
| FR-042 | Main web inbox independent of optional AG-UI: T024, T064, T066. |

## Complexity Tracking

No constitution violation is proposed. A separate interaction namespace is necessary because ordinary answers and Work approvals have different lifecycles; keeping them in separate tables avoids widening Work's authority model. The small provider-neutral observation interface is necessary because coarse TerminalStatus cannot identify a dialog. No new framework or external service is introduced.

## Continuación autorizada: AG-UI y aceptación Docker/host

El usuario pide cerrar T064 y ejecutar las omisiones Docker/host de la ronda anterior. Es trabajo significativo por las fronteras ledger → proyección/stream → autorización y por las dependencias reales de aislamiento. Se conserva el resto de la implementación y las aceptaciones externas de proveedores.

- AG-UI proyecta exclusivamente metadatos y los recibos autoritativos del ledger, diferenciando accepted, returned_to_mcp, agent_acknowledged y uncertain. No copia prompts, opciones, respuestas, transcripciones, secretos ni bindings privados. La proyección no escribe respuestas ni concede autoridad.
- Se mantienen el flag default-off, la validación/revocación de autorización durante streams, la bandeja principal independiente y los cursores existentes. Se prueba conexión, actualización sin eventos fleet, reconexión, paginación/límite explícito, fallo del lector y estados terminales.
- Ledger sigue siendo fuente de verdad; no dependerá de AG-UI. Adaptador/lector de proyección y cableado se revisan contra source antes de implementar.
- Docker: daemon local disponible en nueva sesión, Linux amd64. Ejecutar wrappers T019/T020, setup local, browser auth y aplicación personal sobre imágenes derivadas de la fuente actual. Preservar contenedores/imágenes ajenos; las referencias usadas por tests son inmutables.
- Host: usar guest QEMU desechable con imagen/fuentes verificadas y el checkout vigente; cambios de AppArmor, broker y sysctl quedan dentro del guest. No fingir aprobación del host WSL ni ejecutar esos cambios sobre el host real. Las pruebas scratch requieren el binario aceptado exacto.
- Decisión de conocimiento: contratos/resultado durable en esta spec; no copiar hechos de Graphify ni logs al vault. TDD para proyección y regressiones descubiertas. Verificación final: suites afectadas completas, checks de calidad/arquitectura/composición, review de diff/Graphify y evidencia real de contenedores/guest.

### Corrección de cursor descubierta por suite completa

La paginación ordena/compara el timestamp REAL original SQLite y question_id. No debe reconstruir la frontera desde datetime redondeado ni serializar REAL con sólo seis decimales. Conservar timestamp/ID de la fila autorizada original y codificar float con representación de round-trip; decoder conserva compatibilidad con cursores existentes. Aplicar la misma invariante a la página metadata compartida por REST/AG-UI y agent_list. Antes de implementar: reproducir determinísticamente fracciones que redondean arriba/abajo, verificar dos páginas sin duplicados/omisiones y round-trip exacto, con asserts conservadas. Graphify/source impacto realizado; no migración o cambio de autoridad.
