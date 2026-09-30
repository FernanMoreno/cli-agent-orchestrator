# Feature Specification: Login local y sesiones de navegador persistentes

**Feature Branch**: `main` (checkout actual; no se crea rama en esta fase)

**Created**: 2026-09-30

**Status**: Implementado, verificado y conectado al runtime personal activo; la cuenta se configura en el front autorizado del propietario. Evidencia y límites: [completion-evidence.md](completion-evidence.md).

**Input**: El usuario quiere iniciar sesión con usuario y contraseña y desarrollar sin autenticarse continuamente ni generar bearers manualmente. Acceso personal desde este equipo, sin servicios de pago adicionales.

## User Scenarios & Testing

### User Story 1 — Crear la cuenta local y entrar desde la web (Priority: P1)

El propietario configura una cuenta local una sola vez. Al abrir la dirección habitual de CAO, ve una pantalla de usuario y contraseña cuando no tiene sesión. Tras autenticarse, entra al dashboard y puede usar sus perfiles y agentes con sus permisos actuales. No necesita copiar credenciales técnicas ni ejecutar comandos para cada acceso.

**Why this priority**: Es el acceso normal que falta en la web actual.

**Independent Test**: Preparar una instalación sin cuenta, crearla mediante el procedimiento local del propietario y abrir un navegador sin sesión; comprobar login correcto e incorrecto y acceso al dashboard protegido.

**Acceptance Scenarios**:

1. **Given** una instalación sin cuenta local, **When** el propietario realiza la configuración inicial local, **Then** elige usuario y contraseña, sin credenciales por defecto ni registro público abierto.
2. **Given** un navegador sin sesión, **When** abre la web, **Then** aparece la pantalla de login y no se muestran datos ni controles protegidos.
3. **Given** credenciales correctas, **When** inicia sesión, **Then** accede al dashboard conectado con la misma identidad y permisos del operador configurado.
4. **Given** credenciales incorrectas, **When** intenta entrar, **Then** recibe un error comprensible que no revela si el usuario existe y no obtiene acceso.

### User Story 2 — Desarrollar sin interrupciones de autenticación (Priority: P1)

El usuario activa «Recordar este navegador». Sigue autenticado al recargar, abrir otras pestañas, cerrar y abrir el navegador o reiniciar CAO. La renovación de las credenciales internas ocurre automáticamente mientras la sesión siga autorizada.

**Why this priority**: El objetivo principal es quitar la fricción de autenticarse cada hora.

**Independent Test**: Iniciar sesión recordada, atravesar varios vencimientos internos, recargar, usar dos pestañas, reiniciar el servicio y reabrir el navegador conservando sus datos; verificar acceso sin contraseña adicional y sin repetir operaciones.

**Acceptance Scenarios**:

1. **Given** una sesión recordada válida, **When** recarga o abre otra pestaña de la misma instalación, **Then** continúa trabajando sin introducir credenciales ni generar enlaces.
2. **Given** una sesión recordada válida, **When** reinicia navegador o servidor, **Then** recupera el acceso conservando permisos, datos y contexto de navegación cuando exista.
3. **Given** una sesión autorizada cuyo acceso interno necesita renovación, **When** realiza consultas o usa terminales y eventos, **Then** la renovación sucede en segundo plano y no aparece el formulario de login.
4. **Given** varias pestañas renovando simultáneamente, **When** una renovación llega tarde o compite con otra, **Then** no expulsa al usuario ni invalida una sesión vigente por la concurrencia normal.
5. **Given** un corte del servidor durante una operación de escritura, **When** vuelve la conexión, **Then** la interfaz conserva o recupera el estado comprobable; no repite automáticamente una operación con resultado incierto.
6. **Given** una sesión sin «Recordar este navegador», **When** recarga durante el uso normal, **Then** no vuelve a pedir credenciales; una nueva sesión de navegador sin restaurar el estado temporal exige login.

### User Story 3 — Cerrar sesión y gestionar su vigencia (Priority: P2)

El usuario puede cerrar sesión desde la web o cerrar todas las sesiones de su cuenta. Una sesión revocada o vencida vuelve al login con una explicación clara. Un cierre de sesión retira el control del navegador sin cancelar ni duplicar el trabajo durable de los agentes.

**Why this priority**: Una sesión cómoda necesita controles claros para retirar el acceso.

**Independent Test**: Abrir dos sesiones de navegador, cerrar una y luego todas; comprobar consultas, escrituras, terminales y eventos después de revocar y verificar que el estado de Work no cambia por el logout.

**Acceptance Scenarios**:

1. **Given** una sesión abierta, **When** pulsa «Cerrar sesión», **Then** se revoca ese acceso en todas sus pestañas y se muestra el login; otra sesión independiente permanece vigente.
2. **Given** varias sesiones de su cuenta, **When** pulsa «Cerrar todas las sesiones», **Then** todas pierden acceso sin afectar los procesos o credenciales independientes de servicios internos.
3. **Given** una sesión que superó su límite de inactividad o duración, **When** vuelve a usar la web, **Then** se solicita login y no se renueva la sesión vencida.
4. **Given** una sesión revocada con terminal o eventos abiertos, **When** intenta mantener ese control, **Then** se cierra el acceso protegido del navegador dentro del límite definido, conservando el trabajo y sus recibos.

### User Story 4 — Recuperar la cuenta y conservar compatibilidad (Priority: P2)

El propietario cambia su contraseña desde una sesión válida o la restablece mediante un procedimiento local cuando la olvida. No necesita correo, proveedor externo ni suscripción adicional. La nueva forma de entrar conserva las autorizaciones y los datos existentes.

**Why this priority**: Evita que el login bloquee al propietario o rompa las integraciones actuales.

**Independent Test**: Cambiar y recuperar la contraseña; comprobar rechazo de la anterior, revocación de sesiones existentes y acceso a los mismos registros y provisiones con la identidad preservada.

**Acceptance Scenarios**:

1. **Given** una sesión válida y la contraseña actual, **When** cambia su contraseña, **Then** la anterior deja de funcionar y todas las sesiones anteriores se revocan; puede entrar de nuevo.
2. **Given** una contraseña olvidada, **When** el propietario usa el procedimiento de recuperación autorizado desde su cuenta del sistema, **Then** puede establecer una nueva sin publicar credenciales ni habilitar un restablecimiento anónimo en la web.
3. **Given** la instalación personal existente, **When** habilita login local, **Then** los datos, recibos y autorizaciones siguen vinculados al operador correcto y no se crea un propietario distinto por accidente.
4. **Given** clientes CLI/TUI, integraciones y Work previamente autorizados, **When** se habilita el login web, **Then** sus credenciales y límites de autorización mantienen el comportamiento correspondiente; iniciar sesión no provisiona Work.

### Edge Cases

- Varias pestañas renuevan a la vez; una respuesta antigua llega después del logout o de un nuevo login.
- El equipo duerme y despierta, el reloj cambia o CAO se reinicia durante una renovación.
- El servidor está apagado: la web distingue «servidor no disponible» de «sesión vencida» y no borra una sesión válida por un fallo de red.
- Credenciales vacías, usuario inexistente, contraseñas incorrectas e intentos repetidos; la espera temporal se explica y no causa un bloqueo permanente de la cuenta.
- El navegador bloquea el almacenamiento necesario o se usa en modo privado: se explica que la persistencia no está disponible y se permite login temporal cuando sea posible.
- Cerrar el navegador sin recordar sesión, restaurar ventanas o perder sus datos: una restauración de su estado temporal puede continuar la misma sesión, siempre dentro de sus límites; una nueva sesión sin ese estado exige login.
- Una renovación falla durante una escritura: no reenviar el lanzamiento, asignación o entrada a un agente con resultado incierto.
- Una página de otro origen intenta entrar, renovar, cerrar sesión o controlar una terminal: no obtiene acceso ni provoca efectos protegidos.
- Restaurar un backup no resucita sesiones revocadas: el procedimiento invalida los accesos de navegador del estado restaurado y requiere un nuevo login.
- El mecanismo de login local está deshabilitado o una instalación usa una identidad externa: no sustituir ni degradar esa política automáticamente.

## Requirements

### Functional Requirements

- **FR-001**: La web MUST ofrecer login mediante usuario y contraseña y mostrarlo al acceder sin sesión válida a funcionalidades protegidas (US1).
- **FR-002**: La creación inicial de la cuenta MUST requerir control local explícito del propietario; MUST NOT existir registro público, cuenta por defecto ni contraseña compartida (US1).
- **FR-003**: El login local MUST habilitarse explícitamente para la instalación personal; MUST preservar la política de identidad externa o el modo de otras instalaciones (US4).
- **FR-004**: El sistema MUST comprobar las credenciales y asociar el acceso a la identidad y permisos configurados del operador; los campos del login MUST NOT elegir permisos o propietarios (US1, US4).
- **FR-005**: Las contraseñas MUST NOT almacenarse de forma recuperable ni aparecer en enlaces, respuestas, historial, mensajes de diagnóstico o registros. La interfaz MUST permitir gestores de contraseñas (US1, US4).
- **FR-006**: La interfaz MUST ofrecer «Recordar este navegador» e indicar su vigencia; sin esa opción, MUST permitir recargas durante el uso y MUST NOT crear persistencia recordada. Una nueva sesión de navegador sin restaurar el estado temporal MUST exigir login (US2).
- **FR-007**: La sesión recordada MUST sobrevivir recargas, nuevas pestañas, reinicio de navegador y reinicio ordinario de CAO, hasta revocación o vencimiento (US2).
- **FR-008**: La renovación del acceso MUST ser automática durante una sesión vigente, sin pedir contraseña ni comandos cada hora; MUST respetar los límites de inactividad y duración total (US2, US3).
- **FR-009**: Las credenciales que mantienen la sesión MUST NOT exponerse a los scripts de la página, a enlaces de navegación ni a los registros; las renovaciones MUST restringirse a la instalación y origen autorizados (US2).
- **FR-010**: Consultas, escrituras, eventos y control de terminal MUST exigir identidad vigente y los permisos correspondientes; iniciar sesión MUST NOT ampliar autorización, emitir provisiones Work ni convertir al usuario en otra clase de operador (US2, US4).
- **FR-011**: La renovación concurrente y las respuestas antiguas MUST NOT borrar una sesión nueva ni reactivar una revocada; una revocación MUST prevalecer sobre renovaciones en curso (US2, US3).
- **FR-012**: El sistema MUST NOT repetir automáticamente escrituras o entregas a agentes con resultado incierto al renovar o reconectar; MUST permitir consultar su estado y comunicar la incertidumbre (US2).
- **FR-013**: La web MUST ofrecer cierre de la sesión actual y de todas las sesiones de la cuenta, con revocación comprobable y retorno al login (US3).
- **FR-014**: La revocación MUST impedir nuevas acciones protegidas de inmediato y retirar el control de terminales y eventos abiertos en un máximo de cinco segundos con el servicio disponible; MUST NOT cancelar el trabajo durable de agentes (US3).
- **FR-015**: El sistema MUST permitir cambiar la contraseña comprobando la contraseña actual y MUST revocar las sesiones de navegador anteriores al cambio (US4).
- **FR-016**: El propietario MUST disponer de recuperación local sin correo ni servicio de pago; MUST NOT existir restablecimiento anónimo remoto (US4).
- **FR-017**: Las credenciales incorrectas MUST producir un mensaje equivalente para usuario inexistente o contraseña incorrecta. Por defecto, diez fallos en cinco minutos por origen o cuenta MUST imponer una espera de un minuto, sin bloqueo permanente y sin impedir la recuperación local (US1).
- **FR-018**: La web MUST distinguir credenciales rechazadas, sesión vencida y servidor no disponible; un fallo de red MUST NOT revocar por sí mismo la sesión (US2, US3).
- **FR-019**: La incorporación MUST preservar identidad del operador, datos, recibos, permisos y provisiones vigentes. Las sesiones de navegador MUST ser independientes de las credenciales de CLI/TUI, servicios y proveedores (US4).
- **FR-020**: Las pruebas de aceptación MUST comprobar el login y la sesión en el navegador construido contra el servidor autenticado real, además de renovación, concurrencia, logout, caducidad, reinicio y compatibilidad. Una respuesta pública de salud MUST NOT sustituir estas comprobaciones (US1–US4).
- **FR-021**: Los límites de sesión MUST poder configurarse por el propietario. Valores iniciales propuestos: sesión recordada, siete días de inactividad y treinta días de duración absoluta; sesión temporal, ocho horas de inactividad y veinticuatro horas absolutas, o fin de la sesión temporal del navegador, lo que ocurra antes. Restaurar ventanas no elimina estos límites (US2, US3).
- **FR-022**: Los eventos de login, renovación fallida, logout, revocación y recuperación MUST poder auditarse sin incluir contraseñas ni credenciales de sesión (US1–US4).
- **FR-023**: El backup/restore MUST conservar cuenta e identidad, pero el procedimiento de restauración MUST invalidar las sesiones de navegador contenidas en el backup; MUST documentar recuperación y vuelta al login (US4).

- **FR-024**: La instalación personal sin cuenta MUST ofrecer dos vistas, Iniciar sesión y Crear cuenta, dentro del front principal, con un enlace desde el login para abrir la creación de cuenta, autorizada únicamente por el JWT vigente del operador configurado y origen local exacto. Crear la cuenta MUST conservar ese binding, iniciar sesión con cookie y conectar al dashboard; MUST NOT admitir registro anónimo ni cambiar identidad/scopes.

### Key Entities

- **Cuenta local del operador**: usuario elegido, material no recuperable de verificación de contraseña, estado de la cuenta y asociación explícita a la identidad existente y sus permisos.
- **Sesión de navegador**: acceso de una cuenta desde un navegador; política temporal o recordada, fechas de creación y última actividad, vencimiento absoluto y revocación.
- **Renovación de acceso**: continuidad autorizada de una sesión vigente; no puede recuperar una sesión vencida o revocada ni ampliar sus permisos.
- **Evento de autenticación**: resultado y momento de un cambio de acceso, con información suficiente para diagnóstico sin secretos.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Tras la configuración inicial, el propietario puede entrar desde un navegador limpio en menos de treinta segundos introduciendo sólo usuario y contraseña; no copia tokens ni ejecuta comandos de autenticación.
- **SC-002**: Una jornada de referencia de ocho horas atraviesa al menos tres vencimientos internos de acceso, recargas, dos pestañas y un reinicio de CAO con cero solicitudes adicionales de contraseña para una sesión recordada vigente. Los periodos largos pueden comprobarse adelantando el tiempo de manera controlada.
- **SC-003**: Cerrar y volver a abrir el navegador con datos conservados recupera la sesión recordada válida; una sesión temporal terminada exige login. Ambos escenarios tienen aceptación en un navegador real.
- **SC-004**: Todos los escenarios de credenciales incorrectas, sesión vencida o revocada y acceso desde otro origen rechazan las acciones protegidas; logout retira el control abierto dentro de cinco segundos con el servicio disponible.
- **SC-005**: Login, renovación y logout no duplican ninguna operación de agente o Work ni cambian su estado durable o autorizaciones por sí solos; los escenarios de caída y concurrencia incluyen comprobaciones de esos registros.
- **SC-006**: Cambiar o recuperar la contraseña permite volver a entrar con la nueva, rechaza la antigua y no conserva sesiones previas; restaurar un backup preserva la cuenta y requiere un nuevo login.
- **SC-007**: Ninguna contraseña o credencial que mantenga la sesión aparece en enlaces, historial, almacenamiento accesible a scripts o logs examinados durante la aceptación.

## Assumptions

- Alcance inicial: un propietario y una cuenta en el despliegue personal de este equipo; varias pestañas y sesiones del mismo usuario están dentro del alcance.
- Acceso sólo local. Un despliegue público/remoto, altas de múltiples usuarios, administración de roles, login social, correo de recuperación y autenticación multifactor quedan fuera de esta feature.
- La creación inicial se realiza en el front principal usando el acceso autorizado del operador existente; sólo la recuperación olvidada puede requerir un procedimiento local. El login, las recargas y las renovaciones no requieren consola.
- Los valores de vigencia de FR-021 son defaults documentados para planificación, configurables; no implican que la sesión dure indefinidamente.
- «Recordar este navegador» es una elección explícita del usuario; no garantiza persistencia si el navegador elimina datos o se usa en modo privado.
- Se reutilizan la identidad y las autorizaciones existentes. El diseño técnico deberá resolver su interoperabilidad sin reemplazar el contrato Work ni la autenticación de otros clientes.
- El acceso tradicional mediante bearer se conserva para integraciones autorizadas; deja de ser requisito del flujo normal de login web. Los enlaces privados anteriores no conceden sesiones recordadas; el acceso recordado requiere login con contraseña.
- No se selecciona todavía una librería, algoritmo, formato de sesión o mecanismo de almacenamiento. Esas decisiones y la migración reversible corresponden a la fase de planificación.
- El spec y su checklist quedan en el repositorio como conocimiento durable. No se duplica esta fase documental en la bóveda ni se guardan credenciales.

## Preferencia del propietario

El propietario fija el mínimo de contraseña en **10 caracteres** (máximo128). Aplica a configuración web, alta local, login, cambio y recuperación; se conserva el hash scrypt existente.
