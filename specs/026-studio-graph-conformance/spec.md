# Feature Specification: Conformidad entre la red real y CAOS Studio

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/026-studio-graph-conformance`

**Created**: 2026-10-23

**Status**: Draft — brecha especificada; implementación y aceptación de esta entrega pendientes.

**Input**: Evaluar cada detalle de la maqueta frente al código y especificar lo que falta, priorizando que el grafo y su división correspondan al trabajo real.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — que cada división, estado y conexión del grafo sea una representación comprobable de lo que CAOS está haciendo (Priority: P1)

Como operador quiero que cada división, estado y conexión del grafo sea una representación comprobable de lo que CAOS está haciendo.

**Why this priority**: La interfaz debe permitir tomar decisiones sobre hechos operativos comprobables, sin atribuir ejecución o autoridad a datos de demostración.

**Independent Test**: Preparar los registros y operaciones reales de los escenarios siguientes, consultar la vista como un operador autorizado y comparar sus afirmaciones con el estado de origen independiente. Mocks y datos sintéticos solo acreditan sus pruebas específicas.

**Acceptance Scenarios**:

1. **Given** una red real con dos equipos y una relación entre ellos, **When** recorro resumen, subgrupo, agente y detalle, **Then** las identidades, dirección, conteos y estados concuerdan con sus fuentes en cada nivel.
2. **Given** un proveedor con diagnóstico satisfactorio pero sin aceptación de esta revisión, **When** consulto cobertura, **Then** se distingue diagnóstico de aceptación y no se declara soporte completo.
3. **Given** una discrepancia entre proyección y estado autoritativo, **When** se ejecuta la comprobación de conformidad, **Then** el gate falla y conserva el primer límite discrepante.

### User Story 2 — Conservar verdad y contexto ante cambios (Priority: P1)

Como operador quiero que errores, concurrencia y cambios de acceso no conviertan información parcial en un éxito aparente.

**Why this priority**: La red cambia mientras se consulta y no puede conceder autoridad ni completar trabajo mediante su representación.

**Independent Test**: Repetir el caso principal con interrupción, actualización concurrente y acceso limitado; contrastar identidad, cobertura y efectos admitidos.

**Acceptance Scenarios**:

1. **Given** un proveedor con diagnóstico satisfactorio pero sin aceptación de esta revisión, **When** consulto cobertura, **Then** se distingue diagnóstico de aceptación y no se declara soporte completo.
2. **Given** una discrepancia entre proyección y estado autoritativo, **When** se ejecuta la comprobación de conformidad, **Then** el gate falla y conserva el primer límite discrepante.
3. **Given** un lector sin permiso sobre un recurso del escenario principal, **When** intenta consultarlo o actuar mediante enlace directo, **Then** el servidor conserva la denegación y la vista no revela contenido ni concede autoridad por su posición.

### Edge Cases

- Fuente ausente frente a conjunto realmente vacío.
- Cambios simultáneos durante la comparación.
- Registros antiguos cuyo terminal ya no existe.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Cada elemento y afirmación visible DEBE vincularse a identidad, fuente autoritativa, ámbito autorizado, revisión y momento de observación; los datos desconocidos deben declararse desconocidos.
- **FR-002**: El catálogo DEBE clasificar cada detalle como existente, integración pendiente, capacidad nueva o aceptación pendiente; una prueba sintética no cambia esa clasificación a aceptación real.
- **FR-003**: Equipos, autoridad, tareas, mensajes, esperas y resultados DEBEN conservar significados distintos en todas las vistas.
- **FR-004**: La red DEBE poder auditarse desde un resumen hasta los registros de origen permitidos sin introducir relaciones al navegar.
- **FR-005**: Las pruebas DEBEN confrontar el estado autoritativo y lo presentado después de cada transición, sin usar los propios datos calculados por la vista como único oráculo.
- **FR-006**: La conformidad DEBE incluir creación, asignación, entrega, ejecución, pregunta, respuesta, aceptación de resultado y limpieza, con identidades conservadas.
- **FR-007**: El cierre DEBE incluir fallos parciales, respuesta incierta, duplicados, reinicio y revocación, además del recorrido satisfactorio.
- **FR-008**: Cada capacidad DEBE declarar su alcance validado: proveedor, versión, perfil, topología, carga y revisión; los diagnósticos históricos no acreditan una revisión nueva.
- **FR-009**: La vista DEBE conservar explícitamente cobertura parcial, discrepancias y reconciliación; consultar el grafo no debe reejecutar ni cerrar trabajo.
- **FR-010**: El catálogo DEBE extender la matriz canónica de capacidades de 019 con todos los controles de la maqueta y sus destinos, usando referencias de propiedad en lugar de un catálogo competidor; el resto de Studio puede entregarse después de la fidelidad del grafo.

### Key Entities

- **Catálogo de afirmaciones visibles**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Registro de procedencia**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.
- **Recibo de conformidad por revisión**: entidad diferenciada con identidad y ámbito autorizados; su detalle se fija en el contrato antes de implementar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El 100 % de las filas del inventario tiene una spec responsable y una clasificación de evidencia.
- **SC-002**: Cero nodos, relaciones o privilegios fabricados en el corpus de aceptación.
- **SC-003**: Los recorridos reales declarados reproducen el 100 % de las transiciones esperadas y señalan cualquier divergencia.
- **SC-004**: Ningún fallo, omisión o diagnóstico se publica como aceptación final.

## Assumptions

- Esta spec gobierna la auditoría y el gate conjunto. Los contratos de producto pertenecen a 027–040 y a sus specs previas; no crea un segundo motor de ejecución.
- Specs previas relacionadas: [017](../017-live-execution-graph/spec.md), [018](../018-multi-instance-execution/spec.md), [019](../019-frontend-studio/spec.md), [020](../020-speckit-orchestration/spec.md). Se conserva su autoridad y sus gates abiertos; esta entrega concreta la brecha sin declararlos satisfechos.
- Dependencias nuevas: No requiere otra spec nueva para definir su contrato; consume las capacidades existentes autorizadas.
- No se promete capacidad infinita. Los resultados se acotan a carga, revisión, proveedor y topología realmente ensayados.
- El alcance de esta entrega actual es especificar y revisar documentos; no implementa sus requisitos. Evidencia y límites actuales en [evidence.md](evidence.md).
