# C07 — Falta completar la aceptación integrada T097

**Severidad:** alta (gate de registro)
**Estado:** la suite hosted Ubuntu 26.10/QEMU TCG pasó 8/8 sin skips en el run
`36460464186`. El workflow quedó rojo después de pytest porque `setup-uv`
intentó guardar una caché vacía del host. Se desactiva esa caché y se repite
para completar T101.

## Aceptación QEMU guest — 2026-09-28

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
fixture conserva 10 s por defecto. La repetición hosted deja esa variable sin
definir.

Tras imponer en el preflight la cuenta broker configurada, se volvió a copiar
el código actual al guest y repetir la aceptación como `caos-work-broker` con
`CAO_WORK_BROKER_ACCOUNT=caos-work-broker`: **8 passed, 0 skipped en 327,65 s**.
Esto valida el nuevo control en QEMU; el gate del runner hosted sigue pendiente.

## Decisión sobre el UID host

Una sonda previa confirmó que un proceso host ordinario del mismo UID puede
hacer `ptrace`/`pidfd_getfd` al bootstrap pre-GO con Yama=1. La decisión es
ejecutar el proceso CAO que crea namespaces/proxies como una cuenta OS dedicada,
no root, sin login interactivo ni otros procesos host. Esa cuenta se considera
confiable. Cambiar el `uid_map` por intento no sustituye esta frontera.

## Pendiente

El workflow `.github/workflows/t097-host-acceptance.yml` orquesta QEMU TCG
desde GitHub-hosted `ubuntu-24.04`. Los intentos de ejecutar en el kernel
directo confirmaron ABI 7 en `ubuntu-24.04` y ABI 8 en `ubuntu-26.04`, ambos
inferiores al requisito 9. El workflow QEMU fija la imagen Ubuntu 26.10 por
fecha y SHA-256; construye Bubblewrap 0.13.0 dentro del guest, prepara allí los
sysctls, crea la cuenta broker desde la variable del repositorio y ejecuta la
suite con ABI >= 9. En TCG usa timeout 45 s y falla si pytest informa skips.
La aceptación sólo acredita ese guest fijado, no el kernel del runner o de
despliegue.

La primera ejecución remota también fija el digest del binario compilado en
ese guest. Si aún no está allowlisted, la suite lo rechaza y el log imprime el
hash; sólo se añade tras verificar origen/configuración de build, y se exige
un workflow completo verde. El resultado no certifica hosts de despliegue.
Hasta el run global verde, `WORK_BACKENDS = {}` y T097/T019 siguen `[ ]`.

El run [`36460464186`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36460464186)
(`48909237`) ejecutó esos ocho casos como `caos-work-broker`: **8 passed, 0
skipped en 201,67 s**, con Landlock ABI 11 y Bubblewrap SHA-256
`15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250`. El job
falló después, al guardar la caché de `setup-uv`; esa acción corre en el host,
pero `uv` instaló dentro del guest. El workflow desactivará su cache host y
debe repetirse para estado global verde. La suite prueba el perfil guest; no
certifica el host de despliegue.

## Relación

- Solución: [S07](../soluciones/S07-pruebas-integradas-t097.md).
