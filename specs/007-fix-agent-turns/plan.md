# Implementation Plan: Corrección del arranque y recuperación de turnos

**Branch**: `main` (sin crear rama) | **Date**: 2026-10-02 | **Spec**: [spec.md](spec.md)

## Summary

Corregir la disponibilidad de OpenCode, impedir la entrega temprana de tareas antes de conectar el MCP de CAO en Codex/OpenCode v2 y trasladar el diagnóstico de turnos inciertos al turno durable, independiente de NativeChild. Conservar recibos, entrega única e inbox. Exponer consulta, verificación y cancelación explícitas con identidad de generación; distinguir resultado pendiente y reconciliación de errores internos.

## Technical Context

### Corrección de readiness encontrada el 2026-10-05

La lectura del registro privado debe reconocer `message="mcp connected" server=<id>`, contrastado con el registro real de OpenCode 2.0.18. El descubrimiento de CAO debe omitir `enabled: false` (v1) y `disabled: true` (v2), porque el adaptador no inicia esos servidores. Se reproducirán ambos errores antes de modificar el proveedor; la prueba real continúa siendo una aceptación independiente de las pruebas deterministas.

La repetición real reprodujo `CONNECT_TIMEOUT after 30000ms` en Claude. Para el MCP de CAO, se configura `alwaysLoad: true` y se dan valores por defecto a `MCP_TIMEOUT` y `MCP_CONNECT_TIMEOUT_MS` con el presupuesto del proveedor, en milisegundos. Las expansiones de shell conservan overrides del entorno efectivo del pane. La identificación del MCP propio se comparte en `utils/mcp_resolution.py` para evitar divergencias entre proveedores. Referencias oficiales: [timeouts](https://code.claude.com/docs/en/env-vars) y [alwaysLoad](https://code.claude.com/docs/en/mcp#exempt-a-server-from-deferral).

La prueba real posterior no produjo el archivo XDG esperado. El help del OpenCode instalado documenta que `--print-logs` emite stderr y que los logs del servidor requieren `--standalone`. Para sesiones v2 con CAO configurado, se añaden esos flags con nivel INFO y se redirige stderr a `mcp-startup.log` en el directorio privado 0700. La espera lee únicamente ese archivo. El stdout conserva el TUI.

- **Language/Version**: Python >=3.10; TypeScript/React en web.
- **Primary Dependencies**: FastAPI, SQLAlchemy, libtmux, Click, requests; sin dependencias nuevas.
- **Storage**: SQLite/PostgreSQL existentes. Tabla aditiva de recuperación por terminal/generación, sin alterar CHECK del recibo existente.
- **Testing**: pytest; Vitest y compilación TypeScript; gate project-composition-check.
- **Target Platform**: Linux/macOS con tmux; despliegue Docker personal como prueba real aislada.
- **Project Type**: servidor, CLI, MCP y web compartidos.
- **Performance Goals**: tres intentos automáticos acotados; diagnóstico de respuesta terminada en <=60 s. Sin plazo para trabajo activo.
- **Constraints**: ninguna reentrega automática incierta; CAS por generación; sin secretos en diagnóstico; no tocar instalación personal ni trabajo preexistente.
- **Scale/Scope**: tres historias de spec007; reutilizar coordinación de spec006 sin duplicar su runner ni ampliar permisos.

## Constitution Check

Pre-diseño: PASS. Spec revisado existente; git status inspeccionado; diagnóstico previo contrastable. Diseño y tareas preceden código; reproducción determinista y TDD por frontera. No commits ni pushes.
Post-diseño y post-implementación anteriores: PASS con los límites registrados en composition-review.md; esa evidencia corresponde al snapshot y proyecto de prueba del 2026-10-02. La revalidación del 2026-10-05 añadió FR-018 y detectó un bloqueo del entorno antes de crear trabajadores; la aceptación de este snapshot permanece abierta. Véase el apéndice actual de real-validation.md. Rollback exige resolver turnos activos antes de volver a una versión que no conoce el overlay de cancelación.

## Design

1. **OpenCode**: resolver ejecutable absoluto y fijar PATH explícito en validación y lanzamiento. Validar archivo nativo seleccionado usando normalización existente, antes de generar configuración o entregar tarea. No instalar perfiles implícitamente. En v2, mantener datos privados por terminal y esperar la conexión del MCP de CAO después de la pantalla lista y antes de completar inicialización; timeout aborta el lanzamiento.
2. **Readiness MCP**: Codex marca requerido el MCP de CAO y asigna un timeout explícito de arranque, sin cambiar los demás servidores. La inicialización OpenCode v2 usa evidencia de su propio proceso/datos privados, no una señal compartida con otros terminales. Ningún primer prompt se entrega hasta tener esa evidencia.
3. **Turno durable**: recibo sigue siendo autoridad de identidad y resultado. Tabla aditiva conserva diagnóstico, intentos, detección final, cancelación y resultado verificado por generación. No depender de relación NativeChild para supervisor. Las escrituras de verificación y cancelación compiten mediante transacción/CAS; ninguna puede sobrescribir estado final contrario.
4. **Monitor**: solo una señal final válida inicia presupuesto de verificación. Tres intentos, sin reenviar entrada; la proyección deja de mostrar procesamiento al requerir reconciliación. Reinicio conserva presupuesto y diagnóstico. Diálogos y cuota mantienen sus estados.
5. **Recuperación**: GET consulta; POST verify vuelve a leer evidencia sin enviar tarea. POST cancel exige generación y fence durable antes de detener ejecución. No se borra historial ni inbox. No liberar terminal por un simple C-c o un frame libre; una cancelación sin parada confirmada sigue bloqueando. Reutilizar backend para detener/reiniciar el pane propio, preservando identidad, o comunicar indisponibilidad explícita si backend no soporta recuperación segura.
6. **Resultado**: LAST retorna estado tipado de pendiente/reconciliación; errores reales de captura/persistencia siguen 500. FULL conserva transcripción sin convertirla en resultado. Resultado verificado permanece estable por generación.
7. **Consumidores**: API, CLI/MCP y web consumen misma proyección y acciones. Reutilizar autorización read/write, rechazo Work-owned y herramientas existentes; nuevas acciones no amplían permisos.

## Project Structure

```text
src/cli_agent_orchestrator/
  clients/database.py
  providers/{base.py,codex.py,opencode_cli.py}
  utils/opencode_v2.py
  services/{terminal_service.py,status_monitor.py,turn_recovery_service.py}
  models/terminal.py
  api/main.py
  utils/orchestration.py
  cli/commands/agent.py
  mcp_server/server.py
web/src/{api.ts,components/TerminalView.tsx}
scripts/docker_install.py
test/{providers,services,clients,api,integration}/
specs/007-fix-agent-turns/{research.md,data-model.md,contracts/,quickstart.md,tasks.md}
```

## Validation and stop condition

Reproducir shell PATH y perfil ausente, supervisor ordinario sin recibo, consulta pendiente vs fallo real. Cubrir recibo antiguo/eco, carrera verify/cancel, reinicio, inbox bloqueado y resultado estable. Ejecutar regresiones pertinentes, arquitectura y gate de composición; revisar diff. Prueba real usa recursos propios aislados, tres proveedores y navegador; registrar prerrequisitos ausentes y resultados parciales. No marcar aceptación global si falta SC-006.

## Complexity Tracking

Tabla aditiva necesaria para historial de generaciones y cancelación sin migrar CHECK existente. No se añade nueva orquestación; NativeChild conserva su responsabilidad de delegación.

## Plan de corrección T032/T033 — 2026-10-05

1. Reproducir antes del cambio: transporte sin timeout, cleanup que sustituye el fallo de pegado y ausencia del contrato de fase en los prompts. Conservar snapshots de archivos modificados para revisar solo este incremento del workspace sucio.
2. Introducir `clients/tmux_transport.py` con Server compatible con libtmux que ejecuta comandos con límite de cinco segundos, conserva selectors/config/color y el formato stdout/stderr del adaptador. Evitar monkeypatch global y errores que impriman payloads. Reutilizar en el cliente y acotar sus llamadas CLI directas. El timeout se comunica como transporte incierto, nunca como ausencia. Cleanup es best effort acotado y no sustituye una excepción previa.
3. Corregir únicamente el texto del contrato de recibo compartido: cierre explícito de fase de coordinación asíncrona, resultados pendientes y callbacks como turnos posteriores, sin declarar éxito de trabajadores. No cambiar CAS, hashes, generaciones, parser ni desbloqueo. Alinear skill canónica, copias distribuidas y perfiles de supervisor.
4. Ejecutar TDD, suites cliente/recibos/inbox, reproducción tmux real, composición y Graphify dirigido. Repetir colaboración real con stderr/health/pilas archivados y detener solo recursos propios. No cambiar swap ni procesos ajenos.

### Ajuste del transporte tras inspeccionar libtmux

El libtmux instalado evita `Server.cmd` en sus listados y factories: `neo.fetch_objs` llama directamente al constructor compartido `tmux_cmd`. Una subclase de Server sola dejaría listados sin límite. Se adaptará una vez el constructor de transporte de libtmux dentro del proceso CAO, conservando su representación y sin parchear `subprocess`, ejecutar en shell ni duplicar las clases Session/Window/Pane. El adaptador mantiene atributos de resultado y selectors del libtmux original. La instalación es idempotente; las llamadas CLI propias comparten el mismo presupuesto. Esta modificación afecta explícitamente todas las llamadas libtmux del proceso CAO.

### Readiness Claude encontrada en la repetición real

La repetición posterior a T032/T033 mantuvo `/health` disponible, pero Claude respondió que el MCP seguía conectando al recibir la tarea. `alwaysLoad` no basta como evidencia de readiness. Añadir una señal privada, atómica y con nonce por lanzamiento, emitida por el MCP propio tras un `tools/list` del cliente completado mediante el middleware de FastMCP instalado. Claude espera esa señal antes de declarar initialized. Reusar identidad/config MCP y escritura atómica; no interpretar textos del modelo ni requerir debug logs. Omitir señal para terceros y perfiles nativos que CAO no configura. El fallo de importación por memoria deja la señal ausente y bloquea la primera tarea, sin repetirla.

## Plan de auditoría adicional — 2026-10-05

Constitution: se preservan cambios ajenos, se reusa spec007, no hay commits/publicación; diagnósticos y pruebas preceden a correcciones. Decisión de conocimiento: conclusiones durables en el informe de auditoría del spec; no se duplican logs/Graphify en Obsidian.

1. Congelar snapshots de archivos afectados en CAO y demo, y recoger tiempos/recursos históricos.
2. Graphify de extracción OpenCode, contrato/recibos y salud; comprobar llamadas reales en código.
3. TDD: fixtures de salida real con párrafos del prompt y recibos; API HTTP real en archivos temporales para UTF-8/límite; salud con dependencias lentas inyectadas para localizar bloqueo del event loop.
4. Demo: decodificar bytes explícitamente como UTF-8 antes de json.loads; conservar límite de 16 KiB y documentarlo en CONTRACT/README. Evitar nuevo máximo de título que invalide datos existentes.
5. OpenCode: reutilizar la frontera completa del contrato que ya existe en la ruta de recibo actual también en extracción por duración; no modificar nonce/CAS/redelivery.
6. Salud: auditar código síncrono dentro de async, PATH/montaje, middleware y monitor; aplicar solo correcciones demostradas por reproducción. Preservar payload HTTP de health y manejo de errores.
7. Hacer durable el runner y comprobar recibos/secuencias/callbacks actuales; verificar cada frontera con pruebas reales, gate de composición y diff del incremento.

No hay migración de datos ni dependencias nuevas. HTTP de demo y contratos recibo/provider se verifican con tests propios equivalentes a Pact, porque se despliegan en el mismo proceso/local y no tienen servicios independientes versionados.

Diagnóstico ampliado: una captura histórica del hilo principal entra por WorkflowContinuationDriver.serve -> tick -> WorkRepository.read_snapshot -> _verify -> _schema_objects. Todas las lecturas/transacciones síncronas de tick/drive/shutdown y stop/resume del coordinador deben conservar su conexión y verificación dentro de un mismo worker; tasks y subprocess asyncio permanecen en el event loop. La ruta health también sondea PATH en async; se usará la ejecución síncrona de FastAPI en worker preservando su payload. Nuevas reproducciones fallan con slow_snapshot y slow_which.

Diagnóstico de scratch: _materialize_snapshot usa resume-{run_id}.py con O_TRUNC; dos propietarios/repositorios que comparten scratch pueden colisionar, y el cleanup de uno borra el snapshot del otro. Se sustituye por creación exclusiva con nombre generado por tempfile; no se elimina fencing/guard_drive. La colisión se reproduce directamente; no se declara que explique individualmente el environ vacío histórico sin stderr del hijo.

## Ejecución pendiente de SC-006 — 2026-10-05

Clasificación: validación de composición significativa sobre el plan existente. Se conserva spec007/T020; no se modifica el producto CAO ni el proyecto original para inventar otra feature. Conocimiento durable: informe de aceptación y estado de los artefactos; logs sólo en archivo privado local.

Escenario acotado de implementación en copia del proyecto del usuario: `GET /api/summary` devuelve directamente `{total, completed, pending}` con enteros, sin mutar datos; frontend `#task-summary` muestra `Completadas: X de Y`, consulta ese endpoint al cargar, crear y completar, y muestra `Resumen no disponible` si falla. Codex implementa API/tests/contrato; OpenCode implementa frontend/tests; Claude delega con CAO, recibe los dos callbacks, revisa ambas partes, ejecuta los tests y escribe INTEGRATION.md. Los perfiles se instalan antes de entregar la tarea.

Prueba externa del mismo artefacto: API real y Chromium con datos temporales, estado vacío, creación, completado, recarga, fallo de resumen, persistencia tras reinicio. Acreditación: tres terminales reales, exactamente un trabajador por proveedor, callbacks recibidos, recibos verificados, archivos producidos por los agentes y tests/browser PASS. Sin arreglar rutas ni forzar recibos. Se archivan artefactos/capturas, se verifica el original por manifiesto y se cierran sólo recursos propios. Se detiene al cumplir SC-006.

## Investigación del cierre abrupto — 2026-10-05

Alcance autorizado: diagnóstico profundo; producto sin cambios hasta demostrar mecanismo. Se conservan la aceptación SC-006 y los límites históricos. Matriz: reproducer aislado CPython con/sin watchdog y con volcado desde Python bajo GIL; allocator normal/debug; captura de señal/core/traza GDB local; servidor CAO real con carga HTTP y volcado controlado; comparación del mismo escenario real con diagnóstico seguro. Se separará reproducción del mecanismo de atribución retrospectiva del episodio sin core. Sólo se persistirán conclusiones/evidencia resumida en el spec, no dumps de memoria.

Mecanismo reproducido: SIGSEGV del watchdog en CPython 3.12.13 y 3.14.4; GDB muestra metadatos liberados y caída en dump_frame/faulthandler_thread. Corrección acotada autorizada por las peticiones previas de corregir hallazgos: sólo el instrumento del runner, con muestreador Python/atexit y reporting nativo conservado. Regresión nativa con allocator debug y comparación de servidor real; repetir colaboración con el runner corregido. No actualizar Python ni cambiar producto/SQLite/proveedores.
