# Feature Specification: Decisiones, destinatarios y desbloqueo real

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/033-graph-human-decisions`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — responder desde el grafo una pregunta real y ver su efecto solo cuando CAOS lo haya confirmado (Priority: P1)

Como operador quiero responder desde el grafo una pregunta real y ver su efecto solo cuando CAOS lo haya confirmado.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** una pregunta obligatoria y otra opcional en ramas independientes, **When** respondo la obligatoria, **Then** solo su espera se resuelve cuando confirma el motor y la otra rama no cambia de estado arbitrariamente.
2. **Given** una respuesta aceptada pero todavía no confirmada, **When** consulto detalle, **Then** se ve pendiente de consumo sin completarse automáticamente la tarea.
3. **Given** una delegación revocada, **When** el antiguo destinatario intenta responder, **Then** la operación se rechaza y el grafo deja de ofrecerle responder.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** una respuesta aceptada pero todavía no confirmada, **When** consulto detalle, **Then** se ve pendiente de consumo sin completarse automáticamente la tarea.
2. **Given** una delegación revocada, **When** el antiguo destinatario intenta responder, **Then** la operación se rechaza y el grafo deja de ofrecerle responder.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Respuesta concurrente de dos operadores.
- Pregunta nativa sin estructura disponible.
- Grupo parcialmente respondido.
- ACK después de reconexión.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Cada decisión DEBE enlazar pregunta, grupo, agente origen, encarnación, tarea e intento cuando estén registrados; los vínculos desconocidos deberán seguir siendo desconocidos.
- **FR-002**: La vista DEBE distinguir quien pregunta, quien debe decidir y quien tiene autoridad vigente para responder o delegar.
- **FR-003**: Las opciones y texto DEBEN proceder de la pregunta autorizada vigente; no se inventarán opciones a partir de un estado nativo de espera.
- **FR-004**: Responder DEBE usar el contrato real de 016 con control de versión e idempotencia; el estado local no debe fingir una respuesta persistida.
- **FR-005**: Respuesta aceptada, entregada, confirmada por el agente y trabajo desbloqueado DEBEN mostrarse como hechos separados.
- **FR-006**: Una respuesta solo DEBE liberar las esperas a las que aplique y cuando el motor lo confirme; otras dependencias pendientes conservarán el bloqueo.
- **FR-007**: Las preguntas opcionales DEBEN permitir trabajo continuado conforme a su contrato y no sumarse como bloqueo obligatorio.
- **FR-008**: Delegar o revocar una decisión DEBE invalidar autoridad previa y conservar su historial sin ampliar permisos por la jerarquía dibujada.
- **FR-009**: Cancelar, caducar o reconciliar DEBE conservar causa y revisión; un reinicio no creará preguntas duplicadas ni perderá ACK.
- **FR-010**: El panel de decisiones, sus contadores y el grafo DEBEN referirse al mismo ámbito y revisión, con paginación y cobertura declaradas.

### Key Entities

- **Pregunta y grupo**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Destinatario autorizado**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Respuesta versionada**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Confirmación de consumo**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Espera obligatoria u opcional**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El 100 % de respuestas visibles como aceptadas existe en el registro de 016.
- **SC-002**: Cero desbloqueos ajenos a las condiciones realmente satisfechas.
- **SC-003**: Preguntas y confirmaciones conservan identidad después del reinicio en cada proveedor declarado.
- **SC-004**: Operadores sin autoridad no acceden a contenido ni responden por un botón o enlace directo.

## Assumptions

- Completa integración de decisiones de 016 y 017; no declara cerrado 022/025 por integrar una pantalla.
- Specs previas relacionadas: [016](../016-agent-interactions/spec.md), [017](../017-live-execution-graph/spec.md), [022](../022-provider-agui-restart/spec.md), [025](../025-provider-mcp-profiles/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: [029](../029-execution-graph-provenance/spec.md), [032](../032-graph-work-dependencies/spec.md)
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
