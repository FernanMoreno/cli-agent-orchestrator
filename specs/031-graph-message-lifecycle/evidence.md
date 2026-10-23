# Evaluación de código: Comunicación real y ciclo de entrega en el grafo

Fecha: 2026-10-23. Base de inspección: `f2c381ed87fab87631a363097da50898de96cd06` más los cambios documentales preexistentes de 025, preservados. SHA de la maqueta: `915b0110d1d775ad738d35bee881d9480d5f2b59cc787bc1d067c7bf9f604690`.

## Estado observado

Existen inbox tradicional y cao.work.message con servicio y repositorio propios. El mapa actual lee la primera página del inbox; no representa automáticamente mensajes Work ni su ciclo de confirmación.

## Fuente verificada

- **E05** — [web/src/components/execution/executionMap.ts:158](../../web/src/components/execution/executionMap.ts#L158): Cobertura y primera página del inbox.
- **E11** — [src/cli_agent_orchestrator/services/work_message_service.py:30](../../src/cli_agent_orchestrator/services/work_message_service.py#L30): Contrato cao.work.message y autorización específica.

Las líneas se resolvieron sobre los archivos actuales. Los hashes auditados están en [source-evidence.json](../026-studio-graph-conformance/source-evidence.json). Graphify es un mapa previo y sus líneas no sustituyen esta inspección.

La proyección de la vista descarta texto al construir aristas, pero la lectura actual de inbox sí entrega cuerpos al navegador. Eso no satisface un contrato de red solo de metadatos; está abierto expresamente como 017-T014. [Pendientes originales](../017-live-execution-graph/tasks.md).

## Brecha y propiedad

Integra 023/024; no reimplementa sus buzones ni relaja su autorización o aceptación final.

No se afirma inexistencia de toda capacidad equivalente: las fuentes citadas acreditan el alcance descrito; la ausencia de integración completa se confirma sobre el mapa y la maqueta inspeccionados. Antes de implementar se revalidarán contratos y recibos recientes para conservar trabajo ya integrado.

## Evidencia exigida para cierre

- Pruebas de contrato e integración de los requisitos FR y escenarios de spec.md.
- Comparación entre registros de origen y presentación, con identidades y revisiones independientes.
- Recorrido real para las operaciones o proveedores que esta entrega declare aceptados; mocks y diagnósticos separados.
- Fallos parciales, duplicados, autorización y recuperación aplicables. Los recibos históricos de otra revisión no se promueven a aceptación nueva.
