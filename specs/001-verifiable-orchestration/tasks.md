# Tasks: trabajo colectivo verificable

**Input**: spec.md, plan.md, research.md, data-model.md y contracts/ de esta feature.
**Status**: diseño aprobado; implementación incremental en curso.
Las casillas pendientes no acreditan implementación de producto.
La aprobación T093 del 2026-09-24 cierra sólo el gate de política/contrato
interno; no activa entradas públicas, MCP, proveedores reales ni DB de operador.
**Tests**: obligatorios por spec y AI_WORKFLOW.md. Cada prueba nueva debe fallar por
la conducta faltante antes de implementar; un error de fixture no es evidencia RED.
**Organization**: siete historias y gates comunes. Los números R/O/FR conservan trazabilidad.

## Phase 1: Setup

- [x] T001 Registrar baseline de archivos preexistentes y decisión de aislamiento en specs/001-verifiable-orchestration/workflow-status.md; conservar el workspace completo, sin mover cambios a una rama limpia que los pierda.
- [x] T002 Resolver identidad local del proyecto contra registro de launcher y registrar diagnóstico del gate en specs/001-verifiable-orchestration/workflow-status.md; si corresponde, añadir .ai/project-name con el nombre verificado.
- [x] T003 Verificar alcance real de .importlinter y añadir contratos derivados de dependencias de src/cli_agent_orchestrator/ además de src/cao_workflow/; ejecutar antes de tocar aplicación y registrar violaciones preexistentes.
- [x] T004 Crear fixtures de DB y artefactos temporales en test/fixtures/work_store.py y fixtures de eventos/DTO v1 en test/fixtures/work_contract_v1.json, sin usar DB ni credenciales del operador.

## Phase 2: Foundational

Bloquea US1. No habilita rutas de ejecución nuevas por sí sola.

- [x] T005 Añadir pruebas de transiciones, cancelación, generaciones y estados legacy en test/services/test_work_reducer.py para FR-001, FR-002 y FR-003.
- [x] T006 Definir enums, DTO y validación de entidades v1 en src/cli_agent_orchestrator/models/work.py según data-model.md, sin importar API ni backends.
- [x] T007 Implementar reducer puro con estado esperado, evidencia y generación en src/cli_agent_orchestrator/services/work_reducer.py; probar que ningún late event revive cancelled/succeeded.
- [x] T008 Añadir pruebas de migración desde DB vacía, antigua y parcialmente migrada en test/clients/test_work_migrations.py; una migración incompleta debe impedir admisión nueva.
- [x] T009 Implementar ledger, esquema aditivo y verificación en src/cli_agent_orchestrator/clients/work_repository.py y conectar inicialización en src/cli_agent_orchestrator/clients/database.py sin repetir migraciones existentes.
- [x] T010 Probar round-trip de registros legacy y rollback compatible en test/clients/test_work_migrations.py; documentar procedimiento y límites en docs/work-recovery.md para FR-018.

## Phase 3: US1 — identidad y evidencia (P0)

**Goal**: operación recuperable por cada entrada, con resultado y eventos durables.
**Independent Test**: reiniciar en fronteras de entrega y recuperar por work_item_id;
conservar resultado y niños después de eliminar terminal.

- [x] T011 [P] [US1] Añadir pruebas reales SQLite de idempotencia, CAS y estado+evento atómicos en test/clients/test_work_repository.py (FR-001, FR-004).
- [x] T012 [P] [US1] Añadir matriz de caídas antes/después de envío y settlement en test/services/test_work_service.py (FR-002, SC-002).
- [x] T013 [US1] Implementar creación de job/item/attempt, historial y lecturas en src/cli_agent_orchestrator/clients/work_repository.py; petición idéntica reutiliza identidad y distinto request_hash produce conflicto.
- [x] T014 [US1] Implementar persistencia transaccional de transición y evento, paginación y huecos declarados en src/cli_agent_orchestrator/clients/work_repository.py; deduplicar por event_id.
- [x] T015 [US1] Implementar intención-antes-de-envío, conciliación y settlement en src/cli_agent_orchestrator/services/work_service.py; no enviar si falla persistencia ni repetir tras envío incierto.
- [x] T016 [US1] Persistir resultado recuperable y validación antes de éxito usando src/cli_agent_orchestrator/services/step_output_store.py; cubrir caída entre artefacto y referencia y limpieza de huérfanos en test/services/test_work_result_store.py, conservando las regresiones de test/services/test_step_output_store.py.
- [x] T094 [US1] Añadir RED y contrato interno `TaskReceivedReceiptV1` en test/services/test_work_service.py y test/services/test_work_lineage_origin.py; implementar dueño server-side en src/cli_agent_orchestrator/services/work_service.py, src/cli_agent_orchestrator/services/work_origin.py y esquema aditivo posterior a v24 en src/cli_agent_orchestrator/clients/work_origin_schema.py para aceptar sólo receptor autenticado/registrado con grant y acción vigentes, entrega/hash e intento/generación exactos, nonce único y aceptación durable previa al transition Work; probar spoof, replay idéntico/contradictorio, ACK tardío, revocación, migración interrumpida, mixed-version, rollback y ninguna redelivery automática, sin activar transporte ni proveedor. Gate final interno 2026-09-24: cinco módulos focales completos 125 PASS sin omisiones en ext4 con pytest-asyncio real; rollback/mixed-version SQLite y arquitectura 4/4 PASS. No acredita ingreso público ni ACK de adapter legacy (dictamen `/tmp/caos-exec/T094/sol-async-closure-review.md`).
- [ ] T017 [US1] Integrar launches ordinarios mediante src/cli_agent_orchestrator/services/terminal_service.py y src/cli_agent_orchestrator/cli/commands/launch.py sólo tras definir y autorizar el ingreso interno que aporta `Principal`, selector de provisión e idempotency key server-owned (no inferidos de `agents`, sesión, body ni caller_id); cubrir RED de provisión ausente/ajena, body que suplanta job/grant, retiro o reemplazo entre resolución/admisión/readiness, reinicio y replay con misma clave pero distinto origen en test/services/test_work_launch.py. La activación de entrada pública requiere gate separado; el RED temporal del 2026-09-24 no se conserva como suite verde.
- [x] T018 [US1] Integrar inbox con identidad de operación, deduplicación y entrega incierta en src/cli_agent_orchestrator/services/inbox_service.py; probar reinicio con mensaje enviado no confirmado en test/services/test_work_inbox.py.
- [ ] T019 [US1] Integrar hijos y handoffs gestionados mediante los owners internos `work_repository.py`/`work_origin_schema.py`, `work_origin.py`, `work_admission.py`, `work_service.py`, `work_agent_step.py`, `agent_step.py` y `utils/orchestration.py`: credencial CAO aleatoria por intento, digest sólo en Work DB v27 aditiva y binding durable antes del efecto; proxy MCP server-owned por intento con secreto sólo en descriptor privado heredado y servicio de origen por socket privado. Revalidar instalación/principal/item/intento/generación/grant-revisión/lease/expiración, binding, grant y recuperación en cada petición; revocación/reemplazo corta uso, proxy superviviente tras reinicio sólo usa binding vigente, proxy perdido exige conciliación sin reissue/redelivery; backend aísla endpoint/descriptores o falla cerrado. Padre autenticado selecciona sólo refs hijo/receptor preprovisionadas, sin sus credenciales ni `Principals`; resolver comprueba permisos vivos, hijo/receptor se autentican separados y receipt nace sólo tras aceptación durable exacta del receptor, persistido por `WorkService` junto al Work ACK. Probar en test/services/test_work_delegation.py, test/services/test_work_lineage_origin.py, test/services/test_work_service.py, test/clients/test_work_migrations.py y test/integration/test_work_dispatch.py acceso cruzado entre intentos, redacción de secreto (delivery/prompt/env/terminal/log/event), generación/grant/revocación obsoletos, reinicio, replay/duplicados, fallo parcial, aceptación durable, `caller_id` falso y ACK legacy; v27 sin backfill, rollback/mixed-version fail-closed. No activar entrada pública, proveedor real ni DB del operador.
- [ ] T020 [US1] Integrar YAML y script workflows en src/cli_agent_orchestrator/services/workflow_service.py y src/cli_agent_orchestrator/services/script_runner.py con sujeto/grant explícitos por step gestionado y vínculo Work por intento/generación; probar en test/services/test_work_workflow.py contrato/snapshot congelados, retry/replay/continuation tras reinicio, revocación, falta de fuente efectiva y que run/step legacy no se infiera como Work ACK.
- [x] T021 [US1] Añadir renovación cercada en src/cli_agent_orchestrator/clients/work_repository.py, join recuperable y cleanup separado en src/cli_agent_orchestrator/services/work_service.py; ampliar test/services/test_work_service.py con lease expirado y resultado tardío (FR-003).
- [x] T022 [US1] Exponer DTO y consultas aditivas de trabajo/eventos en src/cli_agent_orchestrator/api/main.py y src/cli_agent_orchestrator/mcp_server/server.py; probar contratos legacy/v1 en test/api/test_work_contract.py.
- [ ] T023 [US1] Ejecutar ciclo end-to-end mock_cli de las cinco entradas con reinicio en test/e2e/test_work_lifecycle.py y registrar pruebas de SC-001/SC-002 en specs/001-verifiable-orchestration/workflow-status.md.

## Phase 4: US2 — autoridad y recursos (P0)

**Goal**: cero efectos fuera de grant/allowlist y reservas atómicas.
**Independent Test**: diez contendientes, dos plazas, dos jobs y revocación concurrente.

- [x] T024 [P] [US2] Añadir pruebas de principal falsificado, allowlist, herencia y revocación antes de envío en test/services/test_work_authority.py (FR-005, FR-006).
- [x] T025 [P] [US2] Añadir carreras de reserva, rutas alias/ancestro y escritor externo aún vivo en test/services/test_work_reservations.py (FR-007).
- [x] T026 [P] [US2] Añadir pruebas de presupuesto, reparto justo, aging, backpressure y ciclos en test/services/test_work_scheduler.py (FR-008, SC-004).
- [x] T027 [US2] Extender descriptores por operación/modelo/esfuerzo/enforcement en src/cli_agent_orchestrator/providers/catalog.py y src/cli_agent_orchestrator/providers/base.py; cubrir contrato en test/providers/test_provider_capabilities.py (FR-012).
- [x] T028 [US2] Implementar grants versionados, allowlist inmutable por revisión y admisión en src/cli_agent_orchestrator/services/work_authority.py, derivando principal de src/cli_agent_orchestrator/security/auth.py.
- [x] T029 [US2] Integrar comprobación antes de asignar/enviar en src/cli_agent_orchestrator/services/terminal_service.py y src/cli_agent_orchestrator/services/agent_step.py; body caller_id no concede autoridad.
- [x] T030 [US2] Definir enforcement verificable en src/cli_agent_orchestrator/backends/base.py y aplicarlo en src/cli_agent_orchestrator/backends/tmux_backend.py; rechazar grants restringidos no imponibles y probar bypass de rutas/comandos/red en test/backends/test_work_enforcement.py.
- [x] T031 [US2] Implementar reservas de rutas normalizadas y fencing en src/cli_agent_orchestrator/services/work_reservations.py con transacciones de src/cli_agent_orchestrator/clients/work_repository.py.
- [x] T032 [US2] Conservar diff/base/untracked autorizados antes de teardown en src/cli_agent_orchestrator/services/worktree_service.py; poner en cuarentena trabajo no acreditado y probarlo en test/services/test_worktree_service.py.
- [x] T033 [US2] Implementar reservas de recursos y presupuesto en src/cli_agent_orchestrator/services/work_scheduler.py; no usar contador best-effort como límite autoritativo.
- [x] T034 [US2] Añadir cola acotada, round-robin por job, aging y detección de ciclos en src/cli_agent_orchestrator/services/work_scheduler.py; ninguna espera mantiene transacción abierta durante I/O externo.
- [ ] T035 [US2] Integrar grants, reservas y errores estructurados en src/cli_agent_orchestrator/api/main.py y src/cli_agent_orchestrator/mcp_server/server.py sólo tras autorización separada para entrada pública; probar en test/api/test_work_authority.py Principal falso, selector ajeno, no ampliación por body/continuación/hijo y rechazo de `task_received` falsificado, duplicado o de generación/receptor revocado, sin que HTTP/MCP creen grants o recibos por inferencia.
- [x] T036 [US2] Ejecutar prueba de composición con procesos independientes y SQLite real en test/integration/test_work_admission.py, incluida caída entre reserva y backend y revocación durante preflight; registrar SC-003/SC-004.

## Phase 5: US3 — conocimiento y snapshots (P1)

**Goal**: conocimiento revisable y contexto recibido identificable en todas las delegaciones.
**Independent Test**: dos hermanos concurrentes, cambio de memoria viva y reinicio.

- [x] T037 [P] [US3] Añadir pruebas de estados de revisión, evidencia y memoria legacy en test/services/test_knowledge_revisions.py (FR-009).
- [x] T038 [P] [US3] Añadir pruebas de ACL local/HTTP/MCP y secretos en frontera de truncamiento en test/services/test_knowledge_policy.py (FR-014).
- [x] T039 [US3] Implementar revisiones inmutables y decisiones en src/cli_agent_orchestrator/services/knowledge_revisions.py y extender src/cli_agent_orchestrator/models/memory.py; no aprobar histórico automáticamente.
- [x] T040 [US3] Centralizar autorización, redacción y auditoría en src/cli_agent_orchestrator/services/knowledge_policy.py, integrando src/cli_agent_orchestrator/services/memory_service.py y src/cli_agent_orchestrator/services/memory_gateway.py.
- [x] T041 [US3] Añadir pruebas de snapshot vacío, CAS entre hermanos, persistencia fallida y reinicio en test/services/test_delegation_snapshot.py (FR-010, SC-005).
- [x] T042 [US3] Implementar snapshot universal y hashes separados fuente/entregado en src/cli_agent_orchestrator/services/delegation_snapshot.py reutilizando redacción de src/cli_agent_orchestrator/services/execution_manifest.py.
- [x] T043 [US3] Añadir en src/cli_agent_orchestrator/services/frozen_run_memory.py un adaptador de snapshot ID ligado a una orden durable y preservar la semántica legacy None/vacío; src/cli_agent_orchestrator/services/terminal_service.py conserva su contrato de contenido `str | None` sin resolver IDs. Alcance: adaptador probado en aislamiento; el wiring de launch, hijo, handoff, YAML, scripts y agent_step queda en T044.
- [ ] T044 [US3] Integrar snapshot en launch, hijo, handoff, YAML y scripts desde src/cli_agent_orchestrator/services/work_service.py; ampliar test/e2e/test_work_lifecycle.py con igualdad de contexto tras reinicio.
- [x] T045 [US3] Reproducir y aislar dependencia del checkout real en test/services/test_manifest_freeze.py; usar repositorio temporal controlado y probar baseline ausente sin ampliar silenciosamente presupuesto de src/cli_agent_orchestrator/utils/git_baseline.py.
- [x] T046 [US3] Implementar caducidad, superseded y tombstones en src/cli_agent_orchestrator/services/knowledge_revisions.py y probar exclusión de instrucciones no aprobadas/obsoletas en test/integration/test_knowledge_context.py.

## Phase 6: US4 — continuidad y decisiones (P1)

**Goal**: sustitución verificable e intervención durable sin efectos duplicados.
**Independent Test**: pausa por cuota, intervención y sustitución con artefactos conservados.

- [x] T047 [P] [US4] Añadir pruebas de paquete manipulado, versión desconocida y artefacto ausente en test/services/test_work_continuation.py (FR-011).
- [x] T048 [P] [US4] Añadir pruebas de decisión repetida, revisión cambiada y revocación en test/services/test_work_decisions.py (FR-016).
- [x] T049 [US4] Implementar registro durable de decisor/evidencia/efectos en src/cli_agent_orchestrator/services/work_decisions.py reutilizando compatibilidad de src/cli_agent_orchestrator/services/approval_store.py.
- [x] T050 [US4] Implementar exportación acotada y validación canónica sin credenciales en src/cli_agent_orchestrator/services/work_continuation.py; no interpretar hash como autorización.
- [x] T051 [US4] Implementar importación idempotente y preflight de sustituto en src/cli_agent_orchestrator/services/work_continuation.py; fallo de integridad o grant impide crear intento ejecutable.
- [x] T052 [US4] Integrar fencing/cese confirmado del intento anterior en src/cli_agent_orchestrator/services/work_service.py y src/cli_agent_orchestrator/services/work_scheduler.py; quota auto-resume impide sustitución insegura.
- [x] T053 [US4] Adaptar decisiones API/MCP y AG-UI en src/cli_agent_orchestrator/api/main.py, src/cli_agent_orchestrator/mcp_server/server.py y src/cli_agent_orchestrator/services/agui/handoff_approval.py a identidad efectiva y almacenamiento durable.
- [x] T054 [US4] Implementar propagación de cancelación a hijos y cleanup pendiente en src/cli_agent_orchestrator/clients/work_repository.py; probar padre muerto, hijo vivo y resultado concurrente en test/integration/test_work_cancellation.py.
- [x] T055 [US4] Demostrar continuidad entre dos adaptadores de prueba con artefacto ya completado en test/e2e/test_work_continuation.py; ningún entregable validado se ejecuta otra vez.

## Phase 7: US5 — estado y capacidades (P1)

**Goal**: misma semántica en todos los clientes y combinaciones backend/proveedor comprobables.
**Independent Test**: replay de fixtures comunes en API, web y TUI.

- [x] T056 [P] [US5] Añadir contratos de DTO y compatibilidad legacy en test/api/test_work_projection.py usando test/fixtures/work_contract_v1.json (FR-013).
- [x] T057 [US5] Implementar src/cli_agent_orchestrator/services/work_projection.py y conectarlo a src/cli_agent_orchestrator/api/main.py; nunca derivar éxito de TerminalStatus.IDLE.
- [x] T058 [US5] Uniformar fábrica/preflight con descriptores en src/cli_agent_orchestrator/providers/manager.py y src/cli_agent_orchestrator/providers/catalog.py, manteniendo campos de discovery; ampliar test/providers/test_provider_capabilities.py (FR-012).
- [x] T059 [US5] Añadir pruebas de estado genérico no nativo en test/backends/test_herdr_work_status.py e implementar transporte/observación en src/cli_agent_orchestrator/backends/herdr_backend.py y src/cli_agent_orchestrator/services/status_monitor.py (FR-022).
- [x] T060 [US5] Adaptar web/src/api.ts, web/src/components/StatusBadge.tsx y web/src/components/TerminalView.tsx al DTO común; probar running con terminal idle en web/src/test/work-state.test.tsx.
- [x] T061 [US5] Adaptar tui/src/server.rs y tui/src/renderer.rs al DTO común, incluidos contadores y foreground/background; añadir pruebas Rust con fixtures compartidos.
- [x] T062 [US5] Adaptar CLI/MCP en src/cli_agent_orchestrator/cli/commands/workflow.py y src/cli_agent_orchestrator/mcp_server/server.py; generar tokens visuales desde design-tokens/status.json y probar no divergencia.
- [ ] T063 [US5] Ejecutar fixtures de todas las transiciones y probar herdr no nativo con backend real disponible en test/e2e/test_herdr_generic_status.py; registrar SC-007 y no cerrar O03 si sólo existe rechazo preventivo.

## Phase 8: US6 — distribución y recuperación (P1)

**Goal**: autoridad versionada multiwriter y restauración que preserve garantías.
**Independent Test**: dos clientes, conflicto sobre revisión común, partición y restore.

- [x] T064 [P] [US6] Añadir contrato HTTP de revisión esperada, ACL, tombstones y cursor en test/api/test_knowledge_authority.py (FR-015).
- [x] T065 [P] [US6] Añadir REDs de corte server-owned en test/services/test_recovery_bundle.py y tests de autoridad/repository: operador autenticado frente a spoof/fallback local, lease durable con owner/scope/epoch/fence/TTL, gate antes de leer inventario y cero writers Work registrados before/after, productor no registrado marcado fuera de cobertura, expiración, revocación, writer nuevo, fence cambiado, reinicio, fallo parcial/publicación huérfana y rechazo durable; v1 legacy, inventario v26 cerrado y mixed-version fail-close; mantener RED de restore aislado T070 separado, sin contarlo como PASS (FR-018, SC-006).
- [x] T066 [US6] Implementar CAS autoritativo de revisiones y ACL proyecto/job en src/cli_agent_orchestrator/services/knowledge_revisions.py y rutas aditivas en src/cli_agent_orchestrator/api/main.py.
- [x] T067 [US6] Añadir protocolo versionado de checkpoint/cursor y conflictos en src/cli_agent_orchestrator/services/memory_gateway.py; ausencia remota no puede degradar a escritura local.
- [x] T068 [US6] Implementar retención, tombstones y cursor caducado en src/cli_agent_orchestrator/services/knowledge_revisions.py; acceso revocado invalida nuevas páginas.
- [x] T069 [US6] Implementar backup de DB, memoria, snapshots y artefactos con hashes en src/cli_agent_orchestrator/services/recovery_bundle.py, perfil cerrado en recovery_inventory.py y owner interno del lease en autoridad/work_repository.py: migración aditiva Work v25→v26, sólo operador autenticado crea/revoca, registro/fence transaccional de writers Work, captura revalida antes/después y publica bundle v2 con evidencia lease+inventario sin prometer quiescencia global; no exportar credenciales ni activar transporte/proveedor/DB operador. Depende de REDs T065; T071 conserva validación operativa de rollback (FR-018).
- [x] T070 [US6] Implementar restore aislado y validación de versiones/referencias en src/cli_agent_orchestrator/services/recovery_bundle.py; marcar intentos activos para conciliación y no relanzar. Cierre limitado al restore privado de bundle v2/perfil 26 publicado: staging bloqueado por T095, referencias cerradas frente a SQLite y publicación sin clobber; no activa ingress público, proveedores, DB del operador ni guard global. T071 conserva rollback/reactivación operativa y T072 la prueba multinodo.
- [x] T071 [US6] Documentar y probar rollback operativo y versiones lector/escritor en docs/work-recovery.md y test/integration/test_work_recovery.py: v25→v26 aditiva/ledger y rollback transaccional al fallar DDL; tras commit, sólo código compatible v26 o snapshot v25 aislado con conciliación/autorización separada de T070, nunca downgrade in-place; v1 sólo integridad histórica, bundle v2 con evidencia obligatoria, mixed-version fail-close, lease durable revalidado al reinicio y migración interrumpida/copia corrupta; no DROP ni restore implícito. Cierre limitado a prueba SQLite temporal y guía operativa; no activa un snapshot v25 con T070, ingreso público, proveedores ni reactivación automática. T072 continúa abierta.
- [x] T072 [US6] Ejecutar dos procesos HTTP locales contra una autoridad SQLite temporal compartida en test/integration/test_knowledge_multinode.py: propuestas concurrentes desde versión 0 producen exactamente 201/409; con B detenido, A avanza y B no confirma; tras reinicio, B rechaza versión obsoleta con 409 y lee el estado durable exacto. Cierre limitado a caída/reconexión de proceso en un host, sin partición de red, replicación, consenso ni HA; el harness requiere `fork` y mantiene una carrera posible al reservar puerto.

## Phase 9: US7 — mantenibilidad y operación (P2)

**Goal**: deuda operativa resuelta con contratos comprobados y preparación de release honesta.
**Independent Test**: matriz de configuración, actualización obsoleta, error durable y trazabilidad.

- [x] T073 [P] [US7] Añadir prueba de dos editores sobre source hash común en test/api/test_workflow_revision.py; implementar actualización condicional en src/cli_agent_orchestrator/services/workflow_spec_service.py y src/cli_agent_orchestrator/api/main.py (FR-020).
- [x] T074 [US7] Capturar contrato efectivo por step en src/cli_agent_orchestrator/services/agent_step.py y src/cli_agent_orchestrator/services/script_runner.py; persistirlo junto al intento y probarlo en test/services/test_step_contract.py (FR-020).
- [x] T075 [P] [US7] Inventariar env vars, precedencia y excepciones de seguridad en docs/configuration.md contrastando src/cli_agent_orchestrator/services/config_service.py, src/cli_agent_orchestrator/services/settings_service.py y src/cli_agent_orchestrator/security/auth.py (FR-021).
- [x] T076 [US7] Extender registro tipado/versionado y resolución consistente en src/cli_agent_orchestrator/services/config_service.py y src/cli_agent_orchestrator/services/settings_service.py; probar valores inválidos, unknown keys y propagación en test/services/test_config_service.py.
- [x] T077 [US7] Añadir regresión de error_kind script durable, reinicio y éxito posterior en test/services/test_script_error_kind.py; propagar tipo desde src/cli_agent_orchestrator/services/script_runner.py a settle_step en src/cli_agent_orchestrator/services/workflow_journal.py sin columna duplicada (FR-023).
- [x] T078 [US7] Actualizar proyección y comentarios obsoletos de error_kind en src/cli_agent_orchestrator/api/main.py y docs/aipm-orchestration-roadmap.md; conservar fallback explícito para histórico sin tipo (FR-023).
- [ ] T079 [US7] Revisar que API, terminal, DB y memoria delegan transiciones al dueño común; extraer sólo lógica duplicada afectada a módulos de plan.md y ampliar .importlinter con dependencias comprobadas (FR-017).
- [x] T080 [P] [US7] Inventariar especificaciones no implementadas en docs/historical-spec-triage.md con evidencia por criterio, empezando por docs/issues/568-js-yaml-omap-dos/requirements.md y design.md; no repetir cambios ya superados (FR-025).
- [x] T081 [P] [US7] Preparar inventario de commits/solapamientos y checks de integración en docs/upstream-integration-plan.md usando refs locales; no fetch, merge ni cambio de historial sin petición (FR-024).
- [x] T082 [US7] Ampliar test/e2e/test_real_provider_matrix.py con evidencia por escenario, cuotas y continuidad; extender test/test_real_provider_matrix_contract.py para distinguir skipped de validado (FR-019).
- [x] T083 [US7] Mantener CI opt-in, protección y prerrequisitos de cuentas en .github/workflows/real-provider-e2e.yml y docs/real-provider-e2e.md; CI ordinaria nunca activa live_provider (SC-009).
- [ ] T084 [US7] Ejecutar matriz real sólo con cuentas/proveedores/modelos autorizados y registrar escenario/proveedor/versión/resultado en specs/001-verifiable-orchestration/workflow-status.md; ausencia de autorización queda pendiente, no passed (FR-019).
- [ ] T085 [US7] Preparar cambios de integración upstream en entorno aislado sólo después de autorización explícita; verificar matriz relevante de docs/upstream-integration-plan.md; ningún merge se infiere de este backlog (FR-024).

## Phase 10: Cierre transversal

- [ ] T086 Ejecutar pruebas de historias, regresiones afectadas y project-composition-check según .ai/project-name; registrar comandos, códigos de salida y primera frontera de cualquier fallo en specs/001-verifiable-orchestration/workflow-status.md.
- [ ] T087 Revisar composición según .ai/composition/review-prompt.md y contracts/ de esta feature: transacciones, autoridad, reservas, cleanup, versión, eventos y fallo parcial; registrar veredicto sin equiparar mocks a proveedores reales.
- [ ] T088 Revisar diff contra baseline inicial y actualizar docs/aipm-orchestration-roadmap.md sólo con garantías demostradas; conservar todos los cambios ajenos y toda limitación pendiente.
- [ ] T089 Decidir actualización de graphify-out/ y selección de invariantes durables para vault en specs/001-verifiable-orchestration/workflow-status.md; no duplicar hechos generados ni publicar conocimiento no revisado.
- [ ] T090 Ejecutar verificación final fresca, comprobar trazabilidad de 25 FR y 9 SC y actualizar checklists de specs/001-verifiable-orchestration/; no firmar cierre completo mientras queden requisitos, gates o demostraciones obligatorias pendientes.

## Huecos descubiertos durante la integración

- [x] T091 [US1] Persistir y recuperar los datos de entrega por operación y seleccionar su adaptador desde un registro del servidor antes de conectar T017–T020. El binding, la orden inmutable y la referencia v14 vinculan contrato, snapshot, hashes y contenido privado verificable; el payload se restaura tras revalidar autoridad y binding. El drift de un adaptador deja sólo esa operación en cola, sin bloquear otra sana ni gastar su presupuesto; órdenes legacy v13 sin referencia no se ejecutan. Pruebas directas de mezcla, reinicio, texto fuera de SQLite/eventos, referencia ausente o alterada, migración, lanzamiento y no reenvío en test/integration/test_work_delivery.py, test/integration/test_work_dispatch.py, test/services/test_work_launch.py y test/clients/test_work_migrations.py (FR-001, FR-002, FR-010, FR-014).

- [x] T092 [US3] Completar T040 integrando memoria legacy local, exportación/importación y proyección de grafo en autorización, redacción y auditoría comunes; impedir bypass por backends de archivo/internos y registrar decisiones sin contenido, incluyendo lint y reparación local/de arranque. Reutilizar persistencia existente, probar fallo de auditoría antes de efectos/entrega y conservar carácter no aprobado de los archivos legacy (FR-014). T040 no se cierra sólo por disponer de fachada versionada.

## Dependencies & Execution Order

- T001–T004 preceden foundation. T005 falla antes de T006–T007; T008 antes de T009–T010.
- US1 depende de foundation. T011/T012 se pueden preparar juntos; T013–T023 siguen orden.
  La decisión T093 precede T094 y los nuevos REDs de T017/T019/T020;
  T094 acredita el receipt interno antes de que cualquiera declare Work ACK.
- US2 depende de US1. T024/T025/T026 pueden escribirse en paralelo; T027–T036 siguen orden.
- US3 depende de US1 y autoridad US2. T037/T038 son independientes; T039–T046 siguen orden.
- US4 depende de US1/US2/US3. T047/T048 pueden escribirse juntos; T049–T055 siguen orden.
- US5 depende del DTO de US1 y catálogo US2. Web T060 y TUI T061 pueden ejecutarse en
  paralelo después de T057; no paralelizar cambios compartidos en api/main.py o MCP.
- US6 depende de revisiones US3 y grants US2. T064/T065 son independientes; T066–T072 siguen orden.
- US7: T073/T075/T080/T081 pueden iniciarse como entregas separadas después de setup,
  si no compiten con archivos activos; T079 depende de las extracciones anteriores y
  T082–T084 necesitan continuidad y lifecycle. T085 necesita autorización externa explícita.
- T086–T090 se ejecutan al cerrar cada entrega sobre su diff y al final del programa.
- T091 es prerrequisito descubierto de T017–T020; el dispatcher asíncrono interno no lo sustituye ni cierra por sí solo T017.
- T093 es un gate documental aprobado, no permiso de entrada pública. T035
  requiere además integración y autorización separada; T094 acredita sólo
  el receipt interno tras suite completa sin omisiones.
  T017/T019/T020/T035 permanecen abiertos con sus gates propios.
- T019 depende del receipt interno T094 y de la provisión explícita de actores;
  además depende del backend Work demostrado por T097. T097 permanece abierto;
  por tanto T019 permanece bloqueado y abierto.
  El proxy y su credencial requieren prueba de aislamiento del backend antes
  de cualquier ejecución en `WorkAdmission._preflight`, antes de crear sesión
  o ventana. El registro Work sigue vacío; Tmux/Herdr no son fallback y un
  token en entorno no basta. RED: backend ausente o sin aislamiento rechaza
  antes del efecto y un intento hermano no abre endpoint ni hereda descriptor.
  T020 no infiere su binding de T019 ni de journales legacy.

## Coverage

| Requisito | Historia | Tareas principales |
|---|---|---|
| FR-001 / R01 | US1 | T005–T023,T091 |
| FR-002 / R02 | US1 | T012,T015,T016,T023,T091,T094 |
| FR-003 / R03 | US1,US4 | T019,T020,T021,T054,T093,T094,T097 |
| FR-004 / R04 | US1 | T011,T014,T022,T094 |
| FR-005 / R05 | US2 | T024,T027,T028,T035 |
| FR-006 / R06 | US2 | T017,T019,T020,T024,T028–T030,T035,T036,T093,T094,T097 |
| FR-007 / R07 | US2 | T025,T031,T032,T036 |
| FR-008 / R08 | US2 | T026,T033,T034,T036 |
| FR-009 / R09 | US3 | T037,T039,T046 |
| FR-010 / R10 | US3 | T041–T045,T091 |
| FR-011 / R11 | US4 | T047,T050–T052,T055 |
| FR-012 / R12 | US2,US5 | T027,T058 |
| FR-013 / R13 | US5 | T056,T057,T060–T063 |
| FR-014 / R14 | US3,US6 | T038,T040,T046,T064,T068,T091 |
| FR-015 / R15 | US6 | T064,T066–T068,T072 |
| FR-016 / R16 | US4 | T048,T049,T053,T054 |
| FR-017 / R17 | Todas | T003,T007,T079 |
| FR-018 / R18 | US1,US6 | T008–T010,T065,T069–T071,T094 |
| FR-019 / R19 | US7 | T082–T084 |
| FR-020 / O01 | US7 | T073,T074 |
| FR-021 / O02 | US7 | T075,T076 |
| FR-022 / O03 | US5 | T059,T063 |
| FR-023 / O04 | US7 | T077,T078 |
| FR-024 / O05 | US7 | T081,T085 |
| FR-025 / O06 | US7 | T080 |
| SC-001 | US1 | T017–T023,T093,T094,T097 |
| SC-002 | US1,US4 | T012,T016,T021,T023,T054,T055,T094 |
| SC-003 | US2 | T024,T028–T030,T035,T036,T094 |
| SC-004 | US2 | T026,T033,T034,T036 |
| SC-005 | US3 | T041–T044 |
| SC-006 | US6 | T065,T069–T071 |
| SC-007 | US5 | T056,T060–T063 |
| SC-008 | Todas | T080,T086–T090 |
| SC-009 | US7 | T082–T084 |

## Implementation Strategy

Primer incremento: foundation y US1 con las cinco entradas; no presentar sólo tablas como MVP.
Se revisa diseño, se ejecuta una historia completa y se verifican sus fronteras antes de ampliar.
Las pruebas de cada historia son independientes de cuentas reales; sus dependencias funcionales
anteriores deben existir. Las tareas [P] sólo son paralelas dentro de su bloque y prerrequisitos.
No se hacen commits automáticos. Una demo real y una integración remota conservan sus gates propios.

## Phase 11: Convergence

- [x] T093 [GATE documental de producto/autoridad] Decisión humana explícita del 2026-09-24 y contrato interno por fases registrados en specs/001-verifiable-orchestration/spec.md, plan.md y tasks.md: sólo operador autenticado provisiona launch; cada hijo/workflow tiene identidad y grant propios; sólo receptor autenticado emite `task_received` ligado a entrega/intento/generación. Owners, versión, migración aditiva/rollback, legacy/mixed-version y REDs de autoridad, procedencia, ACK, reinicio y fallo parcial quedan especificados. Cierra el gate de política, no implementación ni activación; T094 cerró posteriormente sólo el receipt interno, mientras T017/T019/T020/T035 continúan `[ ]` y entradas públicas/proveedores requieren autorización y validación separadas (FR-003/FR-006/SC-001).

## Phase 12: Convergence

- [x] T095 [US6] Implementar antes de reanudar T070 una primitiva privada
  transaccional de `WorkRepository` para una copia portátil v26 ya verificada:
  validar DDL/ledger/FKs/contexto normal e identidad/UUID inbox históricos sin
  adoptarlos como identidad activa; preservar context/bridge/bindings como
  procedencia inmutable; aplicar `blocked_restore` y reconciliar sólo
  `sent`/`acknowledged`/`running` en la misma transacción, preservando
  planned/finales/IDs/generaciones y sin provider/retry/replan. Probar paths
  fuente/staging/destino distintos, corrupción/rollback y que la copia queda
  no ejecutable antes de efectos, sin cambiar triggers/migración ni activar
  ingress (FR-018, SC-006; primitiva interna aceptada, T070 pendiente).

## Phase 13: Convergence

- [x] T096 [US2] Definir en `src/cli_agent_orchestrator/backends/work_registry.py`
  una fuente server-owned explícita y vacía por defecto que vincule identidades
  de `EffectiveWorkContract.backend` con backends capaces de imponer el contrato;
  componer `build_durable_launch_gateway` en el lifespan de
  `src/cli_agent_orchestrator/api/main.py` sólo tras `init_db()` y el punto
  existente de backend, sin inferir claves de tmux/herdr ni construir Herdr
  anticipadamente. Exigir clave exacta y preflight de capacidad/enforcement
  para el contrato efectivo, sin sustitución; registro ausente, clave
  desconocida o capacidad insuficiente conservan el 503 fijo de
  `/work-launches` sin efecto, y schema Work inválido impide servir el store.
  Probar en `test/api/test_work_launch_composition.py` con SQLite temporal y
  backend falso que la clave exacta permite sólo admisión `queued`, los casos
  ausente/desconocido/incapaz mantienen 503, el schema corrupto falla cerrado
  y no hay efectos de terminal ni proveedor. Esta tarea no provisiona
  autoridad, activa entradas públicas ni usa proveedores reales o la DB del
  operador; tampoco crea grants, reservas, dispatch o ACK. La activación
  pública conserva el gate separado de T035 (FR-005/FR-006/SC-003;
  plan US2, partial).

## Phase 14: Convergence

- [ ] T097 [US2] Implementar y demostrar un backend Work Linux dedicado en
  `src/cli_agent_orchestrator/backends/` que permanezca sin registrar en
  `work_registry.py` hasta superar su aceptación. Bubblewrap es candidato,
  no evidencia suficiente: si se usa, exigir versión >=0.12.0 y probar que
  0.11.1 o versión desconocida rechaza antes de cualquier efecto conforme a
  GHSA-pxhw-h44j-8pfx/CVE-2026-87766; el 0.11.1 instalado en este host es
  incompatible. Imponer el `ProcessRestrictionContract` efectivo
  para filesystem (runtime de sólo lectura mínimo, escritura allowlisted y
  rutas alias/symlink), comandos en cada exec descendiente, red e IPC; rechazar
  antes de efecto cualquier dimensión no imponible, contrato que no exprese
  un límite necesario o plataforma sin capacidad probada. Exigir que el proceso
  CAO que crea user namespaces/proxies use una cuenta OS dedicada, no root y no
  interactiva; el UID creador conserva capacidades dentro del namespace, por lo
  que un `uid_map` distinto por intento no sustituye esta separación. Tool grants
  sólo se ejecutan por `WorkMcpProxy` fresco por intento; sin fábrica del proxy,
  rechazar antes de admitir. Mantener proxy y secreto fuera de la
  visibilidad del agente, con transporte mínimo por intento; demostrar que un
  intento hermano no abre endpoint/socket ajeno, no hereda descriptores y no
  accede a rutas, comandos, red o IPC fuera de su allowlist. En
  `test/backends/` y `test/integration/`, probar con procesos adversariales y
  dependencias reales la frontera `preflight_work` y el guard fresco justo antes
  de cada create/send/continuación, incluidas revocación/generación obsoleta
  entre preflight y efecto, launch, reinicio, muerte de proxy, reemplazo,
  terminación, limpieza y fallos parciales; sin fuga de secretos ni nuevos
  efectos no autorizados, con residuo incierto bloqueado para conciliación y
  sin redelivery automático. Registrar el backend sólo para la
  plataforma y contratos demostrados; Tmux/Herdr conservan rechazo Work.
  Este gate no implementa credencial v27, hijo/handoff, ACK ni ingreso público:
  T019 sigue `[ ]` hasta sus propias pruebas y T035 conserva su gate
  (FR-003/FR-006/SC-001).

**Estado T097 — 2026-09-25:** evidencia acotada de probes con Bubblewrap 0.13
en WSL2, no prueba general ni cierre del gate. Con Landlock `FS_EXECUTE`, ELF
estático allowlisted ejecutó; se rechazaron ELF de `write_paths`, ELF con
`PT_INTERP` cuando el loader no estaba permitido, shebang con intérprete no
permitido, ejecución `execveat` de `O_TMPFILE` (EACCES) y un re-exec no
permitido desde descendiente doble-fork. Al permitir `ld.so`, un ELF dinámico
en `write_paths` sí ejecutó; sin filtro, memfd ejecutó mediante
`execveat(AT_EMPTY_PATH)`. En una matriz separada Bwrap noexec+BPF, el ELF
dinámico provider con `PT_INTERP` y fork+re-exec también pasaron; el filtro
denegó `memfd_create` con EPERM incluso en descendiente y `execveat` también
con EPERM; el `O_TMPFILE` de `work` montado `noexec` no ejecutó, y
`/proc/self/fd` y `/dev/fd` estaban enmascarados.
Los probes BPF anteriores también denegaron `PR_SET_PTRACER`, `unshare`,
`setns`, `clone3` y `clone(CLONE_NEW*)`; el BPF del proxy no denegó `ptrace`.

El probe del proxy mostró que un sibling host UID 1000 fuera de la ascendencia
pudo hacer `PTRACE_ATTACH` y `pidfd_getfd` leer FD 0 incluso con
`PR_SET_DUMPABLE(0)`. `PR_SET_PTRACER=1` permitió sólo PID 1; negar
`PR_SET_PTRACER` tampoco impidió el attach. `--uid 2000` siguió mapeado a host
UID 1000 (`uid_map: 2000 1000 1`), y el BPF probado no denegó ptrace: el proxy
sigue fail-closed. El remount `MS_REMOUNT|MS_BIND|MS_NOEXEC` desde userns Bwrap
con `CAP_SYS_ADMIN` devolvió `EPERM`; el checkout 9p sí permite ejecución.
Worktree bajo `/run/lock` es sólo una posibilidad, no probada. No se probaron
memfd heredado por FD, ELF escribible en `/dev/shm` vía `ld.so` ni inventario
empírico de FDs heredados. Estos límites impiden generalizar los resultados.

El contrato actual sólo ofrece `commands: tuple[str]`; no expresa ruta
canónica, digest ni requisito de ELF estático. Los launchers existentes de
proveedores envían texto shell y no admiten el subset directo probado.
El binding terminal de Work y los envíos ordinarios ahora comparten un lock
interprocesal por DB/terminal. `WorkAdmission` cierra el snapshot antes de
esperar y vuelve a resolver/revalidar el target dentro de la transacción de
escritura. `send_input` y `send_special_key` mantienen el lock desde antes de
la lectura de ownership hasta el transporte y el CAS de recibo; lo liberan
antes de actividad, telemetría y callbacks. Una regresión cubre `send_input`
reentrante desde un callback y verifica ambos transportes. Las suites focales
de ownership fence y launch pasaron **29 pruebas**.
La verificación ampliada de terminal y `test/integration/test_work_admission.py`
pasó **153 pruebas**. El único rojo inicial era un setup obsoleto sin terminal
durable; se corrigió sólo el test y el guard productivo no cambió.
`project-composition-check caos` pasó con **4 kept / 0 broken**. Se usaron DBs
SQLite temporales, sin DB del operador ni proveedores reales.

Esto sólo cierra la carrera de coordinación del binding admitido. No acredita
aislamiento, identidad/ejecución de comandos descendientes ni recuperación tras
reinicio; el gate de T097 sigue abierto. El supervisor evita redelivery, pero
no puede reattach ni conciliar cleanup tras reinicio. El backend sigue sin
registrar, `WORK_BACKENDS` permanece vacío y rechaza los contratos Work antes
del efecto; T097 y T019 siguen `[ ]`.

**Incremento de caracterización T097 — 2026-09-26:** dos archivos de test
amplían la evidencia sin cambiar código productivo. El módulo del supervisor
prueba cleanup con pidfd parcial, fallback y reattach tras resultados
`UNCERTAIN`; el intento original conserva `UNCERTAIN` cuando sólo el reattach
acredita la salida, y los descriptores parciales se cierran después de esa
prueba. El inventario aislado con Bubblewrap scratch 0.13.0 comprobó que
workspace, `/tmp` y `/dev/shm` usan mounts `rw+noexec`, que el runner queda
`ro+exec`, que no aparece ningún mount `rw+exec`, y que las seis variantes
directas/loader por `/proc/self/fd/3` de esas superficies no ejecutan ni
crean marcadores. Evidencia informada y revisada: módulo supervisor
**20 passed en 11,84 s**; suite consolidada de 12 módulos **151 passed en
23,43 s**; inventario aislado **1 passed en 4,49 s**; composición **275
archivos, 1017 dependencias, 4 kept, 0 broken**; auditoría de whitespace
limpia en los dos tests. El inventario usa mounts privados de prueba, no un
launcher Work integrado. No demuestra identidad/digest de ELF estático,
política `FS_EXECUTE` de producción, FD allowlist de Bubblewrap, aislamiento
del proxy hermano, reattach durable tras reinicio ni cleanup Work completo.
`WORK_BACKENDS` continúa vacío; Bubblewrap rechaza todos los contratos antes
del efecto. T097 y T019 permanecen `[ ]`. Graphify existente se contrastó con
fuente; al ser sólo pruebas, no se refresca en este incremento.

**Incremento auxiliar T097 — 2026-09-26:** revisión independiente **PASS
acotado** de ocho paths de modelo, parser, Landlock, staging y sus tests;
**FAIL para aceptación T097**. `EffectiveWorkContractV2` sigue separado del
binding v1, es descriptivo y sólo representa ELF estático
`x86_64/ELF64/little` con token canónico y mapping exacto de comandos. El
hash canónico v1 conserva su valor fijo. El parser comprueba `PT_LOAD` con
`PF_X`, entrada en bytes ejecutables y ausencia de `PT_DYNAMIC`/`PT_INTERP`.
Staging comprueba el snapshot contra el digest, sella un memfd y verifica
lectura e identidad del objeto; Landlock añade sólo reglas de ejecución
basadas en rutas, heredables por descendientes. No existe caller de esas
piezas en admisión ni supervisor Work.

Los probes reales fijan dos límites: `landlock_add_rule` sobre el `O_PATH` de
un memfd sellado devuelve **EBADFD=77**; bajo una regla Landlock para archivo
regular y el seccomp actual, otro memfd no listado pero heredado explícitamente
ejecutó por `/proc/self/fd` con **exit 43**. Es caracterización de bypass, no
una cadena segura. El supervisor conserva `os.execvpe(argv[0], ...)`, el
registro `WORK_BACKENDS` sigue vacío y Bubblewrap rechaza todos los contratos.
El Bubblewrap instalado es 0.11.1, inferior al mínimo 0.12.0; el mapeo de UID
host distinto sigue sin acreditarse por ausencia de `newuidmap`, y el proxy
same-UID permanece bloqueado. Faltan un launcher integrado, frontera de FDs,
filesystem/red/IPC, proxy y limpieza durable.

Evidencia final comunicada por root: suite explícita de 18 módulos **304
passed, 4 warnings, 0 skipped, exit 0 en 36,64 s**; staging focal **10
passed, 0 skipped**, Landlock focal **8 passed**; Black `--check` en ocho paths
PASS; `rg '[[:blank:]]+$'` sin matches; composición **278 archivos, 1020
dependencias, 4 kept, 0 broken**. Sin proveedor, DB del operador ni commit.
Graphify existente orientó el análisis y se verificó contra fuente; estos
módulos auxiliares no añaden call path productivo y el árbol sigue muy dirty,
por lo que se difiere el refresh. T097 y T019 permanecen `[ ]`.

**Incremento fail-closed del preflight Bubblewrap — 2026-09-26:** el preflight
productivo ya no ejecuta el candidato configurable antes de rechazar el
contrato. Los tests de marcador cubren wrappers con salida 0.11.1, desconocida
y 0.13.0 falsificada; la suite focal pasó **22 pruebas**. Sol revisó el cambio
acotado **PASS WITH RISKS**. En el harness, el ELF scratch se fija por
descriptor; el cleanup forzado conserva honestamente el resultado `UNCERTAIN`.
La evidencia root fue **99 passed, 1 skipped** (la prueba Landlock ABI 9 se
omitió porque el host ofrece ABI 7), Black en **6 archivos**, whitespace
limpio y composición **279 archivos, 1022 dependencias, 4 kept, 0 broken**.

T097 permanece `[ ]`; `WORK_BACKENDS` sigue vacío y Bubblewrap rechaza todos
los contratos. El callback helper no está unido a la DB Work; el ELF scratch
es input de confianza sin digest verificado. Siguen pendientes identidad
durable, recuperación tras reinicio, aislamiento del proxy entre siblings y
prueba en host ABI 9. Este incremento no cierra T097 ni modifica T019.

**Incremento de cleanup T097 con pidfd — 2026-09-26:** el harness scratch
lanza el comando como PID 1 con `--as-pid-1` y captura JSON `--info-fd` con
límite de tamaño/tiempo. Valida PID, `NSpid`, inode del PID namespace,
starttime y relación padre/hijo antes y después de `pidfd_open`; cleanup mata
PID 1 por pidfd y el test confirma que un trabajador separado mediante doble
fork/`setsid` desaparece y que su pidfd queda legible. Un error al validar el
monitor después de abrir su pidfd cierra ese descriptor; el test de pidfd ya
legible confirma el poll no bloqueante con timeout cero. RED/GREEN focales y
la suite de composición quedaron verdes. Verificación root comunicada:
**107 passed, 1 skipped** (Landlock ABI 9 omitida; el host ofrece ABI 7),
Black y `py_compile` PASS, `project-composition-check` **279 archivos, 1022
dependencias, 4 kept, 0 broken**; revisión Sol **PASS** para este delta.
Sin DB del operador, proveedores ni commit; `WORK_BACKENDS` sigue vacío y
T097/T019 continúan `[ ]`. Esto no satisface la aceptación completa T097:
persistencia/integración, contrato de filesystem/comandos/red/IPC impuesto,
proxy seguro por intento, aislamiento entre siblings, prueba ABI 9, host
Bubblewrap >=0.12 y recuperación siguen pendientes.

**Incremento T097 de persistencia pre-GO v30→v31 — 2026-09-27:** la
migración aditiva guarda una intención `pending` inmutable, el ACK canónico
acotado y la identidad Bubblewrap exacta. `WorkBubblewrapSetupIntent.record_pre_go`
revalida autoridad viva, generación y revisión `sent`, contrato V2,
token/digest/catálogo y ACK en un solo `BEGIN IMMEDIATE`; replay idéntico es
idempotente y cualquier fallo revierte identidad e intención. La lectura
histórica verifica la evidencia sin requerir autoridad vigente y no concede
permiso de ejecución. Revisión independiente **PASS**; verificación root:
**110 tests passed**, Black en **5 archivos**, whitespace localizado limpio y
`project-composition-check caos` **PASS** (285 archivos, 1037 dependencias,
4 kept, 0 broken). Graphify previo se contrastó con fuente; el refresh
intentado quedó incompleto (corpus 248 code/20 docs sin API semántica; AST
`--code-only` terminó, pero se atascó al resolver rutas 9p y se interrumpió
con exit 130). `graph.json` y `.graphify_ast.json` no cambiaron; refresh
diferido, no PASS. No hay integración a GO/release/backend;
`WORK_BACKENDS={}` y T097/T019 permanecen `[ ]`.

## Phase 15: Convergence

- [x] T098 [US2] Componer la ruta productiva de launch Work desde
  `DurableLaunchGateway`/`LaunchRuntime` y `WorkService` hasta
  `BubblewrapWorkBackend` y `WorkProcessSupervisor`, sin fallback de efectos
  por Tmux/Herdr. Ligar dispatch, setup ACK, identidad del proceso, GO,
  terminación y cleanup al item/intento/generación/revisión/contrato durables;
  revalidar autoridad justo antes de GO y de cada efecto protegido. Definir y
  aplicar una política que limite cada `exec` descendiente al mapping inmutable
  de ejecutables/contenido del contrato; cubrir explícitamente loader invocado,
  shebang, `execveat`, memfd, fork y doble fork, además de FDs y secreto/proxy
  entre intentos. Añadir primero pruebas de integración con SQLite temporal y
  workers adversariales que recorran admisión→dispatch→Bubblewrap→supervisor,
  verificando rechazo sin marcador/efecto, cleanup incierto conciliable y
  ninguna redelivery automática. Desarrollar y probar el código sin depender
  del host C08 usando sólo artefactos scratch como fixtures; ese resultado no
  cuenta como aceptación de host. Mantener `WORK_BACKENDS = {}` hasta que T097
  cierre aparte la aceptación adversarial en host aprobado (FR-003/FR-006/
  SC-001, C07, T097; partial).

**Evidencia T098 — 2026-09-28:** el gateway productivo registra sólo el
adaptador V2 de proceso y rechaza V1 antes de persistir una nueva admisión. El
capability de ejecución exige backend process-capable, mapping ELF inmutable
único y sin red directa; tools requieren fábrica de proxy bound al intento. El dispatcher interno se inicia sólo con un
backend Work registrado y se cancela al cerrar lifespan. Bubblewrap persiste
ACK e identidad exacta antes de GO, revalida setup/GO dentro de sus
transacciones y entrega ownership del árbol al supervisor para terminación y
recuperación. El worker recibe snapshot congelado más mensaje, dentro de 32 KiB.

Verificación actualizada — 2026-09-28: 25 módulos focales, **424 passed y 3
skipped**. Desglose: dispatch/backend 38; adversariales y contenido ligado 17;
composición Bubblewrap 39; gateway/runtime/API/MCP y autoridad 82;
supervisor/seccomp/Landlock/recuperación 124 passed, 1 skipped; setup,
identidad, staging y runtime snapshot 124 passed, 2 skipped. El worker
adversarial estático demuestra que un doble fork conserva el filtro: puede
reejecutar sólo `/exec/worker`; `/usr/bin/false` y el loader dinámico son
denegados; `memfd_create` y `execveat` devuelven EPERM. El contenido shebang se
rechaza antes de publicarse como ejecutable. Import Linter en este worktree:
289 archivos, 1070 dependencias, 4 contratos kept y 0 broken;
`project-composition-check caos` en este worktree: PASS. Scratch ejecuta
Bubblewrap 0.13.0 y worker ELF estático. La fixture scratch sobrescribe
`preflight_work` y envuelve el resultado sólo para capturar stdout; la ruta
`execute_bound_process` real, Bubblewrap, Landlock, seccomp y supervisor corren
sin mock. Esto prueba composición de código; no aceptación del host C08. El
host de desarrollo sigue en WSL2 Linux 6.18, Bubblewrap 0.11.1 y Landlock ABI
7, fuera del gate. El digest de Bubblewrap 0.13.0 del guest de aceptación ya
está allowlisted, pero `WORK_BACKENDS = {}` permanece sin registrar. T097/T019
y el gate de despliegue C08 siguen abiertos.

**Aceptación inicial T097/C08 — 2026-09-28:** se preparó un guest Ubuntu 26.10
en QEMU TCG con kernel `7.3.0-5-generic`, Landlock ABI 11, Yama=1 y user
namespaces. Bubblewrap 0.13.0 procede del release upstream `v0.13.0` (commit
`719a4fd`); SHA-256 de source archive
`4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765`, y del
binario `/usr/bin/bwrap`
`f41ba3f7be0280df0afe201f0e2eeb16a17e969782491e830e6753c67f78d70d`.
La suite con `T097_REQUIRE_HOST_ACCEPTANCE=1` terminó **6 passed, 0 skipped en
187,27 s**. Abarca despacho gateway/backend/supervisor, política de
descendientes, FS/red/SysV IPC/FD/entorno, hermanos en user namespaces,
revocación pre-GO y pérdida del ACK de resultado. El timeout de setup quedó
separado del timeout de ejecución y la regresión existente ahora fija ambos
explícitamente.

Esta es aceptación de un guest QEMU, no de runner CI/deployment, y la matriz
S07 aún no cubre endpoint proxy real entre intentos Work concurrentes, todos
los aliases filesystem/IPC ni recuperación tras muerte/reinicio de proxy y
supervisor. Además, el actor host ordinario del mismo UID pudo hacer
`ptrace`/`pidfd_getfd` sobre el bootstrap pre-GO fuera de los user namespaces;
esa frontera queda explícita en C07/C08. T097 sigue `[ ]`, `WORK_BACKENDS={}`
y T019 permanece `[ ]`; no se registra el backend.

**Actualización C07 — aceptación final en QEMU, 2026-09-28:** la suite host
`test/integration/t097 -m t097_host` pasó **8 passed, 0 skipped en 335,30 s**
bajo `caos-work-broker` (UID 999, `nologin`, sin procesos host ajenos al
iniciar). C07 ahora prueba dos launches simultáneos con checkouts separados,
workers ELF reales, proxy/socket FD 3, secretos y respuestas ligados por
intento; sibling `ptrace`/`pidfd_getfd` quedan denegados. El caso incierto
reabre el mismo SQLite, crea repositorio y gateway nuevos, y verifica estado
`uncertain`, una sola llamada upstream y ninguna redelivery.

Para TCG se usó `T097_TEST_WORKER_TIMEOUT_SECONDS=45`: las validaciones del
proof de runtime tardan 8–9 s cada una bajo emulación. El fixture mantiene 10 s
por defecto; el run de despliegue debe omitir el override. La decisión host es
cuenta OS dedicada, no root, `nologin` y sin otros procesos. No se afirma que
`uid_map` por intento aísle frente a procesos del mismo UID fuera de namespaces.

- [x] C07: proxy real por intento, concurrencia y recuperación tras reabrir el store.
- [x] C03: frontera de identidad del broker documentada y comprobada en la aceptación.
- [ ] C08: repetir aceptación sin skips y con Landlock ABI >= 9 en el guest Ubuntu 26.10 fijado, arrancado con QEMU TCG desde GitHub-hosted `ubuntu-24.04`; documentar que no certifica el kernel de despliegue.

El run QEMU hosted de C08/T101 sigue pendiente. T097/T019 y
`WORK_BACKENDS={}` permanecen abiertos hasta aceptar el host de despliegue.

## Phase 16: Cierre de identidad broker y aceptación de runner

- [x] T099 [US2] Enforzar en `BubblewrapWorkBackend.preflight_work` que `CAO_WORK_BROKER_ACCOUNT` nombre una cuenta local existente, no root, con shell no interactiva y UID igual al eUID; rechazar configuración ausente, root, shell de login o mismatch antes de cualquier probe Landlock/Bubblewrap. Añadir pruebas RED/GREEN para cada condición y para rechazo sin probes en `test/backends/test_work_broker_identity.py`; la aceptación host de `test/integration/t097/test_host_acceptance.py` debe usar y comprobar la misma configuración (FR-003/FR-006, SC-001, US2/AC13, C03; partial).
- [x] T100 [US2] Añadir `.github/workflows/t097-host-acceptance.yml` para orquestar desde GitHub-hosted `ubuntu-24.04` un guest Ubuntu 26.10 efímero con QEMU TCG, sólo desde `main` y `workflow_dispatch` de `main`, permisos `contents: read` y timeout acotado. Fijar y verificar SHA-256 de la imagen cloud y del source archive Bubblewrap 0.13.0; compilar e instalar Bubblewrap root:root 0755 dentro del guest; preparar sysctls allí; crear la cuenta broker no interactiva indicada por `CAO_WORK_BROKER_ACCOUNT`; exigir Landlock ABI >= 9; correr los ocho casos como broker sin skips, con timeout 45 s exclusivo de TCG; documentar que el guest no certifica el runner ni un host de despliegue (C07/C08; partial).
- [ ] T101 [US2] Ejecutar el workflow de aceptación QEMU desde `main`; exigir Landlock ABI >= 9, ocho casos sin skips, timeout TCG documentado y Bubblewrap 0.13.0 con digest revisado. Si el build genera un digest nuevo, verificarlo contra el source archive y configuración fijada, allowlistar sólo ese valor y repetir hasta run verde. Con ese resultado cerrar C07/C08 del perfil guest en `specs/001-verifiable-orchestration/` y `docs/auditoria-t097/`, sin afirmar compatibilidad de hosts de despliegue ni registrar `WORK_BACKENDS`. T097/T019 permanecen sujetos a aceptación del host de destino antes de habilitar ejecución Work (C07/C08, T097; partial).

**Repetición tras enforcement de broker — 2026-09-28:** copié el `src/` y
`test/` actualizados a un guest Ubuntu/QEMU Linux y ejecuté toda la suite como
`caos-work-broker` con `CAO_WORK_BROKER_ACCOUNT=caos-work-broker`:
`test/integration/t097 -m t097_host` → **8 passed, 0 skipped en 327,65 s**.
El guest usó `T097_TEST_WORKER_TIMEOUT_SECONDS=45` por la lentitud de TCG; el
workflow no establece esa variable y conserva el timeout normal de 10 s. Esta
repetición verifica el preflight nuevo, pero no reemplaza el run del runner
dedicado. La repetición conserva valor histórico. El intento nativo posterior
en `ubuntu-26.04` confirmó ABI 8 (`36450282433`), también insuficiente. T100 se
cambió a un guest Ubuntu 26.10 fijado con QEMU TCG, orquestado desde
`ubuntu-24.04`; ese workflow aún espera publicación y run remoto. T101 sigue
`[ ]`; `WORK_BACKENDS={}` y T097/T019 continúan abiertos.
