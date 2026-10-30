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

Los claims autorizan una declaración del operador y no prueban ancestros invisibles ni permisos para ejecutar herramientas/tareas. Estos módulos no añaden rutas HTTP/MCP ni conectan el consumidor; el productor operativo y la integración M1 permanecen pendientes.

El sello y verified_at describen una lectura válida en ese instante monotónico local. El consumidor deberá aplicar frescura, generación y presupuestos antes de reservar; retener el objeto no conserva autoridad indefinidamente.

## Red

Capacidad anunciada no es reserva ni permiso. El destino valida identidad, preparación, compatibilidad y reserva antes del primer efecto. La reserva de infraestructura del broker no basta. Los consumos Work cuentan en dominios compartidos conservando sus propios intentos y permisos.

El sampler captura la generación del dueño ANTES de comenzar I/O y entrega ese mismo token con observe. observed_at y now usan segundos del mismo reloj monotónico de ese proceso; no se comparan relojes de nodos. CPU/memoria disponibles son headroom bruto previo al margen de política; almacenamiento ya excluye su reserva protegida. El dueño carga todas las reservas pendientes contra la capacidad observada y rechaza una muestra repetida o anterior.

## Checkpoint T009 — instancia con dueño explícito

La ruta ligada reserva antes de handoff/SQL; una denegación conserva filas PENDING y avisos con su ordinal/Context/registry. Receiver busy aparca esa cabeza y deja avanzar otros receptores; las notificaciones físicas respetan además la propiedad de la clave por el Future del consumidor. La disponibilidad del ledger no permite sobrescribir ese Future antes de retirarlo. El cursor de barrido se cierra antes de unir hijos; sus errores no omiten el drenaje restante ni sustituyen el primer fallo. Cada hijo iniciado conserva conteo propio aunque falle la publicación de Thread.start.

Los costes configurados estiman una invocación serial completa, no cada fila ni N entregas simultáneas: num_messages=0 conserva el límite histórico100 y un positivo conserva el límite solicitado. La reserva cubre todos los envíos y escrituras del lote; confirmar físicamente la estimación con tamaños fijados sigue siendo un gate de aceptación. No se libera almacenamiento antes de acabar auditorías/recibos/estados. API/Herdr/enrolamiento y notificaciones del monitor siguen pendientes; instancias sin dueño explícito no acreditan este contrato de admisión M1.
