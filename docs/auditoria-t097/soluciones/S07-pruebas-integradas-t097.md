# S07 — Suite integrada de aceptación T097

Causa: [C07](../causas/C07-faltan-pruebas-integradas-t097.md)

## Resultado host — 2026-09-28

La suite `pytest.mark.t097_host` cruza el preflight, gateway durable,
Bubblewrap 0.13.0, bootstrap Landlock/seccomp, supervisor y proxy. El runner
QEMU Ubuntu 26.10, kernel `7.3.0-5-generic`, Landlock ABI 11 terminó **8
passed, 0 skipped en 327,65 s** bajo el UID broker dedicado, con el nuevo
preflight de identidad activado.

Ejecución en el guest:

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

El valor `45` permite terminar los dos casos de proxy bajo emulación TCG: las
revalidaciones completas del proof tardan hasta 17 s allí. El fixture usa 10 s
si la variable no está definida; así debe repetirse en el runner de despliegue.

| Módulo | Casos |
|---|---|
| `test_host_acceptance.py` | launch durable, exec descendiente, FS/red/SysV IPC/FD/proc, revocación pre-GO y ACK perdido |
| `test_sibling_isolation.py` | actor del mismo UID host dentro de otro user namespace; FD, proc, ptrace y pidfd denegados |
| `test_proxy_concurrency.py` | dos launches y endpoints MCP reales a la vez; efectos inciertos persisten tras reabrir SQLite y no se repiten |

El fixture requiere Linux, Bubblewrap 0.13.0 con digest aprobado, Landlock ABI
≥9, Yama `ptrace_scope=1`, la política AppArmor configurada para userns, y
`CAO_WORK_BROKER_ACCOUNT` igual al euid actual. La cuenta debe ser no root,
tener shell `nologin`/`false` y no tener otros procesos host al iniciar la
aceptación.

## Repetición de despliegue pendiente

El guest QEMU no es el runner de despliegue. El workflow
`.github/workflows/t097-host-acceptance.yml` ejecuta este gate en el runner
self-hosted Linux dedicado con label `cao-t097-host`, sólo sobre `main` o por
dispatch de `main`. No reutiliza el runner `cao-real-e2e`, que maneja cuentas y
credenciales de proveedores.

Provisionamiento requerido en ese host:

- Crear `caos-work-broker` como cuenta OS no root, shell `nologin`/`false`, sin
  procesos ajenos; ejecutar CAO con `CAO_WORK_BROKER_ACCOUNT=caos-work-broker`.
- La variable no secreta de GitHub Actions
  `CAO_WORK_BROKER_ACCOUNT=caos-work-broker` ya está configurada en el
  repositorio. Crear esa misma cuenta en el host y permitir al usuario del
  runner `sudo -n -u caos-work-broker` sin contraseña.
- Instalar el Bubblewrap 0.13.0 aprobado, Landlock ABI ≥9, Yama
  `ptrace_scope=1`, política AppArmor compatible con userns, compilador `cc`,
  `readelf` y namespaces requeridos por la suite.
- Hacer que el work directory del runner y sus padres sean atravesables y que
  el checkout/`.venv` sean legibles y ejecutables por el broker. El broker no
  necesita permisos de escritura en el checkout; las pruebas escriben en tmp.

El workflow valida la cuenta, instala dependencias bloqueadas y ejecuta la suite
como el broker sin `T097_TEST_WORKER_TIMEOUT_SECONDS`. Cualquier fallo, digest
distinto, prerrequisito ausente o skip deja abierto el gate. Hasta un run verde
en ese runner, `WORK_BACKENDS={}` y T097/T019 permanecen abiertos.
