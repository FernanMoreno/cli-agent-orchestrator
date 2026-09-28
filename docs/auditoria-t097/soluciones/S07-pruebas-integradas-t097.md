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

## Repetición GitHub-hosted en QEMU — historial del primer resultado

El workflow `.github/workflows/t097-host-acceptance.yml` usa
GitHub-hosted `ubuntu-24.04` para arrancar un guest Ubuntu 26.10 con QEMU TCG,
sólo sobre `main` o por dispatch de `main`, con permiso `contents: read`.
GitHub describe el runner estándar como una VM nueva por job para repositorios
públicos
([documentación](https://docs.github.com/en/actions/reference/runners/github-hosted-runners));
no reutiliza el runner `cao-real-e2e`, que maneja cuentas y credenciales de
proveedores.

El script `.github/scripts/t097-qemu-acceptance.sh` prepara un entorno efímero:

- Verifica la imagen cloud Ubuntu 26.10 del 2026-09-19 por SHA-256 y arranca
  el guest con QEMU TCG; el runner exterior sólo orquesta y transfiere el
  checkout.
- Verifica el source archive oficial de Bubblewrap 0.13.0 por SHA-256, compila
  dentro del guest e instala `/usr/bin/bwrap` como root:root 0755.
- Ajusta `kernel.yama.ptrace_scope=1` y, si existe, desactiva la restricción
  AppArmor de user namespaces sólo dentro de la VM efímera.
- Crea la cuenta indicada por la variable no secreta
  `CAO_WORK_BROKER_ACCOUNT` con shell `/usr/sbin/nologin`; no precisa una regla
  `sudoers` configurada por el operador.
- Instala el checkout y `.venv` dentro del guest bajo el usuario runner; el
  broker sólo necesita lectura/ejecución del checkout. Las pruebas escriben
  en temporales.

El workflow confirma Landlock ABI >= 9 dentro del guest, instala dependencias
bloqueadas y ejecuta la suite como broker con
`T097_TEST_WORKER_TIMEOUT_SECONDS=45` sólo para compensar TCG; además falla si
pytest reporta cualquier skip. La compilación imprime el digest. El primer run
con el archive completo detectó un hash nuevo; tras revisar origen y perfil, se
allowlisteó el valor exacto.

El run [`36460464186`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36460464186)
pasó `test/integration/t097 -m t097_host`: **8 passed, 0 skipped en 201,67 s**,
Landlock ABI 11 y Bubblewrap SHA-256
`15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250`. El job
falló después en el guardado de caché de `setup-uv`, porque el cache configurado
pertenece al host y `uv` corre dentro de QEMU. El commit `7135d91a` desactivó
esa caché host.

El run [`36463930292`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36463930292)
terminó con workflow global verde. Ejecutó esos ocho casos con Landlock ABI 11
y el mismo digest revisado: **8 passed, 0 skipped en 288,11 s**. Queda aceptada
la imagen guest fijada; los hosts de despliegue requieren su propio preflight.
Por eso `WORK_BACKENDS={}` sigue vacío y T097/T019 siguen sujetos al host
destino.
