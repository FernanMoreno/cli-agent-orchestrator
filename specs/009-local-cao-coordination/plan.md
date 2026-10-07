# Implementation Plan: Coordinación entre instancias CAO locales

**Branch**: `009-local-cao-coordination` | **Date**: 2026-10-04 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/009-local-cao-coordination/spec.md`

## Summary

Permitir que varias instalaciones/perfiles CAO independientes se reconozcan, se autoricen y repartan tareas sobre un proyecto de una sola PC. La coordinación usará un registro local de instancias y una API accesible solo por loopback, con autorización explícita y credenciales limitadas a cada par y proyecto.

Se reutilizan las asignaciones idempotentes y la creación de terminales que ya existen. El transporte `target_host` seguirá atendiendo su caso actual de nodos remotos; no se usará como prueba de localidad ni se ampliará para representar pares CAO locales.

## Contexto técnico

**Language/Version**: Python 3.10+, TypeScript para las superficies web existentes, Rust en TUI si el alcance llega a ella.\
**Primary Dependencies**: FastAPI, Pydantic, SQLAlchemy, SQLite y CLI existente. No se propone una dependencia nueva.\
**Storage**: SQLite por perfil CAO para permisos y tareas; SQLite de registro común por usuario local en `Path.home()/.aws/cli-agent-orchestrator/local-peers/registry.sqlite3` para procesos activos y leases de escritura compartidos, con directorio 0700 y archivo 0600.
**Testing**: pytest y las verificaciones de integración/composición existentes; aceptación con dos procesos CAO reales en la misma PC.\
**Target Platform**: Una PC y un mismo entorno de ejecución/sistema de archivos local durante la primera entrega. La coordinación entre PCs queda excluida.\
**Project Type**: CLI + API local + servidor MCP; gestión para la persona por CLI, uso por agentes mediante herramientas MCP.\
**Performance Goals**: Enumerar instancias locales y consultar el estado de tareas sin bloquear el servidor ni esperar a que termine el agente.\
**Constraints**: Solo loopback; permisos por par y proyecto; no copiar el token maestro; reintentos idempotentes; un árbol de trabajo aislado por tarea Git o exclusión serializada para proyectos sin Git; no alojar servicios ni añadir cuentas.\
**Scale/Scope**: Un usuario local, varias instancias CAO y uno o más proyectos locales compartidos.

## Comprobación de Constitución

- **I. Evidencia**: se aprovechan relaciones encontradas por Graphify y cada afirmación sobre capacidades existentes se contrasta con los módulos fuente actuales.
- **II. Preservación**: el workspace ya tiene cambios amplios ajenos a este plan; las futuras modificaciones deben limitarse a rutas de CAO local identificadas aquí.
- **III. Diseño**: esta feature cruza identidad, API, permisos, persistencia, terminales y mensajería; requiere Spec Kit, Graphify y revisión de composición.
- **IV. Composición**: deben revisarse permisos, repetición de solicitudes, caída/reinicio, revocación, ownership del proyecto y conflictos entre tareas.
- **V. Cierre**: no se declarará completada hasta verificar dos instancias reales y revisar el diff final.

## Auditoría de código y reutilización

| Existing code | Evidence | Decision |
|---|---|---|
| `services/assignment_service.py` | Persiste identidad de asignación, owner, hash del request y resultado; repeticiones con la misma operación devuelven el recibo existente. | Reusar el patrón y la ruta de creación de terminales; no crear un segundo mecanismo genérico de idempotencia. |
| `api/main.py` ordinary assignment routes | Expone submit/inspect para asignaciones ordinarias y delega en `assignment_service`. | Añadir un router local dedicado; mantener la API y semántica existentes. |
| `utils/orchestration.py` `target_host` | Acepta URL, host:puerto o hostname arbitrarios para colocación remota. | No usarlo para demostrar locality, autorización o pertenencia al proyecto. No alterar su flujo fleet sin requisito. |
| `services/terminal_service.py` | Provee creación, estado y ciclo de vida de terminales; las asignaciones locales ya pueden solicitar worktree. | Reusar para iniciar trabajadores y consultar evidencia de estado. |
| `services/project_marker.py` | El marcador mantiene continuidad de identidad de memoria; no concede autoridad sobre Work o archivos. | No tratar `project_id` de memoria como permiso. Crear una vinculación de proyecto CAO con validación de ruta. |
| `terminal_service.list_siblings` / `mcp_server.list_siblings` | Descubre terminales de una instancia/sesión, incluso con opción explícita entre sesiones. | No extenderlo para representar procesos CAO distintos; crear identidad y descubrimiento de pares. |
| `constants.py` | `CAO_HOME_DIR` admite perfiles aislados y `CAO_API_PORT` permite diferenciar servidores. | Usar identidad persistente por perfil; documentar home y puerto distintos para cada CAO independiente. |

## Decisiones de diseño

1. **Plano local separado**: registro SQLite compartido en el espacio de usuario para procesos y exclusión de escritura entre perfiles + router dedicado con destinos loopback explícitos. No hay DNS, IP LAN, relay ni conexión de red arbitraria.
2. **Identidad y autorización aprobadas**: UUID persistente y clave Ed25519 privada por perfil CAO, más generación de proceso por arranque. El usuario confirma la identidad del par con un código de un solo uso. Cada grant guarda la clave pública del par y los scopes de proyecto/acciones; las peticiones se firman con timestamp y nonce para evitar tokens bearer y replay. La clave privada queda en el home privado del perfil; el token maestro CAO no cruza instancias.
   **Límite de confianza aprobado**: las instancias corren bajo el mismo usuario local confiable. El grant limita las operaciones de coordinación/API y el proyecto de la tarea; no convierte el shell del proveedor en un sandbox del sistema operativo.
3. **Proyecto**: se compara la ruta real autorizada y la raíz Git compartida cuando exista. Un marcador de memoria no equivale a autorización. No se acepta una ruta enviada por un par que escape del binding.
4. **Asignación y estado**: se conserva una clave de operación estable, owner, hash de petición y recibo durable. El servidor de origen consulta estado/resultados del servidor destino usando la identidad del par; una respuesta incierta queda en reconciliación, no crea otra tarea.
5. **Concurrencia**: en repositorios Git cada tarea de par usa un worktree aislado y devuelve referencia/path para revisión. En proyectos sin Git, todos los perfiles adquieren atómicamente un lease único por proyecto en el registro SQLite común; al caer un CAO, la tarea queda pendiente de reconciliación y el lease no se libera a ciegas.
6. **Interfaces iniciales**: comandos CAO para listar/emparejar/revocar instancias y herramientas MCP para listar pares autorizados, asignar tarea y consultar recibo. No se añade una UI nueva hasta que el flujo de backend y MCP sea estable.
7. **Instancias aisladas**: dos perfiles que deban colaborar requieren directorios de datos y puertos distintos. CAO seguirá funcionando sin crear perfiles de pares.

## Arquitectura y límites de fallo

- Un servicio pequeño de identidad/registro descubre anuncios con PID, identidad de perfil, puerto loopback y marca temporal. Cada anuncio se revalida con una comprobación viva contra el servidor; PID o archivo por sí solos no autentican.
- Un servicio de pairing crea y consume retos de un solo uso. Los grants guardan claves públicas por perfil/proyecto/scopes y fecha de revocación; los nonces consumidos bloquean replay.
- Un servicio de coordinación recibe una tarea ya autorizada, valida proyecto/scopes, reclama la clave idempotente y llama a los servicios actuales de asignación/terminal. Su ledger conserva peer, proyecto, request hash, terminal/worker asociado y estado.
- La API local devuelve estados tipados: `accepted`, `running`, `succeeded`, `failed`, `cancelled`, `interrupted` y `reconcile`. No afirma éxito por una respuesta HTTP ni por la mera creación del terminal.
- Las tareas activas pertenecen a un par y a una ruta autorizada. Revocar bloquea peticiones nuevas; una tarea en vuelo conserva estado visible y no se repite ni se marca como exitosa por desconexión.
- La autorización CAO no limita por sí sola los permisos del proceso externo del agente. Los agentes heredan los permisos de su usuario local; usar un aislamiento OS/Work para pares no se promete en esta entrega.
- El camino remoto actual (`target_host`, Kubernetes/fleet) y el modo local de un solo CAO con trabajadores (spec 006) siguen separados y operativos.

## Estructura del proyecto

### Documentation

```text
specs/009-local-cao-coordination/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/local-coordination.md
└── tasks.md
```

### Código fuente previsto (confirmar cada ruta antes de editar)

```text
src/cli_agent_orchestrator/
├── api/local_coordination_routes.py
├── cli/commands/peer.py
├── clients/database.py
├── mcp_server/server.py
└── services/
    ├── local_peer_identity.py
    ├── local_peer_registry.py
    ├── local_peer_auth.py
    └── local_peer_service.py
```

## Secuencia de implementación

1. Add local profile identity, per-user live registry and atomic shared project-write leases; add per-profile project/permission/task records.
2. Add loopback-only discovery and one-time explicit pairing.
3. Complete project/action authorization and write-conflict boundaries before exposing agent task submission.
4. Add scoped task submit/status on top of existing assignment/terminal mechanisms, with isolated Git worktrees and conservative recovery.
5. Add MCP task discovery/assignment/status tools and CLI pair/revoke operations.
6. Add stop/revocation recovery and document the manual two-process acceptance flow.

## Riesgos y mitigaciones

- **Stale registry/PID reuse**: require a live identity challenge and process-start fence before presenting a peer.
- **Master credential exposure**: use project-scoped public-key grants and signed requests; never copy `CAO_AUTH_LOCAL_TOKEN` or bearer peer secrets into another CAO or provider environment.
- **Duplicate effects after timeout**: durable operation key and hash on both sides; unknown outcome returns `reconcile`.
- **Concurrent project edits**: isolate Git tasks in worktrees; serialize non-Git writes and retain uncertain leases for operator review.
- **Compatibilidad de autenticación API**: la autorización entre pares es distinta del login de usuario/navegador y no debilita el middleware actual.
- **Acceso del proveedor a archivos**: el descubrimiento/grupo existente no es un sandbox y `work_authority.py` no reclama aislamiento del backend. Limitar las garantías de esta feature a la API/tarea CAO; si los procesos pares deben ser mutuamente no confiables, hace falta aprobar un diseño de sandbox aparte.
- **Solapamiento en workspace**: ya hay muchos archivos fuente modificados por trabajo previo. Antes de implementar, comparar cada ruta objetivo con el diff actual y preservar los cambios ajenos.

## Alcance local confirmado

La primera entrega coordina procesos CAO dentro del mismo entorno de ejecución del sistema y sistema de archivos local. Conectar instancias entre Windows, WSL y Docker queda fuera de alcance.
