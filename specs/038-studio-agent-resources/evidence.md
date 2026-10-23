# Evaluación de código: Perfiles, prompts, conocimiento y skills efectivos

Fecha: 2026-10-23. Base de inspección: `f2c381ed87fab87631a363097da50898de96cd06` más los cambios documentales preexistentes de 025, preservados. SHA de la maqueta: `915b0110d1d775ad738d35bee881d9480d5f2b59cc787bc1d067c7bf9f604690`.

## Estado observado

Hay ProfilesPanel y editores, MemoryPanel, PluginsPanel y configuración MCP por proveedor en evolución. Los enlaces de biblioteca de la maqueta son marcadores; mostrar un permiso o una skill no prueba su activación en el proceso.

## Fuente verificada

- **E15** — [src/cli_agent_orchestrator/graph/models.py:27](../../src/cli_agent_orchestrator/graph/models.py#L27): Taxonomía del grafo de conocimiento distinta de ejecución.
- **E21** — [web/src/components/ProfilesPanel.tsx:202](../../web/src/components/ProfilesPanel.tsx#L202): Biblioteca de perfiles existente.

Las líneas se resolvieron sobre los archivos actuales. Los hashes auditados están en [source-evidence.json](../026-studio-graph-conformance/source-evidence.json). Graphify es un mapa previo y sus líneas no sustituyen esta inspección.

## Brecha y propiedad

Delta de integración sobre 019 y 025; no declara nuevas herramientas autorizadas ni sustituye el grafo de conocimiento.

No se afirma inexistencia de toda capacidad equivalente: las fuentes citadas acreditan el alcance descrito; la ausencia de integración completa se confirma sobre el mapa y la maqueta inspeccionados. Antes de implementar se revalidarán contratos y recibos recientes para conservar trabajo ya integrado.

## Evidencia exigida para cierre

- Pruebas de contrato e integración de los requisitos FR y escenarios de spec.md.
- Comparación entre registros de origen y presentación, con identidades y revisiones independientes.
- Recorrido real para las operaciones o proveedores que esta entrega declare aceptados; mocks y diagnósticos separados.
- Fallos parciales, duplicados, autorización y recuperación aplicables. Los recibos históricos de otra revisión no se promueven a aceptación nueva.
