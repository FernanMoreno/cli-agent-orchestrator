# Feature Specification: Instalación local reproducible y plugins confiables

**Feature Branch**: `013-reproducible-plugin-trust` (no se creó rama; especificación documental)<br>
**Created**: 2026-10-03<br>
**Updated**: 2026-10-04<br>
**Status**: Draft<br>
**Input**: Hallazgos de auditoría sobre instalación local, versiones soportadas, dependencias reproducibles y confianza en plugins.

## User Scenarios & Testing

### User Story 1 — Instalar CAO localmente con instrucciones repetibles (P1)

El usuario puede instalar CAO en su propio equipo siguiendo instrucciones claras, con versiones compatibles y dependencias conocidas.

**Why this priority**: La instalación local debe funcionar de forma predecible sin requerir un servicio central ni una infraestructura de equipo.

**Independent Test**: Instalar CAO en cada entorno declarado compatible siguiendo solo las instrucciones incluidas y comprobar que las dependencias coinciden con lo documentado.

**Acceptance Scenarios**:

1. **Given** una versión que el proyecto declara compatible, **When** el mantenedor ejecuta la validación de esa versión, **Then** el resultado queda visible y bloquea una regresión nueva.
2. **Given** una guía de instalación local, **When** otra persona la sigue desde un equipo limpio, **Then** puede completar los mismos pasos sin depender de versiones móviles o servicios CAO alojados.
3. **Given** una dependencia con una actualización de seguridad o compatibilidad disponible, **When** llega la revisión programada, **Then** el mantenedor recibe un cambio evaluable o un informe de bloqueo.

### User Story 2 — Verificar procedencia y permisos de plugins (P1)

El operador puede saber quién produjo un plugin, qué compatibilidad declara y con qué autoridad se ejecutará. La política local puede impedir plugins cuya procedencia no cumple sus reglas.

**Why this priority**: Plugins amplían las capacidades del agente y pueden procesar datos o invocar herramientas.

**Independent Test**: Instalar un plugin con procedencia verificada, uno alterado, uno incompatible y uno no firmado; comprobar que el usuario ve sus diferencias y se aplica su política.

**Acceptance Scenarios**:

1. **Given** un plugin verificable y compatible, **When** el operador lo revisa, **Then** ve productor, versión, compatibilidad declarada y permisos antes de habilitarlo.
2. **Given** un plugin cuya firma o procedencia no coincide, **When** se intenta habilitar, **Then** el sistema informa la discrepancia y aplica la política de confianza configurada.
3. **Given** un plugin que solicita permisos fuera de la política, **When** el operador lo revisa, **Then** no obtiene autoridad implícita y la razón del rechazo queda visible.

### User Story 3 — Reconocer paquetes locales confiables (P2)

El usuario que instala un paquete local puede comprobar que viene del proyecto y que corresponde a la versión anunciada.

**Why this priority**: Una procedencia verificable reduce el riesgo de instalar artefactos sustituidos o reconstruidos desde entradas desconocidas.

**Independent Test**: Verificar un paquete publicado y comparar el resultado con una copia alterada o de origen desconocido.

**Acceptance Scenarios**:

1. **Given** un paquete íntegro del proyecto, **When** el usuario revisa su procedencia, **Then** puede relacionarlo con la versión anunciada y consultar qué contiene.
2. **Given** un paquete modificado o de procedencia inválida, **When** se verifica, **Then** el resultado indica que no es confiable.

### Edge Cases

- Una versión de Python anunciada deja de recibir soporte externo.
- Un lock de dependencias queda obsoleto o una fuente de paquetes no está disponible.
- Dos builds usan los mismos nombres de versión pero entradas diferentes.
- La información de confianza local está ausente, expirada o revocada.
- Un plugin está firmado pero declara compatibilidad incorrecta o pide permisos excesivos.
- El resultado de una comprobación de dependencias llega después de preparar un paquete.

## Requirements

### Functional Requirements

- **FR-001**: El proyecto MUST mantener alineadas las versiones declaradas como compatibles y las versiones cubiertas por validación automática.
- **FR-002**: El proyecto MUST realizar una revisión recurrente de dependencias para todos los ecosistemas de runtime que distribuye.
- **FR-003**: El proyecto MUST mantener instrucciones de instalación local repetibles desde entradas conocidas y revisables.
- **FR-004**: El proyecto MUST ejecutar controles de arquitectura, tipos y seguridad como gates cuya falla sea visible y bloqueante según política.
- **FR-005**: El operador MUST poder verificar el productor, procedencia, compatibilidad y permisos declarados por cada plugin antes de habilitarlo.
- **FR-006**: El sistema MUST detectar plugins alterados, incompatibles o con procedencia no verificable y aplicar una política explícita.
- **FR-007**: El usuario MUST poder distinguir una verificación satisfactoria de un resultado omitido, incompleto o no disponible.
- **FR-008**: Cada paquete local distribuido MUST poder relacionarse con la versión del proyecto y con un inventario de componentes verificable.
- **FR-009**: Las políticas de plugin MUST permitir bloquear procedencias o permisos no aprobados sin conceder autoridad implícita.
- **FR-010**: Los resultados de versión, dependencia, arquitectura y procedencia MUST quedar asociados al cambio o artefacto que validan.

### Key Entities

- **Matriz de compatibilidad**: versiones de producto y plataformas que el proyecto declara y valida.
- **Snapshot de dependencias**: conjunto revisable de dependencias y su origen para un release.
- **Manifiesto de plugin**: productor, versión, compatibilidad, permisos y procedencia de un plugin.
- **Atestación de artefacto**: evidencia que liga un artefacto publicado con su origen y contenido declarado.
- **Resultado de gate**: estado, alcance y evidencia de una validación asociada a un cambio.

## Success Criteria

### Measurable Outcomes

- **SC-001**: El 100% de las versiones declaradas compatibles aparece en la matriz de validación o deja de anunciarse como soportada.
- **SC-002**: El 100% de los ecosistemas distribuidos recibe revisión recurrente de dependencias y presenta fallos de revisión de forma visible.
- **SC-003**: Otra persona puede completar una instalación local siguiendo las instrucciones publicadas sin resolver versiones móviles manualmente.
- **SC-004**: El 100% de los plugins habilitados muestra procedencia, compatibilidad y permisos antes de la activación.
- **SC-005**: Los plugins con procedencia alterada o permisos no aprobados nunca se habilitan en contra de la política configurada.
- **SC-006**: El 100% de los artefactos oficiales tiene una evidencia verificable de procedencia e inventario de componentes.

## Assumptions

- El proyecto desea sostener de forma explícita las versiones que anuncia como compatibles.
- El operador puede elegir una política más estricta para plugins; los avisos deben distinguirse de una autorización.
- Algunos controles pueden tener excepciones documentadas, pero no deben aparentar que pasaron si fueron omitidos.
- Esta especificación define resultados de confianza y reproducibilidad; no fija un proveedor de firma ni una herramienta concreta.
- El alcance cubre CAO instalado y ejecutado por el usuario en su equipo; servicios alojados, despliegues de clúster y mercados centrales de plugins quedan fuera por ahora.
- La verificación de plugins debe funcionar con información de confianza disponible localmente y no debe exigir una cuenta central.
