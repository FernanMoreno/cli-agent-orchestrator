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
- [x] T017 [US1] Integrar launches ordinarios mediante src/cli_agent_orchestrator/services/terminal_service.py y src/cli_agent_orchestrator/cli/commands/launch.py sólo tras definir y autorizar el ingreso interno que aporta `Principal`, selector de provisión e idempotency key server-owned (no inferidos de `agents`, sesión, body ni caller_id); cubrir RED de provisión ausente/ajena, body que suplanta job/grant, retiro o reemplazo entre resolución/admisión/readiness, reinicio y replay con misma clave pero distinto origen en test/services/test_work_launch.py y las suites de runtime, gateway y API. La activación de entrada pública requiere gate separado; el RED temporal del 2026-09-24 no se conserva como suite verde.
  Cierre del 2026-09-29 para `CAO_WORK_LAUNCH_MODE=required`: el CLI ordinario usa el ingreso Work autenticado existente, `/sessions` y la creación directa de sesiones legacy rechazan antes del efecto, y el modo inválido detiene el arranque. La configuración por defecto sigue siendo legacy. La prueba de composición CLI→HTTP→SQLite confirma replay de una sola admisión; los contratos previos cubren provisión ajena/ausente, reemplazo, revocación, reinicio y conflicto de origen. No registra backend, cuenta, proveedor ni entrada pública nueva; T035 conserva su gate.
- [x] T018 [US1] Integrar inbox con identidad de operación, deduplicación y entrega incierta en src/cli_agent_orchestrator/services/inbox_service.py; probar reinicio con mensaje enviado no confirmado en test/services/test_work_inbox.py.
- [x] T019 [US1] Integrar hijos y handoffs gestionados por los owners internos `work_repository.py`/`work_origin_schema.py`, `work_origin.py`, `work_admission.py`, `work_service.py` y `work_agent_step.py`: credencial CAO aleatoria por intento, digest en migración Work v36 aditiva y binding durable antes del efecto; proxy MCP server-owned por intento con secreto sólo en descriptor privado heredado y servicio de origen por socket privado. Revalidar instalación/principal/item/intento/generación/grant-revisión/lease/expiración, binding, grant y recuperación en cada petición; revocación/reemplazo corta uso, proxy superviviente tras reinicio sólo usa binding vigente y proxy perdido exige conciliación sin reissue/redelivery. Persistir en v37 aceptación exacta del receptor antes de emitir receipt. El gateway interno registra `agent_step` sólo en composición sellada; no se activa ingreso público ni se da autoridad al camino legacy de `utils/orchestration.py`. Añadir backend Docker por intento: sin socket del daemon en el worker, rootfs readonly, red/IPC/PID aislados, capacidades retiradas, `no-new-privileges`, mounts mínimos y rechazo previo al efecto para cualquier dimensión no imponible; endpoint MCP sólo dentro de su contenedor. Añadir aceptación Bubblewrap en Docker local fijando imagen/base, versión 0.13.0, Landlock ABI >=9, user namespaces y broker dedicado, registrando kernel/runtime y límites WSL2; no afirmar aceptación de producción. Padre autenticado selecciona sólo refs hijo/receptor preprovisionadas, sin sus credenciales ni `Principals`; resolver comprueba permisos vivos e hijo/receptor se autentican por separado. Cubrir acceso cruzado, secretos, autoridad obsoleta, reinicio, replay/duplicados, fallo parcial, aceptación durable, `caller_id` falso, Docker sibling isolation y rollback/mixed-version en `test/services/test_work_delegation.py`, `test/services/test_work_lineage_origin.py`, `test/services/test_work_service.py`, `test/clients/test_work_migrations.py`, `test/backends/` y `test/integration/t019/`. No activar ingress público, proveedor real ni DB del operador.
- [x] T020 [US1] Cerrar sólo después de T117–T122: integrar YAML y script con provisión server-owned por step, binding Work inmutable, receipt/result autenticados y projector CAS reiniciable; mantener separados Work ACK, resultado, estado de terminal y telemetría. Contrato implementable: specs/001-verifiable-orchestration/contracts/workflow-managed.md.
- [x] T021 [US1] Añadir renovación cercada en src/cli_agent_orchestrator/clients/work_repository.py, join recuperable y cleanup separado en src/cli_agent_orchestrator/services/work_service.py; ampliar test/services/test_work_service.py con lease expirado y resultado tardío (FR-003).
- [x] T022 [US1] Exponer DTO y consultas aditivas de trabajo/eventos en src/cli_agent_orchestrator/api/main.py y src/cli_agent_orchestrator/mcp_server/server.py; probar contratos legacy/v1 en test/api/test_work_contract.py.
- [x] T023 [US1] Ejecutar ciclo end-to-end mock_cli de las cinco entradas con reinicio en test/e2e/test_work_lifecycle.py y registrar pruebas de SC-001/SC-002 en specs/001-verifiable-orchestration/workflow-status.md.

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
- [x] T035 [US2] Integrar grants, reservas y errores estructurados en src/cli_agent_orchestrator/api/main.py y src/cli_agent_orchestrator/mcp_server/server.py sólo tras autorización separada para entrada pública; probar en test/api/test_work_authority.py Principal falso, selector ajeno, no ampliación por body/continuación/hijo y rechazo de `task_received` falsificado, duplicado o de generación/receptor revocado, sin que HTTP/MCP creen grants o recibos por inferencia. La instrucción posterior del operador de completar la integración autorizó implementar este ingreso; HTTP/MCP siguen apagados salvo `CAO_ENABLE_PUBLIC_WORK_INGRESS=true` en cada proceso. Provisión, bearer, grant y backend continúan siendo prerrequisitos explícitos; el body no los crea.
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
- [x] T044 [US3] Integrar snapshot en launch, hijo, handoff, YAML y scripts desde src/cli_agent_orchestrator/services/work_service.py; ampliar test/e2e/test_work_lifecycle.py con igualdad de contexto tras reinicio.
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
- [x] T063 [US5] Ejecutar fixtures de todas las transiciones y probar herdr no nativo con backend real disponible en test/e2e/test_herdr_generic_status.py; registrar SC-007 y no cerrar O03 si sólo existe rechazo preventivo. La matriz de 15 aristas se reprodujo en API, Web y TUI. Herdr v0.9.1 real con digest fijado mostró recepción de `mock_cli`, estados y cleanup de workspace/tab/pane; O03 permanece abierto para las demás combinaciones admitidas y no se infiere receipt Work.

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
- [x] T079 [US7] Revisar que API, terminal, DB y memoria delegan transiciones al dueño común; extraer sólo lógica duplicada afectada a módulos de plan.md y ampliar .importlinter con dependencias comprobadas (FR-017).
- [x] T080 [P] [US7] Inventariar especificaciones no implementadas en docs/historical-spec-triage.md con evidencia por criterio, empezando por docs/issues/568-js-yaml-omap-dos/requirements.md y design.md; no repetir cambios ya superados (FR-025).
- [x] T081 [P] [US7] Preparar inventario de commits/solapamientos y checks de integración en docs/upstream-integration-plan.md usando refs locales; no fetch, merge ni cambio de historial sin petición (FR-024).
- [x] T082 [US7] Ampliar test/e2e/test_real_provider_matrix.py con evidencia por escenario, cuotas y continuidad; extender test/test_real_provider_matrix_contract.py para distinguir skipped de validado (FR-019).
- [x] T083 [US7] Mantener CI opt-in, protección y prerrequisitos de cuentas en .github/workflows/real-provider-e2e.yml y docs/real-provider-e2e.md; CI ordinaria nunca activa live_provider (SC-009).
- [x] T084 [US7] Ejecutar matriz real sólo con cuentas/proveedores/modelos autorizados y registrar escenario/proveedor/versión/resultado en specs/001-verifiable-orchestration/workflow-status.md; ausencia de autorización queda pendiente, no passed (FR-019).
- [x] T085 [US7] Preparar cambios de integración upstream en entorno aislado sólo después de autorización explícita; verificar matriz relevante de docs/upstream-integration-plan.md; ningún merge se infiere de este backlog (FR-024).

## Phase 10: Cierre transversal

- [x] T086 Ejecutar pruebas de historias, regresiones afectadas y project-composition-check según .ai/project-name; registrar comandos, códigos de salida y primera frontera de cualquier fallo en specs/001-verifiable-orchestration/workflow-status.md.
- [x] T087 Revisar composición según .ai/composition/review-prompt.md y contracts/ de esta feature: transacciones, autoridad, reservas, cleanup, versión, eventos y fallo parcial; registrar veredicto sin equiparar mocks a proveedores reales.
- [x] T088 Revisar diff contra baseline inicial y actualizar docs/aipm-orchestration-roadmap.md sólo con garantías demostradas; conservar todos los cambios ajenos y toda limitación pendiente.
- [x] T089 Decidir actualización de graphify-out/ y selección de invariantes durables para vault en specs/001-verifiable-orchestration/workflow-status.md; no duplicar hechos generados ni publicar conocimiento no revisado.
- [x] T090 Ejecutar verificación final fresca, comprobar trazabilidad de 25 FR y 10 SC y actualizar checklists de specs/001-verifiable-orchestration/; no firmar cierre completo mientras queden requisitos, gates o demostraciones obligatorias pendientes.

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
- T093 es un gate documental aprobado, no permiso de entrada pública. La
  instrucción posterior del operador de completar la integración autoriza la
  implementación T035, con activación por proceso explícita y apagada por
  defecto; T094 acredita sólo el receipt interno tras suite completa sin
  omisiones. T020 quedaba abierto por su vínculo de workflow/resultados; el
  cierre local tras T117–T122 está registrado el 2026-09-30.
  T019 se cerró para el perfil local Docker y T017 para el modo gestionado
  explícito, sin implicar despliegue ni cierre de las otras integraciones.
- T019 dependía del receipt interno T094, la provisión explícita de actores y el
  backend demostrado por T097. Se cerró el 2026-09-29 para runtime Docker local
  y aceptación reproducible; `WORK_BACKENDS` permanece vacío. No hay host de
  despliegue ni aceptación de producción, que requerirían una tarea separada.
  El proxy y su credencial requieren prueba de aislamiento del backend antes
  de cualquier ejecución en `WorkAdmission._preflight`, antes de crear sesión
  o ventana. El registro Work sigue vacío; Tmux/Herdr no son fallback y un
  token en entorno no basta. RED: backend ausente o sin aislamiento rechaza
  antes del efecto y un intento hermano no abre endpoint ni hereda descriptor.
  T020 no infiere su binding de T019 ni de journales legacy.
- T117 escribe los REDs antes de cerrar la implementación. T118 fija el schema
  v39 y métodos de provision/binding; T119 los consume en YAML/script; T120
  consume el binding para receipt/result; T121 consume binding/result para el
  projector; T122 verifica la composición Docker local. El 2026-09-30, T117–T122
  y T020 se cerraron para la aceptación local Docker; límites y evidencia se
  registraron en `workflow-status.md` y `contracts/workflow-managed.md`.

## Coverage

| Requisito | Historia | Tareas principales |
|---|---|---|
| FR-001 / R01 | US1 | T005–T023,T091 |
| FR-002 / R02 | US1 | T012,T015,T016,T023,T091,T094,T117–T122 |
| FR-003 / R03 | US1,US4 | T019,T020,T021,T054,T093,T094,T097,T117–T122 |
| FR-004 / R04 | US1 | T011,T014,T022,T094 |
| FR-005 / R05 | US2 | T024,T027,T028,T035 |
| FR-006 / R06 | US2 | T017,T019,T020,T024,T028–T030,T035,T036,T093,T094,T097,T117–T122 |
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
| SC-010 | US1,US4 | T015,T021,T052,T055,T094 |

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

- [x] T097 [US2] Implementar y aceptar el backend Work Linux con Bubblewrap en
  `src/cli_agent_orchestrator/backends/`, manteniéndolo sin registrar en
  `work_registry.py`. Para el alcance actual, la aceptación Ubuntu 26.10/QEMU
  TCG cierra la demostración: 8 casos pasaron sin skips. No existe ni se prevé
  un host de despliegue, así que Work queda deshabilitado; esto no declara
  soporte de producción. Una decisión futura de despliegue requiere una tarea
  nueva y aceptación en el host concreto. Exigir Bubblewrap >=0.12.0 y probar que
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
  sin redelivery automático. La aceptación del guest Ubuntu 26.10/QEMU TCG
  cierra la demostración en el alcance actual. Como el proyecto no tiene ni
  prevé un host de despliegue, mantener el backend sin registrar y Work
  deshabilitado; cualquier activación futura requiere una tarea nueva con
  target de despliegue y aceptación propia. Tmux/Herdr conservan rechazo Work.
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
  cuenta como aceptación de host. Mantener `WORK_BACKENDS = {}` por la decisión
  de no desplegar Work: el guest cierra la aceptación T097 de este alcance, pero
  no autoriza ejecución en un host no elegido (FR-003/FR-006/SC-001, C07, T097).

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
esa frontera queda explícita en C07/C08. En ese punto T097 y T019 seguían
`[ ]`; `WORK_BACKENDS={}` permanecía vacío y no se registraba el backend.

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
- [x] C08: aceptación sin skips y Landlock ABI >= 9 en el guest Ubuntu 26.10 fijado, arrancado con QEMU TCG desde GitHub-hosted `ubuntu-24.04`; el perfil guest no certifica el kernel de despliegue.

La aceptación QEMU hosted de C08/T101 está cerrada para el perfil guest.
**T097 queda cerrado con ese alcance:** no existe ni se prevé un host de
despliegue, así que el backend no se registra y Work queda deshabilitado.
Al corte del 2026-09-28, T019 permanecía abierto y diferido, sin target Linux.
Ese estado histórico quedó supersedido por el cierre local de T019 en Phase 19,
registrado abajo el 2026-09-29.

## Phase 16: Cierre de identidad broker y aceptación de runner

- [x] T099 [US2] Enforzar en `BubblewrapWorkBackend.preflight_work` que `CAO_WORK_BROKER_ACCOUNT` nombre una cuenta local existente, no root, con shell no interactiva y UID igual al eUID; rechazar configuración ausente, root, shell de login o mismatch antes de cualquier probe Landlock/Bubblewrap. Añadir pruebas RED/GREEN para cada condición y para rechazo sin probes en `test/backends/test_work_broker_identity.py`; la aceptación host de `test/integration/t097/test_host_acceptance.py` debe usar y comprobar la misma configuración (FR-003/FR-006, SC-001, US2/AC13, C03; partial).
- [x] T100 [US2] Añadir `.github/workflows/t097-host-acceptance.yml` para orquestar desde GitHub-hosted `ubuntu-24.04` un guest Ubuntu 26.10 efímero con QEMU TCG, sólo desde `main` y `workflow_dispatch` de `main`, permisos `contents: read` y timeout acotado. Fijar y verificar SHA-256 de la imagen cloud y del source archive Bubblewrap 0.13.0; compilar e instalar Bubblewrap root:root 0755 dentro del guest; preparar sysctls allí; crear la cuenta broker no interactiva indicada por `CAO_WORK_BROKER_ACCOUNT`; exigir Landlock ABI >= 9; correr los ocho casos como broker sin skips, con timeout 45 s exclusivo de TCG; documentar que el guest no certifica el runner ni un host de despliegue (C07/C08; partial).
- [x] T101 [US2] Ejecutar el workflow de aceptación QEMU desde `main`; exigir Landlock ABI >= 9, ocho casos sin skips, timeout TCG documentado y Bubblewrap 0.13.0 con digest revisado. El run global verde cierra C07/C08 para el perfil guest en `specs/001-verifiable-orchestration/` y `docs/auditoria-t097/`, sin afirmar compatibilidad de hosts de despliegue ni registrar `WORK_BACKENDS`. Con esta aceptación se cierra T097 para el alcance sin despliegue; al corte del 2026-09-28, T019 seguía independiente y abierto (estado supersedido por Phase 19).

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
`ubuntu-24.04`. El primer run `36454812599` falló en `uv sync` porque el
checkout parcial omitía el hook Hatch de `pyproject.toml`; se corrigió el
archivo del workflow. En ese punto T101 aún esperaba un run global verde;
`WORK_BACKENDS={}` y T097/T019 continuaban abiertos.

**Primer run hosted con archive completo — 2026-09-28:** el run
[`36457607699`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36457607699)
(`8d93e018`) arrancó el guest Ubuntu 26.10 fijado, confirmó Landlock ABI 11,
completó `uv sync` y verificó el source archive Bubblewrap 0.13.0
(`4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765`). Compiló
con GCC `15.3.0-4ubuntu1` y Meson `1.10.1`, e instaló versión `0.13.0` como
`root:root 0755`. El digest
`15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250` fue
rechazado por no estar aún allowlisted; los ocho casos fallaron en el fixture
antes de ejecutar sus cuerpos. Se revisaron origen y perfil fijado y se añadió
ese hash exacto. En esa fase T101/C07/C08 aún esperaban un workflow global
verde; la suite pasó en el run siguiente, pero `setup-uv` falló al guardar la
caché vacía del host. No se registra `WORK_BACKENDS` ni se certifica un host
de despliegue.

**Suite hosted pasada; limpieza de caché pendiente — 2026-09-28:** el run
[`36460464186`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36460464186)
(`48909237`) usó el digest revisado, Landlock ABI 11 y ejecutó
`test/integration/t097 -m t097_host` como broker: **8 passed, 0 skipped en
201,67 s**. El workflow quedó rojo después de la suite, cuando `setup-uv`
intentó guardar una caché del host que no existe porque `uv` corre dentro del
guest. El commit `7135d91a` desactivó el cache host; el run global verde
posterior consta a continuación.

**Aceptación final hosted en QEMU — 2026-09-28:** el run
[`36463930292`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36463930292)
(`7135d91a`) terminó con workflow global verde. El guest registró kernel
`7.3.0-5-generic`, Landlock ABI 11 y Bubblewrap 0.13.0 con el digest revisado
`15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250`.
`test/integration/t097 -m t097_host` pasó **8 casos, 0 skips, en 288,11 s**
bajo la cuenta broker. T101 y C07/C08 se cierran para el guest reproducible.
**Decisión de cierre T097 — 2026-09-28:** no existe ni se prevé un host de
producción; se cierra T097 con esta aceptación y no se registra ni habilita el
backend. Cualquier despliegue futuro requerirá una tarea nueva y aceptación del
host concreto. T019 queda independiente. La decisión local posterior habilita
Docker en dos papeles: runtime Work por intento y entorno de aceptación
Bubblewrap. Esto no convierte Docker Desktop/WSL2 en host de producción ni
registra el backend global.

## Phase 17: T019 — Docker local y puente gestionado

**Independent Test**: dispatch gestionado por SQLite temporal hacia workers
Docker separados, recepción exacta con ACK atómico, revocación/reinicio sin
reissue, y aceptación Bubblewrap local con imagen/kernel identificados.

- [x] T102 [US1] Añadir en `src/cli_agent_orchestrator/clients/work_origin_schema.py` y `src/cli_agent_orchestrator/clients/work_repository.py` la migración v36 aditiva para el digest de credencial por intento, binding completo, lease y recuperación; implementar creación/validación/rotación fail-closed antes del efecto y probar no-backfill, rollback, mixed-version, replay y acceso cruzado en `test/clients/test_work_migrations.py`, `test/services/test_work_lineage_origin.py` y `test/services/test_work_service.py`.
- [x] T103 [US1] Conectar un único `WorkOrigins` al runtime, admisión, dispatch y receipt del receptor; integrar la ruta de handoff interna sin caller-provided authority en el gateway sellado y el adapter `agent_step`; cubrir aceptación durable, ACK atómico, revalidación y duplicados en `test/services/test_work_lineage_origin.py`, `test/services/test_work_service.py` y `test/integration/test_work_dispatch.py`. `utils/orchestration.py` queda fuera de la ruta autorizada.
- [x] T106 [US1] Añadir migración v37 append-only para `work_task_receiver_acceptances` en `src/cli_agent_orchestrator/clients/work_origin_schema.py`; exigir aceptación autenticada, exacta a delivery/hash/receiver/attempt/generation antes de emitir receipt en `src/cli_agent_orchestrator/services/work_origin.py`; probar rechazo sin aceptación, aceptación/replay idénticos, contradicción, revocación y ACK atómico en `test/clients/test_work_migrations.py`, `test/services/test_work_lineage_origin.py` y `test/services/test_work_service.py`.
- [x] T104 [US2] Implementar `DockerWorkBackend` por intento y protocolo MCP gestionado en socket local aislado en `src/cli_agent_orchestrator/backends/`, manteniendo `WORK_BACKENDS` vacío por defecto; imponer opciones Docker y mapping de contrato verificables, rechazar mounts/red/proceso no soportados, conciliar identidad/cleanup sin redelivery y probar workers simple, hijo gestionado y aceptación exacta del receptor en `test/backends/` y `test/integration/t019/`.
- [x] T105 [US2] Crear imagen y comando reproducibles de aceptación local Bubblewrap en Docker bajo `test/integration/t019/` y documentar ejecución/límites en `specs/001-verifiable-orchestration/quickstart.md`; fijar digests/versiones, exigir kernel Linux, Bubblewrap 0.13.0, Landlock ABI >=9, user namespaces y broker no root dedicado, registrar evidencia de aceptación y confirmar que el perfil no habilita producción ni el registry global.

**T019 verificada y cerrada en alcance local, 2026-09-29:** T102/T103/T104/
T105/T106 completadas. Migraciones v36/v37/v38, credenciales separadas por
intento/receptor, aceptación exacta y ACK atómico tienen cobertura focal. El
runtime enlaza `WorkOrigins` al backend; el gateway sellado registra el adapter
interno `agent_step` sin habilitar ingreso público ni `utils/orchestration.py`.
`./test/integration/t019/run-docker-backend-acceptance.sh` pasó **7/7** en Docker
Desktop 29.8.1: worker simple, rechazo fail-closed, hijo gestionado, receipt
exacto del receptor, aislamiento de hermanos, recovery con objetos nuevos y
caída del owner con worker activo sin redelivery. `./test/integration/t019/run-docker-acceptance.sh` pasó
**8/8 sin skips** en guest Ubuntu 26.10/QEMU, kernel `7.3.0-5-generic`, Bubblewrap
0.13.0, digest `15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250`
y Landlock ABI 11. La suite amplia relacionada pasó 177 tests; 4 pruebas T098
de host Bubblewrap se omitieron por no ser Linux nativo, cubierto aquí por QEMU.
No hay host de producción: esto cierra la integración y aceptación local T019,
pero no registra backend en `WORK_BACKENDS` ni habilita despliegue.

## Phase 18: Guardas de replay y mutaciones de decisiones

**Independent Test**: ejecutar un retry autorizado mientras otro escritor cambia el
intento previo y comprobar que la transacción lo rechaza; autenticar con permiso de sólo
lectura y comprobar que no crea ni revoca decisiones.

- [x] T107 [US4] Vincular el `EXECUTE` de replay con la identidad observada en `src/cli_agent_orchestrator/services/step_replay.py`, transportarla desde `src/cli_agent_orchestrator/api/main.py` por `src/cli_agent_orchestrator/services/script_runner.py` y comparar dentro de `src/cli_agent_orchestrator/services/workflow_journal.py`; cubrir cambio concurrente/identidad estable en `test/services/test_step_replay.py`, `test/services/test_step_contract.py` y `test/api/test_run_step_replay_branch.py`; inicializar esquema Work en fixtures actuales de `test/api/test_replay_nfr2_and_c1.py` y `test/services/test_settlement_rewire.py`.
- [x] T108 [US4] Exigir permiso de escritura en creación y revocación de decisiones en `src/cli_agent_orchestrator/api/work_routes.py`; probar denegación de sólo lectura y éxito de escritura en `test/api/test_work_decisions.py` y conservar cobertura de rutas mutantes en `test/api/test_scope_coverage.py`.

**Verificación T107/T108 — 2026-09-29:**
`uv run pytest -o addopts= -q --disable-warnings test/services/test_step_replay.py
test/services/test_step_contract.py test/services/test_settlement_rewire.py
test/api/test_work_decisions.py test/api/test_scope_coverage.py
test/api/test_run_step_replay_branch.py test/api/test_replay_nfr2_and_c1.py`:
**510 passed, 4 warnings, 0 skipped**.

## Phase 19: Convergencia de T019 local

**Independent Test**: ejecutar dos intentos Docker concurrentes, probar acceso
cruzado, reiniciar el owner tras crear un contenedor, y confirmar conciliación
durable sin redelivery; repetir aceptación local con el perfil Docker fijado.

- [x] T109 [US1] Limitar `DockerWorkBackend` a un daemon local verificado antes de enviar imagen o tarea; rechazar contextos SSH/TCP y cubrir la frontera con tests unitarios y aceptación local.
- [x] T110 [US1] Distinguir artefacto ausente de daemon/CLI inaccesible en inspect, cleanup y conciliación Docker; ningún error de observación puede acreditar cleanup completo.
- [x] T111 [US1] Integrar recuperación Docker tras reinicio con la conciliación Work por intento/generación; borrar sólo artefactos con identidad exacta y mantener el intento bloqueado si no puede probarse su parada/cleanup.
- [x] T112 [US1] Cerrar durablemente issues MCP sin efectos al demostrar salida normal del worker; recuperar issues/effects tras reinicio sin reissue ni redelivery.
- [x] T113 [US1] Añadir aceptación adversarial real de dos workers Docker concurrentes que intenten abrir el endpoint MCP ajeno o heredar/leer sus descriptores; comprobar rechazo y aislamiento por intento.
- [x] T114 [US1] Actualizar el inventario y bundle de recovery al esquema Work vigente, v38, conservando rechazo de perfiles manipulados y pruebas de copia/restore.
- [x] T115 [US1] Alinear fixtures de proyección, continuación y launch MCP con receipt durable y versión de adapter vigente; ningún booleano legado puede simular aceptación autenticada.
- [x] T116 [US1] Consolidar el estado documental de T019 local: resolver la nota histórica que aún lo deja diferido, registrar cada gate ejecutado y mantener explícito que no se afirma host de producción ni registro global.

## Phase 20: T020 — workflow managed step bridge

**Independent Test**: en YAML y script, fijar provisión y Work delivery; aceptar
receipt y resultado sólo del receiver vigente para ese binding; reiniciar entre
cada frontera y obtener una sola proyección durable. Salida/telemetría terminal
sin receipt+resultado conserva el estado pending. Un retry crea identidad nueva
sólo después de una transición explícita de workflow. Un exit Docker no cero
sólo falla el intento exacto tras comprobar proceso detenido y cleanup completo;
reinicio no reintenta y la repetición del retry cercado es idempotente.

- [x] T117 [US1] Añadir REDs de contrato para `test/services/test_work_workflow.py`, `test/api/` y `test/integration/t020/`: selector server-owned por workflow revision/step; binding exacto; no-source/revocation; YAML/script; task_received/result spoof, replay y generación ajena; caída tras dispatch/receipt/result/projection; CAS perdido; legacy terminal sin ACK; retry sólo explícito; y fallo post-dispatch permitido sólo con exit no cero y cleanup Docker probado. Usar el contrato de `contracts/workflow-managed.md` como fixtures/versiones para mantener T020 bloqueado hasta completar la aceptación.
- [x] T118 [US1] Implementar provisión server-owned y binding inmutable en `WorkProvisioning`/`WorkWorkflowOrigins` y `work_repository.py`: selector mapping por Principal/workflow revision/step, sujetos y autorizaciones workflow/receiver preprovisionados, grant, contrato/snapshot/lease y delivery congelados. Añadir migración Work v39 aditiva, tablas `work_workflow_step_provisions`, `work_workflow_step_bindings` y `work_workflow_step_projections`, unicidad/FKs/checksum/no update-delete, sin backfill; commit del binding junto a admisión/idempotency key antes de dispatch. Probar revocación, sustitución, migración interrumpida, mixed-version y retry del mismo binding.
- [x] T119 [US1] Conectar inicio y recuperación de YAML y script: obtener `Principal` en `api/main.py`; resolver cada step desde el mapping durable; inyectar callback en `workflow_service.start_run` y resolver `run_step` desde el run record de `script_runner.py`. Ningún body/env/terminal crea authority; un step legacy nunca pasa a Work. Pending sin binding no dispatcha y se recupera con la misma provisión y clave. Probar fuente ausente, revocación, reinicio y que run-generation no avance con Work pendiente.
- [x] T120 [US1] Extender `WorkAttemptCredentials`, `WorkOrigins`, `WorkService` y proxy MCP privado para autenticar receptor/acción y añadir `cao.work.submit_result`; conservar `cao.work.task_received` y su receipt atómico. Aceptar sólo `WorkflowStepResultV1` estricto (`schema_version=1`, `status=completed`, `output` objeto JSON hasta 1 MiB), derivar item/attempt/generation desde credenciales, validar contra el output schema congelado y publicar/leer el artefacto durable antes del finish CAS. Probar receipt separado, resultado idéntico idempotente, y rechazar resultado conflictivo, tardío o inválido sin autoridad de terminal/telemetría.
- [x] T121 [US1] Implementar projector restartable en owner de workflow: rehidratar sólo accepted result del Work exacto, validar envelope y hash, y en una transacción SQLite hacer CAS del `workflow_run`/`workflow_run_step` y registrar proyección contra binding/run-generation/step-attempt/Work-attempt. Integrar antes del resume; pending no incrementa generation. Probar caída antes/después del commit, duplicado idempotente, CAS concurrente, Work uncertain/revoked, fallo durable, retry autorizado y continuation sin duplicar efectos; proyectar un fallo Work sólo desde el intento fallido actual.
- [x] T122 [US1] Añadir worker Docker determinista `agent_step` a `test/integration/t020/`, activable sólo por flag opt-in, con imagen/digest fijados y sin provider externo; usar backend Docker local T019 y el proxy MCP privado real para receipt/result. Mantener `WORK_BACKENDS` vacío por defecto. Aceptar YAML y script, reinicio/recovery, aislamiento por intento, ACK sin resultado que queda pending, y `T122_FAIL` que crea fallo retryable sólo con exit y cleanup verificados; demostrar retry admin cercado e idempotente y registrar límites locales sin afirmar host de producción.

## Cierre local revisado — 2026-09-30

T023/T044: cinco entradas y contexto tras reinicio en la aceptación mock_cli;
T079: delegación y contrato directo del projector, deuda indirecta conservada;
T086–T090: pruebas, revisión de composición/diff y trazabilidad completadas.
La marca de T090 acredita la verificación final y sus límites, **no el signoff
completo del programa**. requirements.md conserva sus revisiones de calidad;
checklists/local-acceptance.md distingue aceptación local y externa.

T084 acepta las 18 combinaciones autorizadas registradas en t084-live-matrix.json. T085 completa la preparación/aceptación del candidato aislado; O03 conserva proveedores adicionales no aceptados.
002 está completada con evidencia histórica y revisión LF actual. Evidencia:
completion-evidence.md, composition-review-2026-09-30.md y la sección final de
workflow-status.md. No commit, push ni merge en esta entrega.


## Cierre T085 — 2026-09-30

Candidato upstream preparado y verificado en clon independiente. Inventario
Python único: **13.476 casos**; **13.357 passed, 118 skipped, 1 xfailed**;
cero nodeids faltantes y cero fallos vigentes. Aceptación por particiones y
repetición de archivos afectados, no una ejecución monolítica que se atribuya
a los intentos interrumpidos. Los skips conservan sus límites de entorno y el
xfail conocido no se convierte en passed. Los fallos anteriores quedan
registrados junto a sus reruns; la prueba final de telemetría pasa en ambos
checkouts con DB propia y esquema Work real.

Gates finales: arquitectura candidato 5 kept/0 broken; composición del checkout
original PASS; black/isort sobre 958 archivos exit0; enlaces Markdown/diff
check exit0. Tras normalizar formato se repitieron 1.682 casos de runtime
con 3 skipped y 1 xfailed; 825 casos de settlement/Work/replay pasan.
Web, TUI, MCP Apps, Docker y Agent Plugins conservan sus gates documentados
en la revisión. El wheel final instalado coincide byte a byte con nueve módulos
del candidato y contiene sus recursos Web/TUI/MCP Apps/Agent Plugins.

**T085 completada; ambos specs quedan sin tareas abiertas.** Esto acredita
preparación y aceptación local del candidato; su integración en main sigue
siendo una acción separada. O03 no certifica proveedores adicionales a los
autorizados; no existe despliegue/host de producción dentro de este cierre.
Artefactos locales de cobertura y parche en `/home/felni/ct/`; manifiesto
final: `/home/felni/ct/t085-candidate-manifest.json`.


## Phase 21: Convergence — aceptación omitida y fallo conocido

Petición explícita del usuario, 2026-09-30: corregir las 118 omisiones y el
xfail del inventario T085. El cierre anterior conserva su alcance histórico;
esta fase necesita evidencia nueva. No se eliminan pruebas para cambiar los
contadores ni se equipara un mock con aceptación de kernel/proveedor real.

- [x] T123 [US5] Corregir la falsa WAITING de Claude cuando el agente cita el footer de navegación; reproducir el xfail sin marca y verificar menús activos/descartados en buffer y viewport (FR-022/SC-007).
- [x] T124 [US7] Sustituir omisiones obsoletas de audit, Git transports, parser tmux y packaging de references por aserciones del contrato actual; conservar negativos y parity sin inventar ficheros ni funcionalidades ausentes (FR-024/SC-008).
- [x] T125 [US7] Proveer herramientas locales aisladas para gitleaks/cargo y ejecutar los probes Codex/OpenCode con binarios instalados, sin llamadas de pago ni cambios globales (FR-019/SC-009).
- [x] T126 [US2] Repetir aceptación Docker real T019/T020 y local setup, con imagen inmutable y limpieza de intentos, registrando los nodeids anteriormente omitidos (FR-006/FR-022).
- [x] T127 [US2] Ejecutar las omisiones de Bubblewrap/Landlock/capacidades/FS case-insensitive en runner QEMU local gratuito con kernel ABI>=9, binary digest fijado e identidad broker; las pruebas que requieren root mantienen sandbox guest aislado (FR-006/SC-008).
- [x] T128 [US7] Ejecutar los dos workflows reales omitidos con proveedor/cuenta autorizados y 0 € adicionales, preservando configuración personal y perfiles de la prueba (FR-019/SC-009).
- [x] T129 [US7] Actualizar inventario de cobertura sin fallos esperados ocultos, repetir regresiones afectadas y gates de composición/formato/packaging; documentar cada resultado real y cualquier prerrequisito aún imposible (FR-024/SC-008/SC-009).
