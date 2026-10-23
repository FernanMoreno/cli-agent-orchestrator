# Feature Specification: Trabajo, bloqueos, fases y ruta crítica verificables

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/032-graph-work-dependencies`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — que depende, bloqueada, hecho y ruta crítica describan restricciones y resultados reales del trabajo (Priority: P1)

Como operador quiero que depende, bloqueada, hecho y ruta crítica describan restricciones y resultados reales del trabajo.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** A precede obligatoriamente a B y A no está aceptada, **When** se intenta iniciar B, **Then** el motor rechaza o retiene B y el grafo muestra la causa A.
2. **Given** un turno de A termina sin resultado aceptado, **When** se actualiza el resumen, **Then** A no aumenta hechas hoy ni libera B.
3. **Given** un grafo sin duraciones fiables, **When** se solicita ruta crítica, **Then** se ofrece un camino de bloqueo rotulado correctamente o se explica que no se puede calcular.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** un turno de A termina sin resultado aceptado, **When** se actualiza el resumen, **Then** A no aumenta hechas hoy ni libera B.
2. **Given** un grafo sin duraciones fiables, **When** se solicita ruta crítica, **Then** se ofrece un camino de bloqueo rotulado correctamente o se explica que no se puede calcular.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Múltiples dependencias y preguntas a la vez.
- Cambio de plan con un intento activo.
- Resultado de una revisión antigua.
- Medianoche y zonas horarias distintas.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Cada dependencia DEBE proceder de una relación registrada de tareas o pasos, con condición de satisfacción y revisión; el orden visual no crea dependencias.
- **FR-002**: El motor DEBE impedir iniciar trabajo cuya dependencia obligatoria no esté satisfecha según su contrato, además de mostrar el bloqueo.
- **FR-003**: La vista DEBE separar estado de tarea, fase de intento, estado del proceso y resultado aceptado; un turno terminado no equivale a trabajo aceptado.
- **FR-004**: Las fases Planificar, Construir, Probar y Entregar DEBEN provenir de una definición de trabajo; si no existe, se mostrará fase sin definir.
- **FR-005**: La columna AHORA DEBE representar actividad observada y fechada; no se fijará una fase global para agentes que ejecutan fases distintas.
- **FR-006**: Cada bloqueo DEBE enlazar su causa confirmada: tarea, pregunta, capacidad, recursos o reconciliación; se conservarán múltiples causas simultáneas.
- **FR-007**: Los ciclos DEBEN detectarse solo sobre dependencias registradas, y bloquear admisión incompatible; las comunicaciones bidireccionales no son ciclos de dependencia.
- **FR-008**: Una ruta crítica temporal DEBE declarar objetivo, revisión, duraciones y método; sin duraciones fiables se ofrecerá camino de dependencias o bloqueo con ese nombre.
- **FR-009**: Cuando falte cobertura DEBE mostrarse ruta parcial o no calculable; nunca se declarará una ruta crítica global a partir de una vecindad local.
- **FR-010**: Reintentos, fallos y cambios de aceptación DEBEN recalcular restricciones sin duplicar tareas ni reutilizar un resultado de otra revisión.
- **FR-011**: Hechas hoy DEBE contar resultados aceptados únicos en una zona horaria declarada, no procesos o turnos terminados.

### Key Entities

- **Dependencia de trabajo versionada**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Causa de bloqueo**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Fase declarada**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Resultado aceptado**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Camino de bloqueo o ruta crítica**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cero inicios de dependientes obligatorios antes de satisfacer sus condiciones en pruebas de integración.
- **SC-002**: El 100 % de estados hecho tiene resultado aceptado de la revisión aplicable.
- **SC-003**: Los ciclos registrados se detectan sin clasificar comunicación circular como dependencia.
- **SC-004**: Cada ruta y contador se reproduce desde relaciones y resultados autoritativos de su revisión.

## Assumptions

- Define el significado común y su cumplimiento, reutilizando Beads y Work. No habilita modos de workflow que el motor reserve o no soporte.
- Specs previas relacionadas: [017](../017-live-execution-graph/spec.md), [019](../019-frontend-studio/spec.md), [020](../020-speckit-orchestration/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: [029](../029-execution-graph-provenance/spec.md)
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
