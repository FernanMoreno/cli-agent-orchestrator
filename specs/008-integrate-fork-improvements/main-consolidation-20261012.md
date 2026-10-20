# Consolidación en main — 2026-10-12

## Alcance y estado

### Estado actual — 2026-10-14

Los cambios funcionales reunidos están en el checkout principal de `main`;
permanecen locales y sin confirmar/publicar. Los originales externos se conservan.
La ejecución global R13 terminó con **17.211 PASS, 9 FAIL, 113 SKIP, 0 ERROR**
y cobertura real **86,733308 %**, inferior al 87 % requerido. El manifiesto
`b4e9402e56d5ab2b58dfcf39f50cd8b7c0054334bfb6e5ed7032599015295c49`
fue comprobado antes y después: cero diferencias de sus 3.134 entradas frente
a `main`. Los cambios posteriores de T061–T063 corrigen la contención del
dispatcher, la selección de esquema del helper de continuaciones y contratos
sin pruebas; aún requieren aceptación sobre su fuente nueva. R13 no constituye
un cierre verde de Spec016/021. La imagen controller actual pasó el comando
de construcción, pero sus tres gates Docker siguen pendientes; el wrapper
conserva su fallo de comprobación por cambios de estado de contenedores externos.
No se activó la instalación del operador ni se modificaron sus alias.

Los apartados siguientes preservan la cronología de R10/R11/R12 y las pruebas
focalizadas; sus resultados no sustituyen la aceptación final de la fuente actual.

Petición del usuario: reunir los cambios en `main` y evitar que funcionalidades
queden exclusivamente en otros worktrees. La consolidación del delta del
worktree T097 está mapeada al código vigente. T079 tiene una aceptación nativa
focalizada final: 20/20 fuentes registradas y 100/100 preguntas visibles con
p95 de registro confirmado a metadata de 789,4 ms. La repetición instrumentada
SIGKILL descubrió un deadlock PostgreSQL; el orden de locks se corrigió y pasó
regresiones nativas. La aceptación canónica Guest80 ya terminó con 80 PASS y
cero omisiones en ocho particiones. El suplemento T098 se ejecutó por separado
y obtuvo 4 PASS, 0 SKIP en 312,02 s. La suite global R12 aún no ha comenzado.

### Actualización de estado — ejecución R10 y fuente actual

La suite global R10 se interrumpió al 86% después de escrituras confirmadas
bajo el home real del operador. El recuento corregido es **20 fallos de fase
call y 5 errores de setup**; no hay resumen final de pytest, JUnit ni cobertura
JSON/XML. La corrección del recibo confirma que ninguno de esos 25 registros
fue causado por la interrupción. `runner-exit-full.json` informa exit 0, pero
no acredita una suite completada. El manifiesto de ese intento es
`6c7951fb9efce50d24c56b5cd1f219eb48565ca4e69a58970f64919be7d33657`; los
recibos están en
`/home/felni/.cache/caos-spec021-evidence-20261012/backend-r10/artifacts/`.

La suite global R11 terminó con exit 1: **16.984 PASS, 119 FAIL, 27 ERROR y
115 SKIP** de 17.245 pruebas. La cobertura fue **85,2058 %**, inferior al
objetivo de 87 %. El recibo, JUnit y cobertura están en
`/home/felni/.cache/caos-spec021-evidence-20261012/backend-r11/artifacts/`;
el manifiesto sellado tiene SHA-256
`36a2766bcaa8dd4cfeda7d1c660a29a7a682c161312819128ac693c3b33e263e`.
R11 es una ejecución completa con fallos, no una aceptación global verde. R10
queda como antecedente interrumpido y supersedido por el resultado completo de
R11.

La fuente actual contiene cambios posteriores a ese snapshot y a la imagen
Docker 72ac: `src/cli_agent_orchestrator/api/main.py` tiene SHA-256
`eba6f8ead63ce079f37206cb795999e8f9f2352e619823417d0f1ac741f79440`,
`src/cli_agent_orchestrator/clients/work_repository.py` tiene
`3f92c82ea6d33817aeb7aaa36617815d01defb268a38556336cb702da733e0c4`, y
`src/cli_agent_orchestrator/services/knowledge_policy.py` tiene
`ab400ecccb542f56449607ac9d6999b90be65ff9250a8cb4374bd37ad4d8e5ca`.
La imagen 72ac y sus aceptaciones de instalación/importación siguen siendo
resultados históricos de su propio snapshot; no verifican esta fuente actual.
T054, la lectura agrupada del catálogo PostgreSQL, ya está completo en su
alcance acotado. `src/cli_agent_orchestrator/clients/postgres/migrations.py`
tiene SHA-256
`cda12c08da6df2a16d42941966b181253ffd4dda22d7ed623b13828ed1dcdb49`; el módulo
de catálogo pasó **26 pruebas** y la regresión de dispatch pasó **1 prueba**.
La evidencia está bajo
`/home/felni/.cache/caos-spec021-evidence-20261012/runtime/catalog-final-r2/`
y `runtime/catalog-dispatch-r4/`.

El snapshot R11 quedó sellado con manifiesto
`36a2766bcaa8dd4cfeda7d1c660a29a7a682c161312819128ac693c3b33e263e` (**3125
entradas**). La imagen controller
`sha256:b827abf5d65b9f9c97cece18c38c7da3b096fc56087bbf7924e1658c96767e15`
pasó sus tres gates Docker reales. La aceptación global R11 terminó con los
fallos indicados arriba. Guest80/T074 y el suplemento T098 se completaron en
ejecuciones separadas, descritas abajo.

Después de R10 pasaron pruebas focalizadas de fixture, no la suite global:
Bubblewrap 40, API 46, launch y step 38, revisión de memoria 15, admisión 20,
política de proyectos 14 y transporte de interacciones 14. La prueba de
política de proyectos pasó con un home privado y una guarda que impide
`systemctl --user`; su log está en
`/home/felni/.cache/caos-spec021-evidence-20261012/interaction-transport-policy/green-final-jrng6mhg/green.log`.
Estos resultados no sustituyen la aceptación global del backend. T074/Guest80
y el suplemento T098 de provisioning se completaron en ejecuciones posteriores
y separadas.

Tras R11, los grupos focalizados de reparación también pasaron sin convertirse
en aceptación global: los cinco módulos de transporte, gateway, proyección y
T098 obtuvieron **24 PASS y 4 SKIP**; en esa ejecución, los skips requerían el
binario Bubblewrap scratch. El suplemento T098 tuvo una ejecución de provisioning
separada, con **4 PASS y 0 SKIP**. El grupo Audit obtuvo **23 PASS** y Wiki obtuvo
**73 PASS**. Los logs están en
`/home/felni/.cache/caos-spec021-evidence-20261012/interaction-transport-r11/green-capture/artifacts/stdout.log`,
`/home/felni/.cache/caos-spec021-evidence-20261012/audit/work-agent-step-r11/owned-modules-green.log`
y
`/home/felni/.cache/caos-spec021-evidence-20261012/audit/wiki-lint/wiki-lint-file-green.log`.
Estos resultados acotados no sustituyen R12, que todavía no ha comenzado.

La tanda 114 anterior detectó cuatro fallos en dos módulos nativos: placeholders
SQLite en la consulta de cola y una fixture que no preparaba el store antes de
inyectar el fallo. Tras corregir la adaptación de prueba, los dos módulos
completos obtuvieron **7 PASS**. RED:
`/home/felni/.cache/caos-spec021-evidence-20261012/interaction-transport-fix-2964d92690/artifacts/red.log`;
GREEN:
`/home/felni/.cache/caos-spec021-evidence-20261012/interaction-transport-green-f6046ae2c8/artifacts/green.log`.
Este resultado focalizado no cambia los conteos de R11.

La aceptación huésped canónica Guest80/T074 obtuvo **80 PASS, 0 SKIP** en ocho
particiones. El registro es
`/home/felni/.cache/caos-spec021-evidence-20261012/runtime/t097-guest80-r11/runs/attempt-guest80-3/attempt-20261012T185807Z.q8e1kD.log`.
Las particiones fueron host (**8 PASS**), SIGKILL restaurado (**2 PASS**), robo
de FD tras `exec` (**1 PASS**), scratch Bubblewrap (**62 PASS**), caracterización
de sibling UID 1000 (**1 PASS**), socket pathname con Landlock ABI 9 (**1 PASS**),
nodos de dispositivo (**2 PASS**) y recuperación/casefold (**3 PASS**). Esta
aceptación focalizada no cambia los conteos ni la cobertura de R11.

El suplemento T098 de provisioning se ejecutó por separado y obtuvo **4 PASS,
0 SKIP** en **312,02 s**. El log es
`/home/felni/.cache/caos-spec021-evidence-20261012/runtime/t097-guest80-r11/t098-supplement-r1/runs/attempt-20261012T195632Z.RBBVcL.log`.
Estos cuatro resultados son adicionales a Guest80 y no se combinan para
renombrarlo ni contabilizarlo como una aceptación canónica de 84 pruebas.

En la validación frontend R11, el lote de cuatro archivos obtuvo **34 PASS**
(`/home/felni/.cache/caos-spec021-evidence-20261012/frontend/r11-owned-full-files/logs/green6.log`).
Una ejecución separada volvió a comprobar la aserción `raw.closed`: **1 PASS**
en 5,62 s (`/home/felni/.cache/caos-spec021-evidence-20261012/frontend/r11-memory-assert/logs/green7.log`).
Es una repetición de una aserción reforzada, no una prueba distinta número 35.

El benchmark PostgreSQL 18 de conteo de constraints/fast path para memoria,
ejecutado con la fuente actual en aislamiento, midió entre **1,595 y 1,682 s**
y no satisface el NFR; el perfil de Audit registró 1,682 s. El valor de **1,176
s** pertenece a un prototipo privado de reutilización medido bajo carga Guest,
no a una aceptación de la fuente actual. Otros tres prototipos privados dieron
resultados verdes de 965 ms, 993 ms y 881 ms, pero tampoco acreditan la fuente
del producto. La propuesta T055 sigue pendiente de evaluación. Esto no modifica
la aceptación acotada de Guest80 ni los resultados globales R11.

El frontend final, T019/T020 y Browser-Docker tienen resultados verdes para sus
respectivos snapshots. Guest80 y el suplemento T098 están completos; R12 sigue
pendiente. Los cambios de aplicación siguen locales y sin confirmar;
los alias de despliegue no cambiaron y el proceso personal del operador no se
activó. Este documento no certifica el cierre total de Spec016/021.

El checkout principal es `/mnt/c/users/ferna/onedrive/escritorio/caos`, rama
`main`, HEAD `02309f3f1e8cd1cf3b66dc92ca4d9e6c21ccf2db`. Hay cambios locales de
la migración PostgreSQL y otras mejoras que deben conservarse. Estar en `main`
no significa que esos cambios sin confirmar formen ya parte de su HEAD.

## Carpetas indicadas por el usuario

| Carpeta del Escritorio | Contenido comprobado | Tratamiento |
| --- | --- | --- |
| `caos-publish-verification-87e2d5f3` | Cinco logs; no repositorio Git ni código de aplicación | Evidencia histórica de publicación/verificación. `87e2d5f354374ea8acc685275e749992df51cdbd` es antecesor de `main` (comprobación Git, exit 0). No importar el log de entorno como código. |
| `cao-collaboration-demo-20261001-cf8017b2` | API stdlib, frontend, contrato, pruebas, documentación y captura; sin `.git` | Incorporar en `examples/agent-collaboration/tasks/` con datos iniciales vacíos y procedencia. |
| `cao-turn-recovery-demo-20261002-f9086a25` | API stdlib de notas, frontend, pruebas, contrato, auditorías y capturas; sin `.git` | Incorporar en `examples/agent-collaboration/turn-recovery/`, diferenciando auditorías históricas de código vigente. |

Los originales externos se conservan intactos. `tasks.json` y `notes.json` son
estado mutable de las demos; sus datos existentes no se trasladan al repositorio.
Los ejemplos contienen seeds vacíos. Se excluyen bytecode, cachés y entornos.

## Worktrees

- `codex/015-final-recovery`, en `/home/felni/cao015-final/candidate`, está limpio
  y su HEAD es el mismo `02309f3f` que `main`; no hay un delta local exclusivo.
- `codex/015-final-integration` referencia el commit `87e2d5f3`, ya incluido en
  `main`. El registro duplicado de la misma ruta no constituye una segunda
  implementación pendiente.
- `codex/t097-remediation` es antecesor de `main` (exit 0), pero su directorio
  `.worktrees/t097-remediation` tiene modificaciones sin confirmar. Se ha
  inventariado por separado: **221 entradas**, de las que **58** coinciden
  byte a byte con el checkout principal, **144** son blobs históricos que ya
  aparecieron en la historia de `main`, y **19** no aparecen en la historia de
  sus rutas actuales. Las diferencias no son 163 funcionalidades ausentes.
  Se compararon los 19 deltas con el código y pruebas actuales, registrando
  equivalencias y cambios de frontera en el manifiesto privado
  `consolidation/t097-novel-path-equivalence.json`. La revisión actual confirma
  que sus 19 destinos existen, que las 12 filas de escenario apuntan a pruebas
  o evidencia presentes y que los hashes de las pruebas restauradas coinciden
  con el manifiesto. No se copiaron los antiguos motores SQLite de Work: esas
  rutas siguen siendo validación offline o importación explícita. Se portaron
  las pruebas de ciclo de vida del dispatcher y se cerró el único hueco de API
  con `test/api/test_work_proxy_effects.py`.
- Los registros prunable de worktrees cuyos directorios ya no existen son
  metadatos históricos. No se borran como sustituto de verificar la cobertura
  del código.

Los manifiestos y patches de comparación se guardan con permisos privados en
`/home/felni/.cache/caos-spec021-evidence-20261012/consolidation/`.

## Ejemplos incorporados y comprobados

Las dos demos ya están en el checkout de `main`, con 31 archivos importados y
seis archivos de índice/seeds/ignores añadidos. El manifiesto público
`examples/agent-collaboration/IMPORT_MANIFEST.json` documenta los hashes y las
transformaciones de documentación. Una comprobación independiente de los 31
orígenes y destinos obtuvo **cero diferencias respecto a sus hashes declarados**.
Los datos mutables originales no se leyeron ni copiaron.

Resultados ejecutados desde las rutas incorporadas:

- API de tareas: **11 pruebas**, exit 0, Python 3.12.13.
- Frontend de tareas: **13 pruebas**, cero fallos/omisiones, exit 0, Node 24.19.0.
- API de notas: **13 pruebas**, exit 0, Python 3.12.13.
- Frontend de notas: `node --check`, exit 0; no acredita comportamiento en navegador.

Logs privados:
`/home/felni/.cache/caos-spec021-evidence-20261012/demos/`.
Las instrucciones de los README usan `python3` y `node` y no dependen de las
rutas privadas del entorno de validación.

## Worktree T097: equivalencia, regresiones y ejecución

El inventario de los 19 blobs no históricos se conserva en el manifiesto
privado indicado arriba. Sus 12 escenarios se contrastaron individualmente con
las pruebas actuales: allowlist de herramientas, identidad del ejecutable,
adopción y limpieza del worker, vigencia de la prueba de aislamiento, límite
Yama, reenganche del supervisor, rechazo previo a efectos, y recuperación del
proxy antes y después de un efecto externo. Las rutas de migración SQLite se
clasificaron como cobertura histórica offline/importación; no se presentan como
30 pruebas equivalentes de migración PostgreSQL.

Las pruebas restauradas y el hueco de API tienen esta evidencia focalizada:

| Ruta | Resultado |
| --- | --- |
| `test/api/test_work_dispatcher_lifespan.py` | 2 pasaron con PostgreSQL privado |
| `test/api/test_work_proxy_effects.py` | 2 pasaron con PostgreSQL privado |
| `test/backends/test_work_enforcement_t097_restored.py` | 2 pasaron offline |
| `test/security/test_work_bubblewrap_t097_restored.py` | 1 pasó offline |
| `test/services/test_work_process_t097_restored.py` | 4 pasaron offline |
| `test/services/test_work_proxy_recovery_t097_restored.py` | 2 pasaron con PostgreSQL privado |
| `test/integration/t097/test_proxy_sigkill_t097_restored.py` | La repetición instrumentada anterior falló en ambos casos tras 390,70 s por un deadlock. El orden de locks se corrigió y Guest80 pasó después ambos casos SIGKILL en huésped. |
| `test/integration/t097/test_same_uid_fd_theft_t097_restored.py` | 1 pasó en la comprobación huésped separada de robo de FD tras `exec` |

Los logs y hashes de estas pruebas están en
`/home/felni/.cache/caos-spec021-evidence-20261012/consolidation/`. El test de
FD añade un control sin filtro que demuestra que el robo funciona después de
`exec` y el reinicio de dumpability, y luego verifica `EPERM` bajo el seccomp
filtrado. Su recolección local no cuenta como aceptación del huésped.

La ejecución de huésped r9 fue **5 PASS, 3 FAIL, cero omisiones** en 740,72 s;
se conserva como antecedente histórico y fue supersedida parcialmente por r10.
El registro completo es
`runtime/t097-r9-final/retry/evidence/attempt-20261012T045037Z.5Gr3wD.log` y
usó el snapshot inmutable `backend-r9/source` (manifiesto SHA-256
`fa2952568c97a85cbd5c289e47e4a2a97c2ded0c380ccdb81c9dd1baea9e540d`). Dos
fallos eran binds SQLite `?` en las pruebas nativas: `test_host_acceptance.py`
y `test_proxy_concurrency.py`. Sus fuentes actuales ya usan `%s` y el host r10
aprobó **8 pruebas** en
`runtime/t097-r9-final/retry/evidence/r10-diagnostic-attempt-20261012T-running/guest-pytest-logs/t097-pytest-host.log`.
Scratch obtuvo **61 PASS, 1 FAIL y 1 SKIP** en el log vecino
`t097-pytest-scratch.log`; por ello el diagnóstico r10 no es una ejecución
completamente verde. Landlock obtuvo **1 PASS**, los nodos de dispositivo **2
PASS** y limpieza/casefold **3 PASS**. Los selectors SIGKILL y FD de ese script
quedaron deseleccionados; no se cuentan como cubiertos por dicha ejecución. La
raíz privada de estos registros es
`/home/felni/.cache/caos-spec021-evidence-20261012/runtime/t097-r9-final/retry/evidence/r10-diagnostic-attempt-20261012T-running/`;
las rutas `missing-cases-20261012/` y `ack-debug/` citadas abajo son relativas
a esa raíz.

En comprobaciones separadas, la caracterización de sibling user namespace con
UID 1000 obtuvo **1 PASS** en 57,94 s. El run manual
`missing-cases-20261012/uid999-host-acceptance-rerun1.log` ejecutó ambos casos
SIGKILL y el robo de FD: **2 FAIL, 1 PASS** en 234,18 s. En esos SIGKILL aún
regía la espera de test de 10 s, que venció antes del ACK de setup; el caso de
robo de FD tras `exec` pasó. La prueba focalizada del ACK obtuvo **1 PASS** en
117,69 s en
`ack-debug/extracted-r3/t097-ack-capture-r3/pytest.log`; la captura vecina
`stage-timings.json` registra una lectura de ACK de 11,968 s con un límite de
producción de 20 s. Este resultado mide esa ruta focalizada, no toda la
partición.

La repetición instrumentada posterior dio **2 FAIL en 390,70 s**: ambos tests
terminaron con `TimeoutError` tras esperar 65 s sin recibir el callback
upstream. En la limpieza del dispatch se observó `DeadlockDetected` y
`DeliveryUncertain`. El registro PostgreSQL
capturó un ciclo de locks `job → attempt` frente a `attempt → job`: el INSERT
de `work_events` pide `KEY SHARE` sobre `work_jobs` por la FK mientras otra
transacción bloquea el intento. El extracto sanitizado está en
`missing-cases-20261012/postgres-deadlock-excerpt.log` (SHA-256
`32043d2fad5be7c69b7bb63e19800b97857670dd560206e6b53295442a2740a1`); la
cronología de timeout es `missing-cases-20261012/sigkill-stage-timeline.log`.
El presupuesto de test 20 s/worker 45 s no corrigió esta causa de producción.
WorkRepository y Scheduler corrigieron el orden de locks que producía el ciclo
`job → attempt` / `attempt → job`. La regresión nativa enfocada obtuvo **12 PASS
únicos**: cuatro de Repository (stale/replay), cuatro de reemplazo/capacidad y
cuatro de releases, public claim y enqueue. Los módulos acotados obtuvieron
**93 PASS** en Repository y **80 PASS** en Scheduler. El hash
`d48a9488dc037bf5281ef138646cd3ac85e2ec05d64524050b3a0a557bab1916` de
`work_repository.py` corresponde al baseline previo a la corrección del deadlock;
el hash actual es
`3f92c82ea6d33817aeb7aaa36617815d01defb268a38556336cb702da733e0c4`. El hash
`e59383000eadcf8a889879e7b56467ecceda8042ca48aa812ec424e893cd0dcb` corresponde
a `work_scheduler.py`. Los logs están bajo
`/home/felni/.cache/caos-spec021-evidence-20261012/ledger-deadlock/`,
`design-scheduler-lock/` y `audit/`. Esta cobertura nativa valida la corrección
del ciclo de locks; la repetición huésped canónica posterior pasó ambos casos
SIGKILL, como se registra en el resumen Guest80.
No se amplió el timeout de ACK de producción. El cleanup del huésped reanudó y
retiró el runner detenido PID 7, y liberó la ventana de aceptación.

## Aceptaciones históricas y actuales de instalación e importación Docker

Se construyó y usó la imagen privada e inmutable
`sha256:7143d84462108e715f101c4142d54f00890465ad61e09e3a0f03fed94663dc72`.
Los hashes de `api/main.py`, el driver de continuación de Work, seccomp, la
proyección de interacciones y el servicio de interacciones se comprobaron dentro
de la imagen. El manifiesto de imagen y los logs están en
`/home/felni/.cache/caos-spec021-evidence-20261012/app-current-20261012-r2/`.

- Instalación real, reinstalación idempotente, persistencia de cookie HTTP,
  parada/arranque, actualización, rollback, fallo de candidato y retención de
  imagen worker: **1 passed**, 301.38 s.
- Importación real desde fuentes SQLite detenidas de main/auth/peer: **1
  passed**, 103.14 s. Los recibos verificados cubren 132/5/2 tablas. Los hashes
  de las tres fuentes permanecieron iguales; la importación sola no activó la
  aplicación ni configuró el scheduler. Una instalación normal posterior dejó
  el scheduler listo y la API saludable.
- Se retiraron los contenedores, red, directorios y secretos privados del test.
  Las etiquetas de imagen predeterminadas y el contenedor personal del operador
  conservaron su identidad.
- La ejecución r2 es anterior a la fuente actual y no satisface por sí sola el
  gate Docker14; el resultado posterior T019/T020 se registra debajo.

El snapshot Docker posterior tuvo digest
`sha256:72ac2ba81a3ecc93e616883269b41789a17e69f3b25eaba14f92e68605341876`.
Su ciclo real de instalación/reinstalación obtuvo **1 PASS** en **267,16 s**.
La importación SQLite detenida de los tres ámbitos obtuvo **1 PASS** en
**96,18 s**. T019/T020 obtuvo **14 PASS, 0 SKIP** en **380,67 s** usando el
rootfs worker `sha256:2e741faf558eb5b518cd9a2b86ce4d17f2efe868922c7fb1e83372e76da9f7d4`.
Browser-Docker/T076 obtuvo **1 PASS, 0 SKIP** en **23,20 s**. La prueba renovó
la sesión tres veces y cerró sesión antes de que terminara el worker; el recibo
0600 `/home/felni/.cache/caos-spec021-evidence-20261012/frontend/final-frozen-r2/results/browser-docker-proof-r5.json`
registra `work_state=running`,
`attempt_state=sent` y `accepted_result_id=null`, así que acredita el ciclo de
cookie y logout con transporte Docker, no la aceptación del resultado del
worker. La aceptación huésped T074 se verificó por separado en Guest80. Los logs están en
`/home/felni/.cache/caos-spec021-evidence-20261012/frontend/final-frozen-r2/`
y `runtime/t020-final-r3/logs/t020-acceptance.log`.
Este digest es un antecedente histórico, supersedido para los gates Docker por
la imagen controller b827 registrada a continuación.

La imagen controller actual
`sha256:b827abf5d65b9f9c97cece18c38c7da3b096fc56087bbf7924e1658c96767e15`
pasó tres gates reales: ciclo de instalación **1 PASS** (197,39 s), importación
detenida SQLite de main/auth/peer **1 PASS** (66,24 s) y Browser-Docker/T076
**1 PASS** (22,32 s). Los logs y recibos están en
`/home/felni/.cache/caos-spec021-evidence-20261012/frontend/final-current-r3/`.
Browser-Docker renovó tres veces la cookie y cerró sesión antes de terminar el
worker; el recibo indica `work_state=running`, `attempt_state=sent` y
`accepted_result_id=null`. Acredita ese recorrido de autenticación y transporte,
no que el worker haya aceptado un resultado.

Con el rootfs worker
`sha256:2e741faf558eb5b518cd9a2b86ce4d17f2efe868922c7fb1e83372e76da9f7d4`,
T019/T020 obtuvo **14 PASS, 0 SKIP** en **267,88 s**. La evidencia actual es
`/home/felni/.cache/caos-spec021-evidence-20261012/runtime/t020-final-r7/evidence.json`
y el log
`/home/felni/.cache/caos-spec021-evidence-20261012/runtime/t020-final-r7/logs/t020-acceptance.log`.
Estos gates Docker son evidencia de instalación y transporte; la aceptación
Guest80/T074 se verificó por separado. El suplemento T098 de provisioning se
completó en otra ejecución, con 4 PASS y 0 SKIP.

## T049, T079 y fixture Vault posteriores al snapshot r9

T049 incorpora `DatabaseTarget.identity` a la clave de caché de
`MemoryGraphProvider`. Esto separa los stores PostgreSQL aunque compartan la
misma raíz de artefactos. La regresión fue RED antes del cambio y el módulo
`test/integration/test_memory_knowledge_policy.py` obtuvo **25 PASS**. El
fixture de `test/services/vault/test_scan_exclusions.py` ahora limita el rechazo
de `os.open` al FIFO examinado y delega las demás aperturas; su archivo completo
obtuvo **23 PASS** en 19,28 s.

T079 completó el perfil nativo de capacidad de Spec016. Los hashes finales del
servicio y repositorio son `02e404a1f73f735f0089f850daa72c9e5d6f9470ad9558952b60a4f44d903ecf`
y `3891a84f96e1a21cfcc0be463b651a38516760e2d7ba3ab8349da3e6a7ac4b6d`; el
test es `test/integration/test_interaction_capacity.py`. En PostgreSQL se
confirmaron 20/20 fuentes y 100/100 preguntas visibles. El p95 de registro
confirmado a metadata fue **789,4 ms**. El p95 de solicitud a metadata fue
**3312,8 ms** y se conserva como diagnóstico; el gate SC-001 empieza en el
registro confirmado. Las regresiones de ciclos/Work dieron **10 PASS**, eventos
**4 PASS** y guards AG-UI/nativos/Work/espera **34 PASS**. La validación actual
está registrada en `specs/021-postgresql-persistence/validation.md` y
`specs/016-agent-interactions/composition-review.md`.

En el snapshot final privado ext4, Black dejó **1309 archivos sin cambios**, isort
terminó con exit 0 y mypy, con `mypy.ini` del proyecto, pasó `src/` (**424
archivos**). Los logs están en
`/home/felni/.cache/caos-spec021-evidence-20261012/audit/final-static-r1/logs/`.
La comprobación de composición fresca reportó **427 archivos, 1811
dependencias, 5 contratos conservados y 0 rotos**. El log actual es
`/home/felni/.cache/caos-spec021-evidence-20261012/root-final-checks/project-composition-check.log`.
Estos gates estáticos no sustituyen la suite global.

La validación final del frontend registró **504 pruebas en 45 archivos**, el
build TypeScript/Vite pasó en **45,47 s**, y E2E obtuvo **20 PASS**, cero
omisiones, con p95 SC-001 visible de **1076 ms**. Los resultados están en
`/home/felni/.cache/caos-spec021-evidence-20261012/frontend/final-frozen-r2/`.

## Verificación pendiente

1. Iniciar y ejecutar R12 del backend global después de las correcciones
   focalizadas. R11 completó 17.245 casos con **119 FAIL, 27 ERROR y 115 SKIP**,
   y cobertura de
   85,2058 %, inferior al objetivo de 87 %. R10 quedó interrumpido al 86% y es
   solo antecedente; el resultado global r9 también sigue siendo histórico.
   R12 todavía no ha comenzado y los resultados focalizados no alteran las cifras
   ni la cobertura de R11.
2. Evaluar la propuesta T055 para el NFR de memoria; el benchmark PostgreSQL 18
   de la fuente actual aislada midió entre 1,595 y 1,682 s y no alcanzó el
   objetivo.
3. Revisar el diff final. Los gates estáticos y de composición del snapshot
   actual pasaron, pero no se ha realizado push, publicación ni activación.

No se ha realizado push, publicación ni activación de la instalación personal.
