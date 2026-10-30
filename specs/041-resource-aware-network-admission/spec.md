# Feature Specification: Admisión dinámica y capacidad distribuida de la red

**Feature Branch**: `main` — especificación documental, sin nueva rama.

**Feature Directory**: `specs/041-resource-aware-network-admission`

**Created**: 2026-10-30

**Status**: Draft — requisitos propuestos; implementación y aceptación pendientes.

**Input**: Sustituir las cuatro entregas simultáneas fijas por admisión según recursos, presión de PostgreSQL y capacidad de cada nodo, conservando el orden por receptor. Cada máquina necesita límites; la red debe sumar nodos y repartir trabajo sin un número global fijo de agentes.

## User Scenarios & Testing _(mandatory)_

### User Story 1 — Aprovechar la capacidad disponible de un nodo (Priority: P1)

Como operador quiero que un nodo aumente o reduzca las operaciones que admite según su capacidad comprobada, sin que una constante de concurrencia determine el tamaño de toda la red.

**Why this priority**: Un nodo pequeño debe protegerse y uno con recursos libres debe poder aprovecharlos. Aumentar una cola o cambiar cuatro por otra constante no resuelve esta necesidad.

**Independent Test**: Mantener trabajo elegible pendiente y variar de forma controlada los recursos disponibles y la presión del nodo. Contrastar admisiones, actividad y consumo con observaciones independientes.

**Acceptance Scenarios**:

1. **Given** capacidad suficiente para más de cuatro entregas independientes, **When** hay demanda elegible y los recursos compartidos lo permiten, **Then** se observan más de cuatro entregas activas sin modificar código ni exceder presupuestos.
2. **Given** entregas activas y presión creciente de memoria, ejecución o almacenamiento, **When** se alcanza el umbral de reducción declarado, **Then** baja o se cierra la nueva admisión dentro del plazo de reacción, preservando las operaciones ya iniciadas.
3. **Given** capacidad reducida por presión, **When** las señales frescas muestran recuperación sostenida, **Then** la admisión vuelve a crecer dentro del plazo de recuperación sin reiniciar el servicio ni alternar repetidamente entre extremos.

### User Story 2 — Sumar nodos y repartir trabajo elegible (Priority: P1)

Como operador quiero ampliar la red incorporando nodos autorizados y que el trabajo distribuible utilice su capacidad, conservando la ubicación y autoridad de los agentes existentes.

**Why this priority**: La capacidad total debe crecer con los recursos incorporados; una sola máquina no dispone de recursos infinitos.

**Independent Test**: Comparar la misma carga distribuible con uno y con varios nodos identificados, incorporar un nodo durante la ejecución y contrastar ubicación, admisiones y resultados reales.

**Acceptance Scenarios**:

1. **Given** un nodo saturado, trabajo aún sin ubicación y otro nodo compatible con capacidad autorizada libre, **When** se decide dónde ejecutarlo, **Then** el segundo recibe trabajo sin elevar el presupuesto del primero ni cambiar el código.
2. **Given** una red en ejecución, **When** se incorpora un nodo autenticado con capacidad fresca, **Then** puede recibir trabajo nuevo dentro del plazo de incorporación declarado, sin reiniciar los otros nodos.
3. **Given** un mensaje dirigido a un terminal ya ubicado, **When** su nodo se satura, **Then** el mensaje conserva destinatario, encarnación y orden; no se entrega a otro terminal ni se migra la sesión por conveniencia de capacidad.
4. **Given** capacidad anunciada que ya se consumió, **When** llega una asignación, **Then** el destino revalida y reserva antes de iniciar; una oferta antigua no concede capacidad ni autoridad.

### User Story 3 — Proteger PostgreSQL y los demás recursos compartidos (Priority: P1)

Como operador quiero que añadir nodos no multiplique las conexiones o el consumo por encima de lo que admite un recurso compartido.

**Why this priority**: Escalar procesos mientras se sobrecarga su dependencia común traslada el fallo y puede detener toda la red.

**Independent Test**: Hacer competir al menos dos nodos por un mismo presupuesto compartido, reducirlo o degradar su servicio y verificar las reservas y efectos efectivos, no solo sus anuncios.

**Acceptance Scenarios**:

1. **Given** varios nodos comparten almacenamiento y solo queda una unidad de capacidad admisible, **When** intentan reservarla simultáneamente, **Then** la reserva combinada no excede esa unidad; los demás esperan o reciben rechazo explícito antes de iniciar el efecto protegido.
2. **Given** presión o fallos del almacenamiento, **When** crece la demanda, **Then** se reduce la admisión dependiente sin ampliar conexiones automáticamente, omitir auditorías o interpretar un fallo de consulta como ausencia de datos.
3. **Given** dos nodos comparten un mismo host o una misma cuenta de proveedor, **When** ambos calculan capacidad, **Then** no contabilizan dos veces los mismos recursos físicos o cuotas compartidas.
4. **Given** una reserva o cierre cuyo resultado no puede confirmarse, **When** otro nodo solicita capacidad, **Then** la incertidumbre permanece contabilizada hasta su resolución autorizada; el paso del tiempo por sí solo no demuestra liberación.

### User Story 4 — Conservar mensajes, orden y equidad bajo saturación (Priority: P1)

Como agente u operador quiero que el trabajo aceptado permanezca pendiente de forma durable y que la presión no cause pérdida, duplicación de efectos ni adelantamientos para un mismo receptor.

**Why this priority**: La velocidad de admisión no puede debilitar las garantías que hacen fiable la coordinación multiagente.

**Independent Test**: Mezclar mensajes concurrentes para receptores lentos y rápidos, duplicar avisos de entrega, saturar la cola temporal y reiniciar el consumidor; contrastar identidades, secuencia durable y resultados.

**Acceptance Scenarios**:

1. **Given** varios mensajes aceptados para el mismo receptor, **When** compiten avisos, recuperación y entregas desde distintos caminos, **Then** se conserva su orden durable y solo una operación de entrada puede actuar sobre ese receptor a la vez.
2. **Given** un mensaje confirmado como aceptado y una cola temporal llena, **When** no se admite su aviso, **Then** el mensaje sigue pendiente en almacenamiento y se recupera sin exigir que el emisor lo vuelva a crear.
3. **Given** un receptor lento y otros elegibles, **When** existe capacidad disponible, **Then** los demás avanzan según la política de equidad publicada; el receptor lento no ocupa todas las oportunidades con avisos duplicados.
4. **Given** una entrega pudo producir un efecto antes de una desconexión, **When** se recupera la comunicación, **Then** se conserva la incertidumbre y se reconcilia por identidad; no se reenvía ciegamente ni se declara entrega confirmada solo por el aviso de aceptación.

### User Story 5 — Retirar y recuperar nodos sin doble ejecución (Priority: P1)

Como operador quiero drenar, reiniciar o perder un nodo sin transferir accidentalmente su identidad, sus tareas activas o sus permisos a otro proceso.

**Why this priority**: Las incorporaciones y los fallos parciales son parte del funcionamiento normal de una red que crece.

**Independent Test**: Retirar un nodo con trabajo activo, interrumpir su comunicación y reemplazarlo con el mismo nombre; observar la admisión, el historial de intentos y los efectos autorizados.

**Acceptance Scenarios**:

1. **Given** un nodo entra en drenaje, **When** todavía tiene operaciones activas, **Then** deja de aceptar nuevas y conserva el seguimiento de las existentes hasta su cierre confirmado o su estado explícito de incertidumbre.
2. **Given** un nodo deja de anunciar capacidad fresca, **When** se toma una nueva decisión, **Then** queda excluido de nuevas asignaciones; su trabajo anterior no se declara muerto ni se reasigna automáticamente por esa sola ausencia.
3. **Given** el mismo nombre de nodo reaparece con otra encarnación, **When** llegan resultados o reservas tardíos de la anterior, **Then** no alteran ni liberan recursos de la nueva.
4. **Given** un coordinador reinicia después de aceptar trabajo, **When** reanuda su actividad, **Then** reconstruye el pendiente y las reservas válidas o inciertas desde la fuente durable, sin una ráfaga que ignore presupuestos.

### User Story 6 — Entender por qué la red admite o espera (Priority: P2)

Como operador quiero distinguir demanda, agentes activos, entregas, capacidad y bloqueos para decidir si añadir nodos, ampliar un recurso compartido o resolver una restricción de autoridad.

**Why this priority**: Un total de agentes creados o un grafo de demostración no permite saber qué parte de la red está realmente saturada.

**Independent Test**: Producir esperas por recursos locales, recurso compartido, permisos y receptor ocupado; comparar el diagnóstico autorizado con las causas provocadas.

**Acceptance Scenarios**:

1. **Given** trabajo esperando, **When** se consulta su admisión, **Then** se distingue al menos espera por receptor, recursos locales, recurso compartido, nodo no disponible y restricción de autoridad, con frescura y ámbito.
2. **Given** varios nodos activos, **When** se observa la red, **Then** se separan agentes creados, listos y ejecutando, operaciones de entrega activas, mensajes pendientes y capacidad disponible; los datos parciales se declaran parciales.
3. **Given** un lector con acceso limitado, **When** consulta capacidad o motivos, **Then** ve únicamente lo autorizado y no obtiene secretos, contenido de mensajes ni permisos de ejecución por observarlos.

### Edge Cases

- Señales ausentes, obsoletas, contradictorias, no finitas o imposibles: no convierten capacidad desconocida en capacidad libre.
- Pérdida de eventos de capacidad o desalineación de relojes: no permite reutilizar una observación caducada ni inventar un orden global de mensajes.
- Todos los nodos saturados: el sistema conserva el pendiente aceptado y explica la espera; no crea trabajo o conexiones sin límite.
- Descenso del presupuesto por debajo del consumo activo: se conserva la deuda y se frena la nueva admisión, sin fingir cancelación o liberación.
- Varios procesos, consumidores, sondeos y reconciliadores sobre un mismo nodo: no multiplican su presupuesto ni la autoridad sobre un receptor.
- Nodo con versión antigua que no declara la capacidad necesaria: compatibilidad explícita o exclusión de esa operación, sin asumir soporte.
- Tareas no distribuibles, recursos locales, aislamiento de proyecto o capacidades MCP distintas: no se colocan en un nodo incompatible para aprovechar un hueco.
- Señales que oscilan cerca de un umbral: la admisión debe estabilizarse y no provocar tormentas de conexiones o reintentos.
- Una operación cancelada cuyo hilo, proceso o conexión sigue vivo: sigue consumiendo capacidad hasta confirmar su fin o aplicar el protocolo de incertidumbre correspondiente.
- Un recurso compartido limita toda la red: se informa esa restricción; sumar nodos no acredita más capacidad efectiva de ese recurso.

## Requirements _(mandatory)_

### Functional Requirements

- **FR-001**: La red DEBE carecer de un techo global de agentes, nodos o entregas impuesto por una constante del producto. Los presupuestos físicos y las cuotas autorizadas seguirán siendo finitos y verificables.
- **FR-002**: La admisión DEBE distinguir agentes/sesiones, ejecución de tareas, entregas de mensajes, operaciones de almacenamiento y lectura de estado. Aumentar uno de esos contadores no acreditará capacidad en los demás.
- **FR-003**: Cada decisión DEBE utilizar una política validada y señales frescas de capacidad local, demanda y presión de recursos relevantes. El presupuesto efectivo DEBE poder crecer y reducirse durante la ejecución sin modificar código ni reiniciar la red.
- **FR-004**: La política DEBE declarar ámbito, reservas de seguridad, caducidad de señales, plazos de reacción y recuperación, control de oscilaciones y comportamiento ante información desconocida. Los valores y unidades DEBEN ser finitos, válidos y observables; un cambio inválido no sustituirá la política válida en uso.
- **FR-005**: La admisión DEBE respetar todos los presupuestos aplicables, incluidos host compartido, nodo, almacenamiento compartido y cuotas de proveedor. La suma de reservas en un dominio no excederá su capacidad autorizada aunque haya varios nodos o coordinadores. El consumo activo o reservado de Work también contará cuando comparta esos dominios, conservando su dueño y planificador; mantener rutas separadas no autoriza duplicar un presupuesto común.
- **FR-006**: Las reservas DEBEN contabilizar adquisición en curso, operación activa y liberación no confirmada. Una reducción de presupuesto no borrará consumo existente ni iniciará más trabajo mientras no haya margen efectivo.
- **FR-007**: La presión de PostgreSQL DEBE reducir o cerrar la admisión dependiente sin eliminar comprobaciones de identidad, autorización, esquema, auditoría o aislamiento. Añadir nodos no aumentará por sí solo el presupuesto compartido de conexiones.
- **FR-008**: Una operación DEBE consumir la capacidad correspondiente a sus etapas efectivas. El tiempo de espera de un proveedor no justificará retener innecesariamente una conexión de almacenamiento ni bloquear todos los receptores.
- **FR-009**: Todos los caminos de entrega —solicitud directa, eventos de estado, sondeos de proveedor, reconciliación y llamadas internas— DEBEN respetar la misma autoridad, exclusión por receptor y presupuestos aplicables. Ningún camino añadirá un multiplicador oculto de concurrencia.
- **FR-010**: Para un receptor y encarnación dados DEBE existir como máximo una entrada con efectos simultáneos, preservando la secuencia durable de mensajes aceptados. Los avisos repetidos no crearán mensajes, cambiarán ese orden ni concederán una reserva adicional para el mismo efecto.
- **FR-011**: La confirmación de aceptación DEBE seguir significando persistencia durable, diferenciada de entrega o ejecución. La falta de espacio en una cola temporal no perderá ni revocará trabajo ya aceptado; los rechazos de nueva aceptación durable serán explícitos y no se presentarán como éxito.
- **FR-012**: La espera, los avisos, las observaciones y los reintentos DEBEN tener consumo acotado por política, con recuperación desde el pendiente durable. Declarar más agentes o nodos no provocará preasignación ilimitada ni un recorrido completo de toda la red por cada mensaje.
- **FR-013**: Receptores y ámbitos elegibles DEBEN progresar según una política explícita de equidad dentro de su plazo declarado cuando existe capacidad estable y demanda finita. Un receptor ocupado no monopolizará las oportunidades de los demás.
- **FR-014**: La red DEBE incorporar y retirar nodos durante la ejecución mediante identidad, encarnación, confianza y capacidades comprobadas. Un anuncio de capacidad no concede permisos ni sustituye la reserva y validación del destino.
- **FR-015**: El trabajo aún no ubicado y autorizado como distribuible DEBE poder asignarse a un nodo elegible con capacidad. En la primera fase de distribución, el trabajo elegible será un encargo nuevo del contrato existente de asignación elástica ordinaria; el origen que posee su intento durable conservará la autoridad de colocación y conciliación. La selección respetará recursos requeridos, proveedor/perfil, aislamiento y restricciones de ubicación. Recibir una concesión de infraestructura no acreditará que el trabajador esté listo: el destino deberá confirmar identidad, preparación y reserva antes de la primera ejecución. El trabajo gestionado por Work conserva su propio dueño y contrato; no se desviará por la ruta ordinaria.
- **FR-016**: La presión de capacidad NO DEBE migrar un terminal vivo ni redirigir mensajes destinados a su identidad. La migración en vivo queda fuera de esta entrega; los cambios de ubicación de trabajo existente requerirán su contrato explícito.
- **FR-017**: Un nodo en drenaje o con capacidad no fiable NO DEBE recibir nuevas asignaciones. Su retirada no implicará cancelar, completar ni duplicar el trabajo activo sin evidencia y autoridad.
- **FR-018**: Reservas, intentos, mensajes y decisiones durables DEBEN conservar ámbito e identidad de nodo/receptor y su encarnación. Respuestas tardías o procesos reemplazados no podrán modificar la encarnación vigente.
- **FR-019**: La recuperación tras reinicio o partición DEBE distinguir pendiente seguro, efecto confirmado y efecto incierto. No se promete ejecución exactamente una vez de un efecto externo incierto; su repetición requiere el protocolo de conciliación e idempotencia aplicable.
- **FR-020**: Los cambios de admisión DEBEN preservar cancelación, cierre y drenaje existentes. Un timeout o una cancelación del consumidor no demostrará que su trabajo físico haya terminado ni liberará su capacidad por sí solo.
- **FR-021**: El estado durable de admisión y recuperación DEBE utilizar la persistencia PostgreSQL del proyecto. Una observación o aviso temporal no será la única fuente para recuperar trabajo o atribuir su finalización.
- **FR-022**: El operador DEBE poder conocer política vigente, demanda, reservas, actividad, presión limitante, espera y frescura por dominio autorizado. Las métricas y motivos DEBEN ser estructurados y distinguir hechos observados, estimaciones y cobertura desconocida.
- **FR-023**: Los diagnósticos DEBEN permitir contrastar contadores de admisión con operaciones y recursos efectivos sin registrar credenciales ni contenido privado. Los totales no mezclarán procesos, agentes, mensajes y nodos ni sumarán dos veces recursos compartidos.
- **FR-024**: La política DEBE ser común a los proveedores mediante sus capacidades declaradas. Cambiar proveedor no permitirá eludir presupuestos, herramientas permitidas, recibos ni comprobaciones de propiedad.
- **FR-025**: Las instalaciones de un solo nodo y los contratos de aceptación, entrega, preguntas, ACK y recuperación DEBEN conservar compatibilidad. Una versión o nodo incompatible declarará su limitación y no provocará un modo de ejecución sin controles.
- **FR-026**: La aceptación DEBE conservar los plazos, cobertura, auditorías y criterios existentes. Elevar un timeout, reducir la carga, omitir memoria o contabilizar creación como ejecución no acreditará esta feature.

### Key Entities

- **Nodo y encarnación**: destino de ejecución autorizado, con identidad estable y una instancia concreta de proceso; no equivale necesariamente a un host físico independiente.
- **Dominio de recursos**: ámbito cuyo presupuesto se comparte, como host, nodo, almacenamiento o cuota de proveedor; permite evitar duplicar la misma capacidad en varios anuncios.
- **Observación de capacidad**: medida o estimación con origen, ámbito, antigüedad, validez y limitaciones; no es una reserva ni una autorización.
- **Política de admisión**: criterios y márgenes vigentes para admitir, reducir, esperar, recuperar y distribuir trabajo, con su versión y plazos declarados.
- **Reserva de capacidad**: consumo atribuible a una operación y sus dominios; conserva su estado en adquisición, activo, liberado o incierto.
- **Trabajo elegible**: mensaje u operación con identidad durable, autoridad, ubicación o restricciones, orden y estado; solo el que permite su contrato puede cambiar de destino.
- **Decisión de admisión**: resultado admitir/esperar/rechazar con causa estructurada, política, observaciones y ámbito autorizado.

## Success Criteria _(mandatory)_

### Measurable Outcomes

- **SC-001**: En un nodo con recursos suficientes se observan más de cuatro entregas activas a receptores distintos; una reducción controlada de capacidad reduce la nueva admisión y su recuperación vuelve a ampliarla dentro de los plazos declarados. En el 100 % de observaciones las reservas se mantienen dentro del presupuesto aplicable, salvo deuda explícita por una reducción sobre trabajo ya activo.
- **SC-002**: En una prueba real con al menos dos nodos autorizados, el nodo incorporado recibe y completa trabajo distribuible dentro del plazo de incorporación declarado. Con demanda suficiente y sin un recurso compartido saturado, la red completa más trabajo por unidad de tiempo que la misma carga sobre un nodo, sin aumentar el presupuesto de ese nodo ni cambiar código.
- **SC-003**: Con al menos dos coordinadores compitiendo por recursos compartidos, no se observa ninguna sobrerreserva, doble contabilización de capacidad ni reutilización de reservas de una encarnación anterior, incluidas adquisición y liberación fallidas.
- **SC-004**: En saturación, duplicación de avisos, cancelación, reinicio y partición se conserva el 100 % de los mensajes aceptados en un estado durable explicable, sin adelantamientos por receptor ni ejecución concurrente de entrada para la misma identidad. Los efectos inciertos se concilian y no se presentan como entregas confirmadas.
- **SC-005**: Con demanda finita, recursos estables y al menos un receptor lento, todos los receptores elegibles progresan dentro del plazo de equidad declarado. Las colas temporales y operaciones iniciadas permanecen dentro de sus presupuestos durante toda la observación.
- **SC-006**: Todos los caminos de entrega se ejercitan simultáneamente y respetan el presupuesto combinado y el orden por receptor. Ninguno obtiene capacidad adicional por usar un evento, un sondeo, una recuperación o un proveedor diferente.
- **SC-007**: Las pruebas de capacidad publican tamaños de muestra crecientes —incluidos 20 y 32 trabajadores cuando los recursos los admiten— y separan evidencia de un nodo, varios nodos y proveedores reales. Los tamaños probados son muestras, no techos del producto ni demostración de capacidad infinita.
- **SC-008**: Se mantienen los criterios originales de latencia, autorización, persistencia, auditoría y cierre aplicables. Cada intento conserva su resultado, cobertura, recursos y fallos; una mejora local no se declara aceptación de toda la red.
- **SC-009**: Durante una espera controlada de proveedor, las etapas de almacenamiento ya terminadas no conservan reservas o conexiones innecesarias. Se observa la liberación efectiva y que otra operación autorizada puede utilizar esa capacidad mientras la primera tarea sigue activa.
- **SC-010**: Al aumentar el inventario de nodos y el pendiente, el trabajo de admisión por mensaje y su memoria permanecen dentro de los presupuestos predeclarados; no aparece un recorrido completo de la red por cada mensaje ni una preasignación proporcional a agentes meramente anunciados. La prueba conserva los conteos de trabajo examinado y recursos consumidos.
- **SC-011**: En el 100 % de los casos provocados de presión, receptor ocupado, acceso denegado y cobertura parcial, el diagnóstico autorizado coincide con la causa y su frescura comprobadas independientemente. Los lectores restringidos y los registros no exponen los contenidos o secretos de control introducidos en la prueba.

### Condiciones de medición

Antes de ejecutar aceptación, el plan DEBE fijar un manifiesto de política y medición con los presupuestos por dominio, reservas de seguridad, umbrales de presión, caducidad de señales y plazos de reacción, recuperación, incorporación y equidad. Los plazos se expresarán en segundos; memoria en bytes o MiB con unidad explícita; concurrencia y conexiones en unidades enteras; antigüedad de cola en segundos; carga completada en operaciones por segundo. Los umbrales de CPU indicarán su normalización respecto a la cuota efectiva. Los valores deben ser positivos y finitos donde corresponda y conservarse en el resultado.

La medición independiente DEBE contrastar operaciones y procesos realmente activos, reservas y conexiones efectivas, cola durable y recursos de host/nodo, sin deducirlos únicamente de los contadores del nuevo controlador. Las condiciones de capacidad suficiente deben comprobarse antes y durante la prueba. Un caso sin sus prerrequisitos o valores fijados permanece pendiente; no se considera aprobado por utilizar un plazo todavía indefinido. Las ventanas de comparación, duración sostenida y carga se fijan antes de medir y no se reducen ante un fallo.

## Assumptions

- «Escalar sin un número fijo» significa ausencia de un techo global impuesto por el producto y posibilidad de sumar capacidad autorizada. No significa memoria, conexiones, cuotas o cómputo infinitos en un nodo o dependencia.
- El operador proporciona o autoriza nodos adicionales y las cuotas compartidas. Aprovisionar infraestructura de pago, instalar globalmente, cambiar credenciales y migrar sesiones vivas quedan fuera de esta spec.
- Los presupuestos, márgenes y plazos de reacción, recuperación, incorporación y equidad se fijarán y justificarán en el plan y sus pruebas antes de ejecutarlas; no se ajustarán después de un fallo para declararlo aprobado. No se introducen nuevos valores mágicos de concurrencia como solución.
- La entrega se divide en tres hitos verificables: **M1**, admisión dinámica local común a los caminos de entrega, con protección de recursos y orden; **M2**, reservas y presión coordinadas por dominio compartido, verificadas con varios coordinadores; **M3**, incorporación y colocación real de encargos elásticos ordinarios nuevos en varios nodos autorizados. M1 aporta valor por separado, pero solo M1+M2+M3 completan esta feature. Una simulación o una constante configurable no sustituye esa aceptación.
- Se reutilizan los contratos de ejecución/ubicación y autoridad existentes. Esta spec no amplía permisos MCP ni convierte `complete_assignment` en una operación válida fuera de los trabajadores elásticos configurados.
- La fuente durable sigue siendo PostgreSQL. La capacidad adicional de cómputo puede requerir ampliar una dependencia compartida mediante una operación separada y autorizada; añadir nodos no elimina ese cuello de botella.
- Los anuncios y las consultas del grafo son observación. Leer capacidad o agrupar equipos no reserva recursos, aprovisiona nodos ni demuestra ejecución.

## Dependencies and Current Evidence

- [021 — Persistencia PostgreSQL](../021-postgresql-persistence/spec.md): presupuestos y pruebas de persistencia, auditoría y capacidad. T148/T149 son evidencia acotada, no aceptación de esta feature. La corrección T149 conserva los cuatro consumidores existentes; esta spec define su evolución posterior, no modifica sus recibos históricos.
- [018 — Ejecución multiinstancia](../018-multi-instance-execution/spec.md): identidad, ubicación, confianza y recuperación entre instancias. Su existencia documental no demuestra todos esos recorridos implementados.
- [024 — Mensajes de trabajo](../024-work-agent-messages/spec.md) y [028 — Autoridad de delegación](../028-team-delegation-authority/spec.md): identidad, efectos y facultad de distribuir trabajo; capacidad no sustituye autorización.
- [035 — Federación observable](../035-graph-instance-federation/spec.md) y [038 — Recursos de agentes](../038-studio-agent-resources/spec.md): representación fiel de capacidad, ubicación y cobertura, sin atribuir efectos a la vista.
- Código contrastado al redactar: `services/inbox_service.py` limita por separado el consumidor y los barridos; `services/legacy_memory_audit_repository.py` limita el pool auditado; `services/settings_service.py` distingue profundidad de cola y límite opcional de terminales. La asignación elástica existente no acredita una política común de admisión adaptativa ni su aceptación multinodo. Estos son puntos de partida y brechas, no funcionalidades completadas por esta spec.
