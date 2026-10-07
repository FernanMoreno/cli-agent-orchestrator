# Feature Specification: Corrección del arranque y recuperación de turnos de CAO

**Feature Branch**: `main` (workspace actual; sin crear rama ni modificar historial)
**Created**: 2026-10-02
**Status**: Complete — requisitos implementados y aceptación integral T020/SC-006 verificada el 2026-10-05; límites de fallos históricos no atribuidos en aceptacion-integral-20261005.md
**Input**: «genera un specs de correcion», a partir de los fallos localizados tras la prueba de colaboración de Claude, Codex y OpenCode.

## User Scenarios & Testing

### User Story 1 — Iniciar OpenCode con una configuración válida (Priority: P1)

El propietario inicia OpenCode desde CAO y obtiene un agente operativo sin reparar manualmente rutas ni descubrir después del lanzamiento que falta su perfil.

**Why this priority**: Una validación positiva que no se corresponde con el lanzamiento impide cualquier colaboración posterior.

**Independent Test**: Validar e iniciar en el despliegue integrado con el ejecutable disponible para el proceso principal y una shell de lanzamiento que modifica sus rutas; repetir con perfil nativo ausente.

**Acceptance Scenarios**:

1. **Given** OpenCode instalado y perfil preparado, **When** CAO valida e inicia, **Then** comprueba el entorno efectivo de lanzamiento y el agente queda disponible sin ajustes manuales.
2. **Given** un ejecutable accesible al proceso principal pero inaccesible en la terminal, **When** se valida, **Then** se identifica la incompatibilidad y no se declara el proveedor listo.
3. **Given** un perfil existente en CAO pero ausente en el proveedor, **When** se solicita iniciar, **Then** se rechaza antes de ejecutar la tarea indicando qué perfil falta y cómo prepararlo mediante el mecanismo existente.
4. **Given** un perfil mantenido por el propietario, **When** se comprueba, **Then** no se sobrescribe ni se instala otro perfil silenciosamente.

### User Story 2 — Resolver un turno cuyo resultado no puede verificarse (Priority: P1)

El propietario y el supervisor distinguen un agente trabajando de un turno detenido por falta de recibo. Pueden resolver este último conservando la tarea y los mensajes pendientes.

**Why this priority**: La prueba necesitó otra sesión para integrar resultados. Proteger una entrega incierta debe permitir diagnosticar y resolver su bloqueo.

**Independent Test**: Simular que un supervisor normal y un trabajador terminan su respuesta sin el recibo requerido; comprobar diagnóstico y recuperación en ambos casos sin duplicar trabajo ni inventar éxito.

**Acceptance Scenarios**:

1. **Given** una respuesta final sin recibo verificable, **When** se agota la verificación automática acotada, **Then** se muestra reconciliación con motivo, turno y acciones permitidas, sin aparentar indefinidamente trabajo activo.
2. **Given** un supervisor normal sin relación de hijo nativo, **When** ocurre ese fallo, **Then** dispone del mismo diagnóstico y recuperación que un trabajador.
3. **Given** un turno bloqueado y mensajes pendientes, **When** se consulta o vuelve a verificar, **Then** no se reejecuta la tarea ni se entregan mensajes como si ya hubiera terminado.
4. **Given** reconciliación necesaria, **When** el operador solicita nueva verificación, **Then** se comprueba la evidencia actual; solo un recibo auténtico del turno permite registrar éxito.
5. **Given** un turno sin prueba de finalización, **When** el operador lo cancela mediante una acción explícita, **Then** queda cancelado, su ejecución deja de interferir y se conserva el historial; una nueva tarea puede aceptarse cuando el agente esté realmente disponible.
6. **Given** un supervisor que delega y espera resultados, **When** responden los trabajadores, **Then** continúa por el flujo normal de CAO o muestra reconciliación accionable; resolver la incertidumbre no exige crear otro supervisor.
7. **Given** trabajo legítimo de larga duración sin respuesta final todavía, **When** pasa el plazo de verificación de una respuesta terminada, **Then** no se considera fallido por ese solo motivo.

### User Story 3 — Consultar resultados con un estado inequívoco (Priority: P1)

El consumidor distingue un resultado pendiente, una reconciliación necesaria y un fallo interno real sin interpretar textos de error ni asumir que toda falta de resultado es una avería.

**Why this priority**: La última consulta de la prueba devolvió error interno aunque el motivo era ausencia de recibo verificado.

**Independent Test**: Consultar un turno activo, una respuesta final sin recibo, un resultado verificado y una avería de lectura; comprobar respuestas diferenciadas y coherencia entre consumidores.

**Acceptance Scenarios**:

1. **Given** un turno activo, **When** se consulta su resultado, **Then** se indica pendiente sin comunicar éxito ni fallo interno por esa sola condición.
2. **Given** una respuesta que requiere reconciliación, **When** se consulta, **Then** se devuelve un diagnóstico identificable por el consumidor y la referencia del turno que debe resolver.
3. **Given** un resultado verificado, **When** se consulta repetidamente, **Then** devuelve el mismo resultado sin cambiar identidad ni iniciar trabajo nuevo.
4. **Given** una avería real de lectura, extracción o persistencia, **When** se consulta, **Then** se comunica como fallo interno y no se oculta como estado pendiente.
5. **Given** consumidores de interfaz, CLI y herramientas de coordinación, **When** observan el mismo turno, **Then** coinciden en estado y acción requerida; conservan la consulta de transcripción completa sin presentarla como resultado verificado.

### Edge Cases

- Ejecutable visible durante instalación pero ausente al crear terminal; disponibilidad que cambia después de validar.
- Perfil ausente, ilegible o incompatible, sin sobrescribir perfiles ajenos para arrancar.
- Recibo parcial, antiguo, repetido en la instrucción o presente sin prueba de finalización.
- Finalización tardía después de reconciliación o cancelación explícita.
- Consulta, verificación, cancelación y recepción de mensajes concurrentes sobre el mismo turno.
- Reinicio con recibo pendiente: conservar incertidumbre e identidad sin reenviar la tarea.
- Diálogo que requiere respuesta del usuario: no sustituirlo por reconciliación o éxito sin evidencia.
- Resultados de trabajadores mientras el supervisor trabaja o tiene un turno bloqueado.
- Evidencia que ya no puede recuperarse: mantener diagnóstico y permitir cancelación sin fabricar resultado.

## Requirements

### Functional Requirements

- **FR-001**: CAO MUST validar OpenCode en el entorno efectivo con el que iniciará al agente, incluyendo sus rutas de ejecutables.
- **FR-002**: CAO MUST comprobar que el perfil elegido está preparado para el proveedor antes de entregar una tarea y explicar el prerrequisito ausente.
- **FR-003**: CAO MUST mantener disponibles las rutas necesarias durante el arranque real sin ajustes manuales posteriores a instalar.
- **FR-004**: Una comprobación o arranque fallido MUST NOT dejar un agente declarado listo ni recursos sin dueño; la incertidumbre de una ejecución iniciada MUST quedar explícita.
- **FR-005**: CAO MUST exigir evidencia del turno actual para registrar un resultado correcto; ningún desbloqueo MUST fabricar, reutilizar o ignorar su recibo.
- **FR-006**: CAO MUST distinguir trabajo activo, espera de intervención, resultado pendiente de verificación y reconciliación necesaria, conservando los estados válidos existentes.
- **FR-007**: Tras detectar una respuesta final sin resultado verificable, CAO MUST limitar intentos automáticos y mostrar reconciliación en un máximo de 60 segundos desde esa detección, tanto en supervisores normales como en trabajadores.
- **FR-008**: CAO MUST proporcionar acciones autorizadas de consulta, nueva verificación y cancelación para un turno incierto, conservando identidad y resultado de cada acción.
- **FR-009**: La recuperación MUST NOT repetir la tarea original, reenviar automáticamente una entrega incierta ni declarar éxito porque la terminal parezca libre.
- **FR-010**: Los mensajes bloqueados antes de entrega MUST seguir pendientes, con remitente, destinatario y orden conservados; resolver el turno MUST permitir la entrega normal sin duplicaciones.
- **FR-011**: La cancelación MUST impedir que una ejecución antigua interfiera con tareas nuevas y MUST NOT convertirse en éxito por un recibo tardío.
- **FR-012**: Las consultas MUST diferenciar de forma reconocible para el consumidor la falta esperada de resultado, reconciliación y fallo interno; MUST NOT comunicar este último únicamente por un turno pendiente.
- **FR-013**: Interfaz, CLI y herramientas de coordinación MUST coincidir en estado y acción requerida, sin eliminar la consulta de transcripción ni confundirla con resultado final.
- **FR-014**: Un reinicio MUST conservar incertidumbre del turno e identidad de mensajes pendientes; la recuperación MUST NOT ejecutar de nuevo la tarea automáticamente.
- **FR-015**: Acciones concurrentes MUST NOT verificar turnos distintos con el mismo recibo, sobrescribir una cancelación ni duplicar un mensaje.
- **FR-016**: Los diagnósticos MUST identificar proveedor, terminal, turno y condición relevante sin revelar credenciales ni recibos secretos en errores públicos.
- **FR-017**: La aceptación MUST incluir comprobaciones deterministas de los fallos y una prueba real con Claude, Codex y OpenCode, registrando intervenciones y casos no ejecutados.
- **FR-018**: Codex y OpenCode v2 MUST tener conectado y disponible el servidor MCP de CAO antes de que CAO entregue la primera tarea; si no se conecta dentro del plazo de inicio, la inicialización MUST fallar explícitamente.
- **FR-019**: La espera de OpenCode v2 MUST reconocer el formato logfmt real de su registro privado y MUST excluir servidores MCP deshabilitados explícitamente en configuraciones v1 o v2.
- **FR-020**: Claude MUST cargar explícitamente las herramientas del MCP de CAO al iniciar; sus plazos de conexión MUST usar el presupuesto de inicialización del proveedor por defecto y respetar las variables de timeout explícitas del operador. Otros servidores conservan su política de carga.
- **FR-021**: El registro usado como evidencia de readiness de OpenCode MUST capturarse explícitamente por terminal con `--print-logs`, nivel INFO y redirección privada de stderr; no debe depender de que el CLI respete una ruta XDG implícita.

### Key Entities

- **Disponibilidad del proveedor**: ejecutable, entorno efectivo y perfil; validación y motivo de rechazo.
- **Turno**: trabajo entregado a una terminal, identidad, prueba de recepción/finalización y estado durable, distinto del estado visual.
- **Resultado verificado**: respuesta cuya evidencia corresponde al turno actual, estable tras verificarse.
- **Condición de reconciliación**: incertidumbre identificada, motivo, intentos acotados y acciones autorizadas; aplicable también al supervisor normal.
- **Mensaje pendiente**: contenido dirigido a otra terminal, remitente, destinatario y estado de entrega conservados durante el bloqueo.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Todos los escenarios deterministas de entorno o perfil incompatible identifican el prerrequisito antes de entregar tarea; ninguna validación positiva contradice el arranque en el mismo entorno sin cambios externos.
- **SC-002**: Supervisores normales y trabajadores muestran reconciliación accionable en un máximo de 60 segundos desde detectar una respuesta final no verificable, sin aparentar indefinidamente trabajo activo.
- **SC-003**: Los escenarios de recuperación, concurrencia y reinicio producen cero tareas reejecutadas automáticamente, cero éxitos sin evidencia y cero mensajes duplicados o perdidos por el bloqueo previo a entrega.
- **SC-004**: El operador resuelve incertidumbre mediante verificación o cancelación sin editar almacenamiento ni crear otra sesión de supervisor; una nueva tarea continúa cuando el agente está disponible.
- **SC-005**: Todas las consultas de los cuatro escenarios de resultado distinguen trabajo activo, reconciliación, resultado correcto y avería real; los consumidores muestran estados compatibles.
- **SC-006**: Una nueva prueba en un proyecto del Escritorio usa los tres proveedores reales para una pequeña aplicación y acredita delegación, ida/vuelta de mensajes e integración en navegador, sin reparar rutas manualmente, instalar perfiles durante la ejecución ni forzar recibos.
- **SC-007**: En pruebas reales aisladas, Codex y OpenCode v2 completan una llamada `send_message` y el coordinador recibe ambos callbacks; la entrega inicial ocurre después de que cada MCP de CAO esté conectado.

## Assumptions

- Este spec corrige un subconjunto del [spec006](../006-agent-collaboration/spec.md); no reemplaza sus requisitos de proyectos, permisos, credenciales e integraciones.
- Se conserva la coordinación original y el despliegue integrado existentes. No se introducen contenedores por trabajador ni sandbox nuevo.
- Ejecutables, credenciales válidas y perfiles preparados son prerrequisitos. CAO debe comprobarlos y comunicar su ausencia; no garantiza que un modelo cumpla siempre instrucciones de finalización.
- Los 60 segundos corresponden a verificar una respuesta detectada como terminada, no a duración total de tarea o espera legítima de agentes.
- Las pruebas deterministas inyectan omisiones de recibo; la prueba real no depende de que el modelo lo omita espontáneamente.
- El plan técnico y contracts/turn-recovery.md fijan los contratos concretos de respuesta, compatibilidad de consumidores y acciones existentes reutilizables, conservando autorización y trazabilidad.
- La prueba real usa recursos identificados y aislados, conserva el proyecto y cierra solo recursos propios, sin reiniciar la instalación personal.

## Scope and Dependencies

Dentro del alcance: disponibilidad real de OpenCode, comprobación previa de perfil, disponibilidad MCP antes de entregar tareas a Codex/OpenCode v2, diagnóstico y recuperación de turnos sin resultado verificable, consultas y consumidores afectados.

Fuera del alcance: corregir código de la demo, elegir destinatarios por el modelo, garantizar su elección de herramientas, modificar autenticación general, ampliar permisos de proyectos o rediseñar la orquestación.

Dependencias: entorno de proveedores, identidad de terminal/turno, persistencia de recibos, monitor de estados, inbox y consumidores actuales. No se atribuyen errores de contenido al enrutamiento sin evidencia adicional.

La planificación coordinará tareas solapadas con spec006 para evitar implementaciones duplicadas. La [base del diagnóstico](diagnosis.md) conserva referencias de código y límites de evidencia sin imponer una solución técnica.

## Correcciones autorizadas tras investigación — 2026-10-05

- **FR-022**: Toda llamada tmux del cliente, incluyendo listados libtmux, lectura, pegado y limpieza, MUST tener un límite de espera. Un timeout MUST conservar la incertidumbre y no clasificarse como ausencia de terminal, resultado completado o permiso para repetir una tarea.
- **FR-023**: El recibo MUST confirmar el turno actual. En coordinación asíncrona, el contrato MUST permitir cerrar una fase de despacho aceptado, declarar los resultados pendientes y recibir callbacks en turnos posteriores; MUST NOT presentar ese cierre como prueba de que los trabajadores o el objetivo global han terminado. Una tarea ordinaria sigue requiriendo su resultado antes del recibo.
- **FR-024**: Las instrucciones distribuidas de supervisor MUST describir el mismo contrato de fases y recibos. Ningún callback, reinicio o timeout MUST borrar el fence de un recibo no verificado.

Aceptación adicional: tmux propio suspendido produce un error acotado y recupera lectura tras reanudar; un fallo de pegado seguido de fallo de cleanup conserva el fallo original; el contrato de los tres proveedores contiene el cierre de fase sin alterar identidad/nonce; inbox continúa solo después de verificar el recibo anterior. La prueba real de los tres proveedores sigue siendo una aceptación separada, condicionada por recursos y autenticación válidos.
- **FR-025**: Cuando Claude usa el MCP propio de CAO, MUST esperar a que ese proceso haya respondido correctamente a descubrimiento de herramientas antes de entregar la primera tarea. La señal MUST pertenecer al lanzamiento actual y no reutilizar señales de otro terminal o de un lanzamiento anterior. Fallo/timeout MUST impedir la entrega de la tarea.

## Ampliación autorizada: auditoría y corrección de hallazgos reales (2026-10-05)

Petición: investigar a profundidad y corregir los errores de pruebas-reales-adicionales-20261005.md, siguiendo AI_WORKFLOW.md. Clasificación bug-significant. Se conserva el alcance local y el spec existente.

- FR-026: La demo acepta exclusivamente JSON UTF-8 y documenta su límite de cuerpo de 16.384 bytes; preserva títulos existentes y no añade límites de título incompatibles. Las peticiones rechazadas no modifican datos.
- FR-027: LAST de OpenCode v2 devuelve únicamente la respuesta del asistente, excluyendo el contrato de entrega completo aunque el prompt contenga párrafos o se envuelva. No admite recibos en el eco del usuario ni respuestas de turnos anteriores.
- FR-028: El comprobador real verifica secuencia, generación/recibo y callbacks actuales; el ajuste de marcadores partidos nunca sustituye esa prueba ni reenvía entregas inciertas.
- FR-029: La auditoría de salud identifica primeras fronteras lentas mediante evidencia de servidor/cliente/sistema. Se corrigen bloqueos reproducidos en código; no se atribuyen los tres timeouts históricos a una causa no demostrada ni se promete disponibilidad bajo agotamiento físico.

Aceptación adicional: rechazar UTF-16/32 y bytes UTF-8 inválidos con JSON 400; aceptar UTF-8 válido hasta el límite exacto de bytes y rechazar exceso; comprobar ausencia de mutación tras rechazo; reproducir y corregir contaminación LAST con fixture real; verificar salud concurrente con fronteras lentas controladas y documentar restricciones del entorno; repetir colaboración real con recibos actuales.

FR-030: Dos materializaciones del mismo run_id no deben sobrescribir ni borrar el snapshot de otra ejecución. Cada archivo de ejecución será exclusivo, con permisos 0600 en raíz privada, y su limpieza afectará únicamente a ese archivo. Se prueba la colisión y ejecución/limpieza real antes de corregir.

FR-031: El runner de validación conserva diagnóstico de fallos nativos y muestreo periódico sin usar el watchdog nativo dump_traceback_later demostrado inseguro en los Python instalados. El muestreo se ejecuta desde un hilo Python con GIL y termina al salir; no cambia recibos, runtime CAO ni configuración global.
