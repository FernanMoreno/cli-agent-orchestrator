# Feature Specification: Crear tareas y actuar sobre trabajo real desde Studio

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/036-studio-work-commands`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — que Nueva tarea y las acciones de trabajo produzcan operaciones reales, trazables y recuperables (Priority: P1)

Como operador quiero que Nueva tarea y las acciones de trabajo produzcan operaciones reales, trazables y recuperables.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** una tarea nueva con dependencias, **When** la guardo sin ejecutar, **Then** aparece registrada sin crear trabajador.
2. **Given** se pierde la respuesta de iniciar, **When** vuelvo a intentarlo, **Then** recupero la operación original o la incertidumbre sin duplicar ejecución.
3. **Given** un plan aprobado cambia de destino, **When** solicito iniciar, **Then** se exige revisar la diferencia y no se usa la aprobación anterior.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** se pierde la respuesta de iniciar, **When** vuelvo a intentarlo, **Then** recupero la operación original o la incertidumbre sin duplicar ejecución.
2. **Given** un plan aprobado cambia de destino, **When** solicito iniciar, **Then** se exige revisar la diferencia y no se usa la aprobación anterior.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Doble clic y varias pestañas.
- Cancelación concurrente con aceptación.
- Acción revocada mientras el formulario está abierto.
- Borrador de proyecto anterior.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Nueva tarea DEBE conservar proyecto, equipo opcional, objetivo, aceptación, dependencias y destinatario propuesto sin iniciar trabajo durante la edición.
- **FR-002**: La confirmación DEBE identificar qué se guarda, prepara, aprueba o ejecuta; crear una tarea no será presentado como haberla asignado.
- **FR-003**: Las acciones disponibles DEBEN corresponder a capacidad y autorización efectivas del servidor para el recurso y su revisión.
- **FR-004**: Preparar, asignar, iniciar, cancelar, reintentar y reconciliar DEBEN conservar identidades y recibos distintos según sus contratos.
- **FR-005**: Una respuesta incierta DEBE consultar o reconciliar la operación original antes de permitir un efecto nuevo; no habrá reintento automático oculto.
- **FR-006**: Cambios de plan, perfil, destino o permisos DEBEN invalidar la aprobación afectada y mostrar la diferencia antes de actuar.
- **FR-007**: Cancelar DEBE mostrar solicitado, en curso y confirmado cuando corresponda; no fingir liberación de recursos ni trabajo terminado.
- **FR-008**: El borrador DEBE sobrevivir errores y navegación accidental; cambiar contexto requiere conservarlo con su contexto original o descartarlo explícitamente.
- **FR-009**: El resultado de una acción DEBE reflejarse en el grafo solo mediante el estado confirmado, con vínculo a su operación.
- **FR-010**: Las tareas históricas que requieren reconciliación DEBEN permanecer intactas al abrir Studio y no recibir acciones de cierre por una migración visual.

- **FR-011**: Reasignar DEBE invocar un contrato real que preserve tarea, historial e intentos y declare cómo se detiene o conserva el intento vigente. Si el backend no soporta el traslado solicitado, la acción debe declararse no disponible; cambiar el responsable en la interfaz no transfiere trabajo.

### Key Entities

- **Borrador de tarea**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Plan revisado**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Operación de comando**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Recibo y resultado**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cero efectos de ejecución antes de la confirmación del comando correspondiente.
- **SC-002**: Cada operación confirmada conserva su recibo y una única identidad bajo reintentos.
- **SC-003**: Todas las acciones sin capacidad o autorización se rechazan también por acceso directo.
- **SC-004**: Abrir o actualizar Studio no modifica jobs históricos de reconciliación.

## Assumptions

- Entrega el recorrido visual de contratos existentes; no reemplaza Work ni crea un scheduler por el hecho de dibujar tareas.
- Specs previas relacionadas: [019](../019-frontend-studio/spec.md), [020](../020-speckit-orchestration/spec.md), [024](../024-work-agent-messages/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: [028](../028-team-delegation-authority/spec.md), [029](../029-execution-graph-provenance/spec.md), [032](../032-graph-work-dependencies/spec.md)
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
