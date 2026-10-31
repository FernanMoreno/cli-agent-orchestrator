# Admisión dinámica y capacidad distribuida — Implementation Plan

**Branch**: `main` | **Date**: 2026-10-30 | **Spec**: [spec.md](spec.md)

**Goal**: Sustituir techos locales de despacho por reservas según recursos, coordinar dominios compartidos y distribuir encargos nuevos sin alterar autoridad, orden o recuperación.

**Architecture**: Separar política pura, observación de recursos y dueño de reservas. M1 aplica la misma admisión a los caminos locales conservando exclusión por receptor y contabilización hasta el fin físico. M2 añade reservas PostgreSQL por dominio y encarnación; M3 conecta ese contrato a asignaciones elásticas ordinarias nuevas, sin migrar terminales ni desviar Work.

**Tech Stack**: Python del proyecto, asyncio y threading existentes, PostgreSQL mediante sus adaptadores actuales, pytest y herramientas de composición actuales. Sin dependencias globales ni SQLite.

## Global Constraints

- Preservar deadlines, auditorías, permisos, orden por receptor, Context/registry de cada aviso y resultados inciertos.
- Main es la única rama de integración. No tocar los doce artefactos Graphify preexistentes ni credenciales.
- Una cancelación de awaiter no acredita fin de hilo/proceso/conexión. Reservas activas o inciertas siguen contadas.
- M1 no completa M2/M3; una configuración variable o una prueba con20/32 agentes no acredita crecimiento ilimitado.
- Los tamaños de prueba y los presupuestos de máquina no son techos globales del producto.

## Technical Context

Servicio API y proveedores locales/remotos existentes. InboxService mantiene hoy un consumidor con4slots y dos barridos con ejecutores propios; las llamadas directas son otra entrada. Un budget por cada entrada duplicaría capacidad. El pool auditado tiene su límite físico4 y checkout2s independientes: no se eleva para esconder presión. No existe un anuncio genérico de capacidad ni una reserva multinodo reutilizable.

Los datos durables y la identidad de efectos siguen en PostgreSQL. Las observaciones locales son transitorias y jamás autorizan ni completan trabajo. Todo estado futuro de reserva recuperable M2 debe persistirse allí.

## Constitution Check

Inspección git y conocimiento: completados. Spec aprobada por el usuario; planificación y tareas antes de código. Graphify histórico consultado, relaciones contrastadas con fuente; se conserva sin regenerar los artefactos ajenos. Cada cambio usa RED/GREEN, comprobaciones de composición, revisión y evidencia de alcance. Sin excepciones a autorización ni modificaciones de credenciales.

## Project Structure

- `services/admission_policy.py`: modelos inmutables y decisión pura de capacidad; no I/O ni autoridad.
- `services/admission_postgres.py`: presión agregada de un servidor consultado con rol runtime/readonly; una sonda viva y retiro confirmado, sin identidades ni errores privados en resultados. No sustituye reservas ni un mapeo autorizado de dominios.
- `services/admission_settings.py`: política operativa privada y validada, próxima lectura y retención de última válida en el mismo ámbito; no usa el escritor genérico de ConfigService ni contiene atestaciones de jerarquía.
- `services/admission_bootstrap.py`: verifica envelope explícito de operador persistido en PostgreSQL contra identidad/encarnación/namespaces del proceso; solo devuelve un objeto verificado al observador.
- `clients/admission_bootstrap_schema.py`: componente main de historial de atestaciones; runtime solo lectura, con protección central de ACL después de cada migración y del reinicio de esquemas de prueba. El esquema no acredita al actor ni concede capacidad.
- `models/resource_envelope.py`: contratos inmutables de presupuestos y vinculación, sin I/O ni dependencias de infraestructura.
- `clients/admission_bootstrap_repository.py`: lectura runtime con verificación fresca y escritura del dueño con revisión CAS e historial; no promueve credenciales implícitamente.
- `services/admission_resources.py`: observaciones de host/cgroup y presión PostgreSQL con antigüedad y fallos explícitos.
- `services/delivery_admission.py`: dueño común de reservas, drenaje, exclusión y estadísticas estructuradas locales.
- `services/inbox_service.py`: consumidor, barridos y llamadas directas usan ese dueño; no generan capacidad adicional.
- `clients/admission_repository.py` y migración PostgreSQL: reservas y dominios compartidos M2, independientes del dueño de Work.
- `services/elastic_assignment_service.py`: preparación/reserva confirmadas antes del primer efecto de colocación M3.
- `test/services/test_admission_policy.py`, `test/services/test_delivery_admission.py`, `test/services/test_admission_resources.py`: controles puros, físicos y de cancelación.
- Pruebas de integración en `test/integration/test_inbox_resource_admission.py`, `test/postgres/test_admission_reservations.py`, `test/integration/test_elastic_capacity_admission.py`.

## Milestones and Interfaces

### M1 — Política y dueño local

`AdmissionPolicy` contiene costes estimados por entrega, margen de memoria/CPU/almacenamiento, frescura y estabilización. `CapacityObservation` expresa unidades disponibles con instante monotónico y cobertura. `evaluate_capacity(policy, observation, now)` devuelve `AdmissionDecision(available_slots, reason, constraints)` sin efectos. Se rechazan bools, NaN, infinitos, cantidades negativas y muestras del futuro/caducadas. Desconocido significa admisión cerrada, no capacidad infinita.

La decisión expresa nuevas unidades disponibles en una observación; el dueño no puede volver a gastar una misma observación al cambiar el número de reservas. El dueño local reserva antes de enviar trabajo a un executor; cada reserva diferencia reservada, iniciada y liberada. Solo una cancelación antes de iniciar puede liberar la reserva sin fin físico. Se permiten disminuciones bajo consumo activo con deuda explícita; no nuevas admisiones hasta recuperar margen. Reservas por receptor son únicas. La política aplica a todo el conjunto de entradas, no uno por consumidor.

Las medidas de CPU deben considerar afinidad/cuotas efectivas y presión; memoria debe considerar host y límites efectivos de cgroup, sin duplicar un dominio. El muestreo de PostgreSQL no consulta contenido de mensajes ni omite auditoría de operaciones funcionales. La instrumentación tiene su propio tiempo/acceso acotados y no se ejecuta por mensaje.

T008 conserva una huella inmutable por reserva y totales incrementales de bytes, millicores y unidades de almacenamiento. Cambiar los costes no revaloriza el trabajo activo: se restan sus cantidades originales antes de convertir el headroom restante a unidades del coste nuevo. El presupuesto del operador limita las cantidades comprometidas, además de los márgenes físicos observados. Finalizar una etapa de almacenamiento permite liberar solo ese componente mientras memoria/CPU y exclusión del receptor permanecen hasta el fin físico. La deuda se expresa también por recurso; un total de operaciones activas puede superar las unidades de arranque disponibles sin deuda cuando varias terminaron ya su etapa de almacenamiento. No se recorre el conjunto de leases por cada admisión.

### Prerrequisito de confianza para M1

La investigación no encontró una atestación de recursos reutilizable. La presencia peer y el UUID de host por usuario solo sirven como identidad lógica. Se requiere un envelope explícito y finito del operador, persistido en PostgreSQL y ligado por el servidor al proceso/encarnación y a los namespaces de montaje/cgroup actuales. Una declaración de presupuesto no demuestra jerarquía completa; esa afirmación adicional requiere un Principal admin verificado y ámbito explícito. No se aceptará de settings, variables de entorno, perfiles o solicitudes ordinarias. El loader devolverá un objeto verificado; ausencia, revocación, cambio de namespaces/generación o imposibilidad de verificar conserva cobertura desconocida.

Antes de conectar M1 deben existir este loader y sus controles de autorización/persistencia. No se activarán los flags del observador con True para obtener pruebas verdes. Los valores de política de consumo son configuración, separados de la autoridad de atestación.

El Principal acredita quién autorizó una atestación del operador, no la verdad de ancestros invisibles. La cobertura declarada requiere contraste independiente con el host/orquestador. El servidor verifica su vinculación a generación, namespaces y digest de pertenencia/vista de montaje; cualquier deriva invalida esa vinculación. Un escritor administrativo con credenciales de migración protege frente a DML del rol runtime, pero no frente a un proceso comprometido que posea esas mismas credenciales; la separación fuerte exige mantenerlas fuera del runtime.

El servicio T007 comprobará Principal sellado y scope admin antes de resolver el target o sus credenciales. La solicitud aporta únicamente claims/revisión esperada; proceso, store, namespaces y vista se derivan en el servidor. La escritura CAS serializa por instancia dentro de una transacción READ COMMITTED explícita para observar el último commit después del lock, incluso si el servidor tiene otro aislamiento por defecto. Revocar añade un estado inactivo al historial; el lector consulta la última revisión, sin rescatar una atestación antigua activa. Solo una lectura runtime fresca con esquema/rol válidos y vinculación actual exacta devuelve un resultado opaco verificado. No se añade aquí una ruta HTTP ni una herramienta MCP de administración.

### M2 — Dominios compartidos

Cada dominio tiene identidad, encarnación, presupuesto y revisión. La reserva atómica conjunta incluye consumos de Work cuando comparten recursos, sin cambiar su dueño. Cierres inciertos y nodos perdidos no liberan por mera expiración. Una generación antigua no puede liberar reservas nuevas. Competencia entre dos procesos y fallos en adquisición/cierre son controles nativos obligatorios.

### M3 — Colocación autorizada

El dueño del intento durable de asignación elástica ordinaria selecciona un destino compatible con observación fresca y reserva confirmada. Infraestructura creada no es trabajador listo. Se comprueban preparación, identidad y autoridad antes del primer envío; incertidumbre posterior usa el contrato de conciliación existente. Work y terminales ya ubicados no se redirigen por esa ruta.

## Validation Manifest

Antes de aceptación real se congela un manifiesto con memoria en bytes, CPU en unidades normalizadas, presión de almacenamiento y plazos en segundos. Para controles de política iniciales: muestra máxima3s, reserva de memoria20%, coste estimado64MiB por entrega y250millicores por entrega. Son estimaciones de consumo configurables que requieren contraste físico; no constantes de concurrencia. Una muestra aporta unidades de almacenamiento disponibles, no permisos ni una ampliación del pool auditado.

Se verifica crecimiento por cambios de recursos y política a igual código; caída inmediata de admisión ante presión/desconocido, recuperación estable y deuda activa. Los controles usan reloj/observaciones deterministas, no sleeps. Los valores finales de muestreo y aceptación física se congelan en su propio manifiesto antes de ejecutarlos; sin él no se marca aceptación.

M1 requiere regresiones de orden/contexto/sink/shutdown/barridos/API, observación de recursos y un recorrido real con presupuestos contrastados. M2 exige PostgreSQL real y dos coordinadores. M3 exige nodos de ejecución reales y un resultado correlacionado. Se conservan las muestras2/4/8/12/20/32 y fallos previos; no repetirlas sin cambio relevante.

## Execution

Seguir [tasks.md](tasks.md). Root es único escritor; investigadores/revisores Codex solo lectura. Pruebas nativas serializadas bajo dueño PostgreSQL verificado y memoria mínima6144MiB. Ejecutar composición después de pruebas y revisar diff; registrar conclusiones durables sin duplicar esta spec en el vault.

## T008 implementado — ciclo local

AdmissionMonitor mantiene un único sampler PostgreSQL, recargado bajo su lock sin olvidar cierres inciertos. Carga política y atestación antes del muestreo y revalida la misma revisión/vinculación después. Rechaza componentes futuros antes de tomar el timestamp mínimo, que incluye el inicio del intervalo CPU. Tras huecos reinicia ese historial. La recuperación actúa sobre cantidades físicas por recurso, con tres muestras sanas distintas por defecto; terminar una etapa libera su cantidad sin esperar histéresis. Una reducción retira primero los handoffs pendientes más recientes que ya no caben, sin recorrer ni retirar trabajo iniciado.

El ciclo captura generación antes del I/O y publica por CAS; un ticket de lifecycle capturado antes del lock impide reanudar sondas antiguas tras stop/start. El cierre conserva referencias físicas bajo cancelación, une sondas fallidas y llamadas síncronas activas, limpia solo con testigos de retiro y propaga el error original. El componente no crea autoridad operativa ni conecta Inbox: ambos son prerrequisitos explícitos de T009 y de la aceptación M1.

## T009 — integración por fronteras

Primero se conecta un dueño explícito a InboxService y a consumidor/directo/barridos, con pruebas de composición; la activación del singleton API y el productor administrativo se validan después, antes de cerrar T009. Durante la transición, las instancias antiguas sin dueño explícito mantienen su comportamiento; esa compatibilidad no acredita admisión M1 ni debe presentarse como activación del servidor.

El consumidor reserva antes de extraer ordinal/Context/registry. Aparca cabezas ocupadas conservando su aviso y avanza otros receptores; una señal de capacidad o fin físico reactiva el buffer, sin sondeo activo. Los trabajos admitidos usan hilos propios sin una segunda cola con techo fijo del executor; su cantidad queda limitada por reservas. El trabajador toma el lock por receptor sin espera y confirma start antes de SQL. Finish pertenece al finally físico. Los barridos cierran el cursor al denegar capacidad y antes de esperar sus hijos. Un lote num_messages>1 conserva una sola operación serial y su reserva hasta concluir todos sus envíos/estados; la estimación es por invocación serial completa: num_messages=0 conserva el máximo histórico100 y un positivo conserva el límite solicitado, y su aceptación física sigue pendiente.

El primer wiring mantiene almacenamiento hasta completar send_input y sus escrituras; no libera antes de auditorías/recibos ni eleva el pool4/checkout2s. La separación adicional de etapas requiere sus controles propios. Work/remoto permanecen en T010.

### T009 — activación operativa

CAO_ADMISSION_POLICY_FILE identifica el archivo privado de parámetros y activa el runtime de Inbox en ese despliegue. Su ausencia mantiene compatibilidad durante la migración; una ruta indicada inválida o un documento ilegible nunca vuelve al consumidor antiguo. El archivo no contiene ni concede claims de jerarquía. El runtime consulta atestaciones PostgreSQL actuales y no admite entradas mientras falten autoridad o recursos conocidos.

El productor HTTP exige administrador autenticado por JWT o sesión de navegador validada; el Principal implícito del modo local sin autenticación no autoriza atestaciones. POST de claims/revisión y revocación usan DTO estrictos, errores fijos y el CAS de T007. No se modifican credenciales existentes ni se habilitan flags de jerarquía por configuración.

La vinculación del dueño solo ocurre sin operaciones/sink/leases previos y con retiro comprobado del runtime anterior. El runtime retiene tareas físicas de escritura administrativa aunque se cancele su awaiter; se drenan antes de declarar retiro. Un sampler PostgreSQL persistente conserva incertidumbre entre reconstrucciones en el mismo proceso. Cada ciclo normal notifica cambios al Inbox, y Herdr encola avisos sin ejecutar SQL en el loop. La lectura de capacidad del pool es conservadora y respeta su presupuesto físico existente; no acredita la reserva compartida M2 ni liberación fina T018.


## T009 implementado — control operativo

El lifespan compone y enlaza un runtime único antes de aceptar entrada. El router administrativo comparte DTOs estrictos y exige identidad JWT verificada con cao:admin; consulta el registro, añade revisiones CAS y revoca mediante el writer PostgreSQL existente. No presenta registro como capacidad observada. Una escritura pendiente invalida publicación y conserva su trabajador físico aunque desaparezca el solicitante; los resultados inciertos no se borran al reconstruir el runtime. Todo apagado, incluido error/cancelación del cuerpo del lifespan, espera una tarea protegida que retira monitor, control, consumidores y Herdr. El arranque fallido une monitor/control antes de drenar entregas.

La prueba nativa HTTP/JWKS/PostgreSQL usa aplicación de control y runtime reales con identidad de prueba propia, PID/nacimiento/namespaces reales y jerarquía no verificada: acredita registro/CAS/historial/revocación y capacidad cero, sin conceder prueba de ancestros. El wiring de main se verifica separadamente. El techo físico del pool auditado permanece en cuatro sesiones; no se amplía un recurso para hacer pasar una rampa. Véase validation-t009-operational.json. Este cierre no acepta M1 ni implementa SDK/UI.


## T010 — handoff de recursos Work y dueño remoto

WorkAdmission recibirá por wiring de confianza el mismo DeliveryAdmission del proceso y su reloj; no crea presupuestos por adaptador ni recibe capacidad desde contratos del modelo. Tras resolver el terminal y cerrar el snapshot, reserva e inicia el coste antes del lock/commit a sent. La denegación devuelve ausencia de handoff y conserva el intento planned. Para lanzamientos aún sin terminal, la clave opaca corresponde al intento/generación, sin ampliar colocación. ExitStack retira preparaciones fallidas; la capacidad de efecto transporta la reserva al dueño del envío. La reserva dura hasta acabar envío, recibos y limpieza física. En modo ligado, cancelar el awaiter espera al callback admitido antes de propagar CancelledError al registro Work de reconciliación; no cancela su trabajo físico. La preparación abandonada mantiene su reserva hasta la reconciliación física. API detiene el dispatcher antes de retirar el monitor y rechaza retirement si quedan leases.

El puente necesita su propio dueño por proceso y atestación ligada a ese proceso/destino; el runtime_id, el token de canal y el envelope del API no la sustituyen. Con opt-in y autoridad ausente se debe denegar INPUT sin efecto. La revisión de fuente confirma que falta productor autorizado de atestación Bridge: integrar las reservas y probarlas con wiring controlado no bastará para cerrar la aceptación remota. El contrato del productor se resolverá sin copiar credenciales ni marcar jerarquía completa automáticamente. La reserva remota precede el turno/efecto y no se libera por cancelar to_thread; el rechazo tipado sin efecto debe llegar como PENDING, mientras respuesta perdida mantiene RECONCILE.

TDD: PostgreSQL real para planned/denegación/reintento, ledger compartido por receptor, reserva durante handoff/efecto/registro, fallo de commit y cancelación física; pruebas de composición del wiring. Controles de puente posteriores mantienen epoch/encarnación y rechazo sin efecto. No se acepta M1 con este checkpoint ni se repite la rampa32 sin integración completa.

T010 refinamiento antes de aceptar: el modo ligado requiere adaptadores registrados con destino durable; dispatch callbacks ad hoc se rechazan explícitamente antes del preview (compatibilidad sin owner intacta). Elegibilidad usa un peek O(1) que no reserva y omite receptores ocupados; el commit exime solo al intento exacto ya reservado, manteniendo todos los guards Work. Sin headroom global no se sondea almacenamiento Work. El handoff de tareas difiere el callback una vuelta incluso con factorías eager y conserva su dueño ante cualquier fallo del await wrapper, no solo CancelledError.

T010 revisión de fronteras: conflictos conocidos de destino v1/v2 excluyen solo ese candidato; los fallos inesperados de almacenamiento abortan la selección sin efecto. El lookup de metadatos es readonly por candidato en el scan existente O(N), sin reselecciones O(N²), y no se abre el scan con headroom global cero; su coste/locks siguen sujetos al contraste físico M1. La preparación difiere el submit del hilo hasta obtener el Task owner y transfiere cualquier fallo del wrapper a la ruta existente de abandono/drenaje conservando el error original. Nuevos RED prueban stale/v1, error de store no omitido, factoría eager y wrapper que falla con preparación ya física.


## T010 Bridge — productor local y participante de recursos

Con CAO_ADMISSION_POLICY_FILE presente, Bridge compone antes de aceptar INPUT una aplicación FastAPI mínima con solo admission_routes y su runtime de recursos; no ejecuta el lifespan del API central. Escucha exclusivamente en127.0.0.1 y puerto efímero (o puerto de despliegue explícito), exige el mismo JWT admin verificado y no usa el token de canal como autoridad. No copia/renueva credenciales ni introduce archivos de credenciales/descubrimiento. El ready file existente conserva exactamente PID+newline; la URL administrativa es propiedad/log de arranque, no un contrato de texto para modelos. Uvicorn0.37.0 captura señales en serve: la composición embebida desactiva esa captura y conserva SIGINT/SIGTERM de Bridge.

API y Bridge pueden compartir perfil/destino: ResourceEnvelope actual se particiona por instance_id, por lo que Bridge usa UUIDv5(profilePersistentUUID, "bridge-resource/"+runtime_id exacto y sensible a mayúsculas). Es solo clave lógica, no prueba ni autorización. No incluye runtime_incarnation_id: el proceso reconstruido conserva la clave y cambia process_generation/PID/inicio, invalidando la atestación anterior. Una identidad mínima inmutable se deriva una vez por proceso/ámbito con PID/inicio reales y UUID nuevo, sin cargar/crear clave privada peer; captura de namespaces/vista/target permanece local. Un mismo runtime_id identifica un único Bridge activo; otros IDs y el API no pisan su historial. No se comparan relojes de nodos ni se asume reserva compartida M2.

Sin opt-in se preserva compatibilidad. Opt-in con ruta inválida/auth ausente falla antes de INPUT; sin atestación/muestra válida hay capacidad cero. Cada Bridge reutiliza el mismo dueño/reloj durante reconexiones. Reserva INPUT después del claim durable y antes del turno; la función física verifica/start la lease y la libera en su finally tras send_input y sus recibos. El awaiter solo cancela reservas sin iniciar y une el trabajador admitido antes de propagar cancelación. La denegación tipada sin efecto debe conservar PENDING; respuesta perdida sigue incierta/RECONCILE. Los controles/registro de transporte siguen fuera de esa lease de input; no se afirma contabilizar todo I/O de coordinación. El apagado cierra admisión, une manejadores/trabajos físicos con cancelación resistente y después retira el monitor/control administrativo. No se amplía enforcement Work remoto.

Validación antes de aceptación del componente: partición API/Bridge/IDs con PostgreSQL real, identidad propia en proceso hijo sin clave peer, HTTP/JWKS/PG real con claims incompletos y capacidad cero, pares de canal con recursos controlados para positivo/denegación/epoch/reconexión/cancelación física. Ninguno sustituye calibración/atestación física independiente y aceptación M1 deT019.

### T010 productor pendiente — acoplamiento de importación confirmado

La inclusión de admission_routes importa InboxAdmissionRuntime, cuyo módulo importa InboxService y construye su singleton al cargar. Antes del productor Bridge se separará ese acoplamiento por un port explícito de lifecycle/consumidor y anotaciones sin import runtime, preservando el controlador concreto y su type guard administrativo. Inbox sigue siendo el participante API; Bridge enlaza el mismo ledger/reloj y aporta drenaje real de handlers/hilos. Un builder compartido admite bootstrap de ámbito confiable sin copiar control/CAS/autoridad. Esta conexión y el productor HTTP siguen pendientes; no son funcionalidad implementada por el checkpoint INPUT.

### T010 implementado — estado posterior al diseño pendiente

El port AdmissionParticipant y BridgeAdmissionParticipant reutilizan el controlador sin importar InboxService. _amain compone bootstrap scoped/monitor/HTTP loopback antes del lector y supervisa el servidor de control; mantiene dueño/reloj a través de reconexiones. Retiro exige testigo almacenado en Bridge y cierre de sockets pendientes de Hello, handlers físicos y actores de fondo; cancela la espera solo después del retiro protegido. Claims/rol/schema requieren aprovisionamiento explícito, sin autoautoridad ni credenciales copiadas. Final163PASS y cinco contratos mantenidos según validation-t010-control.json. T010 implementado; T011 y M1/T019 pendientes. El diseño anterior describe historia, no un pendiente actual del productor.

## T011 — fixtures con presupuesto controlado

La auditoría distingue concurrencia de entregas de tamaño de cola y del pool auditado: los cuatro slots físicos del pool y los recuentos de mensajes no se cambian. El helper consumer de status usa por defecto un DeliveryAdmission con observaciones explícitas y reloj controlado; la prueba de saturación varía2/4/7slots y conserva el caso sin owner de compatibilidad4. El caso específico de cola del executor antiguo mantiene modo sin owner; el caso de worker pausado antes de tracking se conserva sin owner y se añade ligado, restaurando presupuesto saludable tras reabrir para demostrar que la entrega antigua no cruza de lifecycle. El modo ligado además mantiene sus controles de cancelación física/arranque fallido. Contexto/registry, equidad, complejidad lineal, orden, capacidad combinada y drenaje conservan sus aserciones.

El burst de requests varía presupuestos1/4/7 más compatibilidad, manteniendo ocho wakes en espera y todas las aserciones de limpieza/contexto/loop. La integración PostgreSQL direct/poll varía el presupuesto recuperado1/2/7 conservando PENDING, contenido Unicode, una sola entrega y estado final. No se altera política ni código de concurrencia del producto. Se corrige además el docstring histórico de Bridge para reflejar el control loopback opt-in; el AST ejecutable debe permanecer idéntico. TDD primero reproduce la falta del parámetro de presupuesto de fixture y después ejecuta la selección Inbox y contratos aplicables. T011 no sustituye aceptación física T019/M1.

## T019 preflight y dependencia T018 — 2026-10-31

La revisión de fuente confirma que la observación operativa toma min(headroom PostgreSQL, pool auditado), cuyo máximo físico es4. Cada entrega conserva una unidad de almacenamiento hasta el callback completo; release_storage existe sin callsites productivos. Por tanto SC-001 no puede aceptarse bajo un dueño M1 con más de cuatro entregas activas. Agentes aún procesando tras finalizar la entrega no sustituyen ese criterio. El preflight conserva cobertura parcial y no atesta ancestros: la lectura de namespaces de PID1 no está permitida. No se ejecuta carga de aceptación ni se cambian flags para obtener verde.

Primer componente T018: reserva opaca de etapa de almacenamiento bajo la misma lease. Reclama la unidad inicial ya cargada o recupera una unidad tras su liberación, solo con observación válida, presupuesto/ceiling y headroom de almacenamiento; no vuelve a cargar memoria/CPU ni cambia receptor. Un token activo impide liberar almacenamiento desde la API antigua o terminar la lease completa; solo su fin físico devuelve esa unidad. Tokens antiguos/copias no liberan una etapa posterior. Sin capacidad, con muestra desconocida/caducada o cierre no se recuperan unidades nuevas. La reclamación de una unidad inicial ya cargada no equivale a nueva admisión.

TDD de aritmética, cierre y ownership; un control PostgreSQL real separa dos transacciones manteniendo receptor y costes. Este componente NO conecta todavía puntos de liberación en Inbox/Work/Bridge: antes se debe trazar todo acceso posterior, incluidos auditorías/recibos/plugins y lotes seriales, y asegurar reentrada/retirada incierta sin omitir SQL. No se amplía pool4/checkout2s, no se declara T018 completo ni M1 aceptado.


## T018 integración física — crédito de cierre y préstamos por conexión

La lectura conjunta de Inbox, Work, Bridge, MemoryService, psycopg y SQLAlchemy identifica tres fronteras: préstamo ORM (incluidos apertura/setup/pre-ping), Connection nativa (apertura/setup/cierre; borrow no posee otra conexión) y checkout auditado (reserva/setup/cuerpo/reset/release). Una variable de contexto privada se enlaza exclusivamente en el dueño físico y entrega un port neutral a esos adaptadores, sin dependencia clients→services ni monkeypatch de psycopg. No se afirma interceptar SQL arbitrario externo de plugins.

La unidad de almacenamiento inicial de la invocación se conserva como crédito ya admitido para cierre/recibos. Un préstamo por conexión usa ese crédito mientras esté libre y lo devuelve al terminar físicamente; no lo vuelve a conceder el monitor. Préstamos simultáneos/anidados adicionales requieren headroom fresco, cobertura completa, presupuesto/ceiling y admisión abierta; se liberan globalmente al terminar. Tokens distintos e identidad exacta permiten concurrencia de conexiones bajo una invocación; finish global rechaza préstamos físicos activos. La API gruesa previa sigue independiente y no se mezcla con préstamos activos. El crédito original no crece tras stop, invalidación o muestra caducada: solo permite reutilizar una cantidad ya reservada para terminar el trabajo iniciado. Ni el awaiter ni una copia del token retiran trabajo físico.

El pool auditado conserva max4 y checkout2s como dominio físico separado. El presupuesto global de conexiones PostgreSQL se obtiene de presión PostgreSQL/claims y deja de usar el límite de ese pool como techo de callbacks. Se conserva una unidad global por entrega hasta su fin, por lo que no se representa ese crédito como headroom libre. Más de cuatro callbacks requiere presupuesto global/CPU/RAM suficiente y no significa más de cuatro checkouts auditados. El muestreo no se convierte en reserva compartida M2.

Denegación de almacenamiento no debe perderse en el fallback de memoria: debe conservarse hasta el punto preefecto. Una denegación antes del transporte conserva PENDING; tras un efecto conserva incertidumbre/RECONCILE. El premarcado de lotes usa el crédito retenido y no puede sufrir un gate nuevo entre filas por falta de admisión. La integración prueba cierre con recibos, conexiones anidadas, setup/pre-ping/reuso, errores de conexión/cierre, no doble cargo borrow, aislamiento de ContextVar y API/Bridge/Work físico. No se acepta T018/T019 por pruebas solo del port o del ledger.

T018 frontera Bridge reproducible: el worker INPUT libera la lease antes de next_revision/settle y el mark_unknown de cancelación usa shield sin unir físicamente el escritor. La reserva de cierre debe abarcar esas operaciones y drenar cada llamada física aunque se cancele la espera; el ACK de red posterior no sustituye el resultado durable. RED parametrizado pausa revisión/settle con PostgreSQL real y comprueba reserva, cancelación repetida, cierre y replay sin duplicado.

T018 — huella física mínima de INPUT: el lock advisory por receptor mantiene una sesión PostgreSQL mientras el envío consulta o escribe por otra. `try_reserve` acepta una huella interna validada y contrasta todas las unidades de almacenamiento antes de crear la lease; Bridge declara dos unidades antes del turno/trabajador. Es una cantidad de consumo por operación, no un techo de agentes ni un dato del payload. Las pruebas separan entregas elegibles por RAM de sesiones físicas disponibles, comprueban aceptación de muestras y conservan PG1→rechazo sin efecto. Inbox/Work y SQL de control requieren todavía completar su propia composición antes de cerrar T018.

T018 — composición física Work: preparación conserva advisory y transacción bajo una huella mínima de dos sesiones, con contextos físicos separados del ExitStack transferido al port. La resolución del destino administrado debe poder usar la transacción actual: el lookup ORM anidado abría una tercera sesión. Se añadió un callback transaccional explícito al contrato interno DeliveryAdapter, manteniendo validación de store UUID/configuración/engine y coordenadas selladas. La entrega conserva su validación fresca previa al efecto. No se aumentan presupuestos para esconder el solapamiento. Los workers nativos de create_terminal requieren rebind del dueño Work dentro del callable real; copiar Context no basta, y el callback debe conservar ownership hasta el retiro físico.

T018 — workers de proveedor: una transferencia opaca de uso único se captura en la tarea/hilo autorizado y se consume por identidad en el worker unido. La denegación se comparte entre padre/hijo y se comprueba antes del callable y en la frontera de efectos; no se hereda autoridad por copiar Context. BaseProvider ofrece un único helper de I/O, utilizado por los70offloads de nueve adaptadores; sin scope mantiene el executor anterior. La creación y su compensación bajo admisión esperan a los workers reales incluso con cancelaciones repetidas, y una creación que nunca entró no transfiere su owner al compensador. No hay reglas de concurrencia por proveedor ni nuevos permisos MCP.

T018 — procesos hijos nuevos: la regresión de diez procesos reproduce el rechazo de conexiones nuevas por el registro global heredado; las pruebas del port privado requieren representar también su contrato de recursos completo. El fork coordina primero la separación de handles mediante un único callback; solo tras confirmación crea un registro de cierres propio del hijo y conserva los registros padres sin tomar sus locks ni retirar tokens. Una separación fallida bloquea incluso la apertura legacy sin scope. El scope heredado se conserva como inválido, y el ledger/reserva comprueba PID antes de permitir rebinding o acceder a locks heredados. Dueños nuevos del hijo usan su propio ledger. No se acredita fork durante libpq connect ni antes del registro del handle Python ni se infiere autoridad de recursos de otro nodo.

T018 — Inbox y descubrimiento (componente implementado y validado): cada entrega requiere dos sesiones; un scanner requiere una reserva propia de almacenamiento antes de avanzar el iterador, ligada al mismo ledger sin receptor ficticio. Su cursor se cierra dentro del scope físico antes del join de entregas, y el crédito permanece hasta el retorno confirmado. Se distinguen operaciones de almacenamiento y entregas en contadores incrementales; coste simultáneo 1+2N, con N determinado por recursos. El staging aislado mantiene su contrato genérico. Las fixtures conservan las mismas aserciones de entregas y declaran sesiones por separado; una sesión insuficiente no habilita transporte. Se reprodujo y corrigió que una copia de lease pudiera incrementar el contador de retiros pendientes; ahora la identidad se comprueba antes de mutarlo. Graphify anterior carece de los nodos nuevos y se contrasta con los tres try_reserve y el cursor real de database.py; se preservan los cambios ajenos del índice.
