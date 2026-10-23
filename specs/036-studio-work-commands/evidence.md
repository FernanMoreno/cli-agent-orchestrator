# Evaluación de código: Crear tareas y actuar sobre trabajo real desde Studio

Fecha: 2026-10-23. Base de inspección: `f2c381ed87fab87631a363097da50898de96cd06` más los cambios documentales preexistentes de 025, preservados. SHA de la maqueta: `915b0110d1d775ad738d35bee881d9480d5f2b59cc787bc1d067c7bf9f604690`.

## Estado observado

La maqueta ofrece Nueva tarea como toast simulado. El producto ya tiene BeadsPanel, planes y asignaciones y Work; la carencia es integrar esos contratos en el recorrido aprobado del grafo con sus capacidades y recibos.

## Fuente verificada

- **E09** — [src/cli_agent_orchestrator/services/assignment_service.py:156](../../src/cli_agent_orchestrator/services/assignment_service.py#L156): Asignaciones con propietario, clave y caller.
- **E10** — [src/cli_agent_orchestrator/models/work.py:96](../../src/cli_agent_orchestrator/models/work.py#L96): Tarea y jerarquía de trabajo distintas de intento y proceso.
- **E16** — [src/cli_agent_orchestrator/services/beads_assignment_service.py:40](../../src/cli_agent_orchestrator/services/beads_assignment_service.py#L40): Comprobación real de dependencias Beads al preparar.

Las líneas se resolvieron sobre los archivos actuales. Los hashes auditados están en [source-evidence.json](../026-studio-graph-conformance/source-evidence.json). Graphify es un mapa previo y sus líneas no sustituyen esta inspección.

## Brecha y propiedad

Entrega el recorrido visual de contratos existentes; no reemplaza Work ni crea un scheduler por el hecho de dibujar tareas.

No se afirma inexistencia de toda capacidad equivalente: las fuentes citadas acreditan el alcance descrito; la ausencia de integración completa se confirma sobre el mapa y la maqueta inspeccionados. Antes de implementar se revalidarán contratos y recibos recientes para conservar trabajo ya integrado.

## Evidencia exigida para cierre

- Pruebas de contrato e integración de los requisitos FR y escenarios de spec.md.
- Comparación entre registros de origen y presentación, con identidades y revisiones independientes.
- Recorrido real para las operaciones o proveedores que esta entrega declare aceptados; mocks y diagnósticos separados.
- Fallos parciales, duplicados, autorización y recuperación aplicables. Los recibos históricos de otra revisión no se promueven a aceptación nueva.
