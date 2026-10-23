# Tasks: Búsqueda, filtros y navegación con contexto real

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

[029](../029-execution-graph-provenance/spec.md), [030](../030-graph-hierarchy-aggregation/spec.md)

T001 → T002 → T003 → T004/T005 → T006 → T007/T008 → T009 → T010. Paralelizar lecturas o módulos con contratos acordados; no ejecutar escrituras simultáneas sobre el mismo checkout ni pruebas nativas intensivas en paralelo. Los cambios aceptados se integran en main preservando el trabajo existente.

## Requirement-specific implementation tasks

Fronteras iniciales inspeccionadas: `web/src/components/StudioNav.tsx`. Son puntos de integración, no orden de crear duplicados. T002 fijará archivos nuevos y contratos exactos antes de implementarlos.

- [ ] T011 [FR-001] Entregar y comprobar: La búsqueda DEBE declarar si cubre el nivel visible, el proyecto o todos los orígenes autorizados; no presentará búsqueda local como global. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T012 [FR-002] Entregar y comprobar: La búsqueda DEBE localizar agentes, equipos, tareas y decisiones por identidad o texto permitido, y distinguir no encontrado de acceso o fuente no disponible. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T013 [FR-003] Entregar y comprobar: Cambiar entre grafo y lista DEBE conservar ámbito, filtros y selección, con los mismos datos y cobertura. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T014 [FR-004] Entregar y comprobar: Los filtros de tipo de nodo, estado, relación e instancia DEBEN componer una consulta explícita y mostrar cuánto se está observando. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T015 [FR-005] Entregar y comprobar: Los breadcrumbs DEBEN reflejar la jerarquía registrada o la agrupación visual elegida; subir no cambiará equipo ni autoridad. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T016 [FR-006] Entregar y comprobar: Los enlaces directos y atrás/adelante DEBEN restaurar un contexto autorizado o explicar su pérdida sin abrir un recurso homónimo. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T017 [FR-007] Entregar y comprobar: El inspector DEBE poder centrar, abrir extremos y volver al grafo preservando selección, página y filtros cuando sigan siendo válidos. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T018 [FR-008] Entregar y comprobar: Vacío real, sin coincidencias, error, carga, parcial y acceso denegado DEBEN tener presentaciones y próximos pasos distintos. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T019 [FR-009] Entregar y comprobar: Las etiquetas de conteo DEBEN distinguir total del ámbito, resultado de filtros y elementos dibujados. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T020 [FR-010] Entregar y comprobar: El atajo de búsqueda DEBE respetar campos en edición; cambiar de ámbito debe cancelar consultas anteriores y eliminar resultados revocados. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.

Estas tareas se ejecutan dentro de T004–T008, después de T003 y antes de T009. T002/T003 definen contrato y oráculo para cada FR; T009/T010 reúnen la evidencia de sus SC. Ningún requisito puede cerrarse solo por completar una tarea genérica.
