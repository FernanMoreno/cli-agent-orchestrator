# C08 — Límites del entorno de validación

**Severidad:** bloqueante para evidencia
**Estado:** entorno de aceptación verificado en guest Linux QEMU; la suite T097
pasó 8/8 sin skips. Falta repetir en el runner Linux de despliegue.

## Entorno original

El checkout de desarrollo corre en WSL2: kernel
`6.18.33.2-microsoft-standard-WSL2`, Landlock ABI 7 y Bubblewrap instalado
`0.11.1`. Ese host no sirve para aceptar el backend y debe seguir rechazando
la ruta productiva. El binario scratch anterior no tenía una procedencia
aprobada y la prueba ABI 9 quedaba sin ejecutar.

## Runner de aceptación reproducible — 2026-09-28

Se ejecutó la aceptación dentro de un guest Ubuntu 26.10 en QEMU TCG. Es un
kernel Linux completo del guest, no el kernel WSL2; no es una máquina física
ni un runner CI externo.

- Kernel: `7.3.0-5-generic`; Landlock ABI **11**; user/PID/net/IPC namespaces
  disponibles; Yama `ptrace_scope=1`; política
  `apparmor_restrict_unprivileged_userns=0`.
- Fuente Bubblewrap: release upstream [`v0.13.0`](https://github.com/containers/bubblewrap/releases/tag/v0.13.0),
  commit `719a4fd`; SHA-256 del `bubblewrap-0.13.0.tar.xz`:
  `4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765`.
- Binario instalado en `/usr/bin/bwrap`: versión `0.13.0`, root:root 0755,
  SHA-256 `f41ba3f7be0280df0afe201f0e2eeb16a17e969782491e830e6753c67f78d70d`.
  Ese digest está allowlisted en el backend y se comprueba antes de ejecutar
  el preflight. La source archive SHA coincide con el hash publicado en el
  release upstream.
- La suite `test/integration/t097/` corrió con
  `T097_REQUIRE_HOST_ACCEPTANCE=1`: tras el nuevo enforcement del broker,
  **8 passed, 0 skipped en 327,65 s** como `caos-work-broker`, UID 999 y shell
  `nologin`. Se configuró `CAO_WORK_BROKER_ACCOUNT=caos-work-broker`; la
  fixture exige ese euid y comprueba que no haya otro proceso host bajo la
  cuenta.
- Los casos de proxy usaron
  `T097_TEST_WORKER_TIMEOUT_SECONDS=45` porque las revalidaciones de proof
  tardan 8–9 s bajo QEMU TCG. El valor predeterminado del fixture es 10 s; la
  aceptación de despliegue debe ejecutarse sin el override.
- El comando y la cobertura constan en
  [S07](../soluciones/S07-pruebas-integradas-t097.md); la suite de guest no es
  el runner de despliegue.
- El upstream Meson test run fue parcial: `test-sandbox.py` pasó 68 subtests;
  el test optional de seccomp informó que faltaba el módulo Python opcional;
  un `test-run --file 0` upstream falló porque su invocación no montó `/dev`,
  mientras que el argv de producción incluye `--dev /dev`. No se afirma que la
  suite upstream completa haya pasado.

## Interpretación y límite de confianza

La aceptación prueba hermanos dentro de namespaces distintos y dos proxies
reales en paralelo. Un probe anterior desde proceso host ordinario del mismo
UID, fuera de esos namespaces, sí pudo hacer `ptrace`/`pidfd_getfd` pre-GO.
La decisión de diseño es ejecutar CAO Work bajo una cuenta OS dedicada, no
root, sin login y sin procesos host ajenos; esa cuenta forma parte de la base
de confianza. No se afirma aislamiento frente a root ni frente a procesos que
ejecuten bajo la propia cuenta broker.

## Resultado

Los requisitos técnicos del guest se probaron sin skips. Esto resuelve el
bloqueo de ABI/Bubblewrap para esa ejecución y cubre el proxy concurrente y su
recuperación durable. No aprueba el runner de despliegue ni autoriza registrar
el backend. El workflow dedicado está definido en
`.github/workflows/t097-host-acceptance.yml`; requiere un runner provisionado
con label `cao-t097-host`, cuenta broker y configuración sudo de sólo ejecución
como esa cuenta. `WORK_BACKENDS` permanece vacío y T097/T019 continúan `[ ]`.
