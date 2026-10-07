# Auditoría profunda y corrección de errores reales

Fecha: 2026-10-05. Alcance: los hallazgos de [pruebas-reales-adicionales-20261005.md](pruebas-reales-adicionales-20261005.md), sus consumidores y sus fronteras inmediatas. Proyecto de prueba: `C:\Users\ferna\OneDrive\Escritorio\cao-collaboration-demo-20261001-cf8017b2`.

## Resultado ejecutivo

Se corrigieron las discrepancias del contrato de la demo, la contaminación de respuestas LAST de OpenCode, la interpretación insegura de una frase citada del contrato, el falso fallo del comprobador por líneas partidas operaciones síncronas que bloqueaban el bucle HTTP en continuaciones y coordinación, y una colisión de snapshots entre ejecuciones con el mismo run_id.

La repetición real final completó dos rondas Claude/Codex/OpenCode con recibos verificados, exactamente dos callbacks nuevos, respuestas limpias y las mismas terminales. Las 14 muestras de salud devolvieron HTTP 200, máximo 0,19 s, con entre aproximadamente 931 y 2223 MiB disponibles y swap prácticamente agotada. Las huellas de los cinco módulos vigilados permanecieron idénticas durante la prueba. Se verificaron además los cuatro módulos efectivamente cargados por este servidor: corresponden al código actual; script_runner se acredita mediante sus pruebas de procesos y aislamiento, pues esta colaboración de revisión no inició un workflow script.

**Veredicto: PASS WITH RISKS.** Los defectos reproducidos tienen corrección y regresiones; no se garantiza disponibilidad bajo agotamiento físico y no se atribuye cada uno de los tres timeouts históricos a una causa individual demostrada. El escenario integral SC-006 continúa fuera de esta aceptación.

## Aplicación de AI_WORKFLOW.md

| Paso | Evidencia |
|---|---|
| Estado inicial y preservación | `git status` guardado en `/tmp/cao-audit-status-start.txt`; snapshots del incremento en `/tmp/cao-audit-baseline-20261005/` |
| Clasificación | `bug-significant`: API/contrato, parser/recibos, bucle async/SQLite/coordinador y validación real |
| Decisión de conocimiento | Este informe y spec007 conservan las conclusiones; no se duplican logs ni Graphify en Obsidian |
| Spec Kit | FR-026–030 y T038–T043 añadidos en orden spec → plan → tasks; setup-plan/setup-tasks sobre spec007; se conservó el selector preexistente de spec008 |
| Impacto Graphify | Consultas sobre OpenCodeCliProvider, WorkflowContinuationDriver y materialización de snapshots; relaciones verificadas contra fuente; actualización AST final acotada: siete archivos, 759 nodos, 1.855 relaciones, cero fuentes fallidas y cero tokens LLM |
| Diagnóstico | Capturas históricas del servidor, pruebas HTTP, fixture nativo y localización de la primera frontera incorrecta |
| TDD | Fallos antes de corregir; prueba nativa adicional vuelve a fallar con el módulo anterior y pasa con el actual |
| Composición | SQLite real, HTTP real, procesos Python reales, Uvicorn, tres CLI reales y Chromium |
| Arquitectura | Gate del proyecto e Import Linter: cinco contratos conservados, cero rotos |
| Cierre | Revisión del incremento, manifiestos de código cargado, limpieza de runtimes propios y verificaciones frescas |

No se creó otra feature ni otra rama. La demo no tiene repositorio Git propio; se congelaron sus archivos relevantes antes de editarlos. Las modificaciones de código y documentación se aplicaron al proyecto original autorizado, pero sus datos `data/tasks.json` se preservaron. Las pruebas con escrituras utilizaron datos temporales o copias.

## Hallazgos y causas

### A1. Codificación de peticiones contraria al contrato

**Causa:** `json.loads(bytes)` detecta UTF-16/UTF-32 además de UTF-8. La API declaraba exclusivamente UTF-8, pero aceptaba esos cuerpos con HTTP 201.

**Corrección:** `api/server.py` decodifica los bytes explícitamente como UTF-8 antes de deserializar JSON. El manejo existente de ValueError incluye UnicodeDecodeError y devuelve JSON 400.

**Pruebas:** UTF-16 y UTF-32 pasan de 201 a 400; bytes UTF-8 inválidos siguen devolviendo 400; los rechazos no crean datos. HTTP real con archivos temporales, en Python 3.12.13 y 3.14.4.

### A2. Límite de cuerpo no documentado

**Causa:** el servidor limitaba a 16.384 bytes, mientras el contrato sólo exigía título no vacío. El formulario tiene una restricción distinta, `maxlength=200`.

**Corrección:** el límite existente queda nombrado como `MAX_REQUEST_BYTES` y documentado en CONTRACT.md/README.md. Se conserva compatibilidad con títulos más largos admitidos por la API cuando el cuerpo cabe en el límite; no se impone un nuevo máximo de título ni se invalida información existente.

**Pruebas:** cuerpos de exactamente 16.384 bytes con ASCII y con `ñ` se aceptan; un byte adicional produce 400; el archivo queda idéntico después del rechazo. Se documenta que se cuenta el JSON entero, no sólo el título.

### A3. Sustitutos Unicode aislados provocaban 500

**Origen:** Codex señaló este caso durante la auditoría real. Se reprodujo: `{"title":"\ud800"}` producía 500 y alcanzaba la escritura temporal antes de fallar.

**Causa:** JSON admite el escape en su representación sintáctica, pero ese valor no es texto que pueda codificarse en UTF-8; `save_tasks(... ensure_ascii=False)` falla al escribirlo.

**Corrección:** validar la codificación del título antes de entrar a la sección de persistencia; devolver JSON 400. Se añadió la regla al contrato.

**Pruebas:** sustituto aislado rechazado, sin archivo de datos ni archivo `.tmp`; par válido `\ud83d\ude00` aceptado como 😀 mediante HTTP real y datos temporales. No se hace normalización ni se cambia el texto válido.

### B1. LAST de OpenCode incluía instrucciones de entrega

**Causa:** dos rutas elegían la respuesta de v2. La ruta por recibo actual excluía el contrato completo; la ruta por duración usaba la primera línea vacía del turno. Un prompt con párrafos podía hacer que el contrato y parte de la tarea se interpretaran como respuesta del asistente.

**Corrección:** ambas rutas reutilizan `_v2_without_delivery_contract`, respetando el bloque completo incluso cuando el terminal lo envuelve. El resultado conserva saltos de línea legítimos, respuesta y recibo final.

**Pruebas:** casos con párrafos, contrato envuelto y marcador `RONDA2_DO`/`NE`; fixture nativo `opencode-v2-multiline-contract-real.txt` capturado de la terminal propia. En ese fixture sólo se reemplazaron nonces por valores ficticios y la raíz temporal por una ruta neutra. El módulo congelado anterior devuelve el contrato y falla la regresión; el módulo corregido la aprueba. La repetición real también rechaza cualquier LAST que aún contenga el encabezado del contrato.

### B2. Una frase citada podía exponer el recibo del eco

**Causa:** la exclusión anterior buscaba la frase final del contrato sin exigir que apareciera después de su encabezado. Si la tarea citaba esa frase antes del contrato auténtico, el parser podía seleccionar el bloque equivocado. Con el nonce envuelto en el eco, `get_status` llegaba a COMPLETED sin respuesta del asistente.

**Corrección:** localizar el último bloque `CAO completion receipt requirement:` que la entrega añade tras la tarea y después su cierre. Si el bloque está truncado, no se expone como respuesta. Una cita anterior de un contrato completo tampoco establece la frontera actual. Una frase anterior de la tarea no establece esa frontera.

**Prueba red/green:** la tarea cita la frase y el eco contiene el nonce en una línea independiente. Antes produce COMPLETED; después no se considera terminado y la extracción falla como corresponde. No se relajan el nonce, su digest, generación, CAS ni la reconciliación de entregas. Dos regresiones adicionales cubren el contrato truncado y la cita de un contrato completo: ambas fallaron antes de completar esta corrección y después pasan.

### C1. El comprobador fallaba por una línea partida

**Causa:** buscaba el literal `RONDA2_DONE`; el terminal lo representó en dos líneas, aunque el recibo real estaba verificado y la secuencia completada era actual.

**Corrección:** runner durable `scripts/validate_collaboration_demo.py`. La comprobación de marcador contempla espacios/saltos, pero exige también:

- LAST HTTP 200 y resultado no vacío.
- Estado `verified`, generación existente y secuencia actual positiva completada.
- Secuencia devuelta por la entrega de segunda ronda y generación distinta de la anterior.
- Dos callbacks nuevos, exactamente uno por trabajador, y síntesis nueva del supervisor.
- Las mismas tres terminales y manifiestos sin cambios durante la prueba.

**Pruebas:** 14 casos del comprobador rechazan recibos pendientes, generaciones antiguas, secuencias incorrectas, respuestas vacías y HTTP 409. No hay reenvío automático para una aceptación incierta. El fallo literal inicial se conserva en el informe anterior.

### D1. SQLite bloqueaba el bucle que atiende HTTP

**Evidencia histórica:** una captura del hilo principal contiene la cadena `WorkflowContinuationDriver.serve → tick → WorkRepository.read_snapshot → _verify → _schema_objects`. Esa verificación se ejecutaba directamente en el bucle async compartido con HTTP.

**Causa:** `tick` combinaba awaits con lecturas/transacciones SQLite síncronas. También había consultas síncronas en `drive`, `shutdown`, `WorkCoordinator.resume` y `stop`.

**Corrección:** operaciones de repositorio, autoridad y journal pasan a workers mediante `asyncio.to_thread`. Cada conexión se abre, verifica, usa y cierra dentro del mismo worker; ninguna conexión cruza un await. La creación/cancelación de tasks y la terminación de subprocess asyncio permanecen en el bucle. Se conserva la comprobación de epoch dentro de la misma transacción que reclama el evento.

Se mantienen consultas, parámetros, orden de fencing, CAS de claim/consume y evidencia de parada. No se elimina verificación del esquema, no se cachea autoridad y no se modifica ninguna migración. El journal sólo se consulta en la rama que ya lo necesitaba.

**Reproducciones:** un snapshot real deliberadamente lento bloquea antes un observador del bucle; la parada del coordinador toca SQLite desde ese mismo hilo. Ambas fallan antes y pasan después. Una prueba adicional con Uvicorn y socket loopback reales devuelve `/health` mientras la verificación SQLite sigue deliberadamente pendiente.

### D2. Health sondeaba PATH en una ruta async

**Causa reproducida:** `shutil.which` hace acceso síncrono al sistema de archivos. La ruta `/health` era async y ejecutaba esas comprobaciones en el bucle HTTP; un PATH lento bloqueaba trabajo no relacionado.

**Corrección:** la ruta es síncrona y FastAPI la ejecuta en su worker. Se conserva exactamente el contrato HTTP y los nombres de componentes. No se convierte una falta de ejecutable en un éxito ficticio.

**Prueba:** `shutil.which` bloqueado de forma controlada; el bucle continúa y el payload sigue reportando `unavailable` correctamente. Esta corrección evita bloquear otras solicitudes, pero no promete que un sondeo individual de filesystem nunca tarde.

### E1. Dos ejecuciones podían sobrescribir o borrar su snapshot

**Causa reproducida:** `_materialize_snapshot` usaba `resume-{run_id}.py` con O_TRUNC en una raíz compartida. Dos materializaciones del mismo identificador escribían el mismo archivo; el cleanup de una eliminaba el archivo que aún necesitaba la otra. La colisión no depende de que los dos procesos compartan la misma base de datos.

**Corrección:** creación exclusiva mediante `tempfile.mkstemp`, sufijo único por materialización, permisos de archivo 0600 y carpeta 0700. La limpieza elimina únicamente la ruta que pertenece a esa ejecución. Un fallo al envolver el descriptor cierra el descriptor y borra su archivo sin ocultar la excepción original. Se conservan el guard de autoridad, el snapshot durable y la barrera privada de admisión.

**TDD y composición:** tres pruebas fallaron antes de corregir y luego pasaron: dos snapshots del mismo ID ejecutados mediante Python real y limpiados independientemente; 16 materializaciones concurrentes con contenido independiente; fallo de fdopen sin fuga de archivo o descriptor. Las pruebas existentes de runner, planes preparados y barrera privada se adaptaron al nombre exclusivo sin debilitar la comprobación de limpieza. Suite focalizada: 98 passed.

### E2. Fallo intermitente de observación del proceso hijo

Una pasada anterior del conjunto de 395 casos terminó con 394 passed y un fallo en `test_real_private_fd_barrier_records_process_before_any_work_effect`: `/proc/<pid>/environ` estaba vacío antes de conectar la barrera privada. No hubo efectos Work autorizados. La repetición individual pasó y la repetición completa en serie dio 395 passed. Se añadieron diagnósticos de returncode y stderr de arranque, redactando la credencial, y se conservó la aserción estricta; no se añadieron reintentos ni skips.

Una exploración de 80 procesos Python reales, en lotes de ocho, no reprodujo entornos vacíos, marcadores ausentes ni salidas fallidas. No se atribuye el fallo al OOM killer: el contador observado era cero. La investigación posterior encontró y reprodujo E1; la colisión es una explicación candidata para una salida temprana, pero el stderr histórico no fue capturado y **no se demuestra que E1 provocara aquel fallo concreto**. El defecto de aislamiento sí quedó corregido y tiene prueba directa.

## Hipótesis que no justificaron cambios

Los proveedores advirtieron que un JSON muy anidado podía cerrar la conexión por RecursionError. No se reprodujo en estos runtimes: las peticiones comprobadas devuelven JSON 400 y los archivos anidados corruptos producen JSON 500 en GET/POST/PATCH, conservando el archivo. Se probaron 1.500 niveles por HTTP y se exploró la deserialización hasta 8.000; el límite de cuerpo acota la entrada.

Se añadieron dos pruebas de contrato que ya pasan en Python 3.12 y 3.14. No se añadió una captura especulativa de excepciones ni un límite nuevo de profundidad. Los hallazgos de los agentes se trataron como hipótesis hasta verificarlos.

La revisión frontend también señaló respuestas exitosas o de error que violan el contrato: un cuerpo no JSON puede mostrar el mensaje del parser y un sobre POST/PATCH incompleto puede no mostrar un error nuevo. No se encontró una ruta del servidor actual que produzca esos sobres exitosos; son mejoras de robustez frente a respuestas externas inválidas. La prueba real sí comprobó el manejo de errores HTTP 500 y el rollback de PATCH. Estas mejoras de validación de sobres quedan propuestas, no implementadas ni presentadas como errores corregidos.

## Revisión de composición

### Superficie examinada

- Ingreso HTTP → decodificación/validación → lock de tareas → escritura atómica → frontend.
- Captura nativa OpenCode → selección de respuesta → recibo/generación → LAST HTTP → supervisor/comprobador.
- Lifespan → driver de continuaciones → verificación SQLite/journal → coordinador → tasks/proceso → endpoint health.
- Vecinos revisados: BaseProvider, terminal_service, inbox_service, WorkRepository, workflow_journal, projector, script_runner y contratos del frontend.

### Invariantes comprobadas

| Frontera | Invariante / prueba |
|---|---|
| Datos de demo | Rechazo antes de escritura; lock conserva creaciones concurrentes; reemplazo atómico y reinicio ordenado |
| SQLite/worker | Apertura y cierre en el mismo hilo; verificación de esquema conservada; no se transportan conexiones entre awaits |
| Autoridad/leases | Owner autenticado, epoch dentro del claim, observador no revoca el lease ajeno, proceso vivo impide recuperar autoridad |
| Outbox | Idempotencia de proyección, consumo condicionado por claim_epoch, continuación sin volver a crear Work pendiente |
| Parada | Fencing antes de terminación; salida de Python no equivale a cleanup de Work; evidencia física sigue necesaria |
| Recibos | Eco/citas/nonce antiguo no acreditan finalización; callbacks no sustituyen el recibo del turno actual |
| HTTP | Formas y códigos conservados; bloqueos de I/O no detienen el bucle compartido |
| Validación | Marcador sólo identifica la respuesta; no reemplaza estado durable, secuencia ni generación |
| Snapshot/proceso | Archivo exclusivo por materialización; limpieza no afecta al vecino; FD cerrado ante fallo de arranque |

No hay un cambio de esquema o protocolo que requiera migración. Se usaron tests del proyecto como comprobación equivalente de contratos; no se añadió Pact porque no hay servicios externos versionados que se desplieguen independientemente. SQLite, HTTP, Uvicorn y procesos reales permiten comprobar estas fronteras sin un contenedor; Docker no fue necesario para esta aceptación.

## Verificaciones

- CAO: **483 passed, 7 warnings**, 111,37 s; 18 archivos afectados, incluida la regresión del fixture nativo, snapshots, runner y planes preparados. Sin skips en esta pasada final.
- Demo: **11 casos API PASS en Python 3.12.13 y 3.14.4**; **1 caso frontend Node PASS**.
- Chromium: creación/completado/recarga, título HTML inerte y recuperación de errores PASS, cero pageerrors.
- HTTP real: 24 POST concurrentes sin perder registros ni repetir IDs; 11 casos negativos; persistencia tras reiniciar; archivo corrupto no sobrescrito y recuperación posterior PASS.
- Gate final: **PASS**, 396 archivos, 1.660 dependencias, cinco contratos conservados y cero rotos.
- Los bytes de `data/tasks.json` del proyecto autorizado se conservaron durante los cambios y pruebas.

### Tres proveedores reales en el código final

| Proveedor | Terminal | Papel |
|---|---|---|
| Claude Code / sonnet | `438f1c36` | Supervisor |
| Codex / gpt-6.1-sol | `45639115` | Revisión API |
| OpenCode / opencode-go/gpt-6-luna | `72dd6886` | Revisión frontend |

Dos rondas PASS; recibos actuales verificados; callbacks nuevos y sin duplicados; mismos tres agentes. Segunda ronda enviada por el comprobador a los trabajadores existentes. Los agentes revisan código; las pruebas HTTP/navegador las ejecuta el runner técnico por separado. No se atribuye a los proveedores la ejecución de esas pruebas.

Runtime final privado `cao-real-audit-20261005-ayay83fy`, eliminado al terminar. Logs conservados en `/tmp/cao-investigation-evidence-20261005/cao-real-audit-20261005-ayay83fy/`.

Las huellas registradas durante la carga y verificadas contra el código actual son:

| Módulo | SHA-256 |
|---|---|
| api.main | `9ad7f6660699b22d7da3a324f125e313b5792c42a33694defce33a34a161e824` |
| providers.opencode_cli | `8e1931a827ad89b90bffe1a49bd6b59a3e8597f7782fc1e0ab8f664f10a7afe9` |
| services.work_coordinator | `c6d867d1221ef696aeee49db8d5f1a39557e909b7d6e9bcf20188f297321c474` |
| services.workflow_continuation_driver | `3bd0238ef15df5155757dcc016d42d9d9e49e3bb70ea7f5d291e8988956499bc` |

El quinto módulo vigilado, `services.script_runner`, mantuvo SHA-256 `9e7a504c7099bf5b472eee9c4ae5422ed31fa7f63344d03b9fd92438bb81f8fb`. No figura en el manifiesto de importación de este servidor porque la revisión no inicia scripts. Su ejecución real, materialización concurrente, permisos y limpieza se comprueban en la suite final. Se evita presentar vigilancia de archivos como evidencia de carga o ejecución.

## Reproducción y evidencia

Runner conservado:

```bash
.venv/bin/python scripts/validate_collaboration_demo.py \
  --project /mnt/c/Users/ferna/OneDrive/Escritorio/cao-collaboration-demo-20261001-cf8017b2
```

Requiere los tres CLI y logins locales existentes. El runner prepara perfiles de prueba antes de la tarea, aísla HOME/CAO/tmux/puerto y limpia su runtime. Detecta si se modifican los módulos CAO durante la prueba y conserva las huellas de carga junto a sus diagnósticos.

Pruebas de la demo:

```bash
python3 /mnt/c/Users/ferna/OneDrive/Escritorio/cao-collaboration-demo-20261001-cf8017b2/tests/test_server.py
.venv/bin/python /mnt/c/Users/ferna/OneDrive/Escritorio/cao-collaboration-demo-20261001-cf8017b2/tests/test_server.py
node --test /mnt/c/Users/ferna/OneDrive/Escritorio/cao-collaboration-demo-20261001-cf8017b2/tests/test_frontend.js
project-composition-check "$(cat .ai/project-name)"
```

Evidencia local temporal:

- `/tmp/cao-audit-demo-red.log`, `/tmp/cao-audit-surrogate-red.log`: fallos de demo previos a corrección.
- `/tmp/cao-audit-opencode-red.log`, `/tmp/cao-audit-echo-red.log`, `/tmp/cao-audit-native-baseline-red.log`: fallos del parser, incluido fixture real con módulo anterior.
- `/tmp/cao-audit-responsiveness-red.log`, `/tmp/cao-audit-stop-red.log`: fallos de bloqueo/afinidad antes de corrección.
- `/tmp/cao-audit-combined-final.log`: 483 casos finales CAO.
- `/tmp/cao-audit-release-suites.log`, `/tmp/cao-audit-release-serial-suites.log`: fallo intermitente histórico y repetición serial de 395 casos.
- `/tmp/cao-audit-snapshot-red.log`, `/tmp/cao-audit-snapshot-green.log`: colisión reproducida y 98 regresiones focalizadas.
- `/tmp/cao-audit-proc-environ-race.json`: exploración de 80 procesos, sin reproducir el fallo de observación.
- `/tmp/cao-audit-demo-python312-final.log`, `/tmp/cao-audit-demo-python314-final.log`, `/tmp/cao-audit-frontend-final.log`: tests de demo.
- `/tmp/cao-audit-browser-final.log`: HTTP/navegador/persistencia posteriores a las correcciones.
- `/tmp/cao-audit-five-modules-real-final.log`: colaboración final con huellas de los cinco módulos.
- `/tmp/cao-audit-five-modules-composition-final.log`: gate final.
- `/tmp/cao-audit-final-ast.json`: actualización AST acotada del código final.
- `/tmp/cao-audit-increment-final.diff`: revisión del incremento sobre los archivos congelados.

## Límites y pendiente

1. La evidencia corrige un bloqueo real y reproducible del event loop. La presión de memoria es una condición observada; no se garantiza que desaparezcan todos los timeouts bajo cualquier carga o montaje lento.
2. Se comprobó persistencia tras reinicio ordenado, no apagado brusco ni pérdida eléctrica.
3. El parser sigue dependiendo de la representación nativa de OpenCode v2; se conserva una captura real para detectar cambios posteriores.
4. El fallo intermitente del entorno del hijo no se reprodujo en la pasada final; la causa de aquel episodio no quedó demostrada. La colisión de snapshots es un defecto independiente confirmado y corregido.
5. El paquete probado es el código editable del repositorio. No se publicó ni se generó una imagen Docker en esta auditoría.
6. T020/SC-006 permanecen pendientes: falta el escenario único de implementación e integración dirigido por proveedores. Revisión, mensajes y navegador por separado no se presentan como ese escenario.

## Cierre del workflow

T038–T043 verificadas. Orden de cierre: pruebas finales → gate → revisión de composición → revisión del incremento y archivos nuevos → actualización AST acotada → persistencia de conclusiones en spec/informe → verificación final de hashes, selector, estado y cleanup. Sin commits, publicación ni cierre de procesos ajenos. Aceptación limitada al alcance descrito; T020/SC-006 continúa pendiente.

### Actualización posterior: pendiente integral completado

T020/SC-006 se verificó posteriormente en [aceptacion-integral-20261005.md](aceptacion-integral-20261005.md). Los tres proveedores implementaron e integraron una función real en la copia, con tests y Chromium sobre el mismo artefacto. Las referencias anteriores a T020 pendiente son históricas. Las causas individuales de los cierres históricos no demostrados permanecen como límites; no se presentan como corregidas por esta aceptación.
