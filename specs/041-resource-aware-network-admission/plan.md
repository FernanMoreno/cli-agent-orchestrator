# Admisión dinámica y capacidad distribuida — Implementation Plan

**Branch**: `main` | **Date**: 2026-10-30 | **Spec**: [spec.md](spec.md)

**Goal**: Sustituir techos locales de despacho por reservas según recursos, coordinar dominios compartidos y distribuir encargos nuevos sin alterar autoridad, orden o recuperación.

**Architecture**: Separar política pura, observación de recursos y dueño de reservas. M1 aplica la misma admisión a los caminos locales conservando exclusión por receptor y contabilización hasta el fin físico. M2 añade reservas PostgreSQL por dominio y encarnación; M3 conecta ese contrato a asignaciones elásticas ordinarias nuevas, sin migrar terminales ni desviar Work.

**Tech Stack**: Python del proyecto, asyncio y threading existentes, PostgreSQL mediante sus adaptadores actuales, pytest y herramientas de composición actuales. Sin dependencias globales ni SQLite.

## Global Constraints

- Preservar deadlines, auditorías, permisos, orden por receptor, Context/registry de cada aviso y resultados inciertos.
- Main es la única rama de integración. No tocar los doce artefactos Graphify preexistentes ni credenciales.
- Una cancelación de awaiter no acredita fin de hilo/proceso/conexión. Reservas activas o inciertas siguen contadas.
- M1 no completa M2/M3; una configuración variable o una prueba con20/32 agentes no acredita crecimiento ilimitado.
- Los tamaños de prueba y los presupuestos de máquina no son techos globales del producto.

## Technical Context

Servicio API y proveedores locales/remotos existentes. InboxService mantiene hoy un consumidor con4slots y dos barridos con ejecutores propios; las llamadas directas son otra entrada. Un budget por cada entrada duplicaría capacidad. El pool auditado tiene su límite físico4 y checkout2s independientes: no se eleva para esconder presión. No existe un anuncio genérico de capacidad ni una reserva multinodo reutilizable.

Los datos durables y la identidad de efectos siguen en PostgreSQL. Las observaciones locales son transitorias y jamás autorizan ni completan trabajo. Todo estado futuro de reserva recuperable M2 debe persistirse allí.

## Constitution Check

Inspección git y conocimiento: completados. Spec aprobada por el usuario; planificación y tareas antes de código. Graphify histórico consultado, relaciones contrastadas con fuente; se conserva sin regenerar los artefactos ajenos. Cada cambio usa RED/GREEN, comprobaciones de composición, revisión y evidencia de alcance. Sin excepciones a autorización ni modificaciones de credenciales.

## Project Structure

- `services/admission_policy.py`: modelos inmutables y decisión pura de capacidad; no I/O ni autoridad.
- `services/admission_postgres.py`: presión agregada de un servidor consultado con rol runtime/readonly; una sonda viva y retiro confirmado, sin identidades ni errores privados en resultados. No sustituye reservas ni un mapeo autorizado de dominios.
- `services/admission_settings.py`: política operativa privada y validada, próxima lectura y retención de última válida en el mismo ámbito; no usa el escritor genérico de ConfigService ni contiene atestaciones de jerarquía.
- `services/admission_bootstrap.py`: verifica envelope explícito de operador persistido en PostgreSQL contra identidad/encarnación/namespaces del proceso; solo devuelve un objeto verificado al observador.
- `clients/admission_bootstrap_schema.py`: componente main de historial de atestaciones; runtime solo lectura, con protección central de ACL después de cada migración y del reinicio de esquemas de prueba. El esquema no acredita al actor ni concede capacidad.
- `models/resource_envelope.py`: contratos inmutables de presupuestos y vinculación, sin I/O ni dependencias de infraestructura.
- `clients/admission_bootstrap_repository.py`: lectura runtime con verificación fresca y escritura del dueño con revisión CAS e historial; no promueve credenciales implícitamente.
- `services/admission_resources.py`: observaciones de host/cgroup y presión PostgreSQL con antigüedad y fallos explícitos.
- `services/delivery_admission.py`: dueño común de reservas, drenaje, exclusión y estadísticas estructuradas locales.
- `services/inbox_service.py`: consumidor, barridos y llamadas directas usan ese dueño; no generan capacidad adicional.
- `clients/admission_repository.py` y migración PostgreSQL: reservas y dominios compartidos M2, independientes del dueño de Work.
- `services/elastic_assignment_service.py`: preparación/reserva confirmadas antes del primer efecto de colocación M3.
- `test/services/test_admission_policy.py`, `test/services/test_delivery_admission.py`, `test/services/test_admission_resources.py`: controles puros, físicos y de cancelación.
- Pruebas de integración en `test/integration/test_inbox_resource_admission.py`, `test/postgres/test_admission_reservations.py`, `test/integration/test_elastic_capacity_admission.py`.

## Milestones and Interfaces

### M1 — Política y dueño local

`AdmissionPolicy` contiene costes estimados por entrega, margen de memoria/CPU/almacenamiento, frescura y estabilización. `CapacityObservation` expresa unidades disponibles con instante monotónico y cobertura. `evaluate_capacity(policy, observation, now)` devuelve `AdmissionDecision(available_slots, reason, constraints)` sin efectos. Se rechazan bools, NaN, infinitos, cantidades negativas y muestras del futuro/caducadas. Desconocido significa admisión cerrada, no capacidad infinita.

La decisión expresa nuevas unidades disponibles en una observación; el dueño no puede volver a gastar una misma observación al cambiar el número de reservas. El dueño local reserva antes de enviar trabajo a un executor; cada reserva diferencia reservada, iniciada y liberada. Solo una cancelación antes de iniciar puede liberar la reserva sin fin físico. Se permiten disminuciones bajo consumo activo con deuda explícita; no nuevas admisiones hasta recuperar margen. Reservas por receptor son únicas. La política aplica a todo el conjunto de entradas, no uno por consumidor.

Las medidas de CPU deben considerar afinidad/cuotas efectivas y presión; memoria debe considerar host y límites efectivos de cgroup, sin duplicar un dominio. El muestreo de PostgreSQL no consulta contenido de mensajes ni omite auditoría de operaciones funcionales. La instrumentación tiene su propio tiempo/acceso acotados y no se ejecuta por mensaje.

T008 conserva una huella inmutable por reserva y totales incrementales de bytes, millicores y unidades de almacenamiento. Cambiar los costes no revaloriza el trabajo activo: se restan sus cantidades originales antes de convertir el headroom restante a unidades del coste nuevo. El presupuesto del operador limita las cantidades comprometidas, además de los márgenes físicos observados. Finalizar una etapa de almacenamiento permite liberar solo ese componente mientras memoria/CPU y exclusión del receptor permanecen hasta el fin físico. La deuda se expresa también por recurso; un total de operaciones activas puede superar las unidades de arranque disponibles sin deuda cuando varias terminaron ya su etapa de almacenamiento. No se recorre el conjunto de leases por cada admisión.

### Prerrequisito de confianza para M1

La investigación no encontró una atestación de recursos reutilizable. La presencia peer y el UUID de host por usuario solo sirven como identidad lógica. Se requiere un envelope explícito y finito del operador, persistido en PostgreSQL y ligado por el servidor al proceso/encarnación y a los namespaces de montaje/cgroup actuales. Una declaración de presupuesto no demuestra jerarquía completa; esa afirmación adicional requiere un Principal admin verificado y ámbito explícito. No se aceptará de settings, variables de entorno, perfiles o solicitudes ordinarias. El loader devolverá un objeto verificado; ausencia, revocación, cambio de namespaces/generación o imposibilidad de verificar conserva cobertura desconocida.

Antes de conectar M1 deben existir este loader y sus controles de autorización/persistencia. No se activarán los flags del observador con True para obtener pruebas verdes. Los valores de política de consumo son configuración, separados de la autoridad de atestación.

El Principal acredita quién autorizó una atestación del operador, no la verdad de ancestros invisibles. La cobertura declarada requiere contraste independiente con el host/orquestador. El servidor verifica su vinculación a generación, namespaces y digest de pertenencia/vista de montaje; cualquier deriva invalida esa vinculación. Un escritor administrativo con credenciales de migración protege frente a DML del rol runtime, pero no frente a un proceso comprometido que posea esas mismas credenciales; la separación fuerte exige mantenerlas fuera del runtime.

El servicio T007 comprobará Principal sellado y scope admin antes de resolver el target o sus credenciales. La solicitud aporta únicamente claims/revisión esperada; proceso, store, namespaces y vista se derivan en el servidor. La escritura CAS serializa por instancia dentro de una transacción READ COMMITTED explícita para observar el último commit después del lock, incluso si el servidor tiene otro aislamiento por defecto. Revocar añade un estado inactivo al historial; el lector consulta la última revisión, sin rescatar una atestación antigua activa. Solo una lectura runtime fresca con esquema/rol válidos y vinculación actual exacta devuelve un resultado opaco verificado. No se añade aquí una ruta HTTP ni una herramienta MCP de administración.

### M2 — Dominios compartidos

Cada dominio tiene identidad, encarnación, presupuesto y revisión. La reserva atómica conjunta incluye consumos de Work cuando comparten recursos, sin cambiar su dueño. Cierres inciertos y nodos perdidos no liberan por mera expiración. Una generación antigua no puede liberar reservas nuevas. Competencia entre dos procesos y fallos en adquisición/cierre son controles nativos obligatorios.

### M3 — Colocación autorizada

El dueño del intento durable de asignación elástica ordinaria selecciona un destino compatible con observación fresca y reserva confirmada. Infraestructura creada no es trabajador listo. Se comprueban preparación, identidad y autoridad antes del primer envío; incertidumbre posterior usa el contrato de conciliación existente. Work y terminales ya ubicados no se redirigen por esa ruta.

## Validation Manifest

Antes de aceptación real se congela un manifiesto con memoria en bytes, CPU en unidades normalizadas, presión de almacenamiento y plazos en segundos. Para controles de política iniciales: muestra máxima3s, reserva de memoria20%, coste estimado64MiB por entrega y250millicores por entrega. Son estimaciones de consumo configurables que requieren contraste físico; no constantes de concurrencia. Una muestra aporta unidades de almacenamiento disponibles, no permisos ni una ampliación del pool auditado.

Se verifica crecimiento por cambios de recursos y política a igual código; caída inmediata de admisión ante presión/desconocido, recuperación estable y deuda activa. Los controles usan reloj/observaciones deterministas, no sleeps. Los valores finales de muestreo y aceptación física se congelan en su propio manifiesto antes de ejecutarlos; sin él no se marca aceptación.

M1 requiere regresiones de orden/contexto/sink/shutdown/barridos/API, observación de recursos y un recorrido real con presupuestos contrastados. M2 exige PostgreSQL real y dos coordinadores. M3 exige nodos de ejecución reales y un resultado correlacionado. Se conservan las muestras2/4/8/12/20/32 y fallos previos; no repetirlas sin cambio relevante.

## Execution

Seguir [tasks.md](tasks.md). Root es único escritor; investigadores/revisores Codex solo lectura. Pruebas nativas serializadas bajo dueño PostgreSQL verificado y memoria mínima6144MiB. Ejecutar composición después de pruebas y revisar diff; registrar conclusiones durables sin duplicar esta spec en el vault.

## T008 implementado — ciclo local

AdmissionMonitor mantiene un único sampler PostgreSQL, recargado bajo su lock sin olvidar cierres inciertos. Carga política y atestación antes del muestreo y revalida la misma revisión/vinculación después. Rechaza componentes futuros antes de tomar el timestamp mínimo, que incluye el inicio del intervalo CPU. Tras huecos reinicia ese historial. La recuperación actúa sobre cantidades físicas por recurso, con tres muestras sanas distintas por defecto; terminar una etapa libera su cantidad sin esperar histéresis. Una reducción retira primero los handoffs pendientes más recientes que ya no caben, sin recorrer ni retirar trabajo iniciado.

El ciclo captura generación antes del I/O y publica por CAS; un ticket de lifecycle capturado antes del lock impide reanudar sondas antiguas tras stop/start. El cierre conserva referencias físicas bajo cancelación, une sondas fallidas y llamadas síncronas activas, limpia solo con testigos de retiro y propaga el error original. El componente no crea autoridad operativa ni conecta Inbox: ambos son prerrequisitos explícitos de T009 y de la aceptación M1.

## T009 — integración por fronteras

Primero se conecta un dueño explícito a InboxService y a consumidor/directo/barridos, con pruebas de composición; la activación del singleton API y el productor administrativo se validan después, antes de cerrar T009. Durante la transición, las instancias antiguas sin dueño explícito mantienen su comportamiento; esa compatibilidad no acredita admisión M1 ni debe presentarse como activación del servidor.

El consumidor reserva antes de extraer ordinal/Context/registry. Aparca cabezas ocupadas conservando su aviso y avanza otros receptores; una señal de capacidad o fin físico reactiva el buffer, sin sondeo activo. Los trabajos admitidos usan hilos propios sin una segunda cola con techo fijo del executor; su cantidad queda limitada por reservas. El trabajador toma el lock por receptor sin espera y confirma start antes de SQL. Finish pertenece al finally físico. Los barridos cierran el cursor al denegar capacidad y antes de esperar sus hijos. Un lote num_messages>1 conserva una sola operación serial y su reserva hasta concluir todos sus envíos/estados; la estimación es por invocación serial completa: num_messages=0 conserva el máximo histórico100 y un positivo conserva el límite solicitado, y su aceptación física sigue pendiente.

El primer wiring mantiene almacenamiento hasta completar send_input y sus escrituras; no libera antes de auditorías/recibos ni eleva el pool4/checkout2s. La separación adicional de etapas requiere sus controles propios. Work/remoto permanecen en T010.
