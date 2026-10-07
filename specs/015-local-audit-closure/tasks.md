---
description: "Dependency-ordered tasks for closing local CAO audit gaps"
---

# Tasks: Cierre de brechas locales de auditoría

**Input**: Design documents from specs/015-local-audit-closure/

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/audit-closure.md, quickstart.md

**Tests**: Las regresiones van primero. Para cada comportamiento nuevo, implementar la prueba, confirmar que detecta el problema y después corregirlo. Las pruebas de aceptación entre procesos y de proveedores se ejecutan cuando llegue su fase.

**Organization**: Las tareas siguen las historias del spec. Los specs 009–013 conservan los contratos detallados de cada dominio.

**Estado de ejecución**: los controles se acreditan en `implementation-evidence.md`. Marcar una tarea de ejecución y registro no convierte un gate fallido en aprobado: el control global de tipos bloquea el cierre completo y la publicación.

## Phase 1: Setup

**Purpose**: Reutilizar los entornos, comandos y fixtures actuales.

Se reutiliza el entorno existente. `packaging`, ya resuelto en el lock, se declara como dependencia directa para la compatibilidad de plugins. Las rutas de pruebas nuevas se crean en su tarea de regresión.

## Phase 2: Foundational

**Purpose**: Preparar infraestructura compartida solo si una historia la necesita.

La vinculación del propietario requiere añadir `requester_principal_id` nullable mediante una migración idempotente y compatible con lectores anteriores. Los tickets temporales de eventos tienen alcance local, vencimiento y uso único. No añadir infraestructura alojada.

---

## Phase 3: User Story 1 — Confiar en el resultado de cada tarea local (Priority: P1) — MVP

**Goal**: Hacer concordar estado terminal, recibo, resultado y disponibilidad del proyecto, incluso ante carreras de cancelación.

**Independent Test**: Completar, fallar y cancelar tareas; competir cancelación con finalización; consultar el estado desde el servicio y las superficies públicas y comprobar un único resultado terminal persistente.

### Tests for User Story 1

- [x] T001 [P] [US1] Añadir una regresión que demuestre que una cancelación persiste estado y recibo terminal coherentes, y que una consulta con el par no disponible conserva el recibo conocido y señala indisponibilidad, en test/services/test_local_peer_recovery.py
- [x] T002 [P] [US1] Cubrir la carrera finalización-cancelación y que la reserva siga activa mientras el trabajador puede escribir en test/services/test_work_reservations.py
- [x] T003 [P] [US1] Crear pruebas de rutas para que la consulta pública refleje el mismo recibo terminal en test/api/test_local_coordination_routes.py
- [x] T004 [P] [US1] Cubrir el recibo cancelado devuelto por el transporte MCP local en test/mcp_server/test_local_peer_transport.py

### Implementation for User Story 1

- [x] T005 [US1] Persistir estado y resultado de cancelación como una única transición terminal en src/cli_agent_orchestrator/services/local_peer_service.py
- [x] T006 [US1] Mantener la reserva del proyecto hasta que el proceso deje de escribir y reconciliar resultados terminales sin reemplazar uno más reciente en src/cli_agent_orchestrator/services/local_peer_service.py y src/cli_agent_orchestrator/services/local_peer_registry.py
- [x] T007 [US1] Exponer el recibo terminal ya persistido de forma coherente en src/cli_agent_orchestrator/api/local_coordination_routes.py y src/cli_agent_orchestrator/mcp_server/server.py

**Checkpoint**: Cada tarea terminal tiene estado y recibo coherentes; el proyecto queda protegido mientras haya escritura activa.

---

## Phase 4: User Story 2 — Coordinar pares y consultar eventos sin perder control (Priority: P1)

**Goal**: Mantener identidad, permisos, sesiones, continuidad de eventos y transporte de credenciales coherentes entre instancias locales.

**Independent Test**: Conectar dos instancias CAO en la misma PC; revocar un par, consultar sesiones vacías y no disponibles, reconectar clientes durante nuevos eventos y rechazar mensajes de otro origen.

### Tests for User Story 2

- [x] T008 [P] [US2] Añadir casos de vínculo entre identidad del par, proyecto, acción y propietario de la asignación en test/services/test_local_peer_auth.py y test/api/test_local_coordination_routes.py
- [x] T009 [P] [US2] Crear casos de sesiones vacías, proceso par no disponible y desaparición durante la consulta en test/api/test_local_coordination_routes.py
- [x] T010 [P] [US2] Comprobar autorización y reanudación de AG-UI sin bearer reutilizable en la URL en test/api/test_agui_auth_hardening.py y test/api/test_agui_stream_reconnect.py
- [x] T011 [P] [US2] Comprobar que el WebSocket de terminal no expone su credencial en la URL en web/src/test/api.test.ts y test/api/test_ws_auth.py
- [x] T012 [P] [US2] Añadir una regresión para el evento que llega entre la carga de historial y la suscripción MCP Apps en cao_mcp_apps/src/test/integration.test.tsx
- [x] T013 [P] [US2] Rechazar la primera respuesta correlacionada si su origen o ventana no corresponde al host esperado en cao_mcp_apps/src/test/integration.test.tsx
- [x] T014 [P] [US2] Cubrir cancelación por timeout de solicitudes de autenticación del navegador en web/src/test/browser-auth.test.ts
- [x] T015 [P] [US2] Comprobar que los parámetros de credencial aceptados se ocultan en registros de acceso en test/utils/test_logging.py
- [x] T016 [P] [US2] Cubrir que el TUI no envía su bearer local a destinos HTTP no confiables en tui/src/server.rs
- [x] T017 [P] [US2] Cubrir que una llamada a otro host no recibe por accidente el token local del servidor en test/utils/test_orchestration.py

### Implementation for User Story 2

- [x] T018 [US2] Comprobar proyecto, ámbito y propietario autenticado antes de aceptar operaciones del par en src/cli_agent_orchestrator/api/local_coordination_routes.py y src/cli_agent_orchestrator/api/main.py
- [x] T019 [US2] Devolver resultados de sesión que distingan lista vacía, sesión desaparecida y par no disponible en src/cli_agent_orchestrator/api/local_coordination_routes.py
- [x] T020 [US2] Cerrar la brecha historial-suscripción de eventos MCP Apps mediante un cursor común y backfill desde ese cursor en src/cli_agent_orchestrator/mcp_server/app_tools.py, src/cli_agent_orchestrator/api/main.py y cao_mcp_apps/src/event-stream/EventStreamView.tsx
- [x] T021 [US2] Autenticar los flujos de eventos con permisos vigentes y tickets breves de un solo uso cuando EventSource no admita headers en src/cli_agent_orchestrator/api/main.py y src/cli_agent_orchestrator/services/event_stream_ticket.py
- [x] T022 [US2] Validar origen y fuente de ventana antes de aceptar una respuesta MCP App correlacionada en cao_mcp_apps/src/shared/mcpApp.ts
- [x] T023 [US2] Sustituir el bearer reutilizable del WebSocket de terminal por un mecanismo de sesión o ticket de un solo uso en web/src/api.ts y src/cli_agent_orchestrator/api/main.py
- [x] T024 [US2] Aplicar límite de tiempo y cancelación a solicitudes, renovaciones y lecturas de autenticación del navegador en web/src/auth.ts
- [x] T025 [US2] Ocultar también el parámetro token en los registros y conservar redacción para las demás credenciales admitidas en src/cli_agent_orchestrator/utils/logging.py
- [x] T026 [US2] Limitar el envío del bearer local del TUI a un destino local validado y mantener bloqueadas las redirecciones en tui/src/server.rs
- [x] T027 [US2] Evitar que el token local de CAO se adjunte a destinos no autorizados por el contrato de la llamada en src/cli_agent_orchestrator/utils/orchestration.py

**Checkpoint**: Un par sin permiso vigente no puede actuar; sesiones vacías e instancias no disponibles se distinguen; los clientes recuperan eventos y no exponen credenciales reutilizables.

**Existing behavior to preserve**: La línea temporal de eventos de workflows en src/cli_agent_orchestrator/api/main.py y su cliente web en web/src/components/workflow/useEventFollow.ts ya admiten cursor/replay y autorización. Mantener sus pruebas en web/src/test/workflow-sse.test.ts y test/api/test_workflow_events_sse.py; no volver a implementar esa ruta.

---

## Phase 5: User Story 3 — Usar workflows que se recuperan sin atascarse (Priority: P1)

**Goal**: Hacer avanzar trabajo elegible, limitar reintentos, liberar recursos terminados y validar la configuración antes de ejecutar.

**Independent Test**: Procesar más trabajo del que cabe en una ronda, provocar fallos de observación persistentes, finalizar tareas sucesivas y presentar opciones o campos inválidos.

### Tests for User Story 3

- [x] T028 [P] [US3] Cubrir avance justo de tareas elegibles en rondas sucesivas en test/services/test_continuation_responsiveness.py
- [x] T029 [P] [US3] Cubrir espera acotada y recuperación después de errores persistentes en test/services/test_terminal_observation_recovery.py
- [x] T030 [P] [US3] Comprobar que limpiar una tarea terminada no borra una tarea nueva de la misma ejecución en test/services/test_integration_008_continuation.py
- [x] T031 [P] [US3] Añadir validación de rechazo de campos desconocidos en test/models/test_workflow.py
- [x] T032 [P] [US3] Añadir regresión para impedir que opts reemplace identidad o variables de entorno administradas en test/services/test_integration_008_continuation.py

### Implementation for User Story 3

- [x] T033 [US3] Seleccionar trabajo elegible sin permitir que filas antiguas no elegibles agoten cada lote en src/cli_agent_orchestrator/services/workflow_continuation_driver.py
- [x] T034 [US3] Aplicar backoff acotado ante fallos repetidos de observación terminal en src/cli_agent_orchestrator/services/terminal_observation_recovery.py
- [x] T035 [US3] Retirar manejadores de tareas finalizadas sin perder resultados ni eliminar generaciones nuevas en src/cli_agent_orchestrator/services/workflow_continuation_driver.py
- [x] T036 [US3] Aplicar primero la identidad y el entorno administrados antes de incorporar opciones del usuario en src/cao_workflow/__init__.py
- [x] T037 [US3] Rechazar campos de workflow desconocidos con una ruta de error clara en src/cli_agent_orchestrator/models/workflow.py

**Checkpoint**: El trabajo elegible progresa; los fallos persistentes no crean ciclos rápidos; la limpieza conserva resultados y configuraciones inválidas no alteran la identidad.

---

## Phase 6: User Story 4 — Publicar cambios validados y probar proveedores sin exponer cuentas (Priority: P1)

**Goal**: Aplicar evidencia ligada al contenido exacto en publicación y usar perfiles aislados en pruebas reales de proveedores.

**Independent Test**: Comparar publicación manual y programada con evidencia aprobada, omitida, fallida o desactualizada; probar aislamiento y limpieza de credenciales de proveedor.

### Tests for User Story 4

- [x] T038 [P] [US4] Cambiar la política para que el disparador manual exija los mismos prerrequisitos que el programado en test/test_integration_008_release_policy.py
- [x] T039 [P] [US4] Bloquear pruebas reales sin hogar de autenticación aislado y comprobar limpieza en éxito, error y cancelación en test/e2e/test_real_provider_matrix.py
- [x] T040 [P] [US4] Comprobar que gates omitidos, fallidos o no disponibles no se cuentan como aprobación en test/test_integration_008_ci_policy.py
- [x] T041 [P] [US4] Crear pruebas de manifiesto con versión, revisión, hashes e inventario de dependencias en test/scripts/test_build_release_manifest.py

### Implementation for User Story 4

- [x] T042 [US4] Hacer que publicación manual y programada usen el mismo preflight para el contenido candidato en .github/workflows/release.yml
- [x] T043 [US4] Exigir un hogar aislado sin fallback a Path.home y limpiar los recursos temporales en .github/workflows/real-provider-e2e.yml y test/e2e/test_real_provider_matrix.py
- [x] T044 [US4] Alinear la matriz Python con las versiones anunciadas, cubrir dependencias de los entornos distribuidos y hacer bloqueantes los controles obligatorios en .github/workflows/ci.yml, .github/dependabot.yml, .github/workflows/cargo-deny.yml y pyproject.toml
- [x] T045 [US4] Generar un manifiesto local verificable de artefactos con stdlib y adjuntarlo a los flujos de publicación Python y GitHub en scripts/build_release_manifest.py, .github/workflows/publish-to-pypi.yml y .github/workflows/release.yml

**Checkpoint**: La publicación manual y programada valida el mismo contenido; las pruebas de proveedor no recurren a credenciales generales; los controles omitidos permanecen visibles.

---

## Phase 7: User Story 5 — Revisar y habilitar plugins locales con información de confianza (Priority: P2)

**Goal**: Mostrar evidencia de origen, contenido y permisos del plugin, y aplicar una política de confianza local antes de habilitarlo.

**Independent Test**: Revisar un plugin con procedencia conocida, alterado, incompatible o sin evidencia; comprobar la decisión local antes de instalarlo o habilitarlo.

### Tests for User Story 5

- [x] T046 [P] [US5] Cubrir referencia, commit resuelto, contenido alterado y procedencia no verificada en test/agent_plugins/test_resolver.py y test/agent_plugins/test_provider_provenance.py
- [x] T047 [P] [US5] Ampliar la cobertura existente de no concesión automática con procedencia ausente, contenido cambiado después de revisión y aprobación local del contenido exacto, sin repetir los casos actuales, en test/agent_plugins/test_no_auto_grant.py
- [x] T048 [P] [US5] Añadir casos CLI para mostrar evidencia de confianza y bloquear plugins según la política local en test/agent_plugins/test_cli.py

### Implementation for User Story 5

- [x] T049 [US5] Conservar procedencia, revisión resuelta e integridad en el registro de instalación del plugin en src/cli_agent_orchestrator/agent_plugins/models.py y src/cli_agent_orchestrator/agent_plugins/installer.py
- [x] T050 [US5] Mostrar el resumen de productor, origen, revisión, compatibilidad y permisos antes de habilitar y aplicar la política local en src/cli_agent_orchestrator/cli/commands/agent_plugin.py y src/cli_agent_orchestrator/agent_plugins/installer.py
- [x] T051 [US5] Documentar qué demuestra un pin de contenido, qué evidencia falta y cómo revisar permisos en docs/agent-plugins.md

**Checkpoint**: El operador distingue contenido fijado de identidad verificada del publicador; una instalación no concede permisos implícitamente.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Registrar aceptación, ejecutar controles de composición y dejar instrucciones acordes con la implementación.

- [x] T052 [P] Actualizar comandos focalizados y prerrequisitos locales después de implementar las regresiones en specs/015-local-audit-closure/quickstart.md
- [x] T053 Ejecutar aceptación con dos o más procesos CAO en la misma PC y registrar resultados, hashes y comportamiento de cancelación en specs/015-local-audit-closure/implementation-evidence.md
- [x] T054 Ejecutar suites Python, Web, MCP Apps y TUI pertinentes y registrar comandos, resultados y bloqueos en specs/015-local-audit-closure/implementation-evidence.md
- [x] T055 Ejecutar project-composition-check y documentar la revisión entre subsistemas en specs/015-local-audit-closure/composition-review.md
- [x] T056 Revisar el diff completo de implementación y actualizar la guía de validación al comportamiento entregado en specs/015-local-audit-closure/quickstart.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Reusar comandos, bases de datos y perfiles presentes; crear solo las rutas de prueba declaradas como nuevas.
- **Foundational (Phase 2)**: Reusar infraestructura existente. La vinculación verificada del propietario exige una migración aditiva e idempotente (`requester_principal_id` nullable); las tareas heredadas sin vínculo requieren recuperación local explícita. Implementar el almacenamiento efímero de tickets antes de sus consumidores US2.
- **User Stories (Phases 3–7)**: Cada historia aplica pruebas primero. US1 es el MVP porque la incoherencia del recibo se reprodujo. US2 y US3 deben pasar revisión integrada por sus límites de identidad, estado y recuperación. US4 y US5 son independientes del ciclo de tareas.
- **Polish (Phase 8)**: Requiere que las historias acordadas estén implementadas y que los bloqueos por proveedor se registren como tales.

### User Story Dependencies

- **US1 (P1)**: Sin dependencias; entrega mínima.
- **US2 (P1)**: Se compone con los recibos de US1 y necesita aceptación entre procesos locales.
- **US3 (P1)**: Puede desarrollarse en paralelo con US2, con revisión conjunta de recuperación y recursos activos.
- **US4 (P1)**: Independiente del runtime local; toca automatización de CI y publicación.
- **US5 (P2)**: Independiente de las demás historias; conserva confianza y permisos locales.

### Parallel Opportunities

- En US1, los casos de persistencia, reservas y transporte MCP viven en archivos de prueba distintos.
- En US2, los cambios Web, MCP Apps y TUI pueden prepararse en paralelo después de acordar el contrato común de credenciales.
- En US3, recuperación y validación de configuración usan módulos y pruebas distintos; cambios del driver deben coordinarse entre sí.
- En US4, aislamiento de proveedores, política de release y manifiesto de artefactos se pueden desarrollar en paralelo con sus pruebas separadas.
- US4 y US5 pueden ejecutarse en paralelo.

No dividir cambios simultáneos sobre el mismo archivo. Revisar juntos las interfaces de ticket, propietario autenticado y cursor antes de integrar.

## Phase 9: Convergence

El usuario solicita corregir todos los bloqueos locales restantes. La ejecución completa reproduce 531 errores de tipos en 81 archivos; el E2E MCP Apps no arranca por una dependencia de sistema ausente. Se conservan los controles existentes y su configuración efectiva, sin nuevas supresiones.

- [X] T057 Corregir contratos de tipos de modelos y clientes de datos y comprobar persistencia real, per FR-018/T044 (partial).
- [X] T058 Corregir contratos de procesos, reservas y Work sin debilitar fences o cleanup, per FR-018/T044 (partial).
- [X] T059 Corregir contratos de API, autenticación y AG-UI preservando autorización y replay, per FR-018/T044 (partial).
- [X] T060 Corregir contratos de memoria, vault y recuperación con pruebas de datos reales, per FR-018/T044 (partial).
- [X] T061 Corregir contratos de terminales, proveedores, workflows y transporte, per FR-018/T044 (partial).
- [X] T062 Corregir los demás contratos MCP/servicios/CLI que bloquean el gate de tipos obligatorio, per FR-018/T044 (partial).
- [X] T063 Resolver las dependencias locales del navegador y ejecutar el E2E MCP Apps sin omitir aserciones, per SC-003/T054 (partial).
- [X] T064 Repetir mypy completo, suites pertinentes, aceptación, composición, revisión independiente, diff y actualidad del grafo; registrar resultados finales, per FR-018/SC-010/T054–T056 (cierre local verificado; evidencia y límites en types-root-evidence.md).
- [X] T065 Cerrar objetos SQLite ejecutables no declarados antes de restaurar/copiar autoridad: reproducir triggers/vistas ajenos, preservar catálogos históricos y comprobar rechazo antes de las escrituras de restauración, per FR-010–012/FR-018/T060 (composition-review finding; requiere repetir T064 después de corregirlo).
- [X] T066 Alinear el catálogo Rust con los nueve comandos Click nuevos de pares/confianza: reproducir el control bidireccional, registrar variantes/orden/filas/rutas y clasificar todos HIDE según la política existente; verificar cardinalidad 141, distribución y pruebas Rust, per FR-004–006/FR-016/FR-018/T019/T046 (cross-language contract finding; requiere repetir T064).
- [X] T067 Resolver el ratchet backend medido en 85.53% frente al mínimo existente de 87% mediante pruebas funcionales de ciclo de pares, composición de procesos, contratos de backend y snapshots privados; conservar el baseline, inclusiones y controles de autoridad, comprobar ambos informes reales y repetir la selección completa, per FR-004–006/FR-010–012/FR-018/T044/T064 (coverage-gate finding).

- [X] T068 Corregir el descubrimiento de curator contra el campo real `tmux_session` del inventario SQLite; reproducir con requester/curator persistidos, conservar el aislamiento por sesión y verificar dispatch, rechazo de otra sesión y fallback, per FR-010–012/FR-018/T064/T067 (producer-consumer mismatch encontrado por cobertura funcional).

## Phase 10: Validation and fork integration

El usuario amplía la aceptación a las comprobaciones antes no ejecutadas y
autoriza expresamente commits y push a `main` del fork `origin`
(`FernanMoreno/cli-agent-orchestrator`). Se conserva el árbol previo y se usa
un candidato nativo aislado. Una cuenta, cuota o capacidad de host ausente
se registra con su evidencia; nunca se transforma en aprobación.

- [X] T069 Ejecutar la selección completa de CI con dependencias bloqueadas en Python 3.10–3.14; registrar resultados, cobertura, omisiones y actualidad de fuentes, per FR-018/T044/T064.
- [X] T070 Ejecutar aceptación real Docker/Bubblewrap y controles de aislamiento, dispositivos, shells y gitleaks disponibles; provisionar únicamente runners/herramientas revisados y documentar límites del host, per FR-018/SC-010/T054–T056.
- [X] T071 Revisar autenticación/modelos de todos los adaptadores reales y ejecutar las celdas utilizables con hogares privados; observar cuota sólo con una ventana explícita y acotada, registrar proveedores y escenarios bloqueados, per FR-009/FR-014/T040/T044.
- [X] T072 Revisar el candidato de publicación, contratos, secretos, arquitectura, tipos y suites de clientes/Rust; comprobar enlaces, diff y evidencia antes de integrar, per FR-013/FR-015/FR-018/T038/T043/T056/T064.
- [ ] T073 Crear commits revisables del estado validado, integrar sin reescribir historial y hacer push no forzado a `origin/main`; verificar el SHA remoto y registrar resultados de CI, según autorización expresa del usuario.

- [X] T074 Corregir dependencias vulnerables con versiones compatibles y locks mínimos; reproducir el gate Trivy exacto, comprobar builds y conservar severidades/política, per FR-018/T072.
- [X] T075 Eliminar la dependencia del reloj real en la regresión Web de expiración del ticket tras aprobación; conservar la caducidad de producción y las aserciones de rechazo, per FR-008/T072.

- [X] T076 Reproducir y corregir los fallos de composición descubiertos por la matriz Python y el navegador Web (aislamiento de perfiles, estado de fixtures, cierre de tmux, cancelación de plugins en Python 3.10 y logout tras reinicio), conservar autorización y rechazos ante indisponibilidad, y repetir los gates afectados, per FR-004–009/FR-016–018/T069/T072.

- [X] T077 Cerrar explícitamente la conexión SQLite propiedad de `_migrate_workflow_index`, conservar commit/rollback y compatibilidad del catálogo; reproducir éxito/error con transacciones reales antes del cambio, comprobar ausencia del aviso de esa conexión y repetir la matriz congelada, per FR-010–012/FR-018/SC-010/T069.

- [X] T078 Rechazar claves públicas Ed25519 de orden pequeño o codificación no canónica tanto al emparejar como al verificar grants existentes; reproducir la aceptación criptográfica y conservar las firmas de claves generadas, separar la regresión de firma incorrecta con clave normal y repetir la matriz congelada, per FR-004–006/FR-018/T069/T076.

- [X] T079 Cerrar las otras 22 conexiones SQLite de arranque identificadas por trazas reales (20 migradores, verificador Beads y catálogo esperado Work); probar commit/rollback y cierre en éxito/error, preservar conexiones prestadas y catálogos, comprobar arranque sin avisos de esos propietarios y repetir matriz con TMPDIR coherente, per FR-010–012/FR-018/SC-010/T069/T077.

- [X] T080 Cerrar las conexiones propias del journal, aprobaciones, specs, planes, generación, recuperación y catálogo Web; proteger la propiedad antes de devolver una conexión si falla su configuración (browser, Work y migración de terminales); conservar transacciones, commits parciales, errores y conexiones prestadas; reproducir avisos/asignaciones con consultas reales y éxito/error antes del cambio, y repetir matriz congelada, per FR-007–012/FR-018/SC-010/T069/T079.

- [X] T081 Aislar el perfil del servidor gestionado al reemplazar HOME y CAO_HOME_DIR juntos; reproducir en un proceso hijo la herencia del perfil del runner, conservar overrides explícitos, verificar el rechazo de escape por HTTP real y repetir la matriz completa congelada, per FR-004–006/FR-018/T069/T076.

- [X] T082 Conservar cancelación de llamadas remotas cuando el envío o el resultado termina en el mismo turno en Python 3.10/3.11; reproducir ambas carreras antes del cambio, poseer y drenar la espera limitada, conservar deadlines/reconciliación y repetir matriz completa, per FR-001–003/FR-010–012/T069/T076.

- [X] T083 Usar el parser TOML declarado para Python 3.10 en los instaladores Docker y su preflight MCP; reproducir los cuatro fallos existentes, conservar políticas de montajes y validación initialize, y repetir matriz completa sin omitir versiones ni pruebas, per FR-013–015/FR-018/T069.

- [X] T084 Hacer explícita la retención de bytes borrados sólo en la fixture privada que prueba compactación de credenciales SQLite; reproducir secure_delete predeterminado en Python 3.14, conservar la ausencia de secretos en el artefacto y verificar builds con ambos defaults, cerrar su conexión observadora después del contexto transaccional y conservar política de producción, per FR-013/FR-017/FR-018/T069/T076.

- [X] T085 Evitar que entradas de expresiones excesivas derriben el parser nativo de Python 3.10 durante lint; reproducir SIGSEGV en proceso hijo, rechazar complejidad antes de ast.parse con límites documentados conservando scripts normales largos, errores syntax y no ejecución; verificar todas las versiones y consumidores, per FR-010–012/FR-018/T069.

- [X] T086 Observar el fin de la transacción de reintento antes del cierre del journal y exigir el cierre después, conservando comparación de autorización durable y estado/contador; reproducir el acceso inválido actual y repetir matriz, per FR-003–006/FR-010–012/T080/T069.

- [X] T087 Esperar de forma acotada la observabilidad del entorno del hijo real antes de verificar el canal privado de credenciales; reproducir lecturas transitorias vacías, rechazar entorno sin FD, credencial expuesta y proceso salido; conservar la barrera previa a efectos Work y repetir la matriz congelada, per FR-005–006/FR-009–012/T069/T076.

## Implementation Strategy

### MVP First

1. Ejecutar primero las regresiones US1 y confirmar que reproducen la incoherencia del recibo o la carrera.
2. Corregir estado, resultado y reserva temporal; validar el checkpoint antes de ampliar el cambio.

### Incremental Delivery

1. Completar US1 y probar el ciclo terminal entre instancias locales.
2. Completar US2 manteniendo intactos el cursor/replay ya presente en la línea temporal de workflows y su cliente Web.
3. Completar US3 y verificar recuperación, limpieza y configuración.
4. Completar US4 y comprobar evidencia de publicación y aislamiento de proveedores.
5. Completar US5 y comprobar revisión local de plugins.
6. Ejecutar aceptación entre procesos, suites aplicables y revisión de composición.

## Notes

- El árbol ya contiene cambios previos en numerosas fuentes, pruebas y automatizaciones. Antes de implementar, revisar el diff actual y conservar cambios ajenos a esta feature.
- Las tareas que nombran un archivo de prueba nuevo indican dónde crear la cobertura faltante; las rutas existentes se reutilizan.
- La reanudación por cursor en `GET /workflows/runs/{id}/events` y `useEventFollow` ya existe; el hueco de historial-suscripción señalado aquí corresponde a MCP Apps. En el estado inicial AG-UI admitía cursores y conservaba bearer en query; la implementación cerrada conserva cursores y usa tickets de un solo uso renovados en cada reconexión.
- Una prueba bloqueada por región, cuenta o disponibilidad de proveedor se informa como no disponible, nunca como aprobación.
- La aceptación local inicial no incluía commit ni push. Phase 10 registra la autorización posterior para commits y push a `origin/main`; no autoriza crear una release o publicar paquetes.
