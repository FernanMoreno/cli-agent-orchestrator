# Feature Specification: Equipos, subequipos y pertenencia reales

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/027-agent-team-topology`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — que Equipo, Subequipo y Célula correspondan a grupos registrados que organizan el trabajo real (Priority: P1)

Como operador quiero que Equipo, Subequipo y Célula correspondan a grupos registrados que organizan el trabajo real.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** dos equipos registrados con subequipos y agentes sin equipo, **When** consulto el grafo, **Then** la contención coincide con las pertenencias y el agente huérfano permanece identificado.
2. **Given** un agente ejecutando trabajo en un equipo, **When** se cambia su pertenencia, **Then** el intento conserva su vinculación histórica y no cambia de ejecutor implícitamente.
3. **Given** dos ediciones concurrentes del mismo grupo, **When** ambas intentan guardar, **Then** solo la revisión válida se acepta y la otra explica el conflicto.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** un agente ejecutando trabajo en un equipo, **When** se cambia su pertenencia, **Then** el intento conserva su vinculación histórica y no cambia de ejecutor implícitamente.
2. **Given** dos ediciones concurrentes del mismo grupo, **When** ambas intentan guardar, **Then** solo la revisión válida se acepta y la otra explica el conflicto.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Equipo vacío o archivado con intentos históricos.
- Responsable eliminado o desconectado.
- Profundidad superior a los niveles de la maqueta.
- Mismo nombre en proyectos diferentes.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El sistema DEBE registrar grupos identificados, nombre, proyecto, relaciones de contención, revisión y estado de ciclo de vida; los nombres no serán identidad.
- **FR-002**: Los niveles DEBEN poder crecer con la organización sin fijar un máximo funcional de tres niveles; los límites operativos deben ser explícitos y verificables.
- **FR-003**: La pertenencia DEBE ser explícita y versionada conforme a la cardinalidad declarada por la fuente organizativa. Cuando admita múltiples pertenencias o padres, el grafo mostrará referencias de la misma identidad, distinguirá pertenencia de colaboración y deduplicará los totales; no impondrá un único padre por conveniencia visual.
- **FR-004**: El sistema DEBE rechazar ciclos de contención, padres de otro proyecto y cambios sobre una revisión obsoleta.
- **FR-005**: El responsable DEBE designarse explícitamente y distinguirse del primer agente, del creador del terminal y de la autoridad efectiva para actuar.
- **FR-006**: La organización DEBE conservarse tras reiniciar la API y permitir inspeccionar la pertenencia que estaba vigente durante un intento.
- **FR-007**: Los agentes sin pertenencia registrada DEBEN aparecer como sin equipo conocido, sin deducirlo del nombre, índice, perfil, directorio o máquina.
- **FR-008**: Cambiar la presentación por proyecto o estado DEBE crear una agrupación visual, sin modificar pertenencias ni asignaciones reales.
- **FR-009**: Mover un agente o archivar un grupo DEBE declarar el efecto sobre trabajo activo; en la primera entrega no transferirá automáticamente intentos, permisos ni resultados.
- **FR-010**: La jerarquía DEBE servir de referencia para las asignaciones de 028; una partición creada únicamente para paginar deberá rotularse como página o agrupación visual.

- **FR-011**: Las agrupaciones opacas ya existentes DEBEN conservar su semántica. Solo podrán representar equipos cuando su productor declare el esquema, la autoridad y la revisión; la adopción no debe inventar líderes ni migrar pertenencias silenciosamente.

### Key Entities

- **Equipo con identidad estable y proyecto**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Pertenencia versionada de agente o subequipo**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Designación de responsable**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Revisión de topología**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El 100 % de las pertenencias visibles se reconstruye desde registros persistentes tras reinicio.
- **SC-002**: Cero ciclos o cruces de proyecto admitidos en los casos negativos.
- **SC-003**: Renombrar o mover un grupo no cambia las identidades de agentes ni intenta ejecutar trabajo.
- **SC-004**: Se navega una jerarquía de seis niveles sin convertir el límite de dibujo en límite organizativo.

- **SC-005**: En un caso con pertenencia múltiple declarada, el mismo agente conserva una identidad y el total de agentes únicos no se duplica.

## Assumptions

- Propietaria de contención y pertenencias. No impone una jerarquía de mando por apariencia ni sustituye sesiones, workers, worktrees o proyectos.
- Specs previas relacionadas: [017](../017-live-execution-graph/spec.md), [018](../018-multi-instance-execution/spec.md), [019](../019-frontend-studio/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: No requiere otra spec nueva para definir su contrato; consume las capacidades existentes autorizadas.
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
