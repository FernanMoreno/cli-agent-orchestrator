# C08 — Límites del entorno de validación

**Severidad:** bloqueante para evidencia
**Estado:** cerrada por el run hosted [`36463930292`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36463930292),
con workflow global verde, suite 8/8 sin skips y Landlock ABI 11. Los runners
nativos `ubuntu-24.04` y `ubuntu-26.04` ofrecieron ABI 7 y ABI 8. No existe ni
se prevé un host de despliegue, por lo que el backend queda deshabilitado.

## Entorno original

El checkout de desarrollo corre en WSL2: kernel
`6.18.33.2-microsoft-standard-WSL2`, Landlock ABI 7 y Bubblewrap instalado
`0.11.1`. Ese host no sirve para aceptar el backend y debe seguir rechazando
la ruta productiva. El binario scratch anterior no tenía una procedencia
aprobada y la prueba ABI 9 quedaba sin ejecutar.

## Guest de aceptación reproducible — 2026-09-28

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

## Primeros runs GitHub-hosted con guest QEMU — digest revisado

El run [`36457607699`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36457607699),
en `main` (`8d93e018`), usó un runner GitHub-hosted `ubuntu-24.04` para
arrancar en QEMU TCG la imagen Ubuntu 26.10 del 2026-09-19, fijada por SHA-256
`6001247f681e87448e3f403b2e5ca859fb23a6cbbf0813f01a456a072b70a529`. El guest
arrancó con kernel `7.3.0-5-generic`, Landlock ABI **11**, y completó `uv sync`.
La descarga del release upstream Bubblewrap 0.13.0 coincidió con el SHA-256
fijado `4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765`;
Meson compiló el binario con GCC `15.3.0-4ubuntu1`, Meson `1.10.1` y las
opciones fijadas en el script. Se verificaron versión `0.13.0` e instalación
root:root 0755.

El digest resultante, `15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250`,
no figuraba en la allowlist del primer run. Revisados el source archive, el
perfil de compilación y su log, se añadió sólo ese digest exacto.

El run [`36460464186`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36460464186),
en el commit `48909237`, reutilizó el digest y terminó la suite con **8 passed,
0 skipped en 201,67 s** bajo el broker configurado. Landlock ABI fue 11. La
acción `setup-uv` falló después, durante el guardado de caché: el directorio
`/home/runner/work/_temp/setup-uv-cache` no existía en el host, porque el
proyecto y las dependencias se instalan dentro del guest. El commit `7135d91a`
desactivó ese cache host; la ejecución final verde consta en la sección de
resultado. El timeout TCG de 45 s sólo se aplica a los workers. Este perfil no
certifica el kernel del runner o de despliegue.

El inventario oficial de imágenes lista el runner estándar `ubuntu-24.04`
([imágenes runner](https://github.com/actions/runner-images#readme)). Para
repositorios públicos, GitHub-hosted usa una VM nueva por job
([referencia oficial](https://docs.github.com/en/actions/reference/runners/github-hosted-runners));
La versión exacta se registra en cada run porque la etiqueta recibe imágenes
actualizadas.

Esta VM es un perfil de referencia separado de cuentas de proveedores, no el
host de despliegue. Su aceptación no sustituye los preflight de runtime en
otros kernels. Si el digest compilado no está allowlisted, el run falla; se
debe revisar su procedencia antes de permitirlo y repetir la suite.

## Resultado

El run [`36463930292`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36463930292)
(`7135d91a`) terminó con workflow global verde. El guest Ubuntu 26.10 registró
kernel `7.3.0-5-generic`, Landlock ABI 11 y Bubblewrap 0.13.0 con digest
`15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250`; la suite
terminó **8 passed, 0 skipped en 288,11 s**. C07/C08 quedan cerradas para este
perfil guest reproducible. T097 se cierra sin habilitar despliegue porque no
existe ni se prevé un host de producción. `WORK_BACKENDS` permanece vacío;
T019 sigue abierto y diferido. Si se define un target futuro, requerirá una
tarea y aceptación específicas para ese host.
