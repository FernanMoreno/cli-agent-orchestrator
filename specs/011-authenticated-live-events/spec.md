# Feature Specification: Transporte autenticado de eventos y UI

**Feature Branch**: `011-authenticated-live-events` (no se creó rama; especificación documental)<br>
**Created**: 2026-10-03<br>
**Updated**: 2026-10-04<br>
**Status**: Draft<br>
**Input**: Hallazgos de auditoría sobre WebSocket, SSE, MCP Apps, renovación de sesión y límites de respuestas de clientes.

## User Scenarios & Testing

### User Story 1 — Ver eventos en vivo sin perder historial (P1)

El usuario abre la vista de eventos, ve el historial disponible y recibe eventos nuevos aun cuando su sesión exige autenticación. Si se desconecta, puede retomar desde un punto conocido.

**Why this priority**: Una vista que parece conectada pero deja de recibir eventos produce diagnósticos incorrectos y trabajo perdido.

**Independent Test**: Generar eventos antes, durante y después de conectar; desconectar y reconectar el cliente; comprobar orden, autenticación y ausencia de huecos.

**Acceptance Scenarios**:

1. **Given** un usuario con permiso de lectura, **When** abre el flujo autenticado, **Then** recibe historial y eventos nuevos autorizados.
2. **Given** un evento que ocurre mientras el usuario carga historial y activa el flujo, **When** la vista termina de conectarse, **Then** el evento aparece exactamente una vez.
3. **Given** una desconexión, **When** el usuario se reconecta desde el último punto confirmado, **Then** recibe los eventos posteriores sin duplicar los ya confirmados.
4. **Given** un usuario sin permiso o con sesión expirada, **When** intenta conectar, **Then** no recibe eventos y ve un estado claro para renovar o pedir acceso.

### User Story 2 — Proteger la comunicación con interfaces embebidas (P1)

La aplicación acepta respuestas de la ventana y sesión esperadas, sin exponer credenciales reutilizables en direcciones, historial del navegador ni logs.

**Why this priority**: Los mensajes falsificados o secretos en URLs permiten que otro contexto suplante respuestas o reutilice acceso.

**Independent Test**: Enviar respuestas correlacionadas desde la ventana correcta y desde otra ventana; revisar URLs, mensajes de error y logs de acceso.

**Acceptance Scenarios**:

1. **Given** un mensaje desde la ventana y origen esperados, **When** responde a una solicitud pendiente, **Then** la respuesta se procesa una vez.
2. **Given** un mensaje con ID válido desde otra ventana u origen, **When** llega al componente, **Then** se rechaza sin resolver solicitudes.
3. **Given** un cliente necesita autenticar una conexión, **When** inicia el transporte, **Then** no coloca un token reutilizable en una URL registrada por historial o access logs.

### User Story 3 — Recuperarse de solicitudes lentas o grandes (P2)

El usuario puede cancelar o esperar una renovación de sesión limitada, y una respuesta grande de la instancia local no consume recursos sin límite antes de ser rechazada.

**Why this priority**: Una petición colgada o una respuesta desproporcionada no debe congelar la interfaz ni agotar memoria.

**Independent Test**: Simular renovación que no responde, cancelación del usuario y respuestas que superan el límite permitido.

**Acceptance Scenarios**:

1. **Given** una renovación sin respuesta, **When** vence el tiempo permitido, **Then** la interfaz informa el fallo y permite recuperarse.
2. **Given** una solicitud cancelada, **When** llega una respuesta tardía, **Then** no sobrescribe el estado más reciente.
3. **Given** una respuesta de la instancia local por encima del límite configurado, **When** se lee, **Then** se detiene el consumo y se informa que fue rechazada por tamaño.

### Edge Cases

- El usuario pierde permiso mientras el flujo está abierto.
- Varios eventos tienen el mismo instante o llegan fuera de orden.
- Una secuencia de reconexiones repite el cursor o solicita un cursor expirado.
- Una respuesta MCP válida llega después de un timeout o desde una ventana recreada.
- La sesión vence durante la renovación o el usuario cancela la navegación.
- La respuesta local no anuncia tamaño o anuncia uno menor que el cuerpo real.

## Requirements

### Functional Requirements

- **FR-001**: El sistema MUST autenticar y autorizar cada flujo de eventos según el usuario y sus permisos vigentes.
- **FR-002**: El sistema MUST permitir que cada cliente autorizado acceda al flujo que le corresponde sin exponer credenciales reutilizables en canales visibles al usuario.
- **FR-003**: El sistema MUST permitir continuar un flujo desde el último evento confirmado y recuperar eventos posteriores.
- **FR-004**: El sistema MUST evitar pérdidas entre la carga inicial del historial y la suscripción a eventos nuevos.
- **FR-005**: El sistema MUST impedir que credenciales reutilizables aparezcan en URLs, historial o logs de acceso.
- **FR-006**: El sistema MUST validar que mensajes de interfaces embebidas provienen del contexto esperado antes de resolver solicitudes.
- **FR-007**: El sistema MUST aplicar un límite de tiempo y cancelación a autenticación y renovación de sesión.
- **FR-008**: El sistema MUST limitar incrementalmente el tamaño de respuestas externas y detener su lectura al alcanzar el límite.
- **FR-009**: El sistema MUST evitar que respuestas antiguas o duplicadas sobrescriban estado más reciente.
- **FR-010**: El sistema MUST mostrar estados distinguibles para conectado, reconectando, sin autorización y con error.

### Key Entities

- **Cursor de evento**: punto confirmado de una secuencia de eventos.
- **Permiso de flujo**: autoridad temporal para leer un conjunto limitado de eventos.
- **Mensaje embebido**: solicitud o respuesta correlacionada con origen y ventana esperados.
- **Presupuesto de solicitud**: límites de tiempo, cancelación y tamaño aceptables para una petición.

## Success Criteria

### Measurable Outcomes

- **SC-001**: El 100% de los eventos generados durante la transición entre historial y conexión aparece exactamente una vez.
- **SC-002**: El 100% de las conexiones sin permiso o con sesión expirada se rechaza sin entregar datos.
- **SC-003**: Ninguna credencial reutilizable aparece en URLs o access logs del flujo normal.
- **SC-004**: Los mensajes recibidos desde un origen no esperado no resuelven solicitudes pendientes.
- **SC-005**: Las solicitudes lentas y las respuestas por encima del límite terminan en un estado visible y acotado.

## Assumptions

- Los usuarios pueden perder conexión y necesitan recuperar el flujo sin recargar toda la aplicación.
- Algunas interfaces de navegador tienen límites sobre cómo envían credenciales; el diseño debe ofrecer un mecanismo compatible sin exponer secretos duraderos.
- Los clientes muestran estados de conexión y errores al usuario; los detalles de implementación del transporte quedan para el plan.
- El alcance incluye Web, TUI y MCP Apps donde comparten transporte o sesión.
- CAO y sus interfaces se ejecutan en la instalación local del usuario; no se añade un relay de eventos ni un servicio alojado.
- Las integraciones de proveedores existentes quedan fuera de esta especificación.
