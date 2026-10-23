# Evaluación de código: Identidad, procedencia y contrato del grafo de ejecución

Fecha: 2026-10-23. Base de inspección: `f2c381ed87fab87631a363097da50898de96cd06` más los cambios documentales preexistentes de 025, preservados. SHA de la maqueta: `915b0110d1d775ad738d35bee881d9480d5f2b59cc787bc1d067c7bf9f604690`.

## Estado observado

executionMap.ts compone agentes, comunicación y esperas; no incluye delegaciones ni enlaza todas las esperas con agentes. graph/models.py pertenece al grafo de conocimiento, con otra taxonomía; no demuestra un grafo de ejecución completo.

## Fuente verificada

- **E04** — [web/src/components/execution/executionMap.ts:39](../../web/src/components/execution/executionMap.ts#L39): Solo comunicación y waits_on en la proyección actual.
- **E08** — [src/cli_agent_orchestrator/clients/database.py:408](../../src/cli_agent_orchestrator/clients/database.py#L408): Creación de terminal distinta de tarea y evidencia durable del hijo.
- **E10** — [src/cli_agent_orchestrator/models/work.py:96](../../src/cli_agent_orchestrator/models/work.py#L96): Tarea y jerarquía de trabajo distintas de intento y proceso.
- **E15** — [src/cli_agent_orchestrator/graph/models.py:27](../../src/cli_agent_orchestrator/graph/models.py#L27): Taxonomía del grafo de conocimiento distinta de ejecución.

Las líneas se resolvieron sobre los archivos actuales. Los hashes auditados están en [source-evidence.json](../026-studio-graph-conformance/source-evidence.json). Graphify es un mapa previo y sus líneas no sustituyen esta inspección.

Work ya persiste `parent_work_item_id` y `WorkAttempt.terminal_id`; `WorkView` y `project_work()` no los exponen en la proyección consumible actual. La brecha es su lectura autorizada y asociación al grafo, no crear otra jerarquía de trabajo. [Modelo](../../src/cli_agent_orchestrator/models/work.py#L96), [proyección](../../src/cli_agent_orchestrator/services/work_projection.py#L8).

## Brecha y propiedad

Refina el contrato de lectura de 017. No convierte el grafo de memoria en autoridad de ejecución ni decide programación.

No se afirma inexistencia de toda capacidad equivalente: las fuentes citadas acreditan el alcance descrito; la ausencia de integración completa se confirma sobre el mapa y la maqueta inspeccionados. Antes de implementar se revalidarán contratos y recibos recientes para conservar trabajo ya integrado.

## Evidencia exigida para cierre

- Pruebas de contrato e integración de los requisitos FR y escenarios de spec.md.
- Comparación entre registros de origen y presentación, con identidades y revisiones independientes.
- Recorrido real para las operaciones o proveedores que esta entrega declare aceptados; mocks y diagnósticos separados.
- Fallos parciales, duplicados, autorización y recuperación aplicables. Los recibos históricos de otra revisión no se promueven a aceptación nueva.
