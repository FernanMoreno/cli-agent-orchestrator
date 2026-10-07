# Auditoría transversal del código

**Fecha:** 2026-10-03<br>
**Tipo:** revisión estática, solo lectura<br>
**Estado:** riesgo alto por hallazgos de seguridad P1

## Resumen ejecutivo

La revisión encontró problemas importantes en la federación entre nodos, aislamiento de asignaciones, transporte de credenciales desde la TUI y el navegador, y controles de publicación CI/CD. También hay riesgos de fiabilidad en la ejecución de workflows y en el flujo de eventos en vivo.

Prioridad recomendada:

1. Cortar el reenvío de credenciales a destinos remotos arbitrarios y corregir el aislamiento de propietarios.
2. Proteger credenciales en TUI, WebSocket y runners de CI.
3. Exigir una validación de CI confiable para releases y publicaciones.
4. Corregir pérdida/reintento de eventos y riesgos del ciclo de vida de workflows.
5. Añadir a CI los controles de arquitectura, versiones y dependencias que hoy faltan.

## Alcance y método

- Revisé rutas principales de API, autorización, asignaciones, orquestación remota, MCP, Web, TUI, ejecución de workflows, plugins, CI/CD, dependencias y ejemplos de despliegue.
- Usé Graphify como mapa estructural y validé los hallazgos contra el código actual.
- El informe Graphify superior está fechado el 23-sep-2026; lo traté como referencia de navegación, no como fuente actual. El código actual es la fuente de verdad.
- El árbol de trabajo ya tenía 473 rutas modificadas o sin seguimiento al iniciar esta auditoría. Esos cambios se conservaron.
- Es una revisión estática transversal; no es una inspección línea a línea de cada archivo ni una prueba dinámica.
- No se ejecutaron tests ni builds. No se modificó código fuente.

## Addendum de alcance — 2026-10-04

El alcance acordado para el trabajo posterior es una sola PC y un proyecto local. Incluye los dos modos: un CAO coordinador con agentes locales (spec 006) y varias instancias CAO independientes que se coordinan entre sí en la misma PC (spec 009). Conectar CAO entre PCs queda fuera de alcance. Tampoco se asumen un servicio CAO alojado, cuentas centrales, suscripciones, directorio ni relay. Los proveedores que el usuario ya configura pueden seguir conectándose con esos proveedores.

La prioridad inicial del resumen corresponde a la auditoría completa. Para el trabajo actual, los specs se limitaron al uso local en una PC y al mantenimiento de las descargas de CAO.

La exfiltración del token hacia `target_host` y el fallo de asignación autenticada entre nodos siguen siendo hallazgos P1/P2 de la auditoría completa. El spec 009 cubre pares CAO en la misma PC y exige que la coordinación local no envíe credenciales maestras ni confíe en destinos de red arbitrarios. La federación entre PCs queda diferida; esa limitación de alcance no elimina los hallazgos de seguridad existentes.

## P1 — seguridad e integridad

### 1. `target_host` puede exfiltrar el token interno

`_resolve_target_base_url()` acepta URLs y hosts arbitrarios. Algunas herramientas remotas envían allí `Authorization: Bearer` con `CAO_AUTH_LOCAL_TOKEN`. Una persona o modelo autorizado a invocar esas herramientas puede indicar un host bajo su control y capturar el token.

**Impacto:** exposición de una credencial reutilizable del proceso operador, potencialmente con scopes amplios.<br>
**Recomendación:** usar destinos remotos de confianza, validar esquema/host/puerto y emitir credenciales delegadas de alcance y audiencia mínimos. No reenviar el bearer operador a un destino libre.

**Evidencia:** [`orchestration.py`](src/cli_agent_orchestrator/utils/orchestration.py) (línea histórica `203`), [`orchestration.py`](src/cli_agent_orchestrator/utils/orchestration.py) (línea histórica `936`), [`orchestration.py`](src/cli_agent_orchestrator/utils/orchestration.py) (línea histórica `1841`), [`server.py`](src/cli_agent_orchestrator/mcp_server/server.py) (línea histórica `1073`), [`auth.py`](src/cli_agent_orchestrator/security/auth.py) (línea histórica `335`).

### 2. Las asignaciones pueden colapsar al propietario compartido `local`

`_optional_run_step_principal()` devuelve `None` si la petición incluye `X-CAO-Workflow-Run-Credential`, sin validar ese valor. Las rutas de asignación también llaman a este helper. Cuando no reciben principal, asignan `local` como propietario. El servicio usa ese propietario para limitar acceso a las asignaciones.

**Impacto:** con autenticación activada, un usuario autenticado con scope para la ruta puede enviar ese header y entrar en el espacio de propietario compartido, debilitando el aislamiento entre identidades.
**Recomendación:** usar dependencias de identidad separadas. La excepción de credencial de workflow debe limitarse a `run-step`; las asignaciones deben conservar siempre el principal autenticado o validar una capacidad específica para esa asignación.

**Evidencia:** [`main.py`](src/cli_agent_orchestrator/api/main.py) (línea histórica `5189`), [`main.py`](src/cli_agent_orchestrator/api/main.py) (línea histórica `11249`), [`assignment_service.py`](src/cli_agent_orchestrator/services/assignment_service.py) (línea histórica `77`).

### 3. La TUI puede enviar bearer por HTTP a un host remoto

La TUI construye una URL `http://` con `CAO_API_HOST`, carga `CAO_AUTH_LOCAL_TOKEN` y lo adjunta como `Authorization: Bearer`. El host se puede configurar como remoto.

**Impacto:** un intermediario de red puede leer y reutilizar el token.
**Recomendación:** usar HTTPS para hosts remotos y rechazar el envío de bearer por HTTP salvo para loopback; documentar claramente túneles seguros.

**Evidencia:** [`server.rs`](tui/src/server.rs) (línea histórica `1027`), [`server.rs`](tui/src/server.rs) (línea histórica `1437`).

### 4. El token WebSocket puede quedar en logs de acceso

El cliente web coloca el bearer en `?token=...`. El servidor acepta ese parámetro, pero el filtro de logs redacta `access_token` y `ticket`, no `token`. La ruta HTTP con query puede aparecer en access logs.

**Impacto:** un JWT podría quedar copiado en logs y ser reutilizable hasta expirar.
**Recomendación:** evitar bearer duradero en URL; usar un ticket de vida corta y alcance limitado, y añadir `token` al filtrado de logs como defensa adicional.

**Evidencia:** [`api.ts`](web/src/api.ts) (línea histórica `42`), [`main.py`](src/cli_agent_orchestrator/api/main.py) (línea histórica `9647`), [`logging.py`](src/cli_agent_orchestrator/utils/logging.py) (línea histórica `9`).

### 5. El workflow de proveedores reales expone un runner self-hosted

El workflow permite `workflow_dispatch`, corre en un runner `self-hosted` y puede usar el HOME del runner si no se configura `CAO_REAL_PROVIDER_E2E_AUTH_HOME`. La ref seleccionada se ejecuta con acceso al entorno local del runner.

**Impacto:** código de una ref no confiable podría leer credenciales locales del proveedor. El checkout también conserva por defecto sus credenciales persistentes.
**Recomendación:** limitar a refs protegidas, requerir aprobación, usar runners efímeros y desactivar la persistencia de credenciales del checkout.

**Evidencia:** [`.github/workflows/real-provider-e2e.yml`](.github/workflows/real-provider-e2e.yml) (línea histórica `3`), [workflow](.github/workflows/real-provider-e2e.yml) (línea histórica `43`), [workflow](.github/workflows/real-provider-e2e.yml) (línea histórica `63`).

### 6. Un release manual puede evitar el gate de CI

El preflight manual del workflow de release permite avanzar antes de verificar el resultado de CI. El job posterior tiene permisos para publicar cambios/tag y crear el release; la publicación del release dispara el workflow de PyPI.

**Impacto:** puede publicarse una ref o SHA que no superó la validación normal.
**Recomendación:** exigir CI exitoso para el SHA exacto que se publica, permitir solo refs confiables y limitar permisos al mínimo por job.

**Evidencia:** [`release.yml`](.github/workflows/release.yml) (línea histórica `4`), [`release.yml`](.github/workflows/release.yml) (línea histórica `54`), [`release.yml`](.github/workflows/release.yml) (línea histórica `140`), [`publish-to-pypi.yml`](.github/workflows/publish-to-pypi.yml) (línea histórica `3`).

## P2/P3 — fiabilidad, límites y configuración

### Federación y autorización

- **Asignación remota falla con auth activa.** `_assign_remote()` hace POST a `/assignments` sin autenticación; la ruta remota exige scopes. La rama local sí agrega autenticación. Corregir con credencial de nodo delegada y probar el flujo multi-nodo autenticado. [`orchestration.py`](src/cli_agent_orchestrator/utils/orchestration.py) (línea histórica `1481`), [`main.py`](src/cli_agent_orchestrator/api/main.py) (línea histórica `11285`).
- **Configuración MCP Apps puede fallar abierta.** `apps.only=true` combinado con Apps desactivado solo genera un aviso y retorna; las herramientas MCP generales quedan visibles. La documentación pide activar ambas opciones. Hacer que esta combinación detenga el arranque o aplicar la restricción. [`mcp_apps.py`](src/cli_agent_orchestrator/plugins/builtin/mcp_apps.py) (línea histórica `86`), [`mcp-apps.md`](docs/mcp-apps.md) (línea histórica `52`).
- **PostMessage MCP no comprueba la ventana fuente.** Se aceptan respuestas correlacionadas sin comprobar `event.source`; una ventana capaz de enviar mensajes al iframe podría falsificar la primera respuesta o resolver llamadas pendientes. Validar fuente y origen antes de aceptar. [`mcpApp.ts`](cao_mcp_apps/src/shared/mcpApp.ts) (línea histórica `312`).

### Eventos y clientes

- **Event Stream no autentica su conexión SSE.** La vista usa `EventSource` sin bearer, mientras `/events` requiere scopes; en despliegues con auth funciona el historial, pero no el flujo en vivo. Añadir un proxy o ticket breve de solo lectura. [`EventStreamView.tsx`](cao_mcp_apps/src/event-stream/EventStreamView.tsx) (línea histórica `64`), [`main.py`](src/cli_agent_orchestrator/api/main.py) (línea histórica `2433`).
- **Hay una ventana de pérdida de eventos.** La vista carga historial y luego se suscribe. Un evento que llegue entre ambos pasos no entra en ninguno. Usar cursor con replay o suscribir, cargar y deduplicar de forma atómica. [`EventStreamView.tsx`](cao_mcp_apps/src/event-stream/EventStreamView.tsx) (línea histórica `57`), [`main.py`](src/cli_agent_orchestrator/api/main.py) (línea histórica `2455`).
- **Renovación de sesión web no tiene timeout propio.** `browserFetch` espera `ensureBrowserAccess()` antes de iniciar su petición abortable. Una renovación colgada puede bloquear llamadas y pantalla. Propagar timeout y cancelación al flujo de auth. [`auth.ts`](web/src/auth.ts) (línea histórica `37`), [`auth.ts`](web/src/auth.ts) (línea histórica `208`).
- **La TUI comprueba el tamaño después de leer el cuerpo completo.** Una respuesta grande desde el host configurado consume memoria antes de aplicar el límite. Leer en streaming con tope incremental. [`server.rs`](tui/src/server.rs) (línea histórica `1426`), [`server.rs`](tui/src/server.rs) (línea histórica `1481`).
- **El polling MCP permite solicitudes solapadas.** Una respuesta lenta puede llegar después de una más nueva y sobrescribir su estado. Mantener una sola solicitud activa o usar números de secuencia. [`mcpApp.ts`](cao_mcp_apps/src/shared/mcpApp.ts) (línea histórica `204`).

### Workflows y estado

- **Recuperación de observaciones puede entrar en reintento caliente.** Tras un error con cursor activo, el loop puede dormir cero y presionar la base de datos. Aplicar backoff cuando la consulta falla. [`terminal_observation_recovery.py`](src/cli_agent_orchestrator/services/terminal_observation_recovery.py) (línea histórica `32`).
- **El driver de continuaciones puede postergar ejecuciones.** Consulta hasta 128 filas sin orden/paginación estable e incluye estados pausados en un lote. Con suficientes filas no elegibles, otras podrían quedar fuera repetidamente. Añadir filtro y paginación justa. [`workflow_continuation_driver.py`](src/cli_agent_orchestrator/services/workflow_continuation_driver.py) (línea histórica `377`).
- **El driver retiene tareas completadas.** `self.tasks` no elimina entradas cuando una tarea termina; puede retener resultado o excepción durante la vida del proceso. Retirar la entrada en callback si aún corresponde a esa tarea. [`workflow_continuation_driver.py`](src/cli_agent_orchestrator/services/workflow_continuation_driver.py) (línea histórica `87`).
- **Carrera al enumerar sesiones remotas.** `session_terminals()` puede devolver `None` después de que la lista inicial fue leída; el llamador itera ese valor y puede responder 500. Tratar desaparición concurrente como lista vacía o leer snapshot coherente. [`remote_terminal_service.py`](src/cli_agent_orchestrator/services/remote_terminal_service.py) (línea histórica `320`).
- **Opciones pueden sobrescribir la identidad del paso.** `body.update(opts)` permite sustituir `env_vars`, incluidas variables reservadas para run, generación y step. Fusionar variables de usuario sin dejar reemplazar la identidad administrada. [`__init__.py`](src/cao_workflow/__init__.py) (línea histórica `120`).
- **Campos YAML desconocidos se ignoran.** Un typo como `output_shema` puede aceptarse y dejar apagada la validación esperada. Rechazar extras o, como transición compatible, emitir diagnóstico de lint. [`workflow.py`](src/cli_agent_orchestrator/models/workflow.py) (línea histórica `180`).

### CI, dependencias y despliegue

- Se anuncian Python 3.13/3.14 pero CI llega a 3.12. Añadir esas versiones a la matriz o ajustar la compatibilidad anunciada. [`pyproject.toml`](pyproject.toml) (línea histórica `16`), [`ci.yml`](.github/workflows/ci.yml) (línea histórica `21`).
- Dependabot actualiza GitHub Actions; no hay cadencia equivalente detectada para paquetes Python/JavaScript. Añadir actualizaciones y análisis recurrente para los lockfiles aplicables. [`.github/dependabot.yml`](.github/dependabot.yml) (línea histórica `5`).
- Ejemplos EKS usan tags móviles y dependencias con límites mínimos sin lock reproducible. Fijar digests y dependencias; validar builds y manifests en CI. [`Dockerfile.broker`](examples/cao-clusters/kubernetes/eks/Dockerfile.broker) (línea histórica `1`), [`Dockerfile.panel`](examples/cao-clusters/kubernetes/eks/Dockerfile.panel) (línea histórica `31`).
- `security-events: write` se concede al workflow de CI completo, aunque solo lo requiere el job que sube SARIF. Moverlo al job correspondiente. [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (línea histórica `9`).
- Mypy usa `continue-on-error`, así que errores de tipos no bloquean CI. Aplicar una línea base que bloquee errores nuevos. [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (línea histórica `266`).
- Import Linter está declarado y hay arquitectura documentada, pero no encontré su invocación ni `project-composition-check` en CI. Añadir gates que validen contratos entre módulos. [`.importlinter`](.importlinter), [`.ai/composition/README.md`](.ai/composition/README.md).
- La política de severidad de Trivy no queda del todo clara: el workflow etiqueta un gate CRITICAL/HIGH, y también documenta que SARIF puede incluir todas las severidades. Definir umbral esperado y reflejarlo de forma consistente. [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (línea histórica `720`).

### Mantenibilidad y endurecimiento

- `api/main.py` concentra más de 11.000 líneas. Separar rutas por dominio mejoraría lectura y revisión de contratos de autorización; hacerlo incrementalmente, conservando interfaces.
- Cada instancia Kimi puede volver a ejecutar `kimi --help`; revisar si la detección puede cachearse sin asumir que binario y entorno no cambian. [`kimi_cli.py`](src/cli_agent_orchestrator/providers/kimi_cli.py) (línea histórica `713`).
- El proceso `bd` hereda casi todo `os.environ`. No se confirmó explotación, pero una allowlist reduce exposición accidental de secretos. [`beads.py`](src/cli_agent_orchestrator/clients/beads.py) (línea histórica `105`).
- Hallazgos de densidad del grafo no siempre incluyen `scope_id`; una coincidencia de nombre puede marcar un nodo de otro ámbito. [`memory.py`](src/cli_agent_orchestrator/graph/providers/memory.py) (línea histórica `387`).
- Posible carrera entre restauración y limpieza de proveedores: requiere reproducción antes de elevarla a defecto confirmado. [`manager.py`](src/cli_agent_orchestrator/providers/manager.py) (línea histórica `363`), [`status_monitor.py`](src/cli_agent_orchestrator/services/status_monitor.py) (línea histórica `334`).

## Oportunidades de producto

1. **Coordinación entre CAO locales:** compartir tareas entre agentes de una instancia (spec 006) o instancias independientes en la misma PC (spec 009), con permisos limitados y estado visible.
2. **Eventos fiables:** cursor/replay, estado de reconexión, última secuencia recibida y deduplicación visible.
3. **Contrato API común:** generar tipos/contratos para Web, TUI y MCP; mostrar matriz de paridad de comandos CLI/TUI/Web.
4. **Catálogo de plugins verificable:** firma, procedencia, compatibilidad, permisos declarados y evaluación automatizada. La documentación reconoce que hoy estas garantías son limitadas. [`agent-plugins.md`](docs/agent-plugins.md) (línea histórica `27`).
5. **Distribución verificable:** imágenes oficiales con SBOM, firma/procedencia y ejemplos Docker/Kubernetes reproducibles.
6. **Panel de operación de agentes:** proveedor, readiness, coste, workflow, handoff, asignación y diagnóstico en una vista común.

## Fortalezas existentes

- Acciones GitHub fijadas por SHA.
- Hay una matriz de paquetes y una base amplia de pruebas en el repo.
- Los plugins Agent pasan por validación y existen ejemplos Docker personales con dependencias congeladas.
- Hay contratos de arquitectura y revisión de composición documentados; falta convertirlos en gates regulares de CI.

## Revisión de composición

| Interacción revisada | Resultado estático | Escenario que conviene validar al corregir |
|---|---|---|
| MCP tool → target remoto → bearer operador | Riesgo P1 confirmado por rutas fuente | Destino hostil no recibe ninguna credencial reutilizable |
| Auth middleware → helper de principal → asignaciones | Riesgo de aislamiento confirmado por rutas fuente | Dos principales no leen ni alteran asignaciones ajenas, incluso con header arbitrario |
| TUI/Web → auth → transporte/logs | Transporte HTTP y query token inseguros | Ningún bearer duradero viaja por HTTP ni queda en access logs |
| Event history → SSE live | Auth incompatible y hueco de cursor | Eventos entre snapshot y conexión se reproducen exactamente una vez desde el punto visible al usuario |
| Dispatch → runner/release → PyPI | Gate de confianza incompleto | Solo SHA con CI exitoso y ref protegida llega a publicación |
| Continuation driver → DB → tareas async | Riesgos de equidad, retry y ciclo de vida | Caída DB no provoca loop caliente; backlog grande progresa y tareas completadas se liberan |

No se ejecutaron pruebas de contrato, integración, composición ni contra dependencias reales. Esta sección contiene escenarios de aceptación recomendados, no resultados dinámicos.

## División local vigente para los specs

Para mantener cada especificación implementable y revisar límites claros, propongo cinco:

1. **Coordinación CAO local:** cubrir agentes trabajadores de una instancia (spec 006) e instancias CAO independientes coordinadas en la misma PC y el mismo proyecto (spec 009), con identidad confirmada, permisos limitados y tareas aisladas.
2. **Integridad de pruebas y descargas locales:** trabajo interno de mantenimiento para entregar paquetes del programa desde contenido validado.
3. **Transporte autenticado de eventos y UI:** proteger el flujo local de eventos, evitar pérdidas, validar mensajes y limitar solicitudes.
4. **Fiabilidad y validación de workflows:** reintentos, progreso justo, ciclo de vida de tareas, identidad/configuración de pasos y sesiones de agentes e instancias CAO locales.
5. **Instalación local reproducible y plugins confiables:** compatibilidad, dependencias conocidas, instalación en el equipo del usuario y procedencia de plugins sin mercado central.

Esta es una propuesta de alcance; no implica cambios de código ni implementación.
