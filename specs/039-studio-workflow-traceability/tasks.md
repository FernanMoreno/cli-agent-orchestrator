# Tasks: Workflows y Spec Kit trazables hasta el trabajo ejecutado

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

[028](../028-team-delegation-authority/spec.md), [029](../029-execution-graph-provenance/spec.md), [032](../032-graph-work-dependencies/spec.md), [036](../036-studio-work-commands/spec.md)

T001 → T002 → T003 → T004/T005 → T006 → T007/T008 → T009 → T010. Paralelizar lecturas o módulos con contratos acordados; no ejecutar escrituras simultáneas sobre el mismo checkout ni pruebas nativas intensivas en paralelo. Los cambios aceptados se integran en main preservando el trabajo existente.

## Requirement-specific implementation tasks

Fronteras iniciales inspeccionadas: `src/cli_agent_orchestrator/services/work_service.py`, `src/cli_agent_orchestrator/services/workflow_step_projector.py`, `web/src/components/WorkflowsPanel.tsx`. Son puntos de integración, no orden de crear duplicados. T002 fijará archivos nuevos y contratos exactos antes de implementarlos.

- [ ] T011 [FR-001] Entregar y comprobar: Cada fase o paso visible DEBE identificar workflow o feature, revisión de documentos, entradas, perfil y destino aprobados. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T012 [FR-002] Entregar y comprobar: Crear, validar, revisar y ejecutar DEBEN ser etapas diferentes con artefactos comprobables; terminar un agente sin artefacto válido no completa una fase. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T013 [FR-003] Entregar y comprobar: Las dependencias DEBEN conservar las reglas soportadas del motor; los modos reservados o incompatibles se bloquearán con motivo. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T014 [FR-004] Entregar y comprobar: La edición visual y la fuente DEBEN preservar semántica y campos no representados, sin sobrescritura silenciosa. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T015 [FR-005] Entregar y comprobar: La vista DEBE presentar la aprobación emitida por el motor existente, con revisiones y límites; ante un cambio relevante debe reflejar su invalidación y bloquear el comando según la capacidad del motor, sin implementar una aprobación alternativa. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T016 [FR-006] Entregar y comprobar: Cada paso ejecutado DEBE vincular su tarea, intento, agente, asignación y resultado aceptado; ningún salto visual debe inventar esa cadena. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T017 [FR-007] Entregar y comprobar: Los hallazgos y preguntas de aclaración DEBEN asociarse a la fase y documentos que los causaron y respetar autoridad de respuesta. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T018 [FR-008] Entregar y comprobar: Los comandos de reintento o recuperación DEBEN invocar los contratos del motor existente y presentar su reconciliación e intentos, distinguiendo nueva ejecución de reanudación sin un motor alternativo en Studio. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T019 [FR-009] Entregar y comprobar: La entrega DEBE mostrar revisión y pruebas aceptadas, pendientes y fallidas; un contador de pasos terminados no sustituye la aceptación del conjunto. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T020 [FR-010] Entregar y comprobar: Usar Spec Kit en desarrollo de CAOS DEBE distinguirse de que CAOS lo ofrezca como capacidad de producto a otro proyecto. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.

Estas tareas se ejecutan dentro de T004–T008, después de T003 y antes de T009. T002/T003 definen contrato y oráculo para cada FR; T009/T010 reúnen la evidencia de sus SC. Ningún requisito puede cerrarse solo por completar una tarea genérica.
