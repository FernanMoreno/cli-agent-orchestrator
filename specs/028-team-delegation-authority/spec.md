# Feature Specification: Coordinación efectiva y delegación entre equipos

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/028-team-delegation-authority`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — que un coordinador mostrado en el grafo realmente pueda asignar el trabajo permitido a su equipo y que las delegaciones reflejen operaciones admitidas (Priority: P1)

Como operador quiero que un coordinador mostrado en el grafo realmente pueda asignar el trabajo permitido a su equipo y que las delegaciones reflejen operaciones admitidas.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** un coordinador autorizado y un revisor de solo lectura, **When** ambos solicitan asignar, **Then** solo el coordinador obtiene una operación admitida y el rechazo no crea trabajador.
2. **Given** una asignación admitida cuya respuesta se pierde, **When** se reintenta, **Then** se recupera la misma identidad sin nuevo efecto.
3. **Given** dos tareas listas que escriben el mismo destino, **When** se programa el equipo, **Then** se aíslan o serializan y el grafo muestra la decisión real.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** una asignación admitida cuya respuesta se pierde, **When** se reintenta, **Then** se recupera la misma identidad sin nuevo efecto.
2. **Given** dos tareas listas que escriben el mismo destino, **When** se programa el equipo, **Then** se aíslan o serializan y el grafo muestra la decisión real.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Revocación entre preparación y despacho.
- Destino ocupado o presupuesto agotado.
- Trabajador desconectado con entrega incierta.
- Perfil visible con herramienta filtrada en servidor.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Una designación de coordinador DEBE referenciar la autoridad ya emitida por los contratos existentes de asignación, Work y, para Spec Kit de producto, 020, con proyecto, operaciones, destinatarios y vigencia; la designación no emitirá otra concesión ni el nombre del perfil concederá permisos.
- **FR-002**: Toda asignación DEBE identificar mandante, coordinador, equipo, tarea, intento, destino, concesión y revisión aplicables.
- **FR-003**: La delegación DEBE verificarse en el servidor por operación antes del efecto; ampliar permisos del cliente no debe ampliar la autoridad del servidor.
- **FR-004**: Crear terminal, delegar trabajo, subcontratar una parte y comunicar un mensaje DEBEN registrarse como hechos distintos.
- **FR-005**: La programación DEBE comprobar dependencias, capacidades del proveedor, límites de recursos y exclusión de escrituras sobre el mismo destino.
- **FR-006**: Cuando se autorice paralelismo con escrituras, el sistema DEBE usar destinos aislados o exclusión demostrada, y conservar su identidad en el grafo.
- **FR-007**: Reintentar una solicitud incierta DEBE reconciliar la asignación existente con la misma identidad e impedir duplicar trabajador o entrega.
- **FR-008**: Revocar un mandato DEBE bloquear nuevas operaciones de ese mandato; los intentos en curso deben mostrar su política de continuación o cancelación sin fingir que ya terminaron.
- **FR-009**: El coordinador DEBE poder resumir resultados únicamente con evidencia aceptada de sus tareas; un ACK o la salida del proceso no completa el equipo.
- **FR-010**: Los trabajadores elásticos DEBEN usar su contrato de finalización específico cuando esté configurado; no debe ofrecerse ese mecanismo a cualquier agente por analogía.

### Key Entities

- **Mandato de coordinación**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Asignación con identidad y revisión**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Referencia a concesión efectiva existente**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Reserva de recursos**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cero efectos de delegación en los casos sin autorización o con revisión obsoleta.
- **SC-002**: Una única asignación y entrega por clave de operación bajo reintentos.
- **SC-003**: Todas las delegaciones visibles tienen recibo de admisión y estado verificable.
- **SC-004**: Las ejecuciones paralelas no comparten una escritura incompatible sin exclusión comprobada.

- Los contratos de 006/009/020/025 y Work son propietarios de autorización, aprobación, admisión y reconciliación. Esta entrega solo vincula equipos, responsables y recibos a esas decisiones; un mandato organizativo nuevo deberá referenciar la concesión vigente y no podrá sustituirla. Para coordinar fases Spec Kit como producto se requiere la capacidad de 020, no solo su documento.

## Assumptions

- Amplía contratos existentes para equipos reales. 025 conserva propiedad de perfiles MCP; 028 consume sus decisiones y no inventa permisos por nombres ni enlaces.
- Specs previas relacionadas: [006](../006-agent-collaboration/spec.md), [009](../009-local-cao-coordination/spec.md), [025](../025-provider-mcp-profiles/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: [027](../027-agent-team-topology/spec.md)
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
