# C01 — El socket tardío sigue siendo accesible desde el worker

**Severidad:** crítica (rompe el aislamiento de IPC del gate T097)
**Estado:** cerrada en la composición y aceptación integrada del guest (C07/C08); no hay host de producción planeado y el backend sigue deshabilitado.

## Síntoma

Un socket Unix con nombre que aparece en un directorio del host *después* del
ACK de setup es alcanzable (`connect()`) desde el worker aunque ese directorio
esté montado con `--ro-bind`.

## Causa exacta

Son cuatro hechos que se combinan; ninguno por separado bloquea la conexión.

1. **Los binds son vivos, no copias.**
   `src/cli_agent_orchestrator/services/work_bubblewrap_composition.py:1002-1098`
   (`_build_bwrap_argv`) monta con `--ro-bind` los árboles del host
   `/usr`, `/lib`, `/lib64`, `sys.base_prefix` (→ `/runtime`), el paquete
   fuente (→ `/src/cli_agent_orchestrator`) y `site-packages` (→ `/deps`).
   Un bind mount comparte el mismo dentry/inode del host: cualquier entrada
   creada después en el host (incluido un socket) aparece dentro del sandbox.

2. **`ro` no protege sockets.** El chequeo de sólo lectura del VFS
   (`sb_permission`/`MNT_READONLY`) sólo aplica a ficheros regulares,
   directorios y symlinks. `connect()` sobre un socket `AF_UNIX` con nombre
   sólo exige permiso de escritura DAC sobre el inode del socket; un mount
   `ro` no lo impide.

3. **`--unshare-net` no cubre sockets con nombre.** El namespace de red aísla
   sockets abstractos e interfaces, pero un socket con ruta se resuelve por el
   sistema de ficheros, que el bind comparte con el host.

4. **Ni Landlock ni seccomp restringen `connect` en este host.**
   - El bootstrap (`_BOOTSTRAP`, líneas ~237-250) sólo exige
     `landlock_abi >= 1` e instala una única regla `READ|EXECUTE` sobre
     `/exec/worker`. El derecho `LANDLOCK_ACCESS_FS_RESOLVE_UNIX` sólo existe
     desde ABI 9 (`work_process_landlock.py:52` y `:279-280`); con ABI 7 no se
     "maneja", así que la resolución de sockets Unix queda **permitida** por
     omisión.
   - `work_process_seccomp.py:60-84` (`_SYSCALL_ERRORS`) no incluye
     `socket` (41), `connect` (42) ni `socketpair` (53).

Además, la cadena bind → ACK → `GO` no tiene ningún punto que congele el
contenido: el ACK certifica el estado *en el instante del setup*, y el socket
se crea en la ventana entre ACK y `execve` o durante la ejecución del worker.

## Evidencia

- `_build_bwrap_argv`: sólo `--ro-bind` de rutas host, ningún `--ro-bind-data`
  ni copia privada para runtime/src/deps.
- `landlock_abi < 1` es el único umbral del bootstrap.
- `_SYSCALL_ERRORS` sin syscalls de socket.
- Caracterización empírica ya existente:
  `test/security/test_work_bubblewrap_socket_mount.py::test_late_socket_in_read_only_runtime_tree_remains_connectable_without_snapshot`
  crea `late.sock` en un árbol montado con `--ro-bind` después de que el worker
  esté listo y **afirma `CONNECTED`**. Es una prueba positiva del bypass, no de
  su cierre.
- La prueba compuesta
  `test_late_socket_in_live_usr_bind_is_denied_by_composed_seccomp_after_ack`
  sustituye `/usr` por un bind vivo de prueba, crea el socket después del ACK
  y confirma `EPERM` dentro del worker. La suite integrada de lanzamiento
  completo sigue en [C07](C07-faltan-pruebas-integradas-t097.md).

## Relación

- Depende de [C02](C02-modulo-snapshot-inexistente.md) (la mitigación prevista
  nunca se implementó) y de [C08](C08-limites-del-entorno.md) (ABI 7).
- Solución: [S01](../soluciones/S01-socket-tardio-accesible.md).

## Estado actual

La composición monta snapshots privados para runtime/fuente/dependencias y
seccomp deniega `connect()` con `EPERM`; el ACK exige esa evidencia. El test
compuesto crea un socket tardío en un bind `/usr` vivo después del ACK y prueba
que no llega al listener. C01 queda cerrado para esta política. La aceptación
guest T097/C07/C08 pasó; como no existe ni se prevé un host de producción,
`WORK_BACKENDS` permanece vacío por decisión de alcance. Véase
[S01](../soluciones/S01-socket-tardio-accesible.md) y
[S08](../soluciones/S08-limites-del-entorno.md).
