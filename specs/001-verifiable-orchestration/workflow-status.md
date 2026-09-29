# Estado de AI_WORKFLOW.md

Fecha: 2026-09-24. Estado del programa: **diseño aprobado, implementación en curso**.
Este archivo no es un signoff de la funcionalidad solicitada.

Último incremento cerrado: **T070** (restore aislado de bundle v2; evidencia al
final). Antes se cerraron **T061** (presentación semántica de Work en Web y TUI),
**T067** (cliente remoto CAS y recovery
versionados) y **T064/T068** (contrato HTTP y cursor de
la autoridad), **T058** (preflight de fábrica por descriptor), **T059**
(observación Herdr simulada), **T056** (contrato DTO de proyección HTTP v1),
**T018** (bridge inbox managed interno),
**T043** (adaptador snapshot-ID en aislamiento),
**T091** (entrega durable interna), **T006** (DTO de dominio v1) y
**T038/T040/T092** (política común de memoria).
Tras la detención solicitada después de T040, se reanudó T006 y luego T091.
**T044 sigue pendiente**;
las entradas públicas T017–T020 y el resto del roadmap siguen pendientes.
Los cierres de T006 y T091 están al final del documento; el cierre anterior
de memoria también se detalla en [t040-composition-review.md](t040-composition-review.md).

## Alcance y decisiones de proceso

- Petición: aplicar AI_WORKFLOW.md a todos los pendientes del roadmap.
- Clasificación: programa significativo por estado, persistencia, autoridad, mensajes,
  backends, memoria, clientes y recuperación. La preparación original no modificó aplicación;
  desde la aprobación se implementan incrementos en este mismo workspace.
- Cobertura: 19 ejes + 6 operativos = 25 requisitos funcionales, 9 criterios de éxito,
  7 historias y 92 tareas, incluidas T091 y T092 descubiertas durante la integración. El estado actualizado por tarea se mantiene en tasks.md;
  las comprobaciones de preparación siguientes son evidencia histórica, no cierre de producto.
- Workspace inicial: main con numerosos cambios previos en servidor, proveedores, clientes,
  tests y documentos; .ai, .specify, graphify-out y tests ya estaban sin seguimiento.
- Aislamiento de preparación: sólo archivos nuevos de planificación y cambios acotados de configuración
  Spec Kit; no se creó rama ni worktree que omitiera el estado local utilizado en el análisis.
  La implementación conserva ese estado y delimita ownership por archivos; no parte sólo de HEAD.
- Conocimiento: decisiones propuestas permanecen en Spec Kit. No se guardó contenido
  no revisado en Obsidian ni se duplicó el grafo.
- Constitución: se completó plantilla como 1.0.0 con reglas existentes de AGENTS.md y
  AI_WORKFLOW.md. No se alteraron templates ni instrucciones superiores.
- Extensiones: .specify/extensions.yml ausente; sin hooks antes/después de comandos.

## Secuencia antes de implementación

| Paso | Estado | Evidencia y límite |
|---|---|---|
| git status / clasificación | realizado | cambios previos reconocidos y preservados |
| knowledge-decision | realizado | decisión anterior: sin persistencia de diseño no revisado en vault |
| speckit-specify | realizado | spec.md y checklists/requirements.md, 25 FR |
| speckit-plan | realizado | setup-plan.sh, plan.md, research.md, data-model.md, contracts/, quickstart.md |
| speckit-tasks | realizado | setup-tasks.sh, tasks.md, T001–T090 |
| speckit-analyze | realizado sobre documentos | cobertura 25 FR/9 SC, tareas únicas y secuenciales, sin requisitos huérfanos |
| graphify-impact | realizado como mapa inicial | consulta NativeChildModel/TerminalTurnReceiptModel, 56 nodos; salida extensa parcialmente truncada, contrastada con código |
| source-inspection | realizado para diseño | evidence de research.md; admisión, recibos, lease, join, snapshot, permisos, cleanup y writers examinados |
| tdd-decision | definido | RED/GREEN por comportamiento y frontera, sin generar tests ficticios de capacidades aún no construidas |
| implementación | en curso | usuario aprobó el diseño mediante «apruebo»; se conserva el workspace con cambios previos |

Las consultas de investigación previas al plan son contexto de diseño; no se presentan
como evidencia de cierre de una implementación. Cada historia repetirá el flujo sobre su diff.

## Evidencia ejecutada en esta preparación

### Resolución Spec Kit

- resolve-template.sh constitution-template --json: exit 0.
- resolve-template.sh spec-template --json: exit 0.
- setup-plan.sh --json: exit 0; plantilla creada en esta feature.
- setup-tasks.sh --json: exit 0; plantilla y artefactos resueltos.
- check-prerequisites.sh --json --require-tasks --include-tasks: exit 0; feature correcta.
- Validador de artefactos: 25 FR, 9 SC, 90 IDs únicos consecutivos y rutas por tarea;
  ninguna referencia FR/SC sin fila de cobertura ni marcador de plantilla restante.
- Tareas por historia: US1 13, US2 13, US3 10, US4 9, US5 8, US6 9, US7 13;
  otras 15 corresponden a setup, foundation y cierre transversal.

### Baseline de pruebas

Comando:

```bash
.venv/bin/python -m pytest -o addopts= -q \
  test/clients/test_workflow_run_migration.py \
  test/services/test_frozen_run_memory.py \
  test/services/test_turn_receipt_delivery.py \
  test/test_real_provider_matrix_contract.py
```

Resultado fresco: **85 passed in 8.75s**, exit 0. Verifica piezas actuales;
no acredita los work items, grants o protocolos nuevos propuestos.

La ejecución de manifest del análisis previo falló por baseline no disponible. Diagnóstico
previo: presupuesto agregado de archivos no versionados de 64 MiB agotado en graphify-out.
No se volvió a ejecutar esa suite en esta preparación, no se reparó y queda en T045.

### Gate de composición

Primer comando exacto de AI_WORKFLOW.md falló: faltaba .ai/project-name (exit 1).
Se verificó el registro del launcher: caos apunta a este checkout. Se añadió únicamente
.ai/project-name con ese nombre; no se cambió el registro global del operador.

Segundo comando:

```bash
project-composition-check "$(cat .ai/project-name)"
```

Resultado: **PASS**, exit 0. Import Linter analizó **7 archivos, 8 dependencias**,
un contrato conservado y cero rotos. Alcance: cao_workflow, según .importlinter.
No corrió pruebas de integración del orquestador ni verificó web/TUI. T003 amplía
el gate a fronteras reales; no se usa este PASS como prueba del roadmap completo.
El launcher reconstruyó/reinstaló el paquete editable del checkout con uv; no se
solicitó actualización de dependencias ni se modificaron fuentes por ese paso.

### Diff y whitespace

- git diff --check global: exit 2 por whitespace/CRLF de cambios tracked preexistentes,
  empezando por .github/workflows/real-provider-e2e.yml. No se normalizó trabajo ajeno.
- Archivos propios de planificación/configuración: sin trailing whitespace y con newline final.
- No se editaron src/, test/, web/, tui/ ni el roadmap preexistente durante preparación.
- No se realizaron commits, pushes, merges, releases, consumo de modelos reales ni publicaciones.

## Revisión de composición de la preparación

- Subsistemas cambiados: resolución de feature Spec Kit, constitución y nombre local de gate.
- Vecinos examinados: herramientas de template/setup, launcher, Import Linter, modelos y
  writers actuales de lifecycle, gateway/memoria, backends, autoridad y clientes.
- Invariantes revisados: compatibilidad de recibos, ausencia de doble autoridad, orden
  persistir/enviar, estado+evento atómico, grants no ampliables y limpieza con evidencia.
- Arquitectura ejecutada: gate configurado, con alcance auxiliar limitado descrito arriba.
- Escenarios ejecutados: 85 tests actuales, con migraciones SQLite temporales y pruebas
  unitarias/contratos del arnés; no se usaron proveedores reales ni se validó multinodo nuevo.
- Contratos de esta feature: revisados como diseño; aún no ejecutables contra producto.
- Fallos/causas: faltaba nombre de proyecto (resuelto); gate parcial, CRLF ajeno y baseline
  de manifest permanecen explícitos. No se introdujo ninguna corrección de aplicación.
- Riesgos residuales: la ejecución de grants fuertes, continuidad, scheduler, multinodo,
  restore y proyección universal todavía no existe como garantía validada.
- Veredicto de preparación: **PASS WITH RISKS**. Veredicto del roadmap: **NO COMPLETADO**.

## Implementación: setup

- T003: baseline Import Linter: 7 archivos, 8 dependencias, 1 contrato conservado.
  Ampliado a ambos paquetes y a la independencia de modelos respecto de servicios,
  clientes, proveedores, backends y adaptadores. Contrastado con imports reales.
  `.venv/bin/lint-imports --no-cache --no-logo`: exit 0, 142 archivos,
  370 dependencias, 2 contratos conservados, 0 rotos.
- T004: fixtures de rutas temporales y contrato JSON v1 creadas; JSON validado.
  No se usa DB ni credenciales del operador. La fixture running/ready evita equiparar
  un turno listo con una operación terminada.

## Implementación: núcleo y entregas independientes

- Diseño aprobado por el usuario. Se aplican Spec Kit, TDD y revisión de composición.
  T080/T081/T075 se delegaron como tareas independientes autorizadas por el plan;
  núcleo de DB y servicio se desarrolla sin competir por archivos compartidos.
- Corrección del alcance de T003: el primer gate ampliado omitía directorios namespace
  `services` y `clients` sin `__init__.py`. Se reprodujo con `grimp.build_graph` y
  se añadieron ambos como raíces explícitas, sin cambiar packaging. Verificación
  `.venv/bin/lint-imports --no-cache --no-logo`: **225 archivos, 810 dependencias,
  2 contratos conservados**, exit 0. El PASS anterior de 142/143 archivos era parcial.
- Reducer: RED de comportamiento con implementación provisional, seguido de GREEN.
  Tras revisión se añadió cancelación de intento, fallo conocido antes de dispatch,
  lease con zona horaria, hash estricto y fase derivada del estado autoritativo.
  `.venv/bin/python -m pytest -o addopts= -q test/services/test_work_reducer.py`:
  **53 passed in 33.05s**. Sin API/backends en modelos.
- Migración: nueve RED por repositorio ausente; nueve GREEN con esquema aditivo.
  Hook `init_db` tuvo su propio RED antes de conectarse. Regresiones de migración,
  permisos y workflow: **26 passed in 10.30s**. Legacy permanece intacto; esquema
  parcial/futuro/alterado falla cerrado. `docs/work-recovery.md` explica límites.
- Identidad/CAS/eventos: pruebas SQLite real, 16 admisiones concurrentes con una sola
  identidad, fallo de trigger que revierte estado+evento+secuencia, generación y lease,
  eventos deduplicados incluso después de retención. Repositorio+migración:
  **25 passed in 19.27s** antes de extender resultados y renovación.
- Envío: seis RED por dueño durable ausente; servicio nuevo confirma intención antes
  de invocar adaptador, sin lock DB durante I/O. Timeout pasa a conciliación; caída
  tras intención y repetición tras reinicio no reenvían. Servicio+repositorio:
  **21 passed in 32.99s**, antes de extender settlement.
- Revisión independiente encontró fase DTO contradictoria (corregida y probada) y
  referencia de resultado aceptado ausente (corregida con CAS y pruebas). Resultado
  validado se registra antes de éxito; aceptación fija referencia y comprueba hijos
  realmente pendientes. Evidencia tardía se conserva sin reemplazar ganador.
  Repositorio+migración+dispatch: **35 passed in 31.86s**, antes de añadir renovación.
- Artifact store, settlement del servicio y renovación/historial: en verificación.
  No se presentan estas piezas internas como integración de las cinco entradas.
- T075/T080/T081: inventarios basados en fuente y revisados; sin cambios remotos.
  #568 ya tiene implementación, pero build/scan/alerta actual no están demostrados.
  Upstream local: 9 commits propios/5 upstream, 28 archivos dirty solapados.
- T076: registro v1 con 35 opciones y 61 descriptores externos; validación antes de
  escritura, resolución mediante lectores efectivos y cache sensible a entorno/ruta.
  Revisión independiente sin hallazgos importantes. Suite de config/settings/CLI/backend/
  Auth0/learning/lint/MCP/event bus repetida por controlador en **.venv**:
  **235 passed in 36.24s**, exit 0. La prueba previa del autor usó otro venv con el
  mismo intérprete/checkout pero diferencias de paquetes; no se considera equivalente.
  Workers ya existentes no reciben cambios retroactivos de entorno; esto queda explícito.
- Extensión de núcleo: repositorio+migración+reducer **85 passed in 27.02s**;
  settlement **10 passed in 30.16s**; join/cleanup/repositorio **35 passed in 45.93s**.
  Renew exige lease vigente y generación actual; retry preserva intento anterior y
  necesita conciliación autorizada y cese confirmado. Join muestra hijo expirado como
  reconcile, nunca success; cleanup pendiente/fallido no cambia el resultado ganador.
- Revisión de composición de resultados: sin inversión de locks ni DB lock durante
  envío/validación; resultado y referencia se confirman antes de aceptación CAS.
  Detectó temporales `.publish-*` no recolectables después de muerte de proceso.
  Corrección con RED de proceso real y propiedad/permisos: store nuevo+legacy
  **28 passed in 37.31s**, pendiente relectura final del controlador.

### Ajuste de secuencia sin ampliar alcance

### Evidencia posterior: resultados, autoridad y deuda operativa

- Núcleo/artifact store/compatibilidad: ejecución conjunta en `.venv`,
  **142 passed in 46.97s**, exit 0. Incluye reducer, migraciones, repositorio,
  WorkService, store inmutable, store legacy, permisos DB y migración workflow.
- Migración v2 de autoridad: RED **3 failed, 10 passed**; después GREEN
  **48 passed in 24.86s** (migración, repositorio y servicio). Se preserva el
  checksum v1 `6cb5f0d4a40282a56affc3cea4e5f6b652c46b060e7c5858eb144fea68ef3821`.
  Un fallo real de autorización DDL SQLite revierte todos los objetos nuevos;
  ledger corrupto/futuro no se actualiza. No hay downgrade destructivo.
- T028: principal procede de claims JWT verificados o identidad local del servidor,
  no de caller_id. Grants y revocaciones son snapshots inmutables; cada comprobación
  recorre ancestros, vencimiento, revocación, revisión, job y allowlist. Permisos
  vacíos no elevan autoridad. **64 passed in 37.66s** en `.venv`: tests de
  autoridad, principal y autenticación legacy. Nivel declarado: cooperativo,
  no sandbox. T024/T029/T030/T035/T036 siguen pendientes de integración y pruebas
  de revocación durante efectos reales de admisión/dispatch.
- T073: revisión independiente sin hallazgos high/medium; **117 passed in 14.28s**
  en `.venv`, con tres avisos de deprecación existentes. El lock coordina escritores
  participantes; no controla editores externos ni hace atómicos filesystem+índice
  ante crash. Lectura reconstruye índice desde el archivo canónico.
- T077/T078: el writer script persiste `error_kind` en columna existente. Revisión
  detectó tipo anterior visible tras retry: RED **3 failed, 11 passed**; corrección
  limpia proyección mutable al comenzar retry y limita tipo de run a fallo/cancelación,
  sin borrar evidencia de steps fallidos. Suites T073/T077/journal combinadas en
  `.venv`: **161 passed in 24.90s**, tres avisos; ocho tests adicionales del lector pasan.
  Una suite más amplia previa tuvo **195 passed, 16 failed**: catorce fixtures
  `_send` rechazaban `task_delivery` ya enviado por código previo, y dos pruebas
  expiraban durante freeze de manifest antes de settlement. No se ocultan esos fallos.
- T045: se reprodujo manifest sin baseline en checkout real. En esta ejecución la
  causa demostrada fue timeout de `git diff` a los diez segundos; la observación
  anterior de presupuesto de untracked no se confunde con esta causa. Fixture Git
  temporal aislada, sin cambiar producción: **31 passed** manifest/baseline y
  **72 passed** manifest/identidad/aprobación. Un archivo temporal de 64 MiB + 1
  demuestra por separado el rechazo por presupuesto, sin ampliar sus límites.
- T016 usa tests nuevos en `test_work_result_store.py` para separar almacén durable
  de las regresiones del store transitorio. El cambio de ubicación no reduce cobertura.

### Secuencia activa

Autoridad y reservas se preparan antes de conectar terminales/API a la nueva ejecución.
Razón: conectar primero con evidencia afirmada por el caller abriría una frontera
temporalmente insegura. T024/T028 se desarrollan sobre el núcleo comprobado; las
integraciones T017–T023 esperan sus contratos. No se activan rutas permisivas intermedias.
T073 y T077 avanzan como entregas operativas independientes, con edits de api/main.py
serializados entre sus responsables. No cambia la autorización para proveedores reales
ni upstream. Las demás dependencias funcionales conservan el orden del plan.

Estado del programa: **NO COMPLETADO**. Pendientes integración de entradas, autoridad,
reservas, conocimiento, continuidad, proyecciones, distribución y gates de cierre.

## Evidencia posterior: composición de foundations

- Migraciones aditivas v3–v8: reservas, contrato de step, scheduler, archivo de worktree,
  conocimiento y frontera de reparto justo. Se verifica cada versión y se conserva el
  checksum de todas las anteriores, incluido v5 después de añadir v8. Suite canónica
  de migración/scheduler/KnowledgePolicy: **51 passed in 8.10s**, exit 0.
- Scheduler: SQLite real y diez procesos con dos plazas; presupuesto durable, cola
  acotada, ciclos y expiración sin liberar procesos vivos. Revisión encontró inanición
  ante llegada continua de jobs nuevos. Dos regresiones y migración v8 corrigen el
  orden sin reescribir v5. Aún falta comprobar grants en la misma transacción que la
  admisión productiva: `claim_next` aislado no acredita autorización de ejecución.
- Reservas: rutas normalizadas, ancestros, aliases y ownership cercado; liberar exige
  cese confirmado, nunca sólo lease vencido. Las comprobaciones de rutas no sustituyen
  un sandbox frente a aliases creados posteriormente por procesos externos.
- Enforcement: entradas protegidas rechazan en tmux/herdr capacidades que no pueden
  imponer, sin ejecutar sus llamadas legacy. **46 tests nuevos pasan**. Suite ampliada:
  **143 passed, 1 failed** por fixture previa que no admite `plain_shell=False`; filtrada:
  **143 passed, 1 deselected**. Las entradas productivas aún no usan estas fronteras.
- Worktree: limpieza legacy conserva dirty/untracked/ignored; archivo protegido exige
  grant vigente, archivos explícitos, evidencia de cese y binding terminal/attempt exacto.
  Se corrigió binding ausente y revocación durante prueba de cese. Artefacto+referencia
  preceden retorno; GC protege referencias; archivo exitoso permanece en cuarentena.
  Suite canónica worktree/scheduler/migraciones: **74 passed in 9.73s**, exit 0.
- Consultas HTTP/MCP aditivas: owner y payload se leen en el mismo snapshot verificado;
  un grant de ejecución no permite leer jobs ajenos. MCP usa HTTP, no DB. Se conserva
  cursor/huecos, error estructurado y estado incierto sin inferir bloqueo del proveedor.
  Suite conjunta de estas consultas y foundations: **188 passed in 16.18s**, tres avisos,
  anterior a v7/v8; no es verificación final del programa.
- T074 parcial: contrato script inmutable antes del prompt y guard fresco después de
  readiness, incluido redelivery. Revisión reprodujo cancelación/generación cambiada
  durante espera y esquema manipulado; ambas fronteras fallan cerrado tras la corrección.
  Suite canónica step/agent/script/journal/API: **164 passed in 19.33s**, tres avisos.
  YAML y contrato previo a asignación siguen pendientes; model/effort desconocidos no
  se presentan como valores efectivos. No promete atomicidad entre DB y efecto externo.
- T074, revisión parcial de recuperación fría: `get_step_contract` usa ahora el
  snapshot de sólo lectura de `WorkRepository`, que verifica esquema, triggers de
  inmutabilidad y ledger antes de devolver el JSON congelado. Una prueba con SQLite
  real y proceso nuevo elimina el trigger de actualización: la recuperación falla
  con `SchemaMismatch` y no expone el contrato. Suite vecina tras el cambio:
  **166 passed, 3 warnings**; compilación, whitespace del código y composición pasan
  (260 archivos/946 dependencias, 2 contratos/0 rotos). T074 permanece **[ ]**:
  faltan fuentes autorizadas para YAML/preasignación y para modelo, esfuerzo,
  permisos, límites y retry policy aplicables; los desconocidos no son efectivos.
- T074, captura script posterior: `run_agent_step` congela los argumentos que puede
  acreditar antes de crear el terminal; el callback script vincula su identidad y
  persiste el contrato con el intento antes del prompt. Reutilización marca perfil,
  herramientas, modelo, directorio y worktree de creación como no aplicables;
  esfuerzo y retry policy siguen desconocidos. La suite step/agent/script/error
  en copia ext4 con hashes iguales al checkout pasó **160 pruebas, 3 avisos**;
  composición fresca: **270 archivos, 996 dependencias, 4 contratos conservados,
  0 rotos**. Es captura previa a crear terminal, no admisión/contrato durable previo
  a asignación. YAML no usa el callback de script; su retry pertenece a
  `workflow_service`. T074 sigue **[ ]** hasta cubrir YAML y acreditar todos los
  campos aplicables, incluidos modelo, permisos y límites finales cuando no se
  suministran explícitamente.
- KnowledgePolicy exige Principal, grant y capacidades explícitas por acción, vigentes
  en la transacción del servicio. No hay bypass admin ni fallback remoto→local.
  Revisiones inmutables, CAS, procedencia de resultados, aprobación explícita, caducidad,
  superseded y tombstones están probados con SQLite real. Redacción precede truncamiento;
  se corrigió filtración de entrada inválida en errores de enums. Suite canónica de
  revisiones/policy/memoria legacy/secretos: **95 passed in 13.38s**, exit 0.
  Consumidores, API de conocimiento y snapshots aún pendientes. Tombstone no borra
  físicamente el histórico; aprobación humana no prueba por sí sola verdad del contenido.
- Gate de arquitectura más reciente: **238 archivos, 846 dependencias, 2 contratos
  conservados**, exit 0. No equivale a ejecutar tests web/TUI ni integración productiva.

Las casillas de integración y aceptación end-to-end permanecen abiertas. Estos resultados
son evidencia acotada de incrementos, no sumas de cobertura ni signoff del roadmap.

### Snapshots, contratos y transporte de conocimiento

- Snapshot v9: contenido SQLite acotado a 64 KiB, hashes fuente/entrega separados,
  redacción antes de truncar, persistencia previa al retorno y resolución local una
  sola vez entre procesos. Empty es un snapshot válido; legacy ausente no activa
  resolución viva. Suite snapshot/policy/revisiones/migraciones: **69 passed in 8.57s**.
  Refactor posterior conserva autorización pública y permite validar evidencia de una
  orden admitida sin fabricar Principal: **26 passed in 24.56s**. El helper interno
  no concede autoridad; su caller debe revalidar grant y ámbito en esa transacción.
- Métodos internos de admisión/transición, autoridad/reservas/cola aceptan conexión
  del coordinador para una transacción única. Interfaces públicas preservadas.
  Repositorio/servicio/migraciones: baseline **52 passed**, refactor **52 passed**,
  más rollback compartido y migración v9 **54 passed in 8.57s**. Servicios de
  autoridad/reservas/scheduler: baseline **52 passed**, después **55 passed in 12.26s**.
- Scheduler incorpora filtro interno de elegibilidad calculado por el servidor:
  excluidos no consumen capacidad/presupuesto/turno, ni bloquean jobs elegibles;
  método público conserva comportamiento. **37 passed in 36.76s**. Todavía requiere
  el coordinador que calcule elegibilidad y confirme las reservas junto a intención.
- HTTP `/v1/knowledge`: propuestas, lectura actual y por revisión, review, tombstone,
  instrucciones; Principal verificado y KnowledgePolicy en cada transacción. DTO v1
  cerrado, cuerpo acotado y diagnósticos saneados en este router. Un RED de revisión
  fuera de int64 reprodujo OverflowError SQLite; límites HTTP corregidos. Suite del
  autor HTTP/servicios/consultas: **70 passed in 13.38s**, tres avisos; 22 son HTTP,
  incluida la aplicación principal, JWT firmado/expirado y CAS concurrente.
- MCP añade únicamente lectores `knowledge_read`/`knowledge_instructions`, con HTTP
  y bearer existentes. RED de registro mostró tools ausentes antes de conectarlos.
  Revisión independiente sin hallazgos high/medium; API/MCP/integración:
  **36 passed in 60.06s**, tres avisos. Prueba de composición posterior añade caducidad
  y tombstone: un error de expectativa tuple/list se corrigió en el test, sin cambiar
  producción; **1 passed in 49.21s**, dos avisos. Usa ASGI y SQLite reales, no TCP/JWKS
  externos. Aprobación, superseded, caducidad y retirada se observan igual desde servicio,
  API y MCP; revocación bloquea nuevas lecturas. No hay fallback de autoridad a legacy.
- Migración v10 incorpora bindings por intento: RED de tabla ausente, posterior suite
  de migraciones **19 passed**. Identidad JSON ausente/null se rechaza, sin aceptar
  CHECK NULL como éxito. Binding y coordinador siguen en desarrollo; no se presenta
  esta migración como activación de ejecución universal.

Pendientes explícitos: integración universal de contexto/ejecución, mutaciones MCP de
conocimiento, sincronización/checkpoints/cursores, continuidad y recuperación de producto.

### Admisión y frontera de efectos en curso

- WorkAdmission confirma grant, item/intento, binding de contrato y cola en una sola
  transacción. Preflight fuera del lock. Prueba real SQLite de concurrencia, cola llena,
  snapshot incorrecto y revocación durante preflight: primer GREEN **7 passed in 30.62s**.
- Revisión reprodujo tres errores de replay: lease vencido, padre terminado y backend
  temporalmente inaccesible impedían recuperar una operación ya admitida. RED **3 failed**;
  búsqueda exacta de binding+cola antes de preflight y de gates de ejecución corrige
  los tres sin renovar lease, reencolar ni enviar. GREEN **10 passed in 14.73s**.
  La autoridad actual del caller sigue siendo obligatoria; no hay permiso por hash.
- Un snapshot de origen debe poder heredarse por hijos/nietos con contratos efectivos
  distintos. RED en esa composición; validator de ancestry compartido conserva mismo
  job/snapshot hasta contrato origen y rechaza ciclos/desconexión. Snapshot+binding:
  **39 passed in 26.64s**. No se duplicó el recorrido entre servicios.
- Revisión detectó que paths del grant podían exceder paths a reservar. RED de dos
  casos; preflight usa exclusivamente `resources.write_paths`, incluso vacío (deny-all).
  Con este contrato de backend sin separación read/write, también restringe lecturas;
  no se promete una allowlist de lectura independiente. Admisión/bindings/snapshots/
  migraciones: **70 passed in 38.29s**, exit 0.
- Guard de efecto en base backend: preflight → guard servidor actual → transporte.
  Ausencia de guard, retorno distinto de None o excepción bloquean efecto. No se acepta
  un booleano del body ni se reenvía el guard como kwarg al transporte. RED **22 failed,
  46 passed**; GREEN enfocado **68 passed in 22.56s**. Suite ampliada **165 passed,
  1 failed** por el `plain_shell=False` legacy ya señalado. No se añadió sandbox.
- WorkService extrae envío posterior al commit a helper privado winner-only, preservando
  conducta: **13 passed antes / 13 passed después**. Recuperar un intent no permite
  llamar ese helper de nuevo. Scheduler extrae retirada transaction-local: baseline
  **37 passed**, RED helper ausente y final **38 passed in 43.92s**, con rollback/commit
  conjuntos de fallo conocido y retirada de cola. Sin cambios de DDL en las extracciones.
- Consulta Graphify posterior sobre WorkAdmission/WorkContracts/WorkScheduler/WorkReservations:
  grafo existente de 27 863 nodos sólo resuelve términos legacy, 39 vecinos y salida
  truncada. No acredita dependencias nuevas. Éstas se verificaron en fuente y gate;
  actualización del grafo sigue pendiente en T089, después de estabilizar integración.

El estado anterior del despacho queda actualizado por la verificación siguiente;
ninguna de estas garantías internas se atribuye todavía a las entradas públicas.

### Reconciliación y despacho asíncrono (2026-09-22)

- Se preservó el workspace existente. Se reutilizaron spec, plan y tareas aprobados;
  `check-prerequisites.sh --json --require-tasks --include-tasks` confirmó la feature.
  Checklist de requisitos: 14/14, sin modificar sus marcadores. Sin hooks registrados.
- Graphify antes de editar: consulta de WorkAdmission, WorkContracts, WorkReservations,
  terminal_service y launch sobre 30 154 nodos y 59 555 relaciones. La consulta devolvió
  895 vecinos y mostró 44 por presupuesto; no se interpreta como revisión exhaustiva.
  Se contrastaron fuentes de coordinador, contratos, snapshots, reservas, scheduler,
  servicio de trabajo, backend, terminal, sesión y CLI. El informe anterior de 27 863
  nodos es histórico; el refresh de la sesión previa ya existe.
- Baseline fresco: admisión/despacho **24 passed in 11.96s**; núcleo ampliado
  **254 passed in 17.77s**, ambos exit 0. Incluye diez procesos independientes,
  dos plazas, dos jobs, equidad, rollback de intención, revocación en preflight,
  envío incierto y ausencia de redelivery tras reinicio. No inicia proveedores reales.
- T017 necesita esperar inicialización asíncrona. Se añadió `dispatch_next_async`
  reutilizando la misma preparación transaccional, selección y guard de efectos que
  el dispatcher síncrono. Preflight/preparación corren fuera del event loop; el
  adaptador asíncrono conserva el loop del caller. WorkService mantiene un único
  escritor de recibos para ambas modalidades. No hay migración ni cambio de DTO.
- TDD: cuatro pruebas fallaron por la ausencia del dispatcher asíncrono; después
  pasaron junto a las regresiones existentes (**29 passed in 13.76s**). Se amplió
  evidencia de composición a snapshot exacto recuperado tras reinicio, timeout,
  recibo inválido y cancelación repetida: **33 passed in 54.58s**, exit 0.
- Una cancelación durante preparación no cancela ficticiamente el thread: observa
  su resultado y cierra su capacidad sin llamar al adaptador. Si ya había intención,
  registra `reconcile`; ante caída o fallo de persistencia queda `sent`, nunca vuelve
  a la cola. Cancelación durante entrega también cierra la capacidad y conserva
  reservas. Ni cancelar la petición ni cerrar el callback prueba cese del proceso.
- Revisión de T017 descubrió T091: falta persistir todos los datos de entrega y
  enrutar por operación desde el servidor. Un callback ligado a una petición no
  reconstruye trabajo después de reinicio ni debe recibir otra clase de operación
  elegida por la cola global. T091 queda abierta antes de T017–T020; no se activó una
  ruta pública parcial ni se rebajó el rechazo de tmux/herdr sin aislamiento.

Comando de verificación final del código de este incremento:

```bash
.venv/bin/python -m pytest -o addopts= -q \
  test/integration/test_work_dispatch.py test/integration/test_work_admission.py \
  test/services/test_work_contract_binding.py test/services/test_work_service.py \
  test/services/test_work_scheduler.py test/services/test_work_reservations.py \
  test/services/test_work_authority.py test/services/test_delegation_snapshot.py \
  test/clients/test_work_repository.py test/clients/test_work_migrations.py \
  test/backends/test_work_enforcement.py
```

Resultado: **262 passed in 69.16s**, exit 0. Después se ejecutó
`project-composition-check "$(cat .ai/project-name)"`: **246 archivos, 877 dependencias,
2 contratos conservados, 0 rotos**, exit 0. No se cambió código después de estos gates.

#### Revisión de composición del incremento

- Subsistemas modificados: WorkAdmission y WorkService; pruebas de integración SQLite.
- Vecinos revisados: contratos efectivos, autoridad, snapshots, reservas, scheduler,
  reducer/repositorio y frontera protegida del backend. Terminal/session/launch fueron
  inspeccionados como consumidores futuros; no están conectados por este incremento.
- Invariantes: intención/capacidad/rutas atómicas, un solo ganador envía, guard fresco,
  lease y revisión vigentes, contexto durable, recibo explícito, capacidad cerrada
  al salir y propiedad retenida cuando el efecto es incierto. No hay lock SQLite
  durante espera del adaptador; no se infiere éxito de un terminal idle.
- Dependencias reales: SQLite temporal, filesystem temporal y diez procesos. Transporte
  de prueba sin tmux/CLI real: verifica guard y orden, no aislamiento del sistema operativo.
- Contratos: suites de bindings, snapshots, migraciones y enforcement incluidas; no hay
  protocolo HTTP/MCP nuevo ni consumidor desplegado independiente que requiera Pact aquí.
- Fallos: RED esperado por interfaz ausente. `git diff --check` global devolvió exit 2
  por CRLF en archivos preexistentes; comprobación con
  `git -c core.whitespace=cr-at-eol diff --check` dio exit 0. No se normalizaron archivos
  ajenos ni se presenta el comando original como aprobado.
- Diff propio revisado contra copias anteriores de los tres archivos de código/test;
  se conserva el contenido preexistente. Los cambios de documentación distinguen
  evidencia interna de activación pública y no marcan T017/T023/T036 como completadas.
- Graphify: se conserva el refresh existente; los nuevos métodos privados/asíncronos
  y tests aún no están reflejados. Se difiere el refresh global a estabilizar T091 y
  la integración pública, con limitación explícita en T089. No se reutilizó el script
  temporal anterior ni se atribuyen al grafo estos nuevos símbolos.
- Conocimiento: no se copia el grafo ni se guarda evidencia transitoria en el vault.
  Las invariantes de incertidumbre ya constan en el contrato aprobado; esta evidencia
  incremental permanece en Spec Kit.
- Veredicto: **PASS WITH RISKS para el despacho interno**. La suite completa del repo,
  las cinco entradas públicas, el aislamiento real y los gates T084/T085 siguen sin
  demostración de cierre. No hay signoff del roadmap.

## Aprobación y límites externos

El usuario aprobó la migración incremental, siete entregas, autoridad única por proyecto
y rechazo de grants que el backend no pueda imponer. El gate de revisión de brainstorming
queda satisfecho; no hace falta repetir la aprobación para avanzar por el backlog.

La revisión debe cubrir spec.md, plan.md, data-model.md y contracts/. El backlog completo
queda listo para continuar por foundation/US1 y recorrer las demás historias sin perder alcance.
No se solicita reautorizar lecturas, planificación ni cambios de preparación ya realizados.

Por separado, T084 requiere cuentas/proveedores/modelos autorizados para consumo real y
T085 petición explícita de integración upstream. Publicar una release no está autorizado.
Estas dependencias no deben convertirse en casillas completed por disponer del plan.

## Registro histórico previo a T091: entrega durable y primer adaptador de launch

Estado en aquel registro: **en verificación; T017, T040 y T091 no se cerraban todavía**.
La numeración no representa una secuencia completada: T040 tiene KnowledgePolicy,
pero memory_service y memory_gateway siguen sin integrarla.

- T091: esquema aditivo v11 para órdenes inmutables, registro de adaptadores por
  operación/versión, hash del request original separado de la forma normalizada,
  replay sin adaptador instalado y recuperación desde un proceso nuevo.
- Validación antes de admitir: JSON canónico, modelo cerrado/frozen, aliases y
  serialización recuperable, límites de tamaño, campos de credenciales y herramientas
  dentro del contrato. La detección de secretos es heurística, no garantía universal.
- Adaptador inicial de launch usa terminal_service real con identidad durable y backend
  protegido por contexto de ejecución. Cada efecto revalida autoridad; la intención,
  reservas y vínculo al terminal se confirman antes. Un paste no equivale a recibo.
- Revisión independiente reprodujo colisión que borraba metadata ajena, revocación
  que dejaba sesión viva sin registro y snapshot omitido por el kill switch. Otra
  reproducción cubrió cancelación durante creación con revocación. Corregido:
  conservar evidencia para reconciliación en rutas gestionadas, no compensar con
  cleanup legacy, y fallar cerrado al inyectar un snapshot deshabilitado.
- RED ejecutables: tools fuera de contrato, aliases/roundtrip, credenciales anidadas,
  snapshot deshabilitado, colisión de identidad y cancelación/revocación. Los errores
  iniciales de fixture/import no se cuentan como RED de comportamiento.
- Verificación focal posterior: **7 passed in 46.30s** en test_work_launch.py.
  La primera regresión ampliada tuvo **287 passed, 1 failed**: timeout de 30 segundos
  al arrancar el proceso independiente en filesystem montado. Se amplió sólo esa
  espera a 120 segundos; la prueba conserva proceso spawn y comprobación de no reenvío.
  La repetición conjunta y el gate de composición se registrarán tras terminar.
- T017 sigue abierta: falta conexión de entradas públicas, congelación del perfil y
  cobertura de variantes legacy. Tampoco se afirma protección universal de callbacks
  posteriores a la capacidad de despacho ni aislamiento real de tmux/herdr.
- Sin proveedores reales, commits ni cambios remotos. Evidencia incremental en Spec Kit;
  no se copia el grafo ni resultados transitorios al vault.

### Verificación conjunta posterior a las correcciones

- `.venv/bin/python -m pytest -o addopts= -q` sobre repositorio/migraciones,
  reducer, servicio, autoridad, reservas, scheduler, contratos, snapshots, enforcement,
  admisión/despacho/delivery, work_launch y las suites terminal_frozen_memory,
  terminal_service, terminal_service_full y terminal_service_coverage:
  **497 passed in 153.71s**, exit 0. Incluye proceso spawn y las siete regresiones
  de launch. No es la suite completa del repositorio.
- Después: `project-composition-check "$(cat .ai/project-name)"`, exit 0;
  **251 archivos, 890 dependencias, 2 contratos conservados, 0 rotos**.
- Revisión de composición: esquema v11 aditivo e inmutable, transacciones de
  admisión/reservas/intención/vínculo, replay sin adaptador y recuperación de payload,
  rechazo de drift, guards antes de efectos y conservación de registros tras
  incertidumbre/cancelación. SQLite y filesystem temporales reales; transporte de
  prueba sin proveedor real. No hay un protocolo público nuevo que requiera Pact.
- Revisión independiente final: sin P1/P2 adicionales en las correcciones; las
  limitaciones de perfil, callbacks posteriores y entradas públicas siguen abiertas.
  No se rebaja el rechazo de backends sin enforcement demostrado.
- Diff: cambios preexistentes preservados; terminal_service conserva sus CRLF.
  No se normaliza el archivo para ocultar el diff global preexistente.
- Decisión Graphify: refrescar los 14 archivos de código/test afectados mediante
  extracción instalada sin LLM, incluyendo work_launch, work_backend y sus consumidores.
  El primer refresh intermedio (30.238 nodos, 59.758 relaciones) no cubría los últimos
  cambios; no se presenta como evidencia final.
- Conocimiento: mantener esta evidencia en Spec Kit; sin escritura al vault.

- Refresh Graphify final: exit 0, **30.286 nodos, 59.740 relaciones, 1.376 comunidades**;
  graph.json, graph.html y GRAPH_REPORT.md regenerados. Etiquetas de 371 comunidades
  se reemplazaron por sus hubs, sin llamar al LLM. La herramienta instalada creó su
  backup local automático; no se copió al vault ni se reutilizó el script temporal.
  El recorrido previo de corpus en /mnt/c tomó varios minutos de I/O, incluso usando
  changed_paths; no se presenta este procedimiento como solución estable de T089.
- `git -c core.whitespace=cr-at-eol diff --check`: exit 0. El check estándar conserva
  la limitación CRLF preexistente; no se anuncia como aprobado.
- Veredicto: incremento interno verificado con límites explícitos; **roadmap no
  completado**. Próximo trabajo: completar T091/T017 (perfil/configuración efectivos y
  entradas públicas) y seguir T018–T023. T040 sigue pendiente de integración de memoria.

## T040: política común de memoria

Incremento significativo autorizado. Checklist de requisitos: 14/14, sin cambios.
Se reutilizan spec/plan/tasks aprobados; consulta Graphify KnowledgePolicy/memory_service/
memory_gateway (373 vecinos, salida acotada) contrastada con fuentes.
Decisión de conocimiento: evidencia en Spec Kit, sin copiar grafo al vault.

Fronteras: MemoryService integra operaciones de conocimiento revisado sin promover
archivos históricos; KnowledgeRevisions conserva CAS y tombstones. Política común
redacta antes de truncar y registra accesos sin contenido. HTTP v1 usa la fachada;
MCP usa transporte del gateway sin fallback ante fallos remotos. Las rutas legacy
compartidas sin ACL durable se restringen a operador local verificado; tener scope
JWT general o aportar terminal_context no autoriza acceso al proyecto de otro trabajo.
Compatibilidad local no acredita revisión/aprobación ni reemplaza el protocolo v1.
TDD: acceso local/HTTP/MCP, secretos en frontera, auditoría atómica y fallo de auditoría,
revocación, rechazo de fallback y de contexto remoto autoatribuido.


### T040: correcciones de revisión y compatibilidad

- RED: cuatro regresiones reproducidas (grafo legacy accesible con JWT, contexto
  curado sin redacción, `store_lesson` ignorando autoridad remota y hashes de auditoría
  iguales para registros distintos). Registro: `/tmp/caos-t040-review-red.log`.
- Corregidos guard previo a proyección/cache/export, redacción del bloque curado,
  gateway de lecciones y objetivo de auditoría por registro/revisión.
- Verificación intermedia: 39 pasan y una falla por import local ausente en la
  redacción del curador; raíz identificada y corregida. No es evidencia de cierre.
- Revisión independiente de las cuatro correcciones: sin P1/P2 adicionales; exige
  documentar exclusión deliberada de legacy cuando auth está activada.
- Añadida prueba de migración v11 poblada a v12 y de inmutabilidad del audit.
- Suite amplia de memoria/conocimiento/API/MCP/grafo/migraciones en curso. T040
  permanece abierta hasta resolver fallos y completar gates frescos.


### Reconciliación de alcance T040 / T092

720 pruebas del bloque memoria/API/MCP/grafo/migraciones pasan; 33 de controles
HTTP pasan tras reconocer autoridad versionada además de scopes. Esto no basta
para cerrar T040: plan.md exige política en rutas locales/HTTP/MCP y el contrato
incluye todas las rutas. La auditoría legacy continúa separada/best-effort y los
backends de archivo/grafo acceden a internos. Se añade T092 como trabajo concreto,
sin redefinir ni marcar completada T040. Continúa integración y TDD sobre ese hueco.


### T092: integración de política legacy en curso

- Revisión/source contra Graphify (407 vecinos, salida acotada): owners MemoryService,
  MemoryRelationshipService, OkfArchiveBackend y MemoryGraphProvider. No hay consumo
  de proveedores; compilación y lint están sustituidos en las pruebas.
- Ruling: reutilizar SQLite real de metadatos mediante Session.get_bind(), cerrando
  la sesión antes de preparar WorkRepository. No usar una DB de auditoría separada
  ni endurecer audit_log (su contrato sigue siendo best-effort).
- Migración 13 append-only: authorized/completed/failed/denied. Intención confirmada
  antes de efectos; fallo posterior informa potencial resultado parcial con ID.
  No inventa grant/job para acceso del operador local.
- Cubiertos owners de archivos, grafos (también cache hit), relaciones y contexto;
  relaciones heredan el engine del MemoryService propietario, evitando DB global
  implícita cuando hay una base inyectada.
- RED reproducidos y corregidos: fallos selectivos de auditoría interior tragados
  por fallback, secretos en salida/metadatos del grafo, entradas/salida de compilación
  y tags históricos exportados. Archivo de fixtures aislado fuera del tmp_path
  del caso; SQLite y memoria por defecto de tests no apuntan al operador.
- Validación intermedia de denegaciones: 32 pasan; auth-read-gating: 80 pasan.
  Suite integrada posterior: 105 pasan, 1 falla por tags exportados; corregido y
  pendiente de nueva suite completa afectada.
- Una ejecución ampliada accidental de API encontró 34 fallos/1225 passed/1 skipped;
  no acredita suite del repo. Tokens sin sub y fixtures remotos ya corregidos en
  archivos focales. Quedan otros grupos que deben contrastarse en cierre transversal:
  dependencia opcional AGUI ausente (15), replay/contratos (9), timeouts HTTP (4).
- Fixtures estabilizados con prueba focal: cliente remoto explícito y lock CLI
  retenido hasta liberación del padre (10 pasan). No se amplía timeout para ocultar
  que el lock anterior expiraba durante el arranque lento del subprocess.

### T040/T092: verificación fresca y correcciones pendientes

- Suite afectada: 965 pasan, 12 fallan (`/tmp/caos-t040-final-memory.log`).
  No constituye cierre ni suite completa del repositorio. Fallos agrupados en
  fixtures BM25/argumentos MCP/tablas workflow, histórico de exportación y tags.
- Revisión concreta descubre otro acceso dentro de T092: `wiki_lint.run_lint`
  lee SQL/archivos y envía cuerpos al modelo antes de política común. Además,
  sus relaciones no heredan el engine inyectado. Corrección y TDD en curso.
- Ruling: auditar lint con acción existente `context` y objetivo `run_lint`;
  conservar metadatos de memoria de sólo lectura, con auditoría durable separada
  en la misma SQLite. No ampliar esquema por un nombre de operación.
- T040/T092 siguen abiertas; composición y revisión final deben ejecutarse
  después de terminar estas correcciones.

- Correcciones focales verificadas: 94 pruebas de fixtures/autorización y 35
  de exportación pasan. El secreto sintético `sk-*` desnudo no pertenece a los
  patrones actuales de secret_gate: se corrigió el fixture para comprobar el
  flujo con AWS soportado; no se afirma cobertura universal de credenciales.
- Despacho/snapshots: 115 pruebas frescas pasan (`/tmp/caos-delivery-fresh.log`).
  La evidencia no sustituye gates posteriores ni integración pública universal.

- Revisión de mantenimiento dentro de T092: plan/apply de reconciliación
  acceden directamente a archivos/metadatos; startup llama apply tras init_db.
  Se cubren ambas entradas, no sólo el comando CLI.
- Ruling: añadir acción `repair` a v13 todavía en desarrollo, manteniendo
  v1-v12 intactas. Dry-run no cambia proyecciones, pero sí debe auditar acceso;
  startup autenticado no fabricará identidad local para reparar.

### T040/T092: pruebas finales después de corregir aislamiento CLI

- Causa CLI confirmada: fixture principal preparaba `metadata.db`, mientras
  subprocess usaba `cli-agent-orchestrator.db`. Se conserva el basename canónico
  dentro del directorio temporal; no se modifica producción por este fallo.
- La tanda conjunta anterior se interrumpió al confirmar que retenía el fixture
  viejo; no se usa como evidencia de aprobación.
- Código estable: 1106 passed, 7 warnings (`/tmp/caos-t040-final-green.log`, exit 0).
  Incluye memoria, conocimiento, lint, reparación/startup, API/MCP, grafo,
  migraciones y despacho/snapshots.
- CLI real de reparación: 5 passed (`/tmp/caos-repair-cli-final.log`, exit 0),
  incluidos duplicados, índices, timestamps mixtos y recuperación tras lock.
- Composición en curso. No hay declaración de suite completa del repositorio.

- Gate de composición: PASS, 2 contratos Import Linter conservados, 254 archivos
  y 912 dependencias. El runner no añade tests porque tests/composition,
  tests/contracts y tests/integration están vacíos; las pruebas de integración
  reales están bajo test/ y se ejecutaron explícitamente arriba.
- Revisión posterior encuentra bloqueante de caché: la clave del grafo distingue
  scope, pero no base_dir/SQLite del dueño. Dos owners pueden compartir datos
  cacheados. Fix acotado y regresión A→B en curso; T040 aún abierta.
- Instrucción vigente del usuario: detenerse al terminar T040. No iniciar T043.

- Corrección de caché reproducida con 3 fallos: owners con roots/DB distintos,
  root compartido/DB distinta y DB compartida/root distinto. La clave ahora
  incluye ambas rutas resueltas; conserva cache hit para el mismo owner y
  auditoría en cada acceso. Focal: 51 passed.
- Verificación posterior al último cambio de implementación: 177 passed,
  4 warnings en grafos, integración de memoria/conocimiento y contratos HTTP/MCP
  (`/tmp/caos-t040-cache-composed.log`, exit 0). Los 1106 de arriba describen
  la tanda anterior a este ajuste; no se reetiquetan como ejecución posterior.
- Gate posterior al ajuste: PASS, 2 contratos conservados
  (`/tmp/caos-t040-final-composition.log`, exit 0). Revisión acotada en curso.

### Cierre verificado de T038/T040/T092 — 2026-09-23

- Política común integrada en fachada revisable y memoria legacy local/HTTP/MCP,
  archivos, relaciones, compilación, lint, reparación y grafo. Autoridad antes
  de acceso; intención durable antes del efecto; resultado antes de devolver;
  fallos de auditoría propagados con identidad y posible efecto parcial.
- SQLite real del owner y redacción antes de límites/modelos; la caché incluye
  wiki y SQLite resueltos. No aprobación automática de memoria histórica.
- Después del último cambio: 177 pruebas de integración/contratos pasan; gate
  de composición PASS (2 contratos), revisión independiente PASS y diff-check
  sin errores. Evidencia ampliada anterior al ajuste final de caché: 1106
  pruebas y 5 de CLI pasan, conservando la distinción temporal indicada arriba.
- Graphify actualizado localmente, sin proveedores: 30.458 nodos, 59.757
  relaciones, 1.404 comunidades. graph.json, graph.html y GRAPH_REPORT.md
  renovados; nueve módulos contrastados con archivos fuente. 403 etiquetas
  usan el hub como fallback; no se ejecuta renombrado LLM. El refresco generó
  su backup automático 2026-09-23; no convierte el script temporal en un
  procedimiento estable. Los pendientes previos de JSON/media no se cierran.
- Decisión de conocimiento: invariantes durables documentadas en
  docs/work-recovery.md y este Spec Kit. Sin nuevas conclusiones de alcance
  transversal que requieran duplicación en el vault; no se copia el grafo.
- No se acredita suite completa del repositorio, proveedores reales, integración
  pública universal ni cierre del roadmap. T084/T085 mantienen gates externos.
- Sin commit/push/merge/release. Se detiene ejecución aquí, por orden del usuario.

### Cierre acotado de T006 — 2026-09-23

- Alcance: enums y DTO v1 de `models/work.py`, sin dependencias de API ni backends;
  `snapshot_id=None` sigue expresando registros legacy. No se editaron rutas públicas,
  persistencia, proveedores ni DB del operador. T091, T017–T020 y T043+ siguen pendientes.
- Causa y corrección C1: `WorkAttempt.model_dump(mode="json")` emitía el campo derivado
  `delivery_phase`, que `model_validate()` rechazaba por `extra="forbid"`.
  La validación admite la fase serializada sólo si coincide con `state` explícito;
  una fase contradictoria falla y `state` conserva la única autoridad.
  Prueba nueva: `test_attempt_serialized_delivery_phase_round_trips_without_authority`.
  RED: `.venv/bin/python -m pytest -o addopts= -q test/services/test_work_reducer.py::test_attempt_serialized_delivery_phase_round_trips_without_authority`
  → exit 1, 1 failed por `delivery_phase` extra; GREEN del mismo comando → exit 0,
  1 passed. Registros en `/tmp/caos-exec/T006/.logs/c1-roundtrip-red.log` y
  `/tmp/caos-exec/T006/.logs/c1-roundtrip-green.log`.
- Gate fresco posterior a C1 (`/tmp/caos-exec/T006/gate.md`):
  `.venv/bin/python -m pytest -o addopts= -q test/services/test_work_reducer.py`
  → exit 0, 54 passed; `.venv/bin/python -m pytest -o addopts= -q test/api/test_work_contract.py`
  → exit 0, 9 passed, 3 warnings de deprecación preexistentes de TestClient
  FastAPI/Starlette y configuración Pydantic externa al modelo.
  `project-composition-check "$(cat .ai/project-name)"` → exit 0:
  Import Linter examinó 254 archivos/912 dependencias, 2 contratos conservados,
  0 rotos. La comprobación AST de imports API/backend también pasó (exit 0).
- Revisión independiente de Spec Kit y código: PASS tras C1; evidencia en
  `/tmp/caos-exec/T006/review.md`. `git -c core.whitespace=cr-at-eol diff --check`
  → exit 0 sobre tracked diff. Los archivos fuente/test de T006 ya eran untracked;
  `git diff --no-index --check /dev/null <file>` no mostró whitespace y devuelve
  exit 1 por diferencia de archivo nuevo, no por fallo de whitespace.
- Graphify: se consultó el grafo existente y se contrastó con fuente. El nuevo
  validador añade un símbolo que ese grafo aún no refleja; se difiere el refresh
  global a T089, sin modificar Graphify en este incremento acotado.
  Conocimiento durable: el contrato está en código y Spec Kit; no hay decisión
  transversal nueva que justifique duplicación en Obsidian.
- Límite: suites focales y contrato consumidor no son suite completa, prueba de
  proveedores reales ni prueba de integración pública universal. T084/T085 y
  demás gates externos permanecen abiertos. Sin commit/push/merge/release.

### Cierre acotado de T091 — 2026-09-23

- B1: la revisión independiente reprodujo que el drift de esquema/normalización
  de un adaptador con la misma versión abortaba el scan del scheduler. `_eligible`
  ahora aísla `ContractConflict` por candidato, deja esa operación en cola y
  permite avanzar a otra sana sin consumir el presupuesto ni las reservas de
  la bloqueada. El diagnóstico registra sólo intento/generación, sin payload.
- B2 y ruling de Astra: la detección de credenciales en texto libre es heurística;
  por ello las órdenes nuevas no guardan mensajes arbitrarios en
  `work_delivery_orders.payload_json` ni en eventos de entrega. La orden
  inmutable guarda operación, versiones, hashes y referencia opaca; el contenido
  exacto reside en un artefacto privado, acotado a 64 KiB y dirigido por hash.
  La referencia v14 se vincula a propietario, intento/generación, contrato y
  snapshot. La restauración verifica
  ese vínculo, bytes/hash y adaptador registrado tras la revalidación vigente.
- Migración v13→v14 aditiva: conserva las órdenes históricas byte a byte y añade
  tabla de referencias inmutable. Una orden legacy con payload en claro, o una
  referencia ausente, alterada o no autorizada, no se ejecuta ni se reconstruye
  desde otra orden. Un lector antiguo rechaza el ledger v14 desconocido; no se
  reescribe historia para un rollback aparente.
- Evidencia RED/GREEN: las trazas completas de mutaciones reversibles prueban
  B1 en la excepción de `_eligible` y B2 en la aserción de plaintext del row;
  tras restaurar código pasan 2/2 nodos y 27/27 pruebas de entrega. Gate final
  fresco: 76/76 en entrega, despacho, launch y migraciones, exit 0
  (`/tmp/caos-exec/T091/gate.md`). `project-composition-check` exit 0:
  Import Linter examinó 254 archivos y 913 dependencias, 2 contratos conservados,
  0 rotos. Revisión independiente B1/B2 PASS y composición PASS WITH RISKS;
  fuentes en `/tmp/caos-exec/T091/rereview.md` y `composition-review.md`.
- Residuales: un rollback SQLite puede dejar un artefacto privado sin referencia,
  incapaz de despacho; el almacén depende de garantías POSIX de propietario y
  locking. Las entradas públicas T017–T020, la adaptación de snapshot T043, la
  matriz real T084 y la integración upstream T085 siguen pendientes. Las pruebas
  usan SQLite/filesystem temporales y dobles de transporte; no prueban proveedor
  real, aislamiento de tmux/herdr ni suite completa del repositorio.
- Graphify: se consultó el grafo existente para WorkDeliveries, WorkAdmission y
  repositorio; cada conclusión relevante se contrastó con fuente actual. Este
  cierre no refresca el grafo; la decisión de refresh estructural global queda
  en T089. Sin escritura al vault, commit, push, merge ni release.

### Dependencia de T017 revisada — 2026-09-23

T017 sigue pendiente. El launch público (`cao launch` → `POST /sessions` →
`session_service.create_session`) aún llama directamente a `create_terminal`.
La admisión durable exige principal verificado, job y grant preexistentes,
revisión y contrato efectivo congelado; esa autoridad no se obtiene de la
petición ordinaria y todavía no hay composición de `WorkAdmission` en el runtime.
El dispatcher registrado selecciona la siguiente orden de la cola global, por
lo que su resultado tampoco puede atribuirse a la petición HTTP que lo activó.
Conectar T017 ahora arriesgaría crear autoridad desde datos del caller o mezclar
la respuesta de una operación con otra.

Secuencia segura revisada para el launch: **T024/T025 → porciones de launch de
T029/T035 → T017**. Las primeras prueban autoridad y reservas; las porciones
intermedias deben componer en el servidor el contexto de admisión confiable, el
registro de adaptadores y la correlación por identidad de operación; después
T017 puede integrar y probar la entrada pública y su recuperación. T029, T035
y T017 permanecen abiertos. Esta dependencia acota el orden de ejecución sin
acreditar la activación pública ni el cierre de esas tareas.

### Prerrequisitos T024/T025 aceptados — 2026-09-23

T024 queda cerrada por pruebas directas de `WorkAuthority` con SQLite temporal:
un body que falsifica el principal se rechaza al autorizar y emitir raíz; la
allowlist durable del job se vuelve a comprobar; los grants hijos no amplían
proveedores, permisos, rutas ni expiración; y la revocación del padre deniega
la autorización del hijo tras reiniciar el repositorio. T025 queda cerrada por
pruebas directas de `WorkReservations`: cuatro procesos compiten por una ruta y
sólo uno reserva; rutas ancestro/hijo y alias por symlink/hardlink colisionan;
un intento cancelado conserva ownership mientras el proceso escritor externo
sigue vivo; la liberación exige prueba `StoppedWriter` del verificador del
servidor y revalida la revisión CAS.

Gate conjunto posterior a restaurar las mutaciones RED controladas:
`.venv/bin/python -m pytest -o addopts= -q test/services/test_work_authority.py test/services/test_work_reservations.py`
→ 30 passed; `project-composition-check "$(cat .ai/project-name)"` → dos
contratos de arquitectura cumplidos (254 archivos, 913 dependencias, cero
rotos). Los RED sólo acreditaron sensibilidad: una allowlist omitida y una
consulta de ocupación anulada hicieron fallar las pruebas correspondientes;
no quedan cambios de producto ni de pruebas de este incremento. Revisión de
composición: PASS WITH BOUNDARIES para estas dos piezas internas.

La reserva de directorio cubre ancestros del namespace y alias inode directos
de rutas solicitadas; no aísla recursivamente inodes descendientes ni alias
creados después. El aislamiento verificable del backend sigue siendo necesario.
Estas pruebas no conectan autoridad ni reservas a HTTP/MCP/CLI, no prueban
proveedores reales y no cierran T029/T035/T017 ni otros gates externos.

### Revisión independiente T029 — 2026-09-23

T029 permanece abierta. La porción de launch registrado sí revalida mediante
el port de `WorkAdmission`: `_prepare_dispatch` crea un guard ligado al binding,
grant/revisión/cadena parental, contrato y snapshot congelados, estado de envío,
capacidad y reserva activos; `WorkBackendView` lo ejecuta antes de cada efecto
protegido. `terminal_service.send_input` lo ejecuta además antes de preparar el
recibo durable de la tarea. La prueba RED/GREEN de revocación entre startup y
mensaje confirma que no se escribe un recibo nuevo ni se envía la tarea; la
intentona queda en `reconcile`. Gate existente, sin repetición en esta revisión:
launch/backend 76 passed, agent_step 58 passed, ambos exit 0; composición de
arquitectura exit 0 (254 archivos, 914 dependencias, 2 contratos, 0 rotos).

Falta la parte expresamente asignada a `agent_step.py` por `tasks.md:T029`.
`run_agent_step` aún usa directamente `create_terminal` y `send_input` para
crear/asignar y enviar, sin binding, reserva ni port de trabajo; su eventual
`guard_delivery` pertenece al contrato de step y puede ser `None`. El
`caller_id` recibido del body sólo participa en CWD, recibo de hijo y routing;
no selecciona ni amplía un grant, pero tampoco sustituye la comprobación de
autoridad antes de un efecto de trabajo. Las 58 pruebas legacy no acreditan esa
condición T029. Siguiente frontera: owner de T029 en servicios debe conectar
una asignación gestionada de `agent_step` a un contexto/port emitido por
`WorkAdmission` y probar revocación/identidad falsificada antes de crear y enviar,
sin tomar autoridad de `caller_id`. La ruta directa actual sólo conserva
compatibilidad legacy; T019 sigue siendo dueño de semántica hijo/handoff y T035
del setup/resolver público confiable. T017 no se activa por este incremento.

### Cierre de T029 — frontera interna gestionada (2026-09-23)

T029 se cierra para la entrega registrada de servicio, tras una segunda revisión
independiente. `WorkAdmission.dispatch_registered_next` restaura el binding,
payload cerrado y snapshot autorizado de la orden durable; el registro del
servidor selecciona `agent_step_adapter` versión 1 y ejecuta la rama real
`run_managed_agent_step`. El payload sólo admite terminal reservado, perfil y
mensaje; un `caller_id` añadido se rechaza antes de admitir efectos. La
identidad terminal queda ligada al intento antes del despacho, y el contexto
compartido con launch la aplica durante la creación.

La rama gestionada revalida el port efímero antes de asignar/crear, deriva
proveedor, modelo conocido, directorio y herramientas del contrato congelado,
usa sólo la memoria del snapshot y revalida después de la inicialización antes
de enviar. `terminal_service.send_input` comprueba de nuevo antes de preparar
un recibo; `WorkBackendView` comprueba preflight y el fence justo antes de cada
mutación del transporte. Revocar tras el startup deja el terminal para
conciliación, sin recibo ni envío de la tarea. El port se cierra al terminar el
callback, por lo que un backend retenido no puede efectuar un envío tardío.
La rama devuelve observación conservadora: IDLE o texto de pantalla no prueban
completion. `run_agent_step` directo conserva compatibilidad legacy y no recibe
autoridad managed por `caller_id` ni por su callback opcional.

Evidencia final existente, sin repetir ejecución en esta revisión: 196 pruebas
pasadas en agent-step gestionado, launch, agent-step legacy, enforcement y
admisión/despacho/entrega; `project-composition-check` exit 0 (256 archivos,
920 dependencias, dos contratos conservados y cero rotos); Black, isort,
whitespace y compilación exit 0. El RED de comportamiento retiró temporalmente
el mensaje restaurado y falló el recorrido admitido→registro→despacho→
agent-step→transporte; se restauró antes del gate final. La revisión de
composición fue PASS WITH BOUNDARIES.

Límites: el transporte de prueba es un backend/proveedor falso con SQLite
temporal; tmux/herdr reales siguen rechazando enforcement de proceso no
verificable. T035 aún debe conectar principal, grant, resolver público y
registro en runtime a HTTP/MCP; T017 espera esa integración para activar launch
público y su correlación. T019 conserva padre/hijo y handoff; T036 conserva la
prueba de procesos independientes y los escenarios externos. Ninguno de esos
trabajos ni sus gates se cierra por T029.

### Prerrequisito interno T035 aceptado — 2026-09-23

Revisión independiente: **PASS WITH BOUNDARIES** para el resolver interno de
launch. `LaunchRuntime` consume contextos preaprovisionados por código confiable,
revalida principal, grant/revisión, contrato y snapshot, calcula solicitud y
entrega deterministas, y delega la admisión durable y su replay en
`WorkAdmission`. Devuelve sólo recibo de la operación propia; no crea terminal,
despacha cola ni fabrica job o grant. Errores propios saneados y selector
ausente/ajeno indistinguibles.

Gate fresco: 23/23 pruebas focalizadas de runtime, launch y admisión con SQLite
temporal; `project-composition-check` exit 0 (257 archivos, 928 dependencias,
dos contratos conservados, cero rotos). Informe y límites:
`/tmp/caos-exec/T035/sol-prerequisite-review.md`.

**T035 permanece `[ ]`**: faltan entrada HTTP/MCP autenticada, aprovisionamiento
confiable de contexto en runtime, mapeo de errores público y pruebas de hijos y
continuaciones sin ampliación de privilegios. T017 aún debe integrar launch
público y correlacionar su respuesta con la identidad admitida; T019/T036
conservan sus gates. No se ha probado proveedor o backend real ni activación CLI.

### Puerto inyectable T017 revisado — 2026-09-23

Revisión independiente: **PASS WITH BOUNDARIES** para
`DurableLaunchGateway`, sólo como puerto interno inactivo. El request cerrado
aporta selector y contenido no privilegiado; el principal verificado y el
`LaunchRuntime` con contexto preaprovisionado proceden del servidor. El gateway
resuelve y admite, devuelve sólo el `LaunchReceipt` propio, conserva replay y
conflicto de clave, y no despacha, crea terminal ni fabrica ACK. La prueba con A
y B en cola comprueba que B no cambia el recibo A. No hay consumidor HTTP,
MCP, CLI, sesión o terminal ni fallback legacy de este puerto.

La primera revisión detectó que un `RuntimeError` privado del preflight del
backend escapaba sin saneamiento. Tras el fix, la prueba RED/GREEN con SQLite
temporal y autoridad/grant/contrato/snapshot/admisión reales comprueba el error
estructurado `launch_runtime_unavailable`, sin texto privado, filas ni efectos.
Los errores propios del gateway y los `LaunchRuntimeError` ya saneados conservan
sus códigos. Gate fresco de revisión: 27/27 pruebas focales de gateway,
runtime, launch y admisión; `project-composition-check` exit 0 (258 archivos,
931 dependencias, dos contratos conservados, cero rotos); Black/isort exit 0.
Informe: `/tmp/caos-exec/T017/sol-port-fix-review.md`.

**T017 y T035 permanecen `[ ]`**. Falta proveedor operativo de contexto e
identidad autenticada de transporte (T035); T017 conserva launch ordinario,
compatibilidad y recuperación pública. No se ejecutó proveedor/backend real ni
DB del operador y esta revisión no acredita activación pública.

### Ingress HTTP parcial T035 revisado — 2026-09-23

Revisión independiente: **PASS WITH BOUNDARIES** para `POST /work-launches`.
La ruta separada de `/sessions` usa body cerrado, principal verificado de
`get_current_principal` y gateway tipado sólo desde `app.state`. Devuelve 202 con
la identidad durable de su propia admisión; no crea terminal, despacha ni
fabrica ACK. Ausencia o fallo inesperado del gateway da 503 fijo; sus errores
saneados conservan códigos y envelope estructurado 403/422/409/503. El guard
general de rutas mutantes conserva su regla y reconoce únicamente esta ruta
con la dependencia de principal verificado.

Gate fresco: 130/130 pruebas de API, guard, gateway, runtime, launch y admisión;
11/11 pruebas de identidad; `project-composition-check` exit 0 (258 archivos,
933 dependencias, dos contratos conservados, cero rotos); diff check exit 0.
Informe: `/tmp/caos-exec/T035/sol-api-review.md`.

**T035 y T017 permanecen `[ ]`**. El 401 de la dependencia compartida mantiene
`detail` plano, límite pendiente para un contrato de error totalmente uniforme.
No hay proveedor operativo de contexto/gateway, MCP, CLI, launch ordinario,
hijos/continuaciones, proveedor real ni DB del operador; la ruta devuelve 503
por defecto hasta recibir composición confiable.

### Envelope 401 local de T035 revisado — 2026-09-23

Revisión independiente: **PASS WITH BOUNDARIES** para el arreglo de identidad
de `POST /work-launches`. `get_work_launch_principal` conserva la validación de
`get_current_principal`, remapea sólo HTTP 401 a `launch_identity_required` fijo
y preserva `WWW-Authenticate`. La prueba HTTP remota comprueba el envelope,
ausencia de filtración y cero llamadas al gateway. El body cerrado, el gateway
exclusivo de `app.state` y la falla cerrada 503 siguen vigentes. El guard
mutante exige exactamente POST, `/work-launches` y este adaptador; conserva las
reglas preexistentes de scopes y conocimiento.

Gate independiente: 142/142 pruebas focales de API, guard, gateway, runtime,
admisión e identidad; `project-composition-check` exit 0 (258 archivos, 933
dependencias, dos contratos conservados, cero rotos); diff check exit 0.
Informe: `/tmp/caos-exec/T035/sol-api-auth-fix-review.md`.

El límite de `detail` plano consignado en la revisión anterior queda resuelto
para esta ruta. **T035 y T017 permanecen `[ ]`**: faltan provisión confiable del
runtime/gateway, MCP, CLI, launch ordinario, consultas públicas e hijos/
continuaciones. No hubo proveedor real ni BD del operador; la ruta sigue
fallando cerrada por defecto.

Revisión de bloqueo de provisión T035: **PASS del bloqueo, sin activación**.
Setup/autoridad confiable aún debe definir y persistir un vínculo verificable
`(principal, selector)` con job, grant/revisión y contrato/snapshot congelados,
incluidos creación administrativa, permisos, ciclo de vida y reconstrucción tras
reinicio. La composición API podrá instalar proveedor/gateway sólo desde ese
vínculo; `app.state`, fixtures, request o entorno no lo crean. Conservar el 503
estructurado hasta demostrar esa provisión. Informe:
`/tmp/caos-exec/T035/sol-provider-block-review.md`.

### Cerca bearer de T035-A revisada — 2026-09-24

Revisión independiente: **PASS parcial** para la identidad de
`POST /work-launches`. La dependencia propia exige bearer por petición aun con
auth global desactivada; loopback, `Host`, cabeceras forwarded y campos del body
no sustituyen identidad. La denegación antecede a la consulta del gateway y
devuelve exactamente `401 launch_identity_required` con
`WWW-Authenticate: Bearer`. Un principal obtenido por el verificador llega al
gateway tipado sin activar auth global ni fabricar identidad desde la petición.

Gate fresco: 33/33 pruebas HTTP de cerca, ingress y autenticación; composición
exit 0 (269 archivos, 988 dependencias, cuatro contratos conservados, cero
rotos). Informe: `/tmp/caos-exec/T035/identity-fence-sol-review.md`.

**T035 permanece `[ ]`**. Sigue sin setup administrativo, gateway instalado en
runtime, provisión operativa, MCP, rutas públicas de hijos/continuaciones y su
prueba de no ampliación, ni evidencia de proveedor real. Sin gateway, una
identidad válida continúa recibiendo el 503 estructurado; este incremento no
autoriza ampliar grants ni reservas y no cierra T017/T093.

### Factory interno de launch T035-B revisado — 2026-09-24

Revisión independiente: **PASS parcial** del factory inactivo
`build_durable_launch_gateway`. Verifica el esquema SQLite antes de publicar el
gateway, recibe sólo el mapping tipado de backends del servidor y registra
exclusivamente `("launch", 1)`. El provider comparte infraestructura; runtime
resuelve `(principal, selector)` de nuevo por petición. La construcción no crea
autoridad ni órdenes. SQLite temporal probó provisión posterior, recibo/cola sin
dispatch, replay tras reinicio, rechazo de sujeto ajeno, retiro, revocación,
backend inválido, esquema incompatible y adapter no soportado sin escrituras de
admisión. Gates frescos: focal **4 passed**, conjunto gateway/runtime/origen/HTTP
**48 passed** y `project-composition-check` **PASS** (269 archivos, 991
dependencias, cuatro contratos conservados). Una copia hash-verificada reprodujo
**29 passed**. Informe: `/tmp/caos-exec/T035/composition-sol-review.md`.

**T035 y T017 permanecen `[ ]`**. El setup usado es administrativo interno de
prueba, no una interfaz operativa. Falta demostrar el factory real inyectado en
HTTP con bearer por petición, vincular el backend productivo y conectar MCP;
el lifespan no instala gateway ni se activa dispatch. Siguen pendientes las
rutas públicas de hijos/continuaciones y la prueba de no ampliación de grants.

### Composición multiproceso T036 aceptada — 2026-09-23

Revisión independiente: **PASS WITH BOUNDARIES** para la prueba interna de
`WorkAdmission` con procesos `fork` distintos y conexiones nuevas al mismo
SQLite temporal. Tras `_prepare_dispatch`, una muerte dura `os._exit(23)` deja
intención `sent`, capacidad `held` y reserva de ruta `active` durables, sin
evento de recepción/ejecución; otro proceso no reentrega el intento incierto.
Durante un preflight bloqueado, otro proceso revoca el grant; la admisión
revalida autoridad y no crea item, intento, binding ni cola. Sólo persiste
`grant.revoked`. Esto aporta evidencia de la frontera de revocación de SC-003
y de la conservación de capacidad/reserva de SC-004; las diez solicitudes,
dos plazas y equidad de SC-004 se cubren por T026 y sus pruebas del scheduler.

TDD: un RED temporal, limitado al hijo de admisión tras la revocación, forzó
`WorkAuthority._chain(live=False)` y terminó por la aserción conductual
`('admitted',) != ('raised', 'AuthorityDenied')` en 13,10 s (exit 1, sin timeout).
Se retiró la instrumentación; GREEN restaurado de los dos casos multiproceso:
2/2 (exit 0). Gate independiente: `project-composition-check` exit 0,
258 archivos, 933 dependencias, dos contratos conservados y cero rotos.
Informe: `/tmp/caos-exec/T036/sol-red-rereview.md`.

**T036 `[x]` sólo para esta composición interna.** `fork` y SQLite temporal no
acreditan arranque `spawn`, backend/proveedor real, sandbox, ruta pública ni
expiración de lease. Ruff no estuvo disponible, mypy quedó bloqueado y la
revisión interactiva no pudo iniciarse sin TTY; ninguno cuenta como PASS.
T035, T017 y los demás gates públicos/externos conservan su estado pendiente.

### Adaptador snapshot-ID T043 aceptado — 2026-09-23

Revisión independiente: **PASS limitado a T043**. `WorkOrderSnapshotReader`
revalida el `WorkContractBinding` durable y lee el snapshot dentro de la misma
transacción SQLite; la referencia sola no concede acceso. La igualdad exacta
con el binding conserva el snapshot, contrato, job, principal, grant y recursos
acordados por la orden e impide sustituir el ID por uno de otro destino; la
revalidación comprueba estado, scope, hash,
cadena de grant y revocación. Cualquier fallo explícito produce
`FrozenSnapshotMemoryUnavailable` sin contenido ni fallback live. Un snapshot
autorizado `""` sigue siendo éxito; sin selección explícita se conserva la ruta
legacy `None`/vacío/texto. `terminal_service` mantiene `frozen_memory: str | None`
y no interpreta IDs.

TDD: el RED previo del caso de reemplazo explícito devolvió `LEGACY` en vez de
`FROZEN`; GREEN independiente actual: 81/81 pruebas de adaptador, frozen legacy,
terminal, binding y snapshots (exit 0). Las pruebas de rechazo usan SQLite,
policy, grant, admisión y binding temporales reales; cubren ID ajeno, otro
destino, corrupción, ausencia y revocación. `project-composition-check` exit 0:
258 archivos, 937 dependencias, dos contratos conservados, cero rotos;
isort, Black, py_compile y diff check acotado pasan. Informe:
`/tmp/caos-exec/T043/sol-review.md`.

**T043 `[x]`; T044 `[ ]`.** El lector no está conectado a launch, hijo,
handoff, YAML, scripts ni al caller legacy de `agent_step`. Esta revisión no
acredita la igualdad end-to-end tras reinicio, proveedor/backend real,
aislamiento ni BD del operador.

### Incremento managed de T044 revisado — 2026-09-23

Revisión independiente: **PASS parcial; T044 `[ ]`**. Las rutas internas
`launch` y `agent_step` con orden durable ya admitida entregan, tras reconstruir
`WorkRepository`, `WorkAdmission` y el registro de adaptadores, el contenido
leído de `work_delegation_snapshots`. Las pruebas comparan el efecto capturado
con los bytes persistidos para Unicode, redacción, truncamiento UTF-8 y vacío;
el vacío no consulta memoria viva. La autorización, binding, revocación e
intención `sent`/conciliación permanecen en el dispatcher existente.

Verificación fresca conjunta: **98 passed in 58.22s** en pruebas de launch,
agent-step, dispatch, delivery, admisión y snapshots. `project-composition-check`
exit 0: 258 archivos, 937 dependencias, dos contratos conservados, cero rotos;
`git -c core.whitespace=cr-at-eol diff --check` exit 0. El RED conductual
temporal de launch está documentado en el informe del autor y la línea de
producción restaurada coincide con la fuente revisada; no se repitió la
mutación durante esta revisión. Informe independiente:
`/tmp/caos-exec/T044/sol-managed-review.md`.

Esta prueba usa SQLite temporal real y dobles de backend/proveedor; no acredita
proveedor real, TTY ni DB del operador. No conecta launch público, hijo/handoff,
YAML, scripts ni `agent_step` legacy. Los puentes T017, T019, T020 y T035
siguen abiertos, por lo que T044 no tiene cobertura universal ni cierre.

### Bridge inbox managed T018 aceptado — 2026-09-23

Re-revisión independiente tras el HOLD de identidad: **PASS acotado; T018 `[x]`**.
La migración v16 añade un UUID lógico no secreto y ruta canónica inmutables,
creados una sola vez en la transacción de migración. Conserva literalmente el
esquema/checksum v15 y sus bridges, y rechaza identidad ausente, corrupta o
contradictoria. Las conexiones reales SQLAlchemy y WorkRepository leen su
propio `PRAGMA database_list` y UUID; ambas cotejan `DATABASE_FILE` y el
repositorio también su ruta. Coordinador y adaptador fijan UUID para su contexto
servidor y revalidan antes de reserva, bridge, habilitación y efecto. La fixture
de prueba alinea constante, sesión y repositorio al mismo SQLite temporal.

El RED correctivo previo falló por `DATABASE_FILE` divergente sin rechazo
(`DID NOT RAISE WorkConflict`); el GREEN lo rechaza antes de item, fila, cola,
orden o efecto. Las pruebas cubren además dos conexiones reales y `os.replace`
same-path por DB de UUID distinto, ausencia de contexto, reapertura estable,
bridge v15 literal, deduplicación, caída parcial, reinicio incierto, legacy
`PENDING` y revocación. Revisión focal propia:
`.venv/bin/python -m pytest -o addopts= -q --no-cov test/services/test_work_inbox.py test/clients/test_work_migrations.py`:
**33 passed in 33.22s**, exit 0. Gate conjunto fresco del controller:
**65 passed in 46.90s**, `--no-cov`, exit 0; `py_compile` y
`git -c core.whitespace=cr-at-eol diff --check` PASS;
`project-composition-check` exit 0, 260 archivos, 945 dependencias, dos
contratos conservados y cero rotos. Informe: `/tmp/caos-exec/T018/sol-rereview-identity.md`.

Corrección regresiva T018 del cursor-tupla del journal: **PASS acotado**. La
lectura de `work_inbox_store_context` usa posiciones compatibles con `sqlite3.Row`
y tuplas y mantiene los rechazos de singleton, UUID canónico y ruta de la
conexión real. La revisión independiente ejecutó el gate conjunto de contrato
de paso, migraciones e inbox: **65 passed**, exit 0; el caso API volvió a
acreditar HTTP 409 `contract_rejected`. Una comprobación SQLite real con cursor
tupla rechazó ruta ajena, UUID inválido y contexto ausente; composición volvió
a pasar con 260 archivos, 945 dependencias y dos contratos intactos. El fallo
`15 failed, 16 passed` registrado en la revisión T020 es evidencia histórica
anterior a esta corrección; el bloqueo de entrada Work de T020 permanece abierto.
Informe: `/tmp/caos-exec/T018/sol-step-contract-rereview.md`.

Graphify existente orientó los vecinos `InboxService`, `WorkAdmission` y
`WorkRepository` (30.458 nodos, consulta truncada); cada invariante del dictamen
se contrastó con fuente. **Decisión: diferir refresh incremental a T089**,
porque el grafo compartido precede T018 y el worktree acumula otros incrementos
concurrentes; no se usa su estado actual como evidencia de v16. T089 deberá
refrescar los módulos T018, comprobar nodos/rutas contra fuente y registrar
conteos/advertencias antes del cierre global.

Límites: un UUID fijado detecta reemplazo con UUID diferente dentro del mismo
contexto, pero un reemplazo completo entre procesos independientes requiere
ancla externa. Copiar una DB con el mismo UUID conserva identidad lógica, no
prueba identidad física. El rollback detiene managed y conserva UUID, bridge,
orden y fila `RECONCILE`; un lector anterior incompatible debe parar sin
reinterpretarla como `PENDING`. El PASS no acredita ruta pública, proveedor o
backend real, DB del operador, suite completa, aislamiento físico ni todas las
versiones desplegadas. Review interactiva sin TTY y Ruff no disponible quedan
sin resultado aprobatorio.

### T019: bloqueo de lineage revisado — 2026-09-23

Revisión independiente: **PASS del bloqueo; T019 `[ ]`**. El registro legacy
`native_children` conserva parentesco de terminal y ciclo de vida, pero no
existe un vínculo durable y autorizado entre intento/generación padre,
hijo/handoff, contrato/grant y recibo de entrega. `caller_id` llega sin
autenticación, la clave de handoff deduplica sólo creación, y el adapter managed
actual no recibe contexto padre. Ninguna de esas señales acredita
`task_received` ni convierte `native_children.acknowledged` en Work ACK.

El siguiente diseño debe fijar en WorkRepository, WorkContracts y WorkAdmission
el port autenticado, el binding y el fencing de generaciones antes de escribir
un RED o implementar la proyección en `agent_step`/handoff. La propuesta de
Astra no es aceptación de ese diseño ni cierre de T019. El test existente del
reducer para ACK con receipt pasó: **1 passed in 12.66s**, exit 0; no acredita
lineage. Informe: `/tmp/caos-exec/T019/sol-block-review.md`.

### T019-A: integridad y replay de lineage interno v23 — 2026-09-24

**PASS parcial independiente; T019 y T093 siguen `[ ]`.** La migración aditiva
v23 añade `work_lineage_integrity`, compañero inmutable por intento/generación
del binding managed. Su SHA-256 canónico vincula identidades, revisiones y
grants de hijo/receptor, job, contratos, snapshot, entrega, solicitud de origen,
idempotencia y nulos terminales explícitos. No se retrocertifica historia
managed v22: replay y revalidación de contrato fallan cerrados sin compañero;
el lineage legacy conserva lectura bajo su marcador.

La revalidación de contrato y el replay exigen las referencias exactas y
vigentes de sujeto/autorización, acción de admisión o `task_received`, job,
expiración, grant seleccionado y cadena viva sin ampliación. El replay rechaza
una sustitución corrupta del grant hijo o receptor antes de devolver el work,
sin añadir filas/eventos; el replay exacto sano sigue idempotente. La entrega
valida su propio `request_hash`, distinto del hash de solicitud WorkOrigin. El
replay no usa la validación de despacho del intento hijo terminal.

La revisión de fuente y la ejecución independiente del subconjunto v22/v23
idéntico al checkout dieron **16 passed, 45 deselected**; el gate de composición
del root registró PASS, con 4 contratos de arquitectura mantenidos. Informe:
`/tmp/caos-exec/T019/sol-stageA-v23-review.md`. Quedan fuera hijo nativo,
ACK autenticado del receptor, proveedor real, rutas públicas web/TUI y
atomicidad de efectos externos. T065 debe usar **v24+** para recuperación; la
referencia anterior a v22+ en etapa 3 queda superada por este nuevo piso.

### T019-R1: identidad verificada en la ruta legacy — 2026-09-25

**PASS del incremento acotado; T019 sigue `[ ]`.** La ruta
`POST /terminals/run-step` conserva su dependencia legacy de scopes. Sin auth,
un cliente remoto aún llega al servicio y se propaga `principal=None`; con
auth activa, se pasa exactamente el `Principal` verificado por el servidor.
`caller_id` continúa como metadato de terminal y no concede autoridad Work.
El nuevo argumento opcional al final de `run_agent_step` conserva la firma
posicional previa. Este corte no crea Work, grants, bindings, receipts ni
efectos nuevos de proveedor. La revisión independiente de fuente/diff es PASS;
el builder reportó 3 pruebas focales en verde tras el RED remoto 401 de R0.
No hubo pytest independiente por instrucción del encargo. Faltan vínculo
padre/hijo autenticado, grant propio, handoff y ACK Work verificable. Informe:
`/tmp/caos-exec/T019/sol-r1-review.md`.

### T020: bloqueo de contrato workflow/script revisado — 2026-09-23

Revisión independiente: **PASS del bloqueo; T020 `[ ]`**. Las rutas públicas
YAML/script crean `run_id` y filas del journal y ejecutan servicios legacy; no
aportan una identidad Work admitida. El intento script cercado por
`(run_id, step_id, generation TEXT, attempt_number)` no tiene asociación durable
con `WorkAttempt` ni con su generación `INTEGER`. YAML llama `run_agent_step`
directamente. Los IDs, variables de entorno, terminal y eventos del journal no
conceden job/grant/snapshot, autoridad de entrega ni ACK Work.

El contrato de entrada debe aportar identidad autorizada por intento y un
puente durable a `WorkAdmission` y al adapter registrado, con recuperación,
revocación, idempotencia e historia de reintentos definidas antes del RED de
T020. El dictamen de Astra propone ese port; no lo implementa. El gate focal
`test/services/test_step_contract.py` terminó entonces **exit 1: 15 failed,
16 passed** por `WorkRepository._verify` indexando como diccionario una fila
SQLite de tupla desde `workflow_journal`. La corrección regresiva T018 restauró
ese gate: **32 passed**, dentro del conjunto de 65 pruebas. Este GREEN acredita
el contrato de paso, no el vínculo ni historial Work por intento. Informe del
bloqueo original: `/tmp/caos-exec/T020/sol-block-review.md`.

### Convergencia de autoridad T093 — etapas 1–3 internas aceptadas parcialmente, 2026-09-24

**Corte histórico de etapas 1–3: T093 aún `[ ]` en esa revisión.** Para FR-003, FR-006
y SC-001, el operador autenticado por una interfaz administrativa interna es el
único provisionador de launch. Antes de entregar, crea un vínculo durable y
revalidable `(principal, selector) -> job, grant, contrato, snapshot`. Una
configuración de step sin fuente autorizada, incluida YAML/preasignación,
modelo, esfuerzo, permisos, límites o retry, queda bloqueada o explícitamente
desconocida; no se vuelve efectiva por inferencia.

Cada hijo y workflow tiene identidad verificable propia y grant explícito
limitado a job y acciones. Sólo el issuer/revoker autorizado concede o revoca;
no existe redelegación sin concesión posterior explícita. Nombres, sesiones y
caller IDs no prueban origen ni permiso de lectura. Sólo el adaptador/agente
receptor autenticado y registrado emite `task_received`: acredita aceptación
durable en ese receptor, no inicio ni resultado del proveedor. El recibo debe
vincularse a entrega, intento y generación vigente y resistir replay y origen
falso; texto de terminal o envío local no son ACK.

El contrato interno versionado de Astra está aprobado para implementación por
incrementos. La **etapa 1 tiene aceptación parcial**: modelos v1 strict/frozen,
migración aditiva verificada **v19** y provisión/retiro internos CAS con historial
inmutable por `(principal_id, selector, revision)`. Un Principal admin dueño
actual del job revalida grant/revisión y cadena viva, provider, contrato
canónico y snapshot antes de cada escritura; la reapertura SQLite reconstruye
sólo la revisión activa y vuelve a comprobar su evidencia. No hay backfill de
orígenes legacy ni downgrade. La regresión combinada independiente pasó
**80 pruebas** y
`project-composition-check` pasó con 4 contratos de arquitectura mantenidos.
La aserción histórica del ledger v18 se ajustó a la versión actual sin alterar
la prueba de conservación del historial previo.

La **etapa 2 tiene aceptación parcial**: migración aditiva verificada **v20**
para sujetos y autorizaciones de origen versionados, con acciones cerradas,
CAS e historial inmutable. El issuer del registro inicial permanece fijo en
revisiones posteriores: emitir/reemitir exige ese issuer y ownership del job;
resolver y la llamada directa a `WorkAuthority.delegate()` deniegan historia
incoherente. Sujetos gestionados sólo delegan con acción `delegate` vigente y
explícita; grants legacy sin sujeto gestionado mantienen su comportamiento.
Revocar sigue siendo posible tras expiración del grant. La regresión
independiente pasó **84 pruebas** y `project-composition-check` mantuvo 4
contratos sin roturas.

La **etapa 3 tiene aceptación parcial**: migración aditiva verificada **v21**,
binding durable de launch por intento/generación y UNIQUE por
`(principal_id, selector, idempotency_key)`. El runtime obtiene provisión
SQLite exacta sin `contexts` ni reconstrucción de Principal, sella el handoff
efectivo y revalida origen en las dos transacciones de admisión. El contrato
revalida el binding antes de dispatch/readiness/efecto; ausencia, contradicción,
retiro o reemplazo fallan cerrados. Sólo se permite replay exacto de una orden
legacy ya existente, sin crear una nueva. La matriz persistida Fix3 cubre
idempotencia tras cambio de job/revisión, corrupción, retiro/reemplazo entre
fases, rollback por etapa, carrera de replay y manipulación de `request_hash`;
la revisión independiente pasó **146 pruebas**, Black, 4 contratos de
importación y composición. Recovery debe usar **v22 o posterior**.

Esta etapa sólo enlaza la provisión con launch y admisión internos. Siguen
pendientes el setup y la entrada pública confiables, identidades operativas de
agentes, bindings de hijo/workflow, ACK `task_received` autenticado y proveedor
real. El guard anterior al I/O no implica atomicidad distribuida. La
aceptación no cerraba entonces T093 ni T017/T019/T020/T035; todos estaban `[ ]`.
Brief aprobado:
`/tmp/caos-exec/T093/approved-decision-brief.md`; contrato:
`/tmp/caos-exec/T093/astra-contract-design.md`; revisiones:
`/tmp/caos-exec/T093/sol-stage1-rereview.md` y
`/tmp/caos-exec/T093/sol-stage2-rereview.md` y
`/tmp/caos-exec/T093/sol-stage3-fix3-review.md`.

### T056: contrato DTO de proyección HTTP v1 — 2026-09-23

Revisión independiente: **PASS acotado; T056 `[x]`**. La nueva prueba valida
`work_contract_v1.json` con `WorkView` y `EventPage`, incluidos campos nullable
legacy y el `schema_version` que `WorkView` aporta al serializar. La ruta real
`GET /work-items/{id}` consulta SQLite temporal con entrega durable `running`;
su DTO mantiene `job_state`, `work_state` y `attempt_state` en `running`, muestra
`turn_state=processing`, deja `process_state=unknown` y `result_ref=null`, sin
declarar éxito a partir de observación del terminal. La fixture retira el override
de identidad al finalizar. No hubo cambio de producción.

Gate independiente: `.venv/bin/python -m pytest -o addopts= -q --no-cov
test/api/test_work_projection.py test/api/test_work_contract.py` → **11 passed**,
cuatro avisos de deprecación; `uv run black --check
test/api/test_work_projection.py`, `git -c core.whitespace=cr-at-eol diff
--check` y `project-composition-check "$(cat .ai/project-name)"` → **PASS**.
Composición analizó 260 archivos/945 dependencias: dos contratos conservados,
cero rotos. El RED conductual informado mutó temporalmente `process_state` a
`alive` y falló en HTTP; la fuente fue restaurada antes de este GREEN. Informe:
`/tmp/caos-exec/T056/sol-review.md`.

La prueba cubre el contrato HTTP v1 de un estado `running`; no acredita todas las
transiciones, MCP, web, TUI, e2e, proveedor real ni SC-007 global. T060–T063 y
O03 siguen abiertos. Graphify no se refresca: T056 añade sólo cobertura de prueba
y no cambia relaciones de producción; la decisión global de refresh queda en T089.

### T059: observación Herdr no nativa simulada — 2026-09-23

Revisión independiente: **PASS acotado; T059 `[x]`**. Herdr entrega texto opaco
del panel sólo cuando su estado nativo no está disponible. Lectura ausente,
vacía, malformada o fallida deja `UNKNOWN`; el proveedor registrado interpreta
la observación y sólo un `TerminalStatus` válido entra en el latch y la
publicación `terminal.{id}.status`. Una señal `PROCESSING` tardía no revierte
`COMPLETED` sin nuevo input. La compuerta de recibo impide publicar una
finalización visual con resultado aún no verificado. La proyección Work consulta
estado durable y no deriva ACK ni resultado del estado terminal.

Gate independiente: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest
-o addopts= --noconftest -q test/backends/test_herdr_work_status.py
test/services/test_status_monitor.py::TestGetStatusEventInbox
test/backends/test_herdr_backend.py::TestGetNativeStatus` → **17 passed**, un
aviso esperado de `asyncio_mode` por desactivar plugins; `project-composition-check
"$(cat .ai/project-name)"` → **PASS**, 260 archivos/946 dependencias, dos
contratos conservados y cero rotos. `git -c core.whitespace=cr-at-eol diff
--check` de los módulos revisados pasó. Dictamen:
`/tmp/caos-exec/T059/sol-review.md`.

La evidencia usa backend y proveedor simulados para la ruta no nativa. No
acredita Herdr real, todas las transiciones, clientes Web/TUI/MCP ni SC-007
global; T063 y O03 permanecen abiertos. Graphify no se refresca: la adición
local no cambia la topología de dependencias; el refresh global sigue en T089.

### T058: preflight de fábrica por descriptor — 2026-09-23

Revisión independiente: **PASS acotado; T058 `[x]`**. El incremento T058 añade
`ProviderDescriptor.preflight()` y una búsqueda de descriptor registrado para
que `ProviderManager.create_provider()` consulte `launch` antes de construir o
registrar la instancia. Sólo una selección solicitada y declarada
`not_applicable` provoca rechazo. `unverified` y `not_probed` no bloquean la
ruta existente; `mock_cli` y tipos ajenos al catálogo mantienen su ruta legacy.
El catálogo por operación/modelo/esfuerzo/enforcement es el baseline T027, no
un resultado nuevo de T058. La salida booleana y el orden de discovery siguen
en la ruta existente de `/agents/providers`.

Gate independiente: `.venv/bin/python -m pytest -o addopts= -q --no-cov
test/providers/test_provider_capabilities.py test/providers/test_provider_manager_unit.py`
→ **40 passed**. Una comprobación de los 13 descriptores confirmó que
`not_probed` no rechaza selecciones y que `mock_cli` no entra en el catálogo.
`git -c core.whitespace=cr-at-eol diff --check` sobre los archivos revisados y
`project-composition-check "$(cat .ai/project-name)"` → **PASS**; 260 archivos,
946 dependencias, dos contratos conservados y cero rotos. El RED centinela del
constructor de Copilot consta en `/tmp/caos-exec/T058/terra-report.md`; esta
revisión verificó su GREEN y el orden en la fuente. Dictamen:
`/tmp/caos-exec/T058/sol-review.md`.

El preflight no acredita instalación, autenticación, aceptación de modelo o
esfuerzo, aplicación de herramientas ni proveedor real; tampoco establece
selección/routing o compatibilidad entre pares. No se ejecutaron binarios de
proveedor ni pruebas live. Graphify existente orientó la fábrica y sus vecinos;
no se refresca en este cierre acotado: la nueva dependencia directa
manager→catálogo queda verificada en la fuente y el refresh global permanece
en T089.

### T060/T061: bloqueos de consumidores Web y TUI — 2026-09-23

Revisión de bloqueo, **sin aceptación; T060 y T061 permanecen `[ ]`**. T060
tiene una implementación parcial: `WorkView` v1, consulta explícita de
`/work-items/{id}` y badge que muestra `Running` para trabajo durable `running`
aunque el terminal sea `idle`. `TerminalView` sólo consulta Work cuando recibe
`workItemId`; sus llamadores actuales no entregan esa asociación. La prueba
React focal expresa el caso, pero Vitest no llegó a recogerla ni con `forks` ni
con `threads` (exit 137); el control preexistente tampoco se recogió. El único
experimento adicional autorizado, copiar físicamente Web y `node_modules` al
filesystem Linux temporal, agotó 90 s (exit 124) antes de ejecutar Vitest. No
se intentarán más variantes locales del runner. `npx tsc --noEmit --pretty false`
pasó (exit 0), exclusivamente como comprobación de tipos; el build Vite quedó
incompleto por timeout (exit 124). No hay RED/GREEN conductual ni build aprobado.

T061 no produjo cambio TUI ni prueba Rust y permanece `[ ]`. `WorkView` v1 no
incluye `terminal_id`, colección, contadores ni clasificación
foreground/background. Su `Terminal` sólo expone identidad, sesión y liveness;
`ServerClient::terminal` consulta `/terminals/{terminal_id}` sin cabecera
Authorization. La ruta owner-only `GET /work-items/{work_item_id}` exige ese ID
y un `Principal` verificado; en modo local loopback el servidor puede generar
el principal operador, pero la TUI sigue sin conocer el ID y no transporta
credencial cuando la autenticación está habilitada. El repositorio sí conserva
terminal→attempt→Work en `work_attempts` y vincula el terminal al intento ganador
en la transacción de envío, pero no publica una consulta inversa autorizada.
Tampoco se puede usar
`idle`, nombre, sesión o proveedor para inferir resultado, vínculo o propiedad;
la proyección Work actual usa el último intento, que puede diferir del intento
histórico asociado a un terminal.

RED reproducido: la aserción ejecutable sobre `WorkView.model_fields` que exige
`terminal_id` terminó con exit 1. La prueba Rust focal se intentó y terminó con
exit 127 (`cargo: command not found`); ningún gate Rust queda acreditado.
El owner técnico mínimo de la frontera pendiente es WorkRepository para la
asociación persistida, WorkQueries para lectura owner-only y snapshot, API para
el contrato versionado, y ServerClient/renderer para credenciales y consumo.
Antes de codificar contadores y foreground/background hay que definir la
colección autorizada, deduplicación por Work y semántica compartida; no se
presupone que deban ser campos nuevos de `WorkView`.

Condiciones para continuar: T060 necesita un entorno donde el test React focal
y su control se recojan y pasen con las dependencias/configuración declaradas,
además de una fuente explícita y autorizada de `workItemId` para integrar los
llamadores Web; T061 necesita una asociación autenticada y durable
terminal→`work_item_id` o una consulta autenticada terminal→`WorkView`, seguida
de pruebas Rust con fixture `terminal idle` + `work_state running` para
contador, foreground y background. Ninguno de estos bloqueos acredita T062,
T063, O03 ni SC-007 global. Informes:
`/tmp/caos-exec/T060/sol-block-review.md`,
`/tmp/caos-exec/T061/terra-report.md` y
`/tmp/caos-exec/T061/sol-block-review.md`. T093 registra las decisiones de
provisión/delegación pública; su contrato e implementación siguen pendientes y
no definen por sí mismos esta lectura TUI, su autenticación ni la colección
visual. No se registra otro gate externo
sin evidencia de una nueva política de autoridad.

### Cierre T060: observación manual Web por ID explícito — 2026-09-24

**PASS; T060 `[x]`.** Esta revisión sustituye el bloqueo Web descrito arriba.
`DashboardHome` y `AgentPanel` montan el mismo `TerminalView` sin `workItemId`;
el operador puede pegar un ID opaco en «Work item ID» y confirmar «Observe
work». Sólo el submit consulta `api.getWorkItem` una vez, tras quitar espacios
externos; la edición no consulta. La prop `workItemId`, cuando existe, prevalece
sobre la selección local. «Clear observed work» borra selección y resultado.
La API conserva el endpoint relativo y codifica el segmento del ID. La vista
separa «Observed work: <id>» de la identidad/liveness terminal y sólo muestra
estado durable si `schema_version === 1`, el ID devuelto coincide y el estado
Work es reconocido. Fallo de lectura o DTO inválido muestra «Work unavailable»
en región `aria-live="polite"`; selección, prop y terminal nuevos ocultan el
resultado previo y descartan respuestas tardías. Sin selección se conserva la
ruta legacy y no hay GET Work. La UI no persiste el ID, no cambia URL ni envía
input al terminal.

El RED de Luna falló por formulario ausente (1 fail/7 pass); su GREEN dio 15/15
y build Web exit 0. Esta revisión ejecutó `npm test -- src/test/work-state.test.tsx`
(15/15, exit 0), `npm run build` (exit 0) y
`project-composition-check "$(cat .ai/project-name)"` (4 contratos conservados,
0 rotos, exit 0). La composición revisó selección local→API→DTO→badge,
identidad obsoleta/error, los dos llamadores y el WebSocket terminal; los
escenarios de UI usan mocks porque no cambian la ruta HTTP ni la persistencia.
El endpoint owner-only y su autorización permanecen en el servidor existente.
No se acredita asociación automática terminal→Work, refresco continuo,
contadores ni SC-007 global. Evidencia de implementación:
`/tmp/caos-exec/T060/luna-manual-selection-report.md`; revisión independiente:
`/tmp/caos-exec/T060/sol-manual-selection-review.md`.

### T061: integración TUI por ID explícito — revisión parcial 2026-09-24

**PASS de esta etapa parcial; T061 permanece `[ ]` y no tiene aceptación completa.**
`cao tui --work-item-id <id>` selecciona un Work durable sin asociarlo a un
terminal. El parser conserva el arranque sin ID y rechaza ID faltante, vacío o
duplicado. La vía productiva entrega el ID a `Renderer`, que sólo consulta
`GET /work-items/{id}` cuando hay selección. `ServerClient` codifica el ID como
un segmento, exige schema v1 e identidad coincidente y conserva 404, 401/403 y
fallos de red como errores visibles. La vista separa estado durable Work de
readiness terminal; una lectura fallida aparece como `unavailable` y no como
éxito. No se añadió acceso directo a DB ni proveedor en esta vía.

Revisión independiente de fuente y pruebas: `cargo test --manifest-path
tui/Cargo.toml --bin cao-tui` → **204 passed**, exit 0; las dos suites
`hermeticity_tripwire` y `no_backend_attach_call` → **11 + 5 passed**, exit 0.
`git -c core.whitespace=cr-at-eol diff --check` sobre los cuatro archivos TUI
revisados → exit 0. `project-composition-check "$(cat .ai/project-name)"` →
**PASS**, 267 archivos, 986 dependencias, cuatro contratos conservados y cero
rotos. Las pruebas `endpoint_contract` de la ejecución amplia anterior no
pasaron: 3 fallaron por ausencia/timeouts de `cao-server` en 127.0.0.1:9889;
no se acreditan como PASS ni como contrato HTTP real verificado en esta revisión.

Gates pendientes para cerrar T061: `WorkView` v1/proyección/fixture no producen
contadores ni foreground/background de ejecución; hay que acordar y producir
ese contrato durable y demostrarlo con pruebas TUI. La TUI tampoco transporta
credenciales para lecturas remotas autenticadas; ese caso necesita contrato y
prueba propios. La selección actual hace una lectura inicial y expone un método
de refresco, pero no acredita seguimiento automático del Work seleccionado.
Tampoco se infiere asociación terminal→Work. La decisión de ID explícito y los
límites constan en `/tmp/caos-exec/T061/astra-identity-ruling.md`.

### T064/T065: revisión parcial de distribución y recuperación — 2026-09-23

**Sin cierre: T064 y T065 permanecen `[ ]`.** T064 añadió un caso HTTP que
propone la revisión 1 y comprueba que `review` con `expected_version=0`
devuelve 409 `knowledge_revision_conflict`. Las pruebas existentes cubren
revocación de grant, lectura/escritura denegadas y tombstone. La revisión
independiente ejecutó `test/api/test_knowledge_authority.py`: **23 passed**,
cuatro avisos, exit 0. La ruta de historial lee una revisión concreta; no hay
DTO, ruta ni contrato HTTP de listado paginado con cursor. T064 permanece
abierta por ese hueco; T067/T068 siguen pendientes para protocolo y retención.

### T067: cliente CAS parcial, sin checkpoint/cursor — 2026-09-23

**Sin cierre: T067 permanece `[ ]`.** `knowledge_propose_revision` añade un
cliente para el POST CAS ya existente de `/v1/knowledge/records/{record_id}/revisions`:
envía `schema_version=1`, `expected_version` y los selectores de autoridad,
usa autenticación y timeout existentes y rechaza redirects. Sólo el HTTP 409
con el sobre conocido `knowledge_revision_conflict` se convierte en
`KnowledgeRevisionConflict`; la excepción conserva código y acción requerida
sin copiar el mensaje remoto. La prueba aislada de ruta/CAS y sobre conocido
pasó (**2 passed, 9 deselected**) con plugins y conftest desactivados; el
runner pytest normal agotó su tiempo antes de dar evidencia de suite completa.

El helper aún no valida identificadores/versiones ni el cuerpo de éxito, y no
hay pruebas separadas de 409 falso, 3xx o integración ASGI. No existe endpoint
ni DTO servidor de checkpoint o página con cursor; tampoco están definidas sus
garantías de retención, caducidad y revocación. Por ello las pruebas con mocks
del cliente no acreditan sincronización extremo a extremo ni snapshot
consistente. Dictamen: `/tmp/caos-exec/T067/astra-protocol-ruling.md`; revisión
parcial: `/tmp/caos-exec/T067/sol-partial-review.md`.

T065 conserva `[ ]`: el RED provisional basado en snapshots y formato externos
inventados fue retirado; no queda prueba candidata ni módulo `recovery_bundle`.
La decisión humana fija captura offline v1. Un responsable operativo autorizado
debe demostrar quiescencia y retención de DB, memoria, artefactos y contenido de
entrega referenciado antes del corte. La implementación sólo valida esa
evidencia y los gates en fixtures; no detiene procesos externos. El restore v1
termina en un destino aislado y durablemente bloqueado para admisión, scheduler,
backend y proveedor. Conserva historia y deja intentos potencialmente activos
para conciliación explícita; reactivación queda fuera de v1. Si contenido
obligatorio contiene una credencial de proveedor, el export aborta con
diagnóstico redactado: no exporta secretos ni altera bytes históricos hashados.

El siguiente artefacto, a cargo de Astra, es un contrato interno versionado
de corte, cierre de referencias, verificación, restore bloqueado,
compatibilidad/migración y matriz RED/GREEN con productores reales en SQLite
temporal. Terra implementará sólo
después de ese dictamen. La decisión no acredita implementación, corte
consistente ni restore probado. T065 y T069–T071 siguen abiertas. Brief
aprobado: `/tmp/caos-exec/T093/approved-decision-brief.md`;
dictamen: `/tmp/caos-exec/T065/astra-recovery-contract-ruling.md`;
revisión parcial: `/tmp/caos-exec/T064/sol-partial-review.md`.

### T065: base de contexto recovery v24 revisada — 2026-09-24

**Incremento parcial PASS; T065 permanece `[ ]`.** La migración aditiva v24
instala un único contexto `normal` con UUID de instalación en la transacción del
ledger, conserva filas e historial 1–23 y mantiene la capacidad de integridad
lineage v23 sin certificar retrospectivamente lineage managed anterior. La
verificación del esquema y `WorkRepository.assert_execution_allowed` rechazan
contexto ausente, duplicado, alterado, con versión/estado desconocido o
`blocked_restore`. La transición interna a `blocked_restore` es unidireccional;
no se añadió endpoint, ingreso, desbloqueo ni autorización por digest.

Revisión independiente de fuente y composición: `init_db()` invoca la
inicialización v24; ninguna ruta de ejecución consume todavía el nuevo guard.
La prueba focal de migraciones y guard pasó 29/29, la regresión de migraciones y
repositorio 46/46 y el caso focal de lineage v23 1/1; Import Linter mantuvo
4/4 contratos. La reproducción independiente de `project-composition-check`
dio PASS y `git diff --check` pasó. El intento de revisión interactiva
`project-composition-review` reportado por el builder no arrancó por falta de
TTY; se hizo análisis de composición read-only. `review.diff` es inventario
manual: estos paths ya eran no rastreados y no existe baseline Git para atribuir
el contenido previo al incremento.

Permanecen pendientes capture/verify/restore, catálogo completo de stores y
referencias, inspector de secretos y conexión del guard a admisión, scheduler,
dispatch, retry, backend, proveedor e ingresos legacy. La agrupación
lineage+delivery no tiene exit final acreditado; no se declara PASS global ni
restore seguro. T065 y T069–T071 siguen abiertas.

### T065-B: inventario SQLite v24 revisado — 2026-09-24

**PASS parcial del catálogo; T065 permanece `[ ]`.** `recovery_inventory`
inspecciona sin escribir el SQLite creado por `init_db()` y acepta sólo el perfil
v24 revisado: 61 tablas de usuario (45 Work y 16 legacy), multiconjunto exacto
de 105 FK (162 componentes) y 23 descriptores de referencias. La extracción
conserva orden, acciones, MATCH observado y multiplicidad; una tabla o FK
ajena o ausente falla con `recovery inventory incompatible`. El DDL Work,
ledger y contexto continúan bajo `_verify`; las tablas legacy requieren sus
columnas conocidas y cero FK. Según el dictamen Astra, PRAGMA no expone
deferrability ni el MATCH declarado: esa garantía Work procede del DDL
verificado. Seis relaciones SQLite tienen origen, destino y columnas mapeadas;
la relación de memoria ambigua exige perfil futuro, sin fingir una FK compuesta.

Revisión independiente: `test/services/test_recovery_bundle.py` **8 passed**;
migración y guard **29 passed**; `project-composition-check` **PASS** (4 contratos)
y `git -c core.whitespace=cr-at-eol diff --check` exit 0. Una reproducción
aparte confirmó 61/105/162/23 y que añadir una FK a `flows` se rechaza. El
catálogo clasifica paths/bytes sin seguirlos ni resolverlos: faltan cierre físico
de referencias, inspector de credenciales, capture/verify/restore y conexión
del guard a efectos e ingresos. No acredita corte, bundle ni restore seguro.
Dictamen FK: `/tmp/caos-exec/T065/astra-catalog-fk-ruling.md`; revisión:
`/tmp/caos-exec/T065/catalog-sol-review.md`.

### T068: revisión independiente de cursor de conocimiento — 2026-09-23

**FAIL; T068 permanece `[ ]`.** La migración v17 añade estado operacional de
cursores con hash SHA-256 y FK compuesta al grant; la ruta
`GET /v1/knowledge/recovery` devuelve revisiones ordenadas por keyset BINARY,
checkpoint por ámbito, tombstones sin contenido y 410/422/403 sanitizados.
Graphify (grafo existente, 30 458 nodos) localizó las fronteras; se verificaron
contra `KnowledgeRevisions`, `MemoryService`, `KnowledgePolicy`, la ruta y SQLite.

Gates independientes: recuperación/migración **10 passed**, política/revisiones/
autoridad/migraciones **82 passed**, integración memoria/contexto **26 passed**,
todos exit 0; `project-composition-check "$(cat .ai/project-name)"` **PASS**,
260 archivos, 946 dependencias, 2 contratos conservados y 0 rotos. La revisión
de composición contrastó transacción, revocación, persistencia, API y migración
con SQLite real y ASGI; no acredita HA, backup/restore ni consumo cliente T067.

Bloqueo reproducido con SQLite real: `recovery_page` lee `self.clock()` antes de
`BEGIN IMMEDIATE`. Una continuación esperó un bloqueo; el cursor vencía en
`101`, el reloj pasó a `102` antes de adquirir la transacción y aun así se
entregó la segunda revisión. La caducidad debe comprobarse con tiempo vigente
una vez adquirida la transacción y quedar cubierta por una prueba de espera.
No se editó código ni tests en esta revisión. Informe:
`/tmp/caos-exec/T068/sol-review.md`. T064/T067/T069/T072 no se cierran por
este trabajo.

### T068: aceptación tras reparación del TTL — 2026-09-23

**PASS; T068 queda `[x]`.** El FAIL anterior permanece arriba como historia de
revisión. `KnowledgeRevisions.recovery_page` ahora toma y valida el reloj
dentro de `BEGIN IMMEDIATE`, después de verificar esquema, reautorizar `read`
y calcular el checkpoint, antes de cargar el cursor. Mi reproducción
independiente con SQLite real y dos hilos dio `expires_at=101`, reloj de
continuación `102`, resultado `expired` y keyset persistido en `('first', 1)`;
no se devolvió ninguna página ni avanzó el cursor.

Las nuevas pruebas cubren esa espera SQLite, otro scope, otra identidad/grant,
formato `kcr2` y rollback de auditoría al fallar el INSERT durable del cursor.
El binding comprobado en fuente liga principal, scope/scope_id, job, grant,
revisión y límite al token SHA-256 persistido. La migración v17, historia
inmutable, keyset BINARY, tombstones, checkpoint por scope, 410/422/403 y
reautorización por grant conservan los límites del dictamen Astra. Graphify
existente se contrastó otra vez con las rutas concretas de fuente.

Gate independiente: nueve módulos de autoridad, recuperación, revisiones,
policy, migración y contexto: **121 passed**, cuatro avisos, exit 0;
`py_compile` exit 0; `project-composition-check "$(cat .ai/project-name)"`
**PASS**, 260 archivos, 946 dependencias, dos contratos conservados y cero
rotos. La composición sobre SQLite real y ASGI queda aceptada para autoridad
única. No se infiere cliente T067, cobertura global T064, backup/restore T069
ni HA T072; esas tareas permanecen abiertas. Informe:
`/tmp/caos-exec/T068/sol-rereview.md`.

### T064: contrato HTTP de autoridad y cursor aceptado — 2026-09-23

**PASS; T064 queda `[x]`.** El caso ASGI/SQLite
`test_http_authority_contract_rejects_stale_cas_hides_tombstone_and_revoked_cursor`
comprueba en una secuencia propuesta, `review` con versión esperada obsoleta
(409 `knowledge_revision_conflict`), `review` válido y tombstone con contenido
nulo, primera página de recuperación con tombstone y cursor, y continuación
denegada tras revocar el grant (403 `knowledge_forbidden`). La ruta devuelve
un error fijo sin copiar el cursor. La fuente reautoriza `read` en la transacción
antes de cargar el cursor y oculta el contenido tombstoned en `_read`.

Gate independiente: `timeout 180s uv run pytest -o addopts= test/api/test_knowledge_authority.py test/api/test_knowledge_recovery.py -q`
→ **31 passed**, cuatro avisos de deprecación, exit 0. El RED conductual de
Terra mutó temporalmente la reautorización y obtuvo 200 frente al 403 exigido;
la mutación se restauró. El cierre acredita el contrato HTTP de T064, sin
atribuir cliente T067, backup/restore T065/T069–T071 ni HA T072. Informe:
`/tmp/caos-exec/T064/sol-final-review.md`.

### T067: cliente remoto CAS y recovery aceptado — 2026-09-23

**PASS; T067 queda `[x]`.** `knowledge_propose_revision` usa el POST CAS real
`/v1/knowledge/records/{record_id}/revisions` con selectores de grant y
`expected_version`; `knowledge_recovery_page` usa el GET v1 real con scope,
límite y cursor opaco. Ambos fijan una autoridad por solicitud, autentican,
limitan el tiempo, deshabilitan redirects y rechazan 3xx. Sólo los envelopes
canónicos 409/410 se tipan; 410 exige reinicio explícito. Un fallo o partición
no activa retry, merge, avance de versión ni escritura/fallback local.

La revisión independiente contrastó URL, entradas y DTOs con ruta, servicio,
`KnowledgeRevision` y DDL: rechaza esquema mayor, JSON/DTO incompleto, hashes
no SHA-256, `fresh_until=1e15`, cursor/identificadores inválidos y URLs de
autoridad sin hostname, con userinfo, delimitadores, controles o estructura
que `requests` no puede preparar. Conserva localhost, IPv4/IPv6, puerto 0,
prefijo de path y puntos internos del ID; `.` y `..` se rechazan como segmentos.
Los rechazos R1–R4 quedan como historia, corregidos en R5.

Gate propio: `test/services/test_memory_gateway.py` **62 passed in 35.06s**,
exit 0; `project-composition-check "$(cat .ai/project-name)"` **PASS**, 260
archivos, 947 dependencias, dos contratos conservados y cero rotos; diff
whitespace focal limpio. Además, un ejercicio independiente gateway→ASGI
con SQLite y grant durables recorrió propuesta 201, CAS obsoleto 409,
recovery 200 y cursor expirado 410, usando serialización real de `requests`
y transporte ASGI local sin red. No es un test persistido en el repositorio.

El cierre es de cliente para una autoridad única. No acredita snapshot
multinodo, HA T072, backup/restore T065/T069–T071, integración visual
T060/T061 ni contrato script T074. La autoridad de grants, retención y
revocación sigue en servidor (T064/T068). Informe:
`/tmp/caos-exec/T067/sol-r5-review.md`.

### T047/T048: REDs de US4 preparados — 2026-09-23

**PASS de preparación; T047 y T048 permanecen `[ ]`.** Las seis pruebas nuevas
fallan deliberadamente al importar `services.work_continuation` y
`services.work_decisions`, todavía ausentes. Antes de ese punto construyen en
SQLite temporal principal efectivo, grant vigente, snapshot congelado y
binding durable revalidado. T047 altera por separado integridad del paquete,
versión de esquema y disponibilidad de artefacto; el principal/grant quedan
fuera del paquete y ningún hash concede autoridad.

T048 conserva la primera decisión antes de preparar, sólo en la fixture, un
intento de nueva generación para el mismo work item. La revisión nueva tiene
snapshot y contrato distintos, usa autorización, retry, bind y revalidación
reales, y mantiene intacto el binding histórico. El RED exige que la decisión
antigua no se consuma en la revisión nueva y que una decisión nueva produzca un
solo claim observable. Esa preparación SQLite no acredita replanificación
productiva, cese externo ni atomicidad de un efecto externo.

Gate independiente: pytest focal combinado **6 failed in 27.23s**, exit 1,
únicamente por los dos módulos futuros ausentes; el constructor de revisión
comprobó mismo work item, intento nuevo, generación incrementada y hash distinto.
T049–T055 siguen pendientes de implementación, GREEN, fencing y composición.
Informe: `/tmp/caos-exec/T047-T048/sol-red-r3-review.md`.

### T049: decisión humana durable — 2026-09-23

**PASS independiente; T049 `[x]`.** `WorkDecisions` conserva actor, fecha,
evidencia, razón, efectos y `contract_hash` en la migración aditiva v18. Replay
idéntico devuelve la primera decisión sin nueva auditoría; payload divergente
falla. Cada efecto se reclama una sola vez bajo la misma transacción que
revalidación de binding, autorización del principal y evento. Revocación
write-once bloquea claims futuros y conserva decisión/claims previos.

El RED T048 era conductual: fallaba por el módulo ausente después de construir
principal, grant, snapshot y binding reales. Gate independiente: 33 pruebas
focales/migración aprobadas; composición PASS (262 archivos, 951 dependencias,
dos contratos conservados, cero rotos). En SQLite temporal, UPDATE/DELETE en
las tres tablas quedaron bloqueados; scope de sólo lectura no revoca; una
carrera revocación→claim dejó cero claims. El límite es recibo local
at-most-once, sin efecto externo ni fencing de proveedor. T047/T048 conservan
su estado de preparación y T050–T055 permanecen abiertas. Graphify requiere
refresh por la nueva frontera de servicio/esquema; el grafo existente orientó
la revisión y se contrastó con fuente. Informe:
`/tmp/caos-exec/T049/sol-review.md`.

### T050: exportación acotada de continuidad revisada — 2026-09-23

**PASS independiente; T050 `[x]` sólo para exportación.** Un paquete v1 canónico
describe el intento vivo, el contrato y snapshot por ID/hash, refs verificadas
del intento exacto y la clasificación conservadora pending/uncertain; no lleva
autoridad, configuración, secretos ni bytes. Binding, actor y grant se comprueban
en una misma lectura SQLite de sólo lectura. El hash del paquete sólo acredita
integridad. No hay importación, sustitución, proveedor, fencing ni reanudación.

Prueba export focal: 1 passed. Probes independientes con SQLite temporal
confirmaron determinismo, cero escritura/lectura de bytes, rechazo de grant
revocado, refs fuera de límite y estados terminales. El archivo de pruebas
completo sigue con tres REDs de importación por API ausente; son T047/T051,
que permanecen `[ ]`, igual que T052. Composición PASS (263 archivos, 955
dependencias, dos contratos conservados, cero rotos). Graphify existente se
consultó y se contrastó con fuente; el refresh global tras T049 agotó cinco
minutos y sigue pendiente en T089 junto con la nueva frontera T050. Informe:
`/tmp/caos-exec/T050/sol-review.md`.

### T047/T051: preflight de importación v1 revisado — 2026-09-23

**PASS independiente; T047 y T051 `[x]`.** Los tres REDs T047 ya son GREEN:
paquete alterado, versión desconocida y artefacto ausente rechazan antes de
efectos. El parser exige JSON UTF-8 canónico de hasta 32 KiB y shape v1 exacto;
la misma lectura SQLite revalida fuente, principal y grant y recompone el
payload. Cada ref se lee y verifica por bytes. Replay válido devuelve un DTO
congelado sin escritura, destino ni autoridad futura. Prueba focal: 12 passed;
regresiones vecinas: 64 passed; composición PASS (263 archivos, 955 dependencias,
dos contratos conservados, cero rotos). Probe temporal ubicó el primer rechazo
en parseo, recomputación o lectura de bytes y confirmó estado idéntico.
**T052 permanece `[ ]`** para fencing/cese y sustitución ejecutable. Refresh de
Graphify diferido a T089. Informe: `/tmp/caos-exec/T051/sol-review.md`.

### T048: pruebas de decisiones humanas reconciliadas — 2026-09-23

**PASS independiente; T048 `[x]`.** Los tests de `test_work_decisions.py`
comprueban replay idéntico sin duplicar decisión, auditoría ni efecto local;
revisión nueva del mismo work item con intento, generación, snapshot y contrato
distintos, sin consumo de la decisión anterior; y revocación durable que impide
claims nuevos y conserva el historial. La fixture usa principal, grant,
snapshot, retry, binding y revalidación reales sobre SQLite temporal.

Gate fresco sin cobertura heredada: `uv run pytest -o addopts= --no-cov -q
test/services/test_work_decisions.py test/clients/test_work_migrations.py`
→ **33 passed in 12.81s**, exit 0. En fuente, `WorkDecisions` revalida
binding y grant dentro de la transacción, registra decisión/claim/revocación
durables y las tablas v18 tienen claves y triggers de inmutabilidad.
Este cierre acredita las pruebas T048/FR-016 para el recibo local; no acredita
efecto externo, fencing de proveedor, replanificación productiva, cancelación
de hijos ni el cierre completo de US4. Informe:
`/tmp/caos-exec/T048-reconcile/sol-review.md`.

### T052: cese confirmado antes de reemplazo — 2026-09-23

**PASS independiente; T052 `[x]` para la ruta interna de sustitución.**
`WorkService.retry_work` exige reconciliación autorizada y una reserva held del
intento exacto. `WorkScheduler.release_for_replacement` verifica cese irreversible
fuera de SQLite, revalida reserva, intento, generación y revisión del work en un
commit nuevo, guarda la referencia de prueba y libera la plaza. Sólo entonces
el repositorio puede crear la generación siguiente con CAS sobre la revisión
devuelta por ese commit. Una pausa por cuota, lease vencido o cleanup no prueban
cese. Si resultado o cancelación ganan una carrera, no se crea sustituto; si el
retry falla tras release, la prueba y liberación durables permanecen.

El RED conductual inicial y las seis variantes de cobertura del dictamen están
en `test_work_replacement_fencing.py`. Revisión fresca: **19 focales y 51
regresiones aprobadas**; composición PASS (263 archivos, 956 dependencias, dos
contratos conservados, cero rotos). No hay enqueue, claim, send ni reanudación
automática en esta ruta; la nueva generación requiere admisión posterior y no
reembolsa unidades consumidas. La recuperación del hueco entre release y retry
queda fuera de T052. El commit local no autorizado `2c83216b` se conserva para
resolución del coordinador; no se hizo otro commit. Informe:
`/tmp/caos-exec/T052/sol-review.md`.

### T053: decisiones durables por API/MCP y handoff AG-UI ligado — 2026-09-23

**PASS independiente; T053 `[x]` en su alcance acotado.** API crea y revoca
decisiones a través de `WorkDecisions` usando sólo `get_current_principal`;
rechaza autoridad inyectada por el cuerpo. MCP usa HTTP y la credencial efectiva
MCP→API, sin acceso directo al ledger ni propagación independiente de identidad
humana. Una petición API y MCP con el mismo principal, payload y clave converge
en una decisión y un evento. La revocación conserva historia y bloquea claims
futuros.

Los interrupts automáticos de `ApprovalBridge` siguen sin binding durable. El
registro interno explícito valida `WorkContracts`, fija interrupt/terminal/
proveedor y exige principal verificado al resume. En el camino ligado, lock por
terminal precede decisión y claim; `consume_effect` revalida autoridad y hace
el claim local antes del I/O, sin transacción SQLite durante entrega. Replay
idéntico no reentrega y petición divergente produce conflicto. El primer fallo
post-claim y su replay devuelven 502 estructurado/no reintentable; cancelación
del task interno conserva el claim y cerca el replay. TTL/cap elimina estado
auxiliar local al expulsar un interrupt terminal sin borrar decisión ni claim.

Gate fresco: **32 pruebas focales y 68 regresiones handoff/bridge aprobadas**;
las 25 de cobertura AG-UI pasaron con el extra declarado `agui`; la regresión
combinada con ese extra terminó con **93 passed**. Composición
PASS (263 archivos, 962 dependencias, dos contratos conservados, cero rotos).
Probes independientes: cancelación post-claim y replay mantuvieron un claim y
una entrega; expulsión por cap borró binding/fingerprint y conservó una
decisión y un claim. Sin proveedor real ni garantía de ejecución externa
exactly-once. No existe aún llamada productiva de registro desde un work
upstream, por lo que la ruta automática sigue UI/terminal-only; cancelación de
hijos T054 y continuidad T055 permanecen abiertas. Informe:
`/tmp/caos-exec/T053/sol-review.md`.

### T054: cascada de cancelación y recuperación heredada aceptadas — 2026-09-23

**PASS independiente; T054 `[x]`.** `WorkRepository` posee el CAS y la
transacción SQLite que cancelan raíz y descendientes vivos, escribiendo estados,
eventos y recibos juntos. Recorre nietos bajo nodos terminales sin modificar
un hijo `succeeded` ni su resultado aceptado. Un RED conductual previo a
producción mostró el hijo aún `running`; el GREEN inicial reveló un defecto de
recuperación: una raíz ya cancelada podía conservar un hijo activo tras restart.
El RED de follow-up reprodujo ese caso antes de la reparación.

La operación explícita `reconcile_cancelled_descendants` valida raíz, intento
vigente, generación y estados cancelados bajo `BEGIN IMMEDIATE`, y el replay
de un recibo de cancelación comprueba primero su fingerprint antes de usar la
misma recuperación. La raíz y los terminales se conservan; repetir tras otro
restart es no-op para filas, eventos y recibos. Tras el commit, un resultado
registrado antes no puede ganar finish ni obtener `accepted_result_id`.
Cleanup conserva su ciclo independiente y `pending` no dispara callback
automático.

Revisión fresca: **183 pruebas aprobadas** de cancelación, repositorio,
servicio, reducer, admisión, scheduler, reservas y dispatch con
`-o addopts=''`; Import Linter conservó dos contratos y composición dio PASS.
Un probe SQLite independiente inyectó fallo en un evento hijo: rollback exacto
de árbol/eventos y reintento completo. La garantía de recuperación empieza en
su commit; antes de él un hijo histórico puede terminar. Ni la cancelación
lógica ni cleanup acreditan cese físico o exactly-once externo. T055 continúa
abierta. Informe: `/tmp/caos-exec/T054/sol-review.md`.

### T055: continuidad descriptiva de resultado completado aceptada — 2026-09-23

**PASS independiente; T055 `[x]`.** El adaptador falso A completa un trabajo por
`WorkService.dispatch` y `settle_attempt`; el finish CAS deja el intento
`finished`, el trabajo `succeeded` y fija `accepted_result_id` al `result_id`
del intento. La ruta separada `_revalidate_continuation` permite describir ese
terminal tras revalidar generación, binding, contrato, snapshot, job, grant y
lease. `_revalidate_order` y el dispatch ejecutable siguen rechazando el
intento terminado.

El adaptador falso B, con otra identidad de instancia y el mismo proveedor
autorizado, reabre SQLite y el almacén durable para importar/repetir el paquete
y leer los bytes exactos del ganador. El import relee referencias y hashes; no
escribe filas, revisiones, eventos ni artefactos y B no envía, ejecuta ni llama
al callback. Paquetes manipulados aun rehasheados, resultado no ganador,
ausencia del ganador CAS, revocación posterior al primer import y corrupción de
bytes de igual longitud se rechazan sin efectos.

Revisión fresca: **174 pruebas aprobadas** en e2e, continuación, resultado,
servicio, contrato, repositorio, scheduler, reservas y dispatch/API con
`uv run pytest -q -o addopts=''`; `project-composition-check` PASS, con dos
contratos Import Linter conservados y cero rotos. El alcance es continuidad
local descriptiva entre adaptadores falsos; la lectura conserva el lease como
límite de frescura. No se acredita proveedor real ni exactly-once externo.
Informe: `/tmp/caos-exec/T055/sol-review.md`.

### T079: límites de importación de Work revisados parcialmente — 2026-09-23

**PASS parcial independiente; T079 permanece `[ ]`.** `.importlinter` añade
`work-reducer-pure` y `work-repository-entry-runtime-boundary` a los dos
contratos previos. La fuente confirma que `work_reducer` sólo importa el modelo
Work y Pydantic; `WorkRepository` importa los esquemas de `clients` y ese
reducer, y delega en él las transiciones de intento, trabajo, cancelación y
retry. Los contratos prohíben entradas, proveedores, backends y owners de
efectos concretos para estos dos módulos sin prohibir la dependencia legítima
repositorio → reducer ni los esquemas hermanos. API, `WorkService` y
`WorkAdmission` conservan su dirección hacia el repositorio.

La revisión de fuente mantiene separados los recibos de hijos nativos en
`clients.database`, el ciclo run/step de `workflow_journal` y las revisiones de
conocimiento en `KnowledgeRevisions`. Gate fresco: `uv run lint-imports --config
.importlinter --no-cache` exit 0, **4 contratos conservados**, 263 archivos y
962 dependencias; suite focal Work/reducer/repositorio/servicio/admisión/
delegación, **122 passed** (exit 0); `project-composition-check
"$(cat .ai/project-name)"` exit 0, **PASS**. El grafo existente orientó la
inspección y se contrastó con fuente; su refresh global sigue diferido a T089.

Estos contratos prueban dirección de imports, no dueño exclusivo de escrituras
ni equivalencia de transiciones. La auditoría de todos los ciclos y posibles
duplicaciones depende de extracciones pendientes, incluida T074 parcial; no
hay cierre global de FR-017/T079. Informe:
`/tmp/caos-exec/T079/sol-partial-review.md`.

### Cierre T061: proyección semántica de Work observado — 2026-09-24

**PASS independiente; T061 `[x]`.** Este cierre sustituye el estado parcial
de T061 descrito arriba. `cao tui --work-item-id <id>` selecciona explícitamente
un Work durable, lee `GET /work-items/{id}` v1 y valida identidad y versión;
sin selección no solicita Work. Web conserva la selección manual de T060.
Ninguno de los clientes deriva Work desde el terminal, URL o localStorage.

Los siete estados Work comparten etiqueta, rol semántico y clasificación de
contadores en `design-tokens/status.json`, generados para Web y TUI. Cada cliente
resuelve su paleta: Web aplica primer plano y fondo; TUI sólo aplica primer
plano Ratatui, conserva el fondo del operador y respeta `NO_COLOR`. Los
contadores «Observed running» y «Observed succeeded» miden únicamente el Work
seleccionado: `(1,0)` para running, `(0,1)` para succeeded, `(0,0)` para los
otros estados válidos. Ausencia de selección no afirma totales; carga, error,
versión/estado inválidos o revisión contradictoria muestran `Unknown` y `—`.
Una revisión inferior se descarta. Idle/processing del terminal y el estado
del intento permanecen independientes del estado durable de Work. No se
añadieron campos al `WorkView` v1 ni a los bodies HTTP del fixture.

Gates frescos de la revisión: Node contrato **2/2**; generador sin drift;
build Web PASS; Rust Work **15/15**, revisión **1/1**, hermeticidad **11/11**,
attach **5/5** y colores **7/7**; composición PASS (**268 archivos, 987
dependencias, cuatro contratos conservados**); whitespace PASS. El test DOM
focal, ejecutado en copia ext4 cuyo contenido crítico y lockfiles coinciden
por SHA256 con el workspace, pasó **28/28**. El contrato Python focal en la
misma copia pasó **2/2** con cuatro avisos de deprecación. Ambos gates de copia
terminaron con código 0; el montaje Windows/9p había bloqueado intentos
anteriores antes de ejecutar pruebas. Los `endpoint_contract` de Rust que
requieren un `cao-server` separado no se ejecutaron por el límite de este
incremento y no constan como PASS.

Límites: los contadores son sólo de la selección observada, no totales del
job/sistema; TUI no transporta credencial remota ni promete seguimiento
continuo. El cierre no acredita la proyección global SC-007, los consumidores
CLI/MCP T062 ni el backend real T063. No se modificaron Graphify ni vault.
Informe: `/tmp/caos-exec/T061/sol-final-review.md`.

### Cierre T062: lectores CLI/MCP y tokens de Work — 2026-09-24

**PASS independiente; T062 `[x]`.** `cao workflow work WORK_ITEM_ID` y
`cao workflow work-events JOB_ID` son lecturas HTTP de `/work-items/{id}` y
`/jobs/{id}/events`. `--json` conserva el `WorkView` o `EventPage` de la API;
la vista humana separa trabajo, intento, turno, proceso, resultado y acción, y
en eventos expone cursor, high-water y gaps. La paginación se valida antes de
solicitarla. El bearer configurado se obtiene sólo con `get_local_bearer`;
errores de API/transporte son acotados y no reflejan cuerpo ni secreto. La
corrección final valida ambos ID con el mismo alfabeto acotado que MCP antes de
leer el bearer o construir la URL. Click real rechaza `a?x=1` en ambos verbos
sin petición y conserva `/work-items/a_1-2` y `/jobs/a_1-2/events` para el ID
válido. MCP sigue leyendo el DTO por HTTP autenticado; API conserva autoridad
de estado. Los tokens Web/TUI se generan desde `design-tokens/status.json` y
coinciden con el fixture común sin añadir campos visuales al DTO v1.

Gates frescos: suite CLI/MCP/API de T062 **34 passed** en copia ext4 cuya
fuente crítica, pruebas, fixture y lockfiles coincidieron por SHA-256 con el
checkout; Node contrato **2 passed** y `gen.mjs --check` sin drift en fuente;
`project-composition-check` del checkout **PASS**, 269 archivos, 992
dependencias, cuatro contratos conservados y cero rotos; diff scoped sin
diagnósticos de whitespace. El intento `timeout 20s uv run pytest -q
test/cli/commands/test_work_queries.py` en el checkout terminó 124 tras emitir
16 puntos y un aviso de coverage: montaje OneDrive/P9 bloqueado, no PASS de
fuente. La copia no prueba un proveedor/backend real ni la transición universal
de T063; SC-007 global permanece abierta. Revisión y hashes:
`/tmp/caos-exec/T062/sol-final-review.md`.

### T069: revisión independiente de captura offline v1 — 2026-09-24

**FAIL de aceptación; T069 permanece `[ ]`.** La implementación nueva hace una
copia SQLite consistente y privada, comprueba el inventario v24 estricto del
origen y el inventario portátil de la copia, copia las referencias externas
enumeradas, verifica hashes/tamaños y publica sólo después de la verificación.
La copia conserva la identidad histórica y `WorkRepository._verify(copia)` la
rechaza como store ejecutable. El verificador de corte es una interfaz interna;
ningún verificador concreto de evidencia offline está conectado.

La garantía de no exportar credenciales falla: una reproducción independiente
publicó con `ImmutableResultStore` un resultado referenciado que contenía una
credencial reconocible por `secret_gate`; `capture` lo incorporó y
`verify_recovery_bundle` aceptó el bundle. La inspección de secretos recorre
celdas SQLite, pero no el contenido externo. Además, el productor real guarda
snapshots de delegación como BLOB SQLite y el capturador rechaza todos los BLOB;
no hay prueba de captura positiva de snapshot. La memoria con filas y rutas
legacy sigue `requires_future_profile` y se rechaza. Delivery, evidencia de
worktree, lineage y referencias de snapshot/knowledge no tienen pruebas de
cierre extremo a extremo con sus productores reales; la prueba positiva usa
sólo un resultado externo. La copia SQLite usa `backup`, sin compactación ni
inspección de páginas libres del objeto final, por lo que tampoco acredita la
ausencia de secretos borrados.

Gates frescos: recuperación/migración focal **49 passed**; composición/import
linter **PASS**, 270 archivos, 996 dependencias, cuatro contratos conservados.
`black --check` e `isort --check-only` fallaron en los tres archivos revisados;
son gates del CI del proyecto. `ruff` no figura en dependencias ni en el lint
del CI y su ausencia no se declara como gate fallido del proyecto. El grafo
existente no contiene los módulos nuevos; los límites anteriores se
contrastaron directamente con fuente y productores. No se modificó código,
pruebas, Graphify ni vault. Informe y reproducción:
`/tmp/caos-exec/T069/sol-review.md`.

### T069: re-revisión de la corrección parcial — 2026-09-24

**FAIL de aceptación; T069 permanece `[ ]`.** La corrección sí rechaza antes
de publicar un resultado UTF-8 referenciado de `ImmutableResultStore` que
contiene `api_key=...`, acepta el BLOB UTF-8 limpio producido por
`DelegationSnapshots.freeze()` sólo en `work_delegation_snapshots.content` y
rechaza su variante no UTF-8. Ahora hace `VACUUM INTO` en staging privado,
reinspecciona inventario y celdas de la copia compactada antes de hashearla;
la prueba de páginas libres comprueba que el secreto borrado no aparece en el
objeto publicado y que la fuente no cambia.

Persiste una frontera incorrecta: `_scan_utf8_bytes` considera inspeccionable
todo byte que decodifique como UTF-8. Una reproducción independiente con el
productor real registró `00 01 02 03` como resultado; `capture` publicó el
objeto binario, conservó esos bytes y `verify_recovery_bundle` lo aceptó.
Por ello no está acreditado el rechazo de binarios/no inspeccionables. Los
gates frescos pasaron: **53 pruebas** de recuperación/migración con cuatro
workers, Black, isort y composición/Import Linter (270 archivos, 996
dependencias, 4/4 contratos). El check global de whitespace no es utilizable
como gate de T069 por cambios CRLF ajenos; los tres paths revisados no
emitieron diagnósticos en el check aislado.

La corrección tampoco acredita backup de memoria, productores reales de
delivery/worktree/lineage ni un verificador de corte concreto server-owned.
No se infiere restore T070. Informe y reproducción:
`/tmp/caos-exec/T069/sol-correction-review.md`.

### T069: re-revisión independiente del gate binario — 2026-09-24

**PASS parcial del gate binario; FAIL de aceptación; T069 permanece `[ ]`.**
`_scan_utf8_bytes` rechaza ahora NUL, controles y DEL aunque decodifiquen como
UTF-8, antes de copiar/publicar referencias externas. El mismo gate inspecciona
`work_delegation_snapshots.content`; admite texto UTF-8 con `\n`, `\r` y `\t`
sin alterar sus bytes. Las pruebas con `ImmutableResultStore` y
`DelegationSnapshots.freeze()` confirman rechazo sin destino publicado y
conservación exacta del snapshot permitido. El rechazo de secretos externos y
la compactación de páginas libres siguen cubiertos por la suite afectada.

Gates independientes frescos: **57 passed** en recuperación/guard/migraciones,
Black e isort PASS, check de whitespace en los dos paths sin coincidencias,
`project-composition-check` PASS (270 archivos, 996 dependencias, 4/4
contratos). `project-composition-review caos codex` terminó con exit 1,
`stdin is not a terminal`; no equivale a revisión automática aprobada. La
revisión manual de composición queda limitada a las rutas inspeccionadas.
Siguen sin acreditarse backup de memoria con contenido real, cierre con
productores reales de delivery/worktree/lineage ni verificador de corte
server-owned concreto. No se acredita restore T070. Informe:
`/tmp/caos-exec/T069/sol-binary-review.md`.

### Cierre T083: matriz real opt-in en CI — 2026-09-24

**PASS de aceptación T083; T083 `[x]`.** Los 10 workflows ordinarios no
referencian `live_provider`, `CAO_RUN_LIVE_PROVIDER_TESTS` ni el módulo de matriz
real. El único workflow que activa la matriz tiene exclusivamente
`workflow_dispatch`, `contents: read`, concurrencia sin cancelación, runner
self-hosted etiquetado `cao-real-e2e`, manifest sin valores de credenciales y
`CAO_REAL_PROVIDER_E2E_STRICT=1`. La guía exige cuenta, modelo exacto, CLI,
autenticación y runner preparados. El harness evita incluso parsear el
manifest si el gate live no vale exactamente `1`; el modo estricto rechaza
endpoints no preparados antes de levantar fixtures.

Gates frescos sin cuentas ni proveedor: YAML estructural y búsqueda de todos
los workflows PASS; suite completa de contrato **38 passed**, exit 0;
ejecución del módulo live con el gate ausente **1 skipped**, exit 0. Durante
la revisión, T082 añadió un contrato RED de evidencia de cuota; tras su cambio
concurrente de harness se repitió la suite completa y pasó. Estos checks no
acreditan la matriz real T084. `git diff --ignore-space-at-eol` de
workflow y guía dio exit 0; `git diff --check` señala sus CRLF preexistentes,
no un cambio semántico de T083. Informe: `/tmp/caos-exec/T083/sol-review.md`.

### Revisión T082: evidencia de matriz real pendiente — 2026-09-24

**FAIL de aceptación; T082 permanece `[ ]`.** El harness registra un JSON
`matrix_evidence` con `status=skipped` y sólo `quota_wait_reconciliation` como
escenario validado tras comprobar `waiting_quota` y el recibo `reconcile`.
`status=validated` se emite después de los cuatro escenarios y del borrado de
sesión. Sin embargo, la ruta de cuota termina en `pytest.skip`: no prueba la
continuación del turno tras el restablecimiento de la cuota. Los fallos antes
del final tampoco dejan un resultado estructurado por escenario.

El workflow real ejecuta pytest con `-o addopts=` y sin `--junitxml`; su único
artefacto es diagnóstico y se sube sólo en fallo. Por ello `record_property`
no conserva evidencia JUnit de cuotas, éxitos ni omisiones en una ejecución
protegida. El contrato local comprueba una lista Python y el texto del skip,
no el XML ni su retención. Además, `uv run isort --check-only` falla en el
módulo E2E: la línea nueva de importación usa LF dentro de un archivo CRLF.

Gates frescos, sin marker ni cuentas: contrato y módulo filtrados **38 passed,
1 deselected**, exit 0; módulo live con gate ausente **1 skipped**, exit 0;
`black --check` de ambos archivos, exit 0; `isort --check-only`, exit 1;
`git -c core.whitespace=cr-at-eol diff --check` de ambos, exit 0. No se
ejecutaron proveedores ni CI. Informe y diff focal:
`/tmp/caos-exec/T082/sol-review.md` y `/tmp/caos-exec/T082/sol-review.diff`.

### T063: preflight de Herdr real — 2026-09-24

**Gate no disponible; T063 permanece `[ ]`, SC-007 global y O03 abiertos.**
`command -v herdr` terminó con exit 1 y sin salida: el ejecutable no está en
`PATH`. `test/e2e/test_herdr_generic_status.py`, exigida por T063, no existe
en el checkout ni en el índice Git; `test/e2e` no contiene otra fixture Herdr.
`test/backends/test_herdr_work_status.py` usa un backend controlado y
`MagicMock`, por lo que su cobertura de observación no acredita Herdr real.

No se instanció `HerdrBackend`: su constructor llama a
`_ensure_session_running()`, que puede lanzar `herdr ... server` si falta el
socket. Tampoco se instaló backend, se contactó proveedor/red ni se abrió una
BD del operador. Para retomar el gate faltan Herdr y proveedor no nativo
autorizados en un entorno aislado, la fixture contractual y autorización para
ejecutarla en el runner protegido. Preflight y revisión independiente:
`/tmp/caos-exec/T063/terra-preflight.md` y
`/tmp/caos-exec/T063/sol-review.md`.

### T074: contrato efectivo YAML y replay revisado — 2026-09-24

**PASS de aceptación T074; T074 `[x]`.** La ruta script ya conservaba el
contrato inmutable antes del prompt. La ruta YAML ordinaria persiste por
intento el contrato v1 y su fingerprint antes de asignar terminal; el guard
consulta un snapshot verificado antes de envío y redelivery. Retry ordinario
exige hash/campos iguales y cierre `failed` durable; reprompt correctivo
declara el cambio. Campos sin fuente explícita permanecen `unknown` o
`not_applicable`.

La corrección de replay registra una generación propia `replay:<nonce>` en
`work_step_contracts` antes de asignar terminal, vuelve a verificarla antes
de cada I/O controlable y conserva intactas las filas de lifecycle y salida
del run origen. Override divergente y YAML legado sin contrato fallan antes
de efecto. Los tests locales usan SQLite temporal real, no proveedores.
Gates frescos: **227 passed, 3 warnings** en step/workflow/replay/journal;
Black, isort y whitespace scoped PASS; `project-composition-check` PASS
(270 archivos, 997 dependencias, 4 contratos conservados). El CLI de revisión
interactiva no se contó como PASS. No se promete atomicidad SQLite-proveedor
ni un lifecycle/listado independiente para cada replay. Dictamen:
`/tmp/caos-exec/T074/sol-rereview.md`.

### T082: evidencia local y ventana manual revisadas — 2026-09-24

**PASS de aceptación T082; T082 `[x]`; T084 permanece `[ ]`.** La celda de
cuota efectúa un único POST `run-step` con `prompt_redelivery=False`; luego
observa por GET el mismo terminal, output y recibo. El doble de output usa
el DTO real (`output`, `mode`). Sólo el recibo original `succeeded` con
`settled_at` y el marcador único permite validar continuación y hacer DELETE
del terminal; `provider_may_resume=False`, entrega no confirmada, ventana
ausente/inválida y límite agotado quedan como omisiones tipadas, sin retry.

La ventana de observación se configura en el dispatch manual con un número
obligatorio, se valida finita/positiva en el harness y se publica con hora de
inicio en `matrix_evidence`; la variable sólo llega al paso pytest. El
workflow sigue siendo sólo `workflow_dispatch` y sube un único JUnit saneado
con `if: always()` y retención acotada. El test sintético verifica la
propiedad por celda en pass/skip/fail y excluye prompt, marcador y output.
Gates frescos sin proveedor: **68 passed, 1 skipped** en contrato/módulo
live desactivado; test API focal **1 passed**; Black, isort, whitespace y
composición automatizada PASS (270 archivos, 997 dependencias, 4 contratos).
No se ejecutó matriz real, CI ni cuenta: esa evidencia corresponde a T084.
Informe: `/tmp/caos-exec/T082/sol-rereview-final.md`.

### T079: commit Work compartido revisado, cierre transversal pendiente — 2026-09-24

**PASS del incremento local; T079 permanece `[ ]`.** La extracción privada
`WorkRepository._commit_checked_transition` centraliza los dos `UPDATE` de
estado Work/attempt, el evento y el receipt bajo la transacción del llamador.
La raíz conserva CAS de generación/revisión, lease, resultado, job, ACK y
orden previo; la cascada conserva recorrido, reducers y fingerprint. Una
prueba con trigger SQLite sobre el receipt hijo demuestra rollback de raíz,
hijo, eventos y receipts. No se alteraron `.importlinter` ni los owners de
API, terminal nativo, workflow o memoria: no se acreditó dependencia nueva
que justificase otra regla. Sus ciclos distintos no son, por sí mismos,
transiciones Work duplicadas.

Gates frescos: repositorio **25 passed**; batería de repositorio/autoridad/
servicio/admisión/API **77 passed, 1 failed**. El fallo único es la aserción
externa `SCHEMA_VERSION == 23` en `test/services/test_work_authority.py:248`
frente a versión actual 24, no una regresión del helper. Black e isort de
los dos archivos T079 exit 0; Import Linter **4 kept, 0 broken** y
`project-composition-check` PASS. Los dos archivos son untracked; el check
no-index de whitespace no imprimió errores (exit 1 por existir diferencias
contra `/dev/null`). No se ejecutó proveedor ni DB de operador.

Esto acredita dueño único de la transición durable **Work/attempt**, no la
equivalencia de los ciclos native-child/run-step/memoria ni un enlace
autenticado operativo de hijo/workflow/ACK a intento Work (T019/T020/T094), ni el fence completo
de restore (T070). FR-017/T079 aún requiere prueba transversal por interfaces
cuando esos contratos estén definidos; no se declara un owner global
inventado ni se cierra por el gate local. Revisión independiente:
`/tmp/caos-exec/T079/sol-review.md`.

### T069: productores reales, revisión parcial de seguridad — 2026-09-24

**T069 permanece `[ ]`; no hay aceptación de backup completo.** La captura
ahora inventaría contenido real de `MemoryService` y lo referencia en el
manifest con rol, id, versión, digest y tamaño; delivery, worktree y lineage
se contrastaron con sus productores/filas reales. El verificador del bundle
es de integridad offline, no prueba quiescencia: el verificador de corte
server-owned aún es sólo protocolo inyectado.

Brecha source-backed: para `memory-content`, la contención usa
`absolute().relative_to(memory_root)` y el veto de symlinks inspecciona raíz
y archivo final, no directorios intermedios. Un symlink intermedio hacia fuera
puede pasar y leerse con `O_NOFOLLOW` aplicado sólo al final; el gate UTF-8 y
secretos no corrige ese escape. Falta RED adversario y corrección del owner.

Runner comunicado: RED inicial **1 failed/29 deselected** por `memory_root`
ausente; luego pytest focal fuente estado D >2 min. En ext4 con SHA paritario,
imports de bundle/inventory/memory y `runpy` test PASS; `pytest collect` sin
nodeid >20 s. En xdist, primer clon falló build script; segundo clon de siete
SHA completó build+118 dependencias, pero `pytest -n4` no emitió stdout >90 s
y fue terminado. **Ningún pytest posterior es PASS**; no se reintentó.
El informe builder aún no estaba disponible al revisar. Dictamen:
`/tmp/caos-exec/T069/sol-producers-review.md`.

### T093: gate documental de autoridad aprobado y especificado — 2026-09-24

**T093 `[x]` sólo como decisión/contrato interno; T017/T019/T020/T035/T094
siguen `[ ]`.** Aprobación humana literal en
`/tmp/caos-exec/T093/approval-2026-09-24.md`: operador autenticado como
único provisionador launch; grants propios para hijos/workflows; ACK
`task_received` sólo de receptor autenticado, ligado a entrega/intento/
generación. `spec.md` incorpora aceptación y límites; `plan.md` fija owners,
DTO internos v1, fases, migración aditiva posterior a v24, rollback sin
downgrade, compatibilidad legacy y mixed-version fail-closed; `tasks.md`
conserva abiertas las integraciones y añade T094 para recibo autenticado,
con REDs de autoridad, procedencia, ACK, reinicio y fallo parcial.

Graphify existente localiza `WorkAuthority`, `Principal`, `Grant` y
`DeliveryObservation`, pero no conoce `WorkProvisioning`/`WorkOrigins`:
se contrastó contra fuente v19–v24 y no se refrescó el grafo. El código
interno de provisión, autorización y lineage no acredita aún wiring público,
identidad operativa de agentes ni recibo receptor. El booleano
`DeliveryObservation.task_received` y `native_children.acknowledged` no son
ese recibo. `POST /work-launches` sigue sin gateway por defecto y responde
503; ninguna ruta, MCP, proveedor o DB operador se activó en este paso.
La revisión `speckit-analyze` se registra en el dictamen:
`/tmp/caos-exec/T093/sol-approval-review.md`.

### T017: integración de launch ordinario bloqueada — 2026-09-24

**T017 permanece `[ ]`; T093 `[x]` es sólo contrato documental.** La ruta
actual `cao launch → POST /sessions → session_service.create_session →
terminal_service.create_terminal` puede alcanzar el backend y el proveedor
sin resolución de `WorkProvisioning` ni admisión de Work. `/sessions` exige
scope general y acepta una `idempotency_key` opcional del caller, pero no
recibe el `Principal`, selector y clave server-owned del contrato T093. La
ruta `/work-launches` es distinta y no está conectada al ingreso ordinario.

El RED temporal comunicado por Terra observó efecto en backend test-only y
fue retirado; no se reejecutó ni se cuenta como gate verde. Falta definir y
autorizar el ingreso interno y handoff de esas tres piezas, sin inferirlas
de `agents`, nombre, body, sesión o `caller_id`. T093 no autoriza ese mapping
ni activar entradas públicas. Dictamen:
`/tmp/caos-exec/T017/sol-blocker-review.md`.

Decisión de routing aceptada — 2026-09-25: `cao launch` ordinario
permanece en `/sessions` legacy. `--queue-work` sigue como opt-in con bearer
verificado hacia `/work-launches`: sólo admite Work y devuelve recibo `queued`,
sin fallback a `/sessions`. Su selector omitido se resuelve en servidor sólo
si hay una provisión activa única para el Principal verificado. Esta ruta
parcial no conecta launch ordinario, no despacha proveedor ni usa la DB del
operador; **T017 sigue `[ ]`** y ninguna entrada pública nueva se activa.

### T094: recibo interno v25, integración todavía abierta — 2026-09-24

**T094 permanece `[ ]`.** Fuente actual: `DeliveryObservation.task_received`
legacy ya no promueve Work; `TaskReceivedReceiptV1` se sella por runtime,
revalida binding y grants, y v25 inserta recibo inmutable antes de transición
en una transacción SQLite. En snapshot ext4 SHA-idéntico, los dos módulos
focales propios dieron 53 PASS. El checker oficial de composición ejecutado
de nuevo dio 4 contratos kept, 0 broken, exit 0; el anterior bloqueo 9p
no se cuenta como PASS.

No hay aceptación global: migraciones dieron 22 PASS y 1 FAIL porque una
regresión T065 exige literalmente schema v24; un test de integración de
dispatch dio FAIL (`running` frente a `succeeded`) al esperar ACK desde
booleano legacy. La suite de otros owners y los casos específicos de nonce
contradictorio, ACK tardío, revocación del recibo, mixed-version y fallo
v25/rollback no quedaron demostrados por pruebas actuales. El brief T094
pedía detenerse antes de editar `work_repository.py` (shared core); Terra
solicitó y coordinación concedió expansión limitada a registro y
verificación de schema v25. No hubo transporte/proveedor/DB operador en
esta revisión. Dictamen: `/tmp/caos-exec/T094/sol-review.md`.

### T094: re-revisión final, regresión de dispatch pendiente — 2026-09-24

**T094 sigue `[ ]`.** Las correcciones superan los focos anteriores: 54
service/lineage + 23 migraciones + dispatch focal = 78 PASS en snapshot ext4
SHA-idéntico; prueba nueva de revocación post-emisión deja `sent` sin receipt
ni evento, y `WorkService` captura ahora `OriginConflict` como rechazo
fail-closed. Upgrade v24→v25, denegación DDL v25/rollback y verificador v24
sobre v25 se reprodujeron con SQLite temporal. Import Linter sobre el propio
snapshot: 4 kept/0 broken; el checker oficial registró también 4/4, aunque
resolvió el proyecto registrado en `/mnt/c` y no el cwd ext4.

El barrido adicional de dispatch síncrono dio **11 PASS, 1 FAIL**:
`test_send_callback_can_commit_a_separate_sqlite_writer` todavía espera
`running` de `DeliveryObservation(task_received=True, execution_started=True)`;
el nuevo contrato deja correctamente `sent` sin recibo autenticado. La prueba
debe adaptarse sin reintroducir ACK por booleano. Las pruebas async de ese
módulo no se ejecutaron en el venv ext4 sin plugin; no se presume suite global
verde. El dictamen anterior queda superado para los focos corregidos, no
para esta regresión. Informe:
`/tmp/caos-exec/T094/sol-final-review.md`.

### T094: callback fix verificado, dos regresiones async — 2026-09-24

**T094 sigue `[ ]` por dos fallos en la suite async aplicable;
T017/T019/T020/T035 siguen `[ ]`.** Producción
`work_service.py`, `work_origin.py`, `work_origin_schema.py` y
`work_repository.py` conserva SHA del dictamen previo; sólo se corrigió el
test consumidor. Snapshot ext4 SHA-idéntico del estado actual: 54 pruebas de
service/lineage, 23 migraciones y 12 dispatch síncronas PASS (89 total),
además de 16 delivery síncronas PASS. El callback mantiene escritor SQLite
separado, deja `sent` sin receipt/ACK y no reenvía. El rollback v25 al denegar
DDL, upgrade v24→v25/mixed-version fail-close, revocación post-emisión,
nonce/hash contradictorios y replay tras reinicio fueron contrastados con
fuente y pruebas SQLite temporal; `OriginConflict` se rechaza sin transición.
Import Linter sobre snapshot: 4/4 contratos PASS. Tras instalar
`pytest-asyncio==1.4.0` en la venv ext4, suite completa de los cinco
módulos focales: **123 PASS, 2 FAIL, ninguna deselection**. Los dos fallos
son expectativas de `running`/`acknowledged` desde booleano legacy en
`test_async_dispatch_runs_delivery_on_callers_loop_after_durable_intent` y
`test_async_restarted_dispatch_reads_exact_durable_snapshot`; la fuente
mantiene `sent` sin receipt. No se activó transporte, API,
proveedor ni DB operador. Dictamen:
`/tmp/caos-exec/T094/sol-closure-review.md`.

### T094: receipt interno aceptado tras suite async completa — 2026-09-24

**T094 `[x]` sólo como contrato interno; T017/T019/T020/T035 siguen `[ ]`.**
Los dictámenes T094 anteriores abiertos describen cortes previos y quedan
superados. Producción conserva SHA del review anterior; dos tests async de
dispatch se ajustaron para preservar loop del caller, intent `sent` antes
del efecto, snapshot exacto tras reinicio y no redelivery, sin inventar ACK
desde el booleano legacy. Snapshot ext4 SHA-idéntico y venv ext4 con
`pytest-asyncio==1.4.0`: cinco módulos completos, **125 PASS, 0 FAIL,
0 deselected**. Denegación DDL v25 revierte a ledger/schema v24;
upgrade v24→v25 y verificador v24 sobre v25 fallan/cumplen según contrato.
Revocación post-emisión, nonce/hash contradictorio, replay exacto tras
reinicio y ausencia de redelivery se verificaron. Import Linter del snapshot:
4 kept/0 broken. No se activó API, proveedor, MCP ni DB operador. Informe:
`/tmp/caos-exec/T094/sol-async-closure-review.md`.

### T069: cierre del symlink intermedio, backup aún no aceptado — 2026-09-24

**T069 permanece `[ ]`.** La corrección source-backed abre `memory_root`
con `O_DIRECTORY|O_NOFOLLOW` y cada directorio relativo mediante `dir_fd`
con las mismas flags; el archivo final usa `O_NOFOLLOW`, `fstat` regular y
FDs cerrados en error/éxito. El descriptor abierto alimenta copia, UTF-8/
controles y secret gate antes de publicar manifest. Control positivo de
`MemoryService` y symlink intermedio: **2 PASS** en snapshot ext4 SHA-idéntico;
gate recuperación/migraciones pertinente **59 PASS**, excluyendo
explícitamente el restore T070. Black/isort/compile PASS e Import Linter de
snapshot **4 kept/0 broken**. El perfil v25 verifica DDL/ledger/FKs y
tabla exacta; una tabla desconocida falla cerrado.

La fuente sólo define `ServerOwnedOfflineCutVerifier` como `Protocol` y
recibe una implementación inyectada; no hay composición productiva que
demuestre quiescencia y retención entre verificaciones before/after. Los
tests usan `_TrustedOfflineCutVerifier` de prueba. Por ello el verifier de
bundle acredita integridad offline, no corte seguro ni backup completo.
El manifest todavía etiqueta el objeto SQLite `sqlite-v24` aunque publica
`profile_version=25`; se registra para la siguiente revisión de contrato,
sin atribuirle un restore ejecutado. Informe:
`/tmp/caos-exec/T069/sol-security-review.md`.

### T069: metadata SQLite coherente; quiescencia pendiente — 2026-09-24

La nota anterior sobre la mezcla `profile_version=25`/`sqlite-v24` queda
superada por la corrección de metadata. La captura actual publica
`sqlite-v25`; el verificador estructural v1 exige exactamente un objeto
`sqlite-vN` cuyo N coincide con `profile_version`, rechaza roles SQLite
contradictorios y conserva lectura canónica de manifests históricos v24.
En snapshot ext4 SHA-idéntico, el focal v25/v24 pasó **1/1** y el gate de
recuperación/migraciones pertinente pasó **59/59**, excluyendo restore T070.
Esto no verifica la versión real del SQLite exportado ni prueba un corte
seguro. **T069 permanece `[ ]`**: `ServerOwnedOfflineCutVerifier` continúa
siendo sólo un `Protocol` inyectado, sin implementación productiva que
demuestre quiescencia/retención before-after. Informe:
`/tmp/caos-exec/T069/sol-metadata-review.md`.

### T069: corte interno aprobado, contrato documental — 2026-09-24

La aprobación humana `/tmp/caos-exec/T069/offline-cut-approval-2026-09-24.md`
autoriza diseñar/implementar un lease server-owned para un operador
autenticado, limitado a writers Work registrados. Spec/plan/tasks fijan
owner, scope, época/fence/TTL durable, revalidación before/after, evidencia
hasheada de lease+inventario, rechazo durable y pruebas RED de expiry,
revoke, writer nuevo, fallo parcial y reinicio. La ruta documental fija
Work store v26 aditivo, inventario cerrado v26 y bundle v2; v1 mantiene sólo lectura
estructural, y mixed-version/rollback fallan cerrados. **T069 y T070 siguen
`[ ]`**: la fuente actual aún tiene sólo el `Protocol` inyectado y no hay
owner productivo ni pruebas del corte. No se habilitó transporte, proveedor
ni DB operador. Informe: `/tmp/caos-exec/T069/sol-cut-contract-review.md`.

### T065/T069: corte offline interno y bundle v2 aceptados — 2026-09-24

**T065 y T069 quedan `[x]` para el alcance offline interno aprobado.** La
revisión independiente R10 comprobó en fuente el lease server-owned para
operador autenticado, el registry de writers Work y el `BEGIN IMMEDIATE` que
cubre manifest privado, `os.replace`, fsync y CAS `live -> published`. El
manifest v2 liga `capture_id`, tres observaciones con timestamp/lease/fence/
inventario/cero writers, cobertura exacta `integrity-only` y hash de objetos
al ledger y a la fila publicada. Un huérfano sin publicación no verifica;
SQLite v26 no se puede rebautizar como v1. Un v1 histórico v25 sólo verifica
integridad, sin receipt de publicación.

Las pruebas R10 usan APIs reales para revoke concurrente y la ventana
`sent`→registro, mutan Work fuera del registry durante stage y adulteran
receipt/ledger; las sensibilidades RED se revirtieron antes del gate. Hashes
R10 de 11 paths fuente/snapshot coinciden. Revisión independiente en ext4:
**135 passed, 1 deselected** en seis archivos focales; el único deselect es
el RED aislado de restore T070 y no cuenta como PASS. Black/compile e Import
Linter 4 kept/0 broken constan en la revisión de composición R10. No se
infiere quiescencia global de productores no registrados ni se acredita
restore, ingreso público, MCP/CLI, proveedor real o DB operador. **T070 y
T071 siguen `[ ]`**; SC-006 de restauración todavía no está satisfecha.
Dictamen: `/tmp/caos-exec/T069/sol-r10-review.md`.

### T095: primitiva portátil bloqueada aceptada; restore pendiente — 2026-09-24

**T095 queda `[x]` sólo para la primitiva privada de repositorio.** La copia
v26 staging se valida contra DDL/ledger/FK y contexto inbox histórico de la
fuente, sin adoptar esa identidad ni modificar bridge/bindings. Un único
`BEGIN IMMEDIATE` pasa el contexto normal a `blocked_restore` con UUID nuevo,
UUID fuente, digest y receipt, y reconcilia únicamente intentos inciertos;
rollback conserva contexto, intentos, eventos y receipts ante fallo intermedio.
La prueba R2 usa fuente, staging y destino distintos: mueve sólo la copia ya
bloqueada y confirma procedencia, contexto y rechazo local en el destino.

Los dos paths fuente del snapshot R2 coinciden por SHA con el checkout; suite
focal actual **44 PASS**. Black/compilación focal y composición: 270 archivos,
999 dependencias, 4 contratos kept/0 broken. Esto no implementa el caller ni
la publicación segura de T070, ni demuestra guard transversal de ingress o
reactivación. **T070 y T071 siguen `[ ]`**; SC-006 de restauración no se declara
satisfecha. Dictamen: `/tmp/caos-exec/T095/sol-r2-stagef-review.md`.

### T070: restore privado v2 aceptado; reactivación pendiente — 2026-09-24

**T070 queda `[x]` sólo para `restore_recovery_bundle` interno.** Exige receipt
publicado y manifest canónico v2/perfil 26 con un objeto `sqlite-v26`; la
identidad inbox proviene de `receipt.source_database`. Cierra el multiset de
referencias SQLite/manifest, incluidos ids `memory-content`, e inspecciona
DDL, ledger y FK antes de crear staging. La ruta configurada de DB del operador
se compara sólo como path, incluso ausente; no se abre.

El staging privado exclusivo `0600` copia desde `O_NOFOLLOW`, verifica digest/
tamaño y hace fsync. Sólo esa copia llama a la primitiva T095: pasa a
`blocked_restore` y reconcilia `sent`/`acknowledged`/`running`, sin relanzar.
`os.link` publica sin clobber; un competidor conserva sus bytes. Si falla el
fsync del padre tras el link, se devuelve error de publicación incierta y el
destino bloqueado permanece, sin reportar éxito.

Evidencia R2 fresca: nuevos REDs reparados **2/2 PASS**; recuperación
**61/61 PASS** y regresión T095 **44/44 PASS**. Snapshot ext4 final:
**105/105 PASS** combinadas (29.56 s); Black, `py_compile`, diff check y
`project-composition-check caos` exit 0 (270 archivos, 1002 dependencias,
4 contratos kept, 0 broken). Revisión independiente Stage C R2 PASS y diff
acotado sin diagnósticos de whitespace.
No hay entrada pública API/CLI/MCP, proveedor, DB del operador ni guard global.
**T071 y T072 siguen `[ ]`**: rollback/reactivación y prueba multinodo no se
acreditan por este cierre. Dictamen: `/tmp/caos-exec/T070/sol-r2-stagef-review.md`.

### T071: integración de restore y guía de rollback aceptadas — 2026-09-24

**T071 queda `[x]`** para la prueba de integración SQLite temporal y la guía
`docs/work-recovery.md`. La captura real v2/perfil 26 restaura a un destino
distinto y bloqueado, conserva la fuente y convierte sólo el intento incierto
`sent` en `reconcile`, sin intento nuevo. V1/perfil 25 conserva únicamente
verificación histórica; perfiles mezclados, lease publicado corrupto y copia
interrumpida rechazan sin destino. La migración v25→v26 es aditiva; el DDL
denegado deja v25 sin tabla nueva y el verificador v25 rechaza v26.

La guía exige revalidar el lease durable tras reinicio para la captura, prohíbe
writer v25 sobre store v26 y downgrade/DROP, y limita el retorno a v25 a un
snapshot aislado con conciliación y autorización separada. T070 no restaura
snapshots v25. Gate local: **4/4 PASS** integración y **90/90 PASS** combinadas
con migraciones y recuperación; Black PASS. Snapshot ext4 SHA-idéntico:
Black, `py_compile`, diff y composición PASS (270 archivos, 1002 dependencias,
4 contratos kept/0 broken). Dictámenes Stage C y composición PASS; revisión
operativa Luna PASS. El destino continúa `blocked_restore`; no hay prueba de
cese externo, activación, ingreso público, proveedor ni guard global.
**T072 sigue `[ ]`** para partición/reconexión multinodo. Dictamen final:
`/tmp/caos-exec/T071/sol-stagef-review.md`.

### T072: autoridad SQLite temporal de dos procesos aceptada — 2026-09-24

**T072 queda `[x]` para el escenario local de FR-015.** Dos procesos uvicorn
con PIDs distintos sirven las rutas de conocimiento reales por HTTP localhost
y comparten una sola SQLite bajo `tmp_path`. Dos propuestas simultáneas con
`expected_version=0` producen exactamente un 201 y un 409, con una sola
revisión durable. Tras detener B, A confirma la versión 2; la petición a B
falla por conexión y la lectura directa del store exige exactamente las dos
revisiones esperadas. Un B nuevo rechaza `expected_version=1` con 409 y lee
la versión 2 y su contenido exacto. La reparación R1 limpia en padre e hijos
la configuración de IdP heredada; el focal con ambas variables de auth
configuradas pasó **1/1** en la revisión independiente R2. Terra registró
también integración relacionada, Black, compilación, whitespace y composición
PASS (4 contratos kept/0 broken).

El corte modelado es indisponibilidad/reinicio de un proceso local, no una
partición de red. No se prueban replicación, consenso, HA, fallo de energía
ni operación entre hosts. El harness usa `fork` y la selección de puerto
libera el socket antes de que uvicorn lo enlace. No se abrió la DB del
operador ni se ejecutó un proveedor. Dictamen Stage C R2:
`/tmp/caos-exec/T072/sol-r2-technical-review.md`.

### T035: ingreso MCP acotado y límite de reintentos aceptados — 2026-09-25

**T035 permanece `[ ]`; T017 también permanece `[ ]`.** En la ruta de prueba
con gateway inyectado, `work_launch` remite sólo selector e intención al HTTP
autenticado y devuelve un recibo de admisión `queued`. La clave de operación
deriva del servidor; el conflicto se expresa como
`launch_idempotency_conflict`/`inspect_existing_launch`, sin pedir una clave
al caller. La prueba de integración con SQLite temporal y backend falso
observó una orden en cola, cero efectos de backend y cero recibos
`task_received`. El envelope público de error se valida contra tuplas
permitidas y los detalles no reconocidos o privados se sustituyen por un
error fijo sin filtrar su texto.

La corrección R3 acota `launch_transport_unavailable` reintentable a
`requests.ConnectionError` y `requests.Timeout`. `InvalidURL`,
`InvalidSchema` y `MissingSchema` dan `launch_internal_error` fijo, sin texto
privado y no reintentable; un `ValueError` propagado por el helper da
`launch_response_invalid` no reintentable. El JSON inválido de una respuesta
2xx sigue otra ruta: el helper devuelve `{}` y el MCP lo clasifica como
`launch_receipt_invalid`, también no reintentable. Esta evidencia no prueba
que ese JSON pase por el catch de `ValueError`.

Evidencia aceptada en ext4: R2, **49 pruebas focales PASS** (34,66 s), aunque
su revisión detectó la clasificación defectuosa corregida en R3; R3, **3
RED esperados** y **13 pruebas MCP GREEN PASS** (20,25 s). La revisión
técnica Sol R3 aprobó alcance y calidad del límite de errores. El checker
posterior del controlador dio **270 archivos, 1002 dependencias, 4 contratos
conservados, 0 rotos**; whitespace acotado, Black del test MCP nuevo y
compilación de `server.py` PASS. Black de `server.py` completo aún informa
bloques sucios preexistentes ajenos a este incremento. Un reintento de las
49 pruebas en otro snapshot ext4 de hashes coincidentes entró en estado D
ininterrumpible y **no tiene resultado**; no sustituye los gates R2/R3.

Sin gateway inyectado, el HTTP normal conserva el **503 fail-closed**: no
hay composición verificada de repositorio y registro de backends en runtime.
No se activaron proveedor, DB del operador, dispatch, grants, reservas ni
ACK. Quedan abiertos `task_received` público, la matriz completa de
autoridad pública y la guarda de CLI/sesiones legacy de T017. Este incremento
no cierra T035 ni acredita operación con proveedor o backend real.
Dictámenes: `/tmp/caos-exec/T035/sol-r2-rereview.md` y
`/tmp/caos-exec/T035/sol-r3-rereview.md`.

### T096: composición de launch durable cerrada — 2026-09-25

**T096 `[x]`; T019 y T035 siguen `[ ]`.** El lifespan compone el gateway tras
`init_db()` y `get_backend()` desde el registro server-owned vacío por defecto.
La admisión exige la clave exacta de `EffectiveWorkContract.backend` y preflight:
ausente, desconocida o incapaz devuelve el 503 fijo sin admisión; el backend
falso capaz persiste sólo `queued` en SQLite temporal, sin terminal ni proveedor.
El schema Work corrupto impide servir. Si la composición falla tras crear tareas,
el startup las cancela sin ceder el loop, las espera y ejecuta teardown de
watchdog, plugins y telemetría conservando la excepción original.

Evidencia: `uv run pytest test/api/test_work_launch_composition.py` RED
conductual antes de producción, exit 1 (2 failed, 3 passed, 1 skipped,
4 warnings; el ImportError previo no cuenta como RED); GREEN exit 0
(6 passed, 4 warnings). La regresión de cleanup tuvo RED exit 1
(1 failed, 4 warnings) y GREEN exit 0 (1 passed, 6 deselected, 4 warnings),
con logs `red-round1-fix.log` y `green-round1-fix.log`. El focal final del
mismo comando dio exit 0 (7 passed, 4 warnings). El comando afectado
`uv run pytest test/api/test_work_launch_composition.py test/api/test_work_launch_ingress.py test/services/test_work_launch_composition.py test/services/test_work_launch_runtime.py test/integration/test_work_admission.py`
dio exit 0 (63 passed, 4 warnings). `project-composition-check "$(cat .ai/project-name)"`
dio exit 0: Import Linter 4 kept/0 broken, 271 archivos/1005 dependencias.
Logs finales: `/tmp/caos-exec/T096/focused-round1-fix.log`,
`affected-round1-fix.log` y `composition-round1-fix.log` en el mismo directorio.

Sol R1 detectó falta de cleanup y falló; R2 aceptó la reparación; R3 aceptó
la cancelación síncrona y el diff de bytes acotado al bloque lifespan.
Dictámenes: `/tmp/caos-exec/T096/sol-r1-review.md`, `sol-r2-review.md` y
`sol-r3-review.md` en ese directorio. Las tres lagunas de prueba y cleanup
detectadas entonces quedaron cubiertas por el seguimiento fechado abajo.
No se activó ingreso público, autoridad, grants,
reservas, dispatch, ACK, proveedor ni DB del operador.

### T096: seguimiento de límites de startup y plugins — 2026-09-25

**T096 sigue `[x]`; T019/T035 siguen `[ ]`.** El set posterior comprueba que
una cancelación en `PluginRegistry.load()` conserva `CancelledError`, ejecuta
cleanup y respeta un singleton Herdr ajeno. Una prueba distinta corrompe la
SQLite temporal tras `init_db()` y confirma que el factory real llama a
`verify_schema()` y bloquea el startup. Si el error llega después de registrar
el Herdr propio, se cancela/espera su tarea y se limpia sólo ese singleton,
conservando la excepción original. `PluginRegistry` ahora intenta `teardown()`
del plugin cuyo `setup()` falló o fue cancelado antes de registrarlo; un fallo
de ese teardown no sustituye el error original y otros plugins sanos continúan.

Evidencia actual: 10 tests de registry PASS y 83 pruebas afectadas PASS con
4 warnings; `project-composition-check caos` PASS (271 archivos,
1005 dependencias, 4 contratos kept/0 broken). Revisión independiente Sol:
PASS en ambos límites. Informe y comandos del seguimiento:
`/tmp/caos-exec/T096/plugin-lifecycle-followup-builder-report.md`; logs
de la etapa previa de composición/lifespan: `/tmp/caos-exec/T096/composition-round2-residuals.log`
y `affected-round2-residuals.log`. Los tres riesgos anteriores quedan resueltos.
Límite residual distinto: `PluginRegistry.teardown()` normal captura
`Exception`, de modo que un plugin que propague `CancelledError` puede detener
el teardown de plugins posteriores; ese caso no se cubre en este incremento.
No hay activación pública, proveedor ni DB del operador.

### T096: cancelación de plugins y cierre normal verificados — 2026-09-25

**T096 sigue `[x]`; T017/T019/T035 siguen `[ ]`.** Un `CancelledError`
originado por un plugin queda aislado y el teardown continúa con los demás.
La cancelación externa se transmite al teardown activo, se espera su fin y el
de los plugins posteriores, y luego se propaga al caller. Las pruebas cubren
cancelación repetida y la carrera entre crear la tarea y comenzar el teardown.
Si `setup()` falla o se cancela, su cleanup resiste cancelaciones repetidas y
conserva el error original aunque el cleanup falle. El shutdown normal del
lifespan ejecuta `shutdown_telemetry()` en `finally` tras el teardown.

Evidencia del snapshot ext4 con hashes coincidentes: **100 pruebas aprobadas**,
4 warnings, exit 0 en 6,92 s; composición PASS (271 archivos, 1006 dependencias,
4 contratos conservados, 0 rotos), diff acotado y whitespace CRLF-aware PASS.
Logs: `/tmp/caos-exec/T096/affected-current-hash-matched.log` y
`/tmp/caos-exec/T096/verified-final-composition.log`.
Revisión independiente Sol: PASS. Persiste un límite: un plugin que suprima la
cancelación indefinidamente puede bloquear el teardown. No se cierra la
integración ordinaria de `/sessions` ni se activan proveedor o DB del operador.
Decisión Graphify Stage F: la consulta de lifespan, teardown y telemetría sobre
el grafo existente de 30 458 nodos devolvió 138 nodos amplios/ambiguos (23
visibles); los callers se comprobaron en source. T096 cambia mecánica interna
de tareas/cancelación asyncio sin añadir módulos ni aristas de imports, así
que este incremento no requiere refrescar Graphify; `graphify-out/` queda intacto.

### T019/T020: gates de procedencia operativa revalidados — 2026-09-25

**T019 y T020 permanecen `[ ]`.** Relectura de fuente, sin pruebas ni cambios de
código: `WorkOrigins.admit` y `WorkAdmission.admit_managed_lineage` ya sellan el
origen y vinculan atómicamente orden, entrega, hijo e intento/generación padre.
La ruta legacy `/terminals/run-step` sólo pasa un `Principal` opcional verificado;
`caller_id` procede del body y el adaptador `run_managed_agent_step` no recibe
contexto de caller. Falta un productor autenticado de intento padre exacto,
identidad de hijo y receptor y sus grants; no se infieren de terminal, bearer
local del operador, nombre, sesión ni telemetría. T019 no tiene todavía un
puente operativo de hijo/handoff ni un ACK Work por recepción nativa.

YAML invoca `run_agent_step` legacy; script conserva generación textual y
contrato en `workflow_journal`, sin vínculo durable a intento/generación Work.
T020 necesita origen y grant propios por step y un binding cercado entre
journal y `WorkAdmission` antes de ejecutar por el adaptador registrado. Tras
fijar ese contrato, el RED debe cubrir ausencia/suplantación de sujeto, grant revocado,
generación obsoleta, replay/reinicio, fallo parcial y que estados legacy no
emitan `task_received`. La corrección requiere owners compartidos de autoridad,
persistencia y posiblemente esquema; no hay corte de producción acotado para
Luna. `cao launch` ordinario sigue en `/sessions`, `--queue-work` sólo admite
trabajo opt-in y T017 sigue `[ ]`. Ninguna entrada, proveedor ni DB del operador
se activó por esta revisión.

### T079: preparación transversal releída tras T094/T070/T096 — 2026-09-25

**T079 permanece `[ ]`; revisión de fuente, sin gate ejecutado.** El owner
durable Work continúa en `WorkRepository._commit_checked_transition` para
estado de item/intento, evento y receipt. T094 hace que `WorkService` valide
el receipt autenticado con `WorkOrigins` antes de escribir ACK mediante el
repositorio; telemetría legacy no lo promueve. T070 reconcilia los intentos
inciertos de la copia restaurada mediante `_transition_attempt` dentro del
repositorio y conserva el destino `blocked_restore`. T096 compone el gateway
desde el registro de backends y `LaunchRuntime` admite por `WorkAdmission`, sin
despacho de proveedor. Estas rutas no añaden un segundo writer de transición
Work en API, terminal o memoria; las cuatro reglas actuales de `.importlinter`
siguen siendo el límite estructural documentado, sin nueva regla justificada
por esta relectura.

Falta la prueba transversal de ownership y equivalencia: `api/main.py` aún
inicia/cancela runs escribiendo `workflow_journal`; `terminal_service` y
`clients.database` transicionan `native_children`; `MemoryService` usa el
repositorio Work para auditoría legacy y `KnowledgeRevisions` para revisiones,
sin convertir esos ciclos en transiciones Work. Sus vínculos autenticados a
intentos Work siguen ausentes para launch ordinario, hijo/handoff y workflow
(T017/T019/T020), y la entrada pública T035 mantiene gate separado. T044 y
T023 necesitan esos vínculos para demostrar contexto y ciclo completo. No se
extrae lógica ni se cierra FR-017/T079 por similitud de nombres o estados.

### T019: decisión de credencial interna por intento — 2026-09-25

Spec Kit registra el contrato pendiente aprobado para T019: credencial CAO
aleatoria de alta entropía, sólo digest en Work DB v27 aditiva, vinculada a
instalación/principal/item/intento/generación/grant-revisión/lease/expiración y
creada durablemente antes del efecto. Proxy MCP server-owned por intento recibe
el secreto sólo por descriptor privado heredado y consulta origen por socket
privado; backend sin aislamiento demostrable de endpoint/descriptores falla
cerrado. Cada petición revalida binding, grant y recuperación vivos; revocación
o reemplazo invalida uso. Proxy superviviente tras reinicio sólo continúa con
binding vigente; proxy perdido exige conciliación, sin reissue ni redelivery.
El secreto no entra en delivery, prompt, entorno, terminal, log ni evento.
`WorkAdmission._preflight` debe rechazar antes de crear sesión/ventana si no
prueba aislamiento entre intentos del endpoint y descriptores. El registro
Work actual está vacío; Tmux/Herdr rechazan preflight, sin fallback ni token
en entorno como sustituto.

El padre autenticado elige sólo refs de hijo/receptor preprovisionadas; resolver
comprueba permisos vivos y ambos actores se autentican separadamente. Receipt
sólo tras aceptación durable exacta del receptor, persistido por `WorkService`
junto al Work ACK; `caller_id` y ACK nativo siguen legacy. RED pendiente:
acceso cruzado, redacción, generación/grant/revocación obsoletos, reinicio,
duplicados/replay, fallo parcial y aceptación durable. T094 introdujo v25 y
T069 v26; v27 sería aditiva sin backfill, con rollback/mixed-version fail-closed.
T019, T017, T035 y entradas públicas/proveedores/DB operador siguen sin activar.
Esta entrada documenta diseño; no acredita código ni pruebas de T019.
Dictamen técnico actual: aplazar T019 completo hasta probar un backend Work
aislado; implementar ahora sólo el core de credencial dejaría un camino inerte.

### T079: auditoría de owners de transición — 2026-09-25

Relectura de fuente, sin ejecutar pruebas: `services/work_admission.py:937-946`
envía la transición Work al repositorio. `services/work_service.py:236-261`
valida el receipt y llama a `_record_task_received_receipt` dentro de su
transacción; `clients/work_repository.py:1397-1557,1611-1650` persiste receipt,
estado de item/intento y evento mediante `_commit_checked_transition`.
En estas rutas hay un solo writer de transición Work.

Los estados legacy tienen owners distintos: `api/main.py:6810` llama a
`services/workflow_journal.py:556-580` para runs; `clients/database.py:2587-2603`
concilia `native_children`; `services/memory_service.py:300-311,366-407`
usa `KnowledgeRevisions` para memoria revisada y `WorkRepository` para auditoría
legacy, sin promover esas filas a transición Work. No se demostró duplicación
que justifique extracción o regla nueva de `.importlinter` en este corte.
**T079 permanece `[ ]`:** faltan equivalencia y vínculos autenticados a Work
para T017/T019/T020; T035 conserva gate de entrada pública. Esta auditoría no
acredita PASS de composición ni cierre de FR-017.

### T097: prerrequisito de aislamiento Work para T019 — 2026-09-25

**T097 `[ ]`; T019 `[ ]` y bloqueado por T097.** La propuesta de backend Linux
dedicado con Bubblewrap es condicional. Una prueba WSL de separación de mounts
observa sólo ese límite; no demuestra el `ProcessRestrictionContract` completo
ni launch, continuación, revocación, reinicio y limpieza de CAO. La fuente
actual conserva `WORK_BACKENDS` vacío, y `TerminalBackend.preflight_work`
rechaza por defecto; `WorkAdmission._preflight` deriva rutas de escritura,
comandos y red del contrato efectivo. No se registró backend por esta decisión.
`/usr/bin/bwrap --version` devuelve 0.11.1 en este host. El [aviso upstream
GHSA-pxhw-h44j-8pfx](https://github.com/containers/bubblewrap/security/advisories/GHSA-pxhw-h44j-8pfx)
indica que CVE-2026-87766 permite traversal de symlink durante setup antes de
0.12.0. T097 exige >=0.12.0 si usa Bubblewrap, con rechazo previo al efecto
para 0.11.1 o versión desconocida. Este host queda sin backend Work apto; la
prueba local de mounts no acredita operación segura.

La aceptación propuesta exige allowlists ejecutables de filesystem, procesos,
red e IPC, proxy/secreto fuera de la visibilidad del agente y denegación
adversarial entre intentos hermanos. Debe probar el guard fresco en la frontera
del efecto real y el fallo cerrado ante capacidad ausente, grant revocado,
generación obsoleta, setup incompleto, proxy perdido y cleanup fallido; reinicio
y reemplazo no autorizan reissue ni redelivery. Sólo tras prueba en la
plataforma y contratos concretos puede registrarse el backend. Esta entrada
actualiza Spec Kit, sin código, pruebas, proveedor, DB del operador ni evidencia
de cierre de T097/T019; T035 conserva gate público separado.

### T097: bypass de comandos y decisión fail-closed — 2026-09-25

**T097 `[ ]`; T019 `[ ]` y bloqueado.** Los probes acotados con Bubblewrap
0.13 en WSL2 no constituyen prueba general ni cierran el gate. Con Landlock
`FS_EXECUTE`, un ELF estático allowlisted ejecutó; se rechazaron ELF en
`write_paths`, ELF con `PT_INTERP` cuando el loader no estaba permitido,
shebang con intérprete no permitido, `execveat` de `O_TMPFILE` (EACCES) y
re-exec no permitido desde un descendiente doble-fork. Al permitir `ld.so`, un
ELF dinámico en `write_paths` sí ejecutó; sin filtro seccomp, memfd ejecutó por
`execveat(AT_EMPTY_PATH)`. En una matriz separada Bwrap noexec+BPF, el ELF
dinámico provider con `PT_INTERP` y fork+re-exec pasaron. El filtro denegó
`memfd_create` con EPERM incluso en un descendiente y `execveat` también con
EPERM; el `O_TMPFILE` de `work` montado `noexec` no ejecutó y `/proc/self/fd`
y `/dev/fd` estaban enmascarados.
Los probes BPF anteriores también denegaron `PR_SET_PTRACER`, `unshare`,
`setns`, `clone3` y `clone(CLONE_NEW*)`; el BPF del proxy no denegó `ptrace`.

El proxy probe mostró que un sibling host UID 1000 fuera de la ascendencia pudo
hacer `PTRACE_ATTACH` y `pidfd_getfd` leer FD 0 incluso con
`PR_SET_DUMPABLE(0)`. `PR_SET_PTRACER=1` permitió sólo PID 1; negar
`PR_SET_PTRACER` tampoco impidió attach. `--uid 2000` siguió mapeado a host UID
1000 (`uid_map: 2000 1000 1`); ptrace no fue denegado por el BPF probado y el
proxy permanece fail-closed. El remount `MS_REMOUNT|MS_BIND|MS_NOEXEC` desde
userns Bwrap con `CAP_SYS_ADMIN` dio `EPERM`, mientras el checkout 9p permite
ejecución. Worktree bajo `/run/lock` sólo es posibilidad, no probado. No se
probaron memfd heredado por FD, ELF escribible de `/dev/shm` vía `ld.so` ni
inventario empírico de FDs heredados.

El contrato actual `commands: tuple[str]` no expresa ruta canónica, digest ni
requisito de ELF estático; los launchers actuales de proveedores pegan texto
shell y no admiten el subset directo probado. La decisión sigue siendo
rechazar antes de efecto los contratos Work actuales, incluidos los de
`commands=()` hasta probar deny-all y bootstrap. El backend permanece sin
registrar y `WORK_BACKENDS` vacío.

La revisión independiente encontró incompleto el binding de target en
`WorkBackendView`/guard para terminal, session y window (P1 para habilitar
Work). El fix está en curso y aún no se verificó.

En esta ronda, el fence durable de `/terminals/{id}/input` y `/key` más legacy
tuvo 20 passed; cubre rutas específicas, no la suite total. También pasaron los
gates focales de registration (5 passed), Bubblewrap adversarial (9 passed),
process supervisor (4 passed) y proxy (5 passed); `project-composition-check`
dio 4 kept / 0 broken. El supervisor evita redelivery, pero no puede reattach
ni conciliar cleanup tras reinicio. No se declara resuelto T097/T019. El
resultado queda limitado a las rutas probadas, sin prueba general de aislamiento.

Comandos locales de reproducción de los probes estáticos iniciales (scripts
scratch, no versionados):

- `/usr/bin/python3 /tmp/caos-exec/T097/static-research/run_seccomp_probes_no_native_userns_guard.py`
- `/usr/bin/python3 /tmp/caos-exec/T097/static-research/run_execveat_only.py`
- `/usr/bin/python3 /tmp/caos-exec/T097/static-research/run_tmpfile_proc.py`

Siguen abiertos el aislamiento del proxy MCP/socket y descriptor real por
intento, y la reconciliación durable de cleanup del supervisor tras reinicio.
T019 conserva además sus propios gates de credencial, lineage y ACK; T035
conserva el de entrada pública. Una pasada anterior de implementación reportó
una suite de **142 passed / 4 warnings**; es evidencia histórica, no resultado
de la ronda actual ni cierre de T097/T019. La revisión independiente de Sol fue
de sólo lectura y no ejecutó pruebas.

### T097: revisión formal del fence de target y cleanup acotado — 2026-09-26

**PASS WITH RISKS para el incremento fail-closed; T097 `[ ]` y T019 `[ ]`.**
La fuente actual liga los efectos de `WorkBackendView` al terminal durable del
intento y al par session/window previsto. El guard de `WorkAdmission` relee el
intento y la fila `terminals` antes de cada efecto protegido. Las rutas
ordinarias de input/key verifican propiedad Work durable, también tras reinicio.
La revisión independiente no encontró un bypass de este fence en los caminos
revisados. El RED inicial registró cinco fallos de efecto ajeno; el gate del
builder pasó 110 pruebas y la integración delivery 28.

La capacidad nueva de rollback por nombre se retiró tras revisar el límite de
identidad del servidor. `libtmux` obtiene `$N` atómicamente con
`new-session -P -F#{session_id}`, pero tmux sólo garantiza ese ID durante la
vida del servidor: el probe local de tmux 3.6 creó `$0`, reinició el servidor y
volvió a crear `$0`. `Session.cmd()` reenvía cada operación por el socket
configurado, así que un objeto viejo puede reconectarse al servidor nuevo y
dirigir el kill a esa reencarnación. El registro CAO sólo conserva nombres; por
eso `WorkBackendView` y el guard de `WorkAdmission` rechazan `kill_session`,
`kill_window` y rollback de sesión por nombre. Si falla un launch, el recurso
queda vivo para `reconcile` con `cleanup_state=not_requested`; las rutas
ordinarias de terminal conservan su comportamiento legacy. No se probó teardown
de tmux real ni recuperación de cleanup tras reinicio. Si el backend crea un
recurso pero falla antes de devolver el window exacto, o el cleanup no puede
probar la misma instancia del servidor, queda pendiente una conciliación durable.

El supervisor dejó de aceptar un entorno suministrado y lanza con `env={}`;
el RED mostró herencia del canary y aceptación de un mapa no validado, y el
GREEN focal en snapshot registró 7 passed. Su evidencia de FDs excluye un FD
heredable ajeno sólo en esa ruta. No reanexa el proceso después de reiniciar.
El snapshot final coincidió por SHA-256 con los 24 paths afectados del
worktree: **164 passed, 4 warnings, exit 0 en 31,74 s**. El check de
composición pasó con 274 archivos, 1016 dependencias y 4 contratos
conservados/0 rotos; `git diff --check` focal y la auditoría de whitespace
también pasaron.

`BubblewrapWorkBackend` sigue sin registrar y rechaza todos los contratos:
faltan política ejecutable de comandos frente a loader/memfd, montaje y
launcher integrados, red/IPC/paths adversariales y aislamiento real del
proxy/FD entre intentos. `WORK_BACKENDS` permanece vacío. T019 conserva además
credencial, hijo/handoff y ACK; T035 conserva ingreso público. Dictamen:
`specs/001-verifiable-orchestration/t097-formal-review.md`.

### T097: ABA de tmux y fallo parcial de create — 2026-09-26

**FAIL para teardown Work por nombre; T097 `[ ]` y T019 `[ ]`.** La conclusión
anterior sobre compensar la carrera de inserción sólo cubre SQLite y backend
en memoria. La revisión de composición encontró que el token de rollback y
el guard durable identifican session/window por nombre, sin identidad de la
instancia tmux. Si la sesión desaparece y otra toma el mismo nombre antes del
kill, rollback puede destruir la nueva; una fila durable obsoleta también
puede autorizar `kill_session` sobre la nueva sesión. `kill_window` comparte
el riesgo de reutilización del nombre. El match exacto evita colisiones de
prefijo, pero no este ABA.

Un servidor tmux 3.6 privado devolvió `$0`; tras `kill-server`, una nueva
instancia devolvió otra vez `$0`. Por tanto, `$session_id` sin fence durable de
instancia de servidor tampoco prueba propiedad. El probe scratch
`/tmp/caos-exec/T097/tmux_post_create_failure_test.py` simuló que
`new_session` creó `$1` y falló al hidratar libtmux: **RED, 1 fallo**; el
cleanup intentó usar el nombre y el recurso quedó presente. No hay GREEN ni
teardown real acreditado para ese camino. Hasta tener identidad de recurso e
instancia de servidor verificable, rollback, `kill_session` y `kill_window`
Work deben rechazar el efecto y conservar `reconcile`; ninguna limpieza por
nombre o `$session_id` aislado acredita T097. El delta correctivo actual
rechaza rollback, `kill_session` y `kill_window` Work antes de tmux y conserva
el intento incierto para conciliación. La limpieza legacy sigue su ruta
anterior. El probe de fallo de hidratación de `TmuxClient` continúa como RED
sin GREEN ni recuperación demostrada; el rechazo Work no lo convierte en un
teardown resuelto.

Root verificó SHA-256 idéntico para **28 paths** de fuente/tests entre
worktree y `/home/felni/.cache/caos-t097-ext4-shadow`. La suite T097
consolidada, incluidos los dos archivos de caracterización Bubblewrap con
un build scratch 0.13.0 verificado, terminó **164 passed, 4 warnings,
0 skipped, exit 0 en 83,65 s**. Sus 12 probes ejecutados caracterizan
bypasses, no aceptan el backend. `project-composition-check caos` pasó:
274 archivos, 1016 dependencias y 4 contratos conservados/0 rotos;
`git diff --check` salió 0 y la auditoría whitespace final pasó 31/31.
Logs: `/tmp/caos-exec/T097/final-affected-pytest.log` y
`/tmp/caos-exec/T097/final-composition.log`. El host conserva Bubblewrap
0.11.1, sin capacidad Work; el build scratch no registra backend. Bubblewrap y
el proxy siguen fail-closed, con aislamiento de UID/FD entre intentos,
política de comandos y cleanup durable pendientes. Un probe rootless del
2026-09-26 corrió con UID host 1000 y `/proc/self/uid_map` limitado a
`0 1000 1`; faltaba `newuidmap`. `unshare --user --map-auto` salió 127
(`newuidmap` no encontrado); un `unshare --user --map-root-user` anidado con
`unshare --user --setgroups=deny --map-users=0:1:1` salió 1
(`uid_map: Operation not permitted`), y `setresuid(1,1,1)` en el namespace
de una sola entrada falló con EINVAL. Esto no proporciona UID host distinto
por intento. [user_namespaces(7)](https://man7.org/linux/man-pages/man7/user_namespaces.7.html)
explica que los UID sin mapeo se muestran como overflow UID en la mayoría de
las vistas (por defecto 65534); ese valor visible no prueba un UID host
distinto y mapeado.

El preflight de comandos añadido a `BubblewrapWorkBackend` es sólo sintáctico:
rechaza fragmentos shell, nombres relativos, rutas no normalizadas y `//`
inicial antes incluso de `_probe_bubblewrap`. Un token absoluto canónico como
`/usr/bin/python3` supera sólo esa forma, llega al probe de versión y sigue
rechazado por `no command contract is currently supportable`. No prueba
identidad del ejecutable ni confinamiento de exec descendiente. Root ejecutó
en `/home/felni/.cache/caos-t097-ext4-shadow` el gate de cinco módulos
Bubblewrap/enforcement/registration: **107 passed, 0 skipped en 9,54 s**. El check de
composición fresco pasó con 274 archivos, 1016 dependencias y 4 contratos
conservados/0 rotos. La suite 164/4 anterior precede este incremento y es
evidencia histórica; el nuevo gate sólo acredita rechazo acotado. T097/T019
siguen `[ ]`. No se usó proveedor ni DB del operador. Dictamen actualizado:
`specs/001-verifiable-orchestration/t097-formal-review.md`.

### T097: rechazo de red previo al probe y salida natural incierta — 2026-09-26

**PASS de revisión para el incremento acotado; FAIL para aceptación T097.
T097 `[ ]` y T019 `[ ]`.** `BubblewrapWorkBackend.preflight_work` valida primero la
sintaxis del comando, rechaza `contract.network` no vacío con
`UnsupportedWorkEnforcement` antes de `_probe_bubblewrap`, y mantiene el
rechazo incondicional de contratos válidos después del probe. El test RED
observó que el ejecutable falso `--version` creaba un marcador; el GREEN
exige el motivo `non-empty network contracts have no supported grammar`
y la ausencia del marcador. La revisión independiente fue PASS para este
orden y el límite fail-closed. El builder registró **21 passed** en el
módulo focal de Bubblewrap; root registró **108 passed** en la suite
combinada backend/seguridad. Esto no demuestra aislamiento de red ni
habilita el backend.

El test de caracterización del supervisor observa `UNCERTAIN` seguido
de salida natural: `wait` confirma `TERMINATED`, código 0 y resultado
del hijo, y libera el bloqueo de este supervisor. No añade fuente
productiva ni acredita reattach o cleanup durable tras reinicio. En el
checkout actual, `test/services/test_work_process_supervisor.py` registró
**7 passed**. El intento de ejecutar la suite completa del supervisor
en el snapshot ext4 produjo **4 errores antes de la colección** por
`PermissionError: /mnt/c/DumpStack.log.tmp`; ese intento no aporta
evidencia PASS. `project-composition-check caos` pasó: **274 archivos,
1016 dependencias, 4 contratos conservados/0 rotos**. Ningún proveedor
ni DB del operador se usó; T097/T019 siguen abiertos.

### T097: coordinación de binding con envíos de terminal — 2026-09-26

El lock interprocesal compartido serializa el binding admitido de Work con
`send_input` y `send_special_key`: dispatch cierra su snapshot antes de esperar,
revalida el target bajo la transacción de escritura, y los envíos conservan el
lock hasta terminar transporte y CAS del recibo. Se libera antes de actividad,
telemetría y callbacks; una regresión verifica `send_input` reentrante al mismo
terminal desde el callback. Ownership-fence y launch: **29 passed**; composición:
**4 kept / 0 broken**. La verificación ampliada de terminal más
`test/integration/test_work_admission.py` pasó **153 passed**. El único test
inicialmente rojo tenía un setup obsoleto sin terminal durable; se corrigió
sólo el test, sin cambiar el guard productivo. Pruebas con SQLite temporal,
sin DB de operador ni proveedor real. Esto cierra sólo la carrera de
coordinación: no demuestra aislamiento, identidad de ejecutable, confinamiento
de descendientes ni recuperación tras reinicio. `WORK_BACKENDS` sigue vacío;
T097 y T019 permanecen `[ ]`.

### T097: inventario noexec y limpieza de pruebas con pidfd parcial — 2026-09-26

**PASS para el incremento acotado de caracterización; FAIL para la aceptación
T097. T097 `[ ]` y T019 `[ ]`.** Revisión independiente de los dos archivos de
test: el módulo de supervisor ejerce fallo de apertura del pidfd de PID 1,
fallback, reattach y `UNCERTAIN` intermedio. Comprueba que una prueba tardía
de salida cierra el pidfd parcial original sin convertir el intento original
en `TERMINATED` sin su propia prueba. El inventario aislado con Bubblewrap
scratch 0.13.0 comprueba los mounts superiores `rw+noexec` de workspace,
`/tmp` y `/dev/shm`, runner `ro+exec`, inventario `rw+exec=[]`, y seis pruebas
de alias `/proc/self/fd/3`: tres exec directos denegados y tres cargas por
loader fallidas, sin marcadores. Son probes privados, no un launch Work.

Evidencia fresca comunicada y contrastada con las aserciones: módulo
`test/services/test_work_process_supervisor.py` **20 passed en 11,84 s**;
suite consolidada de 12 módulos **151 passed en 23,43 s**; inventario aislado
**1 passed en 4,49 s**; composición **275 archivos, 1017 dependencias,
4 kept, 0 broken**; whitespace limpio en ambos tests. No cambió código
productivo, no se usaron proveedor ni DB del operador y no hubo commit.
`WORK_BACKENDS={}` y `BubblewrapWorkBackend.preflight_work` continúa rechazando
todos los contratos antes del efecto. Faltan contrato tipado de ejecutable,
digest/ELF estático inmutable, Landlock y noexec integrados en launcher,
allowlist de FD, aislamiento del proxy hermano, limpieza y recuperación
durables. Graphify existente se consultó y verificó contra fuente; decisión:
sin refresh para un cambio sólo de pruebas. Dictamen:
`specs/001-verifiable-orchestration/t097-formal-review.md`.

### T097: modelo ELF, Landlock y staging auxiliares — 2026-09-26

**PASS acotado del incremento auxiliar; FAIL para aceptación T097. T097 `[ ]`
y T019 `[ ]`.** Revisión independiente de los ocho paths de modelo/parser/
Landlock/staging y tests. V2 es descriptivo, separado de la persistencia v1:
sólo admite ELF estático `x86_64/ELF64/little`, token de comando canónico,
digest `sha256` y mapping 1:1 con `permissions.commands`; el hash v1 conserva
la huella fijada en test. El parser exige `PT_LOAD` ejecutable y entrada en
bytes cargados, y rechaza `PT_DYNAMIC`/`PT_INTERP`. Staging compara el
snapshot con esa identidad, sella el memfd y confirma su contenido y objeto.
Landlock aplica `FS_EXECUTE` por ruta y hereda a descendientes; no restringe
objetos ya abiertos ni constituye identidad ejecutable.

La prueba de memfd sellado registra **EBADFD=77** al añadir su `O_PATH` como
regla Landlock. Otra prueba, con regla para archivo regular y seccomp, pasa
deliberadamente un memfd no listado al hijo: ejecuta por `/proc/self/fd` con
**exit 43**. Ambas son evidencia de que staging y Landlock aún no forman una
cadena segura. No hay callers de estas piezas en admisión ni supervisor; éste
sigue usando `os.execvpe(argv[0], ...)`. `WORK_BACKENDS={}` y Bubblewrap
preflight rechaza todo. El host tiene Bubblewrap 0.11.1, inferior al mínimo
0.12.0; falta `newuidmap` para acreditar UID host distinto por intento y el
proxy same-UID sigue sin aislamiento demostrado. Continúan pendientes
launcher integrado, FDs, rutas/red/IPC, proxy y cleanup durable.

Evidencia final root: suite explícita de 18 módulos **304 passed, 4 warnings,
0 skipped, exit 0 en 36,64 s**; staging **10 passed, 0 skipped**; Landlock
**8 passed**; Black `--check` de ocho paths PASS; búsqueda de whitespace sin
matches; `project-composition-check caos` **278 archivos, 1020 dependencias,
4 kept, 0 broken**. Sin proveedores, DB del operador ni commit. Consulta
Graphify existente contrastada con `WorkAdmission`, supervisor y registro
reales: módulos auxiliares sin nuevo call path productivo. Se difiere refresh
por el árbol muy dirty; la decisión no amplía capacidad Work. Dictamen:
`specs/001-verifiable-orchestration/t097-formal-review.md`.

### T097: recuperación observacional de cleanup de procesos — 2026-09-27

**PASS para este incremento acotado; FAIL para aceptar T097. T097 `[ ]` y
T019 `[ ]`; `WORK_BACKENDS={}`.** `WorkProcessSupervisor.original_pair_terminated`
acepta únicamente la identidad durable v3 y prueba terminación usando boot ID,
PID, starttime y estado del proceso. `recover_process_cleanup`, cuando no recibe
un supervisor explícito, observa tanto identidades v3 de proceso como intentos
Bubblewrap; sólo marca `cleanup_state=complete` tras probar que ambos procesos
originales terminaron. Procesos aún vivos, errores y estados inciertos conservan
`failed`/`reconcile` y el scheduler held. En esa ruta observacional no hay
reattach, `pidfd_open`, señal ni redelivery. Con supervisor explícito sigue la
limpieza activa existente, con revalidación de identidad y pidfd.

El RED de Sol confirmó que omitir v3 dejaba el intento fallido (**1 fallo**).
La primera revisión halló un riesgo importante: Bubblewrap sin supervisor; RED
también detectó `pidfd_open`. Ambos se corrigieron. La revisión independiente
final de Sol marcó el hallazgo **ADDRESSED** y no encontró otros. Verificación
fresca de root: cuatro módulos (`test_work_process_cleanup_recovery.py`,
`test_work_bubblewrap_cleanup_recovery.py`,
`test_work_process_supervisor.py` y `test_work_process_restart_contract.py`),
**75 passed en 40,81 s**; Black `--check` en cuatro paths sin cambios;
`project-composition-check caos` PASS (**283 archivos, 1033 dependencias,
4 kept / 0 broken**); búsqueda de whitespace focalizada sin coincidencias.

Este slice no completa T097: Bubblewrap sigue sin registrar y rechaza contratos;
`WORK_BACKENDS={}` y T097/T019 siguen `[ ]`. No se usaron proveedores ni DB del
operador; no hubo commit. El `git diff --check` global salió con código 2 por
reportes CRLF preexistentes/ajenos; no se normalizaron esos archivos. Dictamen:
`specs/001-verifiable-orchestration/t097-formal-review.md`.

### T097: errores de poll en espera pidfd — 2026-09-27

**PASS para este incremento acotado; T097/T019 siguen `[ ]`.** Se corrigió
`_wait_pidfd_readable` en `work_bubblewrap_composition.py`: los eventos de error
`POLLERR` y `POLLNVAL` ya no se interpretan como salida del proceso, incluso
cuando vienen mezclados con `POLLIN`/`POLLHUP`. La ruta falla de forma cerrada;
una notificación de error no certifica cleanup. RED paramétrico: **2 fallos**;
GREEN focal: **5 passed**; módulo completo: **34 passed en 27,02 s**. La revisión
independiente de Sol fue **PASS**, sin hallazgos Critical, Important ni Minor.
Root repitió el módulo (**34 passed**), Black `--check` en dos paths sin cambios,
composición PASS (**283 archivos, 1033 dependencias, 4 kept / 0 broken**) y
búsqueda focalizada de whitespace sin coincidencias.

La evidencia cubre sólo este helper y la composición scratch: no integra
`launch_staged_static_elf` en `WorkAdmission` ni habilita el backend. Bubblewrap
sigue sin registrar, `WORK_BACKENDS` permanece vacío y T097/T019 continúan
abiertas. Sin proveedores, DB del operador ni commit.

### T097: pidfd tri-state y recuperación supervisada de huérfanos — 2026-09-27

**PASS para el incremento acotado; T097/T019 continúan `[ ]`.** El resultado del
helper pidfd ahora es tri-state: `POLLERR`/`POLLNVAL` => `None`; únicamente un
timeout limpio (`False`) autoriza el paso pre-señal; para confirmar finalización
post-señal se exige `True`. Con supervisor explícito, si el monitor desapareció
o su PID fue reutilizado, la recuperación puede terminar el init sobreviviente
del namespace exacto tras revalidar boot ID/PID/starttime/namespaces y el target
del pidfd. Sin supervisor permanece observacional. Se documentó la carrera de
arranque PDEATHSIG de Bubblewrap `--die-with-parent`; la integración actualizó
ambos resultados y el gate de ownership para el PIDFD propiedad del test.

RED del caller: **6 fallos esperados**; RED de huérfano: **1 fallo**; GREEN focal:
**18 passed**, incluidos seis casos negativos; módulo completo actualizado:
**33 passed**. Root verificó una suite fresca de cuatro módulos (**92 passed en
46,08 s**), Black `--check` en seis archivos unchanged, composición PASS
(**283 archivos, 1033 dependencias, 4 kept / 0 broken**) y búsqueda focalizada
de whitespace sin coincidencias. Revisión independiente Sol: **PASS**.

El incremento no habilita el backend: `WORK_BACKENDS={}` y Bubblewrap sigue sin
registrar y fail-closed. T097/T019 permanecen `[ ]`.

### T097: snapshots de start y reattach estricto del supervisor — 2026-09-27

**PASS para el incremento del supervisor; T097/T019 siguen `[ ]`.** Durante
`start()` se comparan los snapshots estructurales tomados con
`strict_monitor_argv=False` antes de aceptar identidad; luego se valida argv
exacto. `reattach()` conserva la validación estricta contra la identidad
persistida. Si falta identidad no se señaliza ni se confirma salida. El
resultado pidfd conserva el tri-state. El aborto evita invertir el orden de
`_lock`/`_wait_lock` mediante `RLock` y adquisición no bloqueante; si otro
waiter impide comprobar, el intento queda `UNCERTAIN`.

Verificación fresca: **104 tests focales pasan**; Black `--check` en dos
archivos unchanged; composición PASS (**283 archivos, 1033 dependencias,
4 contratos kept, 0 broken**); revisión Sol **PASS**. No se habilita
`WORK_BACKENDS`; T097/T019 permanecen abiertos.

### T097: revalidación de contenido ejecutable en snapshot — 2026-09-27

**PASS para el incremento acotado; T097/T019 siguen abiertos.** El servicio
`WorkExecutableContent` vuelve a ejecutar `WorkContracts._revalidate_order`
dentro de `read_snapshot` antes de seleccionar V2, el token o el catálogo. Las
lecturas del store y el staging continúan después de cerrar el snapshot. El RED
mostró que los casos revoked, expired, replaced y terminal alcanzaban el read;
la revalidación impide ese acceso obsoleto. La prueba root focalizada de
`executable_content`, `contract_binding` y `executable_staging` pasó **80 tests**;
Black `--check` en dos archivos unchanged; composición PASS (**283 archivos,
1033 dependencias, 4 kept / 0 broken**); peer review Sol **PASS**.

Esto no integra el helper en `WorkAdmission` ni en el launcher, ni acredita
autoridad al release. `WORK_BACKENDS={}`; T097/T019 siguen abiertos.

### T097: validación de identidad ELF en argumentos Bubblewrap — 2026-09-27

**PASS para este slice acotado; T097/T019 siguen abiertos.**
`bubblewrap_bind_data_arguments` ahora valida que la identidad mutable coincida
con el ELF en el memfd sellado antes de construir argv. El límite de **1..8 MiB**
se comprueba antes de leer; `_read_exact` usa `pread` y preserva el cursor. RED
confirmó que mismatch de identidad y memfd disperso mayor de 8 MiB no fallaban
antes del fix (**DID NOT RAISE**). La suite root de args, staging, Bubblewrap
composition, bound content, executable content y contract binding pasó **131**;
Black `--check` en dos archivos unchanged; composición PASS (**283 archivos,
1033 dependencias, 4 kept / 0 broken**); revisión peer Sol **PASS** en seguridad.

Detalle retenido para diagnóstico: `OSError` de `pread` conserva `errno` y se
propaga; el fallo es cerrado. El helper no concede autoridad de launch ni se
integra en `WorkAdmission`. `WORK_BACKENDS={}`; T097/T019 siguen abiertos.

### T097: lock de ownership para cleanup — 2026-09-27

**PASS para el slice acotado; T097/T019 siguen abiertos, sin GO ni registro.**
Un lock advisory por intento/generación ahora se comparte entre
`recover_process_cleanup` y `cleanup_attempt`. La contención se rechaza antes de
acceder a la identidad y no hay transacción SQLite mientras se espera cleanup.
Un crash ordinario del proceso deja el intento pendiente para que otro proceso
lo reanude; si falta `fcntl`, falla cerrado. La reservation se conserva durante
la operación. Un hijo creado sólo por `fork` puede heredar y retener el lock,
retrasando recuperación; es fail-closed intencional.

Verificación root: **75 tests focales pasan**; Black `--check` en tres archivos
unchanged; composición PASS (**283 archivos, 1033 dependencias, 4 kept / 0
broken**); revisión independiente **PASS**. `WORK_BACKENDS={}` y no se registra
ni habilita backend.

### T097: intención durable de setup previa a GO — 2026-09-27

**PASS acotado del slice v30→v31; T097/T019 siguen `[ ]`.** La nueva
operación interna `WorkBubblewrapSetupIntent.record_pre_go` une autoridad viva,
revisión exacta de intento `sent`, contrato V2, token/digest/catálogo, ACK
canónico ≤8192 bytes e identidad Bubblewrap con `release_intent=pending` en una
transacción `BEGIN IMMEDIATE`. La migración v31 añade FK compuesta e
inmutabilidad; replay idéntico no duplica filas, fallo revierte ambas filas y
`read_historical` verifica evidencia sin autoridad actual ni permiso de GO.
Se conserva la API pública previa de identidad.

Revisión independiente **PASS** y gate root fresco: **110 tests passed**,
Black en **5 archivos**, whitespace localizado limpio y composición **PASS**
(285 archivos, 1037 dependencias, 4 kept, 0 broken). Graphify previo se
consultó y contrastó con fuente. Refresh intentado: corpus 248 code/20 docs
sin API semántica; `--code-only` completó AST, pero quedó atascado en rutas
9p y se interrumpió (exit 130); `graph.json` y `.graphify_ast.json` intactos.
Refresh diferido. No existe integración a GO/release/backend ni registro:
`WORK_BACKENDS={}`; T097/T019 continúan abiertos.

### T097: aceptación C07 de proxy concurrente y recuperación — 2026-09-28

La suite de host terminó **8 passed, 0 skipped en 335,30 s** en Ubuntu
26.10/QEMU TCG, ejecutada como `caos-work-broker` (no root, `nologin`, sin
procesos host ajenos al inicio). Dos launches reales alcanzaron a la vez sus
endpoints MCP aislados; la recuperación reabrió el mismo SQLite mediante
repositorio/gateway nuevos y confirmó efecto `uncertain`, una llamada upstream
y cero redelivery. Se resolvió la frontera de identidad: cuenta OS dedicada y
exclusiva; el UID remapeado por user namespace no protege contra procesos host
del mismo UID fuera de namespaces.

En TCG se usó el override de timeout 45 s para ambos casos del proxy porque las
pruebas de aislamiento exceden el límite normal de 10 s bajo emulación. Queda
pendiente repetir el gate en el runner Linux de despliegue sin override. No hay
aceptación de ese runner en esta sesión; `WORK_BACKENDS={}` y T097/T019 siguen
abiertos.

### T097: enforcement de identidad broker y workflow Linux — 2026-09-28

`BubblewrapWorkBackend` captura `CAO_WORK_BROKER_ACCOUNT` al construirse y
`preflight_work` verifica cuenta local, UID no root e igual al eUID y shell
resuelto `nologin`/`false` antes de consultar Landlock o ejecutar/probar
Bubblewrap. La suite unitaria prueba config ausente/vacía/malformada, root,
cuenta ausente/root, UID distinto, shell de login y symlink a shell de login:
**11 passed**. El fixture host y el comando de aceptación usan la misma
variable.

Se añadió `.github/workflows/t097-host-acceptance.yml`, sólo main y
dispatch de main, con permisos mínimos y runner self-hosted separado
`cao-t097-host`; ejecuta la suite como broker sin override de timeout. El
workflow parsea como YAML. Provisionamiento del runner, variable Actions y
sudo siguen pendientes fuera del checkout.

Repetición de la aceptación con el código actual en Ubuntu 26.10/QEMU TCG,
Landlock ABI 11 y Bubblewrap 0.13.0 bajo UID 999: **8 passed, 0 skipped en
327,65 s**, con `CAO_WORK_BROKER_ACCOUNT=caos-work-broker` y override TCG 45 s.
El runner de despliegue no está provisionado y su workflow aún no se ha
ejecutado; T097/T019 siguen abiertos, `WORK_BACKENDS={}`.

Regresión local focal: **71 passed**. La selección ampliada terminó 420 passed,
8 deselected y un fallo preexistente fuera de T097: `TestTmuxBackendDelegation`
espera que `send_keys` omita `plain_shell=False`, pero el `TmuxBackend`
actual (ya modificado antes de este trabajo) sí lo envía. Se conserva sin
cambios por ser ajeno a esta auditoría.

### T097: migración de aceptación a runner GitHub-hosted — 2026-09-28

Se actualizó `T100` y `.github/workflows/t097-host-acceptance.yml` para usar
primero `ubuntu-24.04`: verifica el SHA-256 upstream del source archive Bubblewrap
0.13.0, compila sin privilegios, instala `/usr/bin/bwrap` root:root 0755,
prepara los sysctls requeridos sólo dentro de la VM efímera y crea la cuenta
broker no interactiva. La suite corre como broker con timeout normal; el job
falla salvo `8 passed` y rechaza skips/xfails. La aceptación cubre este perfil
de referencia, no declara compatibles kernels de despliegue arbitrarios.

Verificación local: el YAML parseó con dos jobs y los scripts embebidos pasaron
`bash -n`; no hay override de `T097_TEST_WORKER_TIMEOUT_SECONDS`. El SHA-256
del archive descargado coincidió con el valor fijado.
`test/backends/test_work_broker_identity.py` → **11 passed**.
`project-composition-check caos` → **289 archivos, 1072 dependencias, 4
contratos conservados, 0 rotos**. `git diff --check` pasó.

El workflow se publicó en `origin/main` como `19922123`. El primer run hosted,
`36445140172`, construyó Bubblewrap y configuró el kernel, pero falló al validar
el acceso del usuario broker al checkout; la suite T097 todavía no se ejecutó.
Se corrigió el workflow para dar al broker acceso de grupo de solo lectura al
checkout y permiso de recorrido en sus directorios padre. El segundo run,
`36447165686`, pasó el acceso al checkout y falló cerrado en el preflight
compartido por los ocho casos porque el Bubblewrap compilado aún no estaba
allowlisted. El log confirma archive
SHA-256 `4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765`,
versión `0.13.0`, instalación `root:root 0755`, GCC `13.3.0` y Meson `1.3.2`
en Ubuntu `24.04.5` image `20260920.314.1`. Tras revisar esas comprobaciones,
se añadió a la allowlist el digest resultante
`a5882b87c0b8105a5d9e81db5f64a4f8d373affc36f531e5df7409db5b1af8f6`. Falta
repetir hasta obtener un run verde. El tercer run `36448339608` confirmó que
esa imagen ofrece Landlock ABI 7; el código exige ABI >= 9, por lo que los ocho
casos fallaron cerrados antes de ejecutarse. Se cambió el job de aceptación a
GitHub-hosted `ubuntu-26.04` y se añadió un paso explícito que registra/rechaza
ABI menor que 9. El workflow actualizado aún no está publicado ni medido en
GitHub Actions. `T101` sigue `[ ]`,
`WORK_BACKENDS={}` y T097/T019 continúan abiertos.

### T097: guest Ubuntu fijado para aceptación hosted — 2026-09-28

La ejecución nativa posterior `36450282433` en `ubuntu-26.04` falló cerrada
antes de correr la suite: el kernel ofrece Landlock ABI 8, menor que el mínimo
9. No se rebajó ese requisito.

T100 ahora usa GitHub-hosted `ubuntu-24.04` sólo como orquestador de un guest
Ubuntu 26.10 efímero bajo QEMU TCG. El script fija SHA-256 de la imagen cloud y
del source archive Bubblewrap 0.13.0, prepara kernel y cuenta broker dentro del
guest, y exige ABI >= 9, digest allowlisted y ocho casos sin skips. En el
momento de esta entrada aún no había run QEMU hosted verde; T101 y C08 seguían
pendientes. El guest no certificará
el kernel runner ni uno de despliegue; `WORK_BACKENDS={}` y T097/T019 siguen
abiertos para producción.

### T097: primer run QEMU hosted — 2026-09-28

El run `36454812599` pasó el guard de `main`, instaló QEMU, arrancó el guest
Ubuntu fijado y compiló Bubblewrap. Falló antes de ejecutar pytest: `uv sync`
no encontró `scripts/hatch_build_tui_tag.py`, declarado como build hook en
`pyproject.toml`. El archivo del checkout transferido omitía `scripts/`.

El script ahora incluye el hook, `README.md` (declarado en `pyproject.toml`) y
`LICENSE` en el archive, y fija `CAO_TUI_AUTOBUILD=0` durante el sync: T097 no
necesita compilar el TUI. En ese punto faltaba
repetir el run con ese archive completo; el intento cancelado y el fallido no
cuentan como aceptación ni cambian el estado T101/C08.

### T097: digest revisado en el primer run con archive completo — 2026-09-28

El run [`36457607699`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36457607699)
(`8d93e018`) arrancó la imagen Ubuntu 26.10 fijada en QEMU, registró kernel
`7.3.0-5-generic` y Landlock ABI 11, y completó `uv sync`. El checksum del
source archive oficial Bubblewrap 0.13.0 coincidió con el valor fijado
`4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765`. El build
usó GCC `15.3.0-4ubuntu1` y Meson `1.10.1`; instaló `0.13.0` como `root:root
0755`. Los ocho fixtures rechazaron el digest nuevo
`15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250` antes de
ejecutar sus cuerpos. Revisados el source archive y la configuración del build,
se añadió sólo ese hash exacto a la allowlist. Ese run aún no era aceptación:
T101 y C07/C08 seguían pendientes de una repetición con ocho casos pasados y sin
skips. `WORK_BACKENDS={}` y T097/T019 siguen sujetos a la aceptación del host
de despliegue.

### T097: suite guest hosted pasada, post-job de caché falla — 2026-09-28

El run [`36460464186`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36460464186)
(`48909237`) usó el digest revisado y registró Landlock ABI 11. La suite
`test/integration/t097 -m t097_host` terminó **8 passed, 0 skipped en
201,67 s** bajo la cuenta broker configurada. El workflow falló después, en el
post-job de `setup-uv`, porque quiso guardar
`/home/runner/work/_temp/setup-uv-cache`, que no existe en el host: uv y las
dependencias se usan dentro del guest QEMU. El commit `7135d91a` desactivó el
cache de host; el resultado final consta a continuación. T101 y el cierre de
C07/C08 esperaban ese run; `WORK_BACKENDS={}` y T097/T019 siguen sujetos a la
aceptación del host de despliegue.

### T097: aceptación hosted final en guest QEMU — 2026-09-28

El run [`36463930292`](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/36463930292)
(`7135d91a`) terminó con **workflow global verde**. Se desactivó la caché host
de `setup-uv`, que no se usa porque la instalación ocurre dentro del guest.
Ubuntu 26.10 en QEMU registró kernel `7.3.0-5-generic`, Landlock ABI 11 y
Bubblewrap 0.13.0, instalado `root:root 0755`, con digest revisado
`15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250`.
La suite `test/integration/t097 -m t097_host` pasó como cuenta broker:
**8 passed, 0 skipped en 288,11 s**.

T101 y C07/C08 quedan cerradas con el perfil guest reproducible. **Decisión de
cierre T097 — 2026-09-28:** no existe ni se prevé un host de producción; se
cierra T097 con esta aceptación y el backend permanece sin registrar. Work no
se habilita. Al corte del 2026-09-28, T019 seguía abierto y diferido hasta elegir
un target; ese estado fue supersedido por el cierre local documentado abajo.

Se revisó la opción gratuita. Este repositorio es público y los runners
estándar de GitHub Actions son gratuitos, pero cada runner es efímero y se
desmantela al terminar el job; sirven para la aceptación repetible ya pasada,
no como host de producción ([runner y cobro de Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job)).
OCI publica VM Always Free, pero advierte falta temporal de capacidad y que
puede reclamar instancias inactivas ([límites Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)).
Google Cloud limita su cuota Always Free a una `e2-micro` en una de tres
regiones de EE. UU. ([cuota gratuita de Compute Engine](https://cloud.google.com/free/docs/free-cloud-features)).
Ninguna se adopta como host de producción. La opción gratuita cubre las
pruebas; no hay una opción gratuita fiable seleccionada para operar Work.

### T019: integración Docker local completada — 2026-09-29

Se cerró T019 para su alcance local acordado: Docker cumple como runtime Work
por intento y como anfitrión de la aceptación Bubblewrap. El backend no se añade
a `WORK_BACKENDS` y no existe aceptación de producción.

- `./test/integration/t019/run-docker-backend-acceptance.sh`: **7 passed** en
  Docker Desktop 29.8.1 / WSL2. La suite cubrió worker estático, rechazo antes
  del efecto, admisión de hijo por `cao.work.*`, aceptación/receipt exactos del
  receptor, aislamiento de dos workers concurrentes, recovery con objetos
  nuevos y SIGKILL del owner mientras worker/issue MCP estaban activos, sin
  redelivery. El runner fija el daemon local por socket Unix y
  rechaza endpoint/contexto remoto antes de construir o enviar imágenes.
- `./test/integration/t019/run-docker-acceptance.sh`: **8 passed, 0 skipped**
  en guest Ubuntu 26.10 bajo QEMU TCG. Guest kernel `7.3.0-5-generic`, Bubblewrap
  0.13.0 con digest
  `15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250` y
  Landlock ABI 11. Docker Desktop registró el kernel WSL2
  `6.18.33.2-microsoft-standard-WSL2`; no se atribuye ese kernel al guest.
- Suite relacionada: **177 passed, 4 skipped**; los skips son pruebas T098 que
  requieren host Linux nativo. La suite `test/services/test_work_delegation.py`
  pasó por separado: **3 passed**.
- `git diff --check`: limpio. `project-composition-check caos`: 4 contratos
  conservados, 0 rotos.

El worker Docker tiene un ELF estático, sin mounts del host, red, herramientas
locales arbitrarias ni rutas de escritura. El socket MCP privado por intento
permite sólo `cao.work.*` autorizado; `WorkOrigins`
autoriza sólo hijo/receptor preprovisionados y valida la aceptación exacta.
El adapter `agent_step` sólo forma parte del gateway interno sellado. No se
habilita ingreso público, el camino legacy `utils/orchestration.py`, proveedor
real, base de datos del operador ni despliegue.

### T019: convergencia local — evidencia final de 2026-09-29

La aceptación recién repetida con la recuperación tras reinicio pasó **7
passed en 22,44 s**, con Docker 29.8.1 y
`DOCKER_HOST=unix:///var/run/docker.sock`. El caso de hermanos concurrentes
prueba desde workers reales que el endpoint MCP del intento ajeno no se puede
abrir con `/proc/<pid>/fd/3` ni `pidfd_getfd`; las observaciones registran sólo
errno/namespace y ningún secreto. Se comprueban el cierre durable
`issued → abandoned` cuando no hubo efecto y el aislamiento de IDs/canarios por
intento.

El caso de cleanup incierto deja deliberadamente un container y una imagen etiquetados
tras fallar la observación inicial de cleanup. Un `WorkRepository` y backend
nuevos concilian sólo la identidad exacta, confirman ambos flags de eliminación,
conservan el intento en `reconcile`, completan `cleanup_state` y verifican que
el gateway no redespacha. El `finally` reconcilia cualquier artefacto si una
aserción falla.

Una prueba adicional lanza el gateway en un proceso owner separado, espera que
el worker Docker esté activo y su issue MCP permanezca `issued`, y envía SIGKILL
al owner. Un recovery con repository/backend nuevos avanza el lease de la DB
temporal, marca `reconcile`, elimina por labels exactos, cierra la issue como
`abandoned` sin crear effects y comprueba que el gateway no reenvía. Docker
Desktop puede detener el attach cuando desaparece el cliente; la prueba verifica
el artifact residual, no que continúe ejecutándose tras el SIGKILL.

La regresión T110 se reprodujo en RED: dos casos trataban el error de inspect de
container/image como ausencia y alcanzaban comandos de build/cleanup. Tras usar
el clasificador explícito de ausencia en dispatch y conciliar por identidad si
falta el ID del container, el conjunto focal pasó **4/4**.

Regresiones focales ya ejecutadas sobre el diff actual: Docker/backend,
Bubblewrap, tmux, autoridad de launch, proyección y continuación: **86 passed**;
recovery process/bundle/integration y compatibilidad OpenCode local: **127
passed, 1 skipped** (el OpenCode 2.0.18 instalado no implementa `agent list`; se
verifican sus archivos/configuración instalados). Migraciones de inventario
seleccionadas: **7 passed**. El skip restante no simula una prueba de producción.
La composición pasó **4 contratos conservados, 0 rotos** (294 archivos/1098
dependencias). La suite completa se registrará tras el gate final.
