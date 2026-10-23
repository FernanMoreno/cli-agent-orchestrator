# Evaluación de código: Workflows y Spec Kit trazables hasta el trabajo ejecutado

Fecha: 2026-10-23. Base de inspección: `f2c381ed87fab87631a363097da50898de96cd06` más los cambios documentales preexistentes de 025, preservados. SHA de la maqueta: `915b0110d1d775ad738d35bee881d9480d5f2b59cc787bc1d067c7bf9f604690`.

## Estado observado

Hay workflows, journal durable, proyección de pasos y contratos Work. La spec020 define integración Spec Kit, pero sus documentos no prueban que el producto ejecute ese recorrido ni que las fases fijas de la maqueta sean reales.

## Fuente verificada

- **E17** — [src/cli_agent_orchestrator/services/work_service.py:1265](../../src/cli_agent_orchestrator/services/work_service.py#L1265): Aceptación de hijos y resultados Work.
- **E20** — [src/cli_agent_orchestrator/services/workflow_step_projector.py:68](../../src/cli_agent_orchestrator/services/workflow_step_projector.py#L68): Proyección de pasos hacia Work existente.
- **E24** — [web/src/components/WorkflowsPanel.tsx:13](../../web/src/components/WorkflowsPanel.tsx#L13): Panel de workflows existente.

Las líneas se resolvieron sobre los archivos actuales. Los hashes auditados están en [source-evidence.json](../026-studio-graph-conformance/source-evidence.json). Graphify es un mapa previo y sus líneas no sustituyen esta inspección.

## Brecha y propiedad

Descompone la integración pendiente de 019/020 sin sustituir sus requisitos ni dar por implementado el motor de producto de Spec Kit.

No se afirma inexistencia de toda capacidad equivalente: las fuentes citadas acreditan el alcance descrito; la ausencia de integración completa se confirma sobre el mapa y la maqueta inspeccionados. Antes de implementar se revalidarán contratos y recibos recientes para conservar trabajo ya integrado.

## Evidencia exigida para cierre

- Pruebas de contrato e integración de los requisitos FR y escenarios de spec.md.
- Comparación entre registros de origen y presentación, con identidades y revisiones independientes.
- Recorrido real para las operaciones o proveedores que esta entrega declare aceptados; mocks y diagnósticos separados.
- Fallos parciales, duplicados, autorización y recuperación aplicables. Los recibos históricos de otra revisión no se promueven a aceptación nueva.
