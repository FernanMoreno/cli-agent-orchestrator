# Feature Specification: Grafo de ejecuciones en vivo de CAOS

**Feature Branch**: `main` — documentación creada sin cambiar de rama.

**Created**: 2026-10-09

**Status**: Draft — preparada para planificación; grafo propuesto, pendiente de implementación.

**Input**: User description: «se pueede desarrollar una grafo visual de todo el flujo caos? donde cada agente es una arista y demas», aclarado como «mapa de ejecuciones en vivo».

**Base de evidencia**: [Análisis de fuentes y composición](research.md).

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Entender una ejecución en vivo (Priority: P1)

El operador abre una ejecución y reconoce los agentes participantes, sus tareas y cómo se relacionan. Los agentes se representan como nodos y las delegaciones, comunicaciones y esperas como aristas dirigidas y etiquetadas.

**Why this priority**: Permite responder quién está haciendo qué y de quién depende el avance.

**Independent Test**: Una ejecución con supervisor, dos trabajadores y una dependencia conocida muestra únicamente esas relaciones y actualiza sus estados.

**Acceptance Scenarios**:

1. **Given** una ejecución visible con un supervisor y dos trabajadores, **When** se abre el mapa, **Then** aparecen los participantes y las delegaciones registradas con origen, destino y tipo.
2. **Given** un agente que participa en varias tareas, **When** se selecciona una ejecución, **Then** se ven las tareas de ese ámbito sin atribuirle tareas de otra ejecución.
3. **Given** una tarea con resultado aceptado y terminal todavía activo, **When** se actualiza el mapa, **Then** la tarea aparece completada y el agente mantiene su estado de proceso independiente.
4. **Given** un terminal legado sin asociación demostrable a una ejecución, **When** se abre la vista global, **Then** puede aparecer como agente sin ejecución conocida, sin inventar una delegación.

### User Story 2 — Investigar agentes y conexiones (Priority: P1)

El operador selecciona un nodo o una arista para conocer su identidad, contexto, tarea, estado y evidencia de la relación. Puede abrir las vistas existentes de tarea, historial y terminal cuando tiene permisos.

**Why this priority**: Convierte el mapa en una herramienta para investigar lo que ocurre.

**Independent Test**: Seleccionar un agente, una delegación y un mensaje muestra detalles correspondientes y permite navegar a los recursos autorizados.

**Acceptance Scenarios**:

1. **Given** un agente seleccionado, **When** se abre su detalle, **Then** se muestran proveedor, perfil cuando exista, ubicación disponible, identificador, tareas visibles, estado de proceso y antigüedad de la observación.
2. **Given** una arista seleccionada, **When** se abre su detalle, **Then** aparecen tipo, dirección, identidad de la relación, tarea o mensaje asociado y procedencia del dato.
3. **Given** un mensaje entregado cuya tarea no ha terminado, **When** se inspecciona, **Then** entrega y finalización permanecen diferenciadas.
4. **Given** un recurso eliminado o un terminal remoto que no permite apertura desde esta vista, **When** se intenta navegar, **Then** se explica su disponibilidad sin sustituirlo por otro recurso ni iniciar acciones de ejecución.

### User Story 3 — Encontrar esperas y preguntas (Priority: P1)

El operador localiza agentes que esperan una respuesta, ramas bloqueadas por una dependencia y tareas que requieren reconciliación. Identifica al destinatario de una pregunta cuando ese dato existe.

**Why this priority**: Hace visible qué intervención permite avanzar.

**Independent Test**: Una pregunta obligatoria en un trabajador destaca su espera y mantiene el otro trabajador independiente en ejecución.

**Acceptance Scenarios**:

1. **Given** una pregunta estructurada pendiente, **When** se representa, **Then** se identifica el agente que pregunta, la tarea afectada, el destinatario autorizado y la espera dependiente; puede abrirse la interacción correspondiente.
2. **Given** solo un estado nativo de espera sin pregunta estructurada, **When** se representa, **Then** se indica «espera de respuesta; detalle no disponible» y se ofrece inspección autorizada del terminal sin inventar opciones.
3. **Given** una respuesta opcional pendiente, **When** la tarea continúa, **Then** se distingue de un bloqueo obligatorio.
4. **Given** una entrega incierta o un ciclo de dependencias registradas, **When** se observa, **Then** se muestra reconciliación o el ciclo confirmado, sin reintentar ni resolver automáticamente desde el mapa.

### User Story 4 — Confiar en la actualización y recuperación (Priority: P1)

El operador sabe cuándo los datos están actualizados, cuándo se ha interrumpido el seguimiento y qué información falta. Recuperar la conexión restaura el estado observable sin duplicar relaciones.

**Why this priority**: Una visualización desactualizada puede inducir decisiones incorrectas.

**Independent Test**: Interrumpir la conexión, producir eventos, repetir uno y reconectar converge al estado autoritativo o declara el tramo desconocido.

**Acceptance Scenarios**:

1. **Given** seguimiento conectado, **When** cambia un estado confirmado, **Then** se actualiza su elemento sin desplazar la selección o el encuadre del operador.
2. **Given** conexión interrumpida, **When** cesan las actualizaciones, **Then** el mapa conserva la última observación con aviso de desconexión y no muestra «en vivo».
3. **Given** eventos duplicados o anteriores a la revisión visible, **When** se reciben, **Then** no duplican elementos ni revierten estados más recientes.
4. **Given** un hueco declarado o historial agotado por retención, **When** se recupera, **Then** el estado actual puede restablecerse, pero el tramo sin evidencia permanece identificado como incompleto.
5. **Given** un cambio de nodo servidor, ejecución o permisos mientras llega una respuesta anterior, **When** llega el dato antiguo, **Then** no se incorpora al ámbito nuevo; los datos revocados dejan de mostrarse.
6. **Given** varios orígenes sin orden global compartido, **When** sus datos llegan en distinto orden, **Then** se conserva su orden local y no se declara una causalidad entre ellos sin evidencia.

### User Story 5 — Explorar el conjunto sin perderse (Priority: P2)

El operador alterna entre una ejecución y el conjunto visible, filtra agentes y relaciones, busca un identificador y concentra la vista en una rama. Los estados también se entienden sin depender del color.

**Why this priority**: Mantiene útil el mapa cuando crece el número de agentes.

**Independent Test**: Con 100 agentes y 500 relaciones se puede localizar uno, inspeccionarlo y seguir su rama conservando el resto resumido.

**Acceptance Scenarios**:

1. **Given** varias ejecuciones visibles, **When** se abre el conjunto, **Then** se agrupan por ejecución y los elementos compartidos conservan identidad y ámbito de cada relación.
2. **Given** filtros activos, **When** ocultan elementos, **Then** se indica el número oculto y los límites de la rama visible; los filtros no alteran el trabajo real.
3. **Given** navegación por teclado o movimiento reducido, **When** se explora, **Then** se pueden seleccionar agentes y relaciones, leer estados textuales y usar una lista equivalente sin animaciones obligatorias.
4. **Given** volumen superior al objetivo inicial, **When** se resume o limita la vista, **Then** se declara la cobertura y no se presenta la selección visible como todo el sistema.

### User Story 6 — Consultar el historial disponible (Priority: P3)

El operador retrocede a un punto del historial registrado y compara cómo estaba la ejecución. El modo histórico se distingue de la observación en vivo.

**Why this priority**: Ayuda a investigar una ejecución sin exigir esta capacidad para entregar el primer mapa útil.

**Independent Test**: Para una ejecución con eventos conservados, elegir dos posiciones reproduce sus estados conocidos y volver al presente recupera el seguimiento.

**Acceptance Scenarios**:

1. **Given** historial conservado, **When** se elige una posición, **Then** aparecen solo estados y relaciones demostrables hasta ella, con marca de modo histórico.
2. **Given** estados de proceso sin registro histórico o un hueco, **When** se reproduce ese periodo, **Then** se indica desconocido; no se copia el estado actual al pasado.
3. **Given** una ejecución que avanza durante la consulta histórica, **When** se vuelve al presente, **Then** se muestra el estado actual y se distingue el tiempo de observación del tiempo registrado del evento.

### Edge Cases

- Agente reiniciado, identificador de sesión reutilizado o varios intentos sobre una tarea: conservar sus identidades e incarnaciones sin fusionarlos.
- Supervisor terminado y trabajador aún activo: mantener la relación probada y señalar disponibilidad del supervisor.
- Agente en varias ejecuciones, delegaciones paralelas, comunicaciones recíprocas y varias aristas entre el mismo par: conservar dirección, tipo y ámbito.
- Sin agentes o sin ejecuciones: estado vacío explícito; nunca equivalente a error de carga.
- Creación o eliminación durante la carga inicial: reconciliar con el seguimiento posterior sin duplicar ni resucitar recursos.
- Origen remoto inaccesible y relojes distintos: observación desactualizada por origen, sin inferir orden causal con marcas de tiempo.
- Revocación de acceso, cierre de sesión o cambio de servidor: descartar datos y suscripciones del ámbito anterior.
- Saturación o pérdida del canal: recuperar desde la fuente cuando sea posible y declarar la incertidumbre restante.
- Relaciones sugeridas únicamente por texto o metadata libre del agente: no tratarlas como autoridad de delegación.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El sistema DEBE ofrecer en la interfaz web un mapa de una ejecución y una vista del conjunto autorizado, con ámbito visible.
- **FR-002**: Cada instancia de agente DEBE representarse como nodo con identidad estable y distinción de reincarnaciones disponibles.
- **FR-003**: Las aristas DEBEN ser dirigidas y distinguir delegación, comunicación, ejecución de tarea y espera; solo se muestran tipos con evidencia disponible.
- **FR-004**: Una relación DEBE conservar identidad, ámbito y fuente, permitiendo varias relaciones del mismo tipo entre un par sin confundir tareas o mensajes.
- **FR-005**: El sistema DEBE mostrar proveedor, perfil disponible y estado de proceso del agente, diferenciados del estado de tarea, intento, entrega y resultado aceptado.
- **FR-006**: El mapa DEBE incluir las dependencias registradas de tareas; ningún cambio de terminal por sí solo demuestra finalización del trabajo.
- **FR-007**: El sistema DEBE representar agentes sin asociación conocida como tales; no inferir pertenencia a una ejecución a partir del nombre de sesión.
- **FR-008**: Seleccionar un nodo DEBE mostrar sus datos y tareas visibles; seleccionar una arista, su dirección, tipo, fuente y recurso relacionado.
- **FR-009**: El operador DEBE poder navegar a las vistas autorizadas de terminal, tarea, mensaje e historial; la disponibilidad remota o eliminación debe explicarse.
- **FR-010**: Abrir, filtrar o reproducir el mapa DEBE ser una operación de observación y no iniciar, reenviar, cancelar ni completar tareas.
- **FR-011**: Las preguntas estructuradas DEBEN enlazarse al mecanismo de interacciones de la feature 016, respetando sus destinatarios, estados y permisos; el grafo no crea otra autoridad de respuesta.
- **FR-012**: Una espera de terminal sin pregunta estructurada DEBE mostrarse como observación limitada y permitir inspección autorizada, sin generar contenido u opciones.
- **FR-013**: El mapa DEBE distinguir pregunta opcional, espera obligatoria, dependencia de tarea y necesidad de reconciliación, con origen del bloqueo cuando se conozca.
- **FR-014**: Los ciclos DEBEN señalarse únicamente para dependencias registradas del ámbito visible; la comunicación bidireccional por sí sola no demuestra bloqueo.
- **FR-015**: Las observaciones DEBEN actualizarse durante el seguimiento con estado de conexión, antigüedad y cobertura por origen visibles.
- **FR-016**: Desconexión, pérdida de cobertura o fallo de carga DEBEN diferenciarse de ejecución vacía y de trabajo completado.
- **FR-017**: Recuperar el seguimiento DEBE reconciliar estado y relaciones con las fuentes autoritativas, sin pérdida silenciosa en la transición entre carga inicial y actualizaciones.
- **FR-018**: Los duplicados y actualizaciones antiguas DEBEN tolerarse sin duplicar elementos ni revertir revisiones más recientes de la misma entidad.
- **FR-019**: El mapa DEBE conservar orden por origen y relaciones causales explícitas; no asumir un orden global común ni usar solo relojes para deducir causalidad.
- **FR-020**: Los huecos e historial no conservado DEBEN declararse; reconstruir el presente no acredita la integridad del pasado.
- **FR-021**: Cambiar ejecución, servidor o identidad DEBE aislar las respuestas y suscripciones anteriores; una respuesta tardía no puede poblar el nuevo ámbito.
- **FR-022**: El sistema DEBE aplicar los permisos de cada recurso a nodos, conexiones, detalles, búsqueda e historial; una conexión visible no concede acceso a su otro extremo.
- **FR-023**: Ante revocación, el sistema DEBE dejar de servir y retirar los datos afectados de la vista; el mapa ambiental no debe incluir contenido de mensajes, prompts o terminales.
- **FR-024**: El operador DEBE poder buscar por identidad o nombre visible, filtrar por ejecución, proveedor, estado y tipo de relación, y enfocar una rama.
- **FR-025**: El mapa DEBE conservar selección, encuadre y posiciones existentes durante actualizaciones ordinarias; reorganizarse exige acción explícita o incorporación local de elementos nuevos.
- **FR-026**: Los filtros y resúmenes DEBEN declarar elementos ocultos y cobertura; superar la capacidad visible no debe aparentar ausencia de trabajo.
- **FR-027**: Estados y tipos de relación DEBEN tener texto o símbolos diferenciables sin color, navegación por teclado y una vista equivalente en lista; se respeta movimiento reducido.
- **FR-028**: Si se ofrece reproducción histórica, DEBE usar únicamente evidencia conservada y distinguir pasado, presente, huecos y estados desconocidos.
- **FR-029**: Los datos disponibles para terminales legados, Work, workflows y agentes remotos DEBEN declarar su cobertura; el mapa básico debe funcionar sin el subsistema opcional de presentación de agentes.
- **FR-030**: La incorporación del mapa DEBE preservar los contratos y vistas existentes; una falta de datos debe degradar la observación sin modificar ejecución o ampliar permisos.

### Key Entities *(include if feature involves data)*

- **Ámbito observable**: ejecución, trabajo o conjunto autorizado; identidad del servidor, lector y cobertura.
- **Instancia de agente**: identidad e incarnación disponible, proveedor, perfil, ubicación, estado observado y momento de observación.
- **Tarea e intento**: identidad, ejecución, relaciones de dependencia, agente asociado y estado autoritativo independiente del proceso.
- **Relación**: identidad, tipo, origen, destino, dirección, ámbito, fuente y recurso probatorio.
- **Interacción**: referencia a la pregunta o aprobación de la feature 016, destinatario y efecto de espera.
- **Observación**: estado/revisión por fuente, posición de seguimiento, conexión y límites de cobertura.
- **Posición histórica**: posición dentro del historial conservado de un origen; no presupone orden global.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: En una ejecución controlada con 20 agentes y 100 relaciones, el 95 % de los cambios confirmados se hacen visibles en menos de 2 segundos con seguimiento conectado; medición desde disponibilidad en la fuente hasta la vista.
- **SC-002**: Con 100 agentes y 500 relaciones autorizadas, el 95 % de aperturas del mapa se completa en menos de 3 segundos y el 95 % de selecciones de elementos en menos de 200 ms, en el entorno de referencia definido en planificación.
- **SC-003**: En escenarios de duplicación, reordenación por origen, desconexión y cambio de ámbito, el 100 % termina con estado igual a las fuentes o con incertidumbre explícita, sin relaciones duplicadas ni datos mezclados.
- **SC-004**: En los escenarios de autorización y revocación, no se expone ninguna identidad, relación o contenido fuera de los permisos del lector.
- **SC-005**: En una evaluación con al menos cinco operadores y ejecuciones preparadas, al menos el 80 % identifica en menos de 30 segundos quién ejecuta una tarea y cuál es su espera conocida.
- **SC-006**: Todos los recorridos de selección, inspección, búsqueda y cambio de ejecución se completan por teclado y los estados se identifican sin depender del color.
- **SC-007**: Todas las pruebas de observación y reproducción generan cero acciones de ejecución; toda consulta histórica con datos ausentes declara su límite sin inventar estados.

## Assumptions

- El usuario confirmó que quiere un mapa de ejecuciones en vivo. Las prioridades, métricas y diseño de interacción restantes son propuestas para planificar, no decisiones adicionales confirmadas por él.
- La primera entrega cubre historias 1–4 y accesibilidad básica; los filtros de escala y la reproducción completa pueden entregarse después según las prioridades indicadas.
- El grafo conceptual tiene agentes como nodos y relaciones como aristas; tareas y preguntas pueden mostrarse como elementos auxiliares al expandir un agente, manteniendo identidades separadas.
- La vista global comprende el conjunto accesible al servidor y al lector. No promete descubrimiento de todos los procesos externos ni conversaciones internas no expuestas por los proveedores.
- La feature 016 está especificada y aún no implementada. El mapa inicial puede mostrar espera de terminal; detalles y respuesta de preguntas dependen del canal estructurado futuro.
- El historial existente de workflows es reutilizable, pero la reproducción completa de relaciones legadas y estado de procesos requiere captura adicional. Nunca se presupone un registro universal ya existente.
- El objetivo inicial es interfaz web de escritorio. La planificación concreta definirá composición de datos, contrato de observación, recursos, retención y soporte remoto sin fijar una biblioteca de dibujo en esta especificación.
- Quedan fuera del alcance inicial edición de flujos, creación de conexiones para delegar, cancelaciones y ejecución desde el lienzo, así como un mapa del código fuente.
- Este cambio entrega documentación. Plan, tareas e implementación serán fases posteriores conforme al flujo del proyecto.
