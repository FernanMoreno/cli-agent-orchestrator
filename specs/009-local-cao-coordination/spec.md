# Feature Specification: Coordinación entre instancias CAO locales

**Feature Branch**: `009-local-cao-coordination` (no se creó rama; especificación documental)<br>
**Created**: 2026-10-04<br>
**Updated**: 2026-10-04<br>
**Status**: Approved for implementation<br>
**Input**: El usuario quiere que varias instancias CAO puedan conectarse y coordinarse sobre un mismo proyecto desde una sola PC, sin convertir CAO en un servicio alojado.

## User Scenarios & Testing

Este spec cubre varias instancias CAO independientes que se ejecutan en la misma PC y colaboran en el mismo proyecto. El [spec 006](../006-agent-collaboration/spec.md) cubre el otro modo: un CAO coordinador con varios agentes trabajadores locales. Ambos modos forman parte del alcance local actual. Conectar equipos distintos queda fuera de alcance.

### User Story 1 — Conectar instancias CAO del mismo equipo (P1)

La persona ejecuta dos o más instancias CAO independientes en su PC, reconoce cada una y autoriza la conexión entre ellas para un proyecto local concreto.

**Why this priority**: Es el requisito básico para que varios CAO colaboren en un mismo proyecto sin una cuenta o servicio central.

**Independent Test**: Iniciar dos instancias CAO en una PC, conectarlas bajo autorización explícita y comprobar que ambas reconocen el mismo proyecto local y la identidad de la otra instancia.

**Acceptance Scenarios**:

1. **Given** dos instancias CAO activas en la misma PC, **When** la persona selecciona una desde la otra, **Then** CAO muestra la identidad local de ambas y permite confirmar o rechazar la conexión.
2. **Given** una conexión confirmada, **When** la persona vincula un proyecto local disponible para ambas, **Then** cada instancia muestra que participa en el mismo proyecto.
3. **Given** que CAO no puede verificar que la instancia elegida corre en esta PC o que ambas apuntan al mismo proyecto, **When** se solicita la conexión, **Then** no la presenta como conexión local válida ni comparte credenciales o trabajo.
4. **Given** una sola instancia CAO, **When** la persona trabaja sin añadir pares, **Then** todas las funciones locales existentes siguen disponibles sin configuración de coordinación.

### User Story 2 — Coordinar tareas entre instancias sobre el mismo proyecto (P1)

La persona puede asignar una tarea de proyecto a otra instancia CAO y consultar quién la aceptó, su progreso y el resultado devuelto. Cada instancia puede mantener su propio supervisor y agentes.

**Why this priority**: Permite que varios CAO se repartan trabajo en el mismo proyecto manteniendo sus sesiones y ciclos de vida independientes.

**Independent Test**: Asignar tareas identificables entre dos instancias locales, devolver resultados y comprobar que remitente, destinatario, tarea y proyecto se mantienen asociados correctamente.

**Acceptance Scenarios**:

1. **Given** dos instancias autorizadas para el mismo proyecto, **When** una asigna una tarea a la otra, **Then** la instancia destinataria recibe la tarea con el proyecto y los permisos acordados.
2. **Given** una tarea aceptada, **When** la instancia destinataria informa progreso o resultado, **Then** la instancia de origen puede identificar la tarea, el estado y la instancia que los informó.
3. **Given** una respuesta perdida o una solicitud repetida, **When** la instancia de origen consulta o reintenta, **Then** CAO informa el estado existente sin crear una segunda tarea equivalente en silencio.
4. **Given** más de dos instancias conectadas al mismo proyecto, **When** se asignan tareas distintas, **Then** cada resultado vuelve a quien corresponda y no se mezcla con otras tareas.

### User Story 3 — Proteger el proyecto y las credenciales locales (P1)

La persona conserva el control de qué instancia puede usar la API de coordinación, qué proyecto CAO puede seleccionar y qué acciones puede solicitar. Los pares son procesos confiables bajo la misma cuenta local: los permisos de CAO limitan la coordinación, pero no aíslan el proceso del agente ni retiran los permisos de archivos que ya tiene esa cuenta.

**Why this priority**: Varias instancias en una PC comparten recursos del equipo, así que la coordinación debe conservar límites visibles y evitar exponer secretos.

**Independent Test**: Conectar dos instancias, intentar solicitar por la API de coordinación un proyecto no autorizado y una acción no concedida, y comprobar que se rechazan; verificar que no se copia la credencial maestra de una instancia a otra.

**Acceptance Scenarios**:

1. **Given** una instancia confirmada con permiso limitado a un proyecto y unas acciones, **When** solicita por la API de coordinación otra ruta o acción, **Then** la instancia receptora la rechaza sin ampliar el permiso.
2. **Given** una solicitud de coordinación local, **When** se prepara su autenticación, **Then** la credencial maestra reutilizable de la instancia no se entrega a su par.
3. **Given** dos tareas que modifican los mismos archivos o cambios incompatibles, **When** las instancias trabajan simultáneamente, **Then** CAO hace visible el conflicto o evita que un cambio se sobrescriba silenciosamente.
4. **Given** una instancia que intenta contactar un destino de red arbitrario, **When** esa conexión se inicia como parte de esta función, **Then** CAO no la trata como un par local autorizado ni le envía credenciales.

### User Story 4 — Detener o revocar una instancia participante (P2)

La persona puede desconectar una instancia, detener su proceso o quitar su permiso para el proyecto. Las otras instancias siguen funcionando y muestran qué tareas quedaron pendientes.

**Why this priority**: Una instancia detenida o revocada no debe bloquear toda la colaboración ni dejar trabajo con estado engañoso.

**Independent Test**: Asignar una tarea, detener o revocar la instancia destinataria durante y después de la asignación, y comprobar los estados visibles y la continuidad de las instancias restantes.

**Acceptance Scenarios**:

1. **Given** un par CAO autorizado, **When** la persona revoca su permiso, **Then** las solicitudes nuevas de ese par se rechazan.
2. **Given** una tarea en curso cuando se detiene el proceso destinatario, **When** la otra instancia consulta su estado, **Then** distingue entre completada, pendiente e interrumpida, sin marcarla como éxito.
3. **Given** una instancia desconectada o detenida, **When** la persona usa CAO localmente, **Then** las demás instancias y el modo de una sola instancia continúan disponibles.

### Edge Cases

- Dos procesos intentan registrarse con la misma identidad o una identidad local cambia al reiniciar.
- El proceso CAO elegido termina y otro proceso intenta presentarse con su identidad local.
- Las instancias se conectan al mismo repositorio, pero declaran rutas de proyecto distintas.
- Se interrumpe la conexión después de aceptar una tarea, pero antes de devolver la respuesta.
- Se repite una asignación después de un timeout o reinicio.
- Dos asignaciones reclaman el mismo archivo o un cambio local aún no guardado.
- Una instancia se cierra mientras tiene tareas aceptadas o mensajes pendientes.
- El usuario revoca un permiso con tareas activas.
- Hay más de dos instancias conectadas al mismo proyecto.
- Una configuración intenta apuntar a otra PC, una IP de red o un servicio central.

## Requirements

### Functional Requirements

- **FR-001**: CAO MUST permitir que varias instancias independientes que se ejecutan en la misma PC se conecten tras una autorización explícita del usuario.
- **FR-002**: Antes de autorizar un par, CAO MUST mostrar su identidad y confirmar que pertenece al equipo local; MUST NOT tratar un host arbitrario como par local.
- **FR-003**: La persona MUST poder vincular los pares autorizados a un mismo proyecto local y detectar una ruta de proyecto incompatible antes de compartir tareas.
- **FR-004**: La coordinación MUST funcionar sin cuenta, directorio, relay, suscripción ni servicio CAO alojado.
- **FR-005**: El usuario MUST poder limitar o revocar las operaciones de coordinación CAO y el proyecto concedidos a cada par; estos permisos MUST NOT presentarse como aislamiento del sistema operativo para el agente.
- **FR-006**: Una tarea compartida MUST conservar identidad de origen y destino, proyecto, estado y resultado.
- **FR-007**: Reintentar o recibir dos veces una solicitud MUST producir un estado claro y MUST NOT crear tareas duplicadas silenciosamente.
- **FR-008**: Las instancias MUST poder informar progreso, aceptación, finalización, fallo e interrupción de sus tareas sin atribuir actividad a otro proceso.
- **FR-009**: CAO MUST hacer visibles los conflictos de cambios concurrentes sobre el mismo proyecto y MUST NOT sobrescribir cambios de otra instancia en silencio.
- **FR-010**: Una instancia MUST NOT recibir la credencial maestra reutilizable de otra instancia ni persistir tokens bearer de par; los grants guardan solo la clave pública del otro perfil. La clave privada de firma permanece en el directorio privado del perfil y no se transmite.
- **FR-011**: Desconectar o revocar un par MUST impedir sus nuevas operaciones y dejar claro qué ocurre con sus tareas activas.
- **FR-012**: Una instancia única y el modo de un CAO con trabajadores locales del spec 006 MUST seguir funcionando sin requerir pares independientes.
- **FR-013**: La función MUST limitar la coordinación a instancias en la misma PC; conexión CAO entre PCs queda fuera de alcance.

### Key Entities

- **Instancia CAO local**: proceso CAO independiente, con identidad y estado propios, ejecutado en la PC de la persona.
- **Par autorizado**: instancia local cuya identidad y permisos aceptó el usuario.
- **Proyecto compartido**: proyecto local al que los pares autorizados pueden acceder conforme a permisos.
- **Tarea coordinada**: trabajo con instancia de origen, instancia destinataria, proyecto, estado y resultado.
- **Permiso de par**: conjunto revocable de acciones y acceso de proyecto concedidos a una instancia concreta.
- **Conflicto de cambios**: trabajo simultáneo incompatible o solapado que requiere visibilidad o resolución antes de declarar un resultado coherente.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Dos o más instancias CAO de una PC pueden reconocerse y conectarse al mismo proyecto tras autorización explícita.
- **SC-002**: El 100% de las tareas coordinadas conserva el proyecto, el origen, el destinatario y su estado identificables.
- **SC-003**: El 100% de las solicitudes repetidas de la misma tarea se resuelve sin duplicación silenciosa.
- **SC-004**: Ninguna conexión entre pares transmite una credencial maestra reutilizable.
- **SC-005**: El 100% de los intentos de solicitar por la API de coordinación una ruta de proyecto, acción o destino no autorizado se rechaza antes de declarar éxito.
- **SC-006**: Detener o revocar un par no impide que las instancias restantes sigan trabajando localmente.
- **SC-007**: La conexión entre PCs, las cuentas centrales y los servicios alojados no son necesarios para completar los escenarios de aceptación.

## Assumptions

- “Local” significa que las instancias CAO participantes se ejecutan en una sola PC, dentro del mismo entorno de ejecución del sistema, y trabajan con un proyecto disponible en esa PC.
- Cada instancia conserva sus propias sesiones, configuración, permisos y ciclo de vida.
- Los pares autorizados se consideran confiables bajo la misma cuenta local. Los permisos CAO limitan las operaciones de coordinación por proyecto/acción; el trabajador conserva los permisos de sistema operativo de esa cuenta.
- El spec 006 cubre la colaboración entre un supervisor y trabajadores dentro de un mismo despliegue CAO; este spec cubre pares de instancias CAO independientes.
- La conexión entre instancias es opcional. El modo individual existente debe seguir siendo útil por sí solo.
- Los proveedores de modelos que la persona configure pueden seguir siendo servicios externos elegidos por ella; eso no amplía el alcance de coordinación CAO entre equipos.
- Fuera de alcance: coordinar instancias CAO en PCs distintas, hosting, SaaS, cuentas centrales, relay o directorio central.
