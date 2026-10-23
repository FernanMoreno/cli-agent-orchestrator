# Feature Specification: Perfiles, prompts, conocimiento y skills efectivos

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/038-studio-agent-resources`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — que los recursos mostrados para un agente sean los que realmente utilizó y que las áreas de biblioteca sean funcionales (Priority: P1)

Como operador quiero que los recursos mostrados para un agente sean los que realmente utilizó y que las áreas de biblioteca sean funcionales.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** un perfil editado después de lanzar un agente, **When** inspecciono ambos, **Then** distingo revisión guardada de revisión utilizada.
2. **Given** una herramienta instalada pero denegada por el servidor, **When** abro las capacidades del agente, **Then** no aparece como operación permitida.
3. **Given** un perfil con campos avanzados y secretos referenciados, **When** lo duplico, **Then** conservo su semántica sin copiar secretos ni reemplazar referencias por texto redactado.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** una herramienta instalada pero denegada por el servidor, **When** abro las capacidades del agente, **Then** no aparece como operación permitida.
2. **Given** un perfil con campos avanzados y secretos referenciados, **When** lo duplico, **Then** conservo su semántica sin copiar secretos ni reemplazar referencias por texto redactado.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Skill retirada con agente activo.
- Modelo no soportado.
- Contenido privado de memoria.
- Perfil eliminado con ejecución histórica.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El detalle DEBE distinguir perfil guardado, revisión usada al lanzar y configuración efectiva confirmada del agente.
- **FR-002**: Editar un perfil DEBE declarar efecto sobre futuros lanzamientos; no se mostrará aplicado al proceso activo sin mecanismo y confirmación explícitos.
- **FR-003**: Prompts reutilizables, tarea inicial y recursos DEBEN distinguirse; el mapa ambiental solo mostrará sus metadatos autorizados.
- **FR-004**: La biblioteca DEBE permitir abrir sus destinos reales o explicar capacidad no disponible; no bastará un toast de navegación.
- **FR-005**: Skills y herramientas DEBEN distinguir instaladas, configuradas, ofrecidas y autorizadas para cada operación y proveedor.
- **FR-006**: Las referencias de conocimiento DEBEN conservar procedencia y revisión; una relación temática no atribuirá ejecución, dependencia ni autoridad.
- **FR-007**: Guardar o duplicar DEBE conservar campos avanzados y secretos por referencia sin persistir valores redactados como si fueran originales.
- **FR-008**: Lanzar un agente DEBE confirmar perfil, proveedor, modelo, destino y tarea inicial; un fallo incierto no provocará otro lanzamiento implícito.
- **FR-009**: El inspector DEBE señalar capacidades desconocidas o incompatibles con ese proveedor, sin ampliar permisos para hacer coincidir la maqueta.
- **FR-010**: Las pruebas DEBEN comprobar la configuración realmente recibida por el proceso además de la serialización del formulario.

### Key Entities

- **Revisión de perfil**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Configuración efectiva de lanzamiento**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Recurso de conocimiento**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Skill o herramienta disponible**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cada agente de aceptación muestra la revisión efectiva contrastada con su lanzamiento.
- **SC-002**: Cero permisos inferidos a partir de instalación, nombre o presencia de un campo.
- **SC-003**: Todas las entradas de biblioteca tienen destino funcional o indisponibilidad explicada.
- **SC-004**: Los canarios de secretos no aparecen en resúmenes, exportaciones o copias de perfil.

## Assumptions

- Delta de integración sobre 019 y 025; no declara nuevas herramientas autorizadas ni sustituye el grafo de conocimiento.
- Specs previas relacionadas: [013](../013-reproducible-plugin-trust/spec.md), [019](../019-frontend-studio/spec.md), [025](../025-provider-mcp-profiles/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: [029](../029-execution-graph-provenance/spec.md)
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
