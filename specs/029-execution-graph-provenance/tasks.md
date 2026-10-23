# Tasks: Identidad, procedencia y contrato del grafo de ejecución

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

Fronteras iniciales inspeccionadas: `web/src/components/execution/executionMap.ts`, `src/cli_agent_orchestrator/clients/database.py`, `src/cli_agent_orchestrator/models/work.py`, `src/cli_agent_orchestrator/graph/models.py`, `src/cli_agent_orchestrator/services/work_projection.py`. Son puntos de integración, no orden de crear duplicados. T002 fijará archivos nuevos y contratos exactos antes de implementarlos.

- [ ] T011 [FR-001] Entregar y comprobar: La identidad DEBE distinguir proyecto, instancia de servidor, encarnación y recurso; dos terminales de distintos orígenes con igual identificador local no serán el mismo agente. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T012 [FR-002] Entregar y comprobar: Cada nodo DEBE diferenciar agente lógico, terminal/proceso, tarea, intento, pregunta, grupo y resultado, sin equiparar sus ciclos de vida. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T013 [FR-003] Entregar y comprobar: Cada arista DEBE identificar tipo, dirección, extremos, registro de origen, revisión, vigencia y cobertura; deberá conservar su identidad al agregar o filtrar. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T014 [FR-004] Entregar y comprobar: La proyección DEBE exponer únicamente metadatos necesarios; prompts, razonamientos, respuestas y secretos requieren lectura de detalle autorizada separada. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T015 [FR-005] Entregar y comprobar: La propiedad de una tarea DEBE proceder de la asignación vigente del intento; una fila visual por sí sola no prueba quién la ejecuta. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T016 [FR-006] Entregar y comprobar: Una creación de terminal DEBE diferenciarse de asignación o mando. Los registros históricos de hijos y asignaciones deben conservar trazabilidad tras borrar el proceso. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T017 [FR-007] Entregar y comprobar: Las fuentes incompletas o incompatibles DEBEN producir cobertura explícita; los extremos no visibles no se sustituirán por agentes inventados. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T018 [FR-008] Entregar y comprobar: La lectura DEBE definir una revisión consistente o un vector de revisiones por fuente; no se presentarán lecturas mezcladas como una instantánea global atómica. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T019 [FR-009] Entregar y comprobar: La taxonomía de ejecución DEBE separarse de relaciones temáticas del conocimiento; una asociación temática no prueba comunicación, dependencia o delegación. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.
- [ ] T020 [FR-010] Entregar y comprobar: El inspector DEBE ofrecer procedencia, antigüedad, permisos y navegación al registro autorizado, distinguiendo eliminado, inaccesible y no observado. Evidencia: caso positivo y caso de ausencia/conflicto/denegación aplicable, contrastados con la fuente de origen; no basta el resultado calculado por el propio grafo.

Estas tareas se ejecutan dentro de T004–T008, después de T003 y antes de T009. T002/T003 definen contrato y oráculo para cada FR; T009/T010 reúnen la evidencia de sus SC. Ningún requisito puede cerrarse solo por completar una tarea genérica.
