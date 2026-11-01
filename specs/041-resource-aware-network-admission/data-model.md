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
