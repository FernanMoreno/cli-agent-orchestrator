# PostgreSQL validation record

## Continuing acceptance (not final sign-off)

### Estado actual de ejecución — 2026-10-16, 00:27 UTC

- El fixture PostgreSQL R4 está sano tras su reanudación. La metadata
  reanudada registra el perfil `cao-spec021-local-postgres-v3`, loopback
  `127.0.0.1:50469` y SHA-256
  `44c57fa4086772496b36aebb3a8aaeaf6391c61ba6d757e04482225fc3274247`.
  Evidencia: `root-continuation-r1/service-r4/owned-service-r1/metadata-resumed-r1.json`.
- La aceptación nativa R8 contra el snapshot de 3.150 entradas con manifiesto
  `b38930121d118f81d0edae3ecc8a6bd2e7b75afdfe6d228025eafc68bd6c2cd6` terminó
  PASS. El recibo
  `root-continuation-r1/native-acceptance-r8-actual-r1/artifacts/native-acceptance-receipt.json`
  tiene SHA-256
  `1ba8726ee61ac78839ba4d7c1fb2b7d48294d1d892226cc58632a81bc050ebff`. Sus tres
  carriles informan: instalación Docker, **1 PASS** en 201,77 s; restauración
  PostgreSQL, **1 PASS**, 3 deseleccionadas, en 83,03 s; y cookie/browser Docker,
  **1 PASS** en 21,18 s. Los tres tuvieron exit 0, grupo de proceso vacío,
  ningún timeout ni señal de limpieza; el campo `not_run_after_failure_or_resource_hold`
  está vacío. El controlador exterior terminó con exit 0 y sus grupos
  controller/gate estaban ausentes al cierre. Esto acredita estos tres carriles,
  no un resultado global. Quedan 6,65 MB de artefactos temporales para forense;
  no se afirma limpieza completa de temporales.
- El global R8 sigue incompleto: su fase crítica serial terminó **3 PASS,
  0 fallos**, pero REST fue interrumpido por TERM (exit 143).
  `pytest-result-rest.json` quedó vacío; no hay cobertura combinada ni accounting
  final y no se atribuye un PASS global. El usuario informó de un cierre de Docker
  Desktop o reinicio de Windows/WSL, sin hora precisa ni confirmación de cuál
  acción ocurrió; no hay evidencia que atribuya a ello el TERM ni su causa.
  Evidencia R8: `backend-final-runner-r7-current-r8/artifacts/` (`runner-exit-full.json`
  SHA-256 `3b2d9a75c0746baab77b25cf5e9d38bcbb791d535291e06cc863fc921c8810d9`,
  `pytest-result-critical.json` SHA-256
  `7d0116302898bbeeae9c5dcc9ba6dfc1e28b8d10c8a72e3e65bd506b4f2b1d8e`,
  `collection-inventory.json` y `pytest-result-rest.json`).
- El intento anterior de build R8 ordinario sí pasó la calificación del builder
  para la imagen `sha256:214c89a6773b6b80d788f43b52ea13845d367cc62d66e54da6ca2d6a4afe7191`,
  con subcomando Docker exit 0, pero el emisor exterior de ese recibo terminó con
  `ValueError` y exit 2. Ese intento no inició aceptación nativa; la aceptación
  nativa descrita arriba es una ejecución posterior con su propio recibo.
- AG-UI R16 terminó con exit 125 por la carrera `process_task_inventory_raced`
  antes del recibo del journey; sus conteos siguen desconocidos. El diagnóstico de
  espera quedó en 0 bytes. El intento genérico R15 también terminó exit 125 antes
  del recibo interno por la carrera del inventario del supervisor; sus conteos
  siguen desconocidos. Evidencias:
  `agui-provider-restart-r16-resumed-pg-r1/runner-r16/runs/run-20261015T233850Z-0d97f3bfac45/receipt.json`
  (SHA-256 `3f6ab391fae4f533256f1f18425a4ac4897eb93dc0413938cacc4bb41d3f0020`) y
  `root-continuation-r1/spec023-live-launcher-r15/controller-receipts/f770e1191dd543a497eb1a5a57ea237b.json`
  (SHA-256 `391da438d1732b20ca2f374c346a4148ab7d750e694974e53e69dd088da8cb05`).
- El candidato de fuente R16 actual tiene **87 PASS offline**, un GO
  independiente y **87 PASS** también con caché fresca. El primer
  pase portable de 87 pruebas aún requiere revisión de modo/configuración; el
  candidato no está integrado ni entregado a main. La aceptación de runtime
  portable no se ha ejecutado. Esto no revoca los resultados offline anteriores,
  pero tampoco los convierte en cierre de runtime.
- El diagnóstico QEMU R1 está en NO-GO por las guardas de quoting/ownership que
  siguen pendientes de corrección privada; el caso completo de 91 pruebas no ha
  pasado. El R9 source-freeze sigue sin ejecutarse. La aceptación global, el SLO
  estricto `<1 s` y la revisión portable de runtime siguen abiertos. Los dos
  registros externos conservan `RECONCILE_REQUIRED` según su última lectura; este
  checkpoint no afirma una comprobación nueva.

### Integración y límites actuales — 2026-10-15, 12:15 UTC

- Main incorpora los candidatos aceptados de fixtures, preguntas/AG-UI, mensajes
  genéricos Spec023 y caché opcional QEMU. La regresión conjunta más reciente,
  desde main y sin `CAO_SPEC023_SOURCE_ROOT`, dio **291 PASS, 0 fallos/errores/skips**
  en 32,63 s. Evidencia: `root-continuation-r1/main-process-validation-r3/`.
  La comprobación separada de mensajes sin configuración privada dio 50 PASS;
  se conserva un intento fallido por mezclar aliases de capitalización del mismo
  checkout y el posterior comando coherente. No se relajaron guards de origen.
- El fixture de API conserva recursos hasta confirmar la salida del proceso
  líder y mantiene un propietario de cleanup reintentable al fallar startup.
  Eso no constituye prueba universal de salida de todos sus descendientes.
- Codex AG-UI R9 creó dos preguntas, conservó identidades con un reinicio real,
  observó tres snapshots redactados y recibió dos respuestas humanas. Faltó
  el ACK: **FAIL**, con cleanup completo y nueve guards correctos. No hay causa
  probada para la omisión del ACK; no se cuenta como aceptación del recorrido.
- Claude AG-UI R9 creó dos preguntas pero agotó el límite interno de 90 s antes
  de snapshots/reinicio/respuestas. El detector solo entendía eventos Codex.
  La corrección revisada de eventos Claude está integrada y cubierta offline;
  su preflight pasó sin conectar a modelos o PostgreSQL. La ejecución real
  posterior sigue pendiente. Modelo, prompt y límites permanecen iguales.
- OpenCode R7 localizó una regresión del harness al instalar su perfil; la
  corrección está en main. Las trazas R8 posteriores no produjeron recibo
  interno: el supervisor informó identidad inválida (errno desconocido) y,
  en otra traza con diagnóstico ampliado, desaparición de un hilo con ENOENT.
  Ambos cleanups siguen incompletos aunque no se encontraron marcadores de
  procesos propios visibles después; lecturas inaccesibles impiden probar
  ausencia total. No se atribuye el primer fallo a la causa del segundo.
- Spec023 está integrado: observer de dispatch de producción, inbox y AG-UI
  antes/después del reemplazo de API, terminales nativos conservados y sello
  positivo de auditoría. Sus pruebas offline no acreditan las tres ejecuciones
  reales de Codex/Claude/OpenCode, todavía pendientes.
- Docker instalación personal, backup/restore PostgreSQL y autenticación del
  navegador son tres gates separados: los 15 T019/T020 no los cubren. Los pases
  históricos de instalación usan otras imágenes; restore histórico falló.
  No existe PASS vigente de esos tres gates con la fuente/imagen final.
- QEMU conserva 91 casos y 45 min. Solo se prepararon prerrequisitos del host
  y una imagen cloud verificada fuera del gate; no se midió ahorro ni hay
  nuevo PASS de los 91. El guest y los límites siguen intactos. El ALL final
  también sigue pendiente: critical serial de tres casos y resto con cuatro
  workers, cobertura 87 % sin branch, sin excluir E2E por marcador.
- En la última auditoría anotada, dos jobs externos CAO estaban en estado
  `RECONCILE_REQUIRED`. No hay una comprobación posterior registrada, por lo
  que su resolución actual no está verificada. Las observaciones del checkout
  actual no prueban qué código ejecutó el CAO 2.5 instalado en esos errores
  históricos ni autorizan declarar los jobs cerrados.


### Últimos resultados comprobados — 2026-10-15, 10:55 UTC

Estos resultados usan el snapshot R2 de esta continuación, manifiesto SHA-256
`e521cfb27af9fe20a461ac9da319ffc1dd7a96641ac5a452ee7f5871e6ef4602`,
con 3.137 entradas. Se conservan los fallos históricos de las secciones siguientes.

- Docker T019/T020 completo: **15 PASS, cero fallos/errores/skips**, 282,129 s,
  exit 0 y sin timeout; guards de fuente antes/después correctos. Se conservaron
  los límites de 5 s, 15 s y 45 min y las aserciones de teardown de cada caso.
  No demuestra la causa de los nueve fallos históricos ni una limpieza global
  de recursos ajenos. Evidencia privada: `t019-t020-full15-executor-r9-prep-r1/`
  `executor-r9/runs/20261015T094625Z-75443/artifacts/`.
  SHA-256 de `execution-result.json`:
  `37cdc46d630223f18aeb499c713673a5c955c9265815ce1020123791c1358c8d`;
  JUnit: `db5aea0614a6b3d9355a4cc8831b1e9dbfc86307edfdb8af29bed822f03e3a22`.
- Rendimiento: los **tres procesos fríos** ejecutaron una vez el caso original
  con su aserción estricta `elapsed < 1.0`; los tres pytest terminaron en PASS.
  El controlador original falló al buscar un nombre de perfil distinto del
  producido. Una recuperación separada verificó los artefactos originales,
  orígenes, cobertura, PG, HOME y guards de fuente; raíz y revisión independiente
  aceptaron solo ese gate acotado. No se alteró el recibo fallido ni se repitieron
  las mediciones. El tiempo interno exacto no quedó registrado; los tiempos de
  fase pytest no se presentan como esa medida. Evidencia: `nfr-r7-cold-cohort-r3/`
  y `root-continuation-r1/nfr3-root-review.json` (SHA-256
  `011498644f7d7eacab9bf842de4083c34407d465514b8e861f5baca3c45ad28f`).
- QEMU: el límite exterior original de **45 min** venció (exit 124; interior
  130). Ocho grupos terminaron con **80 PASS de 91 previstos**, sin resúmenes
  observados de fallos/errores/skips; el noveno grupo empezó. No se cuenta como
  aceptación completa. El recibo preserva una base marcador porque no pudo
  verificar la vida del contenedor. Después, su CID exacto devolvió not-found y
  no se observaron procesos QEMU/pytest o con marcador exacto del run; eso no
  sustituye la limpieza incompleta registrada. Recibo privado:
  `qemu91-pg-v3-r3/runs/run-06daea2cba10db3a-6nwrr09h/run-receipt.json`, SHA-256
  `2b0f4547cb6003c49592257efb46ad6a9334b50f0c57c3d80a313bddea749af0`.

OpenCode R6 terminó bajo supervisión con limpieza completa, cero grupos propios
remanentes y guards de fuente/auth correctos, pero devolvió `harness_error` /
`ValueError` en la fase amplia `version_probe`, antes de crear el terminal nativo.
No hay captura porque la fuente aún no estaba configurada. No se atribuye el
fallo al binario ni se afirma reproducción del timeout externo de 120 s. Recibo
exterior: `opencode-native-mini-r14-trace-capture-r6/launcher/controller-receipts/`
`713cd82ef14c4965b0c4b4babf87e5a9.json`.

Codex AG-UI R7 falló a los 30,89 s con exit 125 y sin recibo del journey. Su
diagnóstico acotado identifica `file_not_found` / `process_task_inventory_raced`
en `owned_tree_scan` / `children_read`. Se reprodujo offline la desaparición de
un TID durante lectura, conservando la identidad del proceso propietario. Los
guards están correctos, pero el recibo marca limpieza incompleta y árbol no
reapado; cero grupos observados no prueban ausencia de descendientes. Una
inspección posterior no encontró procesos con marcadores positivos del run y
no envió señales. La corrección privada R8 sigue bajo revisión; no es un port
entregado. Recibo: `agui-provider-restart-r7/runner-r7/runs/`
`run-20261015T105052Z-13c07ea44bc3/receipt.json`.

El operador confirmó que el alcance incluye mensajes genéricos entre agentes
mediante `send_message`. Spec 023 y su recorrido completo A→B siguen como
candidato privado, con revisión ECC de Python y seguridad. No se cuentan como
integrados ni como aceptación real. La suite global actual, QEMU completo,
Codex/Claude/OpenCode con AG-UI y preguntas/mensajes tras reinicio, y los dos
incidentes del gestor externo siguen abiertos. No hay sign-off global.

### Continuación — recuperación del entorno, 2026-10-15

La nueva sesión confirmó `main`, HEAD `02309f3f1e8cd1cf3b66dc92ca4d9e6c21ccf2db`,
los 1.898 registros de estado previos y los hashes aceptados de batching de
memoria, harness R14 y preparación pidfd. No se limpiaron cambios existentes.

El servicio PostgreSQL histórico `f84d3eb5…2201c` estaba detenido con exit 255,
sin marca de OOM. Su arranque falló porque el origen del bind del secreto
temporal ya era un directorio. No se afirma recuperación de sus datos tmpfs ni
continuidad de resultados históricos. Se conservaron el contenedor y la
metadata histórica SHA-256
`200b53e7bd46f495ed1246cab792eb0e6909bbec3cc1ad962a05b99f2a14f1d1`.

Se provisionó un servicio de pruebas nuevo mediante la misma fixture fijada:
PostgreSQL 18.6, perfil `cao-spec021-local-postgres-v3`, mismos límites y
parámetros verificados. Su metadata tiene SHA-256
`b0fe3027a0197dca0254b5b6005d47e22ff582dd24f4e0d7cc300ee169851906`;
los recibos privados están en
`root-continuation-r1/service-r3/owned-service-r1/artifacts/` bajo la raíz de
evidencia de 2026-10-15. Los runners deben vincular explícitamente este servicio
antes de nuevas pruebas. Esta recuperación no cierra Docker, QEMU, proveedores,
AG-UI/reinicio, rendimiento ni la suite global.

#### Ports y verificaciones focalizadas de esta continuación

Se integraron en `main` las 13 rutas de fixtures/E2E del candidato R9 con
verificación de cada hash previo y posterior. El contrato de puerto explícito
falló contra R8 y pasó con R9; el caso de startup sigue lanzando un proceso
real de API con `CAO_API_PORT=not-an-int`, separado del sentinel simulado.
Se integraron también cuatro archivos del harness AG-UI/reinicio y nueve
documentos de Spec 022. El common journey es la unión SHA-256
`813f7887ec862da116e37dcdb0732df560357533dcad82807226ad0bd67d8612`;
los otros tres archivos R14 conservan sus bytes aceptados.

Desde `main`, con ocho plugins explícitos, shim temprano de HOME y comprobación
de orígenes, la selección offline obtuvo **200 PASS, 0 FAIL/ERROR/SKIP**
(JUnit SHA-256
`56865f1a11484911cddc330d7a38449beda1c044a6f21be94f063486cd076444`).
El caso real de startup inválido obtuvo **1 PASS, 0 FAIL/ERROR/SKIP** contra el
nuevo PostgreSQL; no invocó modelos. Composición pasó sus cinco contratos,
cero rotos. Son comprobaciones focalizadas, no la suite global ni aceptación
de proveedores. Evidencias privadas: `root-continuation-r1/main-port-validation/`.

La única traza real OpenCode de esta continuación falló: **85,82 s, cero
preguntas/respuestas/ACK**, MCP conectado y listo, TUI idle y turn sequence 1.
El supervisor terminó sin timeout exterior ni grupos propios activos, pero
la limpieza interna quedó incompleta y la captura de stderr no se produjo
(`process_not_stopped`). No se hicieron reenvíos ni pulsaciones adicionales.
El recibo exterior es `launcher/controller-receipts/f89cdf58134143378f6825d10c97297b.json`
del paquete privado `opencode-native-mini-r14-trace-capture-r1`. No constituye
aceptación; se conserva el fallo y sigue pendiente su diagnóstico.

El primer recorrido Codex con AG-UI/reinicio de esta continuación falló durante
`initialize` en 3,135 s con `harness_error`, antes de informar versión, preguntas
o reinicio. El supervisor confirmó limpieza completa, cero grupos propios
remanentes y todos los guards de fuente, binarios y autenticación conservados.
Se conserva `run-20261015T084746Z-8f46b0bdbfd2/receipt.json` en el runner privado
AG-UI R3. No se cuenta como aceptación ni se atribuye el fallo al proveedor.

En la auditoría de solo lectura registrada en este corte, los dos registros
comunicados por el operador quedaron `RECONCILE_REQUIRED` en el gestor externo:
delegación `nd-45a1ef0879624299` / job `j-229e4864b1da4868`
(`run-step`, excepción de comparación no ASCII) y `nd-ca865011567f4d34` /
`j-e6a8fa4568904245` (`handoff`, inicialización OpenCode 120 s). Se comprobaron
en modo de solo lectura, sin modificar jobs. Ese gestor ejecuta CAO instalado
2.5.0, revisión `c282ce5`, distinta del checkout actual. Su mapper HTTP conserva
el texto de la excepción pero no un traceback; no se pudo demostrar el callsite
interno de la comparación Unicode. Reproducir el TypeError de `compare_digest`
aisladamente no demuestra su causa en ese `run-step`. No consta una comprobación posterior, así que la resolución actual de estos
incidentes no está verificada. Tampoco se ha auditado todo el historial externo.

#### Evidencias posteriores: harness y límites aún abiertos

El harness AG-UI reutilizaba `root/tmp` después de que `_base_cli_env` lo creara.
Se reprodujo `FileExistsError` y se integró una preparación de directorios que
verifica tipo, propietario y permisos 0700. La selección offline desde `main`
obtuvo **206 PASS, cero fallos/errores/skips**, con orígenes comprobados y HOME
privado; JUnit SHA-256
`4e129cb53a0c2c43593b23fe57510cb7773bbe68dc25ed0badeb6672d2cda8a4`.
Los tres archivos nativos R14 distintos del common siguen intactos.

El recorrido Codex AG-UI R5 creó **dos preguntas reales** y conservó sus
identificadores después de **un reinicio de API**; tres snapshots pasaron la
comprobación de redacción. Falló después con `human_answer_http_403`, sin
respuestas ni ACK. Se reprodujo offline la frontera: el source se registraba
con un principal JWT sintético y la API real local respondía con el principal
`local_operator`. `InteractionPolicy.require_owner` rechaza correctamente esa
diferencia. Es un defecto de preparación del harness, no una razón para
relajar la política de autorización. Recibo privado R5:
`run-20261015T092657Z-b8b596e68455/receipt.json`; limpieza y guards completos.
No constituye aceptación completa.

El ownerfix se integró en `main`: solo el modo AG-UI usa el factory verificado
`local_operator_principal`; el modo habitual conserva su owner anterior y una
configuración IdP provoca rechazo, sin fallback. Una revisión independiente
contrastó registro de source, dependencia de identidad HTTP, scopes y política
de owner. La selección raíz obtuvo **207 PASS**, cero fallos/errores/skips,
y composición conservó cinco contratos, cero rotos. El primer intento de esa
verificación falló por comparar prefijos de rutas con mayúsculas distintas en
DriveFs; usando la ruta canónica pasó sin cambiar aserciones ni código. Ambos
logs se conservan en `root-continuation-r1/main-owner-validation-r6/`.

El intento Codex AG-UI R6 terminó en 17,081 s por
`owned_process_inventory_incomplete` del supervisor (exit 125), sin recibo
interno; cero grupos remanentes observados no prueba limpieza completa.
Los guards de fuente/auth/binarios se mantuvieron. Se conserva
`run-20261015T093920Z-db3151821358/receipt.json`; no se atribuye a Unicode ni
se cuenta como aceptación. Las llamadas siguientes se detienen hasta revisar
el supervisor y su diagnóstico tipado; Claude combinado sigue pendiente.

Se reprodujo también, antes de cambiar `main`, un defecto portable separado:
el lector `_process_info` nativo OpenCode decodificaba el nombre completo del
proceso como ASCII antes de extraer identidad numérica. Se integró lectura
binaria con numerales ASCII estrictos y tres regresiones (nombre no ASCII con
paréntesis, numerales inválidos y salida durante lectura). Solo cambiaron ese
harness y su test; no se portaron rutas de captura privadas ni controladores
operativos. La selección raíz actual obtuvo **210 PASS, cero fallos/errores/skips**
y composición **5 contratos conservados, cero rotos**. Evidencia:
`root-continuation-r1/main-process-validation-r1/`. Este port sustituye los
hashes previos de los dos archivos R14 afectados; el common ownerfix y el test
de upgrade permanecen intactos. No corrige por sí mismo el incidente externo
Claude ni demuestra la causa histórica de los fallos del supervisor.

El diagnóstico real T019 aislado pasó, conservando límites 5 s/15 s:
setup 2849 ms, call 9028 ms, teardown 241 ms; roundtrip worker 446 ms,
Docker info 1049 ms, 154 eventos sin pérdidas. Evidencia privada
`t019-managed-mcp-diagnostic-r3/runs/20261015T085407Z-30e3cd88/artifacts/`.
No reprodujo ni explica los nueve fallos históricos; la selección completa
Docker de quince casos sigue pendiente.

Se conservan además las trazas OpenCode R3 y R4 fallidas. R3 tuvo cero
preguntas/ACK y no pudo capturar por un directorio 0755. R4 falló en el
supervisor con `process_identity_invalid`, sin recibo interno completo;
las llamadas al modelo de ese intento son desconocidas. Una reproducción
fake demostró que un `comm`/`Name` Unicode rompe la decodificación ASCII del
lector de `/proc`, pero no prueba la causa histórica porque no se retuvo
la identidad que falló. No se cuenta ninguno de estos intentos como aceptación.
No hay nuevas llamadas OpenCode hasta corregir y revisar ese supervisor.

### Estado actual — 2026-10-12

#### Actualización posterior a r9

La suite global r9 **no terminó**: 17.198 casos recolectados, **12.075 PASS,
5 FAIL y 36 SKIP**, exit 3 por un INTERNALERROR al 70 %. No generó cobertura
JSON/XML final. Se conservan sus recibos y JUnit en `backend-r9/artifacts/` bajo
el directorio privado de evidencia de esta fecha. Una prueba de Vault sustituía
`os.open` globalmente y rechazaba tanto la lectura de credenciales PostgreSQL
como la escritura del diagnóstico del runner. La reproducción focalizada falló
en esa lectura. La fixture corregida rechaza exclusivamente el FIFO examinado;
el módulo completo obtuvo **23 PASS**, 19,28 s. El siguiente runner registra
fallos mediante descriptores preabiertos y sus comprobaciones privadas demuestran
que el mismo monkeypatch ya no provoca un INTERNALERROR.

T048 obtuvo **52 PASS** combinando proyección y fence de efectos. T049 corrigió
una colisión real de caché entre stores PostgreSQL con el mismo directorio de
artefactos: la clave incluye ahora `DatabaseTarget.identity`. Su regresión fue
RED antes del cambio y el módulo de política de memoria obtuvo **25 PASS**.
Estos cambios requieren renovar el snapshot y los gates globales.

La aceptación Linux T097 sí ejecutó una partición real: **5 PASS, 3 FAIL,
cero omisiones**. Dos fallos eran binds SQLite `?` en las pruebas y ya se
portaron a `%s`; el tercero fue un timeout de ACK del proceso hijo cuya causa
sigue en investigación. No se amplió el límite de producción. Las particiones
restantes y el cierre completo T097 siguen pendientes.

T079 de Spec016 corrigió los walks redundantes de ciclos y las consultas N+1
de metadata, y agrupó las escrituras en la misma transacción. La prueba nativa
controlada obtuvo **20/20 registros y 100/100 preguntas visibles**: p95 de
**789,4 ms desde el commit confirmado hasta la lectura de metadata**. Conserva
también el diagnóstico de **3312,8 ms desde el inicio de la solicitud hasta esa
lectura**. SC-001 empieza en el registro u observación confirmada; las dos
mediciones no se sustituyen ni se confunden. Las regresiones actualizadas de
ciclos/Work obtuvieron **10 PASS**, las de eventos **4 PASS** y los guards
AG-UI/nativos/Work/espera **34 PASS**. La aceptación final en navegador y la
suite global aún deben repetirse.

La continuación Linux r10 ejecutó **8 PASS** en host, **61 PASS/1 FAIL/1 SKIP**
en scratch, **1 PASS** en Landlock, **2 PASS** en nodos de dispositivo y
**3 PASS** en limpieza/casefold. El fallo scratch fue un timeout de ACK; la
omisión requiere un broker con UID 1000 y el huésped usaba UID 999. SIGKILL y
FD theft fueron deseleccionados por los addopts heredados, por lo que no se
cuentan como ejecutados. El script del proyecto ya limpia los addopts y
selecciona esos marcadores explícitamente; su contrato offline obtuvo **3 PASS**.
El diagnóstico focal y las repeticiones pendientes no están aprobados aún.

#### Actualización posterior a r10 — ejecución interrumpida

La suite global r10 no terminó y no constituye aceptación completa. El recibo
corregido registra la interrupción al **86 %**, con **20 fallos de fase call y
5 errores de setup** capturados antes de detenerse (**25 registros del hook**;
ninguno se atribuyó a la propia interrupción). Se detuvo el grupo de procesos
propio al confirmar escrituras bajo el HOME real; el grupo ignoró SIGINT y se
detuvo con SIGTERM. El recibo corregido reemplaza el conteo preliminar de 18
fallos del primer recibo.

No hay resumen final de pytest, JUnit ni cobertura JSON/XML. El log parcial es
`/home/felni/.cache/caos-spec021-evidence-20261012/backend-r10/artifacts/runner-full.log`;
el manifiesto del snapshot tiene SHA-256
`6c7951fb9efce50d24c56b5cd1f219eb48565ca4e69a58970f64919be7d33657`. Los
recibos `runner-interruption-receipt-correction.json` y
`runner-interruption-receipt.json` conservan la interrupción; el primero es la
fuente del conteo corregido. R10 no acredita la suite global ni cobertura.

Los párrafos siguientes conservan la cronología de ejecuciones anteriores;
sus resultados focalizados no sustituyen una suite final completa.

El cierre global sigue **pendiente**. Los snapshots registrados acreditan la suite
completa de clientes (**654 PASS, una omisión de API libtmux no disponible**),
la batería de navegador (**20 PASS, cero omisiones; p95 1.335 ms**) y la
instalación Docker (**un PASS, 301,38 s**). Once regresiones nativas verifican
la actualización de scopes AG-UI y el rechazo de drift del esquema de
continuación; su registro durable es `audit/green/boundary-green-r7.log` bajo
el directorio privado de evidencia indicado abajo. Los hashes de producción
que acreditan estas pruebas son API `0dcb51b8…db4d8`, driver de continuación
`e76963d2…f5c0a1` y Core `6827d177…25a39`.

La suite global r7 se interrumpió deliberadamente después de **818 PASS, un
fallo y tres omisiones**, para corregir la longitud del TMPDIR de su runner:
un socket de prueba Kimi superaba el límite AF_UNIX. No es una suite completa.
La reproducción de ese caso con temporales cortos pasó. La ejecución r8, con
seis workers y snapshot `4aff55a8…9ff`, se detuvo al 63 % cuando el tmpfs de
PostgreSQL, de 1 GiB, cayó por debajo del guard de 128 MiB. Su resumen parcial
es **11.008 PASS, 18 FAIL, 18 ERROR y 35 SKIP**, exit 2 por interrupción; no
acredita una suite completa ni cobertura. Se conservan el JUnit parcial y las
trazas en `backend-r8/`. Se prepara un servicio desechable de mayor capacidad
antes de repetir la suite canónica de CI sobre un snapshot final.

Las reproducciones nativas posteriores identificaron la reserva concurrente
del mismo intento de workflow (T045), la clasificación incorrecta del rechazo
de integridad durante resume YAML (T046) y el fallo de conexión del bloqueo de
limpieza (T047). Las pruebas de esos módulos obtuvieron respectivamente
**48 PASS**, **92 PASS** y **44 PASS**. La carrera del replay de una proyección
Work (T048) sigue en corrección. Estos cambios posteriores requieren renovar
los gates globales y la aceptación de la imagen final.

La importación Docker r2 obtuvo **1 PASS, 103,14 s**, con fuentes inmutables y
recibos verificados; comparte imagen con la instalación r2 indicada arriba y
precede T045–T048. La aceptación T097 v5b/v6/v7 no ejecutó las particiones de
pruebas: fallaron en setup, permisos o digest. Dos builds de Bubblewrap con las
rutas canónicas reproducen el digest `18a513db…6e4c39`; se está comparando su
perfil con los artefactos aprobados. Se mantiene la allowlist. El cierre
T097/T020, la cobertura global y la aceptación de la imagen final están
pendientes.

La ejecución completa r6 terminó de forma inesperada cuando el host de la
herramienta recibió `SIGTERM`, aproximadamente al **26 %**. Los últimos marcadores
observados fueron **40 fallos, 13 errores y tres omisiones**; no son el resultado
de una suite terminada ni identifican por sí solos sus causas. No se obtuvo
resumen final, JUnit, cobertura JSON/XML ni inventario final de omisiones. La
causa del cierre del host no se ha establecido.

Tras el cambio de entorno, el antiguo servicio PostgreSQL desechable ya no existe
y la ruta privada `/home/felni/.cache/c16t/` tampoco está disponible. Las entradas
anteriores conservan la cronología de resultados observados, pero las rutas bajo
ese directorio no deben presentarse como artefactos actualmente accesibles.
La nueva evidencia se guarda en un directorio real de ext4 con permisos privados:
`/home/felni/.cache/caos-spec021-evidence-20261012/`.

Se reprodujeron y corrigieron las fixtures Beads que invocaban DDL SQLite sobre
PostgreSQL, los binds SQL antiguos de las pruebas de continuación y el contrato
de cinco segundos del journal. Los marcadores de r6 no se atribuyen a esos
defectos sin una reproducción con traza. Los nuevos runners se lanzan de forma
independiente de la sesión de la herramienta, con diagnósticos de fallos emitidos
durante la ejecución y una salida final persistida. La consolidación adicional
de ejemplos/worktrees en `main` se
registra en Spec008; Spec021 y Spec016 siguen sin cierre global.

### Vault: rollback real de recibos — 2026-10-12

Las dos pruebas de fallos de recibos de migración todavía creaban triggers
SQLite sobre el motor PostgreSQL. Se sustituyó esa fixture por inyección de un
error nativo PostgreSQL `23514` exclusivamente en la escritura ORM seleccionada,
sin modificar el catálogo verificado. Se comprueba que el error de inserción
revierte la supersesión del enlace y que el error de rollback conserva el enlace
supersedido y el recibo activo. No se cambió código de producción.

- Reproducción focalizada corregida: **2 PASS**, 24,39 s.
- Módulo completo sobre PostgreSQL r9 de 3 GiB: **59 PASS**, dos warnings
  existentes de Pydantic, 239,16 s. Log `vault-native-fixtures/full-green.log`.
- Archivo `test/services/vault/test_vault_migration_lifecycle.py`, SHA-256
  `552fa566eb72b9e45c50429d23cac89a5e4c45fdeb3a2b683e49a6a760ada92a`.

Los logs están bajo el directorio privado de evidencia de esta fecha. Este
resultado focalizado no sustituye la suite global ni la cobertura final.

### Import-only receipt stability and explicit scheduler activation (r5)

- The r4 installation result (one passed, 225.81 s) predates the packaged
  scheduler fix below and remains historical evidence. Import retry r2 reached
  the actual managed-network PostgreSQL verifier and failed because post-import
  scheduler seeding changed mapped rows after their receipt was verified:
  `/home/felni/.cache/c16t/pa-r4/sqlite-import-r2.log`.
- Import-only now verifies every receipt again immediately before publishing
  configuration, without changing mapped scheduler policy. Normal installation
  explicitly configures the scheduler after verifying all stores, preserving an
  existing operator policy. Receipt verification remains strict.
- Fresh targeted native regressions: **two passed**, 10.09 s, covering explicit
  scheduler activation/idempotency and rejection of mapped-row tampering;
  `/home/felni/.cache/c16t/pg-r5-proof/native.log`. Fresh selected installer
  regressions: **24 passed, 32 deselected**, 1.19 s;
  `/home/felni/.cache/c16t/off-r5-proof/offline.log`.
- The fresh r5 application image built successfully with a newly packed context:
  `sha256:24faedeb973856ab76d0579d4bdfa007e667d34c0b38c977775a7b3cd9088eb4`.
  A separate network-disabled container confirmed packaged
  `personal_deployment.py` SHA-256
  `c24fd516e7cddc9ad725a13ea1f28f591fab4465d08715b17f51cbc4b7d12d00`.
  Build log: `/home/felni/.cache/c16t/spec021-app-image-build-r5.log`;
  source/context/image provenance:
  `/home/felni/.cache/c16t/personal-import-acceptance-20261011-r5/application-image.json`.
  Full installation acceptance on this image passed **one test**, 281.33 s,
  including reinstall, session continuity, upgrade and rollback; retained log:
  `/home/felni/.cache/c16t/spec021-app-acceptance-r5/install.log`.
  Full stopped-SQLite import acceptance passed **one test**, 90.47 s:
  `/home/felni/.cache/c16t/spec021-app-acceptance-r5/import.log`.
  The actual installer CLI imported main/auth/peer, verified their receipts
  before activation, preserved source bytes and enabled the scheduler during
  subsequent normal installation. Both selectors cleaned their owned
  containers/networks. Complete backend and host gates remain pending.

### T020 actual container-start failure (v2)

- The actual T019/T020 Docker run failed: **14 failed**, five warnings,
  163.67 s, exit 1, with no skips/deselections in its final summary.
  Log: `/home/felni/.cache/c16-evidence-archive/t097-local/t020-actual-20261011T101748Z.log`.
- The pinned rootfs build succeeded, but Docker container start returned an OCI
  runtime error before the supervisor emitted its MCP frame. The protocol
  reader exposed only the first 96 bytes of the daemon error; its precise cause
  is under investigation. The run used default `/tmp` test/build paths because
  the private v2 wrapper did not set TMPDIR/basetemp or CAO_HOME/XDG. This is
  recorded as a harness isolation gap, not yet the proven OCI root cause.
- Failed Work containers were removed by suite cleanup. A focused, isolated
  reproduction retained the complete daemon startup error:
  `exec: "/cao-work-supervisor": permission denied`, exit 126.
  `/home/felni/.cache/c16-evidence-archive/t097-local/t020-diagnosis-rerun-2/docker-start.stderr`.
  A separate export of the exact image proves the supervisor was mode **0700**,
  UID/GID **1000:1000**; the capability-restricted launcher cannot execute that
  owner-only file. Root independently read the tar metadata. Evidence:
  `/home/felni/.cache/c16-evidence-archive/t097-local/t020-diagnosis-rerun-2/rootfs-export.tar`.
  The test rootfs Dockerfile will explicitly copy it with mode **0555**, followed
  by a fresh build under umask 077 and focused/full acceptance reruns.
  T097 and overall host acceptance remain pending; the failed run is not green.
  The rebuilt v3 image export confirms mode 0555. The focused Docker case
  subsequently passed (**one passed**, 15.97 s) after porting 12 native SQL test
  statements from SQLite placeholders to psycopg placeholders.
  Full v3 repeat completed with **10 passed, four failed, zero skipped**,
  249.47 s; `/home/felni/.cache/c16-evidence-archive/t097-local/t020-full-v3/full-t020.log`.
  Three failures reach workflow recovery's read-only snapshot and
  `check_effect()`'s writable row locks. The remaining sibling-isolation test
  loses the blocked victim container; its first incorrect boundary remains
  under investigation. The read-only recovery fix must preserve row locks for
  actual writable admission, and all original security assertions remain.
- Native workflow recovery regression reproduced the read-only lock bug:
  **one failed**, 9.43 s, `tmp/workflow-readonly-audit-red.log` in the validation
  snapshot. The fix derives the lock clause from the native transaction mode;
  writable admission retains both row locks. Recovery, stale-generation and
  actual two-connection blocking checks passed **three tests**, 23.05 s;
  `/home/felni/.cache/caos-spec021-validation/tmp/workflow-readonly-audit-green.log`.
- The sibling-isolation diagnostic captured a 5.155 s response timeout before
  concurrent inspection; its artificial upstream barrier exceeded production's
  five-second bridge budget. That deliberately blocked test now uses a local
  60 s monkeypatch; production keeps five seconds and all original PID/FD/canary
  assertions remain. The final combined T019/T020 run passed **14 tests, zero
  skipped**, five warnings, 233.41 s:
  `/home/felni/.cache/c16-evidence-archive/t097-local/t020-full-v3-testtimeout-r1/full-t020.log`.
- The private build PATH lacked rg, so its conditional ELF check was not passed.
  Independent verification of the exact image's exported supervisor confirms
  mode 0555, UID/GID 1000:1000, expected SHA and no PT_INTERP; Root ran readelf
  separately. The separately compiled worker matches the actual run's SHA and
  also has no PT_INTERP. Private proofs are under that run's `verification/`,
  including `root-verified-supervisor.json` and `t020-verification.json`.
- Complete sequential backend r6 was launched, using pinned Python
  3.12.13 and the isolated PostgreSQL service. Its guard verified **1,813 source
  files**, eleven required hashes and zero copied bytecode/cache directories.
  Retained source manifest:
  `/home/felni/.cache/c16t/spec021-r6-backend-source-manifest-20261011.json`.
  The intended logs, JUnit, coverage and skip inventory paths were under
  `/home/felni/.cache/c16t/t021-r6/`; see the interruption and artifact-availability
  update above. No source edits were made during this run.
  T097 guest execution has not started: the latest available-memory reading was
  1,683,484 KiB, below its 2 GiB prerequisite. Current-source application-image
  acceptance must also be refreshed after the workflow driver fix.

### Current CI and interrupted global run

- The complete CI formatting/type commands passed against the isolated source:
  Black reported **1,284 files unchanged**, isort exited zero, and configured
  mypy reported **no issues in 423 source files**. Logs:
  `/tmp/caos-spec021-final-black-src-test-20261011.log`,
  `/tmp/caos-spec021-final-isort-src-test-20261011.log`, and
  `/tmp/caos-spec021-final-mypy-src-20261011.log`.
  The earlier `ledger-final` formatting logs record failures, not this result.
  Formatting was staged privately and checked for non-import AST differences
  and import binding collisions before atomic application; review artifacts:
  `/home/felni/.cache/c16t/ci-format-20261011/ast-review.json`.
- A fresh composition check conserved all **five contracts, zero broken**:
  `/tmp/caos-spec021-composition-frozen-source-20261011.log`.
  Subsequent behavior changes require fresh checks before final sign-off.
- The sequential global PostgreSQL run was intentionally interrupted at the
  5% progress marker after source review found an unverified routine boundary
  in schema capture. Its JUnit records **989 completed cases, no failures or
  errors**, but the runner exited **1** on interruption and produced no
  coverage reports. The interrupted log also contains a pytest temporary-path
  teardown `KeyError`; the JUnit count describes completed cases only.
  This is **not complete backend acceptance**. Original
  log/JUnit remain at `/tmp/caos-spec021-ledger-final-20261011-backend.*`;
  the next execution uses separate `backend-r2` artifacts.
- The recovery/import/personal batch completed with **233 passed, three
  failed, one teardown error** in 504.29s. Two restore tests passed a fixture
  tuple instead of its capture source; the missing auth schema leaked a
  PostgreSQL namespace lookup exception; one test left a database role
  dependency behind. After fixes, the four affected cases passed in a fresh
  native run: **four passed, two warnings**, 22.79s. Logs:
  `/home/felni/.cache/c16t/spec021-recovery-acceptance-20261011-02/recovery-import-personal-final.pytest.log`
  and `recovery-import-personal-focused-green.pytest.log` in the same directory.
  The corrected complete suite remains pending.

### Import receipts and latest catalog regressions

- Three native composition cases reproduced the import-receipt boundary:
  receipt metadata was outside the registered schema, recovery rejected it,
  and the runtime could not read it. Evidence:
  `/home/felni/.cache/c16t/import-recovery-composition-20261011/red-boundary.log`
  (**three failed**, 7.29s). Receipts now have a canonical migration in each
  scope, remain outside legacy application-data mapping, and are included in
  the reviewed physical schema and recovery inventory. Import verification
  performs no implicit DDL repair.
- Import and receipt/recovery composition passed together: **six passed**,
  16.94s, including real capture and blocked restore with historical source
  provenance preserved. Evidence:
  `/home/felni/.cache/caos-spec021-validation/tmp/import-receipts-focused-20261011.log`.
  A separate all-scope registry test passed (**one passed**, 2.66s), verifying
  SELECT-only runtime privileges and denied writes after subsequent migration:
  `/home/felni/.cache/caos-spec021-audit-receipts-20261011/tmp/receipt-registry.log`.
- Worker reset originally restored broad DML grants on receipts. Its native
  regression failed with effective INSERT enabled (**one failed**, 2.99s),
  then passed for all three scopes after the protected-table grant loop used
  the canonical receipt table (**one passed**, 2.88s). Evidence:
  `/home/felni/.cache/caos-spec021-audit-receipts-20261011/tmp/receipt-reset-red.log`
  and `receipt-reset-green.log` in that directory.
- Catalog and registry regressions passed together: **nine passed**, 5.41s,
  covering routine bodies, rewrite rules, generated columns and RLS:
  `/home/felni/.cache/caos-spec021-audit-receipts-20261011/tmp/catalog-registry-final.log`.
- Auxiliary recovery cases first reproduced comment disclosure and admission
  of unreviewed enum types and extended statistics (**three failed**, 13.16s),
  then passed after correction (**three passed**, 10.09s). Evidence:
  `/home/felni/.cache/caos-spec021-validation/tmp/pg-aux-red-20261011b.log`
  and `pg-aux-green-20261011.log` in the same directory. Production dump and
  backup commands now exclude comments. These tests cover the named object
  classes; they do not certify every PostgreSQL catalog object class.
- Worker-image lifecycle and installer argument regressions passed offline:
  **14 passed**, 1.08s. Evidence:
  `/home/felni/.cache/caos-spec021-validation/tmp/worker-image-offline-final-20261011.log`.
  Actual Docker lifecycle acceptance remains pending.
- A bounded two-connection PostgreSQL probe reproduced a catalog snapshot
  mismatch: the repeatable-read reader retained old `pg_proc.prosrc` while
  `pg_get_functiondef()` returned the concurrently committed new body.
  Evidence (**one failed**, 1.07s):
  `/home/felni/.cache/caos-spec021-audit-receipts-20261011/tmp/function-snapshot-probe.log`.
  The exact routine-body boundary was corrected and rechecked below; this
  does not establish MVCC consistency for every catalog deparser.
- The actual post-inventory function replacement case passed: pg_dump used
  the original exported-snapshot function body (**one passed**, 6.18s).
  Its filename contains `red` but records a passing probe:
  `/home/felni/.cache/caos-spec021-validation/tmp/routine-syscache-red-20261011.log`.
  The stronger pre-snapshot mutation/post-export reset case reproduced the
  verification failure (**one failed**, 5.25s, DID NOT RAISE):
  `/home/felni/.cache/caos-spec021-validation/tmp/routine-exported-snapshot-red-20261011.log`.
  Read-only rendering of that retained 641,647-byte archive confirmed the
  synthetic credential was present and the original routine body absent.
  This separates the catalog-verification bug from pg_dump snapshot behavior.
  Read-only archive proof (digest, size and booleans only):
  `/home/felni/.cache/caos-spec021-validation/tmp/routine-exported-snapshot-red-archive-proof.json`.
- The routine catalog now fingerprints MVCC-visible `pg_proc` fields with
  normalized language/type names and no physical OIDs. Triggers reuse those
  same records. Unsupported SQL-standard bodies, argument defaults, support
  routines and aggregates fail closed. The direct reader regression and actual
  exported-snapshot recovery rejection passed together: **two passed**, 4.05s:
  `/home/felni/.cache/caos-spec021-validation/tmp/routine-exported-snapshot-green-20261011.log`.
  The catalog/registry/reset batch passed **11 cases**, 7.80s, in the agent
  tool transcript; no standalone log was written for that batch. Normal
  capture/restore was rechecked in the batch below.
- Runtime ownership verification initially accepted a schema-local function
  transferred to the runtime role. The private administrator performed the
  transfer; this does not establish that the runtime could replace its body
  without CREATE privileges. The native regression failed once, then the
  catalog/registry/receipt-reset/ownership batch passed **12 cases**, 7.88s,
  after ownership checks covered both relations and routines. Evidence:
  `/home/felni/.cache/caos-spec021-audit-receipts-20261011/tmp/runtime-routine-owner-asserted-red.log`
  and `catalog-owner-final-green.log` in the same directory.
- A warm catalog-only timing probe over 136 tables passed (**one case**,
  2.91s), with seven samples and p95 46.8ms. This is not the interaction
  capacity measurement. The actual 20-source/100-pending-question capacity
  regression passed separately (**one case**, 23.60s), enforcing its existing
  two-second visibility bound and explicit saturation behavior. Its log does
  not print the measured percentile:
  `/home/felni/.cache/caos-spec021-audit-receipts-20261011/tmp/interaction-capacity-p95.log`.
- Namespace auxiliary objects reproduced clean native failures: an operator,
  libc collation and undefined shell type were admitted (**three failed**,
  13.89s) at:
  `/home/felni/.cache/caos-spec021-validation/tmp/aux-catalog-red3-20261011.log`.
  Prior `red2` includes a shell-type setup privilege failure and is not a clean
  three-case admission reproduction. Closure now rejects schema-local operators,
  operator classes/families, collations, conversions and text-search objects;
  the type check also includes undefined shell types. The shell regression uses
  the private fixture administrator without changing runtime-role privileges.
  The three rejection cases plus normal native capture/restore and receipt
  capture/blocked-restore passed: **five passed**, 21.89s:
  `/home/felni/.cache/caos-spec021-validation/tmp/recovery-aux-green-20261011.log`.

### Current browser acceptance

- Browser r5 completed **17 passed, three failed**; all three questions cases
  passed with DOM visibility p95 1,339ms. Restart inherited the operator HOME
  before the fixture configured CAO_HOME_DIR, correctly triggering the private
  result-store permissions guard. The fixture also used an invalid two-argument
  `atexit.unregister` call. Both require fixture corrections.
- Browser r6 used a dedicated private HOME and completed **19 passed, one
  failed**. Login continuity, setup and all three questions cases passed;
  DOM visibility p95 was 1,326ms. Backup failed deterministically because the
  external PostgreSQL client container used bridge networking with a host
  loopback target. Its 127.0.0.1 addresses the client container, not the host
  listener. Production transport correction and full rerun remain pending.
  Evidence: `/home/felni/.cache/caos-spec021-live-acceptance/.origin/logs/web-e2e-full-r5.log`
  and `web-e2e-full-r6.log` in the same directory. Neither run is complete
  browser acceptance; actual Codex and Docker/guest acceptance remain pending.
- A direct real-Docker regression reproduced the external-client refusal
  (**one failed**, 4.92s):
  `/home/felni/.cache/c16-spec021-loopback-r1/red.log`.
  The first transport hypothesis, loopback-only host networking, also failed
  on this Docker Desktop/WSL daemon (**one failed, four passed**, 4.02s).
  Its historical `green.log` filename is not a passing result. The configured
  hostname, TLS mode and pgpass matching were preserved; a working routing
  correction and native/browser reruns are still required.
- The working transport keeps bridge networking for Docker Desktop loopback
  targets, resolves its host gateway inside the pinned client image and sets
  numeric `PGHOSTADDR` while preserving the configured `--host`, TLS mode and
  pgpass identity. Linux Engine loopback uses host networking; non-loopback
  targets retain bridge networking. The real private PostgreSQL dump and six
  offline command/configuration cases passed together: **seven passed**, 5.41s:
  `/home/felni/.cache/c16-spec021-loopback-r1/gateway-green-r3.log`.
  This proves the real Desktop dump and configuration preservation, not a
  certificate-handshake test for every platform. The preceding `gateway-green-r2.log`
  recorded **four failed, three passed**, 6.24s: the real dump passed, while
  four offline shell-argument assertions had an incorrect positional index.
  Browser full rerun and current-source CI follow this correction.
- Browser r7 completed **19 passed, one failed**, 3.1 minutes; all three
  questions cases passed with DOM p95 **1,245ms**. Backup/restore completed,
  but the subsequent session request returned 503 instead of 401. The shared
  fixture retains clock advances from earlier tests. Restored startup inherited
  `CAO_BROWSER_LOGIN_ROOT` and replaced the injected clock with wall time,
  behind the committed future auth high-water mark. A same-child advance/restart
  followed by restore reproduced `clock_untrusted` (**one passed, one failed**).
  Evidence: `/home/felni/.cache/caos-spec021-live-acceptance/.origin/logs/web-e2e-full-r7.log`
  and `web-e2e-restore-future-clock-diagnostic-r4.log` in the same directory.
  The isolated restore case passed without the preceding future-clock sequence;
  it does not establish full browser acceptance. The fixture must retain its
  explicitly controlled clock through restoration; production clock rollback
  protections remain intact. A deterministic standalone future-clock restore
  regression and complete browser rerun are pending.
- The fixture restore now removes the deployment-root hook after explicitly
  composing its controlled-clock service. Production lifespan and clock fences
  are unchanged. The restore E2E independently advances eight hours, renews and
  commits a successful session read before restoration. The continuity/restart
  case and this real backup/restore regression passed together: **two passed**,
  27.7s, including the required post-restore 401, preserved account and new
  cookie on login. Evidence:
  `/home/felni/.cache/caos-spec021-live-acceptance/.origin/logs/web-e2e-restore-regression-r5.log`.
  Complete browser r8 subsequently passed **20 cases, zero skips**, 2.5 minutes,
  against the corrected fixture and current source. Its three questions cases
  passed; DOM visibility p95 was **1,294ms** for 20 sources and 100 committed
  questions. Evidence:
  `/home/felni/.cache/caos-spec021-live-acceptance/.origin/logs/web-e2e-full-r8.log`.
  The source manifest is `.origin/SHA256SUMS-r8-source` in the same snapshot;
  no temporary diagnostic modifications remain. Actual Codex, Docker/guest
  acceptance and the complete backend coverage run remain separate gates.

### Current source checks and real Codex boundary

- Fresh repository-configured style/type checks passed: Black **1,289 files
  unchanged**, isort exit zero, and mypy **no issues in 424 source files**.
  Evidence: `/home/felni/.cache/c16t/spec021-ci-20261011-r4/{black,isort,mypy}.log`.
  Mypy retains its existing notes about unchecked bodies of untyped functions.
  Earlier r2/r3 attempts caught import formatting in two files; the first
  candidate used isort defaults instead of the repository profile. Only those
  imports were corrected after non-import AST and binding checks.
- The real Codex run used CLI **0.162.1**, requested `gpt-6-luna`, existing
  ChatGPT auth in a private overlay and no API keys. It exited zero after
  **15.02s**, but created **zero questions** and did not satisfy SC007. Its
  `mcp_or_tool_discovery_failed` classification is not a proven root cause;
  the former observer only matched a tool name in output. Evidence:
  `/home/felni/.cache/caos-spec021-live-acceptance/.origin/logs/codex-gpt6luna-live-r1.log`.
- The same isolated stdio MCP server completed its no-model diagnostic in
  **2.41s**, exposing **79 tools**, including all seven configured interaction
  tools, and a successful metadata-only empty-inbox call. This proves startup,
  handshake and metadata API access, not question creation or acknowledgement.
  Evidence: `/home/felni/.cache/caos-spec021-live-acceptance/.origin/logs/codex-mcp-diagnose-r1.log`.
  Raw provider logs and credentials are not published.
- The instrumented real Codex retry exited zero in **10.87s**, but its actual
  `cao_ask_user` MCP item completed with `status=failed`, an error and no result.
  It created **zero questions**; SC007 remains unsatisfied. Evidence:
  `/home/felni/.cache/caos-spec021-live-acceptance/.origin/logs/codex-gpt6luna-live-r2.log`.
  A subsequent no-model stdio probe with the exact two-question payload and
  `wait_for_required=true` succeeded and persisted **two scoped disposable
  questions**. This verifies the direct MCP/API create path, without proving
  the actual provider's input or dispatch cause. Evidence:
  `/home/felni/.cache/caos-spec021-live-acceptance/.origin/logs/codex-mcp-create-probe-r3.log`.
- Further bounded provider runs r3 and corrected r4 still failed the real
  `cao_ask_user` call, with **zero questions and answers**. R4 took **14.91s**;
  a separate earlier r4 initialization attempt failed before starting Codex
  because its runner omitted the repository root from `PYTHONPATH`.
  The provider error is not classified; no backend or CLI root cause is claimed.
  Evidence: `.origin/logs/codex-gpt6luna-live-r{3,4-provider}.log` in the same
  private live-acceptance snapshot. The error-message sanitizer regression was
  reproduced against the pre-fix harness, then **eight focused tests passed**.
- A subsequent opt-in diagnostic preserved only the unknown CAO MCP error
  in a private mode-0600 file, excluding arguments, results and full JSONL.
  Local inspection identified the exact technical failure:
  `MCP tool call requires approval, but approval policy is never`.
  The harness's `approval_policy="never"` override prevented execution before
  the tool reached CAO. This explains the failed provider boundary; the direct
  stdio create probe remains valid. A scoped harness approval fix and actual
  question/answer/ack verification are still pending. The diagnostic call took
  **15.64s**, again with zero questions. Evidence:
  `.origin/logs/codex-gpt6luna-live-r5-diagnostic.log` in the private snapshot.
  The opt-in capture's permissions/exclusion regressions passed **ten tests**.
- Scoped Codex tool approvals were added for exactly the seven interaction
  tools, retaining the global `never` policy, read-only sandbox and disabled
  shell. The regression was RED (**two failed, nine passed**), then GREEN
  (**11 passed**); CLI 0.162.1 accepted the per-tool configuration. The real
  r6 run then created **two questions** and the harness posted **two human
  answers**, resolving the approval blocker. Its acknowledgement returned
  structured `not_found`, however, leaving both deliveries `returned_to_mcp`.
  It is **not SC007 acceptance**. Evidence:
  `.origin/logs/codex-gpt6luna-live-r6-pertool-approval.log` (13.55s).
  Group/question identity and the complete direct MCP lifecycle are being
  diagnosed before another provider retry.
- Current direct PostgreSQL lifecycle checks passed **two tests** in 12.36s,
  including the real bundled MCP stdio process. The subsequent real Codex r7
  subscription journey completed in **20.88s** with **two questions**, **two
  human answers** and both deliveries **`agent_acknowledged`**. The successful
  service acknowledgement matched the created/returned group and the exact
  returned question IDs, versions, receipts and count; only booleans were
  recorded. The provider also emitted an earlier failed acknowledgement item;
  its cause is unproven, and the final successful service call is separately
  verified. No cause for r6's `not_found` is inferred from this later pass.
  Evidence: `.origin/logs/interaction-ack-no-model-r2.log` and
  `.origin/logs/codex-gpt6luna-live-r7-ack-identity.log` in the private snapshot.
  This establishes the current Codex explicit question/answer/ack journey;
  it does not establish other providers or complete Docker/host/backend gates.
  The obsolete private raw error diagnostic was removed after inspection;
  its SHA256 and the fixed technical error remain recorded, with no payloads.
- Fresh Apps coverage passed **92 tests across 12 files** in 9.68s, with
  **90.01% line coverage (478/531)** against the 90% floor. All 79 manifested
  Apps source files match both snapshot and workspace; the dependency lock
  matches the reused private installation. Evidence:
  `/home/felni/.cache/c16t/spec021-apps-coverage-20261011-r1/coverage-evidence.json`.
  The combined ratchet is still pending the complete Python coverage report.

### Resource recovery and latest source changes

- Current private application image build completed with exit zero. Its immutable
  ID is `sha256:6b45d78bd8a7504d9841300913b757bf10ab31c655a38e2e4bf42c12e663398e`,
  with the verified 2,821-file snapshot manifest and a retained context archive.
  The r3 build failed while resolving public base-image metadata because the
  global Docker Desktop `desktop.exe` credential helper failed in WSL.
  R4 used a separate mode-0700 Docker configuration containing only empty
  public-registry auth settings; global configuration was not modified.
  Root verified that the retry context bytes are identical to the retained r3
  archive and match the recorded SHA256. Evidence:
  `/home/felni/.cache/c16t/spec021-app-image-build-r{3,4}.log` and
  `/home/felni/.cache/c16t/personal-import-acceptance-20261011-r4/application-image.json`.
  A successful image build does not establish installation/import acceptance;
  those real Docker tests follow against this exact immutable ID.
- The real installation selector failed at managed PostgreSQL startup:
  PostgreSQL 18 repeatedly rejected directory creation with
  `mkdir /var/lib/postgresql: Permission denied`, then the installer's
  120-second readiness bound expired. The test cleaned its owned container
  and network; the existing validation PostgreSQL service and operator
  application remained running. Evidence is retained privately at
  `/home/felni/.cache/c16t/pa-r4/installation-r1.log` (mode 0600).
  Bind-mount ownership and the image entrypoint boundary are under direct
  disposable-probe investigation; no successful installation is claimed.
- Pinned-image source and a disposable real probe confirmed that the bind
  root remained UID 1000/mode 0700 while the PostgreSQL entrypoint switched
  to UID 999, which could not traverse the ancestor of `PGDATA`. Running the
  container with the current installer UID:GID preserves the existing private
  layout. The real probe verified init, readiness, psql/pg_dump/pg_restore
  with the image's `postgres` user, stop/start and readiness after restart,
  while retaining directory 0700 and password-file 0600. Evidence:
  `/home/felni/.cache/c16t/pa-r4/pg18-uid-restart-lvs33zqp/probe-summary.json`.
  The installer regression was **one failed** without `--user`, then **one
  passed** with the narrow fix. Full installation and import are being rerun;
  this probe alone does not establish their acceptance.
- Installation r2 passed managed PostgreSQL readiness, then exposed the next
  composition boundary: normal bootstrap omitted the existing `admin()`
  profile-network argument. Its temporary admin container therefore could
  not resolve the managed `main-db`/`peer-db` aliases on that profile's bridge.
  The offline bootstrap/verify callsite regression reproduced this (**two
  failures**). Profile-network propagation through bootstrap, verification
  and upgrade backup is being corrected before another real install retry.
  R2 is failed, not acceptance; its private evidence is
  `/home/felni/.cache/c16t/pa-r4/installation-r2.log`.
- Installation r3 then exposed inconsistent preflight peer ownership:
  creation used the private canonical host UUID, while cleanup expected the
  unrelated hostname/UID hash used for network ownership. Cleanup correctly
  refused its mismatching expected label; r3 is **failed** (52.57s).
  The fix verifies the private canonical identity, isolated service metadata,
  exact container name and store UUID before matching peer labels; network
  ownership retains its existing hash. The new regression was RED against
  the former code, then the installer offline group passed **18 tests, 32
  deselected** in 1.35s. Root reviewed the source and this actual result.
  Evidence: `/home/felni/.cache/c16t/pa-r4/installation-r3.log` and
  `docker-install-focused-r4.log` in the same private directory.
  The exact labeled orphan peer and preflight network from r3 were cleaned;
  no success is claimed for the next real installation rerun until it finishes.
- Installation r4/r5 reached native backup, where the temporary administrator
  could not verify its managed PostgreSQL container without the Docker socket.
  A disposable probe against the same application image reproduced the failure
  without the socket and succeeded with a read-only socket bind and its host
  group. Only backup/restore administration receives that socket; other admin
  operations and provider containers do not. Socket validation rejects symlinks
  and regular files. A read-only bind still permits Docker API operations;
  it is not a read-only Docker API. Probe evidence:
  `/home/felni/.cache/c16t/spec021-admin-socket-probe.json`.
- Installation r6 passed initial installation and native backup but failed on
  repeating installation: the secret-file writer stored a trailing newline
  while its reader compared against the password without that newline.
  Reuse now compares exact canonical bytes. The new regression covers creation,
  unchanged same-password reuse, changed-password rejection and an extra newline.
  The current installer group passed **24 tests, 32 deselected**, 1.60s:
  `/home/felni/.cache/c16t/pa-r4/docker-install-focused-r7.log`.
  Root inspected the implementation and actual result. The complete real
  installation and stopped-SQLite import are being rerun separately; these
  offline passes do not certify those workflows.
- The complete real installation selector subsequently passed **one test**,
  225.81s, using the immutable application image and a fresh private profile:
  `/home/felni/.cache/c16t/pa-r4/installation-r7.log` (0600).
  Root read its actual summary and checked the executed assertions: initial
  install, unchanged reinstall, HTTP setup/login and cookie continuity across
  stop/start, upgrade, rollback and failed-candidate restoration. The test also
  preserves protected image aliases and the operator application fingerprint.
  The separate three-scope stopped-SQLite import remains pending.
- The first actual stopped-SQLite import run completed its import CLI and
  published private configuration plus a three-scope manifest, then failed in
  the test's direct host-side receipt verification. The exact configured
  `main-db` host is a private Docker-network alias and cannot resolve from the
  host. The test now verifies receipts inside a disposable pinned application
  container on the validated profile network, with a read-only private profile
  mount, unchanged target identity, no Docker socket and no published database
  ports. It compares source path/digest/schema fingerprint, target UUID/schema
  and full table manifest using the packaged production verifier and legacy
  profiles. The complete retry, including subsequent normal installation,
  remains pending; successful CLI import alone is not full acceptance.
- The follow-up auth/peer batch produced **160 passed, one failed and one
  skipped** in 434.22s. Despite its historical filename,
  `/tmp/caos-spec021-frontend-auth-peer-final-green.log` is **not green**.
  The two-profile case passed alone, but the full run hit PostgreSQL connection
  timeouts during child startup and exceeded its 180-second readiness bound.
  No readiness or connection timeout was widened.
- The exact incomplete-store corruption check and the opt-in real Hermes
  native-interaction test passed together: **two passed, two warnings** in
  13.64s. Evidence:
  `/home/felni/.cache/caos-spec021-transport-validation/logs/postgres-targeted-cascade-hermes.log`.
  Hermes used a local model stub, not a hosted provider.
- Native recovery batch r5 completed with **50 passed, 15 failed, 27 errors,
  five deselected** in 289.67s. Evidence:
  `/home/felni/.cache/c16t/recovery-postgres-final-r5.log`. Fixture adaptations
  and the personal deployment verifier's staged Work-history deduplication
  have been corrected in source; their rerun remains pending.
- Source review found that before/after inventory equality did not guarantee
  `pg_dump` used the inventoried database snapshot. Capture now holds a native
  repeatable-read, read-only exporter across row scanning, inventory, reference
  copying and `pg_dump --snapshot`, followed by fresh after-inventory and cut
  checks. The real-archive regression now has native RED/GREEN evidence:
  the pre-fix oracle failed specifically because the transient workflow row
  appeared in actual `pg_restore --file=-` output; current source passed the
  same case. Both runs used the existing owned PostgreSQL container, stayed
  below 111 MiB RSS, and completed in under six seconds wall time. Logs:
  `/home/felni/.cache/c16t/spec021-snapshot-regression-20261011-01/{red,green}.pytest.log`.
  This proves the snapshot regression, not the complete recovery suite.
- A snapshot-only full collection completed with **17,340 tests collected**
  in 30.09s; no test bodies or fixtures ran. Evidence:
  `/tmp/caos-spec021-ledger-final-20261011-collect.log`. Copied bytecode was
  removed and module origins checked inside the isolated Linux snapshot.
- Available RAM subsequently fell below 1 GiB with swap full; even small
  commands intermittently returned `ENOMEM`. All heavy test/collection runs
  were held. Other projects' processes and temporary files were preserved.
  Available memory subsequently recovered above 2 GiB, allowing the bounded
  archive regression and a serial recovery-suite rerun to resume. The prepared
  474-case runtime batch, remaining owner suites, complete backend coverage
  run, browser/apps acceptance and Docker/QEMU acceptance remain pending.
  QEMU retains the project's minimum 2 GiB available-memory gate.

- The complete 403-case API batch passed with 402 passed and one guarded live
  provider skip in 207.60s. Evidence:
  `/tmp/caos-spec021-frontend-api-final-green.log`. The live provider case is
  tracked separately; its skip is not a successful provider execution.
- The subsequent 162-case auth/peer batch produced 159 passed, two failed and
  one skipped in 89.14s. Evidence:
  `/tmp/caos-spec021-frontend-auth-peer-final-rerun.log`. The failures concern
  unbootstrapped Work fixture state and a two-process peer-pair response;
  diagnosis and rerun remain pending.
- A fresh intermediate composition check kept all five contracts, with zero
  broken contracts: `/tmp/caos-spec021-composition-current-repeat.log`.
  An AST inspection of every source `read_snapshot()` context found no SQL
  literals containing `FOR UPDATE`, `FOR SHARE` or `LOCK TABLE`. This is a
  narrow source check, not a replacement for native transaction tests.
- The legacy Beads and continuation schema module docstrings now identify
  their offline compatibility role and point to the native runtime schemas.
  Their DDL and executable behavior are unchanged.

Status: implementation and validation in progress; not a completion certificate.

## Current frontend and schema review evidence

- Full configured source mypy completed successfully: **no issues in 416
  source files**. The isolated source file set was independently checked
  against the workspace (**423 Python paths, no missing or extra paths**),
  including current recovery and migration module hashes. Log:
  `/tmp/caos-spec021-mypy-final-20261011.log`. This is the configured type
  check, not a claim that untyped function bodies were all checked.
- Fresh isolated web unit run: **504 passed, 45 files**; exact-lock build and
  typecheck passed. A separate coverage run also passed all 504 tests, with
  **76.35% line coverage**. Logs:
  `/tmp/caos-spec021-frontend-web-unit.log`,
  `/tmp/caos-spec021-frontend-web-coverage.log`. Browser acceptance is separate.
- Fresh MCP Apps unit/coverage run: **92 passed, 12 files**, **90.01% lines**;
  typecheck passed. Evidence:
  `/tmp/caos-spec021-frontend-mcpapps-coverage-green.log`.
  The frontend ratchet passed its 90% floor, but explicitly skipped absent
  Python coverage. The combined ratchet remains pending the backend report.
- Native review reproduced acceptance of an unexpected row-security policy:
  the migration owner saw one row while the runtime role saw zero, and schema
  verification did not reject that catalog drift. Separate native RED tests
  reproduced acceptance of `UNLOGGED` and table-to-view changes. The catalog
  now fingerprints relation kind, durability, RLS flags and policy definitions.
  The three regressions plus full-bootstrap idempotence passed against final
  migration source: **four passed** in 2.89s pytest time. Evidence:
  `/home/felni/.cache/c16t/spec021-recovery-acceptance-20261011-02/rls-green-final.pytest.log`.
  Broader recovery acceptance is still pending the full current suite.

## Native foundations

- Real PostgreSQL 18.6 with separate main/auth/peer roles and private credentials.
- `test_postgres_transactions`, registry, ORM, database regressions,
  credential permissions, connection ownership, private-plan snapshots and approval
  suite: **195 passed, 2 warnings**, 75.32 s. Log:
  `/tmp/caos-postgres-root-foundation-final.log`.
- The ownership test uses native `psycopg.Rollback` inside a transaction context.
- The same run verifies the repeated full-registry bootstrap regression, native
  read-only transaction property, terminal FIFO, signed 64-bit values, catalog
  tamper rejection and stored private-snapshot corruption handling.
- Follow-up regression: borrowed private-plan freeze/release transactions must
  verify their component ledger before writing. **5 passed**, 8.12 s, after
  reproducing two failures. Logs `/tmp/caos-postgres-private-borrowed-{red,green}.log`.
- Catalog fingerprints now include trigger activation state. Disabled,
  `ALWAYS`, and `REPLICA` triggers were accepted incorrectly before the fix;
  transaction suite **8 passed**, 3.66 s, afterwards.
  Logs `/tmp/caos-postgres-trigger-state-{red,green}.log`.
- Metadata/audit target mismatch regression reproduced before source change:
  `/tmp/caos-postgres-memory-target-red.log`. The service now checks native pool
  target identity before preparing its audit repository; green rerun pending.

## Interaction performance

- Before optimization, a measured metadata page took 9.540 s and the setup
  performed 3,844 full Work verifications / 78,925 SQL statements.
- Each service transaction/snapshot already verifies its Work connection.
  Removed the extra schema verification per visible source, retaining per-source
  terminal/attempt/grant revalidation.
- Regression reproduced **8 versus 2** schema verifications, then passed after
  the change: `/tmp/caos-interaction-work-verify-regression-{red,green}.log`.
- Timed capacity SLO: **1 passed**, 32.08 s. All 100 questions from 20 sources
  remain visible and the test asserts metadata p95 below two seconds.
  Log `/tmp/caos-interaction-capacity-slo-final.log`.
- Native Work delivery: **30 passed**, 132.41 s, including same-transaction
  failure injection and rollback. Log `/tmp/caos-work-delivery-module-green.log`.

## Infrastructure and incomplete runs

- Test service hit its 1 GiB Docker cgroup limit while PostgreSQL data lived on
  tmpfs; Docker confirmed OOMKilled and postmaster recovery. Affected overlapping
  Work suites are not acceptance evidence. Test service capacity/cleanup is being
  corrected before serial reruns. The owned test container cap was raised to
  2 GiB without restarting it; its data tmpfs stays capped at 1 GiB.
- Default full collection found 29 import errors from removed SQLite runtime
  migrators and test helpers: `/tmp/caos-postgres-full-collection.log`.
  Consumers are being ported to native fixtures or explicit offline artifact
  validation; runtime SQLite migrator shims will not be restored.
- After those ports, complete collection: **17,307 tests, zero collection
  errors**, 11.10 s. Log `/tmp/caos-spec021-full-collection-current.log`.
  Collection does not establish passing tests.
- Worker isolation regression after the ACL/WAL fixes: **3 passed**, 7.43 s.
  Runtime DML, sequence/function grants are restored; schema CREATE/TRUNCATE and
  migration-ledger writes stay denied. WAL limits and owned tmpfs headroom guard
  are active. Runs are limited to two concurrent database suites after checking
  container memory, WAL retention and tmpfs headroom.
- Earlier root authority batch was interrupted after fixture ACL leakage and server OOM;
  no passing batch result is claimed. Worker reset must restore business-table
  ACLs before subsequent tests, while preserving read-only migration ledgers.

## Remaining acceptance

Complete backend/frontend/integration and applicable Docker/host suites,
all-store offline imports, native backups and recovery, architecture/composition
checks, final diff review and isolated Graphify refresh remain required.
No operator database has been activated, and no commit or deployment was made.

## Native import and recovery follow-up

- Three-scope SQLite-to-PostgreSQL import and native backup/restore files:
  **4 passed, 2 warnings**, 21.65 s. Actual `pg_dump`/`pg_restore` are used;
  log `/tmp/caos-spec021-importer-recovery-full.log`.
- PostgreSQL CHECK deparse/reparse changed equivalent varchar-array casts and
  invalidated exact catalog fingerprints. Native roundtrip regression reproduced
  this (**1 failed**, 4.14 s) and passed after narrowly canonicalizing the equivalent
  text-array form (**1 passed**, 2.80 s). Logs
  `/tmp/caos-spec021-catalog-roundtrip-{red,green}.log`.
- Literal preservation and unchanged/different SQL checks: **10 passed,
  1 deselected**, 3.34 s, `/tmp/caos-spec021-catalog-canonical-offline-final.log`.
- Libpq inherited secret/service variables were not filtered at the provider
  boundary. Six failing regressions reproduced it; native environment and tmux
  tests afterwards: **22 passed**, 15.14 s.
  Logs `/tmp/caos-spec021-libpq-env-{red,green}.log`.
- First global native diagnostic run: **18 failed, 706 passed, 12 errors**,
  340.79 s. All 30 reported failures/errors came from missing packaged plugin
  assets or Git metadata in the Linux copy, not established PostgreSQL behavior
  failures. The copy now contains the packaged assets and an independent local
  Git metadata clone with the original tracked index. The exact corpus Git
  check passed afterwards. This run is failed and is not acceptance evidence.
  Log `/tmp/caos-spec021-full-native-attempt1.log`.
- Broad recovery negative/fencing/concurrency cases and the final full suite
  remain required; the four native import/recovery passes do not replace them.

## Recovery intent map

SQLite remains an offline source-artifact format. Current capture, fencing,
restore, and recovery decisions use PostgreSQL v3. Historical inventories and
bundles are checked against frozen SQLite profiles and never receive runtime
restore authority.

| Historical intent | Current proof | Evidence / state |
| --- | --- | --- |
| Closed historical Work/native catalogs, ordered columns, migration checksums, exact foreign keys, canonical FK multiplicity, source reference-family mappings, read-only inspection, and rejection of missing/extra tables, columns, DDL, FKs, or unsupported versions (the former inventory cases in `test/services/test_recovery_bundle.py`) | `test/services/test_legacy_recovery_catalog.py` exact v28-v32 fixtures, checksum/FK mutation tests, read-only assertions, v32 private-plan exclusion, and reference-family proof; `test/clients/test_legacy_recovery_bundle.py` exact 121/134-table and interaction-object profiles | Audit reports **15 passed** for the legacy catalog module. The combined offline batch below includes that module and passes. |
| Historical v1/v2 byte integrity, verify-only behavior, unsupported/mixed profile rejection, frozen v28-v31 receipts, v32 receipt non-admission, and v38/v39 profile boundaries | `test/clients/test_legacy_recovery_bundle.py`; `test/services/test_recovery_bundle.py::test_historical_v2_receipts_remain_verify_only_for_frozen_profiles` and `::test_historical_v32_bundle_is_not_admitted_as_a_v2_profile` | Combined offline batch: **37 passed, 11 deselected**, 28.40 s, 2 existing Pydantic warnings. Deselects are native PG tests, not acceptance evidence. |
| Exact executable/reference bytes, missing and changed objects, symlink ancestors, inode replacement, bounded reads, orphan exclusion, source mutation, secret/control-byte rejection, and no secret echo | `test/services/test_postgres_recovery_bundle.py::test_capture_preserves_every_declared_work_content_family`, `::test_capture_requires_roots_for_every_referenced_external_object`, `::test_capture_rejects_broken_executable_references_before_publication`, `::test_capture_rejects_executable_root_with_symlinked_ancestor`, `::test_capture_rejects_executable_inode_replaced_after_inventory`, `::test_capture_rejects_oversized_executable_before_reading_payload`, `::test_capture_rejects_database_row_secrets_and_control_text`, `::test_capture_rejects_secret_in_referenced_work_content`, `::test_capture_restores_exact_reviewed_delegation_snapshot_bytes`, `::test_capture_rejects_unsafe_delegation_snapshot_bytes`, `::test_capture_rejects_symlink_and_secret_memory_objects`, and `::test_pg_dump_uses_the_scanned_snapshot_for_transient_workflow_rows` | Authored against real PostgreSQL and executable-content fixtures; awaiting the next PostgreSQL test slot. The exported snapshot binds row inspection and `pg_dump`; the regression inserts a transient secret row between scan and dump, removes it before the after-inventory, and inspects the real archive. The former SQLite deleted-row compaction case is replaced by `::test_postgres_archive_contains_no_deleted_secret_row_version`, which verifies `pg_dump` excludes deleted row versions. |
| Offline-cut authority, phase observations, durable revalidation after restart, source-store binding, dump failures, expiry, mutations during dump, revocation races, staging cleanup, and directory-fsync failure | `test/services/test_postgres_recovery_bundle.py::test_postgres_bundle_captures_and_restores_blocked_target`, `::test_capture_dump_failure_removes_private_staging_and_records_rejection`, `::test_capture_revalidates_durable_cut_after_authority_restart`, `::test_capture_rejects_a_work_authority_for_another_postgres_store`, `::test_capture_rejects_unregistered_database_mutation_during_dump`, `::test_capture_directory_fsync_failure_after_promotion_returns_no_receipt`, `::test_expired_offline_cut_cannot_publish_a_capture`, `::test_revoke_during_capture_fences_publication_and_cleans_staging`, and `::test_revocation_waits_for_publication_and_cannot_revoke_published_cut` | Authored; PG run pending. Assertions include zero writers at cut, phase ordering, inventory fingerprint consistency, and cleanup/no-receipt behavior. |
| Manifest/object tampering, recomputed receipts with altered cut evidence, wrong source identity, incomplete/private members, unreviewed views/triggers, and durable receipt contradiction | `test/services/test_postgres_recovery_bundle.py::test_bundle_refuses_source_target_and_tampered_object`, `::test_verifier_rejects_incomplete_or_nonprivate_bundle_members`, `::test_verifier_rejects_recomputed_manifest_with_duplicate_objects`, `::test_verifier_rejects_recomputed_cut_receipt_tampering`, `::test_verifier_rejects_receipt_when_durable_observation_contradicts_manifest`, `::test_bundle_verification_requires_the_exact_source_target`, `::test_capture_rejects_unreviewed_executable_schema_objects`, and restore-side schema rejection below | Authored; PG run pending. Verification requires the exact source target, rechecks the durable published cut and all three source observations, and binds the receipt to the published bundle path. |
| Portable identity bridge, required indexes, corrupt durable profile, blocked restore, recovery reconciliation, no task replay, and preservation of Work content | `test/clients/test_postgres_legacy_import_profiles.py::test_all_legacy_scopes_import_with_identity_bridge_and_sequences`; `test/services/test_postgres_recovery_bundle.py::test_postgres_bundle_captures_and_restores_blocked_target` and `::test_capture_preserves_every_declared_work_content_family`; `test/integration/test_work_recovery.py::test_v2_capture_restores_only_a_blocked_distinct_portable_store` and `::test_corrupt_publication_lease_and_interrupted_copy_fail_without_destination` | Import coverage is separately scheduled. Native recovery and importer tests still await PG execution. |
| Restore target separation, existing-schema preservation, staged-object validation, exact bytes, atomic `pg_restore` failure cleanup, destination-created-after-preflight race, schema rejection, post-promotion validation rollback, and no activation/relaunch | `test/services/test_postgres_recovery_bundle.py::test_restore_refuses_wrong_store_and_preserves_existing_schema`, `::test_restore_failure_inside_atomic_pg_restore_leaves_no_schema_or_staging`, `::test_restore_does_not_drop_schema_created_after_absence_preflight`, `::test_restore_rejects_unreviewed_relation_and_drops_only_its_schema`, `::test_restore_validation_failure_after_object_promotion_cleans_owned_artifacts`, and `test/scripts/test_personal_deployment.py::test_backup_and_restore_roundtrip_all_postgresql_scopes` | Authored against isolated PostgreSQL targets; PG run pending. No operator target is used or activated. |
| Peer-store exclusion, populated peer authority preservation, historical peer profile verification, and unreviewed peer schema rejection | `test/services/test_recovery_peer_profile.py::test_current_peer_profile_is_closed_and_nonportable`, `::test_frozen_v38_catalog_rejects_current_peer_objects`, `::test_historical_v38_bundle_remains_verify_only`, parametrized `::test_capture_rejects_populated_peer_authority` / `::test_restore_rejects_peer_store_as_a_main_restore_target`, and trigger/view capture/restore cases | Offline historical cases passed in the 37-test batch; native PG cases await PG execution. |
| Legacy work-recovery entry points and interrupted old migration behavior | `test/integration/test_work_recovery.py`: offline v1/mixed-profile rejection and v25→v26 fail-closed-before-mutation; native PG v3 blocked restore, corrupted lease and interrupted copy rejection | Offline cases are included in the **37 passed** batch. Native cases await the PG slot. |

The focused offline command was:

```bash
PYTHONPATH=src:. /home/felni/.cache/caos-spec016-validation-venv/bin/python -m pytest -o addopts= --no-cov -m offline_database -q test/services/test_recovery_bundle.py test/clients/test_legacy_recovery_bundle.py test/services/test_legacy_recovery_catalog.py test/services/test_recovery_peer_profile.py test/integration/test_work_recovery.py
```

## Architecture check (intermediate)

`project-composition-check "$(cat .ai/project-name)"` exited 0:
426 Python files, 1,799 dependencies, **5 architecture contracts kept,
0 broken**. Log `/tmp/caos-postgres-composition-check-inprogress.log`.
This intermediate check must be repeated against the final diff.

## Frontend and static checks

- Web unit suite: **45 files / 504 tests passed**, 1090.42 s on the Windows
  mounted checkout. Log `/tmp/caos-spec021-web-unit-final.log`. The runtime was
  dominated by jsdom environment/setup filesystem work; test execution itself
  was 15.44 s. Console output from expected error-boundary/canvas cases does
  not represent failing tests.
- `npm --prefix web run build`: exit **0**, Vite completed in 33.69 s;
  existing bundle-size warning remains. Log `/tmp/caos-spec021-web-build-final.log`.
- Full production mypy with project `mypy.ini`: **423 source files, zero errors**.
  Log `/tmp/caos-spec021-full-mypy-after-guards.log`.
  Source changes after this check require a fresh final verification.

## Transaction composition and test isolation follow-up

- SQLAlchemy/Work composition reproduced a real borrowed-connection error:
  the owner uses tuple rows while Work verifies named fields. Independent native
  regression: **1 failed**, 1.27 s,
  `/tmp/caos-spec021-borrowed-record-red-retry.log`. A prior attempt failed during
  Docker inspection and is infrastructure evidence, not the behavioral RED.
- Native wrappers now select their record factory per cursor, preserving the
  borrowed owner row factory, active transaction and connection lifetime.
  Transaction and vault composition modules: **17 passed, 2 warnings**, 15.16 s,
  `/tmp/caos-spec021-borrowed-record-green-retry.log`. The first corrected run
  had 16 passes and one test query used an unqualified table after rolling back
  its SET search_path; the final test qualifies that read and verifies rollback.
- Disposable worker reset originally compared catalog structure only. A test
  corrupting migration ledger rows contaminated the next test despite unchanged
  DDL. Actual RED: **1 failed**, 4.40 s; after including deterministic migration
  and logical-identity rows in the fixture baseline, worker module:
  **7 passed**, 15.18 s. Logs
  `/tmp/caos-spec021-worker-ledger-{red,green}.log`. Only the fixture-owned
  disposable namespace is rebuilt; production still rejects schema drift.
- Recovery cursor SQL retained SQLite COLLATE BINARY. Five native recovery tests
  reproduced the syntax error; the SQL now uses PostgreSQL COLLATE "C" and a
  regression checks paginated ASCII identifier order. Full green rerun pending.
- Memory use counters retained SQLite julianday/datetime functions. Native
  scoring RED: **4 failed, 1 passed**, 7.45 s,
  `/tmp/caos-spec021-scoring-pg-red.log`. The implementation now uses the server
  clock_timestamp and an interval predicate in the atomic UPDATE; a concurrent
  same-identity regression was added. Full green rerun pending.
- Host memory pressure caused 30-second Docker inspection timeouts in one
  subsequent authority batch; it was interrupted and is not acceptance evidence
  (`/tmp/caos-spec021-root-authority-memory-repaired.log`). About 900 MiB of
  inactive owned Hermes test artifacts were moved from tmpfs to the private
  evidence archive, retaining original-path symlinks. Unrelated processes and
  artifacts were preserved. Validation resumed after headroom recovered.

## Authority, memory and Work follow-up

- Native authority/memory/API batch: **217 passed, 3 failed**, 373.68 s;
  `/tmp/caos-spec021-root-authority-memory-candidate.log`. Two failed assertions
  still expected naive timestamps instead of native UTC-aware timestamps; those
  assertions are corrected, with reruns pending. Incremental vault reconciliation
  changed a byte-deterministic metadata field and remains under investigation.
- Native Work batch: **136 passed, 11 failed**, 421.81 s;
  `/tmp/caos-spec021-ledger-final-a.log`. The eleven continuation cases failed in
  the shared test snapshot helper because it treated the migration tuple as one
  migration. The helper now flattens the declared tables; rerun pending.
  The three deferred-FK cases, authority, repository, dispatch, inbox,
  cancellation and terminal-input cases in that batch passed.
- Explicit historical recovery catalog/reference mapping tests: **15 passed**
  in an isolated offline run. They validate archived SQLite artifacts only;
  they do not establish native PostgreSQL recovery acceptance.

- The three root-batch failures now pass in a fresh focused native run:
  **9 passed, 2 warnings**, 45.32 s;
  `/tmp/caos-spec021-root-final-three.log`. This includes all seven vault
  determinism tests. PostgreSQL returned aware timestamps, so the ORM considered
  the projected `updated_at` unchanged during rename and applied its automatic
  wall-clock `onupdate` when another field changed. The projection now explicitly
  includes its source timestamp in dirty updates; unchanged projections remain
  no-ops. Rename now asserts the original file mtime immediately, and subsequent
  incremental reconciliation preserves the full byte-equal projection.
- CLI storage display: three failing regressions then **7 passed**, 0.80 s;
  `/tmp/caos-spec021-cli-info-{red,green}.log`. `cao info` identifies PostgreSQL
  instead of displaying the obsolete SQLite source path. Wiki projection drift
  labels its metadata rather than SQLite: **1 passed**, 1.75 s;
  `/tmp/caos-spec021-wiki-diagnostic.log`.
- Intermediate architecture/composition check: **426 files, 1,803 dependencies,
  five contracts kept, zero broken**, exit 0;
  `/tmp/caos-spec021-composition-current.log`. Later implementation changes mean
  this remains intermediate evidence, not final sign-off.

- Complete currently marked offline-database suite after fixture isolation fixes:
  **96 passed, 17,230 deselected, 5 warnings**, 156.08 s;
  `/tmp/caos-spec021-offline-all-retry.log`. PostgreSQL autostart was explicitly
  disabled; this run verifies legacy/read-only artifacts and pure native
  contracts without accessing an operator or test PostgreSQL service.
- Intermediate 403-case API run: **380 passed, 15 failed, 1 skipped, 7 errors**,
  317.10 s; `/tmp/caos-spec021-frontend-api-final.log`. Fourteen replay tests
  assumed the obsolete private-schema prefix; six real-server setup errors lacked
  packaged examples in the owned snapshot; an owner-injected trigger was
  unqualified, and one fixture saw a connection timeout under host pressure.
  Fixes and full rerun are pending; neither failures nor the skip are green
  acceptance evidence.

- Expanded 65-module interaction/AG-UI acceptance: **747 passed, 1 failed,
  1 error**, 967.74 s;
  `/home/felni/.cache/caos-spec021-transport-validation/logs/postgres-acceptance.log`.
  The intentional missing-Work-table test now drops with explicit CASCADE to
  account for PostgreSQL dependencies; its fail-closed assertion remains.
  The error was unnecessary PG fixture access in the pure AC5 gate module,
  not an AG-UI assertion. That module now runs explicitly offline:
  **7 passed**, 3.58 s. Full native acceptance remains pending.
- API repeat after snapshot/helper fixes: **399 passed, 3 failed, 1 skipped**,
  226.34 s; `/tmp/caos-spec021-frontend-api-rerun.log`. The three YAML failures
  exposed a real `FOR UPDATE` inside a native read-only Work snapshot.
  The original SQLite query used a plain SELECT; the native query now matches
  that contract without dropping generation, state, attempt or frozen-field checks.
  A fresh focused run of the three YAML cases plus the new read-only regression
  passed **4/4**, 8.15 s. Full API repeat is running.
  The skipped live-provider case requires explicit account authorization and
  is separate from the no-skip local Docker/host acceptance requirements.

### Pinned native client suite and Core v2 (2026-10-12)

- The earlier r6 client diagnostic reported **43 failed, 605 passed, 1 skipped**,
  but it is not pinned-environment acceptance evidence: its run manifest names
  the workspace `.venv/bin/python` at
  `/home/felni/.cache/caos-spec021-evidence-20261012/client-diagnostics/run-20261012-r6/run-info.txt`.
  That diagnostic result is retained as history, not treated as a product
  regression or a green/failed result for the pinned environment.
- Core migration v2 adds the four query indexes
  `idx_memory_scope`, `idx_memory_updated`, `idx_memory_type`, and
  `idx_memory_rel_lookup`. The regression pins the original v1 checksum
  `cb4d6f0973a20961854be52f2ac90fd93bd5fbdf6eb06e5b9b2f2fcb3038aae1`, upgrades
  a v1 row without changing its data, verifies the v2 indexes and runtime-role
  DDL denial, and proves a failing v2 migration rolls back without removing v1.
- Native vault tests preserve the maximum admitted `related_keys` value of
  1,023 characters. The reviewed legacy SQLite importer fixture includes that
  value in its main source and checks its exact imported bytes; the fresh native
  constraint rejects 1,024 characters. This proves the reviewed catalog limit,
  not compatibility with unreviewed or uncatalogued SQLite schemas.
- The corrected pinned r11 client suite is **654 passed, 1 skipped**, 493.21 s,
  exit 0. It ran all of `test/clients` with Python 3.12.13 from
  `/home/felni/.cache/caos-spec016-validation-venv` against the isolated ext4
  snapshot `/home/felni/.cache/ct7-owned/source-r11`; preflight confirmed the
  project, tests, pytest, psycopg, and SQLAlchemy imports came from the pinned
  environment/snapshot and that no workspace source path was on `sys.path`.
  Log and manifest:
  `/home/felni/.cache/ct7-owned/run/r11/clients-full.log` and
  `/home/felni/.cache/ct7-owned/run/r11/result.json`.
- The only skip is
  `test/clients/test_tmux_lookup_error.py:83`: the installed libtmux predates
  the `neo.parse_output` listing parser. Reproduced with the same pinned
  environment: **29 passed, 1 skipped** in 17.24 s;
  `/home/felni/.cache/ct7-owned/run/r11/skip-reason.log`.
- The first pinned r9 full run had **653 passed, 1 skipped, 1 failed** because
  the test helper seeded an unreferenced metadata row into the historical
  recovery-bundle fixture. The helper now makes the maximum-length seed
  opt-in and enables it only for the importer fixture. The affected recovery
  test plus the full importer-profile module then passed **4/4** before the
  corrected full r11 run.
- Frozen source hashes in r11: `database.py`
  `6827d177e8f32df41fbbf804b58aae70f9066427da6ef0d65b679a57c0b25a39`,
  `test/postgres/fixtures.py`
  `5b17faf138ad9fd6a63fbc923a8ff9b4fef7a79ed1fcb25e30560ab372ffe3c5`, and
  `test_postgres_legacy_import_profiles.py`
  `054e0af757abf9ad22918da0d8ab6e7dafb6b056b23c8da2d5a6988f2b4473a5`.
  This client-suite result does not close the separate full backend, Docker,
  host, or Spec016 acceptance gates.

### T020 SQLite runtime path audit — 2026-10-12

The current source audit found no live SQLite database connection path under
`src/cli_agent_orchestrator`; remaining SQLite connections are confined to the
explicit legacy importer and read-only historical-artifact validators. The
call-path classification, T009–T019 source reconciliation, and isolated fresh
Graphify evidence are recorded in [runtime-sqlite-audit.md](runtime-sqlite-audit.md).
T020 is checked for this source-audit criterion only. T021 and the global
backend, Docker, host, composition, and final validation gates remain pending.

### Stopped-SQLite import diagnostic retry — 2026-10-12

The earlier r3 actual import attempt failed: the install CLI exited 1 after
reporting `Docker exec failed (exit 2)`. That run did not preserve the failing
Docker command's stderr, so its root cause is unknown. Its overlap with the
T097 run is only a timing correlation; it is not evidence of a resource-related
cause.

A test-only diagnostic wrapper then reran the real three-scope import and
normal-install acceptance against the same immutable application image
(`sha256:65e9f82d5e1380fe8b3d26740200379e1b0178d30bff2d6880783366f99ee2a8`)
and current installer source (`scripts/docker_install.py`, SHA-256
`2877dd94508889aff0d864abdbcb519d4e11f9fa28ae2b9e18977861286e0996`). The
corrected run passed **1 test in 71.34 s**, with zero captured failing Docker
execs, no pytest warnings, and the short private temporary root on ext4. Its
mode-0600 evidence is `frontend/docker-import-r4-diagnostic/logs/`
`sqlite-import-diagnostic-r2.log` and `diagnostic-summary-r2.json` under
`/home/felni/.cache/caos-spec021-evidence-20261012/`. The test snapshot used
diagnostic harness SHA-256 `3bf9b9b53909355dde2fe1a534f7459d21bf5c1dbd235ad0c3237fdc0448ccf6`;
the workspace diagnostic harness was subsequently adjusted only to preserve
the install CLI's normal error handling (current test SHA-256
`bf67eb944370fe2a6badfb508c29f491cddba1b0945fed4849fefb3359b9fc06`).

This successful rerun does not establish the cause of the r3 failure and is
not the final-current-source Docker gate. A fresh image build and actual
install/import run against the post-T049/T079 source snapshot remain pending.

### Current Web unit and build gates — 2026-10-12

The frozen Web source snapshot passed **504 tests in 45 files** with one Vitest
worker (58.81 s), followed by `npm run build` (exit 0, 9.05 s). Node was
v24.19.0; the private `node_modules` copy matches Web's package-lock SHA-256
`fa5918d5d963ea747be496ecb0fae7e6537229ea523f1b611b2a371217b63583`, and
`HOME` was unset. All 621 source/test/fixture file hashes matched the workspace
before the run; all non-generated source hashes remained unchanged afterward.
The private run manifest is `frontend/Web-final-ui-r1/source-manifest-ui-r2.json`;
logs are `frontend/Web-final-ui-r1/logs/web-vitest-r3.log` and
`web-build-r3.log` under `/home/felni/.cache/caos-spec021-evidence-20261012/`.
The build emitted Vite's large-chunk notice. These unit/build checks do not
replace the pending current-source 20-case browser acceptance.

### Work row-lock order regressions — 2026-10-12

The T097 retry produced a PostgreSQL deadlock between a transition writer and
the Bubblewrap release path. Before the fix, the transition updated
`work_attempts` and then appended a `work_events` row whose job foreign key
needed a lock; the release path held the job row before waiting on the attempt.
The preserved real-PostgreSQL RED is
`/home/felni/.cache/caos-spec021-evidence-20261012/ledger-deadlock/red-regression-r2.log`
(one failed regression in 9.15 s, with `DeadlockDetected` while locking
`work_attempts`).

`WorkRepository` now acquires locks in job → item → attempt order before child
row mutations and receipt/event foreign-key inserts. Writers reread after a
lock wait, validate the expected identity, generation, revision, and state,
then use conditional updates with affected-row checks. The caller's transaction
and existing grant/revocation serialization remain in force. Frozen source
SHA-256: `src/cli_agent_orchestrator/clients/work_repository.py`
`d48a9488dc037bf5281ef138646cd3ac85e2ec05d64524050b3a0a557bab1916`.

The new native regressions in `test/clients/test_work_repository_deadlock.py`
cover the release/transition interleaving, stale expected revision and state
after a confirmed job-lock wait, and same-transition replay after waiting for
the winner. The stale revision/state cases are parametrized. The final focused
run passed **4 tests in 15.78 s**, exit 0; test SHA-256 is
`ff03d1897c9149146b1785c24d42910233931d3f13c5e5aeee02d85a84cf405a`. Its
mode-0600 log is
`/home/felni/.cache/caos-spec021-evidence-20261012/ledger-deadlock/deadlock-boundary-regressions-green-r3.log`.
The broader focused WorkRepository batch passed **93 tests, 2 warnings** in
302.17 s, exit 0; log:
`/home/felni/.cache/caos-spec021-evidence-20261012/ledger-deadlock/work-repository-focused-r1.log`.

Static checks on the WorkRepository edit passed with the project configuration:
Black, isort, `py_compile`, and `git diff --check`. This bounded WorkRepository
evidence is not the complete backend acceptance; the full backend and final
composition gates remain separate.

### Additional Work scheduler lock-order REDs — 2026-10-12

Follow-up concurrency probes exposed scheduler lock inversions outside the
T050 repository transition path. These preserved real-PostgreSQL RED results
record the defects that T051/T052 addressed; the final green evidence follows:

- Ordinary release versus public claim: **2 failed in 14.71 s**, with
  `DeadlockDetected`, in
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/scheduler-release-lock-red.log`.
- Revoked terminal batch withdrawal versus claim: **1 failed in 12.51 s**, with
  `DeadlockDetected`, in
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/scheduler-claim-withdraw-red.log`.
- Public enqueue wrapper versus claim: **1 failed in 10.05 s**, with
  `DeadlockDetected`, in
  `/home/felni/.cache/caos-spec021-evidence-20261012/audit/scheduler-enqueue-claim-red.log`.
- A separate Design batch reproduced a scheduler inversion: **1 failed in
  12.85 s**, with `DeadlockDetected`, in
  `/home/felni/.cache/caos-spec021-evidence-20261012/design-scheduler-lock/batch-red2.log`.

The final scheduler fix is frozen at
`src/cli_agent_orchestrator/services/work_scheduler.py` SHA-256
`e59383000eadcf8a889879e7b56467ecceda8042ca48aa812ec424e893cd0dcb`. The
WorkRepository SHA-256 is
`3f92c82ea6d33817aeb7aaa36617815d01defb268a38556336cb702da733e0c4`; compared
with the T050 baseline, it adds only the audited-write transaction context, and
the 77 pre-existing methods remain byte-identical. The T050 lock-order methods
were not changed.

T051/T052 are complete: **12 focused PostgreSQL regressions passed** across the
repository and scheduler interleavings, and the broader scheduler module passed
**80 tests, 2 warnings** in 264.77 s. Focused evidence:

- `audit/scheduler-release-lock-green.log`: **3 passed**;
- `design-scheduler-lock/scheduler-lock-green.log`: **1 passed**;
- `audit/scheduler-release-lock-final4-e593830.log`: **4 passed**;
- `design-scheduler-lock/final-e593830/scheduler-lock-order.log`: **4 passed**.

The broad log is
`/home/felni/.cache/caos-spec021-evidence-20261012/design-scheduler-lock/scheduler-surface/scheduler-surface.log`.
The focused logs are under
`/home/felni/.cache/caos-spec021-evidence-20261012/`. These regressions cover
replacement release, ordinary release, revoked batch withdrawal, and public
claim/enqueue interleavings. They close T051/T052, not the global backend or
composition gates.

### Legacy memory-access audit verification fence — T053 complete

The legacy memory-access writer pays repeated full component fingerprint
verification on a path that must still preserve the audit fence. T053 is
limited to avoiding a redundant verification before the write fence: the write
transaction must acquire the configured Work target and its store fence,
perform full schema verification before entering the audit body, then commit or
roll back and close the connection on every path. Verification remains
mandatory and private; no public `verify=False` switch, catalog-check bypass,
audit-event reduction, privacy relaxation, or changed audit limits are part of
this task.

T053 is complete. The frozen implementation adds the private
`audited_write_transaction()` context and routes the legacy append through it;
schema verification remains after the store fence and before the audit body.
The WorkRepository SHA-256 is
`3f92c82ea6d33817aeb7aaa36617815d01defb268a38556336cb702da733e0c4`, and the
`knowledge_policy.py` SHA-256 is
`ab400ecccb542f56449607ac9d6999b90be65ff9250a8cb4374bd37ad4d8e5ca`. Relative
to the T050 baseline, the repository change is confined to the new context;
existing methods and the T050 lock-order implementation remain byte-identical.

Real-PostgreSQL evidence: `legacy-audit-final-r2.log` reports **23 passed**,
`memory-policy-graph-final-r2.log` reports **46 passed**, and
`commit-failure-focused.log` reports **1 passed** for the deferred foreign-key
commit-failure rollback case. The test module SHA-256 is
`290ebe3574ec8b85eb7227e4c9b7d7fccd70d63ee495815bce34d52ed58f4d66`. The
provenance receipt is
`/home/felni/.cache/caos-spec021-evidence-20261012/t053-audit-fence/logs/commit-failure-prelaunch-r2.json`.
Five additional cold-path samples passed; the existing two-second assertion in
`test/graph/providers/test_memory_provider.py` was unchanged. The individual
logs are `nfr-sample-1.log` through `nfr-sample-5.log` in the same `logs/`
directory. This closes T053; it does not replace full-backend validation.

### Fresh catalog verification under concurrent dispatch — T054 complete

The current approved design batches the nine independent structural catalog
reads into one fresh SQL statement snapshot. T054 must preserve exact result
ordering and normalization, completeness checks, errors, manifest digests, and
migration checksums. It must not cache catalog results or alter grant/job lock
modes, transaction semantics, or timeouts.

T054 is complete at the bounded component level. The frozen implementation in
`src/cli_agent_orchestrator/clients/postgres/migrations.py` has SHA-256
`cda12c08da6df2a16d42941966b181253ffd4dda22d7ed623b13828ed1dcdb49`; the new
real-PostgreSQL coverage in `test/clients/test_postgres_catalog_batch.py` has
SHA-256 `e07a391667188820ffc28b7df1cdc624ccab4aa230dd4cf87df6d5daf373815d`.
The bounded module passed **26 tests**. The unchanged ten-contender dispatch
regression passed in **35.78 s**, preserving two sends, eight pending requests,
and its existing ten-second budget. The comparison covered main/auth/peer
targets, empty categories, nested routines, caller-owned snapshots, schema
drift, exact manifest/digest parity, and the JSON serialization contract. The
nine catalog query fragments remained byte-identical; the batched statement
uses JSON rather than JSONB so even unordered serialization is preserved.

The private evidence receipt is
`/home/felni/.cache/caos-spec021-evidence-20261012/runtime/catalog-final-r2/t054-catalog-batch-evidence.json`.
This closes T054's bounded implementation and regression scope; complete
backend acceptance and final composition remain pending.

### Project composition check — T039 complete

The current composition command completed with exit 0. The retained output at
`/home/felni/.cache/caos-spec021-evidence-20261012/root-final-checks/project-composition-check-t054.log`
records 427 files, 1,811 dependencies, five kept architecture contracts, and
zero broken contracts. This closes T039; final system composition review and
the complete backend acceptance remain pending.

### R11 backend attempt and remaining acceptance gates — 2026-10-12

The full R11 backend run completed with exit 1. Its retained reports record
**16,984 passed, 119 failed, 27 errors, and 115 skipped** from 17,245 collected
tests; `coverage-full.json` reports **85.2058%** line coverage. The pytest log,
JUnit, JSON/XML coverage, and exit receipt are preserved under
`/home/felni/.cache/caos-spec021-evidence-20261012/backend-r11/artifacts/`.
This run is diagnostic evidence, not a passing backend acceptance.

A follow-up selected run of the 114 Git-related failures passed 110 and failed
4. The four failures were two SQLite-style `?` placeholders in
`test/services/test_continuation_responsiveness.py` and two prepared-store
fixture lookups that omitted the configured workflow scan directory. Transport
updated the affected tests; the focused follow-up passed **7 tests** with exit
0, recorded at
`/home/felni/.cache/caos-spec021-evidence-20261012/interaction-transport-green-f6046ae2c8/artifacts/green.log`.
That focused result does not replace a fresh full-backend run.

The T097 Guest80 execution passed **80 tests with 0 skips**. Its retained run
and controller logs are
`/home/felni/.cache/caos-spec021-evidence-20261012/runtime/t097-guest80-r11/runs/attempt-guest80-3/attempt-20261012T185807Z.q8e1kD.log`
and the adjacent `controller.log`. The separate T098 supplemental partition
completed with **4 passed, 0 skipped**, exit 0, in 312.02 s. Its run log is
`/home/felni/.cache/caos-spec021-evidence-20261012/runtime/t097-guest80-r11/t098-supplement-r1/runs/attempt-20261012T195632Z.RBBVcL.log`.
These two guest partitions are green; they do not change the failed R11 full
backend result or close the remaining global gates.

For T055, the accepted design is one opt-in shared bounded memory manager with
a hard cap of four physical backends total across all profiles and generations,
and at most one backend per profile. A retired backend that is still active or
awaiting close continues to count toward the shared cap. A per-repository cap
was rejected because repositories retain their backend for concurrent API
requests, which would multiply the physical limit. The pre-batch composition
module passed **11 tests, 3 warnings**, exit 0, in 19.42 s. That receipt is
`/home/felni/.cache/caos-spec021-evidence-20261012/audit/t055-pool-composition/composition-green-r2.json`;
it records the source state before reset batching and must not be attributed to
the later implementation. Its log records two Pydantic deprecations and one
Python warning about forking from a multithreaded process. The tested source hashes were
`legacy_memory_audit_repository.py`
`98ee1e4fbf75b3e62adacba97f49ad35ea7739d259c2ca925d7ff44d1489ff0a`,
PostgreSQL `connection.py`
`3748b0d6432ce67c3d3b19c13c9474305798b38bfbad032d92bb445695863b9c`, and
`test_legacy_memory_audit_pool_composition.py`
`c027b1a426c04a752b229e9658a72e8b3ff9c97dc0311f908fcf565c83e177a1`. Design
also reported strict mypy passing for those pre-batch source modules.

The later reset-batched affected-module run recorded **46 passed, 1 failed,
2 warnings** in 39.16 s. The failure is the existing one-second memory index
round-trip assertion, which measured 1.268 s. A separate cold-path run measured
1.712 s, and the valid instrumented measurement was 1.157 s; each remains over
the unchanged one-second limit. Logs are
`/home/felni/.cache/caos-spec021-evidence-20261012/design/t055-final-r1/logs/affected-modules-batched.log`,
`nfr-batch-cold-1.log`, and `nfr-timing-r4.log` in the same directory. The
controlled reset-versus-no-reset diagnostic found no benefit from resetting
the session and is retained at
`/home/felni/.cache/caos-spec021-evidence-20261012/runtime/t055-reset-config-ab-r1/result.json`;
it is diagnostic, not NFR acceptance. The memory NFR remains unmet and must not
be addressed by relaxing the assertion.

The earlier batched helper
`legacy_memory_audit_repository.py` SHA-256
`a99d43770762a14c4c676fc0f072ab74fd9c1ea008cf936bd4583bc4610346b9` was
verified against both the workspace and Design's isolated snapshot. Its updated
composition suite passed **11 tests, 3 warnings** in 9.94 s at
`/home/felni/.cache/caos-spec021-evidence-20261012/design/t055-final-r1/composition-r3/composition.log`;
the basic pool module passed **40 tests, 2 warnings** in 28.25 s at
`/home/felni/.cache/caos-spec021-evidence-20261012/design/t055-final-r1/basic-r1/basic.log`.
These prove the bounded composition and pool regressions for that helper hash,
before the custom-plan update; they do not clear the one-second NFR failure.

The custom-plan helper now has SHA-256
`1c65b40892e785a799ed3fbedecde33da32d32cdd4375efb6aa082331e820db3`. The
focused replacement-session regression was **1 failed** in 3.72 s before the
custom-plan fix, then **2 passed** in 4.18 s afterward. The final correctness
module passed **51 tests, 3 warnings** in 34.81 s. These logs are
`/home/felni/.cache/caos-spec021-evidence-20261012/design/t055-final-r1/custom-plan-red/red.log`,
`custom-plan-green-r1/green.log`, and `final-correctness-r1/correctness.log`
under the same directory.

The custom-plan NFR runs still failed the unchanged one-second assertion at
1.148 s, 1.126 s, and 1.140 s; their logs are under
`nfr-custom-plan-r1/run-1/`, `run-2/`, and `run-3/` (`pytest.log`). A separate
canonical-cache probe measured 1.100 s and also failed; it added no
product-level cache. The read-only plan-mode comparison at
`/home/felni/.cache/caos-spec021-evidence-20261012/runtime/plan-cache-mode-abc-r1/run-001/result.json`
recorded 18 matching digests per mode, with median catalog-call times of
33.872 ms (`auto`), 28.791 ms (`force_custom_plan`), and 34.953 ms
(`force_generic_plan`). That diagnostic does not meet the end-to-end NFR. T055
remains incomplete; the threshold is unchanged.

A pre-fix native-guard probe is retained at
`/home/felni/.cache/caos-spec021-evidence-20261012/root-final-checks/t055-native-guard-red-r1/result.json`.
It recorded `SchemaMismatch` for a plain non-native connection before any
database calls. The receipt's helper hash is the pre-fix version and is
diagnostic RED evidence only. The actual memory NFR and
broader regression modules remain under validation; T055 and full R12 backend
acceptance are not yet complete.

A fresh full R12 backend run must include the frozen T055 result and corrected
focused fixtures, use the private Git-child configuration and per-worker home
isolation, and produce complete JUnit and coverage reports. The R11 result is
diagnostic, not a passing backend acceptance; overall PostgreSQL migration
acceptance remains **in progress**.

### 2026-10-14: interrupted R12 and focused recovery

The R12 retry under `backend-r12-retry2` did **not** complete. Its owned runner
receipt records exit status 143, outcome `interrupted`, and phase
`signal-TERM:pytest` at 2026-10-13T04:55:23Z. Output reaches 94%; the JSON result
is truncated and there is no complete JUnit or coverage report. The two valid
worker failure logs contain 31 failed reports, including one setup failure.
Those logs are diagnostic evidence, not a final suite result. The source of
the termination has not been established.

Recovery prioritizes reproducing shared memory initialization failures,
checking outdated integration-test injection seams, and retaining the strict
one-second round-trip requirement. On 2026-10-14 the former disposable
PostgreSQL container was found stopped; a new owned private service is being
provisioned before executable reproduction. Prior attempts and artifacts are
preserved. No completion, coverage, deployment, or publication claim follows
from this recovery work.

The six integration mock failures were reproduced against the exact R12 test
baseline on the new native PostgreSQL 18 fixture. The nested audit injection
had patched the base connection method, while the private pool overrides it;
the owner target mocks also rejected `home` and expired before later checkouts.
The corrected test injects `SELECT 1/0` through the actual pooled connection
only for the relationship audit insert and asserts that injection occurred.
Target overrides now cover each complete operation. No schema trigger or
runtime retry was added. After formatting, the full module passed **25 tests**
(2 warnings, 35.50 s), with Black, isort and diff checks passing. Final test
SHA-256 is `0944d8941f4e01a05237d0d39be34d50db3cbcdd4c7f7b1e891bd1856b2d9c9c`;
receipt: `/home/felni/.cache/caos-spec021-evidence-20261014/backend-knowledge-test/artifacts/pytest-result-knowledge-final.json`.

T056 root cause was reproduced on native PostgreSQL: warming the worker audit
session, cleaning a sibling schema with the original fixture, then checking out
the worker session raised `AdminShutdown`. Both schema fixture teardowns now
retire only their exact private audit targets before dropping owned schemas;
database-wide termination remains at the database-owning worker teardown.
The final single regression invokes the actual fixture generator, proves the
worker backend PID remains unchanged, verifies sibling sessions are retired,
and checks durable `authorized`/`completed` audit phases after cleanup. The
native audit/pool/composition group passed **52 tests**, 6 warnings, 91.95 s;
Black and isort passed. Log:
`/home/felni/.cache/caos-spec021-evidence-20261014/auditpool-native.ZFlSfQ/artifacts/pytest.log`.
Final fixture SHA-256:
`fde46779fe7d28b6de8d0287aee6cdd01a3ada6fe96b6435795939f5da2956f4`;
regression SHA-256:
`454ccc5b69a0f07f467d1db002bca350faca7574aac2d93813e04dadb2bdfdd5`.
Runtime retry behavior was not changed. The full R12 failure-module group and
global acceptance remain pending; these 52 tests do not clear the NFR.

The actual T057 source passed its focused native suite (**34 tests**) after
formatting. The subsequent full-module diagnostic group used four workers and
all 24 modules named by the interrupted R12 failure logs plus the new fixture
lifecycle module: **551 passed, 0 failed/errors/skips/deselections**, 26 warnings,
165.77 s. All 31 historical failing node IDs were present and passed, including
the strict round-trip test. Pre/post source parity and inherited HOME/CODEX_HOME
checks passed. Manifest SHA-256:
`b4333c23f0f81bfbc897549ba876474b32978fad87e54ddb02801a769ad0dcf5`;
receipts/JUnit/stdout:
`/home/felni/.cache/caos-spec021-evidence-20261014/failure-modules-r1/artifacts/`.

Cold acceptance is stronger than that warm module-group result and remains
unmet: exact current source on an ext4 snapshot produced 1.006143 s (fail),
one pass below 1 s, and 1.020660 s (fail). A prior workspace/OneDrive-source run
measured 1.118 s. Lazy import cost is under investigation; timed validation must
use byte-identical source and preserve actual operation initialization and all
authority checks. The separate source diagnostic confirmed 21 full checks and
20 audit transactions. Neither the private prototype's three passes nor the
551-test group closes the required three actual-source cold passes or global
backend acceptance. T055/T057 remain unchecked.
# 2026-10-14 final-freeze preparation (T058/T059)

Final T059 source checks subsequently passed: **17/17** native connection,
borrowed/leased cursor and complete PostgreSQL journal cases; full Black
**1,318 files**, isort, five Import Linter contracts and the actual
`project-composition-check caos` each exited 0. Full mypy remains clean at
425 source files. Exact source/home receipts and exits are under
`t059-final-group-r1/artifacts` and `t059-final-validation-r1/logs` in the
2026-10-14 evidence root. T059 is closed; T055/T057/T058 cold performance and
the complete R13 coverage gate remain open.

Subsequent T059 correction: full mypy now passes **425 source files** without
suppressions. Seven real-PostgreSQL adapter-fault tests went RED with the old
None indexing errors and GREEN **7/7**, checking classified rejection, zero
subsequent DML and unchanged journal state. Queries and valid MAX NULL values
are preserved. `test_step_contract.py` plus `test_postgres_registry.py` passed
**50/50** on the corrected source. Receipts: `t059-cursor-none-guards-r3`
(RED), `t059-cursor-none-guards-r4` (GREEN), and
`t059-cursor-none-guards-static-r1/mypy.log` under the dated evidence root.
The final connection/lease and journal checks are still running; this is not
a global-backend or performance acceptance claim.

T060 restores the native replacement-target startup scenario: the old test's
engine selected the same worker target; the corrected test selects a distinct
owned target, repairs exactly four surviving wiki identities, checks second
startup idempotence and preserves the prior target's sentinel column-for-column.
The entire startup module passed **3/3**, with forbidden model/network calls
unchanged. Test hash:
`68da7bb1604d80ea7fd80b39fba3e73b99d7e8c4e1f4e4788f4eec14cfcec321`.
Evidence: `final-composition-r1/t060-startup-green-r1` under the same dated root.

T058 was checked against an exact source copy on ext4, with source hash parity
before and after all three independent cold processes. The unchanged `<1.0s`
assertion failed at **1.019173s, 1.009456s and 1.005604s**. The earlier private
candidate's three passes do not replace these actual-source failures.
`memory_service.py` is `b372dd79b6d1e7c57f7d14fc34f0b97c5161b139b14252b387b6d91e09dd8938`;
`migrations.py` is `c81713550594511e3386b3aff7e47b7af442e509d62d51055d678a3e426f94af`.
Actual native verification-count proof passed **1/1**, retaining 21 full checks
and 20 independent audit commits. Receipts are under
`/home/felni/.cache/caos-spec021-evidence-20261014/nfr-analysis/t058-importmove-source-r1`.
T055/T057/T058 performance acceptance remains open.

The new offline import test initially failed with `KeyError: PYTHONPATH` when
that environment variable was absent. It now derives the actual package root
from MemoryService and passes an explicit child search path, preserving
inherited HOME/CODEX_HOME. Both no-PYTHONPATH and unrelated-first-path scenarios
passed **2/2**; Black/isort passed. Final test hash:
`d80f8ba74704450a5c80daad686471e791ea10f03e8783fe5aca4fad9427c5df`.
Evidence: `nfr-analysis/import-test-portability-r1` under the same dated root.

Fresh configured static checks passed Black (1,317 files), isort, all five
Import Linter contracts and `project-composition-check caos`; that command
runs architecture checks, not the full integration suite. Full mypy reproduced
six `no-any-return` errors in WorkRepository, work_mcp_proxy and
work_provisioning. T059 traces these to the shared cursor's erased row type;
its correction and native regression checks must precede final freeze.
Initial static receipts: `final-static-r1` under the same dated root.

R13 has not yet been launched. Its initial private snapshot preserves 3,133
entries, the 4,191-path Git index, 40 tracked corpus paths and unchanged HEAD.
Retain that initial receipt and explicitly amend only the reviewed portability,
cursor type and specification paths before accepting the final snapshot.


## Complete R13 regression — 2026-10-14

Accepted manifest `b4e9402e56d5ab2b58dfcf39f50cd8b7c0054334bfb6e5ed7032599015295c49`
contains 3,134 entries. The complete eight-worker run collected 17,333 cases:
**17,211 passed, 9 failed, 113 skipped, 0 errors**, in 2,908.30 seconds.
Runner and pytest exit were 1. Coverage is **68,979 / 79,530 statements =
86.7333081856%**, below the unchanged 87% floor; the rounded terminal display
is insufficient. The current denominator needs 213 additional covered statements.

Failures: one ten-process dispatcher grant-row lock timeout; the unchanged
memory round-trip measured 2.081172215 seconds; all six continuation cases
failed because their snapshot helper resolved `current_schema()` to pg_catalog;
the twenty-source metadata visibility profile measured 5,289.3 ms p95 against
2,000 ms. None was filtered or converted into a pass.

The postrun source verifier passed at 03:10:08 UTC: zero drift for all 3,134
workspace/snapshot entries, 4,191 tracked paths and 40 corpus paths. Six
test-generated cache directories containing 16 bytecode files were preserved
with hashes/mtime/source provenance in `artifacts/postrun-generated-cache-r1`
before rerunning the bytecode guard. Workspace HEAD/index are unchanged. The
private snapshot raw Git index hash changed during the suite; its staged
entries, tracked paths and HEAD still match the workspace. That distinction
is retained rather than claiming raw index byte parity.

Evidence root: `/home/felni/.cache/caos-spec021-evidence-20261014/backend-r13/artifacts/`.
R13 is a complete failing run, not final acceptance. T061–T063 remain open.

## Follow-up evidence and remaining gate — 2026-10-14

The final T061 adjacent native group collected 142 cases and exited 0 with
no failures, including the unchanged ten-process/two-slot regression and the
concurrent retirement/held-claim tests. Receipt:
`t061-retirement-module-group-r2/run/artifacts/pytest-result-module-group-retirement-r2.json`
under the dated evidence root. T062's continuation group passed all seven cases.
T064 functional verification passed 82 unique native PostgreSQL cases across
two groups (85 passing executions including overlap), plus 17 offline cases.

New focused transport, workflow/turn, and interaction contracts passed 8, 48,
and 17 cases respectively. Exact intersections with R13 missing statements
in unchanged, hash-matched source files cover 54 + 95 + 40 = 189 previously
missing statements. These are focused results, not a new global coverage
percentage; changed production source changes the final denominator.

Independent source composition review found no blocking T061/T064 defects:
`t061-t064-final-review/artifacts/composition-review-final.md`, SHA-256
`a0e410f6201a4cdf001cb5e40044d72a6b413543a482de2c16de057503329cf6`.
It distinguishes the pure scheduler selector from provisional rolled-back
path probes and durable cleanup. This review did not execute tests.

Three fresh cold strict NFR runs against actual T064 source completed with
coverage enabled and unchanged test/threshold: **one failure, two passes**.
Run 01 measured 1.016984846 seconds against `< 1.0`. Runs 02 and 03 passed;
pytest reported call durations of 0.99 seconds, not exact internal timer
values. JUnit testcase durations include fixture setup/teardown and must not
be treated as the measured body. All pre/post source guards passed against
the 3,141-entry manifest
`34d316148248e7967501a722f4f70e5802ace832ef8754d7e2fb3094274e36bd`.
Evidence: `nfr-analysis/t064-final-strict-r1/run-{01,02,03}/artifacts/`.
The required three-of-three performance acceptance remains OPEN. No best-of
selection, threshold relaxation, or test-body changes were used.

R14 full regression and final-source Docker gates have not been launched.
Their private runner preparation proceeds independently while a separate
unscored profile identifies the remaining latency cost.

## SQLite retirement and current verification — 2026-10-14

CAOS now uses native PostgreSQL persistence. The SQLite importer, legacy
catalog/readers, schema emulators, functional fixtures, old OKF converter and
import CLI implementations have been removed. Remaining old command/archive
spellings enforce rejection before writes; raw `.db`/`.sqlite` exclusions prevent
copying retired artifacts. They provide no connection, reader or import mode.
`DATABASE_FILE` remains a stable artifact anchor, not a database backend.
Installed PostgreSQL DDL, migration checksums and read-only historical receipt
schemas/rows are preserved. The canonical contributing skill now describes
PostgreSQL errors and matches its packaged copy.

### Complete R21 run

Source manifest `e67627b4766a19ad15cea4da9984e07ac46a02133f4ce6013218bd632d2268e1`
contains 3,106 entries. The eight-worker backend run collected 17,390 tests:
**17,252 passed, 15 failed, 113 skipped, 12 errors**, with 131 warnings in
10,249.99 seconds; exit status 1. Its independent postrun guard passed with
zero workspace/snapshot drift, unchanged HEAD/index and a complete scheduler
trace. This is a complete failing run, not global acceptance.

Actual backend statement coverage is **67,793 / 77,816 = 87.1196155032%**,
meeting the unchanged 87% floor. Apps' separate 90.01% result remains earlier
evidence against unchanged Apps source; the combined strict ratchet was not
executed because R21 failed. No threshold or exclusion was changed.

The R21 run passed all five PostgreSQL-only source guards, 70 installer cases,
34 deployment cases, native schema/role/pool contracts and modern OKF tests.
The R21-bound pure wheel/sdist guards also passed. Cargo is unavailable;
these receipts do not claim a native Rust build or a published release.

### Real Docker gates and focused follow-up

All three R6 Docker gates passed against R21 controller image
`sha256:dc76943472901241628ab9f21e1359cc6ca4a7670dd80b11c2f20c0466778455`:
installation/upgrade/rollback (271.80 seconds), native main/auth/peer backup
and isolated restore (125.66 seconds), and browser authentication (28.98
seconds). Restore used a private UID/GID-owned 0700 tmpfs HOME, retained explicit
destination/configuration checks and preserved the backup. Earlier R4/R5/R20
failures remain recorded; the R6 pass does not rewrite their results.

After R21's postrun guard, four files received reviewed documentation/test
corrections: one canonical skill error example, three teardown error-message
expectations and two functional test clocks. The cycle test freezes time before
building real PostgreSQL delegations and advances past their original TTL.
The optional-receipt test retains real PostgreSQL/snapshot/wait/settlement paths,
the 0.5-second virtual budget, two receipt observations and an 8-second real
outer guard. This tests behavior, not latency. Production source is unchanged.

Fresh normal-fixture serial regressions passed **113 cases** for skill parity
and session teardown, **14 cases** across both interaction modules, and the
**18 other unique R21 failed cases**. The last group includes historical receipt
capture/blocked restore, scheduler concurrency and remote bridge recovery;
all 484 selected runtime/test/fixture inputs matched R21 before and after it.
All 14 shipped skills passed the read-only synchronization check. These 145
passing executions supplement R21; they do not constitute a new full green run.

### Open acceptance

The unchanged memory round-trip assertion failed in R21 at
**1.167501597 seconds** against `< 1.0`. Three supplemental cold covered runs
also failed at **1.224877700**, **1.004884519** and **1.552827495** seconds.
Three-of-three strict NFR acceptance and the complete global gate remain open
under T076. During the late full run, read-only measurements found severe host
CPU/memory/I/O pressure, no free swap, and PostgreSQL below its connection cap
with no restart/OOM evidence. Those observations do not prove a single cause
and do not turn connection, deadline or assertion failures into passes.

Private receipts are under
`/home/felni/.cache/caos-spec021-evidence-20261014/`: `backend-r21/artifacts`,
`controller-final-r6/artifacts`, `official-skill-teardown-r22/logs`,
`interaction-clock-rerun-r1/artifacts`, and `supplemental-failed-nodes-r22`.
Earlier failed/interrupted runs and immutable PostgreSQL descriptor/receipt
checks remain available under their original evidence directories. All changes
are in the main working directory; no commit, push or deployment is claimed.

### Focused current-source verification — 2026-10-15

The T099–T101 context-tail batching implementation is now in the main working
directory. Root's current-source offline selection passed 18 tests plus 9
subtests; the native PostgreSQL selection passed 8 tests without skips. The
first native attempt exposed a test observer's handling of `psycopg.SQL`, which
was fixed by checking for strings before set membership; that failed attempt
is preserved. Module origins, source hashes, private `Path.home`, and unchanged
parent HOME/CODEX_HOME were checked. Composition reported 399 files, 1,775
dependencies, five kept constraints and zero broken constraints. Independent
source review found no blocking runtime defect. This establishes functional
coverage of the batch port, not latency acceptance: the earlier three-run
candidate cohort passed only one run, so FR-044/T098 remain open.

The six formerly unavailable shell cases now pass with real extracted zsh 5.9
and fish 4.2.1: three isolated-HOME recipe cases and three Kimi shell-boundary
cases. The exact-node run passed 6 tests in 10.79 seconds, without errors or
skips, using `--noconftest`, the pinned interpreter/plugins and a private HOME
shim. Source hashes matched before and after. No PostgreSQL connection or
provider call was made. An earlier recorder failure ran no test body and is
retained separately. Receipt:
`audit/r21-shell-tools-r1/pytest-shell-r2/artifacts`, consolidated SHA256
`5cebfa7afaf418b6c8b2732ade2db2aa73bb2f9605d9003488ce3a6c778cd8f7`.

The private native OpenCode mini R11 harness passed 127 offline tests. Its live
attempt established MCP readiness and a connected MCP log, but timed out after
89.88 seconds with zero questions, answers or acknowledgments. The task remained
in the idle composer. `turn_sequence=1` records dispatch, not provider
acceptance: transport pasted the task and sent one Enter after the configured
two-second delay, but turn processing was not observed. The submission cause
remains unresolved; no extra Enter or task replay is counted as a fix.

R11 cleanup failed because both marker inventories attempted to read unreadable
environments of two same-user processes born before the private tmux server,
outside its group/session. Root subsequently stopped only the positively
identified owned server, after verifying PID/start time, UID/comm, exact socket
registry inode and owning file descriptor. The command exited zero and the
server exited; private files remain preserved. This is scoped server cleanup,
not proof of a complete unbounded marker inventory. Receipt:
`opencode-native-mini-r11/launcher/runs/65822a64d1454b629065a53489f9e899/root-owned-tmux-cleanup-r1.json`
under the private 2026-10-15 evidence root. R11 remains private and is not live
provider acceptance. The final global run, real-provider matrix, host gates and
strict latency cohort remain pending.

### Codex/Claude live matrix and QEMU launch — 2026-10-15

The current-source R3 live matrix completed in 426.082 seconds with exit zero:
**4 passed, 0 failed, 0 skipped**, covering all ordered pairs of Codex
(`gpt-6-luna`) and Claude (`sonnet`). Each cell validated the provider turn,
sibling message delivery, sibling cancellation cleanup, timeout reconciliation
and session cleanup. Quota wait/continuation were explicitly not applicable;
they were not observed as passing scenarios. All source/auth/metadata/HOME and
owned-process-group guards passed. No provider API keys were forwarded and no
global process cleanup occurred. This closes the four selected cells, not the
remaining OpenCode cells or actual-provider AG-UI/API-restart acceptance.

Private matrix receipt:
`provider-auth-stage-r3/matrix-codex-claude-r3/runs/20261015T013435Z-42bc38c1/run-receipt.json`,
SHA256 `7a33d344e8f7d1ab86970373622cdd97caae44f3242b6ba563abc7901b24d1d4`.
The log SHA256 is
`59f2001bab09c22399eb0e24127b41589923c3c2372e9ac20fd268b4b4951ad4`;
JUnit SHA256 is
`2e14f33ebbf23ae12ae417bf5a2bedd15d6f338281cb0e67236d97722454c8b3`.

After the user released memory, QEMU's unchanged 6144-MiB floor passed with
7117 MiB available at preflight. The complete 91-case run was launched
against the verified R6 snapshot, manifest SHA256
`59d5701b9aa4811478def37af33f0b32375e6f4305499354d3b3f9b145c88176`
(3115 entries, including the four accepted context-batch implementation/test
files). Preflight records `qemu-running`; no host-test pass is claimed before
the final group/cleanup receipts. Other intensive acceptance runs are held.


### R6 QEMU outcome and Docker preflight — 2026-10-15

The unchanged 91-case QEMU run completed with **72 passed, 1 failed and 18
not reached**, exit one. The failed selected node was
`test_wait_pidfd_readable_checks_ready_pidfd_with_zero_timeout`: its
`process.wait(timeout=1.0)` setup for `/bin/sleep 0.05` timed out before the
product's zero-timeout assertion was called. This does not establish a
product pidfd defect. The first three groups passed 8, 2 and 1 cases;
the broker group passed 61 and failed this one case. No fatal guest panic,
Oops, BUG or OOM marker was found. The owned runner container and marker
database were removed; cleanup reported no error. Final receipt SHA256:
`96e3b6bd33768015f0f91f18291ff71d6f7e46e1e1b58e280ff53cdbcef2ad72`.
A test-only setup correction and a complete 91-case rerun remain required.

The separately collected T019/T020 selection contains 15 exact unique nodes
(9 T019 and 6 T020). Its R6 execution stopped in Docker preflight because
the pinned scratch image was absent. **Zero test bodies ran**. Rebuilding
with the exact retained source, Dockerfile and builder produced image
`sha256:d903e24e62e89fa2a4d5e7a62b4299a249aa2b7df6312bef0f3c37ebb932ace4`,
rather than the former
`sha256:73edec968c2e0bbfb8e86e8c7cd04a01e57c21b4ddbd7e5534591427af3fe22e`.
The supervisor ELF, image config and filesystem layer match; BuildKit's
attestation manifest changed. No preflight check was bypassed. A new
explicit artifact receipt/pin and review are being prepared before another
actual execution. Rebuild log SHA256:
`7e372f870a2d1789f933db423e9662463397bd51377614c0f55415da68f9cc9e`.


### Native OpenCode harness port in main — 2026-10-15

The reviewed R14 portable harness was copied to the four authorized paths
in the main working directory after exact baseline hash checks. No production
source file was changed. It uses the actual native terminal create/send path,
preserves canonical MCP recognition until the v2 writer boundary, and pins the
private tmux process/socket before descendant inventory. Cleanup refuses unknown
or replaced identities and retains evidence when ownership cannot be proved.

Root independently ran the three offline harness modules from main:
**156 passed, 0 failed, 0 errors, 0 skipped**, exit zero, 8.156 seconds.
Module origins and source hashes matched main before/after; parent HOME and
CODEX_HOME were unchanged. This run used the pinned Python, eight explicit
plugins, early private-HOME shim, `--noconftest`, and short private basetemp.
Receipt SHA256:
`72a6cc6568f77bf889591cbb7d6816e4ebd60ee2d16116ac6fabebb43a18b352`.
The subsequent composition check passed: 399 files, 1775 dependencies,
five kept contracts and zero broken contracts. Independent R14 runtime review
found no blocking defect; the portable mini test removes private-launcher
path dependencies while preserving ownership regressions. This is functional
harness coverage, not successful live OpenCode questions/answers acceptance.
A protected trace diagnostic and a successful native provider run remain open.


### QEMU selected-node setup port — 2026-10-15

The test-only pidfd correction and three off-selector lifecycle regression
tests are now in main. The selected node still calls
`_wait_pidfd_readable(pidfd, 0.0)` with the original assertion. Its owned
`/bin/true` child must first report native `POLLIN`; cleanup covers pidfd-open
failure, readiness failure and success. No production helper changed.

The bounded current-main run passed **4 tests, zero failures/errors/skips**:
three lifecycle/order regressions and the exact selected native-pidfd node.
Root inspected the receipt and verified main test/product origins, unchanged
pre/post source hashes and parent HOME/CODEX_HOME, absence of PostgreSQL fixtures,
63 unique scratch selectors, the target appearing once, and the unchanged
91-case aggregate guard. Receipt SHA256:
`674b8a42f6059d4fe4bc72cac1c730221d7cdae9f8a44cb51f70c8e560725132`.
The full QEMU rerun remains pending; these four host cases do not close it.
See [the bounded setup specification](validation-pidfd-setup/spec.md).


### Actual R7 Docker T019/T020 result — 2026-10-15

After explicit review of the rebuilt image pin, the source-bound 15-case run
completed in 845.991 seconds: **6 passed, 9 failed, 0 errors, 0 skipped**, exit
one; the 45-minute deadline did not expire. The execution and native JUnit
counts agree. All selected cases completed setup and teardown. Seven failures
were Docker CLI `info` calls exceeding the existing 15-second preflight bound;
two failed at the worker MCP response boundary with its existing 5-second bound.
No production timeout has been increased. Passing cases comprise the T019 local
setup/dispatch cleanup test and five T020 retry-fence, pending-after-restart and
YAML result-projection cases. This does not close the Docker gate.

Source before/after remained the same 3115-entry R6 manifest, including the
runtime/test/timeout input pins. The private-HOME and plugin-origin receipts
were present. Per-test teardown passed, but the executor did not establish a
complete inventory proving every possibly created owned container was gone;
no global Docker cleanup was attempted. Execution result SHA256:
`63f8a7cf8fecce53baad19061dd5739c9f1ca0fc24b5746f00a30a0b4d5036e3`.
Native JUnit SHA256:
`b8f2047b146ceb4d1850f99caa0e82b34e763976d1de0854dafec5546bb82d71`.
Summary SHA256:
`7567b749ed44fb5aa219cb45c950809ee4fa4f6c38ef22850efc748b3b3b297a`.

A separate executor recorder defect writes JUnit to the run root but validates
an artifacts subdirectory path. The original failed receipt is preserved;
a shared-path regression/fix is being prepared. Correcting that recorder does
not make the nine test failures pass.

A subsequent bounded read-only diagnostic returned CLI `info` successfully in
1031.984 ms and direct Unix-socket `GET /info` with HTTP 200 in 10.564 ms; both
reported seccomp. This did not reproduce the 15-second failures. The original
child's complete environment was not archived, so client/plugin work and
transient daemon/host load are hypotheses, not established causes. No raw info
response or environment values were retained in this diagnostic's receipt.


### Current R3 execution status — 2026-10-15

This addendum updates only the status of later evidence. Earlier receipts and
results remain preserved as historical records; a later incomplete run does not
retroactively change an earlier run's result.

#### Current-source QEMU 91-case run

The R3 cached QEMU run reached the unchanged 45-minute outer limit: outer exit
124 and inner pytest exit 130. Ten cases passed in the two completed groups
(8 in 870.85 seconds and 2 in 345.43 seconds). A third group started but did
not complete; **81 of 91 cases were not completed**. This is not a complete
current-source QEMU acceptance. The earlier 80-case passing R2 run is historical
and does not satisfy the current R3 91-case gate.

The marker database and run artifacts were preserved. A read-only follow-up
inspection of the exact runner container ID returned “not found”; that result
does not establish that the runner's full lifetime cleanup completed. Cleanup
remains incomplete. Receipt:
`qemu91-final-source-r3-executor-cache-r1/runs/run-62294588b002b9fd-of29f9va/run-receipt.json`
(SHA-256 `58cf49ea661f3382ccf51fc6f89f3209cef3d054810da30cb36eb810c3e60597`)
under the private evidence root.

#### Application image and native Docker acceptance

The source-bound R3 application-image build passed with exit zero. The image ID
is `sha256:eaae878275cfd478f15b97451224d773b46f66f70a5a86144ea94324a7f100d0`;
its private receipt SHA-256 is
`30a5e98830bf8365cf474ab77767c3f5461a998b4d64119c7bf6e646e4a218f9`. Both
source guards passed, existing aliases and container configurations were
unchanged, and the receipt's 427.23-second build duration includes its postcheck.
This establishes the image-build result only; it does not establish personal or
browser install/import acceptance, PostgreSQL restore acceptance, or runtime
continuity.

The subsequent native Docker acceptance attempt stopped at resource preflight
before its first gate. Available RAM was **4,156 MiB**, below the unchanged
**6,144 MiB** floor; available disk was **890,613 MiB**. **Zero pytest lanes
started**, and no native receipt was produced. These lanes are untested in this
attempt, not passing, skipped, or test failures. Evidence directories were
preserved and no global cleanup or resource-floor change was made.

#### Provider and message acceptance

The original Codex R11 inner receipt records a completed two-question journey,
two human answers, and one ACK call that acknowledged both questions (`ack_call_count=1`,
`ack_count=2`), plus one API restart, three stable redacted AG-UI snapshots,
nine passing guards, and complete cleanup. The original outer receipt remains
failed and immutable: its predicate expected duplicated `delivery_states`,
while the safe projector emits a unique set. The separately reviewed R12
recovery evaluates the corrected outer predicate against that original typed
receipt and was approved by root and independent review. Its recovery receipt is
`agui-provider-restart-r11/acceptance-r11-codex-delivery-set-recovery-r1/runner-r12/recovery/r11-codex-delivery-set-recovery-r3.json`
(SHA-256 `5bf13db099f5a48d2a8ddb0f32fc65324523fe716da1534d3916100a1d3c3f9c`).
No provider replay was performed; the original outer failure remains in the
record. The later Codex MCP diagnostic recorded a fixed unavailable category
for the first wait; `isError` was absent, so the tool's semantic success or
failure is unknown. A second wait and ACK succeeded. This diagnostic does not
establish transparent recovery of a wait left in flight across API replacement,
which remains outside the Spec022 acceptance scope.

Claude R10 failed with `cli_exited_without_ack`: zero questions, zero MCP calls,
zero restarts, and no identified cause. Cleanup completed and all nine guards
passed. OpenCode R9/R10 did not produce a functional provider verdict.
The R10 controller receipt reports `process_children_unreadable` caused by
`FileNotFoundError`/ENOENT while reading owned-tree children; 12 groups were
observed, zero remained at the later observation, no inner receipt was
produced, and cleanup remains incomplete. A bounded read-only marker audit saw
zero exact positive markers among 183 same-UID processes, but it also recorded
unreadable or raced process fields (3 cwd, 3 environment, 2 fd directories,
47 fd links). That partial observation does not prove historical cleanup, and
no raw process data was retained. A bounded shared-helper R11 candidate is
still under independent review; no provider rerun or functional result is
claimed.

The Spec023 source-preflight fix passed its 30 offline checks. Its subsequent
actual attempt remained blocked in the shared process-inventory helper
(`process_task_inventory_raced`); cleanup was incomplete, no driver/operator
journey receipt was produced, and zero completed MCP calls were observed. This
is not Spec023 live acceptance.

#### Scoped latency, repository status and outstanding state

The preserved cold NFR cohort supports the strict `<1 s` assertion for **3/3**
original scoped tests. The original outer/controller failure remains preserved;
exact internal elapsed values are unavailable (`null`). Do not substitute an
invented per-run time or present this scoped cohort as the complete NFR gate.

The two-file R11 MCP diagnostic port was integrated at that checkpoint (journey SHA-256
`f6f5860af353e1595be41b1c2a97e77f0db6476c92955c4d18b30933fcc066d9`, test
SHA-256 `712ddaf3b7eca84003519fd9a37591748e6f24f9d485b8d8578fde592d779753`).
The first validation invocation exited 3 before tests because `main/test/live_provider`
was missing from `PYTHONPATH` and module-origin validation failed. The corrected
R2 invocation changed only the command/environment, not code; it passed **293
tests, 0 failures, 0 errors, 0 skips** in **19.606 seconds** with a private
source-origin proof. JUnit:
`root-continuation-r1/main-mcp-diagnostics-validation-r2/offline-junit.xml`
(SHA-256 `aa013fc4e98aae47b296c3fcaaa256c8efc3368713a752a334c4ff0ee4039cdc`).
The post-port composition check also passed **399 files, 1,775 dependencies,
five contracts kept and zero broken**; log SHA-256
`10da5a946e179225b386aea3f1e836db18c54ba8afc657ebcb1c69aeafa5e364` at
`root-continuation-r1/main-mcp-diagnostics-validation-r2/composition.log`.
R13 subsequently fixed an acceptance omission: a completed MCP event whose
protocol result explicitly marks an error must not count as a successful tool
call. `mcp_is_error` values `true` or `invalid` now set `error_present`; `false`
and an absent flag preserve compatibility. Diagnostic text categories do not
set this flag. The reviewed two-file change is integrated in main: journey
SHA-256 `0458faa92d75a30b80aecb43ade38e8cff79a787cf5059de12d809621f81d38e`,
test SHA-256 `f55b44e647c4069c5621b4107b92c83ca31ac63f902bc82c87ef79a33c92c91d`.
The final bounded main run passed **295 tests, 0 failures/errors/skips**
(pytest summary **18.18 seconds**, JUnit suite **20.236 seconds**), with verified
main module origins and private HOME. Its JUnit is
`root-continuation-r1/main-mcp-semantic-validation-r1/offline-junit.xml`,
SHA-256 `07404a8de8a3bf8b74adeceffeaac531318eae48c64793dc7eb56a4a57f080ac`.
The subsequent composition check again kept all five contracts, with zero
broken (399 files, 1,775 dependencies); log content SHA-256 remains
`10da5a946e179225b386aea3f1e836db18c54ba8afc657ebcb1c69aeafa5e364`.
These offline checks made no API, PostgreSQL or provider calls and do not replay
the historical Codex journey. Global R4 remains inert at this documentation
checkpoint: final source freeze and current-source `ALL` have not occurred.
The historical 80-case R2 QEMU result does not close the current 91-case QEMU
gate; the separate global backend `ALL` remains pending.

At the last recorded audit, two external jobs were classified
`RECONCILE_REQUIRED`. No newer state check is recorded, so their current
resolution has not been checked again; this review does not claim fresh
external-state reconciliation.

The bounded R3 source review found no active SQLite persistence path in the
reviewed current source; its only production Python match is the explicit
rejection of the retired `sqlite-dump.json` marker. The earlier T020/Graphify
SQLite-module description is stale against R3. Review receipt:
`root-continuation-r1/pg-only-source-review-r3-final-20261015/review.md`
(SHA-256 `d9520552efe1bc98cf5bd757c6991831dbcc94618040dabac7b3dfc07cbd1eb4`).
This was a bounded source review, not a test run or a review of external AIPM
stores.

**Current status: IN PROGRESS.** The R3 QEMU gate, native Docker lanes,
current-source global `ALL`, completed live provider matrix, install/restore
acceptance, complete owned-resource cleanup evidence, and external-record
reconciliation remain open. No aggregate acceptance or completion is claimed.

### R4 execution and reviewed fixture integration — 2026-10-15 17:15 UTC

This checkpoint supersedes the earlier statement that R4 had not launched.
The frozen R4 manifest is `84180d6b0276c56782fe11116b145c40a052ffdf14e995695e54cd1ecebbc992`
(3,148 entries). Actual critical and REST testcase elements total **17,919:
17,629 passed, 53 failed, 237 skipped, zero errors**. Critical ran once and
REST excluded its three nodes. REST alone is 17,627/52/237 over 17,916 cases;
its JUnit header overcounts nine tests without nine extra testcase elements.
Coverage passed the unchanged 87% non-branch floor at **87.17464962267057%**.
The strict memory round-trip failed at **1.2292846810014453 seconds**.

R4's original postrun verifier failed because the snapshot Git index raw hash
changed. Binary evidence identified exactly 39 entries with stat-cache changes
only; staged objects, paths, modes, flags and extensions matched. A separate
supplement then exposed an undefined `ARTIFACTS` constant in that verifier.
Neither original failure was overwritten. The narrowly corrected R2 supplement
passed source parity, all 22 runner files, 4,191 tracked paths, 40 corpus paths,
archive, phase accounting and coverage checks. Supplemental receipt SHA-256:
`8eb85011f7101503885a75596b8905f9aa1aac813d570ff9b43d5272b14fdd02`.
This qualifies the source evidence; it does not turn R4 into a green suite or
prove whole-process resource cleanup.

Eight reviewed files are now integrated in main: five replay/handoff test
modules use isolated real integer turn sequences, the provider matrix fixture
and contract preserve primary errors and uncertain cleanup state, and the
generated contributing skill matches its PostgreSQL canonical source. Product
`src/` changed by zero files. The serial current-main selection passed **270
tests, zero failures/errors/skips** (JUnit 197.165 seconds), covering the five
complete modules, 79 matrix contracts, 38 pin-boundedness cases and the package
drift guard. All eight after-hashes were checked again after the run. JUnit:
`root-continuation-r1/bundle8-validation-runner-r1/runs/run-wtMAyAv8/artifacts/junit.xml`,
SHA-256 `5a5b598a12a8f4e734f486c4f4c2112c758a6b11ef1e0be6291e0e47d26a3311`.
This focused result does not replace a complete suite on the integrated tree.

One bound cProfile diagnostic returned normally with source/dependency guards
before and after. It measured about 0.916 seconds, including 21 fresh catalog
reads totaling about 0.493 seconds; cumulative timings overlap. It is explicitly
not SLO acceptance. One subsequent owned-target EXPLAIN sample measured 32.604
ms execution, 8.694 ms planning and no JIT, with 18,251 shared-buffer hits and no
reads. Its fixture database, roles and files were cleaned by the context; source,
metadata and private HOME guards passed. Receipt SHA-256:
`7648696f50165028805656228ff1875c8ddb9720cdfd55bb3a4b232a93a4882a`.
Previously rejected routine/constraint rewrites and result caching remain
rejected; no new product performance change or FR-044 pass is inferred.

Two separate one-shot diagnostics did not reproduce their R4 failures. The HTTP
health test passed with a request around 6.98 ms and handler around 0.212 ms;
the shell relaunch test passed with two readiness waits around 2.25 seconds,
within the unchanged ten-second limit. These use the frozen R4 source, whose
relevant tests and product files match main, and do not establish the causes of
the earlier timeouts. Original failures remain recorded.

The exact two external records were refreshed read-only and still have
`RECONCILE_REQUIRED` with no terminal binding. No administrative reconciliation
or global installation was performed; other historical jobs were not audited.
The latest resource sample had 4,691.8 MiB available against the unchanged
6,144 MiB native Docker/QEMU floor. Existing native Docker omissions and the R3
QEMU 45-minute timeout remain open. The project specifies TCG; a present KVM
device does not close that acceptance gate. Spec023 live provider lanes and
the latest OpenCode native diagnostic remain pending. **Aggregate status:
IN PROGRESS. No commit, push or publication.**

### Integrated diagnostics checkpoint — 2026-10-15 18:45 UTC

Three further test changes are integrated in main, with no product `src/`
change. The native OpenCode install test now waits for its configured agent
within the existing readiness deadline. A pinned diagnostic established an
asynchronous plugin activation race: the agent appeared after two additional
polls, about 112 ms later. The complete main module passed **27 tests**,
including the native agent-list check. This does not establish the cause of
the external historical 120-second OpenCode handoff timeout.

The process-supervisor FD tests now bind the child import to the correct
source-root argument and verify the imported module's origin. Their complete
main module passed **3 tests**. The earlier R4 three-second subprocess timeout
remains unexplained. The real provider matrix now includes `café, español y
日本語` in the actual `/terminals/run-step` instruction. Its **79 contract tests
passed**; a successful live Claude Unicode run-step has not yet been observed.
Each of these focused runs had zero failures, errors and skips. They do not
replace a fresh complete suite.

All four selected tmux diagnostics passed with the original assertions and
deadlines. Their earlier failures remain recorded without a proven cause.
Two further Codex Spec023 attempts, R8 and R9, failed in the private process
supervisor before producing an inner acceptance receipt. R9 exposed an ESRCH
procfs race. A subsequent private supervisor candidate remains under review,
including late process adoption during cleanup; it is not a delivered product
fix or a provider acceptance result. Scoped recovery removed exactly three
owned databases and six owned roles from each failed attempt, with zero
remaining targets or active connections. Configuration and secret files were
preserved. The original incomplete process-cleanup results remain unchanged.

The R5 source manifest is
`81a3f9873d645e03fef42268a3a3d48d9509cf7c7199bce5d0c8ad34bf513c20`.
It predates these three test edits and this documentation checkpoint; a new
source freeze and operator bindings are required before current-main live
acceptance. Generic agent messages before and after API restart remain part
of Spec023's required live scope, alongside questions, human answers and ACK.

The original R4 **53 failures and 237 skips**, the **1.2292846810014453-second
memory SLO failure**, incomplete QEMU run and three unexecuted native Docker
acceptance lanes remain open. The two external `RECONCILE_REQUIRED` records
remain unchanged; no global reinstall or administrative reconciliation was
performed. **Aggregate status: IN PROGRESS.**

### Live boundary checkpoint — 2026-10-15 19:43 UTC

The R6 source snapshot (3,148 files and links, manifest
`6d0875bd80fe1c854c8d1dd5d53b454ad4bb2dc64305ca94aeda5c845388d1d7`)
built the ordinary application image successfully in 56.383 seconds:
`sha256:5398a29cd6b4747246b8ebe54c10c00adff20fe414aec584b16ed59fe822adf3`.
The outer controller subsequently exited 2 because its final event emitter
wrote to a closed stream, after durable build and controller receipts existed.
Both receipts and source/daemon/container guards were reviewed. The original
outer failure is preserved; the build was not repeated and does not count as
one of the three outstanding native Docker acceptance lanes.

The two API startup diagnostics now each have a separate focused passing
result: peer-process health passed in R2, admission isolation passed in R4
(1 pass, zero failures/errors/skips). R2's admission skip, caused by a missing
Claude executable in the clean PATH, remains recorded. No model was invoked;
these followups do not establish the cause of the original global timeouts.

The real Codex Spec023 R10 attempt failed in phase A after one completed
`send_message` dispatch, before any API replacement. Its counters report zero
**validated** rows/events; they do not prove the database contained no row.
Source inspection identified a deterministic validator mismatch: PostgreSQL
and the API expose a positive integer inbox ID, but both phase-A and restart
comparators required a string. The three-file test-harness correction is now
in main. It preserves exact ID, body, sender/receiver, field-set and status
checks and rejects booleans, strings and nonpositive IDs. The complete main
module passed **33 tests**, zero failures/errors/skips, with JUnit SHA-256
`bcb9c62aa940ed67cbc7dd371673aa3f927fae59bd0578a344b359ee4fd01c7e`.
Four newly introduced formatting hunks were then normalized without changing
the parsed AST. A fresh complete module run again passed 33 tests with zero
failures/errors/skips (JUnit SHA-256
`f2fa3da1b61c1ed5d109a49e66c2749fac73ec7ac2a8401d0c1061e0c899a93a`).
The subsequent composition check kept all five architecture contracts. This
is not yet a successful live message/restart acceptance.

The separate real OpenCode question journey initialized OpenCode 2.0.18 and
connected MCP, then timed out before the first question after 88.87 seconds.
It recorded zero questions, human answers and ACK calls, with no trace
captured. Inner cleanup reported `tmux_process_identity_unavailable` and
preserved private files; the outer known process groups were empty. Whole
resource cleanup is not claimed. The failure is under source-based diagnosis
and is not conflated with the historical 120-second initialization error.

The original global failures/skips, 1.2292846810014453-second memory SLO
failure, incomplete QEMU run, three unexecuted native Docker lanes and two
external `RECONCILE_REQUIRED` records remain open. Live Claude Unicode
run-step and OpenCode MCP handoff remain pending. No global reinstall,
administrative reconciliation, commit, push or publication occurred.
**Aggregate status: IN PROGRESS.**

### Snapshot bytecode boundary — 2026-10-15 20:07 UTC

The first R7 freeze stopped after 39.011 seconds because the R6 seed contained
generated Python bytecode. Its child exited 1, was reaped, and its own process
group was verified empty. The partial target was preserved by atomic rename;
no failed receipt was overwritten. Two complete hash passes confirmed all
3,148 manifest entries unchanged. Exactly 88 inventoried bytecodes in 14 cache
directories were then archived by same-filesystem rename, preserving inodes,
bytes and metadata; the source now has zero bytecodes/cache directories and
still matches all manifest hashes. The generation writer remains unknown.

A separate hermetic regression reproduced a missing child-startup safeguard
in the Spec023 test harness: its generated MCP command could write bytecode
when the parent environment was not inherited. A local fake Python package
created four bytecodes and reported `dont_write_bytecode=false` before the
fix. Main now explicitly launches that MCP child with `-B` and
`PYTHONDONTWRITEBYTECODE=1`, and its configuration validator requires both.
The complete main module passed 33 tests, zero failures/errors/skips, in
12.961 seconds (JUnit SHA-256
`42f652d5cc4253e2a77827fd9b14564d53788669f43debe46b29cb20b904f87d`).
The subsequent project composition check kept all five contracts. This
reproduction does not attribute the historical R6 bytecode writer.

Only harness/tests changed. R7 must now be frozen again before current-source
live acceptance; the failed first freeze and original provider/SLO/Docker/QEMU
failures remain recorded. No aggregate signoff is granted.

### Current-source validation checkpoint — 2026-10-15 21:01 UTC

R7 was subsequently frozen successfully: 3,148 entries, manifest SHA-256
`662e5469225ac5c2deb3c08baa27dade8a168a93dc4f6c7c206aec9e27b0d5d2`.
Its freeze took 225.826 seconds with the child reaped and its group empty.
The installed 25-file global runner passed integrity checks and 13 offline
contracts. Independent collect-only found 17,924 unique tests, no duplicates:
five additions and no removals against R4, with each of the three critical
tests present once. This was collection, not a new full-suite execution.

The real Spec023 Codex/R12 attempt failed in phase A: zero MCP dispatches,
zero validated inbox rows/events and zero API replacements. Those row counts
do not prove absence of raw database rows. Inner cleanup passed; the outer
retained failed-run files and reported incomplete cleanup, with zero known
groups remaining. A source-based contract reproduction found that Codex
appends its turn-receipt envelope to the original trigger, while the sender
profile required equality with the bare trigger and a bare `sent` reply.
Main now permits that exact generated envelope and requires the receipt in
the reply, retaining the same two triggers, bodies, receiver and one call
per phase. Other provider prompts retain their previous text. The complete
main module passed 34 tests with no deselection/failure/error/skip; JUnit
SHA-256 `e329d7685e374da381ec74fe8115306c49faa64be2272fffbec6b53e171019ad`.
The subsequent composition check kept all five contracts. This corrects a
reproduced harness contradiction; it does not prove that contradiction caused
the failed run, or establish live message/restart acceptance. A separate real
chunked-HTTP probe did not reproduce the suspected SSE buffering problem;
no chunk-size change was applied.

The main T097 FD-theft test also now reads a newline-framed ready handshake
within its existing 30-second deadline. Previously, `select` could report
partial bytes and the following `readline` could then block indefinitely.
A child that emits a partial line and keeps stdout open reproduced the bug.
The integrated regression passed in main, followed by five kept composition
contracts; JUnit SHA-256
`f7444d7a77a0e299932c4e9468ea337d9f3b53e2034755c5cbaadfc78fd37c31`.
Attack assertions are unchanged. This bounds post-Popen frame reading only;
it does not bound process creation or the earlier procfs scan and does not
establish the historical QEMU hang's cause. Full QEMU acceptance is open.

New real-provider results remain failures:

- Claude AG-UI/R11 exited before any question: provider exit 1 in 4.95 seconds,
  outer 15.933 seconds, zero answers/restarts. Cleanup completed, all nine
  guards passed, and three known groups ended at zero. Fixed failure flags
  did not identify a category; they do not establish authentication success.
- Claude matrix/R2 sent the Unicode run-step instruction and received HTTP
  504 at the first scenario. The other five scenarios were not observed.
  This neither reproduces nor resolves the historical non-ASCII HTTP 500.
- OpenCode matrix/R2 stopped in the supervisor with
  `process_task_inventory_raced` / `owned_tree_retry_budget_exhausted`.
  Seventeen known groups ended at zero, but inventory remained unreadable;
  no inner receipt or JUnit establishes a journey outcome.
- The independent OpenCode MCP lane failed at `parent_create:operator_failure`
  before MCP stdio: list, handoff, join and native-child receipt counts were
  all zero. Six known groups ended at zero with readable inventory; failure
  files were retained. The specific HTTP/response-shape cause is unknown.

The ordinary successful R6 application build has an identical 651-file build
context in R7; all five build inputs match. This is qualified carryforward,
not a fresh R7-labelled image or native Docker acceptance. Its original
outer emitter exit 2 remains preserved. Native Docker and QEMU prerequisites
remain below the unchanged 6-GiB available-memory floor. The prior global
53 failures/237 skips, strict 1.2292846810014453-second SLO failure and two
external `RECONCILE_REQUIRED` records remain open. Failed fixtures and the
legacy records were not administratively reconciled. Main now differs from
R7 by the tested fixes; a new freeze and full suite are pending. No commit,
push, publication or global CAO reinstall occurred. **Aggregate: IN PROGRESS.**

### Current continuation checkpoint — 2026-10-16 21:30 UTC

This checkpoint supersedes the older operational statuses above where they conflict; it does not erase earlier receipts.

**Current-source and system state.** Main remains at HEAD `02309f3f1e8cd1cf3b66dc92ca4d9e6c21ccf2db` with the inherited dirty worktree and index SHA `7ab5d5a55a1d82b114cddd0f525c6350753df4c3c592230775b889c3d548c086`. No commit, push, publication, global CAO reinstall, or administrative reconciliation occurred. At the latest resource sample, 7,438 MiB was available, swap use was 2,700 MiB, and load averages were 3.76/4.19/4.69 while unrelated npm/Playwright processes remained active. The available-memory floor is met; the host is not quiet enough to claim an isolated full-backend rerun.

**QEMU, Docker, and performance.** QEMU R15 passed all 91 cases with zero failures/skips and four warnings in 3,439 seconds under its 3,600-second outer limit. Its source guard passed and the owned guest/container and process checks closed. The older incomplete QEMU runs remain historical failures, not current R15 failures. Three native Docker R8 lanes passed: installation, PostgreSQL restore, and browser-cookie acceptance. The R17 full-backend run itself failed: 18,179 unique selected tests yielded 17,908 passed, 40 failed, and 231 skipped. It recorded 42 phase failure events; 34 were PostgreSQL connection-establishment timeouts during a period with about 770 MiB available, exhausted swap, and load near 102. Those measurements are consistent with resource pressure but do not prove the cause of every timeout. Two RecoveryBundleError cases passed an isolated module rerun and remain unexplained. Three subprocess/Docker timeouts and one incomplete Git probe also remain without a reproduced code cause. The strict three-process `<1 s` NFR cohort R9-r3 passed 3/3 with its assertion enabled; individual elapsed values were not retained. No best-of retry or threshold change was used.

**Providers and interaction journeys.** The latest generic-message Codex R17 and OpenCode R17 runs passed their selected journeys; Claude R17 failed `observer_audit` because the genuine MCP `.complete` seal was not observed after two message calls. Its retained evidence does not identify why the provider server failed to close normally. A separate Codex question/AG-UI R17 run failed because its same-group wait began after API A stopped, received `ConnectionError`, and API B became ready about 2.65 seconds later. The reviewed main prompt now repeats that same-group wait on the existing `pending` or structured `unavailable` result within the unchanged 75-second task budget; no current-source live acceptance has exercised that change. OpenCode question discovery found V2 MCP grant names were normalized differently from the installed profile and `codemode` lacked the required explicit false setting; the reviewed main correction preserves exact hyphenated grants, disables Code Mode for the native MCP server, and keeps `execute` denied. An R18 native OpenCode runner is prepared with live gates closed; no R18 question run is claimed.

The historical Claude Unicode `/run-step` record remains a source-callsite uncertainty. An offline reproduction showed that a non-ASCII answer receipt could reach `hmac.compare_digest` and ASCII receipt hashing; `AnswerAck` now rejects such values at validation, and its service/API regressions passed in a focused 161-test selection together with OpenCode profile tests. This fixes the demonstrated malformed-receipt boundary but does not prove the old external run reached that callsite. The separate historical OpenCode 120-second initialization record is also unresolved. A launcher hash mismatch is a preflight failure before provider acceptance; the newest R18 runner bundle passed static integrity review with its live gates false, but no current-main provider run has been bound to that bundle.

Both historical external CAO records remain `RECONCILE_REQUIRED`. No old job was retried, force-closed, or globally reinstalled. The R17 provider results and R18 candidate bindings do not substitute for a new final source freeze and current-source acceptance.

**Latest offline diagnostics.** The Spec023 AG-UI stream shutdown and MCP logical-result outcome changes passed 64 focused tests with zero skips. Black, isort, a scoped whitespace scan, the five-contract project composition check, and independent ECC review passed. The audit records only fixed `tool`/`status`/`outcome` fields and now rejects a completed dispatcher result whose structured `success` value is false. No provider, API worker, PostgreSQL, Docker, or network was started for these diagnostics. They do not identify the exact failed predicate from the preserved Spec023 phase-A run.

The source edits after R18 require a new source freeze and fresh per-lane bindings before any additional real provider acceptance. Full backend validation remains failed/pending until a quiet-host run closes its failures and reconciles its skips. **Aggregate status: IN PROGRESS.**

### Focused current-worktree follow-up — 2026-10-18

Reused the already-running PostgreSQL container `cao-spec021-pg-1877b0cb41cd`
with fixture auto-provisioning disabled. The previously failing R21 two-profile
local-peer node passed (1/1 in 44.90 s). Three corrected session-teardown nodes
passed (3/3 in 11.10 s), the WebSocket owner-cancellation node passed (1/1 in
9.60 s), the scheduler release-lock node passed (1/1 in 7.01 s), and the remote
response-loss/reconnect node passed (1/1 in 8.40 s).

The 16 remaining R21 failure node IDs were rerun serially against the same
container: 16 passed, zero failed or skipped in 104.98 s. The run first exposed
`test_work_issued_capability_is_private_and_superseded_capability_never_falls_back`
returning `503 private_source_unavailable`. The cause was test isolation: the API
fixture redirected `CAO_HOME_DIR`, while `script_runner.WORKFLOW_SCRIPT_SCRATCH_DIR`
remained bound to the imported operator path whose mount exposed mode `0777`.
Production correctly rejected that directory because snapshots require a private
`0700` parent and `0600` file. The fixture now binds that consumer constant to a
per-test scratch directory under `tmp_path`; the capability test and its whole
five-case module pass (1/1 and 5/5 respectively). The permission checks and
production behavior are unchanged. The two recent `0777` snapshots left by the
failed reproductions were verified as owned test artifacts and removed.

Black and isort pass on the fixture file. `project-composition-check caos`
analyzed 403 files and 1,787 dependencies; all five contracts were kept and none
were broken. These focused results do not replace the full backend/Apps gates,
the source-bound T081 PostgreSQL plan, the three cold strict-NFR runs, Docker or
QEMU acceptance, or current-source live-provider/API-restart acceptance. The
historical CAO `RECONCILE_REQUIRED` records remain untouched.

## T120 benchmark preparation — 2026-10-18

The previous covered failure measured 1.043541594 s; a passive observer recorded
a 50.214883 ms generation-2 GC pause. A diagnostic pre-body collection collected
1,636 objects and passed at 0.961760162 s. Luna reviewed the explicitly scoped
fixture and accepted its ordering after native DB/tmp_path setup. No provenance
claim is made for individual objects; full collection may execute finalizers.

Main now contains a local explicit fixture, gc.collect() once before the
unchanged body. GC remains enabled with unchanged thresholds. The body SHA-256
is 432446cee143278bc16b1266bd3cbe4ca6bcab84cdbb729af3f4d4d0c4126f82.
Three fresh Python3.12 processes, `--cov=src --cov-report=` and unchanged native
fixture passed without retry at 0.913459783 / 0.947938199 / 0.951082612 s.
Receipts and per-run JUnit/log/coverage: `/tmp/caos-t120-*20261018*`.
This is benchmark preparation isolation, not a production speed improvement
or a full-backend PASS. Production Work/catalog/audit code is unchanged by T120.

### T120 composition review

Changed subsystem: one benchmark-local fixture. Reviewed native isolated DB
setup, tmp_path lifecycle, MemoryService and Work/audit boundaries against source.
The explicit dependency ordering completes native setup before collection; the
fixture performs no operation warmup, changes no GC settings and owns no shared
DB state. A collection can run finalizers, so no claim of zero implicit cleanup
I/O is made. Runtime API/schema/messages/transaction contracts are unchanged.
Real dependency: existing owned PostgreSQL container, autostart disabled, no
restart (healthy with restart count zero at precheck). Three cold NFR tests and
the other six round-trip regression tests passed (one NFR deselected only in
that complementary regression selection). Composition check passed, five
contracts kept, none broken. Scoped diff/whitespace and unchanged-body checks
passed. No graph rebuild is warranted for a local fixture; existing native
graph incompleteness remains recorded. No durable-vault write for transient
benchmark diagnostics. Verdict: PASS WITH RISKS for preparation isolation only;
full backend and long-running product performance remain unverified.

### Parallel capacity diagnostic harness failure

The first bounded eight-worker capacity + WorkRepository diagnostic selected
38 nodes and returned 37 failures plus one setup error. The temporary probe
incorrectly replaced classmethod WorkRepository._verify with an ordinary
function wrapping an already bound method; all failed with an extra positional
argument. This is diagnostic-instrument failure, not product evidence. Source
was not changed. Original log/JUnit remain at
`/tmp/caos-capacity-load-probe-20261018.*`. Corrected R2 retains the classmethod
descriptor using its unbound __func__, checks binding synthetically first, and
writes phase values per xdist worker so successful-worker data is preserved.

## T121 capacity trace completion — 2026-10-18

Corrected bounded cohort R2: 38 passed, eight workers, p95 visibility480.1ms.
Its per-worker diagnostics measured expiry p95265.08ms, read context292.22ms,
Work connection104.12ms, full Work verification375.16ms (different nested
populations; do not sum their p95s). This did not reproduce the full-run failure.
Main now records previously omitted expiry and read-snapshot setup per request,
reports them in slowest-request/aggregate traces and verifies all20 entries.
Existing SLO clocks, threshold, workload, twentythreads and production source
are unchanged. Luna static review APPROVE. Full capacity module passed3/3.
Logs/JUnit: `/tmp/caos-t121-capacity-20261018.*`. Full-backend acceptance stays open.

Omission audit reconciled83 skips. Gitleaks8.30.1 was obtained in a private
validation-tools directory from the official release and verified against CI's
archive SHA256551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb.
No global install. Existing private fish/zsh toolchain and Cargo can be placed
on PATH for recovered prerequisite tests. The native guard restart module's
nodes are absent from the previous XML despite unchanged source seals; its
selection omission requires current collection reconciliation.

## T122 Unicode token boundary and T124 selection — 2026-10-18

Changed production: security/local_token pure ASCII comparator and four callsites
in Interaction HTTP and runtime WebSocket handlers. Actual RED boundary tests
returned10TypeError failures and2existing403passes. Shared comparator now rejects
non-ASCII before hmac, preserving exact ASCII comparison and existing principal,
scope, token-read/refresh and resource lifecycle. No prompt/answer validation
changed; no real login or credential bytes read or written. Final synthetic
selection16passed; broader token-file/HTTP/native PG runtime61passed (overlaps
the original12). Native real WebSocket success/epoch reconnect passed. Luna
security review APPROVE; Black/isort and composition5kept/0broken passed.
No consumer schema/API migration required. Residual risk: no historic traceback
links this independently reproduced defect to the old Claude run-step; live
Unicode run and external reconciliation remain open. VerdictPASS for this scoped
fix, no aggregate delivery. Graphify query was broad/truncated; all four actual
source boundaries were inspected. No structural graph rebuild for shared helper.

Recovered prerequisite/cohort selection618passed, zero skips/failures; logs and
JUnit `/tmp/caos-prerequisite-recheck-20261018.*`. Full-source tests must retain
that private tools PATH. Explicit unfiltered collection (`-o addopts='' -m ''`,
no coverage because collection only) succeeded:18,363 unique tests versus18,007
previous XML cases.340existing cases were absent previously, plus16new T122cases.
Twelve native-guard cases now collect. Previous run must not be described as
complete unfiltered acceptance. Node manifest
`/tmp/caos-unfiltered-nodeids-20261018.json`, SHA256
fba2559a4143b90447e8186be6781108dd789b9d88d3d47b655f712991f6facc;
missing-before list`/tmp/caos-unfiltered-omitted-before-20261018.json`.

## T123 UI packaging and frontend verification — 2026-10-18

Root reviewed the narrowly scoped workflow/doc/test diff. Both wheel and sdist
jobs now build all MCP Apps before packaging and cache both locked dependency
trees. Release policy, action pins, platform matrix, permissions and Web build
are preserved. Documentation now names Vite's actual direct output locations.
The new contract failed before the fix and passed after; root ran the complete
wheel-contract module80passed, then applied Black's formatting-only adjustment
and reran the new contract1passed. Black/isort and composition5kept/0broken
passed; final complete backend remains required on the final source.

Fresh main Apps pipeline: npmci, typecheck, full92tests/12files with max8workers,
buildallfourviews, JIT scan and sizecheck all exit0. Line coverage478/531=90.01%
(reported); statements517/594=87.03% is a different metric. Fresh Web: locked
install, typecheck,504tests/45files with max8workers, and build all exit0. Both
pipeline source manifests matched before/after. Logs/results under
`/tmp/caos-{apps,web}-current-main-20261018-r1/`. E2E is separate and pending
at this checkpoint. No workflow dispatch, upload, release or publication.

Composition review: build outputs→Hatch artifacts and packaging job ordering
reviewed against source, with existing platform/TUI contracts. Real Node24.19.0
and npm12.0.2 performed both locked builds; GitHub Node20/platform execution
is not claimed. No runtime API/schema/auth authority change from T123. Verdict
PASS WITH RISKS for local/configuration verification; browser E2E/full backend
and actual cross-platform CI remain separate gates.

Apps browser E2E first attempt returned1: all7 cases failed at browser launch
because the locked Playwright Chromium executable is absent. No UI acceptance
is claimed. Original private log remains
`/tmp/caos-apps-e2e-current-main-20261018-r1/run.log`; install the locked
Chromium prerequisite before a new, separately recorded attempt.


## R3 complete backend and T125 scope-auditor correction — 2026-10-18

The single unfiltered eight-worker run R3 covered `test/` and
`examples/workflow/tests/`: **18,130 passed, seven failed, 236 skipped, zero
errors, 71 subtests passed** in 3384.04 s reported by pytest. All eight workers
collected the same 18,373 unique node IDs; the accounting receipt reports no
unreported or unexpected IDs. Coverage is **68,500 / 78,528 statements =
87.230032599837%**, satisfying the exact 87% floor without rounding. Source,
HEAD, index and inventory checks passed before and after the run. Receipt:
`/tmp/caos-final-backend-n8-20261018-r3/artifacts/result.json`.

The four auditor failures were:

- `test_every_mutating_route_is_scope_or_verified_work_authority_gated`
- `test_every_disclosure_bearing_get_route_is_gated_or_explicitly_open`
- `test_interaction_authority_audit_covers_exact_live_routes`
- `test_interaction_authority_audit_rejects_wrong_live_guard`

All belong to `test/api/test_scope_coverage.py`. Their inline-hmac source
expectation was stale after T122 introduced the canonical ASCII helper. Root
reviewed and integrated the test-only candidate into main after the R3 source
postcheck. The auditor now follows the canonical live binding and exact ordered
ASCII prechecks before constant-time comparison; it adds negative regressions
for a weakened helper and a rebound route helper. No open-route exception or
production auth behavior changed. The complete scope-auditor module plus all
16 Unicode token boundary cases passed **82/82**, zero skips/errors, one run.
Black and isort passed on the changed module. Receipts and the guarded baseline
port are under `/tmp/caos-t125-scope-audit-20261018-r1/`. This is focused green
evidence, not a replacement for fresh complete backend acceptance.

The remaining R3 failures are required-wait signal startup (1 s deadline;
snapshot/status-mock/native phases total 1.109 s), memory round-trip
(**1.8967022409924539 s**, limit <1 s), and remote response-loss receipt recovery
(replayed durable `input_blocked` result). Their causes and corrections remain
separate. All three capacity cases passed, including the unchanged visibility
limit; no exact full-run p95 is separately claimed. A read-only catalogue-churn
sample is `/tmp/caos-pg-catalog-bloat-readonly-20261018.json`; estimates and an
idle activity snapshot do not prove the latency cause.

Docker gates passed 3/3 with no skips, Apps browser E2E passed 7/7 and Web
browser E2E passed 20/20, all without retries. Receipts are respectively
`/tmp/caos-docker-current-main-20261018-r3/gates-receipt.json`,
`/tmp/caos-apps-e2e-current-main-20261018-r3/run.log` and
`/tmp/caos-web-e2e-current-main-20261018-r2/run.log`. Three pinned-Linux cold
covered NFR processes passed at 0.977849819 / 0.897332472 / 0.898801600 s;
`/tmp/caos-final-cold-nfr-20261018-r2/result.json`. These isolated NFR passes do
not close the R3 full-load failure. Final source/build-input equivalence and
full green validation remain required after further corrections.


## T126 required-wait fixture isolation — 2026-10-18

Root accepted the one-predicate test-only correction after tracing the R3
failure: the mocked terminal status used `has_required_wait` and introduced a
redundant PostgreSQL read between the real source snapshot and native-notice
check. It now derives terminal telemetry from the local answer-accepted flag.
The real ledger still owns source/native/required wait decisions; acceptance
alone still leaves the dependency pending until the MCP pull. No claim is made
that this fixture observes COMPLETED before that pull: the completion loop is
already blocked in its positive required wait. The unchanged 1 s startup signal,
2 s active budget, 2.1 s pending interval, ledger/graph assertions and no-redelivery
assertions remain. The complete module passed **7/7**, zero skips/errors, in one
covered invocation. Receipt: `/tmp/caos-t126-wait-composition-20261018-r1/result.json`;
JUnit and log are adjacent. This is test isolation, not a product speed fix or
full-suite closure. The strict memory SLO remains open.


## T127 remote recovery fixture — 2026-10-18

The test previously used `timeout=1` to cause response loss, but that same
budget also becomes the durable command authority deadline. Root integrated a
test-only correction that enqueues the real bridge task and raises an explicit
transport acknowledgement error, while retaining the normal COMMAND_TIMEOUT
for dispatch authority. It now asserts exactly one additional native send after
launch, a successful durable result, and unchanged stored state/result plus no
additional sends after reconnect/replay. It does not alter product fences,
reconciliation, retry, command or storage logic.

The ordinary full `test/runtime_channel/test_remote_services.py` module passed
**6/6**, zero errors/skips, without the diagnostic plugin. Receipt:
`/tmp/caos-t127-remote-services-module-20261018-r1/result.json` with adjacent
JUnit/log. This is in-process bridge/native-mock coverage, not a real network or
provider acceptance claim. Fresh full-backend closure remains open.

Preserved diagnostic attempts `/tmp/caos-t127-response-loss-red-20261018-r1`,
`...green-20261018-r1` and `...green-20261018-r2` are **not causal evidence for
the historical R3 failure**: root discovered that their private autouse plugin
requested the pair fixture eagerly, allowing another autouse fixture to replace
its backend afterwards. The first candidate also counted a post-turn receipt
marker that is not the correct witness for a durable command result. Root
removed that filter and now counts every native send. Those failed probes are
retained; the plugin must preserve normal fixture order before reuse. No
historical error attribution is claimed from them.


## T128 configured typing and Work regressions — 2026-10-18

Root integrated the reviewed R2 candidate into the main worktree, checking all
three baseline hashes and all three resulting hashes. The grant job ID now has
an explicit local str type and still passes the existing authority validator.
The body-free JSON projection has recursive JSON types/overloads while retaining
its field omission and error projection. The request parser retains the same
three-entry mapping, operation/type rejection order and concrete Pydantic model
validation, with explicit class and return union annotations. No SQL, schema,
transaction, authority, retry or configuration behavior changed. Guarded port:
`/tmp/caos-t128-main-regression-20261018-r1/port-receipt.json`.

The five complete existing modules (Work authority, MCP proxy, origin messages,
message models and native PostgreSQL messages) passed **146/146**, zero errors
or skips, in one covered invocation. Receipt and JUnit/log are under
`/tmp/caos-t128-main-regression-20261018-r1/`. Global configured `mypy src/`
passed on all **400** files; global `black --check src/ test/` passed on
**1,342** files; global `isort --check-only src/ test/` passed. Logs:
`/tmp/caos-t128-main-static-20261018-luna/`. Mypy uses the existing mypy.ini
(Python3.11 setting), not the separate strict pyproject section. Pinned-cache
and main-venv versions agree: mypy1.18.2, Black26.3.1 and isort6.0.1. No
configuration weakening, casts or new ignore directives were added.

Graphify identified WorkAuthority and WorkMcpProxy dependencies; the graph does
not contain WorkMessageService. Source inspection verified WorkOrigins message
dispatch, the grant authority lane and durable response projection directly.
The existing graph is guidance, not proof that a missing class is absent.
Composition/architecture and final complete-backend gates remain separately
recorded. This focused acceptance does not close the strict NFR or real-provider,
QEMU and historical reconciliation gates.


### T128 composition review

Changed surface: authority job-ID typing, body-free MCP response projection and
Work message request-model typing. Reviewed neighbors: WorkOrigins grant lane
and message dispatch, WorkRepository verification/transactions, proxy durable
response hashing and the three send/receive/ack Pydantic models. The existing
runtime guards, unknown-operation/validation error order, body omission,
transaction ownership, native PostgreSQL roles, message durability, idempotency,
replay and authority locks are preserved. There is no migration, API/event schema,
cache, resource-lifecycle or environment change; typing imports add no subsystem
dependency. The source diff was reviewed against guarded baseline hashes.

Real dependency: the existing private native PostgreSQL container; five complete
Work test modules passed146 cases, covering authority/proxy/model/message
behavior, failures and repeated/concurrent requests. Current architecture and
PostgreSQL-only source guards passed12/12. `project-composition-check caos`
passed with5 contracts kept/0 broken; log `/tmp/caos-t128-composition-20261018.log`.
Configured mypy400 and global Black/isort1342 checks passed as recorded above.
No additional Pact contract is needed for these unchanged in-process types;
existing models/transport tests exercise the contracts. Scoped verdict: PASS.
Aggregate verdict remains OPEN: fresh full backend, strict loaded NFR, QEMU and
real providers are not replaced by these focused results.

Markdown links and both offline `make check-agent-plugins-schemas` and
`make check-agent-plugin` passed without dependency synchronization; receipts
`/tmp/caos-final-contract-checks-20261018-r1/result.json`. The R3 skip audit
accounts236 skips as60 runner capability/resource gaps,3 platform limits,
76 opt-in acceptance cases and97 other-provider-lane cases; exact33 grouped
messages are in `...r3/artifacts/skip-audit-summary.json`. Those skips are not
unreported collection omissions and do not constitute QEMU/provider acceptance.


### T127 controlled expiry versus lost acknowledgement — valid fixture order

The corrected private R2 observer preserves ordinary fixture ordering and adds
one real 1050 ms delay before calling the original INPUT authority guard.
The unchanged R1 baseline fails: deadline remaining -124 ms, guard denies,
only the two launch native sends occurred. The current main case passes1/1:
first guard allows with58877 ms remaining, replay guard allows with58417 ms
remaining, three total native sends (two launch, exactly one INPUT).
Receipts/logs: `/tmp/caos-t127-response-loss-red-valid-20261018-r1/` and
`/tmp/caos-t127-response-loss-green-valid-20261018-r1/`. This controlled
experiment demonstrates the test fixture's deadline/loss separation; the
original R3 denial boundary was not captured and is not retroactively proven.
No actual remote network or provider acceptance is implied.

### Strict NFR diagnostic exclusions — no SQL change accepted

The private JIT observer found21 catalog calls taking486307269 ns wall time
(81793188 ns thread CPU), with JIT absent from the72-table plan. A separate
read-only planner experiment alternated hash joins on/off in three pairs.
On execution times were25.337/18.090/17.474 ms; off times were
19.863/19.422/19.360 ms. All nine catalog families had identical raw,
normalized and digest results in all three pairs. The apparent first-pair
gain did not persist: both later off plans were slower. No planner setting
or SQL variant was ported. These probes run EXPLAIN before the timed case,
can warm catalogs, and are diagnostics only, never cold-NFR acceptance.
Private receipts: `/tmp/caos-memory-jit-diagnostic-20261018-r1.json` and
`/tmp/caos-memory-plan-abba-20261018-r1.json`. Full R3's1.896702241 s
failure remains open; isolated sub-second results do not close it.


## R5 complete passive diagnostic and R6 isolated control — 2026-10-18

R5 closed exit1:18,138 passed, one failed,236 skipped, zero errors,71 subtests
passed;18,375 unique cases collected identically on eight workers, without
filters or missing/unexpected reports. Coverage68,501/78,536=
87.22242029133136% statements (no branches), exact87% floor passed. Source,
HEAD, index and inventory stayed unchanged. Artifacts:
`/tmp/caos-backend-n8-passive-diagnostic-20261018-r5/artifacts/`.
Root reconciled every original R3 failing node: all four scope audits, required
wait and response-loss recovery passed setup/call/teardown; only strict memory
round-trip failed. The only two added nodes are the negative scope-auditor
regressions; zero R3 nodes disappeared. Comparison:
`/tmp/caos-backend-r3-to-r5-comparison-20261018.json`.

The exact failing body elapsed2.0697171540086856s; the instrumented whole call
was2.070461006s wall/595.423080ms thread CPU. Inclusive metrics: Work verify
21 calls1728.863888ms; catalog21 calls1097.910307ms (161.426087ms CPU);
normalize41.060849ms; digest127.206233ms; pooled execute145 calls1425.134048ms;
commit21 calls14.364643ms; GC about5.04ms, no generation2 collection.
Do not sum these nested measurements. Private profile:
`/tmp/caos-memory-passive-full-n8-20261018-r5.json`.

R6 isolated the exact same case with one worker, the same full-run profile,
plugins and instrumentation, and asserted operation counts21/21/21/145/21.
It passed1/1 with no skips/errors and source before/after guards passed.
Call wall990.186099ms/thread CPU359.927448ms; catalog506.568122ms,
verify682.183672ms, execute523.546382ms, digest72.108547ms, commits6.468361ms.
Focused statement coverage is7855/49044 (~16.02%) and is not a full-suite
gate; its recorded below87% is not a waived aggregate requirement. Receipt/profile:
`/tmp/caos-backend-n1-passive-diagnostic-20261018-r6/artifacts/result.json`,
`/tmp/caos-memory-passive-isolated-n1-20261018-r6.json`. Both R5 and R6 are
diagnostic-only; neither closes final uninstrumented acceptance.

The optional read-only Docker stats/Linux context sampler issued zero SQL
queries and stopped automatically30s after the profile (session29570 exit0).
It sampled every~21s, so it is context, not per-query causal proof. Docker has
32 CPUs, no container CPU quota and a4GiB PostgreSQL cap. Near the profile end
a sample recorded~639% PG CPU and2.854GiB used, with Linux memory pressure
avg10=0; a later sample showed pressure. No inference that memory caused the
failed body is justified. Private context:
`/tmp/caos-n8-docker-context-20261018-r1/`.

R2 remains the immutable technical-source baseline for prepared execution
packets. These new specification/results-only documentation edits occur after
all R5/R6 source guards closed. Product and test code remains R2-identical.
No production SQL, thresholds, workers or authority guarantees changed.


### T129 routine-definition JSON diagnostic: rejected, 2026-10-18

The immutable R2 source was unchanged before and after the targeted diagnostic.
The first private probe stopped before opening a diagnostic connection with
`work_table_count_guard_failed`: its preparer selected only the final migration's
tables instead of the sorted union used by `verify_component`. That failed
diagnostic is preserved at `/tmp/caos-catalog-json-diagnostic-20261018-r1/`; its
passing isolated NFR is not evidence for the proposed SQL change. The controller
loaded SHA `d4569429c96d5d6bdcc9deae86fd4b5760f91d169cae0436e72277cc3a557ef2`,
identified by its result fields. It differed from the earlier root-reviewed copy
only in moving the RAM guard before output creation and clarifying invocation
receipt fields; those changes were reviewed after execution and are recorded
in its separate `root-review-notes.json`. No aggregate acceptance is claimed.

The corrected R2 diagnostic preserved that failed evidence and selected all 72
Work tables by the canonical union rule. It ran six predeclared paired arms in
AB/BA/AB order, after the test call and before fixture teardown, in one owned
repeatable-read, read-only connection with a transaction-local 5 s statement
timeout and savepoints. All six actual reads and EXPLAINs succeeded. Every pair
retained raw values, decoded type fingerprints, numeric types, normalized
manifests and digests. The only experimental difference was JSON builders in
the routines definition; sortable identity_arguments stayed JSONB.

| Pair order | Baseline client ms | Candidate client ms | Baseline server execution ms | Candidate server execution ms |
| --- | --- | --- | --- | --- |
| AB | 24.870 | 23.182 | 26.974 | 19.415 |
| BA | 19.322 | 19.092 | 18.033 | 18.180 |
| AB | 18.737 | 19.897 | 17.416 | 17.713 |

These prepared post-call diagnostic observations show no stable improvement.
The candidate was rejected and no product SQL was ported. EXPLAIN executes
separately before each actual read, so these are not cold acceptance timings.
Root plan buffer counters are distinct from sums of inclusive parent/child
counters; the latter must not be interpreted as total I/O. The isolated test
also passed, with its unchanged 21 verification/catalog/digest calls, 145 pooled
executions and 21 commits. This does not close the failed loaded NFR or the
final uninstrumented backend gate. Receipts: `/tmp/caos-catalog-json-diagnostic-20261018-r2/artifacts/result.json`
and `/tmp/caos-catalog-definition-json-probe-20261018-r2.json`.


### Current R2 Docker acceptance, 2026-10-18

The application image was rebuilt from immutable R2 technical source with the
canonical installer build helper. Image ID:
`sha256:f1e3b76334c0af8a886be8a90c376fc7a8ab123aa1e6db4369d8c36141037fd9`.
The existing worker image source/protocol labels matched the sealed supervisor.
All three real gates then passed serially, each exactly one test, zero failures,
errors or skips: installation lifecycle209.722s, PostgreSQL restore99.759s,
browser cookie renewal/logout25.215s. Pinned Linux Python3.12.13, explicit
plugins, private profiles/cache/temp roots and the verified early-home shim were
used; the image's base runtime independently reported Python3.12.15 during build.
The exact existing test PostgreSQL container remained healthy, restart0, with
autostart disabled and no replacement. Tests managed only their own deployment
resources. No image was published.

Build receipt: `/tmp/caos-docker-current-main-20261018-r4/build-receipt.json`.
Aggregate three-gate receipt: `/tmp/caos-docker-current-main-20261018-r4/gates-receipt.json`.
These receipts bind source manifest `bd46696c6034f1fe419f8c08d8a8d6a676c2f75582ce57e49938284091e8f3df`;
main differs only in the five documented Spec021 files, with runtime/test/build
inputs unchanged. QEMU, live providers, strict loaded NFR and final aggregate
acceptance remain separate.


### Current omission inventory and native launcher preflight

`/tmp/caos-backend-r5-skip-reconciliation-20261018.json` confirms exact
identity of all236 skipped test IDs and their reasons between R3 and R5.
They have33 distinct reasons; no old cases were removed, and two negative
auditor cases were added. The three green R4 Docker gates cover exactly3 of
19 Docker/PostgreSQL opt-in cases. Fifteen T019/T020 cases still have only
older-source execution evidence; the prepared-workflow result/attempt/API
restart case is additional to those15. These16 require current-source runs.
The active QEMU91 selection includes11 host-gated and58 missing-capability
IDs, but no QEMU coverage is closed before its final receipt passes. Herdr
T063 and the separate libtmux skip are outside that selection.

The R6 six-journey plans now have actual CLI preflight facts and root-reviewed
source/runtime bindings for Codex0.163.0, ClaudeCode2.1.293 and OpenCode2.0.18;
no journey has yet called a provider. The separate OpenCode native MCP R4
preflight failed with `mcp_operator_pin_mismatch`, before auth fingerprinting
or provider invocation. Root confirmed the private packet had changed both
MCP artifacts to R2 while retaining their old SHA constants. The R5 private
packet changes only those two constants; root reviewed its diff and seven
sealed files. Actual R5 preflight then returned `preflight_ready`, exit0,
without a provider call. Receipts: `/tmp/caos-opencode-native-r4-actual-preflight-20261018.json`
and `/tmp/caos-opencode-native-r5-actual-preflight-20261018.json`.
This corrects a validation launcher; it is not delivered product functionality
or evidence that the historical120s initialization failure is resolved.

A standalone Claude preflight returned `claude_archive_root_invalid` because
that invocation lacked the private archive layout and TMPDIR established by
the reviewed wrapper. It did not call Claude and is not an authentication or
product failure. The wrapper/live Unicode lane remains unexecuted. Its source
login and staged source credentials were not modified.


### T130 shared-subsets evaluation: no product port

One fixed post-call AB/BA/AB experiment ran against immutable source R2, with
all72 Work tables and the unchanged strict NFR body. All six SQL reads and
EXPLAINs succeeded; all three pairs retained exact raw, numeric-type, normalized
and digest parity. The isolated body passed at748.624399ms call wall with the
unchanged21 verifications/catalogs/digests,145 pooled executes and21 commits.
That is diagnostic evidence, not full-load or cold acceptance.

| Order | Baseline client ms | Candidate client ms | Baseline server execution ms | Candidate server execution ms |
| --- | --- | --- | --- | --- |
| AB | 24.295 | 22.053 | 24.365 | 19.428 |
| BA | 19.261 | 18.010 | 16.786 | 18.639 |
| AB | 21.026 | 17.968 | 17.409 | 17.986 |

Client reads were modestly lower in each pair, but server execution was slower
in the two later pairs. The candidate reduced Seq Scan nodes21→17 while
increasing Hash Join20→22 and CTE Scan16→27. This does not demonstrate enough
stable product benefit to justify porting the five-subset rewrite, much less
closing the2.069717154s loaded failure. No product SQL changed. Per-node metrics
are allowlisted and VERBOSE supplies catalog schemas; per-loop/inclusive
values must not be added as if they were total query time. No SQL, conditions,
catalog values or credentials were persisted. Receipts:
`/tmp/caos-catalog-shared-subsets-diagnostic-20261018-r1/artifacts/result.json`
and `/tmp/caos-catalog-shared-subsets-probe-20261018-r2.json`.

### T131 current-source QEMU failure, preserved before correction

Current R2 QEMU run terminated with exit1 after two groups: host acceptance8/8
passed, SIGKILL group1/2 passed. The failing node is
`test/integration/t097/test_proxy_sigkill_t097_restored.py::test_sigkill_during_proxy_effect_window_is_uncertain_without_redelivery[after_upstream]`.
Its unchanged8s supervisor wait returned UNCERTAIN with reason
`monitor pidfd became readable before its child status was waitable`;
TERMINATED was required. The assertion remained primary and a cleanup
DeliveryUncertain was attached as a note. Totals:9 passed,1 failed,81 not run;
this is not a91-case acceptance. No outer timeout was reached. The owned
runner container and marker database were removed with no cleanup error; the
shared PostgreSQL service was not replaced. Receipt:
`/tmp/caos-qemu91-current-main-20261018-r2/runner/runs/run-2b6451ac3585eb9c-eshlh0gf/run-receipt.json`.

Source tracing confirms the first wrong boundary: both pidfds ready followed by
`Popen.wait(timeout=0)` TimeoutExpired causes immediate uncertainty without
using the caller's remaining positive deadline. The same Popen object is
adopted by the supervisor and used by backend `communicate()`. CPython3.12.13
source and a controlled real-Popen reproduction establish that its waitpid
lock can cause this immediate timeout; the failed QEMU did not capture that
lock, so its actual contention is a hypothesis. Graphify found the supervisor
node and92 relationships; imports/adoption/communicate/wait paths were checked
against actual source. Candidate review requires a fresh deadline calculation
immediately before waiting outside the global supervisor lock, with ownership/
pidfd/reaping uncertainty preserved.

T131 R2 was independently reviewed and integrated into main. The canonical
regression module imports the actual service directly; the private candidate
loader/environment override was not ported. Root reproduced8 failures/7 passes
against the unchanged baseline, then15 passes against the integrated correction.
The same owned Popen and both pinned descriptors are retained while waiting
outside the global lock; the deadline is recomputed immediately before the
positive wait. timeout0/exhaustion/errors stay uncertain, and reattachment
still uses pinned exit proof without a Popen wait. R1's stale remaining-budget
candidate is preserved and excluded. Root receipts/logs:
`/tmp/caos-t131-root-integration-20261018-r1/`.
Broader regressions, configured typing/style/composition and a newly sealed
complete QEMU91 run remain required; these unit results do not close QEMU.


T131 main verification completed before final source seal:10 supervisor/restart/
cleanup/gateway/Bubblewrap/Docker-supervisor modules passed224 cases;22 cases
were skipped (21 require the absent accepted scratch Bubblewrap binary,1 lacks
scratch Bubblewrap/pidfd prerequisites) and remain assigned to the full QEMU
lane. The final exact canonical regression module passes15/15; its controlled
unchanged-baseline comparison reproduces8 failures/7 passes. Product AST is
identical to independently reviewed R2 after Black formatting. Configured mypy
passes400 source files and the new test separately; Black/isort pass both
changed files; composition passes5 contracts/0 broken. Root review:
`/tmp/caos-t131-root-integration-20261018-r1/composition-review.json`.
Static-check failures and an intermediate fixture omission of required
_monitor are preserved; the omission was corrected before the final exact
red/green run. An early R3 snapshot was canceled before atomic publication
so these corrections precede the final R4 seal. QEMU91 and final full backend
acceptance remain open; no historical receipt is relabeled as final-source.

### Current scoped T098 completion — 2026-10-19

The user-requested QEMU scope was the four remaining T098 selectors, not the
historical 91-case aggregate. The current-main final-group runner completed
exactly those four selectors with **4 passed, 0 failed, 0 skipped**, exit code
0, owned marker removal, and no cleanup error. The receipt is
`/tmp/caos-qemu-t098-final-group-current-main-20261019-t131-racefix-r2/runner/runs/run-79652b7293dbae38-eiau03id/run-receipt.json`.
Its private source manifest is
`/home/felni/.cache/caos-spec021-evidence-20261015/current-main-snapshot-20261019-t131-racefix-r2/artifacts/source-manifest.json`
with SHA-256
`42303a57365c5c874cb27db17bfae6c83d2f1d073477f404303c3262426e37a3`.
This closes the four-case scope requested for the current correction; it does
not relabel older incomplete QEMU91 receipts or claim the broader aggregate.

### Current-source backend R6 diagnostic — 2026-10-19

The unfiltered backend invocation used the fresh current-main snapshot
`current-main-snapshot-20261019-r6` (manifest SHA-256
`d1145beaa688246cb0b59931f6c2841828ca8af8cc77f32cf03ba6615bacc536`) and the
existing PostgreSQL service (metadata SHA-256
`fc4f29efe28eabccc23c107c662f9895490669abe8dd6721c2cce18a38c11dc0`). It ran
once with eight workers for 3516.689 seconds: **18,463 selected, 18,223
passed, 4 failed, 236 skipped, 0 errors, and 71 subtests passed**. Statement
coverage was **68,548/78,568 = 87.2467%**. The source manifest and index matched
before and after the run; no retry was performed and the PostgreSQL container
remained healthy with restart count zero.

The four failures were `test_actual_approved_file_criterion_refuses_fifo_with_bounded_owned_process`
(three-second child bound under loaded database/worker contention),
`test_roundtrip_runs_under_one_second` (1.9527287899982184 seconds under the
same load),
`test_composed_v2_receipt_result_and_projection_survive_service_restart[profile_context_drift]`
(a transient `target_not_git` during the loaded live revalidation path), and
`test_reset_replaces_old_execution_preserving_window_and_environment` (the
replacement tmux shell did not consume the immediate probe). The exact four
selectors were rerun once against the existing PostgreSQL service without
coverage or xdist and all **7/7 parametrized cases passed** in 87.51 seconds.
The memory case also has an independent cold/full-profile result below one
second; the FIFO implementation passed repeated focused runs. The FIFO,
memory, and profile-context results remain host/database contention or
non-reproduced timing diagnostics; tmux was a reproducible readiness race and
is corrected in the append-only section below. No threshold, guard, or product
behavior was relaxed to mask them. The full loaded gate remains open and this
diagnostic is not a full-green acceptance.

### Current-source Claude Unicode run-step diagnostic — 2026-10-19

One fresh, source-bound `claude_code->claude_code` cell ran from the immutable
R7 snapshot (manifest SHA-256
`5624d30ce53f11995f2b0da39cebffcd911895040a43bbbd3605f337b98239d6`) against
the existing PostgreSQL service. The run sent the Spanish/Japanese Unicode
instruction through `POST /terminals/run-step`, then returned HTTP **504** after
the provider task timeout; it did not produce the historical
`comparing strings with non-ASCII characters is not supported` exception. The
pytest cell therefore failed 0/1 at the provider timeout boundary, after
222.63 seconds. The server fixture cleaned up its owned resources, the
PostgreSQL container was reused without restart, and the operator Claude login
was not modified. Receipt:
`/tmp/caos-claude-unicode-current-20261019-r7/receipt.json`.

This is a current provider-timeout diagnostic, not successful Claude acceptance
and not retrospective attribution of the old `RECONCILE_REQUIRED` record. The
offline ASCII-receipt guards still pass; the historical record remains open for
manager-side reconciliation.

### Final current-main source seal after these validations — 2026-10-19

A final immutable current-main snapshot was created after the documentation and
offline verification above; its manifest and stability fields are recorded in
the private snapshot receipt.
HEAD remains `02309f3f1e8cd1cf3b66dc92ca4d9e6c21ccf2db`, the index hash remains
`825dd69d88401972c5765ac24018d916010d512df9f95814892d336f1b284e27`, and the
source inventory matched before and after copying. The freezer recorded 2,998
included entries, 27 deleted paths and 1,880 untracked paths, with no commit
created. This seal is the final current-tree binding for review; it does not
turn the open loaded-suite, provider, Work-message or historical-reconciliation
gates into passes.

### Current-source OpenCode generic-message diagnostic — 2026-10-19

A new OpenCode lane then ran from the R10 source snapshot with the Unicode
Spanish/Japanese message bodies. Both native OpenCode processes started, but
the sender never completed phase A: the safe receipt records zero MCP calls,
zero persisted rows, zero AG-UI events and zero API replacements. Cleanup
passed, the stale-launcher-hash preflight was false, and no 120-second provider
initialization timeout was observed. The lane was not retried and did not
replay the historical handoff. Receipt:
`/tmp/caos-opencode-current-20261019-r10-r2/receipt.json`.

This separates the old launcher hash defect from the remaining provider
phase-A failure; OpenCode live acceptance and historical reconciliation remain
open.

### Current-source tmux readiness correction and controlled backend follow-up — 2026-10-19

The loaded R6 failure in
`test_reset_replaces_old_execution_preserving_window_and_environment` exposed a
real readiness race: `respawn-pane` returned while tmux still reported its
bootstrap command, so an immediate probe could be sent before the replacement
shell owned the PTY. The main `TmuxClient.reset_window()` now waits, with a
bounded five-second deadline, for a live pane PID different from the old PID,
the expected shell command, and `pane_dead=0`. The original regression passed
in **16/16** independent copies under xdist `-n 8`; the pre-correction stress
run failed **8/16**. The focused integration test passed. No timeout or probe
threshold was weakened.

The other three R6 failures were investigated against the existing PostgreSQL
service with Docker autostart disabled. The FIFO child/checkpoint case passed
at `-n 2`, `-n 4`, and `-n 8`, plus four concurrent independent sessions; the
instrumented child joined in 0.923091 seconds. The profile-context-drift case
passed serially and in four concurrent sessions, with no escaped traceback.
The memory round-trip is below one second at one to four workers but fails at
eight workers (about 1.19–1.65 seconds; the loaded R6 sample was 1.95 seconds).
Profiling shows concurrent PostgreSQL query-path contention, not lock waits.
No production guard, audit, verification, or SLO condition was relaxed for
these non-reproducible/host-saturation failures.

### Current-source Claude/OpenCode provider diagnostics — 2026-10-19

The fresh Claude Unicode probe reached the Unicode prompt and marker and did
not reproduce the historical non-ASCII comparison exception. CAOS returned
HTTP 504 after its 180-second provider-task timeout; no explicit provider
HTTP/auth failure was observed, and the operator login metadata was unchanged.
The result is diagnostic only and leaves the historical reconciliation record
open.

The fresh OpenCode generic-message probe started both native processes without
the old stale-launcher-hash preflight or a 120-second initialization timeout,
but ended in phase A with zero MCP calls, rows, events, and API replacements.
The earlier stale-hash stop was therefore a real source-binding defect already
fixed by the fail-closed launcher guard; this later phase-A result has no
reproducible product boundary to patch from the retained evidence. Neither
provider lane was replayed.

The single fresh R14 OpenCode follow-up used the post-tmux source seal and the
same allowlisted model. It again reached phase A after 202.31 seconds with
both native processes started, no stale-hash preflight, no initialization
timeout, zero MCP calls, zero persisted rows, zero AG-UI events, zero API
replacements, and successful cleanup. The safe receipt reports no provider
error enum or HTTP status to classify. This confirms the old launcher defect is
fixed but does not supply a reproducible CAOS boundary for the remaining
provider/model turn failure; no further OpenCode replay is justified.

### Current-source bounded-load follow-up — 2026-10-19

The three selectors that had failed only in the loaded R6 worker run were
rerun together with `-n 8` against the same healthy PostgreSQL container:
the FIFO owned-process bound, the `profile_context_drift` restart projection,
and the tmux replacement-shell regression. All **3/3 passed** in 49.77 seconds
with no code or threshold relaxation. This supports the earlier diagnosis that
those three observations were host scheduling/contention effects rather than
stable product failures.

The strict backend N8 latency gate remains red under the same host capacity:
the fresh-verification round trip measured roughly **1.18–1.69 seconds** at
eight workers (the R6 loaded sample was 1.95 seconds), while N1/N2/N4 stayed
under one second. PostgreSQL plans showed buffer hits and no lock or disk-read
waits; the cost is repeated catalog verification and CPU scheduling. The
correct scalable remedy is capacity (CPU/PostgreSQL headroom and a bounded
pool sized for the concurrency target), not caching, batching, or omitting
the required fresh verification/audit. The `<1 s` gate and its semantics were
left unchanged, so the full loaded acceptance remains accurately reported as
incomplete.

### Provider timeout and OpenCode preflight correction — 2026-10-19

The isolated live-provider gate now validates the selected Claude OAuth receipt
before looking up or launching the CLI. It requires the non-secret access and
refresh fields and, when `expiresAt` is present, accepts both Unix seconds and
milliseconds while rejecting a stale access credential with a privacy-safe
actionable failure. Both paths reserve a 30-second safety margin. The
production Claude startup route applies the same freshness rule, so an expired
or immediately expiring access token no longer authorizes an automatic
subscription-route selection that can wait for an interactive refresh until
the 180-second task deadline. An explicit `CLAUDE_CODE_OAUTH_TOKEN` remains an
operator-owned override. The source-bound run using the active, unmodified
login copied into a private HOME passed **1/1** (`/terminals/run-step` HTTP
200); the earlier Unicode prompt did not reproduce the historical comparison
exception. The operator login was not changed.

The OpenCode v2 writer now has a bounded preflight immediately before launch:
the generated CAO MCP server must be enabled with a concrete command and the
selected agent must carry an allow grant for that server. A missing grant now
fails before tmux launch instead of becoming an unattributed no-tool phase
timeout. The preflight and provider regressions passed **252** focused tests;
no model turn is replayed or silently resent when a valid configuration still
produces no tool call. The strict PostgreSQL N8 `<1 s` result remains a host
capacity failure under eight concurrent workers and was not weakened.

### OpenCode no-tool diagnosis and bounded classification — 2026-10-19

The earlier current-main provider receipt stopped OpenCode at the old
`agent_tool_scope_mismatch` boundary. A source-bound run against the corrected
writer reached `agent_tool_scope_exact=true` and the native MCP connected
marker, so that configuration defect is fixed. The selected free provider
model then completed no interaction call before the bounded task budget; the
run produced no questions and was cleaned up. The evidence does not by itself
prove whether the model completed a text-only turn or the v2 composer never
submitted the prompt.

The OpenCode acceptance path now gives the profile and prompt the same three
fully qualified tool names, separates `idle_no_turn` from `completed_turn`,
and reports `prompt_not_submitted` versus `model_completed_without_tool`
without retaining provider text. It probes the final status after a short
grace period instead of waiting the full budget when the state is already
decisive. Offline coverage for the OpenCode/AG-UI journey is **143 passed**;
the provider harness is **64 passed**. No model prompt was replayed.

One AG-UI OpenCode rerun reached the worker-start phase but the API worker did
not become ready within its existing 10-second bound under host CPU pressure;
the owned worker and private run were removed cleanly. The bound was not
increased to disguise the environment failure, and no credential or shared
container state was modified.
