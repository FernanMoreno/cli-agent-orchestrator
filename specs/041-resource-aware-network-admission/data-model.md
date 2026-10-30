# Data model — 041

## M1: observación y decisión

- AdmissionPolicy: coste de memoria por entrega en bytes, coste CPU en millicores, fracción reservada de memoria/CPU, frescura máxima en segundos. Validación estricta finita y rangos explícitos.
- ResourceHeadroom: par inmutable de límite/headroom de UN dominio, en unidades enteras. Los márgenes se aplican a cada par antes de agregar; los mínimos independientes solo sirven de diagnóstico.
- CapacityObservation: instante monotónico, memoria disponible y límite efectivo en bytes, CPU disponible en millicores, capacidad de almacenamiento disponible en unidades, cobertura completa o desconocida. No contiene credenciales, permisos ni texto del usuario.
- AdmissionDecision: nuevas unidades disponibles bajo esa observación, motivo fijo y valores por recurso. Una decisión no es una concesión de autoridad.
- DeliveryReservation: identificador opaco, receptor/encarnación, estado reserved/started/released, generación del dueño y recursos reclamados. Una reserva iniciada solo se libera al terminar físicamente; una reducción permite deuda visible.

## Prerrequisito M1: atestación del operador (T007)

El componente main `resource_envelopes`, versión1, registra `_cao_resource_envelopes`: historial por instance_id/revisión, store UUID e identidad lógica del target, generación/PID/inicio del proceso, pares dispositivo/inodo de namespaces mnt/cgroup y digest de la vista de recursos. Incluye presupuestos positivos y finitos de memoria (bytes), CPU (millicores) y almacenamiento (slots), las declaraciones complete_hierarchy/root_is_host, estado activo/revocado y actor Principal verificado. No almacena rutas privadas ni credenciales. Una raíz host requiere jerarquía declarada completa.

El runtime solo puede leer; cada migración reaplica esa protección tras sus grants genéricos y verifica permisos efectivos. Registrar una fila requiere autoridad administrativa y CAS; el loader valida la última revisión y compara identidad, generación, namespaces y vista antes de devolver atestación opaca. La declaración del operador no demuestra por sí sola ancestros invisibles. Este historial no sustituye reservas de recursos ni acredita M1: faltan ciclo de observación/admisión, productor operativo e integración/aceptación.

## M2: persistencia PostgreSQL

ResourceDomain identifica host/nodo/almacenamiento/cuota con presupuesto y revisión. ResourceReservation vincula operación, dueño, encarnación, dominios y cantidades. Estados preparing/active/releasing/uncertain/released; transiciones condicionales a revisión e identidad. Los índices y la migración se definen en la tareaM2 contra el registro de migraciones vigente; no se reserva un número de migración prematuramente.

## M3: identidad de asignación

La reserva de colocación apunta al identificador del intento de asignación ordinaria y al trabajador/encarnación preparados. No cambia el dueño de Work ni el destinatario de mensajes existentes. Infraestructura concedida, destino preparado, entrada enviada y resultado confirmado son estados distintos.
