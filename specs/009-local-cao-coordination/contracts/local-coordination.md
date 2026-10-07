# Contratos: Coordinación local

Estos son contratos internos previstos para instancias CAO en una PC. No crean una API cloud pública. Todas las rutas exigen una petición loopback; las rutas de tareas también requieren una capacidad no revocada y limitada a ese par/proyecto.

## Registro local

- Cada perfil CAO activo anuncia ID de instancia, ID de proceso, PID y hora de inicio, nombre visible y puerto loopback en el registro SQLite privado del usuario:
  `Path.home()/.aws/cli-agent-orchestrator/local-peers/registry.sqlite3`.
- El registro solo propone candidatos. El cliente comprueba que estén vivos y valida su identidad con el servidor antes de mostrarlos o emparejarlos.
- Solo se quitan registros obsoletos tras fallar la comprobación de generación de proceso y la consulta de identidad al endpoint.
- `GET /local-coordination/identity` devuelve la identidad estable del perfil y la generación del proceso vivo. Solo responde por loopback.

## Flujo de emparejamiento

1. `cao peer list` muestra las instancias locales validadas.
2. En el origen, `cao peer pair <instance-id> --project <path>` crea un reto y muestra un código temporal de un solo uso.
3. En el destino, `cao peer accept <challenge-id>` pide el código, muestra la identidad/proyecto/acciones y requiere confirmación explícita.
4. El destino confirma el reto con el origen por loopback; ambos guardan permisos revocables anclados a la clave pública del par. No se comparten tokens bearer ni el token maestro.
5. `cao peer revoke <instance-id> --project <path>` bloquea solicitudes nuevas y conserva visible el estado de tareas activas.

Si no coincide la identidad o el proyecto, no se crea permiso ni se comparte trabajo.

## Rutas HTTP

| Método y ruta | Propósito | Autorización |
|---|---|---|
| `GET /local-coordination/identity` | Verificar identidad y generación del servidor vivo. | Loopback local. |
| `GET /local-coordination/instances` | Listar candidatos locales activos del usuario. | Loopback local. |
| `POST /local-coordination/pairings` | Crear reto temporal de emparejamiento. | Loopback + confirmación explícita. |
| `POST /local-coordination/pairings/{challenge_id}/accept` | Aceptar identidad, proyecto y permisos del par. | Loopback + reto de un solo uso. |
| `GET /local-coordination/projects/verify` | Comparar identidad canónica de proyecto entre perfiles. | Loopback + reto pendiente. |
| `POST /local-coordination/tasks` | Enviar una tarea idempotente a un CAO emparejado. | Firma del par para `task:submit` en el proyecto. |
| `GET /local-coordination/tasks/{task_id}` | Leer estado/resultado y resolver envío incierto. | Firma del mismo par/proyecto/terminal con `task:status`. |
| `POST /local-coordination/tasks/{task_id}/cancel` | Solicitar cancelación y confirmar si el proceso paró. | Firma del mismo par/proyecto/terminal con `task:cancel`. |
| `DELETE /local-coordination/peers/{peer_id}` | Revocar permiso del par para un proyecto. | Firma local del par y scope `peer:revoke`. |

Errores tipados: `not_local`, `pairing_expired`, `pairing_mismatch`, `project_mismatch`, `scope_denied`, `assignment_conflict`, `task_interrupted`, `reconcile_required`. Las firmas usan timestamp y nonce de un solo uso; los errores y logs no contienen claves privadas ni tokens.

## Solicitud de tarea y recibo

Una petición incluye únicamente:

- clave de operación estable;
- identidad del par destino;
- proyecto vinculado;
- perfil de agente permitido;
- texto de tarea;
- ruta relativa al directorio autorizado;
- política de escritura solicitada.

No incluye `target_host` arbitrario, token maestro, callback elegido por quien llama ni rutas fuera del proyecto.

El recibo identifica origen/destino, proyecto, estado, referencias a asignación/terminal, worktree si aplica y referencia acotada al resultado/salida. La misma clave con la misma solicitud devuelve el recibo existente; cambiar el contenido devuelve conflicto.

## CLI y MCP

- Gestión por la persona: `cao peer list|pair|accept|status|revoke|reconcile`.
- Coordinación de agentes tras autorización: `list_local_cao_peers`, `assign_local_cao_task`, `get_local_cao_task`.
- Crear permisos, ampliar scopes y revocar son operaciones de la persona, no privilegios inferidos por un modelo.
- Se mantienen los contratos actuales de `assign`, `handoff`, `send_message`, `list_siblings` y `target_host` fleet/remoto.
