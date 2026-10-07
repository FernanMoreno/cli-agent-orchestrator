# Feature Specification: Integración completa de mejoras del fork

**Feature Branch**: `main` (destino explícito del usuario)
**Created**: 2026-10-02
**Status**: Implemented and verified on main; external prerequisites explicitly documented
**Input**: Implementar todas las mejoras y correcciones funcionales nuevas identificadas en ramas del fork y upstream relacionado, sin dejar funcionalidades útiles fuera y conservando la mejor solución cuando existan alternativas.

## User Scenarios & Testing

### User Story 1 — Ejecución fiable y recuperación autónoma (P1)

El usuario delega una tarea y recibe un resultado verificable, incluso ante recibos tardíos, reinicio, timeout o fallo temporal de almacenamiento. Una tarea incierta nunca se repite automáticamente mientras pueda seguir ejecutándose.

**Independent Test**: reproducir los fallos de la auditoría con transporte controlado y comprobar resultado, identidad e historial de entregas.

**Acceptance Scenarios**:
1. Dado un trabajador que ya recibió una tarea, cuando vence la espera, no se crea otro intento hasta resolver el existente de manera autorizada.
2. Dado un fallo al reconstruir un proveedor, cuando se consulta su resultado, ninguna respuesta histórica se presenta como verificada.
3. Dado un almacén temporalmente inaccesible, la observación mantiene límites de frecuencia y trabajo, conserva mensajes e incertidumbre y se recupera sin reenviar la tarea.
4. Dada evidencia auténtica tardía, la tarea vigente se reconcilia automáticamente sin cambiar generación ni repetir ejecución.

### User Story 2 — Herramientas, permisos y diagnósticos coherentes (P1)

El agente y el operador reciben herramientas que realmente pueden usar, estados inequívocos y acciones de recuperación efectivas en cada interfaz.

**Independent Test**: matriz proveedor/rol/allowlist e interfaces, con tareas pendientes, reconciliadas, canceladas, fallidas y verificadas.

**Acceptance Scenarios**:
1. Un rol desconocido no amplía permisos y una restricción explícita no se modifica implícitamente.
2. Una herramienta concedida mediante selector compatible no se deniega por un matcher diferente.
3. Todas las interfaces distinguen espera normal, incertidumbre y avería; ninguna ofrece como disponible una acción inexistente.
4. La política efectiva y el perfil del supervisor no exigen herramientas prohibidas.

### User Story 3 — Coordinación y autoría controladas (P1)

El usuario puede crear, revisar, aprobar y ejecutar planes; el coordinador continúa tras resultados tardíos, proporciona correcciones acotadas y valida evidencia actual del objetivo.

**Independent Test**: cadena de tareas con aprobación, resultado diferido, una corrección de herramienta/contexto y comprobación de artefactos actuales.

**Acceptance Scenarios**:
1. Un plan aprobado deja de ser ejecutable con esa aprobación si cambia el material relevante.
2. Un resultado aceptado genera una única continuación, incluso con eventos repetidos o reinicio.
3. La corrección usa herramientas y autoridad existentes; el número de correcciones es finito y el escalado explica causa y evidencia.
4. Un informe sobre revisión antigua no satisface un criterio que exige archivos actuales.

### User Story 4 — Conservar toda capacidad útil de las ramas (P2)

El usuario conserva sus flujos actuales y obtiene capacidades funcionales nuevas de proveedores, perfiles, memoria, grafo, Apps, tareas/epics, fleet y runtimes remotos.

**Independent Test**: inventario completo de comportamiento de las referencias congeladas, con decisión y prueba por capacidad.

**Acceptance Scenarios**:
1. Cada mejora funcional se incorpora o queda cubierta por una implementación equivalente que conserve sus escenarios.
2. Una sustitución incluye comparación de compatibilidad, evidencia y razón; una mera diferencia de nombre o commit no prueba ausencia.
3. Los despliegues locales, multinodo, elásticos y de runtime remoto mantienen sus capacidades propias sin cruzar credenciales ni autoridad.
4. El usuario puede usar las capacidades nuevas sin depender de activación accidental de publicación, cambios de credenciales o recursos externos.

### Edge Cases

- Dos lectores reconstruyen simultáneamente el mismo proveedor; la generación cambia durante una verificación.
- Fallan a la vez captura, persistencia de diagnóstico y callback de deadline.
- Un diálogo o espera de cuota tapa una respuesta auténtica tras reinicio.
- Se pierde la respuesta de una asignación que ya fue aceptada.
- Un resultado o evento remoto llega tras reconnect, cancelación o sustitución de runtime.
- Una entrada se envía mientras el frame todavía muestra el turno anterior.
- Una actualización de plan coincide con aprobación o lectura de material de ejecución.
- Dos ramas ofrecen modelos distintos para turnos, memoria, tareas, permisos o UI.

## Requirements

### Functional Requirements

- **FR-001**: Conservar cambios preexistentes y desarrollar en main sin operaciones de historial/publicación no solicitadas.
- **FR-002**: Mantener un inventario por capacidad que cubra todas las32 ramas del fork congeladas,36 commits adicionales de upstream/main y candidatos upstream de autoría, secuencia de turnos y runtime remoto.
- **FR-003**: Cada capacidad debe tener origen, decisión (incorporar/adaptar/equivalente), tareas, evidencia de aceptación y estado real; ninguna pendiente se clasifica como completada.
- **FR-004**: Resolver todos los hallazgos B01-B11 y el riesgo R01 de la auditoría con regresiones de sus escenarios.
- **FR-005**: No presentar respuesta o trabajo como finalizado por estado visual, silencio, timeout, expiración de lease o texto histórico.
- **FR-006**: Limitar observación y persistencia de diagnósticos de manera independiente, preservando incertidumbre si falla almacenamiento.
- **FR-007**: Reobservar evidencia tardía y continuar tareas durables exactamente una vez por identidad/generación autorizada.
- **FR-008**: Conservar transcripts, mensajes, generaciones y cancelación protegida existentes.
- **FR-009**: Unificar resolución efectiva de herramientas, permisos y diagnósticos en proveedores e interfaces; roles desconocidos y restricciones explícitas no amplían acceso.
- **FR-010**: Aplicar y mostrar capacidades efectivas de Apps, recursos disponibles y recuperación de turnos sin exponer evidencia privada.
- **FR-011**: Incorporar el mejor control de caché/proyección: trabajo independiente del request, límites, degradación observable y catálogo, conservando ámbito y partición de propietario.
- **FR-012**: Completar autoría, aprobación, identidad del plan, material congelado y admisión de ejecución con compatibilidad explícita del flujo anterior.
- **FR-013**: Completar el seguimiento de objetivos con progreso/evidencia, correcciones acotadas, validación actual y escalado; no conceder nueva autoridad por iniciativa del modelo.
- **FR-014**: Conservar o incorporar capacidades funcionales de tareas/epics, dependencias y feedback iterativo mediante el modelo más verificable disponible.
- **FR-015**: Incorporar mejoras funcionales de proveedores y del protocolo causal de entrada/salida, manteniendo separados índice de turno y diagnóstico de recuperación.
- **FR-016**: Incorporar perfiles KAS con política efectiva y controles opt-in, soporte de providers nuevos y capacidades nativas acotadas donde correspondan.
- **FR-017**: Completar vista y routing multinodo, ejecución remota y recuperación de launch/status/delete sin alterar los contratos de despliegue local y Work.
- **FR-018**: Conservar el mejor CRUD de perfiles, TUI, memoria/vault/import/export y prevención de parsers costosos; equivalencia se acredita por conducta y pruebas.
- **FR-019**: Incorporar ejemplos, correcciones de documentación, build/dependencias/CI y automatizaciones funcionales de las ramas; activar publicaciones o recursos externos sólo con instrucciones explícitas correspondientes.
- **FR-020**: Cerrar únicamente tras pruebas aplicables, revisión del diff, gate y revisión de composición, con evidencia honesta de casos reales y prerrequisitos faltantes.

### Key Entities

- **IntegrationCapability**: mejora funcional, referencia original, alternativas, solución elegida, tareas y pruebas que acreditan cobertura.
- **ExecutionAttempt**: trabajo autorizado con generación, entrega, evidencia, incertidumbre, cancelación y resultado.
- **TurnObservation**: correlación causal de entrada y frame, distinta de la evidencia durable del resultado.
- **GoalCheckpoint**: objetivo, progreso observable, revisión de artefactos, presupuesto de correcciones y escalado.
- **ExecutionPlan**: contenido/material/ámbito congelados y aprobación ligada a su identidad.
- **ExecutionRuntime**: ubicación local o remota, identidad/incarnación y comandos/resultados correlacionados.

## Success Criteria

- **SC-001**: El100% de capacidades funcionales inventariadas tiene cobertura implementada o equivalencia demostrada; cero pendientes al cierre.
- **SC-002**: Los escenarios de los tres fallos P1 reproducidos producen cero ejecuciones duplicadas, cero resultados sin prueba y observación acotada.
- **SC-003**: Una respuesta tardía válida resuelve el mismo intento sin intervención rutinaria ni repetición de tarea.
- **SC-004**: Todas las interfaces incluidas muestran el mismo diagnóstico y sólo acciones efectivas autorizadas.
- **SC-005**: Eventos repetidos o reinicio provocan una sola continuación de tarea; revisión antigua no satisface aceptación actual.
- **SC-006**: Flujos vigentes y capacidades nuevas pasan sus pruebas de aceptación y composición; límites externos quedan explícitos sin éxito inventado.

## Assumptions

- La petición del usuario autoriza implementación en main y selección de alternativas conforme a las auditorías previas; no autoriza commits, pushes, releases ni mutaciones de infraestructura externa.
- Se conservarán garantías más fuertes de Work/Spec007 y autenticación browser en lugar de reemplazarlas por variantes antiguas.
- Las alternativas se evalúan por conservación de conducta, evidencia durable, permisos, recuperación, compatibilidad, pruebas y complejidad, en ese orden.
- Los modos opcionales se entregan funcionales con activación explícita/configurable; no se activan contra cuentas o infraestructura personal para demostrar que el código existe.
- La integración se ejecuta por componentes y dependencias; todos pertenecen al alcance y no se descartan por ser posteriores en el orden.
