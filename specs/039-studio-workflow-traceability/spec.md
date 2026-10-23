# Feature Specification: Workflows y Spec Kit trazables hasta el trabajo ejecutado

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/039-studio-workflow-traceability`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — que fases, tareas y revisiones de workflow visibles correspondan al documento aprobado y a su ejecución real (Priority: P1)

Como operador quiero que fases, tareas y revisiones de workflow visibles correspondan al documento aprobado y a su ejecución real.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** un workflow soportado de dos pasos, **When** lo ejecuto tras revisar, **Then** cada paso del grafo se vincula al documento y al intento reales.
2. **Given** se cambia el perfil de un paso aprobado, **When** se solicita despachar, **Then** se invalida la aprobación afectada.
3. **Given** un agente termina sin generar la spec requerida, **When** se evalúa la fase, **Then** sigue incompleta con causa visible.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** se cambia el perfil de un paso aprobado, **When** se solicita despachar, **Then** se invalida la aprobación afectada.
2. **Given** un agente termina sin generar la spec requerida, **When** se evalúa la fase, **Then** sigue incompleta con causa visible.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Documento modificado por otra sesión.
- Dependencia no soportada por el motor.
- Reanudación con resultado pendiente de proyectar.
- Aclaración sin destinatario autorizado.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Cada fase o paso visible DEBE identificar workflow o feature, revisión de documentos, entradas, perfil y destino aprobados.
- **FR-002**: Crear, validar, revisar y ejecutar DEBEN ser etapas diferentes con artefactos comprobables; terminar un agente sin artefacto válido no completa una fase.
- **FR-003**: Las dependencias DEBEN conservar las reglas soportadas del motor; los modos reservados o incompatibles se bloquearán con motivo.
- **FR-004**: La edición visual y la fuente DEBEN preservar semántica y campos no representados, sin sobrescritura silenciosa.
- **FR-005**: La vista DEBE presentar la aprobación emitida por el motor existente, con revisiones y límites; ante un cambio relevante debe reflejar su invalidación y bloquear el comando según la capacidad del motor, sin implementar una aprobación alternativa.
- **FR-006**: Cada paso ejecutado DEBE vincular su tarea, intento, agente, asignación y resultado aceptado; ningún salto visual debe inventar esa cadena.
- **FR-007**: Los hallazgos y preguntas de aclaración DEBEN asociarse a la fase y documentos que los causaron y respetar autoridad de respuesta.
- **FR-008**: Los comandos de reintento o recuperación DEBEN invocar los contratos del motor existente y presentar su reconciliación e intentos, distinguiendo nueva ejecución de reanudación sin un motor alternativo en Studio.
- **FR-009**: La entrega DEBE mostrar revisión y pruebas aceptadas, pendientes y fallidas; un contador de pasos terminados no sustituye la aceptación del conjunto.
- **FR-010**: Usar Spec Kit en desarrollo de CAOS DEBE distinguirse de que CAOS lo ofrezca como capacidad de producto a otro proyecto.

### Key Entities

- **Documento versionado**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Plan aprobado**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Paso de workflow**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Vínculo tarea-intento-resultado**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Hallazgo de validación**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cada paso de aceptación tiene cadena documental y de ejecución completa.
- **SC-002**: Cero despachos con aprobación invalidada en los casos de cambio de revisión.
- **SC-003**: Ninguna fase se completa sin su artefacto y aceptación exigidos.
- **SC-004**: Los recorridos de producto tienen evidencia separada del uso interno de Spec Kit.

- 020 es propietaria y dependencia de entrega del recorrido de Spec Kit como capacidad de producto: sin su capacidad implementada y validada, Studio mostrará indisponibilidad. Los workflows existentes pueden integrarse de forma independiente sobre 012; esta spec añade lectura, vinculación y acceso a comandos existentes, no aprobación, despacho o recuperación alternativos.

## Assumptions

- Descompone la integración pendiente de 019/020 sin sustituir sus requisitos ni dar por implementado el motor de producto de Spec Kit.
- Specs previas relacionadas: [012](../012-workflow-reliability/spec.md), [019](../019-frontend-studio/spec.md), [020](../020-speckit-orchestration/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: [028](../028-team-delegation-authority/spec.md), [029](../029-execution-graph-provenance/spec.md), [032](../032-graph-work-dependencies/spec.md), [036](../036-studio-work-commands/spec.md)
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
