# Feature Specification: Integridad de pruebas y descargas locales

**Feature Branch**: `010-local-release-integrity` (no se creó rama; especificación documental)<br>
**Created**: 2026-10-04<br>
**Updated**: 2026-10-04<br>
**Status**: Draft<br>
**Input**: Hallazgos de auditoría sobre pruebas con proveedores reales, runners self-hosted, releases manuales y publicación de paquetes.

## Alcance

Este spec cubre el trabajo interno de quienes preparan y distribuyen la herramienta gratuita para uso local. No añade cuentas, suscripciones, un servicio central ni requisitos de conexión para quien usa CAO. Los controles de CI/CD existen para entregar descargas confiables.

## User Scenarios & Testing

### User Story 1 — Probar cambios sin exponer credenciales de mantenimiento (P1)

Quienes mantienen CAO pueden comprobar cambios usando las credenciales de prueba autorizadas. Estas credenciales quedan aisladas y no se heredan secretos generales de la cuenta que ejecuta la validación.

**Why this priority**: Código no revisado que accede a credenciales de un runner puede comprometer cuentas y entornos.

**Independent Test**: Solicitar pruebas desde una referencia permitida y otra no permitida; comprobar autorización, aislamiento de credenciales y limpieza del entorno.

**Acceptance Scenarios**:

1. **Given** una referencia protegida y una prueba autorizada, **When** se inicia la validación, **Then** usa solo las credenciales y permisos previstos para ese trabajo.
2. **Given** una referencia no confiable o sin autorización, **When** se solicita la prueba, **Then** no se inicia en un entorno que tenga secretos disponibles.
3. **Given** que no se configuró un almacén de credenciales de prueba, **When** comienza el trabajo, **Then** no recurre silenciosamente al HOME del usuario del runner.
4. **Given** una prueba completada o cancelada, **When** el entorno queda disponible para otro trabajo, **Then** no conserva credenciales ni artefactos sensibles anteriores.

### User Story 2 — Entregar descargas hechas desde contenido revisado (P1)

Quien mantiene el proyecto distribuye una nueva descarga de CAO solo cuando el contenido que la compone superó las revisiones acordadas.

**Why this priority**: La publicación representa una declaración de integridad para quienes instalan el producto.

**Independent Test**: Intentar preparar una descarga con cambios no validados o con contenido distinto del que se revisó; comprobar que la entrega se bloquea.

**Acceptance Scenarios**:

1. **Given** contenido cuya validación terminó con éxito, **When** la persona responsable prepara la descarga, **Then** el paquete corresponde a ese mismo contenido.
2. **Given** contenido sin las revisiones requeridas, **When** se solicita una descarga, **Then** la entrega se detiene antes de publicarla.
3. **Given** que el contenido cambió después de las revisiones, **When** se solicita una descarga, **Then** se exige revisar el contenido nuevo.
4. **Given** un fallo mientras se prepara o publica una descarga, **When** se informa el resultado, **Then** se identifica el fallo sin presentarlo como una entrega completa.

### User Story 3 — Limitar permisos de mantenimiento (P2)

Quienes mantienen el proyecto pueden limitar los permisos que usan las pruebas, validaciones y tareas de distribución.

**Why this priority**: Limitar autoridad reduce el impacto de una acción o workflow comprometido.

**Independent Test**: Inspeccionar permisos efectivos por trabajo y verificar que cada operación falla de forma explícita si le falta un permiso requerido.

**Acceptance Scenarios**:

1. **Given** un trabajo que no publica ni sube resultados de seguridad, **When** solicita permisos, **Then** no recibe permisos de escritura innecesarios.
2. **Given** un trabajo de publicación, **When** termina, **Then** su credencial no habilita operaciones ajenas a esa publicación.

### Edge Cases

- CI está en cola o no tiene resultado, pero el dispatch manual ya fue solicitado.
- La validación de CI corresponde al mismo nombre de branch pero a otro SHA.
- Una release se reintenta después de una creación parcial o un timeout.
- Una persona pierde autorización mientras la ejecución espera aprobación.
- El runner se interrumpe antes de limpiar el entorno.

## Requirements

### Functional Requirements

- **FR-001**: El sistema MUST permitir pruebas con proveedores reales solo desde referencias de confianza y con la aprobación requerida.
- **FR-002**: El sistema MUST aislar credenciales de prueba de la cuenta general del runner y no usar credenciales implícitas del HOME.
- **FR-003**: El sistema MUST retirar credenciales y artefactos sensibles al finalizar o cancelar cada ejecución.
- **FR-004**: El sistema MUST ligar cada release al identificador inmutable del contenido exacto que se validó.
- **FR-005**: El sistema MUST impedir releases cuando falta una validación requerida, está fallida o corresponde a contenido diferente.
- **FR-006**: El sistema MUST aplicar los mismos controles de integridad a releases manuales y automatizadas.
- **FR-007**: El sistema MUST conceder a cada trabajo automatizado solo la autoridad necesaria para sus acciones.
- **FR-008**: El sistema MUST informar quién inició, aprobó y publicó una release y qué validaciones la respaldan.

### Key Entities

- **Candidato de release**: versión y contenido inmutable que se propone publicar.
- **Ejecución de validación**: resultado de controles asociado a un candidato exacto.
- **Trabajo de runner**: ejecución aislada con identidad, permisos y ciclo de vida delimitados.
- **Aprobación de publicación**: decisión de una persona autorizada sobre un candidato validado.

## Success Criteria

### Measurable Outcomes

- **SC-001**: El 100% de las releases publicadas tiene una validación exitosa del mismo contenido.
- **SC-002**: El 100% de las ejecuciones desde refs no confiables se bloquea antes de acceder a credenciales.
- **SC-003**: Ninguna prueba real obtiene credenciales generales del runner por fallback implícito.
- **SC-004**: Cada ejecución y publicación tiene responsable, estado y evidencia de validación consultables.

## Assumptions

- Las pruebas con proveedores configurados pueden seguir existiendo; el objetivo es limitar su origen y autoridad, no eliminarlas.
- Las descargas manuales requieren al menos los mismos controles de contenido que las automatizadas.
- Las personas responsables de release tienen roles distintos de quienes solo ejecutan pruebas.
- Las pruebas con proveedores usan integraciones que mantiene y configura el proyecto; una instalación personal no necesita ejecutarlas.
- CAO se descarga y ejecuta localmente; este spec protege el proceso de preparar esas descargas, no añade un servicio en línea al producto.
- La validación automatizada puede usar infraestructura de mantenimiento, pero la herramienta en tiempo de uso no depende de ella.
- La publicación externa permanece dentro del flujo existente; esta especificación no autoriza publicar durante su implementación.
