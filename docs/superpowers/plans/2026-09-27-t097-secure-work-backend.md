# Plan de implementación T097: backend Work aislado

> Ejecutar en pasos pequeños con pruebas test-first. No crear commits: el usuario pidió mantener todo sin commit.

**Objetivo:** demostrar un backend Work Linux que imponga el contrato efectivo y continúe desregistrado hasta superar aceptación adversarial integrada.

**Arquitectura:** construir snapshots privados y verificar su integridad antes de `Popen`; cerrar rutas IPC por defecto con seccomp y Landlock cuando ABI lo permita. Ligar prueba de aislamiento, proceso y contrato a un endpoint MCP por FD; registrar intención/resultado de cada efecto antes y después del upstream. Ejecutar pruebas integradas con dependencias reales; conservar `WORK_BACKENDS = {}` mientras falte cualquier gate.

**Stack:** Python 3.12, SQLite, Linux Bubblewrap, Landlock, seccomp BPF, pytest.

**Spec:** `specs/001-verifiable-orchestration/spec.md`, tarea T097 en `specs/001-verifiable-orchestration/tasks.md`, y diseños S01–S08 en `docs/auditoria-t097/soluciones/`.

## Restricciones globales

- No registrar ni activar `BubblewrapWorkBackend` antes de aceptación completa.
- No activar T019, T035, ingreso público, proveedores reales ni DB del operador.
- Rechazar antes de cualquier efecto cuando plataforma, contrato o evidencia no se puedan verificar.
- No tratar WSL2 con Bubblewrap 0.11.1 y Landlock ABI 7 como host de aceptación.
- Preservar cambios previos del workspace; modificar sólo archivos necesarios para T097.
- No hacer commit ni push.

---

### Tarea 1: Snapshot verificable de runtime, código y dependencias

**Archivos:**

- Crear `src/cli_agent_orchestrator/services/work_bubblewrap_runtime_snapshot.py`.
- Modificar `test/security/test_work_bubblewrap_runtime_snapshot.py`.
- Crear `test/security/test_work_bubblewrap_runtime_snapshot_module.py` para RED importable.

**Interfaz entregada:** `prepare_runtime_snapshot(runtime, source, deps, *, parent, max_bytes, max_files)` devuelve context manager con `path`, `runtime_path`, `package_path`, `deps_path`, `validate()` y `close(cleanup_confirmed: bool)`; rechazos usan `RuntimeSnapshotError`.

- [x] Escribir test que exija que `importlib.util.find_spec(...)` encuentre el módulo; correrlo y comprobar fallo por módulo ausente.
- [x] Implementar copia recursiva sin seguir enlaces durante recorrido; aceptar sólo archivos regulares, directorios y symlinks relativos internos.
- [x] Rechazar symlink escapado, socket, FIFO, límite de bytes y límite de archivos; probar que origen queda intacto. Dispositivo se rechaza por tipo genérico de nodo.
- [x] Añadir prueba directa de nodo character/block; hace `skip` explícito si el runner no permite `mknod`.
- [x] Registrar digest y modo por archivo; validar contenido y modo; conservar snapshot si cleanup queda incierto y eliminarlo sólo tras cleanup confirmado.
- [x] Ejecutar toda suite `test/security/test_work_bubblewrap_runtime_snapshot.py`.

### Tarea 2: Usar snapshot durante lanzamiento y limpieza

**Archivos:**

- Modificar `src/cli_agent_orchestrator/services/work_bubblewrap_composition.py`.
- Extender `test/security/test_work_bubblewrap_composition.py` y `test/security/test_work_bubblewrap_socket_mount.py`.

- [x] Probar primero que argv monta copias del snapshot y no los árboles mutables originales de runtime, source y deps.
- [x] Preparar snapshot antes de construir argv y validar justo antes de `Popen`.
- [x] Mantener snapshot hasta reap confirmado; eliminarlo sólo con cleanup confirmado y retenerlo ante `UNCERTAIN`.
- [x] Probar socket creado tarde en origen, mutación del origen tras snapshot, fallo de setup y cleanup incierto.
- [x] Añadir el caso de mutación del origen después del ACK y comprobar que el snapshot montado no cambia.
- [x] Mantener mounts `/usr`, `/lib`, `/lib64` sólo con el cierre adicional demostrado por seccomp `connect=EPERM`; documentar que son binds vivos y no parte del snapshot.

### Tarea 3: Denegar conexión a sockets con nombre

**Archivos:**

- Modificar `src/cli_agent_orchestrator/services/work_process_seccomp.py` y, si hace falta, `work_process_landlock.py`.
- Extender `test/services/test_work_process_seccomp.py` y pruebas de composición.

- [x] Añadir test RED que demuestre que worker no puede ejecutar `connect()` hacia socket AF_UNIX preexistente o tardío.
- [x] Denegar `connect` en filtro seccomp para contratos sin red; el socketpair heredado no necesita `connect`.
- [x] En Landlock ABI ≥9, manejar `RESOLVE_UNIX` sin concederlo; en ABI menor, exigir snapshot + seccomp y rechazar si no se puede demostrar.
- [x] Probar política con ABI disponible, matriz simulada de ABI 7/9, y que ACK informa capacidades realmente aplicadas.

### Tarea 4: Prueba ligada y proxy por socketpair

**Archivos:**

- Modificar `src/cli_agent_orchestrator/services/work_bubblewrap_composition.py`, `work_mcp_proxy.py` y `work_bubblewrap_setup_intent.py`.
- Extender `test/services/test_work_mcp_proxy.py`, `test/security/test_work_bubblewrap_composition.py` y `test/security/test_work_bubblewrap_bound_content.py`.

- [x] Añadir pruebas para rechazar proof de otro intento, generación, contrato o proceso terminado.
- [x] Crear proof inmutable después del ACK validado y `record_pre_go`; ligar intento, generación, revisión, hash de contrato, digest de snapshot, pidfds, starttimes e inodes de PID/user namespace.
- [x] Reemplazar endpoint con nombre por `socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC)`; transferir el FD worker por `SCM_RIGHTS` (el Bubblewrap validado no admite `--preserve-fds`) y verificar tipo, número e identidad del socket en el ACK.
- [x] Llamar `require_current(attempt_id, generation, contract_hash)` antes de activar y antes de cada efecto.
- [x] Probar inventario exacto de FD, revocación del endpoint, rechazo de proceso muerto y cambios de starttime/PID namespace.
- [x] Cerrar PR_SET_PTRACER no nulo en seccomp, exigir Yama `ptrace_scope=1` en proof y probar que un hermano same-UID no extrae el FD después de `exec`.

### Tarea 5: Persistir efectos MCP inciertos y conciliarlos

**Archivos:**

- Añadir esquema/migración aditiva en `src/cli_agent_orchestrator/clients/work_mcp_proxy_schema.py` y `work_repository.py`.
- Modificar `src/cli_agent_orchestrator/services/work_mcp_proxy.py` y supervisor de trabajo.
- Extender `test/clients/test_work_migrations.py`, `test/services/test_work_mcp_proxy.py` y pruebas de recuperación.

- [x] Probar que la intención queda durable antes de upstream y ningún lock SQLite vive durante la llamada de red.
- [x] Añadir tablas append-only de efecto/eventos con digest de request y estados monotónicos `intent`, `completed`, `failed_before_effect`, `uncertain`, `reconciled`.
- [x] Integrar recuperación con el arranque del supervisor, confirmar la identidad/proceso exactos, marcar incertidumbre y bloquear redelivery; sólo conciliación admin explícita puede cerrarla.
- [x] Probar excepción upstream, recuperación/reconciliación explícitas, replay y ausencia de segunda llamada para un request idéntico; probar ambas ventanas `SIGKILL` con efecto externo simulado.
- [x] Registrar y exponer issues huérfanos sin efectos, backfillear los issues v34 y probar migración v35 interrumpida por `SIGKILL`, rollback y esquema previo compatible.
- [x] Definir retención append-only sin expiración automática para conservar la cerca de replay.

### Tarea 6: Aceptación integrada y gate de registro

**Archivos:**

- Crear `test/integration/t097/` con fixtures adversariales sin DB del operador.
- Modificar `src/cli_agent_orchestrator/backends/bubblewrap_backend.py` sólo cuando toda capacidad tenga evidencia.
- Mantener `src/cli_agent_orchestrator/backends/work_registry.py` vacío hasta completar aceptación.

- [ ] Añadir tests host-gated para filesystem/symlinks, descendientes exec, red/IPC, sibling isolation, FD inventory, `/proc`, reinicio y cleanup incierto.
- [ ] Recorrer ruta real `preflight_work` → admisión → lanzamiento → supervisor con Bubblewrap del host, no sólo harness privado.
- [ ] Verificar cada rechazo antes de marcador/efecto externo y cada guard inmediatamente antes del efecto.
- [x] Ejecutar arquitectura, suites focales y `project-composition-check caos`.
- [ ] Sólo registrar backend para combinación de plataforma/contratos con evidencia completa. No cerrar T097 ni T019 con pruebas omitidas.

### Tarea 7: Evidencia de host y política reproducible

**Archivos:**

- Modificar `src/cli_agent_orchestrator/backends/bubblewrap_backend.py`, configuración de digest y workflow dedicado sólo con runner disponible.
- Actualizar `docs/auditoria-t097/causas/C08-limites-del-entorno.md` y solución S08 con datos verificados.

- [x] Rechazar Bubblewrap ausente, desconocido, distinto de la versión común 0.13.0 o digest no allowlisted antes de efectos; allowlist vacía hasta aprobar artefacto.
- [x] Alinear política exacta de versión mediante constante compartida entre backend y composición; el SHA scratch de pruebas no se considera artefacto trusted.
- [ ] Ejecutar pruebas ABI 9, sibling isolation y suite integrada en VM/CI Linux nativa con Bubblewrap validado.
- [x] Mantener el gate abierto y el backend desregistrado hasta aceptar el runner Linux de despliegue.

## Actualización C07 — aceptación host QEMU — 2026-09-28

El guest Ubuntu 26.10/QEMU TCG pasó `test/integration/t097 -m t097_host`:
**8 passed, 0 skipped en 335,30 s**. Se añadieron dos launches simultáneos
con proxies MCP reales y distintos, además de recuperación de efecto incierto
tras recrear `WorkRepository`/gateway sobre el mismo SQLite. El efecto quedó
`uncertain`, sin segunda llamada upstream ni redelivery.

El proceso aceptador corrió como cuenta no root `caos-work-broker`, shell
`nologin`, sin otros procesos host. Esta es la frontera elegida tras observar
que un proceso host ordinario del mismo UID puede hacer `ptrace`/`pidfd_getfd`
antes de GO; un remapeo `uid_map` por worker no basta.

Sólo en QEMU se configuró `T097_TEST_WORKER_TIMEOUT_SECONDS=45`: las
revalidaciones de proof bajo TCG tardan 8–9 s cada una. El valor por defecto
sigue en 10 s. Falta repetir el gate sin override en el runner Linux de
despliegue. `WORK_BACKENDS={}` y T097/T019 permanecen abiertos.
