# Feature Specification: Instancias reales y red distribuida observable

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/035-graph-instance-federation`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — ver dónde trabaja cada equipo y seguir conexiones entre instancias sin confundir máquinas, procesos y agentes (Priority: P1)

Como operador quiero ver dónde trabaja cada equipo y seguir conexiones entre instancias sin confundir máquinas, procesos y agentes.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** un equipo con miembros en dos instancias, **When** agrupo por equipo y después por instancia, **Then** se mantiene la pertenencia y cada ubicación real.
2. **Given** una instancia deja de responder, **When** actualizo todas las instancias, **Then** la cobertura se vuelve parcial sin declarar muertos a sus agentes.
3. **Given** un worker se reemplaza con el mismo nombre, **When** reviso su tarea anterior, **Then** se distinguen ambas encarnaciones y sus registros.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** una instancia deja de responder, **When** actualizo todas las instancias, **Then** la cobertura se vuelve parcial sin declarar muertos a sus agentes.
2. **Given** un worker se reemplaza con el mismo nombre, **When** reviso su tarea anterior, **Then** se distinguen ambas encarnaciones y sus registros.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Colisión de identificadores locales.
- Relojes no sincronizados.
- Un origen revocado y otro disponible.
- Lease expirado con proceso todavía vivo.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El inventario DEBE identificar servidor, host permitido, generación, backend, worker y agentes asociados sin equiparar esas entidades.
- **FR-002**: Todas las instancias DEBE significar todas las instancias autorizadas y consultadas del ámbito declarado; las desconocidas o inaccesibles deben quedar como cobertura incompleta.
- **FR-003**: Las relaciones entre instancias DEBEN vincular identidades de ambos orígenes y evidencia de entrega o asignación, no deducirse por nombre.
- **FR-004**: Un equipo DEBE poder tener agentes en varias instancias cuando la fuente organizativa confirme la misma identidad y las concesiones autoricen ese vínculo. Sin mapeo explícito se mantendrán grupos separados o pertenencia desconocida; compartir instancia o nombre no crea equipo.
- **FR-005**: Cada origen DEBE conservar su revisión y frescura. Se prohibirá presentar simultaneidad global sin un contrato que la sostenga.
- **FR-006**: La caída de un origen DEBE conservar última observación con su antigüedad y limitar las acciones; no convertir agentes en muertos o trabajo en fallido sin confirmación.
- **FR-007**: Reemplazar un worker DEBE generar la encarnación correspondiente y conservar el historial de sus intentos previos.
- **FR-008**: La capacidad y disponibilidad DEBEN proceder de inventario o admisión real; cantidades sintéticas no acreditan workers aprovisionados.
- **FR-009**: La federación DEBE controlar permisos y costos por origen, con paginación, concurrencia y tiempos de espera acotados.
- **FR-010**: Las lecturas del grafo DEBEN carecer de efectos de aprovisionar, liberar, cancelar o reintentar; cada acción operativa requiere el contrato específico existente.

- **FR-011**: Abrir terminal DEBE verificar destino, encarnación y autorización, y distinguir observación de envío de entrada. Abrir una vista no enviará comandos ni creará otra sesión; si no existe acceso remoto soportado, se explicará la limitación sin sustituir el terminal.

### Key Entities

- **Instancia observada**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Worker y lease**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Origen federado**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Cobertura por origen**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cero fusiones de agentes por coincidencias de nombre entre instancias.
- **SC-002**: Cada origen no accesible aparece como cobertura parcial con última observación disponible.
- **SC-003**: El 100 % de acciones dirigidas conserva destino y generación correctos.
- **SC-004**: Consultar la red federada no cambia inventario ni estado de ejecución.

## Assumptions

- Refina la observabilidad de 018 sin declararla implementada ni equiparar el registro local a descubrimiento remoto ilimitado.
- Specs previas relacionadas: [009](../009-local-cao-coordination/spec.md), [018](../018-multi-instance-execution/spec.md), [019](../019-frontend-studio/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: [029](../029-execution-graph-provenance/spec.md), [034](../034-graph-live-recovery/spec.md)
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
