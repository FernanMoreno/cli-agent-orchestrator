# Research — 041

## Decisión H2.2b.2: cargo de cola retenido y un solo ledger

Un token pendiente en una cola es insuficiente: observe/invalidate/stop pueden devolverlo mientras el payload aún existe. Se enlaza el token al worker financiado antes de publicación y solo ese worker acredita su rechazo/finish. La cola se limita por recursos, sin una constante de agentes. Se exige que startup y monitor compartan DeliveryAdmission para no duplicar ledgers físicos ni ocultar el coste de infraestructura. Si ese ledger no tiene capacidad fresca/autoridad, start falla cerrado; no se desactiva shared-required ni se crea una muestra para desbloquearlo. El emisor externo que proporciona ese estado sigue siendo dependencia productiva no acreditada. Diagnóstico puede usar caché sin buzón; el primer corte no añade callbacks arbitrarios ni activación shared.

## Decisión H2.2b.1: financiar antes de Thread.start

CommandReservation.start actual fija identidad desde el callback, demasiado tarde para financiar la creación del hilo o impedir liberación de una reserva aún pendiente. Se añade un lanzamiento interno que registra el hilo exacto antes de su inicio y conserva el cargo hasta join. Alternativas rechazadas: cargar desde el hijo; confiar en Future.done como prueba física; liberar tras un error de start; crear un ledger con capacidad inventada para el bootstrap. La financiación externa y la activación shared siguen siendo contratos separados. ECC Arquitectura y Python revisan estas fronteras sin escrituras paralelas.

## Decisión: dueño común, no una nueva constante

Cambiar4por8 o solo ampliar `event_bus_max_queue_size` conserva el problema. La profundidad de cola no es ejecución y tres presupuestos por entrada suman consumo oculto. Se separa una política pura del dueño común de reservas para poder verificar presión y ciclo físico de vida independientemente del scheduler.

## Decisión: capas local, compartida y colocación

M1 utiliza observaciones frescas y mantiene los pendientes existentes. M2 aporta atomicidad entre nodos; las medidas de presión por sí solas no reservan capacidad. M3 reutiliza el intento durable de asignación elástica ordinaria y exige preparación además de lease de infraestructura. El registro local de peers no demuestra recursos libres; no se asume autoscaler existente.

## Decisión: incertidumbre contabilizada

No se usa expiración de una observación como prueba de liberación. Los awaiters y los hilos tienen vidas diferentes; una cancelación antes del inicio se puede liberar, después del inicio requiere fin físico. Se conserva ese principio para conexiones y nodos remotos.

## Decisión: costes y unidades explícitas

Los costes por operación son estimaciones configurables, no límites globales de agentes. Toda decisión identifica el recurso limitante y cobertura. Se rechazan datos imposibles en lugar de aplicar defaults silenciosos que acrediten recursos inexistentes. La aceptación física debe contrastar las estimaciones.

## Evidencia fuente

`inbox_service.py`: consumidor, barridos, exclusión, operaciones y drenaje. `legacy_memory_audit_repository.py`: sesiones físicas y autoridad, límite4/2s. `settings_service.py`: profundidad de cola y límite opcional de terminales. `elastic_assignment_service.py`: intención y broker; no capacidad genérica. `work_scheduler.py`: política durable de Work separada. Graphify:564nodos coincidentes/1031relaciones internas históricas contrastadas con esas fuentes. Artefactos Graphify ajenos preservados.

## T005 — Fronteras de jerarquía y márgenes por dominio

La revisión y cuatro regresiones reproducen tres fallos del primer observador: exigir archivos de control no-root en la raíz real; declarar completa una vista virtualizada; y combinar min(límites) con min(headroom) antes de reservar, perdiendo el margen de un dominio más grande ya presionado. Se preservan pares de límite/headroom por dominio y se calcula el margen antes de tomar mínimos. La observación no puede atestiguar por sí misma su namespace: por defecto es parcial y no aumenta admisión. Solo un bootstrap de confianza, vinculado a la encarnación y dominio reales, puede declarar una jerarquía completa y distinguir raíz host de raíz delegada. Esos hechos no pueden venir de mensajes, perfiles o contexto del modelo. La integración de esa prueba sigue enT006/T007.

Fuentes primarias verificadas: [interfaz cgroup v2 del kernel](https://docs.kernel.org/admin-guide/cgroup-v2.html) y [cgroup_namespaces(7)](https://man7.org/linux/man-pages/man7/cgroup_namespaces.7.html). Los archivos de límites indicados se documentan para grupos no-root; las vistas de pertenencia y montaje pueden estar virtualizadas. La existencia de valores locales no demuestra que todos los ancestros sean visibles.

## T006 — Política operativa separada de atestación

ConfigService no distingue operador y admite rutas desconocidas desde su CLI. Sus lectores genéricos pueden sustituir valores inválidos por defaults, lo que no sirve para presupuestos de seguridad. El MCP no tiene setter genérico y las rutas HTTP actuales solo cambian directorios de agentes/skills. Por ello la política nueva se carga como documento versionado y privado del operador, separado de settings.json, con cambio atómico tras validar todo y retención de última válida solo en el mismo ámbito. Un arranque inválido queda sin política. El documento solo contiene costes/márgenes/intervalos; no reservas, autorización, jerarquía o identidad. Esos estados y la autoridad siguen requiriendo PostgreSQL y un Principal verificado con permiso admin; la dependencia legacy de scopes default-off no basta.

La presión PostgreSQL es agregada del servidor alcanzado por un target, no una reserva ni prueba de identidad física del dominio. No cubre por sí sola límites de frontend de un pooler ni otros targets. M1 necesita mapeo de ámbito autorizado y M2 reservas compartidas; no se usará la identidad lógica de esquema/store como sustituto.

El lector privado comprueba el descriptor antes de leer; su apertura no bloqueante evita esperar por un escritor FIFO. El JSON rechaza campos repetidos en cualquier objeto. El exceso de anidamiento se clasifica como documento inválido y conserva la política anterior. La regresión usa20.000niveles dentro de64KiB: en el runtime CPython3.13,2.000niveles todavía se decodifican aunque el límite de recursión Python sea1.000. No se reduce un límite del intérprete para fabricar la reproducción.

Una sonda puede fallar después de abrir físicamente y antes de entregar un handle: tanto la construcción como __enter__ pueden ocultar un cierre fallido. Ese caso queda bloqueado sin nuevas aperturas ni autocancelación; cuando existe handle solo su testigo closed permite continuar. Un fallo del proveedor de destino anterior a la apertura no genera deuda física y puede recuperarse. T008 debe mantener un único dueño y no sustituirlo al recargar política para eludir esa incertidumbre; crear otro sampler no prueba retiro. El timeout de conexión existente sigue en10s y la consulta tiene su presupuesto local configurable,500ms por defecto; no se promete un timeout global del hilo ni se libera por cancelar su awaiter.

T007 requiere protección central de la tabla de atestaciones después del GRANT genérico de cada migración, además de comprobar privilegios efectivos de tabla/columna/PUBLIC en verify_runtime_role. No basta revocar DML una vez en su propia migración ni añadir una función SECURITY DEFINER accesible al rol runtime. La vinculación derivada del servidor incluirá generación, namespaces y digest de pertenencia/vista; una identidad lógica peer no prueba recursos físicos. La atestación acredita una declaración autorizada del operador, no verifica por sí sola ancestros invisibles.

## T007 — Esquema y ACL, alcance parcial

El historial resource_envelopes se registra solo en main, antes de import_receipts para conservar el contrato de recibos como último grupo. La protección común retira ACL de tabla y de columna para PUBLIC/runtime; REVOKE ALL de tabla no retira las concesiones independientes de columna. Se comprueban privilegios efectivos y se rechazan relaciones sustituidas por vistas. El reset de esquemas de prueba también realiza grants genéricos: debe reutilizar esa misma protección después, antes de verificar cualquier repositorio. La regresión ampliada reprodujo y corrigió esa contaminación sin debilitar el guard de runtime.

La fixture nativa postgres_targets entrega esquemas vacíos, por lo que la preparación aplica explícitamente la migración. Las operaciones CREATE del dueño indican el esquema: el adaptador coloca pg_catalog primero en search_path. Se conservaron los fallos de preparación de vistas, distinguiéndolos de los fallos que sí alcanzaron la protección; la selección final comprueba ambos límites. Las comprobaciones SQL de actor solo validan formato, no procedencia de un Principal. Faltan escritor administrativo CAS y loader opaco; el esquema no completa T007 ni M1.

### T007 — Writer y loader completados en alcance de componentes

El writer verifica Principal sellado/admin antes de acceder a configuración/credenciales y deriva la vinculación en el servidor. El repositorio comprueba rol/esquema frescos con runtime readonly antes de utilizar el dueño explícito, comprueba propiedad de esquema/tabla y serializa el historial por instancia mediante CAS. La transacción fuerza READ COMMITTED como primera sentencia: una lectura posterior al lock ve el commit precedente incluso con un default de sesión REPEATABLE READ. Dos conexiones físicas concurrentes prueban una única escritura y un conflicto, sin fallback de credenciales. El lector compara la vista nuevamente después de SQL y no rescata una autoridad anterior tras fallo, deriva o revocación.

La revisión reprodujo tres fallos antes de corregir: errores RuntimeError de los productores de identidad salían sin razón fija, y revocar sin historia aparentaba un estado durable con revisión0. Se traducen únicamente los fallos esperados de identidad; los errores de programación del repositorio no se ocultan. El caso vacío lanza EnvelopeNotFound sin escribir. La captura por defecto de /proc se comprobó en un proceso propio con identidad/target lógicos sintéticos; no abrió credenciales/SQL ni concedió autoridad. Son controles de componente, no productor operativo ni aceptación M1.

### Control nativo de presión entre instantáneas

Un intento final conservado pasó 299 casos y falló la comparación global 2→2 al añadir un backend controlado; no conservó PID externos para identificar retrospectivamente cuál cambió. La comparación asumía un inventario externo constante. El control corregido conserva en la misma transacción readonly el inventario independiente de PID, verifica igualdad exacta con el contador global y con la fórmula de reserva/clamp, y exige caída exacta de 1 del headroom atribuible a los participantes 1→2 después de contabilizar los externos observados. No cambia el SQL productivo, no reescribe resultados, no reintenta hasta verde ni reduce la selección. Los PID quedan en el JUnit privado, no en el recibo público.

Se contrastó PostgreSQL18 en sus fuentes primarias: [pg_stat_get_db_numbackends y pg_stat_get_activity](https://raw.githubusercontent.com/postgres/postgres/REL_18_STABLE/src/backend/utils/adt/pgstatfuncs.c) y [snapshot de backend_status por transacción](https://raw.githubusercontent.com/postgres/postgres/REL_18_STABLE/src/backend/utils/activity/backend_status.c). Ambas vistas consumen la misma copia del inventario de backends; se filtran los mismos OID de bases visibles en la transacción. Cerrar el cliente no demuestra que su entrada haya desaparecido ya del inventario del servidor.

## T008 — Reconfiguración y etapas (componentes implementados)

Restar solo el número de operaciones a una capacidad calculada con costes nuevos revalorizaría las reservas antiguas. Por ejemplo, una reserva de64MiB seguiría consumiendo64MiB después de cambiar el coste a1MiB. Se necesitan huellas originales por lease, cantidades restantes por etapa y totales agregados O(1); los costes nuevos solo calculan nuevas admisiones. El presupuesto autorizado es un límite adicional de cantidades después de aplicar los márgenes físicos, no una constante de agentes. Una etapa PostgreSQL acabada puede devolver sus unidades sin liberar prematuramente memoria/CPU, receptor ni trabajo físico. El monitor posterior deberá mantener muestras/generaciones, reducir de inmediato y estabilizar la recuperación sin recrear una sonda cuyo cierre sea incierto.

El primer monitor permitió iniciar handoffs tras presión y no limpiaba referencias al fallar la sonda. La revisión y cuatro RED añadieron inventario separado de pendientes, poda bajo el mismo lock, join/cleanup con retiro y validación de cada timestamp antes de mínimos. Otra RED pausó refresh antes de adquirir el lock y confirmó I/O después de stop/start; un ticket de lifecycle ahora se comprueba bajo un guard común al entrar y después de adquirir el lock. Los fallos y las revisiones se conservan en validation-t008-monitor.json; 283 controles finales no acreditan integración o aceptación de consumidores/red.

## T012 — primer ledger compartido (decisión 2026-11-01)

El primer corte implementa PostgreSQL compartido canónico main: dominios versionados y membresías de proceso administrados por Principal verificado/credenciales de dueño, e intentos/allocations/counters runtime. La provisión autorizada declara el agrupamiento físico; una URI o UUID de solicitud nunca descubre ni acredita ese agrupamiento. No se conectan entregas antes de T013, ni se aceptan cuotas de tasa o colocación M3 por este componente.

Cada operación mantiene un ID durable idempotente, proceso/generación/PID/inicio derivados por wiring privado, estados preparing/active/releasing/uncertain/released y CAS. Las cantidades y presupuestos son unidades explícitas; el primer recorrido nativo contrasta sesiones de almacenamiento. Presupuesto reducido o revocado conserva deuda. No hay expiración liberadora ni reemplazo silencioso de encarnación física.

Un contador incremental por dominio evita recorrer el historial de operaciones al admitir. Reserva multi-dominio actualiza encabezado, allocations y counters en una transacción READ COMMITTED. Locks transaccionales separados para operación y dominios, ordenados/deduplicados por clave efectiva; provisión de autoridad usa los mismos locks de dominio y lee después de adquirirlos. Las tablas de autoridad son solo lectura runtime; no requieren SELECT FOR UPDATE. Colisiones solo serializan y no mezclan identidad/cantidades. Clientes conformes comparten rol runtime: el protocolo no pretende aislar un proceso hostil con credenciales SQL.

Expansión aditiva, sin backfill ni borrados, componente versión1 antes del último import_receipts; autoridad protegida tras cada grant genérico. Rollback operativo: no activar el wiring, conservar tablas/datos para conciliación; ninguna contracción implícita. RED de contrato antes de implementación; PostgreSQL existente con dueño verificado y pruebas seriales, dos procesos reales, atomicidad/CAS/idempotencia/ACL y retiro físico. T012 no se marca completo hasta sus pruebas; M2/T013/T014/T020 siguen separados.

Fuente primaria de locks y espacios de claves: [PostgreSQL18, advisory locks](https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS). Se usan familias separadas de dos enteros para operación/dominio; los locks transaccionales terminan con la transacción.

T012, endurecimiento antes de aceptación: el contador incremental evita SUM del historial por mensaje. Una auditoría inicial y explícita posterior compara counters con allocations/estados en una única snapshot MVCC; no repara ni libera filas. Divergencia/error conocido bloquea nuevas reservas y activación en ese repositorio; la recuperación distribuida sigue en T014. La transacción revalida el conocimiento antes del commit (rollback) y después (SharedCommitUncertain con ID/cargo durable retenido). Protocolo entre escritores conformes bajo rol compartido, sin aislamiento de SQL hostil. Se prueban cantidades de bytes/millicores/sesiones; no se acepta presión física ni cuotas de tasa. Preparadas pruebas de escritor administrativo real competidor y medición de consultas ordinarias antes/después de historial liberado. UUIDs canónicos fijan scope/keys, y preparing→active exige revisión/membresía/vista aún actuales; finalizar lo ya poseído conserva su crédito.

T012 backend cerrado (2026-11-01): migración aditiva/autoridad protegida, ledger atómico multi-dominio/counters, identidad privada y CAS/idempotencia bajo realm canónico. 226PASS de regresión amplia más1caso nativo disjunto de drift→active (227únicos,0fallos/errores/omisiones); fuentes ejecutadas exactas, formato10/tipos6/composición5KEPT/0BROKEN y mismoPG0/0/0. Dos procesos reales compiten por1sesión; salida no libera deuda. UUID/activación/counter-commit REDs conservados; auditoría independiente, escritor real y cantidades3unidades probados. GraphifyAST privado parcial14archivos preserva el grafo ajeno (97referencias pendientes/54colapsos potenciales); dependencias contrastadas con fuente. Obsidian actualizado3páginas/lint0. No se activan flujos T013 ni recuperación T014, cuotas de tasa, M1/M2/M3 físico, SDK/UI/global. [Validación](validation-t012-shared-ledger.json), [composición](composition-t012-shared-ledger.md).

## T013 — conjunto completo y frontera de cierre

Validar cada dominio recibido no prueba completitud. La reserva mapeada toma un lock por instancia/generación antes de operación y dominios; provision_member comparte ese lock. UUID5 de la pareja canónica pasa por el realm store/schema del hash de lock. Un escritor real bloqueado en PostgreSQL y una membresía añadida antes de active prueban el límite. Cada miembro forma parte de la huella, aun con coste0.

El primer RED nuevo tuvo7fallos por API ausente; el primer GREEN55PASS. La revisión detectó que cubrir una unidad con algún dominio no exigía host+nodo. Cuatro negativos reprodujeron DID NOT RAISE antes de corregir la condición. La selección final247PASS conserva los dos RED y no cambia límites/cobertura/deadlines. Distinción: v1→v2 prueba preservación de header nullable; los caminos raw/mapped y cierres/idempotencia se prueban en v2, sin afirmar migración de una red productiva.

Revisión de composición encontró que _release_locked corre bajo guard y no debe hacer SQL. La última devolución física puede retirar el crédito local antes de cerrar la reserva compartida; un hold/ticket exacto y un presupuesto independiente para metadatos deben preceder al cableado. El commit de released no demuestra cierre de su propia sesión. La fuente actual cierra admisión/control antes de drain; no puede reutilizarse ese permiso inválido. Estos son requisitos pendientes, no correcciones implementadas en este checkpoint.


## T019 — visibilidad real de rutas de montajes

Fuente actual _groups selecciona max(profundidad root), lo que omite una vista más amplia visible. Un montaje cubierto todavía figura en mountinfo y puede producir rutas hacia otro cgroup. Se contrastó [proc_pid_mountinfo(5)](https://man7.org/linux/man-pages/man5/proc_pid_mountinfo.5.html): montajes apilados enlazan su parentID al montaje inferior; la accesibilidad también depende de prefijos de ruta visibles. La [documentación cgroup v2 del kernel](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html) describe pertenencia y namespaces; la afirmación complete_hierarchy no descubre padres ocultos.

No se añade lectura completa de cgroup.procs en cada muestra: su coste crece con todos los procesos del grupo. Se prioriza validar topología y negar vistas ambiguas, con trabajo ligado a montajes/ancestros. Bootstrap ya captura namespaces mnt/cgroup y compara vista; no duplicar esa corrección como pendiente inexistente. Pruebas sintéticas reproducen decisión del parser, no mount real ni aceptación física.


[proc_pid_fdinfo(5)](https://man7.org/linux/man-pages/man5/proc_pid_fdinfo.5.html) documenta mnt_id del montaje que contiene el descriptor. Esto permite comprobar resolución efectiva incluso con overlays en prefijos sin reconstruir un árbol ambiguo ni recorrer cgroup.procs. Las lecturas relativas de controladores y fstat usan los directorios comprobados; montajes de archivo individuales también se rechazan por mnt_id. Se cierran candidatos rechazados, cuerpo fallido y pruebas interrumpidas. La vista membership/mountinfo se compara al salir; cambios descartan muestra/historia. Namespace host/ancestros invisibles siguen requiriendo prueba externa.


## T014 — alcance del descubrimiento de deuda

La revisión confirma keyset/índice live, PK de créditos y necesidad de una sola sentencia para snapshot por página. No equivale a enumeración completa bajo inserciones UUID concurrentes: comenzar un nuevo barrido descubre IDs inferiores al cursor; ending no concede liberación. Se conservan estado/revisión persistidos y cabeceras descriptivas, sin sumar cantidades de unidades incompatibles. Header y allocations de una reserva preparing se confirman atómicamente en _reserve; la mera fase no prueba ausencia de cargo.

Se mantiene el fence de binding privado antes/después y se rechaza lectura cuya vista cambió, en vez de presentar current_owner incierto o permitir fallback de un dueño inválido. Un custodio independiente correctamente financiado tendrá su propia vista válida; no se implementa aquí. El lector no exige auditoría limpia ni repara divergencias: inspección no es nueva admisión. Nueva interfaz SPI no expone HTTP/SDK ni libera/rehace trabajos.


## T014.C2b — identidad de sesiones y límites de ausencia

Punto de partida contrastado antes del cambio: Connection, database._native_orm_connection y LegacyMemoryAuditConnectionManager usan DatabaseTarget.connection_parameters; el nombre legado común no distinguía procesos. La documentación primaria PostgreSQL18 define application_name y los campos pid/backend_start de pg_stat_activity: [vista de actividad](https://www.postgresql.org/docs/18/monitoring-stats.html#MONITORING-PG-STAT-ACTIVITY-VIEW), [application_name](https://www.postgresql.org/docs/18/runtime-config-logging.html#GUC-APPLICATION-NAME). Las vistas se cachean por transacción y la visibilidad depende del rol; no usar una lectura vacía como autorización. La etiqueta es mutable/descriptiva y solo su presencia tiene valor conservador.

Decisión: primero atribución opt-in del proceso y transporte autenticado; no introducir una retirada con testigos incompletos. Un ledger posterior al TCP no cubre aperturas todavía en vuelo. C1 es un sello lógico de nuevos trabajos, no un sello de las conexiones de finalización financiadas. La nueva frontera deberá distinguir ambos cierres y conservar toda incertidumbre.


P1, revisión de concurrencia/entorno: dos cold misses con PID heredado distinto publicaban dos estados. La regresión fuerza ambos constructores con una barrera; la elección de sucesor usa un único setdefault con clave PID entera, sin adquirir el mutex heredado y sin retener historia de PID en el estado vigente. Se contrastó la sección crítica de [dict.setdefault en CPython3.13.15](https://github.com/python/cpython/blob/v3.13.15/Objects/dictobject.c#L4113). El lanzador elimina solo PGAPPNAME de la copia del entorno del hijo para imponer su atribución; el entorno del operador y las validaciones estrictas de los demás parámetros quedan intactos. Una composición marcada manualmente sin lanzador sigue rechazando PGAPPNAME contradictorio.

La auditoría mantiene application_name en su huella: dentro del proceso es estable y el fork invalida entries/opening/latest_profiles antes de resolver un perfil nuevo. No se excluye el campo para esconder un perfil obsoleto. Fork conserva modo con otra etiqueta; exec posterior al consumo del FD sin nuevo supervisor vuelve al modo legado. Ninguno de estos descendientes se declara cubierto por una observación del líder.


## T014.C2b.P2a — cierre terminal de nuevas aperturas

C1 y el drenaje abren SQL financiado; el finally exterior de Bridge.main posterior a asyncio.run conserva ese orden incluso ante error startup. At-fork lee sin mutex heredado: decrementar opening antes de aumentar unconfirmed perdía el intento en el estado intermedio. RED determinista antes de invertirlo; fork real conserva incertidumbre y cierre. No exceptuar callbacks arbitrarios del pool por el tipo de una negativa tardía: puede existir un raw anterior. La vista de contadores no cubre raw-registration ni sesiones servidor. [Validación](validation-t014-sql-fence.json).


## T014.C2b.P2b — registro financiado previo a TCP

La financiación del registro no puede obtener otro TCP sin recursión: usa el raw de control original y un préstamo/scope todavía activo del mismo worker. psycopg suprime Rollback en su context manager: exigir final de cuerpo/estado INTRANS evita convertir rollback en acuse. Registrar antes del driver separa fallos de preparación e incertidumbre TCP; un commit sin ACK conserva UUID, no autoriza efecto. El primer backend de control se fija positivamente, sin inferir ausencia de destinos. Lectura/fence alternativos son de la misma identidad viva, no recuperación postcrash. [Validación](validation-t014-sql-intents.json).


## T014.C2b.P2c.A — orden del bootstrap supervisado

Probar que el helper de publicación no usa SQL no prueba el orden del entrypoint: _amain ejecutaba init_runtime_db antes de invocarlo. La corrección adelanta solo el bootstrap supervisado y reutiliza la misma instancia tras consumir FD. En la prueba negativa, cerrar un seqpacket con datos sin leer puede causar reset; consumir el paquete y enviar NACK explícito hace determinista el rechazo sin relajar la ausencia de SQL ni el join. [Validación](validation-t014-pre-sql-bootstrap.json).


## T014.C2b.P2c.B — registro explícito previo al acuse

La inscripción de sujeto debe tener tabla propia para no falsificar el significado de una apertura local. Cuota y UUID se comparten bajo lock de journal usando máximos de dos índices, sin scans crecientes. Una fábrica de candidato/no-op no basta: aprobación débil más set/token del supervisor, con expiración real, distingue dato de capacidad. Distinguir commit sin ACK de commit confirmado con ACK posterior no completado; preservar el recibo al atravesar cada capa pública. La prueba real de upgrade acredita compatibilidad de filas, no autoriza runtime anterior sin migración. [Validación](validation-t014-child-sql-intent.json).


## Retención antes de prestar la unidad del hijo — T014.C2b.P2c.C1

El crédito de control no es capacidad de datos; sumar una unidad local bajo ese lane podría saltarse el mapeo compartido. Reutilizar una reserva previamente iniciada/preparada y su unidad original, sin SQL recursivo. Se reprodujo RuntimeError en finish con préstamo externo pero sin hold: exigir hold antes del cargo conserva el finally normal. Rechazar binding no permite emitir ProcessExitObservation autenticada; las fixtures negativas exigen waitid estructurado y marcador negativo, sin inferir que None equivale a proceso vivo. [Validación](validation-t014-child-session-charge.json).


## Emisor real y publicación restrictiva — T014.C2b.P2c.C2

SO_PEERCRED por sí solo identifica el socket original, no al escritor de cada respuesta: se reprodujo el control con escritor fork y se exige SCM_CREDENTIALS del padre original. Latch tardío no basta tras limpieza del entorno; hacerlo al importar runtime. Fork observa asignaciones intermedias sin mutex original, por lo que required/failed deben publicarse primero. OSError de la vista kernel se traduce a rechazo tipado predriver. El fallo inicial de destino era fixture role frente a user; exigir cambio real del digest. [Validación](validation-t014-child-sql-permission.json).


## Permisos efectivos y confirmación de la fila — T014.C2b.P2c.C3.A

Crear una tabla no la integra automáticamente en la política central de ACL. La primera ejecución reprodujo40rechazos correctos del verificador: faltaba declarar la tabla append-only en el migrador. Se corrigió el provisionamiento, no el verificador. Otra reproducción mostró que ignorar RETURNING permite anunciar un recibo sin inserción; ahora se valida la fila devuelta y se compara con el recibo esperado antes de commit. Mantener separados ACK perdido, commit incierto y pérdida del sujeto después de commit. [Validación](validation-t014-child-followup-intents.json).


## Esperas, incertidumbre y propiedad antes de consumir — T014.C2b.P2c.C3.B

El timeout del primer recv del servidor no ha consumido un mensaje SEQPACKET y debe ser Idle, no EOF terminal. En cambio un timeout del cliente después de enviar conserva un UUID posiblemente confirmado y termina el canal. Un proveedor muerto instalado inventaba incertidumbre por cada rechazo posterior: se reproduce y corrige separando errores locales antes de enviar, desconexión del proveedor bajo guard y cierre fuera. También se reprodujo un poll desde task ajeno: ahora se verifica el canal/worker financiado antes de cualquier IPC. La prueba de paralelismo exige barrera dentro del driver y continuidad del mismo backend; esperar solo tras abrir no demuestra que el lock estuviera libre durante el driver. [Validación](validation-t014-sql-opening-broker.json).


## Primera conexión, rechazo y testigo de salida — T014.C2b.P2c.C3.C

El control HTTP/WS propio de Bridge arranca después del primer SQL y no puede financiarlo. Un contexto con canal ya financiado conecta una frontera real pero no resuelve genesis. La primera nativa rechazó correctamente un FD heredado de la fixture; otra ejecución rechazó la vista inicial antes del driver y su causa concreta no quedó capturada. No se relajó el guard: instrumentación posterior solo observa identidades. La revisión reprodujo una salida normal sin observación; ahora conserva handle/error. La fixture anterior de restore usaba async def dentro de asyncio.to_thread y se corrigió a función síncrona, conservando sus aserciones. [Validación](validation-t014-funded-bridge-startup.json).


## Alcance frío e inferencia de tipos — T021.T

Mypy.ini es la configuración efectiva del repositorio; no se modificó. La revisión de dos entrypoints reproducía ocho diagnósticos importados; src/ completo sobre la base, mediante shadow-file de las fuentes originales, también daba ocho, con conjuntos distintos según resolución del ciclo WorkService/WorkOrigins. Tras las tres primeras correcciones el cono quedó limpio y src/ reveló un único retorno Any en local_peer_service; asignar el JSON a object conserva la comparación exacta. La comprobación final usa cachés nuevas en ambos alcances y obtiene cero errores. [Validación](validation-t021-typing.json).


### T014.R.G — raíz externa y cierre del círculo SQL

La primera conexión necesita un emisor de despliegue ya autorizado y financiado por su propio canal PostgreSQL. No existe aún integración productiva que cree ese emisor: una opción, un fichero o el login de un proveedor no son autoridad. El helper de emisión exige principal JWT administrador verificado antes de resolver target, claves o SQL. Firma exclusivamente un commit confirmado; ACK perdido, error de claves o pérdida posterior conservan fila/deuda y no permiten replay.

El emisor y el destinatario son bindings distintos: la cuota/posición pertenecen al destinatario, mientras la capacidad de firma sigue ligada al worker original del emisor. El owner read usa el subject retenido y los créditos persistidos, sin convertir cambios de membresía activa en permiso nuevo ni impedir diagnóstico histórico. La raíz compara firma/kernel/binding/label/destino antes del TCP y confirma la fila en el mismo canal antes de cederlo.

G4.D conserva la etapa/categoría de un rechazo pre-subject mediante campos estructurados y, si el runtime soporta add_note, una nota JSON segura visible en traceback. Esto corrige la pérdida de observabilidad, no demuestra la causa del rechazo histórico previo a commit_ack. Ningún guard, timeout o criterio se relaja.


### T014.R.H1 — transacción externa y puertos reutilizables

La transacción nativa puede crear un savepoint si ya hay una transacción abierta: eso no confirma un commit externo. La sesión operativa exige IDLE antes de cada operación y delega exclusivamente en funded_transaction, manteniendo la financiación y verificando rollback/IDLE antes de reutilizar. El scope existente ya valida task; no fue necesario ampliar ni debilitar Connection/_ControlSqlFunding. El repositorio fija sesión y binding en construcción para impedir fallback/races de bind mutable. Todos los lectores directos deben pasar por la sesión opt-in; los writers privilegiados se rechazan antes de su auditoría. El muestreo no adopta el handle como probe propio y mantiene su timeout LOCAL sin modificar la sesión. La revisión encontró una caída postcommit que se clasificaba como denegación: RED en dos recorridos y corrección a incertidumbre, sin refund/rearmado.


### T014.R.H2.1 — monitor y autoridad originales

El constructor leía control_storage_slots antes del preflight y podía esperar un lock ajeno: el negativo real mantiene ese lock y demuestra TimeoutError antes de corregir el orden, sin ampliar el plazo. Bootstrap convertía un DatabaseError con rollback no verificable en fallo ordinario: un reader SQL real y fallo de observación reproducen el bug antes de revalidar sesión en el except. La revisión read de Claude detectó este segundo caso; su afirmación de constructor sin locks fue refutada por el lead con la reproducción. Los errores de financiación y commit incierto heredan RuntimeError, no el tuple DatabaseError que captura bootstrap. Reconfigure/observe ya niegan cuando owner no acepta; un permit activo no concede admisión de datos ni memoria/CPU para un actor nuevo.


### H2.2a — motivos y evidencia previa

El contador available_slots es el mínimo de unidades estándar de entrega y puede ser0 por storage aunque un comando RAM/CPU quepa: se usa comprobación positiva por componentes con capacidad reciente. Un registro separado evita receivers falsos y crédito shared/data accidental; ningún comando puede obtener un préstamo SQL. El control actual financia PG pero tiene footprint RAM/CPU0, por lo que esta reserva no resuelve el bootstrap de un actor nuevo. La presión recorre solo pendientes relevantes en orden de secuencia, con mezcla lineal.

El productor diagnóstico anterior sumaba solo deliveries y storage-only: un comando legítimo rompía el validador y la ruta devolvía503 en el RED privado. Añadir campos a v1 extra=forbid rompería consumidores estrictos; se elige versión2 y409 explícito para v1 incompatible. Un wirev1 congelado antes del cambio y casos de URL reales contrastan compatibilidad. El fallo de fábrica None del recorrido completo H2.1 exigió conservar la llamada anterior, sin cambiar su test; los consumidores/fábricas se buscan antes del siguiente congelado.


H2.2b.2, revisión de implementación: finish y publicación del reply se serializan con el guard del ledger para que snapshot/admisión no vean cargo liberado con Future pendiente. No se ejecutan SQL ni callbacks de aplicación bajo ese guard. Los errores de retirada conservan el cargo y el error primario. Ready/recuperación/capacidad son señales distintas; el arnés observa el ciclo real y cierra en finally únicamente cuando falta prueba de retiro. [Resultados y límites](composition-t014-original-control-owner.md).

## Decisión H2.3: resolver el ciclo antes del primer TCP

Inspección ECC/lead: cao-server ejecuta init_db antes de runtime_from_environment; el monitor legacy necesita SQL antes de observar capacidad. Sus credenciales runtime no son owner del journal SQL5. funded_bridge no tiene caller productivo y su grant hijo no es root ni RAM/CPU. Por tanto no se promueve el endpoint admin ni se traslada su conexión temporal al actor.

Un paquete firmado separado transporta autoridad explícita del emisor owner-funded; la medida local sigue siendo necesaria. Storage desconocido no se convierte en cero conocido: el arranque utiliza un tipo/reserva solo RAM/CPU y conserva la admisión normal desconocida. Keyring y canal emisor siguen entradas reales del despliegue, no se fabrican desde settings/peer identity. El primer readback impide aceptar una revisión revocada/cambiada tras emisión.


### H2.4a: elegir handoff de comando

PhysicalEffectReservation está ligado a una entrega y callbacks de cierre; ChildSqlSessionCharge financia SQL. Ninguno debe reinterpretarse como la obligación RAM/CPU padre de un supervisor genérico. Se amplía la familia de handoff de CommandReservation, como CommandThread, conservando ResourceProcessSupervisor como única fuente de prueba de proceso. Su wait sin observación tras handshake fallido no se debilita para obtener un refund. Esta conservación puede dejar deuda sin vía de reconciliación: custodia sigue separada.
