# Feature Specification: CAOS Studio — rediseño integral del frontend

**Feature Branch**: `main` — documentación sin cambio de rama.

**Created**: 2026-10-10

**Status**: Draft — preparado para planificación; nueva interfaz no implementada.

**Input**: Rediseñar todo el front para que sea moderno, llamativo, intuitivo y cómodo, aprovechando recursos de interfaces avanzadas creadas con IA; integrar grafo, workflows, agentes con prompts específicos y «todas las funcionalidades de caos».

**Evidencia y entregables**: [Investigación](research.md), [matriz de cobertura](capability-map.md), [dirección de diseño](DESIGN.md) y [prototipo conceptual](design-preview.html).

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Orientarse y encontrar cualquier función (Priority: P1)

El operador entra en un espacio de trabajo que explica qué está ocurriendo y qué requiere atención. Tiene navegación por objetivos, búsqueda de acciones y contexto visible de proyecto, instancia y permisos.

**Why this priority**: La interfaz debe permitir entender CAOS antes de exigir conocer sus nombres internos.

**Independent Test**: Un nuevo operador encuentra crear agente, ejecutar workflow, preguntas y estado del sistema sin consultar documentación técnica.

**Acceptance Scenarios**:

1. **Given** una instalación disponible, **When** se abre Inicio, **Then** aparecen ejecuciones activas, preguntas pendientes, fallos conocidos y acciones de creación basadas en datos autorizados.
2. **Given** una función de la matriz de cobertura, **When** se busca o navega a su área, **Then** existe un recorrido accionable o se explica su indisponibilidad por capacidad o permisos, con el siguiente paso aplicable.
3. **Given** proyecto e instancia seleccionados, **When** se abre un detalle, **Then** ese contexto permanece visible y puede volver al recorrido previo sin perder sus filtros.
4. **Given** acceso directo a una entidad, **When** se recarga o usa atrás/adelante, **Then** se restaura su ubicación autorizada o se informa de que ya no está disponible.

### User Story 2 — Crear agentes con instrucciones propias (Priority: P1)

El operador crea un perfil reutilizable y lanza un agente. Configura instrucciones de sistema, tarea inicial, proveedor, modelo soportado, habilidades, herramientas, recursos, permisos y destino sin confundir perfil con proceso en ejecución.

**Why this priority**: Es una necesidad explícita y el primer paso de muchos recorridos.

**Independent Test**: Crear un perfil de revisor con prompt específico, validarlo y lanzar una instancia con tarea inicial; comprobar identidad, configuración aceptada y resultado de arranque.

**Acceptance Scenarios**:

1. **Given** creación desde plantilla o vacío, **When** se editan las instrucciones, **Then** se distinguen instrucciones reutilizables, configuración y mensaje de la tarea; se puede previsualizar lo que se guardará.
2. **Given** proveedor seleccionado, **When** se configuran modelo, permisos, skills, herramientas o servidores MCP, **Then** solo se presentan valores disponibles o limitaciones explicadas; un campo visible no acredita soporte del proveedor.
3. **Given** documento inválido, **When** se valida o guarda, **Then** los errores apuntan a sus campos y el borrador permanece; las advertencias no se convierten en éxito ni en rechazo genérico.
4. **Given** perfil ya existente, **When** se duplica o edita, **Then** se utiliza la fuente original sin persistir secretos sustituidos y se conserva contenido avanzado que el formulario no representa.
5. **Given** configuración y tarea inicial revisadas, **When** se confirma lanzamiento, **Then** se identifica instancia, directorio/proyecto y sesión; la tarea se entrega una sola vez cuando el arranque lo permite o se declara pendiente/incertidumbre sin un segundo lanzamiento implícito.
6. **Given** edición de un perfil mientras un agente trabaja, **When** se guarda, **Then** se explica a qué futuros lanzamientos aplica; no se afirma que se haya modificado el proceso activo sin soporte explícito.

### User Story 3 — Diseñar y ejecutar workflows (Priority: P1)

El operador dispone de biblioteca de workflows, plantillas, edición guiada y vista de fuente. Define entradas y pasos con perfiles e instrucciones, valida, revisa el plan y ejecuta el documento aprobado.

**Why this priority**: Debe ser posible usar los workflows desde la interfaz, con un ciclo completo de autoría y ejecución.

**Independent Test**: Crear un workflow soportado de dos pasos, validar, revisar/aprobar cuando corresponda, ejecutar y consultar sus salidas.

**Acceptance Scenarios**:

1. **Given** una plantilla o workflow existente, **When** se abre el editor, **Then** se ven pasos, orden/dependencias soportadas, entradas, perfiles y prompts de cada paso.
2. **Given** edición visual y vista de fuente, **When** se cambia entre ellas, **Then** se conserva la semántica; campos no representables permanecen intactos y se explican las limitaciones de edición visual.
3. **Given** una conexión incompatible o modo reservado por el motor, **When** se valida, **Then** aparece una razón clara y no se ofrece ejecutar como si estuviera soportado.
4. **Given** plan preparado y aprobado, **When** cambian entradas, fuente, perfil o destino que afectan al plan, **Then** se invalida la revisión correspondiente y se prepara nuevamente antes de ejecutar.
5. **Given** respuesta de envío incierta, **When** el usuario intenta de nuevo, **Then** se consulta la identidad de ejecución o se solicita reconciliación antes de iniciar otro trabajo.
6. **Given** editor con cambios sin guardar, **When** se navega o llega una actualización externa, **Then** se conserva el borrador y se explica cualquier conflicto sin sobrescribirlo.

### User Story 4 — Seguir ejecuciones en grafo y detalle (Priority: P1)

El operador sigue el mapa vivo de 017 y el conjunto de instancias de 018. Alterna entre grafo, lista, historial, salidas y terminal de una ejecución sin perder contexto.

**Why this priority**: El grafo forma parte del mismo espacio de trabajo que los agentes y workflows.

**Independent Test**: Abrir una ejecución, seleccionar un trabajador y examinar tarea, pregunta y terminal; volver al grafo conservando selección y ámbito.

**Acceptance Scenarios**:

1. **Given** ejecución observada, **When** se abre el grafo, **Then** se distinguen agentes, delegaciones, tareas y esperas con sus fuentes y cobertura.
2. **Given** una tarea completada cuyo terminal sigue activo, **When** se inspecciona, **Then** no se confunde estado de proceso con resultado aceptado.
3. **Given** varias instancias, **When** se selecciona un agente o acción, **Then** se muestra su origen cualificado; un foco visual no cambia el destino de una acción pendiente.
4. **Given** desconexión o hueco histórico, **When** cambia el seguimiento, **Then** se conserva la observación con aviso de frescura e incertidumbre y se recupera según 017/018.
5. **Given** edición de un workflow y observación de una ejecución, **When** se cambia entre ellas, **Then** diseñar conexiones modifica solo el borrador, nunca el trabajo que ya está ejecutándose.

### User Story 5 — Atender preguntas y comunicaciones (Priority: P1)

El operador recibe una bandeja común de preguntas, aprobaciones y mensajes, vinculados a sus agentes y tareas. Contesta únicamente las interacciones en que tiene autoridad según 016.

**Why this priority**: El trabajo debe poder avanzar sin vigilar cada terminal.

**Independent Test**: Resolver una pregunta de selección y otra de texto, consultar una comunicación y localizar el agente correcto en el mapa.

**Acceptance Scenarios**:

1. **Given** varias preguntas estructuradas, **When** se abre la bandeja, **Then** se identifican origen, destinatario, tarea, obligatoriedad y vigencia y se muestran las opciones reales.
2. **Given** respuesta aceptada por el servidor pero aún sin entrega confirmada, **When** se consulta, **Then** se distingue enviada/aceptada/entregada de agente reanudado.
3. **Given** solo espera nativa de terminal, **When** se abre, **Then** aparece inspección disponible con límite de cobertura, sin inventar una pregunta.
4. **Given** un mensaje entre agentes, **When** se consulta o envía desde la vista autorizada, **Then** se distingue comunicación de una decisión que desbloquea una tarea.

### User Story 6 — Gestionar tareas y automatización (Priority: P1)

El operador gestiona tareas Beads, asignaciones, Work durable, automatizaciones programadas y ciclos de coordinación Ralph con su semántica propia, desde recorridos conectados.

**Why this priority**: La petición exige integrar la funcionalidad existente, incluida la ejecución y recuperación del trabajo.

**Independent Test**: Planificar/asignar una tarea, consultar su intento y resultado, revisar una incidencia de reconciliación y activar una programación existente.

**Acceptance Scenarios**:

1. **Given** workspace con tareas, **When** se crean, descomponen, priorizan o asignan, **Then** se conserva identidad y dependencias y se ve el estado de la operación y su ejecución asociada.
2. **Given** un flujo programado, **When** se configura o activa, **Then** se explican frecuencia, zona horaria efectiva, prompt, perfil y próximo disparo cuando exista; ejecutarlo ahora no crea una programación distinta.
3. **Given** un ciclo Ralph disponible, **When** se revisa su plan o estado, **Then** se ven sus iteraciones, feedback, presupuesto/límites disponibles y controles permitidos; el modo histórico no cambia su ejecución.
4. **Given** Work que requiere reconciliación o decisión, **When** se abre el detalle, **Then** se ofrece el recorrido autorizado sobre ese recurso; usar el terminal no sustituye su autoridad.
5. **Given** cancelación o limpieza parcial, **When** llega la respuesta, **Then** se distingue solicitado, cancelado y limpieza pendiente sin anunciar eliminación completa prematuramente.

### User Story 7 — Gestionar conocimiento y extensiones (Priority: P2)

El operador puede administrar memoria, conocimiento revisable, relaciones y vault, y configurar perfiles, skills, plugins y herramientas MCP en sus áreas correspondientes.

**Why this priority**: Son partes del producto que no pueden desaparecer durante el rediseño.

**Independent Test**: Localizar memoria y su relación, consultar revisión de conocimiento y vault, y configurar una skill/plugin disponible respetando permisos y cobertura.

**Acceptance Scenarios**:

1. **Given** memoria autorizada, **When** se busca o abre su grafo, **Then** se identifican alcance, tipo, relaciones y fuente; el grafo de conocimiento se diferencia del de ejecución.
2. **Given** revisiones de conocimiento con política específica, **When** se revisan o proponen, **Then** se conserva su versión, evidencia y autoridad; memoria legada no elude esa política.
3. **Given** una operación de importar, exportar, reparar, sincronizar o eliminar, **When** se revisa, **Then** se explica alcance y resultado, conflictos, acción pendiente y capacidad disponible.
4. **Given** plugin o skill condicionado por instalación/flags, **When** se abre su área, **Then** se ve su disponibilidad y configuración o un motivo accionable; no se desaparece silenciosamente del catálogo de funciones.
5. **Given** instalación de una extensión o modificación de herramientas, **When** se confirma, **Then** se distingue instalar, configurar y cargar en nuevos agentes; no se atribuye efecto inmediato a procesos activos sin soporte.

### User Story 8 — Administrar instancias, acceso y sistema (Priority: P2)

El operador administra conexiones, pairing local, runtimes, workers y configuración con destinos explícitos. Puede gestionar acceso de navegador y consultar salud, eventos y diagnósticos.

**Why this priority**: Completa el espacio operativo manteniendo los límites de acceso del producto.

**Independent Test**: Configurar una conexión permitida, consultar salud, revisar permisos, modificar una configuración admitida y descargar diagnóstico autorizado.

**Acceptance Scenarios**:

1. **Given** permiso de administración, **When** se configura una conexión, worker o pairing, **Then** se muestran su identidad, alcance concedido, modo de transporte y cobertura sin confianza transitiva.
2. **Given** lector sin permiso de escritura, **When** abre acciones administrativas, **Then** puede consultar lo autorizado y conoce por qué una acción no está disponible; acceder directamente tampoco permite ejecutarla.
3. **Given** configuración que exige reinicio, **When** se guarda, **Then** se distingue persistida de aplicada y se indica el proceso afectado sin reiniciarlo implícitamente.
4. **Given** sesión de navegador, **When** se cambia contraseña, renueva o revoca acceso, **Then** se preserva la autenticación existente y se retiran datos/subscripciones afectados.
5. **Given** administración de instalación/actualización sin contrato remoto disponible, **When** se consulta, **Then** se explican requisitos locales; una función operativa pendiente de contrato no se considera integrada por mostrar una terminal genérica.

### User Story 9 — Trabajar con comodidad y recuperar borradores (Priority: P1)

El operador usa un diseño consistente claro u oscuro, paneles adaptables, navegación por teclado y formularios con ayuda. Cambiar de vista no destruye el trabajo de autoría.

**Why this priority**: Corrige la falta de comodidad e intuición señalada por el usuario.

**Independent Test**: Crear un agente y editar un workflow con teclado, cambiar tema/vista, provocar un error de validación y recuperar el borrador.

**Acceptance Scenarios**:

1. **Given** tema claro u oscuro y movimiento reducido, **When** se navega, **Then** foco, errores, estados y texto son legibles y no dependen del color o animación.
2. **Given** pantalla estrecha o zoom de texto, **When** se abre un panel complejo, **Then** controles esenciales siguen accesibles y existe alternativa en lista para el lienzo.
3. **Given** borrador no guardado, **When** se cambia entidad, instancia o se cierra la sesión, **Then** se evita pérdida silenciosa y cualquier recuperación respeta lector/origen; contenido sensible no se conserva en almacenamiento inseguro.
4. **Given** formulario extenso, **When** se usa modo guiado o avanzado, **Then** se conservan los mismos datos y validaciones, y los detalles técnicos solo se muestran donde ayudan a decidir.

### User Story 10 — Completar la transición sin perder funciones (Priority: P1)

El operador migra a la nueva interfaz por etapas manteniendo sus datos, perfiles, workflows y controles. Cada función del inventario tiene dueño, pantalla y evidencia de aceptación.

**Why this priority**: «Todas las funcionalidades» debe ser una condición comprobable de cierre.

**Independent Test**: Revisar matriz de cobertura contra operaciones actuales y recorrer la equivalencia funcional de cada área; ninguna función operativa soportada queda solo en un mockup.

**Acceptance Scenarios**:

1. **Given** inventario de operaciones humanas actuales, **When** se evalúa el rediseño completo, **Then** cada una tiene pantalla, acción autorizada y escenario aceptado; los contratos nuevos necesarios figuran como trabajo pendiente.
2. **Given** función técnicamente interna de agente/runtime, **When** se documenta su cobertura, **Then** se representa su efecto mediante un recorrido humano adecuado; no se expone una llamada privilegiada genérica para fingir paridad.
3. **Given** entrega parcial del rediseño, **When** se comunica su estado, **Then** se enumeran áreas pendientes y las vistas operativas disponibles; no se declara cobertura total.
4. **Given** una fuente o configuración que cambia durante una operación, **When** responde una petición antigua, **Then** no modifica el contexto nuevo ni aplica éxito a otro recurso.

### Edge Cases

- Perfiles del proveedor o integrados que no se pueden editar: mostrar origen y permitir una copia cuando el contrato lo permita.
- Documento avanzado no representable, variables sin resolver, secretos de entorno, contenido Markdown/ANSI y plugin malformado: conservación sin ejecución arbitraria ni filtración de secretos.
- Arranque, guardado, submit y respuesta con timeout; aceptación parcial o doble clic: conservar identidad de operación e incertidumbre, no duplicar efectos.
- Workflow sintácticamente válido pero reservado; DAG visual que el motor solo admite secuencialmente: distinguir diseñable de ejecutable.
- Varias instancias con IDs repetidos, cambio de usuario y permisos revocados: descartar respuestas tardías y separar datos/borradores.
- Capacidad desactivada, backend caído, workspace sin configurar y lista vacía: mensajes diferentes con siguiente paso aplicable.
- Terminal compartido, tarea Work protegida, reconciliación y procesos hijos nativos: acciones según autoridad real, nunca botones que eluden el dueño de estado.
- Saturación de grafo, retención, fuente remota obsoleta y diagnóstico largo: límites/cobertura visibles y cancelación de lecturas sin cancelar trabajo.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El rediseño DEBE cubrir todas las capacidades humanas existentes de CAOS mediante una matriz de función, origen, pantalla, acción, permisos, estado y aceptación; funciones nuevas especificadas se etiquetan como pendientes hasta su implementación.
- **FR-002**: Ninguna función operativa soportada DEBE considerarse cubierta solo por una pantalla decorativa, documentación o acceso genérico a terminal; ausencia de contrato de gestión requiere planificar el contrato y su recorrido.
- **FR-003**: Inicio DEBE explicar actividad, esperas e incidencias autorizadas y ofrecer crear agente, workflow o tarea con contexto explícito.
- **FR-004**: La navegación DEBE organizarse por objetivos, con búsqueda de entidades/acciones, ubicación enlazable, historial de navegación y ayuda contextual.
- **FR-005**: Proyecto, instancia, identidad del lector y destino de acciones DEBEN ser visibles y mantenerse aislados en búsquedas, detalles, borradores y peticiones.
- **FR-006**: Agente en ejecución, perfil reutilizable e instrucciones de tarea DEBEN ser conceptos distintos en creación, edición y seguimiento.
- **FR-007**: Crear perfiles DEBE permitir partir de plantilla o vacío, editar instrucciones/configuración, previsualizar, validar, guardar y duplicar con identidad coherente.
- **FR-008**: Editar perfiles DEBE usar fuente sin sustitución de secretos y conservar campos avanzados no representados en formularios; los perfiles no editables deben declararse.
- **FR-009**: Los controles de modelo, permisos, herramientas, skills, recursos y MCP DEBEN reflejar capacidad/configuración real del proveedor y explicar su efecto sobre futuros o actuales procesos.
- **FR-010**: Lanzar un agente DEBE incluir destino y directorio/proyecto, perfil y tarea inicial, reflejando arranque y entrega confirmados o inciertos sin duplicarlos.
- **FR-011**: La biblioteca de workflows DEBE permitir descubrir, crear, importar fuente, editar, duplicar, validar, guardar y eliminar documentos según contratos y permisos.
- **FR-012**: La autoría visual y la fuente DEBEN conservar el mismo significado y campos no representables; los cambios visuales necesitan validación antes de persistencia o ejecución.
- **FR-013**: Los pasos DEBEN permitir configurar perfiles, instrucciones, entradas/salidas y orden o dependencias realmente soportadas; modos reservados deben identificarse y bloquearse para ejecución.
- **FR-014**: Preparar, revisar, aprobar y enviar un workflow DEBE operar sobre la versión y entradas revisadas; cambios relevantes invalidan su aprobación/preparación.
- **FR-015**: Las ejecuciones DEBEN integrar lista, detalle, resultados, grafo 017, conjunto 018, historial, comparación y diagnósticos conservando identidad y ámbito.
- **FR-016**: El lienzo de diseño DEBE separarse del mapa de ejecución: crear o mover relaciones en un borrador no modifica tareas vivas.
- **FR-017**: Estado de proceso, entrega, intento, trabajo, resultado, cancelación y limpieza DEBEN mostrarse separados cuando difieran; la UI no crea una autoridad alternativa.
- **FR-018**: Recuperación, retry, replay, cancelación y decisiones DEBEN utilizar el contrato autorizado del recurso, con alcance y efecto claros; incertidumbre no habilita reintento ciego.
- **FR-019**: La bandeja de interacciones DEBE integrar 016 con texto, selección simple/múltiple y aprobaciones soportadas, destinatario, vigencia y estado de entrega.
- **FR-020**: Espera nativa sin pregunta estructurada DEBE indicar cobertura limitada; comunicaciones entre agentes no se confunden con respuestas a decisiones.
- **FR-021**: Terminales DEBEN conservar inspección, salida, controles admitidos, sesiones, hijos y recuperación de turno disponibles, con protección de trabajo administrado y ubicación remota.
- **FR-022**: Tareas DEBEN integrar Beads, epics, dependencias, descomposición, operaciones, planes y asignaciones con su ejecución Work y estado de resultado.
- **FR-023**: Automatizaciones programadas DEBEN permitir configurar, activar, desactivar, ejecutar y eliminar flujos con frecuencia, zona horaria efectiva, instrucciones y perfil claros.
- **FR-024**: Coordinación Ralph DEBE presentar sus plantillas, planes, runs, iteraciones, feedback y controles admitidos, preservando la autoridad Work y sus límites.
- **FR-025**: Conocimiento DEBE integrar memoria legada autorizada, revisiones, evidencia, relaciones, vault, grafo de conocimiento y operaciones de archivo/reparación disponibles, con políticas y ámbitos separados.
- **FR-026**: Extensiones DEBEN integrar catálogo/configuración de skills, plugins de agentes, servidores MCP y plugins de eventos, distinguiendo instalación, disponibilidad y efecto sobre agentes.
- **FR-027**: Instancias DEBEN integrar Fleet, peers locales, runtimes y workers con sus contratos propios, capacidades y permisos; conexión de lectura no concede ejecución ni confianza transitiva.
- **FR-028**: Administración DEBE incluir autenticación de navegador, cuenta/sesiones, salud, configuración, directorios/entorno admitidos, eventos y diagnóstico con permisos efectivos y necesidades de reinicio.
- **FR-029**: Cada función DEBE mostrar disponibilidad, limitación por permisos/capacidad y siguiente paso; una capacidad no debe desaparecer silenciosamente del catálogo.
- **FR-030**: Borradores DEBEN sobrevivir a cambios ordinarios de vista y errores de validación; recuperación entre sesiones debe proteger contenido sensible y aislar lector/origen.
- **FR-031**: Peticiones y canales DEBEN cancelarse o aislarse al cambiar ámbito y evitar que respuestas tardías repueblen datos revocados o una entidad distinta.
- **FR-032**: Guardados, lanzamientos y acciones DEBEN prevenir doble envío y declarar aceptación, finalización e incertidumbre de efectos; conflictos concurrentes no sobrescriben borradores silenciosamente.
- **FR-033**: Lecturas en vivo DEBEN declarar frescura, desconexión, huecos y límites de cobertura conforme a 017/018, manteniendo encuadre y foco del usuario.
- **FR-034**: El diseño DEBE tener temas claro/oscuro, jerarquía consistente, densidad ajustable, paneles adaptables y estados de vacío/carga/error diferenciados.
- **FR-035**: Todos los recorridos principales DEBEN funcionar por teclado, con foco visible, etiquetas, errores asociados y alternativas al arrastre; estados no dependen del color y se respeta movimiento reducido.
- **FR-036**: El sistema DEBE mantener los controles esenciales accesibles en pantallas estrechas y zoom de texto; el grafo debe tener alternativa operable en lista.
- **FR-037**: La interfaz DEBE ofrecer lenguaje comprensible en español y un glosario contextual; nombres técnicos se mantienen cuando son necesarios para identificar recursos.
- **FR-038**: Texto de agentes, prompts, Markdown y terminales DEBE tratarse como contenido, sin ejecutar instrucciones de interfaz o ampliar permisos por contenido generado.
- **FR-039**: Material reutilizado o generado con IA DEBE registrar procedencia, licencia aplicable, compatibilidad y adaptaciones; popularidad de una plantilla no sustituye evaluación de usabilidad.
- **FR-040**: Prototipos y datos simulados DEBEN identificarse visiblemente; una maqueta no debe declarar estado en vivo ni acciones reales.
- **FR-041**: La migración DEBE conservar datos, contratos, perfiles, workflows y vistas operativas hasta completar su reemplazo; cada entrega declara cobertura y áreas pendientes.
- **FR-042**: Cierre de cobertura total DEBE reconciliar el inventario actualizado de UI, API, CLI y herramientas con la matriz y evidencias de aceptación por recorrido, sin exponer endpoints internos privilegiados como acciones humanas genéricas.

### Key Entities *(include if feature involves data)*

- **Espacio de trabajo**: contexto de proyecto, instancia(s), lector, navegación, permisos y capacidades disponibles.
- **Entrada de cobertura**: capacidad, fuente, operación, pantalla, autoridad, dependencia y evidencia de aceptación.
- **Perfil de agente**: fuente original, instrucciones, proveedor, configuración y validación; separado de instancia ejecutora.
- **Borrador de workflow/perfil**: contenido y versión base, campos avanzados, modificaciones, validación y política de recuperación.
- **Operación del usuario**: identidad de envío, destino, fase, resultado/incertidumbre y siguiente acción admitida.
- **Ejecución/Interacción/Instancia**: referencias a las entidades y autoridades de 016, 017 y 018.
- **Preferencias visuales**: tema, densidad, encuadre y disposición sin modificar el estado de ejecución.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El 100 % de operaciones humanas soportadas del inventario actualizado tiene pantalla, acción o indisponibilidad intrínseca justificada y evidencia de aceptación; una brecha de contrato pendiente impide declarar integración completa.
- **SC-002**: Con al menos cinco operadores nuevos, el 80 % crea un perfil con prompt propio y lanza un agente en menos de tres minutos, excluyendo espera de arranque externo y autenticación del proveedor.
- **SC-003**: En esa evaluación, el 80 % crea y ejecuta un workflow soportado de dos pasos en menos de cinco minutos, excluyendo ejecución de agentes, sin editar archivos externos.
- **SC-004**: El 80 % localiza en menos de 30 segundos una tarea bloqueada, su agente y el siguiente paso autorizado desde Inicio o el grafo.
- **SC-005**: Todos los recorridos principales definidos en la matriz pueden completarse por teclado en ambos temas; contraste, foco y adaptación se evalúan en los tamaños y niveles de zoom del plan.
- **SC-006**: Todos los escenarios de borradores, revocación, cambio de instancia, respuesta tardía, timeout y doble clic conservan datos autorizados sin duplicar efectos ni anunciar éxito incierto.
- **SC-007**: En el entorno de referencia con 100 agentes y 500 relaciones, el 95 % de cambios de vista con datos disponibles termina en menos de un segundo; el seguimiento conserva los objetivos de 017/018.
- **SC-008**: En evaluación antes/después con los mismos recorridos y al menos cinco operadores, la mediana de facilidad de uso aumenta al menos dos puntos en una escala de 1 a 7; se registran errores y funciones no encontradas.

## Assumptions

- El usuario exige rediseño integral e integración de todas las funcionalidades. El inventario no se limita a pestañas ya existentes: incluye recorridos humanos hoy accesibles por CLI/API y brechas de gestión pendientes.
- Las operaciones internas de agentes, autenticación de transportes y enforcement no se convierten en un explorador arbitrario de endpoints. Su efecto y configuración humana se representan manteniendo autoridad y contratos.
- El estilo propuesto por defecto es un estudio operativo con navegación lateral, lienzo central y detalle contextual; temas claro/oscuro y acento verde. La preferencia visual se consultó y sigue abierta si el usuario no responde; no bloquea investigación y spec.
- El código actual permite crear perfiles con instrucciones y gestionar workflows por API, pero el editor visual completo y los specs 016–018 todavía requieren implementación.
- El modo secuencial de workflows tiene recorrido de ejecución; modos paralelos/pipeline y loops mantienen seams reservados en la fuente inspeccionada. Rediseñar no implementa esos motores implícitamente.
- La selección tecnológica y las migraciones se definirán en plan. Recursos modernos pueden necesitar adaptación al stack actual; no se prescribe una migración completa por copiar una plantilla.
- IA puede acelerar exploración visual y generación de componentes; no se presume que todas las referencias estén íntegramente diseñadas por IA ni que métricas sociales acrediten calidad operativa.
- Se puede entregar por etapas, pero ninguna etapa parcial satisface por sí sola la petición de cobertura total. Las capacidades desactivadas se explican y los contratos nuevos se planifican explícitamente.
- Esta fase entrega investigación, spec, matriz y prototipo conceptual aislado. No modifica el frontend de producción ni implementa todas las funciones.
