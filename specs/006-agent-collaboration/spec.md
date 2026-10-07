# Feature Specification: Colaboración de agentes en el despliegue personal

**Feature Branch**: `main` (workspace actual; sin crear rama ni modificar historial)
**Created**: 2026-10-01
**Status**: Verificado — aceptación local y recuperación acreditadas; alcance y límites en cierre-20261005.md
**Input**: «vale, de esto genera un spec», tras acordar conservar la coordinación original de CAO, ejecutar los agentes habituales dentro de `cao-personal` y comprobar su comunicación y acceso a proyectos externos.

## User Scenarios & Testing

### User Story 1 — Arrancar agentes desde la aplicación (Priority: P1)

El propietario inicia un supervisor y trabajadores desde la aplicación principal. Cada agente dispone de su proveedor, identidad, herramientas de coordinación y directorio de trabajo, sin configurar manualmente un entorno distinto para cada uno.

**Why this priority**: La colaboración requiere agentes operativos y conectados al coordinador existente.

**Independent Test**: Iniciar Claude Code, Codex y OpenCode sobre un proyecto de prueba; comprobar disponibilidad, identidad, directorio y una consulta autenticada al coordinador.

**Acceptance Scenarios**:

1. **Given** el propietario autenticado y un proveedor configurado, **When** inicia un agente, **Then** la interfaz muestra su identidad y estado, y el agente puede utilizar las herramientas de CAO autorizadas para él.
2. **Given** un supervisor activo, **When** crea dos trabajadores, **Then** cada trabajador tiene una terminal independiente dentro del despliegue principal y no se crea un contenedor adicional por agente.
3. **Given** credenciales de proveedor ausentes o herramientas de coordinación inaccesibles, **When** se intenta iniciar, **Then** se identifica el componente que falla y no se presenta al agente como listo para colaborar.
4. **Given** un agente con autorización ausente, caducada o revocada, **When** intenta una operación protegida, **Then** se rechaza sin recurrir a una identidad privilegiada alternativa ni mostrar secretos.

### User Story 2 — Delegar tareas y devolver resultados (Priority: P1)

El supervisor reparte trabajo, continúa su propia tarea cuando la delegación es asíncrona y recibe resultados del trabajador correspondiente. También puede esperar el resultado de una delegación síncrona.

**Why this priority**: Es el flujo central de la orquestación original de CAO.

**Independent Test**: Un supervisor delega dos tareas identificables mediante `assign`; ambos trabajadores devuelven resultados con `send_message`. Después se ejecuta una tarea mediante `handoff` y se verifica su resultado.

**Acceptance Scenarios**:

1. **Given** un supervisor, **When** usa `assign`, **Then** recibe la identidad del trabajador sin esperar a que termine la tarea, y esta se entrega cuando el trabajador está listo.
2. **Given** dos tareas asignadas, **When** ambos trabajadores responden, **Then** el supervisor recibe cada resultado con remitente y tarea identificables, sin mezclarlos.
3. **Given** un trabajador creado por el supervisor, **When** responde sin indicar destinatario, **Then** el resultado se dirige al supervisor que lo creó, conforme al comportamiento existente de CAO.
4. **Given** una delegación mediante `handoff`, **When** termina, **Then** el resultado vuelve al llamador; un fallo o vencimiento del plazo se distingue de una finalización correcta y conserva la referencia necesaria para consultar o cerrar el trabajador.

### User Story 3 — Comunicar trabajadores entre sí (Priority: P1)

Dos trabajadores pueden intercambiar preguntas, información y resultados sin exigir que toda conversación pase manualmente por el supervisor.

**Why this priority**: La colaboración entre agentes es un objetivo explícito del propietario.

**Independent Test**: Un trabajador envía una pregunta a la identidad del otro con `send_message`; el destinatario responde al primero y ambos mensajes se observan en sus terminales correspondientes.

**Acceptance Scenarios**:

1. **Given** dos trabajadores autorizados y con identidades conocidas, **When** uno envía un mensaje al otro, **Then** se conserva el remitente real y el mensaje llega al destinatario correcto.
2. **Given** un destinatario ocupado que no admite entrada durante procesamiento, **When** recibe un mensaje, **Then** este permanece pendiente y se entrega cuando esté disponible, sin interrumpir su tarea en curso.
3. **Given** un mensaje pendiente y varios intentos concurrentes de entrega, **When** el receptor queda disponible, **Then** el mismo mensaje no se inyecta dos veces por esa concurrencia.
4. **Given** un destinatario inexistente o cerrado, **When** se envía, **Then** se informa del fallo o estado pendiente recuperable, sin atribuir el mensaje a otro agente.
5. **Given** un mensaje aceptado por CAO, **When** se consulta su estado, **Then** se distingue almacenamiento, entrega a la terminal y resultado de la tarea; la entrega no se presenta como confirmación de comprensión por el agente.

### User Story 4 — Trabajar sobre proyectos e integraciones externos (Priority: P1)

Los agentes utilizan los proyectos del propietario que permanecen en el host y las integraciones seleccionadas, aunque la aplicación se ejecute en Docker.

**Why this priority**: La colaboración debe producir cambios útiles en los proyectos reales, sin trasladarlos a la imagen de la aplicación.

**Independent Test**: Registrar dos carpetas de prueba externas, editar una desde un agente y comprobar el cambio en el host; utilizar una integración seleccionada y comprobar que los recursos no registrados no aparecen automáticamente.

**Acceptance Scenarios**:

1. **Given** un proyecto registrado con escritura permitida, **When** el agente modifica un archivo de prueba, **Then** el mismo cambio aparece en el host y persiste tras recrear el contenedor de la aplicación.
2. **Given** un supervisor sobre un proyecto registrado, **When** delega sin indicar otra carpeta, **Then** el trabajador hereda el directorio correspondiente; una ruta explícita válida utiliza el proyecto indicado.
3. **Given** un proyecto registrado con acceso de solo lectura, **When** un agente intenta escribir, **Then** se rechaza la escritura y se explica la restricción.
4. **Given** una ruta no registrada, inexistente o inaccesible, **When** se selecciona para una tarea, **Then** se informa del problema y no se amplía el acceso al resto del host.
5. **Given** una integración declarada que se ejecuta fuera del contenedor, **When** un agente la utiliza, **Then** se accede mediante la conexión configurada; si el servicio está detenido o no es alcanzable, se informa de esa causa sin confundirla con un fallo del proveedor.
6. **Given** dos agentes que necesitan cambios independientes, **When** se solicita un worktree, **Then** trabajan en directorios separados del mismo repositorio sin sobrescribir silenciosamente los cambios del otro.

### User Story 5 — Gestionar fallos, cierre y reinicio (Priority: P2)

El propietario identifica tareas y mensajes pendientes, cancela un trabajador y cierra sesiones sin perder cambios persistidos ni dejar procesos activos inadvertidos.

**Why this priority**: La colaboración debe seguir siendo comprensible cuando falla un proveedor o se detiene la aplicación.

**Independent Test**: Cancelar un trabajador de prueba, cerrar la sesión y detener/reiniciar la aplicación; comprobar procesos, mensajes y archivos antes y después.

**Acceptance Scenarios**:

1. **Given** un trabajador activo, **When** se cancela y cierra, **Then** dejan de ejecutarse sus procesos asociados y su estado deja de aparecer como activo.
2. **Given** un proveedor que falla durante una tarea, **When** CAO detecta el fallo, **Then** el supervisor puede identificar al trabajador afectado y el resto de agentes sigue operativo.
3. **Given** tareas activas y mensajes persistidos, **When** se detiene y reinicia `cao-personal`, **Then** se conservan archivos, cuentas y mensajes almacenados; las tareas interrumpidas se reconcilian y no se muestran como activas sin un proceso real ni se ejecutan otra vez automáticamente.
4. **Given** un mensaje cuya entrega quedó incierta por un fallo, **When** se recupera el servicio, **Then** se expone la incertidumbre para revisión en lugar de prometer entrega exacta o repetir una tarea con efectos sin control.

### Edge Cases

- Arranque lento del proveedor, permisos insuficientes, autenticación caducada y herramientas MCP mal configuradas.
- Respuestas simultáneas, receptor ocupado, receptor eliminado y mensajes pendientes que no generan nuevos cambios de estado.
- Vencimiento de `handoff` mientras el trabajador continúa ejecutándose y cancelación durante su inicialización.
- Reinicio entre guardar un mensaje e inyectarlo en la terminal; no se presupone entrega exactamente una vez ante cualquier caída.
- Rutas del host y del contenedor distintas, enlaces simbólicos que salen del proyecto y permisos Linux/WSL incompatibles.
- Servicio disponible solo en el loopback del host, herramienta nativa de Windows y ejecutable del host incompatible con el sistema del contenedor.
- Dos agentes editando el mismo archivo, worktrees inaccesibles y procesos secundarios que sobreviven al cierre del proveedor.

## Requirements

### Functional Requirements

- **FR-001**: La ejecución habitual DEBE mantener aplicación y agentes locales en `cao-personal`, usando las terminales tmux existentes, sin crear un contenedor por agente.
- **FR-002**: El arranque DEBE proporcionar a cada agente identidad propia, proveedor seleccionado, directorio de trabajo y herramientas de coordinación autorizadas.
- **FR-003**: Los agentes DEBEN poder acceder al coordinador con autenticación válida sin copiar contraseñas del navegador, deshabilitar la protección de la API o exponer tokens en prompts o registros públicos.
- **FR-004**: La configuración DEBE conservar las operaciones existentes `assign`, `handoff` y `send_message`, con los comportamientos de US2.
- **FR-005**: Los trabajadores autorizados DEBEN poder enviar mensajes a otros trabajadores por identidad, además de responder al supervisor que los creó.
- **FR-006**: Los mensajes DEBEN conservar remitente, destinatario, contenido y estado persistido; aceptar un mensaje no DEBE equivaler a declarar terminada la tarea.
- **FR-007**: La entrega DEBE respetar la disponibilidad y las capacidades del proveedor, recuperar mensajes pendientes y evitar inyecciones duplicadas por consumidores concurrentes.
- **FR-008**: Los fallos de entrega DEBEN distinguir rechazo definitivo, espera recuperable e incertidumbre, sin reenviar automáticamente efectos que podrían haberse ejecutado.
- **FR-009**: El propietario DEBE poder registrar varios proyectos externos con permisos explícitos de lectura o escritura; los archivos originales DEBEN permanecer en el host y sus modificaciones autorizadas ser visibles allí.
- **FR-010**: Los trabajadores DEBEN heredar el directorio del supervisor cuando proceda, admitir otro proyecto registrado y conservar la opción existente de worktrees independientes.
- **FR-011**: Las integraciones seleccionadas DEBEN comprobar su conexión desde el entorno donde corre el agente; disponer de un archivo de configuración no DEBE considerarse prueba de funcionamiento.
- **FR-012**: La conexión a servicios externos DEBE distinguir servicios internos, servicios del host y herramientas nativas externas, y ofrecer un diagnóstico verificable cuando no sean alcanzables.
- **FR-013**: Los agentes NO DEBEN recibir acceso automático a todo el host por registrar un proyecto; configuración y credenciales DEBEN mantenerse fuera de la imagen y de los resultados públicos de las pruebas.
- **FR-014**: La aplicación DEBE mostrar o permitir consultar identidades, estados, resultados y fallos usando las superficies existentes de CAO.
- **FR-015**: Cancelar y cerrar un trabajador DEBE limpiar sus procesos asociados; detener la aplicación DEBE detener los agentes que aloja.
- **FR-016**: La recuperación DEBE conservar cuentas, permisos, proyectos y mensajes persistidos, y reconciliar ejecuciones interrumpidas sin reejecutarlas silenciosamente.
- **FR-017**: La configuración habitual NO DEBE exigir el modo Work con trabajadores Docker. Esta feature NO elimina sus contratos, registros ni capacidades opcionales existentes.
- **FR-018**: La aceptación DEBE incluir Claude Code como supervisor, Codex y OpenCode como trabajadores, creando una API pequeña y un frontend en una carpeta nueva del Escritorio. DEBE conservar evidencia de delegación, respuestas, mensajes entre trabajadores, acceso externo y cierre. Las pruebas simuladas pueden complementar esa evidencia, pero no sustituirla.
- **FR-019**: La documentación DEBE explicar qué se ejecuta dentro del contenedor, qué permanece en el host, qué proyectos e integraciones se han conectado y cómo comprobar o cerrar la colaboración.

### Key Entities

- **Agente/terminal**: Identidad propia, proveedor, estado y procesos asociados; puede actuar como supervisor o trabajador.
- **Delegación**: Tarea, llamador, trabajador, modalidad de espera, resultado y estado de finalización o fallo.
- **Mensaje**: Remitente, destinatario, contenido y estado de almacenamiento/entrega.
- **Proyecto registrado**: Carpeta externa, ruta visible para los agentes y permisos de acceso.
- **Integración seleccionada**: Herramienta o servicio, ubicación, conexión autorizada y resultado de comprobación.
- **Evidencia de aceptación**: Escenario ejecutado, identidades de prueba, resultados observados, fallos y límites declarados, sin secretos.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Un supervisor y dos trabajadores operan simultáneamente, con tres identidades independientes y cero contenedores adicionales creados para esos agentes.
- **SC-002**: Dos delegaciones asíncronas devuelven sus dos resultados al supervisor correcto y una delegación síncrona devuelve su resultado al llamador.
- **SC-003**: Dos trabajadores intercambian una pregunta y una respuesta; un mensaje adicional enviado mientras el receptor está ocupado llega cuando queda disponible, sin duplicación en la prueba de concurrencia.
- **SC-004**: Dos proyectos externos registrados son accesibles según sus permisos; una edición autorizada persiste en el host tras recrear la aplicación y una escritura sobre el proyecto de solo lectura es rechazada.
- **SC-005**: Al menos una integración externa seleccionada funciona desde un agente real; al detenerla, su indisponibilidad se identifica correctamente.
- **SC-006**: Tras cerrar la sesión de prueba y detener la aplicación quedan cero procesos de sus agentes en ejecución; tras reiniciar no aparecen tareas interrumpidas como activas sin proceso real ni se repiten automáticamente.
- **SC-007**: Todos los escenarios de aceptación tienen un resultado registrado. Los escenarios no ejecutados por falta de proveedor o integración se indican como pendientes, sin declarar completada la aceptación global.
- **SC-008**: La aceptación no altera las cuentas, permisos, credenciales ni proyectos personales ajenos a la prueba, y no incluye secretos en sus evidencias.

## Assumptions

- Se conserva el despliegue integrado descrito en [spec 005](../005-docker-installation/spec.md) y el login de [spec 004](../004-browser-login-sessions/spec.md).
- La plataforma objetivo es la instalación local Linux/WSL del propietario. La ejecución habitual comparte entorno y permisos; una terminal tmux no implica aislamiento entre agentes.
- Claude Code, Codex y OpenCode son los proveedores que se verificarán, usando las configuraciones existentes. La prueba real requiere proveedor disponible y puede consumir su cuota; no se presupone acceso a cuentas ni redes ausentes. Esta elección y el proyecto API/frontend en el Escritorio fueron solicitados expresamente por el propietario durante la planificación.
- Las integraciones se seleccionan entre las ya configuradas, incluyendo MCP cuando corresponda; esta feature no promete conectar automáticamente cualquier servicio del host.
- Se aprovechan los mecanismos originales de CAO. La fase de planificación identificará qué ya cumple estos requisitos y qué precisa corrección, sin reconstruir capacidades que ya funcionan.
- La relación de `localhost` con el entorno donde se ejecuta cada proceso, las rutas montadas y los requisitos de conexión externa se documentarán y probarán durante la planificación y aceptación.
- La referencia original inspeccionada es `upstream/main` local en `2fcc3efa`; no constituye una afirmación sobre la versión remota más reciente.
- Fuera de alcance: introducir un sandbox, un contenedor por subagente como modo habitual, clusters remotos, eliminar el subsistema Work, migrar cuentas o implementar un protocolo de mensajería nuevo.
- La entrega inicial del 2026-10-01 fue documental. El cierre del 2026-10-05 reutiliza las capacidades posteriores y corrige fronteras comprobadas sin recrear el despliegue personal. [Informe vigente](cierre-20261005.md).
