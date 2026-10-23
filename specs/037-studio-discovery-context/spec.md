# Feature Specification: Búsqueda, filtros y navegación con contexto real

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/037-studio-discovery-context`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — buscar, filtrar y volver al mismo lugar sin perder proyecto, selección, revisión ni permisos (Priority: P1)

Como operador quiero buscar, filtrar y volver al mismo lugar sin perder proyecto, selección, revisión ni permisos.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** un agente encontrado fuera de la página actual, **When** abro el resultado y vuelvo, **Then** se conserva su identidad y se abre su página correcta.
2. **Given** una selección en grafo con filtros activos, **When** cambio a lista y uso atrás, **Then** se conserva contexto y cobertura.
3. **Given** no hay coincidencias en una fuente parcial, **When** consulto el resultado, **Then** no se afirma que el recurso no exista globalmente.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** una selección en grafo con filtros activos, **When** cambio a lista y uso atrás, **Then** se conserva contexto y cobertura.
2. **Given** no hay coincidencias en una fuente parcial, **When** consulto el resultado, **Then** no se afirma que el recurso no exista globalmente.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Recurso eliminado entre búsqueda y apertura.
- Identificador Unicode.
- Filtros que esconden la selección.
- URL antigua con permisos distintos.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: La búsqueda DEBE declarar si cubre el nivel visible, el proyecto o todos los orígenes autorizados; no presentará búsqueda local como global.
- **FR-002**: La búsqueda DEBE localizar agentes, equipos, tareas y decisiones por identidad o texto permitido, y distinguir no encontrado de acceso o fuente no disponible.
- **FR-003**: Cambiar entre grafo y lista DEBE conservar ámbito, filtros y selección, con los mismos datos y cobertura.
- **FR-004**: Los filtros de tipo de nodo, estado, relación e instancia DEBEN componer una consulta explícita y mostrar cuánto se está observando.
- **FR-005**: Los breadcrumbs DEBEN reflejar la jerarquía registrada o la agrupación visual elegida; subir no cambiará equipo ni autoridad.
- **FR-006**: Los enlaces directos y atrás/adelante DEBEN restaurar un contexto autorizado o explicar su pérdida sin abrir un recurso homónimo.
- **FR-007**: El inspector DEBE poder centrar, abrir extremos y volver al grafo preservando selección, página y filtros cuando sigan siendo válidos.
- **FR-008**: Vacío real, sin coincidencias, error, carga, parcial y acceso denegado DEBEN tener presentaciones y próximos pasos distintos.
- **FR-009**: Las etiquetas de conteo DEBEN distinguir total del ámbito, resultado de filtros y elementos dibujados.
- **FR-010**: El atajo de búsqueda DEBE respetar campos en edición; cambiar de ámbito debe cancelar consultas anteriores y eliminar resultados revocados.

### Key Entities

- **Contexto de navegación**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Consulta y filtros**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Selección persistible**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Enlace directo autorizado**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Todos los recorridos de ida y vuelta del corpus conservan contexto autorizado e identidad.
- **SC-002**: Grafo y lista concuerdan en el 100 % de elementos y relaciones de la misma consulta.
- **SC-003**: Cero resultados de un ámbito anterior incorporados después de cambiarlo.
- **SC-004**: Cada estado vacío, parcial, fallido y denegado tiene una salida comprobada.

## Assumptions

- Concreta navegación pendiente de 019; no cambia la autoridad o la organización al filtrar.
- Specs previas relacionadas: [017](../017-live-execution-graph/spec.md), [019](../019-frontend-studio/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: [029](../029-execution-graph-provenance/spec.md), [030](../030-graph-hierarchy-aggregation/spec.md)
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
