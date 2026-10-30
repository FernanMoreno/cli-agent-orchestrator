# Data model — 041

## M1: observación y decisión

- AdmissionPolicy: coste de memoria por entrega en bytes, coste CPU en millicores, fracción reservada de memoria/CPU, frescura máxima en segundos. Validación estricta finita y rangos explícitos.
- ResourceHeadroom: par inmutable de límite/headroom de UN dominio, en unidades enteras. Los márgenes se aplican a cada par antes de agregar; los mínimos independientes solo sirven de diagnóstico.
- CapacityObservation: instante monotónico, memoria disponible y límite efectivo en bytes, CPU disponible en millicores, capacidad de almacenamiento disponible en unidades, cobertura completa o desconocida. No contiene credenciales, permisos ni texto del usuario.
- AdmissionDecision: nuevas unidades disponibles bajo esa observación, motivo fijo y valores por recurso. Una decisión no es una concesión de autoridad.
- DeliveryReservation: identificador opaco, receptor/encarnación, estado reserved/started/released, generación del dueño y recursos reclamados. Una reserva iniciada solo se libera al terminar físicamente; una reducción permite deuda visible.

## M2: persistencia PostgreSQL

ResourceDomain identifica host/nodo/almacenamiento/cuota con presupuesto y revisión. ResourceReservation vincula operación, dueño, encarnación, dominios y cantidades. Estados preparing/active/releasing/uncertain/released; transiciones condicionales a revisión e identidad. Los índices y la migración se definen en la tareaM2 contra el registro de migraciones vigente; no se reserva un número de migración prematuramente.

## M3: identidad de asignación

La reserva de colocación apunta al identificador del intento de asignación ordinaria y al trabajador/encarnación preparados. No cambia el dueño de Work ni el destinatario de mensajes existentes. Infraestructura concedida, destino preparado, entrada enviada y resultado confirmado son estados distintos.
