# Tasks: Coordinación efectiva y delegación entre equipos

**Input**: [spec.md](spec.md), [plan.md](plan.md), [evidence.md](evidence.md).

**Status**: Todas las tareas de implementación están pendientes. La auditoría que originó esta lista no equivale a completar estas tareas.

## Phase 1 — Contratos y evidencia

- [ ] T001 Revalidar fuentes y recibos actuales de evidence.md; completar matriz FR → fuente → caso sin duplicar capacidades ya integradas.
- [ ] T002 Definir y revisar contratos detallados de identidad, ámbito, permisos, revisión, errores, persistencia y compatibilidad de esta spec; registrar migraciones o razón de no necesitarlas y presupuestos previos de carga.
- [ ] T003 Escribir casos de contrato y aceptación con oráculo de origen independiente para todos los escenarios y casos límite.

## Phase 2 — User Story 1 (P1)

- [ ] T004 Implementar los requisitos FR de la historia principal sobre los servicios identificados; conservar autoridad en origen y no simular operaciones del producto.
- [ ] T005 Integrar lectura y comandos aplicables en Studio, con estados de carga, vacío, parcial, denegado y error y referencias de evidencia.
- [ ] T006 Comparar identidades, relaciones, estados y efectos del recorrido principal con los registros autoritativos; conservar recibos por revisión.

## Phase 3 — User Story 2 (P1)

- [ ] T007 Implementar y comprobar concurrencia, duplicación, interrupción, revocación, recuperación y límites descritos, sin degradar criterios existentes.
- [ ] T008 Verificar la semántica al cambiar nivel, filtro, vista, instancia y revisión cuando aplique; distinguir representación de efecto real.

## Phase 4 — Cierre

- [ ] T009 Ejecutar pruebas aplicables de contrato, integración y recorrido real; medir carga declarada y conservar fallos o gates omitidos como pendientes.
- [ ] T010 Ejecutar composición, revisión independiente y verificación final; corregir hallazgos y actualizar estado de esta spec y el catálogo de conformidad con evidencia.

## Dependencies and parallel work

[027](../027-agent-team-topology/spec.md)

T001 → T002 → T003 → T004/T005 → T006 → T007/T008 → T009 → T010. Paralelizar lecturas o módulos con contratos acordados; no ejecutar escrituras simultáneas sobre el mismo checkout ni pruebas nativas intensivas en paralelo. Los cambios aceptados se integran en main preservando el trabajo existente.

## Requirement-specific implementation tasks

Fronteras iniciales inspeccionadas: `src/cli_agent_orchestrator/clients/database.py`, `src/cli_agent_orchestrator/services/assignment_service.py`, `src/cli_agent_orchestrator/services/local_peer_registry.py`, `src/cli_agent_orchestrator/services/work_service.py`. Son puntos de integración, no orden de crear duplicados. T002 fijará archivos nuevos y contratos exactos antes de implementarlos.

- [ ] T011 [FR-001] Entregar y comprobar: Una designación de coordinador DEBE referenciar la autoridad ya emitida por los contratos existentes de asignación, Work y, para Spec Kit de producto, 020, con proyecto, operaciones, destinatarios y vigencia; la designación no emitirá otra concesión ni el nombre del perfil concederá permisos. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T012 [FR-002] Entregar y comprobar: Toda asignación DEBE identificar mandante, coordinador, equipo, tarea, intento, destino, concesión y revisión aplicables. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T013 [FR-003] Entregar y comprobar: La delegación DEBE verificarse en el servidor por operación antes del efecto; ampliar permisos del cliente no debe ampliar la autoridad del servidor. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T014 [FR-004] Entregar y comprobar: Crear terminal, delegar trabajo, subcontratar una parte y comunicar un mensaje DEBEN registrarse como hechos distintos. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T015 [FR-005] Entregar y comprobar: La programación DEBE comprobar dependencias, capacidades del proveedor, límites de recursos y exclusión de escrituras sobre el mismo destino. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T016 [FR-006] Entregar y comprobar: Cuando se autorice paralelismo con escrituras, el sistema DEBE usar destinos aislados o exclusión demostrada, y conservar su identidad en el grafo. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T017 [FR-007] Entregar y comprobar: Reintentar una solicitud incierta DEBE reconciliar la asignación existente con la misma identidad e impedir duplicar trabajador o entrega. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T018 [FR-008] Entregar y comprobar: Revocar un mandato DEBE bloquear nuevas operaciones de ese mandato; los intentos en curso deben mostrar su política de continuación o cancelación sin fingir que ya terminaron. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T019 [FR-009] Entregar y comprobar: El coordinador DEBE poder resumir resultados únicamente con evidencia aceptada de sus tareas; un ACK o la salida del proceso no completa el equipo. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T020 [FR-010] Entregar y comprobar: Los trabajadores elásticos DEBEN usar su contrato de finalización específico cuando esté configurado; no debe ofrecerse ese mecanismo a cualquier agente por analogía. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.

Estas tareas se ejecutan dentro de T004–T008, después de T003 y antes de T009. T002/T003 definen contrato y oráculo para cada FR; T009/T010 reúnen la evidencia de sus SC. Ningún requisito puede cerrarse solo por completar una tarea genérica.
