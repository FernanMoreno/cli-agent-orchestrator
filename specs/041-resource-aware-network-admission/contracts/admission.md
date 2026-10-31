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
