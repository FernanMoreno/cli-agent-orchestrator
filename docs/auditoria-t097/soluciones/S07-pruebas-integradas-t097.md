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
si la variable no está definida; el workflow hosted la deja sin definir.

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

## Repetición GitHub-hosted pendiente

El guest QEMU no sustituye una VM Linux hospedada. El workflow
`.github/workflows/t097-host-acceptance.yml` ejecuta este gate en
`ubuntu-26.04`, sólo sobre `main` o por dispatch de `main`, con permiso
`contents: read`. GitHub describe el runner estándar como una VM nueva por job
para repositorios públicos
([documentación](https://docs.github.com/en/actions/reference/runners/github-hosted-runners));
no reutiliza el runner `cao-real-e2e`, que maneja cuentas y credenciales de
proveedores.

El workflow prepara el entorno efímero en cada job:

- Verifica el source archive oficial de Bubblewrap 0.13.0 por SHA-256, compila
  como usuario `runner` e instala `/usr/bin/bwrap` como root:root 0755.
- Ajusta `kernel.yama.ptrace_scope=1` y, si existe, desactiva la restricción
  AppArmor de user namespaces sólo dentro de la VM efímera.
- Crea la cuenta indicada por la variable no secreta
  `CAO_WORK_BROKER_ACCOUNT` con shell `/usr/sbin/nologin`; no precisa una regla
  `sudoers` configurada por el operador.
- Instala el checkout y `.venv` bajo el usuario runner; el broker sólo necesita
  lectura/ejecución del checkout. Las pruebas escriben en temporales.

El workflow confirma Landlock ABI >= 9, instala dependencias bloqueadas y
ejecuta la suite como broker, sin `T097_TEST_WORKER_TIMEOUT_SECONDS`; además
falla si pytest reporta cualquier skip. La compilación imprime el digest. Si
aún no está allowlisted, el primer run falla de forma esperada; tras revisar el
source archive y la configuración fijados, se añade el digest aprobado y se
repite la suite completa hasta obtener 0 skips. Este
resultado acepta el perfil `ubuntu-26.04` registrado en el log; otros hosts
deben pasar sus preflight. Hasta el run verde, `WORK_BACKENDS={}` y T097/T019
permanecen abiertos.
