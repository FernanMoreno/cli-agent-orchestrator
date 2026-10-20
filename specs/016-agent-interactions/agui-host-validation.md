# Continuación: proyección AG-UI y aceptación Docker/host

Estado: **en ejecución**. Petición explícita del usuario después de la validación local completa. Los cambios de implementación de esta ronda invalidan la certificación de la versión anterior hasta repetir las suites aplicables.

## Actualización — PostgreSQL nativo, 2026-10-12

La evidencia actual se conserva bajo
`/home/felni/.cache/caos-spec021-evidence-20261012/`; los registros siguientes
bajo `/home/felni/.cache/c16t/` son históricos y esa ubicación antigua ya no
está disponible. El cierre global continúa pendiente según
[el registro de Spec021](../021-postgresql-persistence/validation.md).

- Claude CLI 2.1.293: recorrido real de dos preguntas (texto y selección), dos
  respuestas HTTP humanas y dos acknowledgements exactos; exit 0, 17,01 s.
- Codex CLI 0.162.1, modelo `gpt-6-luna`: mismo recorrido real 2/2/2 sobre
  PostgreSQL nativo, herramientas MCP ask/wait/ack; exit 0, 16,14 s.
- Hermes CLI y PTY reales: **1 PASS**, 20,50 s; modelo simulado en loopback,
  aviso manual y cierre externo exacto. No acredita inferencia externa.
- OpenCode CLI 2.0.18: conexión MCP real y catálogo de 79 herramientas del
  servidor; el scope del agente expone exclusivamente ask/wait/ack. No se
  envió una tarea a un modelo: sigue pendiente la ruta de proveedor/modelo
  solicitada al usuario.
- Linux T097: primera partición ejecutada, **5 PASS, 3 FAIL**, cero omisiones.
  Dos binds antiguos de las pruebas ya se portaron a PostgreSQL; el timeout
  del ACK del tercer caso sigue bajo diagnóstico. No es aceptación final.

Los recibos y hashes actuales de los proveedores se registran en
`live-provider-current-20261012.json`. T079 añade la regresión nativa de
20 agentes y 100 preguntas: la admisión excede todavía el p95 requerido de
dos segundos. La repetición final de navegador debe ejecutarse después de su
corrección; los resultados históricos no aprueban esa frontera.

## Puerta previa

- Git status revisado; source/test/spec 016 de la funcionalidad y Graphify/auditorías/specs 017–020 preexistentes preservados.
- Spec Kit: requisitos 16/16, FR-040/FR-042 y T064; plan/tareas extendidos con T072–T078.
- Graphify aislado: agui_stream 10 relaciones, fuente contrastada de autorización, snapshots, idle, cursores y mapper estándar. Source de ledger y lectura SQLite revisados; read_connection inicia BEGIN.
- Contrato metadata-only extendido antes de implementación en [runtime-events.md](contracts/runtime-events.md). TDD autorizado para proyección, sin segunda autoridad.

## Evidencia inicial

- Referencia AG-UI previa: **511 passed**, 10 avisos, 117,93 s, exit 0. Suites services/agui, API AG-UI, servicios y default-off en copia Linux de fuente comprobada. `/tmp/caos-spec016-agui-baseline.log`.
- Docker ahora responde en socket Unix local: **29.8.1, Linux amd64, cgroup v2**, host WSL2. Esta disponibilidad cambia respecto de la ronda anterior.
- T019 backend Docker real: **8 passed**, 59,01 s, exit 0; wrapper del repo con CAO_HOME_DIR/TMPDIR privados, supervisor compilado de la fuente actual e imagen inmutable `sha256:b999af155ba17f29bfdcc3ce1410ae15b0e8fb646da7e20caadd516f4dd1f7fd`. `/home/felni/.cache/c16t/r-t019-247affe2/run.log`.
- Se preservan los siete contenedores previos. No se termina ningún proceso ajeno.
- Host WSL2 carece de QEMU/OVMF/cloud-localds y no dispone de sudo sin interacción. La aceptación se prepara en contenedor desechable con QEMU TCG, guest y cuenta broker propios. Cambios de políticas sólo dentro del guest.
- `/tmp` tmpfs lleno y swap saturado: artefactos inactivos propios de baseline/monitor y copia de validación se archivan en ext4 manteniendo symlinks de las rutas originales. Se conserva la evidencia.

## Resultados pendientes

Proyección y tests verdes, instalación/restart/upgrade/rollback, guest host y scratch Bubblewrap; suites aplicables, arquitectura/composición, hashes, diff, Graphify y verificación final. Ninguna ejecución en preparación se declara pasada.

## Aceptación Docker ejecutada

Estos resultados preceden a la congelación de la nueva proyección AG-UI.

- T020 backend/workflow: **14 passed**, 112,20 s, exit 0. Incluye los ocho casos T019; no son 22 pruebas distintas. Imagen inmutable `sha256:2c130ab41c5aa53166a43d07918bfc8ae6220fd2f52292c3ba0d15113de44d0d`; supervisor y worker compilados de fuente comprobada. `/home/felni/.cache/c16t/r-t020-9ae5888b/run.log`.
- Setup local con la imagen Docker explícita: **5 passed, 0 skipped**, 18,27 s, exit 0. La primera ejecución sin seleccionar imagen dejó un skip; esta repetición ejecuta ese caso. `/home/felni/.cache/c16t/r-local-setup-docker-1791669625/run.log`.
- Autenticación de navegador con Docker real: **1 passed**, 30,32 s, exit 0; tres renovaciones y logout anterior a la salida del worker. `/home/felni/.cache/c16t/r-browser-docker-cc530092/run.log` y `proof.json`. La prueba verifica continuidad del trabajo y autoridad de settlement; no certifica transporte de resultado MCP del worker (`worker_mcp_result_transport=false`).
- Alias previo `cao-work-empty-rootfs:local` restaurado a su imagen original. Ningún contenedor Work de estas ejecuciones permanece activo; contenedores ajenos preservados.

## TDD y runner host

- Rojo AG-UI ejecutado contra SQLite/REST/stream reales: el módulo todavía ausente falla por import; integración reproduce ausencia de delta durante idle y cursor inválido aceptado (dos fallos, un caso T064 ampliado pasa).
- Runner privado preparado con QEMU TCG, Ubuntu y Bubblewrap fijados por SHA256; sintaxis de script exterior y heredoc remoto comprobada. Requiere ocho casos T097, **63** casos scratch y un caso Landlock de sockets, sin skips/xfails.
- Perfil local de VM: dos vCPU y 2048 MiB; contenedor normal con límite de 3 GiB, sin `--privileged`, socket Docker ni cambios de políticas del host. Las comprobaciones de ABI, identidad broker y aislamiento conservan sus requisitos. La preparación no constituye aceptación host.
- Locks de vault con aliases de mayúsculas en NTFS real: **2 passed**, 5,65 s, exit 0. `/tmp/caos-spec016-host-case-insensitive-final.log`. Son los dos casos omitidos en ext4. Primer intento falló por basetemp fuera de TMPDIR; el segundo reprodujo una incompatibilidad de captura pytest con archivos temporales borrados en drvfs. Repetición con TMPDIR NTFS y `-s` conserva los tests/guards originales y ejecuta sus escenarios. Código de vault idéntico a la copia validada anterior.

## Ejecuciones de preparación que no certifican aceptación

- Revisión conjunta independiente: **15 passed, 2 failed**, 65,65 s, exit 1. Un test nuevo cambió durante la ejecución y todavía consultaba `ReturnedAnswer.answer_id`, atributo inexistente; el recibo tipado contiene ese ID. El otro fallo fue `OSError: Cannot allocate memory` al importar FastMCP/beartype con varios validadores y QEMU simultáneos. Se repite después de congelar todos los tests y liberar recursos.
- Regresión AG-UI en copia Linux: **516 passed, 1 failed**, 10 avisos, 253,81 s, exit 1. `/tmp/caos-spec016-agui-regression.log`. La copia todavía contenía la misma aserción antigua de `ReturnedAnswer.answer_id`; producción conserva los hashes congelados. La nueva versión de boundaries pasa sus cuatro casos por separado; la suite completa se repetirá en serie.
- Black de preparación de instalación personal también falló por falta de memoria; se limita a un worker y se repite tras salir del guest. No se modifica producción para corregir estos problemas del runner/test.
- Primer arranque QEMU: **exit 1 antes de SSH, archive o tests**. La imagen fijada verifica su SHA256; serial confirma interfaz y ruta de red activas, cloud-init/AppArmor todavía arrancando al alcanzar el plazo de cuatro minutos. `/home/felni/.cache/c16-evidence-archive/t097-private-runner/evidence/t097-qemu-run-attempt-1.log` y `t097-qemu-boot-failure.json`. Reintento acotado a diez minutos de readiness para TCG; pins, ABI, aislamiento y timeouts de los tests se conservan.

## Proyección AG-UI validada

- Adaptador integrado en el estado estándar de flota: `STATE_SNAPSHOT` contiene `interactions`; los deltas de idle sólo actualizan ese campo. Mismo ledger, lectura transaccional de metadata/recibos/high-water y sin segunda autoridad de respuesta.
- **18 passed**, cinco avisos de dependencias, 37,31 s, exit 0, evaluación independiente del árbol final. `/tmp/cao-interaction-audit-agui-final.log`. Incluye recibos accepted/returned/ack, privacidad, enums nativos/delegados, 101 preguntas en dos páginas, observador sin capacidad, invalidación de perfil, fallback unavailable, reconexión y revocación real de sesión SQLite/cookie con cierre de la suscripción.
- Regresión AG-UI completa repetida sobre copia Linux corregida: **517 passed**, diez avisos, 118,62 s, exit 0. `/tmp/caos-spec016-agui-regression-final.log`. Incluye APIs AG-UI existentes, servicios, stream/replay/auth y default-off.
- **1234 archivos Python** de `src/test` coinciden por SHA256 entre workspace y copia Linux: `/tmp/caos-spec016-agui-verified-validation-manifest.json`. Los tres archivos de producción de la proyección mantienen los hashes congelados.
- El reintento de VM no se inicia mientras MemAvailable esté bajo su gate de 2,5 GiB; la instalación personal avanza primero, sin QEMU simultáneo. No se libera memoria de procesos ajenos.

## Instalación personal de la versión congelada

Esta primera imagen precede al hallazgo posterior de revocación durante lectura. Su resultado se conserva como histórico; se construirá y validará de nuevo la imagen corregida.

- Imagen privada construida de contexto explícito sin credenciales: `cao-personal-spec016:20261011-15c2d1bab2f2`, ID `sha256:210665d6d63dccba9e446baa1d3b76ede8a37044caf72eaedfca299a45fcc5eb`. Build exit 0. Manifest del contexto SHA256 `15c2d1bab2f219b44e53b60098e7e0ea0c336bc0901613c96cd0a2a418bb726a`.
- Primer intento: **1 passed, 1 failed**, 64,09 s. `TemporaryDirectory` bajo HOME produjo un bind anidado creado por Docker y falló su limpieza por permisos antes de arrancar la aplicación. Se conserva `/home/felni/.cache/c16t/t076-acceptance-15c2d1/pytest.log`. No se cambia el instalador para resolver la colocación del runner.
- Repetición con TMPDIR/basetemp privados en ext4 fuera de HOME: **2 passed**, 156,47 s, exit 0; instalación real, cookies, persistencia, upgrade y rollback. `/var/tmp/c16t076a/pytest.log`.
- Probes independientes de archivos instalados en imagen sin red y en contenedor candidato vivo confirman los tres SHA256 de producción congelados. `/home/felni/.cache/c16t/t076-acceptance-15c2d1/image-hash-probe.log` y `/var/tmp/c16t076a/container-hash-probe.log`.
- Contenedores/tags temporales propios retirados. Alias `cao-personal:local`, `cao-personal-worker:local` y contenedor operador `cao-personal` preservados. La imagen privada de evidencia se conserva.

## Revisión independiente posterior

Se identifica una ventana entre validar autorización y emitir una proyección cuya lectura tarda: logout durante `projection_snapshot()` permitía publicar el resultado de esa lectura. Las pruebas anteriores revocaban después del snapshot y no cubrían esta ventana. Se decide TDD para revocación durante lectura inicial/idle y revalidación inmediatamente antes de emisión. El guest se pausa antes del archive del checkout y la suite total conserva su espera hasta congelar la corrección. Los resultados previos no certifican este cambio.

- Intento inicial de rojo bloqueado en collection por ENOMEM aun con VM pausada. Se retira sólo el contenedor guest propio para liberar RAM; intento host 2 queda `ABORTED_BY_ROOT_BEFORE_ARCHIVE_FOR_MEMORY`, exit 137, sin ejecutar tests ni archive. Serial y diagnóstico preservados; scratch propio retirado.
- Transporte reproduce dos fallos antes de modificar producción, luego **2 passed**, 19,67 s, y subset completo **18 passed**, 60,07 s, exit 0. Evidencia capturada en `/tmp/agui-post-read-auth-race-evidence.txt`; el rojo inicial tiene resumen observado, no traceback reconstruido.
- Reproducción independiente del root en copia anterior `main.py` SHA256 `1223397d…fde9b4` con tests nuevos: **2 failed**, 5,96 s, exit 1; ambos reciben snapshot después de revocación. `/tmp/caos-spec016-auth-race-red-independent-final.log`. Después de copiar producción corregida: **2 passed**, 5,35 s, exit 0; `/tmp/caos-spec016-auth-race-green-independent.log`. La primera selección independiente todavía usaba el test anterior (1 passed, 11 deselected); ese resultado no constituye rojo y no se utiliza como tal.
- Nuevo `main.py` congelado SHA256 `701af2bc6b92f88ca6b9be1c2144c219307535f93754f6da2fb8191033c6aaaa`; servicio y adaptador conservan sus hashes anteriores. Revisión independiente de fuente verifica autorización después de lectura tanto inicial/evento como idle, sin delta/keepalive tras revocación y con unregister. No identifica otro defecto concreto.
- Riesgos de concurrencia identificados: cada polling comparte la caducidad previa de REST (`BEGIN IMMEDIATE`) y puede competir con respuestas. Las validaciones SQLite/Work son síncronas dentro del generador async, con timeout DB de hasta diez segundos. No se ha certificado carga con muchos observadores; esta continuación conserva la política TTL compartida.

## Nueva imagen después del hardening

- Nuevo contexto: 663 archivos; sólo cambia `api/main.py` respecto al contexto anterior. Manifest SHA256 `4a217c822449fc435b21358bd771d26235decff4b56d48f0df8d3156178180cc`, archive `7334ea2554dd88f9a62009973a07bdebd837976d53cf7380eba9dc64602433d4`.
- Build exit 0, imagen `cao-personal-spec016:20261011-auth-race-4a217c822449`, ID `sha256:d9fd99f8dc38fbbe74e987a94d9a468c5098ee1bf1bd98088971538bc8e8af0a`. `/home/felni/.cache/c16t/t076-context-auth-race/build.log`.
- Probes independientes de imagen y contenedor vivo confirman main corregido y servicio/adaptador congelados. `/home/felni/.cache/c16t/t076-context-auth-race/image-hash-probe.log`, `/var/tmp/c16t076b/container-hash-probe.log`.
- Instalación completa repetida: **2 passed**, 162,02 s, exit 0. `/var/tmp/c16t076b/pytest.log`. Incluye rollback y recuperación después de candidato fallido. Alias/imágenes y contenedor operador intactos, recursos temporales propios retirados; ambas imágenes de evidencia conservadas.
- Nuevo manifiesto separado de 1234 archivos Python raíz/copia Linux coincidentes: `/tmp/caos-spec016-agui-auth-race-final-manifest.json`, hash agregado `308390c9c4b89d79ab1f59a1d95f1a2216112266840061352b433a57e0c56c95`.
- MemAvailable tras cleanup sigue alrededor de 2,4 GiB. Se autoriza adaptación privada del intento host 3 a 1536 MiB guest/2 vCPU y gate host de 2 GiB, conservando contenedor de 3 GiB y pins, políticas, ABI, broker, assertions y timeouts. No se modifica el runner de producción ni se debilitan checks para convertir un fallo en éxito.

## Intento host 3: frontera filesystem del runner

- SSH sí responde y el script obtiene kernel `7.3.0-5-generic` y Ubuntu 26.10 (imagen de desarrollo fijada por el runner original). Después, `tar` falla por `Cannot savedir: Cannot allocate memory` al atravesar dos directorios `src/test` del montaje WSL/9p. Exit 2 **antes de archive, manifiesto o pruebas**. No constituye fallo de arranque ni aceptación de aislamiento.
- `/home/felni/.cache/c16-evidence-archive/t097-private-runner/evidence/t097-qemu-run-attempt-3.log` SHA256 `23a29a8088de30149ef9dd8b62013e2883363ab502c460aa930b862bf047ef07`; diagnóstico `t097-qemu-attempt-3-failed-before-archive.json`. Guest/contenedor retirados; MemAvailable vuelve a 2,5 GiB.
- Source del script ejecutado contrastada: whitelist canonical de pyproject/lock/README/LICENSE/src/test/hatch hook, caches y dependencias excluidas. La hipótesis inicial de archivar todo el workspace era incorrecta y se descarta.
- Siguiente adaptación privada: preparar archive exacto en ext4 **antes** de consumir RAM con QEMU; contrastar todos los archivos/links/config del input con la fuente actual y verificar el manifiesto después de extraer en el guest. Se evita el recorrido 9p bajo presión de memoria.
- Precisión del readiness: 300 probes SSH, pausa de dos segundos y `ConnectTimeout=5` por probe; no equivale a un plazo estricto de diez minutos. Pins y parámetros de autenticación se conservan.

## Input ext4 preparado antes del guest

- Comparación de whitelist completa actual y copia Linux: **1499 archivos / 2 symlinks**; contenido y targets coinciden. Manifest de archivos en checkout, copia y archive extraído: SHA256 `4776218c5abaa2cfdf9fdf4f2d9c2f8ec045a400366b450f2777a568b20318a2`.
- Staging privado restaura modos tracked desde Git index (contenido dirty actual), nuevos Python 0644 y dirs 0755. No se cambian permisos del workspace ni de la copia de validación. NTFS y su copia arrastraban 0777; los modos del archive ahora reproducen un checkout Linux normal.
- Archive de 6,4 MiB SHA256 `ca3a748a9796dbf80245023f63fdd788c834048652d335e14e8913804ae0159c`; 1494 archivos modo 0644, cinco 0755, dos symlinks, uid/gid 1000. Metadata TSV y hash de extracción preservados en `evidence/source-compare` del runner privado.
- Diff privado leído por root: sólo reemplaza el tar local posterior a SSH por checksum pin y copia del archive preconstruido. Resto de manifest verification, Ubuntu/Bubblewrap pins y checks se conserva; scripts pasan bash syntax. Intento 4 todavía no ejecutado al registrar esta preparación.

## Repetición Docker posterior al freeze

- Primera T020 actual: **14 failed**, 91,49 s, exit 1. `/home/felni/.cache/c16t/t076-postfreeze-20261011/t020.log`. El wrapper privado heredó `umask 077` al compilador; `/cao-work-supervisor` quedó 0700. Reproducción aislada confirma `exec: /cao-work-supervisor: permission denied` en OCI y el parser rechaza correctamente la salida del daemon como frame inválido/incertidumbre.
- `docker cp` extraído al host no acredita el owner dentro de la imagen; esa inferencia inicial se descarta. Modo 0700 y denegación OCI sí se observan directamente.
- Alias raíz restaurado al ID original aun con exit 1; recursos y contenedor operador preservados. Repetición privada cambia umask a 022 sólo para el subbuild exacto; perfiles/directorios siguen 0700 y outer umask 077. Source/tests de producción no cambian. Nueva aceptación pendiente de exit real.

- Metadatos tar dentro de las imágenes verifican realmente owner/mode: anterior `0700 1000/1000`, corregida `0755 1000/1000`. Con identidad inicial `0:0` y capacidades DAC retiradas, el primer modo no permite exec. `/var/tmp/spec016-t020-{old-image,image}-metadata-20261011.tar`. Se corrige la inferencia anterior usando la evidencia interna.
- T020 final después del ajuste privado: **14 passed**, cinco avisos, 91,36 s, exit 0. `/home/felni/.cache/c16t/t076-postfreeze-20261011/t020-umaskfix.log`. Rootfs inmutable `sha256:9cc3945d5d0c54865402e4797ed6bd40b21208cbe4243b146bc12fe443f69ab5`; supervisor/worker source hashes iguales. Alias original restaurado `sha256:1b277d81f9d53c2c60bc46c99cfca8630981abb2a47f4b5f29901c78fda58f01`, imagen de prueba preservada con tag privado. Son 14 casos incluyendo los ocho T019.
- Setup final sobre rootfs inmutable: **5 passed**, 26,59 s, exit 0; `/home/felni/.cache/c16t/t076-postfreeze-20261011/setup-docker-5.log`.
- Browser auth Docker final: **1 passed**, cinco avisos, 31,50 s, exit 0; `/home/felni/.cache/c16t/t076-postfreeze-20261011/browser-docker-1.log`. Proof `/var/tmp/c16tbrowser1.ylXxnZ/browser-proof.json` confirma tres renovaciones y logout anterior a salida del worker; `worker_mcp_result_transport=false`, `accepted_result_id=null`. No se extiende esa prueba a aceptación MCP del resultado.

## Comprobación de tipos y nuevo freeze

- Mypy del adaptador nuevo reproduce dos fronteras mal tipadas: el constructor de snapshot recibe diccionarios donde declara modelos; la versión de metadata es `object` antes de convertir a `int`. `/tmp/caos-spec016-agui-projection-mypy.log`, exit 1. El tercer diagnóstico en `work_origin` se contrastará con el comando completo habitual `mypy src/` antes de atribuirlo a producción.
- Se corrigen esas fronteras conservando la validación y el JSON público. Esta mutación invalida los resultados anteriores como certificado del árbol final; permanecen como evidencia histórica. Se repetirán proyección, imagen Docker, aceptación y regresión sobre los nuevos hashes.
- Intento host 4 llegó al arranque de QEMU; se aborta por decisión de root antes de archive/pruebas para congelar la corrección de tipos y liberar RAM. Exit observado 143; registro `evidence/t097-qemu-attempt-4-aborted.json` del runner privado. No se considera aceptación ni fallo de las pruebas host.

- Revisión de la corrección: el adaptador conserva modelos validados hasta el dump final; el servicio comprueba question_id y carga la versión actual en la misma transacción BEGIN de la página. Auditor y root verifican que no introduce drift, exposición o permisos nuevos.
- Manifiesto nuevo de 1234 archivos Python raíz/copia: `/tmp/caos-spec016-agui-typing-final-manifest.json`, agregado `de99a6adebc2fd3fc85bc0a754b0bf07407c71981eefab8f6b9227d0bef5ce3a`; root recalcula cada archivo en ambos árboles, cero discrepancias. Main conserva `701af2bc6b92f88ca6b9be1c2144c219307535f93754f6da2fb8191033c6aaaa`; servicio `f56e48ee090509ef06e02279add9e0eef5f66f7ba1fedea3e11a4ed3d6859991`; adaptador `8ccc2e805df6b3e1a87779c17f137764a720802992e7208b94e53715bf33f5d9`.

- Nueva versión: **18 passed**, cinco avisos, 14,81 s, exit 0 (`/tmp/caos-spec016-agui-typing-focused.log`). Comando habitual completo `mypy src/`: **406 archivos, sin errores, exit 0** (`/tmp/caos-spec016-agui-typing-mypy.log`); el diagnóstico aislado de work_origin era un artefacto de resolución parcial y no se cambia esa fuente. Black/isort de los dos archivos editados exit 0.
- Se autoriza la suite backend completa sobre este freeze antes de repetir Docker/guest, sin VM o builds simultáneos.

- Isort completo `src test` ejecutado por root sobre la copia actual: exit 0; `/tmp/caos-spec016-agui-typing-full-isort.log`. Black completo anterior precede a los dos cambios de tipos y se repetirá sin VM ni pytest simultáneos.

## Input del intento host 5

- Nuevo archive privado preserva el anterior: `evidence/source-compare-attempt-5/checkout-from-ext4-git-modes.tar.gz`, SHA256 `16789c7420d0ef900979db1edd1ef0040f36089d0f658ced0fce964e6396b479`. Root recalcula 1499 archivos en checkout, copia ext4 y archive extraído: cero discrepancias. Manifiestos de contenido iguales `8017e5b30a30a1e7e2ec9324b980a3aa3ee176f671852385f045814fff0e6595`; mismos dos symlinks y modos normales del staging.
- Root lee los diffs de generator y shell: sólo cambia path/hash del archive respecto al intento 4. Preflight `t097-runner-preflight-attempt-5.json`: perfil 2 vCPU/1536 MiB, pins y selección 8+63+1+2 sin skips/xfails, timeout worker 45 s conservados. Estado PREPARED_HOLD_NOT_LAUNCHED; no aceptación aún.

## Suite backend completa: dos fallos de fixtures

- Ejecución completa actual: **2 failed, 16960 passed, 119 skipped**, 93 avisos, 2609,69 s, exit 1. `/tmp/caos-spec016-agui-full-backend.log` y XML; JUnit 17081 casos, dos failures, cero errors. Cobertura 67201/77169 statements = **87,08289598%**, pero la suite no queda certificada por esa cobertura. Artefactos se conservan sin sobrescribir.
- `test_cursor_evicted_before_first_stream_iteration_signals_resync[agui]` invoca directamente la función del endpoint omitiendo parámetros Query nuevos; Python entrega `Query(None)` y preflight lo rechaza con 400. El router HTTP sí resuelve los valores. Se corrige la llamada del test pasando None/100 explícitos.
- `test_service_projection_pages_101_items_without_duplicates` usa el ledger global de worker sin `isolated_memory_db`; aparecen veinte preguntas de otro test y la segunda página tiene 21. Se añade la fixture de aislamiento ya existente. No cambia producción ni assertions de paginación.
- Dos cambios de test sincronizados; se repetirá suite dirigida/AG-UI y después backend completo sobre nuevo manifiesto. Black completo root exit 0, 1234 archivos; `/tmp/caos-spec016-agui-typing-full-black.log`, más check de los tests finales.

## Reproducción descarta la hipótesis inicial de fixture

- Repetición dirigida con fixture explícita todavía falla: **1 failed, 17 passed**, 13,30 s; `/tmp/caos-spec016-test-fixes-focused18.log`. La fixture isolated_memory_db ya era autouse: no se atribuye el fallo a contaminación sin evidencia. Event stream corregido sí pasa en su módulo: 13 passed, 8,14 s.
- Source confirma dos conversiones potencialmente destructivas: cursor metadata/agent deriva de datetime (microsegundos) y cursor_encode fija seis decimales; DB compara timestamps REAL sin ese redondeo. Se reproducirá exactitud/dirección del redondeo antes de corregir la frontera. Los veinte registros duplicados pueden pertenecer al mismo timestamp de un grupo, no a otra base. Context/archive preparados quedan en HOLD hasta nuevo freeze.

- Root reproduce la conversión numérica: REAL `1770000000.1234562` pasa a datetime/cursor `1770000000.123456`; REAL `1770000000.1234567` pasa a `1770000000.123457`. Ambas fronteras pierden precisión. Auditor independiente confirma mismo flujo en metadata/projection/agent_list y política de filas visibles; ya no se atribuye la paginación a contaminación. La compatibilidad de parseo de cursores previos se conserva, pero un cursor antiguo ya redondeado no puede recuperar la posición perdida.

- Reproducción sobre la fuente roja real: `/tmp/caos-spec016-cursor-sequence-full-red.log` muestra cursor `1791678542.085907`, frontera SQLite `1791678542.0859067`, delta `+2.384185791015625e-07`, veinte filas con timestamp igual y segunda página de 21 con solapamiento. Confirma el fallo de precisión sobre datos reales, no sólo un ejemplo numérico.

- TDD determinista limpio antes de corrección: **4 failed, 3 passed**, 5,49 s, exit 1, `/tmp/caos-spec016-cursor-precision-red.log` leído por root. Falla round-trip exacto, frontera metadata/AG-UI en ambas direcciones y agent_list (segunda página 100 en vez de 1). El decoder legacy se conserva y se prueba su parseo. Se autoriza corrección estrecha; no scan ilimitado, se conserva fallback de cursor sobre la última fila SQL ya evaluada cuando la página queda corta por fuentes inválidas. Auditor verifica que ese fallback no omite filas visibles no evaluadas dentro de snapshot estable.

## Freeze previo a migración PostgreSQL solicitada

- Regresión amplia de interacciones/AG-UI/API/event stream/integraciones: **553 passed**, ocho avisos, 198,81 s, exit 0; `/tmp/caos-spec016-cursor-precision-focused-green.log`. Boundaries finales **7 passed**, 6,84 s, exit 0; `/tmp/caos-spec016-cursor-precision-final-boundaries.log`. Mypy completo **406 archivos sin errores**, exit 0; `/tmp/caos-spec016-cursor-precision-mypy.log`. Root lee resultados; Black/isort de los tres archivos editados exit 0.
- Capability SHA256 `b2c6b9a5fc97cda3a5404cef20f2bcc9ba18aa3e05cbba3a3fe7c4faee64ae53`; service `28e1ec57273217def13dd226577bddf28c10d5e911a6477eb455cd68bc2bd1a6`; boundaries `483424254e2ef4ad7551fb3ed82c6c649540dc271ee36a8bd1b7c6f623ad253e`. Proyección/main mantienen su corrección previa.
- Petición explícita posterior «migremos todo a postgres» cambia la prioridad de trabajo. Se preservan estos cambios y evidencia; no se inicia otra suite completa SQLite ni archive/VM/Docker sobre fuentes obsoletas. La aceptación integral 016/AG-UI y Docker/host se compone con la migración de [spec021](../021-postgresql-persistence/spec.md). T077/T078 siguen pendientes; no se declara suite completa pasada.

## Continuación actual PostgreSQL — 2026-10-11

La instalación Docker real de la fuente actual pasa **1 caso**, 225,81 s:
instalación, repetición, cookie conservada tras reinicio, upgrade, rollback y
recuperación ante candidato fallido. La imagen está fijada por ID inmutable y
los recursos usan un perfil privado. Browser completo pasa **20 casos, cero
skips**, p95 DOM **1294 ms** para 20 fuentes/100 preguntas. Codex real completa
dos preguntas y respuestas hasta `agent_acknowledged`; no sustituye los límites
OpenCode/Hermes de SC-007.

Los resultados y fallos anteriores se conservan en
[validation.md de spec021](../021-postgresql-persistence/validation.md).
Importación Docker de los tres ámbitos, T020/T097 y suite backend completa
siguen pendientes; T074–T078 no se cierran con estos resultados parciales.

### Aceptación Docker PostgreSQL r5 — 2026-10-11

La imagen corregida r5 pasa instalación/reinstalación/upgrade/rollback (1 caso, 281,33 s) e importación SQLite de main/auth/peer seguida de instalación normal (1 caso, 90,47 s). Se verifican recibos antes de activar el planificador y se conservan los archivos fuente. Logs: `/home/felni/.cache/c16t/spec021-app-acceptance-r5/install.log` y `/home/felni/.cache/c16t/spec021-app-acceptance-r5/import.log`. La suite global PostgreSQL, T020/T097, grafo final y SC-007 completo siguen pendientes; no es cierre de la feature.
