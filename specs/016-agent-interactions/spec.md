# Feature Specification: Preguntas e interacciones de agentes

**Feature Branch**: `main` — especificación creada sin cambiar de rama.

**Created**: 2026-10-09

**Status**: Núcleo y proyección opcional AG-UI implementados y evaluados — aceptación completa pendiente por proveedor (SC-007), Hermes y puerta host/regresión de la continuación. Véanse evaluation.md y agui-host-validation.md.

**Historial de autorización**: La solicitud inicial del 2026-10-09 autorizó el análisis y la especificación. El usuario autorizó la implementación completa de esta feature el 2026-10-10. Esta especificación conserva su alcance original; la planificación concreta ahora los contratos y límites de soporte antes de modificar producción.

**Input**: User description: "haz un analisis profundo del codigo y genera un specs sobre esto", referido a quién recibe y resuelve las preguntas de texto, selección y otros diálogos de Codex, Claude, OpenCode y demás agentes.

**Base de evidencia**: [Análisis del código actual](research.md) y [revisión de composición](composition-review.md).

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Ver y contestar preguntas desde CAO (Priority: P1)

El usuario encuentra las preguntas pendientes en una bandeja común de la interfaz web de CAO. Puede leer el contenido necesario para decidir, identificar el agente y la tarea, contestar y conocer si su respuesta fue recibida. Puede usar texto, una opción, varias opciones o una aprobación según lo que solicite el agente.

**Why this priority**: El usuario necesita atender preguntas sin vigilar ni interpretar todos los terminales.

**Independent Test**: Un agente solicita explícitamente una respuesta; el usuario la recibe en la bandeja, contesta y el mismo agente continúa con ella.

**Acceptance Scenarios**:

1. **Given** una pregunta explícita de texto y un usuario autorizado conectado, **When** se registra, **Then** aparece con contenido, origen, tarea, destinatario, obligatoriedad y estado; responder entrega el texto al agente que la hizo.
2. **Given** una pregunta con opciones, **When** el usuario responde, **Then** solo se aceptan opciones vigentes y se respeta si permite una o varias selecciones.
3. **Given** una pregunta que permite respuesta libre además de opciones, **When** el usuario utiliza esa posibilidad, **Then** el agente recibe el contenido libre asociado a la pregunta correcta.
4. **Given** varios agentes con preguntas pendientes, **When** el usuario responde a una, **Then** las otras conservan su contenido y estado y no reciben esa respuesta.
5. **Given** una pregunta explícita opcional, **When** el agente continúa trabajando, **Then** la interfaz la mantiene disponible hasta su vencimiento o cierre sin presentar la tarea como bloqueada.
6. **Given** un grupo con preguntas obligatorias y opcionales, **When** el usuario contesta las obligatorias, **Then** la continuación respeta la condición declarada del grupo y cada respuesta conserva su pregunta de origen.
7. **Given** que se alcanza el límite permitido de preguntas pendientes, **When** se solicita otra, **Then** se rechaza de forma visible sin perder las pendientes ni declarar que la nueva solicitud fue admitida.

### User Story 2 — Resolver en el supervisor o escalar al usuario (Priority: P1)

El agente supervisor resuelve aclaraciones que puede contestar con el contexto y la autoridad ya disponibles. Las decisiones reservadas al usuario, las solicitudes de nuevos permisos y los casos de autoridad insuficiente llegan al operador autorizado. El usuario puede ver quién contestó y por qué se eligió ese destinatario.

**Why this priority**: Evita interrupciones humanas innecesarias y mantiene las decisiones del usuario bajo su control.

**Independent Test**: Un trabajador solicita una aclaración al supervisor; este contesta una dentro de su autoridad y escala otra que requiere una decisión humana.

**Acceptance Scenarios**:

1. **Given** una aclaración sobre el trabajo delegado y un supervisor vigente con contexto y autoridad suficientes, **When** se solicita su intervención, **Then** puede responder y queda registrada su identidad como agente.
2. **Given** una decisión humana obligatoria, **When** el supervisor intenta contestarla, **Then** la operación se rechaza y la solicitud permanece disponible al usuario autorizado.
3. **Given** un supervisor ausente o sin autoridad suficiente, **When** se debe resolver una pregunta, **Then** se escala al usuario con una razón visible; si tampoco hay usuario disponible permanece pendiente.
4. **Given** una escalada, **When** el usuario contesta, **Then** el trabajador recibe la respuesta y el supervisor conoce el cambio de estado sin recibir contenido fuera de sus permisos.
5. **Given** una pregunta cuyo origen sugiere un destinatario, **When** se determina quién puede responder, **Then** esa sugerencia no amplía los permisos ni convierte una aprobación humana en una aclaración automática.

### User Story 3 — Atender diálogos nativos con sus opciones reales (Priority: P1)

El usuario atiende las preguntas que aparecen dentro de las CLI de los proveedores desde la misma bandeja cuando CAO puede reconocer su contenido y contestarlas de forma segura. Ve claramente los diálogos que requieren intervención en el terminal.

**Why this priority**: Un selector de opciones, un permiso y un menú de autenticación requieren respuestas diferentes.

**Independent Test**: Presentar un diálogo reconocido, uno desconocido y una copia de un diálogo en la salida normal; verificar qué se publica y qué respuesta puede enviarse.

**Acceptance Scenarios**:

1. **Given** un diálogo nativo soportado de selección, **When** CAO lo reconoce como activo, **Then** conserva las opciones reales, la selección múltiple o simple y la posibilidad de texto libre; la respuesta elige exactamente lo solicitado.
2. **Given** un diálogo desconocido o no contestable de forma segura, **When** se observa, **Then** se muestra como pendiente de inspección manual sin inventar opciones de aprobación.
3. **Given** texto de una conversación que cita una pregunta o un menú antiguo, **When** se observa, **Then** no crea una solicitud accionable ni envía respuestas a ese contenido.
4. **Given** un proveedor que restringe su herramienta nativa de preguntas, **When** necesita aclaración, **Then** puede usar el canal común autorizado o informar su limitación; el sistema conserva las restricciones del perfil.
5. **Given** una solicitud de credenciales, **When** el usuario abre el aviso, **Then** se deriva al mecanismo privado del proveedor y no se recopilan contraseñas ni códigos de acceso en la bandeja de preguntas.

### User Story 4 — Mantener la tarea mientras espera respuesta (Priority: P1)

Una pregunta obligatoria pausa la parte del trabajo que depende de ella. Contestar permite continuar esa tarea; el resto del trabajo independiente puede avanzar. El usuario ve una espera por respuesta y su vencimiento, en lugar de una finalización o un error de ejecución genérico.

**Why this priority**: Esperar una decisión es parte del trabajo y no debe causar su ejecución duplicada.

**Independent Test**: Un trabajador pregunta durante una tarea, otro sigue trabajando y el usuario responde antes y después del vencimiento.

**Acceptance Scenarios**:

1. **Given** una tarea activa que genera una pregunta obligatoria, **When** queda pendiente, **Then** la rama dependiente espera, el supervisor conoce el motivo y el trabajo independiente sigue siendo elegible.
2. **Given** una respuesta entregada y aceptada, **When** continúa la ejecución, **Then** se mantiene la identidad de la tarea y no se vuelve a enviar su encargo inicial.
3. **Given** una pregunta de inicio que impidió entregar el encargo, **When** se resuelve y se comprueba que el encargo nunca se entregó y sigue autorizado, **Then** se entrega una sola vez; si no se puede demostrar ese estado se solicita reconciliación.
4. **Given** una pregunta obligatoria pendiente, **When** vence su plazo o se cancela la tarea, **Then** se explica la causa, se cierra la posibilidad de responder y no se elige una respuesta predeterminada.
5. **Given** una tarea con resultado ya verificado y un diálogo posterior independiente, **When** se consulta, **Then** se conserva el resultado y se muestra por separado la necesidad de atender el diálogo.
6. **Given** agentes cuyas preguntas crean un ciclo de espera, **When** se identifica esa dependencia, **Then** el operador ve los participantes y puede resolver o cancelar la espera sin respuestas inventadas.

### User Story 5 — Responder sin duplicados ni decisiones obsoletas (Priority: P1)

El usuario puede responder desde varias vistas o reconectarse sin que una respuesta se envíe dos veces, llegue a otra pregunta o autorice una tarea diferente.

**Why this priority**: Una confirmación repetida puede seleccionar otra opción, iniciar un turno nuevo o repetir un efecto.

**Independent Test**: Contestar simultáneamente desde dos clientes, cambiar el diálogo antes de contestar y simular la pérdida de confirmación tras entregar la respuesta.

**Acceptance Scenarios**:

1. **Given** dos respuestas concurrentes a la misma pregunta vigente, **When** se aceptan, **Then** solo una respuesta efectiva inicia la entrega y la otra recibe el estado registrado o un conflicto explícito.
2. **Given** una respuesta ya aceptada, **When** el mismo intento de respuesta se repite, **Then** se devuelve el mismo resultado sin repetir la entrega.
3. **Given** una pregunta cuyo contenido, destinatario o tarea cambió, **When** llega una respuesta a su versión anterior, **Then** se rechaza antes de enviar texto o teclas.
4. **Given** una entrega que pudo alcanzar al agente pero cuya confirmación se perdió, **When** se vuelve a intentar, **Then** se muestra entrega incierta y no se reenvía automáticamente.
5. **Given** un usuario que contesta directamente en el terminal, **When** CAO puede observar que desapareció el diálogo, **Then** cierra la solicitud como atendida fuera de la bandeja sin atribuirle una respuesta no observada.

### User Story 6 — Recuperar preguntas tras desconexiones y reinicios (Priority: P1)

El usuario recupera sus preguntas pendientes y el estado de sus respuestas después de reconectar o de que CAO reinicie. Si el agente o su diálogo ya no existen, se explica esa situación antes de permitir una respuesta.

**Why this priority**: La continuidad depende de conservar la relación entre pregunta, tarea, destinatario y entrega.

**Independent Test**: Reiniciar CAO con preguntas pendientes, respuestas por entregar y entregas inciertas; volver a consultar y responder.

**Acceptance Scenarios**:

1. **Given** una pregunta pendiente y un agente que sigue vivo, **When** CAO reinicia, **Then** recupera la solicitud con la misma identidad y comprueba su vigencia antes de habilitar la respuesta.
2. **Given** un agente sustituido o una tarea cancelada, **When** se recupera una solicitud anterior, **Then** se presenta como obsoleta o cerrada y no permite actuar sobre el nuevo agente.
3. **Given** una entrega incierta antes del reinicio, **When** se recupera, **Then** conserva la incertidumbre y no se reenvía como si nunca hubiera sido entregada.
4. **Given** eventos perdidos o repetidos durante la reconexión, **When** se restaura la bandeja, **Then** refleja el estado vigente sin perder preguntas ni duplicarlas.

### User Story 7 — Consultar contenido según la autoridad de cada participante (Priority: P1)

El usuario autorizado puede leer el contenido necesario para decidir. Otros observadores reciben únicamente la información que les corresponde. Contestar una aclaración conserva las reglas vigentes de aprobación del trabajo.

**Why this priority**: Las preguntas pueden contener detalles privados del proyecto y una respuesta no equivale automáticamente a permiso para ejecutar acciones.

**Independent Test**: Consultar y responder como usuario autorizado, observador, supervisor y agente de otra tarea; revocar permisos antes de la entrega.

**Acceptance Scenarios**:

1. **Given** una solicitud con detalles privados y un observador sin autorización de contenido, **When** consulta eventos o la bandeja, **Then** solo ve los metadatos permitidos y no puede contestar.
2. **Given** permisos revocados después de abrir una pregunta, **When** se intenta responder o entregar la respuesta, **Then** se rechaza la acción antes del efecto.
3. **Given** una aprobación asociada a una versión de trabajo, **When** cambia esa versión o su autorización, **Then** la respuesta anterior no permite actuar sobre el nuevo trabajo.
4. **Given** una aclaración de texto como «sí, continúa», **When** el agente la recibe, **Then** las acciones que necesitan aprobación siguen requiriendo la autorización correspondiente.

### User Story 8 — Conocer el soporte por proveedor y conservar los clientes existentes (Priority: P2)

El operador sabe qué tipos de preguntas puede reconocer y contestar cada proveedor. Las interfaces existentes siguen funcionando durante la incorporación del nuevo flujo y señalan cuándo una acción necesita intervención manual.

**Why this priority**: La compatibilidad real depende del proveedor, de su versión, de sus permisos y del lugar donde se ejecuta.

**Independent Test**: Consultar soporte para Codex, Claude Code, OpenCode y Hermes; usar una interfaz antigua y un proveedor sin soporte nativo completo.

**Acceptance Scenarios**:

1. **Given** un proveedor habilitado, **When** se consulta su soporte, **Then** se distingue entre emitir preguntas explícitas, detectar diálogos nativos y contestar cada tipo de diálogo.
2. **Given** una interfaz de terminal o una interfaz opcional existente, **When** se atiende una pregunta, **Then** ambas reflejan el mismo estado vigente y las rutas compatibles no amplían autoridad ni duplican entregas.
3. **Given** un trabajador remoto o sin terminal interactivo accesible, **When** genera una pregunta, **Then** la respuesta usa un canal admitido para ese trabajador o muestra una limitación explícita; nunca se escribe en un terminal local de sustitución.
4. **Given** interfaces opcionales desactivadas, **When** se emite una pregunta explícita admitida, **Then** la bandeja principal de CAO sigue disponible.

### Edge Cases

- Preguntas agrupadas, selecciones múltiples y opciones cuya etiqueta contiene texto parecido a una autorización.
- Preguntas visualmente idénticas en turnos diferentes; redibujos del mismo diálogo y cambios de idioma, tamaño o versión del proveedor.
- Respuesta desde terminal y bandeja al mismo tiempo; respuesta que llega después de vencimiento, cancelación o sustitución del agente.
- El agente completa una tarea antes de que el usuario responda a una pregunta opcional.
- No hay supervisor o usuario conectado; varias preguntas obligatorias crean dependencias circulares entre agentes.
- Se pierde conectividad antes de entregar, tras entregar o a mitad de una secuencia de selección.
- Falla el almacenamiento antes o después de registrar una respuesta; vuelve el servicio mientras hay una entrega en curso.
- Un cambio visual de estado no basta para conocer qué respuesta se dio ni si el agente la aceptó.
- Caduca la reserva o autorización de una tarea mientras espera al usuario.
- Se alcanza la capacidad de solicitudes pendientes; no se elimina una solicitud activa para aceptar otra.
- La pregunta incluye instrucciones, contenido privado, caracteres de control o una recomendación que aparenta autorización.
- Un texto terminado en signo de interrogación es comentario, una cita o una respuesta final legítima.

## Requirements *(mandatory)*

### Functional Requirements

**Contenido y origen**

- **FR-001**: CAO MUST registrar solicitudes de interacción con identidad y versión propias, origen, tarea o sesión si existe, tipo, contenido autorizado, destinatario, obligatoriedad, plazo y estado.
- **FR-002**: CAO MUST admitir texto libre, selección simple, selección múltiple, selección con alternativa libre y aprobación con opciones declaradas. MUST validar respuestas por tipo, obligatoriedad y opciones vigentes.
- **FR-003**: Los agentes autorizados MUST poder emitir preguntas explícitas independientemente de la capacidad de su proveedor para producir diálogos nativos.
- **FR-004**: CAO MUST admitir grupos de preguntas con identidad por pregunta y reglas explícitas sobre cuáles son necesarias para continuar.
- **FR-005**: CAO MUST reconocer diálogos nativos únicamente cuando pueda identificar una solicitud activa. MUST conservar sus opciones reales y declarar por separado su capacidad para responder.
- **FR-006**: CAO MUST tratar el texto normal como contenido de conversación. Una sospecha basada en el texto puede producir un aviso no accionable; MUST NOT inventar una solicitud ejecutable ni autorización.
- **FR-007**: CAO MUST ofrecer inspección manual para diálogos desconocidos o no contestables y MUST NOT traducirlos automáticamente a aprobar/denegar.
- **FR-008**: CAO MUST mantener separadas las preguntas, las esperas de cuota, la autenticación del proveedor, los errores de ejecución y las necesidades de reconciliación.

**Destinatario y autoridad**

- **FR-009**: CAO MUST determinar el destinatario mediante una política vigente y el contexto autorizado. La sugerencia del agente MUST NOT ampliar la autoridad del destinatario.
- **FR-010**: El supervisor MUST poder resolver únicamente aclaraciones dentro de su autoridad y contexto disponibles. Las decisiones humanas obligatorias, preferencias del usuario no conocidas y ampliaciones de permisos MUST llegar al usuario autorizado.
- **FR-011**: CAO MUST escalar preguntas ante autoridad insuficiente, supervisor ausente o vencimiento de su plazo de atención. La escalada MUST conservar identidad, historial y razón.
- **FR-012**: CAO MUST dejar pendientes las preguntas sin destinatario disponible hasta vencimiento o cancelación; MUST NOT escoger respuestas por ausencia de usuario.
- **FR-013**: Cada lectura, aceptación de respuesta y entrega MUST comprobar la autoridad vigente sobre la solicitud y su trabajo asociado. MUST registrar si respondió una persona o un agente.
- **FR-014**: Una aclaración MUST NOT otorgar permisos. Una aprobación de trabajo MUST mantener los controles vigentes y quedar ligada al trabajo y autorización específicos que el usuario revisó.

**Experiencia y confidencialidad**

- **FR-015**: La web principal de CAO MUST ofrecer una bandeja de solicitudes pendientes con filtro por agente, tarea o sesión, destinatario y estado; MUST incluir una vista de detalle y controles adecuados al tipo de pregunta.
- **FR-016**: La interfaz MUST mostrar la diferencia entre respuesta aceptada, pendiente de entrega, entrega confirmada, aceptación observada por el agente y entrega incierta. MUST NOT afirmar continuación por el mero registro de una respuesta.
- **FR-017**: El supervisor y los participantes autorizados MUST poder observar el estado de las preguntas de su trabajo. Los avisos MUST permitir obtener la vista vigente tras perder o repetir eventos.
- **FR-018**: Los canales generales de observación MUST conservar su política de metadatos. El contenido de preguntas y respuestas MUST estar disponible solo mediante vistas con autoridad de contenido y MUST NOT incluir el historial completo del terminal.
- **FR-019**: CAO MUST aplicar límites de contenido y presentación segura. MUST derivar contraseñas, códigos de autenticación y otros secretos de acceso al mecanismo privado del proveedor, sin almacenarlos como respuestas comunes.

**Espera y continuación**

- **FR-020**: Una pregunta obligatoria MUST pausar solamente el trabajo que depende de ella, hacer visible la dependencia y permitir avanzar trabajo independiente.
- **FR-021**: CAO MUST distinguir el tiempo esperando una respuesta del tiempo de ejecución activa. MUST aplicar un plazo de respuesta visible y conservar las reglas de vigencia de autorizaciones y reservas del trabajo.
- **FR-022**: La respuesta a una pregunta durante la ejecución MUST continuar la misma tarea sin repetir su encargo, resultado o efectos ya ejecutados.
- **FR-023**: Resolver una pregunta de inicio MUST permitir entregar una vez el encargo retenido solo si CAO prueba que nunca se entregó, que el destinatario es el mismo y que el trabajo sigue autorizado; en otro caso MUST solicitar reconciliación.
- **FR-024**: Cancelación, vencimiento, retirada de la pregunta o sustitución del agente MUST impedir respuestas posteriores y actualizar las vistas del usuario y del supervisor.
- **FR-025**: Una pregunta pendiente MUST NOT constituir por sí sola prueba de resultado final. Un resultado verificado MUST conservarse aunque exista un diálogo posterior independiente.
- **FR-026**: Las entregas automáticas de nuevas tareas y mensajes MUST NOT contestar diálogos pendientes, incluso si el proveedor permite recibir mensajes durante su procesamiento.

**Entrega, concurrencia y recuperación**

- **FR-027**: CAO MUST comprobar antes de entregar que siguen vigentes la pregunta, su versión, el agente, el turno y el trabajo asociado; MUST rechazar respuestas obsoletas antes del efecto.
- **FR-028**: CAO MUST aceptar una sola respuesta efectiva por pregunta y versión. Intentos equivalentes repetidos MUST devolver el estado registrado; intentos divergentes MUST producir un conflicto explícito.
- **FR-029**: CAO MUST entregar solo mediante una capacidad admitida por el proveedor para ese diálogo. MUST NOT usar respuestas o teclas genéricas cuando no puede determinar su significado.
- **FR-030**: Una entrega parcial o cuyo efecto no puede confirmarse MUST quedar incierta, impedir reenvío automático y proporcionar una acción de reconciliación; MUST distinguirla de un fallo que prueba ausencia de entrega.
- **FR-031**: Preguntas, respuestas, destinatarios, versiones y evidencia de entrega MUST sobrevivir al reinicio. La recuperación MUST comprobar vigencia antes de habilitar acciones pendientes.
- **FR-032**: Redibujos y eventos repetidos de un mismo diálogo MUST mantener una solicitud única; preguntas iguales en turnos diferentes MUST conservar identidades diferentes.
- **FR-033**: La respuesta directa desde el terminal MUST reconciliarse cuando se observa el cierre del diálogo; CAO MUST registrar solamente la identidad y contenido que realmente pueda acreditar.
- **FR-034**: CAO MUST conservar un historial atribuible de creación, enrutamiento, escalada, respuesta, entrega, cancelación, vencimiento y reconciliación, con acceso y retención acordes al trabajo y a su confidencialidad.
- **FR-035**: CAO MUST limitar solicitudes y contenido por participante y tarea, rechazar saturación de forma visible y MUST NOT descartar preguntas activas silenciosamente.
- **FR-036**: CAO MUST mostrar dependencias de espera entre agentes y permitir al operador resolver o cancelar un ciclo sin que se generen respuestas automáticas para romperlo.

**Compatibilidad y alcance de soporte**

- **FR-037**: CAO MUST publicar soporte por proveedor, versión y modalidad de ejecución, distinguiendo emisión explícita, detección nativa y respuesta por tipo. Una capacidad no acreditada MUST aparecer como no soportada o pendiente de validación.
- **FR-038**: La primera entrega MUST cubrir el canal explícito con Codex, Claude Code, OpenCode y Hermes en modalidades autorizadas que admiten comunicación con CAO. Para cada uno MUST validar al menos un diálogo nativo real o declarar la limitación que obliga a intervención manual.
- **FR-039**: La incorporación MUST conservar las políticas de perfiles y permisos de herramientas. Habilitar preguntas MUST NOT habilitar otras herramientas ni ampliar autorizaciones del trabajo.
- **FR-040**: Los clientes y mecanismos existentes de terminal, respuesta y aprobación MUST conservar compatibilidad documentada y observar el mismo estado cuando actúen sobre la misma solicitud; MUST NOT crear una segunda autoridad de respuesta.
- **FR-041**: Trabajadores remotos y trabajo gestionado MUST mantener su identidad, aislamiento y permisos. Una pregunta MUST NOT permitir acceder a un terminal distinto ni eludir el control del trabajo mediante una ruta de respuesta ordinaria.
- **FR-042**: La bandeja principal y el canal común MUST funcionar sin habilitar interfaces opcionales. Esas interfaces MUST reutilizar la autoridad de preguntas y respuestas cuando se integren.

### Key Entities *(include if feature involves data)*

- **Solicitud de interacción**: Pregunta identificable y versionada, tipo, contenido autorizado, origen, relación con trabajo/turno, destinatario, obligatoriedad, plazo, estado y capacidad de respuesta disponible.
- **Grupo de preguntas**: Conjunto coherente de solicitudes con orden, respuestas requeridas y condición para continuar.
- **Opción**: Identidad estable dentro de una versión, etiqueta, disponibilidad y relación con la respuesta libre si existe.
- **Asignación de destinatario**: Participante autorizado, alcance de resolución, motivo, plazo y cambios por escalada.
- **Respuesta**: Contenido validado, pregunta y versión a la que responde, identidad de persona o agente, momento, resultado de aceptación y evidencia para deduplicar intentos.
- **Entrega**: Relación entre respuesta y agente/diálogo vigente; distingue ausencia de entrega, entrega en curso, confirmación, aceptación observada e incertidumbre.
- **Dependencia de espera**: Relación entre la pregunta obligatoria y la parte del trabajo que debe continuar tras resolverla.
- **Registro de interacción**: Historial autorizado de transiciones, decisiones y pruebas de entrega, sin atribuir acciones no observadas.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: En una ejecución local controlada con 20 agentes y hasta 100 solicitudes pendientes, el 95% de preguntas admitidas aparece al destinatario conectado en un máximo de 2 segundos desde su registro u observación confirmada; ninguna se pierde silenciosamente.
- **SC-002**: El 100% de los casos de aceptación de texto, selección simple, selección múltiple, alternativa libre y aprobación entrega el contenido esperado al agente y pregunta correctos.
- **SC-003**: En todos los casos de respuesta repetida, concurrente, obsoleta, parcial o incierta, se registra como máximo una respuesta efectiva por versión y no se repite automáticamente una entrega que pudo producir efecto.
- **SC-004**: En todos los casos de reinicio y reconexión previstos, el usuario recupera el estado vigente de las solicitudes sin duplicarlas y ninguna respuesta antigua llega a una tarea o agente de sustitución.
- **SC-005**: En todos los casos de autoridad insuficiente, revocada o exclusivamente humana, se impiden tanto lectura indebida de contenido como entrega no autorizada; los casos de aclaración delegable se resuelven sin intervención humana obligatoria.
- **SC-006**: Todas las tareas de aceptación que esperan al usuario distinguen ese tiempo del trabajo activo, conservan sus resultados verificados y continúan sin repetir el encargo inicial ni bloquear tareas independientes.
- **SC-007**: Codex, Claude Code, OpenCode y Hermes completan el recorrido del canal explícito en sus modalidades autorizadas; el soporte nativo real se demuestra por proveedor o se muestra como limitación manual, sin presentar casos omitidos como soportados.
- **SC-008**: Un usuario puede contestar cada tipo admitido desde la bandeja principal sin navegar al terminal; los diálogos no soportados ofrecen origen, motivo y una vía de intervención manual identificable.

## Assumptions

- La petición inicial del 2026-10-09 autorizó análisis y especificación. La autorización del usuario del 2026-10-10 cubre planificación e implementación completa de esta feature; este documento conserva el historial y el alcance de los 42 requisitos.
- El supervisor puede resolver aclaraciones dentro de la autoridad vigente. Las preferencias humanas desconocidas, aprobaciones humanas y ampliaciones de permisos se escalan al operador. Es una decisión de diseño propuesta, no una política ya configurada por el usuario.
- El canal explícito constituye la base común entre proveedores; el reconocimiento de preguntas en prosa no ofrece certeza suficiente para automatizar respuestas.
- Se prioriza la web principal de CAO y los mecanismos actuales de agentes y terminales. Interfaces externas pueden consumir el mismo flujo según sus permisos; correo, mensajería externa y nuevos clientes móviles quedan fuera de esta entrega.
- Se admiten texto y opciones declaradas; formularios arbitrarios, adjuntos y respuestas que contienen credenciales quedan fuera del canal común inicial.
- Los límites temporales y de retención deben ser configurables y visibles. Los valores operativos definitivos se concretarán en planificación; no se responderá automáticamente al vencer un plazo.
- La espera por preguntas no extiende por sí misma autorizaciones ni reservas. Si estas pierden vigencia, la continuación necesita nueva validación o reconciliación.
- La localización del trabajador y las capacidades vigentes del trabajo determinan el canal disponible. El soporte explícito inicial no amplía automáticamente contratos de trabajadores aislados que hoy carecen de esa capacidad.
- Se reutilizan las reglas de identidad, autoridad, historial de trabajo y prueba de resultados existentes; la respuesta a una pregunta no crea una vía alternativa de aprobación.
