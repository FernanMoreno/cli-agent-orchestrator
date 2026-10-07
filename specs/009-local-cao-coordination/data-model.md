# Modelo de datos: Coordinación entre instancias CAO locales

## Instancia local

Identidad de perfil persistente y anuncio del proceso activo.

| Campo | Tipo | Regla |
|---|---|---|
| `instance_id` | UUID | Estable para un perfil CAO; se genera una sola vez. |
| `display_name` | texto | Nombre local mostrado al descubrir/emparejar; longitud acotada, sin secretos. |
| `profile_home` | ruta | Directorio de datos CAO; nunca se envía como ruta de archivos arbitraria del par. |
| `process_generation` | UUID | Nuevo en cada arranque del servidor; descarta anuncios antiguos de PID/puerto. |
| `pid` | entero | Indicio de vida; se valida junto con hora de inicio y reto API. |
| `started_at` | fecha/hora | Ayuda a detectar reutilización de PID. |
| `loopback_port` | entero | Puerto API local; nunca se anuncia un hostname de LAN. |
| `last_seen` | fecha/hora | Indicio para descubrir o depurar procesos obsoletos. |

## Vinculación de proyecto

Autorización local compartida solo entre pares identificados.

| Campo | Tipo | Regla |
|---|---|---|
| `project_id` | texto opaco | Derivado de identidad canónica; no es marcador de memoria ni credencial. |
| `canonical_root` | ruta | Directorio real existente autorizado por la persona. |
| `git_common_dir` | ruta o nulo | Directorio Git común, cuando el proyecto usa Git. |
| `permissions` | conjunto | Permisos explícitos de consultar/enviar tarea y política de escritura. |
| `created_by_instance` | UUID | Perfil CAO que creó la vinculación. |

La validación exige que coincida la ruta canónica o el directorio Git común verificado, rechaza escapes por enlaces simbólicos y rechaza directorios de trabajo que salgan del proyecto.

## Permiso de par

Permiso revocable de un perfil para otro y para un proyecto concreto.

| Campo | Tipo | Regla |
|---|---|---|
| `grant_id` | UUID | Registro de autorización único. |
| `grantor_instance_id` | UUID | CAO que acepta operaciones del par. |
| `peer_instance_id` | UUID | Identidad de perfil del otro CAO. |
| `project_id` | texto opaco | Exactamente una vinculación de proyecto. |
| `scopes` | conjunto | Acciones limitadas, por ejemplo `task:submit`, `task:status`, `task:cancel`. |
| `peer_public_key` | clave Ed25519 pública | Vincula el grant al perfil emparejado; no permite firmar solicitudes. |
| `created_at` / `revoked_at` | fecha/hora | La revocación bloquea nuevas operaciones de inmediato. |

La clave privada Ed25519 se guarda solo en `CAO_HOME_DIR/local-peer-signing-key`, con permisos de propietario. Cada petición firma método, ruta, query, proyecto, perfil, generación de proceso, timestamp, nonce y hash del cuerpo. `local_peer_request_nonces` retiene nonces brevemente para rechazar replay. Ninguna credencial bearer de par se escribe en SQLite.

## Reto de emparejamiento

Ceremonia de autorización de un solo uso para dos instancias activas.

| Campo | Tipo | Regla |
|---|---|---|
| `challenge_id` | UUID | Intento de emparejamiento único. |
| `initiator_instance_id` | UUID | Debe coincidir con un registro local activo. |
| `candidate_instance_id` | UUID | Debe responder al reto por loopback. |
| `initiator_public_key` / `candidate_public_key` | clave pública | Se comprueban contra la identidad viva y se fijan en ambos grants. |
| `initiator_loopback_port` | entero | Puerto del origen para completar aceptación; destino derivado como `127.0.0.1`, nunca hostname remoto. |
| `code_verifier` | bytes/texto | Verificador temporal de un solo uso; el código legible no se guarda en logs. |
| `project_id` | texto opaco | Proyecto que ambos perfiles validan antes de crear permisos. |
| `requested_scopes` | conjunto | Se muestra a la persona y requiere aceptación explícita. |
| `expires_at` / `consumed_at` | fecha/hora | Retos vencidos o usados no se pueden repetir. |

## Tarea coordinada

Vista durable en el origen del trabajo enviado a un par.

| Campo | Tipo | Regla |
|---|---|---|
| `task_id` | UUID | Identidad pública de coordinación. |
| `operation_key` | texto | Clave idempotente estable; única por propietario y proyecto. |
| `request_hash` | hexadecimal | Hash canónico de tarea, proyecto, par, perfil y política de escritura. |
| `source_instance_id` / `target_instance_id` | UUID | Identidades de origen y destino inmutables. |
| `requester_terminal_id` | ID de terminal | Aísla consulta/cancelación de tareas entre agentes de una misma instancia CAO. |
| `project_id` | texto opaco | Debe coincidir con un permiso activo del par. |
| `remote_assignment_id` | texto o nulo | Identidad durable de asignación destino tras aceptación. |
| `state` | enumeración | `accepted`, `running`, `succeeded`, `failed`, `cancelled`, `interrupted`, `reconcile`. |
| `worktree_path` | ruta o nulo | Árbol Git aislado; se valida dentro de la raíz CAO administrada. |
| `result_ref` | referencia opaca o nulo | Referencia autorizada al recibo/salida; evita duplicar salidas grandes. |
| `created_at` / `updated_at` | fecha/hora | Proyección monótona de estado. |

Repetir una clave con el mismo hash devuelve la tarea existente; cambiar el contenido bajo la misma clave da conflicto. Un envío incierto nunca autoriza repetir el efecto.

## Lease de escritura del proyecto

Protección entre perfiles para proyectos sin Git. Vive en el registro SQLite común `Path.home()/.aws/cli-agent-orchestrator/local-peers/registry.sqlite3`, no en la base independiente de un perfil, para que todos los procesos serialicen el mismo proyecto. La adquisición y liberación se hacen en transacciones atómicas con unicidad por `project_id`.

| Campo | Tipo | Regla |
|---|---|---|
| `project_id` | texto opaco | Un lease de escritura por proyecto. |
| `task_id` | UUID | Tarea dueña del lease. |
| `owner_instance_id` | UUID | CAO que admitió la escritura. |
| `state` | enumeración | `held`, `interrupted`, `reconcile`, `released`. |
| `heartbeat_at` | fecha/hora | Solo indica actividad; su vencimiento no libera el lease por sí solo. |

Un lease interrumpido sigue bloqueando hasta que CAO verifica que el trabajador terminó o la persona confirma en `cao peer reconcile <task-id>` que detuvo y revisó el trabajador. CAO no libera leases por timeout ni por un registro de proceso obsoleto.

## Transiciones

- Emparejamiento: `pending → accepted → revoked`; `pending → expired`.
- Tarea: `accepted → running → succeeded | failed | cancelled`; un resultado incierto puede pasar a `reconcile`; la pérdida del proceso puede pasar a `interrupted`. Solo una recuperación verificada resuelve una tarea interrumpida.
- Lease: `held → released` tras finalización/parada verificada; `held → interrupted → reconcile → released` solo tras resolución segura.
