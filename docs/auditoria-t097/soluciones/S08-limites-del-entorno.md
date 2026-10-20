# S08 — Evidencia reproducible y decisión de despliegue

Causa: [C08](../causas/C08-limites-del-entorno.md)

## Decisión

El guest temporal y el workflow hosted resolvieron C08 para el perfil Linux
reproducible. El run [`36463930292`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36463930292)
terminó globalmente verde con 8/8 casos y sin skips. Ninguna prueba equivale a
habilitar un despliegue. No existe ni se prevé un host de producción; el workflow
`.github/workflows/t097-host-acceptance.yml` usa GitHub-hosted `ubuntu-24.04`
para arrancar un guest Ubuntu 26.10 con QEMU TCG. Los kernels nativos
`ubuntu-24.04` y `ubuntu-26.04` ofrecieron ABI 7 y 8, inferiores al requisito 9.
El perfil guest está aceptado. `WORK_BACKENDS` permanece `{}` por la decisión
de no desplegar; el backend queda deshabilitado.

## Entorno de aceptación

Guest Ubuntu 26.10 en QEMU TCG (kernel Linux del guest; no kernel WSL2 ni host
de despliegue):

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

## Build en `ubuntu-24.04`, ABI insuficiente

El run `36447165686` en la imagen `ubuntu-24.04` `20260920.314.1`
(`24.04.5`) verificó el source archive fijado
`4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765`, compiló
Bubblewrap `0.13.0` como release con GCC `13.3.0` y Meson `1.3.2`, y comprobó
`/usr/bin/bwrap` como `root:root 0755`. El digest medido fue
`a5882b87c0b8105a5d9e81db5f64a4f8d373affc36f531e5df7409db5b1af8f6`.

El segundo run falló cerrado antes de ejecutar casos porque ese digest aún no
estaba allowlisted. Tras revisar las comprobaciones de origen y compilación, se
añadió el valor exacto. Un run posterior superó ese check, pero confirmó que el
kernel ofrece Landlock ABI 7; el workflow lo rechaza porque exige ABI >= 9. El
artifact y el preflight de build no se cuentan como aceptación T097.

## Build en guest Ubuntu 26.10 hosted, digest revisado

El run [`36457607699`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36457607699)
corrió en `main` con la imagen guest del 2026-09-19 fijada por SHA-256
`6001247f681e87448e3f403b2e5ca859fb23a6cbbf0813f01a456a072b70a529`. Registró
kernel `7.3.0-5-generic`, Landlock ABI **11** y `uv sync` completo. El SHA-256
del source archive oficial coincidió con
`4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765`. Se compiló
con GCC `15.3.0-4ubuntu1`, Meson `1.10.1` y las opciones del script; la versión
fue `0.13.0` y el binario quedó como `root:root 0755`.

El binario produjo SHA-256
`15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250`. Los ocho
fixtures rechazaron ese hash en el primer run, antes de correr los cuerpos de
prueba. Tras revisar la descarga upstream, la configuración y el log de
compilación, se añadió ese valor exacto a la allowlist.

El run [`36460464186`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36460464186),
commit `48909237`, confirmó Landlock ABI **11** y terminó con **8 passed, 0
skipped en 201,67 s**. La suite pasó como broker con el digest aprobado. El
workflow falló después, en `Post Install uv`, porque `setup-uv` intentó guardar
`/home/runner/work/_temp/setup-uv-cache`, una ruta que no existe en el host:
uv corre dentro del guest QEMU. El commit `7135d91a` desactivó la caché host y
el run global verde posterior se documenta abajo. El run `36460464186` demostró
los ocho casos, pero T101 requería además un run global verde.

La aceptación usa un kernel Linux en una VM TCG y sirve como evidencia de las
interfaces probadas del guest. No equivale a certificar otro kernel. El nuevo
perfil hosted sirve como referencia reproducible; cada host de despliegue
sigue sujeto a los gates del preflight.

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

## Aceptación final del perfil guest — 2026-09-28

El run [`36463930292`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36463930292)
se ejecutó desde `main` tras desactivar el cache host de `setup-uv`. El workflow
completo terminó verde. Dentro del guest Ubuntu 26.10 fijado verificó Landlock
ABI 11, Bubblewrap 0.13.0 y el digest aprobado
`15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250`; los ocho
casos pasaron sin skips en 288,11 s. T101 y C07/C08 quedan cerradas para ese
perfil guest.

El run QEMU no certifica un host de despliegue ni habilita el backend. T097 se
cierra para el alcance actual porque no existe ni se prevé un host de
producción; `WORK_BACKENDS={}` permanece vacío y T019 sigue abierto y diferido.
Si se decide desplegar Work más adelante, hará falta una tarea nueva con un
target concreto y aceptación en ese host.


## Aceptación local gratuita de las omisiones — 2026-09-30

QEMU/KVM dentro de `caos-acceptance-qemu:local`, guest Ubuntu 26.10 fijado por
el checksum anterior, kernel 7.3.0-5-generic y Landlock ABI 11. El source
Bubblewrap 0.13.0 se verificó contra el hash oficial anterior y se compiló con
GCC 15.3.0-4ubuntu1/Meson 1.10.1, usando las mismas opciones release del runner.
El path del build fue `/tmp/caos-exec/T097/bubblewrap-build` y el source se
extrajo en `/tmp/bwrap-source/bubblewrap-0.13.0`; los paths de build afectan
al digest. El hash exacto revisado fue
`efd07a9dfd55016ec5cb16facb67aac2735fa900e261f6a44be1517dcf31c015`.
El executable canónico quedó root:root 0755 y la copia scratch también 0755.

La suite T097 completa pasó como `caos-work-broker` (UID 999, nologin):
**8 passed en 22,86 s**, sin override del timeout normal de 10 s, sin skips.
Las pruebas de seguridad adicionales mantuvieron controles negativos reales
de ficheros, red, FD, procesos y ejecución. Los tests que requieren UID 1000
se ejecutaron como runner; mknod y FS casefold dentro del guest aislado.
Reportes/hash por nodeid: `specs/001-verifiable-orchestration/omitted-acceptance.json`.
No cambia la aceptación de un host de producción ni registra WORK_BACKENDS.

## Reproducción de perfil para el digest de build — 2026-10-12

Una reproducción fuera del guest T097 construyó dos veces Bubblewrap upstream
`0.13.0` desde el mismo archivo fuente fijado
(`4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765`) en una
misma ejecución OCI aislada. La imagen usada fue Ubuntu 26.10 amd64 con digest
`sha256:b18403eb2d06baad26fe088450af83744e456d0e7c46d8319d460ce3a39e1f16`, y
el build se ejecutó con 2 CPU y límite de memoria de 3 GiB. Las dos compilaciones
usaron las rutas canónicas `/tmp/bwrap-source/bubblewrap-0.13.0` y
`/tmp/caos-exec/T097/bubblewrap-build`, además de las opciones release del
runner: `--prefix=/usr --buildtype=release -Dtests=false -Dman=disabled
-Dselinux=disabled -Dbash_completion=disabled -Dzsh_completion=disabled`.

El perfil registró GCC `15.3.0-4ubuntu1`, Meson `1.10.1-1ubuntu2`, Ninja
`1.13.2-1`, binutils/ld.bfd `2.47-6ubuntu1`, `libcap-dev` `1:2.78-1ubuntu1`,
libcap `2.78` y libc6 `2.44-1ubuntu2`. Las dos salidas tenían SHA-256
`18a513db177974d371d2c1fbf9d6f8f5b2bcb43565e242e5a7b4bdc4886e4c39`; ambos
ELF eran PIE con `libcap.so.2` y `libc.so.6` como dependencias dinámicas y
`BIND_NOW` habilitado. El resultado es una reproducción de digest con
procedencia registrada, no una ejecución de los casos T097 en el guest.

El digest `18a513db177974d371d2c1fbf9d6f8f5b2bcb43565e242e5a7b4bdc4886e4c39`
se añade como perfil estático explícito. No se atribuye la diferencia frente a
los digests históricos a un único factor: las imágenes/paquetes y algunas rutas
del build difieren, y no se dispone del binario antiguo para una comparación
controlada. Los digests no revisados siguen rechazándose. La reproducción no
certifica aceptación T097, no certifica otro host y no activa
`WORK_BACKENDS`.

El registro privado de la reproducción se conserva fuera del repositorio en
`/home/felni/.cache/caos-spec021-evidence-20261012/runtime/bwrap-repro-v4/reproduce.stdout.log`
(SHA-256 `c147ab4086d51158eb9da3c8d1c4c4c3679d765b1e84081dcf7456afa88c0a83`);
el script que la ejecutó tiene SHA-256
`86ae080f9fa5d3fb95de8f8d82f5f5f83740146af7ca39bde31933b5328100d`.

## Partición adicional del guest — pendiente de ejecución

La siguiente ejecución T097 añade una partición explícita de tres casos: la
recuperación de un monitor muerto y los dos bloqueos de vault con rutas que
difieren solo en mayúsculas/minúsculas. Sus node IDs están fijados en
`test/integration/t097/extra-filesystem-selectors.txt`. El runner crea una
imagen ext4 temporal con `casefold`, la monta dentro del guest desechable,
activa el atributo en un directorio vacío y comprueba la resolución de ambos
alias antes de ejecutar pytest con un `--basetemp` descendiente. La imagen y el
mount se eliminan al terminar. Esta preparación no cambia el filesystem del
host y no cuenta skips como éxitos. La partición está pendiente de una ejecución
real en el guest; no se presenta como resultado pasado.
