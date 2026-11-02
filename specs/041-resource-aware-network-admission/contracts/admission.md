# Admission contract — 041

## Política local

La función pura recibe cantidades y tiempo explícitos y devuelve una decisión estructurada. Sin contexto de credenciales, llamadas SQL o inferencias de estado de tarea. Una muestra caducada, futura o incompleta deniega nuevas operaciones. Un recurso con capacidad cero es conocido pero saturado, no desconocido.

## Reservas y consumidor

El consumidor no extrae definitivamente un aviso/contexto si no puede reservar capacidad para iniciarlo. La reserva y el receptor se toman de forma indivisible en el dueño local. Distintos caminos comparten ese dueño. Por receptor solo una operación con efecto; la capacidad nueva nunca elimina su exclusión ni altera FIFO. Cancelación del awaiter no libera una operación ya iniciada.

## Persistencia y respuestas

Aceptar un mensaje sigue confirmando su fila durable; no su ejecución física. Un aviso rechazado o descartado en cierre no pierde esa fila. Las filas inciertas no se vuelven pendientes para repetirlas ciegamente. Los registros nuevos de reservas recuperables utilizan PostgreSQL y preservan aislamiento y autoridad.

## Atestación del operador (T007)

El servicio recibe Principal verificado/admin, claims de presupuesto y revisión esperada. Comprueba el Principal antes de resolver el target o sus credenciales; actor, store, proceso/generación, namespaces y vista se derivan en el servidor. El writer requiere credenciales explícitas del dueño y propiedad del esquema/tabla. READ COMMITTED se fija antes de consultar dentro de su transacción; lock y lectura de última revisión son sentencias distintas. Un CAS perdido o repetido no añade historia. Si el commit no puede confirmarse, no se interpreta la excepción o cancelación del caller como rollback; la revisión durable debe consultarse antes de decidir otra escritura.

Revocar un registro existente añade una revisión inactiva. Revocar sin registro con revisión esperada0 lanza EnvelopeNotFound: no confirma un efecto durable ni inventa un tombstone; una revisión esperada no vigente produce EnvelopeRevisionConflict. El lector obtiene siempre la última revisión, verifica rol/esquema en runtime readonly y compara la vinculación antes/después de la lectura. Falla sin reutilizar autoridad previa ante identidad no verificable, revocación, deriva, esquema/rol inválidos o falta de cobertura. Solo devuelve un objeto sellado internamente; una representación JSON, un dict o un objeto sin sello no es atestación válida.

Los claims autorizan una declaración del operador y no prueban ancestros invisibles ni permisos para ejecutar herramientas/tareas. Los módulos T007 solos no añaden rutas HTTP/MCP ni conectan el consumidor. El productor HTTP y la integración con API/Work/Bridge se implementan por composición T009–T011; la aceptación física M1 sigue pendiente en T019. No se añaden permisos MCP por atestar recursos.

El sello y verified_at describen una lectura válida en ese instante monotónico local. El consumidor deberá aplicar frescura, generación y presupuestos antes de reservar; retener el objeto no conserva autoridad indefinidamente.

## Red — contrato requerido; M2/M3 pendientes

Capacidad anunciada no es reserva ni permiso. El destino valida identidad, preparación, compatibilidad y reserva antes del primer efecto. La reserva de infraestructura del broker no basta. Los consumos Work cuentan en dominios compartidos conservando sus propios intentos y permisos.

El sampler captura la generación del dueño ANTES de comenzar I/O y entrega ese mismo token con observe. observed_at y now usan segundos del mismo reloj monotónico de ese proceso; no se comparan relojes de nodos. CPU/memoria disponibles son headroom bruto previo al margen de política; almacenamiento ya excluye su reserva protegida. El dueño carga todas las reservas pendientes contra la capacidad observada y rechaza una muestra repetida o anterior.

## Checkpoint T009 — instancia con dueño explícito

La ruta ligada reserva antes de handoff/SQL; una denegación conserva filas PENDING y avisos con su ordinal/Context/registry. Receiver busy aparca esa cabeza y deja avanzar otros receptores; las notificaciones físicas respetan además la propiedad de la clave por el Future del consumidor. La disponibilidad del ledger no permite sobrescribir ese Future antes de retirarlo. El cursor de barrido se cierra antes de unir hijos; sus errores no omiten el drenaje restante ni sustituyen el primer fallo. Cada hijo iniciado conserva conteo propio aunque falle la publicación de Thread.start.

Los costes configurados estiman una invocación serial completa, no cada fila ni N entregas simultáneas: num_messages=0 conserva el límite histórico100 y un positivo conserva el límite solicitado. La reserva cubre todos los envíos y escrituras del lote; confirmar físicamente la estimación con tamaños fijados sigue siendo un gate de aceptación. No se libera almacenamiento antes de acabar auditorías/recibos/estados. API/Herdr/enrolamiento y notificaciones del monitor están conectados por T009/T010; instancias sin dueño explícito conservan compatibilidad y no acreditan este contrato de admisión M1.

## Diagnóstico local T017 — contrato v1

GET /admission/diagnostics comparte router entre API y listener Bridge. Requiere JWT admin verificado antes de resolver el runtime, devuelve Cache-Control:no-store y503fijo si el runtime no está disponible. El contrato tipado/inmutable declara scope process_local y cobertura distribuida no observada; no inventa agentes, mensajes ni nodos. La operación de lectura no llama _execute/registered/refresh, loader, bootstrap, sampler, SQL ni archivos de política. La autenticación conserva su mecanismo habitual.

El dueño copia bajo su lock política efectiva, presupuesto aplicado, observación, ceiling, decisión y snapshot. Se exponen cantidades de memoria en bytes, CPU en millicores y almacenamiento en sesiones/slots; compromisos/deuda y contadores incrementales separan operaciones, entregas, almacenamiento y préstamos. La capacidad vigente es null si falta autoridad/muestra válida: cero conserva el significado conocido pero agotado. El coste de una operación concreta puede requerir varias sesiones; un escalar genérico no se presenta como agentes disponibles.

El monitor copia bajo lifecycle→owner (orden existente) el último resultado y el último PostgresPressureSample inmutable ya obtenido, con edad/generación propias y política aplicada al probe. Esa telemetría está separada del basis de capacidad actual; una generación vieja o una sonda tardía no autoriza admisión. No se espera refresh_guard. Cambiar/revocar autoridad conserva el ledger iniciado y muestra incertidumbre/knowledge unknown; stop hace indisponible el runtime. No se exponen rutas, identidades de target/rol/store/proceso/namespaces, contenido ni excepciones. Esta ruta local no acepta M1 ni reserva compartidaM2 ni toda US6 de red.

T017 v1 validado: sampling_policy describe la última configuración instalada con intervalo, muestras saludables y política PG. current_generation compara epoch local; basis_current exige además la identidad de la observación aceptada y una base vigente. No se comparan generaciones o relojes entre procesos. applied_budget es null sin base admisible vigente (incluido stale/unknown/stop), no cero inventado. Contadores cubren operaciones admitidas por ese dueño, no todas las conexiones o procesos de la red.

## Retiro de almacenamiento T018 — propietario frente a fachada

El PID del servidor es propietario del registro de cierres ya solicitados para sus targets. Retrying un cierre no abre conexión, no cambia permisos y solo retira tokens tras closed() is True. Connection.borrow no posee raw y close no puede cerrarlo ni registrarlo; Connection dueño conserva un cierre fallido conocido con o sin loan. Tras drenar workers, una pasada unida de cierres y el testigo del sampler preceden al retiro del runtime. In-use, fallo persistente, otro PID y apertura sin handle verificable impiden declarar limpieza; ningún timeout o reconstrucción elimina la incertidumbre.

Componente de retiro validado: un Runtime que falla/cancela stop no se marca retirado por la mera terminación del worker ni por inflight0 si queda un cierre sin préstamo. El sampler nuevo del hijo requiere detach confirmado; una referencia del padre no sirve como sampler de la factoría hija. No hay autoridad por sustituir el objeto ni liberación de tokens padres.

## Control protegido T018 — contrato implementado en monitor/control tras bind

Una activación confiable reserva una sesión para SQL de autoridad/observación. Un permiso por identidad en el dueño común crea una operación de control registrada; no autoriza entregas y no se entrega a perfiles, Inbox ni Work. Una operación o préstamo incierto permanece en el mismo snapshot y bloquea reconstrucción. El presupuesto efectivo normal es S−1; S≥1 permite control y S=1 no admite datos. Los backends del probe ya pertenecen al inventario PG, que no se corrige con A+1. Las cantidades/counters de infraestructura se identifican, sin presentarlas como agentes. El GET diagnóstico sigue sin I/O.

Validado216PASS: authority_change admite el scope físico antes de marcar entrada al callback. Denegación previa prueba cero ejecución y no fija incertidumbre; excepción posterior conserva el latch. Registered serializa con el monitor sin invalidación de escritura. DTO de diagnóstico añade control_storage_slots/control_inflight/control_connection_loans; cantidades de datos/infraestructura se distinguen y GET sigue sin I/O. Arranque y otras entradas SQL no se aceptan por este componente.

## Operaciones SQL normales de interacción — implementación tras bind T018

El crédito protegido no sirve para preguntas/respuestas/ACK ni lecturas AG-UI. Sus transacciones normales necesitan StorageReservation propia o la lease normal exacta del mismo dueño ya activa en el worker actual. Préstamo, apertura, verificación, SQL y retorno pertenecen al contexto corto; la espera entre sondeos no conserva una reserva independiente. El binding permanece cerrado durante drain y cleanup; no puede borrarse ante fallo/cancelación ni sustituir el dueño previo aún vivo. Estas condiciones aún requieren implementación y pruebas, sin afirmar arranque ni dominios compartidos.

Validación539PASS: no existe fallback legacy tras cierre fallido; la siguiente lifetime solo entra tras actividad/propiedad física retiradas. Raw-call retiene actor sellado y mapper fija errores503; stream projection unido/off-loop conserva revocación/fallback. Guardas administrativas se reconocen por función/dependencia/AST efectivos y rechazan wrappers. No se afirma presupuesto de startup ni dominio físicoM2 ni SQL de cada poll fuera del loop.

## T012 — contrato backend compartido validado

ResourceDomainAuthority verifica Principal sellado JWT/admin antes de usar repositorio o binding. Provisión de dominio y membresía requieren credenciales explícitas del dueño; grant_current_member deriva identidad del servidor, no de un payload. Runtime resuelve solo el main canónico configurado. No se añaden endpoints HTTP/MCP ni SDK en este componente.

reserve exige ID estable y conjunto exacto de cantidades/revisiones; reintento idéntico devuelve el estado durable, nunca otro cargo. Las escrituras toman lock de operación y locks de dominio ordenados por clave efectiva normalizada; escritores privilegiados comparten el lock de dominio y CAS. Todas las allocations/counters confirman juntas o ninguna. Error/acuse perdido no autoriza efectos; conservar ID hasta resolver el resultado.

preparing→active revalida autoridad, membresía/revisión/vista original y conocimiento de uso; revocación/cambio/deriva deniega sin liberar. preparing→released es cancelación previa al inicio; active→releasing/uncertain conserva cargo; released requiere transición válida y dueño/generación/revisión exactos. El repositorio registra la confirmación del caller confiable, no observa cierre físico por sí mismo: T013/T014 deben conectar sus testigos antes de usarla para efectos reales.

Auditoría inicial/explícita compara en una única snapshot; divergencia/error conocido bloquea reserve/active y no hace reparación automática. Invalidación antes de commit permite rollback; después de commit es SharedCommitUncertain con cargo retenido. SQL directo hostil bajo las mismas credenciales está fuera del protocolo cooperante, no protegido por una RLS ficticia. [Validación](../validation-t012-shared-ledger.json).

## T013 — preparación por coste (backend validado; consumidores pendientes)

Solo el servidor convierte la huella local en cantidades tipadas. El repositorio obtiene la vinculación de su proveedor privado y resuelve la totalidad de membresías de esa encarnación bajo un lock común con el provisionador. La reserva mapeada conserva un digest de ese conjunto y del coste original. Añadir, retirar, revocar o revisar una membresía antes de active invalida la preparación: se conserva el cargo hasta cancelación CAS exacta. Un ACK repetido no es una autorización adicional de efecto. Las operaciones raw anteriores no se promueven al contrato completo por tener metadatos null.

Para memoria y CPU positivas se exige, por unidad, presencia tanto de host como de nodo; para sesiones positivas, storage. Una dimensión cero no necesita presupuesto propio, pero sus membresías declaradas siguen formando parte de la huella y pueden invalidar active. Estos tipos mínimos no prueban ancestros invisibles. SQL de metadatos, control y escáneres conserva su gate de contabilidad física; el mapeo aislado no cubre todas las conexiones de la red.

## T013 — hold local (contrato en implementación)

La confirmación compartida no puede quitar el receptor local antes del testigo físico. Un hold exacto mantiene la lease hasta finish explícito del dueño confiable, después de resolver la reserva compartida. Ready requiere testigo del ledger, no solo Event.set. Solo ese hold vivo habilita la operación de control de retiro después de stop; no concede operación de datos ni crédito SQL adicional. Su propio préstamo SQL debe cerrar antes de que el hold permita retirar la lease. Incertidumbre conserva ambas propiedades.


## T013 — contrato integrado del checkpoint

La cobertura de metadatos se confirma bajo los mismos locks que la reserva mapeada y se vuelve a comprobar antes de active. El modo metadata_required persiste y no cambia por retry. Un crédito completo es requisito de activación del servidor, sin convertirse en permiso por un DTO. Una nueva membresía storage de una generación ya acreditada necesita enrollment conjunto atómico.

El hold conserva receptor/cargos hasta confirmar released y cierre físico del préstamo de limpieza. Solo entonces se notifica el receptor exacto fuera de guards. La identidad async lógica no autoriza SQL ni promoción física en el mismo hilo; el handoff propio crea el scope físico en otro worker y se une incluso al cancelar. El fallo de entrada difiere antes de efectos, los errores de cuerpo conservan su semántica.

Créditos de metadatos no se retiran al cerrar un runtime. Este checkpoint no define autorización de tasa de proveedor, recuperación entre generaciones ni contratos HTTP/SDK/UI de configuración; siguen pendientes.


## Activación compartida de despliegue T013 (implementada en este corte)

Selección fría CAO_ADMISSION_MODE=local|shared; ausente equivale a local, otro valor es error. Shared requiere política privada configurada y auth, sin inferencia desde perfiles o direcciones. No autoriza dominios/creditos. POST /admission/shared/activate usa DTO estricto {"version":1}, JWT admin y respuesta {"version":1,"activated":true}; sin datos de binding/credenciales. Rechazos de autoridad/capacidad/configuración se expresan con503/resource_control_unavailable/no-store, input inválido422/invalid_resource_envelope. El mismo router sirve API/Bridge. No es aún API de configuración persistida ni SDK/UI T022/T023.


Si una activación ya creó coordinador y falla su bind, se conserva para drain y queda detenido; el operador debe retirar ese lifetime y reconstruirlo. No se abandona la referencia ni se reabre modo local.


## T014 — descubrimiento administrativo de deuda (backend/SPI implementado)

El catálogo lista cargos existentes de todas las encarnaciones del mismo coordinador bajo Principal admin. Incluye créditos tras revocación y operaciones distintas de released; estado conserva lo persistido. Cada página usa keyset tipado y límite de transporte, no un límite de agentes. current_owner compara identidad privada completa y se revalida tras lectura; un cursor nunca concede autoridad. No modifica counters, estados ni crédito y no declara ausencia/muerte por acabar una página. Interfaz inicial de backend/SPI; la ruta HTTP y ejecución de reconciliación siguen pendientes y deben conservar financiación propia.


## T014 — cancelación administrativa de preparación (implementada en este corte)

POST /admission/shared/abort-preparing requiere JWT admin, runtime shared ya activado y sesión propia financiada. DTO estricto {version:1,action_id:UUID,operation_id:UUID,expected_revision:1}; acuse tipado contiene version1/mismosIDs/revision2/state=released. No es cancelación de Work ni prueba de muerte. Solo preparing1 mapeado con metadatos puede abortarse: CAS atómico frente a active, cantidades originales y audit durables; otro estado/raw se rechaza. Reintentar mismo actionID recupera el acuse sin nueva devolución. Conflicto409 fijo; indisponibilidad/cierre incierto503 sin eco de excepciones. No liberar metadatos, adoptar efectos activos o reejecutar jobs históricos.


Descubrimiento del objetivo: POST /admission/shared/debt bajo JWT admin y control propio activado recibe version1, limit opcional1..1000, after opcional {kind,entry_id}. Devuelve version1, tuple de cabeceras sin PID/birth/digest y next_cursor; ending solo termina un barrido. No usa sesión gratuita antes de shared_ready ni concede autorización desde un cursor.


El runtime deriva expected_binding de su concesión opaca y lo contrasta antes del primer SQL, durante la transacción y después. Nunca es input HTTP. El cliente persiste action_id antes del intento y lo reutiliza ante503/cancelación o confirmación perdida; ausencia del objetivo en una página no demuestra rollback. Conflicto409 no autoriza otro efecto.


Frontera Work reproducida antes de la corrección: final de callback/hilo no equivale al cierre de su proceso/contenedor. La reserva conserva costes y exclusión mientras quede un efecto externo incierto. Solo no-arranque probado o testigo positivo de cierre del dueño nativo permite su retiro; SQL y efecto externo tienen propietarios distintos. Un futuro testigo durable debe cubrir ambos.


Frontera Work corregida en este corte: la reserva conserva un PhysicalEffectReservation por dueño nativo, con handles/observadores exactos además de préstamos SQL. No-start observado o cierre confirmado permite retirarlo; error, timeout y not-alive de un hilo no arrancado no lo permiten. El token no es serializable ni permiso de replay y no se transmite a clientes. Los cargos durables siguen en PostgreSQL; todavía no existe testigo SQL de cierre para un custodio de otra generación.


## T014 — liberación con testigo durable (componente implementado y validado)

Solo el coordinador con hold exacto ya cerrado puede producir el marcador privado de cierre en la transición a releasing. Un custodio diferente debe poseer su propia cobertura/control y JWT admin; expected_binding corresponde al custodio, no al objetivo. POST /admission/shared/release-witnessed usa UUID de acción estable, operación y revisión esperada>=3. CAS libera únicamente cantidades originales de esa operación y registra actor/resultado una vez. Sin testigo válido se conserva toda carga; no retirar metadatos ni convertir el acuse en permiso de ejecución o replay. El aborto previo conserva DTO/error/semántica existentes.


El endpoint exige version1 y expected_revision entero estricto3..2^63−2, sin actor/binding/testigo suministrado por el cliente. Devuelve version1/mismosUUID/state=released/revision=esperada+1. Sin marcador409/resource_closure_unproven; conflicto de revisión/estado/acción409/resource_operation_conflict; indisponibilidad o ACK incierto503/resource_control_unavailable, sin eco de errores y no-store. Reutilizar exactamente action_id/operation_id/revisión tras respuesta incierta; una nueva acción no es un reintento idempotente. El catálogo no incorpora un campo de elegibilidad y releasing por sí solo no demuestra testigo. Preparación conserva su contrato previo. [Evidencia del componente](../validation-t014-closure-handoff.json).


T014.A (componente validado): el registro privado de cierres tardíos solo acepta un token desde el scope original después de confirmación nativa completa. Reintentos exactos no conceden autoridad desde TTL/PID/Popen/owners vacíos y no amplían HTTP/MCP. El cargo del helper dura hasta su terminación positiva; ningún retry ejecuta Work ni resuelve su estado de negocio. Filtrar por dueño local y mantener registro/cargo tras cualquier incertidumbre. La retirada física confirmada permite al coordinador producir el marcador durable existente.


PhysicalCloseResult.clean acredita solo el registro de cierres ya elegibles del dueño, no todos los recursos físicos: los nativos sin prueba ni registro siguen en el ledger y snapshot.inflight impide retirar runtime. Observers deben ser no bloqueantes y no ejecutar SQL ni Work. Un error de reader previo al cleanup puede dejar el negocio reconcile aunque el recurso se retire más tarde; este acuse local no cambia ese estado. [Validación T014.A](../validation-t014-native-late-retirement.json).


T014.B1 (en diagnóstico): no equiparar process=None o falta de CompletedProcess a no-arranque probado. Un acuse perdido de helper requiere mantener su obligación física aunque Docker confirme contenedor/imagen ausentes. El registro tardío positivo no autoriza clearing de esa incertidumbre ni una reejecución de Work. Reproducción real propia antes de cambiar comportamiento.


B1 validado por componente: el marcador de arranque no observado solo añade deuda al token exacto/PID; no autoriza efectos ni confirma cierre desde otro hilo. La comprobación bajo el guard común impide adelantar confirmación a la actualización del marcador. No hay clear por TTL/ausencia de artifacts; Bubblewrap preserva snapshot y cleanup_confirmed=False sin handle. Fallos opacos de exec pueden retener deuda aun si stdlib reaped internamente: falta prueba positiva original. [Validación](../validation-t014-native-start-ack.json).


T014.C1 (componente validado): generación sellada impide nuevos productores con lock común y conserva acknowledgements idempotentes ya aceptados, lecturas y cierres. Capacidad interna no transferible de binding exacto, solo ciclo terminal; no DTO cliente ni autoridad para liberar recursos. El sello append-only no cambia uso/cantidades/revisiones de créditos, no acredita muerte ni cierre SQL, y API lifespan ordinario sigue reutilizable.


El sello es un hecho negativo append-only de servidores cooperantes confiables, no una firma criptográfica ni RLS: el permiso SQL INSERT puede escribir la fila y la capacidad opaca se valida en el servidor Python. No concede autoridad de cierre físico ni devolución. Solo Bridge opta hoy por ciclo terminal; pérdida del control o coordinador ausente no producen sello, y los créditos permanecen. El último SQL usa el permiso de control original antes de cerrar owner/participantes. [Validación](../validation-t014-generation-seal.json).


## T014.C2a — observación de salida, sin autoridad de devolución

El supervisor común posee su Popen y descriptor kernel; el binding completo llega por canal heredado con SCM_CREDENTIALS comprobado y esquema JSON cerrado. El padre fija realm/config, nacimiento/boot/ticks/pidns/UID/vista antes del ACK. waitid(P_PIDFD) aporta estado real estructurado y el mismo descriptor debe notificar salida; ECHILD, timeout, PID ausente, canal cerrado o sello C1 no bastan. Las señales usan pidfd; los FD inesperados SCM_RIGHTS se cierran incluso en rechazo/truncación y no se heredan por exec. Excepción de arranque con handle retenido y SIGTERM/SIGHUP/SIGINT conservan unión física antes de propagar el primer error.

ProcessExitObservation solo vive en memoria opaca/no transferible; no es un DTO autorizador ni persistencia alternativa a PostgreSQL. Los UUID del hijo son descriptivos hasta contrastar binding original/sello y cierre completo mediante custodio propio financiado y auditado. Un descendiente puede seguir vivo después del testigo del líder. Sin devolución, replay, endpoint, migración ni adopción de despliegues externos. [Validación](../validation-t014-process-exit.json).


## T014.C2b.P1 — atribución SQL

El canal heredado exige protocolo2 con version/binding/kernel/sql_application_name exactos y etiqueta UUIDv4 canónica. Protocolo1, falta/invalidación de etiqueta o emisor ajeno rechazan. Ningún consumidor usa esta etiqueta mutable como autoridad de ausencia o devolución. C1 sigue permitiendo SQL financiado de cierre/control: el fence físico terminal y los intents durables previos a TCP son contratos posteriores. [Validación](../validation-t014-sql-origin.json).


## T014.C2b.P2a — cierre terminal de nuevas aperturas

En procesos supervisados, admisión de apertura física y sellado local se serializan antes del driver. Una llamada ya admitida puede abrir después y sigue contada hasta volver. El main terminal sella después de limpieza financiada; C1 no activa este cierre. Sesiones existentes conservan SQL/reuso/cierre. Error, contador0 o seal no autorizan metadatos. El siguiente protocolo debe financiar y registrar durablemente los intents antes de TCP. [Validación](../validation-t014-sql-fence.json).


## T014.C2b.P2b — registro financiado previo a TCP

SqlIntentRepository recibe canal financiado original, no crea conexión. provision separa actor actual de participant_binding/credit original; record confirma commit antes de permitir driver. Identidad duplicada no concede segundo TCP; pérdida de acuse conserva UUID y bloquea efecto. Canal writer queda fijado al primer backend confirmado. read/fence de otro canal ya financiado admiten solo binding actual idéntico y nunca conceden TCP. C1 permite completion financiada; fence de journal y fence físico son separados. Bootstrap/broker/global/custodio siguen pendientes. [Validación](../validation-t014-sql-intents.json).


## T014.C2b.P2c.A — orden del bootstrap supervisado

Bridge supervisado exige publicación/ACK antes de init_runtime_db, limpieza/restauración y backend. Error de publicación bloquea esa frontera. El ACK acredita identidad; no promete admisión activa, presupuesto, genesis o ausencia. La validación de políticas compartidas sigue posterior y fuera del componente; no usar esta corrección como autorización para abrir sin financiación completa. [Validación](../validation-t014-pre-sql-bootstrap.json).


## T014.C2b.P2c.B — registro explícito previo al acuse

receive_binding_with_child_intent requiere repo nativo y Connection original ya financiada del padre; autentica hijo, registra/commitea, revalida y acusa identidad. No-hook/protocolo2 conserva identidad solamente. Error posterior a commit no pierde recibo; ACK/TCP puede haber ocurrido, así que jamás autoriza retry o liberar crédito. DDL2 por dueño es prerrequisito antes de runtime; no hay fallback DDL/TCP ni quota automática del hijo. Los emisores privados son código confiable, no permisos concedidos por campos del modelo. [Validación](../validation-t014-child-sql-intent.json).


## Cargo opcional antes del ACK de identidad — T014.C2b.P2c.C1

receive_binding_with_child_intent(session_reservation=lease) requiere reserva normal exacta, iniciada, con hold previo y misma raíz que el canal financiado original; en modo compartido, preparación activa ya confirmada. Consume un préstamo prepagado sin ampliar costes ni usar el crédito de control. child_sql_session_charge conserva cargo y las excepciones lo adjuntan incluso al envolver un commit confirmado. No-hook/sin session_reservation conserva el contrato anterior; b1 sigue identidad solamente. Toda la reserva queda retenida hasta custodia, sin refund ni retry automático. [Validación](../validation-t014-child-session-charge.json).


## Protocolo3 explícito de una apertura — T014.C2b.P2c.C2

launch(first_sql_grant=True), publish_resource_binding(first_sql_grant=True) y receive_binding_with_child_grant(session_reservation=lease) componen permiso pagado; modo mismatched falla antes de cargo/SQL. Sesión original preparada y retenida más commit B confirmado preceden al paquete. Valida esquema exacto/duplicados/tamaño/FDs/SCM emisor original, destino y worker; consume una vez antes del driver sin refund. b1/protocolo2 siguen identidad. Devolver ChildSqlOpeningIntent tras emitir no prueba aceptación del hijo ni apertura. [Validación](../validation-t014-child-sql-permission.json).


## Registro explícito de follow-ups — T014.C2b.P2c.C3.A

record_child_followup exige canal original financiado, ancla del mismo journal/actor y VerifiedChildSqlSubject todavía vivo con identidad kernel exacta. sequence debe ser el siguiente entero positivo; intent_id no puede existir en ninguno de los tres tipos. Se confirma la fila antes de devolver. read_child_followup es descriptivo; reescribir el UUID falla. SqlIntentCommitUncertain conserva IDs y SqlFollowupAckUnavailableAfterCommit conserva recibo confirmado. El registro no consume claim/claim_session ni concede permiso. Path/usuario/TLS/scope pueden variar dentro del target del binding; la autorización operativa pertenece al futuro broker. [Validación](../validation-t014-child-followup-intents.json).


## Broker opt-in y resultado de una solicitud — T014.C2b.P2c.C3.B

launch(sql_broker=True, first_sql_grant=True) crea un FD reservado distinto del de startup. El publisher explícito v3 instala el cliente mientras el issuer está aprobado. receive_binding_with_child_grant conserva canal de control y recibo inicial; en modo broker registra también la asociación del cargo. serve_child_sql_request procesa exactamente una solicitud en el owner/canal/task originales y devuelve recibo confirmado, SqlBrokerIdle.NO_REQUEST sin efecto o None por EOF de transporte. Cargo, plan y asociación se confirman antes de respuesta; emisor SCM y kernel originales se revalidan por mensaje. Nunca se transfiere Connection/funding a otro executor. Los permisos2/3 y startup_spent no se reinterpretan; no hay refund ni retry automático. [Validación](../validation-t014-sql-opening-broker.json).


## Contexto de arranque Bridge — T014.C2b.P2c.C3.C

launch_bridge(sql_broker=True) conserva argv fijo y solicita primer permiso más broker; el modo legado conserva kwargs previos. funded_bridge exige Connection/repository exactos, UUIDs canónicos, destino main del proceso y reserva no nula; comprueba worker financiado antes de Popen. Recibe el primer grant antes de yield. El scheduler llama a serve_child_sql_request en el mismo worker mientras permanece abierto el scope original. Una salida normal sin observación se rechaza sin descartar handle; fallo primario se conserva aunque falle cleanup. El env del hijo debe preservar destino; una desviación de realm se rechaza en el handshake; rol/TLS/path incompatibles se rechazan por el permiso antes del driver. [Validación](../validation-t014-funded-bridge-startup.json).


## Contratos runtime preservados — T021.T

Un pane previo inexistente se descarta antes de operar sobre mapas con claves str; registro inicial, reemplazo y owner exacto mantienen comportamiento. La clasificación peer solo acepta HTTP404 con el objeto task_not_found exacto; campos extra, JSON inválido, timeout, 403/503 y otros 404 conservan la semántica de outage/reconcile sin modificar recibos históricos. Esta corrección no cambia admisión o permisos. [Validación](../validation-t021-typing.json).


### T014.R.G — helpers internos de control raíz

- `current_root_sql_subject(bootstrap) -> RootSqlSubject`: captura dato real del proceso, requiere modo root pendiente; no abre SQL ni concede autoridad.
- `issue_root_sql_certificate(bootstrap, channel, *, principal, journal_id, intent_id, session_id, subject, key_supplier)`: verifica administrador primero, usa el canal propietario ya financiado para SQL5, obtiene la clave de composición confiable después del commit y consume la capacidad de firma. `RootCertificateUnavailableAfterCommit` conserva record/journal/intent descriptivos, sin rearmado.
- `root_sql_control_channel(bootstrap, owner, control, certificate, *, trusted_keys, clock)`: verifica firma y proceso actual antes de instalar grant, abre una conexión con el control original y lee el registro en ese mismo canal antes del yield. PID/thread/task y scope permanecen originales hasta el cierre. Nunca adopta un handle ajeno ni abre un segundo canal.
- `CAO_INTERNAL_ROOT_SQL_GENESIS=1` es marcador reservado estricto opt-in, no permiso. Combinación con grant hijo/broker, SQL previo o rearmado se rechaza. Un canal de identidad sin grant SQL puede preceder al certificado.
- El rechazo genérico pre-subject conserva `failure_stage`/`failure_type` allowlisted; la nota opcional usa `caos.resource-binding-rejection.v1` y solo schema y esos dos códigos. No transporta payloads, IDs, secretos ni texto del fallo original.

No se añade endpoint HTTP ni contrato externo SDK en este corte. La integración CLI/emisor productivo y la activación compartida mediante actor/canal persistente siguen pendientes.
