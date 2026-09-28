# C03 — El proxy usa un socket con nombre en lugar de un socketpair por FD

**Severidad:** alta (contradice el diseño mínimo aceptado del propio módulo)
**Estado:** endpoint cerrado; la cuenta broker dedicada define la frontera host. El gate global espera un run verde del perfil Ubuntu 26.10 en QEMU TCG, sin presentarlo como certificación del kernel del runner o de despliegue.

## Síntoma

`WorkMcpProxyManager.create_bound_attempt` publica el endpoint como un fichero
`proxy.sock` en disco.

## Causa exacta

- El docstring del módulo (`work_mcp_proxy.py:1-8`) define el diseño mínimo:
  *"one unnamed AF_UNIX socketpair endpoint passed by FD to exactly one
  worker"*.
- La implementación (`work_mcp_proxy.py:250-265`) hace lo contrario:
  `tempfile.mkdtemp(dir=endpoint_root)` → `socket(AF_UNIX)` →
  `bind(directory/"proxy.sock")` → `listen(1)` → `accept()`.
- Un socket con nombre es direccionable por ruta: cualquier proceso que pueda
  resolver la ruta y tenga el mismo UID puede conectarse, incluido un intento
  hermano. `listen(1)` + `accept()` del primero que llega no identifica al
  peer: no se comprueba `SO_PEERCRED`/`SO_PEERPIDFD` frente al PID/pidfd del
  worker registrado.
- `chmod 0o600` y un directorio `0o700` sólo filtran por UID; los workers
  hermanos corren con el mismo UID host (1000; ver C08), así que no aíslan.
- `WorkMcpEndpoint(path, _SANDBOX_PATH, ...)` presupone que la ruta se
  bindeará dentro del sandbox, lo que reintroduce exactamente la clase de
  riesgo de C01 (socket en árbol visible).

## Relación

- Agrava [C01](C01-socket-tardio-accesible.md) y
  [C04](C04-prueba-aislamiento-no-ligada.md).
- Solución: [S03](../soluciones/S03-proxy-socketpair-por-fd.md).

## Estado actual

El endpoint ya usa `socketpair` sin pathname; el bootstrap recibe el extremo
worker por `SCM_RIGHTS`, valida FD 3 y la identidad del socket en el ACK. El
proxy revoca con `shutdown` y cierra ambos extremos. La política seccomp
deniega `PR_SET_PTRACER` no nulo y la emisión del proof exige Yama
`ptrace_scope=1`. Una prueba ejecuta el filtro, hace `exec` (que reinicia
dumpability), intenta `PR_SET_PTRACER_ANY` y comprueba que un hermano del mismo
UID no puede duplicar el FD mediante `pidfd_getfd`.

Esto cierra el endpoint direccionable por pathname. La aceptación del
2026-09-28 probó dos launches Work simultáneos con proxies y secretos distintos;
un segundo user namespace no pudo leer `/proc/<pid>/fd/3` ni extraerlo con
`pidfd_getfd`.

Una sonda anterior mostró que un proceso host ordinario del mismo UID puede
hacer `ptrace` y `pidfd_getfd` antes de GO, pese a Yama=1. La decisión de diseño
es ejecutar CAO Work con una cuenta OS no root, nologin y exclusiva, sin otros
procesos host con ese UID. La aceptación comprueba esa identidad y que sólo el
proceso pytest de broker está activo al inicio. Es una frontera de confianza
del host: root y la propia cuenta broker siguen siendo confiables. Véase
[C07](C07-faltan-pruebas-integradas-t097.md) y [C08](C08-limites-del-entorno.md).

`BubblewrapWorkBackend.preflight_work` también impone la identidad en código:
captura `CAO_WORK_BROKER_ACCOUNT` al construir el backend y rechaza antes de
consultar Landlock/Bubblewrap si falta la cuenta, el eUID es root, no coincide
con el UID local o el shell resuelto no es `nologin`/`false`. La prueba unitaria
cubre cada rechazo sin permitir probes de host.
