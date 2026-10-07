# Implementation Plan: Colaboración de agentes

**Branch**: `main` (sin cambiar rama; `006-agent-collaboration` identifica la feature)
**Date**: 2026-10-01 | **Spec**: [spec.md](spec.md)

## Summary

Conservar coordinador, proveedores, tmux, delegación e inbox originales. Comprobar sus fronteras dentro de la imagen integrada y corregir únicamente fallos reproducidos. Claude Code será supervisor; Codex desarrollará una API pequeña y OpenCode su frontend en una carpeta nueva del Escritorio.

La aceptación utiliza la misma imagen de aplicación, estado privado separado y puertos propios. Es un despliegue temporal completo con tres agentes en tmux, no un contenedor por agente. No recrea la instalación personal. Las correcciones se prueban en una imagen candidata antes de desplegarlas al propietario.

## Technical Context

- **Language/Version**: Python >=3.10 en el proyecto, Python 3.12 en imagen; web CAO React/TypeScript existente.
- **Primary Dependencies**: FastAPI, Pydantic, libtmux, MCP, HTTP y Docker existentes; ningún coordinador ni dependencia de mensajería nuevos.
- **Storage**: SQLite, configuración privada y manifiesto de montajes existentes; sin migración de esquema prevista.
- **Testing**: pytest y suites actuales; composición con SQLite/tmux/Docker/HTTP reales; aceptación optativa con Claude/Codex/OpenCode y navegador.
- **Target Platform**: Linux/WSL local y Escritorio del propietario.
- **Project Type**: Aplicación local cliente/servidor con agentes CLI.
- **Performance Goals**: Tres agentes concurrentes, cero contenedores por agente; respetar timeouts existentes y registrar tiempos observados sin inventar mejoras.
- **Constraints**: No sandbox nuevo, clusters ni protocolo alternativo. No secretos en argv/prompts/evidencias. No acceso general al host. Los IDs de terminal no implican credenciales independientes.
- **Scale/Scope**: Un supervisor y dos trabajadores; mensajes, proyectos RW/RO, integración externa, cierre y recuperación.

## Constitution Check

Pre-investigación: PASS. Estado de Git inspeccionado; trabajo preexistente preservado; spec actualizado con la prueba expresa del propietario. Investigación mediante dos agentes de solo lectura, requerida por `speckit-plan`.

Post-diseño: PASS para planificación. Graphify leído sin regenerar archivos y relaciones verificadas contra fuente. No se declara implementada la feature. Correcciones requieren reproducción, causa raíz, regresión y revisión de composición. No commit/push automático. Evidencias fuera del repo; decisiones durables en estos artefactos sin duplicarlos en el vault.

## Project Structure

```text
specs/006-agent-collaboration/
  spec.md, plan.md, research.md, data-model.md, quickstart.md, tasks.md
  contracts/collaboration.md, checklists/requirements.md
scripts/
  docker_install.py, docker_personal_runtime.py
  agent_collaboration_acceptance.py          # nuevo runner
src/cli_agent_orchestrator/
  utils/{mcp_resolution,opencode_config,orchestration}.py
  providers/{claude_code,codex,opencode_cli}.py
  services/{terminal_service,inbox_service,session_service,worktree_service}.py
  clients/{tmux,database}.py
  security/auth.py, cli/commands/launch.py
test/
  providers/, utils/, mcp_server/, api/, services/, scripts/
  integration/test_agent_collaboration.py    # nueva composición determinista
docs/agent-collaboration.md                  # nueva guía
```

**Structure Decision**: Reutilizar propietarios actuales. El runner registra escenarios y limpia recursos propios; no sustituye a los agentes ni escribe su API/frontend. Un helper común de entorno solo se justifica si los proveedores lo necesitan.

## Phase 0 — Investigación

[research.md](research.md) registra decisiones y fuente. Riesgos concretos: Codex no reenvía explícitamente conexión/auth a su MCP; Claude solo inyecta terminal ID; `cao launch` normal carece de bearer; montajes no ofrecen RO por CLI; no hay prueba real de descendientes ni reinicio con agentes activos. Reproducir cada riesgo antes de corregirlo. Puede iniciarse la aceptación mediante API autenticada existente.

## Phase 1 — Diseño

### Configuración y autoridad

Para el MCP propio de CAO, verificar terminal ID, API host/port, durable home y autenticación en el subproceso, tanto al crear sesión como ventanas posteriores. Codex reenvía nombres con `env_vars`, nunca bearer como valor de `-c`. Claude/OpenCode usan herencia o config privada según prueba real. No reenviar credenciales a MCP de terceros.

Conservar bearer local compartido y scopes existentes. Logout de navegador no se equipara a revocación de ese bearer. Probar expiración y renovación: la rotación de procesos a 12h y token de 24h no garantiza actualizar el entorno de agentes vivos. Si falla, corregir lectura privada de credencial actual o cierre explícito, sin extender vigencia silenciosamente.

### Delegación e inbox

Perfiles de prueba con proveedor explícito para evitar heredar Claude en ambos hijos. Usar `assign`, `handoff`, `send_message`, caller y resultados originales. Hermanos se comunican por IDs explícitos. Conservar estados `pending/delivered/reconcile/failed`; no prometer comprensión del mensaje ni exactly-once ante cualquier caída.

### Proyectos e integraciones

Conservar `--workspace` RW y añadir `--workspace-readonly` compatible con manifiestos. Canonicalizar rutas/symlinks y admitir directorios de proyectos registrados mediante política del despliegue personal, sin cambiar CAO genérico no configurado. Es control de directorio de trabajo, no aislamiento entre procesos.

Reutilizar worktrees bajo `.cao/worktrees`; comprobar metadatos Git externos en repos ya vinculados, sin montarlos automáticamente. Comprobar integración desde el agente y distinguir loopback de contenedor/host. Conservar puente Windows MCP existente; no abrir bridge general del host.

### Lifecycle

Probar cancelación, cierre y descendientes reales, incluidos hijos separados; ausencia de ventana tmux no basta. Corregir solo el propietario responsable si falla. No matar tmux/procesos ajenos. Reconciliar terminales persistidas tras reinicio; incertidumbre del paste no debe generar repetición automática. Si aparece necesidad de migración, agregar contrato/rollback antes de implementarla.

## Phase 2 — Ejecución

Seguir [tasks.md](tasks.md): pruebas deterministas, correcciones mínimas y después aceptación real. Demo: API de tareas en Python estándar y frontend HTML/CSS/JavaScript servido en el mismo origen. Claude delega, Codex/OpenCode acuerdan contrato por mensajes, devuelven resultados y Claude integra. Verificar también `handoff` en tarea corta. Conservar proyecto; limpiar recursos temporales propios. Llamadas a proveedores con plazos acotados y sin reintentos indefinidos.

## Validation Strategy

- Configuración MCP y proveedores; tmux env; suites `test/utils/test_orchestration.py`, `test/mcp_server/test_assign.py`, `test/mcp_server/test_handoff.py`, `test/mcp_server/test_send_message.py`, `test/api/test_inbox_messages.py`, `test/services/test_inbox_service.py` y tests de scripts/lifecycle según archivos tocados.
- Composición determinista con dependencias reales para permisos, mensajes y procesos; mock de proveedor no acredita aceptación de los tres proveedores.
- Prueba real según [quickstart.md](quickstart.md), con navegador realizando operaciones de API/frontend y resumen por escenario PASS/FAIL/PENDING.
- Gate `project-composition-check "$(cat .ai/project-name)"`, revisión final y `system-composition-review`; registrar en `composition-review.md` límites y evidencia saneada.
- Graphify: actualizar según cambios estructurales reales, no por estos documentos. No duplicación en vault.

## Complexity Tracking

Sin excepciones a la constitución: no hay coordinador, sandbox, protocolo ni base de datos nuevos.

## Cierre contra código actual — 2026-10-05

Se reutilizan `test/fixtures/cao_server.py`, las suites de inbox/lifecycle y
`scripts/validate_collaboration_demo.py`, además de la aceptación autónoma de
008. No se crean fixtures ni un segundo coordinador para satisfacer nombres de
archivo antiguos. Las pruebas históricas conservan su fecha y límites.

Los huecos comprobados de T005–T008 y T016–T017 se cierran en sus propietarios existentes:
configuración exclusiva del MCP propio, bearer en cabecera del CLI normal,
montajes RO explícitos y política optativa `CAO_REGISTERED_PROJECTS` (JSON con
rutas canónicas). Esta política solo admite el directorio inicial; Docker
impone RO. No se afirma aislamiento de herramientas ni de archivos entre
agentes. Modos solapados RO/RW se rechazan para evitar aperturas por otro bind.
CAO sin esta variable conserva su comportamiento genérico.

TDD: primero regresiones de configuración, montaje y admisión; luego pruebas
de composición con HTTP/SQLite/tmux y aceptación nativa. Cada escenario faltante
debe ejecutarse antes de marcar su tarea. El registro durable será un informe
de cierre en este spec; no se duplican logs ni grafos en el vault.

T021: reproducción real de `setsid` demuestra que cerrar el objeto tmux deja
su hijo ejecutándose. En Linux se comprueba usuario, `CAO_TERMINAL_ID` y socket
de `TMUX` heredados; se abre pidfd y se revalida identidad antes de señalizar.
La limpieza se integra después del cierre confirmado y en cancelación, con
plazo acotado y fallo explícito si no se confirma. No se matan procesos por
nombre ni grupos de sesiones ajenas. Un proceso que elimina deliberadamente
su contexto de identidad queda fuera de esta garantía de limpieza local.


### Verificación pendiente tras renovar login

El modo opcional `--recovery` reutiliza la misma instancia privada y los tres
trabajadores del runner completo. Una instrumentación privada solo rechaza
visibilidad de un recibo auténtico del trabajador seleccionado; nunca crea
recibos ni resultados. Con ese trabajador en reconcile, el frontend envía un
mensaje real que queda pending. Se reinicia únicamente el servidor privado,
se comprueban identidades/estado persistido y se libera la instrumentación para
acreditar entrega única y resultado actual sin reinyectar la tarea.

La renovación se acredita para el bearer del controlador mientras los agentes
siguen vivos: rechazo del vencido y aceptación del nuevo sujeto idéntico. No se
presenta como recarga automática de tokens vencidos en un MCP ya iniciado.
Se comprueban además fallo de modelo OpenCode inexistente sin afectar hermanos,
borrado de un Codex nuevo durante deferred-init y handoff nativo con timeout
corto, referencia recuperable y una única creación. Todo usa proyectos copiados,
perfiles privados y plazos; cualquier frontera no ejercitada permanece pendiente.


### Defecto OpenCode reproducido por fallo nativo

El modelo Go inexistente produjo el frame real `Upstream request failed: Model
is unavailable.` con duración, barra de entrada y footer Go idle. El parser no
lo reconocía; la API no publicaba ERROR y la aceptación agotó 150 s. Corregir
solo `_has_current_v2_model_refusal`, con regresiones de buffer y viewport y un
negativo que conserva respuestas completadas que citan ese texto. Repetir la
aceptación real, composición y build candidato después del cambio de producción.
