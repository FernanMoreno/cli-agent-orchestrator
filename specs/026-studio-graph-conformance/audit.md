# Evaluación detalle a detalle de la maqueta

Fecha: 2026-10-23. Fuente: HTML aprobado SHA `915b0110d1d775ad738d35bee881d9480d5f2b59cc787bc1d067c7bf9f604690` y código base `f2c381ed87fab87631a363097da50898de96cd06`.

Esta es una auditoría estática de fuente y documentos, no una nueva aceptación operativa. Se preservan los resultados y pendientes preexistentes de 025. La clasificación indica qué falta para cumplir el detalle completo mostrado, no que todo su soporte actual sea inexistente.

## Resultado principal

1. La maqueta calcula topología, estados y conexiones sintéticos. No se debe trasladar ese generador a producción como fuente de verdad.
2. El producto ya tiene agrupaciones opacas `Terminal.group`, jerarquía de trabajo, hijos nativos, asignaciones, mensajes, preguntas y varias superficies Studio. Parte de la brecha es exponer y unir esas fuentes con autorización; otra parte es la organización de equipos real.
3. El mapa actual es una instantánea parcial; no expone todavía la jerarquía organizativa y completa de ejecución de la maqueta. Descartar el texto del inbox al dibujar no evita que hoy el navegador lo reciba.
4. Un equipo no equivale a sesión, worker, terminal o instancia CAOS. Coordinador no equivale al primer agente ni a un permiso implícito. Crear un terminal no acredita entrega ni resultado.
5. La posición de una tarea debe apoyarse en su asignación real. Contar turnos o procesos terminados como hechas es incorrecto. La ruta crítica requiere evidencia adicional a una cadena dibujada.

## Clasificación

- **Capacidad nueva**: falta un contrato de producto completo para el significado mostrado; verificar y reutilizar soportes parciales al implementar.
- **Integración pendiente**: hay contratos o soporte parcial identificado, pero falta entregar y comprobar el recorrido representado.
- **Aceptación pendiente**: hay implementación o demostración acotada, pero no una evidencia suficiente del comportamiento productivo solicitado.

## Inventario

| Detalle | Estado de la brecha | Spec / requisito | Maqueta |
|---|---|---|---|
| M001 — Equipo registrado y nombre estable | capacidad nueva | [027 / FR-001](../027-agent-team-topology/spec.md) | [L462](../019-frontend-studio/mockup-studio-original.html#L462) |
| M002 — Subequipos reales | capacidad nueva | [027 / FR-003](../027-agent-team-topology/spec.md) | [L451](../019-frontend-studio/mockup-studio-original.html#L451) |
| M003 — Células y niveles adicionales | capacidad nueva | [027 / FR-002](../027-agent-team-topology/spec.md) | [L451](../019-frontend-studio/mockup-studio-original.html#L451) |
| M004 — Agente miembro de equipo | integración pendiente | [027 / FR-003](../027-agent-team-topology/spec.md) | [L489](../019-frontend-studio/mockup-studio-original.html#L489) |
| M005 — Responsable del grupo | capacidad nueva | [027 / FR-005](../027-agent-team-topology/spec.md) | [L488](../019-frontend-studio/mockup-studio-original.html#L488) |
| M006 — Agentes sin equipo conocido | capacidad nueva | [027 / FR-007](../027-agent-team-topology/spec.md) | [L489](../019-frontend-studio/mockup-studio-original.html#L489) |
| M007 — Reorganizar sin cambiar intentos históricos | capacidad nueva | [027 / FR-009](../027-agent-team-topology/spec.md) | [L491](../019-frontend-studio/mockup-studio-original.html#L491) |
| M008 — Grupos opacos existentes sin reinterpretación | integración pendiente | [027 / FR-011](../027-agent-team-topology/spec.md) | [L462](../019-frontend-studio/mockup-studio-original.html#L462) |
| M009 — Coordinador con autoridad efectiva | integración pendiente | [028 / FR-001](../028-team-delegation-authority/spec.md) | [L489](../019-frontend-studio/mockup-studio-original.html#L489) |
| M010 — Permiso para delegar comprobado en servidor | integración pendiente | [028 / FR-003](../028-team-delegation-authority/spec.md) | [L449](../019-frontend-studio/mockup-studio-original.html#L449) |
| M011 — Delegación con recibo de asignación | integración pendiente | [028 / FR-002](../028-team-delegation-authority/spec.md) | [L529](../019-frontend-studio/mockup-studio-original.html#L529) |
| M012 — Creación de terminal distinta de mando | integración pendiente | [028 / FR-004](../028-team-delegation-authority/spec.md) | [L529](../019-frontend-studio/mockup-studio-original.html#L529) |
| M013 — Capacidad y presupuesto antes del despacho | integración pendiente | [028 / FR-005](../028-team-delegation-authority/spec.md) | [L529](../019-frontend-studio/mockup-studio-original.html#L529) |
| M014 — Trabajo paralelo y destinos aislados | integración pendiente | [028 / FR-006](../028-team-delegation-authority/spec.md) | [L449](../019-frontend-studio/mockup-studio-original.html#L449) |
| M015 — Reintento de asignación sin duplicados | aceptación pendiente | [028 / FR-007](../028-team-delegation-authority/spec.md) | [L529](../019-frontend-studio/mockup-studio-original.html#L529) |
| M016 — Revocación de coordinador | capacidad nueva | [028 / FR-008](../028-team-delegation-authority/spec.md) | [L489](../019-frontend-studio/mockup-studio-original.html#L489) |
| M017 — Identidad agente/terminal/encarnación | integración pendiente | [029 / FR-001](../029-execution-graph-provenance/spec.md) | [L489](../019-frontend-studio/mockup-studio-original.html#L489) |
| M018 — Agente, tarea, intento y resultado distintos | integración pendiente | [029 / FR-002](../029-execution-graph-provenance/spec.md) | [L627](../019-frontend-studio/mockup-studio-original.html#L627) |
| M019 — Fila de agente corresponde al ejecutor real | integración pendiente | [029 / FR-005](../029-execution-graph-provenance/spec.md) | [L745](../019-frontend-studio/mockup-studio-original.html#L745) |
| M020 — Inspector con fuente y antigüedad | integración pendiente | [029 / FR-010](../029-execution-graph-provenance/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M021 — Historial de hijo con proceso eliminado | integración pendiente | [029 / FR-006](../029-execution-graph-provenance/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M022 — Extremo no visible y cobertura | integración pendiente | [029 / FR-007](../029-execution-graph-provenance/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M023 — Grafo de memoria separado de ejecución | integración pendiente | [029 / FR-009](../029-execution-graph-provenance/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M024 — Conexiones entre equipos | capacidad nueva | [030 / FR-001](../030-graph-hierarchy-aggregation/spec.md) | [L555](../019-frontend-studio/mockup-studio-original.html#L555) |
| M025 — Conexiones en subequipo y célula | capacidad nueva | [030 / FR-003](../030-graph-hierarchy-aggregation/spec.md) | [L1184](../019-frontend-studio/mockup-studio-original.html#L1184) |
| M026 — Vecindad del agente con relaciones originales | integración pendiente | [030 / FR-003](../030-graph-hierarchy-aggregation/spec.md) | [L582](../019-frontend-studio/mockup-studio-original.html#L582) |
| M027 — Relaciones internas dentro del grupo | capacidad nueva | [030 / FR-002](../030-graph-hierarchy-aggregation/spec.md) | [L555](../019-frontend-studio/mockup-studio-original.html#L555) |
| M028 — Puertos y conexiones exteriores | capacidad nueva | [030 / FR-010](../030-graph-hierarchy-aggregation/spec.md) | [L1027](../019-frontend-studio/mockup-studio-original.html#L1027) |
| M029 — Inspector de haz y número de vínculos | capacidad nueva | [030 / FR-006](../030-graph-hierarchy-aggregation/spec.md) | [L1126](../019-frontend-studio/mockup-studio-original.html#L1126) |
| M030 — Lista de relaciones atómicas del haz | capacidad nueva | [030 / FR-006](../030-graph-hierarchy-aggregation/spec.md) | [L1126](../019-frontend-studio/mockup-studio-original.html#L1126) |
| M031 — Abrir extremo en página correcta | capacidad nueva | [030 / FR-003](../030-graph-hierarchy-aggregation/spec.md) | [L1184](../019-frontend-studio/mockup-studio-original.html#L1184) |
| M032 — Agregación por proyectos | capacidad nueva | [030 / FR-004](../030-graph-hierarchy-aggregation/spec.md) | [L555](../019-frontend-studio/mockup-studio-original.html#L555) |
| M033 — Agregación por estados | capacidad nueva | [030 / FR-004](../030-graph-hierarchy-aggregation/spec.md) | [L555](../019-frontend-studio/mockup-studio-original.html#L555) |
| M034 — Conteos exactos frente a cobertura parcial | capacidad nueva | [030 / FR-005](../030-graph-hierarchy-aggregation/spec.md) | [L474](../019-frontend-studio/mockup-studio-original.html#L474) |
| M035 — Límite tarjetas, haces y puertos con acceso al resto | capacidad nueva | [030 / FR-008](../030-graph-hierarchy-aggregation/spec.md) | [L438](../019-frontend-studio/mockup-studio-original.html#L438) |
| M036 — Páginas y cursores de revisión | capacidad nueva | [030 / FR-009](../030-graph-hierarchy-aggregation/spec.md) | [L1087](../019-frontend-studio/mockup-studio-original.html#L1087) |
| M037 — Escenarios 100/10k/1M y alcance de medición | aceptación pendiente | [030 / FR-011](../030-graph-hierarchy-aggregation/spec.md) | [L372](../019-frontend-studio/mockup-studio-original.html#L372) |
| M038 — Línea de comunicación con dirección | integración pendiente | [031 / FR-001](../031-graph-message-lifecycle/spec.md) | [L613](../019-frontend-studio/mockup-studio-original.html#L613) |
| M039 — Separación inbox y cao.work.message | integración pendiente | [031 / FR-002](../031-graph-message-lifecycle/spec.md) | [L613](../019-frontend-studio/mockup-studio-original.html#L613) |
| M040 — Enviado/recibido/ACK separados de resultado | integración pendiente | [031 / FR-003](../031-graph-message-lifecycle/spec.md) | [L613](../019-frontend-studio/mockup-studio-original.html#L613) |
| M041 — Mensajes únicos al reagrupar | integración pendiente | [031 / FR-004](../031-graph-message-lifecycle/spec.md) | [L555](../019-frontend-studio/mockup-studio-original.html#L555) |
| M042 — Ventana reciente y paginación | integración pendiente | [031 / FR-005](../031-graph-message-lifecycle/spec.md) | [L966](../019-frontend-studio/mockup-studio-original.html#L966) |
| M043 — Metadatos sin recuperar cuerpo en red ambiental | integración pendiente | [031 / FR-007](../031-graph-message-lifecycle/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M044 — Fallos de entrega y reconciliación | integración pendiente | [031 / FR-008](../031-graph-message-lifecycle/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M045 — Dependencia de tareas registrada | integración pendiente | [032 / FR-001](../032-graph-work-dependencies/spec.md) | [L613](../019-frontend-studio/mockup-studio-original.html#L613) |
| M046 — Dependencia que realmente impide despacho | integración pendiente | [032 / FR-002](../032-graph-work-dependencies/spec.md) | [L922](../019-frontend-studio/mockup-studio-original.html#L922) |
| M047 — Bloqueo y sus causas múltiples | integración pendiente | [032 / FR-006](../032-graph-work-dependencies/spec.md) | [L922](../019-frontend-studio/mockup-studio-original.html#L922) |
| M048 — Estado de proceso distinto de hecho | integración pendiente | [032 / FR-003](../032-graph-work-dependencies/spec.md) | [L969](../019-frontend-studio/mockup-studio-original.html#L969) |
| M049 — Fases Planificar/Construir/Probar/Entregar | integración pendiente | [032 / FR-004](../032-graph-work-dependencies/spec.md) | [L625](../019-frontend-studio/mockup-studio-original.html#L625) |
| M050 — Columna AHORA basada en actividad real | capacidad nueva | [032 / FR-005](../032-graph-work-dependencies/spec.md) | [L626](../019-frontend-studio/mockup-studio-original.html#L626) |
| M051 — Ciclo confirmado de dependencias | integración pendiente | [032 / FR-007](../032-graph-work-dependencies/spec.md) | [L922](../019-frontend-studio/mockup-studio-original.html#L922) |
| M052 — Ruta crítica frente a camino de bloqueo | capacidad nueva | [032 / FR-008](../032-graph-work-dependencies/spec.md) | [L974](../019-frontend-studio/mockup-studio-original.html#L974) |
| M053 — Ruta parcial declarada | capacidad nueva | [032 / FR-009](../032-graph-work-dependencies/spec.md) | [L974](../019-frontend-studio/mockup-studio-original.html#L974) |
| M054 — Hechas hoy y zona horaria | capacidad nueva | [032 / FR-011](../032-graph-work-dependencies/spec.md) | [L969](../019-frontend-studio/mockup-studio-original.html#L969) |
| M055 — Pregunta real vinculada a tarea e intento | integración pendiente | [033 / FR-001](../033-graph-human-decisions/spec.md) | [L959](../019-frontend-studio/mockup-studio-original.html#L959) |
| M056 — Quién pregunta y quién decide | integración pendiente | [033 / FR-002](../033-graph-human-decisions/spec.md) | [L959](../019-frontend-studio/mockup-studio-original.html#L959) |
| M057 — Opciones reales y texto de pregunta | integración pendiente | [033 / FR-003](../033-graph-human-decisions/spec.md) | [L959](../019-frontend-studio/mockup-studio-original.html#L959) |
| M058 — Responder con persistencia y versión | integración pendiente | [033 / FR-004](../033-graph-human-decisions/spec.md) | [L964](../019-frontend-studio/mockup-studio-original.html#L964) |
| M059 — Respuesta aceptada/entregada/consumida | integración pendiente | [033 / FR-005](../033-graph-human-decisions/spec.md) | [L964](../019-frontend-studio/mockup-studio-original.html#L964) |
| M060 — Desbloqueo confirmado por motor | integración pendiente | [033 / FR-006](../033-graph-human-decisions/spec.md) | [L964](../019-frontend-studio/mockup-studio-original.html#L964) |
| M061 — Espera opcional frente a obligatoria | integración pendiente | [033 / FR-007](../033-graph-human-decisions/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M062 — Delegación y revocación de respuesta | integración pendiente | [033 / FR-008](../033-graph-human-decisions/spec.md) | [L959](../019-frontend-studio/mockup-studio-original.html#L959) |
| M063 — Contador y páginas de decisiones | integración pendiente | [033 / FR-010](../033-graph-human-decisions/spec.md) | [L1155](../019-frontend-studio/mockup-studio-original.html#L1155) |
| M064 — Primera decisión del grupo | integración pendiente | [033 / FR-010](../033-graph-human-decisions/spec.md) | [L1150](../019-frontend-studio/mockup-studio-original.html#L1150) |
| M065 — Actividad identificada, actor, hora y enlace | integración pendiente | [034 / FR-011](../034-graph-live-recovery/spec.md) | [L1166](../019-frontend-studio/mockup-studio-original.html#L1166) |
| M066 — Eventos nuevos sin perder selección | capacidad nueva | [034 / FR-009](../034-graph-live-recovery/spec.md) | [L1211](../019-frontend-studio/mockup-studio-original.html#L1211) |
| M067 — Desconexión, antigüedad y reconexión | capacidad nueva | [034 / FR-004](../034-graph-live-recovery/spec.md) | [L1211](../019-frontend-studio/mockup-studio-original.html#L1211) |
| M068 — Replay y huecos de historial | capacidad nueva | [034 / FR-005](../034-graph-live-recovery/spec.md) | [L1166](../019-frontend-studio/mockup-studio-original.html#L1166) |
| M069 — Aislamiento al cambiar proyecto o instancia | integración pendiente | [034 / FR-006](../034-graph-live-recovery/spec.md) | [L1204](../019-frontend-studio/mockup-studio-original.html#L1204) |
| M070 — Revocación en consultas y stream | integración pendiente | [034 / FR-007](../034-graph-live-recovery/spec.md) | [L1211](../019-frontend-studio/mockup-studio-original.html#L1211) |
| M071 — Duplicados y borrados tardíos | integración pendiente | [034 / FR-003](../034-graph-live-recovery/spec.md) | [L1211](../019-frontend-studio/mockup-studio-original.html#L1211) |
| M072 — Todas las instancias con cobertura conocida | integración pendiente | [035 / FR-002](../035-graph-instance-federation/spec.md) | [L323](../019-frontend-studio/mockup-studio-original.html#L323) |
| M073 — Worker distinto de servidor CAOS | integración pendiente | [035 / FR-001](../035-graph-instance-federation/spec.md) | [L624](../019-frontend-studio/mockup-studio-original.html#L624) |
| M074 — Equipo distribuido entre instancias | capacidad nueva | [035 / FR-004](../035-graph-instance-federation/spec.md) | [L624](../019-frontend-studio/mockup-studio-original.html#L624) |
| M075 — Origen desconectado y última observación | integración pendiente | [035 / FR-006](../035-graph-instance-federation/spec.md) | [L323](../019-frontend-studio/mockup-studio-original.html#L323) |
| M076 — Generación tras reemplazar worker | integración pendiente | [035 / FR-007](../035-graph-instance-federation/spec.md) | [L489](../019-frontend-studio/mockup-studio-original.html#L489) |
| M077 — Inventario y capacidad de ejecución reales | integración pendiente | [035 / FR-008](../035-graph-instance-federation/spec.md) | [L489](../019-frontend-studio/mockup-studio-original.html#L489) |
| M078 — Nueva tarea | integración pendiente | [036 / FR-001](../036-studio-work-commands/spec.md) | [L365](../019-frontend-studio/mockup-studio-original.html#L365) |
| M079 — Guardar frente a asignar o ejecutar | integración pendiente | [036 / FR-002](../036-studio-work-commands/spec.md) | [L365](../019-frontend-studio/mockup-studio-original.html#L365) |
| M080 — Abrir ejecución desde inspector | integración pendiente | [036 / FR-009](../036-studio-work-commands/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M081 — Cancelar/reintentar solo con autoridad | integración pendiente | [036 / FR-004](../036-studio-work-commands/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M082 — Acción incierta conserva recibo | integración pendiente | [036 / FR-005](../036-studio-work-commands/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M083 — Revisión aprobada y cambio relevante | integración pendiente | [036 / FR-006](../036-studio-work-commands/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M084 — Selector de proyecto real | integración pendiente | [037 / FR-001](../037-studio-discovery-context/spec.md) | [L320](../019-frontend-studio/mockup-studio-original.html#L320) |
| M085 — Buscar tarea/agente/decisión/equipo | integración pendiente | [037 / FR-002](../037-studio-discovery-context/spec.md) | [L330](../019-frontend-studio/mockup-studio-original.html#L330) |
| M086 — Grafo/lista con misma consulta | integración pendiente | [037 / FR-003](../037-studio-discovery-context/spec.md) | [L349](../019-frontend-studio/mockup-studio-original.html#L349) |
| M087 — Filtro agentes/tareas/decisiones | integración pendiente | [037 / FR-004](../037-studio-discovery-context/spec.md) | [L353](../019-frontend-studio/mockup-studio-original.html#L353) |
| M088 — Filtro de estado y requiere atención | integración pendiente | [037 / FR-004](../037-studio-discovery-context/spec.md) | [L358](../019-frontend-studio/mockup-studio-original.html#L358) |
| M089 — Filtro de relación y rama seleccionada | integración pendiente | [037 / FR-004](../037-studio-discovery-context/spec.md) | [L888](../019-frontend-studio/mockup-studio-original.html#L888) |
| M090 — Breadcrumbs y subir niveles | capacidad nueva | [037 / FR-005](../037-studio-discovery-context/spec.md) | [L371](../019-frontend-studio/mockup-studio-original.html#L371) |
| M091 — Enlace directo y atrás/adelante | integración pendiente | [037 / FR-006](../037-studio-discovery-context/spec.md) | [L1194](../019-frontend-studio/mockup-studio-original.html#L1194) |
| M092 — Selección oculta por filtro | integración pendiente | [037 / FR-008](../037-studio-discovery-context/spec.md) | [L1138](../019-frontend-studio/mockup-studio-original.html#L1138) |
| M093 — Mostrando x de y y filtros restablecibles | integración pendiente | [037 / FR-009](../037-studio-discovery-context/spec.md) | [L734](../019-frontend-studio/mockup-studio-original.html#L734) |
| M094 — Atajo / sin interferir en edición | integración pendiente | [037 / FR-010](../037-studio-discovery-context/spec.md) | [L330](../019-frontend-studio/mockup-studio-original.html#L330) |
| M095 — Perfiles y prompts del agente | integración pendiente | [038 / FR-001](../038-studio-agent-resources/spec.md) | [L304](../019-frontend-studio/mockup-studio-original.html#L304) |
| M096 — Editar perfil frente a proceso activo | integración pendiente | [038 / FR-002](../038-studio-agent-resources/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M097 — Conocimiento con fuente y revisión | integración pendiente | [038 / FR-006](../038-studio-agent-resources/spec.md) | [L305](../019-frontend-studio/mockup-studio-original.html#L305) |
| M098 — Skills instaladas/configuradas/autorizadas | integración pendiente | [038 / FR-005](../038-studio-agent-resources/spec.md) | [L306](../019-frontend-studio/mockup-studio-original.html#L306) |
| M099 — Modelo y proveedor efectivos | integración pendiente | [038 / FR-008](../038-studio-agent-resources/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M100 — Autoridad efectiva frente a rótulo ficticio | integración pendiente | [038 / FR-009](../038-studio-agent-resources/spec.md) | [L449](../019-frontend-studio/mockup-studio-original.html#L449) |
| M101 — Workflows como destino funcional | integración pendiente | [039 / FR-001](../039-studio-workflow-traceability/spec.md) | [L301](../019-frontend-studio/mockup-studio-original.html#L301) |
| M102 — Documento, fases y tareas trazables | integración pendiente | [039 / FR-006](../039-studio-workflow-traceability/spec.md) | [L625](../019-frontend-studio/mockup-studio-original.html#L625) |
| M103 — Spec Kit como producto con artefactos reales | integración pendiente | [039 / FR-010](../039-studio-workflow-traceability/spec.md) | [L625](../019-frontend-studio/mockup-studio-original.html#L625) |
| M104 — Plan revisado frente a borrador | integración pendiente | [039 / FR-005](../039-studio-workflow-traceability/spec.md) | [L934](../019-frontend-studio/mockup-studio-original.html#L934) |
| M105 — Revisión/entrega con evidencias de pruebas | integración pendiente | [039 / FR-009](../039-studio-workflow-traceability/spec.md) | [L974](../019-frontend-studio/mockup-studio-original.html#L974) |
| M106 — Nombres de workers sin líneas superpuestas | integración pendiente | [040 / FR-001](../040-studio-graph-usability/spec.md) | [L768](../019-frontend-studio/mockup-studio-original.html#L768) |
| M107 — Tipo y flecha distinguibles sin color | integración pendiente | [040 / FR-002](../040-studio-graph-usability/spec.md) | [L778](../019-frontend-studio/mockup-studio-original.html#L778) |
| M108 — Zoom + y − | integración pendiente | [040 / FR-003](../040-studio-graph-usability/spec.md) | [L385](../019-frontend-studio/mockup-studio-original.html#L385) |
| M109 — Encajar | integración pendiente | [040 / FR-003](../040-studio-graph-usability/spec.md) | [L389](../019-frontend-studio/mockup-studio-original.html#L389) |
| M110 — Zoom 100 % | integración pendiente | [040 / FR-003](../040-studio-graph-usability/spec.md) | [L390](../019-frontend-studio/mockup-studio-original.html#L390) |
| M111 — Centrar selección y destino | integración pendiente | [040 / FR-003](../040-studio-graph-usability/spec.md) | [L391](../019-frontend-studio/mockup-studio-original.html#L391) |
| M112 — Desplazar y Ctrl+rueda | integración pendiente | [040 / FR-006](../040-studio-graph-usability/spec.md) | [L1268](../019-frontend-studio/mockup-studio-original.html#L1268) |
| M113 — Minimapa | integración pendiente | [040 / FR-003](../040-studio-graph-usability/spec.md) | [L397](../019-frontend-studio/mockup-studio-original.html#L397) |
| M114 — Teclado: flechas, Enter, subir y Escape | integración pendiente | [040 / FR-004](../040-studio-graph-usability/spec.md) | [L395](../019-frontend-studio/mockup-studio-original.html#L395) |
| M115 — Lector de pantalla y foco visible | aceptación pendiente | [040 / FR-005](../040-studio-graph-usability/spec.md) | [L290](../019-frontend-studio/mockup-studio-original.html#L290) |
| M116 — Móvil y menú adaptable | integración pendiente | [040 / FR-007](../040-studio-graph-usability/spec.md) | [L318](../019-frontend-studio/mockup-studio-original.html#L318) |
| M117 — Texto truncado accesible completo | integración pendiente | [040 / FR-008](../040-studio-graph-usability/spec.md) | [L747](../019-frontend-studio/mockup-studio-original.html#L747) |
| M118 — Toast informativo frente a éxito real | integración pendiente | [040 / FR-009](../040-studio-graph-usability/spec.md) | [L1228](../019-frontend-studio/mockup-studio-original.html#L1228) |
| M119 — Distinguir maqueta y operación real | integración pendiente | [040 / FR-010](../040-studio-graph-usability/spec.md) | [L331](../019-frontend-studio/mockup-studio-original.html#L331) |
| M120 — Barra de operador y permisos reales | integración pendiente | [038 / FR-009](../038-studio-agent-resources/spec.md) | [L313](../019-frontend-studio/mockup-studio-original.html#L313) |
| M121 — Destinos Tareas, Agentes, Terminales y Actividad | integración pendiente | [037 / FR-006](../037-studio-discovery-context/spec.md) | [L296](../019-frontend-studio/mockup-studio-original.html#L296) |

| M122 — Reasignar tarea conserva intentos e historial | integración pendiente | [036 / FR-011](../036-studio-work-commands/spec.md) | [L953](../019-frontend-studio/mockup-studio-original.html#L953) |
| M123 — Abrir terminal correcto sin enviar entrada | integración pendiente | [035 / FR-011](../035-graph-instance-federation/spec.md) | [L953](../019-frontend-studio/mockup-studio-original.html#L953) |
| M124 — Asignar tarea desde inspector de agente | integración pendiente | [028 / FR-002](../028-team-delegation-authority/spec.md) | [L953](../019-frontend-studio/mockup-studio-original.html#L953) |
| M125 — Cancelar intento y confirmar su efecto | integración pendiente | [036 / FR-007](../036-studio-work-commands/spec.md) | [L953](../019-frontend-studio/mockup-studio-original.html#L953) |
| M126 — Primer bloqueado con causa real | integración pendiente | [037 / FR-002](../037-studio-discovery-context/spec.md) | [L1150](../019-frontend-studio/mockup-studio-original.html#L1150) |
| M127 — Barras, porcentajes y denominador por tipo | integración pendiente | [030 / FR-012](../030-graph-hierarchy-aggregation/spec.md) | [L1001](../019-frontend-studio/mockup-studio-original.html#L1001) |
| M128 — Indicadores En curso/Esperan decisión/Bloqueadas | integración pendiente | [030 / FR-012](../030-graph-hierarchy-aggregation/spec.md) | [L340](../019-frontend-studio/mockup-studio-original.html#L340) |
| M129 — Contadores laterales de tareas y decisiones | integración pendiente | [030 / FR-012](../030-graph-hierarchy-aggregation/spec.md) | [L1209](../019-frontend-studio/mockup-studio-original.html#L1209) |

Se registran 129 detalles semánticos, 30 controles de marcado estático y 88 funciones nombradas del HTML. Los controles generados dinámicamente se cubren por comportamiento en el inventario y por sus funciones en [mockup-controls.json](mockup-controls.json); el conteo no afirma cobertura automática de todas las posibles ejecuciones del JavaScript.

## Evidencia y límites

[Fuente y hashes](source-evidence.json), [inventario estructurado](inventory.json), [controles y funciones](mockup-controls.json). Cada spec incluye evidence.md con fuentes concretas. Las categorías se basan en las fronteras inspeccionadas, no en inferir inexistencia por búsqueda negativa.

Graphify existente: 54.428 nodos, construido en `87e2d5f354374ea8acc685275e749992df51cdbd`, anterior a HEAD. Se consultó su vocabulario real `execution, assignment, workflow, fleet, graph, work` y vecinos de assignment_service/work_projection: repositorio Work, principal y autorización, modelos Work e inbox. Líneas y contratos se contrastaron después con código actual. No se regeneró el grafo ajeno modificado para una entrega documental.

La revisión independiente Codex Luna confirmó Terminal.group, las omisiones de WorkView/project_work y la diferencia knowledge graph/ejecución. Claude no pudo revisar: job `78d46f9e219c4fb98dfa45e0b76ff16d` falló por límite de sesión; no se atribuye revisión aprobada a ese intento.
