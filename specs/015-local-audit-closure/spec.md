# Feature Specification: Cierre de brechas locales de auditoría

**Feature Branch**: 015-local-audit-closure

**Created**: 2026-10-07

**Status**: Implemented; final fork publication verification pending

**Input**: User request to specify the problems and errors identified by the audit, following AI_WORKFLOW.md.

## User Scenarios & Testing *(mandatory)*

Este spec reúne los cierres pendientes identificados al comparar el código con los specs 009–013 y con las pruebas reales de coordinación local. Busca que usuarios y mantenedores puedan confiar en el trabajo local, sus eventos, sus controles de publicación y sus plugins. Los specs 009–013 siguen siendo la fuente detallada de requisitos para cada área; este spec conecta sus criterios pendientes y agrega los casos de integración que faltaban.

El producto sigue siendo una herramienta de trabajo gratuita que se ejecuta localmente. Para la colaboración, varias instancias CAO pueden ejecutarse en la misma PC y coordinarse en el mismo proyecto.

### User Story 1 — Confiar en el resultado de cada tarea local (Priority: P1)

El operador puede saber si una tarea terminó, falló o fue cancelada. El estado presentado y el recibo de resultado cuentan la misma historia, incluso si la tarea termina mientras alguien la cancela o si una instancia CAO deja de responder.

**Why this priority**: Una respuesta contradictoria hace que el usuario no sepa si el trabajo sigue activo y puede impedir que el proyecto quede disponible para la siguiente tarea.

**Independent Test**: Ejecutar tareas que terminen con éxito, error y cancelación, incluidas cancelaciones simultáneas con la finalización. Comparar estado, recibo, resultado y disponibilidad del proyecto desde ambas instancias.

**Acceptance Scenarios**:

1. **Given** una tarea activa y un operador solicita cancelarla, **When** CAO confirma la cancelación, **Then** el estado y el recibo muestran cancelación y el proyecto queda disponible cuando ya no exista escritura activa.
2. **Given** que finalización y cancelación ocurren al mismo tiempo, **When** CAO comunica el resultado final, **Then** todas las consultas posteriores muestran un único resultado terminal coherente.
3. **Given** una instancia par que deja de responder durante una consulta, **When** el operador revisa la tarea, **Then** ve un estado de par no disponible, distinto de una tarea sin resultado.

### User Story 2 — Coordinar pares y consultar eventos sin perder control (Priority: P1)

El operador conecta CAO locales autorizados al mismo proyecto, consulta sus sesiones y recibe eventos sin perder actividad durante una reconexión. Ninguna instancia no autorizada puede leer el flujo, resolver mensajes de otra ventana o recibir una credencial reutilizable.

**Why this priority**: La colaboración ocurre entre procesos independientes que comparten trabajo; errores de identidad, permisos o continuidad pueden exponer datos o hacer desaparecer actividad.

**Independent Test**: Conectar dos instancias en una misma PC; pausar y reanudar la conexión durante nuevos eventos; revocar un par, cambiar el origen de un mensaje y detener otra instancia mientras se consultan sesiones.

**Acceptance Scenarios**:

1. **Given** un cliente autorizado que se desconecta después de confirmar un evento, **When** vuelve a conectarse, **Then** recibe todos los eventos posteriores sin huecos y no muestra duplicados.
2. **Given** un cliente sin permiso vigente o un mensaje de origen inesperado, **When** intenta consultar eventos o resolver una solicitud pendiente, **Then** CAO rechaza esa operación sin entregar datos ni aceptar la respuesta.
3. **Given** que una instancia par no tiene sesiones o dejó de ejecutarse, **When** el operador consulta sus sesiones, **Then** CAO distingue entre lista vacía e instancia no disponible sin interrumpir la instancia local.
4. **Given** una operación local dirigida a un par que no está autorizado para el proyecto y la acción, **When** se solicita, **Then** se rechaza antes de compartir contenido o credenciales reutilizables.

### User Story 3 — Usar workflows que se recuperan sin atascarse (Priority: P1)

El usuario ve avanzar el trabajo elegible aunque haya muchas tareas en espera. Ante fallos temporales, CAO reintenta de forma acotada; al terminar una tarea, conserva el resultado útil y libera los recursos activos.

**Why this priority**: Una cola atascada, un ciclo rápido de reintentos o recursos retenidos puede degradar todas las tareas locales.

**Independent Test**: Procesar una cola superior al tamaño habitual de una ronda, introducir fallos temporales repetidos, completar tareas y validar configuraciones correctas e incorrectas.

**Acceptance Scenarios**:

1. **Given** más tareas elegibles que las que caben en una ronda, **When** el servicio procesa rondas sucesivas, **Then** cada tarea elegible puede avanzar sin quedar postergada permanentemente por tareas no elegibles.
2. **Given** un fallo sostenido de almacenamiento u observación, **When** CAO intenta recuperar el trabajo, **Then** los reintentos tienen pausas acotadas y no forman un ciclo rápido ilimitado.
3. **Given** opciones que intentan cambiar la identidad asignada por CAO o una configuración con campos no reconocidos, **When** se valida el workflow, **Then** la identidad se conserva y la configuración inválida se explica antes de ejecutar.
4. **Given** una tarea finalizada y otra nueva para la misma ejecución, **When** CAO limpia recursos antiguos, **Then** conserva el resultado y no elimina el registro de la tarea nueva.

### User Story 4 — Publicar cambios validados y probar proveedores sin exponer cuentas (Priority: P1)

El mantenedor puede probar proveedores reales de forma aislada y publicar solo el contenido exacto que superó las validaciones requeridas. El camino manual ofrece las mismas garantías que el automático.

**Why this priority**: Una validación omitida o hecha sobre otro contenido puede producir una publicación defectuosa; usar credenciales generales del usuario en una prueba también puede exponer su cuenta.

**Independent Test**: Ejecutar casos manuales y automáticos con validaciones correctas, fallidas, ausentes y asociadas a contenido cambiado. Ejecutar una prueba de proveedor con credenciales aisladas y comprobar la limpieza tras éxito, error y cancelación.

**Acceptance Scenarios**:

1. **Given** una publicación manual o automática cuyo contenido no coincide con el que se validó, **When** se intenta publicar, **Then** CAO bloquea la publicación y muestra qué validación falta o no corresponde.
2. **Given** una prueba real de proveedor que no recibe un hogar autenticado aislado, **When** intenta iniciar, **Then** se detiene de forma visible y no usa las credenciales generales del usuario como alternativa.
3. **Given** una prueba que termina, falla o se cancela, **When** concluye la limpieza, **Then** ya no quedan disponibles sus credenciales o artefactos temporales.
4. **Given** un control obligatorio omitido o fallido, **When** se calcula el resultado de una publicación, **Then** se muestra como omitido o fallido y no como aprobado.

### User Story 5 — Revisar y habilitar plugins locales con información de confianza (Priority: P2)

El operador puede revisar quién produjo un plugin, de dónde viene, con qué versión es compatible y qué permisos solicita. CAO aplica la política local elegida y distingue claramente un plugin verificado de uno cuya procedencia no se pudo comprobar.

**Why this priority**: Los plugins pueden ampliar las acciones del agente; una versión fija no basta para saber quién produjo el contenido.

**Independent Test**: Revisar un plugin verificable, uno modificado, uno incompatible y uno sin información suficiente; comprobar la información visible y la decisión que aplica la política local.

**Acceptance Scenarios**:

1. **Given** un plugin disponible localmente, **When** el operador lo revisa antes de habilitarlo, **Then** puede ver productor, versión, procedencia, compatibilidad y permisos solicitados.
2. **Given** un plugin alterado o una procedencia no verificable, **When** se aplica la política configurada, **Then** CAO muestra el resultado como no verificado y no lo habilita si la política lo prohíbe.
3. **Given** un plugin que pide permisos no aprobados, **When** el operador lo revisa, **Then** no obtiene permisos automáticamente y la razón queda visible.

## Edge Cases

- La tarea termina correctamente en el mismo instante en que llega la cancelación.
- Una cancelación se confirma en una instancia y la consulta llega a otra antes de reconciliarse.
- La reserva temporal del proyecto vence mientras el proceso trabajador todavía puede escribir.
- Llega un evento durante la transición entre historial y conexión en vivo.
- Se revoca una autorización mientras existe una conexión abierta.
- Un origen de mensaje coincide con un identificador de solicitud, pero no con la ventana o contexto autorizado.
- El almacenamiento falla durante varias rondas y vuelve a estar disponible después.
- Un par desaparece a mitad de una enumeración de sesiones.
- La publicación se inicia manualmente con validaciones omitidas o hechas sobre una revisión diferente.
- La prueba real de proveedor se cancela durante el uso de credenciales temporales.
- Un plugin tiene una referencia de contenido fija, pero no existe evidencia confiable de quién lo produjo.
- Una matriz de compatibilidad o revisión de dependencias no se ejecuta y el resultado podría confundirse con una aprobación.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: CAO MUST mantener coherentes el estado visible, el recibo y el resultado de una tarea en todos sus estados terminales, incluidos éxito, fallo y cancelación.
- **FR-002**: Cuando una tarea deja de escribir en el proyecto, CAO MUST liberar la reserva temporal del proyecto y MUST impedir que una limpieza antigua elimine una tarea o resultado más reciente.
- **FR-003**: CAO MUST resolver una carrera entre finalización y cancelación en un único resultado terminal que permanezca igual en consultas posteriores.
- **FR-004**: La coordinación entre pares MUST comprobar la identidad, el proyecto y la acción autorizada antes de compartir tareas, sesiones o resultados.
- **FR-005**: La coordinación local MUST NOT transmitir una credencial maestra reutilizable a un par y MUST asociar cada operación con el principal y propietario autorizados.
- **FR-006**: Las consultas de sesiones MUST distinguir una lista vacía de un par no disponible y MUST tolerar que una sesión o el proceso par desaparezca durante la lectura.
- **FR-007**: Los eventos MUST estar autenticados según permisos vigentes y MUST poder recuperarse desde el último evento confirmado sin huecos ni duplicados visibles.
- **FR-008**: CAO MUST rechazar mensajes de interfaces embebidas cuyo origen no corresponda al contexto autorizado antes de resolver solicitudes pendientes.
- **FR-009**: Las credenciales reutilizables MUST NOT aparecer en URL ni registros de acceso del flujo normal. CAO MUST proteger o rechazar el envío de credenciales hacia destinos no confiables y MUST limitar el tiempo de autenticación, renovación y lectura de respuestas.
- **FR-010**: El servicio MUST hacer progresar el trabajo elegible por rondas sucesivas y MUST aplicar esperas acotadas ante fallos repetidos de almacenamiento u observación.
- **FR-011**: Los workflows MUST rechazar campos desconocidos antes de ejecutar y MUST preservar los campos de identidad administrados frente a opciones del usuario.
- **FR-012**: Las tareas terminadas MUST dejar el conjunto de tareas activas sin retener recursos indefinidamente, mientras se conserva la evidencia de resultado necesaria.
- **FR-013**: Las publicaciones manuales y automáticas MUST exigir las mismas validaciones y relacionarlas con el contenido exacto que se publica.
- **FR-014**: Las pruebas con proveedores reales MUST usar credenciales aisladas, MUST bloquearse si no puede establecerse ese aislamiento y MUST retirar credenciales y artefactos temporales al terminar, fallar o cancelarse.
- **FR-015**: CAO MUST mostrar si cada control requerido pasó, falló, fue omitido o no pudo ejecutarse; un control omitido MUST NOT presentarse como aprobado.
- **FR-016**: Antes de habilitar un plugin, el operador MUST poder revisar productor, procedencia, versión, compatibilidad y permisos, y CAO MUST aplicar la política local cuando esa evidencia falte o no coincida.
- **FR-017**: Cada artefacto oficial MUST poder relacionarse con su versión, el contenido validado y un inventario verificable de componentes.
- **FR-018**: Los controles obligatorios de compatibilidad, dependencias de todos los entornos distribuidos, arquitectura, tipos y seguridad MUST cubrir las versiones anunciadas, informar sus omisiones y bloquear el resultado cuando corresponda a la política.
- **FR-019**: Las validaciones de esta especificación MUST poder completarse para la coordinación CAO usando varias instancias en una misma PC, sin cuenta central, relay ni servicio CAO alojado.

### Entorno de cobertura obligatoria

FR-018 exige que el gate de cobertura ejecute las regresiones nativas en un host apto verificado, con las mismas selecciones y mínimos de Python/MCP Apps. Un probe denegado no acredita aceptación. La preparación de un runner desechable de CI debe comprobar capacidades reales antes de efectos y registrar/restaurar cualquier ajuste temporal; no cambia la política de hosts del producto ni sustituye aislamiento real por simulación. La restauración sólo puede ocurrir tras confirmar el drenaje del grupo propio; si no se confirma, la ejecución falla y declara la eliminación de la VM como frontera final, igual que ante SIGKILL del wrapper.

## Key Entities

- **Tarea local coordinada**: trabajo enviado entre instancias CAO autorizadas, con proyecto, origen, destinatario, estado, resultado y actividad de cancelación identificables.
- **Recibo terminal**: resultado final coherente que permite distinguir éxito, fallo y cancelación.
- **Evento confirmado**: evento que el cliente recibió y puede usar como punto de continuación.
- **Snapshot de sesiones locales**: respuesta que distingue sesiones disponibles, lista vacía y par no disponible.
- **Candidato de publicación**: contenido exacto con las validaciones asociadas a ese mismo contenido.
- **Ejecución aislada de proveedor**: prueba real con credenciales y artefactos de ciclo de vida acotado.
- **Registro de confianza de plugin**: evidencia local sobre productor, procedencia, compatibilidad, integridad y permisos del plugin.
- **Resultado de control**: estado verificable de una validación, incluidos aprobado, fallido, omitido y no disponible.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El 100% de las tareas que terminan en éxito, fallo o cancelación muestran un estado terminal igual al de su recibo en todas las consultas posteriores.
- **SC-002**: En pruebas de cancelación concurrente, ninguna reserva temporal se libera mientras la tarea aún puede escribir y ninguna queda retenida después de cesar toda escritura.
- **SC-003**: En pruebas de desconexión y reconexión, el 100% de los eventos generados después del último evento confirmado aparece sin huecos ni duplicados visibles.
- **SC-004**: Ninguna credencial reutilizable aparece en URL o registros de acceso de los flujos cubiertos por aceptación.
- **SC-005**: El 100% de intentos manuales y automáticos con validaciones ausentes, fallidas o de contenido distinto se bloquea.
- **SC-006**: Bajo una cola superior a una ronda de trabajo, todas las tareas elegibles pueden avanzar en rondas sucesivas; durante fallos sostenidos, los reintentos permanecen dentro del límite definido en planificación.
- **SC-007**: El 100% de los campos desconocidos se informa antes de ejecutar y ninguna opción del usuario reemplaza la identidad administrada.
- **SC-008**: Las pruebas de consultas concurrentes no producen errores internos y distinguen de forma consistente lista vacía de instancia local no disponible.
- **SC-009**: El 100% de los plugins revisados para habilitación muestra productor, procedencia, compatibilidad y permisos, o indica cuáles no pudieron verificarse; las alteraciones rechazadas por la política nunca se habilitan.
- **SC-010**: Cada control requerido queda registrado como aprobado, fallido, omitido o no disponible, y ningún resultado omitido se contabiliza como aprobado.
- **SC-011**: La coordinación entre dos o más instancias CAO de la misma PC completa los escenarios principales sin cuenta, relay ni servicio CAO alojado.

## Assumptions

- El producto es una herramienta gratuita de trabajo que el usuario ejecuta localmente.
- El alcance de coordinación es una sola PC y un entorno de ejecución compartido; varias instancias CAO pueden colaborar en un mismo proyecto.
- No se añade un servicio SaaS, coordinación entre PCs, relay remoto, directorio central ni marketplace alojado.
- Las pruebas de proveedor pueden usar los servicios propios del proveedor, pero CAO mantiene la autenticación local aislada y no comparte credenciales reutilizables entre pares.
- La política de confianza de plugins es elegida localmente por el operador; el sistema debe mostrar cuándo una procedencia no pudo verificarse.
- Este spec cierra brechas y aceptación de los specs aprobados [009](../009-local-cao-coordination/spec.md), [010](../010-local-release-integrity/spec.md), [011](../011-authenticated-live-events/spec.md), [012](../012-workflow-reliability/spec.md) y [013](../013-reproducible-plugin-trust/spec.md). No los reemplaza ni amplía su alcance de producto.
- El informe de auditoría que da origen a estos hallazgos es [estado de specs 009–013](../../docs/audits/estado-specs-009-013-2026-10-06.md).
- Las decisiones de tiempos máximos, backoff, tamaño de respuesta y duración de credenciales se fijarán durante la planificación usando límites verificables y coherentes con el proyecto.
