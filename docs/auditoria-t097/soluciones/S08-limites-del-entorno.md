# S08 — Evidencia reproducible y gate de host

Causa: [C08](../causas/C08-limites-del-entorno.md)

## Decisión

El guest temporal resolvió los requisitos técnicos de C08 y ejecutó la suite
8/8 sin skips. El run hosted también pasó los ocho casos; el workflow quedó
rojo sólo después, al intentar guardar una caché de host vacía. Se desactiva
esa caché y se repite para obtener un estado global verde. Ninguna prueba
equivale a un host de despliegue. El workflow
`.github/workflows/t097-host-acceptance.yml` usa GitHub-hosted `ubuntu-24.04`
para arrancar un guest Ubuntu 26.10 con QEMU TCG. Los kernels nativos
`ubuntu-24.04` y `ubuntu-26.04` ofrecieron ABI 7 y 8, inferiores al requisito 9.
Falta un run QEMU verde; `WORK_BACKENDS` permanece `{}`.

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
uv corre dentro del guest QEMU. Se desactiva la caché host para esta acción y
se repetirá el workflow. El run demuestra los ocho casos, pero T101 requiere
además un run global verde.

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

## Repetición pendiente para cerrar C07/C08 del perfil guest

1. Repetir el workflow desde `main` con la caché host de `uv` desactivada;
   exigir Landlock ABI >= 9, digest aprobado, ocho casos sin skips y timeout
   TCG documentado.
2. Con run global verde, cerrar C07/C08 para el perfil guest en las
   especificaciones y esta auditoría. La prueba no certifica hosts de
   despliegue ni cierra los gates propios de T019/T035.

El run QEMU no certifica un host de despliegue ni habilita backend. Incluso
tras cerrar C07/C08 para el perfil guest, mantener `WORK_BACKENDS={}` y dejar
T097/T019 sujetos a aceptar el host de destino antes de habilitar ejecución
Work.
