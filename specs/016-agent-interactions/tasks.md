# Tasks: Preguntas e interacciones de agentes

**Input**: specs/016-agent-interactions/spec.md and plan.md  
**Prerequisites**: plan.md, data-model.md, contracts/, research.md, quickstart.md  
**Evaluation**: The user requested continuous evaluation. Add tests before each behavior, run targeted tests after each story, then run quickstart, full applicable suites, composition review and final diff inspection. Do not treat provider mocks as real-provider evidence.

## Ownership and file boundaries

- Ledger team owns clients/interaction_repository.py and services/interactions/*.py.
- Transport team owns api/interactions.py, api/main.py, mcp_server/server.py and mcp_server/utils.py. It uses existing security.auth Principal seams; do not introduce an identity from request fields.
- Runtime team owns services/terminal_service.py, services/agent_step.py, services/assignment_service.py, services/inbox_service.py, services/status_monitor.py, services/work_agent_step.py, providers/base.py, providers/mcp_interactions.py and provider MCP config writers.
- Ledger owns repository/service tests under test/clients/ and test/services/interactions/.
- Transport owns API/MCP tests under test/api/ and test/mcp_server/. Runtime owns provider, wait, inbox and legacy/runtime tests under test/providers/ and test/services/. Web owns web/src/** feature code and web test files. The integration lead owns cross-subsystem tests under test/integration/ and security-composition tests.
- Feature docs are owned by the integration lead. Do not edit pre-existing Graphify/audit artifacts. No ordinary-input or MCP change may weaken Work isolation.

## Phase 1: Setup

**Purpose**: Add isolated test seams that later stories can use. No production behavior changes here.

- [x] T001 [P] Create shared ledger test factories for source, Principal, Work binding and interaction fixtures in test/services/interactions/test_service.py and test/integration/test_interaction_work_authority.py (inline isolated factories)
- [x] T002 [P] Create isolated MCP capability and HTTP stub fixtures in test/mcp_server/test_interaction_tools.py and test/integration/test_interaction_stdio.py (inline isolated fixtures)
- [x] T003 [P] Create Questions panel mock API fixture and node-switch test helper in web/src/test/interactions.ts
- [x] T004 [P] Create provider observation fake and effect recorder in test/providers/test_interactions.py (inline fake and recorder)

## Phase 2: Foundational contracts and persistence

**Purpose**: Establish types, durable source identity and transaction rules before story implementation.

- [x] T005 Create frozen Pydantic models/enums for source, group, question, answer, delivery, wait and event in src/cli_agent_orchestrator/services/interactions/models.py
- [x] T006 Add independently versioned interaction_* schema, transaction-connection repository methods, CAS constraints and append-only event storage in src/cli_agent_orchestrator/clients/interaction_repository.py; leave Work migrations/checksums unchanged
- [x] T007 Implement transaction-scoped source capability validation, resource content ACL and server-derived parent/Work relationship policy in src/cli_agent_orchestrator/services/interactions/capability.py and src/cli_agent_orchestrator/services/interactions/policy.py
- [x] T008 Implement InteractionService base operations and connection-passing seam to WorkRepository/WorkAuthority in src/cli_agent_orchestrator/services/interactions/service.py
- [x] T009 Initialize the interaction schema at API startup without folding interaction_* tables into Work migration verification in src/cli_agent_orchestrator/api/main.py
- [x] T010 Runtime: persist service-issued capability material and propagate a private capability-file path through terminal_service and BaseProvider in src/cli_agent_orchestrator/services/terminal_service.py and src/cli_agent_orchestrator/providers/base.py
- [ ] T011 Runtime: overlay the protected capability-file path only on the bundled cao-mcp-server configuration entry in src/cli_agent_orchestrator/providers/mcp_interactions.py, src/cli_agent_orchestrator/providers/codex.py, src/cli_agent_orchestrator/providers/claude_code.py, src/cli_agent_orchestrator/providers/opencode_cli.py, src/cli_agent_orchestrator/providers/hermes.py, src/cli_agent_orchestrator/providers/gemini_cli.py, src/cli_agent_orchestrator/providers/copilot_cli.py, src/cli_agent_orchestrator/providers/cursor_cli.py, src/cli_agent_orchestrator/providers/omp.py, src/cli_agent_orchestrator/providers/kimi_cli.py, src/cli_agent_orchestrator/providers/grok_cli.py, src/cli_agent_orchestrator/providers/minimax_code.py and src/cli_agent_orchestrator/providers/antigravity_cli.py
- [x] T012 Transport: read the private capability file and attach its value only as X-CAO-Interaction-Capability on interaction HTTP calls in src/cli_agent_orchestrator/mcp_server/utils.py
- [x] T013 [P] Verify independent schema bootstrap, transaction rollback, constraints and Work schema-history preservation in test/clients/test_interaction_repository.py
- [x] T014 [P] Verify Principal/capability separation, revocation, owner ACL and current-source binding in test/services/interactions/test_service.py and test/integration/test_interaction_security.py

## Phase 3: User Story 1 — Ver y contestar preguntas desde CAO (Priority: P1)

**Goal**: An authorized human answers a typed explicit question and the same active source task receives the answer through MCP.

**Independent test**: Create a group from an authorized source, answer its required text and choice questions in the web UI, and receive those answers from the same MCP task without PTY input or a duplicate initial task.

- [x] T015 [P] [US1] Ledger: test group limits, typed validation, list/detail redaction and answer idempotency in test/services/interactions/test_service.py
- [x] T016 [P] [US1] Transport: test human metadata/detail/answer routes and reject body-supplied actor identity in test/api/test_interactions_transport.py
- [x] T017 [P] [US1] Transport: test MCP ask/wait/ack source binding and capability authentication in test/mcp_server/test_interaction_tools.py
- [x] T018 [P] [US1] Web: test text, single choice, multi choice, free-text alternative, pending and conflict UI states in web/src/test/questions-panel.test.tsx
- [x] T019 [US1] Implement create_group, list_metadata, get_detail and accept_answer with required/optional group rules and bounded content in src/cli_agent_orchestrator/services/interactions/service.py
- [x] T020 [US1] Add human metadata list, protected detail and versioned/idempotent answer routes in src/cli_agent_orchestrator/api/interactions.py
- [x] T021 [US1] Add capability-authenticated create-group, bounded wait and answer-ack routes in src/cli_agent_orchestrator/api/interactions.py and register the router in src/cli_agent_orchestrator/api/main.py
- [x] T022 [US1] Implement cao_ask_user, cao_wait_for_user_answers and cao_acknowledge_user_answers using the interaction HTTP helper in src/cli_agent_orchestrator/mcp_server/server.py
- [x] T023 [US1] Add typed interaction API methods with If-Match and one idempotency key per user intent in web/src/api.ts
- [x] T024 [US1] Add the standalone Questions tab and typed answer panel while preserving existing Alt+N tab ordering in web/src/components/QuestionsPanel.tsx and web/src/App.tsx
- [x] T025 [US1] Prove one explicit same-task answer journey, duplicate retry and optional-question nonblocking behavior in test/integration/test_interaction_explicit_flow.py

## Phase 4: User Story 2 — Resolver en el supervisor o escalar al usuario (Priority: P1)

**Goal**: A supervisor resolves only delegated clarifications within current authority; human-required questions reach the recorded owner.

**Independent test**: A server-derived supervisor relation can list/read/answer an allowed clarification; an unrelated capability and a supervisor attempting an approval are denied; escalation retains reason/history.

- [x] T026 [P] [US2] Ledger: test supervisor authority, cross-source delegation, escalation and forced human-required routing in test/services/interactions/test_service.py and test/integration/test_interaction_delegation.py
- [x] T027 [US2] Derive recipients and cross-source read/answer permissions from live server assignment/Work relationships in src/cli_agent_orchestrator/services/interactions/policy.py and src/cli_agent_orchestrator/services/interactions/service.py
- [x] T028 [US2] Add supervisor-scoped list/detail/answer/escalation routes with current relationship checks in src/cli_agent_orchestrator/api/interactions.py
- [x] T029 [US2] Add cao_list_interactions, cao_get_interaction, cao_answer_interaction and cao_escalate_interaction tools without caller-selected targets in src/cli_agent_orchestrator/mcp_server/server.py
- [x] T030 [US2] Transport: test unrelated-source denial, delegation revocation and human-only approval behavior across MCP/API in test/mcp_server/test_interaction_tools.py and test/integration/test_interaction_delegation.py

## Phase 5: User Story 3 — Atender diálogos nativos con sus opciones reales (Priority: P1)

**Goal**: Publish provider-owned evidence for a live dialog; unsupported/unknown/credential dialogs remain clearly manual and non-answerable.

**Independent test**: A structured fake observation creates a versioned request only with a live fingerprint and real options; plain prose, unknown dialogs and credentials never create an answerable form.

- [x] T031 [P] [US3] Runtime: test provider observation schema, fingerprint changes, unknown dialog and credential redaction in test/providers/test_interactions.py
- [x] T032 [US3] Add bounded NativeInteractionObservation and independent detect/respond capability fields in src/cli_agent_orchestrator/providers/base.py and src/cli_agent_orchestrator/providers/mcp_interactions.py
- [x] T033 [US3] Implement only evidence-backed active-dialog/manual-only observations for Codex, Claude Code, OpenCode and Hermes in src/cli_agent_orchestrator/providers/codex.py, src/cli_agent_orchestrator/providers/claude_code.py, src/cli_agent_orchestrator/providers/opencode_cli.py and src/cli_agent_orchestrator/providers/hermes.py
- [x] T034 [US3] Add native manual-only/credential notice rendering and an exact-terminal action in web/src/components/QuestionsPanel.tsx and web/src/components/AgentPanel.tsx
- [x] T035 [US3] Integration (Runtime evaluation): run one real native-dialog evaluation per named provider or record the exact manual-only limitation by provider/version in specs/016-agent-interactions/evaluation.md

## Phase 6: User Story 4 — Mantener la tarea mientras espera respuesta (Priority: P1)

**Goal**: Pause only required dependent work, separate active execution time from human expiry, preserve the same task/result, and show cycles.

**Independent test**: A waiting required question pauses one branch's active-time budget while a sibling proceeds; answering resumes the same attempt once; expiry/lease loss blocks continuation.

- [x] T036 [P] [US4] Runtime: test wait budget, verified result preservation, sibling progress, expiry and cycle handling in test/services/test_agent_step.py and test/integration/test_interaction_wait_composition.py
- [x] T037 [US4] Persist required wait edges, deadline state, group completion and cycle graph queries in src/cli_agent_orchestrator/clients/interaction_repository.py and src/cli_agent_orchestrator/services/interactions/service.py
- [x] T038 [US4] Pause/resume agent_step active-time accounting around the exact required interaction wait without extending Work leases or replaying initial task delivery in src/cli_agent_orchestrator/services/agent_step.py
- [x] T039 [US4] Runtime: block eager inbox, assignment and generic input from satisfying a pending required question in src/cli_agent_orchestrator/services/inbox_service.py, src/cli_agent_orchestrator/services/assignment_service.py and src/cli_agent_orchestrator/services/terminal_service.py
- [x] T040 [US4] Add authorized wait-graph and cancellation routes in src/cli_agent_orchestrator/api/interactions.py
- [x] T041 [US4] Render dependency participants, cycle hints and explicit cancel action in web/src/components/QuestionsPanel.tsx and web/src/api.ts
- [x] T042 [US4] Test true InboxService plus terminal-guard composition, cycle cancellation and independent task progress in test/integration/test_interaction_wait_composition.py

## Phase 7: User Story 5 — Responder sin duplicados ni decisiones obsoletas (Priority: P1)

**Goal**: Accept one answer per question version, revalidate source before result/effect, and preserve uncertainty without replaying a possible effect.

**Independent test**: Concurrent identical answers resolve to one receipt; divergent/stale answers conflict; crash after claim produces uncertain state and no automatic resend.

- [x] T043 [P] [US5] Ledger: test concurrent identical/divergent responses, stale turn/version, claim crash and partial effect state in test/services/interactions/test_service.py and test/integration/test_interaction_security.py
- [x] T044 [US5] Enforce one effective answer per version, canonical idempotency replay and transactional result receipt in src/cli_agent_orchestrator/clients/interaction_repository.py and src/cli_agent_orchestrator/services/interactions/service.py
- [x] T045 [US5] Implement durable prepare/claim/confirm/no-effect/uncertain/reconcile transitions and prohibit automatic retry after claim in src/cli_agent_orchestrator/services/interactions/service.py
- [x] T046 [US5] Revalidate exact source/dialog/target immediately before any provider-native effect and keep unsupported dialogs manual-only in src/cli_agent_orchestrator/services/terminal_service.py
- [x] T047 [US5] Show accepted, returned, acknowledged, confirmed, uncertain and reconciled states; require explicit refresh/reconciliation for uncertain state in web/src/components/QuestionsPanel.tsx and web/src/api.ts
- [x] T048 [US5] Test before-effect rejection, after-effect acknowledgement loss, partial navigation and no-resend behavior in test/integration/test_interaction_uncertain_delivery.py

## Phase 8: User Story 6 — Recuperar preguntas tras desconexiones y reinicios (Priority: P1)

**Goal**: Recover durable state and audit history, reject replaced sources, and reconcile direct terminal closure without inventing an answer.

**Independent test**: Restart with pending, returned-unacknowledged and uncertain requests; reload a snapshot and prove IDs and no-resend state are stable.

- [x] T049 [P] [US6] Integration: test restart/replay, repeated events, direct terminal closure and no duplicate initial task in test/integration/test_interaction_recovery.py
- [x] T050 [US6] Advance durable interaction turn_generation on new-task delivery and direct terminal input, but not on MCP answer return/ack in src/cli_agent_orchestrator/services/terminal_service.py (Work V1 passes the exact launch binding; Work V2 remains unsupported without modifying its process tools)
- [x] T051 [US6] Recover in-flight claims as uncertain, expose append-only history and current metadata snapshots, and reconcile exact dialog closure in src/cli_agent_orchestrator/services/interactions/service.py and src/cli_agent_orchestrator/clients/interaction_repository.py
- [x] T052 [US6] Add history/snapshot APIs and stale-incarnation checks in src/cli_agent_orchestrator/api/interactions.py
- [x] T053 [US6] Verify lost/duplicated event recovery and source replacement in test/integration/test_interaction_recovery.py and test/api/test_interactions_transport.py

## Phase 9: User Story 7 — Consultar contenido según la autoridad de cada participante (Priority: P1)

**Goal**: Bind a human content owner from authenticated creation or trusted server lineage; enforce resource ACL and revalidate Work authority at acceptance/effect time.

**Independent test**: Owner can read, unrelated cao:read cannot, admin self-designation of an unowned exact incarnation is audited, and revoked Work grants block late answers/effects.

- [x] T054 [P] [US7] Integration: test browser Principal versus shared service bearer/capability, owner ACL, revocation and Work exact-binding denial in test/integration/test_interaction_security.py and test/integration/test_interaction_work_authority.py
- [x] T055 [US7] Transport: pass the verified Principal to terminal creation and add cao:admin-only self-designation for an exact unowned incarnation in src/cli_agent_orchestrator/api/main.py and src/cli_agent_orchestrator/api/interactions.py
- [x] T056 [US7] Integration (Ledger + Runtime): persist authenticated source owner, validated parent inheritance and Work principal/grant binding; revoke capability on replacement in src/cli_agent_orchestrator/services/terminal_service.py and src/cli_agent_orchestrator/services/interactions/capability.py
- [x] T057 [US7] Revalidate content grant and WorkAuthority in the same transaction at answer acceptance and immediately before a Work effect; keep Work approval in WorkDecisions in src/cli_agent_orchestrator/services/interactions/service.py
- [x] T058 [US7] Runtime: prove Work local/remote/process fencing, no ordinary-input bypass and unchanged Work V2 process tool set in test/api/test_work_terminal_input_fence.py, test/integration/test_interaction_work_authority.py and test/integration/test_interaction_work_launch.py
- [x] T059 [US7] Web: render metadata-only/redacted detail, clear stale node data, and show precise authorization errors without leaking prompt/answer content in web/src/components/QuestionsPanel.tsx

## Phase 10: User Story 8 — Conocer el soporte por proveedor y conservar los clientes existentes (Priority: P2)

**Goal**: Publish truthful per-version capability, keep legacy/optional integrations within the same authority, and never broaden profile or Work permissions.

**Independent test**: Support endpoint distinguishes explicit emit, native detect and native response; old clients retain behavior but cannot answer a request without its exact ID/version; Work V2 and unsupported remote placements show a limit.

- [x] T060 [P] [US8] Runtime: test support matrix and profile restrictions in test/providers/test_interactions.py and test/services/test_terminal_interaction_capability.py
- [x] T061 [US8] Integration (Ledger + Transport): publish provider/version/execution-mode capability data and manual reason through src/cli_agent_orchestrator/services/interactions/service.py and src/cli_agent_orchestrator/api/interactions.py
- [x] T062 [US8] Integration (Transport + Runtime): keep answer_user_prompt outside the interaction ledger and verify legacy input cannot bypass required-question or Work guards in src/cli_agent_orchestrator/mcp_server/server.py and src/cli_agent_orchestrator/services/terminal_service.py
- [x] T063 [US8] Add the exact-terminal manual navigation callback and confirm Questions remains appended without renumbering existing Alt+N tabs in web/src/components/AgentPanel.tsx and web/src/App.tsx
- [x] T064 [US8] Integration: verify enabled optional integrations project the same service receipts and disabled AG-UI leaves the main inbox available in test/integration/test_interaction_transport_contracts.py and test/services/agui (metadata-only adapter, idle/reconnect, safe receipt lifecycle, persisted browser-session revocation, unavailable fallback; 18 focused and 517 AG-UI regression tests pass).
- [x] T065 [US8] Transport: verify legacy answer_user_prompt cannot answer a ledger question or target a different source in test/integration/test_interaction_transport_contracts.py and test/mcp_server/test_answer_user_prompt.py

## Phase 11: Polish and cross-cutting validation

**Purpose**: Complete the provider/e2e matrix, update evidence, and review cross-subsystem contracts.

- [ ] T066 Run the full quickstart journey and record actual test/provider evidence and omissions in specs/016-agent-interactions/evaluation.md
- [x] T067 Run applicable Python and web suites, web build, authorization/MCP HTTP-only/Work/inbox/agent_step regressions and the project-composition-check in specs/016-agent-interactions/evaluation.md
- [x] T068 Review the complete implementation diff and confirm only intended source/test/spec files changed in specs/016-agent-interactions/evaluation.md

## Execution notes

Checked tasks map to the actual source/test paths above. T011 remains partial because Hermes has no proven safe per-launch config overlay. T035 records real-provider limits; SC-007 is not satisfied by mocks. T064 now includes the optional metadata-only receipt adapter; its new evidence is in agui-host-validation.md. T067–T068 were revalidated after the native-dialog corrections by the previous full run in full-validation.md: 16949 Python tests, 119 explicit skips, web/apps/browser suites, coverage floors and composition PASS. The current continuation has its own full completion gate T078. Actual release evidence and remaining provider acceptance limits are recorded in evaluation.md.

## Dependencies and execution order

### Review findings incorporated during implementation

- [x] T069 Persist owner-reviewed, exact-question/version clarification delegations and enforce human-required default, current assignment, expiry and revocation in services/interactions policy/service/repository; ledger-owned tests.
- [x] T070 Add owner-only delegation/revocation HTTP routes and conditional Questions detail controls; transport/web owners implement their respective files and tests.
- [x] T071 Prove parent lineage alone grants no prompt authority, owner-reviewed delegation permits a bounded clarification, approvals stay human-only and revocation/deadline blocks supervisor access in composed security tests.

T069–T071 are release gates discovered by continuous review; complete them before final T066–T068 verification/signoff.

### Phase dependencies

- Setup T001–T004 can run in parallel.
- Foundation T005–T014 blocks all stories. Within it, model/repository/policy precede API/MCP/runtime capability wiring. T013–T014 can run after their implementation seams exist.
- US1 is the MVP and depends on the foundation. Its distinct backend/UI test files can run in parallel; implementation proceeds ledger -> API/MCP -> web -> composed flow.
- US2 depends on US1 because supervisor views reuse question and answer contracts.
- US3 depends on US1's ledger/detail UI; native answer remains disabled without evidence.
- US4 depends on the ledger and MCP required-group flow; wait handling must preserve Work constraints.
- US5 depends on versioned answers and source generation; its claim protocol does not permit replay after uncertainty.
- US6 depends on source registration and transaction/recovery state from the foundation and US5.
- US7 depends on source routes and Work boundary checks; it is a release gate for showing protected content.
- US8 depends on provider capability, legacy guard and UI status work from earlier stories.
- Polish T066–T068 depends on all desired stories. The parent-owned Graphify review/update occurs after design tasks and around implementation changes; preserve any concurrent shared Graphify process and dirty outputs.

### Parallel opportunities

- Setup fixtures T001–T004 have disjoint files.
- Within each story, test tasks marked [P] own distinct test files and can run concurrently.
- Once the foundation is complete, separate agents may work on ledger, API/MCP, runtime and web files according to the ownership table. Do not edit the same production file concurrently; when a later story changes an earlier file, schedule the tasks sequentially.
- Provider manual evaluations are independent across Codex, Claude Code, OpenCode and Hermes if each uses a separate disposable session.

### MVP and incremental validation

MVP is US1: typed durable explicit questions, resource-authorized human list/detail/answer, source capability, same-task MCP wait/result/ack and the Questions tab. Stop after T025 and validate the full explicit journey. Add US2 delegation, US3 native/manual observations, US4 wait integration, US5 no-resend behavior, US6 restart recovery, US7 content/Work authority and US8 capability/legacy compatibility in order. No release can claim support for native response without real provider/version evidence.

## Continuación AG-UI / dependencias reales

- [x] T072 Inspeccionar y verificar impacto Graphify/source de ledger, stream AG-UI, autorización de transporte y snapshots; documentar contrato metadata-only antes de mutar implementación.
- [x] T073 [P] Añadir tests de proyección AG-UI: rojo inicial antes de implementación, recibos exactos, privacidad, cambios en idle, paginación, restart/reconnect, revocación real y modo deshabilitado; 18 casos dirigidos pasan tras congelar los tests.
- [ ] T074 Implementar adaptador metadata-only del ledger y su integración AG-UI sin una segunda autoridad de respuesta; regresión AG-UI completa 517 passed.
- [ ] T075 [P] Ejecutar aceptación real Docker T019/T020 y setup local con imágenes inmutables de la fuente actual; T020 14 passed (incluye T019), setup 5 passed sin skips, fuente supervisor/worker verificada.
- [ ] T076 [P] Ejecutar browser auth Docker e instalación personal/restart/upgrade/rollback con la imagen actual. Browser auth 1 passed; instalación final real 2 passed (162,02 s), imagen y contenedor con main corregido por SHA256; agui-host-validation.md.
- [ ] T077 Ejecutar aceptación host y scratch Bubblewrap en guest desechable verificado; conservar diagnóstico de cualquier dependencia bloqueada.
- [ ] T078 Ejecutar suites/checks aplicables completos y composición después de los cambios; actualizar Graphify aislado, review de diff, resultados y límites sin certificar skips.

La corrección de tipos posterior a los primeros verdes invalida el freeze anterior: T074–T076 se vuelven a abrir hasta repetir la evidencia sobre los nuevos hashes; los resultados históricos se conservan en agui-host-validation.md.

### Admisión concurrente bajo el límite SC-001

- [x] T079 Reproducir y medir la admisión PostgreSQL concurrente de 20 fuentes y 100 preguntas con espera/retención del lock; conservar el tiempo solicitud-a-visible como diagnóstico y medir el p95 desde registro confirmado hasta visible como aceptación SC-001 (≤2 s). Mantener límite global, idempotencia y validaciones de fuente/Work en la misma frontera de autoridad, con RED/GREEN nativo en test/integration/test_interaction_capacity.py. Evidencia final: registro confirmado→visible p95 789,4 ms; 20/20 registros y preguntas visibles; solicitud→visible p95 3312,8 ms conservado como diagnóstico.
