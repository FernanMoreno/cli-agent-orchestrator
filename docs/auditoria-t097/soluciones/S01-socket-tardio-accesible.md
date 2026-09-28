# S01 — Cerrar el acceso a sockets tardíos

Causa: [C01](../causas/C01-socket-tardio-accesible.md)

## Estrategia (defensa en capas; cada capa por sí sola debe bastar en su plataforma)

1. **No exponer árboles vivos del host.** Sustituir en `_build_bwrap_argv`
   los `--ro-bind` de `runtime`, `src` y `deps` por el snapshot privado de
   [S02](S02-modulo-snapshot-inexistente.md). Para `/usr`, `/lib`, `/lib64`:
   reducir al conjunto mínimo de ficheros que necesita el intérprete (o usar
   un ELF estático sin runtime Python dentro del sandbox) y copiarlos también
   al snapshot. Objetivo: ningún directorio del host compartido por bind
   dentro del sandbox.
2. **Denegar sockets con nombre en seccomp cuando el contrato no pide IPC.**
   Añadir `connect` (syscall 42 en x86_64) a `_SYSCALL_ERRORS`. El worker
   puede crear sockets, pero no conectarlos; el FD del proxy (socketpair
   heredado, S03) ya está conectado y no requiere `connect`.
3. **En ABI ≥ 9, manejar `RESOLVE_UNIX`.** Hacer que el bootstrap incluya
   siempre `RESOLVE_UNIX` en los derechos manejados cuando el kernel lo
   soporte, sin reglas que lo concedan, de modo que toda resolución de socket
   Unix por ruta se deniegue.
4. **Fail-closed por plataforma.** Si no se puede garantizar (1) + (2) en
   ABI < 9, el preflight debe rechazar contratos que no prohíban IPC antes de
   cualquier efecto.

## Prueba requerida (RED primero)

Test compuesto real con Bubblewrap 0.13 en `test/integration/`:
1. Lanzar worker hasta el ACK.
2. Tras el ACK y antes de `GO`, crear un listener `AF_UNIX` en el árbol de
   origen de runtime/src/deps y en `/usr`-equivalente de prueba.
3. Liberar el worker y hacer que intente `connect()` a la ruta visible.
4. Esperar `ENOENT` (no existe en el snapshot) o `EPERM/EACCES` (seccomp/
   Landlock); fallar si el listener acepta una conexión.
5. Repetir creando el socket durante la ejecución del worker.

## Estado implementado

- El lanzamiento monta copias privadas de runtime, fuente y dependencias.
- `/usr`, `/lib` y `/lib64` siguen como binds de solo lectura; seccomp cierra
  `connect()` también para sockets que aparezcan en esos árboles.
- Seccomp rechaza `connect()` con `EPERM`; el bootstrap comprueba ese errno y
  el ACK lo conserva como `connect_denied_errno`.
- Pruebas de kernel cubren sockets AF_UNIX preexistentes y tardíos. Las pruebas
  Landlock cubren ABI 7/9; en ABI 9 `RESOLVE_UNIX` queda manejado sin regla
  que lo conceda.
- La prueba de composición con Bubblewrap scratch sustituye `/usr` por un bind
  vivo temporal, crea un socket tardío después del ACK y exige que el worker
  reciba `EPERM` y el listener no acepte conexiones.
- Esta prueba cierra el bypass C01 en la composición. No recorre todavía el
  stack `preflight_work` → admisión → supervisor ni constituye aceptación de
  host; la aceptación guest consta en S07/S08. No hay target de producción y
  el backend queda deshabilitado.
