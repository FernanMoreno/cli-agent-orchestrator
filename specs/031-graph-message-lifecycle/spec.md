# Feature Specification: Comunicación real y ciclo de entrega en el grafo

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/031-graph-message-lifecycle`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — que una línea de comunicación corresponda a un mensaje real y saber si fue enviado, recibido o confirmado (Priority: P1)

Como operador quiero que una línea de comunicación corresponda a un mensaje real y saber si fue enviado, recibido o confirmado.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** un mensaje Work admitido sin confirmación, **When** abro su conexión, **Then** veo el canal y la fase real sin tarea completada.
2. **Given** un inbox con más de una página, **When** filtro por actividad reciente, **Then** no se confunde la primera página antigua con las últimas comunicaciones.
3. **Given** una confirmación repetida tras reinicio, **When** recargo el grafo, **Then** veo el mismo mensaje una sola vez con estado durable.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** un inbox con más de una página, **When** filtro por actividad reciente, **Then** no se confunde la primera página antigua con las últimas comunicaciones.
2. **Given** una confirmación repetida tras reinicio, **When** recargo el grafo, **Then** veo el mismo mensaje una sola vez con estado durable.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Mismo número de mensaje en canales distintos.
- Retención del cuerpo con metadatos conservados.
- Receptor eliminado.
- ACK de entrega sin resultado aceptado.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Cada comunicación DEBE identificar el canal real, mensaje, emisor, receptor, intento cuando exista y estado de entrega, sin fusionar canales por parecido de nombres.
- **FR-002**: El contrato externo cao.work.message DEBE demostrarse a través de su entrada real; send_message histórico no lo acredita.
- **FR-003**: Admisión, entrega, recepción y confirmación DEBEN mostrarse separadas de ejecución y aceptación del resultado.
- **FR-004**: Los mensajes duplicados DEBEN conservar una única identidad visible; las confirmaciones repetidas no crearán nuevas comunicaciones.
- **FR-005**: La lectura DEBE permitir consultar páginas y ventanas temporales definidas; primera página antigua no se presentará como actividad más reciente.
- **FR-006**: Un resumen de mensajes DEBE declarar si cuenta mensajes, pares de agentes o haces; todos los detalles deben remitir a mensajes del mismo canal.
- **FR-007**: La red ambiental DEBE limitarse a metadatos autorizados y no recuperar cuerpos para construir sus aristas.
- **FR-008**: Fallos, expiración, revocación y estado incierto DEBEN permanecer visibles con sus causas permitidas; no se marcarán entregados por actividad del terminal.
- **FR-009**: Reiniciar la API DEBE conservar identidad y estados durables, y reconectar sin reenviar mensajes desde la vista.
- **FR-010**: Un mensaje entre equipos DEBE conservar su identidad y dirección al agruparse, sin convertirse en una dependencia o concesión de autoridad.

### Key Entities

- **Mensaje identificado por canal y origen**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Estado de entrega**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Confirmación**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Vinculación de intento**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cada arista de comunicación se reconcilia con exactamente un mensaje o un haz enumerado de mensajes del canal declarado.
- **SC-002**: Cero cuerpos de mensajes en consultas ambientales de aceptación.
- **SC-003**: Duplicar eventos o confirmaciones no cambia la cardinalidad de mensajes únicos.
- **SC-004**: El recorrido externo Work y el tradicional producen recibos de aceptación separados.

## Assumptions

- Integra 023/024; no reimplementa sus buzones ni relaja su autorización o aceptación final.
- Specs previas relacionadas: [017](../017-live-execution-graph/spec.md), [023](../023-generic-message-api-restart/spec.md), [024](../024-work-agent-messages/spec.md), [025](../025-provider-mcp-profiles/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: [029](../029-execution-graph-provenance/spec.md)
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
