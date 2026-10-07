# Tasks: Colaboración de agentes

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/collaboration.md](contracts/collaboration.md).
**Tests**: Requeridos por FR-018 y SC-001–SC-008. Ninguna casilla significa que exista evidencia antes de ejecutarla. Corrección condicionada a fallo reproducido puede cerrarse sin cambio con prueba que demuestra cumplimiento.

## Phase 1 — Setup

- [x] T001 Registrar referencia upstream, impacto Graphify contrastado y estado inicial de instalación/proveedores sin secretos en specs/006-agent-collaboration/composition-review.md.
- [x] T002 Crear proyecto nuevo del Escritorio, run_id, directorio privado de evidencia y registro de recursos propios para scripts/agent_collaboration_acceptance.py, preservando proyectos existentes.

## Phase 2 — Foundational

- [x] T003 Preparar fixtures con SQLite/tmux/HTTP reales y estado aislado en test/integration/test_agent_collaboration.py, sin llamadas a modelos en pruebas deterministas.
- [x] T004 Implementar preflight, plazos, perfiles de tres proveedores, control de instancia temporal y limpieza propia en scripts/agent_collaboration_acceptance.py; probar errores y limpieza en test/scripts/test_agent_collaboration_acceptance.py antes de lanzar llamadas reales.

## Phase 3 — US1: Arranque conectado (P1)

**Prueba independiente**: Los tres proveedores arrancan y consultan el coordinador autenticado desde el MCP propio.

- [x] T005 [P] [US1] Reproducir propagación de API/auth al MCP de Codex en test/providers/test_codex_provider_unit.py, incluyendo ausencia/caducidad de bearer y sin secretos en argv.
- [x] T006 [P] [US1] Probar entorno y configuración privada del MCP de Claude en test/providers/test_claude_code_unit.py y de OpenCode en test/utils/test_opencode_config.py/test/providers/test_opencode_v2.py.
- [x] T007 [US1] Corregir reenvío necesario solo al MCP propio en src/cli_agent_orchestrator/utils/mcp_resolution.py y providers/{codex,claude_code,opencode_cli}.py, preservando overrides y MCP externos; ejecutar regresiones T005–T006.
- [x] T008 [US1] Reproducir y corregir autenticación de lanzamiento CLI normal en test/cli/commands/test_launch.py y src/cli_agent_orchestrator/cli/commands/launch.py, sin cambiar su ruta Work.
- [x] T009 [US1] Verificar primera sesión, ventanas posteriores y renovación de credencial con agentes vivos en test/clients/test_tmux_merge_extra_env.py y test/integration/test_agent_collaboration.py; corregir solo frontera defectuosa identificada.
- [x] T010 [US1] Arrancar Claude/Codex/OpenCode reales mediante scripts/agent_collaboration_acceptance.py y registrar estado/identidad/API conectada sin declarar éxito si un proveedor falta.

## Phase 4 — US2: Delegación y resultados (P1)

**Prueba independiente**: Dos assign retornan dos resultados al caller y una tarea handoff retorna al llamador.

- [x] T011 [US2] Probar perfiles heterogéneos, caller y timeout recuperable usando test/mcp_server/test_assign.py, test/mcp_server/test_handoff.py y test/utils/test_orchestration.py; solo corregir src/cli_agent_orchestrator/utils/orchestration.py si se reproduce fallo.
- [x] T012 [US2] Ejecutar Claude supervisor que delega API a Codex y frontend a OpenCode, resultados por send_message y handoff corto; conservar trazas saneadas mediante scripts/agent_collaboration_acceptance.py.

## Phase 5 — US3: Mensajes entre trabajadores (P1)

**Prueba independiente**: Pregunta/respuesta entre hermanos, mensaje pendiente a receptor ocupado y una sola inyección bajo concurrencia.

- [x] T013 [P] [US3] Extender casos de destinatario explícito/inexistente y remitente en test/mcp_server/test_send_message.py y test/api/test_inbox_messages.py.
- [x] T014 [P] [US3] Verificar ocupación, concurrencia, recuperación e incertidumbre en test/services/test_inbox_service.py; preservar semántica y corregir services/inbox_service.py solo ante fallo reproducido.
- [x] T015 [US3] Hacer que Codex/OpenCode acuerden CONTRACT.md por mensajes de CAO y verificar ida/vuelta/estados desde scripts/agent_collaboration_acceptance.py, sin suplir esos mensajes desde el runner.

## Phase 6 — US4: Proyectos e integraciones (P1)

**Prueba independiente**: Archivos demo visibles en host, RO rechazado, worktrees válidos e integración externa operativa/fallo diagnosticado.

- [x] T016 [US4] Añadir regresión y soporte --workspace-readonly/modos contradictorios/manifest compatible en test/scripts/test_docker_install.py y scripts/docker_install.py.
- [x] T017 [US4] Probar admisión canónica y symlinks fuera de registro en test/integration/test_agent_collaboration.py; aplicar política personal optativa en scripts/personal_deployment.py y la frontera de validación de directorio de services/terminal_service.py/utils/path_validation.py, conservando CAO genérico.
- [x] T018 [US4] Verificar directorio heredado, worktrees locales y Git metadata externos con test/services/test_worktree_service.py y test/integration/test_agent_collaboration.py; corregir solo incompatibilidad demostrada.
- [x] T019 [US4] Comprobar integración seleccionada desde agente y error al detener exclusivamente servicio de prueba en scripts/agent_collaboration_acceptance.py, preservando scripts/docker_windows_mcp.py y servicios personales.
- [x] T020 [US4] Verificar API/frontend escritos por los agentes en la carpeta demo del Escritorio mediante HTTP y navegador; conservar README/CONTRACT.md y resultados saneados en specs/006-agent-collaboration/composition-review.md.

## Phase 7 — US5: Lifecycle y recuperación (P2)

**Prueba independiente**: Cancelación/cierre elimina descendientes; reinicio conserva persistencia sin tareas fantasma ni repetición incierta.

- [x] T021 [US5] Reproducir cancelación/teardown con hijos reales y detached en test/integration/test_agent_collaboration.py; corregir services/session_service.py/terminal_service.py y clients/tmux.py únicamente si falla la propiedad de procesos.
- [x] T022 [US5] Probar reinicio de instancia temporal con terminales activas y mensajes pending/reconcile; corregir reconciliación en src/cli_agent_orchestrator/api/main.py o propietario existente si falla y ejecutar test/api/test_lifespan_inbox.py.
- [x] T023 [US5] Probar fallo aislado de proveedor, cancelación durante inicialización y handoff timeout sin repetir tareas mediante scripts/agent_collaboration_acceptance.py; registrar pendientes si no pueden acreditarse sin alterar recursos ajenos.

## Phase 8 — Cierre transversal

- [x] T024 Documentar agentes en contenedor principal, conexiones externas, permisos compartidos, límites de autoridad y uso/limpieza de aceptación en docs/agent-collaboration.md y docs/docker-installation.md.
- [x] T025 Ejecutar regresiones aplicables, build candidato y project-composition-check; realizar system-composition-review con evidencia real y FR/SC trazables en specs/006-agent-collaboration/composition-review.md.
- [x] T026 Revisar diff, confirmar conservación de instalación personal/proyecto demo y cero procesos propios sobrantes, decidir actualización Graphify/vault y completar solo tareas acreditadas en specs/006-agent-collaboration/tasks.md.

## Dependencias

Setup T001–T002 → base T003–T004 → US1 T005–T010. US2 y US3 requieren conexión US1; US4 determinista puede prepararse tras base, pero demo real requiere US1–US3. US5 requiere procesos de prueba aislados y puede compartir fixture de base. T025–T026 dependen de todas las historias para declarar aceptación global.

```text
Setup → Foundational → US1 → US2 ─┐
                         └→ US3 ─┼→ demo US4 → US5 → cierre
Foundational → permisos US4 ─────┘
```

## Oportunidades paralelas

- US1: T005 Codex y T006 Claude/OpenCode escriben archivos distintos; T007 espera ambas.
- US2: verificaciones assign/handoff pueden leerse en paralelo, pero no editar simultáneamente orchestration.py.
- US3: T013 API/MCP y T014 inbox trabajan archivos distintos; T015 integra.
- US4: lectura de contratos de worktrees e integración puede ser paralela; runner y script de instalación se editan secuencialmente.
- US5: diagnóstico de descendientes y de reinicio puede investigarse en paralelo; cambios de lifecycle compartidos se serializan.

## Implementation Strategy

MVP: arranque conectado US1 y demostración real US2–US3 con tres proveedores. Después permisos/integraciones US4 y lifecycle US5. Cada fallo se reproduce antes de editar; conservar capacidades ya correctas. Tests antes de correcciones de comportamiento. No commit/push ni despliegue sobre instalación personal automáticos. Prerrequisito ausente → escenario pendiente con causa explícita, no reemplazo por mock ni éxito global.

## Evidencia de esta ejecución (2026-10-01)

T020 acreditada con HTTP y Playwright: crear, completar y recargar conserva tarea id4 en JSON. Claude/Codex/OpenCode trabajaron realmente en una app temporal, con assign, handoff e inbox bidireccional. La prueba usó un controlador privado fuera del repositorio y ajustes temporales de configuración; T004/T010/T012/T015 siguen pendientes del runner mantenible, las regresiones y la aceptación autónoma previstas. No se declara la feature completa ni se han cambiado fuentes de runtime. Ver composition-review.md.

## Reconciliación contra código y pruebas — 2026-10-05

[Informe detallado vigente](cierre-20261005.md): 17/26 tareas acreditadas.
T003/T021 usan también las fixtures HTTP existentes y
`test/clients/test_tmux_detached_cleanup.py`; T016–T018 tienen regresiones
adicionales en `test/scripts/test_collaboration_project_policy.py`. T017 aplica
el registro optativo mediante el entorno del contenedor y la frontera compartida,
sin duplicar política en `personal_deployment.py`. T013–T014 reutilizan contratos
ya cubiertos; no se cambia inbox sin fallo reproducido.

T009/T010/T012/T015/T019/T022/T023 siguen abiertas: Claude tiene login caducado
y faltan escenarios nativos nuevos. T025/T026 tienen sus verificaciones locales
aprobadas, pero su cierre global sigue dependiendo de todas las historias.
La imagen candidata está reconstruida y los montajes RO/RW están probados con
Docker real. No se despliega automáticamente sobre la instalación personal.


## Aceptación real tras renovar login

T010/T012/T015/T019 acreditadas por el runner mantenible completo:
Claude `8d1d6a08`, Codex `ea2944c9`, OpenCode `f74b7915`, dos rondas en
las mismas terminales, callbacks nuevos, mensajes directos con remitentes reales,
MCP externo llamado por Codex disponible/caído y handoff Codex `a674242a` con
resultado recibido por Claude. `COLLABORATION=PASS`, salida 0, proyecto preservado
y runtime eliminado. El token inicial del fixture se corrigió para incluir `sub`,
sin relajar la identidad verificada de producción. 21/26 tareas acreditadas.
El modo `--recovery` verifica los escenarios restantes; su preparación no los
acredita. Ver [informe vigente](cierre-20261005.md).


## Cierre final después de los fallos reproducidos

26/26 tareas acreditadas. Runner completo `--recovery`: salida 0, dos rondas,
peers, MCP externo disponible/caído, handoff, renovación del controlador con
agentes vivos, reinicio con pending/reconcile, fallo nativo Go, cancelación
deferred-init y timeout con un solo job/trabajador y referencia recuperable.
T009 acredita primera sesión, ventanas posteriores y bearer de control renovado;
los MCP conservan su token todavía vigente. No significa autorrenovación de
un token MCP vencido, comportamiento no exigido por los criterios de aceptación.

T025: build candidato actualizado, Docker real con diez módulos verificados,
333 regresiones, gate 5/0 y revisión de composición. T026: diff revisado, cero
procesos/contenedores propios sobrantes, datos y selector conservados, AST
actualizado sin sobrescribir grafo ajeno ni duplicar el informe en el vault.
Las notas anteriores conservan la fecha/estado de cada corte; el resultado
vigente y los límites están en [el informe de cierre](cierre-20261005.md).
