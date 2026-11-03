# Data model — 041

## H2.2b.2 — actor original

OriginalControlConfig es composición interna; no DTO de cliente ni permiso de arranque. OriginalControlOwner conserva PID, worker financiado, estado de ingreso, ready privado, buzón de refreshes registrados y monitor/session locales al worker. Un refresh contiene token exacto y reply privado; su coste queda iniciado antes de publicación. Los registros salen del buzón solo hacia el worker original, que entrega valor/error y finaliza. La reserva de infraestructura pertenece al mismo ledger y permanece separada de los costes por mensaje. Estados iniciando/operativo/cerrando/incierto/retirado no se deducen únicamente de un Future terminado.

## H2.2b.1 — propiedad de hilo

CommandThread conserva identidad del token/hilo/PID, Future tipado y evento de retorno. El token referencia el handle exacto mientras conserva el cargo. Pendiente → iniciado/despachado ocurre antes del inicio físico; iniciado → liberado requiere retorno y join positivos. Un Thread.start fallido conserva estado iniciado incierto, reconciliable con ese handle si el hilo alcanza finalmente retorno y cierre. No se añade permiso SQL ni una observación de capacidad.

## M1: observación y decisión

- AdmissionPolicy: coste de memoria por entrega en bytes, coste CPU en millicores, fracción reservada de memoria/CPU, frescura máxima en segundos. Validación estricta finita y rangos explícitos.
- ResourceHeadroom: par inmutable de límite/headroom de UN dominio, en unidades enteras. Los márgenes se aplican a cada par antes de agregar; los mínimos independientes solo sirven de diagnóstico.
- CapacityObservation: instante monotónico, memoria disponible y límite efectivo en bytes, CPU disponible en millicores, capacidad de almacenamiento disponible en unidades, cobertura completa o desconocida. No contiene credenciales, permisos ni texto del usuario.
- AdmissionDecision: nuevas unidades disponibles bajo esa observación, motivo fijo y valores por recurso. Una decisión no es una concesión de autoridad.
- DeliveryReservation: identificador opaco, receptor/encarnación, estado reserved/started/released, generación del dueño y recursos reclamados. Una reserva iniciada solo se libera al terminar físicamente; una reducción permite deuda visible.
- StorageReservation (componente local T018 implementado y validado): operación de almacenamiento sin receptor, con estados y préstamos físicos en el mismo ledger. No consume el coste RAM/CPU de una entrega ni ocupa una identidad ficticia. El snapshot distingue sus operaciones de las entregas, pero el total de operaciones impide retirar un runtime con un scanner o cierre incierto activo.
- ResourceAmounts: cantidades enteras no negativas e inmutables de memoria en bytes, CPU en millicores y slots de almacenamiento. Cada lease conserva su huella original y sus componentes restantes; el dueño mantiene totales incrementales bajo un lock común, sin recorrer leases por admisión. La resta del ledger rechaza underflow.
- Reconfiguración local: CAS sobre generación capturada antes del I/O; una configuración distinta invalida muestras y cancela handoffs sin iniciar, conservando costes de trabajo iniciado. Omitir un presupuesto conserva el existente. Una recarga idéntica conserva generación y muestras válidas.
- Liberación por etapa: solo el lease iniciado y aún registrado puede liberar almacenamiento una vez. Memoria/CPU y exclusión del receptor permanecen hasta finish físico. El snapshot distingue operaciones activas, capacidad inicial, slots adicionales y deuda por cantidades; debt expresa el máximo de unidades equivalentes con el coste actual, no un conteo de operaciones. Desconocido/caducado mantiene todas las cantidades comprometidas como deuda sin cancelar trabajo activo.
- AdmissionMonitor/MonitorResult: un dueño de muestreo, política, atestación fresca y recuperación por cantidades. Resultados inmutables con motivos tipados y published explícito. El techo de recuperación se publica junto a la observación bajo su generación; no altera la huella de leases activos. Las reducciones podan solo el índice de pendientes, desde el más reciente, conservando los que aún caben.
- Lifecycle del monitor: ticket incremental protegido antes/después de adquirir el lock de sonda; stop/start y fallos del runner lo invalidan. Tareas físicas, evento de retiro y tarea de drenaje se conservan ante cancelación; solo el retiro comprobado permite limpiar referencias. No hay liberación de conexiones ni trabajo activo por la mera cancelación de un awaiter.

## Prerrequisito M1: atestación del operador (T007)

El componente main `resource_envelopes`, versión1, registra `_cao_resource_envelopes`: historial por instance_id/revisión, store UUID e identidad lógica del target, generación/PID/inicio del proceso, pares dispositivo/inodo de namespaces mnt/cgroup y digest de la vista de recursos. Incluye presupuestos positivos y finitos de memoria (bytes), CPU (millicores) y almacenamiento (slots), las declaraciones complete_hierarchy/root_is_host, estado activo/revocado y actor Principal verificado. No almacena rutas privadas ni credenciales. Una raíz host requiere jerarquía declarada completa.

El runtime solo puede leer; cada migración reaplica esa protección tras sus grants genéricos y verifica permisos efectivos. Registrar una fila requiere autoridad administrativa y CAS; el loader valida la última revisión y compara identidad, generación, namespaces y vista antes de devolver atestación opaca. La declaración del operador no demuestra por sí sola ancestros invisibles. Este historial no sustituye reservas de recursos ni acredita M1: faltan ciclo de observación/admisión, productor operativo e integración/aceptación.

## M2: persistencia PostgreSQL

ResourceDomain identifica host/nodo/almacenamiento/cuota con presupuesto y revisión. ResourceReservation vincula operación, dueño, encarnación, dominios y cantidades. Estados preparing/active/releasing/uncertain/released; transiciones condicionales a revisión e identidad. Los índices y la migración se definen en la tareaM2 contra el registro de migraciones vigente; no se reserva un número de migración prematuramente.

## M3: identidad de asignación

La reserva de colocación apunta al identificador del intento de asignación ordinaria y al trabajador/encarnación preparados. No cambia el dueño de Work ni el destinatario de mensajes existentes. Infraestructura concedida, destino preparado, entrada enviada y resultado confirmado son estados distintos.

## Diagnóstico local T017 (implementado/validado)

AdmissionLedgerDiagnostic congela snapshot, policy, budget, observation, ceiling y decisión bajo el mismo guard y una única observación de incertidumbre de cierre. AdmissionDiagnostics v1 es DTO estricto/inmutable process_local, con cantidades explícitas, counters de operaciones/entregas/storage/loans y capacidad nullable.

CachedAdmissionConfiguration identifica configuración instalada y referencia privada de la observación que la convirtió en base; basis_current no se deriva solo de generation. CachedPostgresProbe/CachedResourceProbe y CachedMonitorResult conservan telemetría ya obtenida con procedencia y edad propias. El GET no hace I/O de recursos ni devuelve identidad/rutas/contenido. No crea registros durables ni autoridad nueva.

## Cierre físico T018 (componente conocido validado)

Connection distingue _owns_raw y fachada borrow. StorageCloseRegistry mantiene raw/callback/closed-witness/loans opcionales por identidad en el PID; un record sin loan sigue siendo pendiente. PostgresPressureSampler lleva PID y referencia incierta, y retire_owned solo confirma cierre tras el retry del registro. Apertura incierta sin handle conserva el latch.

El lifecycle del runtime une su pasada de retry tras los cuerpos físicos, mantiene binding hasta los cuatro testigos (registry limpio/sampler/owner/monitor) y no interpreta cancelación como cierre. El fork crea sampler propio tras detach confirmado y conserva referencias extranjeras sin locks/retirar tokens.


## Crédito protegido de SQL T018

StorageControlPermit privado por identidad y PID; _StorageControlReservation iniciada en el mismo registro, sin receptor/coste RAM/CPU. Un préstamo físico simultáneo; retorno exacto permite secuencia. Crédito reservado1 y actividad se muestran separados de committed normal; operaciones/préstamos totales incluyen control. Budget normal S−1 se instala antes de recovery. Denegación anterior a callback no marca autoridad incierta; el scope protege ejecución y cierre antes del retirement. No modelo de dominio compartidoM2 ni autorización nueva de datos.


## Puerto normal de operaciones y lifetimes

StorageOperationAdmission liga dueño/clock/PID/retirement y epoch por servicio. La actividad lógica puede existir con cero leases entre sondeos; cada transacción abre StorageReservation1 propia o reutiliza parent normal exacto. Control nunca sirve datos. Epoch viejo, contexto copiado y binding ajeno fallan sticky. Active0 y dueño físico0 no son conteos de agentes. Runtime mantiene los bindings logrados hasta retry/witness/owner0 y no puede mutar a su sucesor tras retired. El target configurado es pin de aplicación, no identidad de dominio compartido.

## T012 — ledger compartido PostgreSQL implementado (2026-11-01)

resource_admission v1 añade cuatro tablas de autoridad (_cao_resource_domains, _cao_resource_domain_history, _cao_resource_members, _cao_resource_member_history) y tres operativas (_cao_resource_usage, _cao_resource_operations, _cao_resource_allocations). DomainSpec define UUID/encarnación estables, kind host/node/storage, unidad bytes/millicores/postgres_session y presupuesto entero; no define cuotas de tasa. Revisiones administrativas e historia son del dueño explícito. La membresía fija binding privado de proceso y su digest; el digest físico es declaración autorizada de despliegue, no descubrimiento.

Operation conserva ID idempotente, instancia/generación/PID/inicio y huella exacta; allocations conserva dominio/encarnación/revisiones de dominio y membresía/cantidad. preparing/active/releasing/uncertain siguen en committed; released retira cantidades originales una vez. Presupuesto reducido conserva deuda y la encarnación no se sustituye silenciosamente. Uso incremental y auditoría MVCC independiente son conceptos distintos; la auditoría no repara. SharedCommitUncertain distingue grant no confirmable después del commit, con identidad/cargo retenidos.

Este modelo está validado como backend T012; no representa wiring T013, recuperación física T014 ni aceptación M2/M3. [Recibo](validation-t012-shared-ledger.json).

## T013 — huella y modo de reserva

SharedResourceCost es un valor inmutable de bytes, millicores y sesiones, enteros no negativos dentro de BIGINT y al menos una dimensión positiva; no contiene IDs de dominio, propietario o permisos. El cableado pendiente deberá convertir la huella local del servidor al valor, sin aceptar la decisión de un modelo. Operations añade membership_sha256 y cost_sha256: ambos null para raw anterior, ambos digests canónicos para mapeado. Se conserva el conjunto completo de miembros del proceso, incluidas unidades de coste0. El índice por instance/generation evita recorrer otras encarnaciones. No existe aún ticket compartido de retiro, cuota de proveedor ni conexión del consumidor.


## Estado posterior del corte T013 (2026-11-01)

Los párrafos anteriores que dicen cableado/ticket pendientes describen checkpoints anteriores. Actualmente hay MetadataCredit persistente por dominio storage e instancia/generación/binding exactos, MetadataCoverage completo, modo metadata_required persistido y DeliveryRetirementHold/SharedDeliveryCoordinator integrados. El runtime de producción puede seleccionarlos explícitamente y activarlos por JWT admin; no se retiran créditos al cerrar un lifetime. Aún faltan cuotas de proveedor, reconstrucción T014 y aceptaciones/configuración durable/SDK/UI. [Validación de integración](validation-t013-shared-retirement.json), [activación productiva](validation-t013-production-activation.json).


## T014 — catálogo de deuda (backend/SPI implementado)

SharedDebtCursor guarda clase metadata_credit/operation y UUID. SharedDebtEntry guarda clase/UUID, instancia/generación, revisión, estado charged o preparing/active/releasing/uncertain y coincidencia exacta de dueño actual. SharedDebtPage conserva tuple de entries y cursor opcional. Son resultados de lectura, nunca capacidades de adopción/liberación; se omiten credenciales/rutas/PID/birth del transporte. La paginación no representa un snapshot durable de toda la red.


## T014 — acción administrativa de preparación implementada

SharedAbortReceipt inmutable contiene action_id/operation_id canónicos, revision2 y state=released; no es capacidad de ejecución. Migración5 añade _cao_resource_admin_actions con actor derivado del Principal, binding privado del custodio, estado/revisión de origen y destino, snapshot por dominio/cantidad/unidad/kind y fecha. Action UUID es PK y (operation_id,from_revision) único; owner escribe, runtime solo lee. Se preservan los checksums1–4 y el conjunto histórico de tablas v3/v4. No hay retiro de MetadataCredit ni prueba de proceso muerto en este modelo. [Validación](validation-t014-preparing-abort.json).


## T013/T014 — propietario físico nativo local

PhysicalEffectReservation es una identidad opaca por lease con referencias fuertes al dueño nativo y observadores de cierre. DeliveryReservation conserva su conjunto hasta confirmación positiva; DeliveryRetirementHold exige que esté vacío además de SQL/stage terminados. El contexto nativo diferencia ciclo observado, gate, intento y cierre, ligado al PID/hilo del worker. Estos objetos locales no son testigos durables ni DTOs de red. El ledger/cargos compartidos permanece en PostgreSQL sin migración nueva en este corte. [Validación](validation-t014-work-physical-ownership.json).


## T014 — cierre durable de operación, esquema6

_cao_resource_operations.closure_witness_revision es nullable: filas históricas quedan NULL. Un productor del coordinador original registra marker=revision al pasar a releasing; al liberar queda marker=revision−1. La restricción excluye uncertain tras el testigo. _cao_resource_witness_releases conserva UUIDacción/operación, actor y binding propio del custodio, revisiones original/final y cantidades originales; actor/allocations/audit se insertan con el CAS de devolución. Es autoridad owner-written/runtime SELECT-only. El espacio lógico de action_id es común con _cao_resource_admin_actions mediante lock y verificación cruzada; el motor solo admite dos políticas internas enumeradas. La tabla previa conserva preparing1→released2, sin ampliar sus constraints ni sus checksums. No hay marcador de cierre de generación ni retiro de créditos en este esquema. [Validación](validation-t014-closure-handoff.json).


## T014.A — registro local de cierres tardíos elegibles

DeliveryAdmission conserva _physical_closes con referencias fuertes a tokens exactos y un guard de retry no bloqueante. Solo physical_effect_scope registra tras prueba positiva original y finish rechazado por helper tardío. finish quita token físico y registro bajo el mismo guard de dueño, después de observar fuera del lock y volver a comprobar identidad. PhysicalCloseResult contiene pending/busy/clean; no es un DTO HTTP ni autoridad de cierre global. El resultado limpio no sustituye inflight ni retira metadatos. Estado local efímero de propiedad; el ledger/cargos durables sigue en PostgreSQL/esquema6 sin migración nueva en este corte. [Validación](validation-t014-native-late-retirement.json).


## T014.C1 — hecho de sello terminal (esquema7)

_cao_resource_generation_seals usa PK(instance_id,process_generation), seal_id único/estable por realm+generación, PID/nacimiento y digest del binding original, reason=terminal y created_at de auditoría. El runtime solo SELECT+INSERT; prohíbe UPDATE/DELETE/privilegios peligrosos y PUBLIC/ACL de columnas, incluso después de grants genéricos. AUTHORITY_TABLES conserva el tuple de versiones históricas; GENERATION_SEAL_TABLES añade el objeto únicamente a v7 y a comprobaciones actuales de dueño/manifest. No cambia créditos, allocations, usage ni estados/revisiones previos. No es testigo de cierre y no se expira por tiempo. La capacidad Python opaca es memoria de autoridad del proceso, no persistencia alternativa a PostgreSQL. [Validación](validation-t014-generation-seal.json).


## T014.C2a — ProcessExitObservation transitoria

Binding original, boot_id, start_time_ticks, pid_namespace, exit_kind (exited/signaled/core_dumped) y exit_status derivados del wait kernel propio; returncode es una proyección, nunca el cero sintetizado de Popen. Capacidad opaca con PID emisor/factory privada, no copiable ni serializable y sin validez tras fork. El supervisor mantiene Popen/pidfd/canal y Thread exacto; no son tablas ni autoridad de devolución. Esquema PostgreSQL permanece7 sin cambios. El futuro hecho durable de cierre necesita más que este proceso y su sello. [Validación](validation-t014-process-exit.json).


## T014.C2b.P1 — atribución SQL

ProcessExitObservation añade sql_application_name canónico y obligatorio; el supervisor lo conserva solo tras ACK del binding protocolo2. Es atribución opaca en memoria, no tabla de inventario ni prueba de ausencia. PostgreSQL permanece esquema7. Fork cambia etiqueta; exec sin un nuevo supervisor no conserva cobertura. [Validación](validation-t014-sql-origin.json).


## T014.C2b.P2a — cierre terminal de nuevas aperturas

El estado efímero por PID conserva enabled/name/sealed/opening/unconfirmed y lock local. sealed es irreversible; opening cuenta llamadas admitidas, unconfirmed retiene fallos/incertidumbre heredada. Ninguno representa inventario durable o ausencia. Fork nuevo usa identidad distinta y conserva cierre/incertidumbre. No hay cambio del esquema7 ni autorización de devolución. [Validación](validation-t014-sql-fence.json).


## T014.C2b.P2b — registro financiado previo a TCP

sql_opening_intents1 añade4tablas independientes: quotas(journal/credit/instance/generation/binding/max_records/actor), channels(journal/binding/backendPID/backend_start), intents(UUID/journal/ordinal/binding/sourcePID/birth/appName/path/destinationhash), fences(journal/binding). Quotas runtimeSELECT, resto append-only. max_records es crecimiento lógico, no bytes/sesiones. Estado local incorpora registration_unconfirmed separado de unconfirmed TCP; ninguno es inventario completo ni autoridad de liberación. [Validación](validation-t014-sql-intents.json).


## T014.C2b.P2c.A — orden del bootstrap supervisado

El bootstrap supervisado se retiene como la misma instancia efímera entre publicación/ACK y runtime_from_environment. No hay nueva fila, token de financiación, migración o autoridad durable. El binding y su generación no se recrean al desaparecer el FD del entorno. [Validación](validation-t014-pre-sql-bootstrap.json).


## T014.C2b.P2c.B — registro explícito previo al acuse

sql_opening_intents2 añade _cao_sql_child_openings append-only: actor padre, binding JSONB/sujeto tipado, etiqueta y boot/ticks/pidns, destino, ordinal compartido y unicidad binding/generación/físico/etiqueta. Quota1 y tablas1 conservadas. VerifiedChildSqlSubject es efímero aprobado/scoped, no serializable; ChildSqlOpeningIntent es solo descriptivo. SqlChildAckUnavailableAfterCommit contiene fila confirmada; SqlIntentCommitUncertain contiene IDs para reconciliar resultado desconocido. Ninguno es presupuesto de sesión ni testigo de ausencia. [Validación](validation-t014-child-sql-intent.json).


## ChildSqlSessionCharge scoped — T014.C2b.P2c.C1

Objeto opaco no serializable: UUID de sesión y ResourceBinding descriptivos, funding original, referencia de canal y préstamos de control/datos exactos. No es fila durable nueva, permiso TCP o testigo de cierre. El sujeto mantiene claim de sesión separado del claim de registro. Reserva/hold originales retienen toda la huella; SharedDeliveryCoordinator confirma lease/hold/estado activo exactos sin SQL. El recibo durable ChildSqlOpeningIntent mantiene su significado anterior. [Validación](validation-t014-child-session-charge.json).


## Permiso estructurado y autoridad efímera — T014.C2b.P2c.C2

SqlStartupPermissionRecord describe session/journal/intent UUID, binding SHA, etiqueta/path/destino con usuario/TLS/scope y slots exactamente int1. Serializar ese registro no instala autoridad. VerifiedSqlStartupPermission se aprueba/reclama/expira en el handshake vivo; el gate conserva solo una apertura con identidad de PID/Thread/task y callback de vista kernel. Estado required/failed/spent es monotónico; descriptor no equivale a aceptación, fila, TCP o ausencia. [Validación](validation-t014-child-sql-permission.json).


## Solicitudes posteriores ancladas — T014.C2b.P2c.C3.A

SQL3 añade _cao_sql_child_followups: intent_id, journal_id, startup_intent_id, ordinal, sequence, path y destino(scope/store/digest). Una FK compuesta conserva el journal del ancla; UNIQUE(startup,sequence) impide repetir posiciones. Binding/kernel del sujeto se leen del arranque inmutable, sin duplicarlos. ChildSqlFollowupIntent es un recibo tipado e inmutable que valida UUID, posiciones, path/scope y hashes; no tiene métodos de grant/cargo/retiro. [Validación](validation-t014-child-followup-intents.json).


## Solicitudes, permisos y asociación de financiación — T014.C2b.P2c.C3.B

SqlBrokerRequest y SqlBrokerPermissionRecord usan versión 4 y kinds distintos, UUID del intento como nonce, ancla/sequence, destino/path/scope, etiqueta y digest del binding. El permiso posterior conserva slots exactamente 1. SqlBrokerIdle.NO_REQUEST es un resultado explícito distinto de None/EOF. SQL4 añade _cao_sql_child_session_fundings: intent_id PK, session_id UNIQUE y exactamente una FK matching hacia startup o followup. No duplica kernel ni crea crédito físico desde datos. El cargo opaco retiene claim de intento único, canal, sujeto y unidad originales; las filas son descriptivas. [Validación](validation-t014-sql-opening-broker.json).


## Sin nuevos modelos de autoridad — T014.C2b.P2c.C3.C

C3.C reutiliza supervisor, permisos/solicitudes v4, cargos C1 y asociaciones SQL4 existentes. No añade esquema, permiso por datos ni crédito genesis. El contexto retiene el supervisor exacto en resource_process_owner ante fallo y no transforma su observación de salida en cierre de sesiones. [Validación](validation-t014-funded-bridge-startup.json).


## Tipos estáticos sin esquema nuevo — T021.T

unique_indexes declara dict[str,set[tuple[str|None,...]]] porque los índices de expresión pueden carecer de nombre de columna. WorkService.artifacts conserva WorkArtifactStore|None del parámetro. Ghost cleanup Herdr declara sets de str y mapa de owners opcionales. El payload JSON peer se trata como object hasta la igualdad exacta. Ninguna anotación crea autoridad, estado persistido o migración. [Validación](validation-t021-typing.json).


### T014.R.G — registro de génesis y certificado de una apertura raíz

`RootSqlOpeningIntent` es dato inmutable: UUID de journal/intent/sesión, ordinal1, binding exacto y digest, etiqueta SQL, boot/ticks/namespace PID, destino native/main, principal y conjunto completo ordenado de créditos metadata con huella de membresía. SQL5 añade fila root y links owner-only; runtime solo SELECT. Reutiliza créditos amount1 storage/postgres_session existentes; no concede otra capacidad ni convierte el crédito en reserva de datos. Comparte UUID, ordinal, fence y cuota lógica con el journal existente, sin fijarlo al backend del issuer.

`ConfirmedRootSqlOpening` es una capacidad opaca, intransferible, ligada al canal/worker original, emitida solo después del commit confirmado y con un solo intento de firma. Leer la fila no reconstruye esa capacidad. `SignedRootSqlOpening` es certificado JSON canónico acotado16KiB, firma Ed25519 y key_id que referencia claves públicas confiables externas; no incluye claves públicas ni privadas. `RootSqlSubject` solo describe una petición.

La instalación exige root pendiente sin SQL previamente admitido y certifica una sola apertura física. Tras el intento, error/cierre/fork no rearman el permiso ni devuelven crédito. La unidad de conexión no fija un número de agentes en la red.


### T014.R.H1 — sesión operativa de control

`FundedControlSession` referencia el Connection/raw/target/loan/funding y worker originales; no es un crédito, permiso de primera conexión ni prueba de activación. Su vida es única y no transferible: NEW → OPEN → CLOSED; BUSY existe solo durante una transacción externa; POISONED es monotónico ante commit incierto o rollback no verificable. Las consultas readonly usan REPEATABLE READ READ ONLY y las mutaciones del ledger conservan READ COMMITTED. Un rechazo anterior a efectos no consume recursos ni altera el contexto válido; una incertidumbre posterior al commit conserva deuda y se propaga como FundedSqlCommitUncertain/SharedCommitUncertain. Cerrar el wrapper no cierra ni libera el canal del dueño. No se recrean wrappers automáticamente para eludir la incertidumbre.


### T014.R.H2.1 — monitor y autoridad originales

H2.1 mantiene el estado del monitor y su sesión en una única vida síncrona. La sesión/permit se fija en construcción; el primer preflight ocurre antes de leer propiedades del owner con locks. No hay transición de start asíncrono ni emisión de tokens administrativos en ese modo. El wrapper/canal siguen siendo propiedad del root original y stop del monitor no certifica cierre del canal. Un rollback del reader solo permite authority_unavailable si la sesión se revalida; una observación fallida del rollback propaga denegación de sesión bloqueada.


### H2.2a — reserva efímera de comando

CommandReservation es una identidad registrada opaca de un owner y una generación, con footprint RAM/CPU, estado reserved→started→released y worker capturado al iniciar. Pending puede cancelarse; started permanece cargado hasta finish del worker. No se persiste en SQLite ni se interpreta como metadato PG: es contabilidad efímera de CPU/RAM, mientras operación/deuda persistente conserva PostgreSQL. Snapshot añade command_inflight/command_started y conserva componentes de datos/storage/control. DiagnosticV2 añade un subobjeto commands; DiagnosticV1 mantiene su forma y rechaza explícitamente lo que no puede representar.

## H2.3 — paquete, prueba verificada y base de arranque

Paquete firmado: certificado SQL + envelope completo/revisión + dos huellas compute positivas, dominio y versión propios. Prueba opaca verificada: PID, sujeto y objetivo, incapaz de copiarse/serializarse o emitirse desde datos sin verificación. Base startup-only: cantidades RAM/CPU medidas frescas y presupuesto firmado, ligada al ledger/generación y a una única reserva; storage continúa desconocido. El cargo comparte los contadores incrementales existentes y se retira mediante el mismo CommandThread físico. No hay schema ni tabla nueva en este corte.


H2.4a: propietario CommandProcess y error de lanzamiento incierto retienen reserva, contexto original y supervisor exacto opcional. La reserva registra handoff exclusivo para impedir finish directo. Estado pendiente puede cancelarse antes del intento; iniciado conserva compromiso hasta observación exit auténtica y cierre. Estado incierto no tiene retry/refund automático; no existe fila/esquema SQL nuevo.


H24b: RootSqlSubject puro con alias de servicio compatible; frames Root inmutables de versión/secuencia/tipo y payload acotado. Endpoint Root privado con contexto y estado protocolar (await_activation/await_ready/active/stopping/failed/closed), separado del binding. RootControlDeployment retiene CommandProcess seguro, subject autenticado y estado de intento/record poscommit; expone dos fases para autoridad ya provisionada. No filas, migraciones ni grants nuevos.


**2026-11-04 · H24b validado como componente interno opt-in.** Root CLI, publisher y transporte privado autenticado conectados a proceso financiado y activación en dos fases. Emisor único sobre su conexión original; provisión externa para subject vivo, gate de reentrada y registros/cargos conservados. 626PASS/10advertencias/39módulos,572IDs previos+54nuevos;3recorridos Root/PG reales. Tipos467, arquitectura5/0,14Python/8fuentes archivo-exactos y PostgreSQL0/0/0. Sin nuevo techo agregado fijo. El primer diagnóstico2fallos/2pases fue un defecto del arnés de préstamo SQL y se conserva fallido. No acepta autoridad productiva/API, shared, custodia, modelos, M1/M2/M3, SDK/UI restante ni los diez gates originales. [Validación](validation-t014-root-control-deployment.json), [composición](composition-t014-root-control-deployment.md).


### Testigo físico de cierre Root

_root_close_uncertain pertenece al supervisor exacto y solo cambia de False a True. Un fallo/poscondición no demostrada de cierre Root lo conserva incluso si se absorbe el error primario o un reintento cierra. CommandProcess lo incorpora como incertidumbre terminal en aborto y retiro. No forma parte del subject ni concede autoridad; no se reinicia al cerrar ni permite refund por ACK/cleanup. El error RPC con cierre confirmado conserva su camino de join.
