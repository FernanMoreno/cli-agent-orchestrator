# S08 — Evidencia reproducible y gate de host

Causa: [C08](../causas/C08-limites-del-entorno.md)

## Decisión

El guest temporal resolvió los requisitos técnicos de C08 y ejecutó la suite
8/8 sin skips. No es el runner de despliegue. El workflow dedicado ya existe en
`.github/workflows/t097-host-acceptance.yml`; falta provisionar su runner
`cao-t097-host` y obtener allí un run verde. `WORK_BACKENDS` permanece `{}`.

## Entorno de aceptación

Guest Ubuntu 26.10 en QEMU TCG (kernel Linux del guest; no WSL2 ni runner CI
externo):

- Kernel `7.3.0-5-generic`; Landlock ABI **11**; Yama `ptrace_scope=1`;
  namespaces de usuario/PID/red/IPC disponibles.
- Bubblewrap upstream [`v0.13.0`](https://github.com/containers/bubblewrap/releases/tag/v0.13.0),
  commit `719a4fd`. Source archive SHA-256
  `4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765`.
- `/usr/bin/bwrap`: SHA-256
  `f41ba3f7be0280df0afe201f0e2eeb16a17e969782491e830e6753c67f78d70d`;
  versión exacta `0.13.0`. El backend allowlista ese digest y fija el
  descriptor durante verificación y lanzamiento.
- La suite host completa pasó como `caos-work-broker` (UID 999, shell
  `nologin`), sin otros procesos host bajo ese UID al iniciar:

  ```sh
  sudo -u caos-work-broker env \
    HOME=/home/caos-work-broker \
    CAO_WORK_BROKER_ACCOUNT=caos-work-broker \
    T097_REQUIRE_HOST_ACCEPTANCE=1 \
    T097_TEST_WORKER_TIMEOUT_SECONDS=45 \
    PYTHONPATH="$PWD/src" \
    .venv/bin/python -m pytest -q test/integration/t097 \
      -m t097_host --no-cov --tb=short
  ```

  Resultado actualizado con el preflight de broker: **8 passed, 0 skipped en
  327,65 s**. El override a 45 s sólo
  compensa los casos de proxy bajo TCG; por defecto, el fixture conserva
  10 s. Repetir en despliegue sin la variable. Ver detalle en
  [C07](../causas/C07-faltan-pruebas-integradas-t097.md).

La aceptación usa un kernel Linux en una VM TCG y sirve como evidencia de las
interfaces probadas del guest. No equivale a certificar otro kernel ni una
instalación CI/producción; éstos deben repetir el mismo gate.

## Alcance de seguridad observado

El worker dispone de un contrato sin rutas de escritura: Landlock deniega
escrituras en `/tmp`, aunque el mount sea tmpfs. El gate también comprobó red
denegada, SysV SHM confinado al IPC namespace, entorno limpio, ACK con FDs
exactos `(0,1,2)`, `/proc/self/fd` denegado, alias `/dev/fd/0` disponible y
accesos entre user namespaces denegados. Dos launches reales simultáneos
usaron proxies, secretos y respuestas distintos; el efecto incierto persistió
tras reabrir SQLite y no se reenvió.

Un actor host del mismo UID fuera de esos namespaces sí pudo hacer `ptrace` y
`pidfd_getfd` sobre el bootstrap antes de GO, pese a Yama=1. Decisión: CAO Work
corre con cuenta OS no root, nologin y exclusiva, sin procesos host ajenos; esa
cuenta se confía. Véase [C07](../causas/C07-faltan-pruebas-integradas-t097.md).

El timeout de configuración tiene un presupuesto separado de 20 s para el
runner TCG lento; el timeout de ejecución del worker continúa en 10 s. Hay
una prueba que verifica que ambos límites no se mezclan.

## Evidencia upstream

El `test-sandbox.py` de Bubblewrap pasó 68 subtests. El módulo Python opcional
de seccomp no estaba instalado. Un `test-run --file 0` upstream falló porque
la invocación no montó `/dev`; la composición probada sí usa `--dev /dev`.
No se presenta la suite upstream completa como verde.

## Pendiente antes de registrar

1. Provisionar un runner Linux sin credenciales de proveedores, con label
   `cao-t097-host`, cuenta broker dedicada y permisos `sudo -n -u` configurados
   según [S07](S07-pruebas-integradas-t097.md).
2. Ejecutar el workflow en `main`; debe terminar sin skips, sin override de
   timeout y con el digest de Bubblewrap aprobado para ese host.

Hasta completar esos puntos, no cambiar `WORK_BACKENDS={}` ni marcar T097/T019
como completadas.
