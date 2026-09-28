# S03 — Proxy anónimo con socketpair transferido por FD

Causa: [C03](../causas/C03-proxy-socket-con-nombre.md)

## Implementado

- `create_bound_attempt` consume primero el issue durable, crea
  `socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC)` y no crea ruta ni fichero.
- El broker conserva el extremo servidor. El extremo worker se transfiere al
  bootstrap mediante `SCM_RIGHTS` por el canal Unix de control; el bootstrap
  lo coloca en FD 3, exige que sea un socket y permite sólo FDs `(0,1,2,3)`.
- El ACK contiene FD 3 y `(st_dev, st_ino)`; el padre los compara con el
  extremo reservado antes de persistir setup y permitir `GO`.
- `WorkMcpEndpoint` expone el FD mientras vive y guarda la identidad del
  socket. `revoke` hace `shutdown`, cierra ambos extremos y despierta lectores
  que tengan un `dup()` del extremo broker.
- El worker puede emitir solicitudes JSON-RPC secuenciales; el proxy vuelve a
  validar proof, issue, revisión, lease y permiso antes de cada efecto.

## Límites

- El Bubblewrap 0.13 probado no ofrece `--preserve-fds`; el transporte probado
  es `SCM_RIGHTS`, no `pass_fds` hacia Bubblewrap.
- No se valida `SO_PEERCRED`/`SO_PEERPIDFD`. La identidad se liga por el FD
  reservado, su ACK y el proof del proceso. El gate de hermano del mismo UID
  exige ahora seccomp que rechaza `PR_SET_PTRACER` no nulo y Yama
  `ptrace_scope=1`, que el proof comprueba antes de `GO` y en cada efecto.
- La prueba post-`exec` demuestra que el mismo UID no puede extraer el FD con
  `pidfd_getfd` bajo esa política. Un proceso que no pueda verificar Yama=1 no
  recibe proof ni llega al efecto.
- El UID que crea namespaces recibe capacidades dentro de ellos; cambiar sólo
  `uid_map` por intento no bloquea procesos host del mismo UID. CAO Work debe
  correr bajo una cuenta OS no root, nologin y exclusiva, sin procesos host
  ajenos. Esa cuenta es parte de la base de confianza.
- `BubblewrapWorkBackend.preflight_work` ahora exige la cuenta configurada por
  `CAO_WORK_BROKER_ACCOUNT`, eUID no root e igual al UID local, y shell resuelto
  `nologin`/`false`, antes de probar Landlock o ejecutar Bubblewrap. La cuenta
  debe seguir siendo exclusiva por provisión del host.

## Verificación disponible

- El lanzamiento compuesto real verifica ACK, FD exacto, persistencia antes
  de activar secreto y GO.
- Tests cubren ausencia de ruta, revocación/cierre, varios mensajes y replay
  idéntico, y un hermano host tras `exec` bajo el filtro/Yama verificados; el
  registro de backend permanece vacío hasta la aceptación global.
- La aceptación de host ejecutó dos workers reales a la vez: endpoints,
  secretos, respuestas y efectos quedaron ligados a intentos distintos.
