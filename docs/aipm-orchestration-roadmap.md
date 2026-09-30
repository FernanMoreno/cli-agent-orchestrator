# Hoja de ruta: trabajo colectivo verificable

## Estado local — 2026-09-30

Los specs001/002 y las tareas de 003 están completados dentro del alcance
acordado. El candidato T085 está incorporado en el checkout main conservando
trabajo local; la historia Git no cambió. OpenCode v2 acepta 18 combinaciones
autorizadas tmux/Herdr, y dos workflows Claude pasaron tras renovar el login.
El servicio personal persistente y autenticado funciona en localhost de este
equipo, con reinicio automático, provisión Docker y backup/restore comprobados.
[Estado vigente y límites](../specs/001-verifiable-orchestration/acceptance-status.md),
[evidencia 003](../specs/003-personal-deployment/completion-evidence.md) y
[guía operativa](personal-deployment.md).
Los diez proveedores externos no autorizados siguen sin aceptar; las entradas
fechadas posteriores conservan la historia de implementación, no el estado vigente.

## Propósito y alcance

Este fork debe evolucionar CAO de un **orquestador de terminales** a un
orquestador de **trabajo colectivo verificable**. El objetivo no es obligar a
usar una combinación fija de proveedores, sino permitir que cualquier
proveedor admitido ejecute una operación para la que posee capacidades,
permisos y recursos suficientes.

CAO ya aporta piezas útiles: memoria con ámbito persistente, índice SQLite,
recibos y leases de hijos nativos, diarios de workflows, relaciones de
memoria e inyección inicial neutral respecto al proveedor. También hay
implementaciones parciales de recibos de turno, conciliación, catálogo de
proveedores y pruebas de ciclo de vida.

Eso **no** equivale todavía a un contrato completo de trabajo colectivo. Esta
nota es la fuente de verdad para los objetivos arquitectónicos y pendientes
conocidos; no presenta las capacidades parciales como garantías de producción.

## Estado revisado (2026-09-30)

El modo explícito `CAO_WORK_LAUNCH_MODE=required` dirige `cao launch` a la
admisión durable Work. Requiere una provisión previa del selector, bearer JWT
verificado, grant/contrato/snapshot vigentes, un backend Work registrado y
`CAO_ENABLE_PUBLIC_WORK_INGRESS=true` en el servidor. El ingreso HTTP y MCP
T035 está implementado con ese gate apagado por defecto, validación de autoridad
y límites de tamaño; el body no provisiona grants ni emite `task_received`.

El perfil Docker local opt-in registra `docker-local` con una imagen fijada por
ID inmutable y el mismo repositorio Work que usa el gateway. La aceptación
ejecutó un worker ELF estático y comprobó salida y cleanup en Docker real. El
demo desechable usa un emisor JWKS local, bearer firmado y provisión interna;
el recibo de launch `queued` acredita admisión, no resultado del worker ni
ejecución de un proveedor de modelos. `WORK_BACKENDS` sigue vacío por defecto.

La matriz de transiciones de Work se reprodujo en Python, Web y TUI. Herdr real
con `mock_cli` demostró recepción del input, estados y eliminación de sus
recursos. O03 sigue abierto para las demás combinaciones admitidas y esa
observación no equivale a un receipt Work.

T020 compone YAML/script con provisiones server-owned por step, credenciales
ligadas al run, receptor autenticado, resultado validado y projector CAS
reiniciable. Docker local prueba receipt/result, snapshot, resultado ausente,
fallo exit23 con cleanup y reintento autorizado. T023/T044 cubren las cinco
entradas mock_cli y conservación del contexto tras reinicio. La aceptación real
de modelos T084, la integración upstream autorizada T085 y las demás
combinaciones O03 conservan su evidencia pendiente.

T079 conserva un owner de transiciones Work y añade una frontera de imports
directos del projector. La deuda transitiva journal→database→memoria→terminal
sigue documentada. Los resultados y límites actuales constan en
`specs/001-verifiable-orchestration/completion-evidence.md` y
`specs/001-verifiable-orchestration/workflow-status.md`.

## Estado de integración verificado (2026-09-23)

El coordinador interno confirma capacidad, reservas e intención de envío en una
transacción SQLite y revalida autoridad antes del efecto. Dispone de despacho
síncrono y asíncrono; la cancelación conserva la incertidumbre y no autoriza
reenvíos. La suite acotada del núcleo y sus fronteras obtuvo 262 pruebas pasadas;
no es la suite completa del repositorio ni evidencia de proveedores reales.

T091 cerró la entrega durable interna por operación: una orden inmutable guarda
referencia opaca y hashes, y un adaptador registrado recupera el payload exacto
tras reinicio y revalidación de contrato, snapshot y autoridad. Las órdenes
nuevas no guardan texto arbitrario en filas ni eventos de entrega: el contenido
reside en artefactos privados acotados y verificados. La migración v14 conserva
las filas v13, que pueden contener plaintext histórico, pero no las ejecuta.
El drift de un adaptador bloquea sólo su operación, sin impedir el avance de
otra sana. El gate acotado pasó 76 pruebas y dos contratos de arquitectura;
composición: PASS WITH RISKS.

T018 cierra el bridge interno de inbox managed: una migración v16 conserva el
bridge v15 y registra UUID lógico/ruta canónica inmutables. SQLAlchemy y el
repositorio comprueban su conexión real contra esa identidad y `DATABASE_FILE`;
el contexto servidor se fija y revalida antes de reserva, bridge, cola y efecto.
La fila managed permanece `RECONCILE`, fuera del lector legacy `PENDING`; un
reinicio tras paste incierto no reenvía. El gate conjunto acotado pasó 65 pruebas
y dos contratos de arquitectura. El UUID detecta sustitución con identidad
distinta dentro de un contexto servidor; una sustitución completa entre
procesos sin ancla externa no es detectable. Esto no acredita entrada pública,
proveedor/backend real ni DB del operador; detalle en `workflow-status.md`.
La corrección regresiva T018 restauró el cursor-tupla del journal sin relajar
las comprobaciones de identidad: el gate conjunto de contrato de paso,
migraciones e inbox pasó 65 pruebas y la ruta API volvió a acreditar HTTP 409
`contract_rejected`. El fallo de 15 pruebas citado para T020 ocurrió antes de
esta reparación; T020 sigue abierta por su contrato de entrada Work ausente.
Las entradas públicas T017–T020, T044, la matriz real T084 y la integración
upstream T085 siguen pendientes. Los backends tmux/herdr actuales rechazan el
aislamiento requerido; el transporte de pruebas no acredita sandbox real.
La evidencia y los límites de cada incremento están en
`specs/001-verifiable-orchestration/workflow-status.md`.

T043 cierra el adaptador snapshot-ID en aislamiento: la lectura revalida una
orden durable y el snapshot en la misma transacción SQLite; la referencia no
concede acceso. Una selección explícita fallida no entrega contenido ni recurre
a memoria viva, y un snapshot autorizado vacío permanece distinto de `None`.
El terminal conserva su entrada de contenido `str | None` y no interpreta IDs.
El gate focal pasó 81 pruebas y dos contratos de arquitectura; el informe de
revisión está en `/tmp/caos-exec/T043/sol-review.md`. T044 sigue pendiente para
propagar IDs y autoridad por launch, hijo, handoff, YAML, scripts y agent_step;
no hay garantía end-to-end de esas rutas ni prueba con proveedor real.

T024/T025 acreditan sólo dos prerrequisitos internos: `WorkAuthority` rechaza
principales falsificados, revalida la allowlist durable del job, limita grants
hijos y observa la revocación del padre tras reinicio; `WorkReservations`
resuelve contención entre procesos, solapamiento ancestro/hijo y alias directos,
y retiene la reserva de un escritor externo vivo hasta recibir prueba de parada
y superar el fence de revisión. El gate conjunto pasó 30 pruebas focales y dos
contratos de arquitectura; la revisión de composición fue PASS WITH BOUNDARIES.
Una reserva de directorio no aísla recursivamente los inodes descendientes:
queda pendiente el aislamiento verificable del backend. T029 cerró la frontera
interna de servicio para launch y agent-step registrados: `WorkAdmission` entrega
un port efímero, ambos adaptadores revalidan antes de crear/asignar y enviar,
y `caller_id` del body no concede autoridad. El gate focal final obtuvo 196
pruebas pasadas, composición y contratos de arquitectura sin roturas; no
acredita proveedor ni backend real. T017 sigue esperando el setup/resolver
público confiable de T035, el registro en runtime y la correlación de respuesta.
T019 conserva hijos/handoffs; T035, las demás entradas públicas y los
gates externos siguen abiertos.

La revisión T019 confirma un bloqueo de contrato: `native_children` enlaza
terminales, pero falta el port autorizado y durable que une intento/generación
padre con hijo/handoff y recibo verificable. `caller_id`, el banner y la dedupe
de creación no conceden autoridad ni `task_received`; el ACK Work requiere ese
recibo. WorkRepository, WorkContracts y WorkAdmission deben definir el vínculo
antes de implementar T019. T019 sigue abierta; la propuesta de diseño no
constituye aceptación de implementación.

La etapa interna T019-A obtuvo **PASS parcial independiente** para v23:
`work_lineage_integrity` certifica cada nuevo binding managed por
intento/generación y no retrocertifica historia v22. Replay y revalidación de
contrato exigen autoridad exacta y viva de hijo y receptor, sus grants
seleccionados, acciones y cadena sin ampliación; un grant contradictorio
cierra el replay sin nuevas filas ni eventos. La entrega valida su propio hash
de solicitud. El subconjunto independiente v22/v23 pasó **16 pruebas**; la
composición del root pasó con 4 contratos de arquitectura mantenidos. En ese
corte T019 y T093 seguían abiertas: faltaba enlazar hijo nativo, ACK autenticado, proveedor y
entradas públicas. T065 recovery debe usar **v24+**, superando la referencia
histórica a v22+ de etapa 3. Informe:
`/tmp/caos-exec/T019/sol-stageA-v23-review.md`.

El incremento T019-R1 obtuvo **PASS independiente de fuente/diff**, sin cerrar
T019. `POST /terminals/run-step` mantiene el acceso legacy de un cliente remoto
sin auth y pasa `principal=None`; con auth activa propaga exactamente el
`Principal` verificado por el servidor. `caller_id` no concede autoridad Work y
la firma posicional de `run_agent_step` se preserva al añadir el argumento al
final. No se añaden Work, grants, bindings, receipts ni efectos de proveedor.
El builder reportó 3 pruebas focales en verde tras reproducir en RED el 401
remoto de R0; la revisión no repitió pytest. Siguen pendientes el vínculo
autenticado padre/hijo, handoff, grants propios y ACK Work verificable. Informe:
`/tmp/caos-exec/T019/sol-r1-review.md`.

La revisión T020 confirma otro bloqueo de contrato: YAML y scripts conservan
intentos en el journal, pero sus rutas legacy no vinculan cada intento a un
`WorkAttempt` admitido ni usan su port de entrega. `run_id`, `step_id` y la
generación textual no conceden job/grant/snapshot ni ACK Work. La entrada debe
aportar identidad autorizada y una asociación durable cercada antes de conectar
el bridge al adapter registrado. El gate focal del contrato script falló antes
de la corrección T018 (15 failed, 16 passed) y ahora pasa sus 32 pruebas; T020
sigue abierta por el contrato de entrada Work ausente. Detalle en
`workflow-status.md`.

El bloqueo de T035 requiere que setup/autoridad confiable provea un vínculo
durable y reconstruible `(principal, selector)` con job, grant/revisión y
contrato/snapshot congelados antes de que la composición API instale el
proveedor/gateway. La ruta `/work-launches` conserva su 503 estructurado hasta
entonces; el puerto inyectable y los fixtures no acreditan provisión operativa.

T035-A obtuvo **PASS parcial independiente** para la cerca de identidad HTTP:
`/work-launches` exige bearer verificable por petición incluso con auth global
desactivada y deniega loopback sin bearer con `401 launch_identity_required`,
`WWW-Authenticate: Bearer` y cero llamadas al gateway. El gate fresco pasó
33 pruebas y cuatro contratos de arquitectura. No hay setup administrativo,
gateway instalado, MCP ni rutas públicas de hijos/continuaciones; T035 continúa
`[ ]` y la identidad válida sin gateway recibe 503. Informe:
`/tmp/caos-exec/T035/identity-fence-sol-review.md`.

T035-B obtuvo **PASS parcial independiente** para el factory interno inactivo:
verifica el esquema SQLite antes de componer gateway → runtime → admisión,
recibe un mapping explícito de backends y registra sólo `launch` v1. La prueba
con SQLite temporal confirma provisión posterior, replay tras reinicio y fallos
cerrados sin acuñar autoridad ni despachar. Gates frescos: 48 pruebas de
composición, gateway, runtime, origen e ingress HTTP; cuatro contratos de
arquitectura conservados. El setup administrativo sólo existe dentro de la
prueba; falta interfaz operativa, prueba HTTP con el factory real, binding del
backend productivo, MCP y wiring de lifespan. T035 y T017 siguen `[ ]`.
Informe: `/tmp/caos-exec/T035/composition-sol-review.md`.

T036 cierra la composición interna multiproceso: dos procesos `fork` con
conexiones propias comparten SQLite temporal; la caída tras reserva e intención
durable no acredita recepción ni permite reentrega, y la revocación durante
preflight impide crear item, intento, binding y cola. Un RED conductual acotado
al hijo probó la lectura viva de revocación y el GREEN restaurado pasó ambos
casos. Esta evidencia apoya SC-003/SC-004 junto con las pruebas de diez
solicitudes, dos plazas y equidad de T026. No acredita `spawn`, backend o
proveedor real, sandbox, ruta pública ni expiración de lease; Ruff, mypy y
revisión interactiva permanecen sin resultado aprobatorio. Detalle y límites:
`specs/001-verifiable-orchestration/workflow-status.md`.

T006 acredita el modelo de dominio v1: Job, WorkItem, WorkAttempt, WorkView,
WorkEvent y EventPage tienen versión y validación cerrada. El DTO separa estados
de job, trabajo, intento, turno y proceso; la fase de entrega deriva del estado
del intento, admite round-trip serializado y rechaza contradicciones. Las pruebas
focales (54) y del consumidor API (9), más los dos contratos de arquitectura,
pasaron tras la corrección; esta evidencia del DTO no cerraba por sí sola las
entradas públicas T017–T020, la entrega T091 ni la adaptación T043, cerrada
posteriormente con evidencia propia; T044 y etapas posteriores siguen abiertas.

T056 añade contrato de compatibilidad del DTO HTTP v1: el fixture legacy se
valida con `WorkView` y `EventPage`, y una lectura real de `/work-items/{id}`
contra SQLite temporal proyecta una entrega durable `running` sin afirmar éxito
ni vida de proceso (`process_state=unknown`, `result_ref=null`). La revisión
independiente pasó 11 pruebas focales, Black y los dos contratos de arquitectura
(260 archivos, 945 dependencias, cero rotos). Esta evidencia no cubre todas las
transiciones ni adaptaba MCP, web o TUI: en esa revisión T060–T063, SC-007
global y O03 seguían abiertos. Detalle en
`specs/001-verifiable-orchestration/workflow-status.md`.

T059 acredita el transporte simulado de observación de panel Herdr cuando falta
estado nativo: sólo el proveedor registrado interpreta el texto, y
StatusMonitor aplica latch, compuerta de recibo y evento terminal normal. El
gate independiente pasó 17 pruebas y dos contratos de arquitectura (260
archivos, 946 dependencias, cero rotos). El estado terminal no concede ACK ni
resultado Work. La prueba con Herdr real queda en T063; O03 y SC-007 global
siguen abiertos. Detalle en `specs/001-verifiable-orchestration/workflow-status.md`.

T058 conecta la fábrica de proveedores con el descriptor de `launch`: rechaza
una selección explícita sólo cuando el descriptor la declara `not_applicable`,
antes de construir o registrar la instancia. Los estados `unverified` y
`not_probed` conservan la ruta existente. El gate independiente pasó 40 pruebas
focales y dos contratos de arquitectura (260 archivos, 946 dependencias, cero
rotos). El catálogo por operación es el baseline T027; T058 no acredita
binarios, autenticación, modelos aceptados, permisos ni proveedores reales.
Discovery booleana, `mock_cli` y rutas legacy conservan su contrato. El detalle
de la revisión consta en `specs/001-verifiable-orchestration/workflow-status.md`.

T060 está cerrada para observación manual Web por ID explícito. Ambos llamadores
montan `TerminalView`: el operador pega el ID en el formulario compartido y
confirma la lectura de `WorkView` v1, cuyo estado durable se muestra separado
de la identidad y liveness del terminal. La prop explícita prevalece; sin
selección no hay petición Work. La revisión independiente obtuvo 15/15 pruebas
React, build Web y composición del proyecto con cuatro contratos conservados y
cero rotos. El bloqueo anterior de Vitest quedó superado en este gate.
Esta entrega no establece asociación automática terminal→Work ni seguimiento
continuo. SC-007 global sigue abierto. Detalle en
`specs/001-verifiable-orchestration/workflow-status.md`.
T061 obtuvo **PASS parcial de integración TUI por ID explícito**: `cao tui
--work-item-id <id>` entrega la selección a `Renderer`, que consulta el
`WorkView` v1 por HTTP y muestra su estado durable independientemente de
terminal `Idle` o `Completed`; sin selección no consulta Work. La ruta codifica
un único segmento y mantiene visibles 404, errores de autenticación/red,
schema desconocido e identidad devuelta distinta. La revisión independiente
aprobó 204 pruebas unitarias TUI, 11 + 5 pruebas de arquitectura y
`project-composition-check` (267 archivos, 986 dependencias, cuatro contratos
conservados). Las pruebas `endpoint_contract` dependientes de `cao-server`
en 127.0.0.1:9889 tuvieron tres fallos por servidor ausente/timeouts en la
ejecución amplia anterior; no constan como PASS.

**T061 `[x]` (PASS independiente, 2026-09-24).** Foreground/background son
colores, no estados de ejecución. Los siete estados Work comparten etiqueta y
rol semántico generados desde `design-tokens/status.json`; Web resuelve primer
plano y fondo, y TUI resuelve sólo primer plano, sin pintar fondo Ratatui ni
romper `NO_COLOR`. «Observed running» y «Observed succeeded» cuentan sólo el
Work elegido explícitamente: `(1,0)` para running, `(0,1)` para succeeded y
`(0,0)` para los demás estados válidos. Ausencia/error/revisión contradictoria
no inventan un total; el contrato HTTP y los bodies `WorkView` siguen en v1.
Revisiones antiguas se descartan y el terminal no redefine el estado Work.
Gates frescos: Web DOM **28/28**, Python contrato **2/2**, Node **2/2**, Rust
Work **15/15** más revisión **1/1** y guardas **11/11 + 5/5 + 7/7**, build Web,
generación y composición PASS (268 archivos, 987 dependencias, cuatro contratos
conservados). Web/Python corrieron en copia ext4 con hashes y lockfiles iguales
al workspace; los intentos en Windows/9p se habían bloqueado antes de ejecutar.
`endpoint_contract` con servidor separado no se ejecutó. La selección no crea
vínculo terminal→Work; los contadores no son totales de job/sistema. TUI no
transporta credencial remota ni promete seguimiento continuo. SC-007 global y
T062/T063 siguen pendientes.
Dictamen y evidencia en `specs/001-verifiable-orchestration/workflow-status.md`.

**T062 `[x]` (PASS independiente, 2026-09-24).** CLI y MCP leen
`WorkView`/`EventPage` por HTTP autenticado; CLI conserva JSON y los metadatos
de paginación, distingue estados humanos y valida ID antes de URL y bearer.
Click rechazó `a?x=1` sin petición en ambos comandos y aceptó un ID válido.
Tokens Web/TUI generados coinciden con el fixture. La suite focal pasó 34
pruebas en copia ext4 con hashes fuente/copia iguales; Node pasó 2, el
generador no mostró drift y composición del checkout pasó cuatro contratos.
El pytest directo en OneDrive agotó 20 segundos: no cuenta como PASS del
checkout. T063 y SC-007 global siguen abiertos. Dictamen:
`/tmp/caos-exec/T062/sol-final-review.md`.

La política común de memoria cubre la fachada versionada y las entradas legacy
locales, HTTP/MCP, archivos, relaciones, lint, reparación y grafo. La auditoría
se confirma antes de entregar datos o producir efectos; las proyecciones en caché
separan el wiki y SQLite del propietario. La comprobación final posterior al
último ajuste pasó 177 pruebas y los dos contratos de arquitectura; la revisión
de composición no mantiene bloqueantes de T040/T092. Esto no acredita la suite
completa del repositorio ni la integración pública de launch/workflows.

## Modelo objetivo

Los cuatro niveles de estado no son intercambiables:

~~~yaml
terminal: alive | dead
turn: ready | input_sent | acknowledged | processing | blocked
work_item: queued | running | waiting_children | succeeded | failed | reconcile
job: planning | running | waiting | completed | failed | revoked
~~~

La interfaz debe mostrar que se está trabajando cuando el work item está
running, aunque la TUI del terminal parezca momentáneamente inactiva. Un
resultado sólo queda completado cuando se ha persistido y validado; ni un
prompt enviado ni el texto visible en una pantalla son pruebas suficientes.

## Ejes estratégicos

### P0 — Modelo de trabajo y evidencia

1. **Un work item y work attempt durable para toda operación.** Hijos nativos
   y workflows ya tienen ciclos de vida parciales, pero lanzamientos ordinarios,
   mensajes de inbox y delegaciones ad hoc deben compartir el mismo modelo.
   Cada intento conservará padre, proveedor, lease, contrato congelado,
   resultado, evidencia y estado de conciliación.

2. **Recibos durables de entrega y resultado.** Todo trabajo debe recorrer
   planned → sent → acknowledged → running → finished | failed | reconcile.
   La prevención de redelivery no reemplaza una evidencia de recepción,
   ejecución y limpieza.

3. **Árbol nativo de hijos con join y limpieza.** Un hijo necesita un registro
   persistente, lease renovable, padre, resultado durable, cancelación y
   conciliación. Un hijo muerto nunca debe parecer simplemente lento.

4. **Eventos durables de toda la flota.** El bus vivo y la línea de tiempo son
   observabilidad, no evidencia. Cada tarea, mensaje, recibo, transición y
   decisión de conciliación requiere un evento append-only, reproducible y con
   huecos declarados.

### P0 — Coordinación segura de ejecución

5. **Capacidades y allowlist reales por job.** Un job acepta únicamente los
   proveedores elegidos al crearlo y valida binario, modelo, esfuerzo y
   capacidades disponibles. No puede caer implícitamente a Grok, Gemini ni
   otro proveedor no autorizado.

6. **Autoridad server-side para ejecutar.** Los permisos de rutas, comandos,
   herramientas, red y publicación de artefactos deben surgir de un grant
   durable de job/terminal, no de parámetros opcionales enviados por el
   cliente. Deben quedar auditados y ser revocables.

7. **Coordinación de escrituras entre agentes.** El modelo de trabajo debe
   incluir ownership o reservas de rutas, detección de solapamientos,
   worktrees/aislamiento cuando corresponda y evidencia de diff/merge. Dos
   agentes no deben sobrescribir cambios del otro silenciosamente.

8. **Planificación de recursos y progreso.** Además de detectar una cuota
   agotada, el scheduler necesita presupuestos por job, prioridad,
   concurrencia justa, backpressure y prevención de dependencias bloqueadas o
   deadlocks.

### P1 — Conocimiento y contexto confiables

9. **Memoria como conocimiento confiable, no sólo texto.** Cada registro
   colectivo requiere identidad del productor, task/run, artefacto y hash de
   revisión fuente, referencias de evidencia, confianza, frescura y una
   decisión proposed, verified, approved, rejected o superseded. Una
   afirmación no revisada no debe convertirse silenciosamente en instrucción
   compartida.

10. **Congelar contexto para toda delegación.** El snapshot curado y
    redactado de workflows debe extenderse a launches ordinarios, hijos
    nativos, handoffs y continuaciones cruzadas. Hermanos iniciados en tiempos
    distintos deben poder identificar exactamente qué conocimiento recibieron.

11. **Continuación portátil tras cuota o fallo de proveedor.** La pausa debe
    generar un paquete con contrato congelado, snapshot de memoria, evidencia
    completada, hashes de artefactos, trabajo pendiente y razón del cambio.
    El proveedor sustituto continúa sin repetir ni inventar trabajo ya hecho.

12. **Capacidades de proveedor declarativas y uniformes.** Discovery, creación
    en manager, entrega de herramientas/MCP, evidencia de estado, límites,
    memoria y continuación segura deben usar un contrato común. La
    compatibilidad se decide por operación solicitada, no por pares de
    proveedores codificados.

13. **Una proyección semántica de estado.** API, web, TUI, contadores, texto,
    color de primer plano y fondo deben derivar del mismo DTO de work item,
    no de inferencias distintas sobre la pantalla del agente.

### P1 — Seguridad, distribución y control humano

14. **Autorización y protección de memoria.** Publicar a ámbitos compartidos,
    globales o federados debe requerir política y auditoría explícitas.
    Aislamiento por ámbito, detección de secretos, redacción, retención,
    tombstones y auditoría de acceso deben aplicarse de forma uniforme.
    Loopback sin autenticación sólo es admisible en local; remoto o compartido
    exige autenticación y autorización explícitas.

15. **Memoria colectiva entre nodos.** Un endpoint remoto no resuelve
    replicación ni consistencia multiwriter. Hace falta un almacén autoritativo
    versionado, ACL por proyecto/job, semántica de conflictos, retención y
    protocolo de recuperación.

16. **Intervención humana durable.** Aprobaciones, pausas, reanudaciones,
    revocaciones y excepciones de política deben funcionar para cualquier
    work item y conservar quién decidió, qué evidencia examinó y qué efectos
    autorizó.

### P2 — Mantenibilidad y operación

17. **Reducir acoplamiento de cambios.** Extraer reducers de dominio,
    repositorios de persistencia, adaptadores de proveedor y proyecciones de
    lectura de los módulos API, terminal, base de datos y memoria. Así se
    prueba el ciclo de vida sin duplicar lógica de estado.

18. **Migraciones, recuperación y compatibilidad.** Versionar los esquemas,
    migraciones y formatos de recibos/eventos; definir backup/restore y
    compatibilidad entre nodos y versiones antes de afirmar alta
    disponibilidad.

19. **Matriz de integración real y release.** Mantener una matriz protegida,
    opt-in y con cuentas autenticadas para proveedores disponibles: arranque,
    entrega inicial, hijo cruzado, mensajes entre hermanos, cancelación,
    cuota agotada, continuación, limpieza y reconciliación. No debe consumir
    modelos implícitamente en la CI ordinaria.

## Pendientes operativos conocidos

Estos no sustituyen los ejes anteriores: son límites concretos que deben
triagearse y planificarse.

1. **Workflows.** La edición condicional ya rechaza source hash obsoleto
   (GET source / PUT workflow), con pruebas de dos procesos y rollback del
   índice. T074 conserva parcialmente el contrato de cada intento script:
   la recuperación fría lee el JSON congelado desde un snapshot que verifica
   esquema, triggers de inmutabilidad y ledger; la pérdida de un trigger se
   rechaza antes de devolver evidencia. La ruta script ya congela argumentos
   acreditables antes de crear terminal y los vincula al intento antes del prompt;
   la reutilización no hereda como efectivos campos exclusivos de creación.
   Siguen pendientes YAML y el contrato durable previo a asignación, además de
   fuentes autorizadas para model, effort, permissions, limits y retry policy
   cuando la llamada no acredita sus valores aplicables. Los desconocidos no se
   presentan como efectivos; T074 continúa abierta.
2. **Configuración.** Varias opciones siguen siendo sólo env-var o se leen
   ad hoc fuera de ConfigService; deben entrar en un registro versionado y
   coherente con settings.json.
3. **Herdr para proveedores no nativos.** T059 añade una ruta genérica de
   observación de panel probada con dobles controlados. Antigravity conserva
   tmux hasta demostrar la combinación con Herdr real en T063.
4. **Errores de workflow durables.** La columna step.error_kind ya existía;
   el writer de scripts ahora conserva el tipo estructurado sin duplicar
   migraciones. Un retry limpia el tipo de la ejecución reutilizada y los
   runs completados/no terminales no proyectan fallos anteriores como actuales.
   Los registros históricos sin tipo mantienen fallback explícito. Pruebas
   de persistencia, reinicio y lectura: T077/T078 en la feature vigente.
5. **Sincronización upstream.** A fecha de 2026-09-22, main está cinco
   commits por detrás y nueve por delante de upstream/main. Cada integración
   upstream exige resolver solapamientos y repetir la matriz relevante.
6. **Especificaciones históricas.** Los documentos marcados “specified, not
   implemented” deben revisarse uno por uno: pueden ser deuda real, trabajo
   ya superado o notas históricas; no deben convertirse automáticamente en
   compromisos de este fork.

## Orden recomendado

1. Modelo universal de work item/work attempt y recibos.
2. Grants server-side, coordinación de escrituras y scheduler de recursos.
3. Registros de memoria basados en evidencia y snapshots universales.
4. Paquete de continuación portable, eventos durables y reconciliación.
5. Proyección única de estado, capacidades declarativas y matriz real.
6. ACLs distribuidas, migraciones/recuperación y posterior modularización.

Cada etapa debe terminar con contratos versionados, migración o rollback
definidos, pruebas de fallo y una demostración real con los proveedores que
el job autorizó.

## Gate de autoridad T093: etapas 1–3 internas aceptadas parcialmente (2026-09-24)

La decisión humana para FR-003, FR-006 y SC-001 fija al operador autenticado
mediante interfaz administrativa interna como único provisionador de launch.
Antes de la entrega, éste establece un vínculo durable y revalidable
`(principal, selector) -> job, grant, contrato, snapshot`. Configuración de step
sin fuente autorizada, incluida YAML/preasignación, modelo, esfuerzo, permisos,
límites o retry, queda bloqueada o explícitamente desconocida. Cada hijo y
workflow tiene identidad propia verificable y grant explícito, acotado a job y
acciones; sólo el issuer/revoker autorizado puede concederlo o revocarlo y la
redelegación requiere concesión nueva. Nombre, sesión y caller ID no acreditan
procedencia ni permiso de lectura.

`task_received` sólo puede emitirlo el adaptador/agente receptor autenticado y
registrado. Acredita aceptación durable en ese receptor, vinculada a entrega,
intento y generación vigente, con protección contra replay y origen falso; no
acredita inicio ni resultado del proveedor. Texto de terminal y envío local no
son ACK. El contrato interno de Astra habilitó la etapa 1: modelos estrictos,
migración aditiva verificada **v19** y provisión/retiro internos CAS con historial
inmutable por `(principal_id, selector, revision)`. Cada escritura revalida
admin dueño del job, grant/revisión y cadena viva, provider, contrato y snapshot;
la reapertura SQLite recupera sólo la revisión activa. No hay backfill legacy
ni downgrade. La revisión independiente de etapa 1 pasó **80 pruebas** y
composición con 4 contratos de arquitectura mantenidos.

La etapa 2 añade migración aditiva verificada **v20** para sujetos y
autorizaciones de origen con acciones cerradas, revisiones CAS e historial
inmutable. El issuer inicial del sujeto no se transfiere; emitir y sustituir
autorizaciones exige ese issuer y ownership del job, mientras que resolver y
delegar deniegan historia incoherente. Un sujeto gestionado necesita acción
`delegate` viva y explícita para delegar directamente; grants legacy mantienen
su semántica. Revocar conserva su ruta reductora aun con grant vencido. El gate
independiente combinado pasó **84 pruebas** y composición con 4 contratos
mantenidos.

La etapa 3 añade migración aditiva verificada **v21** y binding durable de
launch por intento/generación, ligado a la provisión exacta, principal, selector,
job, contrato, snapshot y clave de idempotencia. `LaunchRuntime` resuelve la
provisión SQLite sin `contexts`, conserva el Principal autenticado y entrega a
`WorkAdmission` un handoff sellado; la admisión revalida origen en ambas
transacciones y `WorkContracts` lo comprueba antes de dispatch/readiness/efecto.
El replay legacy sólo reutiliza una orden previa exacta, sin crear otra. La
revisión independiente Fix3 pasó **146 pruebas** —incluida la matriz SQLite de
idempotencia, corrupción, retiro/reemplazo, rollback y concurrencia—, Black,
4 contratos de importación y composición. Recovery debe reservar migración
**v22 o posterior**.

En ese corte interno T093 y T017/T019/T020/T035 permanecían abiertas: la admisión por origen es
interna; faltan conexión de entrada pública y setup confiable, identidad
operativa de agentes, bindings de hijos/workflows y ACK autenticado. No se
activó proveedor real ni transporte API/MCP/CLI. El guard anterior al I/O no
promete atomicidad distribuida frente a una revocación posterior. Brief:
`/tmp/caos-exec/T093/approved-decision-brief.md`; contrato:
`/tmp/caos-exec/T093/astra-contract-design.md`; revisiones:
`/tmp/caos-exec/T093/sol-stage1-rereview.md` y
`/tmp/caos-exec/T093/sol-stage2-rereview.md` y
`/tmp/caos-exec/T093/sol-stage3-fix3-review.md`.

## Distribución y recuperación: T064/T065 parciales (2026-09-23)

T064 sigue abierta. El contrato HTTP comprueba ahora que una revisión con
`expected_version` obsoleta devuelve 409 `knowledge_revision_conflict`; el gate
focal pasó 23 pruebas. Revocación, ACL y tombstone ya tienen cobertura, pero
falta el listado HTTP con cursor y su semántica de paginación/caducidad. Las
tareas T067/T068 del protocolo y retención también siguen abiertas.

T067 tiene ahora un cliente CAS parcial para la ruta de revisiones existente:
envía versión esperada y selectores, rechaza redirects y tipa sólo el 409 con
el sobre conocido, sin exponer su mensaje remoto. Dos pruebas aisladas pasaron;
el runner pytest normal agotó su tiempo. Faltan validación cerrada de entradas
y respuesta, pruebas de falsos conflictos y ASGI, y el contrato servidor de
checkpoint/página con cursor. No hay sincronización extremo a extremo ni
garantías de retención o revocación acreditadas. T067/T068 siguen abiertas;
detalle en `specs/001-verifiable-orchestration/workflow-status.md`.

T065 permanece abierta. Se retiró la prueba provisional de backup y restore
porque asumía un formato y snapshots que no representan los stores reales.
La decisión humana fija captura offline v1: un responsable operativo autorizado
demuestra quiescencia y retención de DB, memoria, artefactos y contenido de
entrega referenciado antes del corte; la implementación valida esa evidencia
en fixtures, sin detener procesos externos. Restore v1 sólo produce un destino
aislado y durablemente bloqueado para admisión, scheduler, backend y proveedor.
Conserva historia y deja intentos potencialmente activos para conciliación
explícita. La reactivación queda fuera de v1. Si contenido obligatorio contiene
una credencial de proveedor, el export aborta con diagnóstico redactado, sin
exportar secretos ni alterar bytes históricos hashados.

El siguiente artefacto de Astra es el contrato interno versionado de captura,
verificación, restore bloqueado, compatibilidad y pruebas RED/GREEN sobre los
stores reales en SQLite temporal. Terra implementará después del dictamen.
La decisión no acredita implementación, corte consistente ni restore probado;
T065 y T069–T071 siguen pendientes.
Brief: `/tmp/caos-exec/T093/approved-decision-brief.md`; dictamen:
`/tmp/caos-exec/T065/astra-recovery-contract-ruling.md`.

La re-revisión T069 del 2026-09-24 acredita parcialmente la captura offline:
rechazo de credenciales en resultados UTF-8 referenciados, aceptación limitada
de snapshots de delegación UTF-8 y compactación/reescan de SQLite final.
Sigue **FAIL de aceptación**: un resultado binario formado por bytes de
control válidos en UTF-8 se publica y verifica. Memoria, delivery, worktree,
lineage y el verificador de corte server-owned concreto tampoco están
acreditados. T069 sigue `[ ]`; detalle en
`/tmp/caos-exec/T069/sol-correction-review.md`.

La re-revisión independiente del gate binario confirmó rechazo de NUL,
controles y DEL en objetos externos y snapshots UTF-8 antes de publicar,
conservación exacta de texto permitido y secreto externo cerrado. Pasaron 57
pruebas afectadas, formato y composición/Import Linter. La revisión automática
de composición quedó sin ejecutar por `stdin is not a terminal` (exit 1).
Memoria real, delivery/worktree/lineage con productores reales y verificador de
corte server-owned concreto siguen pendientes; T069 continúa `[ ]`. Informe:
`/tmp/caos-exec/T069/sol-binary-review.md`.

La base v24 de T065 superó revisión independiente parcial el 2026-09-24:
contexto `normal` único y verificado, migración aditiva que conserva ledger e
historia v23, y barrera local que rechaza `blocked_restore` y contextos
inválidos. Las comprobaciones independientes pasaron 29/29 (migración y guard),
46/46 (migraciones y repositorio), 1/1 (lineage v23 focal) y 4/4 contratos de
Import Linter. La composición automatizada independiente dio PASS; la revisión
interactiva reportada por el builder no arrancó por falta de TTY, y se
revisaron las fronteras en fuente. El guard aún no está conectado a los
efectos ni ingresos; capture/verify/restore, catálogo de
referencias e inspector de secretos permanecen pendientes. La agrupación
lineage+delivery no tiene exit final acreditado. T065 y T069–T071 continúan
abiertas, sin garantía de restore ni PASS global.

El catálogo SQLite v24 de T065-B obtuvo **PASS parcial** independiente el
2026-09-24. Sobre `init_db()` real acepta 61 tablas, el multiconjunto exacto de
105 FK (162 componentes) y 23 familias; seis relaciones SQLite tienen mapeo
origen→destino, mientras la relación de memoria ambigua exige perfil futuro.
Una FK adicional en `flows` y tablas desconocidas se rechazan; la inspección
no cambia el store. Gates: 8 pruebas de catálogo, 29 de migración/guard,
composición PASS (4 contratos) y `diff --check` exit 0. El dictamen de FK está
en `/tmp/caos-exec/T065/astra-catalog-fk-ruling.md` y la revisión en
`/tmp/caos-exec/T065/catalog-sol-review.md`. Este incremento sólo clasifica:
siguen pendientes referencias físicas, secretos, capture/verify/restore y
conexión del guard. T065 y T069–T071 continúan abiertas.

## T068: cursor de recuperación, pendiente de caducidad transaccional (2026-09-23)

La implementación actual añade migración v17, ruta HTTP de recuperación,
checkpoint por ámbito, keyset y tombstones con metadatos. Graphify localizó
las fronteras y se contrastaron con la fuente. Los gates independientes dieron
10 pruebas focales, 82 pruebas vecinas y 26 pruebas de integración aprobadas;
`project-composition-check` pasó con 260 archivos, 946 dependencias y dos
contratos conservados. La revisión de composición se hizo sobre SQLite real,
ASGI, política y cadena de grants.

T068 sigue abierta: el servicio toma el reloj antes de `BEGIN IMMEDIATE`.
Una reproducción con SQLite real aceptó una página cuando el cursor ya había
vencido mientras la solicitud esperaba el bloqueo de escritura (vencimiento
`101`, reloj `102` al entrar). La corrección y su prueba de concurrencia deben
preceder el cierre. La ruta añadida no cierra T064 ni el cliente T067; tampoco
acredita backup/restore T069 ni HA T072. Informe independiente:
`/tmp/caos-exec/T068/sol-review.md`.

## T068: cursor de recuperación aceptado tras reparación (2026-09-23)

La revisión anterior detectó un TTL vencido durante una espera de SQLite. El
servicio lee ahora el reloj después de adquirir `BEGIN IMMEDIATE`, antes de
validar el cursor. Una reproducción independiente con vencimiento `101` y
reloj `102` devolvió `KnowledgeCursorExpired`, sin página ni avance del keyset.
Las nuevas pruebas acreditan también binding de scope/autoridad y versión,
además de rollback de auditoría si falla la persistencia del cursor.

T068 queda cerrada para autoridad SQLite única: la matriz independiente pasó
121 pruebas, `project-composition-check` conservó dos contratos (260 archivos,
946 dependencias, cero rotos) y Graphify se contrastó con fuente. El servidor
expone páginas versionadas con checkpoint por scope, tombstones y revocación
revalidada. No se atribuye al cierre consumo remoto T067, cobertura HTTP
global T064, backup/restore T069 ni HA T072. Detalle:
`/tmp/caos-exec/T068/sol-rereview.md`.

## T064: contrato HTTP de conocimiento aceptado (2026-09-23)

T064 queda cerrada. Un caso ASGI con SQLite y grants durables recorre el 409
por `expected_version` obsoleta, tombstone con contenido nulo en la respuesta
y en la primera página de recuperación, y 403 fijo al continuar con el cursor
tras revocar el grant. La revisión independiente contrastó la fuente y ejecutó
la suite HTTP de autoridad y recuperación: 31 pruebas aprobadas, cuatro avisos
de deprecación, exit 0. T067 y T065/T069–T072 conservan su alcance propio.
Detalle: `/tmp/caos-exec/T064/sol-final-review.md`.

## T067: cliente remoto de conocimiento aceptado (2026-09-23)

T067 queda cerrada para una autoridad única. El gateway propone revisiones
mediante CAS con versión esperada y obtiene páginas recovery v1 con checkpoint
y cursor opaco desde las rutas reales del servidor. Valida la autoridad URL,
entradas y DTOs antes de exponer éxito; 409 y 410 se tipan sólo con su sobre
canónico. La caída remota no se convierte en escritura local, retry ni merge.

La revisión independiente obtuvo 62 pruebas focales aprobadas y composición
PASS (260 archivos, 947 dependencias, dos contratos conservados, cero rotos).
Un recorrido gateway→ASGI con SQLite y grant durables comprobó 201, CAS 409,
recovery 200 y cursor vencido 410. Las garantías de retención, revocación y
caducidad son del servidor T064/T068; el cliente exige reinicio explícito tras
410. Ese cierre no acreditó T065/T069–T071 (backup/restore), T072
(multinodo/partición), T060/T061 ni T074. Informe:
`/tmp/caos-exec/T067/sol-r5-review.md`.

## T047/T048: REDs de continuidad y decisiones preparados (2026-09-23)

Las seis pruebas de US4 ya fijan rechazo pre-efecto para paquetes alterados,
versiones desconocidas y artefactos ausentes, y decisiones durables ligadas a
un contrato efectivo: reintento idempotente, revisión nueva del mismo work
item y revocación. Usan principal, grant, snapshot y binding reales en SQLite
temporal; la revisión de contrato cambia sólo en la fixture. El gate focal
terminó con seis fallos esperados por los dos servicios aún ausentes.

T047/T048 permanecen abiertas. T049–T055 deben implementar y llevar los tests
a GREEN, verificar fencing/cese, integración y efectos reales. La preparación
no acredita una ruta productiva de replanificación ni continuidad ejecutable.
Detalle: `/tmp/caos-exec/T047-T048/sol-red-r3-review.md`.

## T049: registro durable de decisiones humanas aceptado (2026-09-23)

T049 cierra el registro local de decisión ligado a intento, generación y
`contract_hash`: la migración v18 agrega decisiones, claims por efecto y
revocaciones write-once. El replay idéntico conserva evidencia y auditoría;
el payload divergente falla. Binding, grant, principal y claim se verifican en
una transacción SQLite; revocar conserva el historial y bloquea claims nuevos.

Revisión independiente: 33 pruebas focales/migración aprobadas, composición
PASS (262 archivos, 951 dependencias, dos contratos conservados, cero rotos).
La carrera revocación→claim no dejó claim ni evento de consumo; SQLite bloqueó
UPDATE/DELETE de la historia. Es un recibo local at-most-once: no acredita
efecto externo, fencing, continuidad, API/MCP/AG-UI ni cancelación de hijos.
T047/T048 conservan su estado de preparación; T050–T055 siguen abiertas.
Graphify requiere refresh tras el cambio estructural. Informe:
`/tmp/caos-exec/T049/sol-review.md`.

## T050: exportación de continuidad v1 aceptada (2026-09-23)

T050 cierra sólo el export acotado: revalida intento, binding, principal y grant
en una lectura SQLite de sólo lectura; proyecta refs verificadas del intento
exacto y estado pending/uncertain, con contrato y snapshot expresados por ID/hash.
El paquete canónico tiene límite de 32 KiB y hash de integridad; no transporta
autoridad, configuración, secretos, bytes ni permiso para reanudar. La prueba
focal aprobó; probes independientes confirmaron cero escritura/lectura de bytes,
rechazo de revocación y estados terminales. Composición PASS: 263 archivos,
955 dependencias, dos contratos conservados, cero rotos.

Los tres REDs de importación T047/T051 permanecen abiertos, igual que T052
fencing/cese. Graphify existente se contrastó con fuente; el refresh global
pendiente desde el timeout de T049 se conserva como deuda T089 e incluye esta
nueva frontera. Informe: `/tmp/caos-exec/T050/sol-review.md`.

## T047/T051: importación v1 de continuidad aceptada (2026-09-23)

T047 y T051 cierran con preflight descriptivo, idempotente y de sólo lectura.
Los tres REDs de paquete alterado, versión desconocida y artefacto ausente son
GREEN; parser canónico, fuente/grant vivo, payload recompuesto y bytes de cada
artifact ref se verifican antes de devolver el DTO congelado. Gate focal:
12 passed; regresiones vecinas: 64 passed; composición PASS (263 archivos,
955 dependencias, dos contratos conservados, cero rotos). Probe SQLite temporal
confirmó rechazo en la primera frontera incorrecta y cero cambios durables.

T052 sigue abierta: el preflight no crea destino, reserva, intento ejecutable,
fencing/cese ni autoridad para reanudar. Refresh de Graphify diferido a T089.
Informe: `/tmp/caos-exec/T051/sol-review.md`.

## T048: pruebas de decisiones humanas reconciliadas (2026-09-23)

T048 queda cerrada como tarea de pruebas FR-016. El gate fresco de decisiones
y migraciones, sin cobertura heredada, pasó 33 pruebas: replay idéntico,
revisión nueva del mismo work item y revocación durable. La fixture ejercita
autoridad, snapshot y binding reales sobre SQLite temporal. El claim sigue
siendo local; efecto externo, fencing y cancelación de hijos tienen gates
propios pendientes. Informe: `/tmp/caos-exec/T048-reconcile/sol-review.md`.

## T052: sustitución tras cese confirmado aceptada (2026-09-23)

T052 cierra la ruta interna de reemplazo: el scheduler sólo libera la reserva
held de un intento reconcile tras prueba `StoppedWriter` exacta y revalidación
de reserva, intento, generación y revisión de work. Conserva la referencia de
cese y un evento durable; el servicio usa exactamente la revisión del release
para el retry CAS que crea generación nueva. La cuota, caducidad o cleanup no
autorizan sustitución ni plaza reasignable. Resultado o cancelación concurrente
impiden un segundo intento; un fallo tras release conserva prueba y plaza libre
sin auto-retry. Las unidades released siguen consumiendo presupuesto.

Revisión independiente: 19 focales y 51 regresiones aprobadas sobre SQLite
temporal; composición PASS (263 archivos, 956 dependencias, dos contratos
conservados, cero rotos). El alcance termina en generación planned: no integra
proveedor, envío, reanudación automática ni recuperación del hueco release→retry.
El commit local no autorizado `2c83216b` queda documentado para el coordinador
sin otro commit ni operación Git mutante. Informe:
`/tmp/caos-exec/T052/sol-review.md`.

## T053: decisiones API/MCP y handoff ligado aceptados (2026-09-23)

T053 expone creación y revocación de decisiones por API con principal verificado
y por MCP mediante su credencial HTTP efectiva; ambas rutas usan el único
ledger `WorkDecisions`. Un interrupt AG-UI sólo entra en esta ruta durable si
una integración interna registra y valida explícitamente su binding contra
`WorkContracts`. Los interrupts automáticos siguen limitados a UI/terminal.

Para un handoff ligado, el claim local precede a la entrega, sin SQLite durante
el I/O. Replay no duplica la entrega; conflicto devuelve 409 y fallo post-claim
devuelve 502 incierto/no reintentable. TTL/cap limpia el binding local al
expulsar el interrupt, conservando el ledger. Revisión independiente: 32 pruebas
focales, 68 regresiones de handoff/bridge y 25 pruebas AG-UI con el extra
declarado aprobadas (93 en la regresión combinada); composición PASS (263
archivos, 962 dependencias, dos contratos conservados, cero rotos). No hay
integración productiva de registro
upstream, proveedor real ni garantía exactly-once del efecto externo. T054
cancelación de hijos se cerró después; T055 continuidad sigue pendiente. Informe:
`/tmp/caos-exec/T053/sol-review.md`.

## T054: cancelación de hijos y recuperación heredada aceptadas (2026-09-23)

T054 confirma en una transacción SQLite la cancelación de raíz y descendientes
vivos, con eventos y recibos; conserva terminales y resultados ya aceptados.
Una raíz cancelada por la versión anterior se recupera tras restart mediante
una operación convergente o el replay válido de su recibo, sin reescribir la
raíz ni duplicar eventos. Cleanup sigue separado: `pending` no acredita cese
externo ni autoriza callback automático.

La revisión independiente pasó 183 pruebas focales y de regresión, dos
contratos de arquitectura y composición. Un fallo inyectado en el evento hijo
revirtió todo el árbol, y el reintento completó la cascada. La garantía empieza
en el commit de recuperación: un hijo histórico puede terminar antes; SQLite
no acredita parada de proveedor ni exactly-once externo. T055 permanece
abierta. Informe: `/tmp/caos-exec/T054/sol-review.md`.

## T055: continuidad descriptiva entre adaptadores falsos aceptada (2026-09-23)

T055 confirma con SQLite y artefactos temporales que A completa y valida una
vez; B reabre el resultado ganador sin envío, ejecución ni callback. La
clasificación completada exige el finish CAS (`finished`/`succeeded`), el
`accepted_result_id` del intento vigente, evidencia `verified` de esquema v1 y
bytes legibles con longitud y hash correctos. Export/import revalidan binding,
snapshot, grant y fuente viva; un paquete rehasheado, resultado tardío o bytes
corrompidos se rechazan. La vía ejecutable conserva su rechazo de terminales.

La revisión independiente aprobó 174 pruebas e2e, de contrato, servicio,
persistencia, scheduler, reservas, dispatch y API; composición PASS con dos
contratos conservados. La lectura descriptiva mantiene el lease vigente como
límite. Esta prueba local no acredita continuidad con proveedor real ni
exactly-once externo. Informe: `/tmp/caos-exec/T055/sol-review.md`.

## T079: contratos de Work revisados parcialmente (2026-09-23)

La revisión independiente aceptó el incremento de dos contratos Import Linter:
el reducer Work mantiene sus reglas puras y `WorkRepository` no depende de
entradas ni runtimes de efectos. La fuente conserva la dependencia legítima
repositorio → reducer y esquemas hermanos; API, servicio y admisión delegan
hacia el repositorio. Terminal nativo, journal run/step y revisiones de
conocimiento mantienen ciclos y owners distintos. Gate fresco: 122 pruebas
focales aprobadas; Import Linter conservó los cuatro contratos sobre 263
archivos y 962 dependencias; composición PASS.

T079 sigue abierta: estos contratos no prueban propiedad única de escrituras
ni equivalencia de transiciones, y la auditoría completa de ciclos y
duplicaciones espera las extracciones pendientes, incluida T074 parcial.
Detalle: `specs/001-verifiable-orchestration/workflow-status.md`; informe:
`/tmp/caos-exec/T079/sol-partial-review.md`.

Actualización 2026-09-24: T074 ya está cerrada y la duplicación acreditada
entre transición raíz y cancelación descendiente de Work se extrajo a un
helper privado del repositorio, con rollback SQLite real y gates focales
verificados. No se añadió regla de imports sin dependencia nueva. T079 sigue
`[ ]`: la prueba local no certifica un dueño común extremo a extremo para
hijos/workflows sin el vínculo autenticado operativo pendiente de T019/T020/T094 ni el fence de
restore T070; los ciclos terminal nativo, run/step y memoria no se declaran
equivalentes a Work. La batería cruzada dio 77 passed y 1 failed por la
aserción externa v23 frente a esquema 24. Dictamen actualizado:
`/tmp/caos-exec/T079/sol-review.md`.

## T083: CI de proveedores reales protegida (2026-09-24)

La revisión independiente confirmó que la matriz real sólo se activa por
`workflow_dispatch` en runner protegido, con manifest explícito, preflight
estricto y prerrequisitos de cuenta, CLI y modelo documentados. Los workflows
ordinarios no activan `live_provider`; el harness sin gate produce un skip.
T082 mantiene su cierre independiente y T084 requiere una ejecución real
autorizada. Informe: `/tmp/caos-exec/T083/sol-review.md`.

## T082: evidencia de escenarios aún incompleta (2026-09-24)

La revisión independiente mantiene T082 abierta. La cuota produce un skip
estructurado con la conciliación observada, y la validación completa se registra
después de cuatro escenarios y limpieza. Falta probar continuidad tras la
pausa; el workflow protegido no genera ni conserva JUnit para la propiedad de
evidencia y el archivo E2E no supera el gate `isort` por un salto de línea
introducido. Los checks locales sin proveedor pasaron 38 contratos, pero no
acreditan la matriz real T084. Informe: `/tmp/caos-exec/T082/sol-review.md`.

## T063: Herdr real pendiente de entorno y fixture (2026-09-24)

El preflight independiente confirma que `command -v herdr` devuelve exit 1:
Herdr no está disponible en `PATH`. La fixture contractual
`test/e2e/test_herdr_generic_status.py` no existe en el checkout ni en Git;
la cobertura actual usa un backend controlado y `MagicMock`. Instanciar
`HerdrBackend` podría iniciar el servidor si falta el socket, así que el
preflight no lo hizo. No se instaló backend ni se contactó proveedor o red.
T063, SC-007 global y O03 siguen abiertos hasta disponer de Herdr, proveedor
no nativo y fixture en un entorno autorizado. Evidencia:
`/tmp/caos-exec/T063/sol-review.md`.

## T074: contrato efectivo de cada intento cerrado (2026-09-24)

La revisión independiente acredita T074 `[x]` para script y YAML. Cada
intento YAML ordinario persiste contrato/hash antes de asignación y lo
revalida antes de entrega/redelivery; retry exige evidencia igual y fallo
durable previo. Replay añade una generación inmutable propia antes del nuevo
efecto sin cambiar el lifecycle del run fuente. Override divergente y YAML
histórico sin contrato se rechazan antes de ejecutar; los valores sin fuente
autorizada permanecen `unknown`/`not_applicable`. Gates frescos: 227 pruebas,
Black/isort/whitespace y composición automatizada PASS (4/4 contratos).
Permanece fuera de T074 un lifecycle/listado independiente por replay y la
atomicidad entre SQLite y proveedor. Las notas anteriores de T074 parcial
quedan superadas por este cierre. Informe:
`/tmp/caos-exec/T074/sol-rereview.md`.

## T082: harness y evidencia de continuidad cerrados (2026-09-24)

T082 queda `[x]` tras revisión independiente. La cuota se prueba con un
único POST sin redelivery; continuación exige el mismo recibo exitoso y el
marcador del turno antes de limpiar terminal. Pausa sin reanudación, plazo
ausente/inválido o continuidad no observada quedan como omisiones tipadas.
El plazo finito se elige en `workflow_dispatch` y sólo se pasa al paso pytest;
la evidencia JUnit por escenario persiste pass/skip/fail sin prompt, salida
ni credenciales. Los gates locales dieron 68 passed, 1 skipped; API focal,
formato y composición pasaron. Las notas previas de T082 abierta quedan
superadas. T084 sigue `[ ]`: no se han ejecutado cuentas ni proveedores
reales. Dictamen: `/tmp/caos-exec/T082/sol-rereview-final.md`.

## T069: captura de productores, aún no aceptada (2026-09-24)

La revisión parcial confirma la incorporación de memoria real al manifest
privado, sin confundir el verificador de integridad del bundle con autoridad
de corte. T069 sigue `[ ]`: el path `memory-content` controla raíz y archivo
final, pero puede seguir un symlink en un directorio intermedio fuera de
`memory_root`. Además, los pytest fuente/ext4/xdist de esta ronda quedaron
bloqueados; sólo imports y build de copias SHA paritarias pasaron. No se
infiere backup completo ni restore. Informe:
`/tmp/caos-exec/T069/sol-producers-review.md`.

## T093: política aprobada, contrato interno por fases (2026-09-24)

La aprobación explícita del operador cierra **T093 `[x]` como gate documental**.
Sólo un operador autenticado y dueño del job provisiona el launch mediante
interfaz administrativa interna; `(principal, selector)` resuelve revisión
durable de job, grant, contrato y snapshot. Cada hijo y step de workflow
gestionado exige identidad verificable y grant propio, sin autoridad heredada
de caller ID, nombre o YAML. Sólo receptor autenticado y registrado emite
`task_received`, con recibo durable de entrega/hash, intento/generación y
revisión de autorización; envío local, terminal y `acknowledged` legacy no
son Work ACK.

Spec Kit registra `OriginRequestV1`, handoff interno sellado y
`TaskReceivedReceiptV1`, owners, fases, migración aditiva posterior a v24,
rollback sin downgrade, lectura/replay legacy exactos y mixed-version
fail-closed, con REDs de autoridad, procedencia, ACK, reinicio y fallo
parcial. T017/T019/T020/T035 y T094 siguen `[ ]`: la decisión no acredita
ingress, provider, MCP o DB de operador ni autoriza activarlos. Los párrafos
históricos que dicen T093 abierta describen cortes anteriores a esta
aprobación, no el estado actual. Aprobación:
`/tmp/caos-exec/T093/approval-2026-09-24.md`; revisión:
`/tmp/caos-exec/T093/sol-approval-review.md`.

## T017: ingreso ordinario aún sin autoridad Work (2026-09-24)

T017 sigue `[ ]`. `cao launch` usa `POST /sessions`, que pasa por
`session_service` a `terminal_service` sin `Principal`, selector de
provisión ni idempotency key server-owned; el scope general y la clave
opcional aportada por el caller no satisfacen el binding Work. La ruta
durable `/work-launches` es separada y no está conectada al ingreso legacy.
El RED temporal con backend/proveedor test-only alcanzó efecto y fue
retirado; no se ha ejecutado GREEN. T093 `[x]` no autoriza inventar el
mapping desde campos legacy ni activar una entrada pública. Hace falta
decisión/contrato del ingreso interno autenticado y su handoff antes de
implementar T017. Dictamen: `/tmp/caos-exec/T017/sol-blocker-review.md`.
Decisión de routing aceptada (2026-09-25): `cao launch` ordinario sigue en
`/sessions` legacy; `--queue-work` permanece opt-in con bearer verificado y
admite sólo Work por `/work-launches`, sin fallback ni dispatch. El selector
omitido se resuelve sólo ante una provisión activa única del Principal.
Es avance parcial: **T017 `[ ]`**; no activa nuevo ingreso público, proveedor
ni DB del operador.

## T094: recibo v25 implementado parcialmente, sin cierre (2026-09-24)

T094 sigue `[ ]`. El recibo interno sellado, revalidación de linaje y grant,
tabla inmutable v25 y orden recibo→ACK están en fuente; 53 pruebas focales
pasaron sobre copia ext4 SHA-idéntica. El checker oficial de composición
actual pasó 4/4 contratos, pero hay dos fallos ejecutables: test de
migración T065 fijado literalmente a v24 (22 PASS, 1 FAIL) y test de
integración de dispatch que esperaba éxito desde booleano legacy y obtuvo
`running`. Faltan pruebas completas de revocación/contradicción del recibo,
ACK tardío, mixed-version y rollback v25. El cambio de `work_repository.py`
compartido recibió expansión limitada de coordinación para registrar y
verificar v25. Ninguna ruta, proveedor o DB operador se activó. Dictamen:
`/tmp/caos-exec/T094/sol-review.md`.

## T094: re-revisión, aún sin aceptación global (2026-09-24)

T094 permanece `[ ]`. En snapshot ext4 SHA-idéntico, 78 focales actuales
pasaron; revocación post-emisión, replay exacto/no redelivery y pruebas de
migración v25 ya están presentes. Se reprodujeron upgrade/rollback SQLite
v24→v25 y rechazo mixed-version; Import Linter del snapshot pasó 4/4. Pero
el barrido de dispatch síncrono dio 11 PASS y 1 FAIL: un test todavía espera
`running` del booleano legacy, mientras la implementación deja `sent` sin
receipt autenticado. No se cierra con esa regresión ni se infiere cobertura
async/global. Dictamen: `/tmp/caos-exec/T094/sol-final-review.md`.

## T094: callback fix verificado, async aún rojo (2026-09-24)

T094 sigue `[ ]` por dos regresiones de expectativas async. El
código productivo no cambió desde el dictamen previo;
el test del escritor SQLite separado ahora exige `sent`, ningún ACK y ningún
reenvío. Snapshot ext4 SHA-idéntico: 89 PASS entre service/lineage,
migraciones y dispatch síncrono, más 16 PASS de delivery síncrono. Rollback
v25, mixed-version, revocación post-emisión, nonce contradictorio y replay
exacto/no-redelivery se verificaron; composición del snapshot pasó 4/4
contratos. Con `pytest-asyncio` real en ext4, suite completa de cinco
módulos: 123 PASS y 2 FAIL, sin omisiones. Dispatch async aún espera
`running`/`acknowledged` desde booleano legacy; el contrato deja `sent`.
T017/T019/T020/
T035 continúan `[ ]`; no se infiere ingreso público, proveedor ni DB
operador. Dictamen: `/tmp/caos-exec/T094/sol-closure-review.md`.

## T094: receipt interno cerrado con async real (2026-09-24)

T094 queda `[x]` sólo para el recibo interno; las notas previas abiertas
quedan superadas. En snapshot ext4 SHA-idéntico y venv ext4 con plugin
`pytest-asyncio` real, los cinco módulos de service/lineage, migraciones,
dispatch y delivery dieron **125 PASS sin fallos ni omisiones**. Las dos
pruebas async corregidas conservan loop/intención/snapshot/no-redelivery y
niegan ACK del booleano legacy. Rollback/mixed-version v25, revocación,
nonce/replay e Import Linter 4/4 se verificaron. T017/T019/T020/T035
continúan `[ ]`; no se infiere ingreso público ni proveedor/DB operador.
Dictamen: `/tmp/caos-exec/T094/sol-async-closure-review.md`.

## T069: contención física de memoria aprobada, corte pendiente (2026-09-24)

La brecha de symlink intermedio en `memory-content` está corregida con
apertura `openat`/`dir_fd` sin seguimiento de enlaces por cada componente.
Control real MemoryService + adversario 2 PASS; gate pertinente 59 PASS,
Black/isort/compile y composición ext4 4/4 PASS. El inventario v25 es
cerrado y no adopta tablas desconocidas. **T069 sigue `[ ]`**: no existe
verificador de corte/quiescencia server-owned concreto conectado; el
verificador de bundle sigue siendo de integridad offline. T070 restore se
excluyó y no se infiere aprobado. El manifest mezcla `profile_version=25`
con rol de objeto `sqlite-v24`, pendiente de aclaración/versionado.
Dictamen: `/tmp/caos-exec/T069/sol-security-review.md`.

## T069: rol SQLite versionado; corte aún abierto (2026-09-24)

La incoherencia de metadata señalada arriba quedó corregida: captura v25
emite `sqlite-v25` y el verificador v1 sólo acepta exactamente un rol
`sqlite-vN` que coincida con `profile_version`; v24 canónico conserva
lectura estructural. Focal 1 PASS y gate pertinente 59 PASS en snapshot
ext4 SHA-idéntico, sin restore T070. **T069 sigue `[ ]`** porque el
`ServerOwnedOfflineCutVerifier` no tiene composición productiva concreta:
integridad del bundle no acredita quiescencia/retención del corte ni versión
real del SQLite exportado. Dictamen:
`/tmp/caos-exec/T069/sol-metadata-review.md`.

## T069: contrato de lease interno aprobado, implementación pendiente (2026-09-24)

La aprobación humana permite un corte server-owned sólo de writers Work
registrados: operador autenticado crea/revoca lease durable con owner,
scope, epoch/fence y TTL; captura comprueba cero writers activos e
inventario antes/después y publica evidencia ligada al bundle. Un rechazo
conserva causa durable no sensible, no manifest promovido. Se planifica
store v26 aditivo con inventario cerrado, bundle v2 con evidencia obligatoria y v1
sólo como lectura histórica; mixed-version y rollback no saltan el fence.
No hay promesa de quiescencia global ni activación pública, MCP, proveedor,
DB operador o restore. **T069/T070 permanecen `[ ]`** hasta fuente y gates.
Dictamen: `/tmp/caos-exec/T069/sol-cut-contract-review.md`.

## T065/T069: captura offline interna aceptada; restore pendiente (2026-09-24)

T065/T069 quedan `[x]` sólo para el corte de writers Work registrados y la
captura privada v2 de SQLite v26, memoria, snapshots y artefactos declarados.
La revisión R10 verificó publicación cercada hasta `replace`/fsync/CAS,
observaciones durables `before`/`after`/`promote` ligadas a `capture_id`,
inventario y cobertura exacta `integrity-only`, rechazo de huérfanos y
downgrade v26→v1, v1 histórico sólo de integridad y rollback de DDL v26.
Las carreras de revoke y `sent`→registro usan las APIs reales; un productor
Work no registrado durante stage no se presenta como quiescente.

Snapshot ext4 con hashes R10 coincidentes: **135 PASS, 1 deselected** en la
suite focal; el deselect es el RED T070 de restore, que sigue abierto.
Composición: Import Linter 4 kept/0 broken; Black y compilación focal PASS.
**T070/T071 permanecen `[ ]`** y no se acredita restore, quiescencia global,
ingreso público, proveedor real ni DB del operador. Dictamen independiente:
`/tmp/caos-exec/T069/sol-r10-review.md`.

## T095: copia portátil bloqueada aceptada; restore aún abierto (2026-09-24)

T095 queda `[x]` sólo como primitiva privada de `WorkRepository`: valida la
copia v26 y su procedencia inbox histórica sin reidentificarla, aplica
`blocked_restore` y reconcilia estados inciertos en una transacción. La prueba
R2 cubre fuente, staging y destino distintos; conserva source/context/bridge/
bindings y acredita rechazo por el guard local en el destino. Suite focal
**44 PASS**; Black, compilación y composición **4 kept/0 broken**. No se ha
conectado `restore_recovery_bundle`, publicación segura ni ingress transversal.
**T070/T071 permanecen `[ ]`** y no hay restore operativo acreditado.
Dictamen: `/tmp/caos-exec/T095/sol-r2-stagef-review.md`.

## T070: restore aislado de bundle v2 cerrado (2026-09-24)

T070 queda `[x]` para el restore privado de un bundle publicado v2/perfil 26.
Valida la identidad del origen y el cierre exacto de referencias frente a
SQLite, copia a staging privado con digest/fsync y llama allí a T095 para dejar
el store en `blocked_restore`; sólo los intentos inciertos pasan a
`reconcile`, sin relanzamiento. La publicación usa hard link sin clobber.
Si falla el fsync del padre tras publicar, devuelve error de publicación
incierta y conserva el destino bloqueado; no informa éxito. La DB configurada
del operador sólo se compara como ruta y nunca se abre.

El gate R2 acreditó **61/61** pruebas de recuperación y **44/44** de T095;
el snapshot final confirmó **105/105** combinadas, Black, compilación,
diff check y composición PASS (4 contratos kept/0 broken), con revisión
independiente Stage C R2 PASS. Este cierre no habilita entrada pública
API/CLI/MCP, proveedores, DB del operador ni guard global. **T071/T072 siguen
`[ ]`** para rollback/reactivación operativa y prueba multinodo. Evidencia:
`specs/001-verifiable-orchestration/workflow-status.md` y
`/tmp/caos-exec/T070/sol-r2-stagef-review.md`.

## T071: prueba de rollback y guía operativa cerradas (2026-09-24)

T071 queda `[x]` para la integración de captura v2/perfil 26 y restore privado
a SQLite aislado `blocked_restore`, más la guía de compatibilidad v25/v26 en
`docs/work-recovery.md`. V1/perfil 25 sólo verifica integridad; evidencia o
versiones incompatibles rechazan sin publicar. La migración v25→v26 conserva
historia y revierte el DDL fallido; tras commit no se usa writer v25, downgrade
in-place ni `DROP`. Un regreso a v25 requiere snapshot aislado, conciliación y
autorización separada: T070 no restaura v25.

El gate final local pasó **4/4** pruebas de integración y **90/90** combinadas;
Black PASS. Snapshot ext4 SHA-idéntico: compilación, diff y composición PASS
(270 archivos, 1002 dependencias, 4 contratos kept/0 broken). Revisión técnica
y de composición PASS; guía operativa revisada. El destino sigue bloqueado y
no se acreditan cese externo, reactivación, ingreso público, proveedor ni
guard global. **T072 sigue `[ ]`** para el escenario multinodo. Evidencia:
`specs/001-verifiable-orchestration/workflow-status.md` y
`/tmp/caos-exec/T071/sol-stagef-review.md`.

## T072: conflicto y reconexión entre procesos locales cerrados (2026-09-24)

T072 queda `[x]` para la prueba de dos procesos HTTP reales en un host sobre
una SQLite temporal compartida. La carrera de propuestas a versión 0 da un
201 y un 409; con B detenido sólo A avanza a versión 2 y el store conserva
exactamente dos revisiones. B reiniciado recibe 409 por versión obsoleta y
lee el contenido durable. La configuración de autenticación heredada se
limpia en padre e hijos; el focal independiente con ambas variables de IdP
configuradas pasó **1/1**. La integración relacionada y composición constan
PASS en el informe de implementación (4 contratos kept/0 broken).

La prueba modela caída/reconexión de proceso local. No acredita partición de
red, replicación, consenso, HA, fallo de energía ni soporte multiplataforma:
usa `fork` y libera el puerto reservado antes del bind. No toca DB del
operador ni proveedores. Evidencia:
`specs/001-verifiable-orchestration/workflow-status.md` y
`/tmp/caos-exec/T072/sol-r2-technical-review.md`.

## T096: composición cerrada y límites de startup verificados (2026-09-25)

T096 queda `[x]`: el servidor compone el gateway durable tras `init_db()` y
`get_backend()` con registro de backends explícito y vacío por defecto. La
clave efectiva exacta y el preflight determinan la admisión; ausente,
desconocida o incapaz devuelve 503 sin efecto. Un backend falso capaz sólo
produce un recibo `queued` en SQLite temporal, sin terminal ni proveedor.

El seguimiento cubrió cancelación durante `PluginRegistry.load()` conservando
`CancelledError` y un singleton Herdr ajeno, rechazo del schema corrupto por
el factory real después de `init_db()`, y limpieza condicional del Herdr propio
tras una falla posterior al registro. `PluginRegistry` limpia el plugin cuyo
setup falla o se cancela antes de registrarlo, incluso bajo cancelación repetida;
un fallo de cleanup no sustituye el error original. En teardown normal aísla
`CancelledError` originado por un plugin. La cancelación externa se transmite
al plugin activo, se espera su fin y el de los siguientes, y después se
propaga al caller; están cubiertas la cancelación repetida y la carrera antes
del primer paso de la tarea. El lifespan cierra telemetría en `finally`.
Gate ext4 con hashes coincidentes: 100 pruebas afectadas PASS, 4 warnings,
exit 0 en 6,92 s; composición PASS (271 archivos, 1006 dependencias,
4 contratos kept/0 broken); revisión Sol PASS. Un plugin que suprima la
cancelación indefinidamente aún puede bloquear el teardown. **T017/T019/T035
siguen `[ ]`**; no se activan ingreso público, proveedor ni DB del operador.
Evidencia: `specs/001-verifiable-orchestration/workflow-status.md`,
`/tmp/caos-exec/T096/affected-current-hash-matched.log` y
`/tmp/caos-exec/T096/verified-final-composition.log`.

## T097: revisión formal parcial y backend Work aún bloqueado (2026-09-26)

El fence de efectos Work liga terminal, sesión y ventana al intento durable;
la ruta ordinaria de input/key consulta la propiedad Work aun tras reinicio.
El supervisor no hereda el entorno del servidor. La carrera de inserción que
antes se compensaba en un backend en memoria no acreditaba teardown tmux: una
sesión o ventana nueva puede reutilizar el nombre. En tmux 3.6 privado, el ID
`$0` se reutilizó después de reiniciar el servidor; `$session_id` sin fence de
instancia tampoco basta. Un probe create-then-hydration-failure dejó un recurso
y mostró un intento de cleanup por nombre (**RED, 1 fallo; sin GREEN**).

La revisión de composición identificó ABA en rollback, `kill_session` y
`kill_window`. El delta actual rechaza los tres efectos Work antes del
transporte y conserva `reconcile`; no se acredita limpieza real ni reattach
tras reinicio. Root verificó 28 paths por SHA-256 entre worktree y snapshot
ext4. La suite T097 consolidada, incluidos los dos archivos de
caracterización Bubblewrap con build scratch 0.13.0 verificado, dio
**164 passed, 4 warnings, 0 skipped, exit 0 en 83,65 s**. Sus 12 probes
caracterizan bypasses, sin aceptar el backend. Composición PASS: 274 archivos,
1016 dependencias y 4 contratos conservados/0 rotos. `git diff --check` salió
0 y la auditoría whitespace pasó 31/31. Logs:
`/tmp/caos-exec/T097/final-affected-pytest.log` y
`/tmp/caos-exec/T097/final-composition.log`.

El nuevo preflight de tokens de comando sólo valida sintaxis: fragmentos shell,
nombres relativos, rutas no normalizadas y `//` inicial se rechazan antes del
probe de versión Bubblewrap. `/usr/bin/python3` llega a ese probe y después
al rechazo genérico fail-closed; no se acredita identidad del ejecutable ni
confinamiento de exec descendiente. El gate independiente ext4 de cinco
módulos Bubblewrap/enforcement/registration pasó **107/107, 0 skipped en 9,54 s**;
composición fresca PASS (274 archivos, 1016 dependencias, 4 kept/0 broken).
La suite 164/4 anterior precede este incremento. El probe rootless con UID
host 1000, un solo mapeo `0 1000 1`, `newuidmap` ausente y fallos de
`--map-auto`, mapeo anidado y `setresuid(1,1,1)` no acredita UID host distinto
por intento para el proxy.

El incremento acotado de red rechaza `contract.network` no vacío antes
de `_probe_bubblewrap`: RED creó un marcador al ejecutar el falso
`--version`; GREEN verifica el motivo `UnsupportedWorkEnforcement` y el
marcador ausente. Revisión independiente PASS para ese orden y el rechazo
fail-closed; **21 passed** en el módulo focal y **108 passed** en la suite
combinada backend/seguridad reportada por root. No acredita aislamiento
de red. Una caracterización sólo de test confirma que `UNCERTAIN` puede
resolverse tras salida natural observada; no agrega fuente productiva ni
prueba reattach tras reinicio. El checkout actual del módulo de supervisor
dio **7 passed**. La suite completa del supervisor en ext4 shadow tuvo
**4 errores antes de collection** por
`PermissionError: /mnt/c/DumpStack.log.tmp`; no es evidencia PASS.
Composición fresca PASS: 274 archivos, 1016 dependencias, 4 contratos
conservados/0 rotos. No se usaron proveedor ni DB del operador.

**T097 y T019 siguen `[ ]`.** Bubblewrap permanece sin registrar y rechaza
todos los contratos Work; el host usa 0.11.1. Faltan política de ejecución
frente a loader/memfd, launcher y aislamiento integrado de rutas/red/IPC,
identidad UID/FD segura del proxy entre intentos, fence de instancia tmux,
teardown y conciliación durable tras reinicio. T019 conserva credencial,
hijo/handoff y ACK; T035, ingreso público. Dictamen:
`specs/001-verifiable-orchestration/t097-formal-review.md`.

## T097: inventario noexec y cleanup parcial solo en tests (2026-09-26)

Revisión independiente **PASS del incremento acotado; FAIL de aceptación
T097**. Sólo cambiaron dos archivos de test. El módulo del supervisor cubre
pidfd parcial, fallback y reattach después de un resultado `UNCERTAIN`, con
cierre de descriptores sujeto a prueba de salida sin elevar indebidamente el
intento original a `TERMINATED`. Un inventario aislado con Bubblewrap scratch
0.13.0 observó workspace, `/tmp` y `/dev/shm` como `rw+noexec`, runner
`ro+exec`, ningún mount `rw+exec`, y seis alias `/proc/self/fd/3` denegados
sin marcadores: tres exec directos y tres intentos por loader.

Evidencia comunicada y contrastada con código: supervisor **20 passed en
11,84 s**; suite consolidada de 12 módulos **151 passed en 23,43 s**;
inventario aislado **1 passed en 4,49 s**; composición **275 archivos, 1017
dependencias, 4 kept, 0 broken**; whitespace limpio en ambos tests. No cambió
código productivo, no se usaron proveedor ni DB del operador y no hubo commit.
`WORK_BACKENDS={}` y Bubblewrap sigue rechazando cada contrato antes del
efecto. Las pruebas usan mounts y fixture privados; no prueban launcher Work
integrado, identidad/digest de ELF estático, FD allowlist, aislamiento del
proxy hermano ni cleanup durable tras reinicio. T097 y T019 siguen `[ ]`.
Graphify existente se contrastó con fuente; al ser sólo pruebas, no se
refresca. Dictamen: `specs/001-verifiable-orchestration/t097-formal-review.md`.

## T097: contrato ELF estático, Landlock y staging auxiliar (2026-09-26)

**PASS acotado del incremento auxiliar; FAIL para aceptar T097.** V2 sigue
separado del binding v1 y describe sólo comandos con ELF estático
`x86_64/ELF64/little`, token canónico, digest y mapping exacto. La huella v1
se conserva. El parser exige `PT_LOAD` ejecutable y entrada dentro de bytes
cargados, y rechaza `PT_DYNAMIC`/`PT_INTERP`. Staging verifica el snapshot,
sella un memfd y lee de vuelta los bytes; Landlock sólo aporta reglas
`FS_EXECUTE` basadas en rutas, heredables por descendientes.

La composición de esas piezas aún falla: el `O_PATH` del memfd sellado dio
**EBADFD=77** al añadirlo como regla Landlock. Una prueba con regla para archivo
regular y seccomp ejecutó por `/proc/self/fd` otro memfd heredado no listado,
con **exit 43**. Es un bypass caracterizado, no un launcher seguro. Ninguna
pieza nueva tiene caller en admisión o supervisor Work; éste aún llama
`os.execvpe(argv[0], ...)`. `WORK_BACKENDS={}` y Bubblewrap rechaza todos los
contratos. El host conserva Bubblewrap 0.11.1 (<0.12.0); `newuidmap` ausente
impide acreditar UID host distinto y el proxy same-UID sigue bloqueado.

Gate final informado por root: 18 módulos **304 passed, 4 warnings, 0 skipped,
exit 0 en 36,64 s**; staging **10 passed, 0 skipped**; Landlock **8 passed**;
Black `--check` en ocho paths PASS; sin whitespace final; composición **278
archivos, 1020 dependencias, 4 kept, 0 broken**. Sin proveedor, DB del
operador ni commit. T097/T019 siguen `[ ]`. Graphify existente se contrastó
con fuente; no se refresca porque los módulos auxiliares no agregan call path
productivo y el árbol sigue muy dirty. Dictamen:
`specs/001-verifiable-orchestration/t097-formal-review.md`.

## T097: recuperación observacional del cleanup de procesos (2026-09-27)

**PASS del slice acotado; T097 sigue sin aceptación.** La identidad v3 durable
se contrasta mediante boot ID, PID, starttime y estado. Sin supervisor explícito,
`recover_process_cleanup` sólo observa identidades v3 y Bubblewrap y marca
`complete` tras probar que ambos procesos originales terminaron. Si siguen
vivos o la observación falla, conserva `failed`/`reconcile` y scheduler held;
no hace reattach, `pidfd_open`, señal ni redelivery. Con supervisor explícito
continúa la limpieza activa existente con revalidación y pidfd. La revisión
independiente final de Sol dejó el riesgo Bubblewrap sin supervisor como
**ADDRESSED**, sin nuevos hallazgos.

Gate fresco root: cuatro módulos **75 passed en 40,81 s**; Black `--check` en
cuatro paths unchanged; composición **283 archivos, 1033 dependencias,
4 kept / 0 broken**; búsqueda focalizada de whitespace sin coincidencias.
El `git diff --check` global devolvió 2 por cambios CRLF preexistentes/ajenos;
no se normalizaron. Bubblewrap sigue sin registrar y rechaza contratos,
`WORK_BACKENDS={}`, T097/T019 permanecen `[ ]`. Sin proveedores, DB del operador
ni commit. Dictamen: `specs/001-verifiable-orchestration/t097-formal-review.md`.

## T097: errores poll en espera pidfd (2026-09-27)

`_wait_pidfd_readable` en `work_bubblewrap_composition.py` ahora procesa primero
`POLLERR`/`POLLNVAL` y falla de forma cerrada, también si están combinados con
`POLLIN`/`POLLHUP`; ningún evento de error puede certificar cleanup. RED
paramétrico: **2 fallos**; GREEN focal: **5 passed**; módulo completo y repetido
por root: **34 passed**. Revisión independiente Sol: **PASS**, sin hallazgos
Critical/Important/Minor. Black `--check` en dos paths unchanged; composición
**283 archivos, 1033 dependencias, 4 kept / 0 broken**; búsqueda focalizada de
whitespace sin coincidencias.

El alcance se limita al helper/composición scratch: `launch_staged_static_elf`
no está integrado en `WorkAdmission` y no se habilita backend. T097/T019 siguen
`[ ]`; Bubblewrap sigue sin registrar y `WORK_BACKENDS={}`. Sin proveedores,
DB del operador ni commit.

## T097: pidfd tri-state y recuperación supervisada de huérfanos (2026-09-27)

El helper pidfd ahora conserva tres resultados: `POLLERR`/`POLLNVAL` producen
`None`; sólo el timeout limpio (`False`) autoriza el paso pre-señal; y la
finalización después de señal requiere `True`. Con supervisor explícito, si el
monitor desapareció o su PID fue reutilizado, la recuperación puede terminar
el init sobreviviente de ese namespace exacto sólo tras repetir la prueba de
boot/PID/starttime/namespaces y validar el target del pidfd. Sin supervisor la
recuperación continúa siendo observacional. Bubblewrap `--die-with-parent`
tiene una carrera de arranque PDEATHSIG; la integración de pruebas actualiza
sus dos resultados y el gate de ownership del pidfd propiedad del test.

Evidencia: RED del caller, **6 fallos esperados**; RED de huérfano, **1 fallo**;
GREEN focal, **18 passed** (incluye seis negativos); módulo completo actualizado,
**33 passed**. Suite fresca de root de cuatro módulos, **92 passed en 46,08 s**;
Black `--check` en seis archivos unchanged; composición **283 archivos, 1033
dependencias, 4 kept / 0 broken**; búsqueda focalizada de whitespace sin
coincidencias. Revisión independiente Sol: **PASS**.

T097/T019 siguen `[ ]`; `WORK_BACKENDS={}` y Bubblewrap continúa sin registrar
y fail-closed.

## T097: snapshots de start y reattach estricto del supervisor (2026-09-27)

Durante `start()` se comparan los snapshots estructurales tomados con
`strict_monitor_argv=False` antes de aceptar identidad; luego se valida argv
exacto. `reattach()` conserva validación estricta contra la identidad
persistida. Una identidad ausente no autoriza señalizar ni confirmar salida;
se conserva el resultado pidfd tri-state. El aborto evita invertir
`_lock`/`_wait_lock` con `RLock` y adquisición no bloqueante; si otro waiter
impide la comprobación, el intento queda `UNCERTAIN`.

Evidencia fresca: **104 tests focales pasan**; Black `--check` en dos archivos
unchanged; composición **283 archivos, 1033 dependencias, 4 contratos kept,
0 broken**; revisión Sol **PASS**. T097/T019 siguen `[ ]`; `WORK_BACKENDS={}`.

## T097: revalidación de contenido ejecutable en snapshot (2026-09-27)

`WorkExecutableContent` revalida `WorkContracts._revalidate_order` dentro de
`read_snapshot` antes de seleccionar V2, token o catálogo; `store.read` y staging
siguen después de cerrar el snapshot. RED verificó que revoked, expired,
replaced y terminal alcanzaban el read. Gate root focal de
`executable_content`, `contract_binding` y `executable_staging`: **80 passed**;
Black `--check` en dos archivos unchanged; composición **283 archivos, 1033
dependencias, 4 kept / 0 broken**; revisión peer Sol **PASS**.

El helper no se integra en `WorkAdmission` ni en el launcher y no acredita
autoridad al release. `WORK_BACKENDS={}`; T097/T019 siguen abiertos.

## T097: validación de ELF en argumentos bound-data (2026-09-27)

`bubblewrap_bind_data_arguments` compara la identidad mutable con el ELF del
memfd sellado antes de construir argv. Valida el límite de **1..8 MiB** antes
de leer; `_read_exact` usa `pread` y preserva el cursor. RED: mismatch de
identidad y memfd disperso >8 MiB producían **DID NOT RAISE** antes del cambio.
La suite root de args, staging, Bubblewrap composition, bound content, executable
content y contract binding pasó **131**; Black `--check` en dos archivos
unchanged; composición **283 archivos, 1033 dependencias, 4 kept / 0 broken**;
peer review Sol **PASS** para seguridad.

`OSError` de `pread` conserva `errno`, se propaga y falla cerrado para retener
el diagnóstico. Sin autoridad de launch ni integración en `WorkAdmission`;
`WORK_BACKENDS={}` y T097/T019 siguen abiertos.

## T097: advisory lock de ownership del cleanup (2026-09-27)

Lock advisory por intento/generación compartido entre
`recover_process_cleanup` y `cleanup_attempt`: contención rechazada antes de
acceder a identidad y espera de cleanup fuera de transacción SQLite. Un crash
ordinario deja el intento pendiente para reanudación por otro proceso; falta de
`fcntl` falla cerrado. La reservation permanece retenida. Un hijo sólo de
`fork` puede heredar el lock y retrasar recuperación, fail-closed intencional.

Gate root: **75 tests focales passed**; Black `--check` en tres archivos
unchanged; composición **283 archivos, 1033 dependencias, 4 kept / 0 broken**;
revisión independiente **PASS**. T097/T019 siguen abiertos; `WORK_BACKENDS={}`;
sin GO ni registro.

## T097: persistencia de setup pre-GO v30→v31 (2026-09-27)

La migración aditiva v31 guarda la intención `pending` inmutable con FK a la
identidad Bubblewrap exacta. `record_pre_go` revalida autoridad, intento
`sent` y revisión exacta, contrato V2, token/digest/catálogo y ACK canónico
acotado en un `BEGIN IMMEDIATE`; replay idéntico es idempotente y el fallo
revierte identidad e intención. `read_historical` verifica la evidencia tras
perder autoridad sin conferir permiso de ejecución. Revisión independiente
**PASS** acotado; gate root: **110 tests passed**, Black en **5 archivos**,
whitespace localizado limpio y composición **PASS** (285 archivos, 1037
dependencias, 4 kept, 0 broken).

Graphify previo se contrastó con fuente; refresh intentado sin completarse:
corpus 248 code/20 docs sin API semántica, AST `--code-only` completado y
resolución de rutas 9p atascada hasta interrupción exit 130. `graph.json` y
`.graphify_ast.json` intactos; refresh diferido. Sin integración a
GO/release/backend ni registro; `WORK_BACKENDS={}` y T097/T019 siguen `[ ]`.

### T019 local: aceptación Docker convergida — 2026-09-29

T019 queda cerrada para el runtime Work Docker por intento y su aceptación
Bubblewrap local reproducible. La suite Docker real pasó **7/7** en Docker
Desktop 29.8.1/WSL2: daemon Unix local obligatorio, workers hermanos concurrentes
aislados, cierre durable de issues MCP sin efecto y conciliación tras caída real
del proceso owner, sin redelivery. Worker e issue estaban activos antes del
SIGKILL; Docker puede detener el attach al desaparecer el cliente, y el recovery
elimina el artifact etiquetado que queda. La aceptación Bubblewrap
QEMU previamente cerrada para T097 pasó **8/8 sin skips** en Ubuntu 26.10 con
Landlock ABI 11 y Bubblewrap 0.13.0.

Se actualizaron recovery bundle/inventory al Work schema38 preservando lectura
histórica de los perfiles anteriores. `WORK_BACKENDS` sigue vacío; no existe
aceptación ni despliegue de producción, y no se habilitaron ingreso público,
proveedores reales ni la base de datos del operador. Cualquier host futuro exige
una tarea y aceptación propias.
