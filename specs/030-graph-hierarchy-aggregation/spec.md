# Feature Specification: Conexiones y detalles coherentes en todos los niveles

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/030-graph-hierarchy-aggregation`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — ver conexiones entre equipos y bajar hasta la relación concreta sin que cambie lo que representan (Priority: P1)

Como operador quiero ver conexiones entre equipos y bajar hasta la relación concreta sin que cambie lo que representan.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** un enlace entre agentes de equipos diferentes, **When** abro el haz y bajo a ambos extremos, **Then** encuentro la misma relación, dirección y evidencia en todos los niveles.
2. **Given** más relaciones que las visibles en pantalla, **When** consulto resumen y siguientes páginas, **Then** el resumen declara cobertura y todas las relaciones autorizadas son alcanzables sin duplicados.
3. **Given** un cambio de estado entre páginas, **When** solicito la siguiente página, **Then** se mantiene la revisión o se indica que debe renovarse la consulta.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** más relaciones que las visibles en pantalla, **When** consulto resumen y siguientes páginas, **Then** el resumen declara cobertura y todas las relaciones autorizadas son alcanzables sin duplicados.
2. **Given** un cambio de estado entre páginas, **When** solicito la siguiente página, **Then** se mantiene la revisión o se indica que debe renovarse la consulta.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Ciclos de comunicación y autoagrupación.
- Un equipo vacío con conexiones históricas.
- Puerto exterior fuera de filtros.
- Cambios de permiso entre resumen y detalle.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Cada haz DEBE derivarse de relaciones reales autorizadas del mismo ámbito y revisión, agrupadas por tipo, dirección y grupos de extremos.
- **FR-002**: Un resumen DEBE distinguir relaciones internas, entre grupos, externas y no cubiertas; no se eliminarán silenciosamente relaciones dentro de un mismo grupo.
- **FR-003**: Bajar, subir y abrir un agente DEBE conservar la identidad de la relación concreta, el ámbito y la página que contiene el extremo.
- **FR-004**: La agrupación por estado o proyecto DEBE explicitar su semántica y cobertura; no equivale a una nueva estructura de mando.
- **FR-005**: Los conteos DEBEN indicar si son exactos para la revisión, parciales o desconocidos. Las sumas deben deduplicar agentes y relaciones conforme a esa semántica.
- **FR-006**: El detalle DEBE estar disponible a cualquier nivel mediante selección de grupo, haz, puerto y relación individual, con origen y destino autorizados.
- **FR-007**: La consulta y el dibujo DEBEN tener límites explícitos y paginación; nunca se descargará la red completa para dibujar una ventana pequeña.
- **FR-008**: La primera configuración DEBE limitar cada ventana a 40 tarjetas, 24 haces y 6 puertos dibujados, ofreciendo acceso paginado o listado al resto; esos límites no eliminan datos del ámbito.
- **FR-009**: Cambios de revisión DEBEN invalidar o rebasar explícitamente cursores; se impedirá mezclar páginas de revisiones incompatibles.
- **FR-010**: Las referencias exteriores DEBEN permitir seguir conexiones autorizadas entre equipos y niveles sin filtrar identidades o cantidades de recursos ocultos.
- **FR-011**: Las pruebas DEBEN medir consulta, transferencia y dibujo por separado; una red sintética de un millón de agentes solo prueba lo que realmente se haya ejecutado.

- **FR-012**: Los indicadores de En curso, Esperan decisión, Bloqueadas, Hechas hoy, barras y porcentajes DEBEN declarar si cuentan agentes, tareas o preguntas y la revisión de su denominador. Un agente bloqueado no equivale por sí solo a una decisión pendiente; los estados desconocidos no se ocultarán para aparentar un 100 % completo.

### Key Entities

- **Ámbito y revisión de consulta**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Haz de relaciones**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Puerto externo**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Página y cursor**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Resumen de cobertura**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cero pérdidas o duplicados de identidades al recorrer todos los niveles del corpus.
- **SC-002**: El 100 % de conteos exactos coincide con el conjunto autoritativo autorizado de la misma revisión.
- **SC-003**: El dibujo cumple 40 tarjetas, 24 haces y 6 puertos por ventana en las pruebas de escala.
- **SC-004**: El informe de carga declara tamaño total, tamaño consultado, bytes, latencias y memoria; no acredita capacidad ilimitada.

- **SC-005**: Se conserva 017-SC-002: con 100 agentes y 500 relaciones autorizadas, el 95 % de aperturas tarda menos de 3 segundos y el 95 % de selecciones menos de 200 ms en el entorno de referencia, sin reducir cobertura para cumplir el umbral.

## Assumptions

- Propietaria de agregación y navegación de relaciones. La organización pertenece a 027 y la fuente tipada a 029; los presupuestos finales de carga se fijan antes de implementar y no rebajan 017.
- Specs previas relacionadas: [017](../017-live-execution-graph/spec.md), [019](../019-frontend-studio/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: [027](../027-agent-team-topology/spec.md), [029](../029-execution-graph-provenance/spec.md)
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
