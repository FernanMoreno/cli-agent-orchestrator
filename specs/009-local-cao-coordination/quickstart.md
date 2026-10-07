# Guía de aceptación: Coordinación local CAO

Esta guía describe cómo aceptar la función cuando esté implementada. Supone dos perfiles CAO en una misma PC y el mismo entorno local del sistema. No usa cuentas, servicios alojados ni red entre PCs.

## Requisitos previos

- Dos perfiles CAO con valores distintos de `CAO_HOME_DIR` y `CAO_API_PORT`.
- Ambos servidores iniciados en el mismo entorno local.
- El mismo directorio canónico de proyecto disponible para ambos perfiles.
- Cada perfil tiene al menos un perfil de agente trabajador configurado.
- Para trabajo Git concurrente, el repositorio puede crear worktrees administrados por CAO.

## Comprobar y emparejar

1. Iniciar ambos servidores con homes de perfil y puertos distintos.
2. Ejecutar `cao peer list`; confirmar que aparecen nombres e identidades de proceso activos.
3. Ejecutar `cao peer pair <peer-id> --project <project-path>`.
4. Confirmar la misma identidad, proyecto y permisos en la solicitud de emparejamiento.
5. En el destino, ejecutar `cao peer accept <challenge-id>`, revisar nombre/proyecto/acciones, confirmar y escribir el código temporal compartido por el origen.
6. Consultar el estado del permiso desde ambos perfiles con `cao peer list --project <project-path>`.
7. Intentar usar un host no loopback o una ruta fuera del proyecto mediante las operaciones de coordinación; CAO debe rechazarlo antes de compartir credenciales o aceptar la tarea.

## Delegar y recuperar

1. Desde un agente CAO de un proyecto emparejado, llamar `assign_local_cao_task` con una clave estable y una tarea pequeña/reversible.
2. Consultar el recibo; debe identificar ambos CAO, proyecto, trabajador, estado y resultado.
3. Repetir la solicitud con la misma clave y contenido; CAO debe devolver la misma tarea.
4. Reutilizar la clave con texto o proyecto distinto; CAO debe devolver conflicto.
5. En un proyecto Git, revisar el worktree/rama y su diff antes de integrar cambios.
6. Detener el destino durante una tarea; el origen debe mostrar interrupción o reconciliación, nunca éxito. Consultar/reintentar no debe crear otro trabajador.
7. Si una tarea sin Git deja un lease retenido, revisar/detener el trabajador y ejecutar `cao peer reconcile <task-id>` en el CAO destino. Solo una confirmación humana libera el lease; CAO mantiene la tarea en revisión.

**Límite de confianza aprobado**: el permiso de par limita las solicitudes de coordinación CAO. No crea un sandbox de sistema operativo: el trabajador hereda los permisos de archivos del usuario local configurado. Los pares deben ejecutarse solo bajo una cuenta local confiable.

## Revocar

1. Revocar el permiso desde una superficie de operador.
2. Las operaciones nuevas y consultas con ese permiso deben rechazarse con un error de autorización.
3. Las tareas activas siguen visibles como running/interrupted/reconcile hasta comprobar el estado real; revocar no las oculta ni las marca como éxito.
4. Confirmar que cada CAO sigue funcionando solo y que el modo de trabajadores locales del spec 006 no requiere pares independientes.
