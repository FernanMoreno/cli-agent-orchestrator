# Feature Specification: Admisión dinámica y capacidad distribuida de la red

## T014.R.H2.2b.2 — Propietario original y buzón financiado (validado por componente, 2026-11-03)

Un propietario opt-in conserva un único hilo no daemon y abre root_sql_control_channel/FundedControlSession dentro de ese worker, sin transferir sesiones ni abrir otro TCP. El llamante debe aportar una CommandReservation exacta, pendiente, positiva RAM/CPU y sin SQL, del mismo DeliveryAdmission que usa el monitor. No se crea owner, muestra, crédito, certificado ni autoridad de despliegue para sortear capacidad desconocida o shared-unready. La reserva positiva de infraestructura cubre el arranque y el ciclo de control; no autoriza entregas ni activación compartida.

La primera interfaz admite refresh tipado y diagnóstico cached versionado, sin submit(callback). La publicación de un refresh enlaza una reserva al hilo exacto ya financiado, antes de que el mensaje sea visible. El cargo permanece mientras existe el payload/resultado pendiente; presión o cancelación del solicitante no lo devuelven. Antes de SQL el actor comprueba generación/frescura/capacidad e identidad de la sesión. Rechazo y finalización se acreditan en su worker. Un buzón acotado por recursos sustituye un máximo fijo de mensajes/agentes; no hay executor ni hilo por solicitud.

Ready acredita canal/sesión originales operativos y primer ciclo observado, no capacidad de entrega ni integración productiva. Una sesión incierta queda terminal, sin replay/reconnect. El cierre detiene ingreso, resuelve sus mensajes y conserva cargos/propiedad hasta las pruebas físicas pertinentes. Timeout/cancelación solo separan al solicitante. El cierre async no bloquea el loop con join síncrono. El emisor productivo, wiring CLI/runtime, autoridad shared, participantes/custodia y los gates globales siguen separados.

## T014.R.H2.2b.1 — Arranque físico de un worker financiado (2026-11-03)

Antes de crear el hilo del propietario persistente, una reserva exacta de comando debe pasar atómicamente de pendiente a iniciada. La reserva procede de un dueño con capacidad fresca y costes positivos RAM/CPU y cero SQL; este componente no inventa observaciones ni sustituye al emisor de despliegue. El callback es código interno del servidor, nunca una entrada HTTP/MCP ni autoridad SQL.

El handle conserva el hilo exacto y su resultado. Stop, invalidación, reconfiguración, cancelación del solicitante y el retorno del callback no liberan el cargo del hilo. Solo la observación de retorno y el join físico de ese mismo hilo permiten retirarlo. Un error de Thread.start mantiene la reserva y entrega un handle reconciliable; no prueba que el hilo nunca existió. Copias, otro proceso, finalización manual del token y self-join no permiten retirar el cargo.

Aceptación: barreras con hilos reales prueban cargo antes de la primera instrucción, presión/stop entre despacho y entrada, resultado anterior a salida física, fallo de arranque antes/después del inicio y conciliación positiva posterior. La capacidad procede de recursos, sin techo global de workers. El actor, su buzón, el emisor productivo, SQL/shared y los gates globales permanecen separados hasta implementar sus dependencias.

**Feature Branch**: `main` — integración de especificación y código, sin nueva rama.

**Feature Directory**: `specs/041-resource-aware-network-admission`

**Created**: 2026-10-30

**Status**: Implementation in progress — T001–T012, T017, T022 y T023 validados en sus alcances. T013/T014 siguen parcialmente integrados; financiación productiva, cuotas, presupuesto físico/distribución y M1/M2/M3 continúan pendientes en T013–T016 y T018–T021.

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

### Configuración mediante SDK e interfaz

El operador debe poder consultar y cambiar presupuestos/atestaciones y parámetros de admisión mediante una API de control común, consumible por SDK e interfaz. Los dos clientes comparten esquemas tipados, unidades, validaciones, permisos administrativos y CAS de revisión; no escriben directamente archivos internos ni inventan estado del servidor. Un conflicto devuelve la revisión a reconciliar y no se sobrescribe automáticamente. La respuesta de registro se distingue de capacidad observada y de aceptación física. El SDK no crea ni renueva credenciales, y la interfaz no concede permisos adicionales por ser local.

Los cambios operativos editables desde clientes deben persistirse con revisión/auditoría en PostgreSQL. El archivo privado de despliegue mantiene el rol de activación/semilla durante la transición, sin convertirse en un canal de autoridad ni en un almacén alternativo de coordinación. La interfaz muestra desconocido, caducado, drenaje y conflicto de forma explícita. Un límite local no aparece como techo global de agentes, ni la maqueta se presenta como ejecución real.

La aceptación de esta extensión requiere el mismo recorrido desde HTTP, SDK y controles reales de interfaz: leer revisión, modificar con permiso, observar efecto, provocar conflicto, revocar y rechazar clientes sin autoridad; sin duplicar reglas en cada cliente.

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


## Precisión T014 — handoff durable de cierre físico confirmado

Un propietario original puede registrar durablemente el cierre de una operación solo después de confirmar su body, préstamos SQL y dueños nativos/helpers. Si cae tras ese registro y antes de devolver cantidades, un custodio admin independiente y ya financiado puede finalizar esas cantidades una vez mediante CAS/audit. No se infiere cierre de un estado releasing histórico, caducidad, PID o runtime.retired. El acuse libera recursos, no reejecuta ni completa Work, y nunca retira créditos de metadatos. Operaciones activas/inciertas sin ese testigo siguen cargadas. Cambios de autoridad/vista no impiden terminar recursos realmente cerrados de la encarnación original.


## Precisión T014 — observación tardía de dueños físicos ya cerrados

Si el backend confiable confirmó el cierre nativo completo, pero un helper exacto sobrevivió al join existente, la reserva debe conservarse y observarse de nuevo sin intervención manual. Solo se registran tokens con prueba positiva original; ni owners vacíos ni salida del Popen prueban cierre de contenedor/árbol. El observador trabaja únicamente sobre reservas realmente admitidas de su dueño local, no agentes anunciados, y no crea un pool o techo global. Callbacks y observaciones quedan fuera de locks de admisión. Errores conservan cargo, identidad y registro; las cadencias solo pautan, nunca expiran autoridad.

La observación usa los workers de control existentes y sigue disponible durante el drenaje antes de retirar metadatos. Un helper que termine permite exactamente un retiro y el posterior handoff durable ya implementado. No ejecuta SQL de reparación de negocio, replays ni cierres históricos. Un contenedor/arranque cuyo cierre completo siga sin probarse queda cargado: su limpieza activa/reconciliación es otro protocolo, no una inferencia del observador.


## Precisión T014 — arranque nativo sin acuse observable

Antes de ampliar cleanup automático, contrastar que una excepción de arranque sin handle no se convierte en prueba de no-arranque. Si un helper de CLI/attach pudo iniciarse pero su acuse/handle no llegó al propietario, la ausencia posterior de contenedor/imagen no demuestra su terminación. Ese desconocido conserva el cargo y no entra al registro de cierres tardíos confirmado; tampoco un futuro cleanup de artifacts ni el paso de tiempo puede sustituir la prueba del helper exacto. Distinguir fallo previo al intento, resultado de proceso observado y arranque cuyo acuse se perdió. No ampliar permisos ni resolver Work/replay para obtener verde.


El mismo contrato de arranque sin acuse aplica a Bubblewrap: ninguna excepción de Popen sin handle devuelto acredita no-arranque ni limpieza física; se conserva el cargo y la excepción tipada declara limpieza incierta.


## T014.C1 — sello irreversible de generación (componente validado; cierre/créditos pendientes)

El retiro futuro de créditos requiere primero impedir productores posteriores de la generación exacta. Un sello durable no prueba cierre físico y no devuelve ningún cargo. Solo la composición confiable de un participante con ciclo terminal puede solicitarlo para su propio binding/PID; jamás se infiere desde runtime.retired, TTL, revocación o PID ajeno. stop normal de API conserva la reactivación en el mismo proceso. El único productor inicial será cleanup terminal del Bridge; el método de runtime y protocolo de persistencia serán comunes a todos los participantes terminales.

Serializar sello contra adquisiciones nuevas y primera activación mediante el lock de membresía/generación. Replays exactos de recibos ya aceptados siguen siendo lecturas del resultado, no permisos para otro efecto. Lecturas y cierre de operaciones, testigo original, aborto, devolución auditada y revocación siguen posibles. Rechazar nueva cobertura/activación, reserva raw o mapeada, crédito o membresía activa de la generación sellada. Otras generaciones quedan intactas.

Persistencia aditiva v7 con tablas/checksums1–6 conservados, sello append-only y ACL efectiva SELECT+INSERT sin UPDATE/DELETE ni privilegios peligrosos/PUBLIC. Capacidad de sello opaca ligada a la composición/binding; no recibirla por HTTP/MCP ni fabricar principal admin para un hecho de proceso. SQL del sello conserva financiamiento normal hasta cierre real; fallo o acuse perdido no puede omitir el drenaje ni liberar créditos. El sello no implementa el testigo final de generación ni su custodio/decremento.


## T014.C2a — observación local de salida del proceso (componente validado; generación pendiente)

Un supervisor local de confianza conservará el handle de su propio hijo y verificará su binding por un canal heredado con identidad del emisor comprobada por el kernel. Solo la recogida del hijo propio y notificación positiva de su descriptor fijado producirán una observación de salida para el binding original. Ausencia de PID, error, descriptor cerrado, timeout o datos de otro proceso no constituyen prueba. La referencia no se transfiere por copia, serialización ni fork. No se envían señales ni se recorre el inventario de agentes.

La observación acredita únicamente la salida del proceso fijado: no prueba cierre de descendientes, contenedores, sesiones PostgreSQL del servidor ni toda la generación. No es autorización de devolución de créditos. La integración con el despliegue y el custodio financiado y auditado sigue siendo requisito de T014. El contrato es común a proveedores; Linux sin soporte pidfd conserva desconocido.


## T014.C2b.P1 — origen SQL del proceso supervisado (componente validado; inventario pendiente)

Las conexiones PostgreSQL abiertas por un proceso supervisado llevan una etiqueta aleatoria estable para ese proceso, independiente del proveedor y de sus lifetimes lógicos. El fork tiene otra identidad; no hereda la atribución del padre. El testigo de salida conserva la etiqueta recibida por el canal autenticado antes de admitir el runtime. La activación es interna al supervisor; un proceso no marcado conserva el nombre legado y no obtiene por ello una prueba de sesiones.

La presencia de la etiqueta permite localizar actividad positiva en el servidor. Su ausencia no demuestra cierre: puede haber aperturas en vuelo, etiqueta modificada, visibilidad insuficiente o pooler. No retirar créditos con una consulta vacía, ProcessExitObservation, sello C1 o estado retirado local. El sello C1 sigue permitiendo SQL de finalización financiado; no convertirlo en una prohibición general de aperturas físicas. El inventario durable de intentos previo al TCP y el cierre terminal de productores físicos siguen pendientes antes del custodio liberador.


## T014.C2b.P2a — cierre local de admisión de aperturas SQL (componente validado; inventario pendiente)

Un proceso supervisado que termina su entrypoint cierra irreversiblemente la admisión de nuevos intentos físicos SQL. Los tres caminos nativo/ORM/auditoría comprueban este cierre antes de invocar psycopg.connect, bajo el mismo lock local que serializa la decisión de cierre. La decisión no mantiene el lock durante TCP ni espera un connect ajeno. Intentos ya admitidos pueden completar o fallar después del cierre: su incertidumbre no desaparece. Las sesiones existentes pueden ejecutar SQL de limpieza, devolver préstamos y cerrarse.

El cierre se ejecuta al terminar el main de Bridge, después del intento de limpieza completa, también con error temprano, cancelación o BaseException; C1 y sus consultas financiadas anteriores conservan su contrato. Proceso no supervisado conserva el recorrido legado. Fork posterior al cierre conserva el bloqueo con otra identidad SQL y contador local nuevo; no se atribuye cierre del árbol ni inventario heredado. No hay reapertura/reset público ni transferencia de dueño a otro PID. El estado de intentos en vuelo es memoria local de diagnóstico, jamás ausencia del servidor ni autoridad durable.

P2a no cierra T014: quedan intents financiados durables ANTES de TCP, inventario completo original, custodia externa y retirada auditada. Un contador local en cero, fallo connect o fin de cliente no prueban ausencia de PostgreSQL.

P2a aporta el cierre local de admisión; la composición durable de ese cierre con el inventario completo y el custodio sigue pendiente.


## T014.C2b.P2b — intents PostgreSQL mediante canal prefinanciado (componente scoped validado; activación completa pendiente)

Registrar durablemente cada intento antes del driver cuando una composición confiable instala el registrador en su worker físico. El registrador solo usa una Connection nativa propia ya abierta con préstamo vigente de control, PID/Thread/task originales y scope aún activo. No abre conexiones propias, no tiene fallback ni reutiliza una transacción ajena para emitir un acuse que pudiera revertirse después. Copia/fork/cambio de hilo/task, cierre o préstamo terminado rechazan antes de SQL.

Un componente aditivo sql_opening_intents conserva cuotas owner-written en unidades de registros y canales/intents/fences runtime append-only. La primera inscripción confirmada fija el backend original de control; no autoriza a otro backend a adoptar el writer. El actor dueño puede provisionar para un participante explícito con su binding/crédito original, sin adoptar su identidad. No se convierte una sesión postgres_session en bytes o filas ni se declara que un crédito existente sea automáticamente el intent del bootstrap. La cuota finita explícita es un límite de crecimiento lógico, no aceptación de almacenamiento físico T013. Insertar y acusar exige commit independiente; acuse perdido conserva UUID y nunca permite TCP ni reutilizar el mismo registro para otro efecto.

El fence durable de inscripciones serializa con nuevos intents, sin confundirse con C1: el SQL financiado de finalización sigue necesitando registrar sus aperturas. Un sello C1 no cancela el registro de esos intentos. La instalación es scoped al worker; todas las3rutas usan el mismo callback previo al driver. Reentrada del callback rechaza, jamás omite el registro para evitar recursión.

No se activa aún el registrador globalmente en Bridge: faltan canal persistente/broker concurrente y bootstrap explícitamente financiado anterior a las primeras conexiones. Un intent/contador/fence/etiqueta no prueba ausencia ni devuelve crédito. Jobs históricos y credenciales del operador no se alteran.


## T014.C2b.P2c.A — identidad autenticada antes del primer SQL (orden validado; financiación pendiente)

Un Bridge lanzado con el canal interno del supervisor publica su binding original y espera el acuse autenticado antes de ejecutar init_runtime_db, restaurar estado, crear backends o realizar SQL de arranque. Un rechazo o fallo de publicación impide cruzar ese primer límite, incluidas excepciones BaseException. La configuración básica del canal (servidor, runtime y token) se valida antes de publicar. La misma instancia bootstrap se conserva para la admisión posterior; consumir el FD no crea una segunda generación. El recorrido sin supervisor mantiene su inicialización perezosa.

Esto solo corrige el orden del entrypoint: el ACK actual no acredita intención durable, presupuesto ni ausencia de sesiones. Parent genesis, financiación del primer TCP, broker, inventario y retirada auditada siguen pendientes. No introducir una excepción implícita de bootstrap para declarar cobertura completa.


## T014.C2b.P2c.B — inscripción durable del arranque de un hijo verificado (composición explícita validada; sesión pendiente)

Una composición confiable puede retener el acuse de identidad hasta registrar durablemente el intento previsto de arranque SQL de su hijo propio. La identidad sujeto debe provenir de la comprobación kernel/pidfd del supervisor, estar vigente solo durante ese handshake y no transferirse a otro PID/hilo/copia. El registrador es el padre actual con su canal/préstamo original financiado; no adopta el binding del hijo ni abre otro TCP. Tras commit confirmado, el supervisor vuelve a comprobar la misma identidad viva antes de acusar. Fallo, incertidumbre o reentrada impiden el acuse y conservan al dueño para join.

El intento y su sujeto se insertan atómicamente bajo la quota lógica/ordinal del journal del padre; una misma encarnación física del hijo no recibe otro registro de arranque ni por otro journal. Lectura posterior es descriptiva, sin permiso de repetir. Mantener protocolo2 sin hook como identidad solamente, sin atribuirle registro. No convertir este registro en financiación de sesión del hijo, token de un solo TCP o cobertura de conexiones posteriores. Financiación raíz/primera sesión, broker/activación completa/custodio siguen pendientes.


## T014.C2b.P2c.C1 — cargo previo del padre para una sesión del hijo

Composición explícita: antes de registrar/acusar el hijo, el canal nativo original y financiado puede consumir una unidad ya reservada por una reserva de datos iniciada y con retención de retiro del mismo dueño. El permiso de control no genera capacidad de datos; no ampliar footprint ni volver a cobrar la unidad. En modo compartido se exige preparación activa confirmada de esa reserva, sin SQL recursivo ni transferencia a otro worker. La identidad positiva del hijo solo permite un cargo por handshake; datos/copias/fork/hilos ajenos no conceden autoridad.

El préstamo queda retenido por su reserva original después de cierre de canal, fallo de registro/commit/ACK, expiración o muerte del hijo. Ningún timeout/GC/fin del padre devuelve esa unidad. Cargo opaco separado del recibo durable; no es permiso serializable del hijo ni prueba de ausencia. La vía sin cargo sigue siendo identidad/registro solamente. Este corte implementa el débito previo del padre; transporte de permiso de un arranque, enforcement del primer TCP del hijo, financiación raíz, broker y custodia permanecen pendientes.


## T014.C2b.P2c.C2 — permiso del primer TCP del hijo (modo explícito)

Un hijo propio puede optar a un protocolo3 distinto del ACK2 de identidad. Antes de recibir permiso, el gate físico común rechaza todo TCP SQL. El padre solo emite un paquete estructurado tras cargo C1, commit B confirmado y revalidación del sujeto/cargo originales. El hijo autentica al emisor real por credenciales kernel, valida esquema/versiones/identidad/IDs/destino/path y consume un permiso de una sola apertura antes del driver. El permiso limita el coste físico pagado; no fija un techo de agentes.

No hay downgrade a b1 ni modo legacy después de error/agotar permiso; fork, copia, otro worker y otro destino/path no pueden apropiárselo. Un fallo de registro/driver consume el intento admitido y no devuelve el cargo. El fence terminal sigue siendo irreversible y las sesiones ya abiertas conservan uso. El inventario/ausencia/custodia no se deducen del permiso.

Bridge no opta automáticamente a este modo: su arranque posterior necesita más aperturas financiadas. Un lanzamiento estricto que invoque el publisher de identidad se rechaza antes de SQL. El componente acepta un hijo mínimo con adaptadores reales; no acepta todo Bridge/proveedor, árbol de procesos, root, broker o activación global. La presencia del marcador reservado sin un canal/permiso válido nunca autoriza SQL.


## Alcance comprobado del permiso — T014.C2b.P2c.C2

La garantía corresponde al runtime confiable y al primer efecto pagado, no a código Python arbitrario. Se conserva modo estricto si se limpia el entorno después de importar el runtime; eliminar el marcador antes de importarlo o ejecutar con entorno limpio queda fuera. El permiso es de PID/Thread/task y linaje exec exactos. Emisión, aceptación y conexión física son hechos separados. Bridge completo y financiación/custodia global permanecen abiertos. [Validación](validation-t014-child-sql-permission.json).


## T014.C2b.P2c.C3.A — solicitudes posteriores ligadas al arranque

Cada apertura posterior necesita una solicitud durable propia anclada al intento de arranque del mismo hijo y journal. Conservar las filas y unicidad del arranque: no reinterpretar su permiso ni el cargo C1. La nueva solicitud exige sujeto kernel positivo todavía vivo, mismo actor/canal original y destino perteneciente al binding; secuencia consecutiva positiva por arranque y UUID global sin reutilización entre tipos. Locales, arranques y posteriores comparten ordinal, cuota y fence del journal.

El commit se observa antes de devolver el recibo. Incertidumbre conserva IDs; comprobación fallida después de commit conserva recibo confirmado. No se crea capacidad física ni se autoriza TCP por registrar solicitudes. Migración SQL3 aditiva preserva SQL1/2 y resource1–7; lectura descriptiva sin replay. Broker persistente, financiación por solicitud, renovación autenticada fuera del handshake, root y activación Bridge quedan pendientes.

Path, usuario/TLS y scope pueden variar en cada solicitud dentro del store y target del binding original; el recibo conserva esos datos y no autoriza ninguna operación. La autoridad por operación y el cargo pertenecen al futuro broker.


## T014.C2b.P2c.C3.B — canal persistente y permiso por apertura

Opt-in explícito sql_broker junto a first_sql_grant. Mantener los protocolos 2/3 y startup_spent irreversible. Un segundo socket SEQPACKET heredado y reservado conserva el canal de solicitudes sin reinterpretar ni alargar el ACK inicial. Solo el handshake inicial autenticado puede instalar el cliente; cada mensaje posterior exige emisor SCM original, vista kernel vigente, UUID de solicitud, ancla y secuencia estricta, path y destino exactos. Datos construidos por sí solos no instalan autoridad.

El padre atiende una solicitud por llamada en su PID/Thread/task originales: sujeto nuevo y efímero, una unidad prepagada de reserva de datos iniciada/held/preparada, registro y asociación durable del cargo en la misma transacción, revalidación y respuesta. SQL4 añade un mapeo append-only unívoco session_id↔intent de arranque o posterior, conservando SQL1–3 y resource1–7. El cargo solo puede asociarse una vez por el canal original; no equivale a custodia o recuperación financiada.

El gate común pide un permiso por apertura posterior y lo consume en el worker que solicita, sin I/O bajo su mutex ni mantener el lock de intercambio durante el driver. No reinicia el permiso inicial. Fork cierra el canal heredado y conserva rechazo; timeout después de envío, EOF, schema/emisor inválido o commit incierto terminan el broker sin retry/refund automático. Las sesiones abiertas siguen utilizables. El presupuesto limita recursos del nodo, no fija número de agentes; no crear hilos por agente. Bridge/root/inventario/custodia y los diez gates permanecen pendientes.

La espera del servidor sin mensaje devuelve un estado Idle explícito distinto del EOF; no consume unidad, secuencia ni SQL. Un error local previo al envío o exchange ocupado se rechaza sin envenenar a otros workers. Una solicitud enviada y no confirmada termina el broker; posteriores denegaciones locales no inventan otra incertidumbre. El cierre y la instalación son monotónicos, y el task/canal originales se verifican antes de leer IPC.


## T014.C2b.P2c.C3.C — Bridge con padre financiado

El entrypoint Bridge supervisado debe seleccionar explícitamente el publisher de permiso cuando existe el marcador reservado, conservando rechazo de marcadores inválidos y el recorrido de identidad legado. El adaptador ofrece broker opt-in y un contexto funded_bridge con canal de control original ya financiado, repositorio/journal, UUID de intento y reserva de datos prepagada. Validar propiedad y destino canónico antes de Popen. Confirmar cargo/plan antes de permitir SQL inicial del Bridge real; atender aperturas posteriores con serve_child_sql_request desde el mismo worker, sin hilos, pools o límites de agentes nuevos. El contexto siempre une y cierra el hijo propio, preserva el error primario y nunca devuelve cargos por salida/EOF. No activa automáticamente el CLI de raíz no financiado: genesis, inventario y custodia siguen pendientes.


## T021.T — errores de tipos preexistentes en la integración

El chequeo de tipos en frío debe dejar de emitir los ocho errores reproducidos en las dependencias de Bridge, sin ignorar diagnósticos ni cambiar mypy.ini/opciones. Conservar lector OpenCode nativo, protocolo de artefactos Work y correspondencia de alias/owners Herdr. La ausencia de un pane previo no es una clave str y debe tratarse antes de consultar el mapa.


Ampliación T021.T: el chequeo completo src/ debe resolver también el retorno Any de la clasificación JSON de task_not_found. El error ya existe fuera del cono del chequeo de dos entrypoints; conservar la igualdad exacta y no convertir outages en ausencia.


## T014.R.G — asignación independiente antes del primer TCP root

Génesis requiere emisor independiente ya financiado y autorizado como administrador/dueño; el root no puede consultar PostgreSQL para pagarse su primer TCP. Registrar previamente una apertura de control de una unidad mediante los créditos metadata storage/postgres_session existentes del binding exacto, sin cobrar otra capacidad ni convertirlos en reserva de datos. Exigir conjunto completo de créditos de almacenamiento y su fingerprint, cuota/ordinal/UUID comunes, migración aditiva owner-only. Commit confirmado y fila real antes de firmar Ed25519; datos o lectura del recibo no bastan para firmar. Certificado acotado a kernel/binding/label/destino y un uso; verificación con claves de despliegue confiables antes del driver. Sin refund/TTL/rearm/fork. El origen real del provisionador es dependencia explícita, no se declara integrado por crear interfaces.


T014.R.G4.D — diagnóstico de rechazo previo al subject: conservar en el error genérico una etapa y un tipo de excepción estructurados, con códigos fijos y sin payload, identificadores, entorno ni mensaje original. El diagnóstico no acredita una causa no observada ni modifica guards, ACK, cierre, deadlines o permisos. Motivación: fallo real de handshake previo a la inyección commit_ack; el wrapper anterior suprimía el primer límite.

G4.D: el error conserva además una única nota JSON canónica `caos.resource-binding-rejection.v1` visible en traceback/JUnit, con únicamente `schema`, `failure_stage` y `failure_type`; `str`, args y decisiones permanecen iguales.

G4.D compatibilidad: los atributos están disponibles en Python>=3.10; la nota de traceback se añade únicamente cuando el runtime dispone de `add_note` callable. Probar ambos modos sin cambiar el requisito de Python ni el rechazo original.

G4.D2: reproducir y corregir las dos rutas de launch que invocaban add_note sin soporte; conservar la excepción original y, tras Popen, el owner exacto para join. Las notas opcionales nunca deben reemplazar el fallo primario. Dos negativos ejecutan ausencia de método y limpieza real del hijo, sin afirmar aceptación nativa completa de Python3.10.


### T014.R.H1 — puertos de control sobre el canal original

El repositorio de admisión y el muestreador PostgreSQL deben poder usar una sesión de control explícita ya financiada, en el PID/thread/task/scope originales. La sesión no abre, adopta, cierra ni sustituye conexiones ni concede autoridad de activación. Cada operación comienza desde IDLE, posee su transacción externa real y confirma commit; no se admite reentrada/savepoint como confirmación, fallback a connect ni uso tras cierre/incertidumbre. El repositorio conserva binding, roles/ACL, auditoría y validación de uso; los escritores owner/migración se rechazan en este modo. Los reads mantienen aislamiento/read-only y el timeout LOCAL del muestreo no persiste fuera de su transacción. Un fallo de cuerpo con rollback confirmado puede permitir otra operación; commit incierto bloquea nuevas operaciones sin liberar deuda ni cerrar el handle del dueño. El camino legado sin sesión mantiene su comportamiento.

Aceptación: proceso raíz/PG reales, driver count1 y backend estable para auditoría, créditos/cobertura, contador/deuda, reservas/transiciones y muestras sucesivas; permisos, binding, target, worker, scope, cierre, reentrada y ACK perdido se verifican. No introducir hilos ni un máximo global de agentes. Actor, cola/admisión de comandos, activación shared con préstamo vivo, monitor/CLI productivos y custodia siguen pendientes y no se acreditan por estos puertos.

H1.3 precisión de worker: `check_worker` valida el scope original incluso dentro de su transacción propia, sin SQL ni exigir IDLE; `check_target` añade IDLE/no-busy para iniciar una operación. La auditoría niega reentrada antes de adquirir sus locks; la configuración de sesión del repositorio es permanente, sin bind/unbind mutable ni fallback.


### T014.R.H2.1 — autoridad y monitor sobre el worker original

El repositorio de envelopes y ResourceBootstrap deben ofrecer lecturas explícitas por FundedControlSession, conservando target fresco, validación de roles/esquema, binding/claims/revocación y comparación antes/después. No conectar ni promover credenciales al rechazar sesión. Append/CAS privilegiado queda en su camino separado. AdmissionMonitor opt-in fija una sesión original y su StorageControlPermit exacto en construcción; verifica afinidad antes de locks/cierre/configuración y reutiliza el mismo backend en las dos lecturas de autoridad y presión de cada ciclo. No anidar control.operation ni transferir el handle mediante to_thread. El arranque asíncrono legado se rechaza para un monitor ligado a sesión antes de efectos; los ciclos síncronos originales siguen serializados. No iniciar writers administrativos en ese monitor.

Aceptación: PG y root separados reales con una única apertura, envelope/lecturas ledger y monitor repetidos, crecimiento por observaciones recientes, binding/revocación que cierran admisión, readonly/rollback y ninguna conexión secundaria. Negativos worker/task/scope/permit ajeno/busy se rechazan antes de flags/locks/SQL. Commit incierto propaga y mantiene bloqueo/deuda. No cambia esquema, privilegios, límites ni autoriza shared sobre préstamo vivo. El actor/cola/admisión de comandos/drain y wiring productivo siguen en H2.2; este corte no acredita diez gates globales.


H2.1 precisión de generación: cada refresh conserva un ticket local de la generación que posee el ciclo. Tras reconfigure confirmado actualiza el ticket antes de más I/O; ante fallo invalida esa generación exacta, nunca una anterior ni la generación actual arbitraria del owner. Probar fallo tras reconfiguración propia y conservación de una reconfiguración/observación externa posterior; no ampliar la CAS a otro escritor. El monitor sigue siendo el publicador de observaciones de su ciclo.


### T014.R.H2.2a — comandos por recursos y diagnóstico negociado

Reservar comandos internos con huella RAM/CPU positiva elegida por el servidor y storage_slots=0 cuando reutilicen el canal financiado. Separar identidad/pending/counters de comandos de receivers, storage-only y preparación shared; sumar sus costes al mismo committed/debt y a inflight/started totales. Verificar capacidad reciente por componentes y generación al reservar e iniciar, sin depender del mínimo de unidades de entrega. Cancelación previa libera solo pendientes; después de iniciar únicamente el PID/thread/task físico original puede terminar el cargo. Reconfigure/invalidate/stop liberan pendientes y retienen iniciados; presión cede pendientes relevantes más nuevos, sin escanear iniciados ni imponer cantidad fija. No se crea crédito SQL, permiso de activación, actor o hilo.

El formato estricto de diagnóstico v1 se mantiene cuando no hay comandos. Añadir v2 explícito con operaciones.commands.inflight/started y conservación total=deliveries+storage-only+commands. La petición v1 con comandos activos recibe409 estructurado diagnostics_version_required, required_version2 y supported_versions[1,2], sin datos falsos ni errores de validación; las versiones HTTP desconocidas se rechazan422. Mantener autenticación y no-store. Pruebas incluyen25 reservas sintéticas y25 sobre el ledger de un root/PG real; no equivalen a25 proveedores/modelos reales. Actor, bootstrap de su RAM/CPU, cola/drain/sellado y SDK/UI global siguen pendientes.


ECC H2.2a: los errores de query del diagnóstico usan HTTP422 con detail.code=invalid_diagnostics_query y no-store, sin reflejar entradas ni etiquetarlos como envelope. El resto de rutas de recursos conserva invalid_resource_envelope. Separar la política de validación de la ruta diagnóstica y documentar/probar el DTO422; mantener JWTadmin y la forma v1/v2. Primero reproducir el cuerpo incorrecto en el test ASGI, después corregir sin rebajar el status ni la cobertura.

## T014.R.H2.3 — activación de control anterior a SQL (componente opt-in validado)

El certificado SQL solo paga su apertura; no financia cómputo. Un paquete de activación distinto, versionado, acotado y firmado con dominio propio enlaza ese certificado, el envelope activo completo/revisión de PostgreSQL, el sujeto/kernel/destino exactos y costes positivos RAM/CPU sin SQL de arranque y refresh. Las claves confiables llegan por composición de despliegue, nunca dentro del paquete ni desde la identidad peer. El emisor requiere Principal admin verificado y canal original de migración ya financiado, journal/cuota y cobertura metadata existentes; no provisiona autoridad ni amplía grants para emitir. Firma únicamente después del commit confirmado y la lectura protegida del envelope del otro proceso.

El consumidor previo a init_db verifica formato/firma y binding actual sin SQL. Reclama la activación una sola vez por proceso, sin gastar el permiso SQL, antes de crear ledger/muestrear. Un sampler Linux local mide RAM/CPU con la cadencia y frescura vigentes. Se reserva una obligación startup-only en el mismo DeliveryAdmission; no se inventa storage disponible ni una CapacityObservation conocida, y la admisión normal permanece desconocida/shared-unready. Entrada exacta del hilo revalida cómputo; el cargo sigue hasta retorno/join. El primer canal raíz relee intent/envelope y exige igualdad exacta de revisión/claims/binding/active antes de publicar capacidad o informar ready. Incertidumbre o deriva no autoriza retry/refund.

Aceptación: firma/trust separados, claves/targets/kernel/revisiones/costes inválidos rechazados antes de sampler/SQL; replay/fork/copia/clock/presión; startup positivo con storage desconocido sin habilitar datos; productor sobre canal PostgreSQL propietario real y primer readback. SDK/entrada de proceso consumen el paquete sin generar autoridad. Una medición no verificable falla cerrada y no se sustituye por fixture para aceptar capacidad física. No declarar emisor de despliegue central, integración API completa ni M1/M2/M3/global por este consumidor opt-in.


## T014.R.H2.4a — vida de proceso financiada (primitiva opt-in validada)

Antes de componer el supervisor de despliegue, un propietario interno genérico debe enlazar una reserva CommandReservation existente (RAM/CPU positiva, storage cero, sin basis de activación root) con el ResourceProcessSupervisor exacto. La admisión y el inicio se verifican en el mismo ledger y contexto padre antes del intento Popen; no crear otro contador fijo ni crédito SQL. La reserva iniciada no puede terminar mediante finish/cancel, stop, invalidación o reconfiguración mientras falte la prueba de retiro del proceso.

El propietario no es transferible entre PID/hilo/tarea. Retiene el handle de supervisor retornado o adjunto a un fallo de arranque; una excepción sin handle mantiene una obligación incierta no reembolsable y sin retry. Binding/exit y cierre nativo se obtienen fuera del guard del ledger. Solo una ProcessExitObservation auténtica del supervisor exacto tras handshake, más cierre positivo de sus handles, permite retirar su cargo; timeout, wait sin prueba, reaper ajeno o close fallido lo conservan. La prueba acredita únicamente ese proceso, no descendientes, conexiones SQL, créditos ni custodia de generación.

Aceptar con RED/GREEN de API/lifecycle, negativa de finish prematuro y transferencias, fallos parciales/close/espera, y procesos Linux reales con handshake/reap y cargo retenido. Mantener reservas de comandos/hilos/actor y fuente aceptada H2.3. No declarar el productor de despliegue, transporte del paquete, integración API/shared o diez gates globales terminados por esta primitiva.


## T014.R.H2.4b — Root CLI y transporte privado (en curso)

Componer CommandProcess con Root CLI mediante un segundo socket AF_UNIX/SOCK_SEQPACKET opt-in. El ACK de binding v2 y el modo stdio heredado conservan su contrato. El selector root_control_channel exige root genesis restrictivo y first_sql_grant/broker False; ni ese selector ni el FD conceden SQL, cómputo o confianza de clave. El padre deriva un RootSqlSubject inmutable de binding/etiqueta/identidad kernel autenticados; no acepta un subject descrito por el hijo. Mover su modelo puro con reexport compatible desde root_sql_control.

El canal transporta únicamente activación firmada (máximo32KiB), ready/error, refresh y stop/stopping, con versión/secuencia/esquema estrictos, datagramas acotados y una petición en vuelo por canal. Verificar credenciales kernel por paquete, PID/UID y linaje del endpoint; SO_PEERCRED no identifica al hijo en un socketpair creado por el padre. Rechazar truncamientos, tipos desconocidos, duplicados y SCM_RIGHTS, cerrando derechos recibidos. No reader thread ni cola/buffer ilimitados. Fork o contexto ajeno invalidan endpoints sin ejecutar SQL ni esperar bajo locks heredados.

Root CLI adopta el FD antes del bootstrap, publica su binding sin permiso SQL y espera la activación. Solo después de verificación de cómputo y certificado abre el primer canal. El canal privado no utiliza stdin/stdout para protocolo ni cae a stdio si el FD está mal. Conservar el camino stdio cuando no hay selector. Los waits de petición/respuesta tienen deadline finito; la espera inactiva del hijo usa polls acotados y no muere por un plazo arbitrario mientras su padre siga vivo. EOF solicita cierre; ACK stopping no acredita salida ni retiro.

Una composición en dos fases valida administrador y canal original financiado antes de Popen: lanza/obtiene subject; luego activate usa envelope, journal/cuota y créditos ya provisionados por el operador, llamando al issuer existente sobre ese mismo canal. No provisionar autoridad ni abrir conexiones de reparación, no callback arbitrario para emitir autoridad. Intento de activación/envío único; error o ACK incierto poscommit conserva handle/record/cargos, sin reemisión/refund. Preservar afinidad PID/hilo/tarea y handles de errores; cleanup cierra el socket privado antes de esperar al hijo sin liberar la obligación incierta.

Aceptar un recorrido PostgreSQL real a través del módulo CLI, con identidad antes de SQL, issuer sobre owner ya financiado, ready/refresh/stop y retiro solo tras observación/cierre positivos. Añadir negativos de framing/credenciales/fork/FD/timeout/replay/rechazo y compatibilidad stdio/defaultkwargs. Esto implementa transporte/composición opt-in; no configura el emisor/autoridad de una instalación, no cablea la API central ni cierra shared/custodia/M1/M2/M3/SDKUI/diez gates globales.


### H24b cierre físico incierto — requisito explícito

Un fallo de cierre del descriptor Root es incertidumbre física terminal, aunque un reintento posterior cierre el socket. No confundirlo con timeout/error RPC cuyo cierre sí fue positivo: este último conserva la posibilidad de join auténtico. No retirar CPU/RAM tras un close incierto.


**2026-11-04 · H24b validado como componente interno opt-in.** Root CLI, publisher y transporte privado autenticado conectados a proceso financiado y activación en dos fases. Emisor único sobre su conexión original; provisión externa para subject vivo, gate de reentrada y registros/cargos conservados. 626PASS/10advertencias/39módulos,572IDs previos+54nuevos;3recorridos Root/PG reales. Tipos467, arquitectura5/0,14Python/8fuentes archivo-exactos y PostgreSQL0/0/0. Sin nuevo techo agregado fijo. El primer diagnóstico2fallos/2pases fue un defecto del arnés de préstamo SQL y se conserva fallido. No acepta autoridad productiva/API, shared, custodia, modelos, M1/M2/M3, SDK/UI restante ni los diez gates originales. [Validación](validation-t014-root-control-deployment.json), [composición](composition-t014-root-control-deployment.md).


## T014.R.H2.4c — operador financiado y API opt-in (2026-11-04, en implementación)

US7 (P1, H24c): un administrador autenticado puede iniciar un Root independiente, consultar su sujeto real, pedir su activación después de la provisión externa y controlar su cierre. La aplicación conserva al propietario aunque la petición se cancele.

Requisitos: autenticar JWT admin antes de resolver configuración, claves, SQL o procesos; cada control posterior requiere nuevamente administrador y la misma identidad de operador. Las dependencias de despliegue (ledger ya financiado, permiso de almacenamiento, bootstrap, argv/env, proveedor de firma y costes) proceden exclusivamente de composición confiable del servidor. Ningún campo de petición, selector de entorno o modo local crea estas capacidades. No generar ni reutilizar claves peer; la instalación proporciona el signer y el trust del hijo.

Un único trabajador de control por runtime mantiene su conexión original y todos los handles en el mismo PID/hilo/tarea desde launch hasta cierre. Financiar el trabajador antes de iniciarlo, el proceso antes de Popen y los controles antes de publicarlos. No transferir conexiones ni crear un hilo por petición. Una petición de control en vuelo refleja la serialización del propietario; no limita la cantidad de agentes de la red. Rechazar trabajo concurrente de control sin colas ilimitadas.

Launch entrega el subject autenticado para provisión externa; activate consume journal/intent/session ya autorizados, emite y entrega una sola vez sobre la conexión original. No auto-provisionar envelope/cuotas/créditos ni abrir conexión de reparación. Mantener records y handles ante fallo/commit incierto. Cancelación HTTP no cancela ni repite efectos; la siguiente lectura observa el estado retenido. Stop/ACK no son retiro; cierre completo requiere evidencia positiva antes de devolver cargos.

Aceptación: contrato estricto versionado de launch/status/activate/refresh/stop, JWT admin real en frontera, negativos auth-off/noadmin/lookalike/otrooperador/busy/cancelación/replay/fallo poscommit y cierre incierto. Componer el router en la API existente con runtime opt-in instalado por código confiable; ausencia devuelve503 sin fallback. No adoptar el PID de la API como Root después de init_db. Pruebas controladas no acreditan instalación de autoridad, trust productivo ni capacidad M1/M2/M3/modelos. Diez gates originales siguen abiertos.


**2026-11-04 · H24c validado: operador y API Root opt-in.** Runtime financiado con un worker/conexión original persistentes, JWT admin/identidad estable, comandos estrictos, cancelación retenida y cierre antes del teardown API. Publicación de respuesta y retiro del cargo atómicos; fase cached no prueba que nunca se activó. 748 PASS/10 advertencias/53 módulos:626 IDs conservados+58 nuevos+64existentes adicionales. Cuatro recorridos Root/PG reales (uno nuevo HTTP), tipos de 470 fuentes, arquitectura 5/0,12 Python/4 fuentes archivo-exactos y PostgreSQL 0/0/0. Composición confiable externa aún necesaria; sin autoridad productiva instalada, M1/M2/M3 ni aceptación de modelos/global. Diez gates abiertos. [Validación](validation-t014-root-operator-api.json), [composición](composition-t014-root-operator-api.md).


## T014.R.H2.4d — perfil confiable, firma externa y financiación sin duplicación

US8: el compositor/SDK puede construir un RootDeploymentRuntime desde un perfil estricto y archivos externos de firma/confianza/política, conservando un owner/control entregados por una autoridad independiente. El perfil no aporta claims, presupuestos, observaciones ni claves en el JSON de despliegue y no instala automáticamente runtime en la API.

FR-H24D-01: lector privado acotado y estable compartido entre host e hijo; confianza pública con codec existente y signer Ed25519 externo, sin creación/renovación/escrituras ni reutilización de credenciales peer. Errores del signer fijos sin paths, entradas ni causas privadas.
FR-H24D-02: preflight en el worker ya financiado antes de su primer connect; comprobar key_id y clave pública coincidentes con trust. Revalidar antes del intento de activación; cambios/fallos posteriores al commit siguen el contrato de retención existente. Defaults mantienen firmas de llamadas existentes.
FR-H24D-03: perfil version1 estricto, footprints RAM/CPU positivos y storage0, paths absolutos, scope válido y deadlines existentes. Constructor/assembler inertes, comando Root CLI explícito y entorno copiado de composición confiable; sin lectura de credenciales ni procesos al construir. Mismo perfil/codec para SDK y futuro UI; no endpoint HTTP para secretos.
FR-H24D-04: un productor nuevo de financiación deberá acreditar origen, partición disjunta, binding/target, freshness, SQL y crédito antes de efectos. Linux/política, registro, copia de envelope o préstamo del monitor no sustituyen autoridad. Mientras ese productor no exista, el ensamblador exige funding externo y no declara instalación productiva.

Aceptación: negativas de permisos/symlink/FIFO/límites/JSON/clave/trust, constructor sin I/O, preflight antesSQL y antesissuer, archivo cambiante/rotación y retención poscommit; recorrido Root CLI/PG por ensamblador real sin quitar el caso H24c ni sus748IDs. La configuración externa y keys de fixture no acreditan autoridad productiva, IdP ni M1/M2/M3.

## T015.R.1 — respuestas estructuradas del broker elástico

US9: una asignación ordinaria consume una respuesta de lease validada antes del primer envío remoto. Los campos worker_id, target_host, working_directory y session_name deben ser strings canónicos no vacíos; worker_id es un único segmento seguro de URL. Se conservan IDs opacos heredados como worker-1 y el productor actual de8hex. Los campos informativos adicionales del broker se ignoran y nunca se propagan/persisten como autoridad o secretos. Una identidad de worker válida se conserva para inspección aunque falle otro campo; una identidad malformada no se usa para cleanup.

La devolución de la plaza tras un rechazo remoto exige una respuesta exitosa y un recibo JSON estricto con released=true y workload_present=false. Un HTTP200/404 por sí solo, un cuerpo ausente/no canónico o workload presente no prueba retirada: el intento queda reconcile, sin repetir asignación, y conserva la identidad validada. No se convierte esta validación en prueba de observación/reserva del destino, ni en aceptación T015/M3.


**2026-11-04 · componentes H24d y T015.R.1 validados.** Perfil Root estricto/assembler inerte, signer externo/trust compartido con CLI y preflight financiado antes de SQL/issuer. Leases del broker y recibos de retirada tipados; ambigüedad conserva reconcile sin replay ni token persistido. 829 pruebas aprobadas/10 advertencias/61 módulos:748 IDs conservados+40 Root+16 broker nuevos+25 existentes adicionales. Cinco recorridos Root/PG reales, tipos474, arquitectura5/0,13 Python/7 fuentes archivo-exactos y PostgreSQL limpio. Broker HTTP simulado y presupuestos/grants de fixture: H24D-05, T015/T016, autoridad productiva, cuotas y diez gates globales siguen abiertos. [Validación](validation-profile-elastic-broker.json), [composición](composition-profile-elastic-broker.md).


## T022 implementation contract — 2026-11-04

Persistent admission policy is scoped by configured main store/schema and the stable base profile UUID, resolved by trusted composition, never request input or per-process identity. File mode remains default. Explicit `CAO_ADMISSION_POLICY_STORE=postgres` selects PostgreSQL; missing, revoked, invalid or unreadable database policy yields unknown capacity and never falls back to a file. Configuration changes no resource grants, provider quotas or fixed aggregate worker ceiling.

One append-only component records normalized nine-field policies, revision, active flag, real JWT admin principal and database timestamp. Runtime is SELECT-only; explicit owner writes verify schema/roles, serialize by profile, use expected-revision CAS and retain uncertain commits without replay. HTTP GET/PUT/revoke/history/seed and HTTP-only typed SDK share strict models and fixed errors; history uses bounded keyset pagination. Seed is explicit admin-only from the configured private file, expected revision zero, no caller-supplied path. PUT is complete replacement; revoke requires an existing row. Persisted and actually applied revisions are separate.

Monitor database reads run under its existing funded control operation and original session when present. Revalidate policy revision/content before capacity publication, rejecting changes during sampling. Root pre-SQL bootstrap reads a separately scoped private file via explicit bootstrap method; it never credits a database revision or substitutes for the subsequent database read. Preserve existing default/file call shapes. Missing PostgreSQL row allows API startup unknown and first admin CAS creation.

Acceptance includes strict codec equivalence, real PostgreSQL create/update/revoke/history/concurrent CAS and runtime ACL refusal, admin/validation/error redaction, SDK/HTTP equality, cancellation ownership, source selection/no fallback, bootstrap separation, revision race invalidation and applied metadata. Existing schema5/resource7 migrations remain immutable. Productive Root funding, provider quotas, UI and M1/M2/M3 remain separate open gates.


## T023 local Studio administration — implementation contract (2026-11-04)

The real Settings tab adds a separate admission policy section using the T022 backend and central browser authentication. This controls the local server/profile only: `/admission/policy` uses direct routing, clearly labels local scope/profile, and refuses edits while a remote node is selected until an explicit select-local action. No unsupported node proxy or request-selected profile is invented. Directory settings retain their existing behavior.

Display persisted and applied revisions separately; unknown application is not capacity. The nine-field draft uses explicit units, exact signed64-bit integers and finite duration floats, with review of the full candidate and expected revision before an explicit write. Empty state requires all values; no hidden copied defaults or grants. Conflict409 preserves the draft and demands explicit reload/review; uncertain mutation failures never retry and require explicit status inspection. Revocation is confirmed, history is bounded keyset paging. Async/auth/navigation changes must not apply a stale result to another scope or silently discard pending mutation ownership.

JavaScript must not round valid backend integer quantities or revisions. Use standard source-aware JSON.parse and JSON.rawJSON with bigint and decimal form strings; require both native capabilities and fail safely before policy requests if unavailable. No regex JSON parser, arbitrary max-safe-integer protocol cap, or silent numeric fallback. Duration semantics stay IEEE float. Mandatory actual Chromium acceptance covers large integer response/input/request/database roundtrip; unsupported capability coverage is explicit. Authorization remains solely on the server; no new token/credential storage flow.

The complete web CI suite must exercise the positive exact-integer contract on an engine with native source/raw JSON support. Select Node 22 for that job and verify capabilities explicitly; keep negative unsupported-engine tests rather than skipping positive cases or introducing a parser fallback.

T023 local administration acceptance:569 frontend cases retain all531 baseline IDs; native Chromium/HTTP/JWKS/PostgreSQL2/2, types/build and composition5/0. See [validation](validation-policy-studio.json) and [composition](composition-policy-studio.md). No resource or global acceptance is implied.
