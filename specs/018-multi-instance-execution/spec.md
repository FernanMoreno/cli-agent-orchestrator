# Feature Specification: Varias instancias de CAOS y grafo conjunto de ejecuciones

**Feature Branch**: `main` — documentación creada sin cambiar de rama.

**Created**: 2026-10-09

**Status**: Draft — preparada para planificación; capacidad agregada propuesta, pendiente de implementación.

**Input**: User description: «se pueden conectar varios caos a la vez?» y «vale, haz un specs de eso tambien», dentro de la propuesta de mapa de ejecuciones en vivo.

**Base de evidencia**: [Análisis del código y revisión de composición](research.md).

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Conectar y observar varias instancias (Priority: P1)

El operador configura o selecciona varias instancias autorizadas de CAOS, locales o en otras máquinas, y las observa simultáneamente. Ve disponibilidad, identidad, capacidades y antigüedad de los datos de cada una.

**Why this priority**: Hace posible supervisar el conjunto sin cambiar de servidor para cada consulta.

**Independent Test**: Conectar dos instancias autorizadas y una inaccesible muestra ambas activas y el fallo aislado de la tercera.

**Acceptance Scenarios**:

1. **Given** dos instancias configuradas y autorizadas, **When** el operador abre el conjunto, **Then** ambas actualizan sus datos simultáneamente, manteniendo su identidad y origen.
2. **Given** una instancia inaccesible, **When** se consulta el conjunto, **Then** las demás permanecen observables y la inaccesible muestra último dato disponible y motivo de indisponibilidad conocido.
3. **Given** una candidata descubierta localmente, **When** aún no tiene autorización de lectura, **Then** se presenta como candidata sin añadir sus ejecuciones al conjunto.
4. **Given** una nueva conexión configurada por un operador autorizado, **When** se valida, **Then** se comprueban identidad, acceso y cobertura disponible antes de declararla conectada.

### User Story 2 — Ver el grafo conjunto por instancia (Priority: P1)

El operador explora el grafo de la feature 017 con agentes agrupados por instancia de CAOS. Puede enfocar una instancia, una ejecución o varios ámbitos autorizados sin confundir identidades coincidentes.

**Why this priority**: Una vista conjunta solo sirve si se reconoce a qué servidor pertenece cada agente y tarea.

**Independent Test**: Dos instancias con los mismos identificadores locales de terminal y ejecución producen elementos distintos y seleccionables.

**Acceptance Scenarios**:

1. **Given** agentes de dos instancias con el mismo identificador local, **When** se representa el conjunto, **Then** aparecen separados por origen e identidad de instancia.
2. **Given** nombres de sesión o de proyecto iguales en varias instancias, **When** se agrupa el mapa, **Then** no se declara que pertenecen a una misma ejecución sin una asociación explícita.
3. **Given** una instancia enfocada, **When** se regresa al conjunto, **Then** se recuperan filtros y encuadre conservados sin perder actualizaciones confirmadas.
4. **Given** lectura permitida de una relación pero no de su otro extremo, **When** se muestra el grafo, **Then** no se revela la identidad ni los detalles del extremo restringido; solo se muestra un límite de visibilidad si ese dato está autorizado.

### User Story 3 — Seguir delegaciones entre instancias (Priority: P1)

El operador sigue una tarea delegada desde una instancia a otra cuando existen registros de origen y destino. Distingue solicitud, aceptación, ejecución, resultado e incertidumbre, conservando la autoridad de cada servidor.

**Why this priority**: Permite entender una ejecución distribuida y localizar dónde se detiene.

**Independent Test**: Una tarea coordinada entre dos instancias presenta una conexión probada y estados que reflejan ambos extremos sin convertir un timeout en fallo definitivo.

**Acceptance Scenarios**:

1. **Given** una delegación con identidad y recibo verificables, **When** se observa, **Then** se conecta el origen con el destino y se conservan referencias de tarea y procedencia de cada estado.
2. **Given** la misma delegación visible desde ambos servidores, **When** se agregan sus registros, **Then** se representa una sola relación correlacionada, manteniendo la evidencia de ambos extremos.
3. **Given** timeout después de que el destino pudo aceptar la tarea, **When** se consulta, **Then** aparece estado incierto o reconciliación y no se reenvía automáticamente desde el observador.
4. **Given** registros contradictorios, **When** se reciben, **Then** se muestran las observaciones y su autoridad sin elegir éxito por la marca de tiempo más reciente.
5. **Given** solo coincidencia de texto, nombres o ubicación, **When** se observa, **Then** no se crea una conexión entre instancias ni se presume confianza transitiva.

### User Story 4 — Consultar preguntas del conjunto con su origen (Priority: P2)

El operador ve dónde hay preguntas pendientes y abre la interacción en su instancia de origen cuando está autorizado. Una respuesta conserva la identidad de pregunta, tarea e instancia y utiliza la autoridad de respuesta de la feature 016.

**Why this priority**: Evita atender al agente equivocado cuando hay varias instancias y preguntas similares.

**Independent Test**: Dos instancias plantean preguntas con el mismo identificador local; abrir y responder una afecta únicamente su interacción de origen.

**Acceptance Scenarios**:

1. **Given** una pregunta estructurada visible de una instancia, **When** se abre, **Then** se muestran origen y destinatario y únicamente los controles permitidos para ese lector.
2. **Given** permiso de observación sin permiso de respuesta, **When** se inspecciona, **Then** se puede consultar lo autorizado pero no contestar.
3. **Given** revocación, cambio de identidad de destino o pregunta antigua, **When** llega una respuesta pendiente de envío, **Then** se rechaza o solicita revisión sin redirigirla a otra instancia.
4. **Given** envío de respuesta cuyo resultado no puede confirmarse, **When** se reconecta, **Then** se conserva incertidumbre y se consulta su estado; no se retransmite ciegamente.
5. **Given** una instancia con solo estado de espera nativo, **When** se abre su aviso, **Then** se ofrece inspección disponible sin inventar contenido de pregunta.

### User Story 5 — Recuperar seguimiento ante cambios y fallos (Priority: P1)

El operador mantiene una vista fiable cuando una instancia reinicia, cambia de dirección, pierde acceso o desaparece del registro. El observador se recupera sin afectar al trabajo que las instancias ejecutan.

**Why this priority**: Con varias instancias los fallos parciales son parte del funcionamiento normal.

**Independent Test**: Reiniciar una instancia, cambiar otra por un servidor distinto en la misma dirección y revocar una tercera; las restantes mantienen el seguimiento y no se mezclan datos.

**Acceptance Scenarios**:

1. **Given** reinicio de la misma instancia con nueva generación de proceso, **When** se reconecta, **Then** se verifican identidad y posiciones por origen, separando procesos antiguos y nuevos.
2. **Given** una dirección reutilizada por otra instancia, **When** responde, **Then** no hereda automáticamente identidad, permisos, historial ni respuestas del servidor anterior.
3. **Given** eliminación de una conexión del conjunto, **When** se aplica, **Then** se detiene su seguimiento sin apagar el servidor ni cancelar sus tareas; el historial conservado respeta su política y permisos.
4. **Given** respuesta tardía de un servidor retirado o un lector anterior, **When** llega, **Then** no repuebla la vista actual.
5. **Given** pérdida de eventos o historial retenido insuficiente, **When** se recupera, **Then** converge al presente disponible o declara incertidumbre; no presenta el pasado como íntegro.
6. **Given** dos relojes desalineados, **When** se agregan eventos, **Then** se conserva el orden local y la causalidad explícita sin inventar un orden global.

### User Story 6 — Conservar controles existentes y cobertura explícita (Priority: P2)

El operador puede seguir utilizando las vistas de una sola instancia y conoce qué aporta cada servidor a la vista conjunta. Las versiones antiguas y los límites de capacidad no ocultan trabajo ni conceden acceso adicional.

**Why this priority**: Permite incorporar el conjunto sin romper instalaciones existentes.

**Independent Test**: Un servidor con cobertura completa y otro con solo sesiones/terminales mantienen sus vistas y declaran la diferencia en el grafo.

**Acceptance Scenarios**:

1. **Given** solo una instancia configurada, **When** se abre la interfaz, **Then** los recorridos actuales de consulta siguen disponibles.
2. **Given** servidores de versiones diferentes, **When** uno carece de historial, preguntas o relaciones correlacionables, **Then** declara cobertura limitada sin fabricar datos ni bloquear a los otros.
3. **Given** más instancias o elementos que el límite visible, **When** se reduce o resume la vista, **Then** se explica la cobertura y cómo consultar el resto autorizado.
4. **Given** observación simultánea y una acción desde una vista existente, **When** el operador actúa, **Then** el destino y permiso son explícitos; un foco visual global no determina automáticamente dónde ejecutar.

### Edge Cases

- Dos alias apuntan a la misma instancia: advertir o unir el seguimiento mediante identidad verificada, sin sumar dos veces agentes o relaciones.
- Misma dirección con certificado o identidad cambiada: revisar confianza antes de continuar; no transferir credenciales de un origen a otro.
- Identidad estable no disponible en un servidor legado: usar conexión aislada como ámbito provisional y declarar que no permite correlación automática entre alias o reinicios.
- Registro Fleet obsoleto o en fallback: mostrar origen y antigüedad del registro por separado de la frescura de ejecuciones.
- Fallo de autenticación, permiso insuficiente, incompatibilidad y caída de red: motivos distintos cuando se conozcan, sin exponer secretos.
- Una instancia lee a otra, pero esta no le permite delegar o leer a una tercera: no inferir permisos transitivos.
- Solo un extremo de la delegación está accesible: conservar evidencia visible y límite de cobertura; no afirmar ejecución o resultado desconocidos.
- Observador reinicia durante una respuesta, retirada de conexión o refresco del registro: preservar incertidumbre, permisos y aislamiento de ámbito.
- Workers remotos registrados como runtimes de un CAOS: no contarlos automáticamente como instancias CAOS independientes.
- Saturación, varios observadores y canales lentos: limitar recursos por instancia sin bloquear las demás.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El sistema DEBE permitir observar simultáneamente varias instancias CAOS autorizadas, locales o remotas, desde una interfaz común.
- **FR-002**: Un operador autorizado DEBE poder incorporar, seleccionar y retirar conexiones mediante una configuración administrada; el registro existente puede ser la fuente de esa configuración.
- **FR-003**: Descubrir o registrar una instancia DEBE mantenerse separado de conceder permisos de lectura, ejecución o respuesta.
- **FR-004**: Antes de aceptar datos como pertenecientes a una conexión, el sistema DEBE verificar su identidad y acceso disponibles, declarando cobertura o identidad limitada en servidores legados.
- **FR-005**: Identidad de instancia, dirección de conexión, alias y generación de proceso DEBEN distinguirse; cambiar de dirección no cambia por sí solo identidad y reutilizarla no prueba continuidad.
- **FR-006**: Toda entidad del conjunto DEBE identificarse con su origen y tipo además del identificador local, preservando incarnaciones disponibles.
- **FR-007**: Alias de una identidad verificada DEBEN detectarse para evitar duplicar seguimiento y conteos; sin identidad suficiente no se fusionan automáticamente.
- **FR-008**: El sistema DEBE representar en el grafo 017 agrupaciones por instancia y permitir enfocar instancias o ejecuciones autorizadas.
- **FR-009**: Las agrupaciones DEBEN conservar el ámbito local de ejecución y proyecto; nombres coincidentes no crean un ámbito distribuido compartido.
- **FR-010**: Las relaciones entre instancias DEBEN basarse en delegaciones, mensajes o recibos explícitos, con origen, destino, evidencia y estado por extremo cuando existan.
- **FR-011**: Correlacionar una relación vista en ambos extremos DEBE usar identidad probatoria de operación, no similitud de contenido; eventos duplicados no duplican relaciones.
- **FR-012**: Estado de solicitud, aceptación, entrega, tarea, intento, resultado y proceso DEBEN conservarse por sus fuentes autoritativas; el observador no escribe otra verdad de ejecución.
- **FR-013**: Timeout, contradicción o aceptación incierta DEBEN mostrarse como incertidumbre o reconciliación según evidencia, sin reenvío automático desde la vista agregada.
- **FR-014**: Cada instancia DEBE mostrar conexión, cobertura, momento de última observación y motivo de indisponibilidad conocido; fallar una no bloquea las demás.
- **FR-015**: Cada origen DEBE mantener su propio seguimiento y orden; el sistema no presume cursor ni reloj global compartido.
- **FR-016**: Carga inicial, seguimiento, reconexión y reinicio DEBEN reconciliar datos por origen, evitando omisiones silenciosas, duplicación y regresión de revisiones.
- **FR-017**: Los huecos y límites de retención DEBEN declararse; el presente recuperado no implica historial completo.
- **FR-018**: Retirar una conexión DEBE detener sus canales de observación sin detener el servidor, eliminar su trabajo o cancelar tareas.
- **FR-019**: Respuestas tardías de una conexión, identidad o permiso anterior DEBEN descartarse del ámbito actual; cambiar o revocar acceso invalida los datos afectados.
- **FR-020**: La vista agregada DEBE aplicar permisos del lector por instancia y recurso a identidades, extremos de relaciones, conteos, búsquedas, detalles e historial.
- **FR-021**: Credenciales de una instancia DEBEN limitarse a su destino autorizado y mantenerse fuera del grafo, enlaces, exportaciones y mensajes de error; un endpoint cambiado debe revalidarse antes de reutilizarlas.
- **FR-022**: El sistema DEBE preservar la validación de identidad y transporte de cada conexión; incorporar una instancia no permite eludir su autenticación ni la verificación de su canal.
- **FR-023**: Una pregunta del conjunto DEBE conservar identidad de instancia, pregunta, versión y tarea de la feature 016; el acceso de observación no concede respuesta.
- **FR-024**: Responder DEBE utilizar el canal autorizado del origen y sus garantías de vigencia e idempotencia; entrega incierta exige consulta o reconciliación antes de un nuevo envío.
- **FR-025**: Un origen sin preguntas estructuradas DEBE mostrar únicamente la espera observada e inspección disponible; el mapa no sintetiza permisos ni opciones.
- **FR-026**: La observación conjunta DEBE conservar los recorridos existentes de una sola instancia y el destino explícito de sus acciones; enfoque visual no equivale a autorización de ejecución.
- **FR-027**: Las capacidades DEBEN declararse por instancia, distinguiendo versiones o modos incompatibles; una falta de capacidad no impide observar a los otros orígenes.
- **FR-028**: La fuente y frescura del registro de conexiones DEBEN ser visibles si afectan a cobertura; un registro obsoleto no permite afirmar que no existen más instancias.
- **FR-029**: El seguimiento DEBE tener límites definidos por instancia y conjunto y liberar recursos tras retirada o cambio de ámbito; resúmenes y exclusiones deben ser visibles.
- **FR-030**: Conectar, observar, filtrar o cerrar la vista DEBE generar cero acciones de ejecución o ampliación de confianza; no se infiere confianza transitiva.
- **FR-031**: Instancias CAOS independientes y runtimes remotos de una instancia DEBEN representarse como conceptos diferentes, evitando duplicación de agentes por ubicación remota.
- **FR-032**: La configuración de conexiones administradas DEBE sobrevivir al reinicio del observador; restaurarla no restaura permisos revocados ni convierte observaciones antiguas en actuales.

### Key Entities *(include if feature involves data)*

- **Instancia CAOS**: identidad estable verificada cuando esté disponible, generaciones de proceso y capacidades.
- **Conexión administrada**: identidad propia, alias, endpoint, origen de registro, referencia privada de credencial y estado de validación; varias conexiones pueden apuntar a una instancia.
- **Ámbito de acceso**: lector, instancia, recurso, proyecto y permisos efectivos; observación y ejecución independientes.
- **Entidad de ejecución cualificada**: tipo, identificador local, instancia y incarnación; referencia a los nodos de la feature 017.
- **Relación entre instancias**: identidad correlacionable de operación, extremos, recibos y observaciones de cada origen.
- **Seguimiento por origen**: revisión o posición disponible, cobertura, estado de conexión y última observación.
- **Referencia de interacción**: instancia, pregunta, versión y tarea de la feature 016, con autoridad de respuesta propia.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Con tres instancias autorizadas y un máximo conjunto de 60 agentes y 300 relaciones, el 95 % de cambios confirmados se refleja en menos de 2 segundos mientras su origen permanece conectado, desde disponibilidad del dato en origen.
- **SC-002**: En ese conjunto, el 95 % de aperturas muestra datos de los orígenes disponibles en menos de 3 segundos en el entorno de referencia definido en planificación, aunque una instancia falle o no responda.
- **SC-003**: En todos los escenarios de identificadores locales coincidentes, alias, reinicios y direcciones reutilizadas, no se fusionan entidades sin evidencia ni se incorporan respuestas de un ámbito anterior.
- **SC-004**: En todos los escenarios de desconexión, duplicación y contradicción, la vista converge a la evidencia de cada origen o declara incertidumbre; no genera reenvíos de tareas o respuestas.
- **SC-005**: Los escenarios de observación, revocación y respuesta con permisos insuficientes producen cero exposiciones fuera de autorización y cero acciones no autorizadas.
- **SC-006**: Reiniciar el observador conserva todas las conexiones administradas válidas y revalida su acceso antes de mostrarlas como actuales, sin afectar tareas en los servidores.
- **SC-007**: En una evaluación con al menos cinco operadores, el 80 % identifica en menos de 30 segundos la instancia de origen, la de ejecución y la espera conocida de una delegación distribuida preparada.

## Assumptions

- El usuario solicita un spec adicional para conectar varios CAOS a la vez, en el contexto del mapa en vivo. La vista conjunta es el alcance principal.
- Se reutilizan la feature [017](../017-live-execution-graph/spec.md) para visualización y la [016](../016-agent-interactions/spec.md) para preguntas; ambas están especificadas y pendientes de implementación.
- Conectar instancias para observarlas no crea un protocolo universal nuevo de delegación remota. Se visualizan los mecanismos autorizados existentes; nuevas capacidades de ejecución entre máquinas necesitarían un alcance separado.
- La configuración inicial puede reutilizar registros administrados existentes. No se exige un editor visual nuevo de credenciales o infraestructura, ni descubrimiento automático de toda una red.
- Descubrimiento local es por usuario y máquina; no se presupone extensible a otras máquinas ni suficiente para autorizar lectura global.
- Los objetivos de carga y las prioridades son propuestas. Planificación fijará entorno de referencia, límites, retención, persistencia del observador y compatibilidad mínima por capacidad.
- Servidores legados sin identidad verificable pueden observarse con conexión aislada y cobertura limitada; no se promete correlación automática entre conexiones.
- Quedan fuera del alcance replicación de bases de datos, migración de tareas, consenso entre servidores, compartición automática de secretos y confianza transitiva.
- Esta fase entrega spec y evidencia. Plan, tareas, cambios de código y pruebas de comportamiento serán posteriores según el flujo del proyecto.
