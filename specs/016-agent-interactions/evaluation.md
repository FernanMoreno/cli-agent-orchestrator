# Evaluación continua — Feature 016

> **Continuación PostgreSQL (2026-10-11):** Codex 0.162.1 con gpt-6-luna completa el canal explícito real: dos preguntas, dos respuestas humanas y dos entregas `agent_acknowledged`, verificadas contra grupo/versiones/recibos en servicio. Browser actual: 20 casos sin skips, p95 DOM 1294 ms. Imagen Docker r5 actual: instalación/upgrade/rollback 1 caso completo, 281,33 s; importación de tres almacenes y activación posterior 1 caso, 90,47 s. T020/T097 y la suite global con cobertura aún están pendientes. SC-007 completo sigue abierto por OpenCode y Hermes. Evidencia actual y cronología: [validación PostgreSQL](../021-postgresql-persistence/validation.md); la suite SQLite citada debajo es histórica.

> **Continuación vigente:** el adaptador AG-UI ya está implementado y pasa 18 casos dirigidos y 517 de regresión. Docker real, incluida instalación/upgrade/rollback de imagen congelada, pasa. La aceptación host y nueva puerta completa siguen en ejecución en [agui-host-validation.md](agui-host-validation.md). Los resultados completos de abajo corresponden a la versión anterior; T064 ya no está parcial.

> **Validación local completa finalizada:** 16949 tests Python pasados, 119 omisiones explícitas, cero fallos/errores; web 504, aplicaciones 92, navegador 20+7, Hermes nativo real con modelo loopback 1. Cobertura y composición pasan. El detalle vigente, fallos reproducidos y límites externos están en [full-validation.md](full-validation.md). Las rondas de abajo son históricas; el launcher Hermes global inválido no impide la prueba posterior mediante su venv gestionado.


## Alcance y autorización

El 2026-10-10 el usuario autorizó implementar la spec de preguntas y mensajes utilizando subagentes gpt-6-luna con razonamiento max y evaluación continua. Se conserva el trabajo preexistente de Graphify, auditorías y otras especificaciones. No se realizan commits ni publicaciones.

## Ronda 0 — referencia antes de implementar

- Inspección de git status: cambios previos en Graphify y documentos; ninguna modificación previa de producción o tests en los paths de esta feature.
- Cinco subagentes de diseño/reconocimiento y un revisor independiente inspeccionaron los límites de persistencia, actor, turnos, Work, ejecución, proveedores e interfaz. No hubo cambios de implementación durante esta ronda.
- Pruebas: `.venv/bin/python -m pytest test/mcp_server/test_answer_user_prompt.py test/services/agui/test_approval_bridge.py test/services/agui/test_handoff_approval.py test/services/agui/test_durable_handoff_approval.py test/services/test_agent_step.py test/services/test_inbox_service.py test/test_http_only_boundary.py --no-cov -q`.
- Resultado: **175 passed**, 2 avisos de deprecación Pydantic, exit 0, 76.37 s. Esta referencia no acredita la nueva funcionalidad.
- Arquitectura: `project-composition-check "$(cat .ai/project-name)"`: **PASS**, exit 0, 400 archivos y 1680 dependencias; 5 contratos conservados y 0 rotos.
- Hallazgos relevantes: credencial MCP compartida no prueba identidad humana; preguntas requieren identidad/versionado propio; incertidumbre impide reentrega automática; espera humana debe separarse del presupuesto activo; modalidades aisladas sin capacidad deben permanecer cerradas.

## Versiones y disponibilidad observadas

Comandos ejecutados: `codex --version`, `claude --version`, `opencode --version`, `hermes --version`.

- Codex: codex-cli 0.162.1.
- Claude Code: 2.1.293.
- OpenCode: v2.0.18.
- Hermes: el launcher existe pero falla con exit 127; su intérprete configurado en `/var/tmp/cao-providers-20261003-u29582w6/hermes-test-home/tools/python-3.14.7+20260901-linux-x64/bin/python3` ya no existe. Versión y recorrido real no acreditados. No se modifica una instalación externa como parte de esta feature.

La presencia de las otras tres versiones tampoco acredita un recorrido de preguntas: la validación se registra por separado cuando se ejecuta.

## Ronda 1 — implementación y revisión incremental

- Puerta previa: Spec Kit encuentra todos los artefactos; checklist 16/16. Graphify explain del ID exacto de send_input aporta 35 relaciones, verificadas contra fuente. Decisión TDD y límites documentados en graphify-impact.md.
- Primera prueba de seguridad compuesta: `pytest test/integration/test_interaction_security.py --no-cov -q -m ''` falla al recoger por ausencia de interaction_repository, antes de implementar el módulo; rojo esperado de TDD, no fallo de regresión.
- Evaluación incremental: `pytest test/clients/test_interaction_repository.py test/providers/test_interactions.py --no-cov -q`: **8 passed**, exit 0, 6.62 s. No acredita todavía API, MCP, UI ni composición final.
- La revisión detectó y asignó correcciones de igualdad If-Match/body, IDs MCP no interpolables, respuesta MCP tipada, proyección de aceptación/retorno/ack separadas, delegación exacta revisada por propietario, integridad de versiones/historial y privacidad de eventos. Su resolución se comprueba de nuevo en rondas posteriores.
- Se incorporan T069–T071 para delegación revisada. La respuesta a una aprobación ordinaria es informativa: jamás sustituye WorkDecisions.
- La extracción Graphify aislada sobre el montaje Windows completó AST de 410 archivos, pero el ensamblado quedó limitado por llamadas de filesystem (estado p9_client_rpc; más de 18 minutos). Se terminó solo ese proceso iniciado por esta tarea y se prepara una copia de fuentes en /tmp para extraer estructura sin tocar el grafo compartido ni su proceso preexistente.

- Frontend antes de implementar: `npm test -- --reporter=dot` en `web`: **42 archivos / 470 pruebas passed**, exit 0, 54.35 s. Avisos React de `act(...)` ya existentes y salida de prueba intencional de ErrorBoundary; no fallos.

## Evidencia posterior

- Graphify sobre la copia Linux completó la extracción: exit 0, 416 unidades, 11 868 nodos y 29 080 aristas. Evita el cuello de botella del montaje Windows y conserva intactos los artefactos compartidos.
- Transporte MCP: evaluación del subagente, **7 passed**, 56.43 s; la verificación conjunta se ejecuta después de integrar el servicio. Acredita validación de IDs, clave de operación solo en header y respuesta de espera tipada, no un recorrido real de un proveedor.
- La revisión incremental detectó que una pregunta obligatoria manual-only podía ser omitida al calcular readiness. Se exige que siga bloqueando y se añade una prueba compuesta con respuesta a un hermano opcional. También se comprueba que el cambio de generación invalide la espera antigua.
- El frontend detectó una regresión al dejar de pasar timeoutMs al fetch existente; se restauró la opción y se repiten pruebas y build. El primer run posterior no es evidencia de cierre.

Se registrarán las comprobaciones conjuntas finales aquí. Cada modificación posterior invalida la evidencia de cierre anterior.

## Ronda 2 — composición independiente

- Primera ejecución conjunta de seguridad, capacidad, contratos, Work, delegación y stdio: 15 passed / 5 failed. Los cinco fallos estaban en nuevos fixtures/aserciones: estado Work `completed` no existe (se usa `finished`), un segundo root grant no puede sustituir el grant inmutable del mismo job, `human_required` es interno y no está en el DTO de detalle, If-Match debe ir entre comillas, y el ack tiene un contrato propio. Se corrigieron los fixtures según la fuente, sin ampliar permisos ni debilitar las aserciones de autoridad.
- Segunda ejecución Work + delegación: 8 passed; quedó una aserción antigua de ack en stdio, corregida al DTO `{success, acknowledged}`. La prueba Work positiva usa admisión/contrato/grant/reservas reales y comprueba una transacción compartida; después de revocar el grant, no acepta ni devuelve la respuesta.
- `.venv/bin/python -m pytest test/integration/test_interaction_stdio.py --no-cov -q -m ''`: **1 passed**, exit 0, 54.81 s. Proceso MCP real (stdio), servidor uvicorn sobre TCP loopback, rutas de interacción de producción y SQLite real. Solo el endpoint auxiliar de contexto de terminal es una fixture; no se ejecuta una CLI de proveedor y no se acredita SC-007 ni UI real con esta prueba.
- Regresión detectada: `_pause_timeout_for_native_wait` suspendía indefinidamente el presupuesto para un terminal antiguo sin ledger. Se diagnosticó el bucle sin plazo por inspección y la suite base dejó de progresar. Se terminó únicamente ese pytest propio (exit 143); su ejecución parcial no se registra como pasada. La corrección limita la pausa a una observación durable actual y exige vencimiento/cancelación. Se repite la suite después de corregir.
- La revisión comprobó la verificación independiente del DDL de interacciones (triggers de historial incluidos), separación de metadata general y contenido protegido, no uso de proxy de entorno ni redirects al transmitir la capacidad MCP, y límites de credenciales. Las pruebas conjuntas finales deben acreditar la versión integrada.

## Ronda 3 — regresiones y revisión final en curso

- Suite base repetida sobre la implementación: **181 passed**, 2 warnings, exit 0, 63.34 s. Incluye agent_step, Inbox, MCP legacy, AG-UI y frontera HTTP-only. La corrección de la espera ilimitada legacy ya no deja colgada esta suite.
- Autenticación HTTP/WS y Work launch: **199 passed**, 6 warnings, exit 0, 58.17 s. Comando: `pytest test/api/test_browser_channel_auth.py test/api/test_auth_read_gating.py test/api/test_agui_auth_hardening.py test/api/test_ws_auth.py test/api/test_security.py test/services/test_work_launch.py test/services/test_work_launch_composition.py test/services/test_work_launch_runtime.py test/services/test_work_launch_gateway.py test/services/test_work_launch_durable_origin.py test/mcp_server/test_work_launch_authority.py --no-cov -q -o faulthandler_timeout=90`.
- Selección de proveedores, terminales y repositorio Work: **562 passed / 1 failed / 3 skipped**, exit 1, 247.02 s. El fallo se reprodujo en la configuración Codex legacy por `uvx`: se había usado el reconocedor estricto de capacidad para el flag de arranque obligatorio. Se restaura la regla legacy solo para `required`/timeout de startup; la inyección de capacidad conserva la comprobación estricta. Se repiten las pruebas, sin declarar verde esta ejecución.
- La revisión independiente encontró tres casos adicionales: cierre nativo inferido de un estado transitorio, repetición del mismo recibo de cierre al calcular completion y apertura manual por ID sin incarnación. Se añadieron correcciones y escenarios dirigidos; sus pruebas finales se registran después.
- La detección conservadora de solicitudes sensibles se amplía con PIN/passcode/MFA y códigos de recuperación. No se afirma que un clasificador de texto pueda demostrar la semántica de cualquier mensaje; la delegación exige una revisión humana explícita y no concede autorización de ejecución.
- Se conserva un motivo privado de auditoría para las acciones que lo requieren, separado de los eventos y DTO públicos. Esta corrección se verifica con persistencia real y pruebas de no exposición.

## Ronda 4 — suites completas, navegador y proveedores reales

- Suite completa de proveedores después de corregir el formato argv nativo de OpenCode: `pytest test/providers --no-cov -q -o faulthandler_timeout=90`: **2543 passed, 6 skipped, 7 deselected**, exit 0, 456.96 s. La ejecución anterior tuvo 2539 passed / 1 failed: el helper estricto no reconocía `command: ["cao-mcp-server"]`; se corrigió manteniendo el rechazo de wrappers/argv adicionales y usando el campo nativo `environment` de OpenCode. No se presenta la ejecución fallida como pasada.
- Suite conjunta de interacciones, antes del último gate de retorno requerido: `pytest test/clients/test_interaction_repository.py test/services/interactions test/api/test_interactions_transport.py test/api/test_interaction_websocket_input.py test/mcp_server/test_interaction_tools.py test/mcp_server/test_utils.py test/integration/test_interaction*.py --no-cov -q -m '' -o faulthandler_timeout=90`: **125 passed**, exit 0, 68.40 s. El marcador explícito incluye las integraciones que pytest excluye por defecto.
- Regresión de ejecución/sesiones/terminal/inbox/fences: **321 passed, 3 deselected**, exit 0, 60.76 s. Autenticación HTTP/WS, Work launch, AG-UI, legacy MCP y HTTP-only: **291 passed**, exit 0, 90.31 s. Estas suites preceden el ajuste final de readiness y se repiten donde ese ajuste afecta resultados.
- El lanzamiento real Work V1 reveló que LaunchIntent rechazaba el permiso ya existente `@cao-mcp-server`. Se amplía exclusivamente la gramática de herramienta para un único prefijo `@`, sin cambiar la identidad de perfiles/sesiones ni el chequeo de subset del contrato. Prueba roja: 1 failed / 7 passed; corrección más regresión Work launch runtime: **26 passed**, exit 0, 17.08 s. La integración de lanzamiento verifica la autoridad mediante el join durable attempt/binding/grant.
- Migración independiente v2: `capability_available` registra si se instaló el archivo privado. Un fallo de escritura no deja anunciado un canal explícito usable ni elimina la fuente necesaria para observación manual. La primera migración falló por diferencias textuales de DDL tras ALTER; el verificador ahora genera el DDL esperado mediante el mismo recorrido v1→v2. Repositorio/servicio/seguridad/delegación/ciclos/espera: **51 passed**; fallo inyectado de instalación y monitor: **4 passed**. Work conserva su esquema 39.
- Web: `npm test -- --reporter=dot --maxWorkers=4`: **45 archivos / 503 tests passed**, 205.91 s, seguido de build correcto. Este run precede el último enmascaramiento adicional de prompts de credenciales; el cierre web se repite después de ese cambio.
- Browser Chromium conectado a FastAPI/SQLite temporales reales: 20 fuentes de terminal, 100 preguntas, respuesta tipada aceptada y devuelta por `wait_for_answers`, UI `returned_to_mcp` y limpieza de detalle protegido al cambiar de nodo. p95 desde commit hasta primera aparición DOM: **1538 ms** (otro run: 1244 ms). La carga usa registro concurrente por fuente; la primera medición con seed serial fue **2255 ms**, superior al objetivo, y se conserva como fallo. El proxy de flota/nodo y el nodo vacío son fixtures; no prueba varias instancias reales ni 20 CLIs activas. El browser se repite tras el gate final del backend.

### Recorridos CLI reales y límites de aceptación

| Proveedor / versión | Resultado del canal explícito real | Límite |
| --- | --- | --- |
| Claude Code 2.1.293 | **PASS**, exit 0: crea pregunta requerida, recibe `blue` por MCP y confirma `agent_acknowledged` | Configuración privada aislada, sólo herramientas de interacción; primer presupuesto $0.25 agotado tras aceptación, segunda prueba limitada a $1.00 completa. No certifica respuesta nativa. |
| Codex 0.162.1 | **INCONCLUSO**: `mcp list --json` reconoce el servidor; exec completa un turno, no crea pregunta ni ACK | HOME de CLI privado, sandbox read-only, shell/web deshabilitados, límite 75 s. Descubrimiento MCP no equivale a uso del canal por el modelo. |
| OpenCode 2.0.18 | **BLOQUEADO** antes de MCP: modelo configurado `opencode-go/gpt-6-luna` devuelve `provider.no-route` / `Model unavailable` | La lista de modelos y la credencial instalada no prueban una ruta operativa. Sin pregunta ni efecto. |
| Hermes | **NO SOPORTADO** en esta implementación / launcher exit 127 | Intérprete externo ausente. Su comando configurable no permite identificar un perfil exacto e inyectar un overlay privado sin mutar configuración externa; no se introduce esa mutación. |

Los entornos privados y archivos de capacidad/auth de las pruebas se eliminaron. No se copiaron secretos a evidencia. **SC-007 y la parte Hermes de FR-038 siguen pendientes**; mocks/configuración MCP no satisfacen estos criterios. Todos los adaptadores de respuesta nativa permanecen deshabilitados/manuales. Un perfil sin CAO MCP conserva la inspección manual del terminal y el timeout legacy; no adquiere un ledger de preguntas ni permisos nuevos.

### Hallazgo del último gate de espera

La auditoría encontró que aceptar una respuesta requerida liberaba la ejecución antes de que el mismo agente la recuperara por MCP. La UI distinguía los recibos, pero ese detalle no impedía completar con un resultado anterior. Se exige ahora: aceptación mantiene la espera; `wait_for_answers` cambia a `returned_to_mcp` y libera en la misma transacción; ACK confirma observación y sigue siendo opcional. La composición debe demostrar que no se reenvía el encargo inicial. La suite previa de 125 no acredita esta corrección; se registra su repetición al finalizar.

## Ronda 5 — comprobaciones finales del árbol estabilizado

- Raíz independiente, suite conjunta de interacciones con migración v2, instalación fallida, entrega requerida, recuperación, permisos, delegación, HTTP/MCP/stdio y lanzamiento Work real: **130 passed**, 7 avisos de deprecación, exit 0, 74.00 s. Comando: `.venv/bin/python -m pytest test/clients/test_interaction_repository.py test/services/interactions test/api/test_interactions_transport.py test/api/test_interaction_websocket_input.py test/mcp_server/test_interaction_tools.py test/mcp_server/test_utils.py test/integration/test_interaction*.py test/services/test_terminal_interaction_capability.py --no-cov -q -m '' -o faulthandler_timeout=90`.
- Raíz independiente, ejecución/terminal/sesiones/inbox/Work input después del gate de retorno: **321 passed, 3 deselected**, 5 avisos de deprecación, exit 0, 66.04 s. Comando: `.venv/bin/python -m pytest test/services/test_agent_step.py test/services/test_terminal_service.py test/services/test_terminal_service_full.py test/services/test_terminal_service_inbox_registration.py test/services/test_status_monitor_interactions.py test/services/test_session_service.py test/services/test_inbox_service.py test/api/test_work_terminal_input_fence.py --no-cov -q -o faulthandler_timeout=90`.
- Frontend final después del enmascaramiento de credenciales: **45 archivos / 503 tests passed**, 216.80 s; build de producción correcto. Browser después del gate de retorno: **PASS**, p95 **1586 ms**, usando `LD_LIBRARY_PATH=/tmp/cao-playwright-libs/usr/lib/x86_64-linux-gnu npm run test:e2e -- --grep "real API inbox"` desde web. La biblioteca ausente de Chromium se extrajo localmente en /tmp; no hubo instalación global.
- Arquitectura final: `project-composition-check "$(cat .ai/project-name)"`: **PASS**, 408 archivos / 1712 dependencias, 5 contratos conservados / 0 rotos. Graphify final: 417 unidades / 12 034 nodos / 29 894 aristas; extract/explain exitosos y relaciones importantes verificadas contra fuente actual.
- Revisión independiente del código: sin defecto de prioridad alta en el gate requerido, carrera de caducidad ni revocación tras aceptación. `returned_to_mcp` prueba el commit del resultado autorizado, no comprensión del modelo ni llegada de una respuesta de red perdida; reintentos recuperan el mismo recibo y ACK registra observación por separado.
- Corrección final de recuperación: el test que avanzaba turno inmediatamente tras aceptación debe recuperar primero el resultado desde su fuente; se añaden asserts de deadline y wait-graph mientras sigue pendiente. Sus nuevas aserciones se repiten de forma dirigida y se registra el resultado debajo.
- Diff: se revisan todos los cambios de producción por responsabilidad, nuevos módulos/tests/contratos además de los tracked diffs, y `git diff --check` pasa. Se conserva Graphify/auditorías y specs 017–020 preexistentes; ningún commit, push o publicación.

### Estado de criterios y límites

| Criterio | Evidencia / estado |
| --- | --- |
| SC-001 | Browser controlado PASS para 20 fuentes / 100 preguntas; p95 final independiente 1333 ms (subagente: 1586 ms). No se ejecutan 20 CLIs simultáneas; el run serial anterior de 2255 ms se conserva como fallo. |
| SC-002 | Tipos/validación/entrega cubiertos por tests de servicio, API/MCP e interfaz; browser y Claude real cubren el recorrido de texto, no cada tipo en cada CLI. |
| SC-003 | Concurrencia/idempotencia/versiones/claim/uncertain/no-resend PASS en las pruebas compuestas. |
| SC-004 | Persistencia/reinicio/replay/incarnación/nodo cubiertos; browser cambia entre proxy real y nodo vacío fixture, no prueba varias instancias reales. |
| SC-005 | ACL de contenido, capability humana separada, grant Work, delegación revisada/revocada/TTL cubiertos. |
| SC-006 | Aceptación sola mantiene dependencia; pull libera sin ACK obligatorio ni reenvío; caducidad conserva reconciliación terminal. Pruebas runtime/composición PASS. |
| SC-007 | **PENDIENTE**: Claude real PASS; Codex inconcluso; OpenCode bloqueado por ruta de modelo; Hermes sin canal explícito seguro ni launcher operativo. |
| SC-008 | Formularios de tipos admitidos y navegación manual por origen/incarnación cubiertos; nativos manuales sin respuesta automática. Perfiles sin MCP sólo inspección legacy y limitación visible. |

La integración opcional AG-UI conserva su bridge de estado de terminal y los fences existentes, pero **no incorpora un adaptador de recibos del ledger**. T064 permanece parcial. Hermes mantiene T011 parcial y los recorridos completos de T066/SC-007 pendientes. No se declara aceptada toda la especificación mientras esos criterios sigan abiertos.

### Repetición independiente de recuperación y navegador

- `.venv/bin/python -m pytest test/integration/test_interaction_recovery.py test/integration/test_interaction_wait_composition.py --no-cov -q -m ''`: **11 passed**, 5 avisos de deprecación, exit 0, 35.12 s, después de congelar las últimas aserciones. Aceptar mantiene deadline/arista; pull elimina dependencia; avanzar después conserva la prueba de generación stale.
- Raíz, comando Chromium citado arriba: **1 passed**, exit 0, 29.2 s; 20 fuentes / 100 preguntas, p95 de primera visibilidad DOM **1333 ms**. Confirma respuesta tipada por API real, retorno MCP y limpieza de contenido al cambiar de nodo. No hubo CLIs ni tmux; el límite de la fixture de flota sigue aplicando.
- Última inspección scoped de `git status` y `git diff --check` sin errores de whitespace. Los cambios preexistentes permanecen. Sólo se corrige después un comentario obsoleto de agent_step: el gate exige pull, no ACK. No cambia comportamiento.

## Ronda solicitada de pruebas completas — resultado vigente

La petición posterior del usuario amplió la evaluación a toda la suite Python aplicable de CI, integración, cobertura, calidad, todos los tests web/apps y todos sus E2E. Durante esa ronda se reprodujeron y corrigieron bypass de entrada nativa ante IDLE transitorio, recuperación tras reinicio, reutilización de avisos cerrados/supersedidos, liberación de esperas por otra pregunta y TTL después de entrada WebSocket. Hay regresiones con SQLite real y waiters async activos.

La ejecución final completa con perfiles separados pasa **16949 tests**, 119 skips explícitos, cero fallos/errores y exit 0. El runner anterior compartía un perfil entre cuatro procesos: se reproduce su rechazo por el guard y se corrige el aislamiento fuera del repo, sin relajar producción. Python **87,04%**, Apps **90,01%** superan los mínimos sin cambiarlos. Black/isort completos, mypy 405 módulos, builds, contratos y composición pasan.

Web: **504 tests / 20 Playwright**, cinco formularios y proceso MCP real, p95 **1042 ms** sobre 20 fuentes/100 preguntas de fixture. Apps: **92 tests / 7 Playwright**, typecheck, cuatro builds, JIT y presupuestos de tamaño. Hermes: **1 test CLI/PTy real** contra inferencia simulada loopback; no prueba canal explícito. Todos los resultados corresponden a la misma producción congelada.

**SC-007 permanece pendiente:** Claude completa el recorrido externo; Codex externo termina sin preguntas/toolcalls; OpenCode no tiene ruta al modelo y su recorrido loopback tampoco produce preguntas; Hermes no tiene overlay explícito seguro probado. T011/T064/T066 permanecen parciales. El éxito local no acredita esos límites ni los runners Docker/host/matriz de plataformas.

Evidencia, comandos y diagnóstico: [full-validation.md](full-validation.md). Revisión vigente: [composition-review.md](composition-review.md). T067–T068 se revalidan con esta ronda; no hay commit, push ni publicación.
