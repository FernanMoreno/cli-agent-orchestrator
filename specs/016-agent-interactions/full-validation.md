# Validación completa solicitada

Estado actual: **validación pendiente tras la migración PostgreSQL**. Los resultados
completos de este documento corresponden al snapshot histórico anterior a
Spec021. Sus cifras de cobertura y tests no certifican el código actual. La
suite global PostgreSQL r9 se interrumpió con un INTERNALERROR y debe repetirse;
véase el registro vigente en
[validation.md de Spec021](../021-postgresql-persistence/validation.md).

La validación local histórica completó las suites Python, web, aplicaciones y
navegador sobre su versión congelada, con aceptación externa pendiente. Se
conserva esa evidencia como cronología, sin trasladar su aprobación al código
posterior.

Continuación autorizada: la nueva proyección AG-UI y aceptación Docker/host se registran en [agui-host-validation.md](agui-host-validation.md). Los resultados completos de este documento corresponden a la versión anterior; no certifican los cambios AG-UI hasta terminar su nueva validación.

## Alcance y condiciones de aceptación

- Suite Python completa de CI, incluidas las integraciones marcadas; inventario y ejecución separada de E2E y pruebas opt-in con dependencias reales.
- Black, isort, mypy, arquitectura y revisión de composición.
- Todos los tests web, build y todos los E2E Playwright, con preguntas de los cinco tipos, reintento tras respuesta perdida, conflicto, espera/retorno/ack, recuperación y privacidad por nodo.
- Recorrido del proceso MCP real desde el navegador, no únicamente una llamada directa al servicio.
- Pruebas reproducibles de CLIs reales atravesando configuración de producción. Distinguir inferencia externa de un modelo simulado en loopback; ambas aportan evidencias diferentes.
- WebSocket real compuesto con el ledger: petición explícita de credenciales bloqueada sin bytes/registro; diálogo nativo manual autorizado; incarnación obsoleta denegada; cancelación sin respuesta inventada.
- Registrar fallos, skips, versiones, configuración acotada y dependencias ausentes. Ningún skip o mock se presenta como recorrido externo satisfactorio.

## Referencia de CI y reproducción de los defectos

Referencia `HEAD 02309f3f` en worktree aislado `/tmp/caos-spec016-validation`:

- `black --check src/ test/`: exit 0, 1192 archivos sin cambios.
- `isort --check-only src/ test/`: exit 0.
- `mypy src/`: exit 0, 398 archivos, sin errores.

Árbol con la primera implementación, antes de las correcciones de esta ronda:

- `black --check src/ test/`: exit 1, 41 archivos pendientes de formato.
- `isort --check-only src/ test/`: exit 1, imports sin ordenar en archivos de la funcionalidad.
- `mypy src/`: exit 1, 83 errores en 8 archivos. Son errores introducidos por la funcionalidad, no una referencia preexistente.
- Nuevas pruebas dirigidas de ledger: 3 passed / 2 failed. Cancelar un miembro opcional después de TTL no rechazaba la operación; aceptar 20 respuestas requeridas sin recuperarlas permitía una pregunta 21 aún con dependencias activas.
- La publicación de capacidad comenzaba con el flag durable habilitado: si fallaban la instalación y la escritura de su deshabilitación, la publicidad de soporte podía quedar incorrecta. Se cambia a registro pendiente → publicación privada → activación exacta → inicialización de la CLI.
- El harness antiguo de Codex no usaba la configuración de producción. El handshake MCP medido tarda 20,86 s, superior al margen opcional inicial de 10 s; el builder de producción añade required y 60 s de arranque. Además se verifica que el filtro enabled_tools llegue a la CLI.

El primer intento de suite Python total se interrumpió explícitamente para corregir estos defectos y evitar certificar código reemplazado: 1238 tests habían pasado, exit 2 / KeyboardInterrupt. **No cuenta como suite completa pasada.** El montaje Windows ralentiza la ejecución; la repetición se realiza sobre una copia del código final en un worktree Linux, con hashes comprobados contra el workspace y dependencias de la misma venv.

## Dependencias de entorno

- tmux y Bubblewrap disponibles.
- Docker CLI disponible, pero el daemon en `/var/run/docker.sock` no está disponible. Los tests que requieren contenedores no pueden certificarse con ese entorno.
- El gate T097 de host exige un runner Linux aprobado; su wrapper de CI cambia temporalmente una política AppArmor en una VM desechable. Esta sesión no se presenta como ese runner ni cambia políticas globales del host.
- Chromium requiere la biblioteca extraída previamente bajo `/tmp/cao-playwright-libs`; los E2E se ejecutan con esa ruta de librerías local.
- Hermes: launcher global inválido por shebang hacia un intérprete eliminado. Existe un venv gestionado válido con el que la CLI real muestra su diálogo clarify y recibe la selección contra un modelo simulado exclusivamente en loopback. Esa evidencia no equivale aún a un canal explícito CAO configurado en producción.

## Ampliaciones surgidas de dependencias reales

- Hermes: el diálogo clarify real se clasificaba COMPLETED. La regresión usa un viewport sanitizado y exige título reciente más hint activo; la CLI/PTy real contra modelo local simulado pasa 37 tests junto con el proveedor. Esa prueba registra una fuente de fixture, por lo que no acreditaba el alta normal de terminal. Al revisar el alta se detectó que Hermes no disponía de fuente para el monitor: se desarrolla una rama sólo de observación, sin canal explícito ni permisos nuevos.
- Codex: un filtro enabled_tools vacío/parcial anunciaba soporte explícito aunque faltaran ask/wait/ack. Se corrige la elegibilidad sin anexar herramientas. La CLI se comprueba con el ejecutable de la release concreta; varios intentos preliminares fallaron por montaje del harness que ocultaba ese ejecutable o impedía escritura de estado, sin inferencia.
- OpenCode 2.0.18: el intento local emitió un diagnóstico invalid/skipped para la entrada MCP. La primera hipótesis nested-vs-flat se descartó al aislar un confundidor: el normalizador admite mcp.servers, pero el campo timeout=65000 del harness produce el rechazo. Se investiga unidad/rango y la configuración de producción antes de modificar comportamiento. La aceptación requiere conexión MCP y herramientas reales; ausencia de diagnóstico no equivale a éxito.

## Evidencia intermedia histórica

- Mypy del árbol con el registro observacional de Hermes: exit 0, 405 archivos, sin errores (`/tmp/caos-spec016-current-mypy.log`). La copia Linux anterior también pasa. Se corrigieron los 83 errores originales; comprobaciones parciales intermedias y dos errores de narrowing se conservaron en sus logs. La repetición completa posterior al binding de propietario también pasa: **405 archivos, exit 0**, `/tmp/caos-spec016-final-mypy.log`.
- Composition: exit 0, cinco contratos conservados, cero rotos (`/tmp/caos-spec016-full-composition.log`). Se repetirá tras las últimas correcciones de integración.
- Copia Linux: 1223 archivos Python contrastados por SHA-256; paquete importado desde `/tmp/caos-spec016-validation/src/`. Una última corrección de tipos del helper se verificará también con la regresión de su fallback canónico.
- Python completo, sin excluir integraciones: ejecución separada de `-m 'not e2e'` y `-m e2e`, cuatro y dos workers respectivamente, con cobertura y XML. Primera ejecución terminada: **16637 passed, 252 failed, 117 skipped, 6 errors**, 2661,39 s, exit 1. No se da por pasada. Log/XML en `/tmp/caos-spec016-full-backend.{log,xml}`. Se prepara una repetición final tras corregir el runner y las regresiones de fixtures.
- E2E legacy externos: **13 failed**, interrumpido explícitamente tras 1843,85 s (30:43), exit **2**. No es una suite completada. Tres casos Codex esperan idle/completed pero quedan waiting_user_answer; seis casos Claude y cuatro Kiro fallan en el alta/inicialización. Ambos servidores responden a /health, pero los HOME privados de los fixtures carecen de credenciales y una pantalla Claude muestra inicio de sesión. Los fixtures exigen sólo presencia del binario y las peticiones POST legacy no tienen timeout; el alta falla tras 120 s y reintenta tras 30 s. Se conserva el log/XML y se compara un caso contra HEAD antes de atribuirlo a la funcionalidad. No se copian credenciales ni se presenta este entorno como una sesión autenticada.
- Comprobación independiente del árbol actual: registro observacional + instalación explícita + composición WebSocket real, **12 passed**, 20,74 s (`/tmp/caos-spec016-final-observer.log`). Hermes CLI/PTy real contra modelo simulado en loopback, **1 passed**, 20,35 s (`/tmp/caos-spec016-root-hermes-retry.log`). Un primer lanzamiento del runner falló antes del test porque no existía el padre del basetemp; se creó ese directorio y se repitió.
- Claude Code 2.1.293 con inferencia externa: **recorrido satisfactorio**, 33,53 s, exit 0, dos preguntas requeridas (text y single_choice), dos respuestas humanas HTTP y dos agent_acknowledged. Cap por ejecución de 1 USD; coste reportado no disponible. Evidencia sanitizada en `/home/felni/.cache/cao-live-provider/sanitized-evidence.jsonl`, modo 0600.
- Codex 0.162.1 con inferencia externa: **aceptación fallida**, 30,4 s, exit 0, cero preguntas y cero llamadas a herramientas. No se repitió a ciegas. El probe stdio separado sin inferencia sí inicializa, lista las siete herramientas de interacción dentro del catálogo global de 79 y accede a metadata con la capacidad privada. La configuración generada permite exactamente esas siete. Esto no demuestra que el modelo externo utilice ese canal. El harness usa builders reales pero adapta la CLI a batch y omite un perfil interaction-safe inexistente; no acredita un recorrido entero create_terminal/tmux/browser. Auth Unsupported en mcp list es normal para stdio.
- Playwright completo, tres specs: **20 passed, 0 failed, 0 skipped**. Repetición independiente de raíz: **20 passed, 4,1 min**, `/tmp/caos-spec016-root-playwright.log`. Cinco formularios contra API/SQLite y proceso MCP real, replay/conflicto, pérdida de respuesta y recuperación, privacidad por nodo, terminal nativo con incarnación exacta, credenciales explícitas y recarga. SC001 p95 = **1502 ms** en la repetición independiente (1909 ms en la anterior) con 20 fuentes/100 preguntas de fixture; no equivale a 20 procesos CLI reales ni a una garantía de carga externa.
- Web Vitest: **45 archivos / 504 tests passed**, 200,18 s, cuatro workers; repetición independiente **504 passed, 234,68 s**, `/tmp/caos-spec016-root-web-vitest.log`. Build TypeScript/Vite independiente satisfactorio, 28,97 s, `/tmp/caos-spec016-root-web-build.log`. Quedan avisos JSDOM/React y tamaño de bundle, sin fallos en esos comandos. Estado Playwright leído por raíz: passed, failedTests vacío. No quedan procesos de su fixture/MCP/tmux.
- Consumidores cao_mcp_apps: typecheck, **12 archivos / 92 tests passed**, cobertura de líneas **90,01%**, cuatro builds, escaneo JIT y presupuestos de tamaño satisfactorios; **7/7 Playwright passed**. El primer intento no arrancó por ausencia del navegador; se instaló en /tmp y se repitió. Ratchet de cobertura backend pendiente: no se reutiliza un coverage.json ausente u obsoleto. Validación de links Markdown y checks de esquema/paquetes Agent Plugins pasan.
- La revisión independiente de observer encontró que el binding posterior de propietario de assignments exigía una capacidad explícita habilitada. El alta sucede antes del estado submitted; ese segundo binding debe permitir la fuente observacional vigente manteniendo sus validaciones de assignment/Work/identidad. Se añade una regresión de la secuencia real y su corrección acotada.

## Resultados finales

La producción y los tests están congelados. **Suite Python total con perfiles separados terminada satisfactoriamente**; el resto de los resultados siguientes corresponde a la última versión. Los logs detallados y XML se guardan fuera del repo; la evidencia durable sólo contiene resultados y diagnósticos sin credenciales.

| Comprobación | Resultado de la última versión |
| --- | --- |
| Python completo, integraciones incluidas, `-m 'not e2e'` | **16949 passed / 0 failed / 0 errors / 119 skipped**, 95 avisos, 2621,15 s, exit 0; `/tmp/caos-spec016-isolated-backend.{log,xml}` |
| Identidad de la copia | 1231 archivos Python idénticos por SHA-256; import desde la copia Linux; Python 3.12.13 |
| Black / isort completos | Exit 0; Black 1231 archivos sin cambios |
| Mypy completo | Exit 0; 405 archivos sin errores |
| Arquitectura / composición | Exit 0; 408 archivos / 1713 dependencias / 5 contratos conservados / 0 rotos |
| Web Vitest | 45 archivos / 504 passed; 382,98 s; dos workers |
| Web build | Exit 0; 25,97 s; avisos de tamaño conservados |
| Web Playwright completo | 20 passed / 0 failed / 0 skipped; 3,4 min; p95 1042 ms para 20 fuentes / 100 preguntas de fixture |
| Apps Vitest + cobertura | 12 archivos / 92 passed; 99,19 s; líneas 90,01% (478/531) |
| Apps typecheck / cuatro builds / JIT / tamaño | Todos exit 0; gzip 49,4 / 49,4 / 48,5 / 86,6 KB dentro de límites |
| Apps Playwright completo | 7 passed; 15,5 s; puerto privado 29616 |
| Assets usados por backend | Nueve assets web/apps idénticos por hash tras los builds finales |
| Hermes CLI/PTy real, modelo simulado loopback | 1 passed; 21,21 s; no acredita canal explícito Hermes ni inferencia externa |
| Links Markdown / schemas / paquetes Agent Plugins | Todos exit 0 |
| Ratchet Python 87,0% / Apps 90,0% | **PASS**, exit 0: Python **87,0429%** (67003/76977) frente a 87,0%; Apps **90,01%** frente a 90,0%; informes completos verificados presentes |

Los comandos finales producen logs con prefijos `/tmp/caos-spec016-verified-`, `/tmp/caos-spec016-isolated-backend` y `/tmp/caos-spec016-completed-`. La suite dirigida del runtime previa pasa 370 tests; el archivo independiente de identidad/TTL pasa cuatro. La suite total final los incluye y termina satisfactoriamente.

## Diagnóstico de la primera ejecución total

- 177 fallos y 6 errores de vault: el runner colocó `--basetemp` fuera de `tempfile.gettempdir()` sin establecer TMPDIR; los guards de aislamiento rechazaron correctamente esa ubicación. La repetición usa TMPDIR corto en ext4 y basetemp descendiente.
- Sockets Unix: la ruta extensa del runner excedía el límite del sistema. Trece casos dirigidos de procesos/tmux/seguridad pasan con temporales cortos, sin cambiar producción ni relajar guards.
- Sesiones: se reproduce el fallo de tablas ausentes también en HEAD limpio, preinicializando RemoteBase sobre el engine global. El fixture aislaba SessionLocal pero dejaba database.engine en otro fichero. Se unifican ambos en el mismo engine de test. El singleton de interacciones se reinicia por test cuando cambia DATABASE_FILE.
- Fixtures WebSocket: cuatro MagicMock carecían de query_params concretos y entraban accidentalmente en el guard de incarnación. Se definen parámetros vacíos, manteniendo las pruebas de incarnación correcta y obsoleta: 88 + 4 passed.
- Monitor: dos pruebas inyectaban la antigua interfaz `_detect_screen` en lugar de la nueva instantánea con viewport. Reproducidas antes de actualizar el seam; 2 passed, mismas condiciones de carrera.
- HTTP de interacciones: doubles sin tipo no satisfacían el resolver estricto del servicio. Se usa Mock(spec=InteractionService), sin debilitar producción; 20 passed, incluyendo rechazo de objeto no tipado y fallback del singleton. Autospec MagicMock produjo un falso coroutinefunction durante una iteración intermedia; se descartó ese double.
- Aserciones de envío diferido/recibos y creación de sesión se revisan contra los contratos de same_turn y principal de creación. La auditoría de scopes se amplía a los guards reales de interacciones y añade denegaciones ejecutables; ninguna ruta se añade a exclusiones.

La repetición final se ejecutará sobre una copia congelada contrastada por SHA-256. Los fallos originales y su diagnóstico permanecen registrados; corregir fixtures no convierte la primera ejecución en un resultado satisfactorio.

### Congelación y comprobaciones posteriores

- Copia final Linux: **1227 archivos Python SHA-256 idénticos**; manifest `/tmp/caos-spec016-final-validation-manifest.json`. Hash del harness OpenCode confirmado también tras su formato.
- Segunda repetición total, posteriormente interrumpida: Python 3.12, 8 workers, `-m 'not e2e'`, incluidos `test/` y `examples/workflow/tests/`, TMPDIR `/home/felni/.cache/c16t`, basetemp descendiente `p`, CAO_HOME_DIR privado. Log `/tmp/caos-spec016-final-backend.log`, XML `/tmp/caos-spec016-final-backend.xml`. Su cobertura parcial no sirve para el gate final.
- Scope coverage: **64 passed** tras scanner exacto de 18 handlers, JWT firmados y denegación entre dos fuentes reales de SQLite. Sin nuevas exclusiones.
- Dos archivos de entrega diferida/recibos: rojo **5 failed / 54 passed**, verde **59 passed**, sólo aserciones de contrato actualizadas.
- Aislamiento de sesión/incarnación/seguridad/Work: **75 passed, 3 deselected**. Conftest fija sólo la coherencia del engine y del singleton entre tests.
- OpenCode catalog-only real: **79 herramientas, cero diagnósticos, cero peticiones al modelo**. El intento de recorrido con modelo loopback no anunció herramientas de interacción al modelo y terminó sin preguntas; **no cuenta como aceptación**. Configuración de producción sin cambios por ese intento. El harness queda congelado; tres directorios temporales inactivos permanecen porque la herramienta rechazó su limpieza, sin reintento forzado.
- Codex E2E legacy contrastado con HEAD limpio: el caso de asignación también falla esperando idle/completed (**1 failed, 147,80 s**) mientras muestra inicio de sesión. Acredita la precondición ausente del entorno para ese caso concreto; no acredita aceptación del canal explícito.

- API general y constantes: **127 + 75 passed** tras comprobar el principal de creación y el valor default de CAO_HOME_DIR en un subprocess con Path.home privado. No se reasignan HOME/CODEX_HOME. Un primer intento del subagente se interrumpió al detectar CAO_HOME_DIR ausente; sus imports pudieron inicializar constantes antes de esa detección, por lo que no se afirma ausencia absoluta de efectos de ese intento. Las ejecuciones completas posteriores usan CAO_HOME_DIR privado.
- Fence legacy más sesiones, con marcador de integración explícito: **49 passed**, 155,14 s. Una pregunta requerida no puede resolverse por answer_user_prompt legacy.
- Apps, repetición independiente: **92 passed**, 80,16 s; typecheck y cuatro builds pasan; JIT pasa; gzip 49,4 / 49,4 / 48,5 / 86,6 KB dentro de presupuestos. Líneas 90,01% (478/531).
- Apps navegador independiente: primer intento falla antes de ejecutar tests por puerto 9889 ocupado. No se termina ningún proceso ajeno. Repetición con E2E_PORT=29616: **7 passed**, 23,5 s, log `/tmp/caos-spec016-root-apps-playwright-retry.log`.
- Black/isort completos sobre la copia congelada: exit 0, **1227 archivos sin cambios** para Black. Mypy completo actual: exit 0, **405 archivos**. Graphify final: **449 archivos extraídos / 12411 nodos / 33156 aristas**; hashes de cinco fuentes críticas idénticos, relaciones contrastadas con source. Composition final: **408 archivos / 1712 dependencias / 5 contratos conservados / 0 rotos**, exit 0. Los nueve assets web/apps coinciden por hash con la copia de pruebas.

## Revisión independiente posterior a la congelación

La segunda ejecución total se interrumpe explícitamente al encontrar un defecto de comportamiento en la revisión de source: una entrega genérica o tecla podía continuar cuando el monitor pasaba transitoriamente a IDLE, aunque persistiera un aviso nativo manual sin evidencia exacta de cierre. El gate consultaba sólo preguntas explícitas requeridas y el status momentáneo; avanzar el turno volvía stale el aviso antes del efecto. Se desarrolla una regresión con ledger real y un guard durable que preserve la vía manual WebSocket autorizada. **Esta ejecución parcial no certifica el fix**.

Resultado parcial conservado: **4392 passed, 1 failed, 9 skipped**, 452,59 s, exit **2 / KeyboardInterrupt**. El fallo distinto de monitor procede de una regresión introducida en nuestro primer ajuste de fixtures: un helper compartido pasó a devolver tuple también a caminos que esperaban TerminalStatus escalar. Se restaura el helper escalar y se empaqueta el snapshot sólo en su callback específico. Dos clases afectadas: **6 passed**; el archivo completo se repitió después: **154 passed**. No se atribuye este fallo a producción ni al baseline.

Nuevo archivo de regresión de same_turn ejecutado separadamente contra la misma producción: **3 passed** (generación durable conservada/avanzada y pregunta requerida bloqueada), `test/integration/test_interaction_same_turn.py`. Se añadió después de congelar la segunda suite y no se declara incluido en ella. Se incorporará a la siguiente copia total junto con el fix nativo.

El archivo entero del monitor, después de restaurar el helper compartido, pasa: **154 passed**, 18,20 s. La corrección nativa cubre también varios frames WebSocket con viewport exacto inalterado: sólo una cadena contigua de entradas manuales autorizadas, sin observación nueva ni tarea/tecla automática intermedia, puede conservar la espera del aviso original. Un viewport distinto no se considera automáticamente el mismo diálogo; queda sujeto a reconciliación manual. Expirar o cancelar la espera no demuestra cierre físico ni habilita una nueva tarea.

La primera corrección pasa **62 pruebas dirigidas**, incluidas las **9 regresiones independientes** de `test_interaction_native_guard_contracts.py`. Esa evidencia no cierra la ronda: una reproducción independiente adicional encuentra que un monitor reiniciado pierde el pin en memoria; si el diálogo ya ha cerrado, dos capturas estables no liberan el bloqueo durable. Se desarrolla recuperación con target original persistido, identidad vigente y cierre separado de la reanudación. Los avisos históricos sin prueba del target no pueden recuperarse por inferencia. La siguiente suite completa debe incluir esta corrección y nuevas pruebas de reinicio.

La recuperación pasa **12 regresiones independientes**: cierre tras reinicio con dos capturas cambiadas estables, waiter anterior stale, cero respuestas/entregas/returned/ack, target diferente y binding histórico ausente denegados. La suite dirigida ampliada pasa **296 tests**. Una revisión adicional reproduce la reaparición del mismo diálogo tras cierre en la misma generación; cada nueva aparición con timestamp nuevo obtiene una identidad nueva, mientras un reintento del mismo evento conserva idempotencia.

La siguiente tentativa total se interrumpe a los **712 passed**, 122,77 s, exit 2: Mypy detecta **7 errores en 2 archivos** de la última corrección (casts de SQLite y narrowing de pin opcional), y ejecutar ocho workers junto con navegador/Hermes agota la memoria del runner. El navegador termina con **1 passed / 1 failed / 1 interrupted / 17 no ejecutados**, 1,7 min; sus fixtures fallan con `OSError: Cannot allocate memory`. Hermes también falla por ese error de entorno. Los logs `/tmp/caos-spec016-completed-*` conservan el intento; no son resultados finales aprobados. Black/isort y composición sí terminan con exit 0 sobre esa versión, pero deben repetirse después del último cambio. Se archivan caches propios inactivos en ext4 conservando sus rutas mediante enlaces; la nueva ejecución reducirá la concurrencia.

Los tipos se corrigen sin modificar comportamiento. Antes de reiniciar la suite, la auditoría encuentra otro caso de liveness: el dedupe de observación podía seleccionar un aviso F1 caducado/cancelado aunque F2 fuera más reciente; al reaparecer F1, el monitor obtenía una referencia antigua cuyo cierre se rechazaba por F2. Se limita la reutilización a la observación vigente y se añaden regresiones antes de congelar de nuevo.

Otra reproducción con SQLite y un waiter asíncrono vivo confirma un defecto de identidad: F1 inicia una espera en generación 1; se cancela F1; aparece F2 en esa generación; una entrada WebSocket avanza a generación 2 y su cierre exacto reconcilia F2. El waiter original devolvía `released` con la observación de F2 porque cada poll seleccionaba la última pregunta de la generación, sin retener la identidad inicial. Se exige pinning de la observación en el servicio y entre llamadas de polling del ejecutor. La cancelación/caducidad del aviso original no puede liberar la tarea por el resultado de otro aviso. Esta corrección y sus regresiones también deben incluirse en la suite total siguiente.

## Colisión de perfiles del runner y repetición final

La ejecución completa sobre la última versión terminó con **16948 passed, 1 failed, 119 skipped**, 96 avisos, 2578,18 s, exit 1. XML: 17068 casos, un fallo y cero errores. El único fallo fue `test_agui_surface_defaults_off`: los cuatro procesos xdist usaban el mismo CAO_HOME_DIR y el mismo ID persistido de perfil. El registro de peers rechazó correctamente dos servidores simultáneos con ese ID y distintas generaciones de proceso. El test y el guard de producción no se modifican.

- Reproducción con dos workers y perfil compartido: **1 failed / 5 passed**, 62,25 s; mismo ProfileAlreadyRunningError (`/tmp/c16-shared-profile-repro.log`).
- Prueba con perfiles separados y tests de startup: **4 passed**, 66,12 s, exit 0 (`/tmp/c16-worker-profile-green.log`).
- Plugin externo, sin cambios del repo: `/home/felni/.cache/c16t/worker_profile_plugin.py`, SHA-256 `8c42db3ab73215f74ba1764ef8a1c875076dcf615a0f5eed90191fcdd10f7e6f`. Configura CAO_HOME_DIR por worker antes de importar la aplicación y dirige su registro de peers a un directorio temporal privado. No cambia HOME/CODEX_HOME ni la protección de un servidor por perfil.
- Repetición completa: Python 3.12.13, cuatro workers, `--dist=loadfile`, `-m 'not e2e'`, `test/` y `examples/workflow/tests/`, basetemp `p5` descendiente de TMPDIR corto, plugin externo y perfiles privados. Producción/tests siguen siendo los mismos 1231 archivos congelados. Log/XML/cobertura: `/tmp/caos-spec016-isolated-backend*`.
- La cobertura de la ejecución terminada con el fallo del runner es un informe completo: 67025/76977 statements, **87,071%**. No sustituye la necesidad de terminar satisfactoriamente la nueva suite.

Inventario de los 119 skips de esa ejecución: 56 por Bubblewrap scratch/pidfds; 8 por aceptación de host Linux aprobada; 17 por Docker/imagen/opt-in; 14 por proveedores, workflow o autenticación opt-in (Hermes se ejecuta aparte); 10 por gitleaks; 6 por shells fish/zsh; 2 por filesystem case-insensitive; 4 por Landlock ABI, libtmux antiguo, listado OpenCode y cargo; 2 por creación de nodos de dispositivo. Deben contrastarse otra vez con el XML final. Ninguno se contabiliza como prueba pasada.

Verificación independiente del plugin por raíz: los tres tests del archivo default-off se ejecutan en cada uno de dos workers: **6 passed**, 5,82 s, exit 0; `/tmp/caos-spec016-profile-isolation-probe.log`. No se omite el caso AG-UI que falló en la suite total.

### Reproducción del lanzador aislado

En una copia Linux contrastada con el workspace, añadir al PYTHONPATH un directorio privado que contenga `worker_profile_plugin.py` con este contenido. El plugin se ejecuta antes de los conftest; pytest-cov inicia la cobertura con su hook tryfirst. El registro temporal evita escribir en el registro del usuario. Los tests que prueban coordinación entre perfiles mantienen sus propios fixtures.

```python
import os
from pathlib import Path

def pytest_load_initial_conftests(early_config, parser, args):
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return
    profile = Path(os.environ["C16_PROFILE_BASE"]) / worker
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["CAO_HOME_DIR"] = str(profile)
    from cli_agent_orchestrator import constants
    constants.LOCAL_PEER_DIR = profile / "local-peers"
    constants.LOCAL_PEER_REGISTRY_FILE = constants.LOCAL_PEER_DIR / "registry.sqlite3"
```

Comando de la última ejecución, desde `/tmp/caos-spec016-validation`:

```bash
TMPDIR=/home/felni/.cache/c16t \
CAO_HOME_DIR=/home/felni/.cache/c16t/isolated-cao/controller \
C16_PROFILE_BASE=/home/felni/.cache/c16t/isolated-cao \
C16_PROFILE_MODE=worker \
PYTHONPATH=/home/felni/.cache/c16t:/tmp/caos-spec016-validation/src:/tmp/caos-spec016-validation \
/home/felni/.cache/caos-spec016-validation-venv/bin/python -m pytest \
  -p worker_profile_plugin test examples/workflow/tests -m 'not e2e' \
  -n 4 --dist=loadfile --basetemp=/home/felni/.cache/c16t/p5 \
  --cov=src/cli_agent_orchestrator \
  --cov-report=xml:/tmp/caos-spec016-isolated-backend-coverage.xml \
  --cov-report=json:/tmp/caos-spec016-isolated-backend-coverage.json \
  --cov-report=term --junitxml=/tmp/caos-spec016-isolated-backend.xml \
  -ra -q -o faulthandler_timeout=120 \
  > /tmp/caos-spec016-isolated-backend.log 2>&1
```

Para otra ejecución elegir una base privada nueva y un basetemp corto nuevo descendiente de TMPDIR. No reutilizar un perfil de operador.

## Cierre de la ronda solicitada

- Suite total final: **16949 passed / 119 skipped**, cero fallos/errores, 95 avisos, 2621,15 s, exit 0. XML verificado: 17068 casos. El caso AG-UI default-off que falló con el perfil compartido está incluido y pasa; no hay exclusión nueva.
- Inventario final de skips: 119; mismas causas del inventario anterior, conservado fuera del repo en `/tmp/caos-spec016-isolated-skip-inventory.json`. Hermes nativo opt-in se ejecutó aparte y pasa; los otros límites de host y proveedores siguen abiertos.
- Gate de cobertura ejecutado con ambos informes presentes, numéricos y frescos: **Python 87,0429% / Apps 90,01%, PASS**, exit 0; `/tmp/caos-spec016-completed-coverage-ratchet.log`. Baseline 87,0%/90,0% conservado.
- Composición ejecutada después de la suite completa: **408 archivos / 1713 dependencias / 5 contratos conservados / 0 rotos**, exit 0; `/tmp/caos-spec016-completed-composition.log`.
- Revisión de composición y diff: fuente vigente, tests/fixtures y nuevos módulos inspeccionados; invariantes de identidad, autoridad, TTL, reintento y recuperación contrastados con los escenarios completos. Dictamen: **PASS con límites de aceptación abiertos**, detallados en [composition-review.md](composition-review.md).
- Graphify: no hubo modificación estructural posterior a la última extracción aislada; se conserva esa extracción contrastada con seis archivos críticos y la identidad de los 1231 archivos Python. No se regenera ni sobrescribe el grafo compartido.
- Conocimiento durable: los contratos y decisiones quedan en los artefactos de spec 016. Se decide no duplicar en Obsidian los informes, logs, resultados temporales ni hechos generados por Graphify.
- No se declara aceptación íntegra de SC-007, overlay explícito Hermes ni adaptador de recibos AG-UI. Docker/host aprobado/matriz completa de versiones y OS requieren sus respectivos runners. Las suites legacy externas interrumpidas/fallidas conservan su diagnóstico; no se convierten en resultados satisfactorios.
