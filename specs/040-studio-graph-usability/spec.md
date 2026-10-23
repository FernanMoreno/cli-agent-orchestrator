# Feature Specification: Lectura, accesibilidad y operación de la red

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/040-studio-graph-usability`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — entender y manejar la red a cualquier nivel sin que las líneas tapen nombres ni dependa del ratón o del color (Priority: P1)

Como operador quiero entender y manejar la red a cualquier nivel sin que las líneas tapen nombres ni dependa del ratón o del color.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** nombres largos y conexiones densas en todos los niveles, **When** selecciono y abro los grupos, **Then** leo los nombres completos y ninguna línea bloquea controles.
2. **Given** un operador con teclado y lector de pantalla, **When** sigue un vínculo de equipo a agente y vuelve, **Then** recibe etiquetas correctas y conserva foco.
3. **Given** un móvil de 375 px con error de carga, **When** abro y recupero la vista, **Then** no hay desbordamiento del documento y se conserva contexto.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** un operador con teclado y lector de pantalla, **When** sigue un vínculo de equipo a agente y vuelve, **Then** recibe etiquetas correctas y conserva foco.
2. **Given** un móvil de 375 px con error de carga, **When** abro y recupero la vista, **Then** no hay desbordamiento del documento y se conserva contexto.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Zoom mínimo con nombres largos.
- Movimiento reducido y contraste.
- Elemento seleccionado que desaparece.
- Gran volumen de actualizaciones.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Nombres de equipo, instancia, agente y tarea DEBEN permanecer legibles y seleccionables; las líneas no atravesarán sus etiquetas ni interceptarán sus controles.
- **FR-002**: Toda relación DEBE distinguir tipo y dirección sin depender exclusivamente de color; nodos y estados tendrán texto o símbolos equivalentes.
- **FR-003**: Zoom, encajar, 100 %, centrar, desplazar y minimapa DEBEN actuar sobre el ámbito actual sin alterar datos, permisos o selección.
- **FR-004**: La navegación completa DEBE poder realizarse por teclado y lista: elegir grupo, bajar, subir, seleccionar vínculo, abrir extremo, responder y volver.
- **FR-005**: Los lectores de pantalla DEBEN recibir identidad, estado, dirección, alcance y cantidad de relaciones; las actualizaciones no saturarán anuncios ni robarán foco.
- **FR-006**: Los atajos DEBEN respetar edición de texto, foco y preferencias de movimiento reducido; cada gesto de arrastre tendrá alternativa.
- **FR-007**: Las vistas DEBEN funcionar a 375 px y escritorio sin desbordamiento horizontal del documento; el lienzo podrá desplazarse dentro de su área identificada.
- **FR-008**: Los textos truncados DEBEN poder consultarse completos en un detalle accesible; el encuadre automático no reducirá indefinidamente la lectura para ocultar un exceso de elementos.
- **FR-009**: Los errores de consulta o acción DEBEN conservar contexto y ofrecer recuperación explícita; actividad y contadores no mostrarán éxito hasta confirmación.
- **FR-010**: Los escenarios sintéticos DEBEN permanecer rotulados y separados de datos operativos; la versión productiva no heredará rótulos de autoridad ficticia como si fueran reales.
- **FR-011**: Las pruebas DEBEN realizarse también sobre la aplicación de producto con contratos reales y estados de error, no solo sobre el HTML de maqueta.

### Key Entities

- **Estado de vista**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Foco y selección**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Etiqueta accesible**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Diagnóstico de renderizado**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cero solapamientos de líneas con etiquetas en los casos de nombres largos y densidad publicados.
- **SC-002**: Todos los recorridos críticos se completan por teclado y una revisión manual de lector de pantalla.
- **SC-003**: El documento mantiene ancho de 375 px en los casos móviles; el lienzo usa desplazamiento local.
- **SC-004**: Los estados se comprenden sin color y la selección no se pierde por actualizaciones ordinarias.

- Se heredan todos los criterios globales de accesibilidad de 019; los criterios propios de esta entrega se aplican específicamente a navegar, inspeccionar y operar el grafo. No sustituyen la aceptación global de Studio.

## Assumptions

- Aplica calidad visual y accesibilidad a las vistas reales; no introduce una promesa de agentes ilimitados ni usa el zoom para eludir paginación.
- Specs previas relacionadas: [017](../017-live-execution-graph/spec.md), [019](../019-frontend-studio/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: [030](../030-graph-hierarchy-aggregation/spec.md), [033](../033-graph-human-decisions/spec.md), [037](../037-studio-discovery-context/spec.md)
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
