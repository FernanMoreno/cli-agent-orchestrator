# Tasks: Actualización viva, recuperación y aislamiento de acceso

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

[029](../029-execution-graph-provenance/spec.md)

T001 → T002 → T003 → T004/T005 → T006 → T007/T008 → T009 → T010. Paralelizar lecturas o módulos con contratos acordados; no ejecutar escrituras simultáneas sobre el mismo checkout ni pruebas nativas intensivas en paralelo. Los cambios aceptados se integran en main preservando el trabajo existente.

## Requirement-specific implementation tasks

Fronteras iniciales inspeccionadas: `web/src/components/execution/ExecutionMapPanel.tsx`, `src/cli_agent_orchestrator/api/main.py`. Son puntos de integración, no orden de crear duplicados. T002 fijará archivos nuevos y contratos exactos antes de implementarlos.

- [ ] T011 [FR-001] Entregar y comprobar: La carga inicial y las actualizaciones DEBEN converger a revisiones verificables, sin perder eventos entre instantánea y suscripción. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T012 [FR-002] Entregar y comprobar: Cada fuente DEBE declarar orden local, cursor, retención y cobertura; relojes de máquinas distintas no se usarán como orden causal global. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T013 [FR-003] Entregar y comprobar: Duplicados y eventos anteriores DEBEN ser idempotentes y no revertir estados más recientes ni resucitar entidades eliminadas. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T014 [FR-004] Entregar y comprobar: La desconexión DEBE indicar última observación y estado desactualizado; no se mostrará en vivo mientras no se pueda verificar continuidad. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T015 [FR-005] Entregar y comprobar: Un cursor vencido o hueco DEBE producir resincronización y declarar el tramo desconocido, conservando la diferencia entre estado actual e historia completa. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T016 [FR-006] Entregar y comprobar: Cambiar proyecto, instancia, encarnación o permisos DEBE invalidar lecturas y suscripciones anteriores antes de incorporar datos. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T017 [FR-007] Entregar y comprobar: La autorización DEBE aplicarse en servidor a instantáneas, eventos, agregados y detalles; revocación debe retirar datos y cerrar acceso sin esperar una acción del usuario. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T018 [FR-008] Entregar y comprobar: Las consultas ambientales DEBEN ser de metadatos y no precargar contenido privado con intención de ocultarlo después. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T019 [FR-009] Entregar y comprobar: Actualizar el grafo DEBE conservar selección y encuadre salvo que el recurso deje de estar disponible, explicándolo sin sustituir su identidad. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T020 [FR-010] Entregar y comprobar: Consumidores lentos DEBEN aplicar una política de cola acotada y recuperación explícita; desbordar no debe consumir memoria sin límite ni omitir cambios silenciosamente. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T021 [FR-011] Entregar y comprobar: El panel de actividad DEBE proceder de eventos reales identificados, con fecha, origen, objeto y paginación. Su orden será por fuente cuando no exista orden global; la reproducción histórica no debe ejecutar acciones y debe declarar retención y tramos ausentes. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.

Estas tareas se ejecutan dentro de T004–T008, después de T003 y antes de T009. T002/T003 definen contrato y oráculo para cada FR; T009/T010 reúnen la evidencia de sus SC. Ningún requisito puede cerrarse solo por completar una tarea genérica.
