# Tasks: Corrección del arranque y recuperación de turnos

**Input**: spec.md, plan.md, research.md, data-model.md, contracts/turn-recovery.md.
**Tests**: regresiones deterministas exigidas por FR-017; TDD antes de cambiar comportamiento.

## Phase 1: Setup

- [x] T001 Inspeccionar cambios previos y revisar constitución/diagnóstico en specs/007-fix-agent-turns/research.md.
- [x] T002 Generar diseño, contratos y validación en specs/007-fix-agent-turns/plan.md, data-model.md, contracts/turn-recovery.md y quickstart.md.
- [x] T003 Consultar impacto Graphify, contrastar fuente y registrar reproducción/TDD en specs/007-fix-agent-turns/implementation-evidence.md.

## Phase 2: Foundational

- [x] T004 Escribir regresiones SQLite de generación, reconciliación, cancelación y reinicio en test/clients/test_turn_recovery.py; ejecutar fallos antes de implementación.
- [x] T005 Añadir persistencia aditiva y CAS de recuperación/resultados en src/cli_agent_orchestrator/clients/database.py.

## Phase 3: US1 — OpenCode válido (P1)

**Independent test**: shell login cambia PATH; perfil ausente rechaza antes de launch; perfil preparado conserva contenido y readiness V1/V2.

- [x] T006 [US1] Reproducir PATH/perfil ausente mediante test/providers/test_opencode_launch_contract.py.
- [x] T007 [US1] Fijar binario/PATH, validar perfil y limpiar configuración privada propia en src/cli_agent_orchestrator/providers/opencode_cli.py.
- [x] T008 [US1] Alinear validación de instalación con launch en scripts/docker_install.py y test/scripts/test_docker_install.py.
- [x] T009 [US1] Ejecutar regresiones V1/V2 en test/providers/test_opencode_cli_unit.py y test/providers/test_opencode_v2.py.

## Phase 4: US2 — Recuperar turno incierto (P1)

**Independent test**: supervisor sin NativeChild y trabajador alcanzan reconcile, verify no pega tarea, cancel no acepta recibo tardío; inbox mantiene pendientes.

- [x] T010 [US2] Reproducir falta de reconciliación de supervisor y conservación de mensajes en test/services/test_turn_recovery.py y test/services/test_turn_receipt_delivery.py.
- [x] T011 [US2] Persistir intentos/diagnóstico y proyectar reconciliación acotada en src/cli_agent_orchestrator/services/terminal_service.py y status_monitor.py.
- [x] T012 [US2] Implementar consulta/verificación/cancelación con fence por generación en src/cli_agent_orchestrator/services/turn_recovery_service.py y models/terminal.py.
- [x] T013 [US2] Añadir parada/reinicio seguro de pane propio en src/cli_agent_orchestrator/backends/base.py y clients/tmux.py, sin liberar terminal ante fallo.
- [x] T014 [US2] Probar carreras, reinicio, recibos antiguos/eco, diálogo y trabajo largo en test/clients/test_turn_recovery.py y test/services/test_turn_recovery.py.

## Phase 5: US3 — Resultados inequívocos (P1)

**Independent test**: pending 202, reconcile 409, verified 200 estable, fallo real 500; FULL conserva transcripción; consumidores coinciden.

- [x] T015 [US3] Escribir contratos de resultado/acciones/autorización en test/api/test_turn_recovery_api.py.
- [x] T016 [US3] Añadir errores tipados y proyección/resultados estables en src/cli_agent_orchestrator/services/terminal_service.py y api/main.py.
- [x] T017 [US3] Integrar status/result/verify/cancel en src/cli_agent_orchestrator/utils/orchestration.py, cli/commands/agent.py y mcp_server/server.py con pruebas de consumidores.
- [x] T018 [US3] Mostrar diagnóstico y acciones en web/src/api.ts y components/TerminalView.tsx; probar interfaz y TypeScript.

## Phase 6: Composition and completion

- [x] T019 Ejecutar suites aplicables y gate project-composition-check; registrar revisión de contratos/arquitectura/inbox/rollback en specs/007-fix-agent-turns/composition-review.md.
- [x] T020 Ejecutar prueba real aislada Claude/Codex/OpenCode con ida/vuelta y navegador; registrar recursos/intervenciones/casos omitidos en specs/007-fix-agent-turns/real-validation.md.
- [x] T021 Revisar diff y aceptación FR-001–FR-017; registrar decisión Graphify/vault y resultado final en specs/007-fix-agent-turns/implementation-evidence.md.

## Remaining work found during real acceptance

- [x] T022 Corregir identidad del trabajador tras respawn: pasar terminal_id autoritativo desde recuperación hasta backend tmux y restaurarlo mediante entorno explícito. Reproducir con sesión supervisor y ventana trabajador en test/integration/test_turn_recovery_tmux.py. Repetir recuperación y mensajes reales con remitentes correctos; volver a ejecutar gate.

## Dependencies and parallel work

T001–T003 preceden código. T004 precede T005. US1 independiente de persistencia y puede investigarse en paralelo; T006 precede T007/T008. US2 depende T005; T010 precede T011–T014. US3 depende contrato US2 y T015 precede T016–T018. T019–T021 integran todas las historias.

US1: tests de proveedor y revisión de validación de instalación son investigaciones separables. US2: revisar inbox y revisar cancelación son lecturas separables, pero terminal_service/database se editan secuencialmente. US3: web puede implementarse tras fijar contrato API mientras CLI/MCP se integra, en archivos distintos.

## Implementation strategy

Entregar US1 como primer incremento verificable; continuar persistencia/US2 y después consumidores/US3. No publicar ni hacer commits. Cada checkbox se marca solo después de evidencia. T020 permanece pendiente si prueba real no se ejecuta o no satisface SC-006.

## Phase 7: Convergence

- [x] T023 Incluir `CAO_API_HOST` y `CAO_API_PORT` entre las variables heredadas por el servidor MCP de Codex y probar que sobreviven al comando generado (SC-006, partial).
- [x] T024 Traducir permisos de herramientas MCP al identificador normalizado que exige OpenCode v2 y probar el permiso de `cao-mcp-server` en la configuración privada del agente (SC-006, partial).
- [x] T025 Hacer requerido el MCP propio de CAO en el arranque de Codex y configurar timeout suficiente; probar que solo afecta a ese servidor y que la config de inicio se refleja en el comando (FR-018).
- [x] T026 Aislar los datos OpenCode v2 por terminal, conservar acceso a autenticación local y esperar la señal del MCP propio conectado antes de completar la inicialización; probar espera, timeout y limpieza (FR-018).
- [x] T027 Repetir la prueba real aislada y demostrar que Codex y OpenCode v2 envían `send_message` recibido por Claude antes de marcar T020/SC-006 completos (SC-007).
- [x] T028 Reproducir y corregir la lectura del logfmt real de OpenCode y la espera de MCP explícitamente deshabilitado; verificar aislamiento por nombre y configuración v1/v2 (FR-019).
- [x] T029 Reproducir y corregir el timeout y la carga temprana del MCP propio en Claude; preservar overrides del entorno y política de terceros con identificación compartida (FR-020).
- [x] T030 Corregir captura explícita del log privado de OpenCode; verificar flags, redirección y señal real antes de readiness (FR-021).

## Investigación adicional del 2026-10-05

- [x] T031 Capturar stderr y salida del MCP, vigilar `/health`, volcar pilas del servidor, reproducir captura con tmux privado suspendido y ejecutar contratos de inbox/recibos. Evidencia: investigacion-problemas-20261005.md; 66 pruebas pasan. La aceptación integral sigue pendiente.
- [x] T032 Acotar las operaciones tmux de lectura/entrega/cleanup conservando incertidumbre y evitando reentrega después de un posible pegado; probar transporte suspendido y cleanup. La causa del timeout HTTP histórico sigue sin reconstruirse; la colaboración actual no lo reproduce.
- [x] T033 Resolver el contrato entre fase de delegación, recibo del turno y callback; probar identidad/generación, reinicio, cancelación, orden y entrega única, y repetir la ida/vuelta real Claude/Codex/OpenCode con recursos suficientes.

### Pasos ejecutables de T032/T033

- [x] T034 Reproducir timeout y cleanup con `test/clients/test_tmux_transport.py`; implementar transporte acotado y adaptar el cliente; probar servidor tmux suspendido/reanudado real.
- [x] T035 Reproducir ausencia del contrato de fases con `test/providers/test_receipt_runtime_identity.py`; corregir prompt compartido y sincronizar protocolos/perfiles; ejecutar suites de recibos/inbox sin liberar fences no verificados.
- [x] T036 Ejecutar regresiones afectadas, composición, revisión de incremento y prueba real instrumentada; registrar limitaciones de memoria/autenticación sin atribuir éxito a pruebas unitarias.
- [x] T037 Reproducir y corregir entrega inicial prematura de Claude mediante señal privada del descubrimiento MCP; probar nonce/timeout/terceros y handshake stdio real antes de repetir colaboración.

### Ampliación de validación — 2026-10-05

La segunda ronda y el navegador/HTTP por separado ya tienen evidencia en [pruebas-reales-adicionales-20261005.md](pruebas-reales-adicionales-20261005.md). T020 conserva su estado pendiente por el escenario integral SC-006. Se registran tres timeouts de salud y las discrepancias reproducidas del contrato de la demo; no se crean tareas de implementación nuevas en esta petición de validación.

### Auditoría profunda y correcciones autorizadas — 2026-10-05

- [x] T038 Reproducir y corregir UTF-8/límite documentado en demo/api/server.py, CONTRACT.md, README.md y tests/test_server.py con evidencia red/green sin mutar data/tasks.json (FR-026).
- [x] T039 Reproducir con fixture real y corregir contaminación LAST de OpenCode en providers/opencode_cli.py; cubrir eco/párrafos/recibo actual/turno antiguo y frontend/API consumidores (FR-027).
- [x] T040 Auditar salud, middleware, montaje/PATH y callbacks async; reproducir frontera lenta y corregir causa demostrada con prueba concurrente (FR-029).
- [x] T041 Conservar runner reproducible con verificación de secuencias/recibos/callbacks nuevos y marcador envuelto; repetir proveedores reales sin eludir reconciliación (FR-028).
- [x] T042 Ejecutar pruebas afectadas, arquitectura/gate y composición real; revisar diff del incremento y redactar auditoria-profunda-correcciones-20261005.md con causas, recursos y límites (FR-026–029).

Dependencias: T038/T039/T040 requieren diagnóstico y TDD propios; T041 integra T039/T040; T042 depende de las verificaciones anteriores. Se ejecuta en serie para controlar recursos compartidos.

- [x] T043 Reproducir y corregir la colisión de snapshots del mismo run_id en services/script_runner.py; probar exclusividad, permisos, ejecución y limpieza independiente con archivos y procesos reales (FR-030).

Evidencia de T038–T043: [auditoria-profunda-correcciones-20261005.md](auditoria-profunda-correcciones-20261005.md); suite final 483 passed, gate PASS y colaboración real final de dos rondas. T020/SC-006 conserva su aceptación pendiente.

Cierre posterior de T020/SC-006: [aceptacion-integral-20261005.md](aceptacion-integral-20261005.md), implementación coordinada y Chromium sobre el mismo artefacto, sin forzar recibos. Las anotaciones anteriores de aceptación pendiente son históricas.

- [x] T044 Investigar el cierre abrupto del servidor durante el volcado de pilas: capturar señal/core/traza nativa, comparar watchdog/control y distinguir causa reproducida de atribución histórica; registrar investigacion-cierre-nativo-20261005.md. Sin cambiar producto sin mecanismo demostrado.

- [x] T045 Reproducir el watchdog inseguro del runner con una regresión nativa, sustituirlo por muestreo desde Python bajo GIL preservando faulthandler.enable, y verificar tests/gate/colaboración real; documentar alcance y atribución histórica (FR-031).
