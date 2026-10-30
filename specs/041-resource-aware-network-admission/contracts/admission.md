# Admission contract — 041

## Política local

La función pura recibe cantidades y tiempo explícitos y devuelve una decisión estructurada. Sin contexto de credenciales, llamadas SQL o inferencias de estado de tarea. Una muestra caducada, futura o incompleta deniega nuevas operaciones. Un recurso con capacidad cero es conocido pero saturado, no desconocido.

## Reservas y consumidor

El consumidor no extrae definitivamente un aviso/contexto si no puede reservar capacidad para iniciarlo. La reserva y el receptor se toman de forma indivisible en el dueño local. Distintos caminos comparten ese dueño. Por receptor solo una operación con efecto; la capacidad nueva nunca elimina su exclusión ni altera FIFO. Cancelación del awaiter no libera una operación ya iniciada.

## Persistencia y respuestas

Aceptar un mensaje sigue confirmando su fila durable; no su ejecución física. Un aviso rechazado o descartado en cierre no pierde esa fila. Las filas inciertas no se vuelven pendientes para repetirlas ciegamente. Los registros nuevos de reservas recuperables utilizan PostgreSQL y preservan aislamiento y autoridad.

## Red

Capacidad anunciada no es reserva ni permiso. El destino valida identidad, preparación, compatibilidad y reserva antes del primer efecto. La reserva de infraestructura del broker no basta. Los consumos Work cuentan en dominios compartidos conservando sus propios intentos y permisos.

El sampler captura la generación del dueño ANTES de comenzar I/O y entrega ese mismo token con observe. observed_at y now usan segundos del mismo reloj monotónico de ese proceso; no se comparan relojes de nodos. CPU/memoria disponibles son headroom bruto previo al margen de política; almacenamiento ya excluye su reserva protegida. El dueño carga todas las reservas pendientes contra la capacidad observada y rechaza una muestra repetida o anterior.
