# Data model — 041

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
