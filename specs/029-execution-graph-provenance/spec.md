# Feature Specification: Identidad, procedencia y contrato del grafo de ejecución

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/029-execution-graph-provenance`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — inspeccionar cada nodo y arista y conocer exactamente qué registro real representa (Priority: P1)

Como operador quiero inspeccionar cada nodo y arista y conocer exactamente qué registro real representa.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** dos servidores con terminal local del mismo nombre, **When** se proyectan juntos, **Then** se conservan dos identidades y sus relaciones correctas.
2. **Given** un mensaje con extremo no autorizado, **When** se consulta el grafo, **Then** no se revela el extremo y la cobertura sigue siendo veraz sin filtrar conteos privados.
3. **Given** un terminal eliminado con tarea histórica, **When** se inspecciona el intento, **Then** se conserva el registro de trabajo sin crear un proceso vivo ficticio.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** un mensaje con extremo no autorizado, **When** se consulta el grafo, **Then** no se revela el extremo y la cobertura sigue siendo veraz sin filtrar conteos privados.
2. **Given** un terminal eliminado con tarea histórica, **When** se inspecciona el intento, **Then** se conserva el registro de trabajo sin crear un proceso vivo ficticio.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Reinicio con nueva encarnación.
- Extremo revocado durante carga.
- Fuente sin revisión comparable.
- Relación eliminada cuyo evento llega tarde.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: La identidad DEBE distinguir proyecto, instancia de servidor, encarnación y recurso; dos terminales de distintos orígenes con igual identificador local no serán el mismo agente.
- **FR-002**: Cada nodo DEBE diferenciar agente lógico, terminal/proceso, tarea, intento, pregunta, grupo y resultado, sin equiparar sus ciclos de vida.
- **FR-003**: Cada arista DEBE identificar tipo, dirección, extremos, registro de origen, revisión, vigencia y cobertura; deberá conservar su identidad al agregar o filtrar.
- **FR-004**: La proyección DEBE exponer únicamente metadatos necesarios; prompts, razonamientos, respuestas y secretos requieren lectura de detalle autorizada separada.
- **FR-005**: La propiedad de una tarea DEBE proceder de la asignación vigente del intento; una fila visual por sí sola no prueba quién la ejecuta.
- **FR-006**: Una creación de terminal DEBE diferenciarse de asignación o mando. Los registros históricos de hijos y asignaciones deben conservar trazabilidad tras borrar el proceso.
- **FR-007**: Las fuentes incompletas o incompatibles DEBEN producir cobertura explícita; los extremos no visibles no se sustituirán por agentes inventados.
- **FR-008**: La lectura DEBE definir una revisión consistente o un vector de revisiones por fuente; no se presentarán lecturas mezcladas como una instantánea global atómica.
- **FR-009**: La taxonomía de ejecución DEBE separarse de relaciones temáticas del conocimiento; una asociación temática no prueba comunicación, dependencia o delegación.
- **FR-010**: El inspector DEBE ofrecer procedencia, antigüedad, permisos y navegación al registro autorizado, distinguiendo eliminado, inaccesible y no observado.

### Key Entities

- **Identidad de nodo por origen y encarnación**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Relación tipada con evidencia**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Proyección autorizada**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Cobertura por fuente**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El 100 % de nodos y aristas del corpus de aceptación tiene procedencia verificable.
- **SC-002**: Cero colisiones entre orígenes o encarnaciones en pruebas de identidad.
- **SC-003**: La lectura ambiental no contiene cuerpos ni secretos de los canarios de privacidad.
- **SC-004**: Cada fuente ausente, parcial o fallida se distingue de una fuente vacía.

## Assumptions

- Refina el contrato de lectura de 017. No convierte el grafo de memoria en autoridad de ejecución ni decide programación.
- Specs previas relacionadas: [017](../017-live-execution-graph/spec.md), [021](../021-postgresql-persistence/spec.md), [024](../024-work-agent-messages/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: [027](../027-agent-team-topology/spec.md)
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
