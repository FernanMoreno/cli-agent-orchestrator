# Feature Specification: Integración de GitHub Spec Kit en CAOS

**Feature Directory**: `specs/020-speckit-orchestration`

**Created**: 2026-10-10

**Status**: Draft — preparada para planificación

**Input**: User description: "me parece perfecta, genera un specs para que CAOS use el spec kit". Flujo acordado: petición → especificación → plan → tareas → supervisor → agentes → revisión, conectado al nuevo front y al grafo.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Preparar un proyecto compatible (Priority: P1)

El operador prepara Spec Kit en un proyecto nuevo o existente y conoce las fases disponibles.

**Why this priority**: Permite iniciar el flujo conservando el trabajo existente.

**Independent Test**: Preparar un proyecto vacío y otro con constitución y plantillas propias.

**Acceptance Scenarios**:

1. **Given** proyecto autorizado, **When** se comprueba compatibilidad, **Then** se muestran versión, proveedor, fases soportadas y requisitos ausentes.
2. **Given** Spec Kit configurado, **When** se prepara la integración, **Then** se reutilizan documentos compatibles y se revisan cambios antes de aplicarlos.
3. **Given** constitución existente, **When** se inicia una feature, **Then** se identifica su revisión; modificarla es una acción explícita.
4. **Given** proveedor incompatible, **When** se solicita una fase, **Then** se explica el bloqueo y se puede elegir otro compatible sin perder el borrador.

### User Story 2 — Convertir una petición en especificación (Priority: P1)

El operador describe el objetivo y un agente ejecuta la fase de especificación; las aclaraciones continúan el mismo proceso.

**Why this priority**: Es la entrada del trabajo y permite resolver el alcance antes de delegarlo.

**Independent Test**: Generar una especificación con pregunta pendiente y responderla.

**Acceptance Scenarios**:

1. **Given** objetivo y proyecto, **When** se solicita especificación, **Then** se crea una feature identificada con petición original, progreso y responsable.
2. **Given** pregunta de alcance, **When** se necesita respuesta, **Then** se presenta al usuario; el supervisor responde solo dentro de autoridad delegada vigente.
3. **Given** documento pendiente de revisión, **When** se solicitan cambios, **Then** se produce otra revisión con comentarios e historial.
4. **Given** agente terminado sin artefacto válido, **When** se evalúa la fase, **Then** permanece incompleta con causa visible.

### User Story 3 — Revisar plan y tareas (Priority: P1)

El operador solicita plan y tareas, inspecciona requisitos, dependencias y agentes propuestos y autoriza un conjunto concreto.

**Why this priority**: Hace revisable la conexión entre planificación y ejecución.

**Independent Test**: Importar tareas, corregir una dependencia y aprobar sin despachos durante la preparación.

**Acceptance Scenarios**:

1. **Given** especificación disponible, **When** se generan plan y tareas, **Then** se identifican revisiones de entrada y salida y hallazgos de consistencia.
2. **Given** tareas preparadas, **When** se previsualizan, **Then** se ven identificadores, descripción, requisitos, dependencias, aceptación y agentes propuestos; la información ausente se señala.
3. **Given** ciclo o referencia ambigua, **When** se valida, **Then** se bloquea la ejecución hasta resolverlo sin inventar dependencias confirmadas.
4. **Given** conjunto revisado, **When** se aprueba, **Then** la decisión vincula documentos, tareas, agentes, destino, permisos y límites.
5. **Given** cambio relevante después de aprobar, **When** se solicita ejecutar, **Then** se invalida la autorización afectada y se presenta la diferencia.

### User Story 4 — Delegar con dependencias y límites (Priority: P1)

El supervisor asigna tareas aprobadas a perfiles adecuados con objetivos, contexto y aceptación concretos.

**Why this priority**: Completa el flujo como capacidad del producto.

**Independent Test**: Ejecutar tres tareas con una dependencia y observar su orden, asignación y resultados.

**Acceptance Scenarios**:

1. **Given** tarea aprobada y lista, **When** se delega, **Then** el agente recibe contexto autorizado, revisión, objetivo, aceptación y límites y queda vinculada su asignación.
2. **Given** dependencia pendiente o fallida, **When** se programa trabajo, **Then** la tarea dependiente queda bloqueada con motivo visible.
3. **Given** tareas propuestas en paralelo, **When** se decide su ejecución, **Then** se verifican capacidad, dependencias, recursos y concurrencia; se propone secuencia explícita o incompatibilidad si falta soporte.
4. **Given** perfil o permisos incompatibles, **When** se prepara asignación, **Then** se solicita resolución sin ampliar autoridad silenciosamente.
5. **Given** acuse de envío, **When** se actualiza estado, **Then** se distingue admisión, entrega, ejecución y resultado; el acuse no completa la tarea.

### User Story 5 — Recuperar, pausar y cambiar alcance (Priority: P1)

El operador controla el proceso y conserva identidades y revisiones a través de interrupciones y cambios.

**Why this priority**: El flujo atraviesa documentos, agentes y operaciones con tiempos de vida distintos.

**Independent Test**: Interrumpir una entrega, recuperar y editar el plan mientras una tarea está activa.

**Acceptance Scenarios**:

1. **Given** envío incierto, **When** se recupera o reintenta, **Then** se reconcilia la operación existente antes de crear otro intento.
2. **Given** pausa solicitada, **When** se aplica, **Then** cesan nuevos despachos y se muestra el tratamiento de agentes activos sin afirmar que han terminado.
3. **Given** cancelación, **When** quedan agentes o efectos pendientes, **Then** se distingue intención de cancelar de cese confirmado y se conservan incertidumbres.
4. **Given** dos features del mismo proyecto, **When** avanzan simultáneamente, **Then** no se cruzan selector, documentos ni preguntas; incompatibilidades obligan a aislamiento o serialización visible.
5. **Given** nuevo plan con tarea activa, **When** se adopta la revisión, **Then** el intento conserva su significado original y se decide continuar, cancelar o reconciliar sin modificarlo retroactivamente.

### User Story 6 — Revisar y alcanzar cierre verificable (Priority: P1)

El operador consulta evidencias, revisiones y correcciones hasta satisfacer criterios o alcanzar un límite que requiere decisión.

**Why this priority**: El fin de un turno no acredita cumplimiento del objetivo.

**Independent Test**: Revisar un resultado incompleto, corregirlo y repetir con límite de iteraciones agotado.

**Acceptance Scenarios**:

1. **Given** resultado de implementación, **When** se revisa, **Then** se comprueban criterios y evidencias; texto del agente y casillas marcadas no acreditan éxito por sí solos.
2. **Given** hallazgo de revisión, **When** se corrige, **Then** el nuevo trabajo se vincula al requisito e intento de origen conservando historial.
3. **Given** fase de convergencia disponible, **When** hay discrepancias, **Then** se muestran pendientes y se itera dentro de límites autorizados; agotarlos requiere decisión.
4. **Given** verificación omitida o fallida, **When** se prepara cierre, **Then** se muestra su alcance y queda parcial o bloqueado sin declararlo convergido.

### User Story 7 — Usar el front y el grafo (Priority: P2)

El operador navega por petición, documentos, tareas, agentes, preguntas, decisiones y resultados en un espacio de feature.

**Why this priority**: Integra el proceso en el rediseño y permite seguirlo en vivo.

**Independent Test**: Navegar desde un requisito hasta tarea y agente, responder una pregunta y revisar el resultado.

**Acceptance Scenarios**:

1. **Given** feature abierta, **When** se consulta, **Then** aparecen fase, historial, documentos, pendientes y acciones permitidas con proyecto e instancia.
2. **Given** tarea del grafo, **When** se inspecciona, **Then** se siguen relaciones con requisitos, dependencias, asignaciones, preguntas y resultados autorizados.
3. **Given** instancia desconectada o acceso revocado, **When** se consulta el proceso, **Then** se indica pérdida de actualización o permiso y se evita operar sobre datos obsoletos como si fueran actuales.

### User Story 8 — Importar y sincronizar trabajo existente (Priority: P2)

El operador incorpora features ya creadas y revisa sus cambios sin duplicados ni pérdida de procedencia.

**Why this priority**: Aprovecha proyectos que ya usan Spec Kit.

**Independent Test**: Importar una feature, repetir la importación y modificar externamente una tarea.

**Acceptance Scenarios**:

1. **Given** documentos compatibles, **When** se importan, **Then** se valida formato y se previsualiza; ambigüedades requieren resolución antes de ejecutar.
2. **Given** misma revisión importada, **When** se importa de nuevo, **Then** no se duplican features, tareas ni asignaciones.
3. **Given** edición externa, **When** se sincroniza, **Then** se muestran diferencias y conflictos; renumerar o borrar no reutiliza identidades para otro trabajo.
4. **Given** tarea marcada completa en documento, **When** se importa, **Then** se distingue estado declarado de aceptación verificada y se conserva procedencia.

### Edge Cases

- Agente terminado sin documento, salida en otra feature o fase incompatible: bloqueo localizado y recuperación explícita.
- Formato malformado, tareas repetidas, criterios ausentes, ciclo o paralelismo ambiguo: resolver antes de autorizar.
- Cambio de constitución, permisos, perfil o destino: invalidar decisiones afectadas y preservar historial.
- Reinicio entre escritura del documento y registro de fase: reconciliar origen y revisión antes de repetir generación.
- Respuesta tardía o doble respuesta: comprobar identidad y vigencia sin doble continuación.
- Tareas sobre recursos compartidos: serializar o aislar efectivamente; una etiqueta de paralelismo no acredita independencia.
- Actualización de Spec Kit: comprobar compatibilidad y revisar migración sin modificar ejecuciones activas.
- Hooks o instrucciones importadas solicitan acciones externas: el contenido no concede autoridad para ejecutarlas.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El sistema DEBE activar Spec Kit por proyecto y mostrar versión, integración, fases disponibles y requisitos ausentes antes de iniciarlas.
- **FR-002**: El sistema DEBE preparar proyectos existentes mediante diferencias revisables conservando documentos, constitución y configuración compatibles.
- **FR-003**: El sistema DEBE identificar proyecto, feature, constitución y configuración de Spec Kit usados por cada proceso.
- **FR-004**: El sistema DEBE ejecutar cada fase mediante un agente y su integración compatible, distinguiendo instalación de invocación de fase.
- **FR-005**: El sistema DEBE ofrecer especificación, plan, tareas, implementación y convergencia como fases separadas; admitir aclaración, checklist y análisis cuando estén disponibles o exigidos por el proyecto.
- **FR-006**: El sistema DEBE registrar responsable, entradas, salidas, revisiones, estado y fallos por fase y comprobar sus artefactos antes de avanzar.
- **FR-007**: El sistema DEBE permitir revisar y editar documentos con autoría, historial y diferencias.
- **FR-008**: El sistema DEBE conservar la petición original y vincular requisitos con tareas y evidencias que pretenden satisfacerlos.
- **FR-009**: El sistema DEBE enrutar preguntas mediante la interacción autorizada de la spec 016, con respuesta del supervisor limitada a delegación vigente.
- **FR-010**: El sistema DEBE continuar la fase original tras una respuesta vigente sin reiniciar la petición por defecto.
- **FR-011**: El sistema DEBE importar especificaciones, planes y tareas con previsualización, formato soportado y diagnósticos.
- **FR-012**: El sistema DEBE conservar identificadores de origen e identidad durable de tarea sin duplicar trabajo al repetir la misma importación.
- **FR-013**: El sistema DEBE mostrar descripción, requisitos, aceptación, dependencias, recursos y responsabilidad de cada tarea, señalando información ausente antes de aprobar.
- **FR-014**: El sistema DEBE distinguir dependencias explícitas de inferidas y exigir resolver ambigüedades, identificadores repetidos y ciclos antes de ejecutar.
- **FR-015**: El sistema DEBE interpretar paralelismo como elegibilidad propuesta sujeta a capacidades reales, dependencias, conflictos y límites.
- **FR-016**: El sistema DEBE permitir corregir tareas, dependencias, perfiles e instrucciones antes de autorizar y mostrar la revisión que describe el conjunto resultante.
- **FR-017**: El sistema DEBE requerir revisión y autorización de ejecución por defecto; una política delegada debe identificar autoridad, alcance y límites.
- **FR-018**: El sistema DEBE vincular autorización con revisiones de documentos y tareas, agentes, destino, permisos y límites aplicables.
- **FR-019**: El sistema DEBE invalidar autorizaciones y evidencias afectadas por cambios relevantes, explicando impacto sin reiniciar trabajo activo automáticamente.
- **FR-020**: El sistema DEBE despachar solo tareas aprobadas con dependencias satisfechas y asignación compatible con capacidades y recursos.
- **FR-021**: El sistema DEBE entregar objetivo, contexto autorizado, revisión, criterios y límites al agente, distinguiendo prompt de perfil de mensaje de tarea.
- **FR-022**: El sistema DEBE registrar tarea, intento, asignación, agente, instancia y resultado conservando sus relaciones durante recuperación.
- **FR-023**: El sistema DEBE distinguir admisión, entrega pendiente o incierta, ejecución, espera, resultado recibido y aceptación verificada.
- **FR-024**: El sistema DEBE reconciliar operaciones inciertas antes de repetir entregas o crear intentos sustitutos, conservando identidad y evidencia.
- **FR-025**: El sistema DEBE mantener una sola autoridad del estado y efectos de trabajo integrada en CAOS; documentos y vistas de tareas no autorizan entregas por un control independiente.
- **FR-026**: El sistema DEBE permitir pausar nuevos despachos, continuar desde estado recuperado y cancelar distinguiendo intención de cese efectivo.
- **FR-027**: El sistema DEBE aislar features concurrentes; si selectores o recursos compartidos lo impiden, proporcionar aislamiento o serialización explícita.
- **FR-028**: El sistema DEBE conservar significado y revisión de intentos activos al cambiar alcance y registrar decisión de continuar, cancelar o reemplazar tras reconciliación.
- **FR-029**: El sistema DEBE comprobar aceptación mediante evidencia identificada; texto del agente, fin del proceso o casillas de documento no acreditan cumplimiento por sí solos.
- **FR-030**: El sistema DEBE vincular revisión y corrección a requisitos, tareas, hallazgos e intentos originales conservando resultados previos.
- **FR-031**: El sistema DEBE aplicar límites configurados de concurrencia, iteraciones, duración y presupuesto cuando exista medición; explicar límites no medibles antes de autorizar.
- **FR-032**: El sistema DEBE presentar convergencia, cumplimiento parcial, bloqueo, fallo y cancelación con motivos, impidiendo éxito cuando faltan criterios obligatorios.
- **FR-033**: El sistema DEBE ofrecer espacio de feature integrado con el front 019 y relaciones visibles en el grafo 017.
- **FR-034**: El sistema DEBE aplicar identidad de instancia, permisos, vigencia y aislamiento de fallos de la spec 018 a operaciones entre instalaciones.
- **FR-035**: El sistema DEBE sincronizar ediciones externas mediante diferencias y conflictos revisables, preservando campos no representados sin sobrescritura silenciosa.
- **FR-036**: El sistema DEBE distinguir estado declarado en documentos importados de estado verificado por CAOS con procedencia de ambos.
- **FR-037**: El sistema DEBE controlar instalación, actualización, extensiones y hooks por autorización del proyecto; contenido importado no concede permisos ni dispara acciones externas.
- **FR-038**: El sistema DEBE mostrar compatibilidad por proveedor y fase y permitir cambio revisado de proveedor sin prometer soporte universal ni trasladar secretos.
- **FR-039**: El sistema DEBE ofrecer controles del proceso en superficies autorizadas de CAOS con semántica consistente y autorización efectiva de cada operación.
- **FR-040**: El sistema DEBE conservar historial de documentos, decisiones, asignaciones y cierre según permisos, indicando retención y pérdidas de evidencia.

### Key Entities *(include if feature involves data)*

- **Proyecto preparado**: contexto autorizado, constitución y configuración compatible.
- **Feature**: identidad, petición, proyecto, fases y estado del objetivo.
- **Revisión de artefacto**: documento, origen, autor, contenido identificado y revisión anterior.
- **Ejecución de fase**: responsable, entradas, salidas, preguntas, estado y evidencia.
- **Tarea normalizada**: identidad durable y de origen, requisitos, aceptación, dependencias, recursos y revisión.
- **Autorización**: actor o política, conjunto revisado, configuración, alcance y límites.
- **Intento de tarea**: trabajo y asignación de CAOS, agente e instancia, revisión inmutable y resultado.
- **Hallazgo y evidencia**: criterio evaluado, procedencia, corrección y alcance de comprobación.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Todos los proyectos ya preparados del conjunto de aceptación conservan documentos y configuración; el 100 % de cambios propuestos se puede revisar antes de aplicarse.
- **SC-002**: Un operador completa petición → documentos → tareas revisadas → autorización → ejecución → revisión en un proyecto compatible sin editar configuración interna de CAOS.
- **SC-003**: El 100 % de tareas ejecutadas permite navegar hasta petición, requisito, revisión autorizada, agente, intento y evidencia de aceptación o causa de incompletitud.
- **SC-004**: Todos los casos de dependencia inválida, aprobación obsoleta y permiso insuficiente del conjunto de aceptación producen cero despachos no autorizados.
- **SC-005**: Diez importaciones de la misma revisión y diez recuperaciones de entrega interrumpida producen cero duplicados de tareas o intentos por reintento.
- **SC-006**: Dos features concurrentes completan preparación y recuperación con cero cruces de documentos, preguntas, autorizaciones o asignaciones.
- **SC-007**: El 100 % de casos de evidencia insuficiente, límite agotado y cancelación incierta muestra la causa y evita declarar cierre exitoso.
- **SC-008**: En una evaluación con al menos cinco operadores, cuatro encuentran fase actual, revisan una tarea y responden una pregunta en menos de tres minutos por recorrido, excluyendo generación del agente.

## Assumptions

- Se integra el desarrollo guiado por especificaciones de GitHub Spec Kit; evaluación de ideas y extensiones de reparación de bugs quedan fuera de esta primera feature.
- Se reutilizan fases y artefactos de la versión compatible instalada. CAOS coordina ejecución y trazabilidad; no atribuye a Spec Kit un planificador autónomo ni una garantía sobre documentos generados.
- La preparación de documentos no autoriza implementación. La ejecución requiere revisión por defecto o política delegada explícita.
- Las reglas de proyecto condicionan comprobaciones y cierre. Ninguna fase concede autorización para commits, publicaciones ni otras acciones externas.
- Una feature importada puede retomarse desde una fase compatible si sus entradas y decisiones siguen vigentes.
- El mínimo útil admite ejecución secuencial preservando dependencias. El paralelismo requiere soporte efectivo y revisión de recursos.
- La representación de tareas y su eventual proyección en Beads se resolverán en planificación, conservando una sola autoridad sobre ejecución y efectos.

## Scope & Dependencies

**Incluido**: preparación, fases con agentes, importación, revisión, tareas normalizadas, autorización, delegación, recuperación, convergencia y recorridos de front/grafo.

**Excluido**: nuevo motor general de workflows, soporte universal de versiones/proveedores, actualización sin revisión, edición automática de constitución, publicación o merge automático y nueva federación entre instancias.

**Dependencias**: interacción [016](../016-agent-interactions/spec.md), grafo [017](../017-live-execution-graph/spec.md), contexto entre instancias [018](../018-multi-instance-execution/spec.md) y front [019](../019-frontend-studio/spec.md). Su disponibilidad debe comprobarse: son capacidades especificadas; una entrega parcial no acredita integración completa.
