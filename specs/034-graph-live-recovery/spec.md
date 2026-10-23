# Feature Specification: Actualización viva, recuperación y aislamiento de acceso

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/034-graph-live-recovery`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — saber qué tan reciente es la red y recuperar cambios sin duplicados ni datos de otro ámbito (Priority: P1)

Como operador quiero saber qué tan reciente es la red y recuperar cambios sin duplicados ni datos de otro ámbito.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** una instantánea y una actualización concurrente, **When** inicio el seguimiento, **Then** el estado converge sin perder ni duplicar la transición.
2. **Given** un cursor fuera de retención, **When** reconecto, **Then** se recupera el estado actual y se señala el intervalo histórico desconocido.
3. **Given** un cambio de proyecto con respuesta antigua pendiente, **When** llega la respuesta antigua, **Then** no se incorpora al proyecto nuevo ni revela contenido revocado.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** un cursor fuera de retención, **When** reconecto, **Then** se recupera el estado actual y se señala el intervalo histórico desconocido.
2. **Given** un cambio de proyecto con respuesta antigua pendiente, **When** llega la respuesta antigua, **Then** no se incorpora al proyecto nuevo ni revela contenido revocado.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Reinicio de API durante suscripción.
- Revocación durante lectura de agregados.
- Borrado seguido de evento tardío.
- Consumidor lento y cola llena.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: La carga inicial y las actualizaciones DEBEN converger a revisiones verificables, sin perder eventos entre instantánea y suscripción.
- **FR-002**: Cada fuente DEBE declarar orden local, cursor, retención y cobertura; relojes de máquinas distintas no se usarán como orden causal global.
- **FR-003**: Duplicados y eventos anteriores DEBEN ser idempotentes y no revertir estados más recientes ni resucitar entidades eliminadas.
- **FR-004**: La desconexión DEBE indicar última observación y estado desactualizado; no se mostrará en vivo mientras no se pueda verificar continuidad.
- **FR-005**: Un cursor vencido o hueco DEBE producir resincronización y declarar el tramo desconocido, conservando la diferencia entre estado actual e historia completa.
- **FR-006**: Cambiar proyecto, instancia, encarnación o permisos DEBE invalidar lecturas y suscripciones anteriores antes de incorporar datos.
- **FR-007**: La autorización DEBE aplicarse en servidor a instantáneas, eventos, agregados y detalles; revocación debe retirar datos y cerrar acceso sin esperar una acción del usuario.
- **FR-008**: Las consultas ambientales DEBEN ser de metadatos y no precargar contenido privado con intención de ocultarlo después.
- **FR-009**: Actualizar el grafo DEBE conservar selección y encuadre salvo que el recurso deje de estar disponible, explicándolo sin sustituir su identidad.
- **FR-010**: Consumidores lentos DEBEN aplicar una política de cola acotada y recuperación explícita; desbordar no debe consumir memoria sin límite ni omitir cambios silenciosamente.

- **FR-011**: El panel de actividad DEBE proceder de eventos reales identificados, con fecha, origen, objeto y paginación. Su orden será por fuente cuando no exista orden global; la reproducción histórica no debe ejecutar acciones y debe declarar retención y tramos ausentes.

### Key Entities

- **Instantánea con revisión**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Cursor por fuente**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Evento y tombstone**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Estado de conexión**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Revisión de autorización**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Los casos de duplicación, reordenación y reconexión convergen al estado autoritativo sin divergencias.
- **SC-002**: El 100 % de huecos inducidos se muestra o se resuelve con resincronización explícita.
- **SC-003**: Cero datos del ámbito anterior después de completar el cambio o revocación.
- **SC-004**: Las colas y memoria permanecen dentro del presupuesto publicado bajo consumidor lento.

- **SC-005**: Se conserva 017-SC-001: con 20 agentes y 100 relaciones, el 95 % de cambios confirmados se hace visible en menos de 2 segundos desde disponibilidad en origen, en el entorno de referencia.

## Assumptions

- Propietaria del ciclo de actualización de 017. Reutiliza los transportes existentes y no presupone que el stream fleet cubra todo Work.
- Specs previas relacionadas: [011](../011-authenticated-live-events/spec.md), [016](../016-agent-interactions/spec.md), [017](../017-live-execution-graph/spec.md), [021](../021-postgresql-persistence/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: [029](../029-execution-graph-provenance/spec.md)
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
