# Auditoría T097 — aislamiento Bubblewrap y proxy MCP (2026-09-28)

Auditoría de los problemas abiertos del gate T097. Cada causa tiene un
documento en `causas/` y su solución en `soluciones/`.

| # | Causa | Solución |
|---|---|---|
| 01 | [Socket tardío accesible](causas/C01-socket-tardio-accesible.md) | [S01](soluciones/S01-socket-tardio-accesible.md) |
| 02 | [Módulo de snapshot inexistente](causas/C02-modulo-snapshot-inexistente.md) | [S02](soluciones/S02-modulo-snapshot-inexistente.md) |
| 03 | [Proxy con socket con nombre](causas/C03-proxy-socket-con-nombre.md) | [S03](soluciones/S03-proxy-socketpair-por-fd.md) |
| 04 | [Prueba de aislamiento no ligada](causas/C04-prueba-aislamiento-no-ligada.md) | [S04](soluciones/S04-prueba-aislamiento-ligada.md) |
| 05 | [Efectos inciertos sin registro durable](causas/C05-emision-durable-sin-efectos-inciertos.md) | [S05](soluciones/S05-registro-durable-efectos-inciertos.md) |
| 06 | [2 fallos en la suite del proxy](causas/C06-tests-proxy-fallan.md) | [S06](soluciones/S06-tests-proxy-fallan.md) |
| 07 | [Faltan pruebas integradas T097](causas/C07-faltan-pruebas-integradas-t097.md) | [S07](soluciones/S07-pruebas-integradas-t097.md) |
| 08 | [Límites del entorno](causas/C08-limites-del-entorno.md) | [S08](soluciones/S08-limites-del-entorno.md) |

Orden recomendado: S02 → S01 → S03 → S04 → S05 → S06 → S08 → S07.

## Estado de las causas

| Causa | Estado | Evidencia / bloqueo restante |
|---|---|---|
| C01 | Cerrada | Test compuesto crea socket tardío en bind `/usr` vivo tras ACK; seccomp devuelve `EPERM`. |
| C02 | Cerrada | Módulo de snapshot existe y sus pruebas focales pasan. |
| C03 | Cerrada bajo cuenta broker dedicada | El preflight exige `CAO_WORK_BROKER_ACCOUNT`, no root, UID coincidente y shell `nologin`/`false` antes de probes. Socket anónimo por intento; concurrencia y aislamiento hermano pasan. |
| C04 | Cerrada | Proof revalida pidfds, starttime e inode PID namespace en cada guard. |
| C05 | Cerrada | Recuperación tras muerte confirmada, journal de issues huérfanos, conciliación admin y pruebas SIGKILL/migración. |
| C06 | Cerrada | Fallos originales del proxy corregidos; suite focal pasa. |
| C07 | Parcial | La suite local Ubuntu 26.10/QEMU TCG pasó 8/8. El primer run hosted verificó guest ABI 11 y fuente/build fijados; revisamos y allowlisteamos el digest resultante. Falta que la repetición hosted pase 8/8. |
| C08 | Verificada en guest Linux; repetición hosted pendiente | Ubuntu 26.10/QEMU TCG, kernel 7.3.0-5, Landlock ABI 11. Run hosted `36457607699` verificó source archive y toolchain; falló cerrado antes de los casos al encontrar un digest nuevo, ya revisado y allowlisted. Falta el run verde; no certifica el kernel del runner ni uno de despliegue. |

T097 y `WORK_BACKENDS = {}` siguen `[ ]` hasta completar el run QEMU hosted y
revisar por separado el host de despliegue. La suite guest usa timeout 45 s
porque TCG excede los 10 s de ejecución en dos pruebas. Ese perfil valida sólo
la imagen Ubuntu fijada; no certifica kernels arbitrarios. La política confía
en el proceso CAO de una cuenta OS dedicada, no root, sin login y sin otros
procesos; no afirma que Linux aísle procesos host arbitrarios con el mismo UID.

GitHub-hosted `ubuntu-24.04` sólo orquesta QEMU; para repositorios públicos,
GitHub describe esos runners estándar como VMs nuevas por job
([documentación](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)).
La suite registra versión de imagen y kernel en el log; cada host de despliegue
sigue sujeto a los checks de runtime del backend.

Hallazgos de línea base, reproducidos antes de las correcciones:
- `pytest test/services/test_work_mcp_proxy.py` → 2 failed, 5 passed.
- `pytest test/security/test_work_bubblewrap_runtime_snapshot.py` → error de
  colección (`ModuleNotFoundError`).
- `bwrap --version` → 0.11.1; `ptrace_scope` → 1; kernel WSL2 6.18.

Verificaciones focales de los cortes C05 y seguridad:
- migraciones: 34 passed; proxy: 15 passed.
- API/lifespan: 6 passed; reinicio/SIGKILL: 9 passed.
- limpieza de procesos: 35 passed; repositorio: 34 passed.
- socket tardío en bind vivo: 1 passed; proof/seccomp/ptrace sibling: 17 passed.
- admisión integrada: 30 passed; composición del gateway: 11 passed; gate del backend: 10 passed.
- composición Bubblewrap scratch: 38 passed; el harness no es un lanzamiento Work aceptado.
- aceptación integrada T097 en el guest: **8 passed, 0 skipped**; detalle en C07/S07/S08.
- `project-composition-check caos`: 4 contratos kept, 0 broken; `git diff --check` limpio.
