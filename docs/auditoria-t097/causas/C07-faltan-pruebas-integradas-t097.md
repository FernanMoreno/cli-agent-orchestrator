# C07 — Falta completar la aceptación integrada T097

**Severidad:** alta (gate de registro)
**Estado:** los casos de proxy concurrente y recuperación pasan en QEMU; falta
repetir la suite en el runner Linux de despliegue.

## Aceptación host — 2026-09-28

`test/integration/t097/` contiene tres módulos y ocho casos. La suite cruza el
backend Bubblewrap real, admisión/gateway, bootstrap Landlock/seccomp,
supervisor, proxy MCP con FD 3 y recuperación durable. Usa SQLite temporal, no
la DB del operador ni proveedores reales. El fixture falla ante prerrequisitos
ausentes, cuenta incorrecta o procesos ajenos bajo el UID broker.

En Ubuntu 26.10/QEMU TCG, kernel `7.3.0-5-generic`, Landlock ABI 11 y Bubblewrap
0.13.0 allowlisted, la suite terminó **8 passed, 0 skipped en 335,30 s** como
`caos-work-broker` (UID 999, shell `nologin`).

| Módulo | Evidencia ejecutada |
|---|---|
| `test_host_acceptance.py` | gateway y backend reales; política de descendientes; filesystem, red, SysV IPC, FDs y `/proc`; revocación antes de GO y ACK perdido |
| `test_sibling_isolation.py` | hermano en otro user namespace; lecturas `/proc`, `ptrace` y `pidfd_getfd` denegados durante pre-GO |
| `test_proxy_concurrency.py` | dos intentos simultáneos con checkouts, endpoints, secretos, respuestas y efectos distintos; sibling no lee ni extrae FD 3; efecto incierto persiste tras recrear repositorio/gateway y no se reenvía |

El test de concurrencia usa el worker C estático real y un rendezvous en los dos
upstreams: los dos requests deben estar activos a la vez. Cada intento escribe
su efecto durable como `completed`. El segundo caso simula pérdida de respuesta
del upstream, vuelve a abrir el repositorio SQLite con objetos nuevos y
comprueba que el efecto sigue `uncertain`, no hay segundo upstream ni
redelivery.

En TCG, cada revalidación completa del proof de aislamiento tarda 8–9 s. Sólo
para esa ejecución emulada se usó `T097_TEST_WORKER_TIMEOUT_SECONDS=45`; el
fixture conserva 10 s por defecto. La repetición en el runner nativo debe dejar
esa variable sin definir.

Tras imponer en el preflight la cuenta broker configurada, se volvió a copiar
el código actual al guest y repetir la aceptación como `caos-work-broker` con
`CAO_WORK_BROKER_ACCOUNT=caos-work-broker`: **8 passed, 0 skipped en 327,65 s**.
Esto valida el nuevo control en QEMU; el gate del runner dedicado sigue
pendiente.

## Decisión sobre el UID host

Una sonda previa confirmó que un proceso host ordinario del mismo UID puede
hacer `ptrace`/`pidfd_getfd` al bootstrap pre-GO con Yama=1. La decisión es
ejecutar el proceso CAO que crea namespaces/proxies como una cuenta OS dedicada,
no root, sin login interactivo ni otros procesos host. Esa cuenta se considera
confiable. Cambiar el `uid_map` por intento no sustituye esta frontera.

## Pendiente

La suite QEMU no equivale al runner de despliegue. El workflow
`.github/workflows/t097-host-acceptance.yml` ya define el gate, pero falta
provisionar el runner dedicado `cao-t097-host`, configurar la cuenta broker y
obtener allí un resultado sin skips y con el timeout predeterminado de 10 s.
Hasta entonces, `WORK_BACKENDS = {}` y T097/T019 siguen `[ ]`.

## Relación

- Solución: [S07](../soluciones/S07-pruebas-integradas-t097.md).
